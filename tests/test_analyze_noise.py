from app.analyze import gather_items
from app.dedup import recompute_dedup
from app.mentions import extract_mentions, load_stock_dict
from app.scoring import compute_stats
from app.util import now_ms, simhash64, to_signed64

H = 3_600_000
WINDOW = 24 * H


def _note(conn, note_id, title, desc, ts, likes=10):
    conn.execute(
        "INSERT INTO notes(note_id, title, note_desc, publish_time_ms, liked_count, simhash, source_keyword)"
        " VALUES(?,?,?,?,?,?,'美股')",
        (note_id, title, desc, ts, likes, to_signed64(simhash64(f"{title} {desc}"))),
    )


def _comment(conn, cid, note_id, content, ts, likes=1):
    conn.execute(
        "INSERT INTO comments(comment_id, note_id, content, create_time_ms, like_count)"
        " VALUES(?,?,?,?,?)",
        (cid, note_id, content, ts, likes),
    )


def _process(conn, now):
    conn.commit()
    recompute_dedup(conn, WINDOW, now)
    extract_mentions(conn, load_stock_dict(), [], WINDOW, now=now)
    return compute_stats(conn, WINDOW, now)


def test_image_only_note_still_counts_as_heat_but_carries_no_evidence(conn):
    now = now_ms()
    _note(conn, "img", "Is That True？", "#纳斯达克[话题]# #美股[话题]# #美光[话题]#", now - H, likes=6)
    _note(conn, "real", "美光要涨", "美光的存储周期还没走完，DRAM报价继续往上", now - H, likes=2)
    stats = _process(conn, now)

    assert stats["MU"]["mentions"] == 2  # someone did post about it — the count is honest
    ids = {i["id"] for i in gather_items(conn, "MU", WINDOW, now)}
    assert ids == {"real"}  # but there is nothing in the image post to analyse or quote


def test_a_question_to_the_xhs_bot_is_not_retail_opinion(conn):
    now = now_ms()
    _note(conn, "n1", "海力士登陆纳斯达克", "SK海力士发行价149美元，募资265亿美元", now - H)
    _comment(conn, "c1", "n1", "@问一问 为什么海力士进不了纳指", now - H, likes=2)
    _process(conn, now)
    ids = {i["id"] for i in gather_items(conn, "SKHY", WINDOW, now)}
    assert "c1" not in ids


def test_one_word_reactions_do_not_reach_the_model(conn):
    now = now_ms()
    _note(conn, "n1", "英伟达财报炸裂", "英伟达继续持有", now - H, likes=50)
    _comment(conn, "c1", "n1", "有", now - H, likes=3)
    _comment(conn, "c2", "n1", "这波AI需求还能撑一年，继续拿着", now - H, likes=1)
    _process(conn, now)
    ids = {i["id"] for i in gather_items(conn, "NVDA", WINDOW, now)}
    assert ids == {"n1", "c2"}


def test_reaction_survives_an_unreadable_parent_without_quoting_its_title(conn):
    """The comment is a real bearish datum; "主帖「Is That True？」下的评论" is not context."""
    now = now_ms()
    _note(conn, "img", "Is That True？", "#美光[话题]# #纳斯达克[话题]#", now - H, likes=6)
    _comment(conn, "c1", "img", "我咋感觉股价到头了", now - H, likes=2)
    _process(conn, now)
    items = {i["id"]: i for i in gather_items(conn, "MU", WINDOW, now)}
    assert items["c1"]["text"] == "主帖下的评论: 我咋感觉股价到头了"
