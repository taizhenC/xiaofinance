from app.analyze import gather_items
from app.dedup import recompute_dedup
from app.mentions import extract_mentions, load_stock_dict
from app.scoring import compute_stats
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


def test_thread_comments_from_focused_note_reach_analysis(conn):
    now = now_ms()
    _note(conn, "n1", "英伟达财报炸裂", "英伟达继续持有", now - H, 50)
    conn.execute(
        "INSERT INTO comments(comment_id, note_id, content, create_time_ms, like_count)"
        " VALUES('t1','n1','冲了，已加仓',?,9)", (now - H,),
    )
    _process(conn, now)
    items = {i["id"]: i for i in gather_items(conn, "NVDA", WINDOW, now)}
    assert "t1" in items
    assert items["t1"]["text"].startswith("主帖「英伟达财报炸裂」下的评论:")


def test_thread_comments_skipped_for_roundup_notes(conn):
    now = now_ms()
    _note(conn, "cal", "财报日历", "JPM、GS、MS、英伟达财报下周登场", now - H, 50)
    conn.execute(
        "INSERT INTO comments(comment_id, note_id, content, create_time_ms, like_count)"
        " VALUES('t1','cal','mark',?,9)", (now - H,),
    )
    _process(conn, now)
    ids = {i["id"] for i in gather_items(conn, "NVDA", WINDOW, now)}
    assert "t1" not in ids
