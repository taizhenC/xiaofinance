# infinance — XHS (小红书) US-Stock Sentiment Dashboard — Implementation Plan

## Context

A local personal tool that answers "which US stocks are hot on Xiaohongshu right now, and what do people think of them?" XHS has no official API, so data comes from the open-source **MediaCrawler** (github.com/NanmiCoder/MediaCrawler) using your own XHS account (QR login). A local dictionary detects stock mentions; the DeepSeek API (OpenAI-compatible) summarizes per-stock views ("AAPL: <summary>..."). A FastAPI + ECharts web dashboard visualizes popularity ranking and per-stock cards, supports manually tracked tickers, a "Fetch now" button, and scheduled refresh.

**Hard requirement:** only data ≤ 24h old is analyzed/shown — enforced at BOTH ingestion and query time; all timestamps stored as UTC epoch ms, displayed in local time, with visible data-age badges.

**Confirmed decisions:** MediaCrawler (not paid API) · US stocks only (incl. major US-listed Chinese ADRs like BABA/PDD/NIO and retail-favorite ETFs like QQQ/TQQQ/SOXL) · Hybrid analysis (dictionary detection + DeepSeek `deepseek-chat` summarization, keyless fallback = quotes only) · FastAPI + ECharts dashboard.

Project root: `C:\Coding\Test\xiaofinance` (this repo — greenfield apart from PLAN.md). Machine: Windows 11; setup script installs `uv` if missing.

## Verified MediaCrawler facts (drive the integration)

- Python 3.11+, installed with `uv sync` inside its dir; Playwright chromium via `uv run playwright install chromium`.
- CLI: `uv run main.py --platform xhs --lt qrcode --type search` with flags `--keywords` (comma-sep), `--save_data_option jsonl`, `--save_data_path <dir>`, `--get_comment yes/no`, `--get_sub_comment yes/no`, `--crawler_max_notes_count N`, `--max_comments_count_singlenotes N`, `--start N`, `--headless yes/no`, `--max_concurrency_num N`. CLI args override `config/*.py` globals.
- **Sort order is config-file-only**: `config/xhs_config.py` → `SORT_TYPE`; must patch to `"time_descending"` (default is `popularity_descending`).
- JSONL output: `{save_data_path}/xhs/jsonl/search_contents_{YYYY-MM-DD}.jsonl` + `search_comments_{date}.jsonl`, append-per-item.
- Note fields: `note_id`, `title`, `desc`, **`time` (epoch ms publish)**, `liked_count`/`comment_count`/etc. (Text, may be `"1.2万"`), `note_url`, **`source_keyword`**, `nickname`. Comment fields: `comment_id`, `note_id`, `content`, **`create_time` (epoch ms)**, `like_count`, `parent_comment_id`, `sub_comment_count`.
- QR login cached via `SAVE_LOGIN_STATE=True` in a browser profile dir inside MediaCrawler — scan once, reuse silently.
- Upstream churns (recent store rewrite); non-commercial/learning license — fine for this personal tool; keep crawl volumes modest.

## Directory structure

```
C:\Coding\Test\xiaofinance\
├── pyproject.toml            # py>=3.11: fastapi, uvicorn[standard], apscheduler, openai, pydantic-settings
├── .env / .env.example       # DEEPSEEK_API_KEY etc.; .gitignore: data/, vendor/, .env
├── README.md
├── app\
│   ├── main.py               # FastAPI app, routes, lifespan (scheduler), static mount
│   ├── config.py             # pydantic-settings from .env
│   ├── db.py                 # stdlib sqlite3, WAL, schema init
│   ├── util.py               # now_ms(), normalize_ts (10-digit s → ms), parse_cn_count("1.2万"→12000)
│   ├── crawler_runner.py     # config patch + subprocess + timeout/kill-tree + per-run log
│   ├── ingest.py             # run-dir JSONL → notes/comments (freshness gate #1)
│   ├── dedup.py              # simhash near-dup clustering (notes) + normalized exact-dup (comments)
│   ├── mentions.py           # dictionary matching + ambiguity context gate
│   ├── scoring.py            # popularity score (dup clusters count once)
│   ├── analyze.py            # DeepSeek per-stock summarization + keyless fallback
│   ├── slang_scan.py         # every N cycles: LLM mines unmatched finance-context notes for new slang/谐音 aliases → suggestions
│   ├── pipeline.py           # run_cycle(mode): crawl→ingest→dedup→mentions→score→analyze→cleanup; also CLI
│   └── data\stock_dict.json  # ~150 tickers + aliases + context guard words
├── static\                   # index.html, app.js, style.css, vendor\echarts.min.js (downloaded, no runtime CDN)
├── scripts\setup.ps1         # install uv if missing, clone+pin MediaCrawler, uv sync both, playwright install, download echarts
├── scripts\login_xhs.ps1     # tiny visible crawl to (re)do QR login
├── data\infinance.db         # OUR SQLite (separate from MediaCrawler's)
├── data\stock_dict_local.json # user-approved alias additions (overlay merged over stock_dict.json)
├── data\raw\run_000042\      # per-run MediaCrawler JSONL output
└── vendor\MediaCrawler\      # git clone, pinned commit, own uv venv
```

Two independent uv venvs (our app vs MediaCrawler) — no dependency mixing.

## MediaCrawler integration (app/crawler_runner.py)

**Subprocess, not import** — CLI+config is the stable public surface; internals churn. Per run, with `cwd=vendor\MediaCrawler`:

```
uv run main.py --platform xhs --lt qrcode --type search
  --keywords "美股,纳斯达克,纳指,标普500,美股投资"
  --save_data_option jsonl --save_data_path "C:\Coding\Test\xiaofinance\data\raw\run_000042"
  --get_comment yes --get_sub_comment no
  --crawler_max_notes_count 20 --max_comments_count_singlenotes 20
  --start 1 --headless no --max_concurrency_num 1
```

- One cycle = two runs: **discovery** (generic keywords) then **tracked** (alias keywords), each its own run_dir + fetch_runs row.
- **Config patching** before every run (idempotent regex on `NAME = value` lines; hard-fail with clear message if a variable disappeared): `xhs_config.py: SORT_TYPE = "time_descending"`; `base_config.py: ENABLE_CDP_MODE = False`, `CRAWLER_MAX_SLEEP_SEC = 3` (leave `SAVE_LOGIN_STATE = True`).
- Fallback if the pinned version lacks `--save_data_path`: patch `SAVE_DATA_PATH` in config the same way, or ingest from default `data/` dir filtering by run start time.
- Subprocess: `encoding="utf-8", errors="replace"`, stream to `run_dir/crawler.log`; timeout (default 30 min) → kill process **tree** via `taskkill /PID <pid> /T /F` (plain kill orphans Chromium on Windows).
- **Login UX**: `setup.ps1` then `login_xhs.ps1` opens a visible Chromium with QR; scan once with XHS app; session cached. Keep `--headless no` always (lower detection risk). Login-expiry heuristic: 0 fresh notes AND login keywords (扫码/登录/qrcode) in crawler.log → mark run failed + `login_required` banner in UI.

## SQLite schema (data/infinance.db) — all times UTC epoch ms

```sql
fetch_runs(id PK, mode CHECK('discovery','tracked'), keywords, status CHECK('running','success','partial','failed'),
           started_at_ms, finished_at_ms, notes_fetched, notes_fresh, comments_fresh, raw_dir, error)
notes(note_id PK, title, note_desc, note_type, publish_time_ms NOT NULL,      -- ← freshness anchor
      liked_count INT, collected_count INT, comment_count INT, share_count INT,  -- parsed from "1.2万"
      note_url, tag_list, source_keyword, nickname,
      simhash, dup_group_id,               -- dup_group_id = canonical note_id of its repost cluster (NULL = unique/canonical)
      first_seen_run_id, last_seen_run_id, fetched_at_ms)
      + INDEX(publish_time_ms)
comments(comment_id PK, note_id FK, parent_comment_id, content, create_time_ms NOT NULL,  -- ← freshness anchor
      like_count INT, sub_comment_count INT, nickname,
      content_norm_hash, dup_group_id,     -- exact-dup cluster on normalized content (NULL = unique/canonical)
      first_seen_run_id, fetched_at_ms)
      + INDEX(note_id), INDEX(create_time_ms)
stock_mentions(id PK, ticker, source_type CHECK('note','comment'), source_id, note_id, matched_alias,
      match_basis CHECK('safe_alias','alias+context','ticker_symbol','targeted_search'),
      content_time_ms, run_id, UNIQUE(ticker, source_type, source_id)) + INDEX(ticker, content_time_ms)
stock_analyses(id PK, ticker, run_id, generated_at_ms, window_start_ms, window_end_ms,
      note_count, comment_count, popularity_score REAL, sentiment_counts JSON, summary,
      bull_points JSON, bear_points JSON, notable_quotes JSON,
      input_hash,                          -- sha256 of sorted source_ids → skip unchanged re-analysis
      model, input_tokens, output_tokens, cost_usd,
      status CHECK('ok','no_api_key','error','skipped_unchanged'), error)
tracked_stocks(ticker PK CHECK ^[A-Z]{1,5}$, added_at_ms, custom_keywords JSON)
alias_suggestions(id PK, term, guessed_ticker, evidence_quote, evidence_note_id, suggested_at_ms,
      status CHECK('pending','accepted','rejected'), UNIQUE(term, guessed_ticker))
```

**Freshness enforced twice:**
1. **Ingest**: insert note only if `publish_time_ms >= now-24h`; insert comment only if parent note inserted AND `create_time_ms >= now-24h`. Defensive `normalize_ts` (10-digit seconds → ×1000).
2. **Every query**: `WHERE content_time_ms >= now-24h` computed per request — items age out of the dashboard continuously between fetches. Analyses show `generated_at_ms` age badge; ticker drops off ranking when all mentions age out.

Retention: cleanup step deletes notes/comments/mentions > 7 days and their `run_*` dirs; runs/analyses kept 30 days.

## Dedup & repost collapsing (app/dedup.py)

XHS reposts/copypasta inflate both the popularity score and the LLM's perceived consensus (10 copies of one take would otherwise look like 10 independent bull signals), so near-duplicates are collapsed before anything downstream sees them:

- **Notes**: 64-bit simhash over normalized title+desc (full→half width, lowercase latin, strip punctuation/emoji/whitespace); within the fresh window, Hamming distance ≤ 6 → same cluster; canonical = highest `liked_count`, others get `dup_group_id = canonical note_id`. O(n²) pairwise scan is fine at ≤ a few hundred fresh notes.
- **Comments**: exact match on normalized content (`content_norm_hash`) within the window — catches copy-pasted comment spam without false-collapsing short comments the way fuzzy matching would.
- **Downstream effects**: mentions are still extracted from *all* items (dedup changes weighting, not detection); scoring counts each cluster **once** using the canonical's likes; analyze receives only canonicals, annotated `[×N相似]` so the model sees repetition volume without burning tokens on copies; dashboard counts are distinct clusters (raw counts in tooltip).

## Stock dictionary (app/data/stock_dict.json)

- `context_words`: ~50 finance signals (股, 美股, 股价, 涨, 跌, 买入, 卖出, 抄底, 加仓, 持仓, 财报, 估值, 做多, 做空, 纳指, 标普, ETF, 期权, call, put, 韭菜, 暴涨, 暴跌, 盘前, 盘后, 富途, 老虎...).
- `stocks`: ~150 entries `{ticker, name_cn, aliases[], ambiguous[]}` — mega-caps (AAPL 苹果, TSLA 特斯拉/马斯克, NVDA 英伟达/老黄/黄仁勋, MSFT 微软, GOOGL 谷歌, AMZN 亚马逊, META 脸书/小扎, NFLX 奈飞, TSM 台积电), AI/semis (AMD, AVGO, MU, INTC, ARM, SMCI), ADRs (BABA 阿里, PDD 拼多多, JD 京东, BIDU, NIO 蔚来, XPEV 小鹏, LI 理想, BILI, FUTU, NTES), retail favorites (PLTR, MSTR 微策略, COIN, HOOD, GME, RIVN), blue chips (BRK 伯克希尔/巴菲特, JPM, V, KO, MCD 麦当劳, SBUX 星巴克, NKE, DIS, BA, LLY, XOM), ETFs (SPY, QQQ, TQQQ, SQQQ, SOXL, NVDL, TSLL, YINN, KWEB).

**Matching (mentions.py)** per text unit (note = title+desc; comment = content), after full-width→half-width + lowercase-latin normalization:
1. Ticker symbol: regex `(?<![A-Za-z])TICKER(?![A-Za-z])`, uppercase, ≥2 chars; collision-prone tickers (LI, GM, F, ARM, META, COIN, HOOD, ALL) forced ambiguous.
2. Safe alias: plain substring (CJK has no word boundaries) for non-ambiguous aliases (特斯拉, 英伟达...).
3. Ambiguous alias (苹果, 阿里, 多多, 马斯克, 老黄...): counts only if a `context_word` co-occurs in the same text unit, OR item came from a tracked targeted search for that ticker (`source_keyword` provenance).
4. Dedupe via UNIQUE constraint; store strongest basis. Plain loop is fine at this scale (≤ few thousand texts/cycle).

Tracked tickers without dictionary entry: search the ticker symbol itself + optional user-supplied Chinese keywords. Ambiguous tracked keywords are finance-qualified in the actual XHS query (`苹果 美股`).

**Overlay dict**: `data\stock_dict_local.json` (same shape as the base dict) is merged over `stock_dict.json` at load — user-approved additions live there, so base-dict updates never clobber them.

**Slang/谐音 discovery (app/slang_scan.py)** — a static dictionary is inherently reactive against censorship-dodging nicknames (谐音/黑话/emoji codes change precisely because the old term gets flagged), so the pipeline mines for new ones: every `SLANG_SCAN_EVERY_N_CYCLES` cycles (default 20; 0 disables), sample ≤50 fresh notes that matched **zero** tickers but contain ≥1 `context_word` → one DeepSeek call: "identify likely stock nicknames/homophones and the ticker each refers to, with the evidence phrase" → rows into `alias_suggestions` (UNIQUE-deduped) → surfaced in a UI review panel. **Accept** merges the alias into the overlay dict (flagged ambiguous by default, so the context gate still applies); **reject** hides it permanently. Never auto-added — human-in-the-loop keeps hallucinated aliases out of the dictionary.

## Pipeline, scoring, LLM analysis (DeepSeek)

`pipeline.run_cycle(mode)`: crawl(discovery) → ingest → crawl(tracked) → ingest → dedup → extract_mentions → score → analyze → cleanup; every `SLANG_SCAN_EVERY_N_CYCLES`-th cycle appends a slang_scan pass. Also runnable as CLI: `python -m app.pipeline --mode both`.

**Popularity score** (per ticker, trailing 24h, recomputed live, **over dup clusters** — a repost cluster counts once, with the canonical's likes):
`score = 3·N_notes + 1·N_comments + 2·Σ_notes log10(1+likes) + 0.5·Σ_comments log10(1+likes)`
Discovery ranking shows tickers with ≥ MIN_MENTIONS_FOR_ANALYSIS (2); tracked tickers always render (even as "no fresh data"). Sub-floor tickers (1 ≤ mentions < MIN) aren't invisible: they feed an **on-the-radar** strip (ticker, mention count, top quote, one-click track), so quiet-but-present names stay discoverable without LLM spend.

**DeepSeek (analyze.py)**: `openai` Python SDK pointed at DeepSeek — `OpenAI(api_key=settings.DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")`, model `deepseek-chat` (config `LLM_MODEL`; `LLM_BASE_URL` also configurable, so any OpenAI-compatible provider can be swapped in later), `max_tokens=2000`, `temperature≈0.3`. DeepSeek is natively strong at Chinese — well suited to XHS comment analysis. Analyze top 15 by score + all tracked with ≥1 mention. Skip API call when `input_hash` (sha256 of sorted mention source_ids) unchanged → `skipped_unchanged`. Input: ≤60 **canonical** items per stock (dup clusters pre-collapsed; notes truncated 300 chars, comments 150, sorted by likes), each line `[note|comment] [N小时前] [赞:123] [×N相似]? text`. Prompt (zh system): analyst persona; step 1 discard items not about the stock as an investment (苹果 the fruit/iPhone reviews) and report `irrelevant_item_count`; step 2 stance per item (bullish/bearish/neutral), treating `[×N相似]` clusters and same-argument items as **one viewpoint** — weight by distinct arguments, not repetition; step 3 summary starting `"{ticker}: "`, ≤4 bull points, ≤4 bear points, ≤3 verbatim quotes. Summary language: **English** default (`SUMMARY_LANG=en`, quotes stay in original Chinese; switchable to zh). Structured response via DeepSeek JSON mode (`response_format={"type": "json_object"}`, JSON schema spelled out in the prompt as DeepSeek requires) → validate with a Pydantic model → one retry on parse/validation failure. Sequential calls, 0.5s spacing, per-ticker error isolation (`status='error'`, previous analysis stays visible). **No API key** → `status='no_api_key'`, quotes = top-3 liked mentioning comments, dashboard says "set DEEPSEEK_API_KEY for summaries". Cost is negligible: deepseek-chat ≈ $0.27/M input, $1.10/M output → ~15 stocks × 5K in/0.7K out ≈ **$0.03/cycle** (~$0.15/day at 5 cycles; cheaper still with cache hits / off-peak discount).

## FastAPI (app/main.py)

| Endpoint | Behavior |
|---|---|
| `GET /` + `/static/*` | dashboard + assets |
| `GET /api/status` | `{last_run, running, login_required, scheduler:{enabled, interval_hours, next_run_at_ms}, now_ms, window_hours}` |
| `POST /api/fetch` `{mode}` | non-blocking lock acquire; cycle in background thread; `202 {run_ids}` / `409` if running |
| `GET /api/ranking` | 24h window: `{ranking: [{ticker, name_cn, score, note_count, comment_count, sentiment_counts, tracked, analysis_age_ms, latest_item_age_ms}], radar: [{ticker, mentions, top_quote}]}` — radar = sub-floor tickers (1 ≤ mentions < MIN) |
| `GET /api/stocks/{ticker}` | latest analysis (full) + ≤30 fresh source items with ages, likes, note_url |
| `GET/POST /api/tracked`, `DELETE /api/tracked/{t}` | watchlist CRUD; validate `^[A-Z]{1,5}$` |
| `GET /api/alias_suggestions`, `POST /api/alias_suggestions/{id}` | pending slang-scan candidates; body `{action: "accept"\|"reject"}` — accept merges alias into overlay dict (ambiguous by default) |
| `GET /api/runs?limit=20` | recent runs for status panel |

- APScheduler `BackgroundScheduler` in lifespan; IntervalTrigger every `FETCH_INTERVAL_HOURS` (default 5, 0 disables) → same `run_cycle("both")`.
- Single-crawl guarantee: module-level `threading.Lock`, non-blocking acquire in both endpoint and job; cycle runs in worker thread (blocking subprocess). On startup, stale `running` rows → `failed`.
- `.env` config: `DEEPSEEK_API_KEY`, `LLM_MODEL=deepseek-chat`, `LLM_BASE_URL=https://api.deepseek.com`, `SUMMARY_LANG=en`, `DISCOVERY_KEYWORDS=美股,纳斯达克,纳指,标普500,美股投资`, `MAX_NOTES_PER_KEYWORD=20`, `MAX_COMMENTS_PER_NOTE=20`, `FETCH_INTERVAL_HOURS=5`, `FRESH_WINDOW_HOURS=24`, `MIN_MENTIONS_FOR_ANALYSIS=2`, `MAX_ANALYZED_STOCKS=15`, `SLANG_SCAN_EVERY_N_CYCLES=20`, `CRAWL_TIMEOUT_MIN=30`, `MEDIACRAWLER_DIR`, `UV_EXE`, `HOST=127.0.0.1`, `PORT=8000`.
- Run: `uv run uvicorn app.main:app --host 127.0.0.1 --port 8000` (localhost only, no auth).

## Frontend (static/, no build step, no runtime CDN)

`index.html` + vanilla `app.js` + `style.css` + `vendor/echarts.min.js` (setup downloads from npmmirror, jsdelivr fallback).
- **Header**: last fetch time (local tz), "window: last 24h" badge, Fetch-now button (spinner while running; poll /api/status 3s during run, 30s idle), next scheduled run, red `login_required` banner with re-login instruction.
- **Ranking**: ECharts horizontal bar (score), bar color by net sentiment (green/red/gray); click → scroll to card.
- **Stock cards**: ticker + 中文名, tracked pin, N notes/M comments, sentiment donut (ECharts pie), LLM summary ("AAPL: ..."), bull/bear bullets, quotes with relative age + likes + note link, data-age badges (newest item age; "analysis Xh ago", amber when stale), noise indicator ("model discarded N/M items as off-topic" from `irrelevant_item_count`).
- **Tracked management**: add form (ticker + optional Chinese keywords), remove ✕.
- **On the radar**: compact strip of sub-floor tickers (fresh mentions but below MIN) with mention count + top quote + one-click track.
- **Alias suggestions** (collapsed): pending slang-scan candidates with evidence quote; accept/reject buttons.
- **Runs panel** (collapsed): mode, keywords, status, fresh counts, duration, error.
- One `relTime(ms)` / `localTime(ms)` formatter; epoch ms everywhere in JSON.

## Implementation phases (each with verification)

| # | Phase | Verify |
|---|---|---|
| 0 | Scaffold: pyproject, .env.example, .gitignore, setup.ps1 (installs uv, downloads echarts) | `uv run python -c "import fastapi, openai, apscheduler"` OK; echarts.min.js > 900KB |
| 1 | MediaCrawler clone+pin+sync+playwright; login_xhs.ps1 | Manual smoke crawl `--keywords 美股 --crawler_max_notes_count 5`: QR appears → scan → JSONL lines with 13-digit `time` + `source_keyword`; re-run needs **no QR** |
| 2 | db.py schema + util.py + ingest.py | Ingest phase-1 output: notes count > 0; hand-inserted 3-day-old line excluded (`notes_fresh < notes_fetched`); `parse_cn_count("1.2万")==12000` unit test |
| 3 | crawler_runner.py + pipeline skeleton | `python -m app.pipeline --mode discovery`: fetch_runs row `success`, crawler.log exists, notes ingested; xhs_config.py now has `SORT_TYPE = "time_descending"` |
| 4 | stock_dict.json + dedup.py + mentions.py + scoring.py + pytest | "苹果好吃"→none; "苹果股价新高"→AAPL alias+context; "英伟达yyds"→NVDA safe; "买了点LI"→none w/o context; two near-identical notes → one cluster, scored once. Then full run → sane `GROUP BY ticker` counts |
| 5 | analyze.py + slang_scan.py | With DEEPSEEK_API_KEY set: `python -m app.analyze NVDA` → row `ok`, summary starts "NVDA: ", valid JSON fields, input lines carry `[×N相似]`. Without key → `no_api_key`, quotes populated. slang_scan on synthetic unmatched notes → `alias_suggestions` rows |
| 6 | main.py endpoints + scheduler + lock | `curl /api/ranking` JSON; double `POST /api/fetch` → 409; tracked CRUD round-trip; kill mid-crawl + restart → run marked failed; alias accept → overlay dict updated |
| 7 | Frontend | Open 127.0.0.1:8000: chart + cards render with zero external requests (DevTools); ages in local tz; Fetch-now disables then refreshes; radar strip + suggestions panel render |
| 8 | Hardening + README | Delete browser profile → fetch → login banner appears; >7-day rows purged; README covers setup/login/upgrade procedure |

**End-to-end verification**: run `scripts\setup.ps1` → `scripts\login_xhs.ps1` (scan QR) → start server → click Fetch now → within ~10-15 min dashboard shows ranked tickers with summaries; every visible item timestamp within 24h; add tracked ticker (e.g. `COST`) → next fetch shows its card.

## Risks & mitigations

- **XHS anti-bot / account risk** (uses your own account): low volume (~100-200 notes/cycle), concurrency 1, 3s sleeps, sub-comments off, ≥4-6h cadence, visible browser; suggest a secondary XHS account. MediaCrawler is learning/non-commercial — personal use complies.
- **Upstream breakage**: pin commit; surface limited to CLI + JSONL keys + 2 patched config lines; patcher hard-fails with clear message; ingest skips malformed lines (`partial`); update = git pull + phase-1 smoke test.
- **Login expiry**: heuristic detection → banner + `login_xhs.ps1`.
- **Thin results with time-sort**: multiple keywords; MIN_MENTIONS floor; tracked cards say "no fresh data in last 24h" rather than showing stale data.
- **Timestamps**: XHS provides numeric epoch ms (no relative-time parsing); defensive s→ms normalization; counts like "1.2万"/"10+" parsed with unit tests.
- **Ambiguity false positives** (苹果/阿里/LI): context-word gate + finance-qualified targeted queries + LLM discard step reporting `irrelevant_item_count` (surfaced on the card as a noise indicator).
- **Novel slang / 谐音黑话 evading the dictionary**: dictionary detection is inherently reactive — slang_scan periodically mines unmatched finance-context notes for candidate nicknames; accepted aliases close the gap over time. Brand-new nicknames are still missed until scanned: accepted as a structural limit.
- **Repost inflation / duplicate ideas**: simhash clustering collapses copypasta before scoring and analysis; the prompt treats repetition as one viewpoint — consensus reflects distinct arguments, not the loudest copy-paste.
- **Single-LLM judgment**: all semantic filtering rides on one deepseek-chat call per stock. Mitigations: dedup-cleaned capped input, `irrelevant_item_count` noise indicator on the card, verbatim quotes for spot-checking, per-ticker error isolation with the previous analysis kept visible.
- **LLM output quality/format drift** (DeepSeek JSON mode has no strict schema enforcement): Pydantic validation + one retry; on repeated failure `status='error'`, card falls back to quotes.
- **Windows**: utf-8 subprocess encoding, `taskkill /T /F` process-tree kill, pathlib, absolute UV_EXE for scheduler, 127.0.0.1 bind.

## v1.1 additions (implemented)

- **Cross-cycle memory / trend deltas** (promoted from deferred): per-cycle `score_snapshots` power heat-trend badges (🔥 新上榜 / ↑ 升温 / ↓ 降温, cycle-over-cycle) and per-card sparklines; each analysis also sees the previous cycle's summary (≤48h) as guarded compare-only context so it can call out sentiment shifts without being biased by them.
- **Price reality check**: daily closes from Yahoo's free chart API (`app/prices.py`, `ENABLE_PRICE_QUOTES` toggle) → price-change badge per card and a 🔀 divergence flag when crowd lean and price move conflict.
- **Crowd hit-rate scoreboard** (`app/scoreboard.py`): clear daily leans (|bullish−bearish| ≥ 2) scored against 1d/7d realized moves.
- **Reply threads**: `ENABLE_SUB_COMMENTS` opt-in (off by default for account safety); replies reach the LLM prefixed with their parent snippet.

## v1.2 signal-quality refinements (implemented — driven by first real-data cycles, 2026-07-11)

Observed on the first live crawls: one 财报日历 post fanned out to 12 tickers and put every big bank on the board as "🔥 新上榜"; the same 3 mega-posts were every card's quotes; GS ranked off a `#高盛观察` hashtag; 17/98 notes duplicated their title inside the desc; 206 fresh comments were invisible to analysis (only 3 named a ticker); trend badges showed "+409%" off tiny score bases; and the day's dominant entity (SK海力士 ADR) wasn't in the dict, so its coverage bled into name-dropped neighbors. Fixes:

- **Fan-out weighting**: a source mentioning k tickers contributes weight 1/k to each ticker's score — roundup/calendar posts no longer count like dedicated posts.
- **Focus gate**: main-board ranking (and LLM spend) requires ≥1 *focused* source — a ≤3-ticker item where the alias appears in the prose, not just a `#话题#` tag block. Roundup-only/hashtag-only tickers stay on the radar strip. Mentions of every ticker are still extracted and fed to the LLM (cross-stock signals like "英伟达、谷歌入股海力士产线" stay visible) — the gate only governs heat and ranking.
- **Roundup marker for the LLM**: items naming ≥4 tickers carry `[盘点·提及N股]` plus a prompt instruction not to read being-listed as a viewpoint.
- **Thread comments**: `gather_items` pulls top-liked comments from mentioning notes with fanout ≤ 2 (prefixed `主帖「…」下的评论:`) — most comments never name the ticker but are reactions to it.
- **Text hygiene**: `note_text()` drops descs' repeated title; `clean_tags()` strips `[话题]#` markup; fallback quotes prefer focused sources over globally-hot roundups.
- **Trend badges**: percentage suppressed when the previous score base < 5 (direction-only badge).
- **Prices**: Yahoo symbol overrides (BRK→BRK-B, SKHY→SKHYV while the ADR trades when-issued); single-session IPOs store a price without a change badge.
- **Dict**: SKHY (SK海力士, listed Nasdaq 2026-07-10) added. Note: dict updates require a `--skip-crawl` reprocess (or the next cycle) to re-extract mentions from already-ingested notes.

## Deliberately deferred (v2 candidates)

- **Organic hidden-gem discovery**: discovery ranks what's loud by design; the tracked list + radar strip are the levers for quiet names. No embedding/cluster mining in v1.
- **Auto-accepting slang suggestions**: stays human-in-the-loop until the suggestion stream's precision is observed.
- **Author credibility weighting**: discount serial reposters / up-weight consistently early accounts — needs weeks of accumulated author history first.
- **Second platform (Weibo/Bilibili)**: doubles the crawl/login surface; revisit if XHS-only proves insufficient.
