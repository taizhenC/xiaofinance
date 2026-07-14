# Technical Architecture — Assessment & Target

Scope: justify the architectural changes referenced by the P0/P1 backlog, define the
target architecture for the public release, and sketch (without committing to) the
hosted-future path. Grounded in the code as of commit `6285a85`.

## 1. Current architecture

```
┌─ scripts/*.ps1 (Windows-only setup / login / smoke)
│
│  ┌───────────────────────────── FastAPI app (app/main.py) ─────────────────────────────┐
│  │  module-level threading.Lock (fetch) · APScheduler interval job · static mount      │
│  │  REST: /api/status /fetch /ranking /stocks/{t} /tracked /scoreboard /runs /alias…   │
│  └──────────────────────────────────────┬───────────────────────────────────────────---┘
│                                         │ daemon thread per cycle
│                                         ▼
│   pipeline.run_cycle(mode)  ──  crawl → ingest → dedup → mentions → score → analyze
│        │                          │(discovery, tracked)                → prices → cleanup → (slang_scan)
│        │                          ▼
│        │              crawler_runner: regex-patches vendored MediaCrawler source,
│        │              subprocess `uv run main.py --platform xhs …`, visible Chromium,
│        │              JSONL out, log-string login heuristics, taskkill /T /F timeout
│        ▼
│   SQLite (WAL, data/infinance.db) — schema via CREATE TABLE IF NOT EXISTS on connect
│        ▲                                    ▲
│        │ DeepSeek (openai SDK, JSON mode)   │ Yahoo chart API (free daily closes)
│        └── analyze.py / slang_scan.py       └── prices.py (bg thread + lock)
│
└─ static/ — index.html + 400-line vanilla app.js + ECharts (downloaded by setup script)
```

**Strengths worth preserving** (do not regress these in any refactor):

- **Staged, pure-ish pipeline modules** taking `(conn, settings)` — already unit-tested
  (14 test files) without a live crawler.
- **Freshness enforced twice** (ingest gate + every query recomputes the window) — the
  product's core guarantee is structural, not procedural.
- **Dedup before everything downstream** — scoring and the LLM see clusters, not spam;
  `[×N相似]` keeps repetition visible without token burn.
- **Provenance everywhere** — `match_basis`, `source_keyword` → targeted attribution,
  `input_hash` skip logic, per-analysis token/cost accounting.
- **Failure isolation** — per-ticker analysis errors keep prior results visible; keyless
  fallback; malformed JSONL lines → `partial`, not crash; stale `running` rows healed at
  startup; price failures can never break a cycle.
- **Local-first privacy posture** — localhost bind, zero-CDN frontend, optional external
  calls only (LLM, quotes), raw content pruned at 7 days.

## 2. What blocks the next stage

| # | Constraint | Evidence | Consequence for a public release |
|---|---|---|---|
| 1 | Windows coupling | PS-only scripts; `taskkill` (`crawler_runner.py:134`); Windows UA default | Excludes macOS-heavy target audience |
| 2 | Crawler tangled with pipeline | Regex source-patching, CLI building, login log-heuristics all in one module consumed directly by `pipeline.py` | Upstream churn has full blast radius; second source impossible; license boundary implicit |
| 3 | Opaque, uncancellable jobs | Cycle = daemon thread + boolean lock; status = `lock.locked()` (`main.py:121`); duplicated run-wrappers (`main.py:48` vs `:143`) | 10–15 min spinner; no cancel; no stage visibility; multi-worker uvicorn silently breaks it |
| 4 | Schema cannot evolve | `executescript(CREATE IF NOT EXISTS)` per connect (`db.py:161`) | First released schema change crashes every existing install |
| 5 | Frontend at its ceiling | One vanilla JS file building HTML strings; ECharts fetched "latest" at setup | Cannot carry wizard/routing/i18n/themes; non-reproducible chart lib version |
| 6 | Trust asserted, not verified | No quote verification, no eval corpus | One fabricated quote or matcher regression is undetectable pre-release |

## 3. R1 — `SourceProvider` abstraction (P0 · PL-03)

**Change.** Introduce the narrow interface the pipeline actually needs, and move every
MediaCrawler-specific mechanism behind it:

```python
class SourceProvider(Protocol):
    def search(self, req: SearchRequest) -> RunResult: ...   # keywords, limits, run_dir
    def login(self, visible: bool = True) -> LoginOutcome: ...
    def session_state(self) -> SessionState: ...             # valid | expired | unauthorized | unknown
    def cancel(self, run: RunHandle) -> None: ...            # kill tree, finalize
    # RunResult carries structured telemetry events, not a log path to grep

class MediaCrawlerProvider(SourceProvider):
    # owns: vendor pinning check, config/code/UA patches, CLI construction,
    # cookie delivery (TR-03), JSONL discovery, login heuristics (until DC-08)
```

`ingest.py` keeps consuming a run directory of JSONL — the provider contract includes the
output shape, verified by contract tests against recorded fixtures.

**Justification.**
- **License boundary made structural.** MediaCrawler is non-commercial/learning-licensed.
  It must remain a user-fetched plugin our code *talks to*, never a library our product
  *contains*. An interface with one adapter is the honest encoding of that relationship,
  and it is what allows a future licensed-data adapter (DC-11) or second platform (DC-10)
  without touching the pipeline.
- **Churn containment.** Upstream has already forced three kinds of patching (config
  vars, code injection for timeouts, UA regex — `crawler_runner.py:12-35`). Today those
  patches hard-fail with good messages, which is right; but the failure domain should be
  one adapter file with its own tests, not the module the whole pipeline imports.
- **Testability of the worst path.** Login/auth failure classification (the product's #1
  defect area) becomes a pure function over recorded fixtures instead of live-account
  archaeology.

**Non-goal.** No plugin marketplace, no dynamic loading — one Protocol, adapters chosen
by config. Right-sized for a local tool.

## 4. R2 — Job orchestration with progress & cancellation (P0 · DC-02; P1 · DC-08, UX-07)

**Change.** Replace "daemon thread + module Lock + boolean status" with a small
in-process `JobRunner` (stdlib only — no Celery/Redis, which would be absurd for a
single-user local app):

- One job at a time (same mutual exclusion), but jobs are **records**: id, stages,
  per-stage progress (counts tailed from provider telemetry), cancellation flag checked
  between stages + provider `cancel()` during crawl.
- Progress persisted onto `fetch_runs` (survives restart, feeds `/api/status`), pushed
  to the UI via SSE (UX-07; polling fallback kept).
- `main.py`'s two duplicated wrappers (`_run_cycle_locked` and `api_fetch`'s inline
  worker) collapse into one entry point used by both the endpoint and the scheduler.
- `infinance run` owns the uvicorn invocation and enforces the single-worker constraint
  (QA-02) instead of hoping.

**Justification.** Every P0 UX commitment about the fetch experience (progress, cancel,
login-state clarity) is impossible against a boolean. This is the smallest orchestration
model that delivers them; anything distributed is over-engineering for one machine.

## 5. R3 — Storage: keep SQLite, add migrations & aggregates (P0 · PL-02; P1 · DC-09)

**Change.**
- **Keep SQLite + WAL.** It is the correct database for a local single-writer app; no
  server dependency, trivially backed up (one file), already indexed for the query
  patterns (`db.py`). Postgres enters the picture only in the hosted sketch (§7).
- **Migrations:** ordered chain keyed on `PRAGMA user_version`; file-copy backup before
  upgrading; CI test that migrates a frozen v0 fixture. Connection setup stops running
  `executescript(SCHEMA)` per connect (wasted work; and `IF NOT EXISTS` is not a
  migration system).
- **Two-tier retention (DC-09):** raw third-party content stays short-lived (7 days —
  privacy and license posture), while *derived aggregates* (per-day per-ticker score,
  counts, lean, realized moves; analyses) become permanent. The scoreboard and trend
  features currently delete their own history at 30 days (`pipeline.py:86`) — the
  product's most defensible moat (a longitudinal record of crowd-vs-reality) is being
  discarded on schedule.

**Justification.** #4 in §2 is a release-blocking time bomb; the aggregate tier converts
runtime exhaust into compounding product value at negligible size (rows/day ≈ tickers).

## 6. R4 — Frontend platform: Vite + TypeScript + Preact (P0 · PL-06)

**Change.** Replace the single-file vanilla app with a small typed component app:
Vite build, Preact (~4 KB) + a file-per-view structure, ECharts as a pinned npm
dependency (closing the PL-05 "latest-from-mirror" drift), output bundled into
`static/dist` and served by FastAPI exactly as today — **zero runtime CDN preserved**.
State stays simple (fetch + signals); no router beyond a tiny hash/history helper; no
state-management library.

**Justification.**
- The P0 UX scope (multi-step wizard, routed detail view, themes, error surfaces, then
  P1 i18n/SSE/settings) is 3–5× today's UI surface. `innerHTML` string-building at that
  scale produces exactly the "not in a great place" UI being replaced — and it would be
  rewritten *twice* (once vanilla for release, once properly after).
- TypeScript matters here specifically: the API payloads are the contract between a
  Python backend and the UI (`/api/ranking`'s shape is already subtle — trend/quote/
  divergence/history nullability). Typed payload models catch the drift the current code
  can only discover at runtime as blank cards.
- Preact over React/Vue/Svelte: closest to vanilla mental model, smallest footprint,
  no build-ecosystem lock-in beyond Vite. Over "keep vanilla but split modules": tried
  calibration — the wizard + detail routing alone exceed what hand-rolled DOM code keeps
  maintainable for a solo developer.
- **Deliberately rejected:** SSR/Next (needless server complexity), heavy design systems
  (the current hand-rolled token aesthetic in `style.css` is good — formalize it, don't
  replace it).

## 7. Stage-3 sketch: hosted "community edition" (explicitly out of scope for this release)

Recorded so P0 decisions keep the door open, and to be honest about the barriers:

- **Legal is the gate, not engineering.** MediaCrawler's non-commercial license rules it
  out as a hosted backend. XHS ToS prohibits scraping; centralized collection
  concentrates that risk on the operator, and republishing user content/nicknames adds
  PII exposure (PIPL) that the local-first model avoids entirely. Path: licensed
  social-listening data or a platform partnership (DC-11 spike), with counsel sign-off
  as the go/no-go (TR-06).
- **Engineering shape if greenlit:** the `SourceProvider` swaps to a licensed-data
  adapter; SQLite → Postgres; JobRunner → a real queue; multi-tenant auth; the analysis
  and presentation layers move mostly unchanged — which is precisely why PL-03/R2/R3
  are designed as they are.
- **Interim community option with zero new legal surface:** the maintainer publishes
  read-only *derived* snapshots (scores, leans, hit rates — no third-party content) from
  their own instance, e.g. a static daily page. Worth considering as marketing for the
  local app.

## 8. Performance & scale notes (right-sizing)

| Hotspot | Today | Action threshold |
|---|---|---|
| Simhash dedup O(n²) (`dedup.py:33`) | ~hundreds of fresh notes → negligible | LSH banding (PL-08) only if multi-platform/longer windows reach n ≳ 5k |
| `/api/ranking` per-ticker analysis queries + frontend per-card fetches (`main.py:190`, `app.js:281`) | ~30 tickers → fine locally | Batch endpoint (AN-04) with UX-02, since redesign touches both sides anyway |
| Sequential LLM calls, 0.5s spacing (`analyze.py:229`) | 15 stocks ≈ 1–2 min, ~$0.03/cycle | Modest parallelism only if MAX_ANALYZED_STOCKS grows; cost is a non-issue |
| Connection-per-request SQLite + schema script per connect | Fine under WAL | Remove schema-per-connect with PL-02; nothing else needed |
| Crawl duration (10–15 min/cycle) | Dominated by deliberate politeness sleeps — **do not optimize**; it is account-safety budget (DC-03), not waste | — |

## 9. Security model (local app, public users)

- **Default:** bind `127.0.0.1`, no auth — unchanged.
- **Exposed mode (TR-02):** non-local bind requires `AUTH_TOKEN`; mutating endpoints
  (`/api/fetch`, tracked CRUD, alias accept — which writes a file) check it; startup
  banner states exposure.
- **Secrets (TR-03):** cookies/API keys never in argv, logs, or `doctor` output; `.env`
  stays gitignored; masked in UI settings (UX-08).
- **Content safety:** the UI already escapes all crawled text (`app.js esc()`); preserve
  the equivalent guarantee in the Preact rewrite (default JSX escaping + a lint rule
  against `dangerouslySetInnerHTML`).
- **Supply chain:** vendor pinned by commit (`setup.ps1:$MC_PIN` → provider preflight);
  frontend deps pinned by lockfile (PL-05/PL-06); CI builds artifacts reproducibly.

## 10. Risk register

| Risk | P | Impact | Mitigation |
|---|---|---|---|
| XHS anti-bot changes break collection | High | High | Provider abstraction + pinned vendor + structured telemetry to detect yield collapse (DC-08); politeness budgets (DC-03); honest docs (TR-04) |
| User accounts restricted | Med | High (for that user) | DC-03 defaults, cooldowns after auth errors, secondary-account guidance, visible request accounting |
| MediaCrawler upstream churn | High | Med | Commit pin + hard-failing patch contract (already good) + adapter isolation (PL-03) |
| LLM quality/format drift | Med | Med | Pydantic + retry (exists), verbatim-quote gate (AN-01), eval harness before prompt changes (AN-03), provider-agnostic base URL (exists) |
| Legal/ToS exposure | Low-Med | High | Local-first BYO-account model, no content redistribution, license boundary (TR-01), hosted path gated on counsel (TR-06) |
| Upgrade breakage in the field | High (without action) | High | PL-02 migrations + backup + CI fixture test |
| Solo-maintainer bus factor | High | Med | CI (QA-01), docs (TR-04), boring-tech choices throughout |
