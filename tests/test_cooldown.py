from types import SimpleNamespace

from app.db import connect
from app.pipeline import risk_cooldown_until

NOW = 1_783_887_669_377
WALL = "stopped after 12 CAPTCHAs — XHS is rate-limiting the account"


def _conn(tmp_path):
    return connect(tmp_path / "cooldown.db")


def _run(conn, status, error, finished_at_ms):
    conn.execute(
        "INSERT INTO fetch_runs(mode, status, started_at_ms, finished_at_ms, error) "
        "VALUES('discovery', ?, ?, ?, ?)",
        (status, finished_at_ms - 60_000, finished_at_ms, error),
    )


def test_a_walled_run_starts_the_cooldown(tmp_path):
    conn = _conn(tmp_path)
    _run(conn, "failed", WALL, NOW)
    s = SimpleNamespace(RISK_COOLDOWN_HOURS=24)
    assert risk_cooldown_until(conn, s) == NOW + 24 * 3_600_000


def test_any_later_finished_run_supersedes_the_wall(tmp_path):
    conn = _conn(tmp_path)
    _run(conn, "failed", WALL, NOW)
    _run(conn, "success", None, NOW + 3_600_000)
    s = SimpleNamespace(RISK_COOLDOWN_HOURS=24)
    assert risk_cooldown_until(conn, s) is None


def test_a_still_running_row_does_not_hide_the_wall(tmp_path):
    conn = _conn(tmp_path)
    _run(conn, "failed", WALL, NOW)
    conn.execute(
        "INSERT INTO fetch_runs(mode, status, started_at_ms) VALUES('discovery', 'running', ?)",
        (NOW + 60_000,),
    )
    s = SimpleNamespace(RISK_COOLDOWN_HOURS=24)
    assert risk_cooldown_until(conn, s) == NOW + 24 * 3_600_000


def test_other_failures_do_not_pause_the_schedule(tmp_path):
    conn = _conn(tmp_path)
    _run(conn, "failed", "login_required", NOW)
    s = SimpleNamespace(RISK_COOLDOWN_HOURS=24)
    assert risk_cooldown_until(conn, s) is None


def test_zero_disables_the_cooldown(tmp_path):
    conn = _conn(tmp_path)
    _run(conn, "failed", WALL, NOW)
    s = SimpleNamespace(RISK_COOLDOWN_HOURS=0)
    assert risk_cooldown_until(conn, s) is None
