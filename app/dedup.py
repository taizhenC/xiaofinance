import logging

from .util import from_signed64, hamming64, now_ms

log = logging.getLogger(__name__)

HAMMING_MAX = 6


def recompute_dedup(conn, fresh_window_ms: int, now: int | None = None) -> dict:
    """Cluster fresh reposts: simhash for notes, exact normalized match for comments.
    Canonical = most-liked item; members point at it via dup_group_id."""
    now = now or now_ms()
    cutoff = now - fresh_window_ms

    notes = conn.execute(
        "SELECT note_id, simhash, liked_count FROM notes WHERE publish_time_ms >= ?", (cutoff,)
    ).fetchall()
    conn.execute("UPDATE notes SET dup_group_id=NULL WHERE publish_time_ms >= ?", (cutoff,))

    parent = list(range(len(notes)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    hashes = [from_signed64(n["simhash"] or 0) for n in notes]
    for i in range(len(notes)):
        if not hashes[i]:
            continue
        for j in range(i + 1, len(notes)):
            if hashes[j] and hamming64(hashes[i], hashes[j]) <= HAMMING_MAX:
                parent[find(i)] = find(j)

    clusters: dict[int, list[int]] = {}
    for i in range(len(notes)):
        clusters.setdefault(find(i), []).append(i)

    note_dups = 0
    for members in clusters.values():
        if len(members) < 2:
            continue
        canonical = max(members, key=lambda i: (notes[i]["liked_count"], notes[i]["note_id"]))
        for i in members:
            if i != canonical:
                conn.execute(
                    "UPDATE notes SET dup_group_id=? WHERE note_id=?",
                    (notes[canonical]["note_id"], notes[i]["note_id"]),
                )
                note_dups += 1

    conn.execute("UPDATE comments SET dup_group_id=NULL WHERE create_time_ms >= ?", (cutoff,))
    comment_dups = 0
    groups = conn.execute(
        """SELECT content_norm_hash FROM comments
           WHERE create_time_ms >= ? AND content_norm_hash IS NOT NULL
           GROUP BY content_norm_hash HAVING COUNT(*) > 1""",
        (cutoff,),
    ).fetchall()
    for g in groups:
        rows = conn.execute(
            """SELECT comment_id FROM comments
               WHERE create_time_ms >= ? AND content_norm_hash = ?
               ORDER BY like_count DESC, create_time_ms ASC""",
            (cutoff, g["content_norm_hash"]),
        ).fetchall()
        canonical_id = rows[0]["comment_id"]
        for r in rows[1:]:
            conn.execute(
                "UPDATE comments SET dup_group_id=? WHERE comment_id=?",
                (canonical_id, r["comment_id"]),
            )
            comment_dups += 1

    conn.commit()
    stats = {"note_dups": note_dups, "comment_dups": comment_dups}
    log.info("dedup: %s", stats)
    return stats


def note_cluster_sizes(conn, fresh_window_ms: int, now: int | None = None) -> dict[str, int]:
    """canonical note_id -> total cluster size (1 = unique)."""
    now = now or now_ms()
    cutoff = now - fresh_window_ms
    sizes: dict[str, int] = {}
    for r in conn.execute(
        "SELECT note_id, dup_group_id FROM notes WHERE publish_time_ms >= ?", (cutoff,)
    ):
        key = r["dup_group_id"] or r["note_id"]
        sizes[key] = sizes.get(key, 0) + 1
    return sizes


def comment_cluster_sizes(conn, fresh_window_ms: int, now: int | None = None) -> dict[str, int]:
    now = now or now_ms()
    cutoff = now - fresh_window_ms
    sizes: dict[str, int] = {}
    for r in conn.execute(
        "SELECT comment_id, dup_group_id FROM comments WHERE create_time_ms >= ?", (cutoff,)
    ):
        key = r["dup_group_id"] or r["comment_id"]
        sizes[key] = sizes.get(key, 0) + 1
    return sizes
