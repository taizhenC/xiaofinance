from infinance.analyze import pick_quotes
from infinance.util import is_bot_prompt, substance


def test_hashtag_only_note_has_no_substance():
    # a real note from run 13: the argument is in the image, the desc is a tag block
    text = "Is That True？ #纳斯达克 #美股 #如何财富自由 #海力士 #美光"
    assert substance(text) == len("IsThatTrue")


def test_raw_topic_tags_count_as_nothing_too():
    assert substance("#纳斯达克[话题]# #美股[话题]#") == 0


def test_a_chinese_claim_outweighs_an_english_phrase_of_the_same_length():
    """Both are 10-12 characters; only one of them says anything."""
    assert substance("海力士暴涨13%，美股创新高！ #美股 #海力士") > substance("Is That True？")


def test_emoji_tags_and_handles_are_not_prose():
    assert substance("[加油R][点赞R]") == 0
    assert substance("@问一问 为什么海力士进不了纳指") == substance("为什么海力士进不了纳指")


def test_bot_prompts_are_recognised():
    assert is_bot_prompt("@问一问 为什么海力士进不了纳指")
    assert not is_bot_prompt("问一问身边的朋友都在买什么")


def _item(text, likes=0, fanout=1, prose=None):
    return {"text": text, "likes": likes, "fanout": fanout, "substance": substance(prose or text)}


def test_quotes_prefer_substance_over_likes():
    items = [
        _item("Is That True？", likes=500),
        _item("海力士登陆纳斯达克，发行价149美元，募资265亿美元，刷新外国公司在美上市纪录", likes=3),
    ]
    assert pick_quotes(items, k=1) == [items[1]["text"]]


def test_quotes_prefer_focused_sources_over_roundups():
    roundup = _item("下周美股财报日历：周二 JPM、BAC、C、GS、WFC 大型银行率先登场", likes=900, fanout=12)
    focused = _item("美光这波存储周期还没走完，DRAM 报价还在往上走，我继续拿", likes=10, fanout=1)
    assert pick_quotes([roundup, focused], k=1) == [focused["text"]]


def test_quotes_fall_back_rather_than_pad_with_nothing():
    """Every candidate is thin — show the thin one, don't return an empty card."""
    items = [_item("我咋感觉股价到头了", likes=2)]
    assert pick_quotes(items) == [items[0]["text"]]


def test_framing_prefix_does_not_lift_a_thin_comment_over_the_bar():
    """"主帖下的评论: " is our own wrapper; it must not count as the crowd's words."""
    thin = _item("主帖下的评论: 我咋感觉股价到头了", likes=9, prose="我咋感觉股价到头了")
    real = _item("存储的景气度是真实的，不是炒概念，江波龙兆易创新的业绩确认了这点", likes=1)
    assert pick_quotes([thin, real], k=1) == [real["text"]]
