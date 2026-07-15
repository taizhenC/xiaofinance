import json

from app.ingest import ingest_run_dir
from app.util import norm_for_hash, now_ms, simhash64, to_signed64

H = 3_600_000
DAY = 24 * H


def _write_jsonl(run_dir, name, rows):
    d = run_dir / "xhs" / "jsonl"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / name, "w", encoding="utf-8") as f:
        for r in rows:
            f.write((r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)) + "\n")


def test_ingest_freshness_gate(conn, tmp_path):
    now = now_ms()
    run_dir = tmp_path / "run_000001"
    _write_jsonl(run_dir, "search_contents_2026-07-11.jsonl", [
        {"note_id": "n1", "title": "英伟达大涨", "desc": "冲", "time": now - 1 * H,
         "liked_count": "1.2万", "note_url": "https://x/n1", "source_keyword": "美股"},
        {"note_id": "n2", "title": "特斯拉", "desc": "财报", "time": (now - 2 * H) // 1000,
         "liked_count": "10+", "source_keyword": "美股"},
        {"note_id": "n3", "title": "三天前的旧帖", "desc": "old", "time": now - 3 * DAY,
         "liked_count": "5"},
        "{ this is not valid json",
    ])
    _write_jsonl(run_dir, "search_comments_2026-07-11.jsonl", [
        {"comment_id": "c1", "note_id": "n1", "content": "我也觉得会涨", "create_time": now - 1 * H,
         "like_count": "3"},
        {"comment_id": "c2", "note_id": "n1", "content": "旧评论", "create_time": now - 3 * DAY},
        {"comment_id": "c3", "note_id": "n3", "content": "父帖太旧被排除", "create_time": now - 1 * H},
        {"comment_id": "c4", "note_id": "missing", "content": "孤儿评论", "create_time": now - 1 * H},
    ])

    stats = ingest_run_dir(conn, run_dir, run_id=1, fresh_window_ms=24 * H, now=now)

    assert stats["notes_fetched"] == 3
    assert stats["notes_fresh"] == 2
    assert stats["notes_fresh"] < stats["notes_fetched"]
    assert stats["malformed"] == 1
    assert stats["comments_fresh"] == 1

    n1 = conn.execute("SELECT * FROM notes WHERE note_id='n1'").fetchone()
    assert n1["liked_count"] == 12000
    n2 = conn.execute("SELECT * FROM notes WHERE note_id='n2'").fetchone()
    assert n2["publish_time_ms"] > 10**12  # 10-digit seconds normalized to ms
    assert conn.execute("SELECT COUNT(*) c FROM notes").fetchone()["c"] == 2
    assert conn.execute("SELECT COUNT(*) c FROM comments").fetchone()["c"] == 1


def test_ingest_updates_existing_content_and_hashes(conn, tmp_path):
    now = now_ms()
    first_run = tmp_path / "run_000001"
    _write_jsonl(first_run, "search_contents_1.jsonl", [{
        "note_id": "n1", "title": "旧标题", "desc": "旧描述", "type": "normal",
        "time": now - H, "liked_count": "1", "note_url": "https://x/old",
        "tag_list": "旧标签", "source_keyword": "旧关键词", "nickname": "旧作者",
    }])
    _write_jsonl(first_run, "search_comments_1.jsonl", [{
        "comment_id": "c1", "note_id": "n1", "parent_comment_id": "p1",
        "content": "这是旧的评论内容", "create_time": now - H, "like_count": "1",
        "sub_comment_count": "1", "nickname": "旧评论者",
    }])
    ingest_run_dir(conn, first_run, run_id=1, fresh_window_ms=DAY, now=now)

    second_run = tmp_path / "run_000002"
    _write_jsonl(second_run, "search_contents_2.jsonl", [{
        "note_id": "n1", "title": "新标题", "desc": "新描述", "type": "video",
        "time": now - H, "liked_count": "2", "note_url": "https://x/new",
        "tag_list": "新标签", "source_keyword": "新关键词", "nickname": "新作者",
    }])
    _write_jsonl(second_run, "search_comments_2.jsonl", [{
        "comment_id": "c1", "note_id": "n1", "parent_comment_id": "p2",
        "content": "这是更新后的评论内容", "create_time": now - H, "like_count": "2",
        "sub_comment_count": "2", "nickname": "新评论者",
    }])
    ingest_run_dir(conn, second_run, run_id=2, fresh_window_ms=DAY, now=now + 1)

    note = conn.execute("SELECT * FROM notes WHERE note_id='n1'").fetchone()
    assert note["title"] == "新标题"
    assert note["note_desc"] == "新描述"
    assert note["note_type"] == "video"
    assert note["note_url"] == "https://x/new"
    assert note["tag_list"] == "新标签"
    assert note["source_keyword"] == "新关键词"
    assert note["nickname"] == "新作者"
    assert note["simhash"] == to_signed64(simhash64("新标题 新描述"))
    assert note["first_seen_run_id"] == 1
    assert note["last_seen_run_id"] == 2

    comment = conn.execute("SELECT * FROM comments WHERE comment_id='c1'").fetchone()
    assert comment["parent_comment_id"] == "p2"
    assert comment["content"] == "这是更新后的评论内容"
    assert comment["nickname"] == "新评论者"
    assert comment["content_norm_hash"] == norm_for_hash("这是更新后的评论内容")
    assert comment["first_seen_run_id"] == 1
