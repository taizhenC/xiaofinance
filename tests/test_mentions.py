from app.mentions import Matcher, build_tracked_keywords, extract_mentions, load_stock_dict
from app.util import now_ms

H = 3_600_000


def matcher():
    return Matcher(load_stock_dict())


def test_fruit_apple_not_matched():
    assert "AAPL" not in matcher().extract("苹果好吃，今天买了三斤")


def test_ambiguous_alias_with_context():
    found = matcher().extract("苹果股价创新高，要不要加仓？")
    assert found["AAPL"][1] == "alias+context"


def test_safe_alias():
    found = matcher().extract("英伟达yyds")
    assert found["NVDA"][1] == "safe_alias"


def test_collision_ticker_without_context():
    assert matcher().extract("买了点LI") == {}


def test_collision_ticker_with_context():
    found = matcher().extract("LI 财报超预期，股价大涨")
    assert found["LI"][1] == "ticker_symbol"


def test_plain_ticker_symbol():
    found = matcher().extract("TSLA要起飞了")
    assert found["TSLA"][1] == "ticker_symbol"


def test_latin_alias_word_boundary():
    assert "ARM" not in matcher().extract("the farmer went to the market")


def test_targeted_search_bypasses_context_gate():
    found = matcher().extract("苹果新品发布会见闻", targeted_ticker="AAPL")
    assert found["AAPL"][1] == "targeted_search"


def test_tracked_keywords_qualified():
    d = load_stock_dict()
    rows = [{"ticker": "COST", "custom_keywords": '["开市客"]'}]
    queries, mapping = build_tracked_keywords(d, rows)
    assert "COST" in queries
    assert "开市客 美股" in queries
    assert mapping["开市客 美股"] == "COST"


def test_extract_mentions_integration(conn):
    now = now_ms()
    conn.execute(
        "INSERT INTO notes(note_id, title, note_desc, publish_time_ms, liked_count, source_keyword)"
        " VALUES('n1','英伟达财报炸裂','盘后大涨',?,100,'美股')",
        (now - H,),
    )
    conn.execute(
        "INSERT INTO comments(comment_id, note_id, content, create_time_ms, like_count)"
        " VALUES('c1','n1','特斯拉也不错',?,5)",
        (now - H,),
    )
    conn.commit()
    count = extract_mentions(conn, load_stock_dict(), [], fresh_window_ms=24 * H, now=now)
    assert count >= 2
    rows = conn.execute(
        "SELECT ticker, COUNT(*) c FROM stock_mentions GROUP BY ticker"
    ).fetchall()
    by_ticker = {r["ticker"]: r["c"] for r in rows}
    assert by_ticker["NVDA"] == 1
    assert by_ticker["TSLA"] == 1
