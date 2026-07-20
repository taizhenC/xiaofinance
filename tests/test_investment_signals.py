from infinance.mentions import (
    Matcher,
    extract_mentions,
    extract_topic_tags,
    investment_tickers,
    load_stock_dict,
    topic_breakdown,
)
from infinance.scoring import compute_stats, index_board, ranking_and_radar
from infinance.util import now_ms

H = 3_600_000
WINDOW = 24 * H
DICT = load_stock_dict()


def test_gold_slang_requires_investment_context():
    matcher = Matcher(DICT)
    assert "GOLD" not in matcher.extract("邻居家的大黄今天跑出去了")
    assert matcher.extract("大黄出方向了，可惜是跌，#理财 #投资")["GOLD"][1] == "alias+context"
    assert matcher.extract("Yellow 出方向了，CPI之后继续看跌")["GOLD"][1] == "alias+context"


def test_screenshot_post_is_gold_signal_with_topics():
    text = (
        "大黄出方向了，航班坠机了。Yellow出方向了，很可惜是跌。"
        "架不住美伊互炸持续升级，明晚看cpi给不给机会。#理财 #投资 #黄金"
    )
    found = Matcher(DICT).extract(text)
    assert "GOLD" in found
    assert {"理财", "投资", "宏观"} <= set(extract_topic_tags(text, DICT))


def test_specific_gold_etf_suppresses_generic_gold_signal():
    found = Matcher(DICT).extract("黄金ETF最近跌了，我减仓GLD")
    assert "GLD" in found
    assert "GOLD" not in found


def test_investments_get_their_own_board(conn):
    now = now_ms()
    conn.execute(
        "INSERT INTO notes(note_id,title,note_desc,publish_time_ms,liked_count,source_keyword) "
        "VALUES('g1','大黄出方向了','Yellow跌了，#理财 #投资 #黄金',?,80,'黄金投资')",
        (now - H,),
    )
    conn.commit()
    extract_mentions(conn, DICT, [], WINDOW, now=now)
    investments = investment_tickers(DICT)
    stats = compute_stats(conn, WINDOW, now, indexes=investments)
    stocks, _ = ranking_and_radar(stats, 1, investments)
    board = index_board(stats, investments, 1)

    assert "GOLD" not in {e["ticker"] for e in stocks}
    assert [e["ticker"] for e in board] == ["GOLD"]
    topics = {e["tag"]: e["mentions"] for e in topic_breakdown(conn, DICT, WINDOW, now)}
    assert topics["理财"] == 1
    assert topics["投资"] == 1
