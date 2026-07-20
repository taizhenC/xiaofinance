from infinance.dedup import recompute_dedup
from infinance.mentions import extract_mentions, load_stock_dict
from infinance.scoring import compute_stats, ranking_and_radar
from infinance.util import now_ms, simhash64, to_signed64

H = 3_600_000
WINDOW = 24 * H


def _note(conn, note_id, title, desc, ts, likes):
    conn.execute(
        "INSERT INTO notes(note_id, title, note_desc, publish_time_ms, liked_count, simhash, source_keyword)"
        " VALUES(?,?,?,?,?,?,'美股')",
        (note_id, title, desc, ts, likes, to_signed64(simhash64(f"{title} {desc}"))),
    )


def test_near_identical_notes_collapse(conn):
    now = now_ms()
    text = "英伟达财报炸裂，盘后大涨8%，AI芯片需求依然强劲，继续持有不动摇，冲冲冲"
    _note(conn, "n1", "英伟达财报", text, now - H, 500)
    _note(conn, "n2", "英伟达财报", text + "！！", now - 2 * H, 10)
    _note(conn, "n3", "今天天气不错", "去公园散步了，很舒服，推荐大家周末也出门走走", now - H, 50)
    conn.commit()

    recompute_dedup(conn, WINDOW, now)

    n1 = conn.execute("SELECT dup_group_id FROM notes WHERE note_id='n1'").fetchone()
    n2 = conn.execute("SELECT dup_group_id FROM notes WHERE note_id='n2'").fetchone()
    n3 = conn.execute("SELECT dup_group_id FROM notes WHERE note_id='n3'").fetchone()
    assert n1["dup_group_id"] is None          # canonical (most liked)
    assert n2["dup_group_id"] == "n1"
    assert n3["dup_group_id"] is None


def test_cluster_scored_once(conn):
    now = now_ms()
    text = "英伟达财报炸裂，盘后大涨8%，AI芯片需求依然强劲，继续持有不动摇，冲冲冲"
    _note(conn, "n1", "英伟达财报", text, now - H, 500)
    _note(conn, "n2", "英伟达财报", text + "！！", now - 2 * H, 10)
    conn.commit()

    recompute_dedup(conn, WINDOW, now)
    extract_mentions(conn, load_stock_dict(), [], WINDOW, now=now)
    stats = compute_stats(conn, WINDOW, now)

    assert stats["NVDA"]["note_count"] == 1      # cluster counts once
    assert stats["NVDA"]["note_count_raw"] == 2  # raw count keeps both
    ranking, radar = ranking_and_radar(stats, min_mentions=2)
    assert all(e["ticker"] != "NVDA" for e in ranking)  # 1 cluster mention < floor
    assert any(e["ticker"] == "NVDA" for e in radar)    # ...but visible on radar


def test_comment_exact_dup_collapse(conn):
    now = now_ms()
    _note(conn, "n1", "美股讨论", "大家怎么看", now - H, 10)
    _note(conn, "n2", "美股讨论2", "怎么看后市", now - H, 10)
    for i, note in enumerate(["n1", "n2"]):
        conn.execute(
            "INSERT INTO comments(comment_id, note_id, content, create_time_ms, like_count, content_norm_hash)"
            " VALUES(?,?,?,?,?,?)",
            (f"c{i}", note, "英伟达永远的神，闭眼买入就完事了", now - H, 10 * (i + 1),
             "英伟达永远的神闭眼买入就完事了"),
        )
    conn.commit()

    recompute_dedup(conn, WINDOW, now)
    c0 = conn.execute("SELECT dup_group_id FROM comments WHERE comment_id='c0'").fetchone()
    c1 = conn.execute("SELECT dup_group_id FROM comments WHERE comment_id='c1'").fetchone()
    assert c1["dup_group_id"] is None       # more likes → canonical
    assert c0["dup_group_id"] == "c1"
