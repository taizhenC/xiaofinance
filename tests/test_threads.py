from infinance.analyze import build_prompt, gather_items
from infinance.dedup import recompute_dedup
from infinance.mentions import extract_mentions, load_stock_dict
from infinance.util import now_ms, sha256_hex, simhash64, to_signed64

H = 3_600_000
WINDOW = 24 * H
DICT = load_stock_dict()


def _note(conn, note_id, title, desc, ts, likes=10):
    conn.execute(
        "INSERT INTO notes(note_id, title, note_desc, publish_time_ms, liked_count, simhash, source_keyword)"
        " VALUES(?,?,?,?,?,?,'美股')",
        (note_id, title, desc, ts, likes, to_signed64(simhash64(f"{title} {desc}"))),
    )


def _comment(conn, cid, note_id, content, ts, likes=1, parent=None):
    conn.execute(
        "INSERT INTO comments(comment_id, note_id, parent_comment_id, content, create_time_ms,"
        " like_count, content_norm_hash) VALUES(?,?,?,?,?,?,?)",
        (cid, note_id, parent, content, ts, likes, sha256_hex(content)),
    )


def _items(conn, ticker, now):
    conn.commit()
    recompute_dedup(conn, WINDOW, now)
    extract_mentions(conn, DICT, [], WINDOW, now=now)
    return gather_items(conn, ticker, WINDOW, now)


def _thread(conn, now):
    _note(conn, "n1", "海力士还能追吗", "SK海力士这波涨太多了，存储周期到顶了没，海力士估值怎么看", now - 3 * H, likes=80)
    _comment(conn, "c1", "n1", "海力士还能上车吗，怕站岗", now - 2 * H, likes=30)
    _comment(conn, "c2", "n1", "别追，我已经出货了", now - H, likes=25, parent="c1")
    _comment(conn, "c3", "n1", "我还拿着，AI需求根本没到头", now - 30 * 60_000, likes=12, parent="c1")


def test_a_reply_follows_the_comment_it_answers(conn):
    now = now_ms()
    _thread(conn, now)
    order = [i["id"] for i in _items(conn, "SKHY", now) if i["type"] == "comment"]
    assert order == ["c1", "c2", "c3"]  # root, then its replies in time order


def test_the_reply_does_not_repeat_its_parent_to_the_model(conn):
    """The parent is the line directly above it — quoting it back costs tokens and reads worse."""
    now = now_ms()
    _thread(conn, now)
    by_id = {i["id"]: i for i in _items(conn, "SKHY", now)}
    assert by_id["c2"]["prompt_text"] == "↳ 别追，我已经出货了"
    # but a card quote has to stand alone, so there it keeps its context
    assert by_id["c2"]["text"] == "回复「海力士还能上车吗，怕站岗」: 别追，我已经出货了"


def test_a_thread_rides_on_its_best_line(conn):
    """A dull root with a sharp reply under it should still reach the model."""
    now = now_ms()
    _note(conn, "n1", "美光财报前瞻", "美光这次财报是存储周期的风向标，美光我看多", now - 3 * H, likes=5)
    _comment(conn, "c1", "n1", "坐等财报出来再说吧", now - 2 * H, likes=1)
    _comment(conn, "c2", "n1", "已经提前埋伏了，存储涨价传导到财报还要一个季度", now - H, likes=99, parent="c1")
    ids = [i["id"] for i in _items(conn, "MU", now) if i["type"] == "comment"]
    assert ids == ["c1", "c2"]


def test_an_orphaned_reply_carries_its_own_context(conn):
    """If the parent is dropped as noise, the reply can't lean on the line above it."""
    now = now_ms()
    _note(conn, "n1", "英伟达财报", "英伟达AI需求爆表，英伟达继续拿着不动", now - 3 * H, likes=50)
    _comment(conn, "c1", "n1", "?", now - 2 * H, likes=40)  # no substance -> dropped
    _comment(conn, "c2", "n1", "英伟达这个估值我是真不敢追了", now - H, likes=20, parent="c1")
    by_id = {i["id"]: i for i in _items(conn, "NVDA", now)}
    assert "c1" not in by_id
    assert by_id["c2"]["prompt_text"].startswith("回复「")  # falls back rather than dangling


def test_the_prompt_tells_the_model_what_the_arrow_means(conn):
    now = now_ms()
    _thread(conn, now)
    items = _items(conn, "SKHY", now)
    _, user = build_prompt("SKHY", "SK海力士", items, "en", now)
    assert "↳" in user
    assert "一问一答只是一次交流" in user
    # the exchange reaches the model in reading order, replies attached to their root
    assert user.index("海力士还能上车吗") < user.index("别追，我已经出货了")
