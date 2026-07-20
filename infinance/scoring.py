import math

from .mentions import _compile_alias, alias_hits, is_aside
from .util import (
    QUOTE_MIN_SUBSTANCE,
    clean_tags,
    norm_text,
    note_text,
    now_ms,
    strip_hashtags,
    substance,
)

# a source naming more tickers than this is a roundup (财报日历/涨幅盘点), not discussion
FOCUS_MAX_FANOUT = 3
# What a passing mention is worth. Fan-out already discounts a post that lists a dozen
# tickers; this discounts the other kind — a long post that names one ticker once and is
# about something else entirely. It still counts for something, because being name-dropped
# is weak evidence of relevance, and it still counts as a mention, so nothing disappears
# from the radar or from the model's input.
ASIDE_WEIGHT = 0.3


def _entry(stats: dict, ticker: str) -> dict:
    return stats.setdefault(ticker, {
        "ticker": ticker,
        "note_count": 0, "comment_count": 0,
        "note_count_raw": 0, "comment_count_raw": 0,
        "focused_mentions": 0,
        "_note_w": 0.0, "_comment_w": 0.0,
        "_note_like_sum": 0.0, "_comment_like_sum": 0.0,
        "latest_item_ms": 0,
        "top_quote": None, "_top_quote_key": (-1, -1, -1, -1),
    })


def _quote_key(text: str, focused: bool, likes: int, aside: bool = False) -> tuple[int, ...]:
    """Readable, then about-the-ticker, then focused, then popular. The most-liked source for
    a ticker is often an image post whose desc is a tag block, or a long post that names it
    once — neither says anything when quoted back."""
    return (
        1 if substance(text) >= QUOTE_MIN_SUBSTANCE else 0,
        0 if aside else 1,
        1 if focused else 0,
        likes,
    )


def source_tickers(conn, source_type: str, cutoff: int) -> dict[str, set[str]]:
    """source_id -> the set of tickers that source mentions."""
    out: dict[str, set[str]] = {}
    for source_id, ticker in conn.execute(
        "SELECT source_id, ticker FROM stock_mentions WHERE source_type=? AND content_time_ms>=?",
        (source_type, cutoff),
    ):
        out.setdefault(source_id, set()).add(ticker)
    return out


def fanout(peers: set[str], ticker: str, indexes: set[str]) -> int:
    """How many tickers of the same kind this source names, the ticker itself included.

    Counted within a class, not across it. A post arguing "英伟达带动纳指新高" is a dedicated
    NVDA post that happens to say where the index went — charging NVDA a 1/2 fan-out for it
    would punish the stock for the market context around it. Indexes divide among indexes for
    the same reason, so a 纳指/标普/道指 roundup does not read as three dedicated posts."""
    is_index = ticker in indexes
    return max(sum(1 for p in peers if (p in indexes) == is_index), 1)


def source_fanout(conn, source_type: str, cutoff: int) -> dict[str, int]:
    """source_id -> number of distinct tickers that source mentions."""
    return {sid: len(ts) for sid, ts in source_tickers(conn, source_type, cutoff).items()}


def compute_stats(conn, fresh_window_ms: int, now: int | None = None,
                  indexes: set[str] | None = None) -> dict[str, dict]:
    """Per-ticker fresh-window stats. Counts and like-sums are over dup clusters
    (canonical items only); *_raw counts include repost duplicates.

    A source mentioning k tickers contributes weight 1/k to each, so a calendar
    post name-dropping a dozen tickers no longer counts like a dedicated post.
    focused_mentions counts canonical sources where the ticker appears in the
    prose of a low-fanout item — a match living only inside a #话题# tag block
    (or a 12-ticker roundup) keeps the ticker off the main ranking by itself."""
    now = now or now_ms()
    cutoff = now - fresh_window_ms
    indexes = indexes or set()
    stats: dict[str, dict] = {}
    note_peers = source_tickers(conn, "note", cutoff)
    comment_peers = source_tickers(conn, "comment", cutoff)
    alias_fns: dict[str, object] = {}

    def in_prose(alias: str | None, title, desc) -> bool:
        if not alias:
            return False
        fn = alias_fns.get(alias)
        if fn is None:
            fn = alias_fns[alias] = _compile_alias(alias)
        return fn(strip_hashtags(norm_text(f"{title or ''}\n{desc or ''}")).lower())

    for r in conn.execute(
        """SELECT m.ticker, m.content_time_ms, m.source_id, m.matched_alias,
                  n.liked_count AS likes, n.title, n.note_desc, n.dup_group_id
           FROM stock_mentions m JOIN notes n ON n.note_id = m.source_id
           WHERE m.source_type='note' AND m.content_time_ms >= ?""",
        (cutoff,),
    ):
        e = _entry(stats, r["ticker"])
        e["note_count_raw"] += 1
        e["latest_item_ms"] = max(e["latest_item_ms"], r["content_time_ms"])
        if r["dup_group_id"] is None:
            text = clean_tags(note_text(r["title"], r["note_desc"]))
            _, hits = alias_hits(text, r["matched_alias"] or "")
            aside = is_aside(text, hits)
            k = fanout(note_peers.get(r["source_id"], set()), r["ticker"], indexes)
            w = 1.0 / k * (ASIDE_WEIGHT if aside else 1.0)
            # Being name-dropped still qualifies a ticker for the board — it is just worth
            # less. Dropping it there would hide a name nobody argued about but everybody
            # listed, and that absence is itself worth seeing.
            focused = k <= FOCUS_MAX_FANOUT and in_prose(r["matched_alias"], r["title"], r["note_desc"])
            e["note_count"] += 1
            e["_note_w"] += w
            e["_note_like_sum"] += w * math.log10(1 + max(0, r["likes"]))
            if focused:
                e["focused_mentions"] += 1
            key = _quote_key(text, focused, r["likes"], aside)
            if key > e["_top_quote_key"]:
                e["_top_quote_key"] = key
                e["top_quote"] = text[:100]

    for r in conn.execute(
        """SELECT m.ticker, m.content_time_ms, m.source_id, c.like_count AS likes, c.content,
                  c.dup_group_id
           FROM stock_mentions m JOIN comments c ON c.comment_id = m.source_id
           WHERE m.source_type='comment' AND m.content_time_ms >= ?""",
        (cutoff,),
    ):
        e = _entry(stats, r["ticker"])
        e["comment_count_raw"] += 1
        e["latest_item_ms"] = max(e["latest_item_ms"], r["content_time_ms"])
        if r["dup_group_id"] is None:
            k = fanout(comment_peers.get(r["source_id"], set()), r["ticker"], indexes)
            w = 1.0 / k
            e["comment_count"] += 1
            e["_comment_w"] += w
            e["_comment_like_sum"] += w * math.log10(1 + max(0, r["likes"]))
            focused = k <= FOCUS_MAX_FANOUT
            if focused:
                e["focused_mentions"] += 1
            text = " ".join((r["content"] or "").split())
            key = _quote_key(text, focused, r["likes"])
            if key > e["_top_quote_key"]:
                e["_top_quote_key"] = key
                e["top_quote"] = text[:100]

    for e in stats.values():
        e["score"] = round(
            3 * e["_note_w"] + e["_comment_w"]
            + 2 * e["_note_like_sum"] + 0.5 * e["_comment_like_sum"],
            2,
        )
        e["mentions"] = e["note_count"] + e["comment_count"]
    return stats


TREND_PCT = 25
TREND_MIN_ABS_DELTA = 2.0
# below this base score, percentages read as noise (+409% off a 2-point base);
# the badge keeps its direction but drops the number
TREND_PCT_BASE_MIN = 5.0


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
        pct_raw = delta / p * 100 if p > 0 else None
        if pct_raw is not None and pct_raw >= TREND_PCT and delta >= TREND_MIN_ABS_DELTA:
            d = "up"
        elif pct_raw is not None and pct_raw <= -TREND_PCT and -delta >= TREND_MIN_ABS_DELTA:
            d = "down"
        else:
            d = "flat"
        pct = round(pct_raw) if pct_raw is not None and p >= TREND_PCT_BASE_MIN else None
        trends[ticker] = {"dir": d, "delta_pct": pct, "prev_score": p}
    return trends


def score_history(conn, tickers: list[str], limit_cycles: int = 30) -> dict[str, list[dict]]:
    """Per-ticker score series over the last N snapshot cycles, oldest first.
    Cycles where a ticker had no mentions are zero-filled so sparklines show
    the ticker going quiet instead of skipping the gap."""
    cycles = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT snapped_at_ms FROM score_snapshots ORDER BY snapped_at_ms DESC LIMIT ?",
            (limit_cycles,),
        )
    ]
    if not cycles or not tickers:
        return {t: [] for t in tickers}
    cycles.reverse()
    by_ticker: dict[str, dict[int, float]] = {t: {} for t in tickers}
    for r in conn.execute(
        "SELECT ticker, snapped_at_ms, score FROM score_snapshots WHERE snapped_at_ms >= ?",
        (cycles[0],),
    ):
        if r["ticker"] in by_ticker:
            by_ticker[r["ticker"]][r["snapped_at_ms"]] = r["score"]
    return {
        t: [{"ts": ts, "score": by_ticker[t].get(ts, 0.0)} for ts in cycles]
        for t in tickers
    }


def is_rankable(e: dict, min_mentions: int) -> bool:
    """Main-board eligibility: enough mentions AND at least one focused source —
    tickers carried only by roundup posts or tag blocks stay on the radar."""
    return e.get("mentions", 0) >= min_mentions and e.get("focused_mentions", 0) >= 1


def ranking_and_radar(stats: dict[str, dict], min_mentions: int,
                      indexes: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    indexes = indexes or set()
    stocks = [e for e in stats.values() if e["ticker"] not in indexes]
    ranking = sorted(
        (e for e in stocks if is_rankable(e, min_mentions)),
        key=lambda e: e["score"], reverse=True,
    )
    radar = sorted(
        (e for e in stocks if e["mentions"] >= 1 and not is_rankable(e, min_mentions)),
        key=lambda e: (e["mentions"], e["score"]), reverse=True,
    )
    return ranking, radar


def index_board(stats: dict[str, dict], indexes: set[str], min_mentions: int) -> list[dict]:
    """The 大盘 strip. Same scoring as a stock, its own ranking — index talk is the bulk of
    what XHS says about US markets (纳指 and 标普 outrun every company name in the corpus),
    and it is worth reading on its own terms rather than not at all."""
    return sorted(
        (e for e in stats.values()
         if e["ticker"] in indexes and e.get("mentions", 0) >= min_mentions),
        key=lambda e: e["score"], reverse=True,
    )


def radar_entries(stats: dict[str, dict], exclude: set[str]) -> list[dict]:
    """Everything mentioned in the context window that the board didn't show.

    Fed the wider window, this is where a sector that only produces a post a day becomes
    visible at all — it would never clear the 24h board's bar.
    """
    return sorted(
        (e for e in stats.values() if e.get("mentions", 0) >= 1 and e["ticker"] not in exclude),
        key=lambda e: (e["mentions"], e["score"]), reverse=True,
    )


OTHER_SECTOR = "其他"


def sector_breakdown(stats: dict[str, dict], sectors: dict[str, str],
                     exclude: set[str] | None = None) -> list[dict]:
    """What today's discussion is made of, by share of weighted score.

    Reports rather than corrects: the board stays a pure ranking, and a semiconductor day
    is allowed to look like one. The point is that a 90% semis reading is visible instead
    of being mistaken for the market's whole conversation.

    Indexes are excluded (they have their own board), or the answer to "which sectors are
    being talked about" would be swamped by a single ETF指数 slice worth more than half the
    corpus — true, but not what the question is asking.
    """
    exclude = exclude or set()
    agg: dict[str, dict] = {}
    for ticker, e in stats.items():
        if e.get("mentions", 0) < 1 or ticker in exclude:
            continue
        sector = sectors.get(ticker) or OTHER_SECTOR
        a = agg.setdefault(
            sector,
            {"sector": sector, "score": 0.0, "mentions": 0, "tickers": 0, "leader": None},
        )
        a["score"] += e.get("score", 0.0)
        a["mentions"] += e.get("mentions", 0)
        a["tickers"] += 1
        if a["leader"] is None or e.get("score", 0.0) > a["leader"]["score"]:
            a["leader"] = {
                "ticker": ticker,
                "score": e.get("score", 0.0),
                "mentions": e.get("mentions", 0),
                "focused_mentions": e.get("focused_mentions", 0),
            }

    total = sum(a["score"] for a in agg.values())
    for a in agg.values():
        a["score"] = round(a["score"], 2)
        a["share"] = round(a["score"] / total * 100, 1) if total else 0.0
    return sorted(agg.values(), key=lambda a: -a["score"])
