"""Migration chain tests against a frozen v0 database.

V0_SCHEMA below is the schema exactly as db.py created it before versioned
migrations existed (user_version 0, no migration bookkeeping). It is frozen on
purpose: real installs in the field look like this, and every future migration
must carry one of these databases to LATEST_VERSION without losing data.
"""

import sqlite3

from app.db import connect
from app.migrations import LATEST_VERSION, MIGRATIONS, get_version

V0_SCHEMA = """
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

CREATE TABLE IF NOT EXISTS price_history(
  ticker TEXT NOT NULL,
  date TEXT NOT NULL,
  close REAL NOT NULL,
  PRIMARY KEY(ticker, date)
);

CREATE TABLE IF NOT EXISTS quotes(
  ticker TEXT PRIMARY KEY,
  price REAL,
  prev_close REAL,
  change_pct REAL,
  market_date TEXT,
  quoted_at_ms INTEGER NOT NULL
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

EXPECTED_TABLES = {
    "fetch_runs", "notes", "comments", "stock_mentions", "stock_analyses",
    "tracked_stocks", "alias_suggestions", "price_history", "quotes",
    "score_snapshots", "meta",
}


def make_v0_db(path):
    """A realistic pre-versioning install: v0 schema plus data in every hot table."""
    conn = sqlite3.connect(path)
    conn.executescript(V0_SCHEMA)
    conn.execute(
        "INSERT INTO fetch_runs(mode, keywords, status, started_at_ms, finished_at_ms,"
        " notes_fetched, notes_fresh, comments_fresh) VALUES('discovery','美股','success',1000,2000,12,8,40)"
    )
    conn.execute(
        "INSERT INTO notes(note_id, title, publish_time_ms, liked_count) VALUES('n1','NVDA新高',1500,300)"
    )
    conn.execute(
        "INSERT INTO comments(comment_id, note_id, content, create_time_ms) VALUES('c1','n1','冲',1600)"
    )
    conn.execute(
        "INSERT INTO stock_mentions(ticker, source_type, source_id, note_id, match_basis, content_time_ms)"
        " VALUES('NVDA','note','n1','n1','ticker_symbol',1500)"
    )
    conn.execute(
        "INSERT INTO stock_analyses(ticker, generated_at_ms, status, summary)"
        " VALUES('NVDA',1700,'ok','NVDA: bullish crowd')"
    )
    conn.execute(
        "INSERT INTO score_snapshots(ticker, snapped_at_ms, score, mentions) VALUES('NVDA',1700,9.5,3)"
    )
    conn.execute("INSERT INTO meta(key,value) VALUES('cycle_count','7')")
    conn.commit()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    conn.close()


def test_fresh_db_lands_on_latest_version(tmp_path):
    conn = connect(tmp_path / "fresh.db")
    assert get_version(conn) == LATEST_VERSION
    tables = {
        r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert EXPECTED_TABLES <= tables
    conn.close()
    # nothing existed to back up
    assert not list(tmp_path.glob("*.bak"))


def test_v0_db_migrates_in_place_with_backup_and_data_intact(tmp_path):
    db = tmp_path / "legacy.db"
    make_v0_db(db)

    conn = connect(db)
    assert get_version(conn) == LATEST_VERSION

    # data survived
    run = conn.execute("SELECT * FROM fetch_runs").fetchone()
    assert run["status"] == "success" and run["notes_fresh"] == 8
    assert conn.execute("SELECT title FROM notes WHERE note_id='n1'").fetchone()[0] == "NVDA新高"
    assert conn.execute("SELECT value FROM meta WHERE key='cycle_count'").fetchone()[0] == "7"
    assert conn.execute("SELECT summary FROM stock_analyses").fetchone()[0] == "NVDA: bullish crowd"
    conn.close()

    # pre-migration backup was written and is itself a valid v0 snapshot
    backup = tmp_path / "legacy.db.v0.bak"
    assert backup.exists()
    bconn = sqlite3.connect(backup)
    assert bconn.execute("PRAGMA user_version").fetchone()[0] == 0
    assert bconn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1
    bconn.close()


def test_reconnect_is_idempotent_and_makes_no_second_backup(tmp_path):
    db = tmp_path / "legacy.db"
    make_v0_db(db)
    connect(db).close()
    backups_after_first = sorted(tmp_path.glob("*.bak"))
    conn = connect(db)
    assert get_version(conn) == LATEST_VERSION
    conn.close()
    assert sorted(tmp_path.glob("*.bak")) == backups_after_first


def test_migration_versions_strictly_increasing_from_one():
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions == list(range(1, len(versions) + 1))
    assert LATEST_VERSION == versions[-1]


def test_current_db_connect_is_cheap_no_write(tmp_path):
    """A current database must not be written on connect (WAL should stay clean)."""
    db = tmp_path / "cheap.db"
    connect(db).close()
    wal = db.with_name(db.name + "-wal")
    size_before = wal.stat().st_size if wal.exists() else 0
    conn = connect(db)
    conn.execute("SELECT 1").fetchone()
    conn.close()
    size_after = wal.stat().st_size if wal.exists() else 0
    assert size_after <= max(size_before, 0)
