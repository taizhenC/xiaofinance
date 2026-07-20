import json
from types import SimpleNamespace

from infinance import analyze
from infinance.config import settings as default_settings

ITEMS = [{
    "type": "note",
    "id": "n1",
    "text": "英伟达财报炸裂，继续持有",
    "prompt_text": "英伟达财报炸裂，继续持有",
    "likes": 5,
    "ts": 0,
    "url": None,
    "cluster_size": 1,
    "fanout": 1,
}]


def _context(ticker, items, prev_summary=None):
    return {
        "ticker": ticker,
        "name_cn": ticker,
        "score": 1.0,
        "asset_type": "stock",
        "items": items,
        "prev_summary": prev_summary,
    }


def test_storage_failure_does_not_repeat_a_successful_llm_call(conn, monkeypatch):
    calls = 0

    def call_llm(settings, system, user):
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"summary":"bullish"}'))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
        )

    def fail_store(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(analyze, "gather_items", lambda *args: ITEMS)
    monkeypatch.setattr(analyze, "_call_llm", call_llm)
    monkeypatch.setattr(analyze, "store_result", fail_store)
    settings = default_settings.model_copy(update={"DEEPSEEK_API_KEY": "test-key"})

    assert analyze.analyze_ticker(conn, "NVDA", settings, now=3_600_000) == "error"
    assert calls == 1


def test_shared_evidence_groups_are_bounded_and_leave_unrelated_tickers_alone():
    shared = [{**ITEMS[0], "fanout": 10}]
    contexts = [_context(f"T{n}", shared) for n in range(6)]
    contexts.append(_context("ONLY", [{**ITEMS[0], "id": "unique", "fanout": 1}]))

    groups = analyze.shared_evidence_groups(contexts, max_size=5)

    assert sorted(len(group) for group in groups) == [1, 1, 5]
    assert next(group for group in groups if group[0]["ticker"] == "ONLY")[0]["ticker"] == "ONLY"


def test_batch_prompt_lists_shared_text_once_and_keeps_local_item_numbers():
    shared = {**ITEMS[0], "fanout": 8}
    left = _context("AAA", [shared], prev_summary="AAA: old")
    right = _context("BBB", [shared])

    _, user = analyze.build_batch_prompt([left, right], "en", now=3_600_000)

    assert user.count(shared["text"]) == 1
    assert user.count("[1]=E1") == 2
    assert "AAA: old" in user
    assert "notable_quote_ids从保留条目选最多3个本地编号" in user
    assert "不给E编号" in user


def test_batch_analysis_stores_each_ticker_and_preserves_total_usage(conn, monkeypatch):
    shared = {**ITEMS[0], "fanout": 8}
    contexts = [_context("AAA", [shared]), _context("BBB", [shared])]
    response = {
        "analyses": [
            {"ticker": "AAA", "summary": "up", "notable_quote_ids": [1]},
            {"ticker": "BBB", "summary": "down", "notable_quote_ids": [1]},
        ]
    }
    calls = 0

    def call_llm(settings, system, user, max_tokens=2000):
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(response)))],
            usage=SimpleNamespace(prompt_tokens=101, completion_tokens=41),
        )

    monkeypatch.setattr(analyze, "_call_llm", call_llm)
    settings = default_settings.model_copy(update={"DEEPSEEK_API_KEY": "test-key"})

    result = analyze.analyze_tickers_batch(conn, contexts, settings, None, 3_600_000)

    assert result == {"AAA": "ok", "BBB": "ok"}
    assert calls == 1
    rows = conn.execute(
        "SELECT ticker, summary, notable_quotes, input_tokens, output_tokens "
        "FROM stock_analyses ORDER BY ticker"
    ).fetchall()
    assert [row["summary"] for row in rows] == ["AAA: up", "BBB: down"]
    assert all(json.loads(row["notable_quotes"]) == [shared["text"]] for row in rows)
    assert sum(row["input_tokens"] for row in rows) == 101
    assert sum(row["output_tokens"] for row in rows) == 41
