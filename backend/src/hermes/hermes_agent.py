from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any, Dict, List

from backend.src.domain.product_resolver import extract_product_ids_in_text
from backend.src.nlu.intent_parser import _fast_intent
from backend.src.prompts.task_prompt import TaskType
from backend.src.tools.db_capability_registry import (
    capability_privacy_metadata,
    is_market_data_source_label,
    resolve_database_capability,
    should_use_database_capability_discovery,
)
from backend.src.tools.erp_tools import resolve_period_metadata
from backend.src.hermes.agent_intent_planner import (
    AgentIntent,
    normalize_agent_intent,
    plan_agent_intent,
)


_FORECAST_MARKERS_ZH = (
    "预测",
    "预报",
    "未来",
    "下周",
    "后市",
    "趋势",
    "看涨",
    "看跌",
    "情景",
)
_FORECAST_MARKERS_EN = (
    "forecast",
    "prediction",
    "next week",
    "outlook",
    "trend",
    "scenario",
    "bull",
    "bear",
)
_ERP_MARKERS_ZH = ("erp", "订单", "销售", "销量", "销售额", "客户", "库存", "合同", "出库", "入库")
_ERP_MARKERS_EN = ("erp", "order", "sales", "revenue", "customer", "inventory", "stock", "contract", "shipment")
_MARKET_MARKERS_ZH = ("市场", "行情", "价格", "报价", "价差", "隆众", "中石化", "装置", "检修")
_MARKET_MARKERS_EN = ("market", "price", "pricing", "spread", "sinopec", "plant", "maintenance", "naphtha")
_BOSS_MARKERS_ZH = ("老板", "董事会", "经营会", "领导", "高层")
_BOSS_MARKERS_EN = ("boss", "board", "executive", "management")
_DOCX_MARKERS_ZH = ("word", "docx", "文档", "公文", "报告文档", "导出word")
_DOCX_MARKERS_EN = ("word", "docx", "document", "memo", "letter")
_PRESENTATION_MARKERS_ZH = ("ppt", "pptx", "演示", "幻灯片", "汇报材料", "路演", "演讲稿")
_PRESENTATION_MARKERS_EN = ("ppt", "pptx", "presentation", "slides", "slide deck", "deck")
_FRONTEND_MARKERS_ZH = ("前端", "界面", "页面", "仪表盘", "看板", "ui", "ux")
_FRONTEND_MARKERS_EN = ("frontend", "front-end", "ui", "ux", "dashboard", "page", "component")
_MYSQL_ENGINEERING_MARKERS_ZH = ("索引", "慢查询", "执行计划", "表结构", "迁移", "锁", "死锁")
_MYSQL_ENGINEERING_MARKERS_EN = ("index", "slow query", "explain", "schema", "migration", "locking", "deadlock", "innodb")
_MARKDOWN_MARKERS_ZH = ("markdown", "md", "转换", "转成", "格式转换")
_MARKDOWN_MARKERS_EN = ("markdown", "md", "convert", "converter", "format conversion")
_VISION_MARKERS_ZH = ("图片", "截图", "视觉", "识图", "图像")
_VISION_MARKERS_EN = ("image", "screenshot", "vision", "visual", "diagram")
_RESEARCH_MARKERS_ZH = ("深度研究", "行业研究", "行业报告", "研究报告", "外部调研")
_RESEARCH_MARKERS_EN = ("deep research", "industry research", "research report", "market research")


def _looks_like_commercial_ops_advisory(query: str, lower_query: str) -> bool:
    if not extract_product_ids_in_text(query):
        return False

    ops_markers_zh = ("库存", "报价", "发货", "出货", "发运", "价格", "降价", "跟跌", "节奏")
    ops_markers_en = (
        "inventory",
        "stock",
        "quote",
        "quotes",
        "pricing",
        "price",
        "shipment",
        "shipping",
        "delivery",
        "pace",
        "downward",
        "decrease",
        "flat",
        "7d",
    )
    action_markers_zh = (
        "应该",
        "需要",
        "要不要",
        "怎么做",
        "如何处理",
        "如何调整",
        "如何报价",
        "调整",
        "建议",
        "处理",
    )
    action_markers_en = (
        "should",
        "need to",
        "how should",
        "how to",
        "adjust",
        "handle",
        "recommend",
        "recommendation",
    )
    has_ops = _contains_any(query, lower_query, ops_markers_zh, ops_markers_en)
    has_action = _contains_any(query, lower_query, action_markers_zh, action_markers_en)
    return has_ops and has_action


@dataclass(frozen=True)
class HermesPlan:
    """Structured EDIA chat route plan (domains, capabilities, privacy).

    The Hermes *agent* runtime was removed; this plan builder remains for routing.
    It does not generate business facts. It decides which verified EDIA
    capabilities should run, which privacy policy applies, and which validation
    gates must pass before the answer reaches the UI.
    """

    user_query: str
    task_type: str
    confidence: float
    domains: List[str]
    product_ids: List[str]
    capabilities: List[str]
    tool_sequence: List[str]
    privacy: Dict[str, str]
    presentation_mode: str
    validation_gates: List[str]
    normalized_query: Dict[str, Any] = field(default_factory=dict)
    agent_skills: List[str] = field(default_factory=list)
    forecast_policy: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _contains_any(query: str, lower_query: str, zh_markers: tuple[str, ...], en_markers: tuple[str, ...]) -> bool:
    return any(marker in query for marker in zh_markers) or any(marker in lower_query for marker in en_markers)


_FOLLOW_UP_MARKERS_ZH = ("更多", "详细", "详情", "继续", "展开", "细分", "明细")
_FOLLOW_UP_MARKERS_EN = (
    "more detail",
    "more details",
    "details",
    "breakdown",
    "tell me more",
    "expand",
    "continue",
    "drill down",
)


def _history_text(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    content = item.get("content") or item.get("markdown") or ""
    return str(content or "").strip()


def _looks_like_follow_up_query(query: str) -> bool:
    text = str(query or "").strip()
    lower = text.lower()
    if not text:
        return False
    if _contains_any(text, lower, _FOLLOW_UP_MARKERS_ZH, _FOLLOW_UP_MARKERS_EN):
        return True
    return len(text.split()) <= 6 and any(word in lower for word in ("it", "that", "this", "same"))


def _latest_history_by_role(conversation_history: Any, role: str) -> str:
    if not isinstance(conversation_history, list):
        return ""
    for item in reversed(conversation_history):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() == role:
            text = _history_text(item)
            if text:
                return text
    return ""


def _period_from_history_text(text: str) -> str:
    match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\s+(?:to|至|-)\s+(20\d{2}-\d{2}-\d{2})\b", text)
    if match:
        return f"{match.group(1)} to {match.group(2)}"
    match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    return match.group(1) if match else ""


def resolve_followup_standalone_query(
    user_query: str,
    conversation_history: Any = None,
    prior_turn_state: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Resolve short follow-up text into a standalone EDIA business query."""
    query = str(user_query or "").strip()
    state = dict(prior_turn_state or {})
    latest_user = _latest_history_by_role(conversation_history, "user")
    latest_assistant = _latest_history_by_role(conversation_history, "assistant")
    period = str(state.get("period") or "").strip() or _period_from_history_text(latest_assistant)
    task_hint = str(state.get("task_type") or "").strip()
    if not task_hint:
        history_lower = f"{latest_user}\n{latest_assistant}".lower()
        if any(marker in history_lower for marker in ("sales", "revenue", "销售", "销量", "销售额")):
            task_hint = "erp_sales_summary"
        elif any(marker in history_lower for marker in ("order", "订单", "明细")):
            task_hint = "erp_order_details"
    is_follow_up = bool(latest_assistant and _looks_like_follow_up_query(query))
    standalone = query
    if is_follow_up:
        if task_hint in {"erp_sales_summary", "erp_order_details"} or "sales" in f"{latest_user} {latest_assistant}".lower():
            pieces = ["Show more detailed ERP sales breakdown"]
            if period:
                pieces.append(f"for {period}")
            pieces.append("using the same products and filters as the previous answer")
            pieces.append(f"because the user asked: {query}")
            standalone = ", ".join(pieces) + "."
        else:
            standalone = (
                f"Continue the previous answer with more detail. Previous user request: {latest_user}. "
                f"Current follow-up: {query}."
            )
    return {
        "is_follow_up": is_follow_up,
        "original_query": query,
        "standalone_query": standalone,
        "period": period,
        "task_type": task_hint,
        "latest_user": latest_user,
        "latest_assistant_excerpt": latest_assistant[:1200],
    }


def _is_agent_commercial_ops(agent_intent: AgentIntent | None) -> bool:
    return bool(agent_intent and agent_intent.intent == "commercial_ops_advisory")


def _resolve_domains(
    query: str,
    task_type: TaskType | None,
    agent_intent: AgentIntent | None = None,
) -> List[str]:
    lower_query = query.lower()
    domains: list[str] = []
    commercial_ops = _is_agent_commercial_ops(agent_intent) or _looks_like_commercial_ops_advisory(query, lower_query)
    if agent_intent:
        for domain in agent_intent.domains:
            if domain and domain not in domains:
                domains.append(domain)
    if not commercial_ops and resolve_database_capability(query) is not None:
        domains.append("database")
    elif not commercial_ops and should_use_database_capability_discovery(query, task_type=task_type, agent_intent=agent_intent):
        domains.append("database")
    if _contains_any(query, lower_query, _MYSQL_ENGINEERING_MARKERS_ZH, _MYSQL_ENGINEERING_MARKERS_EN):
        domains.append("database")
    if not commercial_ops and _contains_any(query, lower_query, _ERP_MARKERS_ZH, _ERP_MARKERS_EN):
        domains.append("erp")
    if (
        commercial_ops
        or extract_product_ids_in_text(query)
        or is_market_data_source_label(query)
        or _contains_any(query, lower_query, _MARKET_MARKERS_ZH, _MARKET_MARKERS_EN)
    ):
        domains.append("market")
    if task_type == TaskType.FORECAST_ONLY or _contains_any(query, lower_query, _FORECAST_MARKERS_ZH, _FORECAST_MARKERS_EN):
        domains.append("forecast")
    if task_type == TaskType.BOSS_BRIEFING or _contains_any(query, lower_query, _BOSS_MARKERS_ZH, _BOSS_MARKERS_EN):
        domains.append("executive")
    if _contains_any(query, lower_query, _DOCX_MARKERS_ZH, _DOCX_MARKERS_EN):
        domains.append("document")
    if _contains_any(query, lower_query, _PRESENTATION_MARKERS_ZH, _PRESENTATION_MARKERS_EN):
        domains.append("presentation")
    if _contains_any(query, lower_query, _FRONTEND_MARKERS_ZH, _FRONTEND_MARKERS_EN):
        domains.append("frontend")
    if not domains:
        domains.append("general")

    deduped: list[str] = []
    for domain in domains:
        if domain not in deduped:
            deduped.append(domain)
    return deduped


def _forecast_policy(query: str, domains: List[str], product_ids: List[str]) -> Dict[str, Any]:
    if "forecast" not in domains:
        return {}
    lower_query = query.lower()
    horizons: list[str] = []
    if any(marker in query for marker in ("未来", "下周", "后市")) or any(marker in lower_query for marker in ("next week", "outlook")):
        horizons.append("7d")
    if any(marker in query for marker in ("本月", "30天", "一个月")) or "30" in lower_query or "month" in lower_query:
        horizons.append("30d")
    if not horizons:
        horizons = ["3d", "7d", "30d"]

    return {
        "horizons": horizons,
        "policy_mode": "adaptive",
        "required_inputs": [
            "market_price_history",
            "forecast_accuracy_history",
            "forecast_policy_metrics",
            "diagnosis_signal",
        ],
        "quality_gates": [
            "price_history_freshness",
            "scenario_band_sanity",
            "recent_mape_available_or_disclosed",
            "bias_correction_applied_when_enough_samples",
            "forecast_numbers_locked_before_llm",
        ],
        "improvement_actions": [
            "select ARIMA/XGBoost/STL ensemble by horizon availability",
            "apply ForecastPolicyService adaptive bias and volatility correction",
            "separate verified history from forecast inference in presentation",
            "route high-MAPE products to wider bands and lower confidence language",
            "persist forecast snapshot for later backtest calibration",
        ],
        "products": product_ids,
    }


def _tool_sequence(
    domains: List[str],
    capability_id: str | None,
    capabilities: List[str] | None = None,
) -> List[str]:
    tools: list[str] = ["intent_router", "capability_registry", "privacy_gate"]
    capability_set = set(capabilities or [])
    if capability_id:
        tools.append(f"capability:{capability_id}")
    elif "database_capability_discovery" in capability_set:
        tools.append("database_capability_discovery")
    if "market" in domains:
        tools.append("market_data_snapshot")
    if "commercial_ops_advisory" in capability_set:
        tools.extend(["erp_inventory", "erp_shipments", "pricing_service"])
    if "forecast" in domains:
        tools.extend(["forecasting_service", "forecast_policy_service", "forecast_snapshot_recorder"])
    if "executive" in domains:
        tools.append("executive_briefing_presenter")
    if "document" in domains:
        tools.append("report_export:docx")
    if "presentation" in domains:
        tools.append("report_export:pptx_or_html")
    if "frontend" in domains:
        tools.append("frontend_design_review")
    tools.extend(["presentation_selector", "answer_validator", "harness_feedback"])
    return tools


def _presentation_mode(
    domains: List[str],
    capability_id: str | None,
    query: str = "",
) -> str:
    if "document" in domains and "presentation" in domains:
        return "multi_format_report_export"
    if "presentation" in domains:
        return "presentation_deck"
    if "document" in domains:
        return "docx_report"
    if "frontend" in domains:
        return "frontend_design_brief"
    if capability_id == "erp_order_details":
        return "erp_detail_table"
    try:
        from backend.src.engine.decision_graph import _looks_like_erp_report_query

        erp_report_query = _looks_like_erp_report_query(query)
    except Exception:
        erp_report_query = False
    if capability_id in {"erp_sales_summary", "erp_customer_sales"} or erp_report_query:
        return "erp_business_summary"
    if "forecast" in domains and "executive" in domains:
        return "executive_forecast_memo"
    if "forecast" in domains:
        return "forecast_scenarios"
    if "database" in domains:
        return "capability_discovery"
    if "market" in domains:
        return "market_business_advisory"
    return "general_business_answer"


def _agent_skill_routes(query: str, domains: List[str]) -> List[str]:
    from backend.src.hermes.skill_registry import legacy_agent_skill_ids, route_skills

    import backend.src.hermes.skill_handlers  # noqa: F401 — register handlers

    lower_query = query.lower()
    skills: list[str] = list(legacy_agent_skill_ids(query, domains))
    for skill_id in route_skills(query, domains):
        if skill_id not in skills:
            skills.append(skill_id)

    if "document" in domains:
        for item in ("docx", "minimax-docx"):
            if item not in skills:
                skills.append(item)
    if "presentation" in domains:
        for item in ("presentation-creator", "html-ppt", "pptx-generator"):
            if item not in skills:
                skills.append(item)
    if "frontend" in domains:
        for item in ("frontend-design", "frontend-dev"):
            if item not in skills:
                skills.append(item)
    if _contains_any(query, lower_query, _MARKDOWN_MARKERS_ZH, _MARKDOWN_MARKERS_EN):
        if "markdown-converter" not in skills:
            skills.append("markdown-converter")
    if _contains_any(query, lower_query, _VISION_MARKERS_ZH, _VISION_MARKERS_EN):
        if "vision-analysis" not in skills:
            skills.append("vision-analysis")
    if _contains_any(query, lower_query, _RESEARCH_MARKERS_ZH, _RESEARCH_MARKERS_EN):
        if "edia-market-research" not in skills:
            skills.append("edia-market-research")
    if (
        "database" in domains
        and _contains_any(query, lower_query, _MYSQL_ENGINEERING_MARKERS_ZH, _MYSQL_ENGINEERING_MARKERS_EN)
    ):
        if "mysql" not in skills:
            skills.append("mysql")
    if "forecast" in domains and "forecast-run" not in skills:
        skills.append("forecast-run")
    if any(marker in query for marker in ("销售额", "销售金额", "sales revenue", "revenue")):
        if "erp-revenue-guardrail" not in skills:
            skills.append("erp-revenue-guardrail")

    deduped: list[str] = []
    for skill in skills:
        if skill not in deduped:
            deduped.append(skill)
    return deduped


def _normalized_query_contract(
    query: str,
    capability_id: str | None,
    domains: List[str],
    product_ids: List[str],
    privacy: Dict[str, str],
    agent_intent: AgentIntent | None = None,
) -> Dict[str, Any]:
    contract: Dict[str, Any] = {
        "capability": capability_id,
        "domains": domains,
        "product_ids": product_ids,
        "privacy": {
            "data_sensitivity": privacy.get("data_sensitivity"),
            "external_llm_blocked": privacy.get("external_llm_blocked"),
        },
        "normalization_status": "pending",
    }
    if agent_intent is not None:
        contract["agent_intent"] = agent_intent.to_dict()
    if capability_id and ("erp" in domains or capability_id.startswith("erp_")):
        period = resolve_period_metadata(query)
        contract.update(period)
        contract["normalization_status"] = "canonical"
        contract["requires_structured_filters"] = True
    return contract


def _merge_agent_product_ids(product_ids: List[str], agent_intent: AgentIntent | None) -> List[str]:
    merged = list(product_ids or [])
    if agent_intent:
        for pid in agent_intent.product_ids:
            if pid and pid not in merged:
                merged.append(pid)
    return merged


def _agent_capabilities(agent_intent: AgentIntent | None) -> List[str]:
    if not agent_intent:
        return []
    capabilities = list(agent_intent.capabilities)
    if agent_intent.intent == "commercial_ops_advisory" and "commercial_ops_advisory" not in capabilities:
        capabilities.append("commercial_ops_advisory")
    if agent_intent.intent == "database_catalog" and "database_capability_discovery" not in capabilities:
        capabilities.append("database_capability_discovery")
    return capabilities


def build_hermes_plan(
    user_query: str,
    llm_service: Any = None,
    agent_intent: Any = None,
    conversation_history: Any = None,
    prior_turn_state: Dict[str, Any] | None = None,
) -> HermesPlan:
    followup = resolve_followup_standalone_query(
        user_query,
        conversation_history=conversation_history,
        prior_turn_state=prior_turn_state,
    )
    query = str(followup.get("standalone_query") or user_query or "").strip()
    task_type, confidence = _fast_intent(query)
    capability = resolve_database_capability(query)
    capability_id = capability.capability_id if capability is not None else None
    product_ids = extract_product_ids_in_text(query)
    resolved_agent_intent = normalize_agent_intent(agent_intent, source="provided")
    if resolved_agent_intent is None:
        resolved_agent_intent = plan_agent_intent(
            query,
            product_ids=product_ids,
            llm_service=llm_service,
        )
    product_ids = _merge_agent_product_ids(product_ids, resolved_agent_intent)
    domains = _resolve_domains(query, task_type, resolved_agent_intent)
    commercial_ops = _is_agent_commercial_ops(resolved_agent_intent) or _looks_like_commercial_ops_advisory(query, query.lower())

    privacy = capability_privacy_metadata(capability)
    if resolved_agent_intent and resolved_agent_intent.privacy:
        privacy.update(resolved_agent_intent.privacy)
    if commercial_ops:
        privacy = {
            "data_sensitivity": "market_internal_mixed",
            "answer_mode": "hybrid_commercial_ops_advisory",
            "external_llm_blocked": "false",
            **privacy,
        }
    elif should_use_database_capability_discovery(
        query,
        task_type=task_type,
        hermes_domains=domains,
        agent_intent=resolved_agent_intent,
    ):
        privacy = {
            "data_sensitivity": "internal_metadata",
            "answer_mode": "capability_discovery",
            "external_llm_blocked": "true",
        }
    elif "erp" in domains or capability_id:
        privacy.setdefault("data_sensitivity", "erp_confidential")
        privacy.setdefault("external_llm_blocked", "true")
    elif "forecast" in domains:
        privacy.setdefault("data_sensitivity", "market_internal_mixed")
        privacy.setdefault("external_llm_blocked", "false")

    validation_gates = [
        "non_empty_answer",
        "source_attribution_present",
        "no_unverified_numbers",
        "privacy_policy_respected",
    ]
    if "forecast" in domains:
        validation_gates.extend(
            [
                "forecast_horizons_present",
                "scenario_bands_valid",
                "confidence_matches_recent_accuracy",
            ]
        )
    if "erp" in domains or capability_id:
        validation_gates.extend(["erp_external_finalizer_blocked", "database_rows_or_availability_range"])
    if "document" in domains or "presentation" in domains:
        validation_gates.extend(["export_format_declared", "report_blocks_grounded_in_verified_data"])

    capabilities = [capability_id] if capability_id else []
    for cap in _agent_capabilities(resolved_agent_intent):
        if cap not in capabilities:
            capabilities.append(cap)
    if commercial_ops:
        if "commercial_ops_advisory" not in capabilities:
            capabilities.append("commercial_ops_advisory")
    if "forecast" in domains:
        capabilities.append("forecast_orchestration")
    if should_use_database_capability_discovery(
        query,
        task_type=task_type,
        hermes_domains=domains,
        capabilities=capabilities,
        agent_intent=resolved_agent_intent,
    ):
        if "database_capability_discovery" not in capabilities:
            capabilities.append("database_capability_discovery")
    if "document" in domains:
        capabilities.append("report_export_docx")
    if "presentation" in domains:
        capabilities.append("report_export_presentation")
    if "frontend" in domains:
        capabilities.append("frontend_design_guidance")
    normalized_query = _normalized_query_contract(query, capability_id, domains, product_ids, privacy, resolved_agent_intent)
    if followup.get("is_follow_up"):
        normalized_query["followup"] = {
            "original_query": followup.get("original_query"),
            "standalone_query": followup.get("standalone_query"),
            "period": followup.get("period"),
            "inherited_task_type": followup.get("task_type"),
        }
        if followup.get("period") and not normalized_query.get("period_source"):
            period = str(followup.get("period") or "")
            if " to " in period:
                start, end = period.split(" to ", 1)
                normalized_query["period_start"] = start
                normalized_query["period_end"] = end
            normalized_query["period"] = period
        if followup.get("period"):
            normalized_query["period_source"] = "conversation_history"
    agent_skills = _agent_skill_routes(query, domains)

    return HermesPlan(
        user_query=query,
        task_type=str(getattr(task_type, "value", task_type or "unknown")),
        confidence=round(float(confidence or 0.0), 4),
        domains=domains,
        product_ids=product_ids,
        capabilities=capabilities,
        tool_sequence=_tool_sequence(domains, capability_id, capabilities),
        privacy=privacy,
        presentation_mode=_presentation_mode(domains, capability_id, query),
        validation_gates=validation_gates,
        normalized_query=normalized_query,
        agent_skills=agent_skills,
        forecast_policy=_forecast_policy(query, domains, product_ids),
        notes=[
            note
            for note in [
                "Hermes plans and validates; the agent intent planner classifies business meaning before fallback routing.",
                "Forecast numbers must be locked before LLM narration.",
                "Follow-up turns inherit prior verified context before tool routing."
                if followup.get("is_follow_up")
                else "",
            ]
            if note
        ],
    )