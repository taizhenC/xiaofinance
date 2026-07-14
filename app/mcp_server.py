"""An MCP server that lets a coding agent be the analyst, instead of the DeepSeek API.

The pipeline's LLM step is not special: it reads a numbered list of posts and comments about
one ticker, throws out the ones that express no view, counts the rest, and writes a summary.
Claude Code and Codex can do that — so expose the evidence and the result table over MCP and
let the agent already on this machine do it. No API key, no per-token cost, and the ratings
land in the same `stock_analyses` rows the DeepSeek path writes, distinguishable only by the
`model` column.

The one thing that must not drift is *which* items a rating refers to. `notable_quote_ids` are
positions in the evidence list, and that list changes as the crawl window slides — so evidence()
stamps a hash and submit_rating() refuses a rating carrying a stale one, rather than silently
attaching quotes to the wrong posts.

Run:  .venv/Scripts/python.exe -m app.mcp_server        (stdio; see .mcp.json)
"""
from mcp.server.fastmcp import FastMCP

from .analyze import (
    AnalysisResult,
    SentimentCounts,
    analysis_cols,
    analysis_is_current,
    build_prompt,
    gather_items,
    input_hash,
    store_result,
)
from .analyze import evidence_hash as compute_evidence_hash
from .config import settings
from .db import connect
from .mentions import (
    Matcher,
    asset_classes,
    index_tickers,
    investment_tickers,
    load_stock_dict,
    non_stock_tickers,
)
from .scoring import compute_stats, index_board, ranking_and_radar
from .util import norm_text, now_ms

mcp = FastMCP("xiaofinance")

AGENT_MODEL = "agent/mcp"


def _dict_and_indexes():
    d = load_stock_dict()
    return d, non_stock_tickers(d)


def _names(d: dict) -> dict[str, str]:
    return {s["ticker"]: s.get("name_cn", "") for s in d["stocks"]}


def _row(e: dict, names: dict) -> dict:
    return {
        "ticker": e["ticker"], "name_cn": names.get(e["ticker"], ""),
        "score": e["score"], "mentions": e["mentions"],
        "notes": e["note_count"], "comments": e["comment_count"],
    }


@mcp.tool()
def board() -> dict:
    """The current dashboard state: the stock ranking and the 大盘/index board.

    Scores are XHS discussion heat over the fresh window, not price. The two boards are on
    different scales and must not be compared to each other — index terms (纳指/标普) are the
    most-used words in the corpus and score an order of magnitude above any company.
    """
    conn = connect()
    try:
        d, non_stocks = _dict_and_indexes()
        idx, investments = index_tickers(d), investment_tickers(d)
        names, now = _names(d), now_ms()
        stats = compute_stats(conn, settings.fresh_window_ms, now, indexes=non_stocks)
        ranking, radar = ranking_and_radar(
            stats, settings.MIN_MENTIONS_FOR_ANALYSIS, non_stocks
        )
        return {
            "window_hours": settings.FRESH_WINDOW_HOURS,
            "stocks": [_row(e, names) for e in ranking],
            "indexes": [_row(e, names)
                        for e in index_board(stats, idx, settings.MIN_MENTIONS_FOR_ANALYSIS)],
            "investments": [_row(e, names) for e in index_board(
                stats, investments, settings.MIN_MENTIONS_FOR_ANALYSIS
            )],
            "radar": [_row(e, names) for e in radar[:15]],
        }
    finally:
        conn.close()


@mcp.tool()
def pending_ratings() -> list[dict]:
    """Tickers that are on a board and whose evidence has changed since their last rating.

    This is the work queue: rate these, in this order. A ticker whose evidence is unchanged is
    omitted — re-rating it would only duplicate the row.
    """
    conn = connect()
    try:
        d, non_stocks = _dict_and_indexes()
        idx, investments = index_tickers(d), investment_tickers(d)
        names, now = _names(d), now_ms()
        stats = compute_stats(conn, settings.fresh_window_ms, now, indexes=non_stocks)
        ranking, _ = ranking_and_radar(
            stats, settings.MIN_MENTIONS_FOR_ANALYSIS, non_stocks
        )
        indexes = index_board(stats, idx, settings.MIN_MENTIONS_FOR_ANALYSIS)
        investment_board = index_board(
            stats, investments, settings.MIN_MENTIONS_FOR_ANALYSIS
        )
        candidates = ranking[: settings.MAX_ANALYZED_STOCKS] + indexes[:3] + investment_board[:5]

        out = []
        for e in candidates:
            t = e["ticker"]
            items = gather_items(conn, t, settings.fresh_window_ms, now)
            if not items:
                continue
            # ("ok",) only: a `no_api_key` row is a keyless fallback holding quotes and no
            # judgement — that is exactly the work the agent is here to do.
            if analysis_is_current(conn, t, input_hash(items), statuses=("ok",)):
                continue
            out.append({
                "ticker": t, "name_cn": names.get(t, ""), "score": e["score"],
                "mentions": e["mentions"], "evidence_items": len(items),
                "is_index": t in idx,
                "asset_class": asset_classes(d).get(t, "stock"),
            })
        return out
    finally:
        conn.close()


@mcp.tool()
def evidence(ticker: str) -> dict:
    """The numbered posts and comments to rate this ticker on, plus the rubric.

    `items` is exactly what the DeepSeek path is shown, so a rating produced from it is
    comparable. Cite quotes by item number via `notable_quote_ids` — do not retype the text.
    Pass `evidence_hash` back to submit_rating unchanged.
    """
    conn = connect()
    try:
        d = load_stock_dict()
        now = now_ms()
        items = gather_items(conn, ticker, settings.fresh_window_ms, now)
        if not items:
            return {"ticker": ticker, "items": [], "error": "no fresh items for this ticker"}
        _, rubric = build_prompt(
            ticker, _names(d).get(ticker, ""), items, settings.SUMMARY_LANG, now,
            window_hours=settings.FRESH_WINDOW_HOURS,
            asset_type=asset_classes(d).get(ticker, "stock"),
        )
        return {
            "ticker": ticker,
            "evidence_hash": compute_evidence_hash(items),
            "item_count": len(items),
            "instructions": rubric,
            "items": [
                {"n": n, "type": i["type"], "likes": i["likes"],
                 "age_hours": max(0, (now - i["ts"]) // 3_600_000),
                 "aside": bool(i.get("aside")), "fanout": i.get("fanout", 1),
                 "text": i.get("prompt_text") or i["text"]}
                for n, i in enumerate(items, 1)
            ],
        }
    finally:
        conn.close()


@mcp.tool()
def submit_rating(ticker: str, evidence_hash: str, summary: str,
                  bullish: int, bearish: int, neutral: int,
                  bull_points: list[str], bear_points: list[str],
                  notable_quote_ids: list[int], irrelevant_item_count: int = 0) -> dict:
    """File a rating. It lands in the same table the DeepSeek path writes, and shows on the card.

    `evidence_hash` must be the one evidence() returned. If the evidence has moved since — an
    item aged out, a crawl landed, or a post went viral and the ranking reshuffled — the rating
    is rejected rather than pinned to items it was never shown. Call evidence() again and re-rate.
    """
    conn = connect()
    try:
        now = now_ms()
        items = gather_items(conn, ticker, settings.fresh_window_ms, now)
        if not items:
            return {"status": "error", "error": f"no fresh items for {ticker}"}
        current = compute_evidence_hash(items)
        if evidence_hash != current:
            return {
                "status": "stale_evidence",
                "error": "the evidence moved since you read it — call evidence() again and re-rate",
                "expected": current, "got": evidence_hash,
            }

        d, idx = _dict_and_indexes()
        stats = compute_stats(conn, settings.fresh_window_ms, now, indexes=idx)
        score = stats.get(ticker, {}).get("score", 0.0)

        result = AnalysisResult(
            summary=summary,
            sentiment_counts=SentimentCounts(bullish=bullish, bearish=bearish, neutral=neutral),
            bull_points=bull_points, bear_points=bear_points,
            notable_quote_ids=notable_quote_ids,
            irrelevant_item_count=irrelevant_item_count,
        )
        cols = analysis_cols(ticker, items, settings, score, None, now, AGENT_MODEL)
        quotes = store_result(conn, ticker, items, result, cols)
        return {"status": "ok", "ticker": ticker, "stored_quotes": quotes,
                "model": AGENT_MODEL}
    finally:
        conn.close()


@mcp.tool()
def search_corpus(query: str, limit: int = 20) -> dict:
    """Raw substring search over the crawled notes and comments.

    Use it to check what a term actually means *in this corpus* before trusting it. The
    dictionary matcher has no word boundaries in Chinese, so a plausible alias can be poison:
    减肥药 has 16 hits here and every one is a personal diet-pill diary, not Eli Lilly.
    """
    conn = connect()
    try:
        q = f"%{query}%"
        # counted over the whole corpus, not over the sample — a capped count would understate
        # exactly the terms that are too common to be safe, which is the question being asked
        note_hits = conn.execute(
            "SELECT COUNT(*) FROM notes WHERE title LIKE ? OR note_desc LIKE ?", (q, q)
        ).fetchone()[0]
        comment_hits = conn.execute(
            "SELECT COUNT(*) FROM comments WHERE content LIKE ?", (q,)
        ).fetchone()[0]
        notes = conn.execute(
            """SELECT note_id, title, note_desc, liked_count FROM notes
               WHERE title LIKE ? OR note_desc LIKE ? ORDER BY liked_count DESC LIMIT ?""",
            (q, q, limit),
        ).fetchall()
        cmts = conn.execute(
            "SELECT comment_id, content, like_count FROM comments WHERE content LIKE ?"
            " ORDER BY like_count DESC LIMIT ?",
            (q, limit),
        ).fetchall()
        return {
            "query": query,
            "note_hits": note_hits, "comment_hits": comment_hits,
            "showing": {"notes": len(notes), "comments": len(cmts)},
            "notes": [{"id": r["note_id"], "likes": r["liked_count"],
                       "text": f"{r['title'] or ''} {r['note_desc'] or ''}".strip()[:300]}
                      for r in notes],
            "comments": [{"id": r["comment_id"], "likes": r["like_count"],
                          "text": (r["content"] or "")[:200]} for r in cmts],
        }
    finally:
        conn.close()


@mcp.tool()
def unmatched_finance_notes(limit: int = 30) -> list[dict]:
    """Notes that talk about investing but matched NO ticker — the slang-mining pool.

    If a note here is clearly about a company, the dictionary is missing the word it used.
    Report it with suggest_alias(). This is the job app/slang_scan.py was written to give
    DeepSeek; an agent can do it without the API key.
    """
    conn = connect()
    try:
        d = load_stock_dict()
        matcher = Matcher(d)
        cutoff = now_ms() - settings.context_window_ms
        rows = conn.execute(
            """SELECT note_id, title, note_desc, liked_count FROM notes
               WHERE publish_time_ms >= ? AND dup_group_id IS NULL
                 AND NOT EXISTS (SELECT 1 FROM stock_mentions m
                                 WHERE m.source_type='note' AND m.source_id = notes.note_id)
               ORDER BY liked_count DESC LIMIT 200""",
            (cutoff,),
        ).fetchall()
        out = []
        for r in rows:
            text = f"{r['title'] or ''} {r['note_desc'] or ''}".strip()
            if text and matcher.has_context(norm_text(text).lower()):
                out.append({"note_id": r["note_id"], "likes": r["liked_count"], "text": text[:400]})
            if len(out) >= limit:
                break
        return out
    finally:
        conn.close()


@mcp.tool()
def suggest_alias(term: str, ticker: str, evidence_quote: str = "") -> dict:
    """Propose a dictionary alias for human review. Never auto-applied.

    Before proposing, run search_corpus(term) and read the hits. A term is only worth adding if
    the posts that contain it are actually about the stock — and check it is not a substring of
    an ordinary word, because the Chinese matcher will happily fire inside one (女大 fires
    inside 女大学生; 多多 inside 多多关照).
    """
    conn = connect()
    try:
        cur = conn.execute(
            """INSERT OR IGNORE INTO alias_suggestions
               (term, guessed_ticker, evidence_quote, suggested_at_ms) VALUES(?,?,?,?)""",
            (term.strip(), ticker.strip().upper(), evidence_quote.strip()[:300], now_ms()),
        )
        conn.commit()
        return {"status": "ok" if cur.rowcount else "already_suggested",
                "term": term, "ticker": ticker.upper()}
    finally:
        conn.close()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
