# infinance — 小红书美股热度看板

A local personal dashboard that answers: **which US stocks are hot on Xiaohongshu right now, and what do people think of them?**

Data comes from your own XHS account via [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) (QR login, no API key). A local dictionary detects stock mentions (~145 tickers with Chinese aliases and an ambiguity gate), repost spam is collapsed via simhash clustering, and DeepSeek summarizes per-stock sentiment. Only content from the **last 24 hours** is analyzed and shown.

## Setup (once)

```powershell
scripts\setup.ps1        # installs uv if missing, syncs deps, pins MediaCrawler, downloads chromium + echarts
scripts\login_xhs.ps1    # opens a visible browser — scan the QR with your XHS app (session is cached)
```

Then put your DeepSeek key in `.env` (optional — without it, cards show top quotes instead of AI summaries):

```
DEEPSEEK_API_KEY=sk-...
```

## Run

```powershell
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000, click **Fetch now**. A full cycle (crawl → ingest → dedup → mentions → score → analyze) takes ~10–15 min. Auto-refresh runs every `FETCH_INTERVAL_HOURS` (default 5, `0` disables).

CLI equivalent: `uv run python -m app.pipeline --mode both` (`--skip-crawl` re-analyzes existing data).

## How it works

| Step | Module | What it does |
|---|---|---|
| Crawl | `app/crawler_runner.py` | Runs MediaCrawler as a subprocess (time-sorted search, JSONL output, per-run log, kill-tree timeout) |
| Ingest | `app/ingest.py` | Freshness gate #1: only notes/comments ≤ 24h old enter the DB (`data/infinance.db`) |
| Dedup | `app/dedup.py` | Simhash clustering of reposted notes + exact-dup comments; clusters count once in scoring and reach the LLM as one item tagged `[×N相似]` |
| Mentions | `app/mentions.py` | Dictionary matching: safe aliases (特斯拉), ambiguous aliases need a finance context word (苹果 + 股价), collision tickers (LI/MS/KO…) need context too |
| Score | `app/scoring.py` | `3·notes + 1·comments + 2·Σlog₁₀(1+note likes) + 0.5·Σlog₁₀(1+comment likes)` over clusters |
| Analyze | `app/analyze.py` | DeepSeek JSON-mode per stock: discards off-topic items, weighs distinct arguments (not repetition), outputs summary/bull/bear/quotes; sees the previous cycle's summary as compare-only background (≤48h, never overrides current data) so it can call out sentiment shifts; skips when inputs unchanged |
| Slang scan | `app/slang_scan.py` | Every N cycles: mines unmatched finance posts for new 谐音/黑话 nicknames → review panel, accept merges into `data/stock_dict_local.json` |

Freshness gate #2: every API query re-filters to the trailing 24h window, so items age out continuously between fetches.

## Notes & troubleshooting

- **Login expired**: red banner appears → run `scripts\login_xhs.ps1` again.
- **Trend badges** (🔥 新上榜 / ↑ 升温 / ↓ 降温) compare popularity against the previous fetch cycle, so they appear once two cycles of history exist. Amber/gray on purpose — green/red are reserved for sentiment.
- **Price reality check**: daily closes from Yahoo Finance's public chart API (free, no key, delayed). The 🔀 badge flags sentiment/price divergence (e.g. crowd bullish while the stock fell ≥2%). This is the app's only non-XHS external request; set `ENABLE_PRICE_QUOTES=false` to stay fully offline.
- **Tracked tickers** always render (targeted search: ticker symbol + finance-qualified keywords). Sub-floor tickers appear in the "On the radar" strip.
- **Account safety**: low volume (~100–200 notes/cycle), concurrency 1, 3s sleeps, visible browser, ≥4–6h cadence. Consider a secondary XHS account. MediaCrawler is non-commercial/learning-licensed — keep it personal.
- **Reply threads**: `ENABLE_SUB_COMMENTS=true` crawls comment reply chains (replies reach the LLM as `回复「父评论…」: …` so thread context isn't lost). Off by default — it multiplies requests per note, so weigh it against account risk.
- **Upgrade MediaCrawler**: bump `$MC_PIN` in `scripts/setup.ps1`, re-run it, then do a small smoke crawl. The integration surface is only the CLI args, JSONL field names, and 3 patched config lines (`crawler_runner.PATCHES`) — the patcher hard-fails with a clear message if upstream renames one.
- **Hit-rate scoreboard** (舆论准确率 panel): whenever an analysis leans clearly one way (|bullish−bearish| ≥ 2), that day's call is later scored against the next-day and 7-day price moves. It needs a few days of accumulated analyses + quotes before it shows anything.
- **Retention**: raw content 7 days, runs/analyses 30 days, cleaned each cycle.
- **Tests**: `uv run pytest`
