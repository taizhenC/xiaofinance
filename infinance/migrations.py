"""Versioned schema migrations keyed on PRAGMA user_version.

Every connect() calls ensure_schema(), which is a single PRAGMA read when the
database is current. When it is behind, the database file is backed up next to
itself and the pending migrations run inside one IMMEDIATE transaction, so a
crash mid-upgrade rolls back to the old version and concurrent connections
serialize instead of double-applying.

Legacy databases (created before this module existed) sit at user_version 0
with all baseline tables already present — migration 1 therefore uses
IF NOT EXISTS throughout and adopts them without touching data. Later
migrations must be written as plain ALTER/CREATE statements (or a callable for
table rebuilds) and appended to MIGRATIONS with the next version number.
"""

import logging
import shutil
import sqlite3
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

_BASELINE = [
    """CREATE TABLE IF NOT EXISTS fetch_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mode TEXT NOT NULL CHECK(mode IN ('discovery','tracked')),
  keywords TEXT,
  status TEXT NOT NULL CHECK(status IN ('running','success','partial','failed')),
  started_at_ms INTEGER NOT NULL,
  finished_at_ms INTEGER,
  notes_fetched INTEGER NOT NULL DEFAULT 0,
  notes_fresh INTEGER NOT NULL DEFAULT 0,
  comments_fresh INTEGER NOT NULL DEFAULT 0,
  raw_dir TEXT,
  error TEXT
)""",
    """CREATE TABLE IF NOT EXISTS notes(
  note_id TEXT PRIMARY KEY,
  title TEXT,
  note_desc TEXT,
  note_type TEXT,
  publish_time_ms INTEGER NOT NULL,
  liked_count INTEGER NOT NULL DEFAULT 0,
  collected_count INTEGER NOT NULL DEFAULT 0,
  comment_count INTEGER NOT NULL DEFAULT 0,
  share_count INTEGER NOT NULL DEFAULT 0,
  note_url TEXT,
  tag_list TEXT,
  source_keyword TEXT,
  nickname TEXT,
  simhash INTEGER,
  dup_group_id TEXT,
  first_seen_run_id INTEGER,
  last_seen_run_id INTEGER,
  fetched_at_ms INTEGER
)""",
    "CREATE INDEX IF NOT EXISTS idx_notes_publish ON notes(publish_time_ms)",
    """CREATE TABLE IF NOT EXISTS comments(
  comment_id TEXT PRIMARY KEY,
  note_id TEXT NOT NULL,
  parent_comment_id TEXT,
  content TEXT,
  create_time_ms INTEGER NOT NULL,
  like_count INTEGER NOT NULL DEFAULT 0,
  sub_comment_count INTEGER NOT NULL DEFAULT 0,
  nickname TEXT,
  content_norm_hash TEXT,
  dup_group_id TEXT,
  first_seen_run_id INTEGER,
  fetched_at_ms INTEGER
)""",
    "CREATE INDEX IF NOT EXISTS idx_comments_note ON comments(note_id)",
    "CREATE INDEX IF NOT EXISTS idx_comments_time ON comments(create_time_ms)",
    """CREATE TABLE IF NOT EXISTS stock_mentions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker TEXT NOT NULL,
  source_type TEXT NOT NULL CHECK(source_type IN ('note','comment')),
  source_id TEXT NOT NULL,
  note_id TEXT,
  matched_alias TEXT,
  match_basis TEXT NOT NULL CHECK(match_basis IN ('safe_alias','alias+context','ticker_symbol','targeted_search')),
  content_time_ms INTEGER NOT NULL,
  run_id INTEGER,
  UNIQUE(ticker, source_type, source_id)
)""",
    "CREATE INDEX IF NOT EXISTS idx_mentions_ticker_time ON stock_mentions(ticker, content_time_ms)",
    """CREATE TABLE IF NOT EXISTS stock_analyses(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker TEXT NOT NULL,
  run_id INTEGER,
  generated_at_ms INTEGER NOT NULL,
  window_start_ms INTEGER,
  window_end_ms INTEGER,
  note_count INTEGER,
  comment_count INTEGER,
  popularity_score REAL,
  sentiment_counts TEXT,
  summary TEXT,
  bull_points TEXT,
  bear_points TEXT,
  notable_quotes TEXT,
  irrelevant_item_count INTEGER,
  input_item_count INTEGER,
  input_hash TEXT,
  model TEXT,
  input_tokens INTEGER,
  output_tokens INTEGER,
  cost_usd REAL,
  status TEXT NOT NULL CHECK(status IN ('ok','no_api_key','error','skipped_unchanged')),
  error TEXT
)""",
    "CREATE INDEX IF NOT EXISTS idx_analyses_ticker ON stock_analyses(ticker, generated_at_ms)",
    """CREATE TABLE IF NOT EXISTS tracked_stocks(
  ticker TEXT PRIMARY KEY,
  added_at_ms INTEGER NOT NULL,
  custom_keywords TEXT
)""",
    """CREATE TABLE IF NOT EXISTS alias_suggestions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  term TEXT NOT NULL,
  guessed_ticker TEXT NOT NULL,
  evidence_quote TEXT,
  evidence_note_id TEXT,
  suggested_at_ms INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','rejected')),
  UNIQUE(term, guessed_ticker)
)""",
    """CREATE TABLE IF NOT EXISTS price_history(
  ticker TEXT NOT NULL,
  date TEXT NOT NULL,
  close REAL NOT NULL,
  PRIMARY KEY(ticker, date)
)""",
    """CREATE TABLE IF NOT EXISTS quotes(
  ticker TEXT PRIMARY KEY,
  price REAL,
  prev_close REAL,
  change_pct REAL,
  market_date TEXT,
  quoted_at_ms INTEGER NOT NULL
)""",
    """CREATE TABLE IF NOT EXISTS score_snapshots(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker TEXT NOT NULL,
  run_id INTEGER,
  snapped_at_ms INTEGER NOT NULL,
  score REAL NOT NULL,
  mentions INTEGER NOT NULL,
  note_count INTEGER NOT NULL DEFAULT 0,
  comment_count INTEGER NOT NULL DEFAULT 0,
  UNIQUE(ticker, snapped_at_ms)
)""",
    "CREATE INDEX IF NOT EXISTS idx_snapshots_time ON score_snapshots(snapped_at_ms)",
    """CREATE TABLE IF NOT EXISTS meta(
  key TEXT PRIMARY KEY,
  value TEXT
)""",
]

Migration = list[str] | Callable[[sqlite3.Connection], None]

# (version, name, statements-or-callable) — versions strictly increasing from 1.
MIGRATIONS: list[tuple[int, str, Migration]] = [
    (1, "baseline schema", _BASELINE),
]

LATEST_VERSION = MIGRATIONS[-1][0]


def get_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _backup(conn: sqlite3.Connection, db_path: Path, from_version: int) -> Path | None:
    """File-copy backup before upgrading; None for empty/fresh databases.
    The WAL is checkpointed first so the copied main file is complete alone."""
    if not db_path.exists():
        return None
    has_tables = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
    ).fetchone()[0]
    if not has_tables:
        return None
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    backup_path = db_path.with_name(f"{db_path.name}.v{from_version}.bak")
    shutil.copy2(db_path, backup_path)
    log.info("database backed up to %s before migrating from v%d", backup_path, from_version)
    return backup_path


def ensure_schema(conn: sqlite3.Connection, db_path: Path | str) -> None:
    """Bring the database to LATEST_VERSION. Fast no-op when already current."""
    if get_version(conn) >= LATEST_VERSION:
        return
    _backup(conn, Path(db_path), get_version(conn))
    # IMMEDIATE takes the write lock up front: concurrent connections queue on
    # the busy timeout and re-check the version instead of double-applying.
    conn.execute("BEGIN IMMEDIATE")
    try:
        version = get_version(conn)
        for target, name, migration in MIGRATIONS:
            if target <= version:
                continue
            if callable(migration):
                migration(conn)
            else:
                for stmt in migration:
                    conn.execute(stmt)
            conn.execute(f"PRAGMA user_version = {target}")
            log.info("migrated database to v%d (%s)", target, name)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
