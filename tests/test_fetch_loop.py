"""run_fetch as a per-keyword loop: one crawler process per keyword, stop at the wall,
and advance the rotation only past what was actually sampled — the failure mode being
fixed is run 21, where one marathon process meant the cycle's tail keywords never ran
and the cursor skipped them anyway."""

import json

from app import keywords as keywords_mod
from app import pipeline
from app.config import Settings
from app.util import now_ms


def make_settings(tmp_path, **kw):
    kw.setdefault("KEYWORD_GAP_MIN", 0)
    return Settings(
        RAW_DIR=tmp_path / "raw",
        DISCOVERY_CORE="核心",
        DISCOVERY_POOL="池一,池二,池三,池四",
        KEYWORDS_PER_CYCLE=3,
        **kw,
    )


class FakeCrawler:
    """Scripted stand-in for run_crawl: writes the same artifacts (shared log, jsonl)
    the real one leaves behind, so keyword_counts / login detection / crawl_detail all
    run for real against them."""

    def __init__(self, script: dict):
        self.script = script
        self.calls = []

    def __call__(self, keywords, run_dir, settings, get_comments=True):
        assert len(keywords) == 1, "each keyword must get its own crawler process"
        kw = keywords[0]
        self.calls.append(kw)
        spec = self.script[kw]
        run_dir.mkdir(parents=True, exist_ok=True)
        log_path = run_dir / "crawler.log"
        start = log_path.stat().st_size if log_path.exists() else 0
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"2026-07-12 16:00:00 INFO (core.py:140) - Current search keyword: {kw}\n")
            f.write(spec.get("log", ""))
        if spec.get("notes"):
            jdir = run_dir / "xhs" / "jsonl"
            jdir.mkdir(parents=True, exist_ok=True)
            with open(jdir / "search_contents_2026-07-12.jsonl", "a", encoding="utf-8") as f:
                for i in range(spec["notes"]):
                    f.write(json.dumps(
                        {"note_id": f"{kw}-{i}", "time": now_ms(), "source_keyword": kw},
                        ensure_ascii=False,
                    ) + "\n")
        return {
            "exit_code": spec.get("exit", 0),
            "timed_out": spec.get("timed_out", False),
            "log_path": log_path,
            "risk_controlled": spec.get("walled", False),
            "captchas": spec.get("captchas", 0),
            "log_start": start,
        }


def run(conn, tmp_path, monkeypatch, script, **settings_kw):
    s = make_settings(tmp_path, **settings_kw)
    fake = FakeCrawler(script)
    monkeypatch.setattr(pipeline.crawler_runner, "run_crawl", fake)
    run_id = pipeline.run_fetch(conn, "discovery", {"stocks": []}, s)
    row = conn.execute("SELECT * FROM fetch_runs WHERE id=?", (run_id,)).fetchone()
    return fake, row, s


def test_a_clean_cycle_runs_every_keyword_in_its_own_process(conn, tmp_path, monkeypatch):
    fake, row, s = run(conn, tmp_path, monkeypatch, {
        "池一": {"notes": 3}, "池二": {"notes": 3}, "核心": {"notes": 3},
    })
    assert fake.calls == ["池一", "池二", "核心"]  # pool leads, core takes the tail
    assert row["status"] == "success" and row["error"] is None
    assert row["notes_fresh"] == 9
    keywords, _ = keywords_mod.select_keywords(conn, s)
    assert keywords[:2] == ["池三", "池四"]  # both picks sampled, cursor moved past them


def test_the_wall_stops_the_cycle_and_the_eaten_keyword_leads_the_next(conn, tmp_path, monkeypatch):
    fake, row, s = run(conn, tmp_path, monkeypatch, {
        "池一": {"notes": 5},
        "池二": {"walled": True, "captchas": 10, "exit": 1},
        "核心": {"notes": 5},
    })
    assert fake.calls == ["池一", "池二"]  # no request is spent into the wall
    assert row["status"] == "partial"
    assert "CAPTCHA" in row["error"]  # the wording risk_cooldown_until keys on
    keywords, _ = keywords_mod.select_keywords(conn, s)
    assert keywords[0] == "池二"  # not skipped until the pool wraps


def test_a_crawler_that_died_after_a_few_461s_counts_as_walled(conn, tmp_path, monkeypatch):
    """Below CAPTCHA_ABORT_COUNT the watcher never kills the process — it dies on its own
    RetryError. Same wall, and it must stop the cycle the same way."""
    fake, row, _ = run(conn, tmp_path, monkeypatch, {
        "池一": {"exit": 1, "captchas": 3},
        "池二": {"notes": 5},
        "核心": {"notes": 5},
    })
    assert fake.calls == ["池一"]
    assert row["status"] == "failed"
    assert "CAPTCHA" in row["error"]


def test_a_login_failure_stops_the_cycle_and_holds_the_rotation(conn, tmp_path, monkeypatch):
    fake, row, s = run(conn, tmp_path, monkeypatch, {
        "池一": {"exit": 1, "log": "登录已过期\n"},
        "池二": {"notes": 5},
        "核心": {"notes": 5},
    })
    assert fake.calls == ["池一"]
    assert row["status"] == "failed" and row["error"] == "login_required"
    keywords, _ = keywords_mod.select_keywords(conn, s)
    assert keywords[0] == "池一"


def test_a_non_wall_failure_skips_the_keyword_but_finishes_the_cycle(conn, tmp_path, monkeypatch):
    fake, row, s = run(conn, tmp_path, monkeypatch, {
        "池一": {"exit": 1, "timed_out": True},
        "池二": {"notes": 5},
        "核心": {"notes": 5},
    })
    assert fake.calls == ["池一", "池二", "核心"]
    assert row["status"] == "partial"
    assert "池一" in row["error"] and "timeout" in row["error"]
    # the unsampled first pick stops the cursor, so it gets retried next cycle
    keywords, _ = keywords_mod.select_keywords(conn, s)
    assert keywords[0] == "池一"


def test_the_gap_between_keywords_is_written_into_the_shared_log(conn, tmp_path, monkeypatch):
    fake, row, _ = run(conn, tmp_path, monkeypatch, {
        "池一": {"notes": 3}, "池二": {"notes": 3}, "核心": {"notes": 3},
    }, KEYWORD_GAP_MIN=0.0001)
    text = (tmp_path / "raw" / f"run_{row['id']:06d}" / "crawler.log").read_text(encoding="utf-8")
    assert text.count("xiaofinance INFO - pausing") == 2  # between keywords, not after the last
