from infinance.analyze import gather_items
from infinance.util import now_ms, simhash64, to_signed64

H = 3_600_000
WINDOW = 24 * H


def test_reply_comments_carry_parent_context(conn):
    now = now_ms()
    conn.execute(
        "INSERT INTO notes(note_id, title, note_desc, publish_time_ms, liked_count, simhash, source_keyword)"
        " VALUES('n1','英伟达财报','英伟达财报炸裂，继续持有',?,100,?,'美股')",
        (now - H, to_signed64(simhash64("英伟达财报 英伟达财报炸裂，继续持有"))),
    )
    conn.execute(
        "INSERT INTO comments(comment_id, note_id, content, create_time_ms, like_count)"
        " VALUES('c1','n1','英伟达估值太高了，我清仓了',?,50)",
        (now - H,),
    )
    conn.execute(
        "INSERT INTO comments(comment_id, note_id, parent_comment_id, content, create_time_ms, like_count)"
        " VALUES('c2','n1','c1','同意楼上，我也减了',?,5)",
        (now - H,),
    )
    for src_type, src_id in [("note", "n1"), ("comment", "c1"), ("comment", "c2")]:
        conn.execute(
            "INSERT INTO stock_mentions(ticker, source_type, source_id, note_id, match_basis, content_time_ms)"
            " VALUES('NVDA',?,?,'n1','safe_alias',?)",
            (src_type, src_id, now - H),
        )
    conn.commit()

    items = {i["id"]: i for i in gather_items(conn, "NVDA", WINDOW, now)}
    assert items["c1"]["text"] == "英伟达估值太高了，我清仓了"
    assert items["c2"]["text"] == "回复「英伟达估值太高了，我清仓了」: 同意楼上，我也减了"
