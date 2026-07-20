from infinance.dedup import recompute_dedup
from infinance.mentions import extract_mentions, index_tickers, load_stock_dict, non_stock_tickers
from infinance.scoring import (
    compute_stats,
    fanout,
    index_board,
    ranking_and_radar,
    sector_breakdown,
)
from infinance.util import now_ms, simhash64, to_signed64

H = 3_600_000
WINDOW = 24 * H
DICT = load_stock_dict()
IDX = index_tickers(DICT)
NON_STOCKS = non_stock_tickers(DICT)


def _note(conn, note_id, title, desc, ts, likes=10):
    conn.execute(
        "INSERT INTO notes(note_id, title, note_desc, publish_time_ms, liked_count, simhash, source_keyword)"
        " VALUES(?,?,?,?,?,?,'美股')",
        (note_id, title, desc, ts, likes, to_signed64(simhash64(f"{title} {desc}"))),
    )


def _stats(conn, now):
    conn.commit()
    recompute_dedup(conn, WINDOW, now)
    extract_mentions(conn, DICT, [], WINDOW, now=now)
    return compute_stats(conn, WINDOW, now, indexes=NON_STOCKS)


def test_index_tickers_come_from_the_etf_sector():
    assert {"QQQ", "SPY"} <= IDX
    assert "GLD" not in IDX and "GOLD" not in IDX
    assert "NVDA" not in IDX and "BRK" not in IDX


def test_fanout_is_counted_within_a_class_not_across_it():
    """A post arguing 英伟达带动纳指新高 is a dedicated NVDA post that mentions where the
    market went. Charging NVDA 1/2 for it would punish the stock for its own context."""
    peers = {"NVDA", "QQQ"}
    assert fanout(peers, "NVDA", IDX) == 1
    assert fanout(peers, "QQQ", IDX) == 1
    assert fanout({"NVDA", "AMD", "QQQ"}, "NVDA", IDX) == 2  # two stocks, index not counted
    assert fanout({"QQQ", "SPY", "DIA"}, "QQQ", IDX) == 3  # an index roundup still splits


def test_index_talk_does_not_dilute_the_stock_beside_it(conn):
    """Folding indexes into the stock board cost SKHY a third of its score on real data,
    purely because index terms rode along in the same posts. Class-aware fan-out is what
    stops that."""
    now = now_ms()
    _note(conn, "n1", "英伟达带动纳指新高", "英伟达AI需求爆表，纳指跟着创了新高，我继续拿着英伟达", now - H)
    stats = _stats(conn, now)
    assert {"NVDA", "QQQ"} <= set(stats)  # the post really does name both

    with_index = stats["NVDA"]["score"]
    conn.execute("DELETE FROM stock_mentions WHERE ticker='QQQ'")
    without_index = compute_stats(conn, WINDOW, now, indexes=IDX)["NVDA"]["score"]
    assert with_index == without_index


def test_the_index_gets_its_own_board_and_stays_off_the_stock_one(conn):
    now = now_ms()
    _note(conn, "i1", "纳指还能追吗", "纳指今年涨太多了，纳指100估值不便宜，但纳指趋势还在", now - H, likes=90)
    _note(conn, "s1", "英伟达财报前瞻", "英伟达这波AI需求还没到头，英伟达我继续拿着", now - H, likes=50)
    stats = _stats(conn, now)

    ranking, _ = ranking_and_radar(stats, 1, IDX)
    board = index_board(stats, IDX, 1)

    assert [e["ticker"] for e in ranking] == ["NVDA"]
    assert [e["ticker"] for e in board] == ["QQQ"]
    # the index outscores the stock — which is exactly why it must not share the board
    assert board[0]["score"] > ranking[0]["score"]


def test_sectors_answer_which_sectors_not_stocks_versus_indexes(conn):
    now = now_ms()
    _note(conn, "i1", "纳指标普都新高", "纳指和标普今天都创了新高，标普500强势", now - H, likes=90)
    _note(conn, "s1", "英伟达财报", "英伟达AI需求爆表，英伟达继续拿", now - H, likes=10)
    stats = _stats(conn, now)
    sectors = {s["ticker"]: s.get("sector", "") for s in DICT["stocks"]}

    named = {a["sector"] for a in sector_breakdown(stats, sectors, exclude=NON_STOCKS)}
    assert "ETF指数" not in named
    assert "半导体" in named
