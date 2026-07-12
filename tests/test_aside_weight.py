from app.dedup import recompute_dedup
from app.mentions import extract_mentions, is_aside, load_stock_dict
from app.scoring import compute_stats, is_rankable
from app.util import now_ms, simhash64, to_signed64

H = 3_600_000
WINDOW = 24 * H


def _note(conn, note_id, title, desc, ts, likes=10):
    conn.execute(
        "INSERT INTO notes(note_id, title, note_desc, publish_time_ms, liked_count, simhash, source_keyword)"
        " VALUES(?,?,?,?,?,?,'美股')",
        (note_id, title, desc, ts, likes, to_signed64(simhash64(f"{title} {desc}"))),
    )


def _process(conn, now):
    conn.commit()
    recompute_dedup(conn, WINDOW, now)
    extract_mentions(conn, load_stock_dict(), [], WINDOW, now=now)
    return compute_stats(conn, WINDOW, now)


def test_a_short_post_is_never_an_aside():
    """"英伟达冲了" names it once and is unmistakably about it."""
    assert is_aside("英伟达冲了", 1) is False


def test_named_once_in_a_long_post_is_an_aside():
    assert is_aside("x" * 400, 1) is True


def test_coming_back_to_the_name_is_not_an_aside():
    assert is_aside("x" * 400, 2) is False


def test_a_name_drop_scores_less_than_a_post_about_the_stock(conn):
    now = now_ms()
    # both name NVDA once each; one is a story about it, the other lists it in passing
    _note(conn, "about", "英伟达继续新高", "英伟达AI需求爆表，我继续拿着", now - H, likes=10)
    _note(conn, "drop", "持仓周报", "本周组合表现不错。" * 40 + "英伟达也小涨。", now - H, likes=10)
    conn.commit()
    recompute_dedup(conn, WINDOW, now)
    extract_mentions(conn, load_stock_dict(), [], WINDOW, now=now)

    only_about = compute_stats(conn, WINDOW, now)["NVDA"]["score"]
    conn.execute("DELETE FROM notes WHERE note_id='about'")
    conn.execute("DELETE FROM stock_mentions WHERE source_id='about'")
    only_drop = compute_stats(conn, WINDOW, now)["NVDA"]["score"]

    assert 0 < only_drop < only_about


def test_a_name_drop_still_keeps_the_ticker_on_the_board(conn):
    """The chosen behaviour: discount the heat, don't hide the name."""
    now = now_ms()
    # distinct bodies, or the simhash dedup would collapse them into one cluster
    _note(conn, "n1", "标普500还是纳指100",
          "指数里科技权重很高，长期定投更稳，回撤时也扛得住。" * 15 + "苹果是重要成分股。", now - H)
    _note(conn, "n2", "定投复盘",
          "这周继续买入，纪律比择时重要，慢慢来比较快，静待复利。" * 15 + "苹果的权重不低。", now - 2 * H)
    stats = _process(conn, now)

    e = stats["AAPL"]
    assert e["mentions"] == 2  # the mentions are never dropped
    assert e["score"] > 0
    assert is_rankable(e, min_mentions=2)  # still eligible for the board, just cheaper
