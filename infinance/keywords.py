"""Pick each cycle's search keywords, rotating a pool so the sample isn't one sector.

The crawl budget is fixed (account risk, not compute), so widening coverage means
spending the same ~10 keywords on different topics over time rather than crawling more
at once. Pool picks run BEFORE core: core runs every cycle and can afford the tail,
while a pool keyword gets one shot per wrap — behind core it never survived to run,
because the CAPTCHA wall lands mid-cycle. The cursor advances only past the leading
pool picks that were actually sampled, so a keyword the wall ate leads the next cycle
instead of waiting a full wrap.
"""

from .db import meta_get, meta_set

KEYWORD_CURSOR = "keyword_cursor"


def rotation_candidates(settings) -> list[str]:
    core = settings.discovery_core_list
    pool = settings.discovery_pool_list + settings.discovery_investment_pool_list
    return [k for k in dict.fromkeys(pool) if k not in core]


def select_keywords(conn, settings) -> tuple[list[str], dict | None]:
    """Returns (keywords for this cycle, rotation state for advance_rotation)."""
    candidates = rotation_candidates(settings)
    if not candidates:  # rotation is opt-in: no pool means the old static list
        return settings.discovery_keywords_list[: settings.KEYWORDS_PER_CYCLE], None

    core = settings.discovery_core_list
    slots = max(settings.KEYWORDS_PER_CYCLE - len(core), 0)
    take = min(slots, len(candidates))
    if take == 0:
        return core[: settings.KEYWORDS_PER_CYCLE], None

    cursor = int(meta_get(conn, KEYWORD_CURSOR, "0") or 0) % len(candidates)
    picked = [candidates[(cursor + i) % len(candidates)] for i in range(take)]
    return picked + core, {"picked": picked, "cursor": cursor, "pool_size": len(candidates)}


def advance_rotation(conn, rotation: dict | None, sampled: set[str]) -> None:
    """Move the cursor past the leading pool picks that were sampled. Prefix, not count:
    the cursor is positional, so an unsampled pick mid-list must stop the advance or the
    picks after it would be skipped for a full wrap."""
    if not rotation:
        return
    n = 0
    for kw in rotation["picked"]:
        if kw not in sampled:
            break
        n += 1
    if n:
        meta_set(conn, KEYWORD_CURSOR, str((rotation["cursor"] + n) % rotation["pool_size"]))


def yield_stats(conn, run_id: int | None = None, since_ms: int | None = None) -> list[dict]:
    """Per-keyword hit rate: how many of its notes name a US stock at all.

    A keyword that returns 定投/ETF diaries or A-share posts scores near zero and is
    spending crawl budget on notes nothing can ever be extracted from.
    """
    where, params = "1=1", []
    if run_id is not None:
        where, params = "n.last_seen_run_id = ?", [run_id]
    elif since_ms is not None:
        where, params = "n.fetched_at_ms >= ?", [since_ms]

    rows = conn.execute(
        f"""
        SELECT n.source_keyword AS keyword,
               COUNT(DISTINCT n.note_id) AS notes,
               COUNT(DISTINCT m.source_id) AS with_stock,
               COUNT(DISTINCT m.ticker) AS tickers,
               GROUP_CONCAT(DISTINCT m.ticker) AS ticker_list
        FROM notes n
        LEFT JOIN stock_mentions m ON m.source_type = 'note' AND m.source_id = n.note_id
        WHERE {where} AND n.source_keyword IS NOT NULL
        GROUP BY 1
        ORDER BY 3 DESC, 2 DESC
        """,
        params,
    ).fetchall()

    return [
        {
            "keyword": r["keyword"],
            "notes": r["notes"],
            "with_stock": r["with_stock"],
            "hit_rate": round(r["with_stock"] / r["notes"], 2) if r["notes"] else 0.0,
            "tickers": sorted(filter(None, (r["ticker_list"] or "").split(","))),
        }
        for r in rows
    ]
