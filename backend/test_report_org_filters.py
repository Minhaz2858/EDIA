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

    class StubLLM:
        def __init__(self):
            self.client = object()
            self.model = "stub-llm"
            self.is_ready = True

        def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
            return {}

        def generate_completion(self, system_prompt: str, user_prompt: str) -> str:
            return ""

        def generate_completion_stream(self, system_prompt: str, user_prompt: str):
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


def _install_fake_loader(monkeypatch, server):
    huizhou_rows = [
        {
            "ORG_NAME": "Huizhou Isco",
            "FDATE": "2024-09-06 11:43:34.000000",
            "FDELIVERYDATE": "2024-09-06",
            "FBILLNO": "HZ-001",
            "CUST_NAME": "HZ Customer",
            "material_name": "HZ-TEST",
            "product_type": "液体",
            "grade": "优级品",
            "contract_fqty": 12.0,
            "contract_amount": 1200.0,
            "out_fqty": 10.0,
            "out_amount": 1000.0,
            "out_notoutqty": 2.0,
            "order_tax_unit_price": 100.0,
            "ftaxprice_hit_count": 1,
            "fprice_hit_count": 0,
        }
    ]
    guangdong_rows = [
        {
            "ORG_NAME": "Guangdong Isco",
            "FDATE": "2024-09-06 11:43:34.000000",
            "FDELIVERYDATE": "2024-09-06",
            "FBILLNO": "GD-001",
            "CUST_NAME": "GD Customer",
            "material_name": "GD-TEST",
            "product_type": "液体",
            "grade": "优级品",
            "contract_fqty": 22.0,
            "contract_amount": 2200.0,
            "out_fqty": 20.0,
            "out_amount": 2000.0,
            "out_notoutqty": 2.0,
            "order_tax_unit_price": 100.0,
            "ftaxprice_hit_count": 1,
            "fprice_hit_count": 0,
        }
    ]

    def fake_loader(org: str = "huizhou"):
        org_meta = server._resolve_report_org(org)
        rows = huizhou_rows if org_meta["key"] == "huizhou" else guangdong_rows
        return rows, org_meta

    monkeypatch.setattr(server, "_load_contract_execution", fake_loader)


def test_customer_sales_report_org_isolation(monkeypatch):
    from fastapi.testclient import TestClient

    server = _import_server_with_stubs(monkeypatch)
    _install_fake_loader(monkeypatch, server)

    with TestClient(server.app) as client:
        huizhou = client.get(
            "/api/v2/reports/customer-sales",
            params={"org": "huizhou", "month": "2024-09"},
                headers=_AUTH_HEADERS,
        )
        guangdong = client.get(
            "/api/v2/reports/customer-sales",
            params={"org": "guangdong", "month": "2024-09"},
                headers=_AUTH_HEADERS,
        )

    assert huizhou.status_code == 200
    assert guangdong.status_code == 200

    hz_payload = huizhou.json()
    gd_payload = guangdong.json()

    assert hz_payload["org_name"] == "Huizhou Isco"
    assert gd_payload["org_name"] == "Guangdong Isco"
    assert hz_payload["summary"]["total_sales_qty"] == 10.0
    assert gd_payload["summary"]["total_sales_qty"] == 20.0
    assert hz_payload["available_materials"] == ["HZ-TEST"]
    assert gd_payload["available_materials"] == ["GD-TEST"]


def test_contract_performance_report_org_isolation(monkeypatch):
    from fastapi.testclient import TestClient

    server = _import_server_with_stubs(monkeypatch)
    _install_fake_loader(monkeypatch, server)

    params_hz = {
        "org": "huizhou",
        "start_date": "2024-09-01",
        "end_date": "2024-09-30",
    }
    params_gd = {
        "org": "guangdong",
        "start_date": "2024-09-01",
        "end_date": "2024-09-30",
    }

    with TestClient(server.app) as client:
        huizhou = client.get(
            "/api/v2/reports/contract-performance",
            params=params_hz,
            headers=_AUTH_HEADERS,
        )
        guangdong = client.get(
            "/api/v2/reports/contract-performance",
            params=params_gd,
            headers=_AUTH_HEADERS,
        )

    assert huizhou.status_code == 200
    assert guangdong.status_code == 200

    hz_payload = huizhou.json()
    gd_payload = guangdong.json()

    assert hz_payload["org_name"] == "Huizhou Isco"
    assert gd_payload["org_name"] == "Guangdong Isco"
    assert hz_payload["summary"]["out_qty"] == 10.0
    assert gd_payload["summary"]["out_qty"] == 20.0
    assert hz_payload["execution_chart"][0]["product"].startswith("HZ-TEST")
    assert gd_payload["execution_chart"][0]["product"].startswith("GD-TEST")


@pytest.mark.parametrize(
    "path",
    [
        "/api/v2/reports/customer-sales",
        "/api/v2/reports/contract-performance",
        "/api/v2/reports/contract-performance/ai-analysis",
    ],
)
def test_report_endpoints_reject_unknown_org(monkeypatch, path):
    from fastapi.testclient import TestClient

    server = _import_server_with_stubs(monkeypatch)
    _install_fake_loader(monkeypatch, server)

    with TestClient(server.app) as client:
        response = client.get(
            path,
            params={"org": "unknown-org"},
            headers=_AUTH_HEADERS,
        )

    assert response.status_code == 400
    assert "Unknown organization" in response.json()["detail"]


def test_contract_performance_ai_analysis_cache_isolated_by_org(monkeypatch):
    from fastapi.testclient import TestClient

    server = _import_server_with_stubs(monkeypatch)
    _install_fake_loader(monkeypatch, server)

    cache_hz = {
        "chart_top": "Huizhou chart",
        "chart_bottom": "",
        "summary_paragraph": "",
        "summary_bullets": [],
        "unshipped_analysis": "",
    }
    cache_gd = {
        "chart_top": "Guangdong chart",
        "chart_bottom": "",
        "summary_paragraph": "",
        "summary_bullets": [],
        "unshipped_analysis": "",
    }

    server._ai_analysis_cache.clear()
    server._ai_analysis_cache[("huizhou", "2024-09-01", "2024-09-30", "")] = {
        "data": cache_hz,
        "ts": server.time.time(),
    }
    server._ai_analysis_cache[("guangdong", "2024-09-01", "2024-09-30", "")] = {
        "data": cache_gd,
        "ts": server.time.time(),
    }

    with TestClient(server.app) as client:
        huizhou = client.get(
            "/api/v2/reports/contract-performance/ai-analysis",
            params={
                "org": "huizhou",
                "start_date": "2024-09-01",
                "end_date": "2024-09-30",
            },
            headers=_AUTH_HEADERS,
        )
        guangdong = client.get(
            "/api/v2/reports/contract-performance/ai-analysis",
            params={
                "org": "guangdong",
                "start_date": "2024-09-01",
                "end_date": "2024-09-30",
            },
            headers=_AUTH_HEADERS,
        )

    assert huizhou.status_code == 200
    assert guangdong.status_code == 200
    assert huizhou.json()["chart_top"] == "Huizhou chart"
    assert guangdong.json()["chart_top"] == "Guangdong chart"