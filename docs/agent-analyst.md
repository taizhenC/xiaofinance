# The agent as the analyst (instead of the DeepSeek API)

The LLM step in this pipeline is not privileged. It is handed a numbered list of posts and
comments about one ticker, told to throw out the ones that express no view, count the rest, and
write a summary. Claude Code and Codex can do that — and they are already running on this
machine, already paid for.

So `app/mcp_server.py` exposes the evidence and the results table over MCP. The agent becomes
the analyst. No `DEEPSEEK_API_KEY`, no per-token cost, and the ratings land in the same
`stock_analyses` rows the DeepSeek path writes — the dashboard cannot tell them apart except by
the `model` column (`agent/mcp` vs `deepseek-chat`).

## Turn it on

**Claude Code** — `.mcp.json` in the repo root is picked up automatically. Start Claude Code
from the project directory and approve the server when prompted. Check it with `/mcp`.

**Codex** — add to `~/.codex/config.toml`:

```toml
[mcp_servers.xiaofinance]
command = "C:\\Coding\\Test\\xiaofinance\\.venv\\Scripts\\python.exe"
args = ["-m", "app.mcp_server"]
cwd = "C:\\Coding\\Test\\xiaofinance"
env = { PYTHONIOENCODING = "utf-8", PYTHONUTF8 = "1" }
```

## Rate the board

Say: **"rate the pending tickers"**. The loop is:

1. `pending_ratings()` — the work queue. Tickers on a board whose evidence has changed since
   their last rating. A `no_api_key` row does **not** count as rated: it holds fallback quotes
   and no judgement, which is exactly the gap you are filling.
2. `evidence(ticker)` — the numbered items, plus the same rubric the DeepSeek prompt carries.
   Read the markers: `[顺带提及]` means the ticker is named once in a long post that is about
   something else; `↳` means the line is a reply to the one above it and must be read with it.
3. `submit_rating(...)` — cite quotes by **item number** (`notable_quote_ids`), never by
   retyping the text. Pass back the `evidence_hash` you were given.

The hash is not ceremony. Item numbers are **positions in a list**, and the list is a live
query — "posts about this ticker in the last 24 hours, ranked by likes" — not a snapshot. It
moves under you in two different ways:

- an item **ages out** of the window, or a crawl lands new ones, so the set changes
- a post **goes viral** and the ranking reshuffles, so the set is identical but `[3]` is now a
  different post

The second one is the subtle one, and the first version of this guard missed it: it hashed the
*sorted* item ids, so a pure reorder produced an identical hash, the check passed, and quote
`[3]` silently resolved to a post the agent had never read. Hence two hashes, answering two
different questions:

| | question | order matters? |
| --- | --- | --- |
| `input_hash` | is there new material to read? | no — a reshuffle is not new material, and re-paying DeepSeek to re-read it would be waste |
| `evidence_hash` | does item `[3]` still mean what it meant? | **yes** — that is the entire question |

`submit_rating` refuses a stale `evidence_hash` and stores nothing. If you are refused, call
`evidence()` again and re-rate against the current list.

## Mine the dictionary's blind spots

The other thing DeepSeek was for. `app/slang_scan.py` asks it to find nicknames the dictionary
is missing; without a key it has never run. An agent can do the same job better, because it can
check its own guesses:

- `unmatched_finance_notes()` — notes that talk about investing but matched no ticker. If one
  is clearly about a company, the dictionary is missing the word it used.
- `search_corpus(term)` — what does this term actually mean *in this corpus*?
- `suggest_alias(term, ticker, evidence)` — file it for review. Never auto-applied.

**Search before you suggest.** The Chinese matcher is a plain substring test with no word
boundaries, so a plausible alias can be actively harmful:

| term | looks like | actually |
| --- | --- | --- |
| `减肥药` | Eli Lilly / Novo | 16 hits in this corpus, every one a personal diet-pill diary |
| `多多` | PDD | lives inside 多多关照, 多多益善, 多多少少 |
| `小摩` | JPMorgan | lives inside 小摩托 — everywhere on Xiaohongshu |
| `女大` | NVDA (real slang!) | lives inside 女大学生 |
| `纳斯达克` | QQQ | also 纳斯达克上市 — a listing venue, not a view on the index |

`search_corpus` reports the **true** corpus count, not the size of the page it returns, because
a term is dangerous precisely when it is common. Terms that survive still need a `traps` entry
in `stock_dict.json` if they are substrings of ordinary words.
