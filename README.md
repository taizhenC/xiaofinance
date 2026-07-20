# xiaofinance — 小红书美股热度看板

A local personal dashboard that answers: **which US stocks are hot on Xiaohongshu right now, and what do people think of them?**

Data comes from your own XHS account via [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) (QR login, no API key). A local dictionary detects stock mentions (~250 tickers with Chinese aliases, retail 黑话 like 老黄/苏妈/牙膏厂, and an ambiguity gate), repost spam is collapsed via simhash clustering, and DeepSeek summarizes per-stock sentiment. Only content from the **last 24 hours** is analyzed and shown.

## Setup (once)

Works the same on Windows and macOS (needs [uv](https://docs.astral.sh/uv/) and git):

```
uv sync                    # from a repo checkout; or: pipx install infinance
uv run infinance setup     # fetches MediaCrawler (pinned), installs its deps + Chromium, scaffolds .env
uv run infinance login     # opens a visible browser — scan the QR with your XHS app (session is cached)
```

Then put your DeepSeek key in `.env` (optional — without it, cards show top quotes instead of AI summaries):

```
DEEPSEEK_API_KEY=sk-...
```

## Run

```
uv run infinance run
```

Open http://127.0.0.1:8000, click **立即抓取**. A full cycle (crawl → ingest → dedup → mentions → score → analyze) takes ~10–15 min. Auto-refresh runs every `FETCH_INTERVAL_HOURS` (default 5, `0` disables).

Other commands: `infinance doctor` (diagnose a broken install), `infinance smoke` (3-note test crawl), `infinance cycle --mode both` (one pipeline cycle without the server; `--skip-crawl` re-analyzes existing data).

## How it works

| Step | Module | What it does |
|---|---|---|
| Crawl | `infinance/providers/mediacrawler.py` | Runs MediaCrawler as a subprocess (time-sorted search, JSONL output, per-run log, kill-tree timeout) |
| Ingest | `infinance/ingest.py` | Freshness gate #1: only notes/comments ≤ 24h old enter the DB (`data/infinance.db`) |
| Dedup | `infinance/dedup.py` | Simhash clustering of reposted notes + exact-dup comments; clusters count once in scoring and reach the LLM as one item tagged `[×N相似]` |
| Mentions | `infinance/mentions.py` | Dictionary matching in three strengths: safe aliases fire alone (特斯拉, 巨硬, 皮衣黄), ambiguous ones need a finance context word nearby (苹果 + 股价; 老黄, 马斯克, 谷歌 — a person fronts several ventures and a brand names products), and collision tickers (LI/MS/KO…) need context too |
| Score | `infinance/scoring.py` | `3·notes + 1·comments + 2·Σlog₁₀(1+note likes) + 0.5·Σlog₁₀(1+comment likes)` over clusters |
| Analyze | `infinance/analyze.py` | DeepSeek JSON-mode per stock: discards off-topic items, weighs distinct arguments (not repetition), outputs summary/bull/bear/quotes; sees the previous cycle's summary as compare-only background (≤48h, never overrides current data) so it can call out sentiment shifts; skips when inputs unchanged |
| Slang scan | `infinance/slang_scan.py` | Every N cycles: mines unmatched finance posts for new 谐音/黑话 nicknames → review panel, accept merges into `data/stock_dict_local.json` |

Freshness gate #2: every API query re-filters to the trailing 24h window, so items age out continuously between fetches.

## Notes & troubleshooting

- **Login expired**: red banner appears → run `infinance login` again.
- **Login works but every search fails** with `您当前登录的账号没有权限访问` (see `data\raw\run_*\crawler.log`). In order:
  1. **Is your account on rednote.com?** The international app is a *separate backend* from mainland xiaohongshu.com — different API host, different cookie domain — so a RedNote account is genuinely unauthorized against xiaohongshu.com. Set `XHS_INTERNATIONAL=true` in `.env`. This is the most common cause of the error for overseas users.
  2. **Does the same account search fine in your normal browser?** If yes, the account is healthy and it's the QR-minted session being refused: paste that browser's cookie string into `XHS_COOKIES` (see `.env.example`) to reuse a session the platform already trusts. If no, the account itself is gated — verify the phone number, use it normally for a few days, and stop retrying, since repeated failed logins raise the risk score.

  Test any change cheaply with `infinance smoke` (3 notes, one keyword) instead of a full cycle. Swapping the browser is *not* a lever here: API calls go out over httpx, so which browser drives the login never changes how the search request looks to the platform.
- **"Unknown device" in your XHS login history**: MediaCrawler hardcodes a macOS user-agent. `BROWSER_USER_AGENT` overrides it (defaults to a truthful UA for your actual OS) so the session doesn't contradict the machine it runs on.
- **Trend badges** (🔥 新上榜 / ↑ 升温 / ↓ 降温) compare popularity against the previous fetch cycle, so they appear once two cycles of history exist. Amber/gray on purpose — green/red are reserved for sentiment.
- **Price reality check**: daily closes from Yahoo Finance's public chart API (free, no key, delayed). The 🔀 badge flags sentiment/price divergence (e.g. crowd bullish while the stock fell ≥2%). This is the app's only non-XHS external request; set `ENABLE_PRICE_QUOTES=false` to stay fully offline.
- **Tracked tickers** always render (targeted search: ticker symbol + finance-qualified keywords). Sub-floor tickers appear in the "On the radar" strip.
- **Account safety**: low volume (~100–200 notes/cycle), concurrency 1, 3s sleeps, visible browser, ≥4–6h cadence. Consider a secondary XHS account. MediaCrawler is non-commercial/learning-licensed — keep it personal.
- **Reply threads**: `ENABLE_SUB_COMMENTS=true` crawls comment reply chains (replies reach the LLM as `回复「父评论…」: …` so thread context isn't lost). Off by default — it multiplies requests per note, so weigh it against account risk.
- **Upgrade MediaCrawler**: bump `VENDOR_PIN` in `infinance/providers/mediacrawler.py`, re-run `infinance setup`, then do a small smoke crawl. The integration surface is only the CLI args, JSONL field names, and a few patched lines (`PATCHES` / `CODE_PATCHES`) — the patcher hard-fails with a clear message if upstream renames one.
- **Hit-rate scoreboard** (舆论准确率 panel): whenever an analysis leans clearly one way (|bullish−bearish| ≥ 2), that day's call is later scored against the next-day and 7-day price moves. It needs a few days of accumulated analyses + quotes before it shows anything.
- **Retention**: raw content 7 days, runs/analyses 30 days, cleaned each cycle.
- **Tests**: `uv run pytest`

## License, boundaries & disclaimer

- **infinance itself is MIT-licensed** (see [LICENSE](LICENSE)).
- **MediaCrawler is not part of this software.** `infinance setup` fetches a pinned checkout onto *your* machine, where it runs under its own non-commercial/learning license. infinance talks to it over its CLI only — it is never bundled, imported, or redistributed. Keep usage personal and volumes modest.
- **信息汇总，不是投资建议。** The product summarizes public discussion; it never recommends a stock, never generates buy/sell signals, and ranks only by conversation volume. Every analytical surface carries 仅供参考，不构成投资建议 and a visible data age. The hit-rate scoreboard describes *the crowd's* past leans — it is not a prediction.
- Raw third-party content stays on your machine and is pruned after 7 days; nothing is redistributed.
