"""TR-02: non-local binds refuse to start without AUTH_TOKEN; when exposed,
mutating endpoints demand the bearer token and cross-origin browser requests
are rejected. Local binds keep today's zero-auth behavior."""

import pytest
from fastapi.testclient import TestClient

import infinance.main as main_mod
from infinance.config import settings
from infinance.main import check_bind_security


@pytest.fixture
def exposed_client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DB_PATH", tmp_path / "sec.db")
    monkeypatch.setattr(settings, "FETCH_INTERVAL_HOURS", 0)
    monkeypatch.setattr(settings, "HOST", "0.0.0.0")
    monkeypatch.setattr(settings, "AUTH_TOKEN", "s3cret")
    with TestClient(main_mod.app) as c:
        yield c


def test_nonlocal_bind_without_token_refuses_to_start():
    with pytest.raises(RuntimeError, match="AUTH_TOKEN"):
        check_bind_security("0.0.0.0", "")
    with pytest.raises(RuntimeError, match="AUTH_TOKEN"):
        check_bind_security("192.168.1.20", "")


def test_local_bind_never_needs_token():
    check_bind_security("127.0.0.1", "")
    check_bind_security("localhost", "")
    check_bind_security("::1", "")


def test_exposed_reads_stay_open(exposed_client):
    assert exposed_client.get("/api/status").status_code == 200
    assert exposed_client.get("/api/ranking").status_code == 200


def test_exposed_mutations_require_bearer_token(exposed_client):
    r = exposed_client.post("/api/tracked", json={"ticker": "NVDA"})
    assert r.status_code == 401
    r = exposed_client.post(
        "/api/tracked", json={"ticker": "NVDA"},
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 401
    r = exposed_client.post(
        "/api/tracked", json={"ticker": "NVDA"},
        headers={"Authorization": "Bearer s3cret"},
    )
    assert r.status_code == 200
    # delete is gated too
    assert exposed_client.delete("/api/tracked/NVDA").status_code == 401
    assert exposed_client.delete(
        "/api/tracked/NVDA", headers={"Authorization": "Bearer s3cret"}
    ).status_code == 200


def test_exposed_cross_origin_mutation_rejected(exposed_client):
    r = exposed_client.post(
        "/api/tracked", json={"ticker": "NVDA"},
        headers={"Authorization": "Bearer s3cret", "Origin": "http://evil.example"},
    )
    assert r.status_code == 403
    # same-origin (browser sends Origin matching Host) passes
    r = exposed_client.post(
        "/api/tracked", json={"ticker": "NVDA"},
        headers={"Authorization": "Bearer s3cret", "Origin": "http://testserver"},
    )
    assert r.status_code == 200


def test_local_bind_mutations_stay_open(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DB_PATH", tmp_path / "local.db")
    monkeypatch.setattr(settings, "FETCH_INTERVAL_HOURS", 0)
    monkeypatch.setattr(settings, "HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "AUTH_TOKEN", "")
    with TestClient(main_mod.app) as c:
        assert c.post("/api/tracked", json={"ticker": "NVDA"}).status_code == 200
