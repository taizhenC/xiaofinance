# Executive Summary

## What this product is

**xiaofinance** answers one question in under a minute: *"Which US stocks is the Chinese
retail crowd on Xiaohongshu talking about right now — and what are they actually saying?"*

The MVP proves the full concept loop end-to-end: crawl XHS with the user's own account
(via MediaCrawler, QR login, no API key) → keep only the last 24 hours → collapse repost
spam → detect stock mentions with a slang-aware Chinese dictionary (248 tickers, 谐音/黑话
like 老黄→NVDA, 苏妈→AMD, with an ambiguity gate for 苹果/阿里/LI) → score popularity →
have DeepSeek distill per-stock crowd sentiment into a summary, bull/bear points, and
verbatim quotes → visualize it on a local FastAPI + ECharts dashboard, cross-checked
against real price moves (divergence flags, a crowd hit-rate scoreboard).

**No comparable tool exists.** Western sentiment trackers (ApeWisdom, StockTwits-based
tools) can't see Chinese social platforms; Chinese tools focus on A-shares. XHS has no
official API. This product makes an invisible, fast-moving retail signal legible — and,
uniquely, it tells you how reliable that signal has historically been.

**Product principle (fixed):** the app summarizes information and measures the crowd's
track record. It never recommends. The user decides.

## Where the MVP stands (honest assessment)

The pipeline architecture is genuinely solid for a two-day-old MVP: staged modules,
freshness enforced twice, dedup before scoring and LLM analysis, provenance on every
mention, per-ticker error isolation, cost tracking, and 14 test files. The two known
weaknesses match what the code shows:

1. **Data collection is the fragile half.** Login/session failures dominate the recent
   commit history (`fix/login-timeout` branch, 120s timeout patches, mainland-vs-RedNote
   backend confusion, cookie-paste fallback). Setup is Windows-only PowerShell; login
   requires a terminal; a 10–15 minute crawl cycle shows only a spinner; the crawler is
   integrated by regex-patching a vendored repo's source files.
2. **Presentation is developer-grade.** One dark-only page, mixed Chinese/English copy,
   no onboarding (a blank dashboard until the first successful crawl ~15+ minutes in),
   silent error handling, no per-stock detail view, untested on mobile.

## The strategic call for the next stage

**Ship a polished, local-first, bring-your-own-account public release (free, open
source) — not a hosted service.** Rationale:

- **Legal posture.** MediaCrawler is licensed for non-commercial learning use, and XHS
  ToS prohibits scraping. A hosted service would centralize that risk on the operator and
  requires either a first-party crawler fleet or licensed data. Local-first keeps usage
  personal (each user's own account, low volume), which is the posture the MVP was
  correctly designed around — the app never even redistributes content beyond the user's
  machine.
- **Cost & focus.** Zero server fleet, zero account farm, zero multi-tenant auth. All
  investment goes into the two stated weaknesses: the collection pipeline and the UX.
- **Optionality preserved.** The P0 `SourceProvider` abstraction and the P2 licensed-data
  spike keep a hosted "community edition" possible later without rework.

## Headline P0 themes (18 items — see feature_roadmap.md)

1. **Installable by normal humans** — cross-platform (Windows + macOS) Python CLI
   replacing PowerShell scripts; one-command packaged install; pinned frontend deps; CI.
2. **Login stops being the product's biggest bug** — in-app login & session health
   center with guided diagnosis (the #1 observed failure mode), cycle progress with
   cancellation, account-safety budgets on by default.
3. **First-run experience** — onboarding wizard + bundled demo dataset so the dashboard
   demonstrates its value in 2 minutes, before the first real crawl.
4. **A dashboard worth screenshotting** — information-architecture redesign, per-stock
   detail view, light/dark themes, responsive layout, consistent language, real error
   surfaces; frontend migrated to Vite + TypeScript + Preact to carry this and everything
   after it.
5. **Trust hardening** — verbatim-quote verification (no fabricated quotes, ever),
   schema migrations so upgrades never brick user databases, security defaults
   (localhost-only unless authenticated), cookie hygiene, disclaimers and license
   boundaries in-product.

## Release gate (v1.0 ships when all are true)

- [ ] Fresh-machine install on Windows 11 and macOS reaches **demo mode ≤ 10 min** and a
      **first real cycle ≤ 30 min**, without opening a terminal after install.
- [ ] 48-hour unattended soak: ≥ 95% cycles succeed; login expiry surfaces as a guided
      in-app flow, not a stack trace.
- [ ] A v0-schema database upgrades in place, automatically, with a pre-migration backup.
- [ ] Every analytical surface carries data-age and the non-advice disclaimer; all quotes
      shown are verified verbatim from source items.
- [ ] Server refuses non-localhost binding without an auth token; no secrets in argv,
      logs, or crash reports.
- [ ] LICENSE chosen and published; MediaCrawler license boundary documented (fetched at
      setup, never redistributed); account-safety guide written.
- [ ] CI green on the Windows + macOS test matrix.

## Success metrics (first 60 days post-release)

| Metric | Target |
|---|---|
| Activation: installs reaching a first successful cycle | ≥ 70% |
| Time-to-first-insight (demo mode) | ≤ 2 min |
| Crash-free cycle rate (telemetry-free: from issue reports + soak) | ≥ 95% |
| Login-recovery: expired session → crawling again, guided in-app | ≤ 5 min |
| Fabricated-quote reports | 0 |
| Engagement proxy: watchlist adds per active user | ≥ 2 |

## Sequencing at a glance (solo maintainer, ~6–8 weeks)

- **Phase A — Foundations (wk 1–2):** PL-01…06, TR-02, TR-03, QA-01 (platform, migrations,
  provider abstraction, packaging, frontend scaffold, security defaults, CI).
- **Phase B — Experience (wk 3–5):** DC-01…03, UX-01…03, AN-01, TR-01, TR-04 (login
  center, progress, wizard + demo, redesign, quote verification, compliance, docs).
- **Phase C — Release (wk 6):** beta soak with 5–10 external testers, gate checklist,
  v1.0 tag.
- **Post-release:** P1 in two waves — data coverage (DC-04…09) then trust & depth
  (AN-02…04, UX-04…08, TR-05).
