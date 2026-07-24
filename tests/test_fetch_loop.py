"""run_fetch as a per-keyword loop: one crawler process per keyword, stop at the wall,
and advance the rotation only past what was actually sampled — the failure mode being
fixed is run 21, where one marathon process meant the cycle's tail keywords never ran
and the cursor skipped them anyway."""

import json

from infinance import keywords as keywords_mod
from infinance import pipeline
from infinance.config import Settings
from infinance.providers import RunResult
from infinance.providers.mediacrawler import (
    MediaCrawlerProvider as _MCP,
)
from infinance.providers.mediacrawler import (
    append_log_line,
    crawl_detail,
    failure_reason,
    keyword_counts,
)
from infinance.util import now_ms


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


class FakeProvider:
    """Interface wrapper around FakeCrawler: search() is scripted, while the
    artifact readers are the real ones — they parse what the fake wrote."""

    def __init__(self, fake: "FakeCrawler", settings):
        self.fake = fake
        self.settings = settings

    def search(self, req) -> RunResult:
        out = self.fake(req.keywords, req.run_dir, self.settings,
                        get_comments=req.get_comments)
        return RunResult(
            exit_code=out["exit_code"], timed_out=out["timed_out"], cancelled=False,
            log_path=out["log_path"], risk_controlled=out["risk_controlled"],
            captchas=out["captchas"], log_start=out["log_start"],
        )

    def keyword_counts(self, run_dir):
        return keyword_counts(run_dir)

    def crawl_detail(self, run_dir, keywords, status):
        return crawl_detail(run_dir, keywords, status)

    def failure_reason(self, log_path, exit_code, start=0):
        return failure_reason(log_path, exit_code, start)

    def append_log_line(self, log_path, message):
        append_log_line(log_path, message)

    def login_looks_required(self, log_path, notes_fresh, start=0):
        return _MCP.login_looks_required(self, log_path, notes_fresh, start)


def run(conn, tmp_path, monkeypatch, script, **settings_kw):
    s = make_settings(tmp_path, **settings_kw)
    fake = FakeCrawler(script)
    run_id = pipeline.run_fetch(conn, "discovery", {"stocks": []}, s,
                                provider=FakeProvider(fake, s))
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


def test_a_cancel_during_the_keyword_gap_does_not_wait_the_gap_out(conn, tmp_path, monkeypatch):
    """The gap is 12 min by default. A bare sleep there meant a cancel sat unheard that
    long while the crawl kept holding the browser — exactly when the user wants it back
    to log in. The pause has to wake on the event, not on the clock."""
    import threading
    import time

    cancel = threading.Event()
    fake = FakeCrawler({"池一": {"notes": 3}, "池二": {"notes": 3}, "核心": {"notes": 3}})
    # long enough that waiting it out would blow the test's runtime many times over
    s = make_settings(tmp_path, KEYWORD_GAP_MIN=5)
    threading.Timer(0.2, cancel.set).start()

    started = time.monotonic()
    run_id = pipeline.run_fetch(conn, "discovery", {"stocks": []}, s,
                                provider=FakeProvider(fake, s), cancel_event=cancel)
    elapsed = time.monotonic() - started

    assert elapsed < 30, f"cancel waited out the gap ({elapsed:.1f}s)"
    assert fake.calls == ["池一"]  # stopped in the gap, before keyword 2 launched Chrome
    row = conn.execute("SELECT * FROM fetch_runs WHERE id=?", (run_id,)).fetchone()
    assert row["error"] == "cancelled"
