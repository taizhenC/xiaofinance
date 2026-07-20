from infinance.pipeline import RETAIN_CONTENT_DAYS, cleanup
from infinance.util import now_ms

DAY = 86_400_000


def test_cleanup_keeps_an_old_note_that_has_a_fresh_comment(conn):
    now = now_ms()
    old = now - (RETAIN_CONTENT_DAYS + 1) * DAY
    fresh = now - DAY
    conn.execute(
        "INSERT INTO notes(note_id, publish_time_ms) VALUES('keep', ?), ('remove', ?)",
        (old, old),
    )
    conn.execute(
        "INSERT INTO comments(comment_id, note_id, create_time_ms) VALUES('fresh', 'keep', ?)",
        (fresh,),
    )
    conn.commit()

    cleanup(conn, settings=None, now=now)

    assert conn.execute("SELECT 1 FROM notes WHERE note_id='keep'").fetchone()
    assert conn.execute("SELECT 1 FROM comments WHERE comment_id='fresh'").fetchone()
    assert not conn.execute("SELECT 1 FROM notes WHERE note_id='remove'").fetchone()
