from infinance.config import Settings
from infinance.db import meta_get
from infinance.keywords import advance_rotation, select_keywords, yield_stats

POOL = "美股医药,美股银行,能源股,黄金股,电动车,巴菲特"


def _settings(**kw):
    return Settings(
        **{"DISCOVERY_CORE": "美股,美股财报", "DISCOVERY_POOL": POOL,
           "DISCOVERY_INVESTMENT_POOL": "", "KEYWORDS_PER_CYCLE": 4, **kw}
    )


def test_rotation_covers_the_pool_across_cycles(conn):
    s = _settings()
    seen = []
    for _ in range(3):
        keywords, rotation = select_keywords(conn, s)
        # pool first: the CAPTCHA wall lands mid-cycle, and core gets 5 shots a day
        # while a pool keyword gets one per wrap
        assert keywords[-2:] == ["美股", "美股财报"]
        assert len(keywords) == 4
        advance_rotation(conn, rotation, sampled=set(keywords))
        seen += keywords[:2]

    assert seen == ["美股医药", "美股银行", "能源股", "黄金股", "电动车", "巴菲特"]
    # wraps around rather than running off the end
    keywords, _ = select_keywords(conn, s)
    assert keywords[:2] == ["美股医药", "美股银行"]


def test_failed_crawl_does_not_consume_a_rotation_slot(conn):
    s = _settings()
    first, rotation = select_keywords(conn, s)
    advance_rotation(conn, rotation, sampled=set())  # crawl brought nothing back
    again, _ = select_keywords(conn, s)
    assert again == first
    assert meta_get(conn, "keyword_cursor", "0") in (None, "0")


def test_wall_advances_only_past_the_sampled_prefix(conn):
    """Run 21: the wall ate the cycle's tail, but the old cursor moved past those
    keywords anyway — they waited a full wrap without ever having run."""
    s = _settings()
    keywords, rotation = select_keywords(conn, s)
    assert keywords[:2] == ["美股医药", "美股银行"]
    advance_rotation(conn, rotation, sampled={"美股医药"})  # wall hit during 美股银行
    again, _ = select_keywords(conn, s)
    assert again[:2] == ["美股银行", "能源股"]  # the eaten keyword leads the next cycle


def test_a_gap_in_the_sampled_prefix_stops_the_advance(conn):
    """The cursor is positional: skipping over an unsampled pick would silently drop it."""
    s = _settings()
    first, rotation = select_keywords(conn, s)
    advance_rotation(conn, rotation, sampled={"美股银行"})  # first pick got nothing
    again, _ = select_keywords(conn, s)
    assert again == first


def test_empty_pool_falls_back_to_the_static_list(conn):
    s = Settings(DISCOVERY_POOL="", DISCOVERY_INVESTMENT_POOL="",
                 DISCOVERY_KEYWORDS="美股,纳指", KEYWORDS_PER_CYCLE=10)
    keywords, rotation = select_keywords(conn, s)
    assert keywords == ["美股", "纳指"]
    assert rotation is None


def test_core_alone_when_it_fills_the_cycle(conn):
    s = _settings(KEYWORDS_PER_CYCLE=2)
    keywords, rotation = select_keywords(conn, s)
    assert keywords == ["美股", "美股财报"]
    assert rotation is None


def test_yield_stats_flags_a_keyword_that_names_no_stock(conn):
    conn.executemany(
        "INSERT INTO notes(note_id, title, publish_time_ms, source_keyword, last_seen_run_id)"
        " VALUES(?,?,1,?,7)",
        [("n1", "英伟达", "美股"), ("n2", "定投", "纳指"), ("n3", "定投2", "纳指")],
    )
    conn.execute(
        "INSERT INTO stock_mentions(ticker, source_type, source_id, note_id, matched_alias,"
        " match_basis, content_time_ms) VALUES('NVDA','note','n1','n1','英伟达','safe_alias',1)"
    )
    conn.commit()

    stats = {r["keyword"]: r for r in yield_stats(conn, run_id=7)}
    assert stats["美股"]["hit_rate"] == 1.0
    assert stats["美股"]["tickers"] == ["NVDA"]
    assert stats["纳指"]["notes"] == 2
    assert stats["纳指"]["with_stock"] == 0
    assert stats["纳指"]["hit_rate"] == 0.0
