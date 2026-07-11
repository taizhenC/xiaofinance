from app.scoring import compute_trends, snapshot_scores


def _stats(**scores):
    return {
        t: {"ticker": t, "score": s, "mentions": 2, "note_count": 1, "comment_count": 1}
        for t, s in scores.items()
    }


def test_no_trends_until_two_cycles(conn):
    assert compute_trends(conn) == {}
    snapshot_scores(conn, _stats(NVDA=10.0), 1, now=1_000)
    assert compute_trends(conn) == {}


def test_trend_classification(conn):
    snapshot_scores(conn, _stats(NVDA=10.0, TSLA=20.0, AAPL=10.0, AMD=10.0), 1, now=1_000)
    snapshot_scores(conn, _stats(NVDA=15.0, TSLA=10.0, AAPL=10.5, PLTR=5.0), 2, now=2_000)
    tr = compute_trends(conn)
    assert tr["NVDA"] == {"dir": "up", "delta_pct": 50, "prev_score": 10.0}
    assert tr["TSLA"]["dir"] == "down" and tr["TSLA"]["delta_pct"] == -50
    assert tr["AAPL"]["dir"] == "flat"
    assert tr["PLTR"] == {"dir": "new", "delta_pct": None, "prev_score": 0.0}
    assert "AMD" not in tr


def test_small_absolute_change_damped_to_flat(conn):
    snapshot_scores(conn, _stats(XYZ=1.0), 1, now=1_000)
    snapshot_scores(conn, _stats(XYZ=2.0), 2, now=2_000)
    assert compute_trends(conn)["XYZ"]["dir"] == "flat"


def test_only_latest_two_cycles_compared(conn):
    snapshot_scores(conn, _stats(NVDA=100.0), 1, now=1_000)
    snapshot_scores(conn, _stats(NVDA=10.0), 2, now=2_000)
    snapshot_scores(conn, _stats(NVDA=10.1), 3, now=3_000)
    assert compute_trends(conn)["NVDA"]["dir"] == "flat"
