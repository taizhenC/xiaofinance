from infinance.analyze import excerpt, gather_items, pick_quotes, quotes_from_ids
from infinance.dedup import recompute_dedup
from infinance.mentions import alias_hits, extract_mentions, load_stock_dict
from infinance.util import now_ms, simhash64, to_signed64

H = 3_600_000
WINDOW = 24 * H


def test_alias_hits_reports_where_and_how_often():
    assert alias_hits("英伟达继续新高，英伟达财报在即", "英伟达") == (0, 2)
    assert alias_hits("完全无关的一段话", "英伟达") == (-1, 0)


def test_alias_hits_keeps_the_matcher_word_boundaries():
    """'arm' must not hit 'farmer' here either, or the excerpt would window on nothing."""
    assert alias_hits("the farmer said", "arm") == (-1, 0)
    assert alias_hits("I hold ARM and TSM", "arm") == (7, 1)


def test_short_post_is_untouched():
    assert excerpt("英伟达财报炸裂", 0, 300) == "英伟达财报炸裂"


def test_window_moves_to_the_mention_when_it_falls_past_the_cutoff():
    head = "美股期权攻略①：先看懂期权的整体策略地图。期权策略看起来很多，但本质上交易四件事。"
    filler = "方向、时间、波动率和风险。" * 40
    text = head + filler + "用SPY、QQQ等指数期权对冲组合，相当于给持仓买保险。"
    pos, hits = alias_hits(text, "QQQ")
    assert pos > 300  # the real case: truncating from the start would never show it
    out = excerpt(text, pos, 300)
    assert "QQQ" in out
    assert out.startswith("美股期权攻略")  # the topic sentence survives
    assert "…" in out
    assert len(out) <= 302


def test_a_mention_near_the_edge_of_the_window_still_gets_context_after_it():
    text = "x" * 280 + "英伟达" + "后面还有很多内容" * 20
    out = excerpt(text, 280, 300)
    assert "英伟达后面还有" in out


def _note(conn, note_id, title, desc, ts, likes=10):
    conn.execute(
        "INSERT INTO notes(note_id, title, note_desc, publish_time_ms, liked_count, simhash, source_keyword)"
        " VALUES(?,?,?,?,?,?,'美股')",
        (note_id, title, desc, ts, likes, to_signed64(simhash64(f"{title} {desc}"))),
    )


def test_tutorial_reaches_the_model_with_the_ticker_visible_and_flagged(conn):
    """The post the dashboard showed as QQQ's top quote never mentioned QQQ in 300 chars."""
    now = now_ms()
    desc = (
        "先说清楚，这个账号不是喊单，也不是带单，更不是投资建议。"
        + "把美股里那些看起来很复杂、但其实可以拆开讲清楚的东西整理成认知框架。" * 8
        + "接下来这个号主要会讲4个方向：1｜QQQ 的讲解，我会先从 QQQ 这个 ETF 入手。"
    )
    _note(conn, "edu", "大家好，分享美股基本知识，避免上当", desc, now - H, likes=40)
    conn.commit()
    recompute_dedup(conn, WINDOW, now)
    extract_mentions(conn, load_stock_dict(), [], WINDOW, now=now)

    item = gather_items(conn, "QQQ", WINDOW, now)[0]
    assert "QQQ" in item["text"]  # the model can now see what it is being asked about
    assert item["aside"] is False  # named 4×: a curriculum built on it, not a passing example


def test_a_name_dropped_once_in_a_long_post_is_marked_as_an_aside(conn):
    now = now_ms()
    _note(conn, "wk", "持仓周报", "本周组合表现不错。" * 40 + "英伟达也小涨。", now - H, likes=80)
    conn.commit()
    recompute_dedup(conn, WINDOW, now)
    extract_mentions(conn, load_stock_dict(), [], WINDOW, now=now)
    assert gather_items(conn, "NVDA", WINDOW, now)[0]["aside"] is True


def test_asides_lose_to_posts_that_are_actually_about_the_ticker():
    aside = {"text": "持仓周报…英伟达也小涨", "likes": 900, "fanout": 1, "substance": 40, "aside": True}
    real = {"text": "英伟达这波AI需求还没到头，我继续拿着不动", "likes": 3, "fanout": 1,
            "substance": 40, "aside": False}
    assert pick_quotes([aside, real], k=1) == [real["text"]]


def test_model_chosen_quotes_are_looked_up_not_trusted():
    items = [{"text": "第一条"}, {"text": "第二条"}]
    assert quotes_from_ids(items, [2, 1]) == ["第二条", "第一条"]
    assert quotes_from_ids(items, [99, 0, -1]) == []  # hallucinated indices vanish
    assert quotes_from_ids(items, [1, 1]) == ["第一条"]
