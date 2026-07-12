import sqlite3

from app.db import connect


def test_an_old_database_gains_the_detail_column_on_connect(tmp_path):
    """CREATE TABLE IF NOT EXISTS never evolves an existing table, so a DB created before
    the column existed must be altered on connect."""
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.execute(
        "CREATE TABLE fetch_runs(id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " mode TEXT NOT NULL CHECK(mode IN ('discovery','tracked')),"
        " keywords TEXT, status TEXT NOT NULL, started_at_ms INTEGER NOT NULL,"
        " finished_at_ms INTEGER, notes_fetched INTEGER NOT NULL DEFAULT 0,"
        " notes_fresh INTEGER NOT NULL DEFAULT 0, comments_fresh INTEGER NOT NULL DEFAULT 0,"
        " raw_dir TEXT, error TEXT)"
    )
    raw.commit()
    raw.close()

    conn = connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(fetch_runs)")}
    assert "detail" in cols
    conn.execute(
        "INSERT INTO fetch_runs(mode, status, started_at_ms, detail)"
        " VALUES('discovery','running',1,'{}')"
    )
    conn.close()
