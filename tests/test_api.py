import threading
import time

import pytest
from fastapi.testclient import TestClient

import infinance.main as main_mod
from infinance.config import settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DB_PATH", tmp_path / "api.db")
    monkeypatch.setattr(settings, "STOCK_DICT_LOCAL_PATH", tmp_path / "overlay.json")
    monkeypatch.setattr(settings, "FETCH_INTERVAL_HOURS", 0)
    with TestClient(main_mod.app) as c:
        yield c


def test_status_and_empty_ranking(client):
    s = client.get("/api/status").json()
    assert s["running"] is False
    assert s["window_hours"] == settings.FRESH_WINDOW_HOURS
    r = client.get("/api/ranking").json()
    assert r["ranking"] == []
    assert r["radar"] == []


def test_tracked_crud(client):
    assert client.post("/api/tracked", json={"ticker": "cost", "custom_keywords": "开市客"}).status_code == 200
    rows = client.get("/api/tracked").json()
    assert rows[0]["ticker"] == "COST"
    assert rows[0]["custom_keywords"] == ["开市客"]
    assert client.post("/api/tracked", json={"ticker": "TOOLONG1"}).status_code == 422
    assert client.delete("/api/tracked/COST").status_code == 200
    assert client.delete("/api/tracked/COST").status_code == 404
    # tracked ticker with no data still renders in ranking
    client.post("/api/tracked", json={"ticker": "NVDA"})
    r = client.get("/api/ranking").json()
    assert any(e["ticker"] == "NVDA" and e["tracked"] for e in r["ranking"])


def test_ranking_includes_trend(client):
    from infinance.db import connect
    from infinance.scoring import snapshot_scores

    def stats(score):
        return {"NVDA": {"ticker": "NVDA", "score": score, "mentions": 2,
                         "note_count": 1, "comment_count": 1}}

    conn = connect(settings.DB_PATH)
    snapshot_scores(conn, stats(10.0), 1, now=1_000)
    snapshot_scores(conn, stats(20.0), 2, now=2_000)
    conn.close()

    client.post("/api/tracked", json={"ticker": "NVDA"})
    r = client.get("/api/ranking").json()
    e = next(x for x in r["ranking"] if x["ticker"] == "NVDA")
    assert e["trend"]["dir"] == "up"
    assert e["trend"]["delta_pct"] == 100


def test_double_fetch_409(client, monkeypatch):
    release = threading.Event()

    def slow_cycle(mode, *a, **kw):
        release.wait(timeout=5)

    monkeypatch.setattr("infinance.pipeline.run_cycle", slow_cycle)
    assert client.post("/api/fetch", json={"mode": "both"}).status_code == 202
    time.sleep(0.05)
    assert client.post("/api/fetch", json={"mode": "both"}).status_code == 409
    release.set()
    for _ in range(50):
        if not main_mod.fetch_lock.locked():
            break
        time.sleep(0.05)
    assert not main_mod.fetch_lock.locked()


def test_alias_suggestion_accept(client, tmp_path):
    from infinance.db import connect
    from infinance.util import now_ms

    conn = connect(settings.DB_PATH)
    conn.execute(
        "INSERT INTO alias_suggestions(term, guessed_ticker, evidence_quote, suggested_at_ms)"
        " VALUES('老黄家', 'NVDA', '老黄家的卡又涨了', ?)",
        (now_ms(),),
    )
    conn.commit()
    conn.close()

    rows = client.get("/api/alias_suggestions").json()
    assert len(rows) == 1
    sid = rows[0]["id"]
    assert client.post(f"/api/alias_suggestions/{sid}", json={"action": "accept"}).status_code == 200
    assert client.get("/api/alias_suggestions").json() == []

    import json as jsonlib
    overlay = jsonlib.loads((tmp_path / "overlay.json").read_text(encoding="utf-8"))
    assert overlay["stocks"][0]["ticker"] == "NVDA"
    assert "老黄家" in overlay["stocks"][0]["ambiguous"]

    from infinance.mentions import Matcher, load_stock_dict
    found = Matcher(load_stock_dict()).extract("老黄家股价又新高了")
    assert found.get("NVDA", ("", ""))[1] == "alias+context"
