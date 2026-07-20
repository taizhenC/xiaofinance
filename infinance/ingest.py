import json
import logging
from pathlib import Path

from .util import norm_for_hash, normalize_ts, now_ms, parse_cn_count, simhash64, to_signed64

log = logging.getLogger(__name__)

# Comments shorter than this (normalized) are never deduped — "冲" or "666" colliding
# across notes is not repost spam.
MIN_COMMENT_DEDUP_LEN = 8


def _jsonl_lines(run_dir: Path, prefix: str):
    for f in sorted((run_dir / "xhs" / "jsonl").glob(f"{prefix}_*.jsonl")):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield line


def ingest_run_dir(conn, run_dir: Path, run_id: int, fresh_window_ms: int, now: int | None = None) -> dict:
    now = now or now_ms()
    cutoff = now - fresh_window_ms
    stats = {"notes_fetched": 0, "notes_fresh": 0, "comments_seen": 0, "comments_fresh": 0, "malformed": 0}

    for line in _jsonl_lines(run_dir, "search_contents"):
        try:
            d = json.loads(line)
            note_id = d["note_id"]
            ts = normalize_ts(d.get("time"))
            if not note_id or ts is None:
                raise ValueError("missing note_id/time")
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            stats["malformed"] += 1
            continue
        stats["notes_fetched"] += 1
        if ts < cutoff:
            continue
        stats["notes_fresh"] += 1
        title = d.get("title") or ""
        desc = d.get("desc") or ""
        conn.execute(
            """INSERT INTO notes(note_id, title, note_desc, note_type, publish_time_ms,
                 liked_count, collected_count, comment_count, share_count,
                 note_url, tag_list, source_keyword, nickname, simhash,
                 first_seen_run_id, last_seen_run_id, fetched_at_ms)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(note_id) DO UPDATE SET
                 title=excluded.title, note_desc=excluded.note_desc, note_type=excluded.note_type,
                 liked_count=excluded.liked_count, collected_count=excluded.collected_count,
                 comment_count=excluded.comment_count, share_count=excluded.share_count,
                 note_url=excluded.note_url, tag_list=excluded.tag_list,
                 source_keyword=excluded.source_keyword, nickname=excluded.nickname,
                 simhash=excluded.simhash,
                 last_seen_run_id=excluded.last_seen_run_id, fetched_at_ms=excluded.fetched_at_ms""",
            (
                note_id, title, desc, d.get("type"), ts,
                parse_cn_count(d.get("liked_count")), parse_cn_count(d.get("collected_count")),
                parse_cn_count(d.get("comment_count")), parse_cn_count(d.get("share_count")),
                d.get("note_url"), d.get("tag_list"), d.get("source_keyword"), d.get("nickname"),
                to_signed64(simhash64(f"{title} {desc}")),
                run_id, run_id, now,
            ),
        )

    for line in _jsonl_lines(run_dir, "search_comments"):
        try:
            d = json.loads(line)
            comment_id = d["comment_id"]
            note_id = d["note_id"]
            ts = normalize_ts(d.get("create_time"))
            if not comment_id or not note_id or ts is None:
                raise ValueError("missing comment_id/note_id/create_time")
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            stats["malformed"] += 1
            continue
        stats["comments_seen"] += 1
        if ts < cutoff:
            continue
        if not conn.execute("SELECT 1 FROM notes WHERE note_id=?", (note_id,)).fetchone():
            continue
        stats["comments_fresh"] += 1
        content = d.get("content") or ""
        norm = norm_for_hash(content)
        conn.execute(
            """INSERT INTO comments(comment_id, note_id, parent_comment_id, content, create_time_ms,
                 like_count, sub_comment_count, nickname, content_norm_hash,
                 first_seen_run_id, fetched_at_ms)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(comment_id) DO UPDATE SET
                 parent_comment_id=excluded.parent_comment_id, content=excluded.content,
                 like_count=excluded.like_count, sub_comment_count=excluded.sub_comment_count,
                 nickname=excluded.nickname, content_norm_hash=excluded.content_norm_hash,
                 fetched_at_ms=excluded.fetched_at_ms""",
            (
                comment_id, note_id, d.get("parent_comment_id"), content, ts,
                parse_cn_count(d.get("like_count")), parse_cn_count(d.get("sub_comment_count")),
                d.get("nickname"),
                norm if len(norm) >= MIN_COMMENT_DEDUP_LEN else None,
                run_id, now,
            ),
        )

    conn.commit()
    log.info("ingest %s: %s", run_dir, stats)
    return stats
