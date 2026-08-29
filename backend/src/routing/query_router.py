"""Unified bilingual query router for EDIA chatbot paths."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, List, Literal, Optional

from backend.src.domain.edia_ontology import (
    change_days_for_metric,
    default_products_for_metric,
    is_market_data_source_label as ontology_market_data_label,
    resolve_metric,
    resolve_price_type,
    resolve_products,
    should_skip_llm_finalizer_for_metric,
    should_skip_rag_for_metric,
)
from backend.src.domain.product_resolver import (
    default_sinopec_listing_product_ids,
    extract_product_ids_in_text,
    looks_like_sinopec_listing_query,
)
from backend.src.nlu.intent_parser import _fast_intent, _is_conversational_query
from backend.src.prompts.task_prompt import TaskType
from backend.src.tools.db_capability_registry import (
    resolve_database_capability,
    should_bypass_database_capability_discovery,
    should_use_database_capability_discovery,
)

RoutePath = Literal[
    "deterministic_market",
    "deterministic_erp",
    "deterministic_discovery",
    "tool_then_llm",
    "conversational",
    "full_pipeline",
]


@dataclass
class RoutedQuery:
    path: RoutePath
    task_type: TaskType
    product_ids: List[str] = field(default_factory=list)
    price_types: List[str] = field(default_factory=list)
    metric_id: Optional[str] = None
    erp_capability: Optional[str] = None
    change_days: int = 7
    skip_rag: bool = False
    skip_llm_finalizer: bool = False
    confidence: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "task_type": str(getattr(self.task_type, "value", self.task_type)),
            "product_ids": list(self.product_ids),
            "price_types": list(self.price_types),
            "metric_id": self.metric_id,
            "erp_capability": self.erp_capability,
            "change_days": self.change_days,
            "skip_rag": self.skip_rag,
            "skip_llm_finalizer": self.skip_llm_finalizer,
            "confidence": self.confidence,
            "reason": self.reason,
        }


def _is_direct_market_price_lookup(query: str, product_ids: List[str]) -> bool:
    from backend.src.engine import decision_graph

    return decision_graph._is_direct_market_price_lookup(query, None) or bool(
        product_ids and _has_price_and_period_markers(query)
    )


def _has_price_and_period_markers(query: str) -> bool:
    q = (query or "").strip()
    ql = q.lower()
    price_zh = ["价格", "挂牌价", "挂牌", "市场价", "行情", "现价", "报价", "主流价"]
    price_en = ["price", "listing price", "listed price", "quote", "market price"]
    period_zh = ["本周", "本月", "环比", "涨跌", "多少", "如何", "变动", "方向", "公布", "是否"]
    period_en = ["this week", "weekly", "monthly", "change", "how", "released", "wow", "mom"]
    has_price = any(m in q for m in price_zh) or any(m in ql for m in price_en)
    has_period = any(m in q for m in period_zh) or any(m in ql for m in period_en)
    return has_price and has_period


def _is_multi_product_market_query(query: str) -> bool:
    from backend.src.engine import decision_graph

    return decision_graph._is_multi_product_price_delta_query(query, None)


def route_query(
    query: str,
    *,
    task_type: Any = None,
    hermes_domains: Iterable[str] | None = None,
    capabilities: Iterable[str] | None = None,
    agent_intent: Any = None,
    conversation_history: Optional[List[dict]] = None,
) -> RoutedQuery:
    q = str(query or "").strip()
    ql = q.lower()
    domain_set = {str(d or "").strip().lower() for d in (hermes_domains or []) if d}
    capability_set = {str(c or "").strip().lower() for c in (capabilities or []) if c}

    if task_type is None:
        task_type, confidence = _fast_intent(q)
    else:
        confidence = 0.85
        if not isinstance(task_type, TaskType):
            value = str(getattr(task_type, "value", task_type) or "").strip().lower()
            task_type = next(
                (t for t in TaskType if t.value == value),
                TaskType.MARKET_ANALYSIS_ONLY,
            )

    if _is_conversational_query(q, ql) and task_type == TaskType.CONVERSATIONAL:
        return RoutedQuery(
            path="conversational",
            task_type=task_type,
            confidence=confidence,
            reason="conversational_greeting_or_smalltalk",
        )

    metric_id = resolve_metric(q)
    product_ids = resolve_products(q)
    if not product_ids and metric_id:
        product_ids = default_products_for_metric(metric_id)
    if not product_ids and looks_like_sinopec_listing_query(q):
        metric_id = metric_id or "sinopec_monthly_listing"
        product_ids = default_sinopec_listing_product_ids()

    price_type = resolve_price_type(q, metric_id)
    price_types = [price_type] if price_type else []
    change_days = change_days_for_metric(metric_id, q)

    erp_cap = resolve_database_capability(q)
    erp_capability_id = erp_cap.capability_id if erp_cap else None

    # Deterministic ERP when capability matches
    if erp_capability_id and not should_bypass_database_capability_discovery(
        q,
        task_type=task_type,
        hermes_domains=domain_set,
        capabilities=capability_set,
        agent_intent=agent_intent,
    ):
        return RoutedQuery(
            path="deterministic_erp",
            task_type=TaskType.ERP_DATA_QUERY,
            product_ids=product_ids,
            erp_capability=erp_capability_id,
            skip_rag=True,
            skip_llm_finalizer=True,
            confidence=max(confidence, 0.88),
            reason=f"erp_capability:{erp_capability_id}",
        )

    # Database capability discovery (catalog / unmapped tables)
    if should_use_database_capability_discovery(
        q,
        task_type=task_type,
        hermes_domains=domain_set,
        capabilities=capability_set,
        agent_intent=agent_intent,
    ) and not should_bypass_database_capability_discovery(
        q,
        task_type=task_type,
        hermes_domains=domain_set,
        capabilities=capability_set,
        agent_intent=agent_intent,
    ):
        return RoutedQuery(
            path="deterministic_discovery",
            task_type=task_type,
            product_ids=product_ids,
            metric_id="database_catalog",
            skip_rag=True,
            skip_llm_finalizer=True,
            confidence=max(confidence, 0.8),
            reason="database_capability_discovery",
        )

    try:
        from backend.src.engine.decision_graph import _is_commercial_ops_advisory_query

        commercial_ops = _is_commercial_ops_advisory_query(q)
    except Exception:
        commercial_ops = False
    if not commercial_ops and agent_intent is not None:
        commercial_ops = str(getattr(agent_intent, "intent", "") or "").strip().lower() == "commercial_ops_advisory"
    if commercial_ops or "commercial_ops_advisory" in capability_set:
        return RoutedQuery(
            path="tool_then_llm",
            task_type=TaskType.MARKET_ANALYSIS_DECISION,
            product_ids=product_ids,
            price_types=price_types,
            metric_id=metric_id,
            change_days=change_days,
            skip_rag=False,
            skip_llm_finalizer=False,
            confidence=max(confidence, 0.88),
            reason="commercial_ops_advisory",
        )

    # Deterministic market: SINOPEC listing, direct price lookup, simple lookup
    sinopec = looks_like_sinopec_listing_query(q) or metric_id == "sinopec_monthly_listing"
    direct_market = (
        sinopec
        or _is_direct_market_price_lookup(q, product_ids)
        or task_type == TaskType.SIMPLE_LOOKUP
        or (
            ontology_market_data_label(q)
            and (product_ids or _is_multi_product_market_query(q))
        )
    )

    if direct_market and not erp_capability_id:
        skip_rag = should_skip_rag_for_metric(metric_id) or task_type == TaskType.SIMPLE_LOOKUP or sinopec
        skip_llm = should_skip_llm_finalizer_for_metric(metric_id) or sinopec or task_type == TaskType.SIMPLE_LOOKUP
        if _is_multi_product_market_query(q) and not sinopec:
            skip_rag = False
            skip_llm = False
        return RoutedQuery(
            path="deterministic_market",
            task_type=TaskType.SIMPLE_LOOKUP if sinopec or task_type == TaskType.SIMPLE_LOOKUP else task_type,
            product_ids=product_ids,
            price_types=price_types or (["sinopec_sh"] if sinopec else ["east_china"]),
            metric_id=metric_id or ("sinopec_monthly_listing" if sinopec else "east_china_spot"),
            change_days=change_days,
            skip_rag=skip_rag,
            skip_llm_finalizer=skip_llm,
            confidence=max(confidence, 0.9 if sinopec else 0.85),
            reason="deterministic_market_lookup",
        )

    if task_type in (TaskType.CONVERSATIONAL, TaskType.OUT_OF_DOMAIN):
        return RoutedQuery(
            path="conversational",
            task_type=task_type,
            product_ids=product_ids,
            confidence=confidence,
            reason="conversational_or_out_of_domain",
        )

    if task_type == TaskType.ERP_DATA_QUERY:
        return RoutedQuery(
            path="deterministic_erp",
            task_type=task_type,
            product_ids=product_ids,
            erp_capability=erp_capability_id,
            skip_rag=True,
            confidence=confidence,
            reason="erp_data_query_intent",
        )

    # Analysis / decision paths need tools + optional RAG + LLM
    if task_type in (
        TaskType.MARKET_ANALYSIS_DECISION,
        TaskType.MARKET_ANALYSIS_ONLY,
        TaskType.BOSS_BRIEFING,
        TaskType.FORECAST_ONLY,
        TaskType.WHAT_IF_SCENARIO,
    ):
        tool_first = bool(product_ids) or ontology_market_data_label(q)
        return RoutedQuery(
            path="tool_then_llm" if tool_first else "full_pipeline",
            task_type=task_type,
            product_ids=product_ids,
            price_types=price_types,
            metric_id=metric_id,
            change_days=change_days,
            skip_rag=False,
            skip_llm_finalizer=False,
            confidence=confidence,
            reason="analysis_with_tools" if tool_first else "full_pipeline",
        )

    return RoutedQuery(
        path="full_pipeline",
        task_type=task_type,
        product_ids=product_ids,
        price_types=price_types,
        metric_id=metric_id,
        change_days=change_days,
        confidence=confidence,
        reason="default_full_pipeline",
    )


def is_deterministic_erp_stream(routed: RoutedQuery) -> bool:
    return routed.path in ("deterministic_erp", "deterministic_discovery")


def stream_enhancements_for_route(routed: RoutedQuery) -> dict[str, str]:
    applied: dict[str, str] = {}
    if routed.path == "deterministic_erp":
        applied["deterministic_erp_stream"] = "true"
        applied["skip_finalizer"] = "true"
        if routed.erp_capability:
            from backend.src.tools.db_capability_registry import capability_privacy_metadata, get_database_capability

            cap = get_database_capability(routed.erp_capability)
            applied.update(capability_privacy_metadata(cap))
    elif routed.path == "deterministic_discovery":
        applied["database_capability_discovery"] = "true"
        applied["skip_finalizer"] = "true"
        applied["external_llm_blocked"] = "true"
    elif routed.path == "deterministic_market":
        applied["deterministic_market_lookup"] = "true"
        if routed.skip_llm_finalizer:
            applied["skip_finalizer"] = "true"
    return applied
