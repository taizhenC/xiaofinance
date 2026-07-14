# Product Analysis — deduced from the codebase

Everything below is inferred from the code, schema, prompts, config, and docs in this
repository (reviewed 2026-07-12 at commit `6285a85`).

## 1. Core features (as implemented today)

| Feature | Evidence | Maturity |
|---|---|---|
| **XHS crawling with the user's own account** — QR or cookie login, discovery keywords + targeted per-ticker searches, time-sorted, subprocess-isolated with kill-tree timeout | `app/crawler_runner.py`, `scripts/login_xhs.ps1` | Working but fragile (login failures dominate recent commits) |
| **24-hour freshness guarantee** — enforced at ingest AND at every query, so items age out continuously between fetches | `app/ingest.py:39`, `app/scoring.py:23`, PLAN "hard requirement" | Solid |
| **Repost/copypasta collapsing** — 64-bit simhash union-find clustering for notes, normalized exact-match for comments; clusters count once in scoring and reach the LLM as one item tagged `[×N相似]` | `app/dedup.py`, `app/analyze.py:91` | Solid; O(n²) fine at current volume |
| **Slang-aware mention detection** — 248 tickers, 175 finance context words, 70 collision tickers; three match strengths (safe alias / alias+context / ticker symbol) plus targeted-search provenance; word boundaries for Latin, substring for CJK | `app/mentions.py`, `app/data/stock_dict.json` | Good precision design; recall bounded by dictionary |
| **Self-improving dictionary** — periodic LLM mining of unmatched finance posts for new 谐音/黑话 nicknames → human-reviewed suggestions → overlay dict (never auto-added) | `app/slang_scan.py`, `data/stock_dict_local.json` | Working; cadence too slow (every 20 cycles ≈ 4 days) |
| **LLM sentiment distillation** — DeepSeek JSON mode per stock: discards off-topic items (noise count surfaced in UI), weighs distinct arguments over repetition, outputs summary / ≤4 bull / ≤4 bear / ≤3 verbatim quotes; sees previous cycle's summary as guarded compare-only context; skips when inputs unchanged; keyless fallback shows top quotes | `app/analyze.py` | Working; quality unverified (no evals), quotes unvalidated |
| **Popularity scoring & trends** — `3·notes + 1·comments + 2·Σlog₁₀(1+note likes) + 0.5·Σlog₁₀(1+comment likes)` over clusters; per-cycle snapshots power 🔥/↑/↓ trend badges and sparklines | `app/scoring.py` | Solid |
| **Price reality check** — free Yahoo daily closes; per-card price badge; 🔀 divergence flag when crowd lean and price move conflict | `app/prices.py`, `app/main.py:194` | Solid |
| **Crowd hit-rate scoreboard** — every clear daily lean (\|bull−bear\| ≥ 2) is later scored against 1-day and 7-day realized moves; overall and per-ticker hit rates | `app/scoreboard.py` | Working; presentation minimal |
| **Watchlist ("tracked")** — always-rendered tickers with custom Chinese keywords, finance-qualified targeted queries | `app/mentions.py:159` | Solid |
| **On-the-radar strip** — sub-threshold tickers stay discoverable without LLM spend | `app/scoring.py:147` | Solid |
| **Ops surface** — runs panel, login-expiry banner, retention cleanup (raw 7d / analyses 30d), per-analysis token cost tracking | `app/pipeline.py:72`, `app/main.py` | Adequate for a personal tool |

## 2. Unique value proposition

1. **An invisible signal, made visible.** Chinese retail investors — one of the most
   active retail cohorts in US equities and ADRs — coordinate on platforms Western
   tooling cannot see. XHS has no API. This is, to our knowledge, the only tool that
   turns XHS finance chatter into a structured, deduplicated, per-ticker view.
2. **Fluent in 黑话.** Detection understands censorship-dodging nicknames and
   ambiguity (苹果 the fruit vs AAPL; 老黄 needs finance context), and it *learns new
   slang over time* with a human veto. A naive keyword scraper cannot replicate this.
3. **Skeptical by construction.** Repost collapsing means consensus reflects distinct
   arguments, not the loudest copy-paste; the model reports how much noise it discarded;
   sentiment is cross-checked against actual price moves; and the scoreboard openly
   answers "is this crowd ever right?" — a credibility feature no hype-tracker offers.
4. **Information, not advice — structurally.** Ranking is by conversation volume only.
   Summaries describe what people say, with verbatim quotes and source links for
   verification. The decision stays with the user (fixed product principle).
5. **Local-first and private.** The user's account, machine, and data. The only external
   calls are the optional LLM API and optional free price quotes.

## 3. Mission (proposed articulation)

> **让美股散户看清中文社区此刻在想什么 —— 只呈现，不建议。**
> Make the Chinese-speaking retail crowd's view of US stocks legible in minutes:
> summarize the conversation faithfully, measure its track record honestly, and leave
> the decision entirely to the user.

## 4. Overarching direction

- **Stage 1 (done, this MVP):** prove the loop — crawl → detect → dedup → summarize →
  visualize — reliably enough for its own author to use daily.
- **Stage 2 (this roadmap):** a **public local-first release** — installable by
  non-developers on Windows and macOS, resilient collection, a presentation layer that
  matches the quality of the data pipeline. Free, open source; the crawler dependency
  stays user-fetched to respect its non-commercial license.
- **Stage 3 (exploratory, gated on Stage 2 traction):** depth and reach — longer
  history, a second platform behind the provider abstraction, richer creator/export
  workflows, and a go/no-go on a hosted read-only "community edition" (requires licensed
  data or first-party collection; see technical_architecture.md §7).

## 5. Target audience

**Primary — Chinese-speaking retail investors in US equities.**
- *Mainland-based:* trade via 富途/老虎/长桥; already browse XHS finance posts daily;
  want the chatter distilled instead of doomscrolled. The DeepSeek dependency (domestic,
  cheap, strongest-in-class Chinese) fits this user perfectly.
- *Diaspora (US/CA/SG/AU students & professionals):* trade via Robinhood/IBKR; culturally
  attached to XHS; often on the international RedNote backend — which the code already
  explicitly supports (`XHS_INTERNATIONAL`, `.env.example:20`). The default
  `SUMMARY_LANG=en` with quotes kept in Chinese is strong evidence this bilingual reader
  is a first-class persona.

**Secondary — bilingual analysts, finance content creators, and quant-curious hobbyists**
who want a differentiated alt-data lens on US names and Chinese ADRs (BABA/PDD/NIO…),
material for posts, or exportable data to test against.

**Anti-persona:** anyone seeking trade signals or automation. The product will
deliberately not serve them (see the product principle).

## 6. Known limitations (acknowledged, and what this roadmap does about them)

| Limitation | Root cause in code | Roadmap response |
|---|---|---|
| Collection is fragile: login expiry, mainland-vs-RedNote auth splits, opaque 10–15 min cycles, Windows-only setup, brittle vendored-crawler patching | `crawler_runner.py` (regex patches, `taskkill`, log string-matching), PS-only scripts, thread-with-spinner job model | P0: DC-01 login center, DC-02 progress/cancel, PL-01 cross-platform, PL-03 provider abstraction; P1: DC-08 structured telemetry |
| Recall bounded: fixed keywords, top-20 comments per note, fresh-note-anchored ingestion drops fresh comments on older viral notes, slang scan every ~4 days | `config.py:18-25`, `ingest.py:78`, `config.py:43` | P1: DC-04 context notes, DC-05 keyword packs w/ yield analytics, DC-06 adaptive scheduling, DC-07 slang v2 |
| Presentation developer-grade: no onboarding, blank first run, dark-only, mixed zh/en, silent errors, no detail view, no mobile | `static/` (one page, 400-line vanilla JS, `catch(console.error)`) | P0: UX-01 wizard + demo mode, UX-02 redesign, UX-03 copy pass, PL-06 frontend platform |
| Trust unproven: summaries and quotes unvalidated, no eval harness | `analyze.py` (single call, Pydantic shape-check only) | P0: AN-01 verbatim quotes; P1: AN-02 evidence links, AN-03 eval harness |
| Not upgrade-safe or share-safe: schema can't evolve, no auth if bound beyond localhost, cookies on argv | `db.py:154` (`CREATE IF NOT EXISTS` only), `main.py` (no auth), `crawler_runner.py:106` | P0: PL-02 migrations, TR-02 security defaults, TR-03 cookie hygiene |
