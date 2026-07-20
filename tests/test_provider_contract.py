"""Contract tests: the pipeline consumes only the SourceProvider interface,
and a provider's run directory must be exactly what ingest.py can read
(xhs/jsonl/search_contents_*.jsonl + search_comments_*.jsonl, one JSON object
per line). A provider that satisfies these tests can replace MediaCrawler
without the pipeline noticing."""

import json
from pathlib import Path
from types import SimpleNamespace

from infinance import pipeline
from infinance.mentions import load_stock_dict
from infinance.providers.base import RunResult, SearchRequest, SessionState
from infinance.providers.mediacrawler import MediaCrawlerProvider, keyword_counts
from infinance.util import now_ms

H = 3_600_000


class RecordingProvider:
    """Interface-only fake: writes a recorded fixture run and remembers calls."""

    name = "recording"

    def __init__(self, rows_by_file=None, log_text="update_xhs_note ok\n"):
        self.rows_by_file = rows_by_file or {}
        self.log_text = log_text
        self.requests: list[SearchRequest] = []
        self.cancelled = False

    def preflight(self):
        return []

    def search(self, req: SearchRequest) -> RunResult:
        self.requests.append(req)
        out = req.run_dir / "xhs" / "jsonl"
        out.mkdir(parents=True, exist_ok=True)
        for name, rows in self.rows_by_file.items():
            with open(out / name, "a", encoding="utf-8") as f:
                for r in rows:
                    # each fixture row lands once, on the call whose keyword owns
                    # it (comments ride with the first keyword's process)
                    if r.get("source_keyword", req.keywords[0]) in req.keywords \
                            and not r.get("_emitted"):
                        r["_emitted"] = True
                        f.write(json.dumps(
                            {k: v for k, v in r.items() if k != "_emitted"},
                            ensure_ascii=False) + "\n")
        log_path = req.run_dir / "crawler.log"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(self.log_text)
        return RunResult(exit_code=0, timed_out=False, cancelled=False, log_path=log_path)

    def login(self, timeout_min: int = 6):
        raise AssertionError("pipeline must never trigger interactive login")

    def login_looks_required(self, log_path: Path, notes_fresh: int, start: int = 0) -> bool:
        return "登录已过期" in self.log_text

    def classify_log(self, log_text: str) -> SessionState:
        return SessionState.UNKNOWN

    def cancel(self):
        self.cancelled = True

    def keyword_counts(self, run_dir: Path):
        return keyword_counts(run_dir)

    def crawl_progress(self, run_dir, keywords, target_per_keyword=20):
        return {}

    def crawl_detail(self, run_dir, keywords, status):
        return {"keywords": [], "captchas": 0, "errors": [], "exceptions": [], "log_tail": ""}

    def failure_reason(self, log_path, exit_code, start=0):
        return f"crawler exit code {exit_code}"

    def append_log_line(self, log_path, message):
        pass


def settings_for(tmp_path):
    return SimpleNamespace(
        RAW_DIR=tmp_path / "raw",
        DISCOVERY_KEYWORDS="美股,纳斯达克",
        discovery_keywords_list=["美股", "纳斯达克"],
        discovery_core_list=[], discovery_pool_list=[], discovery_investment_pool_list=[],
        KEYWORDS_PER_CYCLE=6, KEYWORD_GAP_MIN=0,
        MAX_NOTES_PER_KEYWORD=5, MAX_COMMENTS_PER_NOTE=10,
        ENABLE_SUB_COMMENTS=False, CRAWL_TIMEOUT_MIN=1,
        fresh_window_ms=24 * H, context_window_ms=72 * H,
    )


def test_run_fetch_consumes_only_the_interface(conn, tmp_path):
    now = now_ms()
    provider = RecordingProvider({
        "search_contents_2026-07-18.jsonl": [
            {"note_id": "n1", "title": "NVDA 新高", "desc": "老黄又赢了", "time": now - H,
             "liked_count": "88", "note_url": "https://x/n1", "source_keyword": "美股"},
        ],
        "search_comments_2026-07-18.jsonl": [
            {"comment_id": "c1", "note_id": "n1", "content": "冲了兄弟们", "create_time": now - H,
             "like_count": "3"},
        ],
    })
    run_id = pipeline.run_fetch(
        conn, "discovery", load_stock_dict(), settings_for(tmp_path), provider
    )
    assert run_id is not None
    # one crawler process per keyword — the per-keyword loop is the contract now
    assert [r.keywords for r in provider.requests] == [["美股"], ["纳斯达克"]]
    run = conn.execute("SELECT * FROM fetch_runs WHERE id=?", (run_id,)).fetchone()
    assert run["status"] == "success"
    assert run["notes_fresh"] == 1
    assert run["comments_fresh"] == 1
    assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1


def test_run_fetch_flags_login_required_from_provider_classification(conn, tmp_path):
    provider = RecordingProvider(log_text="DataFetchError: 登录已过期\n")
    run_id = pipeline.run_fetch(
        conn, "discovery", load_stock_dict(), settings_for(tmp_path), provider
    )
    run = conn.execute("SELECT * FROM fetch_runs WHERE id=?", (run_id,)).fetchone()
    assert run["status"] == "failed"
    assert run["error"] == "login_required"


def test_mediacrawler_provider_writes_ingestable_layout(tmp_path, make_vendor):
    """The real adapter, spawn faked to emit what MediaCrawler emits, produces
    a run dir the ingest layer accepts — the output half of the contract."""
    from infinance.ingest import ingest_run_dir

    vendor = make_vendor()
    provider = MediaCrawlerProvider(SimpleNamespace(
        MEDIACRAWLER_DIR=vendor, UV_EXE="uv", XHS_COOKIES="",
        XHS_INTERNATIONAL=False, BROWSER_USER_AGENT="UA/1.0",
        BROWSER_HEADLESS=True, CRAWL_SLEEP_SEC=8, CAPTCHA_ABORT_COUNT=10,
        MAX_NOTES_PER_KEYWORD=5,
    ))
    now = now_ms()

    def spawn(cmd, log_path, timeout_s):
        out = Path(cmd[cmd.index("--save_data_path") + 1]) / "xhs" / "jsonl"
        out.mkdir(parents=True, exist_ok=True)
        (out / "search_contents_2026-07-18.jsonl").write_text(
            json.dumps({"note_id": "n1", "title": "特斯拉", "desc": "财报", "time": now - H,
                        "liked_count": "10+", "source_keyword": "美股"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        log_path.write_text("update_xhs_note ok\n", encoding="utf-8")
        return {"exit_code": 0, "timed_out": False, "risk_controlled": False,
                "captchas": 0, "log_start": 0}

    provider._spawn = spawn
    result = provider.search(SearchRequest(
        keywords=["美股"], run_dir=tmp_path / "run", max_notes_per_keyword=5,
        max_comments_per_note=10, include_sub_comments=False, timeout_min=1,
    ))
    assert result.exit_code == 0

    from infinance.db import connect
    conn = connect(tmp_path / "contract.db")
    stats = ingest_run_dir(conn, tmp_path / "run", run_id=1, fresh_window_ms=24 * H, now=now)
    conn.close()
    assert stats["notes_fresh"] == 1
