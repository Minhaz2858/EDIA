import importlib
import sys

import pytest

_AUTH_HEADERS = {"Authorization": "Bearer test-token"}


def _import_server_with_stubs(monkeypatch):
    pytest.importorskip("fastapi.testclient")

    monkeypatch.setenv("USE_REAL_LLM", "true")
    monkeypatch.setenv("MARKET_PRICES_REFRESH_ON_STARTUP", "false")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    monkeypatch.setenv("EDIA_FAST_ERP_STREAM", "false")

    class StubLLM:
        def __init__(self):
            self.client = object()
            self.model = "stub-llm"
            self.is_ready = True

        def generate_json(self, system_prompt: str, user_prompt: str, **kwargs) -> dict:
            return {}

        def generate_completion(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
            return ""

        def generate_completion_stream(self, system_prompt: str, user_prompt: str, **kwargs):
            yield ""

    for module_name in ["backend.server"]:
        sys.modules.pop(module_name, None)

    import backend.src.nlu.llm_service as llm_module
    import backend.src.service.alert_service as alert_module
    import backend.src.intelligence.intelligence_orchestrator as intel_module

    monkeypatch.setattr(llm_module, "RealLLMService", StubLLM)
    monkeypatch.setattr(alert_module.alert_worker, "start", lambda: None)
    monkeypatch.setattr(alert_module.alert_worker, "stop", lambda: None)
    monkeypatch.setattr(intel_module, "init_intelligence", lambda llm: None)
    monkeypatch.setattr(intel_module, "get_news_agent", lambda: None)
    monkeypatch.setattr(intel_module, "get_override_engine", lambda: None)
    monkeypatch.setattr(intel_module, "get_learning_loop", lambda: None)
    monkeypatch.setattr(
        intel_module,
        "get_intelligence_context",
        lambda hours=4, **kwargs: {
            "has_intelligence": False,
            "checked": True,
            "checked_hours": hours,
            "events": [],
            "causal_analyses": [],
            "scenarios": [],
            "override_decisions": [],
            "intelligence_summary": "",
        },
    )

    server = importlib.import_module("backend.server")
    server.market_data.refresh = lambda: None
    server._ensure_market_runtime_fresh = lambda force=False: None
    server.db.get_user_by_token = lambda token: (
        {
            "id": "test-user",
            "username": "test-user",
            "is_admin": True,
            "is_active": True,
            "must_change_password": False,
        }
        if token == "test-token"
        else None
    )
    return server


def _create_report(server):
    session = server.db.create_session("test-user", "Test Session")
    session_id = str(session["id"]) if isinstance(session, dict) else str(session)
    return server.db.create_generated_report(
        user_id="test-user",
        session_id=session_id,
        title="Block Report",
        summary="summary",
        markdown="# Block Report",
        blocks=[
            {
                "id": "block-kpi",
                "type": "kpi",
                "title": "Spot Price",
                "definition": {"label": "Price", "value": 4780, "unit": "CNY/t"},
            },
            {
                "id": "block-sql",
                "type": "custom_sql",
                "title": "Health Query",
                "definition": {"query": "SELECT 1 AS value", "limit": 10},
            },
        ],
        context={},
    )


def test_report_blocks_roundtrip_and_refresh(monkeypatch):
    from fastapi.testclient import TestClient

    server = _import_server_with_stubs(monkeypatch)
    report = _create_report(server)

    with TestClient(server.app) as client:
        detail = client.get(f"/api/reports/{report['id']}", headers=_AUTH_HEADERS)
        assert detail.status_code == 200
        payload = detail.json()
        assert len(payload.get("blocks") or []) == 2

        refreshed = client.post(
            f"/api/reports/{report['id']}/refresh", headers=_AUTH_HEADERS
        )
        assert refreshed.status_code == 200
        refreshed_payload = refreshed.json()
        assert refreshed_payload["refreshed_block_count"] == 2
        assert refreshed_payload["report"]["last_refreshed_at"]

        sql_block = next(
            block
            for block in refreshed_payload["report"]["blocks"]
            if block.get("id") == "block-sql"
        )
        rows = (sql_block.get("rendered") or {}).get("rows") or []
        assert rows and rows[0].get("value") == 1


def test_refresh_single_block_not_found(monkeypatch):
    from fastapi.testclient import TestClient

    server = _import_server_with_stubs(monkeypatch)
    report = _create_report(server)

    with TestClient(server.app) as client:
        response = client.post(
            f"/api/reports/{report['id']}/refresh/missing-block",
            headers=_AUTH_HEADERS,
        )

    assert response.status_code == 404
