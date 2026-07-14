# Feature Roadmap — MVP → Public Release

Every item carries a tag (`[Feature]` `[Bug Fix]` `[Refactor]` `[UI/UX]`), a strict
priority (P0 / P1 / P2), and an effort estimate for a solo maintainer:
**S** ≈ ≤1 day · **M** ≈ 2–4 days · **L** ≈ 1–2 weeks.

ID prefixes: **DC** data collection · **AN** analysis/summary quality · **UX** interface ·
**PL** platform/infrastructure · **TR** trust/compliance/security · **QA** quality/process.

## Guardrails — what we will NOT build

Per the fixed product decision (*the user decides the stock; we only summarize
information*), the following are out of scope at every priority level:

- Buy/sell/hold recommendations, price targets, or "AI stock picks" of any kind.
- Ranking or sorting by anything implying attractiveness — only by conversation volume,
  recency, or user-chosen order.
- Trade execution, broker integrations, or portfolio advice.
- Engagement-bait alerting ("don't miss out on…"). Notifications (P2) describe
  information events only, in neutral language.

---

## P0 — Critical: gates the public release (18 items)

| ID | Tag | Item | Effort | Depends on |
|---|---|---|---|---|
| PL-01 | [Refactor] | Cross-platform runtime (Windows + macOS) | M | — |
| PL-02 | [Refactor] | Versioned SQLite migrations | S | — |
| PL-03 | [Refactor] | `SourceProvider` abstraction around the crawler | M | — |
| PL-04 | [Feature] | Packaged one-command install & unified CLI | M | PL-01 |
| PL-05 | [Bug Fix] | Pin ECharts; resolve `static/` vendoring drift | S | — |
| PL-06 | [Refactor] | Frontend platform: Vite + TypeScript + Preact | M | PL-05 |
| DC-01 | [Feature] | In-app login & session health center | L | PL-03 |
| DC-02 | [Feature] | Cycle progress + cancellation | M | PL-03 |
| DC-03 | [Feature] | Account-safety guardrails on by default | S | — |
| AN-01 | [Feature] | Verbatim-quote verification | S | — |
| UX-01 | [UI/UX] | Onboarding wizard + bundled demo mode | M | PL-06, DC-01 |
| UX-02 | [UI/UX] | Dashboard redesign v2 (IA, detail view, themes, responsive) | L | PL-06 |
| UX-03 | [UI/UX] | Language & copy consistency pass | S | UX-02 |
| TR-01 | [Feature] | Compliance & disclaimer layer | S | — |
| TR-02 | [Feature] | Network security defaults | S | — |
| TR-03 | [Bug Fix] | Cookie hygiene (argv/log exposure) | S | — |
| TR-04 | [Feature] | Docs v1 (install, account safety, troubleshooting, upgrade) | M | PL-04 |
| QA-01 | [Refactor] | CI pipeline (Win+mac test matrix, lint, artifact) | S | — |

### PL-01 · [Refactor] · Cross-platform runtime — **M**
**What:** Replace `scripts/*.ps1` with a Python CLI (`infinance setup / login / run /
doctor`); replace the hardcoded `taskkill` process-tree kill (`crawler_runner.py:134`)
with `psutil`; platform-appropriate default user agent; path handling audit.
**Why:** The target audience is heavily on macOS (diaspora professionals/students). A
public release that is Windows-only, driven by PowerShell, forfeits half the audience and
reads as unfinished. Linux becomes best-effort (same code path, untested claim).
**Done when:** `uvx infinance setup && infinance login && infinance run` completes a real
cycle on a clean Windows 11 and macOS machine; no PowerShell anywhere in the happy path.

### PL-02 · [Refactor] · Versioned SQLite migrations — **S**
**What:** `PRAGMA user_version`-based ordered migration chain run at connect; automatic
pre-migration file backup; migration test fixture from a frozen v0 database.
**Why:** `db.py` runs `CREATE TABLE IF NOT EXISTS` on every connect — it can never ALTER
an existing table. The first post-release schema change (several P1 items need one) would
crash every existing install with an `OperationalError`. This is the single cheapest item
protecting release-and-iterate.
**Done when:** a copy of today's DB upgrades in place through ≥1 real migration in CI.

### PL-03 · [Refactor] · `SourceProvider` abstraction — **M**
**What:** Define the interface the pipeline actually consumes (`search(keywords, limits)
→ run_dir`, `login()`, `session_state()`, `cancel()`, structured result/telemetry) and
move all MediaCrawler-specific machinery — config regex patches, code patches, UA patch,
CLI construction, log heuristics (`crawler_runner.py:12-35`) — into a
`MediaCrawlerProvider`. Contract tests against recorded fixtures.
**Why:** (1) *License boundary:* MediaCrawler is non-commercial/learning-licensed; it must
stay a user-fetched, swappable dependency, never load-bearing in our public API surface.
(2) *Churn containment:* upstream rewrites regularly; today's blast radius is the whole
runner. (3) *Future sources* (second platform P2, licensed data P2) become adapters, not
rewrites. Justified fully in technical_architecture.md §3.
**Done when:** `pipeline.py` and `main.py` import only the interface; all patch/heuristic
code lives in one adapter module with its own tests.

### PL-04 · [Feature] · Packaged install & unified CLI — **M**
**What:** Publish as an installable package (pipx/uvx path); `infinance setup` performs
today's setup.ps1 duties (deps, clone+pin vendor, Playwright chromium, .env scaffold)
with progress and preflight checks; `infinance doctor` diagnoses common breakage (vendor
missing, Playwright absent, login expired, port busy).
**Why:** Setup friction is the #1 adoption killer for local tools. Today's flow (clone
repo → PS scripts → edit .env → uvicorn incantation) selects for developers only.
**Done when:** a non-developer following README installs and reaches demo mode in ≤10 min.

### PL-05 · [Bug Fix] · Pin ECharts; resolve static vendoring drift — **S**
**What:** `setup.ps1:47` downloads ECharts **"latest"** from npmmirror with an
**@5-pinned** jsdelivr fallback — two installs can get different major versions — while an
untracked `static/package.json` (`echarts ^6.1.0`, with node_modules) sits unused by
`index.html`. Pick one pinned source of truth (with PL-06: an npm dependency bundled at
build time), delete the rest, and commit or remove the stray package files.
**Why:** A major-version-dependent UI that installs differently per machine and per
mirror-availability is a latent support nightmare; the current repo state (untracked
manifest, unused node_modules) confuses contributors.
**Done when:** one pinned ECharts version, reproducible on both mirrors or vendored;
`git status` clean.

### PL-06 · [Refactor] · Frontend platform: Vite + TypeScript + Preact — **M**
**What:** Migrate `static/` (one 400-line `app.js` building HTML strings) to a small
Vite + TS + Preact app; FastAPI serves the built output; keep the zero-runtime-CDN
guarantee (everything bundled); ECharts as a pinned, tree-shaken dependency.
**Why:** P0 UX work (wizard with steps, a routed per-stock detail view, themes, i18n
scaffolding, live progress) cannot be responsibly built with `innerHTML` string
concatenation — it would be written once for release and rewritten immediately after.
Since UX-02 rewrites the frontend anyway, migrating *first* means writing it once.
Preact keeps the footprint tiny and the mental model close to the current code. Full
justification in technical_architecture.md §5.
**Done when:** current dashboard reaches feature parity on the new stack; `infinance run`
serves it with no external requests (verified in DevTools, as today).

### DC-01 · [Feature] · In-app login & session health center — **L**
**What:** A first-class login surface in the UI: current session state (valid / expired /
unauthorized), a "Login" button that drives the provider's visible-browser QR flow and
reports outcome, guided diagnosis distinguishing the three observed failure classes —
expired session, mainland-vs-RedNote backend mismatch (auto-suggest `XHS_INTERNATIONAL`
from log evidence), and platform-gated account (advice: stop retrying) — plus a
cookie-paste fallback form with validation (`a1=`/`web_session=` presence), replacing the
`.env` hand-edit.
**Why:** Login is the product's actual biggest defect right now: the current branch
(`fix/login-timeout`), the 120s-timeout code patch, and a third of the README are all
login triage. Public users get a red banner telling them to run a PowerShell script —
that is the moment they uninstall. Everything else in the app is worthless while the
session is dead, so this deserves the largest single P0 investment.
**Done when:** a user whose session expired recovers to a successful crawl entirely
inside the UI in ≤5 minutes; the three failure classes each show distinct guidance.

### DC-02 · [Feature] · Cycle progress + cancellation — **M**
**What:** Persist a per-run `progress` state (stage + counts) updated by the pipeline;
derive crawl-stage counts by tailing the crawler log/JSONL output; expose in
`/api/status`; UI shows a stage bar ("crawling 41/120 notes · keyword 3/10 → ingest →
analyze 4/15") with elapsed time and a **Cancel** button (provider `cancel()` = existing
kill-tree, then the run finalizes as `failed/cancelled` cleanly).
**Why:** A 10–15 minute opaque spinner (today: `fetchBtn` disabled + 3s polling of a
boolean) reads as "hung" to any new user; cancellation is basic respect for the user's
account (abort a mistaken fetch instead of burning request budget).
**Done when:** every stage transition is visible within 5s; cancel leaves DB and lock
state consistent (no stuck `running` rows).

### DC-03 · [Feature] · Account-safety guardrails on by default — **S**
**What:** A per-day request budget and a minimum gap between cycles (manual fetches
within the gap require an explicit confirm); automatic cooldown with visible countdown
after auth-error runs (stop hammering a flagged session — the README already warns
retries raise risk score); a per-cycle request counter surfaced in the runs panel.
**Why:** The MVP's safety posture (low volume, concurrency 1, 3s sleeps) is static and
invisible. Public users will click "Fetch now" repeatedly and re-login in loops —
exactly the behaviors that get accounts restricted. Protecting the user's account is a
product feature, and it must be *defaults*, not README advice.
**Done when:** back-to-back manual fetches are gated; a `login_required` run triggers a
cooldown banner instead of allowing immediate retry.

### AN-01 · [Feature] · Verbatim-quote verification — **S**
**What:** After Pydantic validation (`analyze.py:185`), check each `notable_quote` is a
(normalized) substring of the input items; drop non-matching quotes, log the event, and
fall back to top-liked real comments if all are dropped. Same check in `slang_scan`
evidence quotes.
**Why:** Quotes are the product's proof layer — the thing users check the summary
against. An LLM-paraphrased "quote" presented as verbatim is a fabrication; one public
screenshot of a fake quote destroys the credibility the scoreboard exists to build.
Cheapest trust win in the backlog.
**Done when:** no rendered quote fails a substring check against its analysis inputs
(property-tested with adversarial fixtures).

### UX-01 · [UI/UX] · Onboarding wizard + bundled demo mode — **M**
**What:** First-run wizard: environment checklist (vendor present, browser installed,
session state, LLM key optional) with fix-it actions → guided first login (DC-01) →
first crawl with progress (DC-02). Until real data exists, the dashboard renders a
bundled **synthetic demo dataset** (clearly watermarked "示例数据 DEMO") exercising every
feature: ranking, cards, trends, divergence, radar, scoreboard.
**Why:** Today's first run is a blank page with an empty-state hint, a QR scan in a
separate terminal window, and a 15-minute wait before *anything* renders. Demo mode
proves the value proposition in minutes, decoupling "is this tool for me?" from "did my
login work?". Synthetic data avoids redistributing any real XHS content (license/privacy
clean).
**Done when:** fresh install → understandable, fully-populated (demo) dashboard in ≤2
minutes; wizard completion leads to a real first cycle without touching a terminal.

### UX-02 · [UI/UX] · Dashboard redesign v2 — **L**
**What:** Information architecture: a "market pulse" home (ranking chart, movers/trend
strip, divergence callouts, radar) and a **routed per-stock detail view** (today a card
grid is the only depth): summary → bull/bear → verified quotes with source links →
sparkline + price context → all source items. Design tokens; **light + dark themes**;
responsive layout audited on mobile widths; skeleton loading states; toast/error surfaces
(today every fetch failure dies in `console.error` — `app.js:400`); empty states per
panel; consistent badge system (heat=amber, sentiment=green/red kept, as today).
**Why:** This is the half of the stated MVP gap that users *see*. The data pipeline is
more sophisticated than the page presenting it; screenshots are the product's only
marketing. The detail view also creates the natural home for P1 evidence-linking and
history features.
**Done when:** design tokens documented; mobile-width usable; every API failure surfaces
visibly; a per-stock URL is shareable (locally).

### UX-03 · [UI/UX] · Language & copy consistency pass — **S**
**What:** Choose Chinese as the v1 interface language (audience-first; English UI arrives
with P1 i18n); sweep all mixed-language strings ("Fetch now" beside 抓取记录, English
table headers in a Chinese UI); rewrite every error message to state what happened and
the next action; align terminology (帖/评/热度/舆论) across UI, README, and prompts.
**Why:** Mixed-language copy reads as unfinished and erodes trust in an information
product. Cheap, high perceived-quality delta.
**Done when:** a zh-native reviewer finds no mixed-register strings; all errors carry a
next step.

### TR-01 · [Feature] · Compliance & disclaimer layer — **S**
**What:** Persistent non-advice disclaimer (仅供参考，不构成投资建议 / informational
only, not investment advice) on every analytical surface (cards, detail, scoreboard,
future exports); first-run acknowledgment in the wizard; LICENSE file for our code (MIT
recommended); explicit in-repo statement of the MediaCrawler boundary (fetched at setup
under its own non-commercial license, never redistributed; personal, low-volume use);
data-age always visible (already largely true — keep it a rule).
**Why:** A public finance-adjacent tool without disclaimers is negligent, and the
"information, not advice" principle should be *visible product copy*, not just an
internal decision. The license boundary is what keeps the distribution model clean.
**Done when:** legal-review-ready checklist passes; no analytical surface renders without
age + disclaimer.

### TR-02 · [Feature] · Network security defaults — **S**
**What:** Refuse to bind non-`127.0.0.1` unless `AUTH_TOKEN` is set; when set, require
the token on all mutating endpoints (`/api/fetch`, tracked CRUD, alias accept/reject) and
add basic origin checking; startup warning when exposed.
**Why:** Today the server is honest localhost-only (`config.py:55`), but public users
*will* set `HOST=0.0.0.0` to check the dashboard from their phone — instantly exposing
unauthenticated endpoints that trigger crawls with *their* XHS account and write to disk
(alias overlay). Guard the foot-gun before strangers hold it.
**Done when:** non-local bind without a token exits with a clear message; mutating
endpoints 401 without the token when bound non-locally.

### TR-03 · [Bug Fix] · Cookie hygiene — **S**
**What:** `XHS_COOKIES` is passed as a CLI argument (`crawler_runner.py:106`), making a
live session cookie visible in the OS process list and at risk of appearing in logs/error
reports. Deliver it via the provider's config-patch mechanism (or env var) instead;
audit logging paths to ensure cookie values are never persisted; redact in `doctor`
output.
**Why:** The `.env.example` already (correctly) tells users to treat the cookie like a
password; the implementation should honor that. Session theft = account takeover.
**Done when:** cookie value appears in no argv, no log file, no crash output (test greps
run artifacts).

### TR-04 · [Feature] · Docs v1 — **M**
**What:** Restructure the (already strong) README troubleshooting knowledge into user
docs: 10-minute install guide (Win/mac), account-safety guide (what the tool does and
doesn't do with your account, realistic risk framing, secondary-account suggestion),
login troubleshooting decision tree (the three failure classes), upgrade guide
(migrations + vendor re-pin), FAQ, and a "how it works" page (the pipeline table).
**Why:** The knowledge exists but lives in one dense README written for its author.
Public releases are judged by their first 10 minutes and their worst failure mode — both
are documentation problems.
**Done when:** docs cover install → first cycle → recovery → upgrade without reading code.

### QA-01 · [Refactor] · CI pipeline — **S**
**What:** GitHub Actions: pytest on Windows + macOS (the suite is already
subprocess-free), lint (ruff), type check for the new TS frontend, build artifact, and
the PL-02 migration test.
**Why:** Cross-platform claims (PL-01) are only real if tested per-commit; a public repo
without CI signals abandonment.
**Done when:** green matrix badge on README; failing tests block merge to `main`.

---

## P1 — High priority: first post-release waves (16 items)

| ID | Tag | Item | Effort |
|---|---|---|---|
| DC-04 | [Feature] | Context-note ingestion (fresh comments on older notes) | M |
| DC-05 | [Feature] | Keyword pack manager + per-keyword yield analytics | M |
| DC-06 | [Feature] | Market-aware adaptive scheduling | S |
| DC-07 | [Feature] | Slang discovery v2 (time-based cadence, preview, precision) | S |
| DC-08 | [Refactor] | Structured crawler telemetry | M |
| DC-09 | [Feature] | Long-horizon aggregate retention | S |
| AN-02 | [Feature] | Evidence-linked bull/bear points | M |
| AN-03 | [Feature] | Detection & summary eval harness | M |
| AN-04 | [Refactor] | Batched dashboard payload (kill the N+1s) | S |
| UX-04 | [UI/UX] | History & trend views | M |
| UX-05 | [UI/UX] | Scoreboard v2 (calibration + honest framing) | M |
| UX-06 | [UI/UX] | Full i18n (zh/en interface toggle) | M |
| UX-07 | [UI/UX] | Live pipeline activity via SSE | S |
| UX-08 | [UI/UX] | In-app settings | M |
| TR-05 | [Feature] | Data export (CSV/JSON) | S |
| QA-02 | [Bug Fix] | Single-process enforcement | S |

### DC-04 · [Feature] · Context-note ingestion — **M**
`ingest.py:78` drops any comment whose parent note is older than 24h — but a fresh
comment on a 3-day-old viral note is *exactly* the fresh crowd signal the product
promises. Ingest such notes flagged `context_only` (excluded from note counts/scores);
their fresh comments count normally and reach the LLM with note-title context. Requires
PL-02 (schema change). Measurably widens recall without raising crawl volume.

### DC-05 · [Feature] · Keyword pack manager + yield analytics — **M**
Discovery recall is bounded by a hardcoded keyword list (`config.py:18`). Track fresh-note
yield per keyword per run (data already exists via `source_keyword`); show it; let users
enable/disable curated packs (财报季, ETF/杠杆, 中概, 宏观) and add custom keywords with
the cost warning the `.env.example` comment already articulates (cycle cost = keywords ×
notes-per-keyword). Data-driven recall tuning instead of .env archaeology.

### DC-06 · [Feature] · Market-aware adaptive scheduling — **S**
A fixed `FETCH_INTERVAL_HOURS=5` treats Saturday 4am and earnings-night open identically.
Schedule against the US market calendar (denser near open/close and on weekdays, sparse
weekends) under the same daily request budget (DC-03). Descriptive freshness where it
matters, no added account risk.

### DC-07 · [Feature] · Slang discovery v2 — **S**
`SLANG_SCAN_EVERY_N_CYCLES=20` ≈ once per ~4 days at the default cadence — too slow for
censorship-dodging slang whose whole point is churn. Make it time-based (daily, budgeted);
add an accept-preview ("this alias would have matched N recent posts: …") so review
decisions are informed; track per-suggestion precision (accepted alias's subsequent
match_basis distribution) to ground the P2 question of whether auto-accept is ever safe.

### DC-08 · [Refactor] · Structured crawler telemetry — **M**
Login detection is substring-matching Chinese log lines (`crawler_runner.py:32`) — it
breaks silently if upstream rewords a message. Wrap the provider run with a thin driver
that emits structured JSONL events (note fetched, auth error, rate limit, done) parsed
from defined observation points; heuristics remain only as fallback. Feeds DC-01/DC-02
displays and makes failure classification testable.

### DC-09 · [Feature] · Long-horizon aggregate retention — **S**
Cleanup currently deletes analyses and snapshots after 30 days (`pipeline.py:86-88`) —
the scoreboard and trend features erase their own long-term value. Keep per-day
per-ticker aggregates (score, counts, sentiment, lean, realized moves) indefinitely
(tiny rows); raw content still prunes at 7 days (privacy + size posture unchanged).
Requires PL-02.

### AN-02 · [Feature] · Evidence-linked bull/bear points — **M**
Have the LLM cite supporting item ids per bull/bear point; UI renders each point
expandable to its verified source quotes/links (into UX-02's detail view). Where AN-01
verifies quotes, this verifies *reasoning* — the strongest possible expression of
"we summarize, you judge."

### AN-03 · [Feature] · Detection & summary eval harness — **M**
Quality is currently asserted, not measured. Build a labeled golden corpus from
accumulated real data (mention precision/recall — 苹果-the-fruit cases, collision
tickers; sentiment agreement on a hand-labeled sample; summary faithfulness
spot-checks). Run in CI on every dictionary or prompt change. Prerequisite for safely
touching the prompt (AN-02, UX-06's zh summaries) and for the P2 auto-accept question.

### AN-04 · [Refactor] · Batched dashboard payload — **S**
`/api/ranking` runs a per-ticker `_latest_analysis` query in a loop (`main.py:190`) and
the frontend then fetches `/api/stocks/{ticker}` per card (`app.js:281`) — ~30 requests
and ~60 queries per refresh. One batched endpoint returning ranking + card payloads.
Matters once UX-02 makes refreshes frequent; trivial at current volume, so P1 not P0.

### UX-04 · [UI/UX] · History & trend views — **M**
The data for real trend pages already exists (`score_snapshots`, analyses): per-ticker
heat/sentiment timelines, "when did the crowd flip on TSLA," daily top-5 movers. With
DC-09, extends from 30 days to product lifetime. Sparklines today tease this; a detail
tab delivers it.

### UX-05 · [UI/UX] · Scoreboard v2 — **M**
The hit-rate scoreboard is the product's most differentiated honesty feature, currently a
collapsed table. Give it: calibration view (hit rate by lean strength), per-ticker
reliability, pending-call visibility, minimum-sample gating (no "100%" off 3 calls), and
explicitly neutral framing copy ("descriptive record of the crowd's past leans — not a
prediction, not advice"). Guardrail-compliant by design.

### UX-06 · [UI/UX] · Full i18n — **M**
String catalog + zh/en interface toggle, synced with `SUMMARY_LANG` (quotes always stay
原文). The bilingual diaspora persona is first-class (the default summary language is
already English); the interface should match. Builds on UX-03's cleanup.

### UX-07 · [UI/UX] · Live pipeline activity via SSE — **S**
Replace 3s/30s polling (`app.js:54`) with an SSE stream of DC-02/DC-08 events; instant
banner/progress updates, less chatter, groundwork for future desktop notifications.

### UX-08 · [UI/UX] · In-app settings — **M**
Editable settings surface (keywords via DC-05, interval, language, LLM key with masked
input + connection test, price toggle) persisted server-side; `.env` remains for power
users. Public users should never hand-edit dotfiles to change a refresh interval.

### TR-05 · [Feature] · Data export — **S**
CSV/JSON export of ranking snapshots, analyses, and mention-level data (own-use framing;
excludes raw third-party content bodies beyond quotes already shown). Serves the
creator/quant personas and honors "user decides" by letting them take the data to their
own tools.

### QA-02 · [Bug Fix] · Single-process enforcement — **S**
`fetch_lock`, `quotes_lock`, and the APScheduler instance are module-level
(`main.py:22`) — running `uvicorn --workers 2` silently breaks mutual exclusion
(concurrent crawls = account risk) and double-schedules. Document, and fail fast at
startup when `workers > 1` is detected (or move the guard into `infinance run` which owns
the uvicorn invocation).

---

## P2 — Low priority: future considerations (12 items)

| ID | Tag | Item | Notes |
|---|---|---|---|
| DC-10 | [Feature] | Second platform adapter (Weibo or Bilibili) | Only after XHS collection is proven stable; PLAN already deferred it — doubles login/anti-bot surface. Rides PL-03. |
| DC-11 | [Feature] | Licensed-data provider spike | Precondition for any hosted edition (TR-06): evaluate commercial social-listening data vendors / partnerships as a `SourceProvider`. Legal first, code second. |
| AN-05 | [Feature] | Author credibility weighting | Discount serial reposters, up-weight historically-early accounts. Needs weeks of author history (nickname-keyed) + DC-09; deferred in PLAN for the same reason. |
| AN-06 | [Feature] | LLM provider presets + local-model guide | `LLM_BASE_URL` already allows any OpenAI-compatible endpoint; add tested presets (DeepSeek/Kimi/Qwen/OpenAI) and an Ollama guide for fully-offline privacy purists. |
| AN-07 | [Feature] | Theme/sector rollups | Descriptive clusters ("AI硬件", "中概", "减肥药") aggregating per-ticker data — still volume-ranked, no attractiveness signal (guardrail-checked). |
| UX-09 | [UI/UX] | PWA + information-only notifications | Opt-in, neutral-language events ("NVDA 新上榜", "TSLA 舆论与股价背离"); no urgency framing (guardrail). |
| UX-10 | [UI/UX] | Share-card image export | Per-stock snapshot image with embedded data-age + disclaimer + attribution — the creator persona's growth loop back onto XHS/WeChat. |
| UX-11 | [UI/UX] | Accessibility deep pass | Chart table-fallbacks, contrast audit of the badge system, keyboard navigation, reduced-motion. |
| PL-07 | [Feature] | Desktop shell (Tauri) | One-click app bundling server + UI + tray; revisit if PL-04 CLI adoption shows non-technical demand. |
| PL-08 | [Refactor] | Dedup LSH banding | `dedup.py:33` is O(n²) pairwise simhash — fine at ≤ a few hundred fresh notes; band into buckets only when multi-platform/longer windows push n into the thousands. |
| TR-06 | [Feature] | Hosted "community edition" go/no-go | A read-only hosted dashboard requires licensed data (DC-11) or first-party collection with counsel sign-off; MediaCrawler's license rules it out for hosting. Deliverable is a decision memo + architecture sketch (technical_architecture.md §7), not code. |
| QA-03 | [Bug Fix] | Doc/comment drift cleanup | `main.py:29` and `app.js:79` say "stooq" but `prices.py` uses Yahoo; scoreboard's zero-move handling (`scoreboard.py:64`) counts flat closes as misses — document or exclude. Cosmetic bundle. |

---

## Dependency & sequencing notes

- **PL-02 (migrations) unblocks** every schema-touching item: DC-04, DC-09, DC-02
  (progress column), UX-08. Do it first; it is small.
- **PL-03 (provider) unblocks** DC-01, DC-02, DC-08, and both P2 adapters. Its cost is
  mostly moving existing code behind an interface.
- **PL-06 (frontend platform) precedes** UX-01/UX-02 so the redesign is written once.
- **DC-03 + DC-06 share** the request-budget mechanism; build the budget in DC-03,
  reuse it for scheduling.
- **AN-03 (evals) should land before** any further prompt/dictionary iteration beyond
  AN-01/AN-02 — otherwise quality changes are unverifiable.
