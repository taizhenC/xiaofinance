from app.prices import (
    get_quotes,
    parse_chart_json,
    quotes_need_refresh,
    store_quote,
    yahoo_symbol,
)

# midnights UTC; gmtoffset -4h (EDT) must pull each onto the previous exchange-local day
CHART = {
    "chart": {
        "result": [
            {
                "meta": {"gmtoffset": -14400},
                "timestamp": [86400, 172800, 259200],
                "indicators": {"quote": [{"close": [100.0, 102.0, 104.55]}]},
            }
        ]
    }
}


def test_parse_chart_json():
    closes = parse_chart_json(CHART)
    assert closes == [("1970-01-01", 100.0), ("1970-01-02", 102.0), ("1970-01-03", 104.55)]
    assert parse_chart_json({"chart": {"result": None}}) == []
    assert parse_chart_json({}) == []


def test_parse_chart_json_skips_null_closes():
    data = {
        "chart": {"result": [{
            "meta": {"gmtoffset": 0},
            "timestamp": [86400, 172800],
            "indicators": {"quote": [{"close": [None, 50.0]}]},
        }]}
    }
    assert parse_chart_json(data) == [("1970-01-03", 50.0)]


def test_yahoo_symbol():
    assert yahoo_symbol("NVDA") == "NVDA"
    assert yahoo_symbol("BRK.B") == "BRK-B"
    assert yahoo_symbol("BRK") == "BRK-B"


def test_store_quote_single_session_ipo(conn):
    assert store_quote(conn, "SKHY", [("2026-07-10", 168.01)], now=5_000)
    q = get_quotes(conn, ["SKHY"])["SKHY"]
    assert q["price"] == 168.01
    assert q["prev_close"] is None
    assert q["change_pct"] is None


def test_store_quote_computes_change(conn):
    closes = [("2026-07-08", 100.0), ("2026-07-09", 102.0), ("2026-07-10", 104.55)]
    assert store_quote(conn, "NVDA", closes, now=5_000)
    q = get_quotes(conn, ["NVDA"])["NVDA"]
    assert q["price"] == 104.55
    assert q["prev_close"] == 102.0
    assert q["change_pct"] == 2.5
    assert q["market_date"] == "2026-07-10"
    assert q["quoted_at_ms"] == 5_000
    rows = conn.execute("SELECT COUNT(*) AS n FROM price_history WHERE ticker='NVDA'").fetchone()
    assert rows["n"] == 3


def test_store_quote_empty_history(conn):
    assert not store_quote(conn, "XYZ", [], now=1)
    assert get_quotes(conn, ["XYZ"]) == {}


def test_quotes_need_refresh(conn):
    closes = [("2026-07-09", 102.0), ("2026-07-10", 104.55)]
    assert not quotes_need_refresh(conn, [], 1_000, now=10_000)
    assert quotes_need_refresh(conn, ["NVDA"], 1_000, now=10_000)  # missing quote
    store_quote(conn, "NVDA", closes, now=9_500)
    assert not quotes_need_refresh(conn, ["NVDA"], 1_000, now=10_000)
    assert quotes_need_refresh(conn, ["NVDA"], 100, now=10_000)  # stale
