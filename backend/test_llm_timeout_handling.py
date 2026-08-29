import httpx
import json

from backend.src.engine.decision_graph import _replace_transport_error_with_fallback
from backend.src.nlu.llm_service import RealLLMService


def test_real_llm_service_returns_clear_timeout_message(monkeypatch):
    monkeypatch.setenv("DISABLE_LOCAL_LLM", "false")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:11434/api/generate")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_TIMEOUT_SEC", "180")

    service = RealLLMService()

    def _raise_timeout(*args, **kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(service, "_ollama_generate", _raise_timeout)

    result = service.generate_completion("system", "user")

    assert "Error generating response:" in result
    assert "response was interrupted" in result
    assert "check external LLM (Kimi) service availability" in result


def test_real_llm_service_reads_stage_timeout_budgets(monkeypatch):
    monkeypatch.setenv("DISABLE_LOCAL_LLM", "false")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:11434/api/generate")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_TIMEOUT_SEC", "90")
    monkeypatch.setenv("LLM_COMPLETION_TIMEOUT_SEC", "60")
    monkeypatch.setenv("LLM_JSON_TIMEOUT_SEC", "20")
    monkeypatch.setenv("EXTERNAL_LLM_TIMEOUT_SEC", "90")
    monkeypatch.setenv("EXTERNAL_LLM_COMPLETION_TIMEOUT_SEC", "30")
    monkeypatch.setenv("EXTERNAL_LLM_JSON_TIMEOUT_SEC", "12")

    service = RealLLMService()

    assert service.local_completion_timeout_sec == 60.0
    assert service.local_json_timeout_sec == 20.0
    assert service.external_completion_timeout_sec == 30.0
    assert service.external_json_timeout_sec == 12.0


def test_transport_error_fallback_returns_human_readable_report_for_general_queries():
    result = _replace_transport_error_with_fallback(
        "Error generating response: model response was interrupted.",
        {
            "user_query": "Please explain the market situation and what we should do this week.",
            "user_prefs": {"language": "en"},
            "pricing_decision": {},
            "parsed_intent": None,
            "user_context": None,
        },
    )

    assert "Analysis Interrupted" in result
    assert "response was interrupted" in result
    assert "Please retry" in result


def test_blank_llm_output_also_returns_human_readable_fallback_report():
    result = _replace_transport_error_with_fallback(
        "",
        {
            "user_query": "Please explain the market situation and what we should do this week.",
            "user_prefs": {"language": "en"},
            "pricing_decision": {},
            "parsed_intent": None,
            "user_context": None,
        },
    )

    assert "Analysis Interrupted" in result
    assert "Please retry" in result


def test_streaming_llm_recovers_when_visible_output_is_empty(monkeypatch):
    monkeypatch.setenv("DISABLE_LOCAL_LLM", "false")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:11434/api/generate")
    monkeypatch.setenv("LLM_MODEL", "test-model")

    service = RealLLMService()

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield json.dumps({"response": "<think>I am reasoning only", "done": False}).encode()
            yield json.dumps({"response": "</think>", "done": True}).encode()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(service, "_ollama_generate", lambda *args, **kwargs: _FakeResponse())
    monkeypatch.setattr(
        service,
        "generate_completion",
        lambda *args, **kwargs: "Recovered final answer.",
    )

    chunks = list(service.generate_completion_stream("system", "user"))

    assert chunks == ["Recovered final answer."]