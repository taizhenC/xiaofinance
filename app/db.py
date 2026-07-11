import sqlite3
from pathlib import Path

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS fetch_runs(
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
);

CREATE TABLE IF NOT EXISTS notes(
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
);
CREATE INDEX IF NOT EXISTS idx_notes_publish ON notes(publish_time_ms);

CREATE TABLE IF NOT EXISTS comments(
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
);
CREATE INDEX IF NOT EXISTS idx_comments_note ON comments(note_id);
CREATE INDEX IF NOT EXISTS idx_comments_time ON comments(create_time_ms);

CREATE TABLE IF NOT EXISTS stock_mentions(
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
);
CREATE INDEX IF NOT EXISTS idx_mentions_ticker_time ON stock_mentions(ticker, content_time_ms);

CREATE TABLE IF NOT EXISTS stock_analyses(
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
);
CREATE INDEX IF NOT EXISTS idx_analyses_ticker ON stock_analyses(ticker, generated_at_ms);

CREATE TABLE IF NOT EXISTS tracked_stocks(
  ticker TEXT PRIMARY KEY,
  added_at_ms INTEGER NOT NULL,
  custom_keywords TEXT
);

CREATE TABLE IF NOT EXISTS alias_suggestions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  term TEXT NOT NULL,
  guessed_ticker TEXT NOT NULL,
  evidence_quote TEXT,
  evidence_note_id TEXT,
  suggested_at_ms INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','rejected')),
  UNIQUE(term, guessed_ticker)
);

CREATE TABLE IF NOT EXISTS score_snapshots(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker TEXT NOT NULL,
  run_id INTEGER,
  snapped_at_ms INTEGER NOT NULL,
  score REAL NOT NULL,
  mentions INTEGER NOT NULL,
  note_count INTEGER NOT NULL DEFAULT 0,
  comment_count INTEGER NOT NULL DEFAULT 0,
  UNIQUE(ticker, snapped_at_ms)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_time ON score_snapshots(snapped_at_ms);

CREATE TABLE IF NOT EXISTS meta(
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path or settings.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


def meta_get(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
