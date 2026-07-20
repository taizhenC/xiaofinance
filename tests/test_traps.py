from infinance.mentions import Matcher, load_stock_dict, mask_traps

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


def test_qcom_does_not_fire_on_raising_an_approval_rate():
    """Was live in the DB: 高通 matched inside 提高通过率, in a brokerage-account how-to."""
    assert "QCOM" not in tickers("需要准备美国地址，建议提前办理ITIN以提高通过率")
    assert "QCOM" not in tickers("美联储加息应对高通胀，美股承压")
    assert "QCOM" in tickers("高通骁龙出货大涨，高通财报亮眼")


def test_美国银行_was_dropped_because_it_is_also_an_ordinary_noun():
    """4 of BAC's 6 mentions were false: '#美国银行股#', '美国银行开户', '美国银行卡', and
    '部分美国银行收取$500' — where 美国银行 just means "American banks".

    That last one is why traps were not enough. 美国银行 is both a proper noun (Bank of America)
    and a common noun, and the context gate cannot separate them: an account-opening guide is
    full of finance words. So the alias is gone, and BAC rides on 美银 and its ticker. The cost
    is real and accepted — a bank-earnings roundup listing 美国banking names no longer counts
    for BAC. Those are fan-out-discounted roundups anyway; XHS 开户 guides are far more common.
    """
    assert "BAC" not in tickers("#美国银行股# 美股财报季来了")
    assert "BAC" not in tickers("提到美国银行开户，很多人第一反应就是Chase")
    assert "BAC" not in tickers("部分美国银行收取$500-$1000的开户查册费")
    assert "BAC" not in tickers("下周盘前摩根大通、美国银行、高盛财报密集")  # the accepted miss
    assert "BAC" in tickers("美银财报超预期，股价大涨")


def test_美银_does_not_fire_inside_east_west_bank():
    """华美银行 (East West Bank) contains 美银 — a false positive that predated this pass."""
    assert "BAC" not in tickers("在用华美银行个人户，平时做海外投资挺顺手")


def test_the_micron_misspelling_is_caught_but_not_the_spotlight_idiom():
    """镁光 is how people actually type 美光 — and 镁光灯 ('in the spotlight') is stock prose."""
    assert "MU" in tickers("镁光内存涨价，存储周期到了")
    assert "MU" not in tickers("站在镁光灯下的美股新贵，股价大涨")


def test_terms_that_could_not_be_saved_were_removed_rather_than_guarded():
    """多多/理想/老虎 are ordinary words. The full names still catch the companies, so the bare
    token bought nothing and its substring surface was the whole language."""
    assert "PDD" not in tickers("多多关照，美股新手请大家指教")
    assert "PDD" in tickers("拼多多财报超预期")
    assert "LI" not in tickers("我的理想生活就是靠美股财富自由")
    assert "LI" in tickers("理想汽车交付量创新高")
    assert "TIGR" not in tickers("去澳门玩老虎机，顺便看看美股")
    assert "TIGR" in tickers("老虎证券开户送股票")


def test_a_good_future_for_your_kid_is_not_a_tutoring_stock():
    assert "TAL" not in tickers("孩子的好未来比什么都重要，我在美股定投")
    assert "TAL" in tickers("好未来教育财报，学而思业务回暖")


def test_the_new_retail_slang_resolves():
    assert "META" in tickers("非死不可的广告收入又涨了")
    assert "NVDA" in tickers("核弹厂又发新卡，股价起飞")
    assert "GOOGL" in tickers("狗家财报，云业务增长强劲")
    assert "BRK" in tickers("巴郡今年跑赢标普")  # the HK name — 伯克希尔 would have missed it
    assert tickers("蔚小理三家美股表现分化") == {"NIO", "XPEV", "LI"}
    assert "C" not in tickers("花旗参礼盒，美股博主推荐的")  # ginseng, not Citigroup


def test_a_dictionary_fix_can_unmake_a_mention_it_used_to_make(conn):
    """extract_mentions only ever upserted, so a mention, once made, was permanent: removing a
    bad alias or adding a trap left every false positive it had ever produced sitting in the DB
    until the note aged out of the window. This is what made the 高通/提高通过率 phantom
    survive its own fix."""
    from infinance.mentions import extract_mentions
    from infinance.util import now_ms, simhash64, to_signed64

    now = now_ms()
    window = 24 * 3_600_000
    conn.execute(
        "INSERT INTO notes(note_id, title, note_desc, publish_time_ms, liked_count, simhash,"
        " source_keyword) VALUES('n1','美股开户攻略','建议提前办理ITIN以提高通过率，美股券商对比',?,10,?,'美股')",
        (now - 3_600_000, to_signed64(simhash64("开户"))),
    )
    conn.commit()

    # a dictionary that still has the bug: 高通 with no guard
    broken = {"context_words": ["美股", "券商"], "collision_tickers": [], "traps": [],
              "stocks": [{"ticker": "QCOM", "name_cn": "高通", "aliases": ["高通"], "ambiguous": []}]}
    extract_mentions(conn, broken, [], window, now=now)
    assert conn.execute("SELECT COUNT(*) FROM stock_mentions WHERE ticker='QCOM'").fetchone()[0] == 1

    # now fix it, and re-extract over the same corpus — the phantom must be gone
    fixed = {**broken, "traps": ["提高通"]}
    extract_mentions(conn, fixed, [], window, now=now)
    assert conn.execute("SELECT COUNT(*) FROM stock_mentions WHERE ticker='QCOM'").fetchone()[0] == 0
