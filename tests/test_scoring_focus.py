from app.dedup import recompute_dedup
from app.mentions import extract_mentions, load_stock_dict
from app.scoring import compute_stats, is_rankable, ranking_and_radar
from app.util import now_ms, simhash64, to_signed64

H = 3_600_000
WINDOW = 24 * H


def _note(conn, note_id, title, desc, ts, likes):
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


def test_roundup_note_weighs_less_than_dedicated_note(conn):
    now = now_ms()
    _note(conn, "cal", "下周财报日历", "周二：JPM、BAC、GS、WFC 周三：MS、NFLX 财报集中登场", now - H, 500)
    _note(conn, "ded", "英伟达发布新一代芯片", "英伟达股价大涨，AI需求依然强劲", now - H, 5)
    stats = _process(conn, now)

    assert stats["JPM"]["focused_mentions"] == 0
    assert stats["NVDA"]["focused_mentions"] == 1
    # 1/6 weight per roundup ticker vs full weight for the dedicated post
    assert stats["JPM"]["score"] < stats["NVDA"]["score"]

    ranking, radar = ranking_and_radar(stats, min_mentions=1)
    assert [e["ticker"] for e in ranking] == ["NVDA"]
    assert {e["ticker"] for e in radar} >= {"JPM", "GS", "MS"}


def test_hashtag_only_match_is_not_focused(conn):
    now = now_ms()
    _note(conn, "tag", "炒美股睡不着", "#美光[话题]# #英伟达[话题]#", now - H, 100)
    stats = _process(conn, now)
    assert stats["MU"]["mentions"] == 1
    assert stats["MU"]["focused_mentions"] == 0
    assert not is_rankable(stats["MU"], 1)


def test_top_quote_prefers_focused_source(conn):
    now = now_ms()
    _note(conn, "cal", "下周财报日历", "周二：JPM、BAC、GS、WFC 周三：MS、NFLX、英伟达 财报集中登场", now - H, 900)
    _note(conn, "ded", "英伟达财报前瞻", "英伟达数据中心业务还能打吗", now - H, 3)
    stats = _process(conn, now)
    assert "英伟达财报前瞻" in stats["NVDA"]["top_quote"]
