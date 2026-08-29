from __future__ import annotations

from backend.src.services.report_authoring_service import ReportAuthoringService
from backend.src.services.report_builder_copilot import report_builder_copilot


def _gallery() -> list[dict]:
    return ReportAuthoringService().get_template_gallery()


def _data_sources() -> list[dict]:
    return ReportAuthoringService().get_available_data_sources()


def test_report_builder_copilot_selects_customer_sales_template() -> None:
    plan = report_builder_copilot.build_plan(
        objective="给老板输出客户销售报告，包含增长点、流失风险和下一步动作",
        current_blocks=[],
        templates=_gallery(),
        data_sources=_data_sources(),
        language="zh",
    )

    assert plan["contract"] == "report_builder_copilot_v1"
    assert plan["recommended_template"]["id"] == "customer_sales_report"
    assert "customer_sales" in plan["required_data"]
    assert "data_block" in plan["missing_sections"]
    assert any(block["type"] == "erp_customer_breakdown" for block in plan["recommended_blocks"])
    assert plan["readiness"]["status"] == "not_ready"


def test_report_builder_copilot_marks_ready_when_blocks_cover_data_and_narrative() -> None:
    current_blocks = [
        {"type": "erp_customer_breakdown", "title": "客户销售明细", "config": {"product_id": "blowing_agent"}},
        {"type": "text", "title": "管理层结论", "config": {"content": "行动建议"}},
    ]

    plan = report_builder_copilot.build_plan(
        objective="boss customer sales report",
        current_blocks=current_blocks,
        templates=_gallery(),
        data_sources=_data_sources(),
        language="en",
    )

    assert plan["language"] == "en"
    assert plan["missing_sections"] == []
    assert plan["quality_gates"]["has_narrative"] == "pass"
    assert plan["readiness"]["status"] in {"ready", "needs_work"}


def test_report_builder_copilot_endpoint_returns_plan(monkeypatch) -> None:
    import pytest

    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from backend.test_report_blocks import _AUTH_HEADERS, _import_server_with_stubs

    server = _import_server_with_stubs(monkeypatch)

    with TestClient(server.app) as client:
        response = client.post(
            "/api/report-authoring/copilot-plan",
            json={
                "objective": "老板要看库存预警和销售动作",
                "current_blocks": [],
                "language": "zh",
            },
            headers=_AUTH_HEADERS,
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["contract"] == "report_builder_copilot_v1"
    assert payload["recommended_template"]["id"] in {"inventory_alert_report", "customer_sales_report", "executive_dashboard"}
    assert payload["recommended_blocks"]