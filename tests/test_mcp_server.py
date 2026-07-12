import json

import pytest

from app import mcp_server as M
from app.db import connect as db_connect
from app.dedup import recompute_dedup
from app.mentions import extract_mentions, load_stock_dict
from app.util import now_ms, sha256_hex, simhash64, to_signed64

H = 3_600_000
DICT = load_stock_dict()


@pytest.fixture
def corpus(conn, tmp_path, monkeypatch):
    """Point the tools at the fixture's DB *file*, so each call opens and closes its own
    connection exactly as it does in production — handing them the fixture's own handle would
    let the first tool call close it out from under the test."""
    now = now_ms()
    conn.execute(
        "INSERT INTO notes(note_id, title, note_desc, publish_time_ms, liked_count, simhash, source_keyword)"
        " VALUES('n1','海力士还能追吗','SK海力士这波涨太多了，海力士估值到顶没，存储周期怎么看',?,80,?,'美股')",
        (now - 3 * H, to_signed64(simhash64("海力士还能追吗"))),
    )
    conn.execute(
        "INSERT INTO comments(comment_id, note_id, parent_comment_id, content, create_time_ms,"
        " like_count, content_norm_hash) VALUES('c1','n1',NULL,'海力士还能上车吗，怕站岗',?,30,?)",
        (now - 2 * H, sha256_hex("c1")),
    )
    conn.execute(
        "INSERT INTO comments(comment_id, note_id, parent_comment_id, content, create_time_ms,"
        " like_count, content_norm_hash) VALUES('c2','n1','c1','别追，我已经出货了',?,25,?)",
        (now - H, sha256_hex("c2")),
    )
    conn.commit()
    recompute_dedup(conn, 24 * H, now)
    extract_mentions(conn, DICT, [], 24 * H, now=now)
    monkeypatch.setattr(M, "connect", lambda: db_connect(tmp_path / "test.db"))
    return conn


def test_evidence_hands_over_numbered_items_and_a_hash(corpus):
    ev = M.evidence("SKHY")
    assert ev["item_count"] == 3
    assert ev["evidence_hash"]
    assert [i["n"] for i in ev["items"]] == [1, 2, 3]
    # the reply reaches the agent threaded, exactly as it reaches DeepSeek
    assert any(i["text"].startswith("↳") for i in ev["items"])
    assert "notable_quote_ids" in ev["instructions"]


def test_a_rating_lands_in_the_same_row_deepseek_would_have_written(corpus):
    ev = M.evidence("SKHY")
    out = M.submit_rating(
        ticker="SKHY", evidence_hash=ev["evidence_hash"],
        summary="ADR debut dominates; bulls cite the pop, bears the premium.",
        bullish=2, bearish=1, neutral=0,
        bull_points=["first-day pop"], bear_points=["ADR premium"],
        notable_quote_ids=[1], irrelevant_item_count=1,
    )
    assert out["status"] == "ok"

    r = corpus.execute(
        "SELECT * FROM stock_analyses WHERE ticker='SKHY' ORDER BY generated_at_ms DESC LIMIT 1"
    ).fetchone()
    assert r["status"] == "ok"
    assert r["model"] == M.AGENT_MODEL
    assert r["summary"].startswith("SKHY: ")  # the ticker prefix is enforced for us
    assert json.loads(r["sentiment_counts"]) == {"bullish": 2, "bearish": 1, "neutral": 0}
    assert r["irrelevant_item_count"] == 1


def test_quotes_are_looked_up_by_id_not_trusted_from_the_agent(corpus):
    ev = M.evidence("SKHY")
    out = M.submit_rating(
        "SKHY", ev["evidence_hash"], "x", 1, 0, 0, [], [],
        notable_quote_ids=[2, 99, 0],  # 99 and 0 do not exist
    )
    assert len(out["stored_quotes"]) == 1
    assert out["stored_quotes"][0] == ev["items"][1]["text"]


def test_a_rating_built_on_stale_evidence_is_refused(corpus):
    """The window slides. Item numbers are positions in a list, so a rating written against an
    older list would pin its quotes to whatever now sits at those positions."""
    out = M.submit_rating("SKHY", "not-the-real-hash", "x", 1, 0, 0, [], [], [1])
    assert out["status"] == "stale_evidence"
    assert corpus.execute("SELECT COUNT(*) FROM stock_analyses").fetchone()[0] == 0


def test_pending_ratings_does_not_count_a_keyless_fallback_as_rated(corpus):
    from app.analyze import analyze_ticker
    from app.config import settings

    analyze_ticker(corpus, "SKHY", settings)  # no API key -> writes a no_api_key row
    assert corpus.execute(
        "SELECT status FROM stock_analyses WHERE ticker='SKHY'"
    ).fetchone()["status"] == "no_api_key"

    # that row holds quotes and no judgement, so the agent must still be asked to rate it
    assert "SKHY" in [p["ticker"] for p in M.pending_ratings()]

    ev = M.evidence("SKHY")
    M.submit_rating("SKHY", ev["evidence_hash"], "x", 1, 0, 0, [], [], [])
    assert "SKHY" not in [p["ticker"] for p in M.pending_ratings()]  # now it is


def test_search_corpus_reports_the_true_count_not_the_sample(corpus):
    """A term is dangerous precisely when it is common — a count capped by the page size would
    hide that."""
    out = M.search_corpus("海力士", limit=1)
    assert out["note_hits"] == 1
    assert out["showing"]["notes"] == 1
    assert M.search_corpus("完全不存在的词")["note_hits"] == 0
