"""Session health center backend (DC-01).

Login is the product's biggest defect surface: a dead session makes every
other feature worthless. This module gives the UI everything it needs to fix
one without touching a terminal or hand-editing .env:

- health(): the current session state (valid/expired/unauthorized/unknown)
  plus a guided diagnosis distinguishing the three observed failure classes —
  expired session, mainland-vs-RedNote backend mismatch (suggests
  XHS_INTERNATIONAL from log evidence), and platform-gated account (advice:
  stop retrying).
- start_login(): drives the provider's visible-browser QR flow in a worker
  thread; a verified login writes meta login_verified_at_ms, which also
  clears the DC-03 auth cooldown.
- Cookie-paste fallback and the XHS_INTERNATIONAL toggle, persisted in the
  meta table and applied to the live settings object — the .env stays the
  domain of power users. Cookie VALUES never leave the server: endpoints
  return only configured/format flags.
"""

import logging
import threading
from pathlib import Path

from .db import connect, meta_get, meta_set
from .providers import SessionState, get_provider
from .util import now_ms

log = logging.getLogger(__name__)

COOKIE_OVERRIDE_KEY = "xhs_cookies_override"
INTL_OVERRIDE_KEY = "xhs_international_override"
LOGIN_VERIFIED_KEY = "login_verified_at_ms"

MAX_LOG_READ = 200_000  # tail size; login/auth markers appear near the end

_login_lock = threading.Lock()
_login_state: dict = {
    "running": False, "started_at_ms": None, "finished_at_ms": None, "outcome": None,
}


def apply_overrides(conn, settings) -> None:
    """Apply UI-saved overrides on startup so they survive restarts."""
    cookies = meta_get(conn, COOKIE_OVERRIDE_KEY)
    if cookies:
        settings.XHS_COOKIES = cookies
    intl = meta_get(conn, INTL_OVERRIDE_KEY)
    if intl is not None:
        settings.XHS_INTERNATIONAL = intl == "true"


def cookie_format_ok(cookies: str) -> bool:
    return "a1=" in cookies and "web_session=" in cookies


def set_cookies(conn, settings, cookies: str) -> None:
    cookies = cookies.strip()
    meta_set(conn, COOKIE_OVERRIDE_KEY, cookies)
    conn.commit()
    settings.XHS_COOKIES = cookies


def clear_cookies(conn, settings, env_cookies: str) -> None:
    conn.execute("DELETE FROM meta WHERE key=?", (COOKIE_OVERRIDE_KEY,))
    conn.commit()
    settings.XHS_COOKIES = env_cookies


def set_international(conn, settings, value: bool) -> None:
    meta_set(conn, INTL_OVERRIDE_KEY, "true" if value else "false")
    conn.commit()
    settings.XHS_INTERNATIONAL = value


def _last_finished_run(conn):
    return conn.execute(
        "SELECT * FROM fetch_runs WHERE status != 'running' ORDER BY id DESC LIMIT 1"
    ).fetchone()


def _read_run_log(run) -> str:
    if not run or not run["raw_dir"]:
        return ""
    path = Path(run["raw_dir"]) / "crawler.log"
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return data[-MAX_LOG_READ:].decode("utf-8", errors="replace")


def health(conn, settings) -> dict:
    """Session state + guided diagnosis for the login center UI."""
    provider = get_provider(settings)
    last = _last_finished_run(conn)
    verified_at = int(meta_get(conn, LOGIN_VERIFIED_KEY, "0") or 0)
    cookie = (settings.XHS_COOKIES or "").strip()

    log_state = SessionState.UNKNOWN
    if last is not None:
        log_text = _read_run_log(last)
        if log_text:
            log_state = provider.classify_log(log_text)

    state = "unknown"
    diagnosis = "none"
    if last is not None:
        if log_state == SessionState.UNAUTHORIZED:
            state = "unauthorized"
            if not settings.XHS_INTERNATIONAL and not cookie:
                # most common cause for overseas users: rednote.com account
                # against the mainland backend
                diagnosis = "backend_mismatch"
            elif not cookie:
                diagnosis = "try_cookie"
            else:
                diagnosis = "account_gated"
        elif last["error"] == "login_required" or log_state == SessionState.EXPIRED:
            state = "expired"
            diagnosis = "expired"
        elif last["notes_fresh"] and last["notes_fresh"] > 0:
            state = "valid"
    # a verified in-app login after the last run overrides its verdict
    last_end = (last["finished_at_ms"] or last["started_at_ms"]) if last is not None else 0
    if verified_at and verified_at >= last_end:
        state, diagnosis = "valid", "none"

    return {
        "state": state,
        "source": "cookie" if cookie else "qrcode",
        "diagnosis": diagnosis,
        "xhs_international": bool(settings.XHS_INTERNATIONAL),
        "cookie": {
            "configured": bool(cookie),
            "format_ok": cookie_format_ok(cookie) if cookie else None,
        },
        "login_verified_at_ms": verified_at or None,
        "last_run_id": last["id"] if last is not None else None,
        "login_job": dict(_login_state),
    }


def start_login(settings, timeout_min: int = 6) -> bool:
    """Run the provider's interactive login in a worker thread.
    False when a login is already in progress."""
    if not _login_lock.acquire(blocking=False):
        return False
    _login_state.update(
        running=True, started_at_ms=now_ms(), finished_at_ms=None, outcome=None,
    )

    def worker():
        outcome = None
        try:
            provider = get_provider(settings)
            outcome = provider.login(timeout_min=timeout_min)
            if outcome.ok:
                conn = connect(settings.DB_PATH)
                try:
                    meta_set(conn, LOGIN_VERIFIED_KEY, str(now_ms()))
                    conn.commit()
                finally:
                    conn.close()
        except Exception as e:
            log.exception("login job failed")
            outcome = None
            _login_state.update(error=str(e)[:300])
        finally:
            _login_state.update(
                running=False, finished_at_ms=now_ms(),
                outcome={
                    "ok": outcome.ok, "state": str(outcome.state), "detail": outcome.detail,
                } if outcome is not None else None,
            )
            _login_lock.release()

    threading.Thread(target=worker, daemon=True).start()
    return True


def login_running() -> bool:
    return bool(_login_state["running"])
