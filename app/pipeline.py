import argparse
import json
import logging
import shutil
from pathlib import Path

from . import analyze, crawler_runner, dedup, ingest, mentions, prices, scoring, slang_scan
from .config import settings as default_settings
from .db import connect, meta_get, meta_set
from .util import now_ms

log = logging.getLogger(__name__)

RETAIN_CONTENT_DAYS = 7
RETAIN_RUNS_DAYS = 30


def _tracked_rows(conn):
    return conn.execute("SELECT ticker, custom_keywords FROM tracked_stocks ORDER BY ticker").fetchall()


def run_fetch(conn, mode: str, dict_data: dict, settings) -> int | None:
    if mode == "discovery":
        keywords = settings.discovery_keywords_list
    else:
        keywords, _ = mentions.build_tracked_keywords(dict_data, _tracked_rows(conn))
        if not keywords:
            log.info("no tracked tickers, skipping tracked run")
            return None

    started = now_ms()
    cur = conn.execute(
        "INSERT INTO fetch_runs(mode, keywords, status, started_at_ms) VALUES(?,?,'running',?)",
        (mode, ",".join(keywords), started),
    )
    run_id = cur.lastrowid
    conn.commit()
    run_dir = Path(settings.RAW_DIR) / f"run_{run_id:06d}"

    status, error = "failed", None
    stats = {"notes_fetched": 0, "notes_fresh": 0, "comments_fresh": 0, "malformed": 0}
    try:
        result = crawler_runner.run_crawl(keywords, run_dir, settings)
        stats = ingest.ingest_run_dir(conn, run_dir, run_id, settings.fresh_window_ms)
        if crawler_runner.login_looks_required(result["log_path"], stats["notes_fresh"]):
            status, error = "failed", "login_required"
        elif result["timed_out"]:
            status = "partial" if stats["notes_fresh"] > 0 else "failed"
            error = f"timeout after {settings.CRAWL_TIMEOUT_MIN} min"
        elif result["exit_code"] != 0:
            status = "partial" if stats["notes_fresh"] > 0 else "failed"
            error = f"crawler exit code {result['exit_code']}"
        elif stats["malformed"] > 0:
            status, error = "partial", f"{stats['malformed']} malformed lines skipped"
        else:
            status = "success"
    except Exception as e:
        error = str(e)[:500]
        log.exception("fetch run %d failed", run_id)

    conn.execute(
        """UPDATE fetch_runs SET status=?, finished_at_ms=?, notes_fetched=?, notes_fresh=?,
           comments_fresh=?, raw_dir=?, error=? WHERE id=?""",
        (status, now_ms(), stats["notes_fetched"], stats["notes_fresh"],
         stats["comments_fresh"], str(run_dir), error, run_id),
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


def run_cycle(mode: str = "both", skip_crawl: bool = False, settings=None) -> dict:
    settings = settings or default_settings
    conn = connect(settings.DB_PATH)
    try:
        dict_data = mentions.load_stock_dict()
        run_ids = []
        if not skip_crawl:
            modes = {"both": ["discovery", "tracked"], "discovery": ["discovery"], "tracked": ["tracked"]}[mode]
            for m in modes:
                rid = run_fetch(conn, m, dict_data, settings)
                if rid is not None:
                    run_ids.append(rid)
        last_run_id = run_ids[-1] if run_ids else None

        dedup.recompute_dedup(conn, settings.fresh_window_ms)
        mentions.extract_mentions(conn, dict_data, _tracked_rows(conn), settings.fresh_window_ms, last_run_id)
        stats = scoring.compute_stats(conn, settings.fresh_window_ms)
        if not skip_crawl:
            scoring.snapshot_scores(conn, stats, last_run_id)
        tracked = {r["ticker"] for r in _tracked_rows(conn)}
        analysis = analyze.analyze_all(
            conn, settings, dict_data, stats, tracked,
            settings.MIN_MENTIONS_FOR_ANALYSIS, settings.MAX_ANALYZED_STOCKS, last_run_id,
        )
        if settings.ENABLE_PRICE_QUOTES:
            ranked = sorted(stats, key=lambda t: -stats[t]["score"])[: settings.MAX_ANALYZED_STOCKS]
            prices.refresh_quotes(conn, ranked + sorted(t for t in tracked if t not in ranked))
        cleanup(conn, settings)

        cycle = int(meta_get(conn, "cycle_count", "0") or 0) + 1
        meta_set(conn, "cycle_count", str(cycle))
        conn.commit()
        scan_result = None
        if settings.SLANG_SCAN_EVERY_N_CYCLES > 0 and cycle % settings.SLANG_SCAN_EVERY_N_CYCLES == 0:
            scan_result = slang_scan.run_slang_scan(conn, settings, dict_data)

        return {"run_ids": run_ids, "analysis": analysis, "cycle": cycle, "slang_scan": scan_result}
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
