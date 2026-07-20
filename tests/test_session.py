"""DC-01 backend: session health distinguishes the three observed failure
classes with distinct guidance, in-app login verifies and clears the auth
cooldown, and the cookie fallback validates format without ever echoing the
value back."""

import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import infinance.main as main_mod
from infinance import session
from infinance.config import settings
from infinance.db import connect, meta_get
from infinance.providers.base import LoginOutcome, SessionState


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DB_PATH", tmp_path / "sess.db")
    monkeypatch.setattr(settings, "FETCH_INTERVAL_HOURS", 0)
    monkeypatch.setattr(settings, "HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "XHS_COOKIES", "")
    monkeypatch.setattr(settings, "XHS_INTERNATIONAL", False)
    monkeypatch.setattr(settings, "MEDIACRAWLER_DIR", tmp_path / "vendor")
    with TestClient(main_mod.app) as c:
        yield c


def add_run(tmp_path, status="failed", error=None, notes_fresh=0, log_text=None):
    conn = connect(settings.DB_PATH)
    raw_dir = None
    if log_text is not None:
        raw_dir = tmp_path / "run_x"
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "crawler.log").write_text(log_text, encoding="utf-8")
    conn.execute(
        "INSERT INTO fetch_runs(mode, status, started_at_ms, finished_at_ms, error,"
        " notes_fresh, raw_dir) VALUES('discovery',?,?,?,?,?,?)",
        (status, 1000, 2000, error, notes_fresh, str(raw_dir) if raw_dir else None),
    )
    conn.commit()
    conn.close()


def test_no_runs_is_unknown(client):
    s = client.get("/api/session").json()
    assert s["state"] == "unknown"
    assert s["diagnosis"] == "none"
    assert s["source"] == "qrcode"


def test_expired_run_diagnosed(client, tmp_path):
    add_run(tmp_path, error="login_required", log_text="DataFetchError: 登录已过期\n")
    s = client.get("/api/session").json()
    assert s["state"] == "expired"
    assert s["diagnosis"] == "expired"


def test_unauthorized_suggests_backend_mismatch_first(client, tmp_path):
    add_run(tmp_path, error="login_required",
            log_text="您当前登录的账号没有权限访问该内容\n")
    s = client.get("/api/session").json()
    assert s["state"] == "unauthorized"
    assert s["diagnosis"] == "backend_mismatch"
    assert s["xhs_international"] is False


def test_unauthorized_with_intl_already_on_suggests_cookie(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "XHS_INTERNATIONAL", True)
    add_run(tmp_path, error="login_required", log_text="没有权限访问\n")
    s = client.get("/api/session").json()
    assert s["diagnosis"] == "try_cookie"


def test_unauthorized_with_cookie_means_gated_account(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "XHS_COOKIES", "a1=x; web_session=y")
    add_run(tmp_path, error="login_required", log_text="没有权限访问\n")
    s = client.get("/api/session").json()
    assert s["diagnosis"] == "account_gated"
    assert s["source"] == "cookie"


def test_successful_run_is_valid(client, tmp_path):
    add_run(tmp_path, status="success", notes_fresh=42, log_text="update_xhs_note ok\n")
    s = client.get("/api/session").json()
    assert s["state"] == "valid"


def test_cookie_endpoint_validates_and_never_echoes(client):
    r = client.post("/api/session/cookies", json={"cookies": "garbage"})
    assert r.status_code == 422
    r = client.post("/api/session/cookies", json={"cookies": "a1=abc; web_session=xyz"})
    assert r.status_code == 200
    s = client.get("/api/session").json()
    assert s["cookie"]["configured"] is True
    assert s["cookie"]["format_ok"] is True
    assert "abc" not in str(s) and "xyz" not in str(s)  # value never serialized
    # persisted for restarts
    conn = connect(settings.DB_PATH)
    assert meta_get(conn, session.COOKIE_OVERRIDE_KEY) == "a1=abc; web_session=xyz"
    conn.close()
    # delete reverts to the env value (empty in this fixture)
    assert client.delete("/api/session/cookies").json() == {"configured": False}
    assert settings.XHS_COOKIES == ""


def test_international_toggle_persists(client):
    r = client.post("/api/session/config", json={"xhs_international": True})
    assert r.status_code == 200
    assert settings.XHS_INTERNATIONAL is True
    conn = connect(settings.DB_PATH)
    assert meta_get(conn, session.INTL_OVERRIDE_KEY) == "true"
    # apply_overrides replays it onto a fresh settings object at startup
    fresh = SimpleNamespace(XHS_COOKIES="", XHS_INTERNATIONAL=False)
    session.apply_overrides(conn, fresh)
    conn.close()
    assert fresh.XHS_INTERNATIONAL is True


def test_login_flow_verifies_and_clears_cooldown(client, tmp_path, monkeypatch):
    class FakeProvider:
        def login(self, timeout_min=6):
            return LoginOutcome(True, SessionState.VALID, "fetched 3 rows")

        def classify_log(self, text):
            return SessionState.UNKNOWN

    monkeypatch.setattr(session, "get_provider", lambda s=None: FakeProvider())
    add_run(tmp_path, error="login_required", log_text="登录已过期\n")

    assert client.post("/api/session/login", json={}).status_code == 202
    for _ in range(100):
        s = client.get("/api/session").json()
        if not s["login_job"]["running"] and s["login_job"]["outcome"]:
            break
        time.sleep(0.05)
    assert s["login_job"]["outcome"]["ok"] is True
    # verified login flips state to valid even though the last run failed
    assert s["state"] == "valid"
    # and clears the DC-03 auth cooldown
    status = client.get("/api/status").json()
    assert status["guardrails"]["cooldown_until_ms"] is None
