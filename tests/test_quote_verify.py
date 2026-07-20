"""AN-01: no rendered quote may fail a (normalized) substring check against
the analysis inputs. Quotes are the product's proof layer — one fabricated
quote in a screenshot destroys the credibility the scoreboard exists to build."""

from infinance.analyze import fallback_quotes, verify_quotes


def items(*texts, types=None):
    types = types or ["note"] * len(texts)
    return [
        {"type": t, "id": f"i{n}", "text": x, "likes": 100 - n, "ts": 0,
         "url": None, "cluster_size": 1}
        for n, (x, t) in enumerate(zip(texts, types, strict=True))
    ]


def test_exact_quote_passes():
    src = items("英伟达财报炸裂，明天梭哈", "特斯拉不行了")
    kept, dropped = verify_quotes(["英伟达财报炸裂，明天梭哈"], src)
    assert kept == ["英伟达财报炸裂，明天梭哈"]
    assert dropped == 0


def test_partial_verbatim_quote_passes():
    src = items("今天大盘不错。英伟达财报炸裂，明天梭哈！大家怎么看")
    kept, dropped = verify_quotes(["英伟达财报炸裂"], src)
    assert kept and dropped == 0


def test_punctuation_and_whitespace_reflow_still_passes():
    # the model often drops emoji/spacing when quoting — content chars identical
    src = items("老黄  又赢了！！🚀🚀 4090根本抢不到")
    kept, dropped = verify_quotes(["老黄又赢了，4090 根本抢不到"], src)
    assert kept and dropped == 0


def test_paraphrase_is_dropped():
    src = items("英伟达财报炸裂，明天梭哈")
    kept, dropped = verify_quotes(["财报很好，投资者打算买入"], src)
    assert kept == []
    assert dropped == 1


def test_single_changed_character_is_dropped():
    src = items("英伟达财报炸裂")
    kept, dropped = verify_quotes(["英伟达财报爆裂"], src)
    assert kept == [] and dropped == 1


def test_quote_spanning_two_items_is_dropped():
    # concatenating unrelated items must not create a verifiable quote
    src = items("英伟达涨疯了", "特斯拉崩了")
    kept, dropped = verify_quotes(["英伟达涨疯了特斯拉崩了"], src)
    assert kept == [] and dropped == 1


def test_pure_emoji_quote_is_dropped():
    src = items("🚀🚀🚀", "英伟达冲")
    kept, dropped = verify_quotes(["🚀🚀🚀"], src)
    assert kept == [] and dropped == 1


def test_mixed_batch_keeps_only_verifiable():
    src = items("英伟达财报炸裂", "苹果不涨了")
    kept, dropped = verify_quotes(["苹果不涨了", "编造的引用", "英伟达财报炸裂"], src)
    assert kept == ["苹果不涨了", "英伟达财报炸裂"]
    assert dropped == 1


def test_fallback_prefers_comments():
    src = items("帖子正文", "热评第一", "热评第二", types=["note", "comment", "comment"])
    assert fallback_quotes(src) == ["热评第一", "热评第二"]


def test_fallback_uses_notes_when_no_comments():
    src = items("帖子一", "帖子二")
    assert fallback_quotes(src) == ["帖子一", "帖子二"]
