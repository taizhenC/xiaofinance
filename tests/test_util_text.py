from infinance.util import clean_tags, note_text, strip_hashtags


def test_note_text_drops_repeated_title():
    assert note_text("标题党", "标题党 正文继续") == "标题党 正文继续"
    assert note_text("标题", "正文") == "标题 正文"
    assert note_text(None, "只有正文") == "只有正文"


def test_tag_helpers():
    assert clean_tags("#美光[话题]# #英伟达[话题]# 正文") == "#美光 #英伟达 正文"
    assert "美光" not in strip_hashtags("失眠 #美光[话题]#")
    assert "失眠" in strip_hashtags("失眠 #美光[话题]#")
