"""Account-safety guardrails (DC-03) — on by default.

The MVP's safety posture (low volume, concurrency 1, polite sleeps) was
static and invisible. Public users will click "fetch now" repeatedly and
re-login in loops — exactly the behaviors that get XHS accounts restricted.
These gates make protection a default, not README advice:

- minimum gap between cycles: a manual fetch inside the gap needs an explicit
  confirm (force); scheduled runs inside the gap are skipped silently.
- daily request budget: the trailing-24h sum of fetched items plus the
  projected next cycle must fit the budget; manual force may override.
- auth cooldown: after a login_required run, crawling is blocked for a
  cooldown period with a visible countdown. force does NOT override it —
  retrying into a flagged session raises the account's risk score, which is
  the one outcome this feature exists to prevent. A verified re-login
  (login_verified_at_ms in meta) clears it immediately.
"""

import logging

from .db import meta_get
from .util import now_ms

log = logging.getLogger(__name__)

DAY_MS = 86_400_000


def estimate_cycle_requests(conn, settings) -> int:
    """Worst-case fetched items (notes + their comment pages) for one full
    cycle with the current keyword set. Deliberately pessimistic: the budget
    should trip *before* a mis-configured keyword list burns the account."""
    from .mentions import build_tracked_keywords, load_stock_dict

    n_keywords = len(settings.discovery_keywords_list)
    try:
        tracked = conn.execute(
            "SELECT ticker, custom_keywords FROM tracked_stocks ORDER BY ticker"
        ).fetchall()
        if tracked:
            queries, _ = build_tracked_keywords(load_stock_dict(), tracked)
            n_keywords += len(queries)
    except Exception:
        log.exception("tracked-keyword estimate failed; using discovery only")
    per_keyword = settings.MAX_NOTES_PER_KEYWORD * (1 + settings.MAX_COMMENTS_PER_NOTE)
    return n_keywords * per_keyword


def state(conn, settings, now: int | None = None) -> dict:
    """Current guardrail state, also served on /api/status for the UI."""
    now = now or now_ms()

    cooldown_until = None
    flagged = conn.execute(
        "SELECT * FROM fetch_runs WHERE error='login_required' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if flagged:
        base = flagged["finished_at_ms"] or flagged["started_at_ms"]
        verified = int(meta_get(conn, "login_verified_at_ms", "0") or 0)
        until = base + settings.AUTH_COOLDOWN_MIN * 60_000
        if verified < base and until > now:
            cooldown_until = until

    gap_until = None
    last = conn.execute(
        "SELECT started_at_ms FROM fetch_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if last and last["started_at_ms"]:
        until = last["started_at_ms"] + settings.MIN_FETCH_GAP_MIN * 60_000
        if until > now:
            gap_until = until

    used = conn.execute(
        "SELECT COALESCE(SUM(requests_est), 0) FROM fetch_runs WHERE started_at_ms >= ?",
        (now - DAY_MS,),
    ).fetchone()[0]
    estimated = estimate_cycle_requests(conn, settings)

    return {
        "cooldown_until_ms": cooldown_until,
        "gap_until_ms": gap_until,
        "budget": {
            "limit": settings.DAILY_REQUEST_BUDGET,
            "used_24h": used,
            "estimated_next_cycle": estimated,
            "exhausted": used + estimated > settings.DAILY_REQUEST_BUDGET,
        },
    }


def check_fetch_allowed(conn, settings, manual: bool, force: bool = False,
                        now: int | None = None) -> dict | None:
    """None when a cycle may start; otherwise a block descriptor
    {reason, until_ms, retry_after_ms, force_allowed} for the API/UI."""
    now = now or now_ms()
    st = state(conn, settings, now)

    if st["cooldown_until_ms"]:
        return {
            "reason": "auth_cooldown",
            "until_ms": st["cooldown_until_ms"],
            "retry_after_ms": st["cooldown_until_ms"] - now,
            "force_allowed": False,
        }
    if st["gap_until_ms"] and not (manual and force):
        return {
            "reason": "min_gap",
            "until_ms": st["gap_until_ms"],
            "retry_after_ms": st["gap_until_ms"] - now,
            "force_allowed": manual,
        }
    if st["budget"]["exhausted"] and not (manual and force):
        return {
            "reason": "daily_budget",
            "until_ms": None,
            "retry_after_ms": None,
            "budget": st["budget"],
            "force_allowed": manual,
        }
    return None
