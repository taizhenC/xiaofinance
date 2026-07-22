# xiaofinance — 小红书投资热度看板

[![CI](https://github.com/taizhenC/xiaofinance/actions/workflows/ci.yml/badge.svg)](https://github.com/taizhenC/xiaofinance/actions/workflows/ci.yml)

A local personal dashboard that answers: **which US stocks and investment themes are hot on Xiaohongshu right now, and what do people think of them?**

Data comes from your own XHS account via a pinned [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) checkout (QR login, no API key). A local dictionary detects stocks, indexes and diversified investments (gold, bonds, funds, crypto, FX), understands Chinese aliases and retail 黑话 — including context-gated terms such as 大黄/Yellow for gold — and extracts discussion tags such as 理财/定投/资产配置. Repost spam is collapsed via simhash clustering, and an LLM (or an agent over MCP) summarizes sentiment — with every rendered quote verified verbatim against its sources. Only content from the **last 24 hours** is analyzed and shown.

**信息汇总，不是投资建议** — the product never recommends a stock, never generates buy/sell signals, and ranks only by conversation volume.

## Quick start

Works the same on Windows and macOS. Needs [uv](https://docs.astral.sh/uv/) and git.

```
pipx install xiaofinance     # or: git clone … && uv sync  (then prefix commands with `uv run`)
xiaofinance setup            # fetch MediaCrawler (pinned), install deps + Chromium, scaffold .env
xiaofinance run              # open http://127.0.0.1:8000 — the first-run wizard takes it from here
```

The wizard walks through: acknowledgment → environment check → QR login → first crawl (with live progress). Until real data exists the dashboard shows a clearly watermarked demo dataset. Optional: put `DEEPSEEK_API_KEY=sk-...` in `.env` for AI summaries (keyless mode shows top quotes instead).

Something broken? `xiaofinance doctor` diagnoses and prints the fix for each problem.

## Documentation

| Doc | Contents |
|---|---|
| [docs/install.md](docs/install.md) | 10-minute install guide (Windows/macOS), prerequisites, commands |
| [docs/account-safety.md](docs/account-safety.md) | What the tool does with your account, honest risk framing, the built-in guardrails, secondary-account advice |
| [docs/troubleshooting.md](docs/troubleshooting.md) | The login decision tree (expired / rednote-backend mismatch / gated account) and everything else |
| [docs/upgrade.md](docs/upgrade.md) | Upgrading the app, automatic DB migrations + backups, vendor re-pinning |
| [docs/how-it-works.md](docs/how-it-works.md) | The pipeline, trust mechanisms (verbatim quotes, scoreboard), boundaries |
| [docs/faq.md](docs/faq.md) | Everything else, honestly answered |

## For developers

```
uv sync                                # backend deps + editable install
uv run pytest                          # test suite
uv run ruff check xiaofinance tests      # lint
npm ci --prefix frontend               # frontend toolchain (Vite + TS + Preact)
npm run build --prefix frontend        # build web UI into xiaofinance/webui/
npm run dev --prefix frontend          # dev server on :5173, proxies /api to :8000
```

Architecture notes live in [roadmap/technical_architecture.md](roadmap/technical_architecture.md). The crawler sits behind a `SourceProvider` interface (`xiaofinance/providers/`); the pipeline (`ingest → dedup → mentions → score → analyze`) is pure-ish modules over SQLite with versioned migrations.

## License, boundaries & disclaimer

- **xiaofinance itself is MIT-licensed** (see [LICENSE](LICENSE)).
- **MediaCrawler is not part of this software.** `xiaofinance setup` fetches a pinned checkout onto *your* machine, where it runs under its own non-commercial/learning license. xiaofinance talks to it over its CLI only — it is never bundled, imported, or redistributed. Keep usage personal and volumes modest.
- **信息汇总，不是投资建议。** Every analytical surface carries 仅供参考，不构成投资建议 and a visible data age. The hit-rate scoreboard describes *the crowd's* past leans — it is not a prediction.
- Raw third-party content stays on your machine and is pruned after 7 days; nothing is redistributed.

## Beyond US stocks, and an optional agent analyst

The dictionary also covers **indexes** (纳指/纳斯达克/标普 get their own board, scored like any ticker but kept off the stock ranking so an index riding along in a post can't dilute the stock beside it) and **diversified investments** (gold, silver, bonds, funds, crypto, FX — e.g. 大黄/Yellow for gold, gated on investment context). Discussion tags such as 理财/定投/资产配置 are extracted separately from assets.

`DEEPSEEK_API_KEY` is optional in a second way: instead of DeepSeek, you can point a coding agent at the corpus over MCP (`xiaofinance/mcp_server.py`) and let it write the ratings — same evidence, same validation, same `stock_analyses` row, only the `model` column differs. See [docs/agent-analyst.md](docs/agent-analyst.md).
