from app.config import Settings
from app.dedup import recompute_dedup
from app.mentions import extract_mentions, load_stock_dict
from app.scoring import compute_stats, radar_entries, ranking_and_radar, sector_breakdown
from app.util import now_ms, simhash64, to_signed64

H = 3_600_000
BOARD = 24 * H
CONTEXT = 72 * H


def _note(conn, note_id, title, desc, ts, likes=10):
    conn.execute(
        "INSERT INTO notes(note_id, title, note_desc, publish_time_ms, liked_count, simhash, source_keyword)"
        " VALUES(?,?,?,?,?,?,'美股')",
        (note_id, title, desc, ts, likes, to_signed64(simhash64(f"{title} {desc}"))),
    )


def test_context_window_defaults_wider_than_the_board():
    s = Settings()
    assert s.context_window_ms > s.fresh_window_ms


def test_context_window_never_narrower_than_the_board():
    s = Settings(FRESH_WINDOW_HOURS=48, CONTEXT_WINDOW_HOURS=12)
    assert s.context_window_ms == s.fresh_window_ms


def test_slow_sector_is_invisible_on_the_board_but_lives_in_the_context_window(conn):
    now = now_ms()
    # semis: today's noise. pharma: one post every couple of days — real, but never 24h-hot
    _note(conn, "s1", "英伟达继续新高", "英伟达AI需求爆表", now - 2 * H)
    _note(conn, "s2", "英伟达财报前瞻", "英伟达数据中心还能打", now - 5 * H)
    _note(conn, "p1", "美股医药触底反弹", "莫德纳带头反弹", now - 40 * H)
    _note(conn, "p2", "莫德纳太夸张了", "莫德纳又涨了", now - 60 * H)
    conn.commit()

    recompute_dedup(conn, CONTEXT, now)
    extract_mentions(conn, load_stock_dict(), [], CONTEXT, now=now)

    board = compute_stats(conn, BOARD, now)
    context = compute_stats(conn, CONTEXT, now)

    assert "MRNA" not in board  # 24h sees nothing of it
    assert context["MRNA"]["mentions"] == 2

    ranking, _ = ranking_and_radar(board, min_mentions=2)
    shown = {e["ticker"] for e in ranking}
    assert shown == {"NVDA"}

    radar = radar_entries(context, exclude=shown)
    assert "MRNA" in {e["ticker"] for e in radar}

    sectors = {s["ticker"]: s.get("sector", "") for s in load_stock_dict()["stocks"]}
    breakdown = {s["sector"]: s for s in sector_breakdown(context, sectors)}
    assert "医药" in breakdown
    assert breakdown["医药"]["leader"]["ticker"] == "MRNA"


def test_radar_excludes_whatever_the_board_already_shows(conn):
    stats = {
        "NVDA": {"ticker": "NVDA", "score": 30.0, "mentions": 6},
        "MRNA": {"ticker": "MRNA", "score": 2.0, "mentions": 2},
    }
    radar = radar_entries(stats, exclude={"NVDA"})
    assert [e["ticker"] for e in radar] == ["MRNA"]
