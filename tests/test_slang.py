from infinance.mentions import Matcher, load_stock_dict

DICT = load_stock_dict()


def tickers(text):
    return set(Matcher(DICT).extract(text))


def basis(text, ticker):
    return Matcher(DICT).extract(text).get(ticker, (None, None))[1]


def test_distinctive_slang_matches_without_context():
    # 巨硬 / 皮衣黄 / 拼夕夕 are only ever used for one company
    assert "MSFT" in tickers("巨硬这波AI布局挺猛")
    assert "NVDA" in tickers("皮衣黄又发新卡了")
    assert "PDD" in tickers("拼夕夕的增速真夸张")


def test_slang_needing_context_is_gated():
    # 老黄 alone is just "old Huang"; with finance context it means Jensen Huang
    assert "NVDA" not in tickers("老黄today给我带了午饭")
    assert "NVDA" in tickers("老黄的财报又炸了，英伟达股价起飞")
    # 牙膏厂 = Intel only in a market conversation
    assert "INTC" in tickers("牙膏厂这次挤了不少，股价大涨")


def test_person_names_are_ambiguous_not_safe():
    # Musk fronts SpaceX/X/Neuralink too, so 马斯克 must never be a standalone TSLA hit
    assert "TSLA" not in tickers("马斯克又发推了")
    assert basis("马斯克说特斯拉要涨，我加仓了", "TSLA") == "safe_alias"  # 特斯拉 wins


def test_brand_in_product_context_does_not_tag_the_stock():
    # the real crawl produced a book ad mentioning 谷歌Play — not an Alphabet mention
    assert "GOOGL" not in tickers("我的书可以在谷歌Play书店找到")


def test_latin_context_word_needs_word_boundary():
    # "pe" must not match inside "people", or any English text gains finance context
    assert "GOOGL" not in tickers("a lot of people use 谷歌 every day")
    assert "GOOGL" in tickers("谷歌 pe 太高了")


def test_newly_added_tickers_resolve():
    assert "SNDK" in tickers("闪迪涨疯了")           # seen in the real corpus
    assert "STX" in tickers("希捷和美光都在涨")
    assert "CEG" in tickers("星座能源受益于AI电力需求")
