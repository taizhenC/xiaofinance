from app.mentions import Matcher, load_stock_dict, mask_traps

DICT = load_stock_dict()


def tickers(text: str) -> set[str]:
    return set(Matcher(DICT).extract(text))


def test_mask_keeps_offsets_so_match_positions_still_line_up():
    masked = mask_traps("我是女大学生", ["女大学生"])
    assert len(masked) == len("我是女大学生")
    assert "女大" not in masked


def test_nvda_slang_is_matched():
    """女大 = NVDA, from the syllables of the ticker. Ambiguous, so it needs a stock context."""
    assert "NVDA" in tickers("女大财报要炸了，美股这波稳了")


def test_nvda_slang_does_not_fire_inside_a_female_college_student():
    """The CJK matcher is a substring test, so 女大 lives inside 女大学生 — and a post about a
    student who trades US stocks satisfies the context gate perfectly."""
    assert "NVDA" not in tickers("一个女大学生的美股定投记录，涨了20%")


def test_qqq_matches_the_index_but_not_the_listing_venue():
    assert "QQQ" in tickers("纳指又新高了")
    assert "QQQ" not in tickers("这家公司要在纳斯达克上市了")


def test_the_index_terms_that_dominate_the_corpus_now_resolve():
    """纳指/标普 outnumber every company name in the corpus and matched nothing before."""
    assert "QQQ" in tickers("纳斯达克100今年翻倍")
    assert tickers("标普500还是纳指100，长期定投选哪个") == {"SPY", "QQQ"}
