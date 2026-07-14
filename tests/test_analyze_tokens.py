from types import SimpleNamespace

from app import analyze
from app.config import settings as default_settings


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
