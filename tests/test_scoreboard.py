import json
from datetime import UTC, datetime

from infinance.scoreboard import compute_scoreboard


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).replace(tzinfo=UTC).timestamp() * 1000)


def _analysis(conn, ticker, at_ms, bull, bear, status="ok"):
    conn.execute(
        "INSERT INTO stock_analyses(ticker, generated_at_ms, sentiment_counts, status) VALUES(?,?,?,?)",
        (ticker, at_ms, json.dumps({"bullish": bull, "bearish": bear, "neutral": 0}), status),
    )


def _close(conn, ticker, date, close):
    conn.execute("INSERT INTO price_history(ticker, date, close) VALUES(?,?,?)", (ticker, date, close))


def test_scoreboard_scores_calls(conn):
    now = ms("2026-01-20T12:00:00")
    # NVDA 01-05: earlier bearish call superseded by later bullish call the same day
    _analysis(conn, "NVDA", ms("2026-01-05T08:00:00"), 1, 4)
    _analysis(conn, "NVDA", ms("2026-01-05T10:00:00"), 4, 1)
    # weak lean: never a call
    _analysis(conn, "AAPL", ms("2026-01-05T10:00:00"), 1, 0)
    # TSLA 01-19: bearish call, no outcome data yet -> pending
    _analysis(conn, "TSLA", ms("2026-01-19T10:00:00"), 0, 3)
    _close(conn, "NVDA", "2026-01-05", 100.0)
    _close(conn, "NVDA", "2026-01-06", 105.0)   # 1d: +5% -> bullish correct
    _close(conn, "NVDA", "2026-01-12", 95.0)    # 7d: -5% -> bullish wrong
    _close(conn, "TSLA", "2026-01-19", 200.0)
    conn.commit()

    sb = compute_scoreboard(conn, now=now)
    assert sb["overall"] == {
        "evaluated_1d": 1, "correct_1d": 1, "hit_rate_1d": 100.0,
        "evaluated_7d": 1, "correct_7d": 0, "hit_rate_7d": 0.0,
    }
    assert set(sb["by_ticker"]) == {"NVDA", "TSLA"}

    calls = {c["ticker"]: c for c in sb["calls"]}
    assert len(sb["calls"]) == 2  # AAPL weak lean excluded
    nvda = calls["NVDA"]
    assert nvda["dir"] == "up"          # later same-day call wins
    assert nvda["move_1d_pct"] == 5.0 and nvda["correct_1d"] is True
    assert nvda["move_7d_pct"] == -5.0 and nvda["correct_7d"] is False
    tsla = calls["TSLA"]
    assert tsla["dir"] == "down"
    assert tsla["correct_1d"] is None and tsla["correct_7d"] is None  # pending


def test_scoreboard_empty(conn):
    sb = compute_scoreboard(conn, now=ms("2026-01-20T12:00:00"))
    assert sb["calls"] == []
    assert sb["overall"]["hit_rate_1d"] is None
