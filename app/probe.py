"""Measure what a search keyword actually returns before it joins the rotation pool.

A keyword is only worth its share of the crawl budget if the notes it returns name US
stocks. Index terms like 纳指 mostly return 定投/ETF posts that name none, and Chinese
sector terms (医药股, 银行股) mostly return A-share content — both look reasonable in a
config file and quietly waste 20% of a cycle. This crawls a few notes per candidate,
skipping comments, and reports the hit rate.
"""

import argparse
import json
import logging
from pathlib import Path

from . import crawler_runner, dedup, ingest, keywords, mentions
from .config import settings as default_settings
from .db import connect
from .util import now_ms

log = logging.getLogger(__name__)

PROBE_TIMEOUT_MIN = 45


def probe_keywords(keywords: list[str], notes_per_keyword: int = 5, settings=None) -> dict:
    settings = settings or default_settings
    probe_settings = settings.model_copy(
        update={
            "MAX_NOTES_PER_KEYWORD": notes_per_keyword,
            "CRAWL_TIMEOUT_MIN": max(settings.CRAWL_TIMEOUT_MIN, PROBE_TIMEOUT_MIN),
        }
    )
    conn = connect(settings.DB_PATH)
    try:
        cur = conn.execute(
            "INSERT INTO fetch_runs(mode, keywords, status, started_at_ms) VALUES('discovery',?,'running',?)",
            (",".join(keywords), now_ms()),
        )
        run_id = cur.lastrowid
        conn.commit()
        run_dir = Path(settings.RAW_DIR) / f"run_{run_id:06d}"

        result = crawler_runner.run_crawl(keywords, run_dir, probe_settings, get_comments=False)
        stats = ingest.ingest_run_dir(conn, run_dir, run_id, settings.fresh_window_ms)

        status = "success"
        error = None
        if crawler_runner.login_looks_required(result["log_path"], stats["notes_fresh"]):
            status, error = "failed", "login_required"
        elif result["timed_out"]:
            status, error = "partial", f"timeout after {probe_settings.CRAWL_TIMEOUT_MIN} min"
        elif result["exit_code"] != 0:
            status = "partial" if stats["notes_fresh"] > 0 else "failed"
            error = f"crawler exit code {result['exit_code']}"

        dedup.recompute_dedup(conn, settings.fresh_window_ms)
        mentions.extract_mentions(
            conn, mentions.load_stock_dict(), [], settings.fresh_window_ms, run_id
        )
        conn.execute(
            "UPDATE fetch_runs SET status=?, finished_at_ms=?, notes_fetched=?, notes_fresh=?,"
            " comments_fresh=?, raw_dir=?, error=? WHERE id=?",
            (status, now_ms(), stats["notes_fetched"], stats["notes_fresh"],
             stats["comments_fresh"], str(run_dir), error, run_id),
        )
        conn.commit()
        return {
            "run_id": run_id,
            "status": status,
            "error": error,
            "yield": keywords.yield_stats(conn, run_id=run_id),
        }
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keywords", required=True, help="comma-separated candidates")
    parser.add_argument("--notes", type=int, default=5, help="notes per keyword")
    args = parser.parse_args()
    result = probe_keywords([k.strip() for k in args.keywords.split(",") if k.strip()], args.notes)
    print(json.dumps(result, ensure_ascii=False, indent=2))
