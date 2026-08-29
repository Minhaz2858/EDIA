"""Tests for cross-source (ERP + market) feedback fixes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.src.nlu.types import TaskType


# ---------------------------------------------------------------------------
# Query planner — blended intent detection
# ---------------------------------------------------------------------------

def test_detect_blended_intents_chinese_erp_market():
    from backend.src.planner.query_planner import _detect_blended_intents

    result = _detect_blended_intents(
        "为什么我们的异戊二烯销售比市场价格低？",
        "why is our isoprene sales lower than market price?",
    )
    assert "cross_source_erp_market" in result


def test_detect_blended_intents_english_margin():
    from backend.src.planner.query_planner import _detect_blended_intents

    result = _detect_blended_intents(
        "How is our margin vs market trend?",
        "how is our margin vs market trend?",
    )
    assert "cross_source_margin_analysis" in result


def test_detect_blended_intents_no_blending():
    from backend.src.planner.query_planner import _detect_blended_intents

    result = _detect_blended_intents("异戊二烯本周价格", "isoprene price this week")
    assert result == []


def test_keyword_fallback_cross_source_injects_intents():
    from backend.src.planner.query_planner import keyword_fallback

    plan = keyword_fallback("异戊二烯库存对定价策略有什么影响")
    assert plan is not None
    assert "actual_price" in plan["intents"]
    assert "market_reference" in plan["intents"]


def test_build_planner_prompt_contains_cross_source_instructions():
    from backend.src.planner.query_planner import build_planner_prompt
    from datetime import date

    prompt = build_planner_prompt("Compare our sales with market", date.today())
    assert "CRITICAL" in prompt
    assert "cross-source" in prompt.lower()


def test_build_planner_fast_preview_shows_sources():
    from backend.src.planner.query_planner import build_planner_fast_preview, QueryPlan

    plan = QueryPlan(
        products=["isoprene"],
        intents=["actual_price", "market_reference"],
        source_operations=[
            {
                "source": "erp/mysql",
                "intent": "actual_price",
                "product": "isoprene",
                "operation": "average",
                "metric": "price",
                "group_by": "day",
                "reason": "test",
            },
            {
                "source": "market/mysql",
                "intent": "market_reference",
                "product": "isoprene",
                "operation": "latest",
                "metric": "price",
                "group_by": "day",
                "reason": "test",
            },
        ],
        time_range={"start": "2026-05-01", "end": "2026-05-07"},
        needs_forecast=False,
        confidence="high",
        raw_query="test",
    )
    preview = build_planner_fast_preview(plan, "异戊二烯价格")
    assert "数据来源" in preview or "Data sources" in preview


# ---------------------------------------------------------------------------
# Skill registry — new cross-source skills exist
# ---------------------------------------------------------------------------

def test_registry_has_compare_erp_market_skill():
    from backend.src.hermes.skill_registry import get_skill

    skill = get_skill("compare-erp-market")
    assert skill is not None
    assert skill.category == "analysis"
    assert skill.privacy_level == "erp_confidential"
    assert "compare" in skill.markers_en


def test_registry_has_inventory_pricing_impact_skill():
    from backend.src.hermes.skill_registry import get_skill

    skill = get_skill("inventory-pricing-impact")
    assert skill is not None
    assert skill.tool_name == "edia_inventory_pricing_impact"


def test_registry_has_customer_quote_recommendation_skill():
    from backend.src.hermes.skill_registry import get_skill

    skill = get_skill("customer-quote-recommendation")
    assert skill is not None
    assert "quote recommendation" in skill.markers_en


def test_route_skills_matches_cross_source_queries():
    from backend.src.hermes.skill_registry import route_skills

    skills = route_skills("对比实际价格和市场价格差距", ["erp", "market"])
    assert "compare-erp-market" in skills


# ---------------------------------------------------------------------------
# Skill handlers — new cross-source handlers
# ---------------------------------------------------------------------------

def test_handle_compare_erp_market_returns_spread_when_data_available():
    from backend.src.hermes.skill_handlers import handle_compare_erp_market

    with patch("backend.src.service.erp_kpi_service.ErpKPIService") as MockKPI, patch(
        "backend.src.service.market_data_service.market_data"
    ) as MockMD:
        kpi = MagicMock()
        kpi.avg_price_30d = 8500.0
        kpi.sales_30d_ton = 120.0
        kpi.inventory_tons = 500.0
        kpi.inventory_days = 15.0
        MockKPI.return_value.get_product_kpi.return_value = kpi
        MockMD.get_regional_prices.return_value = {"east_china": 8200.0}

        result = handle_compare_erp_market({"product_id": "isoprene"}, {})
        assert result["status"] == "ok"
        assert result["erp_price"] == 8500.0
        assert result["market_price"] == 8200.0
        assert result["spread"] == 300.0


def test_handle_inventory_pricing_impact_returns_pressure():
    from backend.src.hermes.skill_handlers import handle_inventory_pricing_impact

    with patch("backend.src.service.erp_kpi_service.ErpKPIService") as MockKPI, patch(
        "backend.src.service.market_data_service.market_data"
    ) as MockMD:
        kpi = MagicMock()
        kpi.inventory_days = 45
        kpi.inventory_tons = 1000.0
        MockKPI.return_value.get_product_kpi.return_value = kpi
        MockMD.get_regional_prices.return_value = {"east_china": 8000.0}

        result = handle_inventory_pricing_impact({"product_id": "cracked_c5"}, {})
        assert result["status"] == "ok"
        assert result["pricing_pressure"] == "destock_pressure"
        assert "promotional pricing" in result["recommendation"]


def test_handle_customer_quote_recommendation_returns_floor_ceiling():
    from backend.src.hermes.skill_handlers import handle_customer_quote_recommendation

    with patch("backend.src.service.erp_kpi_service.ErpKPIService") as MockKPI, patch(
        "backend.src.service.market_data_service.market_data"
    ) as MockMD, patch("backend.src.tools.erp_tools.query_customer_sales_report") as MockReport:
        kpi = MagicMock()
        kpi.avg_price_30d = 8100.0
        MockKPI.return_value.get_product_kpi.return_value = kpi
        MockMD.get_regional_prices.return_value = {"east_china": 8000.0}
        MockReport.return_value = {"total_revenue": 1_000_000}

        result = handle_customer_quote_recommendation({"product_id": "isoprene"}, {})
        assert result["status"] == "ok"
        assert result["suggested_floor"] == 7600.0  # 8000 * 0.95
        assert result["suggested_ceiling"] == 8640.0  # 8000 * 1.08


# ---------------------------------------------------------------------------
# Execution — cross-source observations are appended
# ---------------------------------------------------------------------------

def test_execute_hermes_plan_includes_compare_erp_market_observation():
    from backend.src.hermes.execution import execute_hermes_plan

    with patch("backend.src.hermes.skill_handlers.handle_compare_erp_market") as mock_handler, patch(
        "backend.src.hermes.skill_executor.execute_routed_skills"
    ) as mock_routed:
        mock_handler.return_value = {
            "status": "ok",
            "summary": "ERP vs Market comparison fetched.",
            "erp_price": 8500,
            "market_price": 8200,
        }
        mock_routed.return_value = {
            "observations": [],
            "executed_skills": [],
            "routed_skills": [],
        }

        result = execute_hermes_plan(
            {
                "user_query": "对比实际价格和市场价格差距",
                "normalized_query": {},
                "capabilities": [],
                "tool_sequence": [],
                "agent_skills": [],
            },
            user_query="对比实际价格和市场价格差距",
        )
        tools = [obs["tool"] for obs in result["observations"]]
        assert "edia_compare_erp_market" in tools


def test_execute_hermes_plan_includes_inventory_pricing_observation():
    from backend.src.hermes.execution import execute_hermes_plan

    with patch("backend.src.hermes.skill_handlers.handle_inventory_pricing_impact") as mock_handler, patch(
        "backend.src.hermes.skill_executor.execute_routed_skills"
    ) as mock_routed:
        mock_handler.return_value = {
            "status": "ok",
            "summary": "Analyzed.",
            "pricing_pressure": "neutral",
        }
        mock_routed.return_value = {
            "observations": [],
            "executed_skills": [],
            "routed_skills": [],
        }

        result = execute_hermes_plan(
            {
                "user_query": "库存影响价格",
                "normalized_query": {},
                "capabilities": [],
                "tool_sequence": [],
                "agent_skills": [],
            },
            user_query="库存影响价格",
        )
        tools = [obs["tool"] for obs in result["observations"]]
        assert "edia_inventory_pricing_impact" in tools


def test_execute_hermes_plan_includes_quote_recommendation_observation():
    from backend.src.hermes.execution import execute_hermes_plan

    with patch("backend.src.hermes.skill_handlers.handle_customer_quote_recommendation") as mock_handler, patch(
        "backend.src.hermes.skill_executor.execute_routed_skills"
    ) as mock_routed:
        mock_handler.return_value = {
            "status": "ok",
            "summary": "Quote generated.",
            "suggested_floor": 7000,
        }
        mock_routed.return_value = {
            "observations": [],
            "executed_skills": [],
            "routed_skills": [],
        }

        result = execute_hermes_plan(
            {
                "user_query": "报价建议",
                "normalized_query": {},
                "capabilities": [],
                "tool_sequence": [],
                "agent_skills": [],
            },
            user_query="报价建议",
        )
        tools = [obs["tool"] for obs in result["observations"]]
        assert "edia_customer_quote_recommendation" in tools


# ---------------------------------------------------------------------------
# Decision graph — hybrid analysis mode for cross-source
# ---------------------------------------------------------------------------

def test_is_erp_hybrid_analysis_mode_cross_source_erp_market():
    from backend.src.engine.decision_graph import _is_erp_hybrid_analysis_mode

    state = {
        "parsed_intent": SimpleNamespace(task_type=TaskType.ERP_DATA_QUERY),
        "user_query": "销售情况和市场价格对比",
        "resolved_chat_mode": "analysis",
        "user_prefs": {},
    }
    assert _is_erp_hybrid_analysis_mode(state) is True


def test_is_erp_hybrid_analysis_mode_margin_analysis():
    from backend.src.engine.decision_graph import _is_erp_hybrid_analysis_mode

    state = {
        "parsed_intent": SimpleNamespace(task_type=TaskType.ERP_DATA_QUERY),
        "user_query": "How is our margin trend?",
        "resolved_chat_mode": "analysis",
        "user_prefs": {},
    }
    assert _is_erp_hybrid_analysis_mode(state) is True


def test_is_erp_hybrid_analysis_mode_pure_market_not_hybrid():
    from backend.src.engine.decision_graph import _is_erp_hybrid_analysis_mode

    state = {
        "parsed_intent": SimpleNamespace(task_type=TaskType.ERP_DATA_QUERY),
        "user_query": "C5 market price today",
        "resolved_chat_mode": "chat",
        "user_prefs": {"chat_mode": "chat"},
    }
    assert _is_erp_hybrid_analysis_mode(state) is False


# ---------------------------------------------------------------------------
# Citations — cross-source tools get tagged
# ---------------------------------------------------------------------------

def test_build_citations_tags_cross_source_tools():
    from backend.src.hermes.execution import build_citations_from_hermes_execution

    result = build_citations_from_hermes_execution(
        {
            "observations": [
                {
                    "tool": "edia_compare_erp_market",
                    "status": "ok",
                    "summary": "Compared prices",
                },
                {
                    "tool": "edia_inventory_pricing_impact",
                    "status": "ok",
                    "summary": "Analyzed stock",
                },
            ]
        }
    )
    sources = {c["source"] for c in result}
    assert "edia_compare_erp_market" in sources
    assert "edia_inventory_pricing_impact" in sources
