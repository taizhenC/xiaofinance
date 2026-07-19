"""DC-03: back-to-back manual fetches are gated, a login_required run
triggers a hard cooldown (force never overrides it), and the daily request
budget trips before a misconfigured keyword list burns the account."""

from types import SimpleNamespace

from infinance.db import meta_set
from infinance.guardrails import check_fetch_allowed, estimate_cycle_requests, state

MIN = 60_000
NOW = 1_000_000_000_000


def gsettings(**over):
    base = dict(
        MIN_FETCH_GAP_MIN=45, AUTH_COOLDOWN_MIN=30, DAILY_REQUEST_BUDGET=15000,
        DISCOVERY_KEYWORDS="美股,纳斯达克", discovery_keywords_list=["美股", "纳斯达克"],
        MAX_NOTES_PER_KEYWORD=10, MAX_COMMENTS_PER_NOTE=9,
    )
    base.update(over)
    return SimpleNamespace(**base)


def add_run(conn, started, finished=None, error=None, requests_est=0, status="success"):
    conn.execute(
        "INSERT INTO fetch_runs(mode, status, started_at_ms, finished_at_ms, error, requests_est)"
        " VALUES('discovery',?,?,?,?,?)",
        (status, started, finished or started + MIN, error, requests_est),
    )
    conn.commit()


def test_no_history_allows_fetch(conn):
    assert check_fetch_allowed(conn, gsettings(), manual=True, now=NOW) is None
    assert check_fetch_allowed(conn, gsettings(), manual=False, now=NOW) is None


def test_min_gap_blocks_manual_without_force(conn):
    add_run(conn, started=NOW - 10 * MIN)
    block = check_fetch_allowed(conn, gsettings(), manual=True, now=NOW)
    assert block["reason"] == "min_gap"
    assert block["force_allowed"] is True
    assert block["until_ms"] == NOW - 10 * MIN + 45 * MIN
    assert block["retry_after_ms"] == 35 * MIN


def test_min_gap_force_overrides_for_manual_only(conn):
    add_run(conn, started=NOW - 10 * MIN)
    assert check_fetch_allowed(conn, gsettings(), manual=True, force=True, now=NOW) is None
    # a scheduler must never force its way through
    block = check_fetch_allowed(conn, gsettings(), manual=False, force=True, now=NOW)
    assert block["reason"] == "min_gap"


def test_gap_expires(conn):
    add_run(conn, started=NOW - 46 * MIN)
    assert check_fetch_allowed(conn, gsettings(), manual=True, now=NOW) is None


def test_auth_cooldown_blocks_even_with_force(conn):
    add_run(conn, started=NOW - 60 * MIN, finished=NOW - 5 * MIN, error="login_required",
            status="failed")
    block = check_fetch_allowed(conn, gsettings(), manual=True, force=True, now=NOW)
    assert block["reason"] == "auth_cooldown"
    assert block["force_allowed"] is False
    assert block["until_ms"] == NOW - 5 * MIN + 30 * MIN


def test_verified_relogin_clears_cooldown(conn):
    add_run(conn, started=NOW - 10 * MIN, finished=NOW - 5 * MIN, error="login_required",
            status="failed")
    meta_set(conn, "login_verified_at_ms", str(NOW - MIN))
    conn.commit()
    # cooldown gone; the min-gap rule still applies to the recent run and can be forced
    block = check_fetch_allowed(conn, gsettings(), manual=True, now=NOW)
    assert block["reason"] == "min_gap"
    assert check_fetch_allowed(conn, gsettings(), manual=True, force=True, now=NOW) is None


def test_budget_counts_trailing_24h_and_blocks(conn):
    s = gsettings(DAILY_REQUEST_BUDGET=1000)
    # estimate: 2 keywords * 10 notes * (1+9) = 200 per cycle
    assert estimate_cycle_requests(conn, s) == 200
    add_run(conn, started=NOW - 23 * 60 * MIN, requests_est=900)
    add_run(conn, started=NOW - 30 * 60 * MIN, requests_est=500)  # older than 24h, ignored
    block = check_fetch_allowed(conn, gsettings(DAILY_REQUEST_BUDGET=1000), manual=True, now=NOW)
    assert block["reason"] == "daily_budget"
    assert block["budget"]["used_24h"] == 900
    assert block["force_allowed"] is True
    assert check_fetch_allowed(conn, s, manual=True, force=True, now=NOW) is None
    block = check_fetch_allowed(conn, s, manual=False, now=NOW)
    assert block["reason"] == "daily_budget"


def test_state_surfaces_countdowns_for_the_ui(conn):
    add_run(conn, started=NOW - 10 * MIN, finished=NOW - 5 * MIN, error="login_required",
            status="failed", requests_est=300)
    st = state(conn, gsettings(), now=NOW)
    assert st["cooldown_until_ms"] == NOW - 5 * MIN + 30 * MIN
    assert st["gap_until_ms"] == NOW - 10 * MIN + 45 * MIN
    assert st["budget"]["used_24h"] == 300
    assert st["budget"]["exhausted"] is False
