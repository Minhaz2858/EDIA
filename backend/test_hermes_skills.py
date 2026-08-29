from __future__ import annotations

from types import SimpleNamespace

import backend.src.hermes.skill_handlers  # noqa: F401
from backend.src.hermes.hermes_agent import build_hermes_plan
from backend.src.hermes.skill_executor import execute_routed_skills
from backend.src.hermes.skill_registry import get_skill, route_skills
from backend.src.hermes.skills.erp_revenue_skill import ErpRevenueGuardrailSkill
from backend.src.nlu.types import TaskType as NluTaskType
from backend.src.service.types import DecisionReport


def test_skill_registry_has_core_skills():
    assert get_skill("forecast-run") is not None
    assert get_skill("chart-render") is not None
    assert get_skill("erp-revenue-guardrail") is not None
    assert get_skill("erp-customer-sales") is not None


def test_route_skills_for_revenue_query():
    skills = route_skills("本月销售额是多少", ["erp"])
    assert "erp-sales-summary" in skills or any("sales" in s for s in skills)


def test_route_skills_for_forecast_query():
    skills = route_skills("裂解C5下周价格预测", ["forecast", "market"])
    assert "forecast-run" in skills


def test_build_hermes_plan_includes_forecast_run_skill():
    plan = build_hermes_plan("裂解C5下周价格预测走势")
    assert "forecast-run" in plan.agent_skills or "forecast" in plan.domains


def test_format_hermes_observations_includes_revenue_rule():
    from backend.src.hermes.execution import format_hermes_observations_for_prompt

    text = format_hermes_observations_for_prompt(
        {
            "observations": [
                {
                    "tool": "erp_sales_summary",
                    "status": "ok",
                    "summary": "sales",
                    "payload": {"total_revenue": 100.0},
                }
            ]
        }
    )
    assert "FAMOUNT" in text
    assert "never volume" in text.lower() or "never" in text


def test_erp_revenue_guardrail_blocks_volume_avg_pattern():
    skill = ErpRevenueGuardrailSkill()
    report = DecisionReport(
        raw_markdown="本月总销量100吨，加权均价7000元/吨，销售额约70万元。",
        status="success",
        task_type=NluTaskType.ERP_DATA_QUERY,
        context=SimpleNamespace(to_dict=lambda: {}),
    )
    state = {
        "user_query": "本月销售额是多少",
        "user_prefs": {"language": "zh"},
        "hermes_execution": {
            "observations": [
                {
                    "tool": "erp_sales_summary",
                    "payload": {"total_revenue": 650000.0},
                }
            ]
        },
    }
    updated = skill.run(report, state)
    assert "FAMOUNT" in updated.raw_markdown
    assert updated.enhancement_applied.get(skill.name) == "corrected:volume_avg_blocked"


def test_execute_routed_skills_returns_observations():
    plan = {
        "user_query": "输出后台预警和风险监控",
        "domains": ["executive"],
        "agent_skills": ["background-insights"],
        "product_ids": [],
        "normalized_query": {},
    }
    result = execute_routed_skills(plan, user_query=plan["user_query"])
    assert "observations" in result
    assert "background-insights" in result.get("executed_skills") or any(
        obs.get("skill_id") == "background-insights" for obs in result.get("observations") or []
    )
