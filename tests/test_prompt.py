from infinance.analyze import build_prompt

ITEMS = [{"type": "note", "id": "n1", "text": "英伟达财报炸裂，继续持有",
          "likes": 5, "ts": 0, "url": None, "cluster_size": 1}]


def test_prompt_includes_prev_summary_with_guardrails():
    _, user = build_prompt("NVDA", "英伟达", ITEMS, "en", now=3_600_000,
                           prev_summary="NVDA: previously strongly bullish on earnings.")
    assert "上一周期的分析结论" in user
    assert "NVDA: previously strongly bullish on earnings." in user
    assert "以本次内容为准" in user
    assert "不是本次判断的依据" in user


def test_prompt_without_prev_summary_has_no_reference():
    _, user = build_prompt("NVDA", "英伟达", ITEMS, "en", now=3_600_000)
    assert "上一周期" not in user


def test_prompt_preserves_evidence_and_evaluation_contract():
    system, user = build_prompt("NVDA", "英伟达", ITEMS, "en", now=3_600_000)

    assert "[1] [note] [1小时前] [赞:5] 英伟达财报炸裂，继续持有" in user
    assert "同名歧义" in user
    assert "教学/科普例子" in user
    assert "广告引流" in user
    assert "相同论点只算一次" in user
    assert "不按重复次数" in user
    assert "不从剔除内容推测" in user
    assert "notable_quote_ids从保留条目" in user
    assert "只给编号，不抄原文" in user
    assert "只输出JSON" in system


def test_prompt_only_explains_markers_that_are_present():
    _, plain = build_prompt("NVDA", "英伟达", ITEMS, "en", now=3_600_000)
    assert "[×N相似]" not in plain
    assert "[盘点·提及N股]" not in plain
    assert "[顺带提及]" not in plain
    assert "- ↳：" not in plain
    assert "- …：" not in plain

    marked = [{
        **ITEMS[0],
        "text": "开头…↳ 继续持有",
        "prompt_text": "↳ 继续持有…",
        "cluster_size": 2,
        "fanout": 4,
        "aside": True,
    }]
    _, user = build_prompt("NVDA", "英伟达", marked, "en", now=3_600_000)
    assert "[×N相似]" in user
    assert "[盘点·提及N股]" in user
    assert "[顺带提及]" in user
    assert "- ↳：" in user
    assert "- …：" in user
    assert "一问一答只是一次交流" in user


def test_fixed_prompt_stays_compact():
    system, user = build_prompt("NVDA", "英伟达", ITEMS, "en", now=3_600_000)
    evidence = "[1] [note] [1小时前] [赞:5] 英伟达财报炸裂，继续持有"
    assert len(system) + len(user) - len(evidence) < 700
