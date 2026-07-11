from app.analyze import build_prompt

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
