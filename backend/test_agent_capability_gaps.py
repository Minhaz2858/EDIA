from __future__ import annotations

from dataclasses import dataclass

from backend.src.hermes.execution import execute_hermes_plan
from backend.src.hermes.hermes_agent import build_hermes_plan


@dataclass(frozen=True)
class CapabilityGapCase:
    capability: str
    edia_current: str
    claude_minimax_advantage: str
    target_for_edia: str


GENERAL_AGENT_GAPS = [
    CapabilityGapCase(
        capability="autonomous_browser_tasks",
        edia_current="limited",
        claude_minimax_advantage="can browse, inspect pages, and perform multi-step web workflows with broader tool use",
        target_for_edia="add a browser research/action agent with source capture and safety gates",
    ),
    CapabilityGapCase(
        capability="multimodal_document_understanding",
        edia_current="limited",
        claude_minimax_advantage="can reason over images, PDFs, screenshots, and mixed documents more generally",
        target_for_edia="add OCR/document/image ingestion that converts files into verified EDIA evidence blocks",
    ),
    CapabilityGapCase(
        capability="direct_artifact_creation_from_chat",
        edia_current="partial",
        claude_minimax_advantage="can produce richer standalone artifacts from a natural language task",
        target_for_edia="connect chat-planned report blocks directly to DOCX/PPTX/HTML export creation",
    ),
    CapabilityGapCase(
        capability="general_coding_agent_work",
        edia_current="limited",
        claude_minimax_advantage="can inspect code, edit files, run tests, and iterate as a software agent",
        target_for_edia="keep EDIA focused on business workflows unless an internal engineering agent is added",
    ),
    CapabilityGapCase(
        capability="long_context_general_reasoning",
        edia_current="limited",
        claude_minimax_advantage="can sustain broader long-context reasoning across arbitrary topics and files",
        target_for_edia="add persistent task memory and retrieval over validated company knowledge only",
    ),
]


EDIA_ADVANTAGE_CASES = [
    CapabilityGapCase(
        capability="live_erp_grounding",
        edia_current="strong",
        claude_minimax_advantage="requires custom integration before it can answer from Ecisco ERP data",
        target_for_edia="expand deterministic ERP tools while preserving privacy gates",
    ),
    CapabilityGapCase(
        capability="petrochemical_forecast_policy",
        edia_current="strong",
        claude_minimax_advantage="has broader reasoning but no native Ecisco forecast policy or MAPE gates",
        target_for_edia="keep locking forecast numbers before narrative and persist backtest snapshots",
    ),
]


def test_capability_gap_matrix_names_where_claude_and_minimax_are_stronger():
    capability_names = {case.capability for case in GENERAL_AGENT_GAPS}

    assert "autonomous_browser_tasks" in capability_names
    assert "multimodal_document_understanding" in capability_names
    assert "direct_artifact_creation_from_chat" in capability_names
    assert "general_coding_agent_work" in capability_names
    assert "long_context_general_reasoning" in capability_names
    assert all(case.edia_current in {"limited", "partial"} for case in GENERAL_AGENT_GAPS)
    assert all(case.target_for_edia for case in GENERAL_AGENT_GAPS)


def test_capability_gap_matrix_preserves_edia_business_advantages():
    capability_names = {case.capability for case in EDIA_ADVANTAGE_CASES}

    assert "live_erp_grounding" in capability_names
    assert "petrochemical_forecast_policy" in capability_names
    assert all(case.edia_current == "strong" for case in EDIA_ADVANTAGE_CASES)


def test_hermes_does_not_overclaim_general_browser_agent_capability():
    plan = build_hermes_plan("Open a browser, compare supplier websites, fill a form, and submit the request.").to_dict()

    assert plan["domains"] == ["general"]
    assert plan["agent_skills"] == []
    assert plan["capabilities"] == []
    assert plan["presentation_mode"] == "general_business_answer"


def test_hermes_routes_export_skills_but_marks_them_as_planned_observations():
    plan = build_hermes_plan("Create a DOCX and PPTX executive report for pentane blowing agent sales.").to_dict()
    execution = execute_hermes_plan(plan, user_query=plan["user_query"])
    observations = execution["observations"]
    skill_observation = next(item for item in observations if item["tool"] == "agent_skill_router")

    assert plan["presentation_mode"] == "multi_format_report_export"
    assert "docx" in plan["agent_skills"]
    assert "minimax-docx" in plan["agent_skills"]
    assert "presentation-creator" in plan["agent_skills"]
    assert "html-ppt" in plan["agent_skills"]
    assert "pptx-generator" in plan["agent_skills"]
    assert skill_observation["status"] == "planned"
    assert skill_observation["payload"]["agent_skills"] == plan["agent_skills"]


def test_hermes_remains_stronger_for_private_erp_order_details_than_general_agents():
    plan = build_hermes_plan("Please provide order details for May 11th to May 12th.").to_dict()

    assert "erp_order_details" in plan["capabilities"]
    assert plan["privacy"]["external_llm_blocked"] == "true"
    assert plan["normalized_query"]["period_start"] == "2026-05-11"
    assert plan["normalized_query"]["period_end"] == "2026-05-12"
    assert "erp_external_finalizer_blocked" in plan["validation_gates"]