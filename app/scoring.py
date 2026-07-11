import math

from .util import now_ms


def _entry(stats: dict, ticker: str) -> dict:
    return stats.setdefault(ticker, {
        "ticker": ticker,
        "note_count": 0, "comment_count": 0,
        "note_count_raw": 0, "comment_count_raw": 0,
        "_note_like_sum": 0.0, "_comment_like_sum": 0.0,
        "latest_item_ms": 0,
        "top_quote": None, "_top_quote_likes": -1,
    })


def compute_stats(conn, fresh_window_ms: int, now: int | None = None) -> dict[str, dict]:
    """Per-ticker fresh-window stats. Counts and like-sums are over dup clusters
    (canonical items only); *_raw counts include repost duplicates."""
    now = now or now_ms()
    cutoff = now - fresh_window_ms
    stats: dict[str, dict] = {}

    for r in conn.execute(
        """SELECT m.ticker, m.content_time_ms, n.liked_count AS likes, n.title, n.note_desc,
                  n.dup_group_id
           FROM stock_mentions m JOIN notes n ON n.note_id = m.source_id
           WHERE m.source_type='note' AND m.content_time_ms >= ?""",
        (cutoff,),
    ):
        e = _entry(stats, r["ticker"])
        e["note_count_raw"] += 1
        e["latest_item_ms"] = max(e["latest_item_ms"], r["content_time_ms"])
        if r["dup_group_id"] is None:
            e["note_count"] += 1
            e["_note_like_sum"] += math.log10(1 + max(0, r["likes"]))
            if r["likes"] > e["_top_quote_likes"]:
                e["_top_quote_likes"] = r["likes"]
                e["top_quote"] = (r["title"] or r["note_desc"] or "").strip()[:100]

    for r in conn.execute(
        """SELECT m.ticker, m.content_time_ms, c.like_count AS likes, c.content, c.dup_group_id
           FROM stock_mentions m JOIN comments c ON c.comment_id = m.source_id
           WHERE m.source_type='comment' AND m.content_time_ms >= ?""",
        (cutoff,),
    ):
        e = _entry(stats, r["ticker"])
        e["comment_count_raw"] += 1
        e["latest_item_ms"] = max(e["latest_item_ms"], r["content_time_ms"])
        if r["dup_group_id"] is None:
            e["comment_count"] += 1
            e["_comment_like_sum"] += math.log10(1 + max(0, r["likes"]))
            if r["likes"] > e["_top_quote_likes"]:
                e["_top_quote_likes"] = r["likes"]
                e["top_quote"] = (r["content"] or "").strip()[:100]

    for e in stats.values():
        e["score"] = round(
            3 * e["note_count"] + e["comment_count"]
            + 2 * e["_note_like_sum"] + 0.5 * e["_comment_like_sum"],
            2,
        )
        e["mentions"] = e["note_count"] + e["comment_count"]
    return stats


def ranking_and_radar(stats: dict[str, dict], min_mentions: int) -> tuple[list[dict], list[dict]]:
    ranking = sorted(
        (e for e in stats.values() if e["mentions"] >= min_mentions),
        key=lambda e: e["score"], reverse=True,
    )
    radar = sorted(
        (e for e in stats.values() if 1 <= e["mentions"] < min_mentions),
        key=lambda e: (e["mentions"], e["score"]), reverse=True,
    )
    return ranking, radar
