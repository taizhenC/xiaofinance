import argparse
import json
import logging
import shutil
import threading
from pathlib import Path

from . import analyze, dedup, ingest, mentions, prices, scoring, slang_scan
from .config import settings as default_settings
from .db import connect, meta_get, meta_set
from .providers import SearchRequest, get_provider
from .util import now_ms

log = logging.getLogger(__name__)

RETAIN_CONTENT_DAYS = 7
RETAIN_RUNS_DAYS = 30


def _tracked_rows(conn):
    return conn.execute("SELECT ticker, custom_keywords FROM tracked_stocks ORDER BY ticker").fetchall()


def _count_jsonl_lines(run_dir: Path, prefix: str) -> int:
    total = 0
    for f in (run_dir / "xhs" / "jsonl").glob(f"{prefix}_*.jsonl"):
        try:
            with open(f, "rb") as fh:
                total += sum(1 for _ in fh)
        except OSError:
            pass
    return total


def _tail_crawl_output(run_dir: Path, report, stop: threading.Event) -> None:
    """While the crawl subprocess runs, poll its JSONL output so the UI sees
    live counts instead of a 10-minute opaque spinner."""
    while not stop.wait(2):
        report(
            notes_seen=_count_jsonl_lines(run_dir, "search_contents"),
            comments_seen=_count_jsonl_lines(run_dir, "search_comments"),
        )


def run_fetch(conn, mode: str, dict_data: dict, settings, provider=None, progress=None) -> int | None:
    if mode == "discovery":
        keywords = settings.discovery_keywords_list
    else:
        keywords, _ = mentions.build_tracked_keywords(dict_data, _tracked_rows(conn))
        if not keywords:
            log.info("no tracked tickers, skipping tracked run")
            return None

    provider = provider or get_provider(settings)
    report = progress or (lambda stage=None, **kw: None)
    started = now_ms()
    cur = conn.execute(
        "INSERT INTO fetch_runs(mode, keywords, status, started_at_ms) VALUES(?,?,'running',?)",
        (mode, ",".join(keywords), started),
    )
    run_id = cur.lastrowid
    conn.commit()
    run_dir = Path(settings.RAW_DIR) / f"run_{run_id:06d}"
    report(stage=f"crawl:{mode}", run_id=run_id, keywords=len(keywords),
           notes_seen=0, comments_seen=0)

    status, error = "failed", None
    stats = {"notes_fetched": 0, "notes_fresh": 0, "comments_fresh": 0, "malformed": 0}
    stop_tail = threading.Event()
    tail = threading.Thread(
        target=_tail_crawl_output, args=(run_dir, report, stop_tail), daemon=True
    )
    tail.start()
    try:
        result = provider.search(SearchRequest(
            keywords=keywords, run_dir=run_dir,
            max_notes_per_keyword=settings.MAX_NOTES_PER_KEYWORD,
            max_comments_per_note=settings.MAX_COMMENTS_PER_NOTE,
            include_sub_comments=settings.ENABLE_SUB_COMMENTS,
            timeout_min=settings.CRAWL_TIMEOUT_MIN,
        ))
        stop_tail.set()
        tail.join(timeout=5)
        report(stage=f"ingest:{mode}")
        stats = ingest.ingest_run_dir(conn, run_dir, run_id, settings.fresh_window_ms)
        report(notes_fresh=stats["notes_fresh"], comments_fresh=stats["comments_fresh"])
        if result.cancelled:
            status = "partial" if stats["notes_fresh"] > 0 else "failed"
            error = "cancelled"
        elif provider.login_looks_required(result.log_path, stats["notes_fresh"]):
            status, error = "failed", "login_required"
        elif result.timed_out:
            status = "partial" if stats["notes_fresh"] > 0 else "failed"
            error = f"timeout after {settings.CRAWL_TIMEOUT_MIN} min"
        elif result.exit_code != 0:
            status = "partial" if stats["notes_fresh"] > 0 else "failed"
            error = f"crawler exit code {result.exit_code}"
        elif stats["malformed"] > 0:
            status, error = "partial", f"{stats['malformed']} malformed lines skipped"
        else:
            status = "success"
    except Exception as e:
        error = str(e)[:500]
        log.exception("fetch run %d failed", run_id)
    finally:
        stop_tail.set()

    requests_est = stats["notes_fetched"] + stats.get("comments_seen", 0)
    conn.execute(
        """UPDATE fetch_runs SET status=?, finished_at_ms=?, notes_fetched=?, notes_fresh=?,
           comments_fresh=?, requests_est=?, raw_dir=?, error=? WHERE id=?""",
        (status, now_ms(), stats["notes_fetched"], stats["notes_fresh"],
         stats["comments_fresh"], requests_est, str(run_dir), error, run_id),
    )
    conn.commit()
    log.info("run %d (%s): %s %s", run_id, mode, status, error or "")
    return run_id


def cleanup(conn, settings, now: int | None = None) -> None:
    now = now or now_ms()
    content_cutoff = now - RETAIN_CONTENT_DAYS * 86_400_000
    runs_cutoff = now - RETAIN_RUNS_DAYS * 86_400_000

    for row in conn.execute(
        "SELECT raw_dir FROM fetch_runs WHERE started_at_ms < ? AND raw_dir IS NOT NULL",
        (content_cutoff,),
    ).fetchall():
        shutil.rmtree(row["raw_dir"], ignore_errors=True)

    conn.execute("DELETE FROM stock_mentions WHERE content_time_ms < ?", (content_cutoff,))
    conn.execute("DELETE FROM comments WHERE create_time_ms < ?", (content_cutoff,))
    conn.execute("DELETE FROM notes WHERE publish_time_ms < ?", (content_cutoff,))
    conn.execute("DELETE FROM stock_analyses WHERE generated_at_ms < ?", (runs_cutoff,))
    conn.execute("DELETE FROM score_snapshots WHERE snapped_at_ms < ?", (runs_cutoff,))
    conn.execute("DELETE FROM fetch_runs WHERE started_at_ms < ?", (runs_cutoff,))
    conn.commit()


def run_cycle(mode: str = "both", skip_crawl: bool = False, settings=None, provider=None,
              progress=None, cancel_event=None) -> dict:
    settings = settings or default_settings
    report = progress or (lambda stage=None, **kw: None)

    def cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    conn = connect(settings.DB_PATH)
    try:
        dict_data = mentions.load_stock_dict()
        run_ids = []
        if not skip_crawl:
            provider = provider or get_provider(settings)
            modes = {"both": ["discovery", "tracked"], "discovery": ["discovery"], "tracked": ["tracked"]}[mode]
            for m in modes:
                if cancelled():
                    break
                rid = run_fetch(conn, m, dict_data, settings, provider, progress=report)
                if rid is not None:
                    run_ids.append(rid)
        last_run_id = run_ids[-1] if run_ids else None

        # cancellation keeps whatever the aborted crawl already banked, but
        # skips the expensive derived stages — the next cycle recomputes them
        if cancelled():
            log.info("cycle cancelled after crawl stage (runs: %s)", run_ids)
            return {"run_ids": run_ids, "analysis": {}, "cycle": None,
                    "slang_scan": None, "cancelled": True}

        report(stage="dedup")
        dedup.recompute_dedup(conn, settings.fresh_window_ms)
        report(stage="mentions")
        mentions.extract_mentions(conn, dict_data, _tracked_rows(conn), settings.fresh_window_ms, last_run_id)
        stats = scoring.compute_stats(conn, settings.fresh_window_ms)
        if not skip_crawl:
            scoring.snapshot_scores(conn, stats, last_run_id)
        tracked = {r["ticker"] for r in _tracked_rows(conn)}
        report(stage="analyze", done=0)
        analysis = analyze.analyze_all(
            conn, settings, dict_data, stats, tracked,
            settings.MIN_MENTIONS_FOR_ANALYSIS, settings.MAX_ANALYZED_STOCKS, last_run_id,
            progress=report, cancel_event=cancel_event,
        )
        if cancelled():
            return {"run_ids": run_ids, "analysis": analysis, "cycle": None,
                    "slang_scan": None, "cancelled": True}
        if settings.ENABLE_PRICE_QUOTES:
            report(stage="prices")
            ranked = sorted(stats, key=lambda t: -stats[t]["score"])[: settings.MAX_ANALYZED_STOCKS]
            prices.refresh_quotes(conn, ranked + sorted(t for t in tracked if t not in ranked))
        report(stage="cleanup")
        cleanup(conn, settings)

        cycle = int(meta_get(conn, "cycle_count", "0") or 0) + 1
        meta_set(conn, "cycle_count", str(cycle))
        conn.commit()
        scan_result = None
        if settings.SLANG_SCAN_EVERY_N_CYCLES > 0 and cycle % settings.SLANG_SCAN_EVERY_N_CYCLES == 0:
            report(stage="slang_scan")
            scan_result = slang_scan.run_slang_scan(conn, settings, dict_data)

        return {"run_ids": run_ids, "analysis": analysis, "cycle": cycle,
                "slang_scan": scan_result, "cancelled": False}
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["both", "discovery", "tracked"], default="both")
    parser.add_argument("--skip-crawl", action="store_true", help="re-run analysis on existing data")
    args = parser.parse_args()
    result = run_cycle(args.mode, args.skip_crawl)
    print(json.dumps(result, ensure_ascii=False, indent=2))
