import argparse
import json
import logging
import shutil
import time
from pathlib import Path

from . import analyze, crawler_runner, dedup, ingest, mentions, prices, scoring, slang_scan
from . import keywords as keywords_mod
from .config import settings as default_settings
from .db import connect, meta_get, meta_set
from .util import now_ms

log = logging.getLogger(__name__)

RETAIN_CONTENT_DAYS = 7
RETAIN_RUNS_DAYS = 30


def _tracked_rows(conn):
    return conn.execute("SELECT ticker, custom_keywords FROM tracked_stocks ORDER BY ticker").fetchall()


def run_fetch(conn, mode: str, dict_data: dict, settings) -> int | None:
    rotation = None
    if mode == "discovery":
        keywords, rotation = keywords_mod.select_keywords(conn, settings)
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
    run_dir = Path(settings.RAW_DIR) / f"run_{run_id:06d}"
    # Recorded up front, not at the end: it is what lets the dashboard read a crawl's
    # progress out of the run directory while it is still running.
    conn.execute("UPDATE fetch_runs SET raw_dir=? WHERE id=?", (str(run_dir), run_id))
    conn.commit()

    status, error = "failed", None
    stats = {"notes_fetched": 0, "notes_fresh": 0, "comments_fresh": 0, "malformed": 0}
    # One crawler process per keyword, with a pause in between. The wall triggers on
    # session volume (runs 16-21 died at ~100-130 continuous requests), which a
    # multi-keyword marathon crosses at keyword 2-3 every time — so the tail keywords
    # never ran. Spaced single-keyword bursts stay under it, and a wall now costs the
    # rest of the list instead of poisoning what was already fetched.
    sampled: set[str] = set()
    failures: list[str] = []
    walled = login_needed = False
    captchas = 0
    try:
        log_path = run_dir / "crawler.log"
        for i, kw in enumerate(keywords):
            if i and settings.KEYWORD_GAP_MIN > 0:
                crawler_runner.append_log_line(
                    log_path, f"pausing {settings.KEYWORD_GAP_MIN:g} min before {kw}"
                )
                time.sleep(settings.KEYWORD_GAP_MIN * 60)
            result = crawler_runner.run_crawl([kw], run_dir, settings)
            captchas += result["captchas"]
            kw_notes = crawler_runner.keyword_counts(run_dir)[0].get(kw, 0)
            if crawler_runner.login_looks_required(
                result["log_path"], kw_notes, start=result["log_start"]
            ):
                login_needed = True
                break
            # Sampled means "don't spend budget here again next cycle": its notes are in,
            # even if the wall then ate the comments.
            if result["exit_code"] == 0 or kw_notes > 0:
                sampled.add(kw)
            # risk_controlled is the watcher's kill at CAPTCHA_ABORT_COUNT; the second
            # arm catches the crawler dying on its own after fewer 461s — either way the
            # wall is up, and the keywords behind it are better spent after the cooldown.
            if result["risk_controlled"] or (result["exit_code"] != 0 and result["captchas"] > 0):
                walled = True
                break
            if result["timed_out"]:
                failures.append(f"{kw}: timeout after {settings.CRAWL_TIMEOUT_MIN} min")
            elif result["exit_code"] != 0:
                failures.append(f"{kw}: " + crawler_runner.failure_reason(
                    result["log_path"], result["exit_code"], start=result["log_start"]
                ))
        stats = ingest.ingest_run_dir(conn, run_dir, run_id, settings.context_window_ms)
        if login_needed:
            status, error = "failed", "login_required"
        elif walled:
            status = "partial" if stats["notes_fresh"] > 0 else "failed"
            error = (
                f"stopped after {captchas} CAPTCHAs — XHS is rate-limiting the "
                "account; what was fetched before the wall is kept"
            )
        elif failures:
            status = "partial" if stats["notes_fresh"] > 0 else "failed"
            error = "; ".join(failures)[:500]
        elif stats["malformed"] > 0:
            status, error = "partial", f"{stats['malformed']} malformed lines skipped"
        else:
            status = "success"
    except Exception as e:
        error = str(e)[:500]
        log.exception("fetch run %d failed", run_id)

    # Snapshotted now because the raw dir it is computed from outlives the run by only
    # RETAIN_CONTENT_DAYS, while the run row lives RETAIN_RUNS_DAYS.
    detail = None
    try:
        detail = json.dumps(
            crawler_runner.crawl_detail(run_dir, keywords, status), ensure_ascii=False
        )
    except Exception:
        log.exception("run %d: could not build the run detail", run_id)

    conn.execute(
        """UPDATE fetch_runs SET status=?, finished_at_ms=?, notes_fetched=?, notes_fresh=?,
           comments_fresh=?, raw_dir=?, error=?, detail=? WHERE id=?""",
        (status, now_ms(), stats["notes_fetched"], stats["notes_fresh"],
         stats["comments_fresh"], str(run_dir), error, detail, run_id),
    )
    # Advances only past the pool picks that were actually sampled: a keyword the wall
    # (or a login failure) ate leads the next cycle instead of waiting out a full wrap.
    keywords_mod.advance_rotation(conn, rotation, sampled)
    conn.commit()
    log.info("run %d (%s): %s %s", run_id, mode, status, error or "")
    return run_id


def risk_cooldown_until(conn, settings) -> int | None:
    """When the newest finished run hit the CAPTCHA wall, scheduled cycles pause until the
    cooldown lapses — retrying against the wall burns another dozen walled requests per
    attempt and keeps the account flagged. Any later run, clean or not, supersedes it."""
    if settings.RISK_COOLDOWN_HOURS <= 0:
        return None
    row = conn.execute(
        "SELECT finished_at_ms, error FROM fetch_runs WHERE finished_at_ms IS NOT NULL "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row or "CAPTCHA" not in (row["error"] or ""):
        return None
    return row["finished_at_ms"] + int(settings.RISK_COOLDOWN_HOURS * 3_600_000)


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


def run_cycle(mode: str = "both", skip_crawl: bool = False, settings=None,
              force_analysis: bool = False) -> dict:
    settings = settings or default_settings
    conn = connect(settings.DB_PATH)
    try:
        dict_data = mentions.load_stock_dict()
        run_ids = []
        if not skip_crawl:
            modes = {"both": ["discovery", "tracked"], "discovery": ["discovery"], "tracked": ["tracked"]}[mode]
            for m in modes:
                if run_ids:
                    # the previous run in this cycle just failed for account reasons —
                    # a second run right now only burns requests into the same wall
                    prev = conn.execute(
                        "SELECT error FROM fetch_runs WHERE id=?", (run_ids[-1],)
                    ).fetchone()
                    prev_error = (prev["error"] if prev else None) or ""
                    if "CAPTCHA" in prev_error or prev_error == "login_required":
                        log.warning("skipping %s run: previous run ended with %r", m, prev_error)
                        break
                    if settings.KEYWORD_GAP_MIN > 0:
                        time.sleep(settings.KEYWORD_GAP_MIN * 60)
                rid = run_fetch(conn, m, dict_data, settings)
                if rid is not None:
                    run_ids.append(rid)
        last_run_id = run_ids[-1] if run_ids else None

        dedup.recompute_dedup(conn, settings.context_window_ms)
        mentions.extract_mentions(conn, dict_data, _tracked_rows(conn), settings.context_window_ms, last_run_id)
        stats = scoring.compute_stats(conn, settings.fresh_window_ms,
                                      indexes=mentions.non_stock_tickers(dict_data))
        if not skip_crawl:
            scoring.snapshot_scores(conn, stats, last_run_id)
        tracked = {r["ticker"] for r in _tracked_rows(conn)}
        analysis = analyze.analyze_all(
            conn, settings, dict_data, stats, tracked,
            settings.MIN_MENTIONS_FOR_ANALYSIS, settings.MAX_ANALYZED_STOCKS, last_run_id,
            force=force_analysis,
        )
        if settings.ENABLE_PRICE_QUOTES:
            ranked = sorted(stats, key=lambda t: -stats[t]["score"])[: settings.MAX_ANALYZED_STOCKS]
            entries = {s["ticker"]: s for s in dict_data.get("stocks", [])}
            classes = mentions.asset_classes(dict_data)
            requested = ranked + sorted(t for t in tracked if t not in ranked)
            quoteable = [
                t for t in requested
                if classes.get(t) in ("stock", "index") or entries.get(t, {}).get("quote_symbol")
            ]
            symbol_overrides = {
                t: entries[t]["quote_symbol"] for t in quoteable
                if entries.get(t, {}).get("quote_symbol")
            }
            prices.refresh_quotes(conn, quoteable, symbol_overrides=symbol_overrides)
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
    parser.add_argument(
        "--force", action="store_true",
        help="re-analyse even when the inputs are unchanged — the cache is keyed on which "
             "items came in, so a change to how they are ranked, quoted or prompted is "
             "otherwise invisible until new data arrives",
    )
    args = parser.parse_args()
    result = run_cycle(args.mode, args.skip_crawl, force_analysis=args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2))
