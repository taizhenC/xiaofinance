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


TREND_PCT = 25
TREND_MIN_ABS_DELTA = 2.0


def snapshot_scores(conn, stats: dict[str, dict], run_id: int | None, now: int | None = None) -> int:
    """One row per ticker per fetch cycle; all rows of a cycle share snapped_at_ms
    so consecutive cycles can be compared apples-to-apples."""
    now = now or now_ms()
    rows = [
        (e["ticker"], run_id, now, e["score"], e["mentions"], e["note_count"], e["comment_count"])
        for e in stats.values()
    ]
    conn.executemany(
        """INSERT INTO score_snapshots(ticker, run_id, snapped_at_ms, score, mentions, note_count, comment_count)
           VALUES(?,?,?,?,?,?,?) ON CONFLICT(ticker, snapped_at_ms) DO NOTHING""",
        rows,
    )
    conn.commit()
    return len(rows)


def compute_trends(conn) -> dict[str, dict]:
    """Heat trend per ticker: latest snapshot cycle vs the one before it.
    Empty until two cycles of history exist. Small absolute moves are damped
    to 'flat' so low-score tickers don't flap on percentage noise."""
    cycles = conn.execute(
        "SELECT DISTINCT snapped_at_ms FROM score_snapshots ORDER BY snapped_at_ms DESC LIMIT 2"
    ).fetchall()
    if len(cycles) < 2:
        return {}
    latest_ts, prev_ts = cycles[0][0], cycles[1][0]
    prev = {
        r["ticker"]: r["score"]
        for r in conn.execute("SELECT ticker, score FROM score_snapshots WHERE snapped_at_ms=?", (prev_ts,))
    }
    trends: dict[str, dict] = {}
    for r in conn.execute("SELECT ticker, score FROM score_snapshots WHERE snapped_at_ms=?", (latest_ts,)):
        ticker, score = r["ticker"], r["score"]
        p = prev.get(ticker)
        if p is None:
            trends[ticker] = {"dir": "new", "delta_pct": None, "prev_score": 0.0}
            continue
        delta = score - p
        pct = round(delta / p * 100) if p > 0 else None
        if pct is not None and pct >= TREND_PCT and delta >= TREND_MIN_ABS_DELTA:
            d = "up"
        elif pct is not None and pct <= -TREND_PCT and -delta >= TREND_MIN_ABS_DELTA:
            d = "down"
        else:
            d = "flat"
        trends[ticker] = {"dir": d, "delta_pct": pct, "prev_score": p}
    return trends


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
