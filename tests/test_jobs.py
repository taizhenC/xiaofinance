"""DC-02: stage transitions are visible, and cancel leaves DB + lock state
consistent (no stuck 'running' rows, banked data kept, later stages skipped)."""

import json
import threading
import time
from types import SimpleNamespace

from infinance import pipeline
from infinance.db import connect
from infinance.jobs import JobRunner, JobState
from infinance.mentions import load_stock_dict
from infinance.providers.base import RunResult, SearchRequest
from infinance.util import now_ms

H = 3_600_000


def cycle_settings(tmp_path, **over):
    base = dict(
        DB_PATH=tmp_path / "cycle.db", RAW_DIR=tmp_path / "raw",
        DISCOVERY_KEYWORDS="美股", discovery_keywords_list=["美股"],
        MAX_NOTES_PER_KEYWORD=5, MAX_COMMENTS_PER_NOTE=5,
        ENABLE_SUB_COMMENTS=False, CRAWL_TIMEOUT_MIN=1,
        FRESH_WINDOW_HOURS=24, fresh_window_ms=24 * H,
        MIN_MENTIONS_FOR_ANALYSIS=1, MAX_ANALYZED_STOCKS=5,
        ENABLE_PRICE_QUOTES=False, SLANG_SCAN_EVERY_N_CYCLES=0,
        DEEPSEEK_API_KEY="", LLM_MODEL="deepseek-chat",
    )
    base.update(over)
    return SimpleNamespace(**base)


class SlowFakeProvider:
    """Writes one fresh note, then (optionally) blocks until cancelled —
    close enough to a real crawl for orchestration tests."""

    name = "slow-fake"

    def __init__(self, block_event: threading.Event | None = None):
        self.block_event = block_event
        self.cancel_called = threading.Event()
        self.searches = 0

    def preflight(self):
        return []

    def search(self, req: SearchRequest) -> RunResult:
        self.searches += 1
        out = req.run_dir / "xhs" / "jsonl"
        out.mkdir(parents=True, exist_ok=True)
        row = {"note_id": f"n{self.searches}", "title": "英伟达 涨了", "desc": "股价 好",
               "time": now_ms() - H, "liked_count": "5", "source_keyword": "美股"}
        (out / "search_contents_x.jsonl").write_text(
            json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
        log_path = req.run_dir / "crawler.log"
        log_path.write_text("update_xhs_note ok\n", encoding="utf-8")
        if self.block_event is not None:
            # a real crawl blocks here until the subprocess dies
            self.cancel_called.wait(timeout=10)
        return RunResult(exit_code=0, timed_out=False,
                         cancelled=self.cancel_called.is_set(), log_path=log_path)

    def login(self, timeout_min: int = 6):
        raise AssertionError("never")

    def login_looks_required(self, log_path, notes_fresh):
        return False

    def classify_log(self, log_text):
        from infinance.providers.base import SessionState

        return SessionState.UNKNOWN

    def cancel(self):
        self.cancel_called.set()


def test_progress_reports_every_stage(tmp_path):
    s = cycle_settings(tmp_path)
    stages = []
    provider = SlowFakeProvider()

    def report(stage=None, **detail):
        if stage and (not stages or stages[-1] != stage):
            stages.append(stage)

    result = pipeline.run_cycle("discovery", settings=s, provider=provider, progress=report)
    assert result["cancelled"] is False
    assert stages[0] == "crawl:discovery"
    for expected in ("ingest:discovery", "dedup", "mentions", "analyze", "cleanup"):
        assert expected in stages, f"missing stage {expected}: {stages}"

    conn = connect(s.DB_PATH)
    run = conn.execute("SELECT * FROM fetch_runs").fetchone()
    assert run["status"] == "success"
    conn.close()


def test_cancel_mid_crawl_keeps_banked_data_and_skips_analysis(tmp_path):
    s = cycle_settings(tmp_path)
    block = threading.Event()
    provider = SlowFakeProvider(block_event=block)
    cancel_event = threading.Event()
    done = {}

    def run():
        done["result"] = pipeline.run_cycle(
            "discovery", settings=s, provider=provider, cancel_event=cancel_event)

    t = threading.Thread(target=run)
    t.start()
    time.sleep(0.3)  # let the crawl start and block
    cancel_event.set()
    provider.cancel()
    t.join(timeout=10)
    assert not t.is_alive()

    assert done["result"]["cancelled"] is True
    conn = connect(s.DB_PATH)
    run_row = conn.execute("SELECT * FROM fetch_runs").fetchone()
    # no stuck 'running' rows; the aborted crawl's data was still ingested
    assert run_row["status"] in ("partial", "failed")
    assert run_row["error"] == "cancelled"
    assert run_row["notes_fresh"] == 1
    assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1
    # analysis never ran
    assert conn.execute("SELECT COUNT(*) FROM stock_analyses").fetchone()[0] == 0
    conn.close()


def test_job_runner_lifecycle_and_cancel(tmp_path, monkeypatch):
    lock = threading.Lock()
    runner = JobRunner(lock)
    started = threading.Event()

    def fake_cycle(mode, settings=None, provider=None, progress=None, cancel_event=None):
        progress(stage="crawl:discovery", keywords=3)
        started.set()
        cancel_event.wait(timeout=10)
        return {"cancelled": True, "run_ids": []}

    monkeypatch.setattr("infinance.pipeline.run_cycle", fake_cycle)
    job = runner.start("both")
    assert job is not None
    assert runner.start("both") is None  # mutual exclusion
    assert started.wait(timeout=5)
    st = runner.status()
    assert st["stage"] == "crawl:discovery"
    assert st["detail"]["keywords"] == 3

    assert runner.cancel() is True
    for _ in range(100):
        if runner.status()["done"]:
            break
        time.sleep(0.05)
    st = runner.status()
    assert st["done"] and st["cancelled"] and st["stage"] == "cancelled"
    assert not lock.locked()
    assert runner.cancel() is False  # nothing to cancel anymore


def test_job_state_stage_change_resets_detail():
    job = JobState(id=1, mode="both", started_at_ms=0)
    job.report(stage="crawl:discovery", notes_seen=10)
    job.report(stage="analyze", done=1, total=5)
    assert "notes_seen" not in job.detail
    assert job.snapshot()["detail"] == {"done": 1, "total": 5}


def test_analyze_progress_counts(conn):
    from infinance.analyze import analyze_all

    now = now_ms()
    conn.execute("INSERT INTO notes(note_id, title, publish_time_ms, liked_count)"
                 " VALUES('n1','英伟达冲',?,10)", (now - H,))
    conn.execute("INSERT INTO stock_mentions(ticker, source_type, source_id, note_id,"
                 " match_basis, content_time_ms) VALUES('NVDA','note','n1','n1','safe_alias',?)",
                 (now - H,))
    conn.commit()
    from infinance.scoring import compute_stats

    stats = compute_stats(conn, 24 * H, now)
    reports = []

    def report(stage=None, **detail):
        reports.append(detail)

    s = SimpleNamespace(DEEPSEEK_API_KEY="", fresh_window_ms=24 * H, LLM_MODEL="m")
    results = analyze_all(conn, s, load_stock_dict(), stats, set(), 1, 5, progress=report)
    assert results.get("NVDA") == "no_api_key"
    assert {"done": 1, "total": 1} in [{k: d[k] for k in ("done", "total") if k in d} for d in reports]
