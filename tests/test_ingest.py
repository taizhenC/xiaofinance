import json

from infinance.ingest import ingest_run_dir
from infinance.util import now_ms

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
