# infinance — XHS (小红书) US-Stock Sentiment Dashboard — Implementation Plan

## Context

A local personal tool that answers "which US stocks are hot on Xiaohongshu right now, and what do people think of them?" XHS has no official API, so data comes from the open-source **MediaCrawler** (github.com/NanmiCoder/MediaCrawler) using your own XHS account (QR login). A local dictionary detects stock mentions; the DeepSeek API (OpenAI-compatible) summarizes per-stock views ("AAPL: <summary>..."). A FastAPI + ECharts web dashboard visualizes popularity ranking and per-stock cards, supports manually tracked tickers, a "Fetch now" button, and scheduled refresh.

**Hard requirement:** only data ≤ 24h old is analyzed/shown — enforced at BOTH ingestion and query time; all timestamps stored as UTC epoch ms, displayed in local time, with visible data-age badges.

**Confirmed decisions:** MediaCrawler (not paid API) · US stocks only (incl. major US-listed Chinese ADRs like BABA/PDD/NIO and retail-favorite ETFs like QQQ/TQQQ/SOXL) · Hybrid analysis (dictionary detection + DeepSeek `deepseek-chat` summarization, keyless fallback = quotes only) · FastAPI + ECharts dashboard.

Directory `D:\code_save\infinance` is empty (greenfield). Machine: Windows 11, currently no `uv` on PATH (setup script installs it).

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
D:\code_save\infinance\
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
│   ├── mentions.py           # dictionary matching + ambiguity context gate
│   ├── scoring.py            # popularity score
│   ├── analyze.py            # DeepSeek per-stock summarization + keyless fallback
│   ├── pipeline.py           # run_cycle(mode): crawl→ingest→mentions→score→analyze→cleanup; also CLI
│   └── data\stock_dict.json  # ~150 tickers + aliases + context guard words
├── static\                   # index.html, app.js, style.css, vendor\echarts.min.js (downloaded, no runtime CDN)
├── scripts\setup.ps1         # install uv if missing, clone+pin MediaCrawler, uv sync both, playwright install, download echarts
├── scripts\login_xhs.ps1     # tiny visible crawl to (re)do QR login
├── data\infinance.db         # OUR SQLite (separate from MediaCrawler's)
├── data\raw\run_000042\      # per-run MediaCrawler JSONL output
└── vendor\MediaCrawler\      # git clone, pinned commit, own uv venv
```

Two independent uv venvs (our app vs MediaCrawler) — no dependency mixing.

## MediaCrawler integration (app/crawler_runner.py)

**Subprocess, not import** — CLI+config is the stable public surface; internals churn. Per run, with `cwd=vendor\MediaCrawler`:

```
uv run main.py --platform xhs --lt qrcode --type search
  --keywords "美股,纳斯达克,纳指,标普500,美股投资"
  --save_data_option jsonl --save_data_path "D:\code_save\infinance\data\raw\run_000042"
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
      note_url, tag_list, source_keyword, nickname, first_seen_run_id, last_seen_run_id, fetched_at_ms)
      + INDEX(publish_time_ms)
comments(comment_id PK, note_id FK, parent_comment_id, content, create_time_ms NOT NULL,  -- ← freshness anchor
      like_count INT, sub_comment_count INT, nickname, first_seen_run_id, fetched_at_ms)
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
```

**Freshness enforced twice:**
1. **Ingest**: insert note only if `publish_time_ms >= now-24h`; insert comment only if parent note inserted AND `create_time_ms >= now-24h`. Defensive `normalize_ts` (10-digit seconds → ×1000).
2. **Every query**: `WHERE content_time_ms >= now-24h` computed per request — items age out of the dashboard continuously between fetches. Analyses show `generated_at_ms` age badge; ticker drops off ranking when all mentions age out.

Retention: cleanup step deletes notes/comments/mentions > 7 days and their `run_*` dirs; runs/analyses kept 30 days.

## Stock dictionary (app/data/stock_dict.json)

- `context_words`: ~50 finance signals (股, 美股, 股价, 涨, 跌, 买入, 卖出, 抄底, 加仓, 持仓, 财报, 估值, 做多, 做空, 纳指, 标普, ETF, 期权, call, put, 韭菜, 暴涨, 暴跌, 盘前, 盘后, 富途, 老虎...).
- `stocks`: ~150 entries `{ticker, name_cn, aliases[], ambiguous[]}` — mega-caps (AAPL 苹果, TSLA 特斯拉/马斯克, NVDA 英伟达/老黄/黄仁勋, MSFT 微软, GOOGL 谷歌, AMZN 亚马逊, META 脸书/小扎, NFLX 奈飞, TSM 台积电), AI/semis (AMD, AVGO, MU, INTC, ARM, SMCI), ADRs (BABA 阿里, PDD 拼多多, JD 京东, BIDU, NIO 蔚来, XPEV 小鹏, LI 理想, BILI, FUTU, NTES), retail favorites (PLTR, MSTR 微策略, COIN, HOOD, GME, RIVN), blue chips (BRK 伯克希尔/巴菲特, JPM, V, KO, MCD 麦当劳, SBUX 星巴克, NKE, DIS, BA, LLY, XOM), ETFs (SPY, QQQ, TQQQ, SQQQ, SOXL, NVDL, TSLL, YINN, KWEB).

**Matching (mentions.py)** per text unit (note = title+desc; comment = content), after full-width→half-width + lowercase-latin normalization:
1. Ticker symbol: regex `(?<![A-Za-z])TICKER(?![A-Za-z])`, uppercase, ≥2 chars; collision-prone tickers (LI, GM, F, ARM, META, COIN, HOOD, ALL) forced ambiguous.
2. Safe alias: plain substring (CJK has no word boundaries) for non-ambiguous aliases (特斯拉, 英伟达...).
3. Ambiguous alias (苹果, 阿里, 多多, 马斯克, 老黄...): counts only if a `context_word` co-occurs in the same text unit, OR item came from a tracked targeted search for that ticker (`source_keyword` provenance).
4. Dedupe via UNIQUE constraint; store strongest basis. Plain loop is fine at this scale (≤ few thousand texts/cycle).

Tracked tickers without dictionary entry: search the ticker symbol itself + optional user-supplied Chinese keywords. Ambiguous tracked keywords are finance-qualified in the actual XHS query (`苹果 美股`).

## Pipeline, scoring, LLM analysis (DeepSeek)

`pipeline.run_cycle(mode)`: crawl(discovery) → ingest → crawl(tracked) → ingest → extract_mentions → score → analyze → cleanup. Also runnable as CLI: `python -m app.pipeline --mode both`.

**Popularity score** (per ticker, trailing 24h, recomputed live):
`score = 3·N_notes + 1·N_comments + 2·Σ_notes log10(1+likes) + 0.5·Σ_comments log10(1+likes)`
Discovery ranking shows tickers with ≥ MIN_MENTIONS_FOR_ANALYSIS (2); tracked tickers always render (even as "no fresh data").

**DeepSeek (analyze.py)**: `openai` Python SDK pointed at DeepSeek — `OpenAI(api_key=settings.DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")`, model `deepseek-chat` (config `LLM_MODEL`; `LLM_BASE_URL` also configurable, so any OpenAI-compatible provider can be swapped in later), `max_tokens=2000`, `temperature≈0.3`. DeepSeek is natively strong at Chinese — well suited to XHS comment analysis. Analyze top 15 by score + all tracked with ≥1 mention. Skip API call when `input_hash` (sha256 of sorted mention source_ids) unchanged → `skipped_unchanged`. Input: ≤60 items per stock (notes truncated 300 chars, comments 150, sorted by likes), each line `[note|comment] [N小时前] [赞:123] text`. Prompt (zh system): analyst persona; step 1 discard items not about the stock as an investment (苹果 the fruit/iPhone reviews) and report `irrelevant_item_count`; step 2 stance per item (bullish/bearish/neutral); step 3 summary starting `"{ticker}: "`, ≤4 bull points, ≤4 bear points, ≤3 verbatim quotes. Summary language: **English** default (`SUMMARY_LANG=en`, quotes stay in original Chinese; switchable to zh). Structured response via DeepSeek JSON mode (`response_format={"type": "json_object"}`, JSON schema spelled out in the prompt as DeepSeek requires) → validate with a Pydantic model → one retry on parse/validation failure. Sequential calls, 0.5s spacing, per-ticker error isolation (`status='error'`, previous analysis stays visible). **No API key** → `status='no_api_key'`, quotes = top-3 liked mentioning comments, dashboard says "set DEEPSEEK_API_KEY for summaries". Cost is negligible: deepseek-chat ≈ $0.27/M input, $1.10/M output → ~15 stocks × 5K in/0.7K out ≈ **$0.03/cycle** (~$0.15/day at 5 cycles; cheaper still with cache hits / off-peak discount).

## FastAPI (app/main.py)

| Endpoint | Behavior |
|---|---|
| `GET /` + `/static/*` | dashboard + assets |
| `GET /api/status` | `{last_run, running, login_required, scheduler:{enabled, interval_hours, next_run_at_ms}, now_ms, window_hours}` |
| `POST /api/fetch` `{mode}` | non-blocking lock acquire; cycle in background thread; `202 {run_ids}` / `409` if running |
| `GET /api/ranking` | 24h window: `[{ticker, name_cn, score, note_count, comment_count, sentiment_counts, tracked, analysis_age_ms, latest_item_age_ms}]` |
| `GET /api/stocks/{ticker}` | latest analysis (full) + ≤30 fresh source items with ages, likes, note_url |
| `GET/POST /api/tracked`, `DELETE /api/tracked/{t}` | watchlist CRUD; validate `^[A-Z]{1,5}$` |
| `GET /api/runs?limit=20` | recent runs for status panel |

- APScheduler `BackgroundScheduler` in lifespan; IntervalTrigger every `FETCH_INTERVAL_HOURS` (default 5, 0 disables) → same `run_cycle("both")`.
- Single-crawl guarantee: module-level `threading.Lock`, non-blocking acquire in both endpoint and job; cycle runs in worker thread (blocking subprocess). On startup, stale `running` rows → `failed`.
- `.env` config: `DEEPSEEK_API_KEY`, `LLM_MODEL=deepseek-chat`, `LLM_BASE_URL=https://api.deepseek.com`, `SUMMARY_LANG=en`, `DISCOVERY_KEYWORDS=美股,纳斯达克,纳指,标普500,美股投资`, `MAX_NOTES_PER_KEYWORD=20`, `MAX_COMMENTS_PER_NOTE=20`, `FETCH_INTERVAL_HOURS=5`, `FRESH_WINDOW_HOURS=24`, `MIN_MENTIONS_FOR_ANALYSIS=2`, `MAX_ANALYZED_STOCKS=15`, `CRAWL_TIMEOUT_MIN=30`, `MEDIACRAWLER_DIR`, `UV_EXE`, `HOST=127.0.0.1`, `PORT=8000`.
- Run: `uv run uvicorn app.main:app --host 127.0.0.1 --port 8000` (localhost only, no auth).

## Frontend (static/, no build step, no runtime CDN)

`index.html` + vanilla `app.js` + `style.css` + `vendor/echarts.min.js` (setup downloads from npmmirror, jsdelivr fallback).
- **Header**: last fetch time (local tz), "window: last 24h" badge, Fetch-now button (spinner while running; poll /api/status 3s during run, 30s idle), next scheduled run, red `login_required` banner with re-login instruction.
- **Ranking**: ECharts horizontal bar (score), bar color by net sentiment (green/red/gray); click → scroll to card.
- **Stock cards**: ticker + 中文名, tracked pin, N notes/M comments, sentiment donut (ECharts pie), LLM summary ("AAPL: ..."), bull/bear bullets, quotes with relative age + likes + note link, data-age badges (newest item age; "analysis Xh ago", amber when stale).
- **Tracked management**: add form (ticker + optional Chinese keywords), remove ✕.
- **Runs panel** (collapsed): mode, keywords, status, fresh counts, duration, error.
- One `relTime(ms)` / `localTime(ms)` formatter; epoch ms everywhere in JSON.

## Implementation phases (each with verification)

| # | Phase | Verify |
|---|---|---|
| 0 | Scaffold: pyproject, .env.example, .gitignore, setup.ps1 (installs uv, downloads echarts) | `uv run python -c "import fastapi, openai, apscheduler"` OK; echarts.min.js > 900KB |
| 1 | MediaCrawler clone+pin+sync+playwright; login_xhs.ps1 | Manual smoke crawl `--keywords 美股 --crawler_max_notes_count 5`: QR appears → scan → JSONL lines with 13-digit `time` + `source_keyword`; re-run needs **no QR** |
| 2 | db.py schema + util.py + ingest.py | Ingest phase-1 output: notes count > 0; hand-inserted 3-day-old line excluded (`notes_fresh < notes_fetched`); `parse_cn_count("1.2万")==12000` unit test |
| 3 | crawler_runner.py + pipeline skeleton | `python -m app.pipeline --mode discovery`: fetch_runs row `success`, crawler.log exists, notes ingested; xhs_config.py now has `SORT_TYPE = "time_descending"` |
| 4 | stock_dict.json + mentions.py + scoring.py + pytest | "苹果好吃"→none; "苹果股价新高"→AAPL alias+context; "英伟达yyds"→NVDA safe; "买了点LI"→none w/o context. Then full run → sane `GROUP BY ticker` counts |
| 5 | analyze.py | With DEEPSEEK_API_KEY set: `python -m app.analyze NVDA` → row `ok`, summary starts "NVDA: ", valid JSON fields. Without key → `no_api_key`, quotes populated |
| 6 | main.py endpoints + scheduler + lock | `curl /api/ranking` JSON; double `POST /api/fetch` → 409; tracked CRUD round-trip; kill mid-crawl + restart → run marked failed |
| 7 | Frontend | Open 127.0.0.1:8000: chart + cards render with zero external requests (DevTools); ages in local tz; Fetch-now disables then refreshes |
| 8 | Hardening + README | Delete browser profile → fetch → login banner appears; >7-day rows purged; README covers setup/login/upgrade procedure |

**End-to-end verification**: run `scripts\setup.ps1` → `scripts\login_xhs.ps1` (scan QR) → start server → click Fetch now → within ~10-15 min dashboard shows ranked tickers with summaries; every visible item timestamp within 24h; add tracked ticker (e.g. `COST`) → next fetch shows its card.

## Risks & mitigations

- **XHS anti-bot / account risk** (uses your own account): low volume (~100-200 notes/cycle), concurrency 1, 3s sleeps, sub-comments off, ≥4-6h cadence, visible browser; suggest a secondary XHS account. MediaCrawler is learning/non-commercial — personal use complies.
- **Upstream breakage**: pin commit; surface limited to CLI + JSONL keys + 2 patched config lines; patcher hard-fails with clear message; ingest skips malformed lines (`partial`); update = git pull + phase-1 smoke test.
- **Login expiry**: heuristic detection → banner + `login_xhs.ps1`.
- **Thin results with time-sort**: multiple keywords; MIN_MENTIONS floor; tracked cards say "no fresh data in last 24h" rather than showing stale data.
- **Timestamps**: XHS provides numeric epoch ms (no relative-time parsing); defensive s→ms normalization; counts like "1.2万"/"10+" parsed with unit tests.
- **Ambiguity false positives** (苹果/阿里/LI): context-word gate + finance-qualified targeted queries + LLM discard step reporting `irrelevant_item_count`.
- **LLM output quality/format drift** (DeepSeek JSON mode has no strict schema enforcement): Pydantic validation + one retry; on repeated failure `status='error'`, card falls back to quotes.
- **Windows**: utf-8 subprocess encoding, `taskkill /T /F` process-tree kill, pathlib, absolute UV_EXE for scheduler, 127.0.0.1 bind.
