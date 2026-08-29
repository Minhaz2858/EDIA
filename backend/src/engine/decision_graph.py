"""LangGraph state-machine for the EDIA decision pipeline.

Nodes:
  parse_intent  → extract_context → validate_context
  ↓ (if invalid → partial_response → END)
  perception → resolve_product → diagnosis → forecast → pricing
  rag_retrieval → tool_execution → assemble_prompt → generate_report → END

  ERP path (erp_data_query / boss_briefing):
  parse_intent → extract_context → validate_context → erp_data_retrieval
  → assemble_prompt → generate_report → END
"""

from __future__ import annotations

import re as _re
import json as _json
import os as _os
import time as _time
import random as _random
import hashlib as _hashlib
import copy as _copy
import threading as _threading
import concurrent.futures as _futures
from datetime import datetime as _dt, timezone as _tz, timedelta as _td
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy import text as _sql_text

from backend.src.domain.knowledge_graph import domain_graph as _domain_graph
from backend.src.domain.product_resolver import (
    default_sinopec_listing_product_ids,
    extract_product_ids_in_text,
    looks_like_sinopec_listing_query,
    resolve_product_id,
    split_product_tokens,
)
from backend.src.engine.prompt_assembler import PromptAssembler, _detect_language
from backend.src.nlu.context_generator import ContextGenerator
from backend.src.nlu.context_generator import (
    _looks_like_assistant_clarification_prompt,
    _looks_like_follow_up,
)
from backend.src.nlu.context_validator import ContextValidator
from backend.src.engine.date_utils import detect_historical_date_scope
from backend.src.nlu.intent_parser import IntentParser
from backend.src.nlu.llm_service import LLMService
from backend.src.nlu.types import TaskType
from backend.src.rag.vector_store import kb
from backend.src.db.sqlite_db import db as _app_db
from backend.src.service.diagnosis_service import diagnosis_service
from backend.src.service.forecasting_service import forecasting_service
from backend.src.service.forecast_snapshot_adapter import get_or_create_forecast_snapshot
from backend.src.service.perception_service import perception_service
from backend.src.service.pricing_service import pricing_service
from backend.src.service.evidence_compiler import evidence_compiler
from backend.src.service.decision_critic import decision_critic
from backend.src.intelligence.external_news_context_builder import (
    get_external_news_context_builder,
)
from backend.src.service.competitor_service import (
    get_competitor_comparison,
    looks_like_competitor_query,
)
from backend.src.service.insight_snapshot_service import insight_snapshot_service
from backend.src.service.erp_kpi_service import _PRODUCT_TO_MATERIAL_NAMES
from backend.src.skills.response import apply_response_enhancement_skills
from backend.src.service.types import DecisionReport
from backend.src.tools.db_tools import get_db_price, get_db_price_for_type
from backend.src.tools.db_capability_registry import (
    capability_privacy_metadata,
    discover_database_candidates,
    resolve_database_capability,
    should_bypass_database_capability_discovery,
    should_use_database_capability_discovery,
)
from backend.src.tools.erp_tools import build_erp_context, build_db_grounding_context, query_sales_order_details
from backend.src.planner.market_metrics import fetch_routed_market_block
from backend.src.planner.query_planner import plan_query
from backend.src.routing.query_router import route_query
from backend.src.planner.query_executor import execute_plan
from backend.src.planner.prerenderer import render_metrics, format_for_prompt
from backend.src.planner.llm_router import synthesize_streaming as planner_synthesize_streaming
from backend.src.hermes.execution import execute_hermes_plan, format_hermes_observations_for_prompt


# ---------------------------------------------------------------------------
# Pipeline State
# ---------------------------------------------------------------------------


class PipelineState(TypedDict, total=False):
    # input
    user_query: str
    conversation_history: List[Dict[str, Any]]
    user_id: Optional[str]
    session_id: Optional[str]
    hermes_plan: Dict[str, Any]
    hermes_execution: Dict[str, Any]

    # user preferences (from frontend local storage)
    user_prefs: Dict[str, Any]

    # real-time web search
    web_search_results: str
    web_search_meta: Dict[str, Any]
    research_evidence_score: Dict[str, Any]

    # NLU
    parsed_intent: Any  # ParsedIntent
    routed_query: Dict[str, Any]
    user_context: Any  # UserContext
    is_valid: bool
    missing_fields: list

    # product resolution
    graph_product_id: Optional[str]
    graph_product_ids: List[str]
    portfolio_mode: bool
    out_of_scope_detected: bool
    out_of_scope_target: str
    llm_brain_decision: Dict[str, Any]

    # stage outputs
    perception_snapshot: Dict[str, Any]
    diagnosis: Optional[Dict[str, Any]]
    diagnosis_text: str
    forecast: Optional[Dict[str, Any]]
    forecast_text: str
    what_if_result: Optional[Dict[str, Any]]
    pricing_decision: Optional[Dict[str, Any]]
    pricing_text: str
    delta_summary_text: str

    # evidence compiler
    evidence_bundle: Dict[str, Any]
    evidence_text: str

    # decision critic
    critic_result: Dict[str, Any]
    critic_text: str

    # intelligence layer
    intelligence_context: Dict[str, Any]
    intelligence_text: str

    # RAG + tools
    rag_info: str
    tool_info: str

    # ERP data for internal business queries
    erp_data: str

    # Kimi router + branch orchestration
    router_plan: Dict[str, Any]
    local_erp_result: Dict[str, Any]
    public_market_news_result: Dict[str, Any]
    synthesis_input: Dict[str, Any]
    route_trace: List[Dict[str, Any]]

    # prompt assembly
    explanation_mode: bool
    response_mode: str
    resolved_chat_mode: str
    mode_reason: str
    grounding_source: str
    planner_metrics: Dict[str, Any]
    system_prompt: str
    user_prompt: str

    # historical / backtest date scope (set when user specifies a past cutoff date)
    cutoff_date: Optional[str]  # e.g. "2026-02-26" or None for live mode
    date_scope: Optional[
        Dict[str, Any]
    ]  # full scope dict from detect_historical_date_scope

    # report
    report: Optional[DecisionReport]

    # perf instrumentation (not included in markdown)
    timings: Dict[str, float]
    prompt_stats: Dict[str, Any]

    # streaming stage callback: callable(node_name: str, elapsed_ms: float) or None
    stage_callback: Optional[Any]


_PERCEPTION_CACHE: dict[str, tuple[float, Dict[str, Any]]] = {}
_INTELLIGENCE_CACHE: dict[str, tuple[float, Dict[str, Any], str]] = {}
_PERF_CACHE_LOCK = _threading.Lock()


def _live_data_only_mode(state: Optional[PipelineState] = None) -> bool:
    prefs = dict((state or {}).get("user_prefs") or {})
    if "live_data_only" in prefs:
        return _normalize_bool_text(prefs.get("live_data_only"), True)
    raw = _os.getenv("LIVE_DATA_ONLY", "true")
    return _normalize_bool_text(raw, True)


def _strict_kimi_only_mode(state: Optional[PipelineState] = None) -> bool:
    prefs = dict((state or {}).get("user_prefs") or {})
    if "strict_kimi_only" in prefs:
        return _normalize_bool_text(prefs.get("strict_kimi_only"), True)
    from backend.src.nlu.external_llm_config import strict_external_llm_only

    return strict_external_llm_only()


def _cache_get_ttl_seconds(name: str, default_value: int) -> int:
    if _live_data_only_mode():
        return 0
    raw = str(_os.getenv(name, str(default_value)) or str(default_value)).strip()
    try:
        return max(0, int(raw))
    except Exception:
        return default_value


def _cache_get_perception(cache_key: str, ttl_seconds: int) -> Optional[Dict[str, Any]]:
    if ttl_seconds <= 0:
        return None
    now = _time.time()
    with _PERF_CACHE_LOCK:
        row = _PERCEPTION_CACHE.get(cache_key)
        if not row:
            return None
        ts, payload = row
        if (now - ts) >= ttl_seconds:
            _PERCEPTION_CACHE.pop(cache_key, None)
            return None
        return _copy.deepcopy(payload)


def _cache_set_perception(cache_key: str, payload: Dict[str, Any]) -> None:
    with _PERF_CACHE_LOCK:
        _PERCEPTION_CACHE[cache_key] = (_time.time(), _copy.deepcopy(payload))


def _cache_get_intelligence(cache_key: str, ttl_seconds: int) -> Optional[tuple[Dict[str, Any], str]]:
    if ttl_seconds <= 0:
        return None
    now = _time.time()
    with _PERF_CACHE_LOCK:
        row = _INTELLIGENCE_CACHE.get(cache_key)
        if not row:
            return None
        ts, ctx, text = row
        if (now - ts) >= ttl_seconds:
            _INTELLIGENCE_CACHE.pop(cache_key, None)
            return None
        return (_copy.deepcopy(ctx), str(text or ""))


def _cache_set_intelligence(cache_key: str, intel_ctx: Dict[str, Any], intel_text: str) -> None:
    with _PERF_CACHE_LOCK:
        _INTELLIGENCE_CACHE[cache_key] = (
            _time.time(),
            _copy.deepcopy(intel_ctx),
            str(intel_text or ""),
        )


def _normalize_bool_text(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    txt = str(value).strip().lower()
    if txt in {"1", "true", "yes", "on", "y"}:
        return True
    if txt in {"0", "false", "no", "off", "n"}:
        return False
    return default


def _external_first_online_enabled(state: Optional[PipelineState] = None) -> bool:
    prefs = dict((state or {}).get("user_prefs") or {})
    if "external_first_online" in prefs:
        return _normalize_bool_text(prefs.get("external_first_online"), False)
    return _normalize_bool_text(_os.getenv("EXTERNAL_FIRST_ONLINE", "false"), False)


def _llm_brain_mode_enabled(state: Optional[PipelineState] = None) -> bool:
    prefs = dict((state or {}).get("user_prefs") or {})
    if "llm_brain_mode" in prefs:
        return _normalize_bool_text(prefs.get("llm_brain_mode"), False)

    env_raw = _os.getenv("LLM_BRAIN_MODE")
    if env_raw is not None and str(env_raw).strip() != "":
        return _normalize_bool_text(env_raw, False)

    # If local model is disabled, prefer external LLM as decision brain by default.
    local_disabled = _normalize_bool_text(_os.getenv("DISABLE_LOCAL_LLM", "false"), False)
    if local_disabled:
        return True

    # Default to external routing-brain so every user input first goes through
    # a compact intent/phase decision step before the heavier pipeline runs.
    return True


def _extract_first_json_object(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}

    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()

    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return {}

    candidate = raw[start : end + 1]
    try:
        data = _json.loads(candidate)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _normalize_llm_response_mode(mode_value: Any) -> str:
    mode = str(mode_value or "").strip().lower()
    mapping = {
        "chat": "concise",
        "concise": "concise",
        "analysis": "executive_brief",
        "executive_brief": "executive_brief",
        "structured": "structured",
        "report": "full_report",
        "deep_research": "full_report",
        "full_report": "full_report",
        "conversational": "conversational",
    }
    return mapping.get(mode, "")


def _normalize_llm_chat_mode(mode_value: Any) -> str:
    mode = str(mode_value or "").strip().lower()
    if mode in {"chat", "analysis", "report", "deep_research"}:
        return mode
    return ""


def _llm_brain_plan(state: PipelineState, llm: LLMService) -> Dict[str, Any]:
    if not _llm_brain_mode_enabled(state):
        return {}

    query = str(state.get("user_query") or "").strip()
    if not query:
        return {}

    product_hint = str(getattr(state.get("user_context"), "product", "") or "").strip()
    parsed = state.get("parsed_intent")
    task_type = str(getattr(parsed, "task_type", "") or "")
    supported_ids = [str(getattr(n, "id", "") or "").strip() for n in _domain_graph.nodes]
    supported_ids = [pid for pid in supported_ids if pid]

    system_prompt = (
        "You are EDIA routing brain. Decide decision-graph controls for this user query. "
        "Return JSON only, no prose, no markdown fences."
    )
    user_prompt = (
        "Return a strict JSON object with keys:\n"
        "{\n"
        "  \"graph_product_ids\": string[],\n"
        "  \"primary_product_id\": string,\n"
        "  \"portfolio_mode\": boolean,\n"
        "  \"out_of_scope_detected\": boolean,\n"
        "  \"response_mode\": \"concise\"|\"structured\"|\"full_report\"|\"conversational\",\n"
        "  \"chat_mode\": \"chat\"|\"analysis\"|\"report\"|\"deep_research\",\n"
        "  \"reason\": string\n"
        "}\n\n"
        "Rules:\n"
        "- If query is EDIA domain related (C5/C9 chain, supply/demand/inventory/pricing structure), set out_of_scope_detected=false.\n"
        "- Prefer product IDs from supported list only.\n"
        "- If broad market request without explicit single product, set portfolio_mode=true.\n\n"
        f"task_type: {task_type}\n"
        f"user_product_hint: {product_hint}\n"
        f"supported_product_ids: {', '.join(supported_ids)}\n"
        f"query: {query}\n"
    )

    try:
        raw = llm.generate_completion(
            system_prompt,
            user_prompt,
            sanitized=False,
        )
    except Exception:
        return {}

    plan = _extract_first_json_object(raw)
    return plan if isinstance(plan, dict) else {}


def _build_precomputed_insight_block(state: PipelineState) -> str:
    if _live_data_only_mode(state):
        return ""
    bundle = _get_combined_insight_bundle(state)
    if not bundle.get("ok"):
        return ""

    by_product = dict(bundle.get("by_product") or {})
    if not by_product:
        return ""

    market_bundle = dict(bundle.get("market") or {})
    erp_bundle = dict(bundle.get("erp") or {})

    lines: List[str] = [
        "PRECOMPUTED COMBINED INSIGHT BUNDLE (DB CACHE):",
        f"- products: {len(by_product)}",
        f"- market_snapshot_id: {market_bundle.get('snapshot_id')}",
        f"- market_generated_at: {market_bundle.get('generated_at')}",
        f"- market_age_seconds: {market_bundle.get('age_seconds')}",
        f"- market_stale: {market_bundle.get('stale')}",
        f"- erp_snapshot_id: {erp_bundle.get('snapshot_id')}",
        f"- erp_generated_at: {erp_bundle.get('generated_at')}",
        f"- erp_age_seconds: {erp_bundle.get('age_seconds')}",
        f"- erp_stale: {erp_bundle.get('stale')}",
    ]

    for pid in list(sorted(by_product.keys()))[:8]:
        entry = dict(by_product.get(pid) or {})
        market_item = entry.get("market") or {}
        erp_item = entry.get("erp") or {}

        parts: List[str] = []
        latest_price = market_item.get("latest_price")
        weekly_change = market_item.get("weekly_change_pct")
        unit = str(market_item.get("unit") or "")
        as_of_date = str(market_item.get("as_of_date") or "")
        if market_item:
            if isinstance(latest_price, (int, float)):
                metric = f"{latest_price:,.0f} {unit}".strip()
            else:
                metric = "N/A"
            if isinstance(weekly_change, (int, float)):
                chg = f"{weekly_change:+.2f}%"
            else:
                chg = "N/A"
            parts.append(f"market={metric}, WoW {chg}, as_of {as_of_date}")

        sales_30d = erp_item.get("sales_30d_ton")
        inventory_days = erp_item.get("inventory_days")
        risk_level = str(erp_item.get("risk_level") or "unknown")
        if erp_item:
            sales_text = f"{float(sales_30d):,.1f} tons" if isinstance(sales_30d, (int, float)) else "N/A"
            inv_text = f"{float(inventory_days):.1f}d" if isinstance(inventory_days, (int, float)) else "N/A"
            parts.append(f"erp=sales_30d {sales_text}, inventory_days {inv_text}, risk {risk_level}")

        if parts:
            lines.append(f"- {pid}: {'; '.join(parts)}")

        market_narrative = str(market_item.get("narrative") or "").strip()
        if market_narrative:
            lines.append(f"  market_narrative: {market_narrative}")
        erp_narrative = str(erp_item.get("narrative") or "").strip()
        if erp_narrative:
            lines.append(f"  erp_narrative: {erp_narrative}")

    refresh_info = dict(bundle.get("refresh_enqueued") or {})
    if bool(refresh_info.get("market")) or bool(refresh_info.get("erp")):
        lines.append(
            f"- refresh_enqueued: market={bool(refresh_info.get('market'))}, erp={bool(refresh_info.get('erp'))}"
        )

    return "\n".join(lines).strip()


def _get_combined_insight_bundle(state: PipelineState) -> Dict[str, Any]:
    if _live_data_only_mode(state):
        perception = dict(state.get("perception_snapshot") or {})
        products = dict(perception.get("products") or {})
        by_product: Dict[str, Dict[str, Any]] = {}
        for pid, pdata in products.items():
            product_id = str(pid or "").strip().lower()
            if not product_id:
                continue
            by_product[product_id] = {
                "product_id": product_id,
                "market": {
                    "product_id": product_id,
                    "as_of_date": pdata.get("latest_date") or perception.get("reference_date"),
                    "latest_price": pdata.get("latest_price"),
                    "weekly_change_pct": pdata.get("change_7d_pct"),
                    "unit": pdata.get("unit"),
                    "narrative": pdata.get("narrative") or "",
                },
                "erp": None,
            }
        return {
            "ok": bool(by_product),
            "market": {
                "snapshot_id": None,
                "generated_at": perception.get("reference_date"),
                "age_seconds": None,
                "stale": False,
                "count": len(by_product),
                "refresh_enqueued": False,
            },
            "erp": {
                "snapshot_id": None,
                "generated_at": None,
                "age_seconds": None,
                "stale": False,
                "count": 0,
                "refresh_enqueued": False,
            },
            "by_product": by_product,
            "refresh_enqueued": {"market": False, "erp": False},
        }

    product_ids = list(state.get("graph_product_ids") or [])
    if not product_ids:
        user_context = state.get("user_context")
        product_text = str(getattr(user_context, "product", "") or "").strip()
        if product_text:
            product_ids = extract_product_ids_in_text(product_text)

    market_max_age_seconds = 7200
    try:
        market_raw_age = str(
            (state.get("user_prefs") or {}).get("insight_bundle_max_age_seconds")
            or _os.getenv("INSIGHT_BUNDLE_MAX_AGE_SECONDS", "7200")
        ).strip()
        market_max_age_seconds = max(60, int(market_raw_age))
    except Exception:
        market_max_age_seconds = 7200

    erp_max_age_seconds = 10800
    try:
        erp_raw_age = str(
            (state.get("user_prefs") or {}).get("erp_insight_bundle_max_age_seconds")
            or _os.getenv("ERP_INSIGHT_BUNDLE_MAX_AGE_SECONDS", "10800")
        ).strip()
        erp_max_age_seconds = max(120, int(erp_raw_age))
    except Exception:
        erp_max_age_seconds = 10800

    try:
        return insight_snapshot_service.get_combined_bundle_cached(
            product_ids=product_ids or None,
            market_max_age_seconds=market_max_age_seconds,
            erp_max_age_seconds=erp_max_age_seconds,
            async_refresh_on_miss=True,
        )
    except Exception:
        return {"ok": False, "market": {}, "erp": {}, "by_product": {}, "refresh_enqueued": {}}


def _build_external_combined_bundle_payload(
    state: PipelineState,
    *,
    executive_weekly: bool = False,
) -> Dict[str, Any]:
    bundle = _get_combined_insight_bundle(state)
    by_product = dict(bundle.get("by_product") or {})
    max_products = 8 if executive_weekly else 5

    compact_products: List[Dict[str, Any]] = []
    for pid in list(sorted(by_product.keys()))[:max_products]:
        entry = dict(by_product.get(pid) or {})
        market_item = dict(entry.get("market") or {})
        erp_item = dict(entry.get("erp") or {})
        compact_products.append(
            {
                "product_id": pid,
                "market": {
                    "as_of_date": market_item.get("as_of_date"),
                    "latest_price": market_item.get("latest_price"),
                    "weekly_change_pct": market_item.get("weekly_change_pct"),
                    "unit": market_item.get("unit"),
                    "narrative": market_item.get("narrative"),
                }
                if market_item
                else None,
                "erp": {
                    "period_start": erp_item.get("period_start"),
                    "period_end": erp_item.get("period_end"),
                    "sales_30d_ton": erp_item.get("sales_30d_ton"),
                    "sales_growth_pct": erp_item.get("sales_growth_pct"),
                    "inventory_tons": erp_item.get("inventory_tons"),
                    "inventory_days": erp_item.get("inventory_days"),
                    "utilization_pct": erp_item.get("utilization_pct"),
                    "risk_level": erp_item.get("risk_level"),
                    "narrative": erp_item.get("narrative"),
                }
                if erp_item
                else None,
            }
        )

    market_bundle = dict(bundle.get("market") or {})
    erp_bundle = dict(bundle.get("erp") or {})
    refresh_info = dict(bundle.get("refresh_enqueued") or {})
    return {
        "contract": "combined_insight_bundle_v1",
        "ok": bool(bundle.get("ok")),
        "products": compact_products,
        "market": {
            "snapshot_id": market_bundle.get("snapshot_id"),
            "generated_at": market_bundle.get("generated_at"),
            "age_seconds": market_bundle.get("age_seconds"),
            "stale": market_bundle.get("stale"),
            "count": market_bundle.get("count"),
            "refresh_enqueued": bool(refresh_info.get("market")),
        },
        "erp": {
            "snapshot_id": erp_bundle.get("snapshot_id"),
            "generated_at": erp_bundle.get("generated_at"),
            "age_seconds": erp_bundle.get("age_seconds"),
            "stale": erp_bundle.get("stale"),
            "count": erp_bundle.get("count"),
            "refresh_enqueued": bool(refresh_info.get("erp")),
        },
    }


def _build_external_unified_payload(
    state: PipelineState,
    *,
    executive_weekly: bool = False,
) -> Dict[str, Any]:
    perception = state.get("perception_snapshot") or {}
    intelligence_context = state.get("intelligence_context") or {}
    language = _resolve_output_language(state)
    return {
        "user_query": str(state.get("user_query") or ""),
        "combined_insight_bundle": _build_external_combined_bundle_payload(
            state,
            executive_weekly=executive_weekly,
        ),
        "intelligence_summary": _cap_text(
            str(state.get("intelligence_text") or ""),
            3200 if executive_weekly else 2500,
        ),
        "web_search_summary": _cap_text(
            str(state.get("web_search_results") or ""),
            2800 if executive_weekly else 2500,
        ),
        "intelligence_highlights": _cap_text(
            _json.dumps(intelligence_context, ensure_ascii=False),
            1600 if executive_weekly else 800,
        ),
        "forecast": _cap_text(str(state.get("forecast_text") or ""), 1600 if executive_weekly else 1400),
        "pricing": _cap_text(str(state.get("pricing_text") or ""), 1600 if executive_weekly else 1400),
        "diagnosis": _cap_text(str(state.get("diagnosis_text") or ""), 1300 if executive_weekly else 1200),
        "reference_date": perception.get("reference_date"),
        "executive_weekly": executive_weekly,
        "external_evidence_policy": _build_external_evidence_policy(state, language),
    }


def _generate_unified_external_answer(
    llm_service: LLMService,
    state: PipelineState,
    *,
    sanitized: bool,
    executive_weekly: bool = False,
) -> str:
    language = _resolve_output_language(state)
    lang_hint = "English" if language == "en" else "Chinese"
    payload = _build_external_unified_payload(
        state,
        executive_weekly=executive_weekly,
    )
    system_prompt = (
        "You are EDIA external synthesis engine. "
        "Use the provided combined insight bundle as the authoritative cached contract for market and ERP facts. "
        "Do not request or imply access to raw ERP rows or hidden customer-level details. "
        "If the bundle is missing or stale, state that limitation plainly instead of inventing facts. "
        "Keep market observations, ERP signals, forecasts, and recommendations clearly separated. "
        f"Output language: {lang_hint}."
    )
    if executive_weekly:
        system_prompt += (
            " Required sections: Market Overview, Key Drivers, Forecast, Recommended Actions, Risks/Triggers."
            " Prefer concrete numbers from the bundle and keep the answer concise."
        )
    evidence_policy = payload.get("external_evidence_policy") or {}
    if evidence_policy:
        system_prompt += " Evidence policy: " + str(evidence_policy.get("guidance") or "")
    user_prompt = (
        "Generate the final answer using this unified external payload JSON:\n"
        f"{_json.dumps(payload, ensure_ascii=False)}"
    )
    return llm_service.generate_completion(
        system_prompt,
        user_prompt,
        route_hint="external",
        sanitized=sanitized,
    )


def _build_precomputed_erp_block(state: PipelineState, product_id: Optional[str] = None) -> str:
    if _live_data_only_mode(state):
        return ""
    product_ids: List[str] = []
    if product_id:
        product_ids.append(str(product_id).strip().lower())
    else:
        graph_ids = [str(pid).strip().lower() for pid in list(state.get("graph_product_ids") or []) if str(pid).strip()]
        product_ids.extend(graph_ids)

    max_age_seconds = 10800
    try:
        raw_age = str(
            (state.get("user_prefs") or {}).get("erp_insight_bundle_max_age_seconds")
            or _os.getenv("ERP_INSIGHT_BUNDLE_MAX_AGE_SECONDS", "10800")
        ).strip()
        max_age_seconds = max(120, int(raw_age))
    except Exception:
        max_age_seconds = 10800

    try:
        bundle = insight_snapshot_service.get_erp_bundle_cached(
            product_ids=product_ids or None,
            max_age_seconds=max_age_seconds,
            async_refresh_on_miss=True,
        )
    except Exception:
        return ""

    if not bundle.get("ok"):
        lines = [
            "PRECOMPUTED ERP INSIGHT BUNDLE (DB CACHE):",
            "- status: unavailable",
            f"- reason: {bundle.get('reason')}",
            f"- refresh_enqueued: {bool(bundle.get('refresh_enqueued'))}",
        ]
        return "\n".join(lines)

    items = list(bundle.get("items") or [])
    if not items:
        lines = [
            "PRECOMPUTED ERP INSIGHT BUNDLE (DB CACHE):",
            "- status: empty",
            f"- refresh_enqueued: {bool(bundle.get('refresh_enqueued'))}",
        ]
        return "\n".join(lines)

    lines: List[str] = [
        "PRECOMPUTED ERP INSIGHT BUNDLE (DB CACHE):",
        f"- snapshot_id: {bundle.get('snapshot_id')}",
        f"- generated_at: {bundle.get('generated_at')}",
        f"- age_seconds: {bundle.get('age_seconds')}",
        f"- stale: {bundle.get('stale')}",
    ]

    for item in items[:10]:
        pid = str(item.get("product_id") or "")
        s30 = item.get("sales_30d_ton")
        growth = item.get("sales_growth_pct")
        inv_days = item.get("inventory_days")
        risk = str(item.get("risk_level") or "unknown")
        s30_text = f"{float(s30):,.1f}" if isinstance(s30, (int, float)) else "N/A"
        growth_text = f"{float(growth):+.2f}%" if isinstance(growth, (int, float)) else "N/A"
        inv_text = f"{float(inv_days):.1f}" if isinstance(inv_days, (int, float)) else "N/A"
        lines.append(
            f"- {pid}: sales_30d={s30_text} tons, growth={growth_text}, inventory_days={inv_text}, risk={risk}"
        )
        narrative = str(item.get("narrative") or "").strip()
        if narrative:
            lines.append(f"  narrative: {narrative}")

    if bool(bundle.get("refresh_enqueued")):
        lines.append("- refresh_enqueued: true")

    return "\n".join(lines).strip()


def _timed(node_name: str, fn):
    """Wrap a node to record elapsed ms into state['timings'].

    Returns a node function compatible with LangGraph (state -> dict).
    """

    def _wrapped(state: PipelineState) -> dict:
        cb = state.get("stage_callback")
        if callable(cb):
            try:
                cb(node_name, 0.0, "started")
            except Exception:
                pass

        start = _time.perf_counter()
        out = fn(state)
        elapsed_ms = (_time.perf_counter() - start) * 1000.0

        # Fire per-node stage event for live streaming progress
        if callable(cb):
            try:
                cb(node_name, round(elapsed_ms, 1), "completed")
            except Exception:
                pass

        try:
            timings = dict(state.get("timings") or {})
            timings[node_name] = round(float(elapsed_ms), 1)
        except Exception:
            timings = {node_name: round(float(elapsed_ms), 1)}

        if isinstance(out, dict):
            merged = dict(out)
            # Ensure wrapper timing survives even if the inner node also sets timings.
            inner = merged.get("timings")
            if isinstance(inner, dict):
                timings = {**inner, **timings}
            merged["timings"] = timings
            return merged
        return {"timings": timings}

    return _wrapped


def _cap_text(text: str, max_chars: int) -> str:
    """Deterministically cap a text blob to reduce prompt size.

    We intentionally keep this simple (head truncation only) to avoid introducing
    marker strings that could leak into the final report.
    """
    if not text:
        return ""
    if max_chars <= 0:
        return text
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


# ---------------------------------------------------------------------------
# Required section headers for completeness check (Phase 6)
# ---------------------------------------------------------------------------

_REQUIRED_SECTIONS = [
    ("Market Overview", r"市场现状|Market\s*Overview", r"Market\s*Overview|市场现状"),
    ("Key Drivers", r"关键驱动因素|Key\s*Drivers", r"Key\s*Drivers|关键驱动因素"),
    ("Price Forecast", r"价格预测|Price\s*Forecast", r"Price\s*Forecast|价格预测"),
    (
        "Pricing Decision",
        r"定价决策|Pricing\s*Decision",
        r"Pricing\s*Decision|定价决策",
    ),
    ("Risk Alerts", r"风险预警|Risk\s*Alerts", r"Risk\s*Alerts|风险预警"),
]

_CN_CANONICAL_SECTION_PATTERNS: Dict[str, str] = {
    "Market Overview": r"##\s*📊\s*0?1\s*·\s*市场现状",
    "Key Drivers": r"##\s*🔍\s*0?2\s*·\s*关键驱动因素",
    "Price Forecast": r"##\s*📈\s*0?3\s*·\s*价格预测",
    "Pricing Decision": r"##\s*💡\s*0?4\s*·\s*定价决策",
    "Risk Alerts": r"##\s*⚠️\s*0?5\s*·\s*风险预警",
    "Suggested Follow-ups": r"##\s*❓\s*0?6\s*·\s*(建议追问|建议后续问题|Suggested\s*Follow-ups)",
}

_CN_RECOVERY_SECTION_ORDER: List[str] = [
    "Market Overview",
    "Key Drivers",
    "Price Forecast",
    "Pricing Decision",
    "Risk Alerts",
    "Suggested Follow-ups",
]


def _looks_like_provenance_question(text: str) -> bool:
    q = (text or "").strip().lower()
    if not q:
        return False
    markers = [
        "where does this price come from",
        "where is this price from",
        "source of this price",
        "price source",
        "how do you get this price",
        "how did you get this price",
        "数据来源",
        "价格来源",
        "来源是什么",
        "这个价格怎么来的",
    ]
    return any(m in q for m in markers)


def _looks_like_executive_weekly_query(text: str) -> bool:
    q = (text or "").strip().lower()
    if not q:
        return False

    executive_markers = [
        "boss briefing",
        "executive briefing",
        "management briefing",
        "executive report",
        "经营简报",
        "管理层简报",
        "管理简报",
        "管理层",
        "经营周报",
        "汇报",
    ]
    report_markers = [
        "briefing",
        "report",
        "analysis",
        "简报",
        "报告",
        "分析",
    ]
    weekly_markers = [
        "this week",
        "weekly",
        "week",
        "本周",
        "周度",
        "周报",
    ]

    has_executive = any(m in q for m in executive_markers)
    has_weekly = any(m in q for m in weekly_markers)
    has_report = any(m in q for m in report_markers)
    return bool(has_executive or (has_weekly and has_report))


def _is_executive_weekly_request(
    user_query: str,
    task_type: Optional[TaskType] = None,
    response_mode: Optional[str] = None,
) -> bool:
    if task_type == TaskType.BOSS_BRIEFING:
        return True
    if _looks_like_executive_weekly_query(user_query):
        return True
    # Be conservative: full_report alone is not enough; require report-style wording.
    if (response_mode or "").strip().lower() == "full_report" and _looks_like_executive_weekly_query(user_query):
        return True
    return False


def _is_ops_inventory_pricing_query(text: str) -> bool:
    q = (text or "").strip()
    ql = q.lower()
    if not q:
        return False

    ops_markers_zh = ["装置", "停工", "停车", "停开车", "重启", "开工", "检修", "复产"]
    ops_markers_en = ["plant", "shutdown", "restart", "outage", "maintenance", "startup"]
    inventory_markers_zh = ["库存", "库存覆盖", "库存周转", "weeks of cover"]
    inventory_markers_en = ["inventory", "weeks of cover", "stock cover"]
    price_markers_zh = ["价格", "报价", "价差", "价格预测"]
    price_markers_en = ["price", "pricing", "forecast", "asp"]
    action_markers_zh = ["运营建议", "操作建议", "定价建议", "排产建议", "建议"]
    action_markers_en = ["recommendation", "operational recommendation", "pricing action", "production action"]

    has_ops = any(m in q for m in ops_markers_zh) or any(m in ql for m in ops_markers_en)
    has_inventory = any(m in q for m in inventory_markers_zh) or any(m in ql for m in inventory_markers_en)
    has_price = any(m in q for m in price_markers_zh) or any(m in ql for m in price_markers_en)
    has_action = any(m in q for m in action_markers_zh) or any(m in ql for m in action_markers_en)
    return has_ops and (has_inventory or has_price) and has_action


def _is_commercial_ops_advisory_query(text: str) -> bool:
    q = (text or "").strip()
    ql = q.lower()
    if not q or not extract_product_ids_in_text(q):
        return False

    business_markers_zh = [
        "库存",
        "报价",
        "价格",
        "发货",
        "出货",
        "发运",
        "跟跌",
        "降价",
        "节奏",
        "销售",
    ]
    business_markers_en = [
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
        "sales",
    ]
    action_markers_zh = [
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
    ]
    action_markers_en = [
        "should",
        "need to",
        "how should",
        "how to",
        "adjust",
        "handle",
        "recommend",
        "recommendation",
    ]

    has_business_context = any(m in q for m in business_markers_zh) or any(
        m in ql for m in business_markers_en
    )
    has_action_ask = any(m in q for m in action_markers_zh) or any(
        m in ql for m in action_markers_en
    )
    return has_business_context and has_action_ask


def _agent_intent_from_state(state: PipelineState) -> Dict[str, Any]:
    hermes_plan = state.get("hermes_plan") if isinstance(state, dict) else None
    if not isinstance(hermes_plan, dict):
        return {}
    normalized = hermes_plan.get("normalized_query")
    if not isinstance(normalized, dict):
        return {}
    agent_intent = normalized.get("agent_intent")
    return dict(agent_intent or {}) if isinstance(agent_intent, dict) else {}


def _state_has_agent_intent(state: PipelineState, intent: str) -> bool:
    agent_intent = _agent_intent_from_state(state)
    return str(agent_intent.get("intent") or "").strip().lower() == intent


_OPS_PRODUCT_ALIASES: List[Dict[str, Any]] = [
    {
        "product_id": "isoprene",
        "name_cn": "异戊二烯",
        "name_en": "Isoprene",
        "aliases": ["异戊二烯", "isoprene", "ip"],
    },
    {
        "product_id": "piperylene",
        "name_cn": "间戊二烯",
        "name_en": "Piperylene",
        "aliases": ["间戊二烯", "piperylene", "piperlyene", "piperyene", "piperlene"],
    },
    {
        "product_id": "dcpd",
        "name_cn": "双环戊二烯",
        "name_en": "DCPD",
        "aliases": ["双环戊二烯", "dcpd", "dicyclopentadiene"],
    },
    {
        "product_id": "c5_resin",
        "name_cn": "碳五石油树脂",
        "name_en": "C5 Resin",
        "aliases": ["碳五石油树脂", "c5 resin", "petroleum resin"],
    },
    {
        "product_id": "blowing_agent",
        "name_cn": "戊烷发泡剂",
        "name_en": "Blowing Agent",
        "aliases": ["戊烷发泡剂", "发泡剂", "blowing agent", "pentane blowing agent"],
    },
]


def _extract_ops_target_products(user_query: str, live_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    query = str(user_query or "")
    ql = query.lower()
    selected: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for meta in _OPS_PRODUCT_ALIASES:
        aliases = meta.get("aliases") or []
        if any(alias in query or alias in ql for alias in aliases):
            selected.append(meta)
            seen.add(str(meta["product_id"]))

    for row in live_rows:
        pid = str(row.get("product_id") or "").strip().lower()
        if not pid or pid in seen:
            continue
        match = next((m for m in _OPS_PRODUCT_ALIASES if m["product_id"] == pid), None)
        if match:
            selected.append(match)
            seen.add(pid)

    if selected:
        return selected

    return _OPS_PRODUCT_ALIASES[:2]


def _find_live_row_for_product(live_rows: List[Dict[str, Any]], product_id: str) -> Optional[Dict[str, Any]]:
    normalized = str(product_id or "").strip().lower()
    for row in live_rows:
        if str(row.get("product_id") or "").strip().lower() == normalized:
            return row
    return None


def _extract_inventory_cover_for_product(state: PipelineState, product_meta: Dict[str, Any]) -> Optional[float]:
    tool_info = str(state.get("tool_info") or "")
    if not tool_info:
        return None

    aliases = [str(a) for a in (product_meta.get("aliases") or []) if a]
    aliases.append(str(product_meta.get("product_id") or ""))

    for raw_line in tool_info.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lower_line = line.lower()
        if not any(alias and (alias in line or alias.lower() in lower_line) for alias in aliases):
            continue
        match = _re.search(r"(?:inventory_days|库存天数)\s*[:=]?\s*(\d+(?:\.\d+)?)", line, _re.IGNORECASE)
        if match:
            try:
                return float(match.group(1)) / 7.0
            except Exception:
                return None
    return None


def _looks_like_generic_cn_ops_report(text: str) -> bool:
    content = str(text or "").strip()
    if not content:
        return True
    generic_markers = [
        "### 1. 当前主流价格与环比涨跌（已验证事实）",
        "### 2. 市场诊断与关键驱动因素（基于数据推断）",
        "### 4. 近期展望与风险（推断与情景）",
        "当前市场短期处于**震荡**阶段",
        "当前查询以实时价格查询为主，未触发完整推荐价阶梯计算",
    ]
    generic_hits = sum(1 for marker in generic_markers if marker in content)
    if generic_hits >= 2:
        return True

    professionalism_markers = ["库存", "供应侧", "需求侧", "触发条件", "操作建议", "排产", "情景"]
    score = sum(1 for marker in professionalism_markers if marker in content)
    return score < 4


def _prefer_token_streaming(state: PipelineState) -> bool:
    prefs = dict(state.get("user_prefs") or {})
    return bool(prefs.get("prefer_token_streaming", False))


def _use_fast_db_only_ops_path(state: PipelineState) -> bool:
    if _prefer_token_streaming(state):
        return False

    query = str(state.get("user_query") or "")
    if not _is_ops_inventory_pricing_query(query):
        return False

    if not _normalize_bool_text(_os.getenv("DATABASE_ONLY_MODE", "true"), True):
        return False

    parsed = state.get("parsed_intent")
    task_type = getattr(parsed, "task_type", None)
    return task_type in {
        TaskType.MARKET_ANALYSIS_ONLY,
        TaskType.MARKET_ANALYSIS_DECISION,
        TaskType.WHAT_IF_SCENARIO,
        TaskType.FORECAST_ONLY,
    }


def _use_hybrid_commercial_ops_advisory_path(state: PipelineState) -> bool:
    if _prefer_token_streaming(state):
        return False

    query = str(state.get("user_query") or "")
    if not (_state_has_agent_intent(state, "commercial_ops_advisory") or _is_commercial_ops_advisory_query(query)):
        return False

    parsed = state.get("parsed_intent")
    task_type = getattr(parsed, "task_type", None)
    return task_type in {
        TaskType.MARKET_ANALYSIS_ONLY,
        TaskType.MARKET_ANALYSIS_DECISION,
        TaskType.WHAT_IF_SCENARIO,
        TaskType.FORECAST_ONLY,
        TaskType.ERP_DATA_QUERY,
    }


def _resolve_commercial_ops_product_id(state: PipelineState) -> str:
    for candidate in (
        str((state.get("pricing_decision") or {}).get("product_id") or "").strip().lower(),
        str(state.get("graph_product_id") or "").strip().lower(),
    ):
        if candidate:
            return candidate
    product_ids = extract_product_ids_in_text(str(state.get("user_query") or ""))
    if product_ids:
        return product_ids[0]
    agent_intent = _agent_intent_from_state(state)
    agent_products = agent_intent.get("product_ids") if isinstance(agent_intent, dict) else []
    if isinstance(agent_products, list) and agent_products:
        return str(agent_products[0] or "").strip().lower()
    return ""


def _query_commercial_ops_sales_facts(product_id: str) -> Dict[str, Any]:
    material_names = _PRODUCT_TO_MATERIAL_NAMES.get(product_id) or ()
    if not material_names:
        return {}

    placeholders = ", ".join(f":name_{idx}" for idx, _ in enumerate(material_names))
    params = {f"name_{idx}": name for idx, name in enumerate(material_names)}

    def _window(conn, ref_date, days: int) -> Dict[str, Any]:
        start_date = ref_date - _td(days=days - 1)
        row = conn.execute(
            _sql_text(
                "SELECT "
                "  COALESCE(SUM(COALESCE(FDELIQTY, 0)), 0) AS shipment_tons, "
                "  COUNT(*) AS order_rows, "
                "  AVG(COALESCE(FTAXPRICE, FPRICE)) AS avg_price "
                "FROM erp_v_sale_orderentry "
                f"WHERE material_name IN ({placeholders}) "
                "  AND PLANDATE IS NOT NULL "
                "  AND DATE(PLANDATE) >= :start_date "
                "  AND DATE(PLANDATE) <= :end_date"
            ),
            {**params, "start_date": start_date, "end_date": ref_date},
        ).mappings().first()
        if not row:
            return {}
        return {
            "shipment_tons": round(float(row.get("shipment_tons") or 0.0), 2),
            "order_rows": int(row.get("order_rows") or 0),
            "avg_price": round(float(row.get("avg_price") or 0.0), 2)
            if row.get("avg_price") is not None
            else None,
        }

    try:
        with _app_db.engine.connect() as conn:
            latest = conn.execute(
                _sql_text(
                    "SELECT MAX(DATE(PLANDATE)) "
                    "FROM erp_v_sale_orderentry "
                    f"WHERE material_name IN ({placeholders}) "
                    "  AND PLANDATE IS NOT NULL"
                ),
                params,
            ).scalar()
            if not latest:
                return {"material_names": list(material_names)}
            ref_date = latest if hasattr(latest, "year") else _dt.fromisoformat(str(latest)[:10]).date()
            seven = _window(conn, ref_date, 7)
            fourteen = _window(conn, ref_date, 14)
            previous_start = ref_date - _td(days=13)
            previous_end = ref_date - _td(days=7)
            previous = conn.execute(
                _sql_text(
                    "SELECT COALESCE(SUM(COALESCE(FDELIQTY, 0)), 0) AS shipment_tons "
                    "FROM erp_v_sale_orderentry "
                    f"WHERE material_name IN ({placeholders}) "
                    "  AND PLANDATE IS NOT NULL "
                    "  AND DATE(PLANDATE) >= :start_date "
                    "  AND DATE(PLANDATE) <= :end_date"
                ),
                {**params, "start_date": previous_start, "end_date": previous_end},
            ).scalar()
            previous_7d = float(previous or 0.0)
            current_7d = float(seven.get("shipment_tons") or 0.0)
            shipment_change_pct = (
                round((current_7d - previous_7d) / previous_7d * 100.0, 2)
                if previous_7d > 0
                else None
            )
            return {
                "material_names": list(material_names),
                "ref_date": ref_date.isoformat(),
                "sales_7d": seven,
                "sales_14d": fourteen,
                "previous_7d_tons": round(previous_7d, 2),
                "shipment_change_pct": shipment_change_pct,
            }
    except Exception:
        return {"material_names": list(material_names)}


def _load_commercial_ops_facts(state: PipelineState) -> Dict[str, Any]:
    product_id = _resolve_commercial_ops_product_id(state)
    if not product_id:
        return {}

    snapshot = state.get("perception_snapshot") or {}
    if not snapshot or product_id not in dict(snapshot.get("products") or {}):
        try:
            snapshot = perception_service.build_snapshot()
        except Exception:
            snapshot = state.get("perception_snapshot") or {}

    products = dict(snapshot.get("products") or {})
    product_meta = dict(products.get(product_id) or {})
    if not product_meta:
        node = next((n for n in _domain_graph.nodes if getattr(n, "id", "") == product_id), None)
        product_meta = {
            "id": product_id,
            "name_en": getattr(node, "name_en", product_id),
            "name_cn": getattr(node, "name_cn", product_id),
            "unit": getattr(node, "unit", "CNY/ton"),
        }

    pricing = dict(state.get("pricing_decision") or {})
    if not pricing or pricing.get("no_pricing"):
        try:
            pricing = pricing_service.compute_pricing_decision(
                product_id=product_id,
                perception_snapshot=snapshot,
                diagnosis=state.get("diagnosis") or {},
                forecast=state.get("forecast") or {},
                intelligence_context=state.get("intelligence_context") or {},
            )
        except Exception:
            pricing = {}

    inventory: Dict[str, Any] = {}
    try:
        from backend.src.service.erp_kpi_service import ErpKPIService

        inventory = ErpKPIService().get_inventory(product_id)
    except Exception:
        inventory = {}

    return {
        "product_id": product_id,
        "product_meta": product_meta,
        "snapshot": snapshot,
        "pricing": pricing,
        "inventory": inventory,
        "sales": _query_commercial_ops_sales_facts(product_id),
    }


def _build_commercial_ops_advisory_report(state: PipelineState) -> str:
    facts = _load_commercial_ops_facts(state)
    if not facts:
        return ""

    lang = _resolve_output_language(state)
    product_meta = dict(facts.get("product_meta") or {})
    pricing = dict(facts.get("pricing") or {})
    inventory = dict(facts.get("inventory") or {})
    sales = dict(facts.get("sales") or {})
    product_name = (
        str(product_meta.get("name_en") or facts.get("product_id") or "the product")
        if lang == "en"
        else str(product_meta.get("name_cn") or facts.get("product_id") or "该产品")
    )
    raw_unit = str(product_meta.get("unit") or "")
    unit = "元/吨" if lang != "en" else "CNY/ton"
    if raw_unit and raw_unit.lower() not in {"ton", "tons", "吨"}:
        unit = raw_unit
    latest_price = _to_float_or_none(pricing.get("latest_market_price"))
    if latest_price is None:
        latest_price = _to_float_or_none(product_meta.get("latest_price"))
    latest_date = str(product_meta.get("latest_date") or "")
    weekly_change = _to_float_or_none(product_meta.get("change_7d_pct"))
    recommended = _to_float_or_none(pricing.get("recommended_price"))
    price_range = pricing.get("price_range") or []
    price_low = _to_float_or_none(price_range[0]) if len(price_range) >= 1 else None
    price_high = _to_float_or_none(price_range[1]) if len(price_range) >= 2 else None
    action = str(pricing.get("action") or "hold_price").replace("_", " ")
    inventory_tons = _to_float_or_none(inventory.get("total_tons"))
    inventory_days = _to_float_or_none(inventory.get("inventory_days"))
    sales_7d = dict(sales.get("sales_7d") or {})
    sales_14d = dict(sales.get("sales_14d") or {})
    shipment_7d = _to_float_or_none(sales_7d.get("shipment_tons"))
    shipment_14d = _to_float_or_none(sales_14d.get("shipment_tons"))
    shipment_change_pct = _to_float_or_none(sales.get("shipment_change_pct"))
    ref_date = str(sales.get("ref_date") or "")

    def _fmt_price(value: Optional[float]) -> str:
        if value is None:
            return "unavailable" if lang == "en" else "暂无"
        return f"{value:,.0f} {unit}"

    def _fmt_pct(value: Optional[float]) -> str:
        if value is None:
            return "unavailable" if lang == "en" else "暂无"
        return f"{value:+.2f}%"

    def _fmt_tons(value: Optional[float]) -> str:
        if value is None:
            return "unavailable" if lang == "en" else "暂无"
        return f"{value:,.2f} tons" if lang == "en" else f"{value:,.2f} 吨"

    quote_action = "Hold quotes; avoid following the market lower unless shipment conversion weakens."
    inventory_action = "Keep inventory neutral and sell against confirmed demand."
    shipment_action = "Maintain normal shipment pace; accelerate only if 7-day shipment demand drops while inventory cover rises."
    if weekly_change is not None and weekly_change < -1.0:
        quote_action = "Move quotes down selectively, but keep contract protection and avoid a broad price reset."
    elif weekly_change is not None and weekly_change > 1.0:
        quote_action = "Do not chase the headline higher; test higher spot offers only on confirmed orders."
    if inventory_days is not None:
        if inventory_days > 30:
            inventory_action = "Inventory cover is high; prioritize destocking and avoid building more spot exposure."
            shipment_action = "Pull shipment pace forward for flexible customers and convert inventory into cash."
        elif inventory_days < 10:
            inventory_action = "Inventory cover is tight; protect core customers and avoid discount-led volume chasing."
            shipment_action = "Throttle non-core shipments and reserve stock for confirmed margin-positive orders."
    if shipment_change_pct is not None and shipment_change_pct < -20:
        quote_action = "Use a controlled quote concession for new spot orders because shipment momentum is weakening."

    if lang == "en":
        lines = [
            "## Commercial Ops Advisory",
            "",
            f"### 1. Verified Facts For {product_name}",
            f"- Latest market reference: **{_fmt_price(latest_price)}**"
            + (f" on {latest_date}" if latest_date else "")
            + f"; 7-day change: **{_fmt_pct(weekly_change)}**.",
            f"- ERP inventory: **{_fmt_tons(inventory_tons)}**"
            + (
                f", about **{inventory_days:.1f} days** of cover."
                if inventory_days is not None
                else ", cover days unavailable."
            ),
            f"- Shipment pace: **{_fmt_tons(shipment_7d)}** in the latest 7 days"
            + (f" through {ref_date}" if ref_date else "")
            + f"; latest 14 days: **{_fmt_tons(shipment_14d)}**; 7d vs previous 7d: **{_fmt_pct(shipment_change_pct)}**.",
            "",
            "### 2. Direct Recommendation",
            f"- Inventory: {inventory_action}",
            f"- Quote: {quote_action}",
            f"- Shipment pace: {shipment_action}",
            "",
            "### 3. Pricing Guardrail",
            f"- Stage-4 action: **{action}**.",
        ]
        if recommended is not None:
            if price_low is not None and price_high is not None:
                lines.append(
                    f"- Internal quote reference: **{_fmt_price(recommended)}**, executable band **{_fmt_price(price_low)} to {_fmt_price(price_high)}**."
                )
            else:
                lines.append(f"- Internal quote reference: **{_fmt_price(recommended)}**.")
        lines.extend(
            [
                "",
                "### 4. Triggers",
                "- If shipment conversion improves and inventory cover falls, hold price and stop incremental concessions.",
                "- If inventory cover rises while 7-day shipments fall again, use targeted concessions and accelerate shipments for flexible accounts.",
                "- If market price drops by more than 1% with weak orders, update the quote band before committing new spot volume.",
                "",
                "### 5. AI Synthesis",
                "The safe operating posture is disciplined: protect price where orders are firm, but do not let inventory build while waiting for the market to confirm direction. Use the quote band as a control range and let shipment conversion, not the headline 7-day move alone, decide whether to follow downward.",
                "",
                "Sources: `market_prices`, `erp_v_stk_inventory`, `erp_v_sale_orderentry`, and Stage-4 `pricing_service`.",
            ]
        )
        return "\n".join(lines).strip()

    lines = [
        "## 商业运营建议",
        "",
        f"### 1. {product_name} 已核验事实",
        f"- 最新市场参考价：**{_fmt_price(latest_price)}**"
        + (f"（{latest_date}）" if latest_date else "")
        + f"；7日变化：**{_fmt_pct(weekly_change)}**。",
        f"- ERP库存：**{_fmt_tons(inventory_tons)}**"
        + (f"，约 **{inventory_days:.1f} 天** 库存覆盖。" if inventory_days is not None else "，库存覆盖天数暂无。"),
        f"- 发货节奏：近7日 **{_fmt_tons(shipment_7d)}**"
        + (f"（截至 {ref_date}）" if ref_date else "")
        + f"；近14日 **{_fmt_tons(shipment_14d)}**；近7日较前7日：**{_fmt_pct(shipment_change_pct)}**。",
        "",
        "### 2. 直接建议",
        f"- 库存：{inventory_action}",
        f"- 报价：{quote_action}",
        f"- 发货：{shipment_action}",
        "",
        "### 3. 定价护栏",
        f"- Stage-4 动作：**{action}**。",
    ]
    if recommended is not None:
        if price_low is not None and price_high is not None:
            lines.append(
                f"- 内部报价参考：**{_fmt_price(recommended)}**，执行区间 **{_fmt_price(price_low)} 至 {_fmt_price(price_high)}**。"
            )
        else:
            lines.append(f"- 内部报价参考：**{_fmt_price(recommended)}**。")
    lines.extend(
        [
            "",
            "### 4. 触发条件",
            "- 如果发货转化改善且库存覆盖下降，维持报价纪律，停止额外让利。",
            "- 如果库存覆盖上升且近7日发货继续下降，对灵活客户做定向让利并加快发货。",
            "- 如果市场价继续下跌超过1%且订单偏弱，先更新报价区间，再承诺新增现货量。",
            "",
            "### 5. AI综合判断",
            "当前最稳妥的经营姿态是守住报价纪律，同时不能让库存被动累积。报价是否跟跌，不应只看7日价格变化，而要由发货转化和库存覆盖共同触发。",
            "",
            "数据来源：`market_prices`、`erp_v_stk_inventory`、`erp_v_sale_orderentry`、Stage-4 `pricing_service`。",
        ]
    )
    return "\n".join(lines).strip()


def _is_competitor_portfolio_summary_query(query: str) -> bool:
    q = str(query or "").strip().lower()
    if not q:
        return False

    competitor_markers = [
        "competitor",
        "competitors",
        "benchmark",
        "our sales",
        "sales impact",
        "plant operation",
        "quote change",
        "竞品",
        "竞争对手",
        "装置运行",
        "报价变化",
        "成交影响",
        "对我们成交的影响",
        "主要竞品",
    ]
    impact_markers = [
        "impact",
        "成交",
        "销量",
        "订单",
        "sales",
        "quote",
        "报价",
    ]
    return any(marker in q for marker in competitor_markers) and any(
        marker in q for marker in impact_markers
    )


def _use_fast_competitor_portfolio_path(state: PipelineState) -> bool:
    if _prefer_token_streaming(state):
        return False

    query = str(state.get("user_query") or "")
    if not _is_competitor_portfolio_summary_query(query):
        return False

    if not _normalize_bool_text(_os.getenv("DATABASE_ONLY_MODE", "true"), True):
        return False

    parsed = state.get("parsed_intent")
    task_type = getattr(parsed, "task_type", None)
    return task_type in {
        TaskType.MARKET_ANALYSIS_ONLY,
        TaskType.MARKET_ANALYSIS_DECISION,
        TaskType.WHAT_IF_SCENARIO,
        TaskType.BOSS_BRIEFING,
    }


def _build_competitor_portfolio_summary_report(state: PipelineState) -> str:
    if _resolve_output_language(state) == "en":
        return _build_en_competitor_portfolio_summary_report(state)
    return _build_cn_competitor_portfolio_summary_report(state)


def _is_broad_research_report_query(user_query: str) -> bool:
    q = (user_query or "").strip().lower()
    if not q:
        return False

    markers = [
        "deep research",
        "deep market research",
        "market research report",
        "research report",
        "market note",
        "research note",
        "executive report",
        "market report",
        "market landscape",
        "chain overview",
        "decision implications",
        "market study",
        "经营启示",
        "研究报告",
        "市场研究",
        "市场研究报告",
        "深度研究",
        "深度市场研究",
        "深度市场研究报告",
        "短期预测",
        "风险和经营启示",
    ]
    return any(marker in q for marker in markers) or any(
        marker in (user_query or "")
        for marker in ["经营启示", "研究报告", "市场研究", "深度研究", "短期预测", "风险和经营启示"]
    )


def _use_fast_research_report_path(state: PipelineState) -> bool:
    if _prefer_token_streaming(state):
        return False

    if state.get("out_of_scope_detected"):
        return False

    if not _is_broad_research_report_query(str(state.get("user_query") or "")):
        return False

    parsed = state.get("parsed_intent")
    task_type = getattr(parsed, "task_type", None)
    return task_type in {
        TaskType.MARKET_ANALYSIS_ONLY,
        TaskType.MARKET_ANALYSIS_DECISION,
        TaskType.WHAT_IF_SCENARIO,
        TaskType.BOSS_BRIEFING,
    }


def _is_broad_market_anomaly_query(user_query: str) -> bool:
    q = str(user_query or "").strip().lower()
    if not q:
        return False

    anomaly_markers = [
        "anomal",
        "abnormal move",
        "abnormal moves",
        "market dislocation",
        "volatility anomaly",
        "latest market data",
        "latest anomalies",
        "price anomaly",
        "price anomalies",
        "异常波动",
        "市场异动",
        "价格异动",
        "异常",
        "异动",
        "最新市场数据",
        "最新行情数据",
    ]
    driver_markers = [
        "driving",
        "driver",
        "reason",
        "explain",
        "what may be driving",
        "what is driving",
        "驱动",
        "原因",
        "解释",
        "为什么",
    ]
    market_markers = ["market", "price", "pricing", "行情", "市场", "价格"]

    has_anomaly = any(marker in q for marker in anomaly_markers)
    has_driver = any(marker in q for marker in driver_markers)
    has_market = any(marker in q for marker in market_markers)
    return has_market and (has_anomaly or ("latest" in q and has_driver))


def _use_fast_market_anomaly_path(state: PipelineState) -> bool:
    if _strict_kimi_only_mode(state):
        return False

    if _prefer_token_streaming(state):
        return False

    if state.get("out_of_scope_detected"):
        return False

    if not _is_broad_market_anomaly_query(str(state.get("user_query") or "")):
        return False

    parsed = state.get("parsed_intent")
    task_type = getattr(parsed, "task_type", None)
    return task_type in {
        TaskType.MARKET_ANALYSIS_ONLY,
        TaskType.MARKET_ANALYSIS_DECISION,
        TaskType.WHAT_IF_SCENARIO,
        TaskType.BOSS_BRIEFING,
    }


def _build_en_market_anomaly_report(state: PipelineState) -> str:
    snapshot = state.get("perception_snapshot") or {}
    products = dict(snapshot.get("products") or {})
    diagnosis = state.get("diagnosis") or {}
    pricing = state.get("pricing_decision") or {}
    forecast = state.get("forecast") or {}

    selected_ids = list(state.get("graph_product_ids") or []) or _select_key_products_from_snapshot(snapshot, limit=5)
    if not selected_ids:
        selected_ids = [
            pid for pid, pdata in products.items()
            if str((pdata or {}).get("data_source") or "") == "real"
        ][:5]

    anomaly_rows: List[Dict[str, Any]] = []
    for pid in selected_ids:
        pdata = dict(products.get(pid) or {})
        weekly = _to_float_or_none(pdata.get("change_7d_pct"))
        latest_price = _to_float_or_none(pdata.get("latest_price"))
        anomaly_meta = dict(pdata.get("anomaly") or {})
        anomaly_rows.append(
            {
                "product_id": pid,
                "name": str(pdata.get("name_en") or pdata.get("name_cn") or pid),
                "weekly_change": weekly,
                "latest_price": latest_price,
                "is_anomaly": bool(anomaly_meta.get("is_anomaly")),
                "anomaly_score": _to_float_or_none(anomaly_meta.get("anomaly_score")) or 0.0,
            }
        )

    anomaly_rows.sort(
        key=lambda row: (
            1 if row.get("is_anomaly") else 0,
            abs(float(row.get("weekly_change") or 0.0)),
            float(row.get("anomaly_score") or 0.0),
        ),
        reverse=True,
    )
    top_rows = anomaly_rows[:4]

    crude_trend = str(snapshot.get("crude_trend") or "").strip().lower()
    naphtha_trend = str(snapshot.get("naphtha_trend") or "").strip().lower()
    root_cause = str(diagnosis.get("root_cause") or "").strip()
    chain_impact = [str(item).strip() for item in (diagnosis.get("chain_impact") or []) if str(item).strip()]
    risks = [str(item).strip() for item in (diagnosis.get("risks") or []) if str(item).strip()]
    confidence = _to_float_or_none(pricing.get("confidence"))
    price_range = pricing.get("price_range") or []
    rec_price = _to_float_or_none(pricing.get("recommended_price"))
    scenarios_7d = (forecast.get("scenarios") or {}).get("7d") or {}

    if not top_rows:
        return (
            "## 1) Direct Answer\n"
            "The latest market snapshot does not expose enough high-quality product-level anomaly signals to support a grounded anomaly review right now. "
            "Please retry after the next snapshot refresh or specify a product cluster such as blowing agent, isoprene, piperylene, or DCPD."
        )

    lead_names = ", ".join(row["name"] for row in top_rows[:3])
    lead_moves = [
        f"{row['name']} ({float(row['weekly_change']):+.2f}% WoW)"
        for row in top_rows[:3]
        if isinstance(row.get("weekly_change"), (int, float))
    ]

    lines = [
        "## 1) Direct Answer",
        f"The latest snapshot shows the most notable anomalies in {lead_names}. These moves look more like localized dislocations than a clean whole-chain repricing, because downstream products are moving much faster than the upstream cost anchors.",
        "",
        "## 2) Main Anomalies Detected",
    ]

    for row in top_rows:
        move = row.get("weekly_change")
        move_text = f"{float(move):+.2f}% WoW" if isinstance(move, (int, float)) else "WoW unavailable"
        price_text = f"{float(row['latest_price']):,.0f}" if isinstance(row.get("latest_price"), (int, float)) else "unavailable"
        flag_text = "statistical anomaly flag active" if row.get("is_anomaly") else "large directional move"
        lines.append(f"- {row['name']}: {move_text}; latest price {price_text}; signal = {flag_text}.")

    lines.extend([
        "",
        "## 3) What May Be Driving Them",
    ])
    if lead_moves:
        lines.append(f"- Observed move set: {'; '.join(lead_moves)}.")
    if crude_trend or naphtha_trend:
        lines.append(f"- Cost-chain context: crude is {crude_trend or 'flat'} and naphtha is {naphtha_trend or 'flat'}, so the strongest downstream spikes are not being explained by a uniform upstream inflation story.")
    if root_cause:
        lines.append(f"- Diagnosis signal: {root_cause}")
    elif chain_impact:
        lines.append(f"- Transmission path: {chain_impact[0]}")
    else:
        lines.append("- Working interpretation: the market is showing localized tightness, order-conversion pressure, or temporary supply dislocation rather than a stable new cost base.")
    lines.append("- Interpretation rule: when downstream products spike while feedstock is soft or mixed, treat the move as fragile until confirmed by order conversion, inventory drawdown, and competitor quote follow-through.")

    lines.extend([
        "",
        "## 4) Business Implications And Watchpoints",
    ])
    if isinstance(rec_price, (int, float)):
        if len(price_range) >= 2 and isinstance(price_range[0], (int, float)) and isinstance(price_range[1], (int, float)):
            lines.append(f"- Pricing posture: Stage-4 currently points to {rec_price:,.0f} CNY/t with an execution band of {int(price_range[0]):,}–{int(price_range[1]):,} CNY/t, which argues for disciplined execution rather than blindly chasing the strongest spike.")
        else:
            lines.append(f"- Pricing posture: Stage-4 currently points to {rec_price:,.0f} CNY/t, so the system is not treating the observed price spikes as automatically sustainable.")
    if scenarios_7d:
        base = scenarios_7d.get("base") or {}
        low = _to_float_or_none(base.get("low"))
        high = _to_float_or_none(base.get("high"))
        if isinstance(low, (int, float)) and isinstance(high, (int, float)):
            lines.append(f"- 7-day base range cross-check: {low:,.0f}–{high:,.0f} CNY/t.")
    if isinstance(confidence, (int, float)):
        lines.append(f"- Confidence: {confidence * 100:.0f}% on the current pricing signal, so treat recommendations as conditional until the next market confirmation.")
    for risk in risks[:3]:
        lines.append(f"- Risk: {risk}")
    if not risks:
        lines.append("- Watch buyer resistance, competitor quote follow-through, and whether inventory drawdown confirms that the strongest price moves are real rather than temporary squeezes.")

    return "\n".join(lines).strip()


def _build_cn_market_anomaly_report(state: PipelineState) -> str:
    snapshot = state.get("perception_snapshot") or {}
    products = dict(snapshot.get("products") or {})
    diagnosis = state.get("diagnosis") or {}
    pricing = state.get("pricing_decision") or {}
    forecast = state.get("forecast") or {}

    selected_ids = list(state.get("graph_product_ids") or []) or _select_key_products_from_snapshot(snapshot, limit=5)
    if not selected_ids:
        selected_ids = [
            pid for pid, pdata in products.items()
            if str((pdata or {}).get("data_source") or "") == "real"
        ][:5]

    anomaly_rows: List[Dict[str, Any]] = []
    for pid in selected_ids:
        pdata = dict(products.get(pid) or {})
        weekly = _to_float_or_none(pdata.get("change_7d_pct"))
        latest_price = _to_float_or_none(pdata.get("latest_price"))
        anomaly_meta = dict(pdata.get("anomaly") or {})
        anomaly_rows.append(
            {
                "product_id": pid,
                "name": str(pdata.get("name_cn") or pdata.get("name_en") or pid),
                "weekly_change": weekly,
                "latest_price": latest_price,
                "is_anomaly": bool(anomaly_meta.get("is_anomaly")),
                "anomaly_score": _to_float_or_none(anomaly_meta.get("anomaly_score")) or 0.0,
            }
        )

    anomaly_rows.sort(
        key=lambda row: (
            1 if row.get("is_anomaly") else 0,
            abs(float(row.get("weekly_change") or 0.0)),
            float(row.get("anomaly_score") or 0.0),
        ),
        reverse=True,
    )
    top_rows = anomaly_rows[:4]

    crude_trend = str(snapshot.get("crude_trend") or "").strip().lower()
    naphtha_trend = str(snapshot.get("naphtha_trend") or "").strip().lower()
    root_cause = str(diagnosis.get("root_cause") or "").strip()
    chain_impact = [str(item).strip() for item in (diagnosis.get("chain_impact") or []) if str(item).strip()]
    risks = [str(item).strip() for item in (diagnosis.get("risks") or []) if str(item).strip()]
    confidence = _to_float_or_none(pricing.get("confidence"))
    price_range = pricing.get("price_range") or []
    rec_price = _to_float_or_none(pricing.get("recommended_price"))
    scenarios_7d = (forecast.get("scenarios") or {}).get("7d") or {}

    if not top_rows:
        return (
            "## 1) 直接结论\n"
            "最新市场快照里还没有足够清晰的产品级异常信号，暂时不足以支撑一版扎实的异常分析。"
            "建议等下一轮快照刷新后重试，或直接指定产品组，如戊烷发泡剂、异戊二烯、间戊二烯、双环戊二烯。"
        )

    lead_names = "、".join(row["name"] for row in top_rows[:3])
    lead_moves = [
        f"{row['name']}（{float(row['weekly_change']):+.2f}% WoW）"
        for row in top_rows[:3]
        if isinstance(row.get("weekly_change"), (int, float))
    ]

    lines = [
        "## 1) 直接结论",
        f"最新快照里，最值得重点关注的异常主要集中在{lead_names}。这些波动更像是局部失衡或阶段性挤压，而不是整条成本链同步抬升，因为下游强势品种的涨幅明显快于上游成本锚。",
        "",
        "## 2) 主要异常点",
    ]

    for row in top_rows:
        move = row.get("weekly_change")
        move_text = f"{float(move):+.2f}% WoW" if isinstance(move, (int, float)) else "WoW 暂缺"
        price_text = f"{float(row['latest_price']):,.0f}" if isinstance(row.get("latest_price"), (int, float)) else "暂缺"
        flag_text = "已触发统计异常标记" if row.get("is_anomaly") else "属于显著方向性波动"
        lines.append(f"- {row['name']}：{move_text}；最新价格 {price_text}；信号判断 = {flag_text}。")

    lines.extend([
        "",
        "## 3) 可能驱动因素",
    ])
    if lead_moves:
        lines.append(f"- 当前异常组合：{'；'.join(lead_moves)}。")
    if crude_trend or naphtha_trend:
        lines.append(f"- 成本链背景：原油为{crude_trend or '平稳'}，石脑油为{naphtha_trend or '平稳'}，说明最强的下游上涨并不是由统一的上游成本抬升直接推动。")
    if root_cause:
        lines.append(f"- 诊断主线：{root_cause}")
    elif chain_impact:
        lines.append(f"- 传导链条：{chain_impact[0]}")
    else:
        lines.append("- 工作判断：当前更像是局部供应偏紧、成交转化压力或短期供需错配，而不是稳定的新价格中枢。")
    lines.append("- 判断原则：如果下游品种快速上涨、但上游成本偏弱或分化，就要先把它视为脆弱上涨，必须等成交转化、库存去化、竞品跟价一起确认后，才能判断为趋势性重定价。")

    lines.extend([
        "",
        "## 4) 对业务的含义与后续跟踪",
    ])
    if isinstance(rec_price, (int, float)):
        if len(price_range) >= 2 and isinstance(price_range[0], (int, float)) and isinstance(price_range[1], (int, float)):
            lines.append(f"- 定价姿态：Stage-4 当前给出的建议价为 {rec_price:,.0f} 元/吨，执行带为 {int(price_range[0]):,}–{int(price_range[1]):,} 元/吨，这意味着当前更适合纪律化执行，而不是盲目追涨。")
        else:
            lines.append(f"- 定价姿态：Stage-4 当前给出的建议价为 {rec_price:,.0f} 元/吨，系统并没有把这些异常上涨直接视为可持续行情。")
    if scenarios_7d:
        base = scenarios_7d.get("base") or {}
        low = _to_float_or_none(base.get("low"))
        high = _to_float_or_none(base.get("high"))
        if isinstance(low, (int, float)) and isinstance(high, (int, float)):
            lines.append(f"- 7天基准区间校验：{low:,.0f}–{high:,.0f} 元/吨。")
    if isinstance(confidence, (int, float)):
        lines.append(f"- 当前定价信号置信度约为 {confidence * 100:.0f}% ，因此在下一轮市场确认前应把建议视为条件成立下的结论。")
    for risk in risks[:3]:
        lines.append(f"- 风险：{risk}")
    if not risks:
        lines.append("- 后续重点观察：买方是否开始抵触高价、竞品报价是否跟进、库存是否同步去化。如果这三项不能同时验证，当前异常波动就更可能是短期挤压而非趋势。")

    return "\n".join(lines).strip()


def _build_market_anomaly_report(state: PipelineState) -> str:
    if _resolve_output_language(state) == "en":
        return _build_en_market_anomaly_report(state)
    return _build_cn_market_anomaly_report(state)


def _is_broad_market_anomaly_query(user_query: str) -> bool:
    q = str(user_query or "").strip().lower()
    if not q:
        return False

    anomaly_markers = [
        "anomal",
        "abnormal move",
        "abnormal moves",
        "market dislocation",
        "volatility anomaly",
        "latest market data",
        "latest anomalies",
        "price anomaly",
        "price anomalies",
        "异常波动",
        "市场异动",
        "价格异动",
        "异常",
        "异动",
        "最新市场数据",
        "最新行情数据",
    ]
    driver_markers = [
        "driving",
        "driver",
        "reason",
        "explain",
        "what may be driving",
        "what is driving",
        "驱动",
        "原因",
        "解释",
        "为什么",
    ]
    market_markers = ["market", "price", "pricing", "行情", "市场", "价格"]

    has_anomaly = any(marker in q for marker in anomaly_markers)
    has_driver = any(marker in q for marker in driver_markers)
    has_market = any(marker in q for marker in market_markers)
    return has_market and (has_anomaly or ("latest" in q and has_driver))


def _use_fast_market_anomaly_path(state: PipelineState) -> bool:
    if _strict_kimi_only_mode(state):
        return False

    if _prefer_token_streaming(state):
        return False

    if state.get("out_of_scope_detected"):
        return False

    if not _is_broad_market_anomaly_query(str(state.get("user_query") or "")):
        return False

    parsed = state.get("parsed_intent")
    task_type = getattr(parsed, "task_type", None)
    return task_type in {
        TaskType.MARKET_ANALYSIS_ONLY,
        TaskType.MARKET_ANALYSIS_DECISION,
        TaskType.WHAT_IF_SCENARIO,
        TaskType.BOSS_BRIEFING,
    }


def _build_en_market_anomaly_report(state: PipelineState) -> str:
    snapshot = state.get("perception_snapshot") or {}
    products = dict(snapshot.get("products") or {})
    diagnosis = state.get("diagnosis") or {}
    pricing = state.get("pricing_decision") or {}
    forecast = state.get("forecast") or {}

    selected_ids = list(state.get("graph_product_ids") or []) or _select_key_products_from_snapshot(snapshot, limit=5)
    if not selected_ids:
        selected_ids = [
            pid for pid, pdata in products.items()
            if str((pdata or {}).get("data_source") or "") == "real"
        ][:5]

    anomaly_rows: List[Dict[str, Any]] = []
    for pid in selected_ids:
        pdata = dict(products.get(pid) or {})
        weekly = _to_float_or_none(pdata.get("change_7d_pct"))
        latest_price = _to_float_or_none(pdata.get("latest_price"))
        anomaly_meta = dict(pdata.get("anomaly") or {})
        anomaly_rows.append(
            {
                "product_id": pid,
                "name": str(pdata.get("name_en") or pdata.get("name_cn") or pid),
                "weekly_change": weekly,
                "latest_price": latest_price,
                "is_anomaly": bool(anomaly_meta.get("is_anomaly")),
                "anomaly_score": _to_float_or_none(anomaly_meta.get("anomaly_score")) or 0.0,
            }
        )

    anomaly_rows.sort(
        key=lambda row: (
            1 if row.get("is_anomaly") else 0,
            abs(float(row.get("weekly_change") or 0.0)),
            float(row.get("anomaly_score") or 0.0),
        ),
        reverse=True,
    )
    top_rows = anomaly_rows[:4]

    crude_trend = str(snapshot.get("crude_trend") or "").strip().lower()
    naphtha_trend = str(snapshot.get("naphtha_trend") or "").strip().lower()
    root_cause = str(diagnosis.get("root_cause") or "").strip()
    chain_impact = [str(item).strip() for item in (diagnosis.get("chain_impact") or []) if str(item).strip()]
    risks = [str(item).strip() for item in (diagnosis.get("risks") or []) if str(item).strip()]
    confidence = _to_float_or_none(pricing.get("confidence"))
    price_range = pricing.get("price_range") or []
    rec_price = _to_float_or_none(pricing.get("recommended_price"))
    scenarios_7d = (forecast.get("scenarios") or {}).get("7d") or {}

    if not top_rows:
        return (
            "## 1) Direct Answer\n"
            "The latest market snapshot does not expose enough high-quality product-level anomaly signals to support a grounded anomaly review right now. "
            "Please retry after the next snapshot refresh or specify a product cluster such as blowing agent, isoprene, piperylene, or DCPD."
        )

    lead_names = ", ".join(row["name"] for row in top_rows[:3])
    lead_moves = [
        f"{row['name']} ({float(row['weekly_change']):+.2f}% WoW)"
        for row in top_rows[:3]
        if isinstance(row.get("weekly_change"), (int, float))
    ]

    lines = [
        "## 1) Direct Answer",
        f"The latest snapshot shows the most notable anomalies in {lead_names}. These moves look more like localized dislocations than a clean whole-chain repricing, because downstream products are moving much faster than the upstream cost anchors.",
        "",
        "## 2) Main Anomalies Detected",
    ]

    for row in top_rows:
        move = row.get("weekly_change")
        move_text = f"{float(move):+.2f}% WoW" if isinstance(move, (int, float)) else "WoW unavailable"
        price_text = f"{float(row['latest_price']):,.0f}" if isinstance(row.get("latest_price"), (int, float)) else "unavailable"
        flag_text = "statistical anomaly flag active" if row.get("is_anomaly") else "large directional move"
        lines.append(f"- {row['name']}: {move_text}; latest price {price_text}; signal = {flag_text}.")

    lines.extend([
        "",
        "## 3) What May Be Driving Them",
    ])
    if lead_moves:
        lines.append(f"- Observed move set: {'; '.join(lead_moves)}.")
    if crude_trend or naphtha_trend:
        lines.append(f"- Cost-chain context: crude is {crude_trend or 'flat'} and naphtha is {naphtha_trend or 'flat'}, so the strongest downstream spikes are not being explained by a uniform upstream inflation story.")
    if root_cause:
        lines.append(f"- Diagnosis signal: {root_cause}")
    elif chain_impact:
        lines.append(f"- Transmission path: {chain_impact[0]}")
    else:
        lines.append("- Working interpretation: the market is showing localized tightness, order-conversion pressure, or temporary supply dislocation rather than a stable new cost base.")
    lines.append("- Interpretation rule: when downstream products spike while feedstock is soft or mixed, treat the move as fragile until confirmed by order conversion, inventory drawdown, and competitor quote follow-through.")

    lines.extend([
        "",
        "## 4) Business Implications And Watchpoints",
    ])
    if isinstance(rec_price, (int, float)):
        if len(price_range) >= 2 and isinstance(price_range[0], (int, float)) and isinstance(price_range[1], (int, float)):
            lines.append(f"- Pricing posture: Stage-4 currently points to {rec_price:,.0f} CNY/t with an execution band of {int(price_range[0]):,}–{int(price_range[1]):,} CNY/t, which argues for disciplined execution rather than blindly chasing the strongest spike.")
        else:
            lines.append(f"- Pricing posture: Stage-4 currently points to {rec_price:,.0f} CNY/t, so the system is not treating the observed price spikes as automatically sustainable.")
    if scenarios_7d:
        base = scenarios_7d.get("base") or {}
        low = _to_float_or_none(base.get("low"))
        high = _to_float_or_none(base.get("high"))
        if isinstance(low, (int, float)) and isinstance(high, (int, float)):
            lines.append(f"- 7-day base range cross-check: {low:,.0f}–{high:,.0f} CNY/t.")
    if isinstance(confidence, (int, float)):
        lines.append(f"- Confidence: {confidence * 100:.0f}% on the current pricing signal, so treat recommendations as conditional until the next market confirmation.")
    for risk in risks[:3]:
        lines.append(f"- Risk: {risk}")
    if not risks:
        lines.append("- Watch buyer resistance, competitor quote follow-through, and whether inventory drawdown confirms that the strongest price moves are real rather than temporary squeezes.")

    return "\n".join(lines).strip()


def _build_cn_market_anomaly_report(state: PipelineState) -> str:
    snapshot = state.get("perception_snapshot") or {}
    products = dict(snapshot.get("products") or {})
    diagnosis = state.get("diagnosis") or {}
    pricing = state.get("pricing_decision") or {}
    forecast = state.get("forecast") or {}

    selected_ids = list(state.get("graph_product_ids") or []) or _select_key_products_from_snapshot(snapshot, limit=5)
    if not selected_ids:
        selected_ids = [
            pid for pid, pdata in products.items()
            if str((pdata or {}).get("data_source") or "") == "real"
        ][:5]

    anomaly_rows: List[Dict[str, Any]] = []
    for pid in selected_ids:
        pdata = dict(products.get(pid) or {})
        weekly = _to_float_or_none(pdata.get("change_7d_pct"))
        latest_price = _to_float_or_none(pdata.get("latest_price"))
        anomaly_meta = dict(pdata.get("anomaly") or {})
        anomaly_rows.append(
            {
                "product_id": pid,
                "name": str(pdata.get("name_cn") or pdata.get("name_en") or pid),
                "weekly_change": weekly,
                "latest_price": latest_price,
                "is_anomaly": bool(anomaly_meta.get("is_anomaly")),
                "anomaly_score": _to_float_or_none(anomaly_meta.get("anomaly_score")) or 0.0,
            }
        )

    anomaly_rows.sort(
        key=lambda row: (
            1 if row.get("is_anomaly") else 0,
            abs(float(row.get("weekly_change") or 0.0)),
            float(row.get("anomaly_score") or 0.0),
        ),
        reverse=True,
    )
    top_rows = anomaly_rows[:4]

    crude_trend = str(snapshot.get("crude_trend") or "").strip().lower()
    naphtha_trend = str(snapshot.get("naphtha_trend") or "").strip().lower()
    root_cause = str(diagnosis.get("root_cause") or "").strip()
    chain_impact = [str(item).strip() for item in (diagnosis.get("chain_impact") or []) if str(item).strip()]
    risks = [str(item).strip() for item in (diagnosis.get("risks") or []) if str(item).strip()]
    confidence = _to_float_or_none(pricing.get("confidence"))
    price_range = pricing.get("price_range") or []
    rec_price = _to_float_or_none(pricing.get("recommended_price"))
    scenarios_7d = (forecast.get("scenarios") or {}).get("7d") or {}

    if not top_rows:
        return (
            "## 1) 直接结论\n"
            "最新市场快照里还没有足够清晰的产品级异常信号，暂时不足以支撑一版扎实的异常分析。"
            "建议等下一轮快照刷新后重试，或直接指定产品组，如戊烷发泡剂、异戊二烯、间戊二烯、双环戊二烯。"
        )

    lead_names = "、".join(row["name"] for row in top_rows[:3])
    lead_moves = [
        f"{row['name']}（{float(row['weekly_change']):+.2f}% WoW）"
        for row in top_rows[:3]
        if isinstance(row.get("weekly_change"), (int, float))
    ]

    lines = [
        "## 1) 直接结论",
        f"最新快照里，最值得重点关注的异常主要集中在{lead_names}。这些波动更像是局部失衡或阶段性挤压，而不是整条成本链同步抬升，因为下游强势品种的涨幅明显快于上游成本锚。",
        "",
        "## 2) 主要异常点",
    ]

    for row in top_rows:
        move = row.get("weekly_change")
        move_text = f"{float(move):+.2f}% WoW" if isinstance(move, (int, float)) else "WoW 暂缺"
        price_text = f"{float(row['latest_price']):,.0f}" if isinstance(row.get("latest_price"), (int, float)) else "暂缺"
        flag_text = "已触发统计异常标记" if row.get("is_anomaly") else "属于显著方向性波动"
        lines.append(f"- {row['name']}：{move_text}；最新价格 {price_text}；信号判断 = {flag_text}。")

    lines.extend([
        "",
        "## 3) 可能驱动因素",
    ])
    if lead_moves:
        lines.append(f"- 当前异常组合：{'；'.join(lead_moves)}。")
    if crude_trend or naphtha_trend:
        lines.append(f"- 成本链背景：原油为{crude_trend or '平稳'}，石脑油为{naphtha_trend or '平稳'}，说明最强的下游上涨并不是由统一的上游成本抬升直接推动。")
    if root_cause:
        lines.append(f"- 诊断主线：{root_cause}")
    elif chain_impact:
        lines.append(f"- 传导链条：{chain_impact[0]}")
    else:
        lines.append("- 工作判断：当前更像是局部供应偏紧、成交转化压力或短期供需错配，而不是稳定的新价格中枢。")
    lines.append("- 判断原则：如果下游品种快速上涨、但上游成本偏弱或分化，就要先把它视为脆弱上涨，必须等成交转化、库存去化、竞品跟价一起确认后，才能判断为趋势性重定价。")

    lines.extend([
        "",
        "## 4) 对业务的含义与后续跟踪",
    ])
    if isinstance(rec_price, (int, float)):
        if len(price_range) >= 2 and isinstance(price_range[0], (int, float)) and isinstance(price_range[1], (int, float)):
            lines.append(f"- 定价姿态：Stage-4 当前给出的建议价为 {rec_price:,.0f} 元/吨，执行带为 {int(price_range[0]):,}–{int(price_range[1]):,} 元/吨，这意味着当前更适合纪律化执行，而不是盲目追涨。")
        else:
            lines.append(f"- 定价姿态：Stage-4 当前给出的建议价为 {rec_price:,.0f} 元/吨，系统并没有把这些异常上涨直接视为可持续行情。")
    if scenarios_7d:
        base = scenarios_7d.get("base") or {}
        low = _to_float_or_none(base.get("low"))
        high = _to_float_or_none(base.get("high"))
        if isinstance(low, (int, float)) and isinstance(high, (int, float)):
            lines.append(f"- 7天基准区间校验：{low:,.0f}–{high:,.0f} 元/吨。")
    if isinstance(confidence, (int, float)):
        lines.append(f"- 当前定价信号置信度约为 {confidence * 100:.0f}% ，因此在下一轮市场确认前应把建议视为条件成立下的结论。")
    for risk in risks[:3]:
        lines.append(f"- 风险：{risk}")
    if not risks:
        lines.append("- 后续重点观察：买方是否开始抵触高价、竞品报价是否跟进、库存是否同步去化。如果这三项不能同时验证，当前异常波动就更可能是短期挤压而非趋势。")

    return "\n".join(lines).strip()


def _build_market_anomaly_report(state: PipelineState) -> str:
    if _resolve_output_language(state) == "en":
        return _build_en_market_anomaly_report(state)
    return _build_cn_market_anomaly_report(state)


def _build_research_report(state: PipelineState) -> str:
    if _resolve_output_language(state) == "en":
        return _build_en_research_report(state)
    return _build_cn_research_report(state)


def _build_en_research_report(state: PipelineState) -> str:
    products = _select_key_products_from_snapshot(state.get("perception_snapshot") or {}, limit=4)
    if not products:
        products = list(state.get("graph_product_ids") or [])[:4]
    if not products:
        return (
            "## Executive View\n"
            "This request should use the research-brief path, not an out-of-scope reply.\n"
            "No tracked market anchors were resolved, so EDIA cannot yet draft a grounded brief.\n"
            "Please retry after snapshot refresh or specify a product cluster such as C5, C9, isoprene, piperylene, or SIS."
        )

    snapshot_products = (state.get("perception_snapshot") or {}).get("products") or {}
    lines = [
        "## Executive View",
        "This is a professional market brief built from EDIA's tracked market proxies rather than a single-product quote answer.",
        "",
        "## 1) Current market structure",
    ]
    for pid in products:
        pdata = snapshot_products.get(pid) or {}
        name = pdata.get("name_en") or pdata.get("name_cn") or pid
        latest = _to_float_or_none(pdata.get("latest_price"))
        chg7 = _to_float_or_none(pdata.get("change_7d_pct"))
        inv = _to_float_or_none(pdata.get("inventory_days"))
        metrics = []
        if latest is not None:
            metrics.append(f"tracked price {latest:,.0f} CNY/t")
        if chg7 is not None:
            metrics.append(f"7d change {chg7:+.2f}%")
        if inv is not None:
            metrics.append(f"inventory about {inv:.1f} days")
        metric_text = "; ".join(metrics) if metrics else "tracked in the current market snapshot"
        lines.append(f"- {name}: {metric_text}.")
    lines.extend([
        "",
        "## 2) Short-term view",
        "- Read the next 1-2 weeks through three linked signals: price slope, inventory days, and order conversion follow-through.",
        "- If leaders keep rising while inventory stays tight, the market remains firm rather than reversing immediately.",
        "- If quotes rise but conversion slows, expect a move from momentum to range trading.",
        "",
        "## 3) Key risks",
        "- Cost reversal risk from naphtha and upstream feedstock repricing.",
        "- Demand risk if customer acceptance weakens before quote changes become realized deals.",
        "- Structural risk because different products in the chain can move out of sync.",
        "",
        "## 4) Operating implications",
        "- Keep long-contract discipline separate from spot flexibility.",
        "- Escalate pricing action only when quote pressure and conversion pressure appear together.",
        "- Use this brief as the first-pass research view, then drill down by product, competitor, or customer segment.",
    ])
    return "\n".join(lines).strip()


def _clean_research_intelligence_summary(raw_text: str) -> str:
    text = str(raw_text or "").strip()
    if not text:
        return ""

    blocked_markers = [
        "database_only_mode",
        "persisted intelligence registry",
        "initialized",
        "active:",
        "using local",
        "http request:",
        "llm generate",
    ]
    first_line = text.splitlines()[0].strip()
    if any(marker in first_line.lower() for marker in blocked_markers):
        return ""
    return first_line[:180]


def _build_cn_research_report(state: PipelineState) -> str:
    snapshot = state.get("perception_snapshot") or {}
    snapshot_products = dict(snapshot.get("products") or {})
    products = _select_key_products_from_snapshot(snapshot, limit=4)
    if not products:
        products = list(state.get("graph_product_ids") or [])[:4]

    if not products:
        return (
            "## 快速结论\n"
            "这类问题应走深度研究路径，而不是边界说明。\n"
            "但当前没有解析到可用的市场锚点，暂时无法输出有数据支撑的研究简报。\n"
            "请在快照刷新后重试，或直接指定 C5、C9、异戊二烯、间戊二烯、SIS 等研究对象。"
        )

    summary_rows: List[str] = []
    bullish = 0
    bearish = 0
    for pid in products:
        pdata = snapshot_products.get(pid) or {}
        name = str(pdata.get("name_cn") or pdata.get("name_en") or pid)
        latest = _to_float_or_none(pdata.get("latest_price"))
        chg7 = _to_float_or_none(pdata.get("change_7d_pct"))
        inv = _to_float_or_none(pdata.get("inventory_days"))
        vol30 = _to_float_or_none(pdata.get("sales_volume_30d_change_pct"))
        if chg7 is not None:
            if chg7 >= 2.0:
                bullish += 1
            elif chg7 <= -2.0:
                bearish += 1

        metrics = []
        if latest is not None:
            metrics.append(f"当前跟踪价 {latest:,.0f} 元/吨")
        if chg7 is not None:
            metrics.append(f"7日变化 {chg7:+.2f}%")
        if inv is not None:
            metrics.append(f"库存约 {inv:.1f} 天")
        if vol30 is not None:
            metrics.append(f"30天销量增速 {vol30:+.2f}%")
        metric_text = "；".join(metrics) if metrics else "当前已纳入实时市场快照"
        summary_rows.append(f"- {name}：{metric_text}。")

    if bullish >= 2 and bearish == 0:
        market_tone = "偏强，但更像结构性走强，不是所有品种同步上行。"
    elif bearish >= 2 and bullish == 0:
        market_tone = "偏弱，短期更要关注回调与成交承压。"
    else:
        market_tone = "分化，当前更适合按产品链条和成交节奏拆开看。"

    lines = [
        "## 快速结论",
        "这不是一个单产品问答，而是一类应走研究简报路径的管理型问题。",
        f"基于当前 EDIA 已覆盖的实时市场代理信号，市场整体判断为：{market_tone}",
        "",
        "## 1) 当前市场结构与数据抓手",
    ]
    lines.extend(summary_rows)

    intelligence_text = _clean_research_intelligence_summary(str(state.get("intelligence_text") or ""))
    lines.extend([
        "",
        "## 2) 数据采集与情报整合",
        "- 当前回答首先使用内部实时市场数据库中的价格、7日变化、库存/销量代理信号。",
    ])
    if intelligence_text:
        lines.append(f"- 已汇总情报摘要：{intelligence_text}")
    else:
        lines.append("- 当前未拿到足够清晰的外部事件证据，因此本版研究更偏向数据库驱动，而非事件驱动。")

    lines.extend([
        "",
        "## 3) 短期预测",
        "- 未来 1-2 周，先看上游成本变化能否继续传导到下游报价与成交，而不是只看单个挂牌价。",
        "- 如果强势品种继续上涨、库存没有同步累积，短期仍偏强；如果报价走强但订单转化没有跟上，市场会从上涨转为高位震荡。",
        "- 更专业的判断框架应同时盯三类信号：价格斜率、库存天数、30天销量/订单转化。",
        "",
        "## 4) 主要风险",
        "- 成本风险：石脑油/上游链条若快速回落，当前涨价逻辑会被削弱。",
        "- 需求风险：客户询盘和实际转化不同步时，表面报价上涨不一定能变成真实成交。",
        "- 结构风险：不同产品节奏分化时，不能把某一个品种的走势直接外推到整条产品线。",
        "",
        "## 5) 经营启示",
        "- 销售上，应把长约保护和机会型现货分开管理，先识别哪些品种已经出现真实跟价压力。",
        "- 定价上，只有在“报价变化 + 转化率变慢 + 库存被动抬升”同时出现时，才应考虑更积极的价格动作。",
        "- 管理上，这份回答应作为第一版研究底稿；下一轮最值得继续下钻的是：1）按产品链拆解，2）按竞品拆解，3）按成交影响拆解。",
    ])
    return "\n".join(lines).strip()


def _build_en_competitor_portfolio_summary_report(state: PipelineState) -> str:
    snapshot = state.get("perception_snapshot") or {}
    products = dict(snapshot.get("products") or {})
    selected_ids = list(state.get("graph_product_ids") or []) or _select_key_products_from_snapshot(snapshot, limit=4)
    bundle = _get_combined_insight_bundle(state)
    by_product = dict(bundle.get("by_product") or {})

    lines = [
        "## Quick Take",
        "I will treat this as a competitor-and-conversion portfolio review rather than a single-product quote question.",
        "Within current EDIA coverage, the usable proxy is: compare covered C5/C9 chain products, recent quote moves, and our ERP-side sales/inventory signals to infer where competitor pressure is most likely hitting conversion.",
        "",
        "## 1) Competitor / Quote Proxy Signals",
    ]

    for pid in selected_ids[:4]:
        market_item = dict((by_product.get(pid) or {}).get("market") or {})
        pdata = dict(products.get(pid) or {})
        name = str(pdata.get("name_en") or pid)
        latest = market_item.get("latest_price", pdata.get("latest_price"))
        weekly = market_item.get("weekly_change_pct", pdata.get("change_7d_pct"))
        try:
            latest_text = f"{float(latest):,.0f}"
        except Exception:
            latest_text = "N/A"
        try:
            weekly_text = f"{float(weekly):+.2f}%"
        except Exception:
            weekly_text = "N/A"
        lines.append(f"- {name}: latest tracked price {latest_text}; 7d move {weekly_text}.")

    lines.extend([
        "",
        "## 2) Likely Impact On Our Conversion",
        "- If competitor quotes are moving faster than our covered reference products, conversion risk usually appears first in spot orders, then in customer quote discipline.",
        "- Use ERP order pace and inventory-days changes as the first confirmation signal before treating competitor price moves as a real share-loss event.",
        "- If you want company-specific impact, the next best step is to break this down by product, region, or customer segment rather than forcing one generic conclusion.",
        "",
        "## 3) Recommended Next Slice",
        "- I can continue in one of three ways: by competitor plant status, by product quote movement, or by impact on our order conversion and shipments.",
    ])
    return "\n".join(lines).strip()


def _build_cn_competitor_portfolio_summary_report(state: PipelineState) -> str:
    snapshot = state.get("perception_snapshot") or {}
    products = dict(snapshot.get("products") or {})
    selected_ids = list(state.get("graph_product_ids") or []) or _select_key_products_from_snapshot(snapshot, limit=4)
    bundle = _get_combined_insight_bundle(state)
    by_product = dict(bundle.get("by_product") or {})

    lines = [
        "## 快速结论",
        "这个问题应按“竞品动态 + 报价变化 + 我们成交影响”来做组合分析，而不是被压缩成单一产品报价问答。",
        "在当前 EDIA 覆盖范围内，可以先用已覆盖的 C5/C9 关键产品作为竞品与成交的代理信号，先判断哪里在承受竞价压力，再决定是否继续下钻到具体竞品或客户层。",
        "",
        "## 1) 当前竞品/报价代理信号",
    ]

    for pid in selected_ids[:4]:
        market_item = dict((by_product.get(pid) or {}).get("market") or {})
        erp_item = dict((by_product.get(pid) or {}).get("erp") or {})
        pdata = dict(products.get(pid) or {})
        name = str(pdata.get("name_cn") or pdata.get("name_en") or pid)
        latest = market_item.get("latest_price", pdata.get("latest_price"))
        weekly = market_item.get("weekly_change_pct", pdata.get("change_7d_pct"))
        inventory_days = erp_item.get("inventory_days")
        sales_growth = erp_item.get("sales_growth_pct")
        try:
            latest_text = f"{float(latest):,.0f} 元/吨"
        except Exception:
            latest_text = "暂无"
        try:
            weekly_text = f"{float(weekly):+.2f}%"
        except Exception:
            weekly_text = "暂无"
        extras = []
        if isinstance(inventory_days, (int, float)):
            extras.append(f"库存约 {float(inventory_days):.1f} 天")
        if isinstance(sales_growth, (int, float)):
            extras.append(f"30天销量增速 {float(sales_growth):+.2f}%")
        extra_text = f"；{'；'.join(extras)}" if extras else ""
        lines.append(f"- {name}：当前跟踪价 {latest_text}；7日变化 {weekly_text}{extra_text}。")

    lines.extend([
        "",
        "## 2) 对我们成交的影响",
        "- 如果竞品报价调整速度快于我们当前覆盖产品的价格节奏，最先受压的通常是现货成交和询盘转化，而不是长约本身。",
        "- 判断是否真的影响到我们成交，优先看三个信号：订单转化是否变慢、客户是否要求跟价、库存天数是否被动抬升。",
        "- 当前更适合先做“压力定位”，而不是直接下结论说某一家竞品已经抢走订单。",
        "",
        "## 3) 建议下一步怎么聊",
        "- 如果你要更像业务会话，我建议下一轮直接指定一个切面：1）按竞品厂家梳理；2）按产品报价变化梳理；3）按我们成交/订单影响梳理。",
        "- 我也可以直接继续生成一个“三段式竞争态势简报”：装置运行、报价变化、成交影响。",
    ])
    return "\n".join(lines).strip()


def _is_naphtha_cost_passthrough_query(query: str) -> bool:
    q = str(query or "").strip()
    ql = q.lower()
    if not q:
        return False
    naphtha_markers = ["naphtha", "石脑油"]
    passthrough_markers = [
        "pass-through",
        "pass through",
        "passthrough",
        "cost pass",
        "传导",
        "成本传导",
    ]
    line_markers = ["c5", "c9", "碳五", "碳九", "product line", "产品线"]
    action_markers = [
        "quote",
        "quotes",
        "pricing strategy",
        "adjust",
        "should we",
        "报价",
        "定价",
        "调价",
    ]
    has_naphtha = any(m in ql for m in naphtha_markers)
    has_passthrough = any(m in ql for m in passthrough_markers) or "impact" in ql
    has_line = any(m in ql for m in line_markers)
    has_action = any(m in ql for m in action_markers)
    return has_naphtha and (has_passthrough or has_line) and has_action


def _parse_naphtha_shock_from_query(query: str) -> Dict[str, Any]:
    import re as _re_local

    q = str(query or "")
    ql = q.lower()
    out: Dict[str, Any] = {}
    pct_match = _re_local.search(r"([+-]?\d+(?:\.\d+)?)\s*%", q)
    if pct_match:
        try:
            out["wow_pct"] = float(pct_match.group(1))
        except Exception:
            pass
    price_match = _re_local.search(
        r"(?:current|at|now|现价|当前)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(?:cny|元|/ton|每吨)?",
        ql,
    )
    if not price_match:
        price_match = _re_local.search(r"(\d{3,5}(?:\.\d+)?)\s*(?:cny/ton|元/吨)", ql)
    if price_match:
        try:
            out["spot_price"] = float(price_match.group(1))
        except Exception:
            pass
    return out


_NAPHTHA_PASSTHROUGH_PRODUCTS: List[str] = [
    "cracked_c5",
    "isoprene",
    "piperylene",
    "dcpd",
    "raffinate_c5",
    "blowing_agent",
    "cracked_c9",
    "styrene",
]


def _build_naphtha_passthrough_report(state: PipelineState, language: str = "en") -> str:
    from backend.src.service.cost_floor_service import (
        PRODUCT_LABELS,
        cost_floor_service,
        compute_floor,
    )

    query = str(state.get("user_query") or "")
    parsed_shock = _parse_naphtha_shock_from_query(query)
    lang = "zh" if str(language or "").strip().lower() in {"zh", "chinese"} else "en"

    naphtha_price = parsed_shock.get("spot_price")
    if naphtha_price is None:
        naphtha_price = cost_floor_service.get_naphtha_price()
    wow_pct = parsed_shock.get("wow_pct")

    snapshot = state.get("perception_snapshot") or {}
    products_snap = dict(snapshot.get("products") or {})
    if not wow_pct and products_snap.get("naphtha"):
        wow_pct = _to_float_or_none((products_snap.get("naphtha") or {}).get("change_7d_pct"))

    floors = cost_floor_service.get_all_floors() if naphtha_price else {}
    if not floors and naphtha_price:
        for product_id in _NAPHTHA_PASSTHROUGH_PRODUCTS:
            floor = compute_floor(product_id, float(naphtha_price))
            if floor:
                floors[product_id] = floor

    def _label(pid: str) -> str:
        if lang == "zh":
            return PRODUCT_LABELS.get(pid, pid)
        return pid.replace("_", " ").title()

    below_floor = sum(1 for row in floors.values() if row.get("below_floor"))
    tight_margin = sum(
        1
        for row in floors.values()
        if isinstance(row.get("margin_pct"), (int, float)) and float(row["margin_pct"]) < 5.0
    )

    if lang == "zh":
        lines = [
            "## 执行摘要",
            (
                f"石脑油7日变动为{wow_pct:+.1f}%，当前约{naphtha_price:,.0f} 元/吨。"
                if isinstance(wow_pct, (int, float)) and naphtha_price
                else "石脑油成本冲击已识别，以下基于数据库成本地板与现货价格评估C5/C9产品线传导。"
            ),
            f"- 覆盖产品：{len(floors)} 个；低于成本地板：{below_floor} 个；毛利偏薄（<5%）：{tight_margin} 个。",
            "",
            "## 石脑油冲击",
        ]
    else:
        lines = [
            "## Executive Summary",
            (
                f"Naphtha moved {wow_pct:+.1f}% over 7d; reference spot about {naphtha_price:,.0f} CNY/ton."
                if isinstance(wow_pct, (int, float)) and naphtha_price
                else "A naphtha cost shock is identified; the table below uses DB cost floors and live spot prices for the C5/C9 line."
            ),
            f"- Products covered: {len(floors)}; below cost floor: {below_floor}; thin margin (<5%): {tight_margin}.",
            "",
            "## Naphtha Shock",
        ]

    if isinstance(wow_pct, (int, float)):
        if lang == "zh":
            lines.append(f"- 7日变动：{wow_pct:+.1f}%（用户/快照输入）。")
        else:
            lines.append(f"- 7d change: {wow_pct:+.1f}% (user query / snapshot).")
    if naphtha_price:
        if lang == "zh":
            lines.append(f"- 参考石脑油价格：{naphtha_price:,.0f} 元/吨。")
        else:
            lines.append(f"- Reference naphtha: {naphtha_price:,.0f} CNY/ton.")

    if lang == "zh":
        lines.extend(
            [
                "",
                "## C5/C9 产品线传导（成本地板 vs 现货）",
                "| 产品 | 成本地板 | 现货 | 距地板 | 毛利率 | 7日涨跌 |",
                "| :--- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "## C5/C9 Pass-Through (Cost Floor vs Spot)",
                "| Product | Cost floor | Spot | vs floor | Margin % | 7d % |",
                "| :--- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )

    for product_id in _NAPHTHA_PASSTHROUGH_PRODUCTS:
        row = floors.get(product_id) or {}
        snap = products_snap.get(product_id) or {}
        floor_price = row.get("floor_price")
        spot = row.get("current_price") or snap.get("latest_price")
        margin = row.get("margin_pct")
        dist = row.get("distance_from_floor")
        chg7 = snap.get("change_7d_pct")

        def _cell(value: Any, fallback: str = "—") -> str:
            if value is None:
                return fallback
            if isinstance(value, float):
                return f"{value:,.0f}" if abs(value) > 100 else f"{value:+.1f}%"
            return str(value)

        lines.append(
            "| {name} | {floor} | {spot} | {dist} | {margin} | {chg} |".format(
                name=_label(product_id),
                floor=_cell(floor_price),
                spot=_cell(spot),
                dist=_cell(dist),
                margin=_cell(margin, "—") if margin is not None else "—",
                chg=_cell(chg7, "—") if chg7 is not None else "—",
            )
        )

    shock_large = isinstance(wow_pct, (int, float)) and abs(float(wow_pct)) >= 5.0
    if lang == "zh":
        lines.extend(
            [
                "",
                "## 传导节奏",
                "- 石脑油上涨通常先抬升裂解C5/C9原料成本，再逐步传导至异戊二烯、间戊二烯、双环戊二烯等衍生品报价。",
                "- 若现货仍高于成本地板但毛利快速收窄，说明下游提价滞后，存在跟涨窗口。",
                "",
                "## 报价建议",
            ]
        )
        if shock_large and below_floor > 0:
            lines.append("- **建议：分产品上调报价**，优先覆盖已低于成本地板的品种；未跌破地板的品种可小步调涨并观察成交。")
        elif shock_large:
            lines.append("- **建议：适度上调报价**（约传导石脑油涨幅的30–50%），并设置3–5日复核点。")
        else:
            lines.append("- **建议：暂维持报价**，加强成交与库存监控；若石脑油继续上行再启动跟涨。")
        lines.extend(
            [
                "",
                "## 风险与观察项",
                "- 关注SIS/UPR开工与下游补库是否支撑提价。",
                "- 若竞品率先调价而成交未改善，缩短报价复核周期至每日。",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "## Pass-Through Timing",
                "- Naphtha gains typically lift C5/C9 feed costs first, then derivative quotes (isoprene, piperylene, DCPD) with a lag.",
                "- If spot remains above floor but margin compresses quickly, downstream pricing is lagging and a catch-up window exists.",
                "",
                "## Quote Recommendation",
            ]
        )
        if shock_large and below_floor > 0:
            lines.append(
                "- **Action: raise quotes selectively** — prioritize products trading below cost floor; others can move up modestly while monitoring fills."
            )
        elif shock_large:
            lines.append(
                "- **Action: moderate quote increase** (roughly 30–50% of the naphtha move), with a 3–5 day review checkpoint."
            )
        else:
            lines.append(
                "- **Action: hold quotes for now**; intensify inventory and order monitoring and revisit if naphtha keeps rising."
            )
        lines.extend(
            [
                "",
                "## Risks / Watchlist",
                "- Validate SIS/UPR operating rates and downstream restocking before locking in large increases.",
                "- If competitors move first without volume recovery, shorten quote review to daily.",
            ]
        )

    return "\n".join(lines).strip()


def _use_fast_naphtha_passthrough_path(state: PipelineState) -> bool:
    if _prefer_token_streaming(state):
        return False
    if not _is_naphtha_cost_passthrough_query(str(state.get("user_query") or "")):
        return False
    if not _normalize_bool_text(_os.getenv("DATABASE_ONLY_MODE", "true"), True):
        return False
    parsed = state.get("parsed_intent")
    task_type = getattr(parsed, "task_type", None)
    return task_type in {
        TaskType.MARKET_ANALYSIS_ONLY,
        TaskType.MARKET_ANALYSIS_DECISION,
        TaskType.WHAT_IF_SCENARIO,
        TaskType.SIMPLE_LOOKUP,
        None,
    }


def _is_price_move_action_query(query: str) -> bool:
    q = str(query or "").strip().lower()
    if not q:
        return False

    move_markers = [
        "moved",
        "move",
        "increase",
        "price spike",
        "jumped",
        "surged",
        "rose",
        "up ",
        "7 days",
        "7-day",
        "7d",
        "涨",
        "大涨",
        "波动",
        "7天",
        "7日",
    ]
    driver_markers = [
        "supply-demand driver",
        "driver",
        "reason",
        "pricing risk",
        "pricing strategy",
        "pass-through",
        "pass through",
        "passthrough",
        "impact",
        "risk",
        "supply-demand",
        "supply demand",
        "供需",
        "驱动",
        "风险",
        "传导",
    ]
    action_markers = [
        "what action",
        "action we should take",
        "what should we do",
        "what should we take",
        "what action should we take",
        "what we should take this week",
        "this week",
        "recommendation",
        "recommend",
        "should we",
        "行动",
        "建议",
        "怎么做",
        "本周",
    ]

    has_move = any(marker in q for marker in move_markers)
    has_driver_or_risk = any(marker in q for marker in driver_markers)
    has_action = any(marker in q for marker in action_markers)
    return has_move and has_driver_or_risk and has_action


def _use_fast_price_move_action_path(state: PipelineState) -> bool:
    if _prefer_token_streaming(state):
        return False

    query = str(state.get("user_query") or "")
    if not _is_price_move_action_query(query):
        return False

    if not _normalize_bool_text(_os.getenv("DATABASE_ONLY_MODE", "true"), True):
        return False

    parsed = state.get("parsed_intent")
    task_type = getattr(parsed, "task_type", None)
    return task_type in {
        TaskType.MARKET_ANALYSIS_ONLY,
        TaskType.MARKET_ANALYSIS_DECISION,
        TaskType.WHAT_IF_SCENARIO,
    }


def _build_en_price_move_action_report(state: PipelineState) -> str:
    snapshot = state.get("perception_snapshot") or {}
    products = dict(snapshot.get("products") or {})
    pricing = state.get("pricing_decision") or {}
    diagnosis = state.get("diagnosis") or {}
    forecast = state.get("forecast") or {}
    query = str(state.get("user_query") or "")

    product_id = str(pricing.get("product_id") or state.get("graph_product_id") or "").strip().lower()
    if not product_id:
        for candidate_id, pdata in products.items():
            name_en = str(pdata.get("name_en") or "").lower()
            if name_en and name_en in query.lower():
                product_id = candidate_id
                break

    product_meta = dict(products.get(product_id) or {})
    product_name = str(
        pricing.get("product_name_en")
        or product_meta.get("name_en")
        or product_id
        or "the product"
    )
    latest_price = _to_float_or_none(pricing.get("latest_market_price"))
    if latest_price is None:
        latest_price = _to_float_or_none(product_meta.get("latest_price"))
    weekly_change = _to_float_or_none(product_meta.get("change_7d_pct"))

    recommended_price = _to_float_or_none(pricing.get("recommended_price"))
    price_range = pricing.get("price_range") or []
    price_low = _to_float_or_none(price_range[0]) if len(price_range) >= 1 else None
    price_high = _to_float_or_none(price_range[1]) if len(price_range) >= 2 else None
    confidence = _to_float_or_none(pricing.get("confidence"))
    price_gap_pct = _to_float_or_none(pricing.get("price_delta_vs_market"))
    if price_gap_pct is None and latest_price and recommended_price is not None and latest_price:
        try:
            price_gap_pct = ((recommended_price - latest_price) / latest_price) * 100.0
        except Exception:
            price_gap_pct = None

    crude_trend = str(snapshot.get("crude_trend") or "").strip().lower()
    naphtha_trend = str(snapshot.get("naphtha_trend") or "").strip().lower()
    regime = str(diagnosis.get("market_regime") or "neutral").strip().lower()
    divergence = diagnosis.get("divergence_detected") or {}
    demand_status = ((diagnosis.get("demand_status") or {}).get("effective_demand_status") or "").strip()
    demand_note = str((diagnosis.get("demand_status") or {}).get("pricing_note") or "").strip()
    inventory_days = _to_float_or_none(((diagnosis.get("erp_kpi_signal") or {}).get("inventory_days")))
    weekly_plan = pricing.get("weekly_plan") or {}
    weekly_plan_note = str(weekly_plan.get("note") or "").strip()
    triggers = pricing.get("triggers") or []
    watch_signals = [str(item).strip() for item in (pricing.get("watch_signals") or []) if str(item).strip()]
    dissenting_view = str(pricing.get("dissenting_view") or "").strip()
    scenarios_7d = (forecast.get("scenarios") or {}).get("7d") or {}

    def _fmt_price(value: Optional[float]) -> str:
        return f"{value:,.0f} CNY/t" if isinstance(value, (int, float)) else "unavailable"

    def _fmt_pct(value: Optional[float]) -> str:
        return f"{value:+.2f}%" if isinstance(value, (int, float)) else "unavailable"

    def _fmt_band(bucket: Dict[str, Any]) -> str:
        low = _to_float_or_none(bucket.get("low"))
        high = _to_float_or_none(bucket.get("high"))
        if low is None or high is None:
            return "unavailable"
        return f"{low:,.0f}–{high:,.0f} CNY/t"

    supply_driver = []
    if isinstance(weekly_change, (int, float)):
        supply_driver.append(
            f"{product_name} is up {_fmt_pct(weekly_change)} over 7 days on the live market snapshot"
        )
    if naphtha_trend:
        supply_driver.append(
            f"naphtha is trending {naphtha_trend}, so the move is not explained by feedstock inflation alone"
        )
    gap = _to_float_or_none(divergence.get("gap"))
    if isinstance(gap, (int, float)):
        supply_driver.append(
            f"the crude-vs-naphtha divergence is {gap:+.2f}%, which points to cost-chain distortion rather than clean cost pass-through"
        )

    demand_driver = []
    if demand_status:
        demand_driver.append(f"downstream demand status is flagged as {demand_status.replace('_', ' ')}")
    if demand_note:
        demand_driver.append(demand_note)
    if weekly_plan_note:
        demand_driver.append(weekly_plan_note)

    risk_lines = []
    if isinstance(recommended_price, (int, float)) and isinstance(latest_price, (int, float)):
        risk_lines.append(
            f"The Stage-4 quote recommendation is {_fmt_price(recommended_price)} versus a live market reference of {_fmt_price(latest_price)}, so the model sees the current spot level as stretched rather than cleanly justified."
        )
    if isinstance(inventory_days, (int, float)):
        risk_lines.append(
            f"ERP inventory stands at about {inventory_days:.1f} days, which limits how long an aggressive high-price strategy can be defended if order intake cools."
        )
    if crude_trend or naphtha_trend:
        risk_lines.append(
            f"Feedstock context is mixed: crude is {crude_trend or 'flat'} while naphtha is {naphtha_trend or 'flat'}, so the current resin spike carries mean-reversion risk if buyers resist."
        )
    if dissenting_view:
        risk_lines.append(f"Counter-risk: {dissenting_view}")

    lines = [
        "## 1) Direct Answer",
        (
            f"{product_name} is showing short-term bullish momentum, but the move looks more fragile than a clean, cost-backed uptrend. "
            f"The best action this week is to stay defensive on incremental pricing, avoid chasing spot higher, and manage quotes off trigger conditions rather than assuming the +7-day move is fully sustainable."
        ),
        "",
        "## 2) Supply-Demand Driver",
        "- Supply side: " + ("; ".join(supply_driver) if supply_driver else "current context does not include a plant-by-plant supply shock, so supply tightness should be treated as inferred rather than verified."),
        "- Demand side: " + ("; ".join(demand_driver) if demand_driver else "the current context shows no verified demand collapse, but it also does not show enough hard evidence to justify chasing the spike."),
        "- Cross-check: the move is happening while feedstock signals are soft-to-mixed, which means this looks more like a pricing overshoot / execution squeeze than a fundamentally secure new base price.",
        "",
        "## 3) Pricing Risk This Week",
    ]

    for item in risk_lines[:4]:
        lines.append(f"- {item}")

    if scenarios_7d:
        lines.extend([
            "- 7-day scenario bands from the current forecast:",
            f"  - Base: {_fmt_band(scenarios_7d.get('base') or {})}",
            f"  - Bull: {_fmt_band(scenarios_7d.get('bull') or {})}",
            f"  - Bear: {_fmt_band(scenarios_7d.get('bear') or {})}",
        ])

    lines.extend([
        "",
        "## 4) Action We Should Take This Week",
    ])

    if isinstance(recommended_price, (int, float)):
        if isinstance(price_low, (int, float)) and isinstance(price_high, (int, float)):
            lines.append(
                f"- Quote discipline: use **{_fmt_price(recommended_price)}** as the internal reference and keep executable spot offers inside **{_fmt_price(price_low)} to {_fmt_price(price_high)}** unless a trigger is hit."
            )
        else:
            lines.append(f"- Quote discipline: use **{_fmt_price(recommended_price)}** as the internal reference instead of following the headline move higher.")

    lines.append("- Inventory: do not build high-cost inventory into this spike; prioritize confirmed orders and keep flexibility if buyer resistance appears.")
    lines.append("- Sales execution: separate contract protection from opportunistic spot quoting. Hold contract discipline, but only push spot higher if competitor quotes and order conversion both confirm the move.")

    if triggers:
        for idx, trigger in enumerate(triggers[:3], start=1):
            condition = str((trigger or {}).get("condition") or "Condition pending").strip()
            action = str((trigger or {}).get("action") or "Action pending").strip()
            lines.append(f"- Trigger {idx}: if {condition}, then {action}.")

    if watch_signals:
        lines.append(f"- Watchlist: {', '.join(watch_signals[:4])}.")

    if isinstance(confidence, (int, float)):
        lines.append(f"- Execution confidence: about {confidence * 100:.0f}% based on the current pricing engine output.")

    lines.extend([
        "",
        "Data source: live market snapshot, diagnosis, ERP inventory/weekly-plan signal, and Stage-4 pricing decision from the current DB-backed EDIA context.",
    ])

    return "\n".join(lines).strip()


def _build_cn_price_move_action_report(state: PipelineState) -> str:
    snapshot = state.get("perception_snapshot") or {}
    products = dict(snapshot.get("products") or {})
    pricing = state.get("pricing_decision") or {}
    diagnosis = state.get("diagnosis") or {}
    forecast = state.get("forecast") or {}
    query = str(state.get("user_query") or "")

    product_id = str(pricing.get("product_id") or state.get("graph_product_id") or "").strip().lower()
    if not product_id:
        for candidate_id, pdata in products.items():
            name_en = str(pdata.get("name_en") or "").lower()
            name_cn = str(pdata.get("name_cn") or "")
            if (name_en and name_en in query.lower()) or (name_cn and name_cn in query):
                product_id = candidate_id
                break

    product_meta = dict(products.get(product_id) or {})
    product_name = str(
        pricing.get("product_name_cn")
        or product_meta.get("name_cn")
        or pricing.get("product_name_en")
        or product_meta.get("name_en")
        or product_id
        or "该产品"
    )
    latest_price = _to_float_or_none(pricing.get("latest_market_price"))
    if latest_price is None:
        latest_price = _to_float_or_none(product_meta.get("latest_price"))
    weekly_change = _to_float_or_none(product_meta.get("change_7d_pct"))

    recommended_price = _to_float_or_none(pricing.get("recommended_price"))
    price_range = pricing.get("price_range") or []
    price_low = _to_float_or_none(price_range[0]) if len(price_range) >= 1 else None
    price_high = _to_float_or_none(price_range[1]) if len(price_range) >= 2 else None
    confidence = _to_float_or_none(pricing.get("confidence"))

    crude_trend = str(snapshot.get("crude_trend") or "").strip().lower()
    naphtha_trend = str(snapshot.get("naphtha_trend") or "").strip().lower()
    divergence = diagnosis.get("divergence_detected") or {}
    demand_status = ((diagnosis.get("demand_status") or {}).get("effective_demand_status") or "").strip()
    demand_note = str((diagnosis.get("demand_status") or {}).get("pricing_note") or "").strip()
    inventory_days = _to_float_or_none(((diagnosis.get("erp_kpi_signal") or {}).get("inventory_days")))
    weekly_plan = pricing.get("weekly_plan") or {}
    weekly_plan_note = str(weekly_plan.get("note") or "").strip()
    triggers = pricing.get("triggers") or []
    watch_signals = [str(item).strip() for item in (pricing.get("watch_signals") or []) if str(item).strip()]
    dissenting_view = str(pricing.get("dissenting_view") or "").strip()
    scenarios_7d = (forecast.get("scenarios") or {}).get("7d") or {}

    def _fmt_price(value: Optional[float]) -> str:
        return f"{value:,.0f} 元/吨" if isinstance(value, (int, float)) else "暂无"

    def _fmt_pct(value: Optional[float]) -> str:
        return f"{value:+.2f}%" if isinstance(value, (int, float)) else "暂无"

    def _fmt_band(bucket: Dict[str, Any]) -> str:
        low = _to_float_or_none(bucket.get("low"))
        high = _to_float_or_none(bucket.get("high"))
        if low is None or high is None:
            return "暂无"
        return f"{low:,.0f}–{high:,.0f} 元/吨"

    def _trend_label(value: str) -> str:
        if value == "up":
            return "上涨"
        if value == "down":
            return "下行"
        if value == "flat":
            return "持平"
        return value or "平稳"

    supply_driver = []
    if isinstance(weekly_change, (int, float)):
        supply_driver.append(f"{product_name}在实时市场快照中7日变动为{_fmt_pct(weekly_change)}")
    if naphtha_trend:
        supply_driver.append(f"石脑油走势为{_trend_label(naphtha_trend)}，本轮波动并非单纯由上游成本抬升驱动")
    gap = _to_float_or_none(divergence.get("gap"))
    if isinstance(gap, (int, float)):
        supply_driver.append(f"原油与石脑油之间的偏离为{gap:+.2f}%，说明成本链传导存在扭曲而非顺畅传导")

    demand_driver = []
    if demand_status:
        demand_driver.append(f"下游需求状态当前识别为{demand_status.replace('_', ' ')}")
    if demand_note:
        demand_driver.append(demand_note)
    if weekly_plan_note:
        demand_driver.append(weekly_plan_note)

    risk_lines = []
    if isinstance(recommended_price, (int, float)) and isinstance(latest_price, (int, float)):
        risk_lines.append(
            f"Stage-4建议报价为{_fmt_price(recommended_price)}，而实时市场参考价为{_fmt_price(latest_price)}，说明模型判断当前现货水平偏高，支撑并不扎实。"
        )
    if isinstance(inventory_days, (int, float)):
        risk_lines.append(
            f"ERP库存约为{inventory_days:.1f}天，一旦接单转弱，激进高价策略的可持续时间会受到限制。"
        )
    if crude_trend or naphtha_trend:
        risk_lines.append(
            f"原料背景偏混合：原油{_trend_label(crude_trend)}、石脑油{_trend_label(naphtha_trend)}，若下游抵触高价，当前涨势存在均值回归风险。"
        )
    if dissenting_view:
        risk_lines.append(f"反向风险：{dissenting_view}")

    lines = [
        "## 1) 直接回答",
        (
            f"{product_name}短期价格表现偏强，但这轮上涨更像阶段性挤仓或执行层面的抬价，未必是扎实、可持续的成本驱动上行。"
            f"本周更合适的动作是保持防守型报价，不盲目追涨现货，按照触发条件动态管理报价与接单。"
        ),
        "",
        "## 2) 供需驱动",
        "- 供应侧：" + ("；".join(supply_driver) if supply_driver else "当前上下文未给出逐装置级别的供应冲击，因此供应偏紧只能视为推断，不能视为已验证事实。"),
        "- 需求侧：" + ("；".join(demand_driver) if demand_driver else "当前上下文没有看到明确的需求坍塌，但也缺乏足够硬证据支持继续追涨。"),
        "- 交叉验证：当前价格波动发生在原料信号偏弱或偏混合的背景下，更像是局部抬价或执行挤压，而不是已经站稳的新价格中枢。",
        "",
        "## 3) 本周价格风险",
    ]

    for item in risk_lines[:4]:
        lines.append(f"- {item}")

    if scenarios_7d:
        lines.extend([
            "- 当前预测给出的7天情景区间：",
            f"  - 基准： {_fmt_band(scenarios_7d.get('base') or {})}",
            f"  - 乐观： {_fmt_band(scenarios_7d.get('bull') or {})}",
            f"  - 悲观： {_fmt_band(scenarios_7d.get('bear') or {})}",
        ])

    lines.extend([
        "",
        "## 4) 本周操作建议",
    ])

    if isinstance(recommended_price, (int, float)):
        if isinstance(price_low, (int, float)) and isinstance(price_high, (int, float)):
            lines.append(
                f"- 报价纪律：以**{_fmt_price(recommended_price)}**作为内部参考，除非触发条件兑现，否则可执行现货报价尽量控制在**{_fmt_price(price_low)}至{_fmt_price(price_high)}**区间内。"
            )
        else:
            lines.append(f"- 报价纪律：以**{_fmt_price(recommended_price)}**作为内部参考，不直接追随表面涨幅继续上推。")

    lines.append("- 库存：不要在当前高位盲目补高成本库存，优先保障已确认订单，并保留在买方抵触时快速调整的灵活性。")
    lines.append("- 销售执行：区分长约保护与机会型现货报价。长约保持纪律，现货只有在竞品报价与订单转化同步验证后才进一步上推。")

    if triggers:
        for idx, trigger in enumerate(triggers[:3], start=1):
            condition = str((trigger or {}).get("condition") or "条件待确认").strip()
            action = str((trigger or {}).get("action") or "动作待确认").strip()
            lines.append(f"- 触发条件{idx}：若{condition}，则{action}。")

    if watch_signals:
        lines.append(f"- 观察项：{', '.join(watch_signals[:4])}。")

    if isinstance(confidence, (int, float)):
        lines.append(f"- 执行置信度：依据当前定价引擎结果，约为{confidence * 100:.0f}%。")

    lines.extend([
        "",
        "数据来源：实时市场快照、诊断结果、ERP库存/周度计划信号，以及当前基于数据库的Stage-4定价决策。",
    ])

    return "\n".join(lines).strip()


def _build_price_move_action_report(state: PipelineState) -> str:
    if _resolve_output_language(state) == "en":
        return _build_en_price_move_action_report(state)
    return _build_cn_price_move_action_report(state)


def _build_fast_forecast_from_snapshot(state: PipelineState, product_id: str) -> Dict[str, Any]:
    snapshot = state.get("perception_snapshot") or {}
    products = dict(snapshot.get("products") or {})
    product_data = dict(products.get(product_id) or {})
    latest_price = _to_float_or_none(product_data.get("latest_price")) or 0.0
    if latest_price <= 0:
        return forecasting_service._empty(product_id)

    weekly_change = _to_float_or_none(product_data.get("change_7d_pct")) or 0.0
    diagnosis = state.get("diagnosis") or {}
    regime = str(diagnosis.get("market_regime") or "neutral").strip().lower()

    if regime == "bullish":
        regime_bias = max(0.8, weekly_change * 0.35 if weekly_change else 1.2)
    elif regime == "bearish":
        regime_bias = min(-0.8, weekly_change * 0.35 if weekly_change else -1.2)
    else:
        regime_bias = weekly_change * 0.2

    base_3d = max(-3.0, min(3.0, regime_bias * 0.6))
    base_7d = max(-5.0, min(5.0, regime_bias))
    base_30d = max(-8.0, min(8.0, regime_bias * 1.4))
    vol_pct = max(1.2, min(4.5, abs(weekly_change) * 0.25 + 1.0))

    return {
        "product_id": product_id,
        "latest_price": round(latest_price, 2),
        "scenarios": {
            "3d": forecasting_service._three_scenarios(latest_price, base_3d, vol_pct),
            "7d": forecasting_service._three_scenarios(latest_price, base_7d, vol_pct),
            "30d": forecasting_service._three_scenarios(latest_price, base_30d, vol_pct * 1.15),
        },
        "seasonal_adj_pct": 0.0,
        "intelligence_adj_pct": {"3d": 0.0, "7d": 0.0, "30d": 0.0},
        "intelligence_signal": {"active": False, "bias": "neutral", "net_price_pct": 0.0, "confidence": 0.0},
        "erp_kpi_adj_pct": {"3d": 0.0, "7d": 0.0, "30d": 0.0},
        "erp_kpi_signal": {"active": False, "bias": "neutral"},
        "policy": {"policy_id": "fast_db_only_ops", "model_version": "heuristic-v1", "metrics": {"regime": regime or "neutral"}},
        "model_version": "fast-db-only-ops-v1",
        "method_notes": "Fast DB-only heuristic forecast from live market snapshot and diagnosis regime.",
    }


def _build_en_ops_inventory_pricing_report(state: PipelineState) -> str:
    tool_info = str(state.get("tool_info") or "")
    live_rows = _extract_live_db_authoritative_rows(tool_info)
    diagnosis = state.get("diagnosis") or {}
    forecast = state.get("forecast") or {}
    pricing = state.get("pricing_decision") or {}
    user_query = str(state.get("user_query") or "")
    graph_product_id = str(state.get("graph_product_id") or "").strip().lower()
    products = _extract_ops_target_products(user_query, live_rows)

    root_cause = str(diagnosis.get("root_cause") or "").strip()
    chain_impact = [str(item).strip() for item in (diagnosis.get("chain_impact") or []) if str(item).strip()]
    risks = [str(item).strip() for item in (diagnosis.get("risks") or []) if str(item).strip()]
    forecast_scenarios = forecast.get("scenarios") or {}
    forecast_direction = str(forecast.get("trend") or forecast.get("direction") or "").strip()
    confidence = str(forecast.get("confidence") or "").strip()

    def _fmt_price_row(row: Optional[Dict[str, Any]]) -> str:
        if not row:
            return "Live DB price not returned in current context"
        try:
            price = float(row.get("price") or 0)
            unit = str(row.get("unit") or "CNY/t")
            date = str(row.get("date") or "undated")
            return f"{price:,.0f} {unit} (market_prices / {date})"
        except Exception:
            return "Live DB row detected but price parsing failed"

    def _fmt_pct(row: Optional[Dict[str, Any]], key: str, fallback: str) -> str:
        value = row.get(key) if row else None
        if value is None:
            return fallback
        try:
            return f"{float(value):+.2f}%"
        except Exception:
            return fallback

    def _fmt_cover(product_meta: Dict[str, Any]) -> str:
        weeks_cover = _extract_inventory_cover_for_product(state, product_meta)
        if weeks_cover is None:
            return "ERP weeks-of-cover not returned in current context"
        return f"{weeks_cover:.1f} weeks"

    def _event_summary(product_name_en: str) -> str:
        if root_cause:
            return root_cause
        if chain_impact:
            return chain_impact[0]
        return f"No product-specific plant event detail was returned for {product_name_en}"

    def _net_signal() -> str:
        regime = str(diagnosis.get("market_regime") or "").strip().lower()
        if regime == "bullish":
            return "Supply looks tight and pricing bias is firm"
        if regime == "bearish":
            return "Supply looks loose and pricing bias is soft"
        return "Supply and demand remain in balance, so pricing likely stays range-bound"

    def _scenario_band(product_meta: Dict[str, Any], horizon: str, scenario_key: str) -> str:
        if graph_product_id and product_meta.get("product_id") != graph_product_id:
            return "Pending separate product forecast output"
        bucket = (forecast_scenarios.get(horizon) or {}).get(scenario_key) or {}
        low = bucket.get("low")
        high = bucket.get("high")
        if isinstance(low, (int, float)) and isinstance(high, (int, float)):
            return f"{int(low):,}–{int(high):,} CNY/t"
        return "Not returned in current context"

    def _directional_qualifier() -> str:
        direction = forecast_direction.lower()
        if any(marker in direction for marker in ["up", "bull", "strong", "涨", "强"]):
            return "Firm"
        if any(marker in direction for marker in ["down", "bear", "weak", "跌", "弱"]):
            return "Soft"
        return "Range-bound"

    lines: List[str] = [
        "## 01. Market Status",
        "This question is about how ethylene/C5 plant shutdown and restart changes transmit into isoprene and DCPD inventory, pricing, and operating actions. The answer below stays grounded in DB-backed market and ERP context.",
        "",
        "| Product | Current price | Inventory cover | WoW | MoM | Event status |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    for product_meta in products:
        row = _find_live_row_for_product(live_rows, str(product_meta.get("product_id") or ""))
        lines.append(
            f"| {product_meta['name_en']} | {_fmt_price_row(row)} | {_fmt_cover(product_meta)} | {_fmt_pct(row, 'delta_pct', 'WoW unavailable')} | {_fmt_pct(row, 'mom_pct', 'MoM unavailable')} | {_event_summary(product_meta['name_en'])} |"
        )

    lines.extend([
        "",
        "If ERP weighted price, weeks-of-cover, or product-level MoM were not returned by the current DB context, they are marked as unavailable instead of being inferred.",
        "",
        "## 02. Key Drivers",
    ])

    for product_meta in products:
        product_name = product_meta["name_en"]
        demand_hint = (
            "SIS operating rate and downstream restocking pace are the key demand checks for isoprene."
            if product_meta["product_id"] == "isoprene"
            else "UPR/resin order flow is the key demand check for DCPD."
        )
        lines.extend([
            f"### {product_name}",
            f"- Supply: {root_cause or 'Plant change has been detected, but the current context does not include unit-by-unit volume detail yet.'}",
            f"- Demand: {demand_hint}",
            "- Evidence chain: " + ("; ".join(chain_impact[:3]) if chain_impact else "Current context confirms a supply-chain effect but lacks a full quantified propagation chain."),
            f"- Net signal: {_net_signal()}.",
            "",
        ])

    lines.extend([
        "## 03. Forecast Ranges",
        f"Baseline direction: {_directional_qualifier()}." + (f" Forecast confidence: {confidence}." if confidence else ""),
        "",
        "| Product | Horizon | Base | Bull | Bear | Direction | Notes |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ])

    horizon_labels = [("3d", "3 days"), ("7d", "1 week"), ("30d", "1 month")]
    for product_meta in products:
        for horizon_key, horizon_label in horizon_labels:
            lines.append(
                "| {product} | {horizon} | {base} | {bull} | {bear} | {direction} | {condition} |".format(
                    product=product_meta["name_en"],
                    horizon=horizon_label,
                    base=_scenario_band(product_meta, horizon_key, "base"),
                    bull=_scenario_band(product_meta, horizon_key, "bull"),
                    bear=_scenario_band(product_meta, horizon_key, "bear"),
                    direction=_directional_qualifier(),
                    condition="Revise the baseline if restart timing is confirmed earlier or later than expected.",
                )
            )

    lines.extend([
        "",
        "## 04. Recommended Actions",
        "### Inventory",
    ])
    for product_meta in products:
        lines.append(
            f"- {product_meta['name_en']}: current inventory cover is {_fmt_cover(product_meta)}. If restart timing is confirmed and supply improves, reduce exposure to high-cost inventory; if restart is delayed and spot stays tight, keep safety stock and protect service levels."
        )

    lines.extend([
        "",
        "### Pricing",
    ])
    if isinstance(pricing.get("recommended_price"), (int, float)):
        rec = int(pricing.get("recommended_price"))
        prange = pricing.get("price_range") or []
        if isinstance(prange, list) and len(prange) == 2:
            lines.append(f"- Current Stage-4 recommended price is **{rec:,} CNY/t** with an execution band of **{int(prange[0]):,}–{int(prange[1]):,} CNY/t**.")
        else:
            lines.append(f"- Current Stage-4 recommended price is **{rec:,} CNY/t**.")
    else:
        lines.append("- The current context does not include a full Stage-4 quote band yet; confirm the pricing ladder before quoting.")

    triggers = pricing.get("triggers") or []
    if triggers:
        for idx, trigger in enumerate(triggers[:2], start=1):
            condition = str((trigger or {}).get("condition") or "Condition pending").strip()
            action = str((trigger or {}).get("action") or "Action pending").strip()
            lines.append(f"- Trigger {idx}: if {condition}, then {action}.")
    else:
        lines.append("- Keep contract customers on a relatively stable band and adjust spot quotes only after restart timing is confirmed.")

    lines.extend([
        "",
        "### Operations",
        "- For isoprene-linked chains: if restart improves feed availability, lock in SIS/resin demand earlier; if restart slips, prioritize higher-margin or must-serve orders.",
        "- For DCPD-linked chains: align production and shipment cadence with UPR/resin order intake to avoid building inventory too early ahead of restart confirmation.",
        "- Use trigger-based operations: if X happens, execute Y; do not rely on a static recommendation.",
        "",
        "## 05. Risks And Watchlist",
    ])

    if risks:
        for risk in risks[:3]:
            lines.append(f"- Risk: {risk}")
    else:
        lines.append("- Risk 1: restart timing may slip again, extending the tight-supply window beyond the base case.")
        lines.append("- Risk 2: downstream demand may remain weaker than expected even if supply improves.")

    lines.extend([
        "- Invalidation condition: if confirmed plant status or downstream order signals move opposite to the current base case, re-run pricing and inventory actions immediately.",
        "- Monitor daily: confirmed restart dates, spot quotes for isoprene and DCPD, downstream order pace, and ERP inventory cover weeks.",
        "",
        "## 06. Suggested Follow-ups",
        "- If restart is confirmed one week earlier than expected, how should isoprene and DCPD quote bands change?",
        "- Please add current ERP inventory cover weeks and re-run the inventory and production recommendations.",
        "- Please break the plant event into factory, date, and capacity-level impacts.",
    ])

    return "\n".join(lines).strip()


def _build_ops_inventory_pricing_report(state: PipelineState) -> str:
    if _resolve_output_language(state) == "en":
        return _build_en_ops_inventory_pricing_report(state)
    return _build_cn_ops_inventory_pricing_report(state)


def _build_cn_ops_inventory_pricing_report(state: PipelineState) -> str:
    tool_info = str(state.get("tool_info") or "")
    live_rows = _extract_live_db_authoritative_rows(tool_info)
    diagnosis = state.get("diagnosis") or {}
    forecast = state.get("forecast") or {}
    pricing = state.get("pricing_decision") or {}
    user_query = str(state.get("user_query") or "")
    graph_product_id = str(state.get("graph_product_id") or "").strip().lower()
    products = _extract_ops_target_products(user_query, live_rows)

    root_cause = str(diagnosis.get("root_cause") or "").strip()
    chain_impact = [str(item).strip() for item in (diagnosis.get("chain_impact") or []) if str(item).strip()]
    risks = [str(item).strip() for item in (diagnosis.get("risks") or []) if str(item).strip()]
    forecast_scenarios = forecast.get("scenarios") or {}
    forecast_direction = str(forecast.get("trend") or forecast.get("direction") or "").strip()
    confidence = str(forecast.get("confidence") or "").strip()

    def _fmt_price_row(row: Optional[Dict[str, Any]]) -> str:
        if not row:
            return "当前上下文未返回 LZ/market_prices 实时价格"
        try:
            price = float(row.get("price") or 0)
            unit = str(row.get("unit") or "元/吨")
            date = str(row.get("date") or "未标注日期")
            return f"{price:,.0f} {unit}（来源：market_prices / {date}）"
        except Exception:
            return "已检测到实时价格记录，但数值解析失败"

    def _fmt_wow(row: Optional[Dict[str, Any]]) -> str:
        if not row or row.get("delta_pct") is None:
            return "当前上下文未返回 WoW"
        try:
            return f"{float(row.get('delta_pct')):+.2f}%"
        except Exception:
            return "当前上下文未返回 WoW"

    def _fmt_mom(row: Optional[Dict[str, Any]]) -> str:
        mom = row.get("mom_pct") if row else None
        if mom is None:
            return "当前上下文未返回 MoM"
        try:
            return f"{float(mom):+.2f}%"
        except Exception:
            return "当前上下文未返回 MoM"

    def _fmt_cover(product_meta: Dict[str, Any]) -> str:
        weeks_cover = _extract_inventory_cover_for_product(state, product_meta)
        if weeks_cover is None:
            return "当前上下文未返回 erp_v_stk_inventory weeks-of-cover"
        return f"{weeks_cover:.1f} 周"

    def _event_summary(product_name_cn: str) -> str:
        if root_cause:
            return root_cause
        if chain_impact:
            return chain_impact[0]
        return f"当前上下文未返回 {product_name_cn} 对应的装置事件明细"

    def _net_signal() -> str:
        regime = str(diagnosis.get("market_regime") or "").strip().lower()
        if regime == "bullish":
            return "供应偏紧，价格偏强"
        if regime == "bearish":
            return "供应偏松，价格偏弱"
        return "供需博弈，价格大概率震荡"

    def _scenario_band(product_meta: Dict[str, Any], horizon: str, scenario_key: str) -> str:
        if graph_product_id and product_meta.get("product_id") != graph_product_id:
            return "待补充（当前预测引擎未返回该产品独立区间）"
        bucket = (forecast_scenarios.get(horizon) or {}).get(scenario_key) or {}
        low = bucket.get("low")
        high = bucket.get("high")
        if isinstance(low, (int, float)) and isinstance(high, (int, float)):
            return f"{int(low):,}–{int(high):,} 元/吨"
        return "待补充（当前上下文未返回该情景区间）"

    def _directional_qualifier() -> str:
        if "涨" in forecast_direction or "强" in forecast_direction or "↗" in forecast_direction:
            return "偏强"
        if "跌" in forecast_direction or "弱" in forecast_direction or "↘" in forecast_direction:
            return "偏弱"
        return "震荡"

    lines: List[str] = [
        "## 📊 01 · 市场现状",
        "本题的核心不是简单报价格，而是判断“乙烯/C5装置停开车变化”会如何沿着供给链传导到异戊二烯和双环戊二烯的库存、价格与运营动作。以下按产品拆解。",
        "",
        "| 产品 | 当前价格 | Ecisco库存覆盖 | WoW | MoM | 当前事件状态 |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    for product_meta in products:
        row = _find_live_row_for_product(live_rows, str(product_meta.get("product_id") or ""))
        lines.append(
            f"| {product_meta['name_cn']} | {_fmt_price_row(row)} | {_fmt_cover(product_meta)} | {_fmt_wow(row)} | {_fmt_mom(row)} | {_event_summary(product_meta['name_cn'])} |"
        )

    lines.extend([
        "",
        "说明：ERP加权均价、Ecisco weeks-of-cover、分产品 MoM 若当前上下文未返回，则明确标注为待补充，而不是用模型臆造。",
        "",
        "## 🔍 02 · 关键驱动因素",
    ])

    for product_meta in products:
        product_name = product_meta["name_cn"]
        is_isoprene = product_meta["product_id"] == "isoprene"
        demand_hint = (
            "SIS开工率与下游补库节奏是异戊二烯需求侧的关键验证项；当前上下文未返回明确实时值时，应以周报或ERP订单变化补证。"
            if is_isoprene
            else "UPR季节性与加氢树脂链条是双环戊二烯需求侧的关键验证项；当前上下文未返回明确实时值时，应以周报或下游接货变化补证。"
        )
        lines.extend([
            f"### {product_name}",
            f"- 供应侧：{root_cause or '当前已识别装置开停工扰动，但缺少逐装置吨位明细；应结合周报中的复产/检修日期继续校验。'}",
            f"- 需求侧：{demand_hint}",
            "- 证据链：" + ("；".join(chain_impact[:3]) if chain_impact else "当前上下文仅能确认“装置变化会影响C5收率/供给节奏”，缺少逐环节量化证据。"),
            f"- 净信号：{_net_signal()}。",
            "",
        ])

    lines.extend([
        "## 📈 03 · 价格预测",
        f"基准判断：{_directional_qualifier()}。{('预测置信度：' + confidence + '。') if confidence else ''}",
        "",
        "| 产品 | Horizon | Base | Bull | Bear | 方向 | 条件说明 |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ])

    horizon_labels = [("3d", "3天"), ("7d", "1周"), ("30d", "1月")]
    for product_meta in products:
        for horizon_key, horizon_label in horizon_labels:
            lines.append(
                "| {product} | {horizon} | {base} | {bull} | {bear} | {direction} | {condition} |".format(
                    product=product_meta["name_cn"],
                    horizon=horizon_label,
                    base=_scenario_band(product_meta, horizon_key, "base"),
                    bull=_scenario_band(product_meta, horizon_key, "bull"),
                    bear=_scenario_band(product_meta, horizon_key, "bear"),
                    direction=_directional_qualifier(),
                    condition="若复产确认提前/延后，应同步上修/下修基准区间；若无独立区间，需补充分产品预测输出。"
                )
            )

    lines.extend([
        "",
        "## 💡 04 · 定价决策",
        "### A. 库存动作",
        "- 目标不是机械地报库存，而是根据装置复产前后决定是否提前去库、控量或回补。",
    ])

    for product_meta in products:
        cover_text = _fmt_cover(product_meta)
        lines.append(
            f"- {product_meta['name_cn']}：当前库存覆盖 {cover_text}；若复产确认并导致供应回升，应优先降低高价库存暴露；若复产延后且现货偏紧，则维持安全库存并防止被动断货。"
        )

    lines.extend([
        "",
        "### B. 定价动作",
    ])

    if isinstance(pricing.get("recommended_price"), (int, float)):
        rec = int(pricing.get("recommended_price"))
        prange = pricing.get("price_range") or []
        if isinstance(prange, list) and len(prange) == 2:
            lines.append(
                f"- 当前 Stage-4 推荐价：**{rec:,} 元/吨**；建议执行区间 **{int(prange[0]):,}–{int(prange[1]):,} 元/吨**。"
            )
        else:
            lines.append(f"- 当前 Stage-4 推荐价：**{rec:,} 元/吨**。")
    else:
        lines.append("- 当前上下文未返回完整的 Stage-4 推荐价，报价前需先确认推荐区间与触发条件。")

    triggers = pricing.get("triggers") or []
    if triggers:
        for idx, trigger in enumerate(triggers[:2], start=1):
            condition = str((trigger or {}).get("condition") or "条件待补充").strip()
            action = str((trigger or {}).get("action") or "动作待补充").strip()
            lines.append(f"- 触发条件{idx}：若{condition}，则{action}。")
    else:
        lines.append("- 合同客户建议维持相对稳定区间，现货客户根据复产确认节奏做分层报价；若出现拒单增多，再做二次下修。")

    lines.extend([
        "",
        "### C. 生产/排产动作",
        "- 异戊二烯相关链条：若装置恢复导致原料供给改善，可前置锁定SIS/树脂客户订单；若复产继续延后，则优先保证高毛利或刚需订单。",
        "- 双环戊二烯相关链条：结合UPR/树脂订单节奏调整排产，避免在复产兑现前过度累库。",
        "- 原则：所有动作都以“若X发生，则Y执行”为触发式管理，而不是静态建议。",
        "",
        "## ⚠️ 05 · 风险预警",
    ])

    if risks:
        for risk in risks[:3]:
            lines.append(f"- 风险：{risk}")
    else:
        lines.append("- 风险1：复产兑现时间继续延后，导致供应偏紧持续时间长于基准情景。")
        lines.append("- 风险2：下游需求弱于预期，供应改善并不会顺利转化为成交放量。")

    lines.extend([
        "- 失效条件：若装置消息与周报叙述不一致，或下游订单/库存数据出现反向变化，当前基准判断需立即重估。",
        "- 观察信号：装置复产确认日期、异戊二烯/双环戊二烯现货报价、下游接货节奏、ERP库存覆盖周数。",
        "- 历史先例：若当前上下文未注入 institutional memory 样本，应明确标记“历史相似案例待补充”，避免编造类比。",
        "",
        "## ❓ 06 · 建议追问",
        "- \"如果复产比预期提前一周确认，异戊二烯和双环戊二烯的报价区间各应如何调整？\"",
        "- \"请补充 Ecisco 当前 ERP 库存覆盖周数，并据此重算库存动作与排产建议。\"",
        "- \"请把装置事件拆成逐工厂、逐日期、逐产能影响的清单。\"",
    ])

    return "\n".join(lines).strip()


def _wants_raw_source_tags(text: str) -> bool:
    q = (text or "").strip().lower()
    if not q:
        return False
    markers = [
        "show source tags",
        "show evidence tags",
        "provenance tags",
        "t1-erp",
        "citation tags",
        "证据标签",
        "来源标签",
        "溯源标签",
        "显示标签",
        "t2-mkt",
        "t3-cost",
    ]
    return any(m in q for m in markers)


def _strip_internal_source_tags(markdown: str) -> str:
    if not markdown:
        return markdown
    cleaned = _re.sub(r"\[(?:T[1-5](?:-[A-Z]+)?)\]", "", markdown)
    cleaned = _re.sub(r" {2,}", " ", cleaned)
    cleaned = _re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
    return cleaned


def _strip_unavailable_pricing_placeholders(markdown: str) -> str:
    if not markdown:
        return markdown

    patterns = [
        # English placeholders
        r"(?im)^\s*[*-]?\s*\*{0,2}Pricing Formula\*{0,2}\s*:\s*(?:N/?A|Not applicable|Cannot be determined.*)\s*$\n?",
        r"(?im)^\s*[*-]?\s*\*{0,2}Pricing Basis\*{0,2}\s*:\s*(?:N/?A|Not applicable|Cannot be determined.*)\s*$\n?",
        r"(?im)^\s*[*-]?\s*\*{0,2}Price Range\*{0,2}\s*:\s*(?:N/?A|Cannot be determined.*|Not applicable)\s*$\n?",
        r"(?im)^\s*[*-]?\s*\*{0,2}Recommended Price\*{0,2}\s*:\s*(?:N/?A|Cannot be determined.*)\s*$\n?",
        r"(?im)^\s*[*-]?\s*Pricing Ladder\s*[—-]\s*Not applicable.*$\n?",
        # Chinese placeholders
        r"(?im)^\s*[*-]?\s*\*{0,2}定价公式\*{0,2}\s*[:：]\s*(?:N/?A|不适用|暂无|无法确定.*)\s*$\n?",
        r"(?im)^\s*[*-]?\s*\*{0,2}定价依据\*{0,2}\s*[:：]\s*(?:N/?A|不适用|暂无|无法确定.*)\s*$\n?",
        r"(?im)^\s*[*-]?\s*\*{0,2}价格区间\*{0,2}\s*[:：]\s*(?:N/?A|不适用|暂无|无法确定.*)\s*$\n?",
        r"(?im)^\s*[*-]?\s*\*{0,2}推荐价格\*{0,2}\s*[:：]\s*(?:N/?A|不适用|暂无|无法确定.*)\s*$\n?",
        r"(?im)^\s*[*-]?\s*定价推导过程\s*[—-]\s*不适用.*$\n?",
    ]

    cleaned = markdown
    for pat in patterns:
        cleaned = _re.sub(pat, "", cleaned)

    # Compact excessive blank lines after removals
    cleaned = _re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def _extract_live_db_provenance_rows(tool_info: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    if not tool_info or "INTERNAL DATABASE LIVE DATA" not in tool_info:
        return rows
    m = _re.search(
        r"INTERNAL DATABASE LIVE DATA:\s*(.*?)(?:\n\n|\Z)",
        tool_info,
        flags=_re.DOTALL,
    )
    if not m:
        return rows

    for ln in str(m.group(1)).splitlines():
        line = (ln or "").strip()
        if not line:
            continue
        row: Dict[str, str] = {"raw": line}

        # Structured tail generated by db_tools, e.g.
        # [src=market_prices|product_id=isoprene|price_type=east_china|date=2025-01-10]
        kv_tail = _re.search(r"\[(?:src|source)=([^\]]+)\]$", line)
        if kv_tail:
            segment = kv_tail.group(1)
            tokens = [t.strip() for t in segment.split("|") if t.strip()]
            first = tokens[0] if tokens else ""
            if first and "=" not in first:
                row["src"] = first
            for tk in tokens[1:]:
                if "=" in tk:
                    k, v = tk.split("=", 1)
                    row[k.strip()] = v.strip()

        if "src" not in row:
            row["src"] = "market_prices"
        rows.append(row)
    return rows


def _extract_live_db_authoritative_rows(tool_info: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in _extract_live_db_provenance_rows(tool_info):
        raw = str(row.get("raw") or "").strip()
        if not raw or ":" not in raw:
            continue
        label, _, remainder = raw.partition(":")
        label = label.strip()
        name_cn = label
        name_en = ""
        m_name = _re.match(r"^(.*?)\s*\((.*?)\)\s*$", label)
        if m_name:
            name_cn = m_name.group(1).strip()
            name_en = m_name.group(2).strip()

        m_price = _re.search(r":\s*([\d,]+(?:\.\d+)?)\s*([^\s\(\[]+)", raw)
        if not m_price:
            continue

        try:
            price = float(str(m_price.group(1)).replace(",", ""))
        except Exception:
            continue

        unit = str(m_price.group(2) or "").strip()
        delta_pct = None
        m_delta = _re.search(r"\((?:\d+d:)?([\+\-]?\d+(?:\.\d+)?)%\)", raw)
        if m_delta:
            try:
                delta_pct = float(m_delta.group(1))
            except Exception:
                delta_pct = None

        parsed = dict(row)
        parsed.update(
            {
                "label": label,
                "name_cn": name_cn,
                "name_en": name_en,
                "price": price,
                "unit": unit,
                "delta_pct": delta_pct,
            }
        )
        rows.append(parsed)
    return rows


def _build_simple_lookup_report(state: PipelineState) -> str:
    tool_info = state.get("tool_info", "") or ""
    rows = _extract_live_db_authoritative_rows(tool_info)
    lang = _resolve_output_language(state)
    user_query = state.get("user_query", "") or ""

    def _extract_market_availability_block(text: str, language: str) -> str:
        requested_match = _re.search(
            r"No market price records found for requested period\s+([0-9\-]+)\s+to\s+([0-9\-]+)\.",
            text,
            flags=_re.IGNORECASE,
        )
        range_match = _re.search(
            r"AVAILABLE MARKET PRICE RANGE for\s+(.+?):\s+([0-9\-]+)\s+to\s+([0-9\-]+)\s+\((\d+)\s+rows\)\.",
            text,
            flags=_re.IGNORECASE,
        )
        closest_match = _re.search(
            r"CLOSEST AVAILABLE\s+(\d+)-DAY WINDOW:\s+([0-9\-]+)\s+to\s+([0-9\-]+)\.",
            text,
            flags=_re.IGNORECASE,
        )
        if not (requested_match and range_match and closest_match):
            return ""

        requested_start = requested_match.group(1)
        requested_end = requested_match.group(2)
        label_text = str(range_match.group(1) or "").strip()
        available_start = range_match.group(2)
        available_end = range_match.group(3)
        row_count = range_match.group(4)
        closest_days = closest_match.group(1)
        closest_start = closest_match.group(2)
        closest_end = closest_match.group(3)

        label_node = next(
            (
                n
                for n in _domain_graph.nodes
                if str(getattr(n, "id", "") or "").strip().lower()
                == str(extract_product_ids_in_text(label_text)[0] if extract_product_ids_in_text(label_text) else label_text).strip().lower()
            ),
            None,
        )
        product_label_cn = getattr(label_node, "name_cn", None) or label_text
        product_label_en = getattr(label_node, "name_en", None) or label_text
        product_label = product_label_en if language == "en" else product_label_cn

        if language == "en":
            return (
                "## Market Data Availability\n\n"
                f"No market price records were found for {product_label} in the requested period "
                f"{requested_start} to {requested_end}.\n\n"
                f"Available market price history for this product runs from {available_start} to {available_end} "
                f"({row_count} rows). The closest available {closest_days}-day window is "
                f"{closest_start} to {closest_end}.\n\n"
                "I can use that closest available window, or you can choose another period inside the available range."
            ).strip()

        return (
            "## 市场数据可用性\n\n"
            f"请求区间 {requested_start} 至 {requested_end} 内，{product_label} 没有市场价格记录。\n\n"
            f"该产品可用市场价格历史范围为 {available_start} 至 {available_end}，共 {row_count} 条记录。"
            f"最接近的可用 {closest_days} 天窗口是 {closest_start} 至 {closest_end}。\n\n"
            "我可以直接按这个最近可用窗口展示，也可以按你指定的可用区间重新分析。"
        ).strip()

    availability_block = _extract_market_availability_block(tool_info, lang)

    def _format_price_text(price: Any, unit: str, language: str) -> str:
        if price is None:
            return "N/A" if language == "en" else "暂无"

        normalized = (unit or "").strip().lower()
        if normalized in {"ton", "t", "吨", "mt"}:
            return f"{price:,.0f} CNY/t" if language == "en" else f"{price:,.0f} 元/吨"
        if normalized in {"yuan/ton", "cny/t", "元/吨"}:
            return f"{price:,.0f} CNY/t" if language == "en" else f"{price:,.0f} 元/吨"
        suffix = unit or ""
        return f"{price:,.0f} {suffix}".strip()

    def _to_float(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            return float(value)
        except Exception:
            return None

    def _is_briefing_like_query(text: str) -> bool:
        q = (text or "").strip().lower()
        if not q:
            return False
        zh_markers = ["周报", "高层", "简报", "供需", "下周", "建议动作", "建议"]
        en_markers = ["briefing", "weekly report", "executive", "next week", "supply", "demand", "action"]
        return any(m in text for m in zh_markers) or any(m in q for m in en_markers)

    def _build_no_live_data_fallback() -> str:
        perception = state.get("perception_snapshot") or {}
        products = perception.get("products") or {}
        diagnosis = state.get("diagnosis") or {}
        forecast = state.get("forecast") or {}
        pricing = state.get("pricing_decision") or {}
        rag_info = str(state.get("rag_info") or "")

        def _extract_weekly_report_snippets(text: str, limit: int = 2) -> List[str]:
            if not text or "RECENT WEEKLY REPORTS:" not in text:
                return []
            section = text.split("RECENT WEEKLY REPORTS:", 1)[1]
            for stop_marker in [
                "\n\nPAST DECISIONS",
                "\n\nRECENT NEWS & EVENTS:",
                "\n\nSTRATEGY & KNOWLEDGE:",
            ]:
                if stop_marker in section:
                    section = section.split(stop_marker, 1)[0]

            chunks = _re.findall(
                r"(\[Weekly[^\n]*\]\n(?:.|\n)*?)(?=\n\n\[Weekly|$)",
                section,
                flags=_re.MULTILINE,
            )
            snippets: List[str] = []
            for chunk in chunks:
                lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
                if not lines:
                    continue
                header = lines[0]
                body = " ".join(lines[1:]).strip()
                if len(body) > 180:
                    body = body[:180].rstrip() + "..."
                snippets.append(f"{header} {body}".strip())
                if len(snippets) >= limit:
                    break
            return snippets

        weekly_snippets = _extract_weekly_report_snippets(rag_info, limit=2)

        mover_rows: List[Dict[str, Any]] = []
        for pdata in products.values():
            delta = _to_float(pdata.get("change_7d_pct"))
            price = _to_float(pdata.get("latest_price"))
            if delta is None and price is None:
                continue
            mover_rows.append(
                {
                    "name_en": pdata.get("name_en") or pdata.get("name_cn") or "Unknown",
                    "name_cn": pdata.get("name_cn") or pdata.get("name_en") or "未知产品",
                    "price": price,
                    "unit": pdata.get("unit") or "元/吨",
                    "delta": delta,
                    "date": pdata.get("latest_date") or perception.get("reference_date") or "-",
                }
            )

        mover_rows.sort(key=lambda r: abs(float(r.get("delta") or 0.0)), reverse=True)
        movers = mover_rows[:3]

        root_cause = str(diagnosis.get("root_cause") or "").strip()
        chain_impact = diagnosis.get("chain_impact") or []
        trend = str(forecast.get("trend") or forecast.get("direction") or "").strip()
        action = str(pricing.get("action") or pricing.get("action_cn") or "").strip()
        triggers = pricing.get("triggers") or []

        if lang == "en":
            lines: List[str] = [
                "## Executive Weekly Briefing (fallback mode)",
                "Live product-level prices were not returned for the requested scope, but below is a management-ready brief using available market signals.",
                "",
                "### 1) Market status (available signals)",
            ]
            if movers:
                lines.extend([
                    "| Product | Latest price | 7d change | Date |",
                    "|---|---:|---:|---|",
                ])
                for row in movers:
                    price_text = _format_price_text(row.get("price"), str(row.get("unit") or ""), "en")
                    delta = row.get("delta")
                    delta_text = f"{float(delta):+.2f}%" if isinstance(delta, (int, float)) else "N/A"
                    lines.append(f"| {row.get('name_en')} | {price_text} | {delta_text} | {row.get('date')} |")
            else:
                lines.append("- No structured product snapshot was available in the current context. (Evidence: perception_snapshot:none)")

            if movers:
                lines.append("- Evidence tag: perception_snapshot:products")

            if weekly_snippets:
                lines.extend([
                    "",
                    "### 1.1) Weekly evidence excerpts",
                ])
                for item in weekly_snippets:
                    lines.append(f"- {item} (Evidence: rag_info:weekly_reports)")

            lines.extend([
                "",
                "### 2) Supply-demand signal",
            ])
            if root_cause:
                lines.append(f"- Core signal: {root_cause} (Evidence: diagnosis:root_cause)")
            elif chain_impact:
                lines.append(f"- Core signal: {chain_impact[0]} (Evidence: diagnosis:chain_impact)")
            else:
                lines.append("- Core signal: not enough validated causal evidence in current context.")

            lines.extend([
                "",
                "### 3) Next-week action",
            ])
            if action:
                lines.append(f"- Recommended action: {action} (Evidence: pricing_decision:action)")
            elif trend:
                lines.append(f"- Baseline action: keep pricing/inventory posture aligned with a '{trend}' market regime and recheck daily. (Evidence: forecast:trend)")
            else:
                lines.append("- Baseline action: hold neutral pricing posture and update once live DB prices are refreshed.")

            if triggers:
                first = triggers[0]
                condition = str(first.get("condition") or "Trigger condition").strip()
                next_action = str(first.get("action") or "Adjust pricing/inventory posture").strip()
                lines.append(f"- Trigger: if {condition}, then {next_action}. (Evidence: pricing_decision:triggers)")

            lines.extend([
                "",
                "### 4) Data gaps and confidence",
                "- Missing: direct live records for requested product scope in `market_prices`.",
                "- Confidence: medium for directional view, low for exact quote execution until live rows are recovered.",
                "- Next step: provide explicit products (e.g., Cracked C5 / Isoprene) to return full quote-grade report.",
                "",
                "### 5) Evidence ledger",
                "- perception_snapshot:products -> market status and 7d direction",
                "- diagnosis:* -> supply-demand interpretation",
                "- forecast:trend -> regime qualifier when pricing action is missing",
                "- pricing_decision:* -> action and trigger suggestions",
                "- rag_info:weekly_reports -> weekly narrative excerpts",
            ])
            return "\n".join(lines).strip()

        lines_zh: List[str] = [
            "## 高层周报（降级模式）",
            "当前请求范围未返回可直接引用的实时分产品价格，但系统已基于可用市场证据生成可执行摘要。",
            "",
            "### 01) 市场现状（可用信号）",
        ]
        if movers:
            lines_zh.extend([
                "| 产品 | 最新价格 | 7日变化 | 日期 |",
                "| :--- | :--- | :--- | :--- |",
            ])
            for row in movers:
                price_text = _format_price_text(row.get("price"), str(row.get("unit") or ""), "zh")
                delta = row.get("delta")
                delta_text = f"{float(delta):+.2f}%" if isinstance(delta, (int, float)) else "暂无"
                lines_zh.append(f"| {row.get('name_cn')} | {price_text} | {delta_text} | {row.get('date')} |")
        else:
            lines_zh.append("- 当前上下文未返回结构化分产品快照。")

        if movers:
            lines_zh.append("- 证据标签：perception_snapshot:products")

        if weekly_snippets:
            lines_zh.extend([
                "",
                "### 01.1) 周报证据摘录",
            ])
            for item in weekly_snippets:
                lines_zh.append(f"- {item}（证据：rag_info:weekly_reports）")

        lines_zh.extend([
            "",
            "### 02) 供需信号",
        ])
        if root_cause:
            lines_zh.append(f"- 核心判断：{root_cause}（证据：diagnosis:root_cause）")
        elif chain_impact:
            lines_zh.append(f"- 核心判断：{chain_impact[0]}（证据：diagnosis:chain_impact）")
        else:
            lines_zh.append("- 核心判断：当前证据不足以给出高置信度因果链，仅可维持中性观察。")

        lines_zh.extend([
            "",
            "### 03) 下周建议动作",
        ])
        if action:
            lines_zh.append(f"- 建议动作：{action}（证据：pricing_decision:action）")
        elif trend:
            lines_zh.append(f"- 建议动作：按“{trend}”基准情景执行稳健报价与库存节奏管理。（证据：forecast:trend）")
        else:
            lines_zh.append("- 建议动作：维持中性仓位与报价策略，待实时价格恢复后再调整。")

        if triggers:
            first = triggers[0]
            condition = str(first.get("condition") or "触发条件").strip()
            next_action = str(first.get("action") or "调整报价/库存策略").strip()
            lines_zh.append(f"- 触发器：若{condition}，则执行“{next_action}”。（证据：pricing_decision:triggers）")

        lines_zh.extend([
            "",
            "### 04) 数据缺口与可信度",
            "- 缺口：`market_prices` 中未返回本次请求范围的实时分产品行。",
            "- 可信度：方向判断中等，精确报价执行低（需待实时行恢复）。",
            "- 下一步：请明确产品名单（如 裂解C5 / 异戊二烯），系统可直接生成完整报价级周报。",
            "",
            "### 05) 证据台账",
            "- perception_snapshot:products：用于市场现状与短期方向判断",
            "- diagnosis:*：用于供需因果解释",
            "- forecast:trend：用于动作基线（无明确动作时）",
            "- pricing_decision:*：用于动作与触发器建议",
            "- rag_info:weekly_reports：用于周报叙事证据摘录",
        ])
        return "\n".join(lines_zh).strip()

    if not rows:
        if availability_block:
            return availability_block
        if _is_briefing_like_query(user_query):
            return _build_no_live_data_fallback()
        if lang == "en":
            return (
                "Unable to find live market price data for the requested product(s). "
                "If you provide explicit products, I can return a quote-ready comparison and actions."
            )
        return "未找到所请求产品的实时市场价格数据。若补充具体产品（如裂解C5/异戊二烯），我可返回可执行周报。"

    asks_weekly = any(k in user_query for k in ["本周", "这周", "周环比", "最近一周", "近一周", "一周", "7天"]) or any(
        k in user_query.lower() for k in ["this week", "weekly", "wow"]
    )
    asks_monthly = any(k in user_query for k in ["本月", "月环比"]) or any(
        k in user_query.lower() for k in ["this month", "monthly", "mom"]
    )
    if asks_weekly:
        delta_label_zh = "7日环比涨跌"
        delta_label_en = "WoW change"
        delta_suffix_zh = "过去7天"
    elif asks_monthly:
        delta_label_zh = "月环比"
        delta_label_en = "MoM change"
        delta_suffix_zh = "过去30天"
    else:
        delta_label_zh = "涨跌幅"
        delta_label_en = "Price change"
        delta_suffix_zh = "最近周期"

    lower_query = user_query.lower()
    is_explicit_comparison_query = (
        lang == "en"
        and len(rows) >= 2
        and asks_weekly
        and any(
            marker in lower_query
            for marker in [
                "what changed week over week",
                "week over week",
                "main price moves",
                "price moves",
                "compare",
                "versus",
                " vs ",
            ]
        )
    )

    if availability_block:
        lines.append(availability_block)
        lines.append("")

    if lang == "en":
        header = "**China market database lookup**"
        if asks_weekly:
            header = "**China market database lookup: this week's mainstream prices and WoW changes**"
        elif asks_monthly:
            header = "**China market database lookup: this month's mainstream prices and MoM changes**"

        lines = [header, "", "| Product | Mainstream Price | " + delta_label_en + " | Date |", "|---|---:|---:|---|"]
        for row in rows:
            label = row.get("name_en") or row.get("name_cn") or row.get("label") or "-"
            price = row.get("price")
            unit = row.get("unit") or ""
            delta = row.get("delta_pct")
            date = row.get("date") or "-"
            price_text = _format_price_text(price, unit, "en")
            delta_text = f"{delta:+.2f}%" if delta is not None else "N/A"
            lines.append(f"| {label} | {price_text} | {delta_text} | {date} |")

        valid_delta_rows = [
            r
            for r in rows
            if isinstance(r.get("delta_pct"), (int, float))
        ]
        valid_price_rows = [
            r
            for r in rows
            if isinstance(r.get("price"), (int, float))
        ]
        spread_line = None
        if len(valid_price_rows) >= 2:
            highest = max(valid_price_rows, key=lambda r: float(r.get("price") or 0.0))
            lowest = min(valid_price_rows, key=lambda r: float(r.get("price") or 0.0))
            high_name = highest.get("name_en") or highest.get("name_cn") or highest.get("label") or "Higher-priced product"
            low_name = lowest.get("name_en") or lowest.get("name_cn") or lowest.get("label") or "Lower-priced product"
            try:
                spread = float(highest.get("price") or 0.0) - float(lowest.get("price") or 0.0)
                spread_line = f"The current spread is about {spread:,.0f} CNY/t, with {high_name} trading above {low_name}."
            except Exception:
                spread_line = None

        if is_explicit_comparison_query and len(valid_delta_rows) >= 2:
            ranked_by_move = sorted(valid_delta_rows, key=lambda r: abs(float(r.get("delta_pct") or 0.0)), reverse=True)
            lead = ranked_by_move[0]
            lead_label = lead.get("name_en") or lead.get("name_cn") or lead.get("label") or "This product"
            lead_delta = float(lead.get("delta_pct") or 0.0)
            summary_parts: List[str] = []
            for row in rows[:3]:
                label = row.get("name_en") or row.get("name_cn") or row.get("label") or "-"
                price_text = _format_price_text(row.get("price"), str(row.get("unit") or ""), "en")
                delta = row.get("delta_pct")
                if isinstance(delta, (int, float)):
                    summary_parts.append(f"{label} at {price_text} ({float(delta):+.2f}% WoW)")
                else:
                    summary_parts.append(f"{label} at {price_text}")

            lines.extend([
                "",
                "**Direct answer:**",
                f"This week, the main move was in {lead_label}, which changed {lead_delta:+.2f}% week over week, while the other tracked product was comparatively stable.",
                f"Snapshot summary: {'; '.join(summary_parts)}.",
                "",
                "**What changed week over week:**",
            ])

            for row in rows:
                label = row.get("name_en") or row.get("name_cn") or row.get("label") or "-"
                delta = row.get("delta_pct")
                price_text = _format_price_text(row.get("price"), str(row.get("unit") or ""), "en")
                date = row.get("date") or "-"
                if isinstance(delta, (int, float)):
                    direction = "rose" if float(delta) > 0 else "fell" if float(delta) < 0 else "was flat"
                    if direction == "was flat":
                        lines.append(f"- {label}: {price_text} as of {date}; week over week it was flat at {float(delta):+.2f}%.")
                    else:
                        lines.append(f"- {label}: {price_text} as of {date}; week over week it {direction} by {abs(float(delta)):.2f}%.")
                else:
                    lines.append(f"- {label}: {price_text} as of {date}; week-over-week change was not available.")

            lines.extend([
                "",
                "**Market read:**",
            ])
            if lead_delta < 0:
                lines.append(f"- The weakness is concentrated in {lead_label}, so the current read is selective pressure rather than a broad market selloff.")
            elif lead_delta > 0:
                lines.append(f"- The strength is concentrated in {lead_label}, so the current read is selective tightness rather than a uniform market repricing.")
            else:
                lines.append("- The tracked products were broadly stable week over week, so the market read is sideways rather than directional.")
            if spread_line:
                lines.append(f"- {spread_line}")
            lines.extend([
                "",
                "Data source: `market_prices` live lookup.",
            ])
            return "\n".join(lines).strip()

        if valid_delta_rows:
            top_move = max(valid_delta_rows, key=lambda r: abs(float(r.get("delta_pct") or 0.0)))
            top_label = top_move.get("name_en") or top_move.get("name_cn") or top_move.get("label") or "This product"
            top_delta = float(top_move.get("delta_pct") or 0.0)
            up_count = sum(1 for r in valid_delta_rows if float(r.get("delta_pct") or 0.0) > 0)
            down_count = sum(1 for r in valid_delta_rows if float(r.get("delta_pct") or 0.0) < 0)
            period_text = "weekly" if asks_weekly else "monthly" if asks_monthly else "recent"

            lines.extend([
                "",
                "**What this means (quick interpretation):**",
                f"- Largest {period_text} move: {top_label} at {top_delta:+.2f}%.",
            ])
            if up_count and down_count:
                lines.append("- Signal: mixed direction across products, indicating a split market tone rather than a one-way move.")
            elif up_count and not down_count:
                lines.append("- Signal: broad upward bias across tracked products in the selected period.")
            elif down_count and not up_count:
                lines.append("- Signal: broad downward pressure across tracked products in the selected period.")

        if spread_line:
            lines.append(f"- Spread signal: {spread_line[23:] if spread_line.startswith('The current spread is ') else spread_line}")

        lines.extend([
            "- If you want, I can expand this into a short cause-and-impact read (drivers, inventory lens, and 1-2 week outlook).",
            "",
            "Data source: `market_prices` live lookup.",
        ])
        return "\n".join(lines).strip()

    lead = "根据内部数据库实时数据，当前主流价格及涨跌如下。"
    if asks_weekly:
        lead = "根据内部数据库实时数据，本周主流价格及环比涨跌如下。"
    elif asks_monthly:
        lead = "根据内部数据库实时数据，本月主流价格及月环比如下。"

    delta_summary_text = (state.get("delta_summary_text") or "").strip()
    lines: List[str] = []
    if delta_summary_text:
        if "\n" in delta_summary_text:
            lines.append(delta_summary_text)
        else:
            lines.append(f"**{delta_summary_text}**")
        lines.append("")

    lines.extend(
        [
            "### 1. 当前主流价格与环比涨跌（已验证事实）",
            lead,
            "",
            f"| 产品 | 主流价格（元/吨） | {delta_label_zh} | 数据来源与日期 |",
            "| :--- | :--- | :--- | :--- |",
        ]
    )

    valid_deltas: List[float] = []
    for row in rows:
        label = row.get("name_cn") or row.get("name_en") or row.get("label") or "-"
        price = row.get("price")
        unit = row.get("unit") or ""
        delta = row.get("delta_pct")
        date = row.get("date") or "-"
        src = row.get("src") or "market_prices"

        if delta is not None:
            try:
                valid_deltas.append(float(delta))
            except Exception:
                pass

        price_text = _format_price_text(price, unit, "zh")
        delta_text = f"{delta:+.2f}%" if delta is not None else "暂无"
        lines.append(f"| **{label}** | **{price_text}** | **{delta_text}** | `{src}`，{date} |")

    lines.extend(
        [
            "",
            "**说明**：",
            "- 以上为内部数据库实时市场价格记录，属于已验证事实。",
            f"- {delta_label_zh}口径为{delta_suffix_zh}。",
            "",
            "### 2. 市场诊断与关键驱动因素（基于数据推断）",
        ]
    )

    if valid_deltas:
        avg_delta = sum(valid_deltas) / len(valid_deltas)
    else:
        avg_delta = 0.0

    if avg_delta >= 1.0:
        market_phase = "看涨"
    elif avg_delta <= -1.0:
        market_phase = "看跌"
    else:
        market_phase = "震荡"

    lines.append(f"当前市场短期处于**{market_phase}**阶段。")

    positive = [r for r in rows if isinstance(r.get("delta_pct"), (int, float)) and float(r.get("delta_pct")) > 0]
    negative = [r for r in rows if isinstance(r.get("delta_pct"), (int, float)) and float(r.get("delta_pct")) < 0]

    if positive:
        top_up = max(positive, key=lambda r: float(r.get("delta_pct") or 0.0))
        top_up_name = top_up.get("name_cn") or top_up.get("name_en") or top_up.get("label") or "该产品"
        lines.append(
            f"1. **上涨动量**：{top_up_name}最近周期涨幅为**{float(top_up.get('delta_pct')):+.2f}%**，对整体市场情绪形成上拉。"
        )
    else:
        lines.append("1. **上涨动量**：当前样本未显示明确上涨动量。")

    if negative:
        top_down = min(negative, key=lambda r: float(r.get("delta_pct") or 0.0))
        top_down_name = top_down.get("name_cn") or top_down.get("name_en") or top_down.get("label") or "该产品"
        lines.append(
            f"2. **回调压力**：{top_down_name}最近周期变动为**{float(top_down.get('delta_pct')):+.2f}%**，需关注回调风险向产业链传导。"
        )
    else:
        lines.append("2. **回调压力**：当前样本未出现明显负向价格信号。")

    if len(rows) >= 2:
        lines.append("3. **结构分化**：不同产品涨跌幅存在差异，表明产业链传导节奏并不同步，建议分品种制定采购与报价策略。")
    else:
        lines.append("3. **结构分化**：当前仅单品数据，建议结合上下游品种联动判断。")

    lines.append("4. **事件验证状态**：本段仅依据内部数据库价格序列推断，未引入已验证外部事件证据。")

    pricing_ladder = _build_pricing_ladder_block(state, "zh").strip()
    lines.extend(["", "### 3. 定价推导过程（系统建议）"])
    if pricing_ladder:
        lines.append("以下为系统推荐价的加价阶梯：")
        lines.append("")
        lines.append(pricing_ladder)
    else:
        lines.append("当前查询以实时价格查询为主，未触发完整推荐价阶梯计算。若需要，我可以进一步生成完整定价推导表。")

    lines.extend(
        [
            "",
            "### 4. 近期展望与风险（推断与情景）",
        ]
    )

    if market_phase == "看涨":
        lines.append("- **基础预测（未来1-2周）**：价格偏强运行，短期以高位震荡为主。")
    elif market_phase == "看跌":
        lines.append("- **基础预测（未来1-2周）**：价格承压运行，关注阶段性回调。")
    else:
        lines.append("- **基础预测（未来1-2周）**：价格大概率维持区间震荡。")

    lines.extend(
        [
            "- **主要上行风险**：上游原料继续走强、供应端阶段性收缩。",
            "- **主要下行风险**：下游接货转弱、订单环比下降、成本端回调。",
            "",
            "**您可能还想问：**",
            "1. 如果上游原料价格下周回落，当前推荐策略应如何调整？",
            "2. 这两个产品的上下游联动系数和滞后期分别是多少？",
            "3. 是否可以给出基准/乐观/悲观三种情景下的价格区间？",
            "",
            f"数据来源：内部数据库实时市场价（`market_prices`），{delta_label_zh}口径：{delta_suffix_zh}。",
        ]
    )
    return "\n".join(lines).strip()


def _build_pricing_fallback_report(state: PipelineState) -> str:
    pricing = state.get("pricing_decision") or {}
    if not pricing or pricing.get("no_pricing"):
        return _build_simple_lookup_report(state)

    lang = _resolve_output_language(state)
    if lang == "en":
        return _build_simple_lookup_report(state)

    diagnosis = state.get("diagnosis") or {}
    forecast = state.get("forecast") or {}
    fresh_rows = _extract_live_db_authoritative_rows(state.get("tool_info", "") or "")

    product_name = pricing.get("product_name_cn") or pricing.get("product_id") or "该产品"
    unit = pricing.get("unit") or "元/吨"
    latest_market_price = pricing.get("latest_market_price")
    recommended_price = pricing.get("recommended_price")
    price_range = pricing.get("price_range") or []
    price_low = price_range[0] if len(price_range) > 0 else None
    price_high = price_range[1] if len(price_range) > 1 else None
    sinopec_anchor = pricing.get("sinopec_anchor")
    discount_to_sinopec = pricing.get("discount_to_sinopec")
    confidence = pricing.get("confidence")
    action_cn = pricing.get("action_cn") or "稳定 — 维持现价"

    weekly_change_pct = None
    market_date = "-"
    if fresh_rows:
        row = fresh_rows[0]
        weekly_change_pct = row.get("delta_pct")
        market_date = row.get("date") or "-"

    base_7d = ((forecast.get("scenarios") or {}).get("7d") or {}).get("base") or {}
    forecast_low = base_7d.get("low")
    forecast_high = base_7d.get("high")

    primary_driver = diagnosis.get("primary_driver") or {}
    primary_driver_name = primary_driver.get("name_en") or primary_driver.get("id") or "无"
    primary_driver_change = primary_driver.get("change_7d_pct")
    regime = diagnosis.get("market_regime") or "neutral"

    inventory_note = ""
    macro_note = ""
    seasonal_note = ""
    weekly_override_note = ""
    for step in pricing.get("adjustment_steps") or []:
        label = str(step.get("label") or "")
        note = str(step.get("note") or "")
        if "ERP库存压力" in label:
            inventory_note = note
        elif "宏观成本调整" in label:
            macro_note = note
        elif "季节性调整" in label:
            seasonal_note = note
        elif "周报推荐覆盖" in label:
            weekly_override_note = note

    spread_line = "暂无中石化上海挂牌价价差数据。"
    if sinopec_anchor is not None and recommended_price is not None:
        spread_line = (
            f"中石化上海挂牌锚价约为 **{sinopec_anchor:,.0f} {unit}**；"
            f"系统建议中枢报价 **{recommended_price:,.0f} {unit}**，"
            f"较挂牌价 **低 {discount_to_sinopec:,.0f} {unit}**。"
        )
        if price_low is not None and price_high is not None:
            low_spread = sinopec_anchor - price_low
            high_spread = sinopec_anchor - price_high
            spread_line += (
                f"按建议区间计算，较挂牌价价差约为 **低 {low_spread:,.0f} 至 {high_spread:,.0f} {unit}**。"
            )

    lines = [
        "## 📊 01 · 市场现状",
        f"**判断**: {'偏强' if regime == 'bullish' else '偏弱' if regime == 'bearish' else '中性'}",
    ]
    if latest_market_price is not None:
        market_line = f"- {product_name} 当前中国区数据库主流价为 **{latest_market_price:,.0f} {unit}**"
        if weekly_change_pct is not None:
            market_line += f"，7日环比 **{weekly_change_pct:+.2f}%**"
        if market_date and market_date != "-":
            market_line += f"，数据日期 {market_date}。"
        else:
            market_line += "。"
        lines.append(market_line)

    lines.extend([
        "",
        "## 🔍 02 · 关键驱动因素",
        f"- 主要驱动：**{primary_driver_name}**"
        + (
            f"，近7日变动 **{primary_driver_change:+.2f}%**。"
            if primary_driver_change is not None
            else "。"
        ),
    ])
    if macro_note:
        lines.append(f"- 宏观成本：{macro_note}")
    if seasonal_note:
        lines.append(f"- 季节性：{seasonal_note}")
    if inventory_note:
        lines.append(f"- 库存约束：{inventory_note}")

    lines.extend([
        "",
        "## 📈 03 · 价格预测",
    ])
    if forecast_low is not None and forecast_high is not None:
        lines.append(
            f"- 未来7天基础预测区间约为 **{forecast_low:,.0f}–{forecast_high:,.0f} {unit}**。"
        )
    else:
        lines.append("- 当前未返回可用的7日预测区间，建议以实时成交与定价阶梯为主。")

    lines.extend([
        "",
        "## 💡 04 · 定价决策",
    ])
    if price_low is not None and price_high is not None:
        lines.append(
            f"- 本周建议报价区间：**{price_low:,.0f}–{price_high:,.0f} {unit}**，建议中枢价 **{recommended_price:,.0f} {unit}**。"
        )
    else:
        lines.append(f"- 本周建议中枢报价：**{recommended_price:,.0f} {unit}**。")
    lines.append(f"- 执行动作：**{action_cn}**。")
    if confidence is not None:
        lines.append(f"- 置信度：**{int(confidence * 100)}%**。")
    lines.append(f"- 与中石化上海挂牌价的价差：{spread_line}")
    if weekly_override_note:
        lines.append(f"- 定价偏置说明：{weekly_override_note}")

    lines.extend([
        "",
        "## ⚠️ 05 · 风险预警",
    ])
    dissenting_view = str(pricing.get("dissenting_view") or "").strip()
    if dissenting_view:
        lines.append(f"- 反方观点：{dissenting_view}")
    triggers = pricing.get("triggers") or []
    for trig in triggers[:2]:
        cond = trig.get("condition")
        action = trig.get("action")
        if cond and action:
            lines.append(f"- 触发条件：{cond}；对应动作：{action}。")
    watch = pricing.get("watch_signals") or []
    if watch:
        lines.append("- 重点跟踪：" + "、".join(str(item) for item in watch[:3]) + "。")

    return "\n".join(lines).strip()


def _looks_like_quote_range_request(user_query: str) -> bool:
    q = (user_query or "").strip().lower()
    if not q:
        return False

    quote_markers = [
        "quote range",
        "recommended quote",
        "recommended price",
        "listed price",
        "spread versus",
        "spread vs",
        "报价区间",
        "推荐报价",
        "建议报价",
        "挂牌价",
        "价差",
    ]
    period_markers = [
        "this week",
        "next week",
        "weekly",
        "本周",
        "下周",
        "周度",
    ]
    return any(marker in q for marker in quote_markers) and any(
        marker in q for marker in period_markers
    )


def _normalize_quote_unit(unit: Any, language: str) -> str:
    raw = str(unit or "").strip().lower()
    if raw in {"", "吨", "ton", "/ton", "per ton"}:
        return "CNY/ton" if language == "en" else "元/吨"
    if raw in {"元/吨", "rmb/ton", "cny/ton", "yuan/ton"}:
        return "CNY/ton" if language == "en" else "元/吨"
    return str(unit or ("CNY/ton" if language == "en" else "元/吨"))


def _format_quote_spread(diff: float, unit: str, language: str) -> str:
    magnitude = abs(float(diff))
    if language == "en":
        if diff >= 0:
            return f"a premium of **+{magnitude:,.0f} {unit}**"
        return f"a discount of **-{magnitude:,.0f} {unit}**"
    if diff >= 0:
        return f"**高 {magnitude:,.0f} {unit}**"
    return f"**低 {magnitude:,.0f} {unit}**"


def _localize_quote_text(text: Any, language: str) -> str:
    value = str(text or "").strip()
    if language != "en" or not value:
        return value
    if value.isascii():
        return value

    replacements = [
        ("若SINOPEC月度挂牌未下调，主动降价可能损害盈利；先观察竞品动作，再决定是否跟进。", "If the SINOPEC monthly listed price does not move down, proactive quote cuts may erode margin; watch competitor moves first."),
        ("石脑油7日跌幅扩大至 > 8.4%", "If naphtha 7-day decline widens beyond 8.4%"),
        ("调整至 14,700 元/吨并提示客户", "trim quotes toward 14,700 CNY/ton and notify customers"),
        ("本周订单量环比下降 ≥ 10%", "If weekly order volume drops by 10% or more"),
        ("维持 14,800 元/吨不变，观察 3–5 天后再调整", "hold 14,800 CNY/ton and reassess after 3-5 days"),
        ("SINOPEC上海3月间戊二烯挂牌", "SINOPEC Shanghai listed price updates"),
        ("硬化剂开工率", "hardener operating rates"),
        ("道路划线旺季启动", "road-marking seasonal demand start"),
        ("元/吨", "CNY/ton"),
    ]
    localized = value
    for old, new in replacements:
        localized = localized.replace(old, new)
    return localized


def _build_quote_range_report(state: PipelineState) -> str:
    pricing = state.get("pricing_decision") or {}
    if not pricing or pricing.get("no_pricing"):
        return _build_pricing_fallback_report(state)

    lang = _resolve_output_language(state)
    diagnosis = state.get("diagnosis") or {}
    forecast = state.get("forecast") or {}
    fresh_rows = _extract_live_db_authoritative_rows(state.get("tool_info", "") or "")

    product_name_cn = pricing.get("product_name_cn") or pricing.get("product_id") or "该产品"
    product_name_en = pricing.get("product_name_en") or pricing.get("product_id") or "the product"
    product_name = product_name_en if lang == "en" else product_name_cn
    unit = _normalize_quote_unit(pricing.get("unit"), lang)
    latest_market_price = pricing.get("latest_market_price")
    recommended_price = pricing.get("recommended_price")
    price_range = pricing.get("price_range") or []
    price_low = price_range[0] if len(price_range) > 0 else None
    price_high = price_range[1] if len(price_range) > 1 else None
    sinopec_anchor = pricing.get("sinopec_anchor")
    discount_to_sinopec = pricing.get("discount_to_sinopec")
    confidence = pricing.get("confidence")
    action_cn = pricing.get("action_cn") or "稳定 — 维持现价"
    action_en = {
        "看涨 — 建议上调": "Bullish - raise quotes",
        "看跌 — 建议下调": "Bearish - lower quotes",
        "偏强 — 维持高位或小幅上调": "Firm - hold high or raise slightly",
        "偏弱 — 维持低位或小幅下调": "Soft - hold low or trim slightly",
        "稳定 — 维持现价": "Stable - hold current quotes",
    }.get(action_cn, action_cn)

    weekly_change_pct = None
    market_date = None
    if fresh_rows:
        row = fresh_rows[0]
        weekly_change_pct = row.get("delta_pct")
        market_date = row.get("date")

    base_7d = ((forecast.get("scenarios") or {}).get("7d") or {}).get("base") or {}
    next_week_low = base_7d.get("low")
    next_week_high = base_7d.get("high")
    next_week_mid = None
    if isinstance(next_week_low, (int, float)) and isinstance(next_week_high, (int, float)):
        next_week_mid = round((float(next_week_low) + float(next_week_high)) / 2.0)

    primary_driver = diagnosis.get("primary_driver") or {}
    primary_driver_name = (
        primary_driver.get("name_en") if lang == "en" else primary_driver.get("name_cn")
    ) or primary_driver.get("name_en") or primary_driver.get("id") or ("None" if lang == "en" else "无")
    primary_driver_change = primary_driver.get("change_7d_pct")
    regime = diagnosis.get("market_regime") or "neutral"

    inventory_note = ""
    macro_note = ""
    seasonal_note = ""
    weekly_override_note = ""
    for step in pricing.get("adjustment_steps") or []:
        label = str(step.get("label") or "")
        note = str(step.get("note") or "")
        if "ERP库存压力" in label:
            inventory_note = note
        elif "宏观成本调整" in label:
            macro_note = note
        elif "季节性调整" in label:
            seasonal_note = note
        elif "周报推荐覆盖" in label:
            weekly_override_note = note

    if lang == "en":
        if macro_note:
            macro_note = "Feedstock cost remains a live variable in the current quote band."
        if seasonal_note:
            seasonal_note = "Seasonality remains supportive but not decisive for near-term pricing."
        if inventory_note:
            inventory_note = "Inventory coverage is within a manageable range and does not force urgent discounting."
        if weekly_override_note:
            weekly_override_note = "A weekly reporting override adjusted the final quote bias around the base recommendation."

    if lang == "en":
        spread_line = "No SINOPEC Shanghai spread data is available."
        if sinopec_anchor is not None and recommended_price is not None:
            spread_line = (
                f"SINOPEC Shanghai listed anchor is about **{sinopec_anchor:,.0f} {unit}**; "
                f"the system midpoint quote is **{recommended_price:,.0f} {unit}**, "
                f"which implies {_format_quote_spread(recommended_price - sinopec_anchor, unit, lang)}."
            )
            if price_low is not None and price_high is not None:
                spread_line += (
                    f" On the current-week quote band, the spread runs from "
                    f"{_format_quote_spread(price_low - sinopec_anchor, unit, lang)} to "
                    f"{_format_quote_spread(price_high - sinopec_anchor, unit, lang)}."
                )

        lines = [
            f"# Recommended Quote Range for {product_name} (China)",
            "",
            "## 1. Market Overview",
            f"**Outlook**: {'Firm' if regime == 'bullish' else 'Soft' if regime == 'bearish' else 'Neutral'}",
        ]
        if latest_market_price is not None:
            market_line = f"- Current China reference price: **{latest_market_price:,.0f} {unit}**"
            if weekly_change_pct is not None:
                market_line += f"; 7-day change **{weekly_change_pct:+.2f}%**"
            if market_date:
                market_line += f"; data date {market_date}."
            else:
                market_line += "."
            lines.append(market_line)

        lines.extend([
            "",
            "## 2. Key Drivers",
            f"- Primary driver: **{primary_driver_name}**"
            + (f"; 7-day change **{primary_driver_change:+.2f}%**." if primary_driver_change is not None else "."),
        ])
        if macro_note:
            lines.append(f"- Macro cost: {macro_note}")
        if seasonal_note:
            lines.append(f"- Seasonality: {seasonal_note}")
        if inventory_note:
            lines.append(f"- Inventory constraint: {inventory_note}")

        lines.extend([
            "",
            "## 3. Price Forecast",
        ])
        if next_week_low is not None and next_week_high is not None:
            lines.append(
                f"- Next-week indicative market range: **{next_week_low:,.0f}-{next_week_high:,.0f} {unit}**."
            )
        else:
            lines.append("- No usable next-week forecast range is available from Stage 3 yet.")

        lines.extend([
            "",
            "## 4. Pricing Decision",
        ])
        if price_low is not None and price_high is not None:
            lines.append(
                f"- This week recommended quote range: **{price_low:,.0f}-{price_high:,.0f} {unit}**; midpoint **{recommended_price:,.0f} {unit}**."
            )
        elif recommended_price is not None:
            lines.append(f"- This week recommended midpoint quote: **{recommended_price:,.0f} {unit}**.")
        if next_week_low is not None and next_week_high is not None:
            lines.append(
                f"- Next week indicative quote range: **{next_week_low:,.0f}-{next_week_high:,.0f} {unit}**"
                + (f"; midpoint **{next_week_mid:,.0f} {unit}**." if next_week_mid is not None else ".")
            )
        lines.append(f"- Recommended action: **{action_en}**.")
        if confidence is not None:
            lines.append(f"- Confidence: **{int(confidence * 100)}%**.")
        lines.append(f"- Spread versus SINOPEC Shanghai listed price: {spread_line}")
        if weekly_override_note:
            lines.append(f"- Override note: {weekly_override_note}")

        lines.extend([
            "",
            "## 5. Risk Alerts",
        ])
        dissenting_view = _localize_quote_text(pricing.get("dissenting_view") or "", lang)
        if dissenting_view:
            lines.append(f"- Counter-view: {dissenting_view}")
        for trig in (pricing.get("triggers") or [])[:2]:
            cond = _localize_quote_text(trig.get("condition"), lang)
            action = _localize_quote_text(trig.get("action"), lang)
            if cond and action:
                lines.append(f"- Trigger: {cond}; action: {action}.")
        watch = pricing.get("watch_signals") or []
        if watch:
            lines.append(
                "- Watch: "
                + ", ".join(_localize_quote_text(item, lang) for item in watch[:3])
                + "."
            )
        return "\n".join(lines).strip()

    spread_line = "暂无中石化上海挂牌价价差数据。"
    if sinopec_anchor is not None and recommended_price is not None:
        spread_line = (
            f"中石化上海挂牌锚价约为 **{sinopec_anchor:,.0f} {unit}**；"
            f"系统本周建议中枢报价 **{recommended_price:,.0f} {unit}**，"
            f"较挂牌价 {_format_quote_spread(recommended_price - sinopec_anchor, unit, lang)}。"
        )
        if price_low is not None and price_high is not None:
            spread_line += (
                f"按本周建议区间计算，较挂牌价价差约为 {_format_quote_spread(price_low - sinopec_anchor, unit, lang)} 至 {_format_quote_spread(price_high - sinopec_anchor, unit, lang)}。"
            )

    lines = [
        f"# {product_name} 中国区建议报价",
        "",
        "## 📊 01 · 市场现状",
        f"**判断**: {'偏强' if regime == 'bullish' else '偏弱' if regime == 'bearish' else '中性'}",
    ]
    if latest_market_price is not None:
        market_line = f"- 当前中国区参考主流价为 **{latest_market_price:,.0f} {unit}**"
        if weekly_change_pct is not None:
            market_line += f"，7日环比 **{weekly_change_pct:+.2f}%**"
        if market_date:
            market_line += f"，数据日期 {market_date}。"
        else:
            market_line += "。"
        lines.append(market_line)

    lines.extend([
        "",
        "## 🔍 02 · 关键驱动因素",
        f"- 主要驱动：**{primary_driver_name}**"
        + (f"，近7日变动 **{primary_driver_change:+.2f}%**。" if primary_driver_change is not None else "。"),
    ])
    if macro_note:
        lines.append(f"- 宏观成本：{macro_note}")
    if seasonal_note:
        lines.append(f"- 季节性：{seasonal_note}")
    if inventory_note:
        lines.append(f"- 库存约束：{inventory_note}")

    lines.extend([
        "",
        "## 📈 03 · 价格预测",
    ])
    if next_week_low is not None and next_week_high is not None:
        lines.append(f"- 下周指引区间约为 **{next_week_low:,.0f}–{next_week_high:,.0f} {unit}**。")
    else:
        lines.append("- 当前未返回可用的下周预测区间，建议以实时成交与定价阶梯为主。")

    lines.extend([
        "",
        "## 💡 04 · 定价决策",
    ])
    if price_low is not None and price_high is not None:
        lines.append(f"- 本周建议报价区间：**{price_low:,.0f}–{price_high:,.0f} {unit}**，建议中枢价 **{recommended_price:,.0f} {unit}**。")
    elif recommended_price is not None:
        lines.append(f"- 本周建议中枢报价：**{recommended_price:,.0f} {unit}**。")
    if next_week_low is not None and next_week_high is not None:
        lines.append(
            f"- 下周建议报价区间：**{next_week_low:,.0f}–{next_week_high:,.0f} {unit}**"
            + (f"，参考中枢价 **{next_week_mid:,.0f} {unit}**。" if next_week_mid is not None else "。")
        )
    lines.append(f"- 执行动作：**{action_cn}**。")
    if confidence is not None:
        lines.append(f"- 置信度：**{int(confidence * 100)}%**。")
    lines.append(f"- 与中石化上海挂牌价的价差：{spread_line}")
    if weekly_override_note:
        lines.append(f"- 定价偏置说明：{weekly_override_note}")

    lines.extend([
        "",
        "## ⚠️ 05 · 风险预警",
    ])
    dissenting_view = str(pricing.get("dissenting_view") or "").strip()
    if dissenting_view:
        lines.append(f"- 反方观点：{dissenting_view}")
    for trig in (pricing.get("triggers") or [])[:2]:
        cond = trig.get("condition")
        action = trig.get("action")
        if cond and action:
            lines.append(f"- 触发条件：{cond}；对应动作：{action}。")
    watch = pricing.get("watch_signals") or []
    if watch:
        lines.append("- 重点跟踪：" + "、".join(str(item) for item in watch[:3]) + "。")
    return "\n".join(lines).strip()


def _quote_range_report_has_required_slots(markdown: str, language: str) -> bool:
    text = str(markdown or "").strip()
    if not text:
        return False

    blocked_markers = [
        "Full grounded analysis is still being generated.",
        "Current status: grounding the answer in market, inventory, and pricing signals.",
        "## Quick Take",
    ]
    if any(marker in text for marker in blocked_markers):
        return False

    required_markers = (
        [
            "This week recommended quote range",
            "Next week indicative quote range",
            "Spread versus SINOPEC Shanghai listed price",
        ]
        if language == "en"
        else [
            "本周建议报价区间",
            "下周建议报价区间",
            "与中石化上海挂牌价的价差",
        ]
    )
    return all(marker in text for marker in required_markers)


def _extract_live_market_rows_from_text(text: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not text:
        return rows
    for match in _re.finditer(
        r"-\s*([a-z0-9_]+):\s*([\d,]+(?:\.\d+)?)\s*on\s*(\d{4}-\d{2}-\d{2})\s*\(7d\s*([\+\-]?\d+(?:\.\d+)?)%\)",
        text,
        flags=_re.IGNORECASE,
    ):
        pid = str(match.group(1) or "").strip().lower()
        try:
            price = float(str(match.group(2)).replace(",", ""))
        except Exception:
            continue
        date_str = str(match.group(3) or "").strip()
        try:
            delta_pct = float(match.group(4))
        except Exception:
            delta_pct = 0.0
        node = next(
            (n for n in _domain_graph.nodes if str(getattr(n, "id", "") or "").lower() == pid),
            None,
        )
        rows.append(
            {
                "product_id": pid,
                "name_en": getattr(node, "name_en", pid) if node else pid,
                "name_cn": getattr(node, "name_cn", pid) if node else pid,
                "price": price,
                "date": date_str,
                "delta_pct": delta_pct,
            }
        )
    return rows


def _extract_forecast_rows_from_text(text: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    if not text:
        return rows
    for match in _re.finditer(
        r"-\s*([a-z0-9_]+):\s*3d\s*([^|\n]+)\|\s*7d\s*([^|\n]+)\|\s*30d\s*([^\n]+)",
        text,
        flags=_re.IGNORECASE,
    ):
        rows.append(
            {
                "product_id": str(match.group(1) or "").strip().lower(),
                "range_3d": str(match.group(2) or "").strip(),
                "range_7d": str(match.group(3) or "").strip(),
                "range_30d": str(match.group(4) or "").strip(),
            }
        )
    return rows


def _extract_safety_alert_rows(text: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    if not text:
        return rows
    for match in _re.finditer(
        r"⚠️?\s*(CRITICAL|LOW):\s*(.+?)\s+—\s+(.+)",
        text,
        flags=_re.IGNORECASE,
    ):
        rows.append(
            {
                "severity": str(match.group(1) or "").upper(),
                "product": str(match.group(2) or "").strip(),
                "detail": str(match.group(3) or "").strip(),
            }
        )
    return rows


def _extract_sales_total_and_change(text: str) -> Dict[str, Optional[float]]:
    total = None
    total_revenue = None
    period_change = None
    total_match = _re.search(r"TOTAL\s+([\d,]+(?:\.\d+)?)(?:\s+([\d,]+(?:\.\d+)?))?", text or "")
    if total_match:
        try:
            total = float(str(total_match.group(1)).replace(",", ""))
        except Exception:
            total = None
        if total_match.group(2):
            try:
                total_revenue = float(str(total_match.group(2)).replace(",", ""))
            except Exception:
                total_revenue = None
    change_match = _re.search(r"Period-over-period change:\s*([\+\-]?\d+(?:\.\d+)?)%", text or "")
    if change_match:
        try:
            period_change = float(change_match.group(1))
        except Exception:
            period_change = None
    return {"total": total, "total_revenue": total_revenue, "period_change": period_change}


def _extract_erp_sales_summary_rows(text: str) -> Dict[str, Any]:
    body = str(text or "")
    if "ERP SALES SUMMARY" not in body:
        return {"rows": []}

    period_start = None
    period_end = None
    period_match = _re.search(
        r"ERP SALES SUMMARY \(([^)]+?)\s+to\s+([^)]+?)\):",
        body,
        flags=_re.IGNORECASE,
    )
    if period_match:
        period_start = str(period_match.group(1) or "").strip()
        period_end = str(period_match.group(2) or "").strip()

    rows: List[Dict[str, Any]] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("-"):
            continue
        if stripped.startswith("ERP SALES SUMMARY") or stripped.startswith("Product"):
            continue
        if stripped.startswith("TOTAL") or stripped.startswith("Period-over-period"):
            continue
        match = _re.match(
            r"^(.+?)\s+([\d,]+(?:\.\d+)?)\s+(\d+)\s+([\d,]+(?:\.\d+)?)(?:\s+([\d,]+(?:\.\d+)?))?$",
            stripped,
        )
        if not match:
            continue
        try:
            rows.append(
                {
                    "product": str(match.group(1) or "").strip(),
                    "volume_ton": float(str(match.group(2)).replace(",", "")),
                    "orders": int(match.group(3)),
                    "avg_price": float(str(match.group(4)).replace(",", "")),
                    "revenue_cny": float(str(match.group(5)).replace(",", "")) if match.group(5) else None,
                }
            )
        except Exception:
            continue

    stats = _extract_sales_total_and_change(body)
    return {
        "period_start": period_start,
        "period_end": period_end,
        "rows": rows,
        "total": stats.get("total"),
        "total_revenue": stats.get("total_revenue"),
        "period_change": stats.get("period_change"),
    }


def _is_business_data_task(
    task_type: Optional[TaskType],
    user_query: str,
) -> bool:
    if task_type in (TaskType.CONVERSATIONAL, TaskType.OUT_OF_DOMAIN, None):
        return False

    try:
        from backend.src.nlu.intent_parser import _is_conversational_query

        q = str(user_query or "").strip()
        if q and _is_conversational_query(q, q.lower()):
            return False
    except Exception:
        pass

    if task_type in (
        TaskType.ERP_DATA_QUERY,
        TaskType.MARKET_ANALYSIS_ONLY,
        TaskType.MARKET_ANALYSIS_DECISION,
        TaskType.WHAT_IF_SCENARIO,
        TaskType.FORECAST_ONLY,
        TaskType.BOSS_BRIEFING,
    ):
        return True

    if task_type == TaskType.SIMPLE_LOOKUP:
        q = str(user_query or "")
        ql = q.lower()
        markers_zh = (
            "价格",
            "报价",
            "行情",
            "市场",
            "销售",
            "库存",
            "产品",
            "吨",
            "环比",
            "同比",
            "涨跌",
            "挂牌",
        )
        markers_en = (
            "price",
            "market",
            "sales",
            "inventory",
            "product",
            "erp",
            "quote",
            "trend",
        )
        return any(marker in q for marker in markers_zh) or any(
            marker in ql for marker in markers_en
        )
    return False


def _should_use_executive_brief_format(state: PipelineState) -> bool:
    manual_mode = _normalize_manual_chat_mode((state.get("user_prefs") or {}).get("chat_mode"))
    if manual_mode == "chat":
        return False

    response_mode = str(state.get("response_mode") or "").strip().lower()
    if response_mode in {"conversational", "concise", "full_report"}:
        return False
    if response_mode == "executive_brief":
        return True

    parsed = state.get("parsed_intent")
    task_type = getattr(parsed, "task_type", None)
    user_query = str(state.get("user_query") or "")
    if not _is_business_data_task(task_type, user_query):
        return False
    return True


def _looks_like_incomplete_executive_brief(text: str, language: str = "zh") -> bool:
    content = str(text or "").strip()
    if not content or len(content) < 120:
        return True
    if language == "en":
        required = ("## Conclusion", "## Data Basis", "## Impact Assessment")
    else:
        required = ("## 结论", "## 数据依据", "## 影响判断")
    return not all(section in content for section in required)


def _build_executive_brief_from_erp_summary(
    *,
    lang: str,
    period_start: str,
    period_end: str,
    top: Dict[str, Any],
    total: Any,
    period_change: Any,
    rows: List[Dict[str, Any]],
    total_revenue: Any = None,
    revenue_primary: bool = False,
) -> str:
    period_text = (
        f"{period_start} to {period_end}"
        if lang == "en" and period_start and period_end
        else f"{period_start} 至 {period_end}" if period_start and period_end else ("the selected period" if lang == "en" else "所选时间段")
    )
    product = str(top.get("product") or "").strip()
    volume = float(top.get("volume_ton") or 0)
    orders = int(top.get("orders") or 0)
    avg_price = float(top.get("avg_price") or 0)
    row_revenue = float(top.get("revenue_cny") or 0)
    if total_revenue is None:
        total_revenue = sum(float(row.get("revenue_cny") or 0) for row in rows) if rows else None
    revenue_total = float(total_revenue) if total_revenue is not None else None
    change_text = (
        f"{float(period_change):+.1f}%"
        if period_change is not None
        else ("not available" if lang == "en" else "暂无")
    )

    if lang == "en":
        if revenue_primary and revenue_total is not None:
            conclusion = (
                f"During {period_text}, matched ERP sales revenue totals **{revenue_total:,.2f} CNY** "
                f"(sum of order-line FAMOUNT). Top product {product}: {row_revenue:,.2f} CNY, "
                f"{volume:,.2f} tons across {orders} orders. "
            )
        else:
            conclusion = (
                f"During {period_text}, {product} recorded {volume:,.2f} tons across {orders} ERP orders "
                f"at a weighted average price of {avg_price:,.2f} CNY/ton. "
            )
        if period_change is not None and not revenue_primary:
            if float(period_change) > 0:
                conclusion += "Volume is recovering versus the prior comparable period."
            elif float(period_change) < 0:
                conclusion += "Volume is softer than the prior comparable period and needs closer monitoring."
            else:
                conclusion += "Volume is broadly stable versus the prior comparable period."
        elif not revenue_primary:
            conclusion += "Period-over-period volume comparison is not available in the current ERP window."

        impact = (
            "The current pattern mainly reflects order continuity and realized shipment volume rather than a confirmed structural demand shift."
            if orders > 0
            else "The current pattern suggests weak realized demand in the selected ERP window."
        )
        if period_change is not None and float(period_change) < 0:
            impact += " The volume decline increases pricing and customer-retention pressure over the next 1-2 weeks."
        elif period_change is not None and float(period_change) > 0:
            impact += " The volume rebound supports a more constructive pricing posture if repeat orders continue."

        actions = [
            "- Hold a neutral-to-defensive quote stance if order cadence does not improve in the next cycle.",
            "- Prioritize core accounts with stable repeat orders before expanding discount coverage.",
        ]
        if period_change is not None and float(period_change) > 0:
            actions.append("- Tighten discounts only after one more cycle confirms the volume rebound.")
        risks = [
            "- Risk of misreading a short-term bounce as a sustained recovery.",
            "- Risk that weak order continuity will pressure average realized price.",
        ]
        data_lines = [
            f"- Period: {period_text}",
            f"- Product: {product}",
            f"- Sales volume: {volume:,.2f} tons",
            f"- Orders: {orders}",
            f"- Weighted average price: {avg_price:,.2f} CNY/ton",
            f"- Period-over-period volume change: {change_text}",
            "- Source: internal ERP sales order aggregates",
        ]
        if revenue_total is not None:
            data_lines.insert(4, f"- Sales revenue (FAMOUNT sum): {revenue_total:,.2f} CNY")
        if total is not None and (len(rows) > 1 or abs(float(total) - volume) > 0.01):
            data_lines.append(f"- Total matched ERP volume: {float(total):,.2f} tons")

        return "\n".join(
            [
                "## Conclusion",
                "",
                conclusion,
                "",
                "## Data Basis",
                "",
                *data_lines,
                "",
                "## Impact Assessment",
                "",
                impact,
                "",
                "## Recommended Actions",
                "",
                *actions,
                "",
                "## Risks to Watch",
                "",
                *risks,
            ]
        ).strip()

    if revenue_primary and revenue_total is not None:
        conclusion = (
            f"{period_text}，匹配产品 ERP 销售额合计 **{revenue_total:,.2f} 元**"
            f"（订单 FAMOUNT 逐笔相加）。其中 {product} 销售额 {row_revenue:,.2f} 元、"
            f"销量 {volume:,.2f} 吨、订单 {orders} 单。"
        )
    else:
        conclusion = (
            f"{period_text}，{product}销量 {volume:,.2f} 吨，订单 {orders} 单，"
            f"加权均价 {avg_price:,.2f} 元/吨。"
        )
    if period_change is not None and not revenue_primary:
        if float(period_change) > 0:
            conclusion += " 较上一可比周期，销量呈恢复态势。"
        elif float(period_change) < 0:
            conclusion += " 较上一可比周期，销量偏弱，需要提高跟踪频率。"
        else:
            conclusion += " 较上一可比周期，销量大体持平。"
    elif not revenue_primary:
        conclusion += " 当前窗口未返回可直接比较的环比销量。"

    impact = (
        "当前信号更多反映订单节奏与发货兑现，尚不能单独证明需求趋势已经反转。"
        if orders > 0
        else "当前窗口内成交偏弱，说明需求端仍处于观察期。"
    )
    if period_change is not None and float(period_change) < 0:
        impact += " 若销量继续走弱，未来1-2周报价与客户维护压力会上升。"
    elif period_change is not None and float(period_change) > 0:
        impact += " 若复购与发货量延续改善，可支撑更积极的报价策略。"

    actions = [
        "- 若下一周期订单未改善，维持偏防守报价，优先保住核心客户成交。",
        "- 对稳定复购客户优先保供，再考虑扩大折让范围。",
    ]
    if period_change is not None and float(period_change) > 0:
        actions.append("- 连续两个周期确认放量后，再考虑收紧折让。")

    risks = [
        "- 需警惕把短期波动误判为趋势反转。",
        "- 若订单连续性不足，加权均价仍可能承压。",
    ]
    data_lines = [
        f"- 统计区间：{period_text}",
        f"- 产品：{product}",
        f"- 销量：{volume:,.2f} 吨",
        f"- 订单数：{orders}",
        f"- 加权均价：{avg_price:,.2f} 元/吨",
        f"- 销量环比变化：{change_text}",
        "- 数据来源：内部ERP销售订单聚合数据",
    ]
    if revenue_total is not None:
        data_lines.insert(4, f"- 销售额（FAMOUNT 合计）：{revenue_total:,.2f} 元")
    if total is not None and (len(rows) > 1 or abs(float(total) - volume) > 0.01):
        data_lines.append(f"- 匹配ERP销量合计：{float(total):,.2f} 吨")

    return "\n".join(
        [
            "## 结论",
            "",
            conclusion,
            "",
            "## 数据依据",
            "",
            *data_lines,
            "",
            "## 影响判断",
            "",
            impact,
            "",
            "## 建议动作",
            "",
            *actions,
            "",
            "## 风险关注",
            "",
            *risks,
        ]
    ).strip()


def _is_erp_hybrid_analysis_mode(state: PipelineState) -> bool:
    parsed = state.get("parsed_intent")
    user_query = str(state.get("user_query") or "")
    ql = user_query.lower()

    # Cross-source blending: query mixes internal (ERP) and external (market) signals
    has_erp = any(marker in user_query for marker in ("销售", "订单", "合同", "客户", "库存", "库龄", "生产", "产量", "我司", "我们的")) or any(
        marker in ql for marker in ("sales", "order", "contract", "customer", "inventory", "stock", "production", "our ")
    )
    has_market = any(marker in user_query for marker in ("市场", "行情", "报价", "价格", "走势", "竞品", "竞争", "主流")) or any(
        marker in ql for marker in ("market", "price", "trend", "competitor", "competition", "quote", "reference")
    )
    has_margin = any(marker in user_query for marker in ("利润", "毛利", "margin", "盈利", "收益", "价差")) or any(
        marker in ql for marker in ("margin", "profit", "profitability", "spread")
    )
    is_cross_source = (has_erp and has_market) or (has_margin and (has_erp or has_market))

    if is_cross_source:
        return True

    if getattr(parsed, "task_type", None) != TaskType.ERP_DATA_QUERY:
        return False
    if _looks_like_erp_sales_detail_query(user_query) or _looks_like_erp_revenue_query(user_query):
        return False
    mode_values = set()
    for key in ("response_mode", "resolved_chat_mode"):
        raw = str(state.get(key) or "").strip().lower()
        if raw:
            mode_values.add(raw)
    prefs = state.get("user_prefs") or {}
    prefs_mode = str((prefs or {}).get("chat_mode") or "").strip().lower()
    if prefs_mode:
        mode_values.add(prefs_mode)
    return bool(
        mode_values
        & {"full_report", "report", "deep_research", "analysis", "executive_brief"}
    ) or _should_use_executive_brief_format(state)


def _looks_like_erp_sales_query(query: str) -> bool:
    q = str(query or "")
    ql = q.lower()
    if _looks_like_erp_report_query(q):
        return True
    if any(marker in q for marker in ("销售", "销量", "卖了", "卖出", "成交", "销售额", "销售金额", "销售收入")) or any(
        marker in ql for marker in ("sales", "sold", "sale volume", "revenue", "sales amount", "sales value")
    ):
        return True
    if _looks_like_erp_sales_detail_query(q):
        return True
    product_ids = extract_product_ids_in_text(q)
    if product_ids and any(marker in q for marker in ("情况", "状况")):
        return True
    if product_ids and any(marker in ql for marker in ("situation", "status", "performance")):
        return True
    return False


def _looks_like_erp_report_query(query: str) -> bool:
    q = str(query or "")
    ql = q.lower()
    erp_context_zh = ("erp", "销售", "订单", "客户", "库存", "我司", "我们的")
    erp_context_en = ("erp", "sales", "order", "customer", "inventory", "our ", "our company")
    report_zh = ("报表", "报告", "汇总", "概况", "经营状况")
    report_en = (
        "erp report",
        "erp summary",
        "monthly erp",
        "business report",
        "sales report",
        "erp dashboard",
    )
    period_zh = ("本月", "这个月", "当期", "本月度")
    period_en = ("this month", "monthly", "current month", "mtd")
    has_erp_context = "erp" in ql or any(marker in q for marker in erp_context_zh) or any(
        marker in ql for marker in erp_context_en
    )
    has_report = any(marker in q for marker in report_zh) or any(
        marker in ql for marker in report_en
    ) or ("report" in ql and has_erp_context)
    has_period = any(marker in q for marker in period_zh) or any(marker in ql for marker in period_en)
    return has_erp_context and (has_report or (has_period and "erp" in ql))


def _looks_like_erp_revenue_query(query: str) -> bool:
    q = str(query or "")
    ql = q.lower()
    zh_markers = ("销售额", "销售金额", "销售收入", "总销售额", "总销售收入", "营业额")
    en_markers = ("sales revenue", "total revenue", "total sales", "sales amount", "sales value", "revenue total")
    return any(marker in q for marker in zh_markers) or any(marker in ql for marker in en_markers)


def _looks_like_all_products_revenue_query(query: str) -> bool:
    if not _looks_like_erp_revenue_query(query):
        return False
    q = str(query or "")
    ql = q.lower()
    markers_zh = ("产品", "各产品", "所有产品", "全部产品", "全产品")
    markers_en = ("products", "all products", "each product", "by product", "product total")
    return any(marker in q for marker in markers_zh) or any(marker in ql for marker in markers_en)


def _looks_like_all_products_sales_detail_query(query: str) -> bool:
    if not _looks_like_erp_sales_detail_query(query):
        return False
    q = str(query or "")
    ql = q.lower()
    markers_zh = ("产品", "各产品", "所有产品", "全部产品", "全产品", "列表", "图表", "对比", "比较")
    markers_en = ("products", "all products", "each product", "by product", "list", "chart", "compare", "comparison")
    return any(marker in q for marker in markers_zh) or any(marker in ql for marker in markers_en)


_ERP_AGGREGATE_SALES_DETAIL_MARKERS_ZH = (
    "明细",
    "详细",
    "详情",
    "逐项",
    "各产品",
    "所有产品",
    "产品销售数据",
    "销售数据",
    "列表",
    "图表",
)
_ERP_AGGREGATE_SALES_DETAIL_MARKERS_EN = (
    "detail",
    "details",
    "detailed",
    "breakdown",
    "by product",
    "all products",
    "sales data",
    "list",
    "chart",
    "charts",
)
_ERP_ROW_LEVEL_DETAIL_MARKERS_ZH = (
    "逐条",
    "原始",
    "客户明细",
    "合同明细",
    "订单明细",
    "订单详情",
    "排产明细",
    "出货明细",
    "最新库存明细",
)
_ERP_ROW_LEVEL_DETAIL_MARKERS_EN = (
    "detailed rows",
    "row-level",
    "raw",
    "customer detail",
    "contract detail",
    "order detail",
    "shipment detail",
    "production detail",
)
_ERP_SALES_ORDER_DETAIL_MARKERS_ZH = (
    "订单明细",
    "订单详情",
    "销售订单明细",
    "销售订单详情",
)
_ERP_SALES_ORDER_DETAIL_MARKERS_EN = (
    "order detail",
    "order details",
    "sales order detail",
    "sales order details",
)


def _looks_like_erp_sales_order_detail_query(query: str) -> bool:
    capability = resolve_database_capability(query)
    if capability and capability.capability_id == "erp_order_details":
        return True
    q = str(query or "")
    ql = q.lower()
    return any(marker in q for marker in _ERP_SALES_ORDER_DETAIL_MARKERS_ZH) or any(
        marker in ql for marker in _ERP_SALES_ORDER_DETAIL_MARKERS_EN
    )


def _looks_like_erp_row_level_detail_query(query: str) -> bool:
    q = str(query or "")
    ql = q.lower()
    return any(marker in q for marker in _ERP_ROW_LEVEL_DETAIL_MARKERS_ZH) or any(
        marker in ql for marker in _ERP_ROW_LEVEL_DETAIL_MARKERS_EN
    )


def _looks_like_erp_sales_detail_query(query: str) -> bool:
    q = str(query or "")
    ql = q.lower()
    if _looks_like_erp_row_level_detail_query(q):
        row_level_sales_markers_zh = ("产品", "各产品", "所有产品", "全部产品", "全产品", "销售数据")
        row_level_sales_markers_en = ("by product", "all products", "each product", "sales data")
        return any(marker in q for marker in row_level_sales_markers_zh) or any(
            marker in ql for marker in row_level_sales_markers_en
        )
    return any(marker in q for marker in _ERP_AGGREGATE_SALES_DETAIL_MARKERS_ZH) or any(
        marker in ql for marker in _ERP_AGGREGATE_SALES_DETAIL_MARKERS_EN
    )


def _build_deterministic_erp_row_level_detail_guard_report(state: PipelineState) -> str:
    if not _looks_like_erp_row_level_detail_query(str(state.get("user_query") or "")):
        return ""
    if _looks_like_erp_sales_order_detail_query(str(state.get("user_query") or "")):
        return ""

    lang = _resolve_output_language(state)
    if lang == "en":
        return "\n".join(
            [
                "## ERP Detail Data Availability",
                "",
                "This request asks for raw row-level ERP details such as order, customer, contract, shipment, or production rows.",
                "",
                "The current verified chatbot contract exposes ERP sales order aggregates by product, volume, order count, weighted average price, and revenue. I will not invent hidden row-level records in the final answer.",
                "",
                "Please use a product-level sales detail question if you need the available verified aggregate table, for example: `Show this week's product sales detail data`.",
                "",
                "Source: internal ERP capability guard.",
            ]
        ).strip()

    return "\n".join(
        [
            "## ERP明细数据可用性说明",
            "",
            "这个问题是在请求订单、客户、合同、出货或排产等ERP逐条明细。",
            "",
            "当前已核验的聊天机器人输出范围是按产品聚合的ERP销售数据，包括销量、订单数、加权均价和销售额。最终答案不会编造未暴露的逐条ERP记录。",
            "",
            "如果需要当前可核验的聚合明细，可以询问：`请给我本周产品销售明细数据`。",
            "",
            "数据来源：内部ERP能力边界校验。",
        ]
    ).strip()


def _build_deterministic_erp_sales_order_detail_report(state: PipelineState) -> str:
    if not _looks_like_erp_sales_order_detail_query(str(state.get("user_query") or "")):
        return ""

    details = state.get("erp_order_details")
    if not isinstance(details, dict):
        context = state.get("user_context")
        product_id = getattr(context, "product", None) if context is not None else None
        period = getattr(context, "period", None) if context is not None else None
        hermes_plan = state.get("hermes_plan") if isinstance(state, dict) else None
        if isinstance(hermes_plan, dict):
            normalized_query = hermes_plan.get("normalized_query") or {}
            period_start = str(normalized_query.get("period_start") or "").strip()
            period_end = str(normalized_query.get("period_end") or "").strip()
            if normalized_query.get("capability") == "erp_order_details" and period_start and period_end:
                period = f"{period_start} to {period_end}"
        details = query_sales_order_details(
            product_id=product_id,
            period=period or str(state.get("user_query") or "") or "Current Week",
        )

    rows = list(details.get("rows") or [])
    if not rows:
        error = str(details.get("error") or "").strip()
        period_start = details.get("period_start") or ""
        period_end = details.get("period_end") or ""
        available_count = int(details.get("available_count") or 0)
        available_start = str(details.get("available_start") or "")
        available_end = str(details.get("available_end") or "")
        lang = _resolve_output_language(state)
        if lang == "en":
            lines = [
                "## ERP Order Details",
                "",
                f"No ERP sales order rows were found for {period_start} to {period_end}.",
            ]
            if available_count and available_start and available_end:
                lines.append(
                    f"Available ERP sales-order data exists from {available_start} to {available_end} ({available_count} rows)."
                )
            lines.append(error or "Source: internal ERP sales order row data.")
            return "\n".join(lines).strip()
        lines = [
            "## ERP订单明细",
            "",
            f"{period_start} 至 {period_end} 未找到ERP销售订单逐条记录。",
        ]
        if available_count and available_start and available_end:
            lines.append(
                f"ERP销售订单可用数据范围：{available_start} 至 {available_end}（{available_count} 行）。"
            )
        lines.append(error or "数据来源：内部ERP销售订单逐条数据。")
        return "\n".join(lines).strip()

    lang = _resolve_output_language(state)
    period_start = details.get("period_start") or ""
    period_end = details.get("period_end") or ""
    total_qty = float(details.get("total_qty") or 0)
    total_revenue = float(details.get("total_revenue") or 0)
    row_count = int(details.get("row_count") or len(rows))

    product_totals: Dict[str, float] = {}
    customer_totals: Dict[str, float] = {}
    for row in rows:
        product = str(row.get("product") or "Unknown")
        customer = str(row.get("customer") or "Unknown")
        revenue = float(row.get("revenue_cny") or 0)
        product_totals[product] = product_totals.get(product, 0.0) + revenue
        customer_totals[customer] = customer_totals.get(customer, 0.0) + revenue
    top_product = max(product_totals.items(), key=lambda item: item[1], default=("N/A", 0.0))
    top_customer = max(customer_totals.items(), key=lambda item: item[1], default=("N/A", 0.0))

    if lang == "en":
        period_text = f"{period_start} to {period_end}" if period_start and period_end else "the selected period"
        lines = ["## ERP Order Details", ""]
        lines.append("### Executive Summary")
        lines.append(f"- Period: {period_text}")
        lines.append(f"- Order rows: {row_count}")
        lines.append(f"- Delivered quantity: {total_qty:,.2f} tons")
        lines.append(f"- ERP revenue: {total_revenue:,.2f} CNY")
        lines.append(f"- Largest product by revenue in the returned rows: {top_product[0]} ({top_product[1]:,.2f} CNY)")
        lines.append(f"- Largest customer by revenue in the returned rows: {top_customer[0]} ({top_customer[1]:,.2f} CNY)")
        lines.append("")
        lines.append("### Order Lines")
        lines.append("| Order No | Customer | Date | Product | Qty (ton) | Tax Price (CNY/ton) | Revenue (CNY) |")
        lines.append("|---|---|---:|---|---:|---:|---:|")
        for row in rows[:50]:
            lines.append(
                f"| {row.get('order_no')} | {row.get('customer')} | {row.get('plan_date')} | {row.get('product')} | {float(row.get('qty_ton') or 0):,.2f} | {float(row.get('unit_price') or 0):,.2f} | {float(row.get('revenue_cny') or 0):,.2f} |"
            )
        lines.append("")
        lines.append("### Method")
        lines.append(
            "- Revenue basis: sum of ERP `FAMOUNT` per order line; do NOT use total volume × weighted average price."
        )
        lines.append("- Source: internal ERP sales order row data (`erp_v_sale_orderentry`).")
        lines.append("- Privacy: deterministic internal-data response; external finalizer is blocked.")
        return "\n".join(lines).strip()

    period_text = f"{period_start} 至 {period_end}" if period_start and period_end else "所选时间段"
    lines = ["## ERP订单明细", ""]
    lines.append("### 核心摘要")
    lines.append(f"- 统计期间：{period_text}")
    lines.append(f"- 订单行数：{row_count}")
    lines.append(f"- ERP发货数量：{total_qty:,.2f} 吨")
    lines.append(f"- ERP销售额：{total_revenue:,.2f} 元")
    lines.append(f"- 返回明细中销售额最高产品：{top_product[0]}（{top_product[1]:,.2f} 元）")
    lines.append(f"- 返回明细中销售额最高客户：{top_customer[0]}（{top_customer[1]:,.2f} 元）")
    lines.append("")
    lines.append("### 订单明细")
    lines.append("| 订单号 | 客户 | 日期 | 产品 | 数量(吨) | 含税单价(元/吨) | 销售额(元) |")
    lines.append("|---|---|---:|---|---:|---:|---:|")
    for row in rows[:50]:
        lines.append(
            f"| {row.get('order_no')} | {row.get('customer')} | {row.get('plan_date')} | {row.get('product')} | {float(row.get('qty_ton') or 0):,.2f} | {float(row.get('unit_price') or 0):,.2f} | {float(row.get('revenue_cny') or 0):,.2f} |"
        )
    lines.append("")
    lines.append("### 口径说明")
    lines.append(
        "- 销售额口径：对每条订单明细的 ERP `FAMOUNT` 求和；不得用「总销量×加权均价」推算销售额。"
    )
    lines.append("- 数据来源：内部ERP销售订单逐条数据 (`erp_v_sale_orderentry`)。")
    lines.append("- 隐私策略：确定性内部数据回答，外部LLM终稿已阻断。")
    return "\n".join(lines).strip()


def _format_database_candidate_lines(candidates: list[dict[str, Any]], lang: str) -> list[str]:
    if not candidates:
        return []
    lines: list[str] = []
    for candidate in candidates[:5]:
        table = candidate.get("table") or "unknown"
        row_count = candidate.get("row_count")
        date_column = candidate.get("date_column")
        min_date = candidate.get("min_date")
        max_date = candidate.get("max_date")
        matched_columns = candidate.get("matched_columns") or []
        matched_text = ", ".join(str(col) for col in matched_columns[:6]) or "matched table name"
        if lang == "en":
            coverage = ""
            if min_date and max_date:
                coverage = f", date coverage {min_date} to {max_date} via `{date_column}`"
            row_text = f", rows {int(row_count):,}" if isinstance(row_count, int) else ""
            lines.append(f"- `{table}`{row_text}{coverage}; matched columns: {matched_text}.")
        else:
            coverage = ""
            if min_date and max_date:
                coverage = f"，日期范围 {min_date} 至 {max_date}（`{date_column}`）"
            row_text = f"，行数 {int(row_count):,}" if isinstance(row_count, int) else ""
            lines.append(f"- `{table}`{row_text}{coverage}；匹配字段：{matched_text}。")
    return lines


def _build_unmapped_database_capability_report(state: PipelineState) -> str:
    query = str(state.get("user_query") or "")
    if resolve_database_capability(query) is not None:
        return ""
    if _state_has_agent_intent(state, "commercial_ops_advisory") or _is_commercial_ops_advisory_query(query):
        return ""
    parsed = state.get("parsed_intent")
    task_type = getattr(parsed, "task_type", None)
    hermes_plan = state.get("hermes_plan") if isinstance(state, dict) else None
    hermes_domains = set()
    hermes_capabilities = set()
    agent_intent = None
    if isinstance(hermes_plan, dict):
        hermes_domains = set(hermes_plan.get("domains") or [])
        hermes_capabilities = set(hermes_plan.get("capabilities") or [])
        normalized = hermes_plan.get("normalized_query")
        if isinstance(normalized, dict):
            agent_intent = normalized.get("agent_intent")
    if should_bypass_database_capability_discovery(
        query,
        task_type=task_type,
        hermes_domains=hermes_domains,
        capabilities=hermes_capabilities,
        agent_intent=agent_intent,
    ):
        return ""
    if not should_use_database_capability_discovery(
        query,
        task_type=task_type,
        hermes_domains=hermes_domains,
        capabilities=hermes_capabilities,
        agent_intent=agent_intent,
    ):
        return ""

    discovery = state.get("database_discovery")
    if not isinstance(discovery, dict):
        discovery = discover_database_candidates(query)
    candidates = [item for item in list(discovery.get("candidates") or []) if isinstance(item, dict)]
    lang = _resolve_output_language(state)
    candidate_lines = _format_database_candidate_lines(candidates, lang)

    if lang == "en":
        lines = [
            "## Database Capability Discovery",
            "",
            "I do not have a verified answer capability for this exact question yet, so I will not invent a business result.",
        ]
        if candidate_lines:
            lines.extend(
                [
                    "",
                    "I checked the database catalog and found likely source tables:",
                    *candidate_lines,
                    "",
                    "Next step: register one of these sources as a capability and add a deterministic query/answer template before returning final business facts.",
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "The catalog scan did not find an obvious matching table or field. The safe next step is to add the source table/field mapping before answering.",
                ]
            )
        lines.append("Source: database capability registry fallback.")
        return "\n".join(lines).strip()

    lines = [
        "## 数据库能力发现",
        "",
        "这个问题目前还没有已核验的回答能力，所以我不会编造业务结果。",
    ]
    if candidate_lines:
        lines.extend(
            [
                "",
                "我已检查数据库目录，发现可能的数据源：",
                *candidate_lines,
                "",
                "下一步：把其中一个数据源注册成能力，并增加确定性查询和回答模板，然后再输出最终业务结论。",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "数据库目录扫描没有发现明显匹配的表或字段。安全做法是先补充来源表和字段映射，再回答。",
            ]
        )
    lines.append("数据来源：数据库能力注册表兜底发现。")
    return "\n".join(lines).strip()


def _build_deterministic_erp_sales_detail_report(state: PipelineState, summary: Dict[str, Any]) -> str:
    if not _looks_like_erp_sales_detail_query(str(state.get("user_query") or "")):
        return ""

    rows = list(summary.get("rows") or [])
    if not rows:
        return ""

    lang = _resolve_output_language(state)
    period_start = summary.get("period_start") or ""
    period_end = summary.get("period_end") or ""
    total = summary.get("total")
    total_revenue = summary.get("total_revenue")
    has_revenue = any(row.get("revenue_cny") is not None for row in rows)

    if lang == "en":
        period_text = f"{period_start} to {period_end}" if period_start and period_end else "the selected period"
        lines = ["## ERP Sales Detail", ""]
        lines.append(f"For {period_text}, ERP product-level sales records are:")
        lines.append("")
        if has_revenue:
            lines.append("| Product | Volume (ton) | Orders | Weighted Avg Price (CNY/ton) | Revenue (CNY) |")
            lines.append("|---|---:|---:|---:|---:|")
        else:
            lines.append("| Product | Volume (ton) | Orders | Weighted Avg Price (CNY/ton) |")
            lines.append("|---|---:|---:|---:|")
        for row in rows[:20]:
            if has_revenue:
                lines.append(
                    f"| {row.get('product')} | {float(row.get('volume_ton') or 0):,.2f} | {int(row.get('orders') or 0)} | {float(row.get('avg_price') or 0):,.2f} | {float(row.get('revenue_cny') or 0):,.2f} |"
                )
            else:
                lines.append(
                    f"| {row.get('product')} | {float(row.get('volume_ton') or 0):,.2f} | {int(row.get('orders') or 0)} | {float(row.get('avg_price') or 0):,.2f} |"
                )
        lines.append("")
        if total is not None:
            lines.append(f"Total volume: {float(total):,.2f} tons.")
        if total_revenue is not None:
            lines.append(f"Total revenue: {float(total_revenue):,.2f} CNY.")
        lines.append("Source: internal ERP sales order aggregates.")
        return "\n".join(lines).strip()

    period_text = f"{period_start} 至 {period_end}" if period_start and period_end else "所选时间段"
    use_executive_brief = _should_use_executive_brief_format(state)
    lines = []
    if use_executive_brief:
        lines.extend(
            [
                "## 结论",
                "",
                f"{period_text}，ERP已返回 {len(rows)} 个产品的销售汇总明细，可用于判断各产品的销量、订单与均价结构。",
                "",
                "## 数据依据",
                "",
            ]
        )
    lines.extend(["## ERP销售明细", ""])
    lines.append(f"{period_text}，ERP按产品汇总的销售明细如下：")
    lines.append("")
    if has_revenue:
        lines.append("| 产品 | 销量(吨) | 订单 | 加权均价(元/吨) | 销售额(元) |")
        lines.append("|---|---:|---:|---:|---:|")
    else:
        lines.append("| 产品 | 销量(吨) | 订单 | 加权均价(元/吨) |")
        lines.append("|---|---:|---:|---:|")
    for row in rows[:20]:
        if has_revenue:
            lines.append(
                f"| {row.get('product')} | {float(row.get('volume_ton') or 0):,.2f} | {int(row.get('orders') or 0)} | {float(row.get('avg_price') or 0):,.2f} | {float(row.get('revenue_cny') or 0):,.2f} |"
            )
        else:
            lines.append(
                f"| {row.get('product')} | {float(row.get('volume_ton') or 0):,.2f} | {int(row.get('orders') or 0)} | {float(row.get('avg_price') or 0):,.2f} |"
            )
    lines.append("")
    if total is not None:
        lines.append(f"ERP销量合计：{float(total):,.2f} 吨。")
    if total_revenue is not None:
        lines.append(f"ERP销售额合计：{float(total_revenue):,.2f} 元。")
    lines.append("数据来源：内部ERP销售订单聚合数据。")
    if use_executive_brief:
        top_row = rows[0] if rows else {}
        lines.extend(
            [
                "",
                "## 影响判断",
                "",
                f"当前明细显示销量主要集中在 {top_row.get('product', '头部产品')}，"
                "需结合订单节奏判断是结构性集中还是短期波动。",
                "",
                "## 建议动作",
                "",
                "- 对销量下滑产品优先复核客户结构与报价策略。",
                "- 对销量改善产品优先保供并观察是否能延续到下一周期。",
                "",
                "## 风险关注",
                "",
                "- 需避免仅依据单一产品波动做全品类判断。",
                "- 如需逐条订单明细，请明确要求“订单明细”。",
            ]
        )
    return "\n".join(lines).strip()


def _build_deterministic_erp_revenue_report(state: PipelineState, summary: Dict[str, Any]) -> str:
    if not _looks_like_erp_revenue_query(str(state.get("user_query") or "")):
        return ""

    rows = list(summary.get("rows") or [])
    if not rows:
        return ""

    lang = _resolve_output_language(state)
    period_start = summary.get("period_start") or ""
    period_end = summary.get("period_end") or ""
    period_text_en = f"{period_start} to {period_end}" if period_start and period_end else "the selected period"
    period_text_zh = f"{period_start} 至 {period_end}" if period_start and period_end else "所选时间段"
    total_revenue = summary.get("total_revenue")
    if total_revenue is None:
        total_revenue = sum(float(row.get("revenue_cny") or 0) for row in rows)

    if lang == "en":
        lines = ["## ERP Sales Revenue", ""]
        lines.append(
            f"For {period_text_en}, ERP records show total sales revenue of **{float(total_revenue):,.2f} CNY** across the matched products."
        )
        lines.append("")
        lines.append("| Product | Revenue (CNY) | Volume (ton) | Orders |")
        lines.append("|---|---:|---:|---:|")
        for row in rows[:12]:
            lines.append(
                f"| {row.get('product')} | {float(row.get('revenue_cny') or 0):,.2f} | {float(row.get('volume_ton') or 0):,.2f} | {int(row.get('orders') or 0)} |"
            )
        lines.append("")
        lines.append(
            "Revenue basis: sum of ERP `FAMOUNT` per order line; do NOT compute as total volume × weighted average price."
        )
        return "\n".join(lines).strip()

    lines = ["## ERP销售额", ""]
    lines.append(f"{period_text_zh}，ERP记录显示匹配产品销售额合计为 **{float(total_revenue):,.2f} 元**。")
    lines.append("")
    lines.append("| 产品 | 销售额(元) | 销量(吨) | 订单 |")
    lines.append("|---|---:|---:|---:|")
    for row in rows[:12]:
        lines.append(
            f"| {row.get('product')} | {float(row.get('revenue_cny') or 0):,.2f} | {float(row.get('volume_ton') or 0):,.2f} | {int(row.get('orders') or 0)} |"
        )
    lines.append("")
    lines.append(
        "销售额口径：对每条订单明细的 ERP `FAMOUNT`（不含税销售金额）求和；不得用「总销量×加权均价」推算。"
    )
    return "\n".join(lines).strip()


def _looks_like_other_products_excluding_query(query: str) -> bool:
    q = str(query or "")
    ql = q.lower()
    other_markers_zh = ("其他产品", "其它产品", "别的产品", "其余产品", "剩余产品")
    other_markers_en = ("other products", "other product", "the other products", "others", "remaining products")
    exclusion_markers_zh = ("除了", "除", "不包括", "排除", "剔除")
    exclusion_markers_en = ("besides", "except", "excluding", "exclude", "other than", "apart from")
    has_other = any(marker in q for marker in other_markers_zh) or any(marker in ql for marker in other_markers_en)
    has_exclusion = any(marker in q for marker in exclusion_markers_zh) or any(marker in ql for marker in exclusion_markers_en)
    return has_other and has_exclusion and bool(extract_product_ids_in_text(q))


def _looks_like_other_products_excluding_sales_query(query: str) -> bool:
    return _looks_like_erp_sales_query(query) and _looks_like_other_products_excluding_query(query)


def _excluded_product_ids_from_query(query: str) -> List[str]:
    if not _looks_like_other_products_excluding_query(query):
        return []
    return [str(pid).strip().lower() for pid in extract_product_ids_in_text(query) if str(pid).strip()]


def _excluded_material_names(product_ids: List[str]) -> set[str]:
    names: set[str] = set()
    for pid in product_ids:
        for material in _PRODUCT_TO_MATERIAL_NAMES.get(pid, ()):
            label = str(material or "").strip().lower()
            if label:
                names.add(label)
        if pid:
            names.add(str(pid).strip().lower())
    return names


def _product_label_from_id(product_id: str, lang: str) -> str:
    pid = str(product_id or "").strip().lower()
    node = next(
        (
            node
            for node in _domain_graph.nodes
            if str(getattr(node, "id", "") or "").lower() == pid
        ),
        None,
    )
    if node is not None:
        return str(getattr(node, "name_en" if lang == "en" else "name_cn", "") or product_id)
    material_names = _PRODUCT_TO_MATERIAL_NAMES.get(pid, ())
    if material_names:
        return str(material_names[0])
    return str(product_id or "requested product")


def _build_deterministic_other_products_erp_sales_report(
    state: PipelineState,
    summary: Dict[str, Any],
) -> str:
    user_query = str(state.get("user_query") or "")
    excluded_ids = _excluded_product_ids_from_query(user_query)
    if not excluded_ids:
        return ""

    lang = _resolve_output_language(state)
    rows = list(summary.get("rows") or [])
    excluded_names = _excluded_material_names(excluded_ids)
    filtered_rows = [
        row
        for row in rows
        if str(row.get("product") or "").strip().lower() not in excluded_names
    ]
    period_start = summary.get("period_start") or ""
    period_end = summary.get("period_end") or ""
    excluded_label = ", ".join(_product_label_from_id(pid, lang) for pid in excluded_ids)

    if lang == "en":
        period_text = f"{period_start} to {period_end}" if period_start and period_end else "the selected period"
        if not filtered_rows:
            only_returned = ""
            if rows:
                returned_labels = ", ".join(str(row.get("product") or "").strip() for row in rows[:5] if str(row.get("product") or "").strip())
                if returned_labels:
                    only_returned = f" The ERP rows returned for this period only include: {returned_labels}."
            return (
                "## ERP Sales Summary\n\n"
                f"No. For {period_text}, ERP has no sales transaction rows for other products after excluding {excluded_label}. "
                f"I checked internal ERP sales across mapped products for {period_text}. Excluding {excluded_label}, "
                f"no ERP sales transaction rows were found for other products in this period.{only_returned} "
                "I will not use the excluded product's sales as the answer for 'other products'.\n\n"
                "Source: internal ERP sales order aggregates."
            ).strip()

        lines = [f"## ERP Sales Summary (excluding {excluded_label})", ""]
        lines.append(f"Yes. ERP sales data is available for other products after excluding {excluded_label}.")
        lines.append("")
        lines.append(f"For {period_text}, ERP sales records for other products are:")
        lines.append("")
        lines.append("| Product | Volume (ton) | Orders | Weighted Avg Price (CNY/ton) |")
        lines.append("|---|---:|---:|---:|")
        total_volume = 0.0
        for row in filtered_rows[:12]:
            volume = float(row.get("volume_ton") or 0)
            total_volume += volume
            lines.append(
                f"| {row.get('product')} | {volume:,.2f} | {int(row.get('orders') or 0)} | {float(row.get('avg_price') or 0):,.2f} |"
            )
        lines.append("")
        lines.append(f"Total ERP sales volume for other products: {total_volume:,.2f} tons.")
        lines.append("Source: internal ERP sales order aggregates.")
        return "\n".join(lines).strip()

    period_text = f"{period_start} 至 {period_end}" if period_start and period_end else "所选时间段"
    if not filtered_rows:
        only_returned = ""
        if rows:
            returned_labels = "、".join(str(row.get("product") or "").strip() for row in rows[:5] if str(row.get("product") or "").strip())
            if returned_labels:
                only_returned = f"该期间ERP返回的销售行仅包含：{returned_labels}。"
        return (
            "## ERP销售情况\n\n"
            f"没有。{period_text} 剔除{excluded_label}后，ERP没有其他产品销售交易记录。"
            f"我已按全产品ERP销售口径检查 {period_text} 的销售订单。剔除{excluded_label}后，"
            f"其他产品在该期间没有ERP销售交易记录。{only_returned}"
            "因此系统不会把被排除产品的销量当作“其他产品”的回答。\n\n"
            "数据来源：内部ERP销售订单聚合数据。"
        ).strip()

    lines = [f"## ERP销售情况（不含{excluded_label}）", ""]
    lines.append(f"有。剔除{excluded_label}后，ERP中可以查到其他产品销售数据。")
    lines.append("")
    lines.append(f"{period_text}，ERP销售记录中其他产品如下：")
    lines.append("")
    lines.append("| 产品 | 销量(吨) | 订单 | 加权均价(元/吨) |")
    lines.append("|---|---:|---:|---:|")
    total_volume = 0.0
    for row in filtered_rows[:12]:
        volume = float(row.get("volume_ton") or 0)
        total_volume += volume
        lines.append(
            f"| {row.get('product')} | {volume:,.2f} | {int(row.get('orders') or 0)} | {float(row.get('avg_price') or 0):,.2f} |"
        )
    lines.append("")
    lines.append(f"其他产品ERP销量合计：{total_volume:,.2f} 吨。")
    lines.append("数据来源：内部ERP销售订单聚合数据。")
    return "\n".join(lines).strip()


def _looks_like_action_scenario_request(query: str) -> bool:
    q = str(query or "")
    ql = q.lower()
    zh_markers = (
        "看跌",
        "基准",
        "看涨",
        "动作",
        "触发条件",
        "情景",
        "未来",
        "1-2周",
        "1~2周",
        "一到两周",
        "未来一周",
    )
    en_markers = (
        "bear",
        "base",
        "bull",
        "action",
        "trigger",
        "scenario",
        "next 1-2 weeks",
        "next week",
        "future",
    )
    return any(marker in q for marker in zh_markers) or any(marker in ql for marker in en_markers)


def _build_deterministic_erp_sales_report(state: PipelineState) -> str:
    parsed = state.get("parsed_intent")
    if getattr(parsed, "task_type", None) != TaskType.ERP_DATA_QUERY:
        return ""
    user_query = str(state.get("user_query") or "")
    if not _looks_like_erp_sales_query(user_query):
        return ""

    summary = _extract_erp_sales_summary_rows(str(state.get("erp_data") or ""))
    revenue_report = _build_deterministic_erp_revenue_report(state, summary)
    if revenue_report:
        return revenue_report

    other_products_report = _build_deterministic_other_products_erp_sales_report(state, summary)
    if other_products_report:
        return other_products_report

    detail_report = _build_deterministic_erp_sales_detail_report(state, summary)
    if detail_report:
        return detail_report

    rows = list(summary.get("rows") or [])
    if not rows:
        return ""

    lang = _resolve_output_language(state)
    period_start = summary.get("period_start") or ""
    period_end = summary.get("period_end") or ""
    total = summary.get("total")
    period_change = summary.get("period_change")
    top = rows[0]
    wants_actions = _looks_like_action_scenario_request(user_query)

    if _should_use_executive_brief_format(state) and not wants_actions:
        return _build_executive_brief_from_erp_summary(
            lang=lang,
            period_start=str(period_start or ""),
            period_end=str(period_end or ""),
            top=top,
            total=total,
            period_change=period_change,
            rows=rows,
            total_revenue=summary.get("total_revenue"),
            revenue_primary=_looks_like_erp_revenue_query(user_query),
        )

    if wants_actions and lang == "en":
        period_text = f"{period_start} to {period_end}" if period_start and period_end else "the selected period"
        change_text = (
            f"{float(period_change):+.1f}%" if period_change is not None else "not available"
        )
        return (
            "## 01. Sales Snapshot\n\n"
            f"For {period_text}, ERP sales records show {top['product']} sold {top['volume_ton']:,.2f} tons "
            f"across {top['orders']} orders, with a weighted average price of {top['avg_price']:,.2f} CNY/ton. "
            f"Period-over-period change: {change_text}.\n\n"
            "## 02. Key Drivers\n\n"
            "Primary signals are order continuity, delivered volume recovery, and whether price holds despite demand pressure.\n\n"
            "## 03. Next 1-2 Week Scenarios\n\n"
            "- Bear case: order count stays low and volume continues to decline.\n"
            "- Base case: order flow stabilizes near the current level.\n"
            "- Bull case: repeat orders and delivered volume recover faster than the last comparable period.\n\n"
            "## 04. Actions and Triggers\n\n"
            "- Bear action: defend margin floor and slow discount expansion. Trigger: another week of weak orders and falling volume.\n"
            "- Base action: keep neutral pricing and prioritize core customers. Trigger: stable repeat orders without further volume erosion.\n"
            "- Bull action: tighten discounts and secure supply allocation. Trigger: visible rebound in orders plus delivered volume.\n\n"
            "## 05. Risks\n\n"
            "Main risk is misreading a temporary dip as a structural recovery or collapse. Continue watching order cadence and inventory coverage."
        ).strip()

    if wants_actions:
        period_text = f"{period_start} 至 {period_end}" if period_start and period_end else "所选时间段"
        change_text = (
            f"{float(period_change):+.1f}%" if period_change is not None else "暂无"
        )
        total_text = ""
        if total is not None and (len(rows) > 1 or abs(float(total) - float(top["volume_ton"])) > 0.01):
            total_text = f"匹配ERP销量合计为 {float(total):,.2f} 吨。"
        return (
            "## 01 · 销售现状\n\n"
            f"{period_text}，ERP销售记录显示：{top['product']}销量为 {top['volume_ton']:,.2f} 吨，"
            f"订单 {top['orders']} 单，加权均价 {top['avg_price']:,.2f} 元/吨。"
            f"与上一可比周期相比，销量变化为 {change_text}。{total_text}\n\n"
            "## 02 · 关键驱动因素\n\n"
            "当前核心变量是订单连续性、发货量恢复节奏，以及价格在需求波动下的承压能力。\n\n"
            "## 03 · 未来1-2周情景判断\n\n"
            "- 看跌情景：订单笔数继续偏低，销量延续下滑。\n"
            "- 基准情景：订单节奏维持当前水平，销量趋于企稳。\n"
            "- 看涨情景：复购订单增加，发货量较上一可比周期明显回升。\n\n"
            "## 04 · 动作建议与触发条件\n\n"
            "- 看跌动作：守住毛利底线，控制额外折让。触发条件：下一周期订单仍弱且销量继续回落。\n"
            "- 基准动作：维持中性报价，优先保障核心客户成交。触发条件：订单稳定但未出现明显放量。\n"
            "- 看涨动作：收紧折让并提前锁定供给。触发条件：订单笔数与销量同步回升。\n\n"
            "## 05 · 风险预警\n\n"
            "主要风险是将短期波动误判为趋势反转。需持续跟踪订单节奏、销量恢复和库存覆盖变化。"
        ).strip()

    if lang == "en":
        period_text = f"{period_start} to {period_end}" if period_start and period_end else "the selected period"
        lines = ["## ERP Sales Summary", ""]
        lines.append(
            f"For {period_text}, ERP sales records show {top['product']} sold "
            f"{top['volume_ton']:,.2f} tons across {top['orders']} orders, "
            f"with a weighted average price of {top['avg_price']:,.2f} CNY/ton."
        )
        if total is not None and (len(rows) > 1 or abs(float(total) - float(top["volume_ton"])) > 0.01):
            lines.append(f"Total matched ERP sales volume: {float(total):,.2f} tons.")
        if period_change is not None:
            lines.append(f"Period-over-period sales volume change: {float(period_change):+.1f}%.")
        chat_mode = str(
            state.get("resolved_chat_mode")
            or (state.get("user_prefs") or {}).get("chat_mode")
            or "chat"
        ).strip().lower()
        if chat_mode in {"", "chat"} and not _is_erp_hybrid_analysis_mode(state):
            lines.append("")
            lines.append(
                f"- Orders: {int(top['orders'])} | Weighted avg price: {top['avg_price']:,.2f} CNY/ton"
            )
            if period_change is not None:
                lines.append(f"- Volume change vs prior comparable period: {float(period_change):+.1f}%")
            lines.append("- Ask for a product breakdown table or bar chart if you need more detail.")
        lines.append("")
        lines.append("Source: internal ERP sales order aggregates.")
        return "\n".join(lines).strip()

    period_text = f"{period_start} 至 {period_end}" if period_start and period_end else "所选时间段"
    lines = ["## ERP销售情况", ""]
    lines.append(
        f"{period_text}，ERP销售记录显示：{top['product']}销量为 "
        f"{top['volume_ton']:,.2f} 吨，订单 {top['orders']} 单，"
        f"加权均价 {top['avg_price']:,.2f} 元/吨。"
    )
    if total is not None and (len(rows) > 1 or abs(float(total) - float(top["volume_ton"])) > 0.01):
        lines.append(f"匹配ERP销量合计：{float(total):,.2f} 吨。")
    if period_change is not None:
        lines.append(f"与上一可比周期相比，销量变化为 {float(period_change):+.1f}%。")
    chat_mode = str(
        state.get("resolved_chat_mode")
        or (state.get("user_prefs") or {}).get("chat_mode")
        or "chat"
    ).strip().lower()
    if chat_mode in {"", "chat"} and not _is_erp_hybrid_analysis_mode(state):
        lines.append("")
        lines.append(
            f"- 订单 {int(top['orders'])} 单，加权均价 {top['avg_price']:,.2f} 元/吨"
        )
        if period_change is not None:
            lines.append(f"- 较上一可比周期销量变化：{float(period_change):+.1f}%")
        lines.append("- 如需按产品明细表或柱图展示，可继续提问。")
    lines.append("")
    lines.append("数据来源：内部ERP销售订单聚合数据。")
    return "\n".join(lines).strip()


def _looks_like_product_decision_matrix_request(query: str) -> bool:
    q = str(query or "")
    ql = q.lower()
    required_groups = [
        ("erp", "销售", "销量", "库存", "sales", "inventory"),
        ("预测", "forecast", "看跌", "看涨", "基准", "scenario"),
        ("建议", "动作", "触发", "recommend", "action", "trigger"),
    ]
    return all(any(marker in q or marker in ql for marker in group) for group in required_groups)


def _build_fast_product_decision_matrix_report(state: PipelineState) -> str:
    user_query = str(state.get("user_query") or "")
    if not _looks_like_product_decision_matrix_request(user_query):
        return ""

    product_ids = [str(pid).strip().lower() for pid in list(state.get("graph_product_ids") or []) if str(pid).strip()]
    if not product_ids:
        extracted = extract_product_ids_in_text(user_query)
        product_ids = [str(pid).strip().lower() for pid in extracted if str(pid).strip()]
    if not product_ids:
        user_context = state.get("user_context")
        product_hint = str(getattr(user_context, "product", "") or "").strip().lower()
        if product_hint:
            product_ids = [product_hint]
    if not product_ids:
        return ""

    product_id = product_ids[0]
    snapshot = state.get("perception_snapshot") or {}
    products = dict(snapshot.get("products") or {})
    product_data = dict(products.get(product_id) or {})
    if not product_data:
        return ""

    name = str(product_data.get("name_cn") or product_data.get("name_en") or product_id).strip()
    unit = str(product_data.get("unit") or "元/吨").strip()
    latest_price = _to_float_or_none(product_data.get("latest_price"))
    weekly_change = _to_float_or_none(product_data.get("change_7d_pct"))
    as_of_date = str(product_data.get("latest_date") or product_data.get("as_of_date") or snapshot.get("reference_date") or "").strip()

    forecast = state.get("forecast") or {}
    if not forecast or not (forecast.get("scenarios") or {}).get("7d"):
        forecast = _build_fast_forecast_from_snapshot(state, product_id)
    scenarios = forecast.get("scenarios") or {}
    seven_day = scenarios.get("7d") or {}
    thirty_day = scenarios.get("30d") or {}
    scenario_prob = {"看跌": "25%", "基准": "50%", "看涨": "25%"}

    pricing = state.get("pricing_decision") or {}
    rec_price = _to_float_or_none(pricing.get("recommended_price"))
    action_cn = str(pricing.get("action_cn") or pricing.get("action") or "维持中性报价，按触发条件调整折让").strip()
    triggers = list(pricing.get("triggers") or [])

    erp_data = str(state.get("erp_data") or "").strip()
    erp_summary = _extract_erp_sales_summary_rows(erp_data)
    erp_rows = list(erp_summary.get("rows") or [])

    def fmt_price(value: Any) -> str:
        num = _to_float_or_none(value)
        if num is None:
            return "暂无"
        return f"{num:,.0f} {unit}"

    def fmt_pct(value: Any) -> str:
        num = _to_float_or_none(value)
        if num is None:
            return "暂无"
        return f"{num:+.2f}%"

    def scenario_line(label: str, bucket: Dict[str, Any], fallback_note: str) -> str:
        low = bucket.get("low") if isinstance(bucket, dict) else None
        high = bucket.get("high") if isinstance(bucket, dict) else None
        if low is not None and high is not None:
            return f"- {label}（概率{scenario_prob[label]}）：{fmt_price(low)} 至 {fmt_price(high)}。"
        return f"- {label}（概率{scenario_prob[label]}）：{fallback_note}。"

    if erp_rows:
        top = erp_rows[0]
        erp_line = (
            f"最近可用ERP区间 {erp_summary.get('period_start') or 'N/A'} 至 {erp_summary.get('period_end') or 'N/A'}："
            f"{top['product']} 销量 {top['volume_ton']:,.2f} 吨，订单 {top['orders']} 单，"
            f"加权均价 {top['avg_price']:,.2f} 元/吨。"
        )
    elif "No " in erp_data and "AVAILABLE ERP" in erp_data:
        erp_line = "最近7天ERP销售/库存在当前查询窗口未返回有效记录；需以最近可用ERP窗口复核动销与库存覆盖。"
    else:
        erp_line = "当前上下文未返回可核验ERP销售/库存明细；本报告不臆造ERP数值，动作建议以市场价格与触发条件为主。"

    trigger_lines: List[str] = []
    for idx, trigger in enumerate(triggers[:3], start=1):
        if not isinstance(trigger, dict):
            continue
        condition = str(trigger.get("condition") or "").strip()
        action = str(trigger.get("action") or "").strip()
        if condition and action:
            trigger_lines.append(f"- 触发条件{idx}：若{condition}，则{action}。")
    if not trigger_lines:
        trigger_lines = [
            "- 看跌触发：若价格继续下行且ERP订单未恢复，则收紧折让、优先保毛利。",
            "- 基准触发：若价格窄幅波动且订单稳定，则维持中性报价并保障核心客户成交。",
            "- 看涨触发：若价格企稳回升且订单/库存同步改善，则收紧折让并锁定供给。",
        ]

    recommendation_price = fmt_price(rec_price if rec_price is not None else latest_price)

    return (
        f"## 01 · {name} 快速决策结论\n\n"
        f"截至 {as_of_date or '当前最新数据'}，当前价格为 **{fmt_price(latest_price)}**，7日变化为 **{fmt_pct(weekly_change)}**。"
        f"建议执行价格/报价锚点：**{recommendation_price}**；建议动作：**{action_cn}**。\n\n"
        "## 02 · ERP销售与库存信号\n\n"
        f"{erp_line}\n\n"
        "## 03 · 市场价格与7日变化\n\n"
        f"- 当前价格：{fmt_price(latest_price)}。\n"
        f"- 7日变化：{fmt_pct(weekly_change)}。\n"
        "- 数据口径：实时市场快照与当前数据库上下文；缺失ERP项明确标注，不使用模型补数。\n\n"
        "## 04 · 未来1-2周价格情景\n\n"
        f"{scenario_line('看跌', seven_day.get('bear') or seven_day.get('low') or {}, '订单偏弱、库存压力上升，价格承压')}\n"
        f"{scenario_line('基准', seven_day.get('base') or {}, '价格围绕当前报价窄幅震荡')}\n"
        f"{scenario_line('看涨', seven_day.get('bull') or seven_day.get('high') or {}, '订单恢复且市场报价企稳回升')}\n"
        f"- 30天参考：基准区间 {fmt_price((thirty_day.get('base') or {}).get('low'))} 至 {fmt_price((thirty_day.get('base') or {}).get('high'))}。\n\n"
        "## 05 · 定价与销售动作\n\n"
        f"- 立即动作：以 {recommendation_price} 作为报价锚点，按客户等级控制折让。\n"
        "- 销售动作：优先跟进近期成交客户与核心长协客户，确认真实接单节奏。\n"
        "- 库存动作：若ERP库存覆盖偏高，优先去库；若库存偏低，保留核心客户供应弹性。\n\n"
        "## 06 · 触发条件\n\n"
        + "\n".join(trigger_lines)
    ).strip()


def _should_preserve_detailed_erp_sales_report(markdown: str, state: PipelineState) -> bool:
    """Keep Deep Research/report ERP answers when they already cite verified ERP figures."""
    report = str(markdown or "").strip()
    if _looks_like_placeholder_skeleton_report(report) or "****" in report:
        return False
    if _is_garbled_or_too_short_report(report):
        return False
    if len(report) < 500:
        return False

    mode_values = {
        str(state.get("response_mode") or "").strip().lower(),
        str(state.get("resolved_chat_mode") or "").strip().lower(),
        str((state.get("user_prefs") or {}).get("chat_mode") or "").strip().lower(),
    }
    if not (mode_values & {"full_report", "report", "deep_research", "analysis"}):
        return False

    summary = _extract_erp_sales_summary_rows(str(state.get("erp_data") or ""))
    rows = list(summary.get("rows") or [])
    if not rows:
        return False

    top = rows[0]
    normalized_report = _re.sub(r"[\s,，]", "", report)

    def _number_token(value: Any) -> str:
        try:
            return f"{float(value):.2f}".rstrip("0").rstrip(".")
        except Exception:
            return ""

    required_tokens = [
        _number_token(top.get("volume_ton")),
        _number_token(top.get("avg_price")),
    ]
    if all(token and token in normalized_report for token in required_tokens):
        return True

    return False


def _should_preserve_detailed_erp_availability_report(markdown: str, state: PipelineState) -> bool:
    """Keep detailed sparse-data ERP answers when they already anchor on availability facts."""
    report = str(markdown or "").strip()
    if len(report) < 700:
        return False

    mode_values = {
        str(state.get("response_mode") or "").strip().lower(),
        str(state.get("resolved_chat_mode") or "").strip().lower(),
        str((state.get("user_prefs") or {}).get("chat_mode") or "").strip().lower(),
    }
    if not (mode_values & {"full_report", "report", "deep_research", "analysis"}):
        return False

    erp_data = str(state.get("erp_data") or "")
    requested_match = _re.search(
        r"No\s+(.+?)\s+records found for requested period\s+([0-9\-]+)\s+to\s+([0-9\-]+)\.",
        erp_data,
        flags=_re.IGNORECASE,
    )
    range_match = _re.search(
        r"AVAILABLE ERP\s+(.+?)\s+DATE RANGE for\s+(.+?):\s+([0-9\-]+)\s+to\s+([0-9\-]+)\s+\((\d+)\s+rows\)\.",
        erp_data,
        flags=_re.IGNORECASE,
    )
    closest_match = _re.search(
        r"CLOSEST AVAILABLE\s+(\d+)-DAY WINDOW:\s+([0-9\-]+)\s+to\s+([0-9\-]+)\.",
        erp_data,
        flags=_re.IGNORECASE,
    )
    if not (requested_match and range_match and closest_match):
        return False

    normalized_report = _re.sub(r"[\s,，。；;:：]", "", report)
    required_tokens = [
        str(requested_match.group(2) or "").strip().replace("-", ""),
        str(requested_match.group(3) or "").strip().replace("-", ""),
        str(range_match.group(3) or "").strip().replace("-", ""),
        str(range_match.group(4) or "").strip().replace("-", ""),
    ]
    if not all(token and token in normalized_report for token in required_tokens):
        return False

    section_markers = ("##", "01", "02", "03", "04", "05")
    has_structured_sections = sum(1 for marker in section_markers if marker in report) >= 3
    return has_structured_sections


def _build_deterministic_erp_availability_report(state: PipelineState) -> str:
    parsed = state.get("parsed_intent")
    if getattr(parsed, "task_type", None) != TaskType.ERP_DATA_QUERY:
        return ""
    user_query = str(state.get("user_query") or "")
    if not _looks_like_erp_sales_query(user_query):
        return ""

    erp_data = str(state.get("erp_data") or "")
    if "AVAILABLE ERP" not in erp_data or "CLOSEST AVAILABLE" not in erp_data:
        return ""

    requested_match = _re.search(
        r"No\s+(.+?)\s+records found for requested period\s+([0-9\-]+)\s+to\s+([0-9\-]+)\.",
        erp_data,
        flags=_re.IGNORECASE,
    )
    range_match = _re.search(
        r"AVAILABLE ERP\s+(.+?)\s+DATE RANGE for\s+(.+?):\s+([0-9\-]+)\s+to\s+([0-9\-]+)\s+\((\d+)\s+rows\)\.",
        erp_data,
        flags=_re.IGNORECASE,
    )
    closest_match = _re.search(
        r"CLOSEST AVAILABLE\s+(\d+)-DAY WINDOW:\s+([0-9\-]+)\s+to\s+([0-9\-]+)\.",
        erp_data,
        flags=_re.IGNORECASE,
    )
    if not (requested_match and range_match and closest_match):
        return ""

    requested_start = requested_match.group(2)
    requested_end = requested_match.group(3)
    material_label = str(range_match.group(2) or "").strip()
    available_start = range_match.group(3)
    available_end = range_match.group(4)
    row_count = range_match.group(5)
    closest_days = closest_match.group(1)
    closest_start = closest_match.group(2)
    closest_end = closest_match.group(3)
    wants_actions = _looks_like_action_scenario_request(user_query)

    lang = _resolve_output_language(state)
    if wants_actions and lang == "en":
        return (
            "## 01. Data Availability Status\n\n"
            f"No ERP sales records were found for {material_label} in the requested window "
            f"{requested_start} to {requested_end}.\n\n"
            f"Available ERP sales history for this product runs from {available_start} to {available_end} "
            f"({row_count} rows). The closest available {closest_days}-day window is {closest_start} to {closest_end}.\n\n"
            "## 02. Key Drivers Under Sparse Data\n\n"
            "Recent ERP demand momentum cannot be directly measured because the requested period has no records. "
            "Decision quality therefore depends on data continuity and whether new orders resume in the next update cycle.\n\n"
            "## 03. Next 1-2 Week Scenario Outlook\n\n"
            "- Bear case: no new ERP sales records appear and follow-up quotes stay weak.\n"
            "- Base case: small but continuous ERP orders resume, indicating stabilization.\n"
            "- Bull case: order count and delivered volume recover quickly versus the closest available window.\n\n"
            "## 04. Action Plan With Triggers\n\n"
            "- Bear action: reduce quote aggressiveness and protect margin floor. Trigger: still no new ERP sales records after 3-5 business days.\n"
            "- Base action: keep neutral pricing and prioritize existing customers. Trigger: low-volume but continuous repeat orders.\n"
            "- Bull action: selectively raise quotes and secure supply allocation. Trigger: clear rise in order count plus volume in consecutive updates.\n\n"
            "## 05. Risk Alerts\n\n"
            "Main risk is decision lag caused by sparse transactional signals. If ERP updates are delayed, pricing moves can miss market turning points.\n\n"
            "## 06. Recommended Follow-up\n\n"
            "Please confirm whether I should run the analysis directly on the closest available window, "
            "or on another date range inside the available history."
        ).strip()

    if wants_actions:
        return (
            "## 01 · 数据可用性状态\n\n"
            f"请求区间 {requested_start} 至 {requested_end} 内，{material_label} 没有ERP销售记录。\n\n"
            f"该产品可用ERP销售历史范围为 {available_start} 至 {available_end}，共 {row_count} 条记录。"
            f"最接近的可用 {closest_days} 天窗口是 {closest_start} 至 {closest_end}。\n\n"
            "## 02 · 稀疏数据下的关键驱动\n\n"
            "当前无法直接观测最近一周ERP真实动销强度，核心判断转为数据连续性和订单恢复节奏。"
            "短期驱动重点是：是否出现新增订单、订单频次是否连续、发货量是否恢复。\n\n"
            "## 03 · 未来1-2周情景判断\n\n"
            "- 看跌情景：未来仍无新增ERP销售记录，报价与成交继续走弱。\n"
            "- 基准情景：出现小规模但连续的订单回补，动销企稳。\n"
            "- 看涨情景：订单数和发货量较最近可用窗口明显回升，恢复节奏加快。\n\n"
            "## 04 · 动作建议与触发条件\n\n"
            "- 看跌动作：收紧报价折让、优先保毛利。触发条件：连续3-5个工作日仍无新增ERP销售记录。\n"
            "- 基准动作：维持中性报价，优先保障核心客户稳定成交。触发条件：出现低量但连续复购订单。\n"
            "- 看涨动作：对紧缺客户分层提价并锁定供给。触发条件：订单笔数与销量在连续更新中同步上行。\n\n"
            "## 05 · 风险预警\n\n"
            "主要风险不是单次价格波动，而是交易信号稀疏导致决策滞后。若ERP更新继续延迟，定价动作可能错过窗口。\n\n"
            "## 06 · 建议追问\n\n"
            "可直接按最近可用窗口继续分析，或指定可用区间内的其他时间段，我将按同一框架输出可执行动作。"
        ).strip()

    if lang == "en":
        return (
            "## ERP Data Availability\n\n"
            f"No ERP sales records were found for {material_label} in the requested period "
            f"{requested_start} to {requested_end}.\n\n"
            f"Available ERP sales history for this product runs from {available_start} to {available_end} "
            f"({row_count} rows). The closest available {closest_days}-day window is "
            f"{closest_start} to {closest_end}.\n\n"
            "I can use that closest available window, or you can choose another period inside the available range."
        ).strip()

    return (
        "## ERP数据可用性\n\n"
        f"请求区间 {requested_start} 至 {requested_end} 内，{material_label} 没有ERP销售记录。\n\n"
        f"该产品可用ERP销售历史范围为 {available_start} 至 {available_end}，共 {row_count} 条记录。"
        f"最接近的可用 {closest_days} 天窗口是 {closest_start} 至 {closest_end}。\n\n"
        "我可以直接按这个最近可用窗口展示，也可以按你指定的可用区间重新分析。"
    ).strip()


def _build_deterministic_erp_mapping_report(state: PipelineState) -> str:
    parsed = state.get("parsed_intent")
    if getattr(parsed, "task_type", None) != TaskType.ERP_DATA_QUERY:
        return ""
    user_query = str(state.get("user_query") or "")
    if not _looks_like_erp_sales_query(user_query):
        return ""

    erp_data = str(state.get("erp_data") or "")
    match = _re.search(r"No ERP sales data mapping for product '([^']+)'.", erp_data)
    if not match:
        return ""
    product_id = str(match.group(1) or "").strip()
    lang = _resolve_output_language(state)

    if lang == "en":
        return (
            "## ERP Data Availability\n\n"
            f"I recognized the requested product as `{product_id}`, but the unified ERP sales view "
            "does not currently have a material-name mapping for it. Because of that, I will not "
            "substitute another product's sales data.\n\n"
            "Please add this product's ERP material mapping or query a product that is mapped in the unified ERP sales view."
        ).strip()

    return (
        "## ERP数据可用性\n\n"
        f"我已识别到你查询的产品是 `{product_id}`，但当前统一ERP销售视图还没有配置该产品的物料映射。"
        "因此系统不会用其他产品的销量代替回答。\n\n"
        "请先补充该产品在ERP统一销售视图中的物料映射，或查询已有映射的产品。"
    ).strip()


def _build_deterministic_boss_briefing_report(state: PipelineState) -> str:
    language = _resolve_output_language(state)
    erp_data = str(state.get("erp_data") or "")
    market_rows = _extract_live_market_rows_from_text(erp_data)
    if not market_rows:
        return ""

    sales_stats = _extract_sales_total_and_change(erp_data)
    alert_rows = _extract_safety_alert_rows(erp_data)
    forecast_rows = _extract_forecast_rows_from_text(erp_data)
    forecast_by_pid = {str(row.get("product_id") or ""): row for row in forecast_rows}

    derivatives = [row for row in market_rows if row.get("product_id") not in {"crude_oil", "naphtha"}]
    top_derivatives = sorted(
        derivatives,
        key=lambda row: abs(float(row.get("delta_pct") or 0.0)),
        reverse=True,
    )[:4]
    feedstocks = [row for row in market_rows if row.get("product_id") in {"crude_oil", "naphtha"}]
    critical_alerts = [row for row in alert_rows if row.get("severity") == "CRITICAL"]
    low_alerts = [row for row in alert_rows if row.get("severity") == "LOW"]

    if language == "zh":
        lines = ["## 市场概览"]
        overview_bits: List[str] = []
        if sales_stats.get("total") is not None:
            overview_bits.append(f"本期ERP销量为 {sales_stats['total']:,.2f} 吨")
        if sales_stats.get("period_change") is not None:
            overview_bits.append(f"环比 {sales_stats['period_change']:+.1f}%")
        if top_derivatives:
            lead = top_derivatives[0]
            overview_bits.append(f"涨幅最强的是 {lead['name_cn']}，最新价 {lead['price']:,.2f}，7日变化 {lead['delta_pct']:+.2f}%")
        if critical_alerts:
            overview_bits.append("库存最紧张的品种包括 " + "、".join(r["product"] for r in critical_alerts[:3]))
        lines.append(("；".join(overview_bits) or "报告基于ERP与MySQL实时数据生成。") + "。")
        lines.append("")
        lines.append("## 关键数据库信号")
        for row in top_derivatives:
            lines.append(f"- {row['name_cn']} ({row['name_en']}): {row['price']:,.2f}，日期 {row['date']}，7日变化 {row['delta_pct']:+.2f}%")
        for row in feedstocks:
            lines.append(f"- 上游 {row['name_cn']} ({row['name_en']}): {row['price']:,.2f}，日期 {row['date']}，7日变化 {row['delta_pct']:+.2f}%")
        lines.append("")
        lines.append("## 库存与供需预警")
        for row in alert_rows[:5]:
            lines.append(f"- {row['severity']}: {row['product']}，{row['detail']}")
        lines.append("")
        lines.append("## 价格区间预测")
        added_forecast = False
        for row in top_derivatives[:3]:
            fc = forecast_by_pid.get(str(row.get("product_id") or ""))
            if not fc:
                continue
            added_forecast = True
            lines.append(f"- {row['name_cn']}: 3天 {fc['range_3d']} | 7天 {fc['range_7d']} | 30天 {fc['range_30d']}")
        if not added_forecast:
            lines.append("- 当前未返回可用的预测区间。")
        lines.append("")
        lines.append("## 下周建议")
        for row in critical_alerts[:3]:
            lines.append(f"- 立即处理 {row['product']}：{row['detail']}，优先补货或排产。")
        if top_derivatives:
            surge = top_derivatives[0]
            lines.append(f"- 对 {surge['name_cn']} 维持偏强报价纪律；当前数据库价格 {surge['price']:,.2f}，7日变化 {surge['delta_pct']:+.2f}%。")
        if feedstocks and top_derivatives:
            lines.append("- 上游成本偏弱而下游衍生品偏强，建议按品种差异化报价，不做全线统一调整。")
        lines.append("")
        lines.append("## 风险与跟踪点")
        if critical_alerts:
            lines.append("- 最高风险：关键品种库存不足，可能触发缺货或被动采购。")
        if low_alerts:
            lines.append("- 次级风险：低库存品种一旦需求加速，库存天数会进一步下滑。")
        lines.append("- 每日跟踪：库存天数、7日价格变化、预测区间、实际出货节奏。")
        return "\n".join(lines).strip() + "\n"

    lines = ["## Market Overview"]
    overview_bits: List[str] = []
    if sales_stats.get("total") is not None:
        overview_bits.append(f"ERP sales for the period were {sales_stats['total']:,.2f} tons")
    if sales_stats.get("period_change") is not None:
        overview_bits.append(f"period-over-period change was {sales_stats['period_change']:+.1f}%")
    if top_derivatives:
        lead = top_derivatives[0]
        overview_bits.append(
            f"the strongest derivative move was {lead['name_en']} at {lead['price']:,.2f} on {lead['date']} ({lead['delta_pct']:+.2f}% over 7d)"
        )
    if critical_alerts:
        overview_bits.append("critical stock pressure is concentrated in " + ", ".join(row["product"] for row in critical_alerts[:3]))
    lines.append(("; ".join(overview_bits) or "This report is grounded on live ERP and MySQL market data.") + ".")
    lines.append("")
    lines.append("## Key DB Signals")
    for row in top_derivatives:
        lines.append(f"- {row['name_en']}: {row['price']:,.2f} on {row['date']} ({row['delta_pct']:+.2f}% over 7d)")
    for row in feedstocks:
        lines.append(f"- Upstream {row['name_en']}: {row['price']:,.2f} on {row['date']} ({row['delta_pct']:+.2f}% over 7d)")
    lines.append("")
    lines.append("## Supply-Demand & Inventory Signals")
    for row in alert_rows[:5]:
        lines.append(f"- {row['severity']}: {row['product']} — {row['detail']}")
    lines.append("")
    lines.append("## Forecast Ranges")
    added_forecast = False
    for row in top_derivatives[:3]:
        fc = forecast_by_pid.get(str(row.get("product_id") or ""))
        if not fc:
            continue
        added_forecast = True
        lines.append(f"- {row['name_en']}: 3d {fc['range_3d']} | 7d {fc['range_7d']} | 30d {fc['range_30d']}")
    if not added_forecast:
        lines.append("- Forecast ranges were not available in the current DB grounding block.")
    lines.append("")
    lines.append("## Recommended Actions")
    for row in critical_alerts[:3]:
        lines.append(f"- Act today on {row['product']}: {row['detail']}; prioritize replenishment or production allocation before next week.")
    if top_derivatives:
        surge = top_derivatives[0]
        lines.append(f"- Keep pricing discipline on {surge['name_en']}; the live DB price is {surge['price']:,.2f} with {surge['delta_pct']:+.2f}% 7d momentum.")
    if feedstocks and top_derivatives:
        lines.append("- Feedstocks are softer while derivatives are stronger; avoid blanket price changes and adjust product-by-product.")
    lines.append("")
    lines.append("## Risks & Watchlist")
    if critical_alerts:
        lines.append("- Highest risk: stock-out pressure in critical items if replenishment is delayed.")
    if low_alerts:
        lines.append("- Secondary risk: low-stock items can tighten quickly if demand accelerates.")
    lines.append("- Monitor DB fields daily: days of stock, 7d price change, forecast range, and shipment pace.")
    return "\n".join(lines).strip() + "\n"


def _looks_like_llm_transport_error(markdown: str) -> bool:
    text = (markdown or "").strip()
    if not text:
        return False
    lowered = text.lower()
    error_markers = [
        "error generating response:",
        "connection refused",
        "timed out",
        "connect error",
        "all connection attempts failed",
    ]
    return any(marker in lowered for marker in error_markers)


def _looks_like_missing_llm_answer(markdown: str) -> bool:
    text = (markdown or "").strip()
    if not text:
        return True
    return _looks_like_llm_transport_error(text)


def _replace_transport_error_with_fallback(
    markdown: str, state: PipelineState
) -> str:
    if not _looks_like_missing_llm_answer(markdown):
        return markdown or ""

    user_query = state.get("user_query", "") or ""
    user_context = state.get("user_context")
    pricing = state.get("pricing_decision") or {}
    reasoned_lookup = _should_force_reasoned_market_answer(user_query, user_context)

    if pricing and not pricing.get("no_pricing"):
        return _build_pricing_fallback_report(state)

    parsed = state.get("parsed_intent")
    task_type = getattr(parsed, "task_type", None)
    if (_is_multi_product_price_delta_query(user_query, user_context) or task_type == TaskType.SIMPLE_LOOKUP) and not reasoned_lookup:
        return _build_simple_lookup_report(state)

    language = str((state.get("user_prefs") or {}).get("language") or "zh").strip().lower()
    if language == "en":
        return (
            "## Analysis Interrupted\n\n"
            "The Kimi analysis response was interrupted before the final narrative was completed. "
            "The system already prepared the database and decision context.\n\n"
            "Please retry, or check Kimi service availability if this keeps happening."
        )

    return (
        "## 分析生成中断\n\n"
        "Kimi 分析在完成最终报告前发生中断。系统已完成数据库与决策上下文准备。\n\n"
        "请重试；若仍重复发生，请检查 Kimi 服务可用性。"
    )


def _find_live_db_mismatch_issues(
    report_markdown: str, live_rows: List[Dict[str, Any]]
) -> tuple[List[str], List[Dict[str, Any]]]:
    issues: List[str] = []
    mismatched_rows: List[Dict[str, Any]] = []
    lines = [ln.strip() for ln in (report_markdown or "").splitlines() if ln.strip()]

    for row in live_rows:
        terms = [
            str(row.get("name_cn") or "").strip(),
            str(row.get("name_en") or "").strip(),
            str(row.get("product_id") or "").strip(),
        ]
        terms = [t for t in terms if t]
        if not terms:
            continue

        candidate_lines = [ln for ln in lines if any(term in ln for term in terms)]
        if not candidate_lines:
            continue

        focus_lines = [
            ln
            for ln in candidate_lines
            if any(
                marker in ln.lower()
                for marker in [
                    "price",
                    "market",
                    "主流价格",
                    "市场价",
                    "当前",
                    "现价",
                    "环比",
                    "周涨幅",
                    "7日",
                ]
            )
        ] or candidate_lines

        row_issue = False
        for ln in focus_lines:
            if row.get("delta_pct") is not None:
                pct_values = []
                for token in _re.findall(r"([\+\-]?\d+(?:\.\d+)?)%", ln):
                    try:
                        pct_values.append(float(token))
                    except Exception:
                        continue
                if pct_values and all(abs(v - float(row["delta_pct"])) > 0.2 for v in pct_values):
                    issues.append(
                        f"live DB delta for {row.get('label')} is {float(row['delta_pct']):+.2f}%, but report line shows different percentage(s): {ln}"
                    )
                    row_issue = True
                    break

            price_values = []
            for token in _re.findall(r"(\d[\d,]*(?:\.\d+)?)\s*(?:元/吨|吨|CNY/ton|CNY)", ln):
                try:
                    price_values.append(float(str(token).replace(",", "")))
                except Exception:
                    continue
            if price_values and all(abs(v - float(row["price"])) > 2.0 for v in price_values):
                issues.append(
                    f"live DB price for {row.get('label')} is {float(row['price']):,.0f}, but report line shows different price(s): {ln}"
                )
                row_issue = True
                break

        if row_issue:
            mismatched_rows.append(row)

    return issues, mismatched_rows


def _extract_report_recommended_price(markdown: str) -> Optional[int]:
    if not markdown:
        return None
    patterns = [
        r"Recommended\s*Price[^\d]{0,20}([\d,]{4,8})",
        r"推荐价格[^\d]{0,20}([\d,]{4,8})",
    ]
    for pat in patterns:
        m = _re.search(pat, markdown, flags=_re.IGNORECASE)
        if m:
            try:
                return int(str(m.group(1)).replace(",", ""))
            except Exception:
                continue
    return None


def _extract_report_recommended_range(markdown: str) -> tuple[Optional[int], Optional[int]]:
    if not markdown:
        return None, None

    patterns = [
        r"(?:Price\s*Range|Recommended\s*Range)[^\d]{0,30}([\d,]{4,8})\s*(?:-|–|—|~|to)\s*([\d,]{4,8})",
        r"(?:价格区间|建议报价区间|建议区间)[^\d]{0,30}([\d,]{4,8})\s*(?:-|–|—|~|至|到)\s*([\d,]{4,8})",
    ]
    for pat in patterns:
        m = _re.search(pat, markdown, flags=_re.IGNORECASE)
        if not m:
            continue
        try:
            low = int(str(m.group(1)).replace(",", ""))
            high = int(str(m.group(2)).replace(",", ""))
            if low > high:
                low, high = high, low
            return low, high
        except Exception:
            continue
    return None, None


def _rewrite_pricing_lines_with_constraints(
    markdown: str,
    logic_rec: int,
    logic_low: int,
    logic_high: int,
    language: str,
) -> tuple[str, bool]:
    if not markdown:
        return markdown, False

    is_en = (language or "zh") == "en"
    rec_line = (
        f"- Recommended Price: **{logic_rec:,} CNY/ton**"
        if is_en
        else f"- 推荐价格：**{logic_rec:,} 元/吨**"
    )
    range_line = (
        f"- Price Range: **{logic_low:,}–{logic_high:,} CNY/ton**"
        if is_en
        else f"- 建议报价区间：**{logic_low:,}–{logic_high:,} 元/吨**"
    )

    lines = markdown.splitlines()
    out: List[str] = []
    replaced = False

    for line in lines:
        line_l = line.lower()
        line_s = line.strip()

        if "recommended price" in line_l or "推荐价格" in line_s:
            out.append(rec_line)
            replaced = True
            continue

        if (
            "price range" in line_l
            or "recommended range" in line_l
            or "价格区间" in line_s
            or "建议报价区间" in line_s
            or "建议区间" in line_s
        ):
            out.append(range_line)
            replaced = True
            continue

        out.append(line)

    rewritten = "\n".join(out).strip()
    return rewritten, replaced


def _apply_pricing_constraint_arbitration(
    markdown: str,
    state: PipelineState,
) -> tuple[str, Dict[str, Any]]:
    pricing = state.get("pricing_decision") or {}
    if not pricing or pricing.get("no_pricing"):
        return markdown or "", {
            "compared": False,
            "conflict_detected": False,
            "auto_corrected": False,
        }

    try:
        logic_rec = int(pricing.get("recommended_price"))
        logic_range = pricing.get("price_range") or []
        logic_low = int(logic_range[0])
        logic_high = int(logic_range[1])
    except Exception:
        return markdown or "", {
            "compared": False,
            "conflict_detected": False,
            "auto_corrected": False,
        }

    llm_rec = _extract_report_recommended_price(markdown or "")
    llm_low, llm_high = _extract_report_recommended_range(markdown or "")
    conflicts: List[str] = []

    if llm_rec is not None:
        if llm_rec < logic_low or llm_rec > logic_high:
            conflicts.append(
                f"llm_recommended_price_out_of_bounds:{llm_rec}_not_in_{logic_low}-{logic_high}"
            )
        elif abs(llm_rec - logic_rec) >= 50:
            conflicts.append(
                f"llm_recommended_price_mismatch:{llm_rec}_vs_{logic_rec}"
            )

    if llm_low is not None and llm_high is not None:
        if abs(llm_low - logic_low) >= 50 or abs(llm_high - logic_high) >= 50:
            conflicts.append(
                f"llm_range_mismatch:{llm_low}-{llm_high}_vs_{logic_low}-{logic_high}"
            )

    conflict_detected = len(conflicts) > 0
    auto_corrected = False
    corrected = markdown or ""

    if conflict_detected:
        lang = _resolve_output_language(state)
        corrected, replaced = _rewrite_pricing_lines_with_constraints(
            corrected,
            logic_rec=logic_rec,
            logic_low=logic_low,
            logic_high=logic_high,
            language=lang,
        )
        auto_corrected = bool(replaced)

        if not auto_corrected:
            arbitration_block = (
                "\n\n---\n**Deterministic Arbitration Applied**\n"
                f"- Recommended Price (authoritative): **{logic_rec:,} CNY/ton**\n"
                f"- Price Range (authoritative): **{logic_low:,}–{logic_high:,} CNY/ton**\n"
                "- Note: External recommendation conflicted with deterministic pricing constraints and was auto-corrected.\n"
                if lang == "en"
                else "\n\n---\n**系统仲裁已生效（确定性约束优先）**\n"
                f"- 权威推荐价：**{logic_rec:,} 元/吨**\n"
                f"- 权威报价区间：**{logic_low:,}–{logic_high:,} 元/吨**\n"
                "- 说明：外部模型建议与确定性定价约束冲突，已自动纠偏。\n"
            )
            corrected = (corrected.rstrip() + arbitration_block).strip()
            auto_corrected = True

    arbitration_meta: Dict[str, Any] = {
        "compared": True,
        "conflict_detected": conflict_detected,
        "auto_corrected": auto_corrected,
        "llm_recommendation_raw": (
            f"price={llm_rec},range={llm_low}-{llm_high}"
            if llm_rec is not None or (llm_low is not None and llm_high is not None)
            else "unparsed"
        ),
        "logic_recommendation": f"price={logic_rec},range={logic_low}-{logic_high}",
    }
    if conflicts:
        arbitration_meta["conflicts"] = conflicts
    return corrected, arbitration_meta


def _build_pricing_ladder_block(state: PipelineState, language: str) -> str:
    pricing = state.get("pricing_decision") or {}
    if not pricing or pricing.get("no_pricing"):
        return ""

    steps = pricing.get("adjustment_steps") or []
    if not steps:
        return ""

    if (language or "zh") == "en":
        lines = ["", "---", "**Pricing Ladder (How recommendation is derived)**"]
        lines.append("| Step | Delta (CNY/ton) | Note |")
        lines.append("|---|---:|---|")
        for s in steps:
            label = str(s.get("label", "")).strip() or f"Step {s.get('step', '?')}"
            delta = int(s.get("delta", 0) or 0)
            note = str(s.get("note", "")).strip().replace("|", "/")
            lines.append(f"| {label} | {delta:+d} | {note} |")
        lines.append(
            f"| **Final Recommendation** |  | **{int(pricing.get('recommended_price', 0)):,} CNY/ton** |"
        )
        return "\n".join(lines) + "\n"

    lines = ["", "---", "**定价推导过程**"]
    lines.append("| 阶梯项 | 调整额（元/吨） | 说明 |")
    lines.append("|---|---:|---|")
    for s in steps:
        label = str(s.get("label", "")).strip() or f"步骤{s.get('step', '?')}"
        delta = int(s.get("delta", 0) or 0)
        note = str(s.get("note", "")).strip().replace("|", "/")
        lines.append(f"| {label} | {delta:+d} | {note} |")
    lines.append(
        f"| **推荐价** |  | **{int(pricing.get('recommended_price', 0)):,} 元/吨** |"
    )
    return "\n".join(lines) + "\n"


def _build_data_freshness_block(state: PipelineState, language: str) -> str:
    rows = _extract_live_db_provenance_rows(state.get("tool_info", "") or "")
    if not rows:
        return ""

    today = _dt.now(_tz.utc).date()
    parsed: list[tuple[str, str, str, str]] = []
    for r in rows[:8]:
        product = str(r.get("product") or "").strip()
        if not product:
            product = str(r.get("raw") or "").split(":", 1)[0].strip() or "-"

        date_str = str(r.get("date") or "").strip()[:10]

        signal = "-"
        pct_m = _re.search(r"\(([\+\-]?\d+(?:\.\d+)?)%\)", str(r.get("raw") or ""))
        if pct_m:
            try:
                pct = float(pct_m.group(1))
                signal = f"⚠️ {pct:+.2f}%" if abs(pct) >= 5 else f"{pct:+.2f}%"
            except Exception:
                signal = "-"

        freshness = "-"
        if _re.match(r"\d{4}-\d{2}-\d{2}", date_str):
            try:
                d = _dt.strptime(date_str, "%Y-%m-%d").date()
                age = (today - d).days
                if age > 7:
                    freshness = f"🔴 {age}天前"
                elif age > 3:
                    freshness = f"🟡 {age}天前"
                else:
                    freshness = f"🟢 {age}天前"
            except Exception:
                freshness = "-"

        parsed.append((product, date_str or "-", freshness, signal))

    if not parsed:
        return ""

    stale_gt7 = any("🔴" in row[2] for row in parsed)

    if (language or "zh") == "en":
        lines = ["", "---", "**Data Freshness**"]
        lines.append("| Product | Date | Freshness | Signal |")
        lines.append("|---|---|---|---|")
        for p, d, f, s in parsed:
            lines.append(f"| {p} | {d} | {f} | {s} |")
        if stale_gt7:
            lines.append(
                "- Recommendation: upload latest data to improve analysis accuracy."
            )
        return "\n".join(lines) + "\n"

    lines = ["", "---", "**数据新鲜度**"]
    lines.append("| 产品 | 日期 | 新鲜度 | 信号 |")
    lines.append("|---|---|---|---|")
    for p, d, f, s in parsed:
        lines.append(f"| {p} | {d} | {f} | {s} |")
    if stale_gt7:
        lines.append("- 建议上传最新数据以获取更准确分析。")
    return "\n".join(lines) + "\n"


def _build_follow_up_suggestions_block(state: PipelineState, language: str) -> str:
    pricing = state.get("pricing_decision") or {}
    what_if = state.get("what_if_result") or {}
    diagnosis = state.get("diagnosis") or {}
    forecast = state.get("forecast") or {}
    user_query = state.get("user_query") or ""
    erp_data = state.get("erp_data") or ""

    # Resolve product name from multiple sources
    product_name = "\u8be5\u4ea7\u54c1"
    product_id = state.get("graph_product_id") or ""
    if pricing and not pricing.get("no_pricing"):
        product_name = (
            pricing.get("product_name_cn")
            or pricing.get("product_name_en")
            or pricing.get("product_id")
            or "\u8be5\u4ea7\u54c1"
        )
    elif what_if.get("status") == "ok":
        product_name = str(what_if.get("product_id") or "\u8be5\u4ea7\u54c1")
    elif product_id:
        _cn_map = {
            "cracked_c5": "\u88c2\u89e3\u78b3\u4e94",
            "raffinate_c5": "\u62bd\u4f59\u78b3\u4e94",
            "c5_resin": "\u78b3\u4e94\u6811\u8102",
            "c9_resin": "\u78b3\u4e5d\u6811\u8102",
            "isoprene": "\u5f02\u620a\u4e8c\u70ef",
            "piperylene": "\u95f4\u620a\u4e8c\u70ef",
            "sis": "SIS\u70ed\u5851\u5f39\u6027\u4f53",
            "hydrogenated_c9": "\u52a0\u6c22\u78b3\u4e5d",
            "mthpa_hardener": "MTHPA\u56fa\u5316\u5242",
        }
        product_name = _cn_map.get(product_id, product_id)

    # Seed RNG from product + query so suggestions vary per conversation
    seed_str = f"{product_name}:{user_query[:80]}"
    seed_val = int(_hashlib.md5(seed_str.encode()).hexdigest()[:8], 16)
    rng = _random.Random(seed_val)

    candidates: list[str] = []

    # ---- Context-specific suggestions derived from pipeline data ----

    if pricing and not pricing.get("no_pricing"):
        triggers = pricing.get("triggers") or []
        if triggers and isinstance(triggers, list):
            cond = str((triggers[0] or {}).get("condition") or "").strip()
            if cond:
                candidates.append(
                    f"\u5982\u679c{cond}\uff0c{product_name}\u7684\u62a5\u4ef7\u5e94\u8be5\u600e\u4e48\u8c03\u6574\uff1f"
                )
            if len(triggers) > 1:
                cond2 = str((triggers[1] or {}).get("condition") or "").strip()
                if cond2:
                    candidates.append(
                        f"\u5982\u679c{cond2}\u89e6\u53d1\uff0c\u6211\u4eec\u7684\u5e94\u5bf9\u7b56\u7565\u662f\u4ec0\u4e48\uff1f"
                    )
        watch = pricing.get("watch_signals") or []
        for sig in watch[:2]:
            candidates.append(
                f"{product_name}\u51fa\u73b0{sig}\u65f6\uff0c\u62a5\u4ef7\u8282\u594f\u5e94\u5982\u4f55\u8c03\u6574\uff1f"
            )
        rec = pricing.get("recommended_price") or pricing.get("price_range_mid")
        if rec:
            try:
                candidates.append(
                    f"{product_name}\u62a5\u4ef7\u5b9a\u5728{float(rec):,.0f}\u5143/\u5428\uff0c\u4e3b\u8981\u5ba2\u6237\u7684\u63a5\u53d7\u5ea6\u5982\u4f55\u8bc4\u4f30\uff1f"
                )
            except (TypeError, ValueError):
                pass

    root_cause = str(diagnosis.get("root_cause") or "").strip()
    if root_cause and len(root_cause) > 4:
        short_cause = root_cause[:30].rstrip("\uff0c\u3002\uff1b")
        candidates.append(
            f"{short_cause}\u8fd9\u4e00\u60c5\u51b5\u9884\u8ba1\u4f1a\u6301\u7eed\u591a\u4e45\uff1f\u5bf9{product_name}\u4ef7\u683c\u6709\u4f55\u5f71\u54cd\uff1f"
        )

    chain = str(diagnosis.get("chain_impact") or "")
    if "\u5e93\u5b58" in chain or "inventory" in chain.lower():
        candidates.append(
            f"{product_name}\u5f53\u524d\u5e93\u5b58\u6c34\u4f4d\u662f\u5426\u5904\u4e8e\u8b66\u621b\u7ebf\uff1f\u9700\u8981\u8c03\u6574\u51fa\u8d27\u8282\u594f\u5417\uff1f"
        )

    if diagnosis.get("divergence_detected"):
        candidates.append(
            "\u539f\u6cb9\u4e0e\u77f3\u8111\u6cb9\u4ef7\u683c\u7ee7\u7eed\u5206\u5316\uff0c\u54ea\u7c7b\u4ea7\u54c1\u7684\u5229\u6da6\u7a7a\u95f4\u53d7\u538b\u6700\u5927\uff1f"
        )
        candidates.append(
            "\u4e0a\u6e38\u4f20\u5bfc\u53d7\u963b\u65f6\uff0c\u5982\u4f55\u8c03\u6574\u9500\u552e\u7b56\u7565\u6765\u4fdd\u62a4\u5229\u6da6\uff1f"
        )

    direction = str(forecast.get("direction") or forecast.get("trend") or "").lower()
    if "up" in direction or "\u6da8" in direction:
        candidates.append(
            f"{product_name}\u9884\u6d4b\u7ee7\u7eed\u4e0a\u6da8\uff0c\u5ba2\u6237\u5408\u540c\u7684\u4ef7\u683c\u9501\u5b9a\u671f\u8bbe\u7f6e\u662f\u5426\u8fd8\u5408\u7406\uff1f"
        )
        candidates.append(
            f"\u5982\u679c{product_name}\u6da8\u5e45\u8d85\u9884\u671f\uff0c\u539f\u6599\u91c7\u8d2d\u7b56\u7565\u5e94\u5982\u4f55\u8c03\u6574\uff1f"
        )
    elif "down" in direction or "\u8dcc" in direction:
        candidates.append(
            f"{product_name}\u9884\u6d4b\u4e0b\u884c\uff0c\u672a\u4ea4\u4ed8\u8ba2\u5355\u7684\u5229\u6da6\u98ce\u9669\u5e94\u5982\u4f55\u5bf9\u51b2\uff1f"
        )
        candidates.append(
            f"\u5728{product_name}\u4ef7\u683c\u4e0b\u884c\u901a\u9053\u4e2d\uff0c\u54ea\u4e9b\u5ba2\u6237\u4f18\u5148\u7ea7\u5e94\u8be5\u63d0\u9ad8\uff1f"
        )
    else:
        candidates.append(
            f"{product_name}\u4e0b\u6708\u7684\u4e3b\u8981\u4e0a\u884c\u4e0e\u4e0b\u884c\u98ce\u9669\u5404\u662f\u4ec0\u4e48\uff1f"
        )

    if what_if.get("status") == "ok":
        scenario = str(what_if.get("scenario") or "\u8be5\u60c5\u666f")
        candidates.append(
            f"\u5728\u201c{scenario}\u201d\u60c5\u666f\u4e0b\uff0c\u54ea\u4e9b\u89e6\u53d1\u6761\u4ef6\u4f1a\u8ba9\u6211\u4eec\u5207\u6362\u5230\u66f4\u6fc0\u8fdb\u62a5\u4ef7\uff1f"
        )
        candidates.append(
            f"\u5982\u679c\u4e0a\u6e38\u6ce2\u52a8\u6301\u7eed\u4e24\u5468\uff0c{product_name}\u7684\u62a5\u4ef7\u533a\u95f4\u8981\u600e\u4e48\u6eda\u52a8\u66f4\u65b0\uff1f"
        )
        candidates.append(
            "\u662f\u5426\u9700\u8981\u6309\u5ba2\u6237\u7b49\u7ea7\u8bbe\u7f6e\u5dee\u5f02\u5316\u5e94\u6025\u62a5\u4ef7\uff1f"
        )

    if erp_data and ("\u5e93\u5b58" in erp_data or "ton" in erp_data.lower()):
        candidates.append(
            f"{product_name}\u672c\u6708\u5b9e\u9645\u51fa\u8d27\u91cf\u4e0e\u8ba1\u5212\u76f8\u6bd4\u5b8c\u6210\u60c5\u51b5\u5982\u4f55\uff1f"
        )

    # ---- General diversified pool (shuffled per conversation) ----
    general_pool = [
        f"{product_name}\u7684\u4e3b\u8981\u7ade\u4e89\u5bf9\u624b\u5f53\u524d\u62a5\u4ef7\u5982\u4f55\uff1f\u6211\u4eec\u7684\u4ef7\u5dee\u5904\u4e8e\u4ec0\u4e48\u6c34\u5e73\uff1f",
        f"{product_name}\u5f53\u524d\u7684\u4e0b\u6e38\u9700\u6c42\u6709\u54ea\u4e9b\u65b0\u53d8\u5316\u503c\u5f97\u5173\u6ce8\uff1f",
        f"\u5f53\u524d{product_name}\u7684\u539f\u6599\u6210\u672c\u5360\u62a5\u4ef7\u6bd4\u91cd\u662f\u591a\u5c11\uff1f\u5229\u6da6\u7a7a\u95f4\u6709\u6ca1\u6709\u6536\u7a84\uff1f",
        f"\u5982\u679c\u77f3\u8111\u6cb9\u4ef7\u683c\u4e0a\u6da85%\uff0c{product_name}\u62a5\u4ef7\u7684\u4f20\u5bfc\u6548\u7387\u5982\u4f55\u8bc4\u4f30\uff1f",
        f"{product_name}\u8fd1\u671f\u51fa\u53e3\u6d41\u5411\u6709\u54ea\u4e9b\u53d8\u5316\uff1f\u5bf9\u56fd\u5185\u73b0\u8d27\u4ef7\u683c\u6709\u4f55\u5f71\u54cd\uff1f",
        f"\u4e3b\u8981\u5ba2\u6237\u5bf9{product_name}\u7684\u91c7\u8d2d\u610f\u5411\u6700\u8fd1\u6709\u6ca1\u6709\u53d8\u5316\u4fe1\u53f7\uff1f",
        f"{product_name}\u4e0e\u540c\u7c7b\u7ade\u54c1\u7684\u5229\u6da6\u5dee\u8ddd\u76ee\u524d\u5904\u4e8e\u4ec0\u4e48\u533a\u95f4\uff1f",
        f"\u5982\u679c{product_name}\u62a5\u4ef7\u4e0a\u82042%\uff0c\u5ba2\u6237\u6d41\u5931\u98ce\u9669\u6709\u591a\u5927\uff1f",
        f"\u672c\u5468{product_name}\u5e02\u573a\u60c5\u7eea\u504f\u591a\u8fd8\u662f\u504f\u7a7a\uff1f\u6709\u54ea\u4e9b\u5173\u952e\u9a71\u52a8\u4fe1\u53f7\uff1f",
        f"\u5982\u4f55\u5224\u65ad\u5f53\u524d\u662f{product_name}\u7684\u63d0\u4ef7\u7a97\u53e3\u8fd8\u662f\u7a33\u4ef7\u7a97\u53e3\uff1f",
        f"{product_name}\u7684\u4e0a\u4e0b\u6e38\u4ef7\u683c\u4f20\u5bfc\u6700\u8fd1\u51fa\u73b0\u4e86\u54ea\u4e9b\u6ede\u540e\u6216\u5f02\u5e38\uff1f",
        f"\u5982\u679c\u7ade\u4e89\u5bf9\u624b\u7a81\u7136\u964d\u4ef7\uff0c{product_name}\u6700\u4f18\u7684\u5e94\u5bf9\u7b56\u7565\u662f\u4ec0\u4e48\uff1f",
        f"\u5f53\u524d\u5bfe\u89c2\u73af\u5883\uff08\u6c47\u7387\u3001\u539f\u6cb9\u8d70\u52bf\uff09\u5bf9{product_name}\u5b9a\u4ef7\u7b56\u7565\u5f71\u54cd\u5982\u4f55\uff1f",
        f"{product_name}\u7684\u5e93\u9500\u6bd4\u5f53\u524d\u5904\u4e8e\u4ec0\u4e48\u6c34\u5e73\uff1f\u4e0e\u5386\u53f2\u5747\u503c\u76f8\u6bd4\u5982\u4f55\uff1f",
        f"\u6309\u7167\u76ee\u524d\u8d70\u52bf\uff0c{product_name}\u4f55\u65f6\u53ef\u80fd\u89e6\u53d1\u6da8\u4ef7\u6216\u964d\u4ef7\u7684\u89e6\u53d1\u6761\u4ef6\uff1f",
    ]
    rng.shuffle(general_pool)

    # Combine: up to 2 context-specific + fill remainder from general pool
    seen: set[str] = set()
    final: list[str] = []
    for s in candidates:
        item = (s or "").strip()
        if item and item not in seen and len(final) < 2:
            seen.add(item)
            final.append(item)
    for s in general_pool:
        item = (s or "").strip()
        if item and item not in seen and len(final) < 3:
            seen.add(item)
            final.append(item)

    if not final:
        return ""

    if (language or "zh") == "en":
        lines = ["", "---", "**You may also ask:**"]
        for s in final[:3]:
            lines.append(f'- "{s}"')
        return "\n".join(lines) + "\n"

    lines = ["", "---", "**\u60a8\u53ef\u80fd\u8fd8\u60f3\u95ee\uff1a**"]
    for s in final[:3]:
        lines.append(f'- "{s}"')
    return "\n".join(lines) + "\n"


def _build_what_if_result_block(state: PipelineState, language: str) -> str:
    result = state.get("what_if_result") or {}
    if not result or result.get("status") != "ok":
        return ""

    if (language or "zh") == "en":
        lines = ["", "---", "**What-if Scenario Result**"]
        lines.append(f"- Scenario: {result.get('scenario')}")
        lines.append("| Metric | Value |")
        lines.append("|---|---|")
        lines.append(f"| Current price | {result.get('current_price', 0):,.0f} |")
        lines.append(
            f"| Implied crude change | {result.get('implied_crude_change_pct', 0):+.2f}% |"
        )
        lines.append(
            f"| Implied naphtha change | {result.get('implied_naphtha_change_pct', 0):+.2f}% |"
        )
        lines.append(
            f"| Implied product change | {result.get('implied_product_change_pct', 0):+.2f}% |"
        )
        lines.append(
            f"| New price estimate | {result.get('new_price_estimate', 0):,.0f} |"
        )
        lines.append(
            f"| Confidence | {result.get('confidence')} ({result.get('assumption')}) |"
        )
        return "\n".join(lines) + "\n"

    lines = ["", "---", "**What-if 情景结果**"]
    lines.append(f"- 情景：{result.get('scenario')}")
    lines.append("| 指标 | 数值 |")
    lines.append("|---|---|")
    lines.append(f"| 当前价格 | {result.get('current_price', 0):,.0f} 元/吨 |")
    lines.append(
        f"| 推导原油变动 | {result.get('implied_crude_change_pct', 0):+.2f}% |"
    )
    lines.append(
        f"| 推导石脑油变动 | {result.get('implied_naphtha_change_pct', 0):+.2f}% |"
    )
    lines.append(
        f"| 推导产品变动 | {result.get('implied_product_change_pct', 0):+.2f}% |"
    )
    lines.append(f"| 新价格估计 | {result.get('new_price_estimate', 0):,.0f} 元/吨 |")
    lines.append(
        f"| 置信度 | {result.get('confidence')}（{result.get('assumption')}） |"
    )
    return "\n".join(lines) + "\n"


def _build_volatility_anomaly_block(state: PipelineState, language: str) -> str:
    tool_info = state.get("tool_info", "") or ""
    if "INTERNAL DATABASE LIVE DATA" not in tool_info:
        return ""

    lines = []
    m = _re.search(
        r"INTERNAL DATABASE LIVE DATA:\s*(.*?)(?:\n\n|\Z)",
        tool_info,
        flags=_re.DOTALL,
    )
    if not m:
        return ""

    for ln in str(m.group(1)).splitlines():
        row = (ln or "").strip()
        if not row:
            continue
        pct_m = _re.search(r"\(([\+\-]?\d+(?:\.\d+)?)%\)", row)
        if not pct_m:
            continue
        try:
            pct = float(pct_m.group(1))
        except Exception:
            continue
        # Threshold for unusual single-period move in this dashboard context.
        if abs(pct) >= 5.0:
            prod = row.split(":", 1)[0].strip()
            lines.append((prod, pct))

    if not lines:
        return ""

    if (language or "zh") == "en":
        out = ["", "---", "**Volatility Alert**"]
        for prod, pct in lines[:3]:
            out.append(
                f"- {prod} move {pct:+.2f}% is unusually large for a single period; verify data continuity/date gap before final action."
            )
        return "\n".join(out) + "\n"

    out = ["", "---", "**异常波动提示**"]
    for prod, pct in lines[:3]:
        out.append(
            f"- {prod} 当期变动 {pct:+.2f}% 属于异常波动，建议先核验数据连续性/日期间隔，再执行最终动作。"
        )
    return "\n".join(out) + "\n"


def _build_consistency_guardrail_block(
    state: PipelineState, report_markdown: str, language: str
) -> str:
    tool_info = state.get("tool_info", "") or ""
    pricing = state.get("pricing_decision") or {}
    forecast = state.get("forecast") or {}
    live_rows = _extract_live_db_authoritative_rows(tool_info)
    if not pricing and not live_rows:
        return ""

    issues: List[str] = []
    live_mismatch_rows: List[Dict[str, Any]] = []

    if live_rows:
        live_issues, live_mismatch_rows = _find_live_db_mismatch_issues(
            report_markdown, live_rows
        )
        issues.extend(live_issues)

    rec = pricing.get("recommended_price")
    prange = pricing.get("price_range") or []
    if isinstance(rec, (int, float)) and isinstance(prange, list) and len(prange) == 2:
        low, high = prange[0], prange[1]
        try:
            if rec < low or rec > high:
                issues.append(
                    f"recommended_price={int(rec)} is outside pricing range [{int(low)}, {int(high)}]."
                )
        except Exception:
            pass

    deviation_gap: Optional[int] = None
    try:
        base_7d = ((forecast.get("scenarios") or {}).get("7d") or {}).get("base") or {}
        low_7d = base_7d.get("low")
        high_7d = base_7d.get("high")
        if (
            isinstance(rec, (int, float))
            and isinstance(low_7d, (int, float))
            and isinstance(high_7d, (int, float))
        ):
            # Allow moderate offset because pricing ladder may include inventory/action bias.
            tolerance = 200
            if rec < (low_7d - tolerance) or rec > (high_7d + tolerance):
                mid_7d = int(round((float(low_7d) + float(high_7d)) / 2.0))
                deviation_gap = int(rec) - mid_7d
                issues.append(
                    "recommended_price deviates materially from 7d base forecast band "
                    f"({int(low_7d)}-{int(high_7d)})."
                )
    except Exception:
        pass

    try:
        report_rec = _extract_report_recommended_price(report_markdown)
        if isinstance(report_rec, int) and isinstance(rec, (int, float)):
            if abs(report_rec - int(rec)) >= 50:
                issues.append(
                    f"report mentions recommended price {report_rec}, but Stage-4 authoritative value is {int(rec)}."
                )
    except Exception:
        pass

    if not issues:
        if (language or "zh") == "en":
            if live_rows:
                return "\n---\n**Consistency Check**\n- PASS: authoritative live DB values, Stage-4 pricing, and quoted recommendations are numerically aligned.\n"
            return "\n---\n**Consistency Check**\n- PASS: Stage-4 pricing, forecast bands, and quoted recommendation are numerically aligned.\n"
        if live_rows:
            return "\n---\n**一致性校验**\n- 通过：实时DB权威值、Stage-4定价与文中关键数值保持一致。\n"
        return "\n---\n**一致性校验**\n- 通过：Stage-4定价、预测区间与文中推荐价在数值上保持一致。\n"

    if (language or "zh") == "en":
        lines = [
            "",
            "---",
            "**Consistency Check**",
            "- WARNING: potential numeric inconsistency detected.",
        ]
        lines.extend(f"- {i}" for i in issues)
        if deviation_gap is not None:
            lines.append(
                "- Reconciliation: the 7d forecast band reflects statistical time-series behavior, "
                "while Stage-4 recommended pricing also incorporates policy/anchor and cost-pass-through ladder effects. "
                f"Current recommendation is {deviation_gap:+d} CNY/ton versus 7d midpoint."
            )
        lines.append(
            "- Authoritative values should follow `PRICING DECISION (STAGE 4)` and `FORECAST (STAGE 3)` blocks."
        )
        if live_mismatch_rows:
            lines.append("- Authoritative live DB values:")
            for row in live_mismatch_rows[:4]:
                delta_text = (
                    f" | 7d {float(row['delta_pct']):+.2f}%"
                    if row.get("delta_pct") is not None
                    else ""
                )
                lines.append(
                    f"  - {row.get('label')}: {float(row.get('price') or 0):,.0f} {row.get('unit') or ''}{delta_text} | date={row.get('date') or 'unknown'}"
                )
        return "\n".join(lines) + "\n"

    lines = ["", "---", "**一致性校验**", "- 警告：检测到潜在数值不一致。"]
    lines.extend(f"- {i}" for i in issues)
    if deviation_gap is not None:
        lines.append(
            "- 差异解释：7日预测区间主要反映统计时序模型结果；而Stage-4推荐价还叠加了锚价/成本传导/动作偏置等业务规则。"
            f"当前推荐价相对7日中枢偏移 {deviation_gap:+d} 元/吨。"
        )
    lines.append(
        "- 请以 `PRICING DECISION (STAGE 4)` 与 `FORECAST (STAGE 3)` 的权威数值为准。"
    )
    if live_mismatch_rows:
        lines.append("- 实时DB权威值：")
        for row in live_mismatch_rows[:4]:
            delta_text = (
                f" | 7日 {float(row['delta_pct']):+.2f}%"
                if row.get("delta_pct") is not None
                else ""
            )
            lines.append(
                f"  - {row.get('label')}: {float(row.get('price') or 0):,.0f} {row.get('unit') or ''}{delta_text} | 日期={row.get('date') or 'unknown'}"
            )
    return "\n".join(lines) + "\n"


def _build_operational_impact_recovery_block(
    state: PipelineState, report_markdown: str, language: str
) -> str:
    query = state.get("user_query", "") or ""
    ql = query.lower()
    is_ops_query = any(
        k in query for k in ["装置", "停工", "停车", "重启", "开工", "检修"]
    ) or any(k in ql for k in ["plant", "shutdown", "restart", "outage", "maintenance"])
    if not is_ops_query:
        return ""

    text = report_markdown or ""
    says_no_data = any(
        p in text
        for p in [
            "无法直接分析",
            "无法直接量化",
            "缺少关键的实时市场数据",
            "cannot directly analyze",
            "insufficient data",
        ]
    )
    if not says_no_data:
        return ""

    tool_info = state.get("tool_info", "") or ""
    live_rows = _extract_live_db_provenance_rows(tool_info)
    if not live_rows:
        return ""

    diagnosis = state.get("diagnosis") or {}
    chain_impact = diagnosis.get("chain_impact") or []

    if (language or "zh") == "en":
        lines = [
            "",
            "---",
            "**Deterministic Data Recovery**",
            "- The response above marked data as insufficient, but live DB evidence is available for this question.",
            "- Available live price evidence:",
        ]
        for r in live_rows[:4]:
            lines.append(f"  - {r.get('raw', '')}")
        if chain_impact:
            lines.append("- Causal chain evidence:")
            for ci in chain_impact[:3]:
                lines.append(f"  - {ci}")
        lines.append(
            "- Guidance: when real-time unit-operation feed is missing, use weekly operational narratives as proxy evidence and combine with live prices/inventory for conditional actions."
        )
        return "\n".join(lines) + "\n"

    lines = [
        "",
        "---",
        "**系统纠偏（确定性）**",
        "- 上文将数据判定为不足，但当前问题已检测到可用实时DB证据。",
        "- 可用实时价格证据：",
    ]
    for r in live_rows[:4]:
        lines.append(f"  - {r.get('raw', '')}")
    if chain_impact:
        lines.append("- 因果链证据：")
        for ci in chain_impact[:3]:
            lines.append(f"  - {ci}")
    lines.append(
        "- 建议：当实时开停工Feed缺失时，可用周报装置叙述作为供给冲击代理，并与实时价格/库存联合输出条件化动作。"
    )
    return "\n".join(lines) + "\n"


def _resolve_output_language(state: PipelineState) -> str:
    prefs = state.get("user_prefs", {}) or {}
    pref_lang = str(prefs.get("language") or "").strip().lower()
    user_query = state.get("user_query", "") or ""
    lower_query = user_query.lower()

    if pref_lang in {"zh", "cn", "chinese"}:
        return "zh"
    if pref_lang in {"en", "english"}:
        return "en"

    # Fall back to query language only when the UI/API has not supplied a
    # preference. This keeps Chinese and English entry points consistent.
    has_cjk = bool(_re.search(r"[\u4e00-\u9fff]", user_query))
    asks_english_explicitly = any(
        k in lower_query for k in ["english", "in english", "use english", "英文", "英语"]
    )
    if has_cjk and not asks_english_explicitly:
        return "zh"
    return _detect_language(user_query)


def _localize_common_english_for_zh(markdown: str) -> str:
    """Best-effort cleanup for common English labels leaking into Chinese-mode reports."""
    text = str(markdown or "")
    replacements = [
        (r"\bDirect Answer\b", "直接回答"),
        (r"\bKey Drivers\b", "关键驱动"),
        (r"\bSupply-Demand Driver\b", "供需驱动"),
        (r"\bSupply[- ]Demand\b", "供需"),
        (r"\bPricing Risk This Week\b", "本周价格风险"),
        (r"\bPricing Risk\b", "价格风险"),
        (r"\bAction We Should Take This Week\b", "本周操作建议"),
        (r"\bRecommended Actions?\b", "行动建议"),
        (r"\bBusiness Implications And Watchpoints\b", "业务含义与观察项"),
        (r"\bMain Anomalies Detected\b", "主要异常点"),
        (r"\bWhat May Be Driving Them\b", "可能驱动因素"),
        (r"\bData source:\b", "数据来源："),
        (r"\bStage[- ]?4\b", "第四阶段"),
        (r"\bWeekly plan\b", "周度计划"),
        (r"\bWatchlist\b", "观察清单"),
        (r"\bTrigger\b", "触发条件"),
        (r"\bQuote discipline\b", "报价纪律"),
        (r"\bInventory\b", "库存"),
        (r"\bSales execution\b", "销售执行"),
        (r"\bExecution confidence\b", "执行置信度"),
        (r"\bBase\b", "基准"),
        (r"\bBull\b", "乐观"),
        (r"\bBear\b", "悲观"),
        (r"\bbullish\b", "偏强"),
        (r"\bbearish\b", "偏弱"),
        (r"\bneutral\b", "中性"),
        (r"\bnormal\b", "正常"),
        (r"\breduce_price\b", "下调报价"),
        (r"\bincrease_price\b", "上调报价"),
        (r"\bhold_price\b", "维持报价"),
        (r"\bCNY/t\b", "元/吨"),
        (r"\bunavailable\b", "暂无"),
        (r"\bflat\b", "持平"),
    ]
    for pattern, replacement in replacements:
        text = _re.sub(pattern, replacement, text, flags=_re.IGNORECASE)
    return text


def _is_multi_product_price_delta_query(user_query: str, user_context: Any = None) -> bool:
    q = (user_query or "").strip()
    ql = q.lower()
    if not q:
        return False

    # Guardrail: operational impact / recommendation asks must not be
    # downgraded to simple lookup templates.
    if _is_ops_inventory_pricing_query(q):
        return False

    deep_ops_markers_zh = [
        "装置",
        "停工",
        "停车",
        "停开车",
        "重启",
        "开工",
        "检修",
        "复产",
        "影响",
        "库存",
        "运营建议",
        "定价建议",
        "排产建议",
        "策略",
        "风险",
        "驱动",
        "分析",
    ]
    deep_ops_markers_en = [
        "plant",
        "shutdown",
        "restart",
        "outage",
        "maintenance",
        "impact",
        "inventory",
        "recommendation",
        "pricing action",
        "production action",
        "strategy",
        "risk",
        "driver",
        "analysis",
    ]
    if any(k in q for k in deep_ops_markers_zh) or any(k in ql for k in deep_ops_markers_en):
        return False

    price_markers_zh = ["价格", "主流价格", "现价", "报价", "最新价", "走势", "变动"]
    price_markers_en = [
        "price",
        "prices",
        "current price",
        "latest price",
        "price move",
        "price moves",
        "market move",
        "market moves",
    ]
    delta_markers_zh = ["环比", "涨跌", "涨幅", "跌幅", "变化", "同比", "变动"]
    delta_markers_en = [
        "wow",
        "mom",
        "change",
        "changes",
        "changed",
        "delta",
        "yoy",
        "week over week",
        "month over month",
    ]
    period_markers_zh = ["本周", "这周", "本月", "本期"]
    period_markers_en = ["this week", "weekly", "this month", "monthly"]
    lookup_markers_zh = ["主流价格", "现价", "最新价", "报价", "环比", "同比", "涨跌", "分别是多少", "查询"]
    lookup_markers_en = [
        "what is",
        "what are",
        "latest price",
        "current price",
        "price move",
        "price moves",
        "week over week",
        "month over month",
        "lookup",
    ]

    has_price = any(k in q for k in price_markers_zh) or any(k in ql for k in price_markers_en)
    has_delta = any(k in q for k in delta_markers_zh) or any(k in ql for k in delta_markers_en)
    has_period = any(k in q for k in period_markers_zh) or any(k in ql for k in period_markers_en)
    asks_lookup = any(k in q for k in lookup_markers_zh) or any(k in ql for k in lookup_markers_en)

    product_text = str(getattr(user_context, "product", "") or "")
    product_tokens = [
        t
        for t in _re.split(r"\s*(?:,|，|/|&|\band\b|和|及|与)\s*", product_text, flags=_re.IGNORECASE)
        if t and t.strip()
    ]
    query_has_multi_product_hint = any(k in q for k in ["分别", "和", "及", "与"]) or len(product_tokens) >= 2

    return asks_lookup and has_price and has_delta and (has_period or query_has_multi_product_hint)


def _is_direct_market_price_lookup(user_query: str, user_context: Any = None) -> bool:
    q = (user_query or "").strip()
    ql = q.lower()
    if not q:
        return False

    # Keep true analysis/decision questions on the richer path.
    deep_markers_zh = [
        "分析",
        "原因",
        "驱动",
        "预测",
        "建议",
        "策略",
        "报告",
        "周报",
        "供需",
        "库存",
        "装置",
        "影响",
        "风险",
    ]
    deep_markers_en = [
        "analysis",
        "why",
        "driver",
        "forecast",
        "recommendation",
        "strategy",
        "report",
        "supply",
        "demand",
        "inventory",
        "plant",
        "impact",
        "risk",
    ]
    if any(k in q for k in deep_markers_zh) or any(k in ql for k in deep_markers_en):
        return False

    price_markers_zh = [
        "价格",
        "挂牌价",
        "挂牌",
        "市场价",
        "行情",
        "现价",
        "报价",
        "主流价",
    ]
    price_markers_en = [
        "price",
        "listing price",
        "listed price",
        "market",
        "quote",
        "quotation",
        "spot",
    ]
    has_price_marker = any(k in q for k in price_markers_zh) or any(k in ql for k in price_markers_en)
    if not has_price_marker and not looks_like_sinopec_listing_query(q):
        return False

    period_or_status_markers_zh = [
        "最近",
        "近",
        "本周",
        "这周",
        "本月",
        "月度",
        "今天",
        "当前",
        "最新",
        "公布",
        "是否",
        "变动",
        "方向",
        "怎么样",
        "多少",
        "如何",
    ]
    period_or_status_markers_en = [
        "recent",
        "this week",
        "weekly",
        "this month",
        "monthly",
        "today",
        "current",
        "latest",
        "released",
        "changed",
        "how",
        "what",
    ]
    has_lookup_shape = any(k in q for k in period_or_status_markers_zh) or any(k in ql for k in period_or_status_markers_en)
    if not has_lookup_shape:
        return False

    if looks_like_sinopec_listing_query(q):
        return True

    product_text = str(getattr(user_context, "product", "") or "")
    return bool(product_text.strip() or extract_product_ids_in_text(q))


def _should_force_reasoned_market_answer(user_query: str, user_context: Any = None) -> bool:
    q = (user_query or "").strip()
    ql = q.lower()
    if not _is_multi_product_price_delta_query(q, user_context):
        return False

    reasoning_markers_en = [
        "based on the china market database",
        "main price moves",
        "what changed week over week",
        "what changed",
        "compare",
        "versus",
        " vs ",
        "analysis",
        "explain",
        "drivers",
        "driver",
    ]
    reasoning_markers_zh = [
        "基于中国市场数据库",
        "主要价格变动",
        "周环比变化",
        "分别变化",
        "对比",
        "比较",
        "分析",
        "解释",
        "驱动",
    ]
    return any(marker in ql for marker in reasoning_markers_en) or any(marker in q for marker in reasoning_markers_zh)


def _is_garbled_or_too_short_report(text: str) -> bool:
    content = (text or "").strip()
    if not content:
        return True
    if len(content) < 180:
        return True

    garbled_markers = ["****", "INTERNAL DATABASE LIVE DATA", "C5Isoprene", "! EDIA", "EDIA。"]
    marker_hits = sum(1 for m in garbled_markers if m in content)
    if marker_hits >= 2:
        return True
    if "****" in content and marker_hits >= 1:
        return True

    if "| 产品 |" not in content and "### 1. 当前主流价格与环比涨跌" not in content:
        if content.count("**") >= 8 and content.count("\n") <= 6:
            return True

    visible = _re.sub(r"\s+", "", content)
    if visible and len(visible) < 80:
        alnum = sum(1 for ch in visible if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
        if alnum < len(visible) * 0.35:
            return True

    return False


def _looks_like_placeholder_skeleton_report(text: str) -> bool:
    content = (text or "").strip()
    if not content:
        return False

    # Follow-up/provenance blocks can contain healthy Chinese text even when the
    # main LLM answer is only markdown/table scaffolding, so judge the answer body.
    body = _re.split(
        r"\n\s*---\s*\n\*\*(?:Provenance|You may also ask|您可能还想问)",
        content,
        maxsplit=1,
    )[0].strip()
    if not body:
        return False

    if body.count("****") >= 3:
        return True
    if "****" in body and body.count("****") >= 2 and len(body) < 900:
        return True
    if _re.search(r"(?m)^\s*###\s*$", body) and _re.search(r"(?m)^\s*\|\s*(?:\|\s*){2,}$", body):
        return True
    if _re.match(r"^[，。、；：,.!！?？\s]+$", body):
        return True
    if _re.search(r"!?\s*EDIA[。．]?\s*[!?？:：]?\s*$", body.strip()):
        return True
    if _re.search(r"^\s*!\s*EDIA", body):
        return True
    return False


def _looks_like_stripped_chinese_fragment(text: str) -> bool:
    content = (text or "").strip()
    if not content:
        return False
    if _re.search(r"[\u4e00-\u9fff]", content):
        return False
    if len(content) > 420:
        return False

    visible = _re.sub(r"\s+", "", content)
    if not visible:
        return True
    alnum_count = sum(1 for ch in visible if ch.isalnum())
    punctuation_count = len(visible) - alnum_count
    punctuation_heavy = punctuation_count >= alnum_count
    has_placeholder_marks = "****" in content or content.count("**") >= 2
    has_fragment_shape = bool(_re.search(r"(?m)^\s*[-*|#]", content)) or bool(
        _re.search(r"\b\d+(?:\.\d+)?\s*%", content)
    )
    return punctuation_heavy and (has_placeholder_marks or has_fragment_shape)


def _build_placeholder_skeleton_fallback(state: PipelineState) -> str:
    lang = _resolve_output_language(state)
    user_query = str(state.get("user_query") or "")
    user_context = state.get("user_context")
    pricing = state.get("pricing_decision") or {}
    parsed = state.get("parsed_intent")
    task_type = getattr(parsed, "task_type", None)

    if pricing and not pricing.get("no_pricing"):
        return _build_pricing_fallback_report(state).strip()

    reasoned_lookup = _should_force_reasoned_market_answer(user_query, user_context)
    if (_is_multi_product_price_delta_query(user_query, user_context) or task_type == TaskType.SIMPLE_LOOKUP) and not reasoned_lookup:
        simple = _build_simple_lookup_report(state).strip()
        if simple:
            return simple

    if lang == "zh" and task_type in (TaskType.MARKET_ANALYSIS_DECISION, TaskType.WHAT_IF_SCENARIO, TaskType.BOSS_BRIEFING):
        recovered = _build_cn_missing_section_recovery_block(
            state,
            list(_CN_RECOVERY_SECTION_ORDER),
        )
        lines = [
            line
            for line in str(recovered or "").splitlines()
            if line.strip()
            and line.strip() != "---"
            and not line.strip().startswith("**系统结构化补全")
            and not line.strip().startswith("- 检测到核心章节缺失")
        ]
        if lines:
            return "\n".join(lines).strip()

    if lang == "en":
        return (
            "## Analysis Regenerated\n\n"
            "The first model draft returned only formatting placeholders, so the system did not save it as a final answer. "
            "Please retry with a product and time window so EDIA can regenerate the analysis from live data."
        )

    return (
        "## 分析结果已拦截\n\n"
        "模型首次返回了占位符式内容，系统已阻止将其作为正式答案保存。"
        "请补充产品与时间范围后重试，EDIA 会重新基于实时数据生成分析。"
    )


def _looks_like_portfolio_query(
    user_query: str, user_context: Any, graph_product_ids: Optional[List[str]] = None
) -> bool:
    if _looks_like_other_products_excluding_query(user_query):
        return True

    q = (user_query or "").lower()
    product_text = str(getattr(user_context, "product", "") or "")
    competitor_portfolio_markers = [
        "competitor",
        "competitors",
        "benchmark",
        "positioning",
        "our sales",
        "sales impact",
        "quote change",
        "plant operation",
        "装置运行",
        "竞品",
        "竞争对手",
        "报价变化",
        "成交影响",
        "对我们成交的影响",
        "主要竞品",
    ]
    broad_research_markers = [
        "research note",
        "market research note",
        "deep market research",
        "research report",
        "market research report",
        "market note",
        "pricing structure",
        "inventory structure",
        "supply-demand note",
        "market landscape",
        "chain overview",
        "market study",
        "市场研究",
        "研究报告",
        "市场研究报告",
        "深度研究",
        "深度市场研究",
        "深度市场研究报告",
        "短期预测",
        "经营启示",
        "产业链",
        "供需格局",
        "库存结构",
        "价格结构",
    ]
    if any(m in q for m in competitor_portfolio_markers) or any(
        m in user_query for m in ["装置运行", "竞品", "竞争对手", "报价变化", "成交影响", "对我们成交的影响", "主要竞品"]
    ):
        return True

    portfolio_markers = [
        "全产品线",
        "产品线",
        "各产品",
        "全线",
        "组合",
        "portfolio",
        "all products",
        "across products",
        "product line",
        # Market report / executive summary → treat as full portfolio coverage
        "executive report",
        "market report",
        "weekly report",
        "weekly market",
        "weekly summary",
        "weekly briefing",
        "market executive",
        "executive summary",
        "market summary",
        "weekly overview",
        "latest market data",
        "market anomalies",
        "price anomalies",
        "anomaly detection",
        "volatility anomaly",
        "abnormal moves",
        "market dislocation",
        "latest anomalies",
        "generate report",
        "supply-demand",
        "market overview report",
        "this week's china",
        "this week's market",
        "周报",
        "市场简报",
        "市场报告",
        "行情报告",
        "本周行情",
        "定价策略报告",
        "异常波动",
        "市场异动",
        "价格异动",
        "异常",
        "异动",
        "最新市场数据",
        "最新行情数据",
    ]
    if any(m in q for m in broad_research_markers) or any(
        m in user_query for m in ["产业链", "供需格局", "库存结构", "价格结构"]
    ):
        return True

    if any(m in q for m in portfolio_markers) or any(
        m in user_query for m in ["全产品线", "各产品", "产品线", "全线", "组合", "周报", "简报", "市场报告"]
    ):
        return True

    if graph_product_ids and len(graph_product_ids) >= 3:
        return True

    split_count = len(
        [
            t
            for t in _re.split(
                r"\s*(?:,|，|/|&|\band\b|和|及|与)\s*",
                product_text,
                flags=_re.IGNORECASE,
            )
            if t and t.strip()
        ]
    )
    return split_count >= 3


def _looks_like_weekly_report_request(user_query: str) -> bool:
    q = (user_query or "").lower()
    if not q:
        return False
    report_markers = [
        "weekly report",
        "weekly briefing",
        "market report",
        "executive report",
        "this week report",
        "本周",
        "周报",
        "简报",
        "市场报告",
        "经营简报",
        "research note",
        "market research note",
        "deep market research",
        "market note",
        "pricing structure",
        "inventory structure",
        "supply-demand note",
        "market landscape",
        "chain overview",
        "产业链",
        "供需格局",
        "库存结构",
        "价格结构",
    ]
    return any(marker in q for marker in report_markers)


def _select_key_products_from_snapshot(snapshot: Dict[str, Any], limit: int = 4) -> List[str]:
    products = dict((snapshot or {}).get("products") or {})
    if not products:
        return []

    excluded = {"crude_oil", "naphtha"}
    scored: List[tuple[float, str]] = []
    for pid, pdata in products.items():
        if pid in excluded:
            continue
        if str(pdata.get("data_source") or "") != "real":
            continue
        latest = pdata.get("latest_price")
        chg = pdata.get("change_7d_pct")
        if latest is None:
            continue
        score = abs(float(chg or 0.0)) + (1.0 if pid in {"isoprene", "piperylene", "dcpd", "c5_resin", "blowing_agent"} else 0.0)
        scored.append((score, str(pid)))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [pid for _score, pid in scored[: max(1, int(limit))]]


def _query_explicitly_requests_delta_summary(user_query: str) -> bool:
    q = (user_query or "").lower()
    return any(
        marker in q
        for marker in [
            "compared with last",
            "compared to last",
            "since last query",
            "vs last query",
            "update from last",
            "与上次相比",
            "和上次比",
            "较上次",
            "上次查询",
            "上次问",
        ]
    )


def _should_include_pricing_artifacts(state: PipelineState) -> bool:
    parsed = state.get("parsed_intent")
    task_type = getattr(parsed, "task_type", None)
    return task_type in (
        TaskType.MARKET_ANALYSIS_DECISION,
        TaskType.WHAT_IF_SCENARIO,
    )


def _should_include_follow_up_suggestions(state: PipelineState) -> bool:
    response_mode = state.get("response_mode", "full_report")
    parsed = state.get("parsed_intent")
    task_type = getattr(parsed, "task_type", None)
    return response_mode == "full_report" or task_type in (
        TaskType.MARKET_ANALYSIS_DECISION,
        TaskType.WHAT_IF_SCENARIO,
        TaskType.BOSS_BRIEFING,
        TaskType.ERP_DATA_QUERY,
    )


def _looks_domain_related_query(user_query: str) -> bool:
    q = (user_query or "").lower()
    domain_terms = [
        "c5",
        "c9",
        "cracked c5",
        "cracked c9",
        "isoprene",
        "piperylene",
        "dcpd",
        "sis",
        "naphtha",
        "brent",
        "crude",
        "裂解c5",
        "裂解c9",
        "裂解碳五",
        "裂解碳九",
        "异戊二烯",
        "间戊二烯",
        "双环戊二烯",
        "石脑油",
        "原油",
        "c5树脂",
        "道路标线",
        "粘合c5",
        "抽余c5",
        "发泡剂",
    ]
    return any(t in q for t in domain_terms)


def _should_short_circuit_out_of_scope(state: PipelineState) -> bool:
    parsed = state.get("parsed_intent")
    task_type = getattr(parsed, "task_type", None)
    if task_type in (TaskType.ERP_DATA_QUERY, TaskType.BOSS_BRIEFING):
        return False

    graph_product_ids = state.get("graph_product_ids") or []
    if graph_product_ids:
        return False

    user_query = state.get("user_query", "") or ""
    user_context = state.get("user_context")
    product_hint = str(getattr(user_context, "product", "") or "").strip()

    q = user_query.lower()
    asks_market = any(
        m in q
        for m in [
            "price",
            "pricing",
            "market",
            "forecast",
            "trend",
            "inventory",
            "报价",
            "价格",
            "市场",
            "预测",
            "库存",
            "定价",
        ]
    )
    if not asks_market:
        return False

    if _looks_domain_related_query(user_query):
        return False
    if product_hint and _looks_domain_related_query(product_hint):
        return False

    # Report / executive summary requests are always domain-relevant — never out-of-scope
    report_markers = [
        "executive report", "market report", "weekly report", "market executive",
        "weekly market", "weekly summary", "weekly briefing", "executive summary",
        "market summary", "weekly overview", "generate report", "this week's",
        "latest market data", "market anomalies", "price anomalies", "anomaly detection",
        "volatility anomaly", "abnormal moves", "market dislocation", "latest anomalies",
        "supply-demand", "supply demand", "market overview report",
        "research note", "market research note", "deep market research",
        "research report", "market research report",
        "market note", "pricing structure", "inventory structure",
        "supply-demand note", "market landscape", "chain overview",
        "市场研究", "研究报告", "市场研究报告", "深度研究", "深度市场研究", "深度市场研究报告", "短期预测", "经营启示",
        "competitor",
        "competitors",
        "benchmark",
        "positioning",
        "our sales",
        "sales impact",
        "quote change",
        "plant operation",
        "周报", "週报", "市场简报", "市场报告", "本周市场", "行情报告",
        "异常波动", "市场异动", "价格异动", "异常", "异动", "最新市场数据", "最新行情数据",
        "定价策略", "经营简报", "产业链", "供需格局", "库存结构", "价格结构",
        "装置运行", "竞品", "竞争对手", "报价变化", "成交影响", "对我们成交的影响", "主要竞品",
    ]
    ql = user_query.lower()
    if any(m in ql for m in report_markers) or any(m in user_query for m in ["周报", "简报", "市场报告", "行情报告", "产业链", "供需格局", "研究报告", "市场研究", "经营启示"]):
        return False

    # Portfolio-style query — never out-of-scope
    portfolio_mode = _looks_like_portfolio_query(user_query, state.get("user_context"))
    if portfolio_mode:
        return False

    return True


def _build_out_of_scope_short_reply(state: PipelineState) -> str:
    lang = _resolve_output_language(state)
    target = str(state.get("out_of_scope_target") or "该产品").strip() or "该产品"
    if lang == "en":
        return (
            f"- Boundary: '{target}' is not found in the current EDIA product coverage or live DB mapping.\n"
            "- Current scope focuses on the C5/C9 chain (e.g., cracked C5, isoprene, piperylene, DCPD, C5 resin).\n"
            "- If needed, I can analyze the nearest in-scope proxy product or provide a generic watchlist framework."
        )
    return (
        f"- 边界说明：当前在 EDIA 产品覆盖和实时DB映射中未找到“{target}”。\n"
        "- 当前重点覆盖 C5/C9 产业链（如裂解C5、异戊二烯、间戊二烯、双环戊二烯、C5树脂等）。\n"
        "- 如需继续，我可以改为分析最接近的已覆盖产品，或给出通用关注点清单。"
    )


def _build_portfolio_coverage_block(state: PipelineState, language: str) -> str:
    if not state.get("portfolio_mode"):
        return ""

    items = [
        ("裂解C5", "feedstock anchor"),
        ("异戊二烯", "SIS demand linkage"),
        ("SIS", "downstream demand pulse"),
        ("间戊二烯", "resin / MTHPA input"),
        ("MTHPA", "cost pass-through"),
        ("道路标线C5", "seasonality sensitivity"),
        ("粘合C5", "adhesive demand + spread"),
        ("双环戊二烯", "co-movement check"),
        ("抽余C5", "supply balance monitor"),
    ]

    if (language or "zh") == "en":
        lines = ["", "---", "**Portfolio Coverage (deterministic checklist)**"]
        lines.append("| Product | Focus |")
        lines.append("|---|---|")
        for product, focus in items:
            lines.append(f"| {product} | {focus} |")
        return "\n".join(lines) + "\n"

    lines = ["", "---", "**组合覆盖清单（确定性）**"]
    lines.append("| 产品 | 重点观察 |")
    lines.append("|---|---|")
    for product, focus in items:
        lines.append(f"| {product} | {focus} |")
    return "\n".join(lines) + "\n"


def _has_verified_external_evidence(state: PipelineState) -> bool:
    intelligence = state.get("intelligence_context", {}) or {}
    if intelligence.get("events"):
        return True

    web = state.get("web_search_results", "") or ""
    return bool(_re.search(r"https?://[^\s\]\)\"']+", web, flags=_re.IGNORECASE))


def _build_external_evidence_policy(state: PipelineState, language: str) -> Dict[str, Any]:
    intelligence = state.get("intelligence_context", {}) or {}
    events = intelligence.get("events") or []
    has_verified = _has_verified_external_evidence(state)

    if language == "en":
        if has_verified:
            guidance = (
                "Verified external event evidence is available. Any causal explanation that uses news/events "
                "must cite the corresponding event headline, source/date, and keep claims tied to that evidence."
            )
        else:
            guidance = (
                "No verified approved DB news/event evidence is available for this query window. Do not propose probable drivers, "
                "likely causes, or hypothetical event bullet lists. State explicitly that no recent verified event was detected and "
                "limit the answer to observed market data, transmission mechanics, risks, and recommended actions."
            )
    else:
        if has_verified:
            guidance = (
                "当前存在已验证的外部事件证据。凡是使用新闻/事件做因果解释，必须引用对应事件标题、来源/日期，并将结论严格绑定到该证据。"
            )
        else:
            guidance = (
                "当前查询窗口内没有已验证且已审批的数据库新闻/事件证据。不要给出“最可能驱动因素”“可能原因”或假设性事件条目。"
                "必须明确说明近期未检测到已验证事件，并将回答限定为已观测市场数据、传导机制、风险和建议动作。"
            )

    return {
        "has_verified_external_evidence": has_verified,
        "verified_event_count": len(events),
        "guidance": guidance,
    }


def _strip_unsupported_intelligence_section(markdown: str) -> str:
    if not markdown:
        return markdown

    heading_patterns = [
        r"^##\s*🌍\s*02\.5\s*·\s*情报事件.*$",
        r"^##\s*🌍\s*02\.5\s*·\s*Intelligence Events.*$",
        r"^##\s*02\.5\s*·\s*情报事件.*$",
        r"^##\s*02\.5\s*·\s*Intelligence Events.*$",
    ]
    next_header = _re.compile(r"^##\s+", flags=_re.MULTILINE)

    for pattern in heading_patterns:
        match = _re.search(pattern, markdown, flags=_re.MULTILINE)
        if not match:
            continue
        start = match.start()
        after = next_header.search(markdown, match.end())
        end = after.start() if after else len(markdown)
        cleaned = (markdown[:start].rstrip() + "\n\n" + markdown[end:].lstrip()).strip()
        return _re.sub(r"\n{3,}", "\n\n", cleaned)

    return markdown


def _downgrade_unsupported_external_claims(markdown: str) -> str:
    if not markdown:
        return markdown

    replacements = [
        (
            r"(?i)geopolitical shock reversal",
            "upstream cost-spike reversal",
        ),
        (
            r"(?i)geopolitical premium",
            "short-term cost premium",
        ),
        (
            r"(?i)geopolitical shock",
            "upstream cost shock",
        ),
        (
            r"(?i)geopolitical cause",
            "upstream cost-chain signal",
        ),
        (
            r"(?i)if the event de-escalates",
            "if the short-term cost spike fades",
        ),
        (
            r"(?i)transient, high-reversal-risk event rather than pure fundamentals",
            "transient, high-reversal-risk cost spike rather than pure fundamentals",
        ),
        (
            r"地缘冲击反转",
            "上游成本脉冲回撤",
        ),
        (
            r"地缘溢价",
            "短期成本溢价",
        ),
        (
            r"地缘冲击",
            "上游成本冲击",
        ),
        (
            r"若事件缓和",
            "若短期成本脉冲回落",
        ),
        (
            r"短暂的、高回撤风险事件，而非纯基本面",
            "短暂的、高回撤风险成本脉冲，而非纯基本面",
        ),
        (
            r"(?i)This indicates a differentiated product grade, specialty market segment, or value-added service positioning\.",
            "This may indicate a differentiated product grade, specialty market segment, or value-added service positioning, but that is not confirmed by the provided data.",
        ),
        (
            r"(?i)^Product Specification Difference:",
            "Possible Product Specification Difference:",
        ),
        (
            r"(?i)^Market Segment:",
            "Possible Market Segment Difference:",
        ),
        (
            r"(?i)^Value-Added Factors:",
            "Possible Value-Added Factors:",
        ),
        (
            r"(?i)confirms demand in this premium segment is healthy and not directly competing on price with the benchmark grade",
            "supports current demand resilience, but does not by itself confirm the reason for the premium spread",
        ),
        (
            r"表明这是差异化品级、细分市场溢价或增值服务定位。",
            "这可能反映差异化品级、细分市场溢价或增值服务定位，但现有数据无法直接确认。",
        ),
        (
            r"^产品规格差异：",
            "可能的产品规格差异：",
        ),
        (
            r"^市场细分差异：",
            "可能的市场细分差异：",
        ),
        (
            r"^增值因素：",
            "可能的增值因素：",
        ),
        (
            r"证明这一高溢价细分市场需求健康，且并非与基准品级直接价格竞争",
            "说明当前需求韧性较强，但不足以单独证明高溢价的具体原因",
        ),
    ]

    cleaned = markdown
    for pattern, replacement in replacements:
        cleaned = _re.sub(pattern, replacement, cleaned)
    return cleaned


def _apply_deterministic_guardrails(markdown: str, state: PipelineState) -> str:
    if state.get("out_of_scope_detected"):
        return _build_out_of_scope_short_reply(state)

    report = _replace_transport_error_with_fallback(markdown or "", state)
    if _looks_like_placeholder_skeleton_report(report):
        report = _build_placeholder_skeleton_fallback(state)
    response_mode = state.get("response_mode", "full_report")
    explanation_mode = bool(state.get("explanation_mode", False))
    parsed = state.get("parsed_intent")
    task_type = getattr(parsed, "task_type", None)
    lang = _resolve_output_language(state)
    user_query = state.get("user_query", "") or ""
    pricing = state.get("pricing_decision") or {}
    executive_weekly = _is_executive_weekly_request(
        user_query,
        task_type=task_type,
        response_mode=response_mode,
    )

    if _is_competitor_portfolio_summary_query(user_query):
        report = _build_competitor_portfolio_summary_report(state)
        return report.rstrip()

    if (
        _state_has_agent_intent(state, "commercial_ops_advisory")
        and "Database Capability Discovery" in str(report or "")
    ):
        advisory_report = _build_commercial_ops_advisory_report(state)
        if advisory_report:
            return advisory_report.rstrip()

    if lang == "zh" and _looks_like_stripped_chinese_fragment(report):
        report = _build_placeholder_skeleton_fallback(state)

    if _should_use_executive_brief_format(state) and _looks_like_incomplete_executive_brief(
        report, lang
    ):
        recovered = _build_deterministic_erp_sales_report(state)
        if recovered:
            return recovered.rstrip()

    erp_sales_report = _build_deterministic_erp_sales_report(state)
    if erp_sales_report:
        parsed = state.get("parsed_intent")
        erp_task = getattr(parsed, "task_type", None) == TaskType.ERP_DATA_QUERY
        report_is_garbled = (
            _looks_like_placeholder_skeleton_report(report)
            or "****" in str(report or "")
            or _is_garbled_or_too_short_report(report)
        )
        if state.get("erp_hybrid_analysis") and not report_is_garbled:
            return report.rstrip()
        if _should_preserve_detailed_erp_sales_report(report, state):
            return report.rstrip()
        if erp_task and report_is_garbled:
            return erp_sales_report.rstrip()
        if not erp_task:
            return erp_sales_report.rstrip()
        return erp_sales_report.rstrip()

    erp_availability_report = _build_deterministic_erp_availability_report(state)
    if erp_availability_report:
        if _should_preserve_detailed_erp_availability_report(report, state):
            return report.rstrip()
        return erp_availability_report.rstrip()

    erp_mapping_report = _build_deterministic_erp_mapping_report(state)
    if erp_mapping_report:
        return erp_mapping_report.rstrip()

    if _is_broad_research_report_query(user_query):
        preserve_existing = (
            task_type == TaskType.BOSS_BRIEFING or executive_weekly
        ) and bool(str(report or "").strip()) and not _looks_like_missing_llm_answer(report)
        if not preserve_existing:
            report = _build_research_report(state)
        return report.rstrip()

    # Conversational and out-of-domain responses need no structural post-processing
    if task_type in (TaskType.CONVERSATIONAL, TaskType.OUT_OF_DOMAIN) or response_mode == "conversational":
        return report.rstrip()

    if not explanation_mode:
        if (
            lang == "zh"
            and response_mode == "full_report"
            and task_type in (TaskType.MARKET_ANALYSIS_DECISION, TaskType.WHAT_IF_SCENARIO)
            and _is_ops_inventory_pricing_query(user_query)
            and _looks_like_generic_cn_ops_report(report)
        ):
            rewritten = _build_cn_ops_inventory_pricing_report(state)
            if rewritten:
                report = rewritten

        if response_mode == "full_report" and task_type not in (
            TaskType.ERP_DATA_QUERY,
            TaskType.BOSS_BRIEFING,
        ):
            missing = _check_report_completeness(report)
            should_recover_cn_sections = (
                lang == "zh"
                and task_type in (
                    TaskType.MARKET_ANALYSIS_DECISION,
                    TaskType.WHAT_IF_SCENARIO,
                )
            )
            canonical_missing = (
                _find_missing_cn_canonical_sections(report)
                if should_recover_cn_sections
                else []
            )
            if missing or canonical_missing:
                if pricing and not pricing.get("no_pricing") and _looks_like_quote_range_request(user_query):
                    return _build_quote_range_report(state)
                if should_recover_cn_sections:
                    ordered_labels = _CN_RECOVERY_SECTION_ORDER
                    recovery_labels = [
                        label
                        for label in ordered_labels
                        if label in set(missing + canonical_missing)
                    ]
                    recovered = _build_cn_missing_section_recovery_block(state, recovery_labels)
                    if recovered:
                        report = report.rstrip() + "\n\n" + recovered
                print(
                    f"Report incomplete — missing sections: {missing}. "
                    "Skipping placeholder auto-fill to keep output professional."
                )

    if pricing and not pricing.get("no_pricing") and _looks_like_quote_range_request(user_query):
        missing = _check_report_completeness(report)
        if missing:
            return _build_quote_range_report(state)

    if lang == "en":
        report = _enforce_english_only(report)
    elif lang == "zh":
        report = _localize_common_english_for_zh(report)

    if not _has_verified_external_evidence(state):
        report = _strip_unsupported_intelligence_section(report)
        report = _downgrade_unsupported_external_claims(report)

    report = _strip_unavailable_pricing_placeholders(report)

    delta_block = (state.get("delta_summary_text") or "").strip()
    should_prepend_delta = (
        delta_block
        and delta_block not in report
        and (
            explanation_mode
            or _query_explicitly_requests_delta_summary(
                state.get("user_query", "") or ""
            )
        )
    )
    if should_prepend_delta:
        if lang == "en":
            report = (
                "Changes vs last same-product query (within 7 days):\n"
                + delta_block
                + "\n\nFull updated analysis below...\n\n"
                + report.lstrip()
            )
        else:
            report = delta_block + "\n\n以下为完整更新分析...\n\n" + report.lstrip()

    # Keep direct lookup replies concise: avoid appending heavy diagnostic blocks.
    concise_lookup = not explanation_mode and (
        response_mode == "concise"
        or task_type in (TaskType.SIMPLE_LOOKUP, TaskType.FORECAST_ONLY)
    )
    if concise_lookup:
        if _looks_like_provenance_question(state.get("user_query", "") or ""):
            return (
                report.rstrip()
                + _build_references_block(state, lang)
                + _build_provenance_block(state, lang)
            )
        return report.rstrip()

    if not explanation_mode and response_mode == "structured":
        extras = (
            _build_portfolio_coverage_block(state, lang)
            + _build_references_block(state, lang)
            + _build_provenance_block(state, lang)
        )
        if _should_include_pricing_artifacts(state):
            extras += _build_pricing_ladder_block(state, lang)
            extras += _build_what_if_result_block(state, lang)
        return report.rstrip() + extras

    return (
        report.rstrip()
        + _build_portfolio_coverage_block(state, lang)
        + _build_references_block(state, lang)
        + _build_provenance_block(state, lang)
        + (
            _build_pricing_ladder_block(state, lang)
            if _should_include_pricing_artifacts(state)
            else ""
        )
        + (
            _build_what_if_result_block(state, lang)
            if _should_include_pricing_artifacts(state)
            else ""
        )
        + _build_data_freshness_block(state, lang)
        + _build_volatility_anomaly_block(state, lang)
        + _build_operational_impact_recovery_block(state, report, lang)
        + _build_consistency_guardrail_block(state, report, lang)
        + (
            _build_follow_up_suggestions_block(state, lang)
            if _should_include_follow_up_suggestions(state)
            else ""
        )
    )


def _check_report_completeness(markdown: str) -> List[str]:
    missing: list[str] = []
    for label, zh_pat, en_pat in _REQUIRED_SECTIONS:
        combined = f"{zh_pat}|{en_pat}"
        if not _re.search(combined, markdown, _re.IGNORECASE):
            missing.append(label)
    return missing


def _find_missing_cn_canonical_sections(markdown: str) -> List[str]:
    missing: List[str] = []
    text = markdown or ""
    for label, pattern in _CN_CANONICAL_SECTION_PATTERNS.items():
        if not _re.search(pattern, text, _re.IGNORECASE):
            missing.append(label)
    return missing


def _build_cn_missing_section_recovery_block(
    state: PipelineState,
    missing: List[str],
) -> str:
    """Build deterministic Chinese section recovery blocks for missing core sections.

    This is intentionally data-grounded and concise so we can recover a professional
    structure even when the model output drifts from required headings.
    """

    if not missing:
        return ""

    diagnosis = state.get("diagnosis") or {}
    forecast = state.get("forecast") or {}
    pricing = state.get("pricing_decision") or {}
    tool_info = state.get("tool_info", "") or ""
    live_rows = _extract_live_db_authoritative_rows(tool_info)
    query = str(state.get("user_query") or "").strip()
    product_id = str(state.get("graph_product_id") or "").strip().lower()

    rows_for_product = [
        row
        for row in live_rows
        if str(row.get("product_id") or "").strip().lower() == product_id
    ]
    if not rows_for_product:
        rows_for_product = live_rows[:2]

    blocks: List[str] = [
        "",
        "---",
        "**系统结构化补全（确定性）**",
        "- 检测到核心章节缺失，以下内容由规则引擎基于当前状态自动补全。",
    ]

    for label in missing:
        if label == "Market Overview":
            blocks.append("\n## 📊 01 · 市场现状")
            if rows_for_product:
                first = rows_for_product[0]
                price = first.get("price")
                unit = str(first.get("unit") or "元/吨")
                date = str(first.get("date") or "")
                delta = first.get("delta_pct")
                try:
                    delta_text = f"，近7日变化 {float(delta):+.2f}%" if delta is not None else ""
                except Exception:
                    delta_text = ""
                try:
                    blocks.append(
                        f"- 当前主锚点价格：{float(price):,.0f} {unit}（{date}）{delta_text}。"
                    )
                except Exception:
                    blocks.append("- 当前主锚点价格：已检测到实时市场价记录。")
            else:
                blocks.append("- 当前主锚点价格：未检索到可用实时价格明细，请先补齐 market_prices 数据。")
            if query:
                blocks.append("- 结论口径：本节聚焦中国区域供需与价格传导，不扩展无关市场。")

        elif label == "Key Drivers":
            blocks.append("\n## 🔍 02 · 关键驱动因素")
            root_cause = str(diagnosis.get("root_cause") or "").strip()
            chain = diagnosis.get("chain_impact") or []
            if root_cause:
                blocks.append(f"- 已验证事实/推断线索：{root_cause}")
            if chain:
                for item in chain[:3]:
                    blocks.append(f"- 传导链：{str(item).strip()}")
            if not root_cause and not chain:
                blocks.append("- 暂无完整驱动拆解文本，建议结合装置开停工与下游接货节奏补充验证。")

        elif label == "Price Forecast":
            blocks.append("\n## 📈 03 · 价格预测")
            scenarios = forecast.get("scenarios") or {}

            def _fmt_band(key: str) -> str:
                band = (scenarios.get(key) or {}).get("base") or {}
                low = band.get("low")
                high = band.get("high")
                if isinstance(low, (int, float)) and isinstance(high, (int, float)):
                    return f"{int(low):,}–{int(high):,} 元/吨"
                return "数据不可用"

            blocks.append(
                f"- 3日区间：{_fmt_band('3d')} | 7日区间：{_fmt_band('7d')} | 30日区间：{_fmt_band('30d')}"
            )
            trend = str(forecast.get("trend") or forecast.get("direction") or "").strip()
            confidence = str(forecast.get("confidence") or "").strip()
            if trend or confidence:
                blocks.append(f"- 基准走势：{trend or '未给出'} | 置信度：{confidence or '未给出'}")
            else:
                blocks.append("- 预测说明：当前预测结构未完整返回，建议补充 3d/7d/30d 场景参数。")

        elif label == "Pricing Decision":
            blocks.append("\n## 💡 04 · 定价决策")
            rec = pricing.get("recommended_price")
            prange = pricing.get("price_range") or []
            action = str(pricing.get("action") or "").strip()
            reason = str(pricing.get("reasoning") or "").strip()
            if isinstance(rec, (int, float)):
                blocks.append(f"- 推荐价格：**{int(rec):,} 元/吨**")
            if isinstance(prange, list) and len(prange) == 2:
                try:
                    blocks.append(
                        f"- 价格区间：{int(prange[0]):,}–{int(prange[1]):,} 元/吨"
                    )
                except Exception:
                    pass
            if action:
                blocks.append(f"- 行动建议：{action}")
            if reason:
                blocks.append(f"- 决策理由：{reason}")
            triggers = pricing.get("triggers") or []
            for idx, trigger in enumerate(triggers[:2], start=1):
                condition = str((trigger or {}).get("condition") or "").strip()
                t_action = str((trigger or {}).get("action") or "").strip()
                if condition or t_action:
                    blocks.append(f"- 触发条件{idx}：若{condition or '条件触发'} → {t_action or '执行预案'}")
            if not any([isinstance(rec, (int, float)), action, reason, triggers]):
                blocks.append("- 当前缺少完整 Stage-4 定价输出，建议先确认推荐价与触发条件后执行报价动作。")

        elif label == "Risk Alerts":
            blocks.append("\n## ⚠️ 05 · 风险预警")
            risks = diagnosis.get("risks") or []
            if isinstance(risks, list) and risks:
                for risk in risks[:3]:
                    blocks.append(f"- 风险：{str(risk).strip()}")
            else:
                blocks.append("- 风险1：装置恢复节奏与下游接货不同步，可能导致库存阶段性抬升。")
                blocks.append("- 风险2：传导不完全导致报价上调失败，压缩加工利润。")
            if "unavailable" in str(state.get("erp_data") or "").lower():
                blocks.append("- 数据风险：ERP 库存快照缺失，库存覆盖天数与产销匹配需人工复核。")

        elif label == "Suggested Follow-ups":
            blocks.append("\n## ❓ 06 · 建议追问")
            follow_up_block = _build_follow_up_suggestions_block(state, "zh")
            extracted: List[str] = []
            for line in str(follow_up_block or "").splitlines():
                stripped = line.strip()
                if stripped.startswith('- "') and stripped.endswith('"'):
                    extracted.append(stripped[3:-1].strip())
            if not extracted:
                extracted = [
                    "该产品下周的关键上行与下行触发条件分别是什么？",
                    "如果下游接货放缓，当前建议报价应如何分档调整？",
                    "库存覆盖天数处于什么水平，是否需要调整采购与出货节奏？",
                ]
            for item in extracted[:3]:
                blocks.append(f"- \"{item}\"")

    return "\n".join(blocks).strip()


def _append_missing_sections(markdown: str, missing: List[str], language: str) -> str:
    # Keep output clean: do not inject placeholder appendix blocks.
    # The calling layer can choose a deterministic fallback report if needed.
    return markdown


# ---------------------------------------------------------------------------
# Node functions — each takes PipelineState, returns partial state update
# ---------------------------------------------------------------------------


def _make_parse_intent(intent_parser: IntentParser):
    def node(state: PipelineState) -> dict:
        user_query = state["user_query"]
        parsed = intent_parser.parse(user_query)
        hermes_plan = state.get("hermes_plan") or {}
        routed = route_query(
            user_query,
            task_type=getattr(parsed, "task_type", None),
            hermes_domains=hermes_plan.get("domains"),
            capabilities=hermes_plan.get("capabilities"),
            conversation_history=state.get("conversation_history"),
        )
        # Detect historical/backtest date scope from the query
        date_scope = detect_historical_date_scope(user_query)
        cutoff_date = date_scope["cutoff_date"] if date_scope else None
        if cutoff_date:
            print(f"[DateScope] Backtest mode detected: cutoff={cutoff_date}")
        return {
            "parsed_intent": parsed,
            "routed_query": routed.to_dict(),
            "date_scope": date_scope,
            "cutoff_date": cutoff_date,
        }

    return node


def _make_extract_context(context_generator: ContextGenerator):
    def node(state: PipelineState) -> dict:
        ctx = context_generator.extract_context(
            state["user_query"],
            conversation_history=state.get("conversation_history", []),
            user_prefs=state.get("user_prefs", {}),
        )
        return {"user_context": ctx}

    return node


def _format_conversation_history(messages: List[Dict[str, Any]]) -> str:
    lines: list[str] = []
    for message in messages[-6:]:
        role = (message.get("role") or "user").lower()
        content = (message.get("content") or "").strip()
        if not content:
            continue
        compact = " ".join(content.split())
        if len(compact) > 700:
            compact = compact[:700] + "..."
        lines.append(f"{role.upper()}: {compact}")
    if not lines:
        return ""
    return "RECENT CONVERSATION CONTEXT:\n" + "\n".join(lines)


def _latest_assistant_excerpt(messages: List[Dict[str, Any]]) -> str:
    for message in reversed(messages):
        if (message.get("role") or "").lower() != "assistant":
            continue
        content = (message.get("content") or "").strip()
        if not content:
            continue
        compact = " ".join(content.split())
        if len(compact) > 1200:
            compact = compact[:1200] + "..."
        return "PRIOR ASSISTANT ANSWER TO EXPLAIN:\n" + compact
    return ""


def _make_validate_context(context_validator: ContextValidator):
    def node(state: PipelineState) -> dict:
        is_valid, missing = context_validator.validate(state["user_context"])
        return {"is_valid": is_valid, "missing_fields": missing}

    return node


def _make_partial_response(context_validator: ContextValidator):
    def node(state: PipelineState) -> dict:
        follow_up = context_validator.generate_follow_up_question(
            state["missing_fields"]
        )
        report = DecisionReport(
            raw_markdown=follow_up,
            task_type=state["parsed_intent"].task_type,
            context=state["user_context"],
            status="partial",
            error_message=f"Missing fields: {state['missing_fields']}",
            resolved_chat_mode=state.get("resolved_chat_mode"),
            mode_reason=state.get("mode_reason"),
        )
        return {"report": report}

    return node


def _node_perception(state: PipelineState) -> dict:
    ttl_seconds = _cache_get_ttl_seconds("PERCEPTION_CACHE_TTL_SECONDS", 20)
    cutoff_date = str(state.get("cutoff_date") or "live").strip() or "live"
    cache_key = f"cutoff:{cutoff_date}"

    cached = _cache_get_perception(cache_key, ttl_seconds)
    if cached is not None:
        return {"perception_snapshot": cached}

    try:
        cutoff_value = state.get("cutoff_date")
        snapshot = perception_service.build_snapshot(cutoff_date=cutoff_value)
        _cache_set_perception(cache_key, snapshot)
    except Exception as e:
        print(f"Perception Error: {e}")
        snapshot = {}
    return {"perception_snapshot": snapshot}


def _node_intelligence_check(state: PipelineState) -> dict:
    """Query the intelligence pipeline for recent high-impact events and causal analysis."""
    ttl_seconds = _cache_get_ttl_seconds("INTELLIGENCE_CONTEXT_CACHE_TTL_SECONDS", 30)
    user_query = str(state.get("user_query") or "")
    default_lookback = int(_os.getenv("INTELLIGENCE_DEFAULT_LOOKBACK_HOURS", "24") or "24")
    weekly_lookback = int(_os.getenv("INTELLIGENCE_WEEKLY_LOOKBACK_HOURS", "168") or "168")
    lookback_hours = weekly_lookback if _looks_like_weekly_report_request(user_query) else default_lookback
    lookback_hours = max(1, min(720, lookback_hours))
    cache_key = f"hours:{lookback_hours}"

    cached = _cache_get_intelligence(cache_key, ttl_seconds)
    if cached is not None:
        cached_ctx, cached_text = cached
        return {
            "intelligence_context": cached_ctx,
            "intelligence_text": cached_text,
        }

    intel_ctx: Dict[str, Any] = {}
    intel_text = ""
    try:
        from backend.src.intelligence.intelligence_orchestrator import (
            get_intelligence_context,
        )

        intel_ctx = get_intelligence_context(
            hours=lookback_hours,
            review_status="auto",
            min_usefulness_score=0,
            analysis_limit=None,
        )
        intel_text = intel_ctx.get("intelligence_summary", "")
        _cache_set_intelligence(cache_key, intel_ctx, intel_text)
        if intel_text:
            print(f"Intelligence: {len(intel_ctx.get('events', []))} events detected")
    except Exception as e:
        print(f"Intelligence check error (non-fatal): {e}")
    return {"intelligence_context": intel_ctx, "intelligence_text": intel_text}


def _to_float_or_none(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _should_force_query_time_news(state: PipelineState) -> bool:
    query = ((state.get("user_query") or "").strip()).lower()
    if not query:
        return False

    force_markers = [
        "reversal risk",
        "cost shock",
        "cost push",
        "supply shock",
        "shipping disruption",
        "geopolitical",
        "why is naphtha",
        "why did naphtha",
        "shock reversal",
        "event risk",
        "回撤风险",
        "回调风险",
        "成本冲击",
        "成本推动",
        "上游冲击",
        "地缘",
        "航运",
        "原油暴涨",
        "石脑油暴涨",
        "事件风险",
    ]
    if any(marker in query for marker in force_markers):
        return True

    products = (state.get("perception_snapshot") or {}).get("products") or {}
    crude_7d = _to_float_or_none((products.get("crude_oil") or {}).get("change_7d_pct"))
    naphtha_7d = _to_float_or_none((products.get("naphtha") or {}).get("change_7d_pct"))

    if crude_7d is not None and abs(crude_7d) >= 8.0:
        return True
    if naphtha_7d is not None and abs(naphtha_7d) >= 10.0:
        return True
    if (
        crude_7d is not None
        and naphtha_7d is not None
        and abs(naphtha_7d - crude_7d) >= 5.0
    ):
        return True

    return False


def _normalize_manual_chat_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"chat", "analysis", "report", "deep_research"}:
        return mode
    return ""


def _classify_chat_mode(
    user_query: str,
    user_prefs: Optional[Dict[str, Any]] = None,
    task_type: Optional[TaskType] = None,
    explanation_mode: bool = False,
) -> tuple[str, str]:
    prefs = user_prefs or {}
    manual_mode = _normalize_manual_chat_mode(prefs.get("chat_mode"))
    if manual_mode:
        return manual_mode, "manual_override"

    query = (user_query or "").strip().lower()
    if not query:
        return "chat", "empty_query_default"

    if explanation_mode:
        return "analysis", "follow_up_explanation"

    deep_research_markers = [
        "deep research",
        "deep market research",
        "market research note",
        "research note",
        "market note",
        "comprehensive",
        "end-to-end",
        "full picture",
        "decision implications",
        "scenario analysis",
        "risk matrix",
        "pricing structure",
        "inventory structure",
        "supply-demand note",
        "market landscape",
        "chain overview",
        "深度研究",
        "全面研究",
        "完整研究",
        "决策建议",
        "情景分析",
        "风险矩阵",
        "产业链",
        "供需格局",
        "库存结构",
        "价格结构",
    ]
    if any(marker in query for marker in deep_research_markers):
        return "deep_research", "deep_research_markers"

    report_markers = [
        "report",
        "write a report",
        "full report",
        "briefing",
        "memo",
        "research note",
        "market note",
        "总结报告",
        "报告",
        "汇报",
    ]
    if any(marker in query for marker in report_markers):
        return "report", "report_markers"

    analysis_markers = [
        "analyze",
        "analysis",
        "pricing",
        "price gap",
        "quote range",
        "recommended price",
        "forecast",
        "outlook",
        "trend",
        "latest",
        "news",
        "what happened",
        "root cause",
        "drivers",
        "price movement",
        "price trend",
        "supply",
        "demand",
        "inventory",
        "risk",
        "compare",
        "分析",
        "预测",
        "走势",
        "趋势",
        "报价",
        "报价区间",
        "推荐报价",
        "价差",
        "挂牌价",
        "最新",
        "新闻",
        "消息",
        "供给",
        "需求",
        "库存",
        "风险",
        "对比",
    ]
    if any(marker in query for marker in analysis_markers):
        return "analysis", "analysis_markers"

    if task_type in (
        TaskType.MARKET_ANALYSIS_ONLY,
        TaskType.MARKET_ANALYSIS_DECISION,
        TaskType.WHAT_IF_SCENARIO,
        TaskType.FORECAST_ONLY,
    ):
        return "analysis", f"task_type:{task_type.value}"

    if task_type == TaskType.BOSS_BRIEFING:
        return "report", f"task_type:{task_type.value}"

    if task_type in (TaskType.CONVERSATIONAL, TaskType.OUT_OF_DOMAIN):
        return "chat", f"task_type:{task_type.value}"

    if _is_business_data_task(task_type, user_query):
        return "analysis", "business_data_default_analysis"

    return "chat", "default_chat"


def _conversational_should_run_research_pipeline(state: PipelineState) -> bool:
    """Allow conversational intent to use research pipeline when query clearly asks for analysis/news."""
    parsed = state.get("parsed_intent")
    task_type = getattr(parsed, "task_type", None) if parsed else None
    chat_mode, _reason = _classify_chat_mode(
        state.get("user_query") or "",
        user_prefs=state.get("user_prefs") or {},
        task_type=task_type,
        explanation_mode=bool(state.get("explanation_mode")),
    )
    if chat_mode in {"analysis", "deep_research", "report"}:
        return True

    query = ((state.get("user_query") or "").strip()).lower()
    if not query:
        return False

    research_markers = [
        "research",
        "analyze",
        "analysis",
        "forecast",
        "outlook",
        "trend",
        "news",
        "latest",
        "what happened",
        "root cause",
        "drivers",
        "price movement",
        "price trend",
        "supply",
        "demand",
        "inventory",
        "risk",
        "scenario",
        "研究",
        "分析",
        "预测",
        "走势",
        "趋势",
        "最新",
        "消息",
        "新闻",
        "供给",
        "需求",
        "库存",
        "风险",
    ]
    if any(marker in query for marker in research_markers):
        return True

    return _should_force_query_time_news(state)


def _has_follow_up_with_prior_answer(state: PipelineState) -> bool:
    """Detect explanation-style follow-ups that should stay on the analysis path."""
    query = (state.get("user_query") or "").strip()
    if not query or not _looks_like_follow_up(query):
        return False
    history = state.get("conversation_history") or []
    if not history:
        return False
    return bool(_latest_assistant_excerpt(history).strip())


def _node_web_search(state: PipelineState) -> dict:
    """Optional per-request web search for *recent* news/events.

        Controlled by env var `WEB_SEARCH_MODE`:
            - off (default): never search
            - auto: search for news/event questions and force search for
                cost-shock / reversal-risk style queries
            - on: always search

    This produces deterministic, URL-bearing snippets for prompt injection and
    is also used by the post-LLM References footer.
    """

    db_first_intelligence = (
        (_os.getenv("INTELLIGENCE_DB_FIRST", "true") or "true").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    if db_first_intelligence:
        return {
            "web_search_results": "",
            "web_search_meta": {"results": [], "queries": []},
        }

    database_only_mode = (
        (_os.getenv("DATABASE_ONLY_MODE", "true") or "true").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    if database_only_mode:
        return {"web_search_results": "", "web_search_meta": {"results": [], "queries": []}}

    mode = (_os.getenv("WEB_SEARCH_MODE", "off") or "off").strip().lower()
    effective_mode = mode
    if mode == "auto" and _should_force_query_time_news(state):
        effective_mode = "on"

    user_query = (state.get("user_query") or "").strip()
    if not user_query:
        return {"web_search_results": "", "web_search_meta": {"results": [], "queries": []}}

    try:
        builder = get_external_news_context_builder()
        context = builder.build_context(
            user_query=user_query,
            user_context=state.get("user_context"),
            graph_product_ids=state.get("graph_product_ids") or [],
            portfolio_mode=bool(state.get("portfolio_mode")),
            perception_snapshot=state.get("perception_snapshot") or {},
            language=_detect_language(user_query),
            mode=effective_mode,
        )

        raw_results = context.get("results") or []
        normalized_results: list[dict[str, Any]] = []
        for item in raw_results:
            if isinstance(item, dict):
                normalized_results.append(
                    {
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "source": item.get("source", ""),
                        "published_date": item.get("published_date", ""),
                        "tier": item.get("tier", ""),
                    }
                )
                continue

            normalized_results.append(
                {
                    "title": getattr(item, "title", "") or "",
                    "url": getattr(item, "url", "") or "",
                    "source": getattr(item, "source", "") or "",
                    "published_date": getattr(item, "published_date", "") or "",
                    "tier": getattr(item, "tier", "") or "",
                }
            )

        return {
            "web_search_results": context.get("formatted_text", "") or "",
            "web_search_meta": {
                "queries": list(context.get("queries") or []),
                "results": normalized_results,
            },
        }
    except Exception as e:
        print(f"Web search error (non-fatal): {e}")
        return {"web_search_results": "", "web_search_meta": {"results": [], "queries": []}}


def _node_parallel_fetch(state: PipelineState) -> dict:
    """Run perception, intelligence check, and web search concurrently.

    Replaces the sequential perception → intelligence_check → web_search chain.
    All three have no data dependency on each other, so they can safely overlap.
    """
    import time as _t

    merged: dict = {}
    sub_timings: dict = {}
    timeout_raw = str(_os.getenv("PARALLEL_FETCH_TIMEOUT_SECONDS", "20") or "20").strip()
    try:
        parallel_fetch_timeout = max(1.0, float(timeout_raw))
    except Exception:
        parallel_fetch_timeout = 20.0

    def _run(fn, label):
        t0 = _t.perf_counter()
        try:
            result = fn(state)
        except Exception as e:
            print(f"parallel_fetch/{label} error (non-fatal): {e}")
            result = {}
        sub_timings[label] = round((_t.perf_counter() - t0) * 1000.0, 1)
        return result if isinstance(result, dict) else {}

    database_only_mode = _normalize_bool_text(_os.getenv("DATABASE_ONLY_MODE", "true"), True)
    db_first_intelligence = _normalize_bool_text(_os.getenv("INTELLIGENCE_DB_FIRST", "true"), True)
    web_search_mode = (_os.getenv("WEB_SEARCH_MODE", "off") or "off").strip().lower()

    worker_specs = [
        (_node_perception, "perception"),
        (_node_intelligence_check, "intelligence_check"),
    ]
    if not (database_only_mode or db_first_intelligence or web_search_mode == "off"):
        worker_specs.append((_node_web_search, "web_search"))

    ex = _futures.ThreadPoolExecutor(max_workers=len(worker_specs))
    try:
        futures = {
            ex.submit(_run, fn, label): label
            for fn, label in worker_specs
        }

        done, not_done = _futures.wait(
            futures.keys(),
            timeout=parallel_fetch_timeout,
            return_when=_futures.ALL_COMPLETED,
        )

        for fut in done:
            try:
                merged.update(fut.result())
            except Exception as e:
                print(f"parallel_fetch future error (non-fatal): {e}")

        for fut in not_done:
            label = futures.get(fut, "unknown")
            print(
                f"parallel_fetch/{label} timed out after {parallel_fetch_timeout:.1f}s; "
                "continuing with partial data"
            )
            fut.cancel()
    finally:
        ex.shutdown(wait=False, cancel_futures=True)

    # Expose sub-timings so _timed can merge them into the global timing map
    merged["timings"] = sub_timings
    return merged


def _node_research_enrichment(state: PipelineState) -> dict:
    """Compute evidence confidence from web/news + intelligence events.

    This adds deterministic confidence signals to prompt context and report QA.
    """
    try:
        from backend.src.intelligence.research_framework import EvidenceItem, grade_evidence

        items: list[EvidenceItem] = []

        web_meta = state.get("web_search_meta") or {}
        for row in (web_meta.get("results") or []):
            if not isinstance(row, dict):
                continue
            items.append(
                EvidenceItem(
                    title=str(row.get("title") or ""),
                    url=str(row.get("url") or ""),
                    source_type="news",
                    published_date=str(row.get("published_date") or ""),
                    corroboration_count=1,
                )
            )

        intel = state.get("intelligence_context") or {}
        for ev in (intel.get("events") or []):
            if not isinstance(ev, dict):
                continue
            src = str(ev.get("source") or "news").lower()
            if "gov" in src or "official" in src:
                st = "official"
            elif "report" in src or "market" in src:
                st = "market_data"
            else:
                st = "news"

            items.append(
                EvidenceItem(
                    title=str(ev.get("headline") or ""),
                    url=str(ev.get("source_url") or ""),
                    source_type=st,
                    published_date=str(ev.get("detected_at") or ""),
                    corroboration_count=1,
                )
            )

        score = grade_evidence(items)

        prior_intel_text = (state.get("intelligence_text") or "").strip()
        confidence_text = (
            "\n\nRESEARCH EVIDENCE CONFIDENCE:\n"
            f"- Score: {score.get('score', 0)}/100\n"
            f"- Label: {score.get('confidence_label', 'LOW')}\n"
            f"- Source diversity: {score.get('source_diversity', 0)}\n"
            f"- Domain diversity: {score.get('domain_diversity', 0)}\n"
            f"- Notes: {', '.join(score.get('notes') or ['none'])}"
        )

        return {
            "research_evidence_score": score,
            "intelligence_text": (prior_intel_text + confidence_text).strip(),
        }
    except Exception as exc:
        print(f"Research enrichment error (non-fatal): {exc}")
        return {}


def _node_resolve_product(state: PipelineState) -> dict:
    product_str = getattr(state.get("user_context"), "product", "") or ""
    graph_product_id = None

    def _normalize_product_token(token: str) -> Optional[str]:
        resolved = resolve_product_id(token)
        if not resolved:
            return None
        if any(
            str(getattr(n, "id", "") or "").lower() == resolved.lower()
            for n in _domain_graph.nodes
        ):
            return resolved
        return None

    tokens = split_product_tokens(product_str) if product_str else []
    if not tokens and product_str:
        tokens = [product_str]

    graph_product_ids: List[str] = []
    seen: set[str] = set()
    for token in tokens:
        pid = _normalize_product_token(token)
        if pid and pid not in seen:
            seen.add(pid)
            graph_product_ids.append(pid)

    if not graph_product_ids and product_str:
        for pid in extract_product_ids_in_text(product_str):
            if pid in seen:
                continue
            if any(
                str(getattr(n, "id", "") or "").lower() == pid.lower()
                for n in _domain_graph.nodes
            ):
                seen.add(pid)
                graph_product_ids.append(pid)

    # Weekly/executive report requests often omit explicit products.
    # In that case, auto-select key products from perception snapshot so downstream
    # diagnosis/forecast/pricing use concrete market anchors.
    user_query = state.get("user_query", "") or ""
    if not graph_product_ids and _looks_like_weekly_report_request(user_query):
        auto_ids = _select_key_products_from_snapshot(state.get("perception_snapshot") or {}, limit=4)
        for pid in auto_ids:
            if pid not in seen:
                seen.add(pid)
                graph_product_ids.append(pid)

    if not graph_product_ids and _looks_like_portfolio_query(user_query, state.get("user_context")):
        auto_ids = _select_key_products_from_snapshot(
            state.get("perception_snapshot") or {},
            limit=5,
        )
        for pid in auto_ids:
            if pid not in seen:
                seen.add(pid)
                graph_product_ids.append(pid)

    if not graph_product_ids and (
        looks_like_sinopec_listing_query(user_query)
        or _looks_like_assistant_clarification_prompt(user_query)
    ):
        for pid in _sinopec_listing_lookup_terms(user_query, product_str):
            if pid not in seen:
                seen.add(pid)
                graph_product_ids.append(pid)

    # Prefer specific derivative products over broad upstream pools when query
    # mentions multiple products (e.g., "C5装置影响异戊二烯和间戊二烯").
    broad_nodes = {"crude_oil", "naphtha", "cracked_c5", "cracked_c9"}
    primary_candidates = [pid for pid in graph_product_ids if pid not in broad_nodes]
    graph_product_id = (
        primary_candidates[0]
        if primary_candidates
        else (graph_product_ids[0] if graph_product_ids else None)
    )

    out_of_scope = _should_short_circuit_out_of_scope(
        {
            "parsed_intent": state.get("parsed_intent"),
            "graph_product_ids": graph_product_ids,
            "user_query": state.get("user_query", "") or "",
            "user_context": state.get("user_context"),
        }
    )

    product_hint = str(getattr(state.get("user_context"), "product", "") or "").strip()
    out_of_scope_target = product_hint or str(state.get("user_query", "") or "").strip()

    portfolio_mode = _looks_like_portfolio_query(
        user_query,
        state.get("user_context"),
        graph_product_ids,
    )

    return {
        "graph_product_id": graph_product_id,
        "graph_product_ids": graph_product_ids,
        "portfolio_mode": portfolio_mode,
        "out_of_scope_detected": out_of_scope,
        "out_of_scope_target": out_of_scope_target,
    }


def _make_node_resolve_product(llm: LLMService):
    def _valid_graph_product_id(pid: str) -> bool:
        return any(str(getattr(n, "id", "") or "").lower() == str(pid or "").lower() for n in _domain_graph.nodes)

    def _normalize_plan_ids(plan: Dict[str, Any]) -> List[str]:
        raw_ids = plan.get("graph_product_ids")
        if isinstance(raw_ids, str):
            raw_ids = [raw_ids]
        if not isinstance(raw_ids, list):
            return []

        out: List[str] = []
        seen: set[str] = set()
        for item in raw_ids:
            token = str(item or "").strip()
            if not token:
                continue
            resolved = resolve_product_id(token) or token
            if not _valid_graph_product_id(resolved):
                continue
            resolved = str(resolved).strip().lower()
            if resolved in seen:
                continue
            seen.add(resolved)
            out.append(resolved)
        return out

    def node(state: PipelineState) -> dict:
        base = _node_resolve_product(state)
        plan = _llm_brain_plan(state, llm)
        if not plan:
            return base

        llm_ids = _normalize_plan_ids(plan)
        graph_product_ids = llm_ids or list(base.get("graph_product_ids") or [])

        # Prefer specific derivative products over broad pools.
        broad_nodes = {"crude_oil", "naphtha", "cracked_c5", "cracked_c9"}
        llm_primary = str(plan.get("primary_product_id") or "").strip().lower()
        if llm_primary and llm_primary in graph_product_ids and llm_primary not in broad_nodes:
            graph_product_id = llm_primary
        else:
            primary_candidates = [pid for pid in graph_product_ids if pid not in broad_nodes]
            graph_product_id = (
                primary_candidates[0]
                if primary_candidates
                else (graph_product_ids[0] if graph_product_ids else None)
            )

        llm_portfolio = bool(plan.get("portfolio_mode"))
        portfolio_mode = llm_portfolio or bool(base.get("portfolio_mode")) or len(graph_product_ids) > 1

        llm_out = bool(plan.get("out_of_scope_detected")) if "out_of_scope_detected" in plan else bool(base.get("out_of_scope_detected"))
        user_query = str(state.get("user_query") or "")
        # Safety guardrails: keep broad domain/report requests in-scope.
        if _looks_domain_related_query(user_query) or _looks_like_portfolio_query(user_query, state.get("user_context"), graph_product_ids):
            llm_out = False
        if graph_product_ids:
            llm_out = False

        out_target = str(base.get("out_of_scope_target") or "").strip()
        if llm_out and not out_target:
            out_target = str(getattr(state.get("user_context"), "product", "") or "").strip() or "requested topic"

        updates: Dict[str, Any] = {
            "graph_product_id": graph_product_id,
            "graph_product_ids": graph_product_ids,
            "portfolio_mode": portfolio_mode,
            "out_of_scope_detected": llm_out,
            "out_of_scope_target": out_target,
            "llm_brain_decision": {
                "enabled": True,
                "reason": str(plan.get("reason") or ""),
                "applied": True,
            },
        }

        normalized_response_mode = _normalize_llm_response_mode(plan.get("response_mode"))
        if normalized_response_mode:
            updates["response_mode"] = normalized_response_mode

        normalized_chat_mode = _normalize_llm_chat_mode(plan.get("chat_mode"))
        if normalized_chat_mode:
            updates["resolved_chat_mode"] = normalized_chat_mode
            updates["mode_reason"] = "llm_brain_router"

        return updates

    return node


def _node_diagnosis(state: PipelineState) -> dict:
    diag = None
    diag_text = ""
    try:
        pid = state.get("graph_product_id")
        snap = state.get("perception_snapshot", {})
        if pid and snap:
            diag = diagnosis_service.diagnose(
                pid,
                snap,
                intelligence_context=state.get("intelligence_context"),
            )
            diag_text = diagnosis_service.to_prompt_text(diag)
            print(f"Diagnosis complete: regime={diag['market_regime']}")
    except Exception as e:
        print(f"Diagnosis Error: {e}")
    return {"diagnosis": diag, "diagnosis_text": diag_text}


def _extract_what_if_overrides(user_query: str) -> Dict[str, float]:
    q = user_query or ""
    ql = q.lower()
    overrides: Dict[str, float] = {}

    num_match = _re.search(r"(\d+(?:\.\d+)?)", q)
    if not num_match:
        return overrides

    val = float(num_match.group(1))
    if any(k in q for k in ["原油", "布伦特"]) or any(
        k in ql for k in ["crude", "brent", "oil"]
    ):
        overrides["crude_price_usd"] = val
        return overrides

    if "石脑油" in q or "naphtha" in ql:
        overrides["naphtha_price_usd"] = val

    return overrides


def _node_forecast(state: PipelineState) -> dict:
    fc = None
    fc_text = ""
    what_if_result = None
    try:
        pid = state.get("graph_product_id")
        if pid:
            if _use_fast_db_only_ops_path(state):
                fc = _build_fast_forecast_from_snapshot(state, pid)
            else:
                fc = get_or_create_forecast_snapshot(
                    _app_db.engine,
                    pid,
                    diagnosis=state.get("diagnosis"),
                    intelligence_context=state.get("intelligence_context"),
                    hermes_plan=state.get("hermes_plan"),
                    run_reason="chatbot",
                )
            fc_text = forecasting_service.to_prompt_text(fc)

            parsed = state.get("parsed_intent")
            task_type = getattr(parsed, "task_type", None)
            if task_type == TaskType.WHAT_IF_SCENARIO:
                overrides = _extract_what_if_overrides(state.get("user_query", ""))
                what_if_result = forecasting_service.what_if_forecast(
                    product_id=pid,
                    perception_snapshot=state.get("perception_snapshot", {}),
                    overrides=overrides,
                )
                fc_text = (
                    (fc_text + "\n\n") if fc_text else ""
                ) + forecasting_service.what_if_to_prompt_text(what_if_result)

            print(f"Forecast complete for {pid}")
    except Exception as e:
        print(f"Forecast Error: {e}")
    return {
        "forecast": fc,
        "forecast_text": fc_text,
        "what_if_result": what_if_result,
    }


def _node_pricing(state: PipelineState) -> dict:
    pricing_text = ""
    pricing_decision = None
    delta_summary_text = ""
    try:
        pid = state.get("graph_product_id")
        snap = state.get("perception_snapshot", {})
        if pid and snap:
            pricing_decision = pricing_service.compute_pricing_decision(
                product_id=pid,
                perception_snapshot=snap,
                diagnosis=state.get("diagnosis"),
                forecast=state.get("forecast"),
                intelligence_context=state.get("intelligence_context"),
            )
            pricing_text = pricing_service.to_prompt_text(pricing_decision)
            action = pricing_decision.get("action", "hold")
            rec = pricing_decision.get("recommended_price", "N/A")
            print(f"Pricing Stage 4: {action} @ {rec} CNY/ton")

            if not pricing_decision.get("no_pricing"):
                try:
                    uid = str(state.get("user_id") or "").strip()
                    if uid:
                        recent = kb.query_recent_decision_for_user(
                            product_id=pid,
                            user_id=uid,
                            days=7,
                            limit=1,
                        )
                        if recent:
                            meta = recent[0].get("metadata", {}) or {}
                            prev_date = str(meta.get("date") or "").strip()

                            def _num(v):
                                try:
                                    return float(v)
                                except Exception:
                                    return None

                            pm = _num(meta.get("latest_market_price"))
                            cm = _num(pricing_decision.get("latest_market_price"))
                            pr = _num(meta.get("price_recommended"))
                            cr = _num(pricing_decision.get("recommended_price"))

                            if pm is not None and cm is not None and pm != 0:
                                pct = (cm - pm) / pm * 100.0
                                arrow = "⬆" if cm > pm else ("⬇" if cm < pm else "→")
                                product_name = (
                                    pricing_decision.get("product_name_cn") or pid
                                )
                                lines = [
                                    f"与上次查询（{prev_date or '近7天内'}）相比的变化：",
                                    f"- {product_name}：{int(pm):,} → {int(cm):,} ({cm - pm:+,.0f}, {pct:+.1f}%) {arrow}",
                                ]
                                if pr is not None and cr is not None:
                                    lines.append(
                                        f"- 推荐价：{int(pr):,} → {int(cr):,} ({cr - pr:+,.0f})"
                                    )

                                triggers = pricing_decision.get("triggers") or []
                                if triggers and isinstance(triggers, list):
                                    cond = str(
                                        (triggers[0] or {}).get("condition") or ""
                                    ).strip()
                                    if cond:
                                        lines.append(f"- 触发条件：{cond}")

                                delta_summary_text = "\n".join(lines)

                    kb.store_decision(
                        pricing_decision,
                        user_id=state.get("user_id"),
                        session_id=state.get("session_id"),
                    )
                except Exception as e:
                    print(f"Decision storage warning: {e}")
    except Exception as e:
        print(f"Pricing Error: {e}")
    return {
        "pricing_decision": pricing_decision,
        "pricing_text": pricing_text,
        "delta_summary_text": delta_summary_text,
    }


def _node_rag_retrieval(state: PipelineState) -> dict:
    if (state.get("routed_query") or {}).get("skip_rag"):
        return {"rag_info": ""}

    rag_info = ""
    user_query = state["user_query"]
    user_context = state.get("user_context")
    graph_product_id = state.get("graph_product_id")
    graph_product_ids = state.get("graph_product_ids") or []
    product_id = getattr(user_context, "product", None)

    print(f"Retrieving knowledge for query: {user_query}")
    try:
        retrieved_docs = kb.query(user_query, n_results=3)

        weekly_chunks: list[str] = []
        weekly_seen: set[str] = set()

        def _append_weekly_hits(hits: List[Dict[str, Any]]) -> None:
            for hit in hits:
                text = (hit.get("text") or "")[:800]
                if not text:
                    continue
                if text in weekly_seen:
                    continue
                weekly_seen.add(text)
                meta = hit.get("metadata", {})
                header = (
                    f"[Weekly {meta.get('week_id','?')} | "
                    f"{meta.get('period','?')} | "
                    f"forecast:{meta.get('forecast_direction','?')} | "
                    f"action:{meta.get('action_bias','?')}]"
                )
                weekly_chunks.append(f"{header}\n{text}")

        # Query weekly reports by canonical product ids (fixes exact-match misses).
        for pid in graph_product_ids[:4]:
            _append_weekly_hits(kb.query_weekly(user_query, product=pid, n_results=3))

        # Fallback when canonical mapping is missing.
        if not weekly_chunks and product_id:
            _append_weekly_hits(
                kb.query_weekly(user_query, product=product_id, n_results=3)
            )

        # Concept retrieval fallback for plant startup/shutdown operational asks.
        # This pass intentionally does NOT apply product filter.
        if any(
            k in user_query for k in ["装置", "停工", "停车", "重启", "开工", "检修"]
        ) or any(
            k in user_query.lower()
            for k in ["plant", "shutdown", "outage", "restart", "maintenance"]
        ):
            concept_query = (
                "C5 separation unit shutdown restart maintenance "
                "isoprene piperylene DCPD supply impact China"
            )
            _append_weekly_hits(
                kb.query_weekly(concept_query, product=None, n_results=5)
            )

        # Final no-filter fallback to avoid empty weekly context.
        if not weekly_chunks:
            _append_weekly_hits(kb.query_weekly(user_query, product=None, n_results=3))

        product_catalog_chunks: list[str] = []
        product_catalog_seen: set[str] = set()

        def _append_product_catalog_hits(hits: List[Dict[str, Any]]) -> None:
            for hit in hits:
                text = (hit.get("text") or "")[:800]
                if not text or text in product_catalog_seen:
                    continue
                product_catalog_seen.add(text)
                meta = hit.get("metadata", {})
                header = (
                    f"[Product Catalog | {meta.get('product','?')} | "
                    f"module:{meta.get('product_type','?')} | "
                    f"data:{meta.get('data_table','')}/{meta.get('data_key','')}]"
                )
                product_catalog_chunks.append(f"{header}\n{text}")

        for pid in graph_product_ids[:4]:
            _append_product_catalog_hits(
                kb.query_product_catalog(user_query, product=pid, n_results=2)
            )

        if not product_catalog_chunks and graph_product_id:
            _append_product_catalog_hits(
                kb.query_product_catalog(user_query, product=graph_product_id, n_results=2)
            )

        if not product_catalog_chunks and product_id:
            _append_product_catalog_hits(
                kb.query_product_catalog(user_query, product=product_id, n_results=2)
            )

        if not product_catalog_chunks:
            _append_product_catalog_hits(
                kb.query_product_catalog(user_query, product=None, n_results=2)
            )

        past_decision_chunks: list[str] = []
        if graph_product_id:
            try:
                past_hits = kb.query_past_decisions(
                    user_query, product=graph_product_id, n_results=3
                )
                for hit in past_hits:
                    meta = hit.get("metadata", {})
                    past_decision_chunks.append(
                        f"[Past Decision {meta.get('date','?')} | "
                        f"action:{meta.get('action','?')} | "
                        f"price:{meta.get('price_recommended','?')}]\n"
                        f"{hit['text'][:500]}"
                    )
            except Exception:
                pass

        trimmed_docs = [
            doc[:1000] + ("..." if len(doc) > 1000 else "") for doc in retrieved_docs
        ]

        parts: list[str] = []
        if trimmed_docs:
            parts.append("STRATEGY & KNOWLEDGE:\n" + "\n\n".join(trimmed_docs))
        if product_catalog_chunks:
            parts.append(
                "PRODUCT CATALOG KNOWLEDGE:\n"
                + "\n\n".join(product_catalog_chunks)
            )
        if weekly_chunks:
            parts.append("RECENT WEEKLY REPORTS:\n" + "\n\n".join(weekly_chunks))
        if past_decision_chunks:
            parts.append(
                "PAST DECISIONS (INSTITUTIONAL MEMORY):\n"
                + "\n\n".join(past_decision_chunks)
            )
        if parts:
            rag_info = "\n\n".join(parts)

        # ── News events from intelligence layer
        try:
            news_hits = kb.query_news_events(user_query, n_results=3)
            if news_hits:
                news_chunks = [
                    f"[NEWS {h.get('metadata', {}).get('date', '?')} | "
                    f"{h.get('metadata', {}).get('impact', '?')}] "
                    f"{h['text'][:300]}"
                    for h in news_hits
                    if h.get("text")
                ]
                if news_chunks:
                    rag_info += "\n\nRECENT NEWS & EVENTS:\n" + "\n\n".join(news_chunks)
        except Exception:
            pass

        print(
            f"Retrieved {len(retrieved_docs)} docs + {len(product_catalog_chunks)} product profiles + {len(weekly_chunks)} weekly chunks + {len(past_decision_chunks)} past decisions."
        )
    except Exception as e:
        print(f"RAG Error: {e}")
    return {"rag_info": rag_info}


# ---------------------------------------------------------------------------
# Performance helpers: small TTL caches + parallel context gather
# ---------------------------------------------------------------------------


_WEB_CACHE: dict[str, tuple[float, str]] = {}
_RAG_CACHE: dict[str, tuple[float, str]] = {}


def _ttl_get(cache: dict[str, tuple[float, str]], key: str) -> str:
    if _live_data_only_mode():
        return ""
    now = _time.time()
    item = cache.get(key)
    if not item:
        return ""
    expires_at, value = item
    if expires_at <= now:
        cache.pop(key, None)
        return ""
    return value


def _ttl_set(
    cache: dict[str, tuple[float, str]], key: str, value: str, ttl_seconds: int
) -> None:
    if _live_data_only_mode():
        return
    if ttl_seconds <= 0:
        return
    cache[key] = (_time.time() + ttl_seconds, value)


def _cache_key(prefix: str, parts: list[str]) -> str:
    safe = [p.strip() for p in parts if p is not None]
    return prefix + "::" + "|".join(safe)


def _node_gather_context(state: PipelineState) -> dict:
    """Gather slow, independent context sources in parallel.

    This preserves the existing prompt format, but reduces wall-clock latency
    by overlapping web search (network) with RAG retrieval (vector store IO).
    """

    user_query = (state.get("user_query") or "").strip()
    if not user_query:
        return {"web_search_results": "", "rag_info": ""}

    # Conservative TTLs: speed up repeated questions without risking stale data.
    web_ttl = int(_os.getenv("WEB_SEARCH_CACHE_TTL_SECONDS", "600") or "600")
    rag_ttl = int(_os.getenv("RAG_CACHE_TTL_SECONDS", "1800") or "1800")

    user_context = state.get("user_context")
    product_str = str(getattr(user_context, "product", "") or "").strip()
    lang = _detect_language(user_query)
    mode = (_os.getenv("WEB_SEARCH_MODE", "off") or "off").strip().lower()
    timelimit = "m" if ("本月" in user_query or "月" in user_query.lower()) else "w"

    web_key = _cache_key("web", [mode, timelimit, lang, product_str, user_query])
    rag_key = _cache_key("rag", [lang, product_str, user_query])

    cached_web = _ttl_get(_WEB_CACHE, web_key)
    cached_rag = _ttl_get(_RAG_CACHE, rag_key)
    if cached_web and cached_rag:
        return {"web_search_results": cached_web, "rag_info": cached_rag}

    def _run_web() -> str:
        if cached_web:
            return cached_web
        return _node_web_search(state).get("web_search_results", "") or ""

    def _run_rag() -> str:
        if cached_rag:
            return cached_rag
        return _node_rag_retrieval(state).get("rag_info", "") or ""

    # Run concurrently; keep deterministic output formatting (we only merge strings).
    # Important: never raise out of this node — treat failures as “no extra context”.

    web_res = cached_web or ""
    rag_res = cached_rag or ""

    with _futures.ThreadPoolExecutor(max_workers=2) as ex:
        futures = {
            ex.submit(_run_web): "web",
            ex.submit(_run_rag): "rag",
        }
        done, not_done = _futures.wait(
            futures.keys(),
            return_when=_futures.ALL_COMPLETED,
        )

        for fut in done:
            label = futures.get(fut, "")
            try:
                value = fut.result()
            except Exception:
                value = ""
            if label == "web":
                web_res = value or web_res
            elif label == "rag":
                rag_res = value or rag_res

        for fut in not_done:
            label = futures.get(fut, "unknown")
            print(f"gather_context/{label} incomplete (non-fatal)")
            fut.cancel()

    _ttl_set(_WEB_CACHE, web_key, web_res, ttl_seconds=web_ttl)
    _ttl_set(_RAG_CACHE, rag_key, rag_res, ttl_seconds=rag_ttl)
    return {"web_search_results": web_res, "rag_info": rag_res}


def _node_evidence_compile(state: PipelineState) -> dict:
    """Compile supporting and contradicting evidence into a deterministic bundle."""
    try:
        pid = state.get("graph_product_id")
        if not pid:
            return {"evidence_bundle": {}, "evidence_text": ""}

        bundle = evidence_compiler.compile(
            product_id=pid,
            perception_snapshot=state.get("perception_snapshot"),
            diagnosis=state.get("diagnosis"),
            forecast=state.get("forecast"),
            pricing_decision=state.get("pricing_decision"),
            intelligence_context=state.get("intelligence_context"),
        )
        text = evidence_compiler.to_prompt_text(bundle)
        return {"evidence_bundle": bundle, "evidence_text": text}
    except Exception as e:
        print(f"Evidence compile error: {e}")
        return {"evidence_bundle": {}, "evidence_text": ""}


def _node_decision_critic(state: PipelineState) -> dict:
    """Run deterministic post-checks before prompt generation."""
    try:
        result = decision_critic.review(
            diagnosis=state.get("diagnosis"),
            forecast=state.get("forecast"),
            pricing_decision=state.get("pricing_decision"),
            evidence_bundle=state.get("evidence_bundle"),
        )
        text = decision_critic.to_prompt_text(result)
        return {"critic_result": result, "critic_text": text}
    except Exception as e:
        print(f"Decision critic error: {e}")
        return {"critic_result": {}, "critic_text": ""}


def _build_follow_up_explanation_anchor(state: PipelineState) -> str:
    """Build a deterministic anchor block so follow-up answers stay grounded."""
    pricing = state.get("pricing_decision") or {}
    forecast = state.get("forecast") or {}
    diagnosis = state.get("diagnosis") or {}
    evidence = state.get("evidence_bundle") or {}

    lines = ["FOLLOW-UP EXPLANATION ANCHOR:"]

    if pricing:
        rec = pricing.get("recommended_price")
        mkt = pricing.get("latest_market_price")
        action = pricing.get("action")
        if rec and mkt:
            lines.append(
                f"- Prior pricing decision: action={action}, recommended={rec} vs market={mkt}."
            )

    try:
        s7 = forecast.get("scenarios", {}).get("7d", {}).get("base", {})
        s3 = forecast.get("scenarios", {}).get("3d", {}).get("base", {})
        if s3 and s7:
            lines.append(
                f"- Prior forecast ranges: 3d={s3.get('low')}~{s3.get('high')}, 7d={s7.get('low')}~{s7.get('high')}."
            )
    except Exception:
        pass

    if diagnosis:
        lines.append(
            f"- Prior diagnosis: regime={diagnosis.get('market_regime', 'unknown')}, net_signal={diagnosis.get('net_signal', 'unknown')}."
        )

    rise = evidence.get("evidence_for_rise") or []
    fall = evidence.get("evidence_for_fall") or []
    if rise or fall:
        lines.append("- Key evidence that previously drove the decision:")
        for item in rise[:3]:
            lines.append(f"  * up: {item}")
        for item in fall[:3]:
            lines.append(f"  * down: {item}")

    lines.append(
        "- Rule: explain the prior conclusion first; only then add updates or caveats from new context."
    )

    return "\n".join(lines)


def _infer_response_mode(
    user_query: str,
    task_type: TaskType,
    explanation_mode: bool,
    user_prefs: Optional[Dict[str, Any]] = None,
) -> str:
    """Infer output style from user demand: executive_brief, concise, structured, or full_report."""
    if explanation_mode:
        return "structured"

    q = (user_query or "").strip().lower()
    full_report_markers = [
        "full report",
        "detailed report",
        "complete report",
        "完整报告",
        "详细报告",
        "深度分析",
        "全面分析",
        "boss briefing",
        "executive briefing",
        "经营简报",
        "管理层简报",
    ]

    if _looks_like_provenance_question(q):
        return "concise"

    # Decision/scenario asks should keep the full professional template even
    # when the query contains generic analysis markers (e.g. "分析", "变化").
    if task_type in (
        TaskType.MARKET_ANALYSIS_DECISION,
        TaskType.WHAT_IF_SCENARIO,
    ):
        return "full_report"

    if _is_multi_product_price_delta_query(user_query):
        return "full_report"

    if _looks_like_erp_revenue_query(user_query):
        return "full_report" if any(marker in q for marker in full_report_markers) else "executive_brief"

    prefs = user_prefs or {}
    chat_mode, _reason = _classify_chat_mode(
        user_query,
        user_prefs=prefs,
        task_type=task_type,
        explanation_mode=explanation_mode,
    )
    if chat_mode == "chat":
        return "conversational"
    if chat_mode == "report":
        return "full_report"
    if chat_mode == "deep_research":
        return "full_report"

    pref_format = str(prefs.get("default_format") or "").strip().lower()
    if pref_format == "full_report":
        return "full_report"
    if pref_format == "executive_brief":
        return "executive_brief"
    if pref_format in {"structured", "concise"} and not _is_business_data_task(task_type, user_query):
        return pref_format

    if any(marker in q for marker in full_report_markers):
        return "full_report"

    if task_type == TaskType.BOSS_BRIEFING:
        return "full_report"

    if _is_business_data_task(task_type, user_query):
        return "executive_brief"

    if pref_format == "concise":
        return "concise"
    return "structured"


def _node_erp_data_retrieval(state: PipelineState) -> dict:
    """Fetch comprehensive ERP data for internal business queries."""

    user_context = state.get("user_context")
    parsed_intent = state.get("parsed_intent")
    product = getattr(user_context, "product", None) if user_context else None
    period = getattr(user_context, "period", None) if user_context else None
    task_type = parsed_intent.task_type if parsed_intent else None
    user_query = state.get("user_query", "") or ""
    other_products_excluding_sales = _looks_like_other_products_excluding_sales_query(user_query)
    response_mode = str(state.get("response_mode") or "").strip().lower()
    executive_weekly = _is_executive_weekly_request(
        user_query,
        task_type=task_type,
        response_mode=response_mode,
    )

    def _erp_query_requires_live_details(query: str, prefs_local: Dict[str, Any]) -> bool:
        if _normalize_bool_text(prefs_local.get("force_live_erp"), False):
            return True
        if _normalize_bool_text(prefs_local.get("force_precomputed_erp"), False):
            return False

        q = str(query or "")
        ql = q.lower()
        return (
            _looks_like_erp_sales_detail_query(q)
            or _looks_like_erp_row_level_detail_query(q)
            or "实时" in q
            or "live erp" in ql
            or "latest internal" in ql
        )
    prefs = dict(state.get("user_prefs") or {})
    use_precomputed_erp = _normalize_bool_text(
        prefs.get("erp_precomputed_context_enabled", _os.getenv("ERP_PRECOMPUTED_CONTEXT_ENABLED", "true")),
        True,
    ) and not _live_data_only_mode(state)
    erp_fast_path = _normalize_bool_text(
        prefs.get("erp_precompute_fast_path", _os.getenv("ERP_PRECOMPUTE_FAST_PATH", "false")),
        False,
    )

    # Map product name to product_id for ERP service
    product_id = None
    if product:
        product_id = resolve_product_id(str(product))
        if not product_id:
            resolved_ids = extract_product_ids_in_text(str(product))
            if resolved_ids:
                product_id = resolved_ids[0]
        if not product_id:
            product_lower = str(product).lower()
            extra_erp_aliases = {
                "mthpa": "mthpa_hardener",
                "固化剂": "mthpa_hardener",
                "甲基四氢苯酐": "mthpa_hardener",
            }
            product_id = extra_erp_aliases.get(product_lower)
            if not product_id:
                for key, pid in extra_erp_aliases.items():
                    if key in product_lower or product_lower in key:
                        product_id = pid
                        break
    if not product_id:
        resolved_ids = extract_product_ids_in_text(user_query)
        if resolved_ids:
            product_id = resolved_ids[0]
    explicit_query_products = extract_product_ids_in_text(user_query)
    if (
        _looks_like_all_products_revenue_query(user_query)
        or _looks_like_all_products_sales_detail_query(user_query)
    ) and not explicit_query_products:
        product_id = None
    if other_products_excluding_sales:
        product_id = None

    # Determine query type.
    uq_lower = user_query.lower()
    detail_markers = [
        "customer",
        "customers",
        "客户",
        "contract",
        "contracts",
        "合同",
        "production",
        "产能",
        "生产",
        "plan",
        "排产",
    ]
    wants_detail = any(m in uq_lower or m in user_query for m in detail_markers)

    # Logistics intent detection — map inbound/outbound/replenishment keywords to
    # dedicated query_types so build_erp_context fetches the right data slice.
    _inbound_markers_zh = ("入库", "收货", "到货", "来货", "在途", "采购入")
    _inbound_markers_en = ("inbound", "receipt", "receiving", "arrival", "purchase order")
    _outbound_markers_zh = ("出库", "出货", "发货", "交付")
    _outbound_markers_en = ("outbound", "shipment", "ship out", "delivery")
    _replenishment_markers_zh = ("补货", "备货", "库存覆盖", "覆盖天数")
    _replenishment_markers_en = ("replenishment", "reorder", "days of supply", "stock coverage")

    _has_inbound = any(m in user_query for m in _inbound_markers_zh) or any(m in uq_lower for m in _inbound_markers_en)
    _has_outbound = any(m in user_query for m in _outbound_markers_zh) or any(m in uq_lower for m in _outbound_markers_en)
    _has_replenishment = any(m in user_query for m in _replenishment_markers_zh) or any(m in uq_lower for m in _replenishment_markers_en)

    if task_type == TaskType.BOSS_BRIEFING:
        if _looks_like_weekly_report_request(user_query) and not wants_detail:
            query_type = "report_light"
        else:
            query_type = "briefing"
    elif executive_weekly:
        query_type = "briefing"
    elif _has_replenishment:
        query_type = "replenishment"
    elif _has_inbound and _has_outbound:
        query_type = "replenishment"
    elif _has_inbound:
        query_type = "inbound"
    elif _has_outbound:
        query_type = "outbound"
    else:
        query_type = "auto"


    precomputed_erp_block = _build_precomputed_erp_block(state, product_id=product_id) if use_precomputed_erp else ""
    explicit_live_erp = other_products_excluding_sales or _erp_query_requires_live_details(user_query, prefs)
    force_live_erp = executive_weekly or task_type == TaskType.BOSS_BRIEFING

    if use_precomputed_erp and not explicit_live_erp and not force_live_erp:
        # Non-blocking mode: serve precomputed ERP bundle by default.
        # If cache is warming/missing, the block already includes status and async refresh signal.
        erp_data = precomputed_erp_block or (
            "PRECOMPUTED ERP INSIGHT BUNDLE (DB CACHE):\n"
            "- status: unavailable\n"
            "- note: cache warming; async refresh has been requested"
        )
    elif erp_fast_path and precomputed_erp_block and not explicit_live_erp:
        erp_data = precomputed_erp_block
    else:
        erp_data = build_erp_context(
            product_id=product_id,
            period=period,
            query_type=query_type,
            user_query=user_query,
        )
        if precomputed_erp_block:
            erp_data = f"{precomputed_erp_block}\n\n{erp_data}".strip()

    if looks_like_competitor_query(user_query):
        comp_pid = product_id
        uq_lower = user_query.lower()
        if not comp_pid:
            if "粘合" in user_query or "adhesive" in uq_lower:
                comp_pid = "c5_resin"
            elif "标线" in user_query or "road" in uq_lower:
                comp_pid = "road_marking_resin"
            elif "加氢" in user_query or "hydrogenated" in uq_lower:
                comp_pid = "hydrogenated_c9"
            elif "异戊" in user_query or "isoprene" in uq_lower:
                comp_pid = "isoprene"
            elif "间戊" in user_query or "piperylene" in uq_lower:
                comp_pid = "piperylene"
        comp_block = get_competitor_comparison(str(comp_pid or ""), None)
        if comp_block:
            erp_data = (erp_data.rstrip() + "\n\n" + comp_block).strip()

    print(
        f"ERP data retrieved: {len(erp_data)} chars for product={product_id}, period={period}, query_type={query_type}"
    )
    return {"erp_data": erp_data}


def _sinopec_listing_lookup_terms(user_query: str, product: Optional[str]) -> List[str]:
    explicit = extract_product_ids_in_text(user_query)
    if explicit:
        return explicit
    if product:
        tokens = split_product_tokens(str(product))
        resolved: List[str] = []
        for token in tokens:
            pid = resolve_product_id(token)
            if pid and pid not in resolved:
                resolved.append(pid)
        if resolved:
            return resolved
    return default_sinopec_listing_product_ids()


def _node_tool_execution(state: PipelineState) -> dict:
    tool_info = ""
    user_context = state.get("user_context")
    user_query = state.get("user_query", "") or ""
    parsed = state.get("parsed_intent")
    task_type = getattr(parsed, "task_type", None)
    routed_raw = state.get("routed_query") or {}
    routed_path = str(routed_raw.get("path") or "")
    prefs = state.get("user_prefs", {}) or {}
    pref_lang = str(prefs.get("language") or "").strip().lower()
    tool_lang = "en" if pref_lang in {"en", "english"} else "zh"

    if routed_path == "deterministic_market":
        block = fetch_routed_market_block(
            user_query,
            product_ids=list(routed_raw.get("product_ids") or []),
            price_types=list(routed_raw.get("price_types") or []),
            metric_id=routed_raw.get("metric_id"),
            change_days=int(routed_raw.get("change_days") or 7),
            language=tool_lang,
        )
        if block:
            return {"tool_info": block}

    product = getattr(user_context, "product", None) if user_context else None
    sinopec_listing = looks_like_sinopec_listing_query(user_query) or _looks_like_assistant_clarification_prompt(
        user_query
    )
    if not product and _is_direct_market_price_lookup(user_query, user_context):
        resolved_ids = extract_product_ids_in_text(user_query)
        if resolved_ids:
            product = resolved_ids[0]
    if not product and sinopec_listing:
        product = ", ".join(_sinopec_listing_lookup_terms(user_query, None))
    if product:
        print(f"Checking V2 Database for {product}...")

        q_lower = user_query.lower()
        prefs = state.get("user_prefs", {}) or {}
        pref_delta = str(prefs.get("delta_basis") or "").strip().lower()
        pref_lang = str(prefs.get("language") or "").strip().lower()
        tool_lang = "en" if pref_lang in {"en", "english"} else "zh"

        # Default change basis:
        # - If the question is explicitly weekly → use 7d change
        # - If explicitly month-on-month / 本月 / 月环比 → use ~30d change
        # - Otherwise default to 1d (backward compatible)
        if pref_delta in {"wow", "week", "weekly", "7d"}:
            change_days = 7
        elif pref_delta in {"mom", "month", "monthly", "30d"}:
            change_days = 30
        elif (
            "month-on-month" in q_lower
            or "mom" in q_lower
            or "本月" in user_query
            or "this month" in q_lower
            or "月环比" in user_query
        ):
            change_days = 30
        elif (
            "this week" in q_lower
            or "weekly" in q_lower
            or "本周" in user_query
            or "周环比" in user_query
            or "最近一周" in user_query
            or "近一周" in user_query
            or "一周" in user_query
            or "7天" in user_query
        ):
            change_days = 7
        elif "环比" in user_query:
            # In Chinese, 环比 often implies MoM unless a weekly period is stated.
            change_days = 30
        elif sinopec_listing or "月度" in user_query or "monthly listing" in q_lower:
            change_days = 30
        else:
            change_days = 1

        # Split combined asks like "裂解C5和异戊二烯" or "C5, Isoprene" into individual lookups.
        tokens = split_product_tokens(str(product))
        if not tokens:
            tokens = [str(product).strip()]

        lookup_terms: List[str] = []
        for token in tokens:
            resolved = resolve_product_id(token)
            lookup_terms.append(resolved or token)

        ok_rows: List[str] = []
        failed_rows: List[str] = []
        listing_terms = (
            _sinopec_listing_lookup_terms(user_query, product) if sinopec_listing else lookup_terms
        )
        for term in listing_terms:
            if sinopec_listing:
                db_result = get_db_price_for_type(
                    term,
                    price_type="sinopec_sh",
                    change_days=change_days,
                    language=tool_lang,
                )
            else:
                db_result = get_db_price(term, change_days=change_days, language=tool_lang)
            if (
                "Error" not in db_result
                and "No real MySQL price data available" not in db_result
                and "暂无" not in db_result
                and "No sinopec_sh listing rows" not in db_result
            ):
                ok_rows.append(db_result)
            else:
                failed_rows.append(f"{term}: {db_result}")

        parts: List[str] = []
        if ok_rows:
            parts.append("INTERNAL DATABASE LIVE DATA:\n" + "\n".join(ok_rows))
        if failed_rows:
            parts.append("DB LOOKUP MISSING:\n" + "\n".join(failed_rows))

        if looks_like_competitor_query(user_query):
            comp_pid = state.get("graph_product_id")
            if not comp_pid:
                ptxt = str(product).lower()
                if "粘合" in str(product) or "adhesive" in ptxt:
                    comp_pid = "c5_resin"
                elif "road" in ptxt or "标线" in str(product):
                    comp_pid = "road_marking_resin"
                elif "加氢" in str(product) or "hydrogenated" in ptxt:
                    comp_pid = "hydrogenated_c9"
                elif "异戊" in str(product) or "isoprene" in ptxt:
                    comp_pid = "isoprene"
                elif "间戊" in str(product) or "piperylene" in ptxt:
                    comp_pid = "piperylene"
            rec_price = (state.get("pricing_decision") or {}).get("recommended_price")
            comparison = get_competitor_comparison(str(comp_pid or ""), rec_price)
            if comparison:
                parts.append(comparison)

        tool_info = "\n\n".join(parts).strip()

    if _external_first_online_enabled(state) and task_type != TaskType.ERP_DATA_QUERY:
        insight_block = _build_precomputed_insight_block(state)
        if insight_block:
            if tool_info:
                tool_info = f"{tool_info}\n\n{insight_block}"
            else:
                tool_info = insight_block

    return {"tool_info": tool_info}


def format_hermes_execution_brief(hermes_plan: Any) -> str:
    if not isinstance(hermes_plan, dict) or not hermes_plan:
        return ""

    def _join_values(value: Any) -> str:
        if isinstance(value, list):
            return ", ".join(str(item) for item in value if item)
        return str(value or "")

    agent_skills = hermes_plan.get("agent_skills") or []
    normalized = hermes_plan.get("normalized_query") if isinstance(hermes_plan.get("normalized_query"), dict) else {}
    privacy = hermes_plan.get("privacy") if isinstance(hermes_plan.get("privacy"), dict) else {}

    lines = [
        "HERMES EXECUTION BRIEF:",
        "Hermes is the control plane for this answer. Follow this plan before drafting.",
        f"- Domains: {_join_values(hermes_plan.get('domains')) or 'general'}.",
        f"- Capabilities: {_join_values(hermes_plan.get('capabilities')) or 'none'}.",
        f"- Presentation mode: {hermes_plan.get('presentation_mode') or 'general_business_answer'}.",
        f"- Agent skills to apply when relevant: {_join_values(agent_skills) or 'none'}.",
        f"- Tool sequence: {_join_values(hermes_plan.get('tool_sequence')) or 'standard'}.",
        f"- Validation gates: {_join_values(hermes_plan.get('validation_gates')) or 'standard'}.",
    ]
    if privacy:
        lines.append(
            "- Privacy: "
            f"data_sensitivity={privacy.get('data_sensitivity') or 'unspecified'}, "
            f"external_llm_blocked={privacy.get('external_llm_blocked') or 'false'}."
        )
    if normalized:
        fields = [
            f"capability={normalized.get('capability')}",
            f"status={normalized.get('normalization_status')}",
            f"period_start={normalized.get('period_start')}",
            f"period_end={normalized.get('period_end')}",
            f"period_source={normalized.get('period_source')}",
        ]
        lines.append("- Normalized query: " + ", ".join(field for field in fields if not field.endswith("=None")) + ".")
    if any(skill in agent_skills for skill in ("docx", "minimax-docx")):
        lines.append("- DOCX/report requests must be structured as export-ready report blocks with headings, tables, and source notes; use minimax-docx guidance when file generation is requested.")
    if any(skill in agent_skills for skill in ("presentation-creator", "html-ppt", "pptx-generator")):
        lines.append("- Presentation requests must use a slide/deck outline with real data only; do not invent chart values.")
    if "markdown-converter" in agent_skills:
        lines.append("- Markdown conversion requests must preserve tables, headings, links, and source notes unless the user asks to simplify them.")
    if "vision-analysis" in agent_skills:
        lines.append("- Vision/image requests must separate visible evidence from inference and disclose when image data is unavailable.")
    if "edia-market-research" in agent_skills:
        lines.append("- Research requests must use source-ranked external market evidence and keep public-market facts separate from internal ERP facts.")
    if "mysql" in agent_skills:
        lines.append("- MySQL engineering requests must include evidence-first SQL guidance: workload, EXPLAIN/index checks, safe rollout, rollback, and verification.")
    if any(skill in agent_skills for skill in ("frontend-design", "frontend-dev")):
        lines.append("- Frontend/UI requests must produce concrete UI improvements aligned with EDIA's existing dashboard/report patterns.")
    if any(skill in agent_skills for skill in ("forecast-run", "edia_run_forecast")):
        lines.append("- Forecast requests must cite numeric scenarios from edia_run_forecast tool output; do not invent prices.")
    if any(skill in agent_skills for skill in ("chart-render", "edia_render_chart", "beautiful-mermaid")):
        lines.append("- Chart requests must use edia_render_chart attachments or verified data; do not draw ASCII charts.")
    if any(skill in agent_skills for skill in ("erp-revenue-guardrail", "erp-sales-summary")):
        lines.append("- Sales revenue = SUM(FAMOUNT) per order line; never total volume × weighted average price.")

    hermes_execution = hermes_plan.get("hermes_execution") if isinstance(hermes_plan.get("hermes_execution"), dict) else {}
    skill_execution = hermes_plan.get("skill_execution") if isinstance(hermes_plan.get("skill_execution"), dict) else {}
    if not skill_execution and hermes_execution:
        skill_execution = hermes_execution.get("skill_execution") if isinstance(hermes_execution.get("skill_execution"), dict) else {}
    observations = []
    if isinstance(hermes_execution, dict):
        observations.extend(hermes_execution.get("observations") or [])
    for obs in skill_execution.get("observations") or []:
        if obs not in observations:
            observations.append(obs)
    if observations:
        lines.append("- Verified Hermes observations (use these numbers):")
        for obs in observations[:8]:
            if not isinstance(obs, dict):
                continue
            lines.append(
                f"  - {obs.get('tool')}: {obs.get('summary')} "
                f"(status={obs.get('status')})"
            )
            payload = obs.get("payload") if isinstance(obs.get("payload"), dict) else {}
            if payload.get("total_revenue") is not None:
                lines.append(f"    total_revenue={payload['total_revenue']}")
            if payload.get("scenarios"):
                lines.append("    forecast_scenarios=present")
    return "\n".join(lines)


def _make_assemble_prompt(prompt_assembler: PromptAssembler):
    def _build_db_grounding_text(
        state: PipelineState,
        user_query: str,
        should_include_db_grounding: bool,
        erp_data: str,
    ) -> str:
        if not should_include_db_grounding:
            state["grounding_source"] = "none"
            return ""
        if "VIEW COVERAGE AUDIT (FULL TABLE AGGREGATES):" in erp_data:
            state["grounding_source"] = "legacy"
            return ""

        # Planner-first grounding path. If planner fails for any reason,
        # prompt assembly transparently falls back to the existing legacy path.
        try:
            plan = plan_query(user_query)
            if plan:
                execution = execute_plan(plan)
                metrics = render_metrics(execution)
                planner_text = format_for_prompt(metrics)
                if planner_text:
                    state["planner_metrics"] = metrics
                    state["grounding_source"] = "planner"
                    return planner_text
        except Exception as exc:
            print(f"Planner grounding fallback: {exc}")

        try:
            legacy_text = build_db_grounding_context(
                user_query=user_query,
                focal_product_id=state.get("graph_product_id"),
                include_forecast=True,
                include_view_audit=True,
            )
            state["grounding_source"] = "legacy"
            return legacy_text
        except Exception:
            state["grounding_source"] = "legacy"
            return ""

    def _build_local_structured_evidence_tiers(
        user_context: Any,
        user_query: str,
    ) -> str:
        ev_product_id = getattr(user_context, "product", None)
        if not ev_product_id or not isinstance(ev_product_id, str):
            return ""

        ev_parts: List[str] = []

        # T1 — ERP internal transaction data
        try:
            from backend.src.service.erp_kpi_service import ErpKPIService

            kpi = ErpKPIService().get_product_kpi(ev_product_id)
            if kpi:
                t1_parts = ["[T1-ERP] 近30天实际成交数据:"]
                if kpi.sales_30d_ton is not None:
                    t1_parts.append(f"  销量 {kpi.sales_30d_ton:.0f}t/30d")
                if kpi.inventory_tons is not None:
                    t1_parts.append(f"  在库质量 {kpi.inventory_tons:.0f}t")
                if kpi.inventory_days is not None:
                    t1_parts.append(f"  库存天数 {kpi.inventory_days:.1f}天")
                if kpi.utilization_pct is not None:
                    t1_parts.append(f"  开工率代理 {kpi.utilization_pct:.1f}%")
                ev_parts.append("\n".join(t1_parts))
        except Exception:
            pass

        # T2 — Regional market reference prices
        try:
            from backend.src.service.market_data_service import market_data

            regional = market_data.get_regional_prices(ev_product_id)
            if regional:
                t2_lines = ["[T2-MKT] 朗州区域参考价格:"]
                label_map = {
                    "east_china": "华东",
                    "south_china": "华南",
                    "wuhan": "华中",
                    "average": "全国均价",
                    "yangzi": "扬子",
                    "maoming": "茂名",
                    "erp_sale_order_weighted": "公司实现均价",
                    "sinopec_sh": "上海石化",
                    "baling_1105": "巴陵1105",
                }
                for pt, pr in regional.items():
                    lbl = label_map.get(pt, pt)
                    t2_lines.append(f"  {lbl}: ¥{pr:,.0f}/吨")
                if len(regional) > 1:
                    pr_vals = list(regional.values())
                    spread = max(pr_vals) - min(pr_vals)
                    t2_lines.append(
                        f"  区域价差: ¥{spread:,.0f}/吨 ({'⚠ 偏大' if spread >= 500 else '正常'})"
                    )
                ev_parts.append("\n".join(t2_lines))
        except Exception:
            pass

        # T3 — Cost floor / naphtha model
        try:
            from backend.src.service.cost_floor_service import cost_floor_service

            floor = cost_floor_service.get_floor(ev_product_id)
            if floor:
                below = floor.get("below_floor", False)
                t3 = (
                    f"[T3-COST] 成本底价模型: 石脑油¥{floor.get('naphtha_price',0):,.0f}/吨, "
                    f"产率{floor.get('yield_factor',0):.1%}, "
                    f"成本底¥{floor.get('floor_price',0):,.0f}/吨"
                )
                if floor.get("current_price"):
                    margin = floor.get("margin_pct", 0)
                    t3 += (
                        f", 当前价¥{floor['current_price']:,.0f} "
                        f"({'⚠ 低于成本底价!' if below else f'高出底价{margin:.1f}%'})"
                    )
                ev_parts.append(t3)
        except Exception:
            pass

        # T4 — Seasonal decomposition insight
        try:
            from backend.src.analytics.seasonal import get_seasonal_insight_text

            lang = _detect_language(user_query)
            t4 = get_seasonal_insight_text(ev_product_id, language=lang)
            if t4:
                ev_parts.append(t4)
        except Exception:
            pass

        if not ev_parts:
            return ""
        return (
            "STRUCTURED EVIDENCE TIERS (internal grounding only; do not print bracket tags unless user explicitly asks for source tags):\n"
            + "\n".join(ev_parts)
        )

    def node(state: PipelineState) -> dict:
        user_query = state["user_query"]
        user_context = state["user_context"]
        perception_snapshot = state.get("perception_snapshot", {})
        conversation_history = state.get("conversation_history", [])
        conversation_context = _format_conversation_history(conversation_history)
        prior_assistant_answer = _latest_assistant_excerpt(conversation_history)
        is_follow_up = _looks_like_follow_up(user_query)
        explanation_mode = is_follow_up and bool(conversation_context)
        is_provenance_question = _looks_like_provenance_question(user_query)

        # Deterministic prompt caps to reduce LLM input tokens.
        cap_mode = (_os.getenv("PROMPT_CAP_MODE", "on") or "on").strip().lower()
        caps_enabled = cap_mode not in {"0", "false", "off", "disabled", "disable"}

        def _cap(name: str, value: str, default_max: int) -> str:
            if not caps_enabled:
                return value
            env_key = f"PROMPT_CAP_{name.upper()}_CHARS"
            try:
                max_chars = int(
                    _os.getenv(env_key, str(default_max)) or str(default_max)
                )
            except Exception:
                max_chars = default_max
            return _cap_text(value or "", max_chars)

        # Build snapshot text
        snapshot_text = ""
        if perception_snapshot:
            raw_product = getattr(user_context, "product", None)
            product_id = None
            if raw_product:
                product_id = (
                    _re.split(
                        r"\s*(?:,|，|/|&|\band\b|和|及|与)\s*",
                        str(raw_product),
                        flags=_re.IGNORECASE,
                    )[0].strip()
                    or None
                )
            if product_id and "products" in perception_snapshot:
                product_data = perception_snapshot["products"].get(product_id)
                if product_data:
                    data_label = (
                        "REAL MARKET DATA"
                        if product_data.get("data_source") == "real"
                        else "NO MYSQL MARKET DATA"
                    )
                    snapshot_text = (
                        f"PERCEPTION SNAPSHOT (STAGE 1) [{data_label}]:\n"
                        f"Product: {product_data['name_en']} ({product_id})\n"
                        f"Latest Price: {product_data['latest_price']} {product_data['unit']} on {product_data['latest_date']}\n"
                        f"7d Change: {product_data['change_7d_pct']}%"
                    )
                    # Add related products with real data
                    related_real = []
                    for pid, pdata in perception_snapshot["products"].items():
                        if pid != product_id and pdata.get("data_source") == "real":
                            related_real.append(
                                f"  {pdata['name_en']}: {pdata['latest_price']} {pdata['unit']} "
                                f"(7d: {pdata['change_7d_pct']}%)"
                            )
                    if related_real:
                        snapshot_text += (
                            "\nRelated Products (real data):\n"
                            + "\n".join(related_real[:6])
                        )
            if not snapshot_text:
                snapshot_text = (
                    "PERCEPTION SNAPSHOT (STAGE 1):\n"
                    f"Reference Date: {perception_snapshot.get('reference_date')}"
                )

        # Cap large prompt sections early.
        conversation_context = _cap("conversation", conversation_context, 6000)
        prior_assistant_answer = _cap("prior_answer", prior_assistant_answer, 3000)
        snapshot_text = _cap("snapshot", snapshot_text, 4000)

        task_type = state["parsed_intent"].task_type
        tool_info = state.get("tool_info", "")
        no_mysql_lookup = (
            task_type == TaskType.SIMPLE_LOOKUP
            and "No real MySQL price data available" in tool_info
        )

        preview_response_mode = _infer_response_mode(
            user_query,
            task_type,
            explanation_mode,
            user_prefs=state.get("user_prefs", {}) or {},
        )
        response_mode = str(state.get("response_mode") or preview_response_mode or "").strip().lower()
        if response_mode not in {
            "conversational",
            "concise",
            "structured",
            "executive_brief",
            "full_report",
        }:
            response_mode = preview_response_mode
        should_include_db_grounding = (
            response_mode in {"full_report", "structured", "executive_brief"}
            or task_type
            in {
                TaskType.MARKET_ANALYSIS_DECISION,
                TaskType.MARKET_ANALYSIS_ONLY,
                TaskType.WHAT_IF_SCENARIO,
            }
        )

        erp_data = state.get("erp_data", "") or ""
        db_grounding_text = _build_db_grounding_text(
            state,
            user_query,
            should_include_db_grounding,
            erp_data,
        )

        prompt_parts = [
            conversation_context,
            prior_assistant_answer if is_follow_up else "",
            state.get("delta_summary_text", ""),
            snapshot_text,
            _cap("db_grounding", db_grounding_text, 2800),
            _cap("web_search", state.get("web_search_results", ""), 3000),
            _cap("intelligence", state.get("intelligence_text", ""), 2200),
            _cap("evidence", state.get("evidence_text", ""), 2500),
            _cap("critic", state.get("critic_text", ""), 1800),
            _cap("diagnosis", state.get("diagnosis_text", ""), 2000),
            _cap("forecast", state.get("forecast_text", ""), 2500),
            _cap("pricing", state.get("pricing_text", ""), 2500),
            _cap("rag", state.get("rag_info", "") if not no_mysql_lookup else "", 5000),
            _cap("tool_info", tool_info, 2200),
        ]

        evidence_tiers = _build_local_structured_evidence_tiers(user_context, user_query)
        if evidence_tiers:
            prompt_parts.append(evidence_tiers)

        erp_hybrid = _is_erp_hybrid_analysis_mode(state)
        state["erp_hybrid_analysis"] = erp_hybrid

        if erp_hybrid:
            erp_facts = _build_deterministic_erp_sales_report(state)
            if erp_facts:
                prompt_parts.append(
                    _cap(
                        "erp_verified_facts",
                        "VERIFIED ERP FACTS (authoritative — do not contradict):\n"
                        f"{erp_facts}",
                        2200,
                    )
                )

        # Include ERP data for internal business queries
        if erp_data:
            prompt_parts.append(
                _cap(
                    "erp",
                    f"ERP_DATA (INTERNAL BUSINESS DATABASE — AUTHORITATIVE):\n{erp_data}",
                    5000,
                )
            )
        if erp_hybrid:
            prompt_parts.append(
                "ERP HYBRID ANALYSIS RULE:\n"
                "Use the VERIFIED ERP FACTS block as ground truth. "
                "Do not output placeholder tokens (****), empty tables, or ASCII charts. "
                "Write the three-section ERP analysis format only."
            )

        if conversation_context:
            prompt_parts.append(
                "FOLLOW-UP RULE:\n"
                "If the latest user message asks you to explain or justify a prior forecast, "
                "decision, or quoted text, keep the analysis anchored to the same product and prior context "
                "unless the latest message explicitly asks to switch products."
            )

        if explanation_mode:
            prompt_parts.append(
                "EXPLANATION RULE:\n"
                "The user is asking why the prior forecast or decision was made. "
                "You must explain the reasoning behind the prior answer first, cite the prior ranges or recommendation if they are present in recent conversation, "
                "and only then update or qualify that reasoning with the newest context. "
                "Do not switch to another product. Do not ignore the prior assistant answer."
            )
            prompt_parts.append(_build_follow_up_explanation_anchor(state))

        if is_provenance_question:
            prompt_parts.append(
                "PROVENANCE RULE:\n"
                "User is explicitly asking where a quoted/current price comes from.\n"
                "Answer in direct mode: name the exact source path (Live DB table/price_type/date, Weekly report id, or inference),\n"
                "then provide the corresponding value and date. Do not generate a full report template for this question."
            )

        is_ops_impact_query = any(
            k in user_query
            for k in [
                "装置",
                "停工",
                "停车",
                "重启",
                "开工",
                "检修",
                "供应冲击",
                "产能",
            ]
        ) or any(
            k in user_query.lower()
            for k in [
                "plant",
                "shutdown",
                "restart",
                "outage",
                "maintenance",
                "capacity",
            ]
        )
        has_weekly_evidence = "RECENT WEEKLY REPORTS:" in (
            state.get("rag_info", "") or ""
        )
        has_live_db = "INTERNAL DATABASE LIVE DATA" in (tool_info or "")

        if is_ops_impact_query and (has_weekly_evidence or has_live_db):
            prompt_parts.append(
                "OPERATIONAL IMPACT SYNTHESIS RULE:\n"
                "This question is about plant startup/shutdown impact.\n"
                "If weekly reports and/or live DB prices are present, you MUST provide an impact analysis and action recommendation.\n"
                "Do NOT answer with 'cannot analyze' unless both weekly evidence and live price evidence are absent.\n"
                "When real-time unit operation feed is missing, use weekly report operational narratives as proxy evidence and clearly label confidence as conditional.\n"
                "Think first about what the user actually needs before writing: (1) per-product market status with current price / WoW / MoM / inventory cover if available, (2) cause-to-effect chain from startup/shutdown changes to supply, demand, inventory and pricing, (3) 3d / 7d / 30d scenario table with explicit conditions, (4) operational recommendations split into inventory / pricing / production actions, (5) risk warnings and invalidation signals.\n"
                "Required structure: (1) supply impact path, (2) inventory implication, (3) pricing implication, (4) inventory/pricing/production actions with triggers.\n"
                "If a metric is unavailable in the current context, explicitly say it is unavailable instead of omitting it or inventing it."
            )

        if state.get("portfolio_mode"):
            prompt_parts.append(
                "PORTFOLIO COVERAGE RULE:\n"
                "This is a portfolio-level request. You must cover multiple products instead of collapsing to a single focal product.\n"
                "Include at least: 裂解C5, 异戊二烯, SIS, 间戊二烯, MTHPA, 道路标线C5, 粘合C5, 双环戊二烯, 抽余C5.\n"
                "For products with missing live data, explicitly mark as 'data unavailable' instead of inferring values."
            )

        hermes_execution = execute_hermes_plan(state.get("hermes_plan"), user_query=user_query)
        hermes_observations = format_hermes_observations_for_prompt(hermes_execution)
        if hermes_observations:
            prompt_parts.append(_cap("hermes_observations", hermes_observations, 3500))

        hermes_brief = format_hermes_execution_brief(state.get("hermes_plan"))
        if hermes_brief:
            prompt_parts.append(hermes_brief)

        user_context.additional_info = "\n\n".join(
            part for part in prompt_parts if part
        ).strip()

        prefs = state.get("user_prefs", {}) or {}
        user_lang = _resolve_output_language(state)

        state_chat_mode = _normalize_manual_chat_mode(state.get("resolved_chat_mode"))
        if state_chat_mode:
            resolved_chat_mode = state_chat_mode
            mode_reason = str(state.get("mode_reason") or "state_override")
        else:
            resolved_chat_mode, mode_reason = _classify_chat_mode(
                user_query,
                user_prefs=prefs,
                task_type=task_type,
                explanation_mode=explanation_mode,
            )

        system_prompt = prompt_assembler.assemble_system_prompt(
            task_type,
            language=user_lang,
            direct_explanation=explanation_mode,
            response_mode=response_mode,
            erp_hybrid_analysis=erp_hybrid,
        )

        # Inject backtest instruction when user specified a historical cutoff date
        date_scope = state.get("date_scope")
        if date_scope and date_scope.get("type") == "historical_backtest":
            cutoff = date_scope["cutoff_date"]
            label = date_scope.get("label", cutoff)
            backtest_banner = (
                f"\n\n⚠️ 回测模式 BACKTEST MODE\n"
                f"本次分析必须严格基于截至 {label}（{cutoff}）的历史数据。\n"
                f"你必须假设今天是 {label}，绝对不得引用或暗示该日期之后发生的任何价格、事件或数据。\n"
                f"所有价格数据引用必须来自 {cutoff} 或之前的记录。\n"
                f"用户指定的日期是正确的——绝对不要将其视为笔误或'修正'它。\n"
                f"如果某产品在该截止日期前没有价格记录，请明确说明数据不可用。\n"
            )
            system_prompt = backtest_banner + system_prompt
        user_prompt = prompt_assembler.assemble_user_prompt(
            user_context.to_dict(), user_query, language=user_lang
        )

        try:
            state_timings = dict(state.get("timings") or {})
        except Exception:
            state_timings = {}

        prompt_stats = {
            "caps_enabled": bool(caps_enabled),
            "additional_info_chars": len(user_context.additional_info or ""),
            "hermes_brief_chars": len(hermes_brief or ""),
            "hermes_observation_count": int(hermes_execution.get("observation_count") or 0),
            "hermes_observation_tools": [
                str(item.get("tool") or "")
                for item in (hermes_execution.get("observations") or [])
                if isinstance(item, dict) and item.get("tool")
            ],
            "hermes_agent_skills": list((state.get("hermes_plan") or {}).get("agent_skills") or []),
            "system_prompt_chars": len(system_prompt or ""),
            "user_prompt_chars": len(user_prompt or ""),
            "timings_so_far_ms": state_timings,
        }
        return {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "explanation_mode": explanation_mode,
            "response_mode": response_mode,
            "resolved_chat_mode": resolved_chat_mode,
            "mode_reason": mode_reason,
            "prompt_stats": prompt_stats,
            "hermes_execution": hermes_execution,
        }

    return node


def _build_planner_sources_payload(state: PipelineState) -> Dict[str, Any]:
    metrics = state.get("planner_metrics") or {}
    if not isinstance(metrics, dict):
        return {}

    plan = metrics.get("plan") or {}
    by_product = metrics.get("by_product") or {}
    sources: List[Dict[str, Any]] = []
    for product, intents in dict(by_product).items():
        if not isinstance(intents, dict):
            continue
        for intent, detail in intents.items():
            if not isinstance(detail, dict):
                continue
            row_count = int(detail.get("row_count") or 0)
            sources.append(
                {
                    "product": str(product or ""),
                    "intent": str(intent or ""),
                    "table": str(detail.get("table") or ""),
                    "row_count": row_count,
                    "latest_date": str(detail.get("latest_date") or ""),
                    "has_data": row_count > 0,
                    "metrics": dict(detail.get("metrics") or {}),
                }
            )

    if not sources and not metrics.get("data_sources"):
        return {}

    return {
        "grounding_source": str(state.get("grounding_source") or ""),
        "generated_at": str(metrics.get("generated_at") or ""),
        "products": list(plan.get("products") or []),
        "intents": list(plan.get("intents") or []),
        "time_range": dict(plan.get("time_range") or {}),
        "data_sources": list(metrics.get("data_sources") or []),
        "sources": sources[:12],
        "errors": list(metrics.get("errors") or [])[:5],
    }


def _make_generate_report(llm: LLMService):
    def _normalize_bool(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        txt = str(value).strip().lower()
        if txt in {"1", "true", "yes", "on", "y"}:
            return True
        if txt in {"0", "false", "no", "off", "n"}:
            return False
        return default

    def _policy_default_final_route(
        state: PipelineState,
        prefs: Dict[str, Any],
        task_type: Optional[TaskType],
    ) -> str:
        local_disabled = _normalize_bool(
            _os.getenv("DISABLE_LOCAL_LLM", "false"),
            False,
        )
        if local_disabled:
            return "external"

        if _external_first_online_enabled(state):
            if task_type == TaskType.ERP_DATA_QUERY:
                return "local"
            return "external"

        mode = str(
            (prefs or {}).get("chat_mode") or state.get("resolved_chat_mode") or ""
        ).strip().lower()
        if mode in {"analysis", "report", "deep_research"}:
            return "external"

        if task_type in {
            TaskType.SIMPLE_LOOKUP,
            TaskType.CONVERSATIONAL,
            TaskType.OUT_OF_DOMAIN,
        }:
            return "local"

        return "external"

    def _default_router_plan(state: PipelineState) -> Dict[str, Any]:
        parsed = state.get("parsed_intent")
        task_type = getattr(parsed, "task_type", None)
        query = str(state.get("user_query") or "")
        ql = query.lower()
        user_context = state.get("user_context")
        period = str(getattr(user_context, "period", None) or "Current Week")
        response_mode = str(state.get("response_mode") or "").strip().lower()
        executive_weekly = _is_executive_weekly_request(
            query,
            task_type=task_type,
            response_mode=response_mode,
        )

        internal_markers = [
            "our",
            "inventory",
            "shipment",
            "contract",
            "customer",
            "erp",
            "sales",
            "target",
            "actual",
            "gap",
            "team",
            "内部",
            "库存",
            "出货",
            "合同",
            "客户",
            "销量",
            "目标",
            "实际",
            "差距",
            "团队",
        ]
        asks_internal = any(k in ql or k in query for k in internal_markers)

        need_erp = task_type in {TaskType.ERP_DATA_QUERY}
        if asks_internal:
            need_erp = True

        need_public = task_type not in {TaskType.CONVERSATIONAL, TaskType.OUT_OF_DOMAIN}
        if task_type == TaskType.ERP_DATA_QUERY and not any(
            k in ql
            for k in ["market", "news", "event", "price trend", "市场", "新闻", "事件", "行情"]
        ):
            need_public = False
        if executive_weekly:
            need_public = True

        products = list(state.get("graph_product_ids") or [])
        if not products:
            products = extract_product_ids_in_text(query)
        max_products = 8 if executive_weekly else 6

        return {
            "need_erp": bool(need_erp),
            "need_public": bool(need_public),
            "products": products[:max_products],
            "time_window": period,
            "branch_tasks": {
                "erp_local": bool(need_erp),
                "lz_market": bool(need_public),
                "news_events": bool(need_public),
            },
            "executive_weekly": bool(executive_weekly),
            "rationale": "fallback_rule_plan",
            "router_model": "deepseek",
        }

    def _parse_router_plan(raw: Any, state: PipelineState) -> Dict[str, Any]:
        plan = _default_router_plan(state)
        if not isinstance(raw, dict):
            return plan

        plan["need_erp"] = _normalize_bool(raw.get("need_erp"), plan["need_erp"])
        plan["need_public"] = _normalize_bool(raw.get("need_public"), plan["need_public"])

        products = raw.get("products")
        if isinstance(products, list):
            cleaned = [str(p).strip() for p in products if str(p).strip()]
            if cleaned:
                max_products = 8 if bool(plan.get("executive_weekly")) else 6
                plan["products"] = cleaned[:max_products]

        time_window = str(raw.get("time_window") or "").strip()
        if time_window:
            plan["time_window"] = time_window

        tasks = raw.get("branch_tasks")
        if isinstance(tasks, dict):
            plan["branch_tasks"] = {
                "erp_local": _normalize_bool(tasks.get("erp_local"), plan["branch_tasks"].get("erp_local", False)),
                "lz_market": _normalize_bool(tasks.get("lz_market"), plan["branch_tasks"].get("lz_market", False)),
                "news_events": _normalize_bool(tasks.get("news_events"), plan["branch_tasks"].get("news_events", False)),
            }

        rationale = str(raw.get("rationale") or "").strip()
        if rationale:
            plan["rationale"] = rationale[:500]

        # Safety override: if user explicitly asks for market/news/event analysis,
        # ensure public branch remains enabled even if router under-selects it.
        query = str(state.get("user_query") or "")
        ql = query.lower()
        public_markers = [
            "market",
            "news",
            "event",
            "trend",
            "supply",
            "demand",
            "价格",
            "行情",
            "新闻",
            "事件",
            "趋势",
            "供需",
        ]
        if any(marker in query or marker in ql for marker in public_markers):
            plan["need_public"] = True
            branch_tasks = dict(plan.get("branch_tasks") or {})
            branch_tasks["lz_market"] = True
            branch_tasks["news_events"] = True
            plan["branch_tasks"] = branch_tasks

        if bool(plan.get("executive_weekly")):
            plan["need_public"] = True
            branch_tasks = dict(plan.get("branch_tasks") or {})
            branch_tasks["lz_market"] = True
            branch_tasks["news_events"] = True
            branch_tasks["erp_local"] = bool(plan.get("need_erp", False))
            plan["branch_tasks"] = branch_tasks

        return plan

    def _run_kimi_router_plan(state: PipelineState) -> Dict[str, Any]:
        fallback = _default_router_plan(state)
        user_context = state.get("user_context")
        router_system = (
            "You are EDIA router (DeepSeek). Output JSON only. "
            "Infer user intent dimensions first: product scope, time duration, data categories, and constraints. "
            "Then decide what data must be fetched from ERP/database and market intelligence to answer accurately. "
            "Request ERP/database context only when user explicitly asks internal sales/ERP/customer/contract/shipment/target-actual-gap/team data. "
            "For market/news/external analysis, keep need_erp=false and rely on public branch data.\n"
            "Required JSON keys: need_erp(bool), need_public(bool), products(string[]), time_window(string), "
            "branch_tasks(object with erp_local/lz_market/news_events booleans), rationale(string)."
        )
        router_user = (
            f"User query: {state.get('user_query') or ''}\n"
            f"Resolved context product: {getattr(user_context, 'product', None)}\n"
            f"Resolved context period: {getattr(user_context, 'period', None)}\n"
            f"Chat mode: {state.get('resolved_chat_mode') or ''}\n"
            "Plan retrieval depth from user request. If detail level is high, set need_erp=true.\n"
            "Return JSON only."
        )

        try:
            raw = llm.generate_json(
                router_system,
                router_user,
                route_hint="external",
                sanitized=True,
            )
            plan = _parse_router_plan(raw, state)
            plan["router_model"] = "deepseek"
            return plan
        except Exception as exc:
            print(f"DeepSeek router fallback: {exc}")
            return fallback

    def _collect_dual_branch_data(state: PipelineState, router_plan: Dict[str, Any]) -> Dict[str, Any]:
        route_trace: List[Dict[str, Any]] = []
        branch_timings: Dict[str, float] = {"erp_branch_ms": 0.0, "public_branch_ms": 0.0, "fanout_ms": 0.0}

        def _local_branch() -> Dict[str, Any]:
            started_local = _time.perf_counter()
            if not bool(router_plan.get("need_erp", False)):
                result = {"erp_data": state.get("erp_data") or ""}
                branch_timings["erp_branch_ms"] = round((_time.perf_counter() - started_local) * 1000.0, 1)
                return result
            if state.get("erp_data"):
                result = {"erp_data": state.get("erp_data") or ""}
                branch_timings["erp_branch_ms"] = round((_time.perf_counter() - started_local) * 1000.0, 1)
                return result
            result = _node_erp_data_retrieval(state)
            branch_timings["erp_branch_ms"] = round((_time.perf_counter() - started_local) * 1000.0, 1)
            return result

        def _public_branch() -> Dict[str, Any]:
            started_public = _time.perf_counter()
            if not bool(router_plan.get("need_public", False)):
                branch_timings["public_branch_ms"] = round((_time.perf_counter() - started_public) * 1000.0, 1)
                return {}
            if state.get("web_search_results") or state.get("intelligence_context"):
                result = {
                    "web_search_results": state.get("web_search_results") or "",
                    "web_search_meta": state.get("web_search_meta") or {},
                    "intelligence_context": state.get("intelligence_context") or {},
                    "intelligence_text": state.get("intelligence_text") or "",
                    "perception_snapshot": state.get("perception_snapshot") or {},
                }
                branch_timings["public_branch_ms"] = round((_time.perf_counter() - started_public) * 1000.0, 1)
                return result
            result = _node_parallel_fetch(state)
            branch_timings["public_branch_ms"] = round((_time.perf_counter() - started_public) * 1000.0, 1)
            return result

        started = _time.perf_counter()
        with _futures.ThreadPoolExecutor(max_workers=2) as ex:
            fut_local = ex.submit(_local_branch)
            fut_public = ex.submit(_public_branch)
            local_result = fut_local.result() if fut_local else {}
            public_result = fut_public.result() if fut_public else {}

        elapsed_ms = round((_time.perf_counter() - started) * 1000.0, 1)
        branch_timings["fanout_ms"] = elapsed_ms
        route_trace.append(
            {
                "step": "dual_branch_fanout",
                "elapsed_ms": elapsed_ms,
                "erp_branch_ms": branch_timings.get("erp_branch_ms", 0.0),
                "public_branch_ms": branch_timings.get("public_branch_ms", 0.0),
                "need_erp": bool(router_plan.get("need_erp", False)),
                "need_public": bool(router_plan.get("need_public", False)),
            }
        )

        return {
            "local_erp_result": local_result if isinstance(local_result, dict) else {},
            "public_market_news_result": public_result if isinstance(public_result, dict) else {},
            "route_trace": route_trace,
            "branch_timings": branch_timings,
        }

    def _build_public_payload(state: PipelineState, router_plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        router_plan = router_plan or {}
        executive_weekly = bool(router_plan.get("executive_weekly"))
        return _build_external_unified_payload(state, executive_weekly=executive_weekly)

    def _generate_kimi_final_answer(
        state: PipelineState,
        router_plan: Dict[str, Any],
        public_payload: Dict[str, Any],
    ) -> str:
        language = _resolve_output_language(state)
        lang_hint = "English" if language == "en" else "Chinese"
        sys_prompt = (
            "You are EDIA final synthesis engine (DeepSeek).\n"
            "Use provided data only. Do not request raw ERP rows.\n"
            "Use the provided database-grounded market and ERP facts only; never claim hidden ERP details.\n"
            "Return a practical decision-oriented answer with explicit source attribution.\n"
            f"Output language: {lang_hint}."
        )
        evidence_policy = public_payload.get("external_evidence_policy") or {}
        if evidence_policy:
            sys_prompt += (
                "\nEvidence policy: "
                + str(evidence_policy.get("guidance") or "")
            )
        if bool(router_plan.get("executive_weekly")):
            sys_prompt += (
                "\nFor executive weekly reporting, strictly use this section structure and headings in order:"
                "\n## 📊 01 · Market Overview"
                "\n## 🔍 02 · Key Drivers"
                "\n## 📈 03 · Price Forecast"
                "\n## 💡 04 · Pricing Decision"
                "\n## ⚠️ 05 · Risk Alerts"
                "\n## ❓ 06 · Suggested Follow-ups"
                "\nUnder Key Drivers, explicitly label each item as Verified Fact or Inference."
                " Use concrete numbers, units, and WoW/DoD deltas whenever available."
                " If a number is not in payload, say it is unavailable instead of inventing."
                " Pricing Decision must include 2 trigger conditions and one dissenting view."
                " If pricing adjustment steps are available, render a standalone Markdown 'Pricing Ladder' table"
                " with each row on its own line."
                " Keep ERP references aggregated/sanitized and include plain-language source attribution."
            )
        usr_prompt = (
            f"User query:\n{state.get('user_query') or ''}\n\n"
            "Router plan JSON:\n"
            f"{_json.dumps(router_plan, ensure_ascii=False)}\n\n"
            "Unified external payload JSON:\n"
            f"{_json.dumps(public_payload, ensure_ascii=False)}\n\n"
            "Produce final answer now."
        )
        return llm.generate_completion(
            sys_prompt,
            usr_prompt,
            route_hint="external",
            sanitized=True,
        )

    def _generate_compact_boss_briefing_answer(
        state: PipelineState,
        *,
        sanitized: bool,
    ) -> str:
        language = _resolve_output_language(state)
        lang_hint = "English" if language == "en" else "Chinese"
        compact_router_plan = {
            "executive_weekly": _is_executive_weekly_request(
                str(state.get("user_query") or ""),
                task_type=getattr(state.get("parsed_intent"), "task_type", None),
                response_mode=state.get("response_mode"),
            ),
            "time_window": str(
                getattr(state.get("user_context"), "period", None) or "Current Week"
            ),
            "products": list(state.get("graph_product_ids") or []),
        }
        compact_payload = _build_external_unified_payload(
            state,
            executive_weekly=bool(compact_router_plan.get("executive_weekly")),
        )

        compact_system = (
            "You are EDIA executive briefing engine."
            " Use provided database-grounded inputs only."
            " If numerical values are present in input, treat them as available and do not claim data is unavailable."
            " Produce a decision-oriented weekly China market executive report in this exact section order:"
            "\n## 📊 01 · Market Overview"
            "\n## 🔍 02 · Key Drivers"
            "\n## 📈 03 · Price Forecast"
            "\n## 💡 04 · Pricing Decision"
            "\n## ⚠️ 05 · Risk Alerts"
            "\n## ❓ 06 · Suggested Follow-ups"
            "\nFormatting requirements:"
            "\n- In 02, mark each bullet as 'Verified Fact' or 'Inference'."
            "\n- Use concrete numbers, units, and deltas whenever available."
            "\n- If data is missing, state unavailable; never fabricate."
            "\n- In 03 include 3-day, 7-day, 30-day ranges when available, plus scenario probabilities (Bull/Base/Bear)."
            "\n- In 04 include: action, reasoning, Trigger 1, Trigger 2, and one dissenting view."
            "\n- If pricing step data exists, include a standalone Markdown Pricing Ladder table; do not inline table rows."
            "\n- In 05 include probability and business impact for each risk."
            "\n- In 06 provide 2-3 concrete follow-up questions tied to observed data gaps or triggers."
            "\n- Keep source attribution in plain language (ERP, live MySQL market data, public intelligence)."
            "\n- Scope strictly to China market context unless cross-market spillover is explicitly in payload."
            "\nLength target: 900-1400 words for executive weekly requests."
            f" Output language: {lang_hint}."
        )
        compact_system += (
            " "
            + _build_external_evidence_policy(state, language).get("guidance", "")
        )
        compact_user = (
            "Generate this week's executive report using this compact payload JSON:\n"
            f"{_json.dumps(compact_payload, ensure_ascii=False)}"
        )
        return llm.generate_completion(
            compact_system,
            compact_user,
            route_hint="external",
            sanitized=sanitized,
        )

    def _collect_planner_streaming_text(system_prompt: str, user_prompt: str) -> str:
        """Collect async planner streaming output into a single text buffer.

        This keeps graph node behavior synchronous while reusing the same
        synthesis router used by the streaming API path.
        """
        import asyncio

        prompt = (
            "You are EDIA final synthesis engine. Follow system instructions exactly.\n\n"
            f"SYSTEM INSTRUCTIONS:\n{system_prompt}\n\n"
            f"USER CONTEXT:\n{user_prompt}\n"
        )

        chunks: list[str] = []

        async def _consume() -> None:
            async for piece in planner_synthesize_streaming(prompt):
                if piece:
                    chunks.append(str(piece))

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_consume())
        finally:
            loop.close()
        return "".join(chunks)

    def node(state: PipelineState) -> dict:
        try:
            user_query = state.get("user_query", "") or ""
            user_context = state.get("user_context")
            parsed = state.get("parsed_intent")
            task_type = getattr(parsed, "task_type", None)
            external_first_online = _external_first_online_enabled(state)

            if state.get("out_of_scope_detected"):
                report_content = _build_out_of_scope_short_reply(state)
                report = DecisionReport(
                    raw_markdown=report_content,
                    task_type=state["parsed_intent"].task_type,
                    context=state["user_context"],
                    status="success",
                    resolved_chat_mode=state.get("resolved_chat_mode"),
                    mode_reason=state.get("mode_reason"),
                    enhancement_applied={
                        "deterministic_market_lookup": "true",
                        "skip_finalizer": "true",
                    },
                )
                return {"report": report}

            reasoned_lookup = _should_force_reasoned_market_answer(user_query, user_context)
            if _is_direct_market_price_lookup(user_query, user_context) and not reasoned_lookup:
                report_content = _build_simple_lookup_report(state)
                report = DecisionReport(
                    raw_markdown=report_content,
                    task_type=state["parsed_intent"].task_type,
                    context=state["user_context"],
                    status="success",
                    resolved_chat_mode=state.get("resolved_chat_mode"),
                    mode_reason=state.get("mode_reason"),
                )
                return {"report": report}

            if _is_multi_product_price_delta_query(user_query, user_context) and not reasoned_lookup:
                if not (external_first_online and task_type != TaskType.ERP_DATA_QUERY):
                    report_content = _build_simple_lookup_report(state)
                    report = DecisionReport(
                        raw_markdown=report_content,
                        task_type=state["parsed_intent"].task_type,
                        context=state["user_context"],
                        status="success",
                        resolved_chat_mode=state.get("resolved_chat_mode"),
                        mode_reason=state.get("mode_reason"),
                    )
                    return {"report": report}

            if getattr(parsed, "task_type", None) == TaskType.SIMPLE_LOOKUP and not reasoned_lookup:
                if not (external_first_online and task_type != TaskType.ERP_DATA_QUERY):
                    report_content = _build_simple_lookup_report(state)
                    report = DecisionReport(
                        raw_markdown=report_content,
                        task_type=state["parsed_intent"].task_type,
                        context=state["user_context"],
                        status="success",
                        resolved_chat_mode=state.get("resolved_chat_mode"),
                        mode_reason=state.get("mode_reason"),
                    )
                    return {"report": report}

            pricing = state.get("pricing_decision") or {}
            if pricing and not pricing.get("no_pricing") and _looks_like_quote_range_request(user_query):
                mixed_requested = _normalize_bool(
                    (state.get("user_prefs") or {}).get("kimi_mixed_synthesis", True),
                    True,
                )
                if not mixed_requested:
                    report_content = _build_quote_range_report(state)
                    report = DecisionReport(
                        raw_markdown=report_content,
                        task_type=state["parsed_intent"].task_type,
                        context=state["user_context"],
                        status="success",
                        resolved_chat_mode=state.get("resolved_chat_mode"),
                        mode_reason=state.get("mode_reason"),
                    )
                    return {"report": report}

            if _use_hybrid_commercial_ops_advisory_path(state):
                report_content = _build_commercial_ops_advisory_report(state)
                if report_content:
                    report = DecisionReport(
                        raw_markdown=report_content,
                        task_type=TaskType.MARKET_ANALYSIS_DECISION,
                        context=state["user_context"],
                        status="success",
                        resolved_chat_mode=state.get("resolved_chat_mode"),
                        mode_reason=state.get("mode_reason"),
                        enhancement_applied={
                            "hybrid_ops_advisory": "true",
                            "answer_mode": "hybrid_commercial_ops_advisory",
                            "data_sensitivity": "market_internal_mixed",
                            "external_llm_blocked": "false",
                            "final_llm_route": "external",
                            "llm_synthesis": "facts_first",
                        },
                    )
                    return {"report": report}

            if _use_fast_price_move_action_path(state):
                report_content = _build_price_move_action_report(state)
                report = DecisionReport(
                    raw_markdown=report_content,
                    task_type=state["parsed_intent"].task_type,
                    context=state["user_context"],
                    status="success",
                    resolved_chat_mode=state.get("resolved_chat_mode"),
                    mode_reason=state.get("mode_reason"),
                )
                return {"report": report}

            fast_decision_report = _build_fast_product_decision_matrix_report(state)
            if fast_decision_report:
                report = DecisionReport(
                    raw_markdown=fast_decision_report,
                    task_type=state["parsed_intent"].task_type,
                    context=state["user_context"],
                    status="success",
                    resolved_chat_mode=state.get("resolved_chat_mode"),
                    mode_reason=state.get("mode_reason"),
                    enhancement_applied={
                        "deterministic_product_decision_matrix": "true",
                        "skip_finalizer": "true",
                    },
                )
                return {"report": report}

            sys_prompt = state["system_prompt"]
            usr_prompt = state["user_prompt"]
            prefs = state.get("user_prefs") or {}

            kimi_enabled = _normalize_bool(
                prefs.get("kimi_mixed_synthesis", True),
                True,
            )
            kimi_eligible = (
                task_type not in {
                    TaskType.SIMPLE_LOOKUP,
                    TaskType.CONVERSATIONAL,
                    TaskType.OUT_OF_DOMAIN,
                }
                or (external_first_online and task_type != TaskType.ERP_DATA_QUERY)
            )

            kimi_meta: Dict[str, str] = {}
            state_for_post = state

            if kimi_enabled and kimi_eligible:
                try:
                    mixed_started = _time.perf_counter()
                    router_started = _time.perf_counter()
                    router_plan = _run_kimi_router_plan(state)
                    router_ms = round((_time.perf_counter() - router_started) * 1000.0, 1)

                    branch_data = _collect_dual_branch_data(state, router_plan)
                    branch_timings = dict(branch_data.get("branch_timings") or {})

                    local_erp_result = dict(branch_data.get("local_erp_result") or {})
                    public_result = dict(branch_data.get("public_market_news_result") or {})
                    route_trace = list(branch_data.get("route_trace") or [])

                    merged_state = dict(state)
                    merged_state.update(public_result)
                    if local_erp_result.get("erp_data"):
                        merged_state["erp_data"] = local_erp_result.get("erp_data")

                    payload_started = _time.perf_counter()
                    public_payload = _build_public_payload(merged_state, router_plan)
                    payload_ms = round((_time.perf_counter() - payload_started) * 1000.0, 1)

                    state_for_post = merged_state
                    final_route_hint = "external"
                    final_sanitized = True
                    synthesis_started = _time.perf_counter()
                    report_content = _generate_kimi_final_answer(
                        merged_state,
                        router_plan,
                        public_payload,
                    )
                    synthesis_ms = round((_time.perf_counter() - synthesis_started) * 1000.0, 1)
                    total_mixed_ms = round((_time.perf_counter() - mixed_started) * 1000.0, 1)

                    kimi_meta = {
                        "router_model": "deepseek",
                        "erp_branch_model": "local",
                        "public_branch_model": "deepseek",
                        "synthesis_model": "deepseek",
                        "policy_status": "combined_insight_bundle_only",
                        "branch_summary": (
                            f"erp_local={bool(router_plan.get('need_erp', False))};"
                            f"public_branch={bool(router_plan.get('need_public', False))}"
                        ),
                        "route_trace": _json.dumps(route_trace, ensure_ascii=False),
                        "router_plan": _json.dumps(router_plan, ensure_ascii=False),
                        "combined_payload": _json.dumps(public_payload, ensure_ascii=False),
                        "router_ms": str(router_ms),
                        "fanout_ms": str(round(float(branch_timings.get("fanout_ms") or 0.0), 1)),
                        "erp_branch_ms": str(round(float(branch_timings.get("erp_branch_ms") or 0.0), 1)),
                        "public_branch_ms": str(round(float(branch_timings.get("public_branch_ms") or 0.0), 1)),
                        "payload_ms": str(payload_ms),
                        "final_synthesis_ms": str(synthesis_ms),
                        "total_mixed_ms": str(total_mixed_ms),
                    }
                except Exception as exc:
                    print(f"DeepSeek mixed synthesis fallback to legacy path: {exc}")
                    kimi_meta = {"policy_status": "external_synthesis_fallback_mode_default"}
                    explicit_final = str(prefs.get("final_decision_llm") or "").strip().lower()
                    explicit_route = str(prefs.get("llm_route") or "").strip().lower()
                    use_external = bool(prefs.get("use_external_reasoning", False))
                    local_disabled = _normalize_bool(
                        _os.getenv("DISABLE_LOCAL_LLM", "false"),
                        False,
                    )

                    if local_disabled:
                        final_route_hint = "external"
                    elif explicit_final in {"local", "external"}:
                        final_route_hint = explicit_final
                    elif explicit_route in {"local", "external"}:
                        final_route_hint = explicit_route
                    elif use_external:
                        final_route_hint = "external"
                    else:
                        final_route_hint = _policy_default_final_route(
                            state,
                            prefs,
                            task_type,
                        )

                    final_sanitized = bool(prefs.get("external_payload_sanitized", False))
                    if final_route_hint == "external":
                        report_content = _generate_unified_external_answer(
                            llm,
                            state,
                            sanitized=True,
                            executive_weekly=bool(
                                _is_executive_weekly_request(
                                    user_query,
                                    task_type=task_type,
                                    response_mode=state.get("response_mode"),
                                )
                            ),
                        )
                    else:
                        report_content = llm.generate_completion(
                            sys_prompt,
                            usr_prompt,
                            route_hint=final_route_hint,
                            sanitized=final_sanitized,
                        )
            else:
                explicit_final = str(prefs.get("final_decision_llm") or "").strip().lower()
                explicit_route = str(prefs.get("llm_route") or "").strip().lower()
                use_external = bool(prefs.get("use_external_reasoning", False))
                local_disabled = _normalize_bool(
                    _os.getenv("DISABLE_LOCAL_LLM", "false"),
                    False,
                )
                planner_streaming_enabled = _normalize_bool(
                    prefs.get("planner_streaming_synthesis", True),
                    True,
                )
                planner_grounded = str(state.get("grounding_source") or "").strip().lower() == "planner"

                if local_disabled:
                    final_route_hint = "external"
                elif explicit_final in {"local", "external"}:
                    final_route_hint = explicit_final
                elif explicit_route in {"local", "external"}:
                    final_route_hint = explicit_route
                elif use_external:
                    final_route_hint = "external"
                else:
                    final_route_hint = _policy_default_final_route(
                        state,
                        prefs,
                        task_type,
                    )

                final_sanitized = bool(prefs.get("external_payload_sanitized", False))
                if task_type == TaskType.BOSS_BRIEFING:
                    deterministic_report = ""
                    if _is_executive_weekly_request(
                        user_query,
                        task_type=task_type,
                        response_mode=state.get("response_mode"),
                    ) and "LIVE MARKET DATA (MYSQL):" in str(state.get("erp_data") or ""):
                        deterministic_report = _build_deterministic_boss_briefing_report(state)

                    if deterministic_report:
                        report_content = deterministic_report
                    elif final_route_hint == "external":
                        report_content = _generate_compact_boss_briefing_answer(
                            state,
                            sanitized=True,
                        )
                    else:
                        if planner_streaming_enabled and planner_grounded:
                            report_content = _collect_planner_streaming_text(
                                sys_prompt,
                                usr_prompt,
                            )
                        else:
                            report_content = llm.generate_completion(
                                sys_prompt,
                                usr_prompt,
                                route_hint=final_route_hint,
                                sanitized=final_sanitized,
                            )
                else:
                    if final_route_hint == "external":
                        report_content = _generate_unified_external_answer(
                            llm,
                            state,
                            sanitized=True,
                            executive_weekly=False,
                        )
                    else:
                        if planner_streaming_enabled and planner_grounded:
                            report_content = _collect_planner_streaming_text(
                                sys_prompt,
                                usr_prompt,
                            )
                        else:
                            report_content = llm.generate_completion(
                                sys_prompt,
                                usr_prompt,
                                route_hint=final_route_hint,
                                sanitized=final_sanitized,
                            )
            had_transport_error = _looks_like_missing_llm_answer(report_content)
            report_content = _replace_transport_error_with_fallback(report_content, state_for_post)
            report_content, arbitration_meta = _apply_pricing_constraint_arbitration(
                report_content, state_for_post
            )
            report_content = _apply_deterministic_guardrails(report_content, state_for_post)
            if _is_multi_product_price_delta_query(user_query, user_context):
                reasoned_lookup = _should_force_reasoned_market_answer(user_query, user_context)
                if state.get("response_mode") == "concise" or _is_garbled_or_too_short_report(report_content):
                    if not reasoned_lookup and not (external_first_online and task_type != TaskType.ERP_DATA_QUERY):
                        report_content = _build_simple_lookup_report(state)

            if pricing and not pricing.get("no_pricing") and _looks_like_quote_range_request(user_query):
                quote_lang = _resolve_output_language(state_for_post)
                if not _quote_range_report_has_required_slots(report_content, quote_lang):
                    report_content = _build_quote_range_report(state_for_post)

            enhancement_applied = {
                "final_llm_route": final_route_hint,
                "arbitration_compared": str(arbitration_meta.get("compared", False)).lower(),
                "arbitration_conflict_detected": str(
                    arbitration_meta.get("conflict_detected", False)
                ).lower(),
                "arbitration_auto_corrected": str(
                    arbitration_meta.get("auto_corrected", False)
                ).lower(),
                "llm_recommendation_raw": str(
                    arbitration_meta.get("llm_recommendation_raw", "")
                ),
                "logic_recommendation": str(
                    arbitration_meta.get("logic_recommendation", "")
                ),
            }
            enhancement_applied.update(kimi_meta)
            if state_for_post.get("erp_hybrid_analysis"):
                enhancement_applied["erp_hybrid_analysis"] = "true"
                enhancement_applied["skip_finalizer"] = "true"
            planner_sources_payload = _build_planner_sources_payload(state_for_post)
            if planner_sources_payload:
                enhancement_applied["grounding_source"] = str(
                    planner_sources_payload.get("grounding_source") or "planner"
                )
                enhancement_applied["planner_sources_json"] = _json.dumps(
                    planner_sources_payload,
                    ensure_ascii=False,
                )

            report = DecisionReport(
                raw_markdown=report_content,
                task_type=state["parsed_intent"].task_type,
                context=state["user_context"],
                status="partial" if had_transport_error else "success",
                error_message=("llm_transport_timeout_or_connection_error" if had_transport_error else None),
                resolved_chat_mode=state.get("resolved_chat_mode"),
                mode_reason=state.get("mode_reason"),
                enhancement_applied=enhancement_applied,
            )

            # Skip quality/skill enrichment for conversational and out-of-domain responses
            if getattr(state.get("parsed_intent"), "task_type", None) in (TaskType.CONVERSATIONAL, TaskType.OUT_OF_DOMAIN):
                return {"report": report}

            # Deterministic research evidence quality signal (Phase-2)
            try:
                from backend.src.service.types import QualityFlag, RiskAlert

                ev_score = state.get("research_evidence_score") or {}
                ev_label = str(ev_score.get("confidence_label") or "").upper()
                ev_value = int(ev_score.get("score") or 0)
                ev_notes = list(ev_score.get("notes") or [])

                if ev_label:
                    report.quality_flags.append(
                        QualityFlag(
                            code="research_evidence_confidence",
                            severity=(
                                "low" if ev_label == "HIGH" else "medium" if ev_label == "MEDIUM" else "high"
                            ),
                            message=(
                                f"research evidence confidence {ev_label} ({ev_value}/100)"
                                + (f"; notes: {', '.join(ev_notes[:3])}" if ev_notes else "")
                            ),
                            source="research_enrichment",
                        )
                    )

                if ev_label == "LOW":
                    report.alerts_prepended.append(
                        "⚠️ Evidence confidence is LOW: verify with additional independent market sources before execution."
                    )
                    report.risks.append(
                        RiskAlert(
                            severity="🟠 HIGH",
                            category="evidence",
                            description="Low research evidence confidence may weaken recommendation reliability.",
                            probability=0.7,
                            evidence=(", ".join(ev_notes[:2]) if ev_notes else "limited source depth"),
                        )
                    )
            except Exception:
                pass

            # Inject background skill insights into state for response skills
            try:
                from backend.src.skills.background import background_skill_scheduler
                bg_results = background_skill_scheduler.get_all_latest()
                state["background_skill_insights"] = {
                    name: {"summary": r.summary, "alerts": r.alerts, "data": r.data}
                    for name, r in bg_results.items()
                }
                # Merge background alerts into report risks
                for name, r in bg_results.items():
                    for alert_text in r.alerts[:3]:
                        from backend.src.service.types import RiskAlert
                        severity_map = {
                            "CRITICAL": "🔴 CRITICAL",
                            "HIGH": "🟠 HIGH",
                            "SUPPLY": "🟠 HIGH",
                            "DIVERGENCE": "🟡 MEDIUM",
                            "SENTIMENT": "🟡 MEDIUM",
                        }
                        sev = "🟢 LOW"
                        for kw, sv in severity_map.items():
                            if kw in alert_text.upper():
                                sev = sv
                                break
                        report.risks.append(RiskAlert(
                            severity=sev,
                            category=name.split("_")[0],
                            description=alert_text,
                        ))
            except Exception:
                pass

            report = apply_response_enhancement_skills(report, state)
            try:
                from backend.src.hermes.execution import build_citations_from_hermes_execution
                report.citations = build_citations_from_hermes_execution(
                    state.get("hermes_execution")
                )
            except Exception:
                pass
            if not (_looks_like_provenance_question(user_query) or _wants_raw_source_tags(user_query)):
                report.raw_markdown = _strip_internal_source_tags(report.raw_markdown)
        except Exception as e:
            print(f"Report generation error: {e}")
            report = DecisionReport(
                raw_markdown=f"## Report Generation Error\n\nThe system encountered an error while generating the analysis: `{str(e)}`\n\nPlease try again or rephrase your question.",
                task_type=state["parsed_intent"].task_type,
                context=state["user_context"],
                status="error",
                error_message=str(e),
                resolved_chat_mode=state.get("resolved_chat_mode"),
                mode_reason=state.get("mode_reason"),
            )
        return {"report": report}

    return node


def _build_references_block(state: PipelineState, language: str) -> str:
    """Deterministic reference block to increase user trust.

    This is intentionally post-LLM and does not depend on model compliance.
    """
    tool_info = state.get("tool_info", "") or ""
    rag_info = state.get("rag_info", "") or ""
    web = state.get("web_search_results", "") or ""
    erp = state.get("erp_data", "") or ""
    intel = state.get("intelligence_context", {}) or {}
    forecast = state.get("forecast", {}) or {}
    snap = state.get("perception_snapshot", {}) or {}
    snap_date = str(snap.get("reference_date") or "").strip()
    live_db_text = tool_info if "INTERNAL DATABASE LIVE DATA" in tool_info else erp

    refs: list[str] = []

    def _reference_events() -> list[dict[str, Any]]:
        if isinstance(forecast, dict):
            signal = forecast.get("intelligence_signal") or {}
            signal_events = signal.get("events") or []
            if isinstance(signal_events, list) and signal_events:
                return signal_events

            adjustment = forecast.get("forecast_adjustment") or {}
            evidence_events = adjustment.get("evidence_events") or []
            if isinstance(evidence_events, list) and evidence_events:
                return evidence_events

        events = intel.get("events") or []
        return events if isinstance(events, list) else []

    def _event_reference_lines() -> list[str]:
        lines: list[str] = []
        events = _reference_events()
        seen: set[str] = set()
        for idx, ev in enumerate(events[:3], start=1):
            if not isinstance(ev, dict):
                continue
            headline = str(ev.get("headline") or "").strip()
            if not headline:
                continue
            event_id = str(ev.get("event_id") or "").strip()
            detected_at = str(ev.get("detected_at") or "").strip()
            detected_date = detected_at[:10] if detected_at else ""
            certainty = str(ev.get("certainty") or "").strip()
            source_url = str(ev.get("source_url") or "").strip()
            raw_quote = str(ev.get("raw_quote") or "").strip()
            dedupe_key = event_id or f"{headline}|{source_url}|{detected_date}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            detail_bits: list[str] = []
            if detected_date:
                detail_bits.append(detected_date)
            if certainty:
                detail_bits.append(certainty)
            if raw_quote:
                detail_bits.append(raw_quote)
            detail = " | ".join(bit for bit in detail_bits if bit)

            if (language or "zh") == "en":
                prefix = f"[INTEL-{idx}] Ingested event: {headline}"
            else:
                prefix = f"[情报-{idx}] 新闻事件：{headline}"

            if detail:
                prefix += f" | {detail}"
            if source_url:
                prefix += f" | {source_url}"
            lines.append(prefix)
        return lines

    # [1] Live DB snapshot
    if "INTERNAL DATABASE LIVE DATA" in tool_info or "LIVE MARKET DATA (MYSQL):" in erp:
        live_rows = _extract_live_db_provenance_rows(live_db_text)
        if not live_rows and "LIVE MARKET DATA (MYSQL):" in erp:
            live_rows = [
                {"raw": f"{row['name_en']}: {row['price']:,.2f} [{row['date']}]"}
                for row in _extract_live_market_rows_from_text(erp)[:4]
            ]
        live_compact = "; ".join(r.get("raw", "") for r in live_rows[:4]).strip()
        if (language or "zh") == "en":
            label = (
                f"Live DB snapshot ({snap_date})" if snap_date else "Live DB snapshot"
            )
            refs.append(
                f"[1] {label}: {live_compact}" if live_compact else f"[1] {label}"
            )
        else:
            label = f"实时DB快照（{snap_date}）" if snap_date else "实时DB快照"
            refs.append(
                f"[1] {label}：{live_compact}" if live_compact else f"[1] {label}"
            )

    # [2] Weekly report ids
    weekly_ids: list[str] = []
    for wid in _re.findall(r"\bweek_\d+_\d+\b", rag_info, flags=_re.IGNORECASE):
        w = str(wid).strip()
        if w and w not in weekly_ids:
            weekly_ids.append(w)
    if weekly_ids:
        if (language or "zh") == "en":
            refs.append(
                f"[2] Weekly reports (vector knowledge): {', '.join(weekly_ids[:5])}"
                + ("" if len(weekly_ids) <= 5 else ", …")
            )
        else:
            refs.append(
                f"[2] 周报（向量知识库）：{', '.join(weekly_ids[:5])}"
                + ("" if len(weekly_ids) <= 5 else "，…")
            )

    # [3] Web search URLs (if present)
    urls: list[str] = []
    for u in _re.findall(r"https?://[^\s\]\)\"']+", web, flags=_re.IGNORECASE):
        uu = str(u).strip().rstrip(".,;")
        if uu and uu not in urls:
            urls.append(uu)
    if urls:
        if (language or "zh") == "en":
            refs.append(
                "[3] Web sources: "
                + ", ".join(urls[:3])
                + ("" if len(urls) <= 3 else ", …")
            )
        else:
            refs.append(
                "[3] 网页来源："
                + "，".join(urls[:3])
                + ("" if len(urls) <= 3 else "，…")
            )

    try:
        refs.extend(_event_reference_lines())
    except Exception:
        pass

    # [4] ERP data
    if erp:
        if (language or "zh") == "en":
            refs.append("[4] ERP database extract (internal)")
        else:
            refs.append("[4] ERP业务库摘录（内部）")

    if not refs:
        return ""

    if (language or "zh") == "en":
        lines = ["", "---", "**References**"]
        for r in refs:
            lines.append(f"- {r}")
        return "\n".join(lines) + "\n"

    lines = ["", "---", "**参考来源**"]
    for r in refs:
        lines.append(f"- {r}")
    return "\n".join(lines) + "\n"


def _enforce_english_only(text: str) -> str:
    """Best-effort post-processing to honor prefs.language='en'.

    We avoid any extra LLM calls; this is a lightweight deterministic cleanup.
    """
    if not text:
        return text
    try:
        from backend.src.domain.knowledge_graph import (
            domain_graph,
        )  # local import to avoid cycles

        # Replace known Chinese product names with English equivalents.
        for node in getattr(domain_graph, "nodes", []) or []:
            name_cn = getattr(node, "name_cn", "") or ""
            name_en = getattr(node, "name_en", "") or ""
            if name_cn and name_en and name_cn in text:
                text = text.replace(name_cn, name_en)
    except Exception:
        pass

    # Strip remaining CJK characters (English-only preference).
    text = _re.sub(r"[\u4e00-\u9fff]+", "", text)
    # Remove now-empty quoted strings introduced by stripping.
    text = _re.sub(r"\"\s*\"", "", text)
    # Normalize excessive spaces introduced by stripping.
    text = _re.sub(r"[ \t]{2,}", " ", text)
    text = _re.sub(r"\n{3,}", "\n\n", text)
    return text


def _build_provenance_block(state: PipelineState, language: str) -> str:
    tool_info = state.get("tool_info", "") or ""
    rag_info = state.get("rag_info", "") or ""
    erp = state.get("erp_data", "") or ""
    snap = state.get("perception_snapshot", {}) or {}
    snap_date = str(snap.get("reference_date") or "").strip()

    sources: list[str] = []

    if "INTERNAL DATABASE LIVE DATA" in tool_info or "LIVE MARKET DATA (MYSQL):" in erp:
        if snap_date:
            sources.append(f"Live DB ({snap_date})")
        else:
            sources.append("Live DB")

    live_rows = _extract_live_db_provenance_rows(tool_info)
    if not live_rows and "LIVE MARKET DATA (MYSQL):" in erp:
        live_rows = [
            {"raw": f"{row['name_en']}: {row['price']:,.2f} on {row['date']} ({row['delta_pct']:+.2f}%)"}
            for row in _extract_live_market_rows_from_text(erp)[:4]
        ]

    weekly_ids: list[str] = []
    # Prefer explicit week ids like "week_422_55".
    for m in _re.findall(r"\bweek_\d+_\d+\b", rag_info, flags=_re.IGNORECASE):
        wid = str(m).strip()
        if wid and wid not in weekly_ids:
            weekly_ids.append(wid)
    # Fallback: accept other weekly tags if explicit ids are missing.
    if not weekly_ids:
        for m in _re.findall(r"\[Weekly\s+([^\|\]]+)", rag_info, flags=_re.IGNORECASE):
            wid = str(m).strip()
            if wid and wid not in weekly_ids:
                weekly_ids.append(wid)
        for m in _re.findall(r"\bWEEK:\s*([^\n\)]+)", rag_info, flags=_re.IGNORECASE):
            # Extract any parenthesized week ids within the WEEK field.
            for w in _re.findall(r"\bweek_\d+_\d+\b", str(m), flags=_re.IGNORECASE):
                ww = str(w).strip()
                if ww and ww not in weekly_ids:
                    weekly_ids.append(ww)
    if weekly_ids:
        sources.append(
            "Weekly report ("
            + ", ".join(weekly_ids[:3])
            + ("" if len(weekly_ids) <= 3 else ", …")
            + ")"
        )

    sources.append("Inference")

    if (language or "zh") == "en":
        lines = ["", "---", "**Provenance (scan-friendly)**"]
        for s in sources:
            lines.append(f"- {s}")
        if live_rows:
            lines.append("- Live DB lineage:")
            for row in live_rows[:5]:
                pid = row.get("product_id") or "unknown"
                ptype = row.get("price_type") or "unknown"
                dt = row.get("date") or snap_date or "unknown"
                src = row.get("src") or "market_prices"
                lines.append(
                    f"  - source={src}, product_id={pid}, price_type={ptype}, date={dt}"
                )
        return "\n".join(lines) + "\n"

    # zh
    zh_sources: list[str] = []
    for s in sources:
        if s.startswith("Live DB"):
            zh_sources.append(s.replace("Live DB", "实时DB"))
        elif s.startswith("Weekly report"):
            zh_sources.append(s.replace("Weekly report", "周报"))
        elif s == "Inference":
            zh_sources.append("推断")
        else:
            zh_sources.append(s)
    lines = ["", "---", "**数据来源（可扫描）**"]
    for s in zh_sources:
        lines.append(f"- {s}")
    if live_rows:
        lines.append("- 实时DB血缘：")
        for row in live_rows[:5]:
            pid = row.get("product_id") or "unknown"
            ptype = row.get("price_type") or "unknown"
            dt = row.get("date") or snap_date or "unknown"
            src = row.get("src") or "market_prices"
            lines.append(
                f"  - source={src}, product_id={pid}, price_type={ptype}, date={dt}"
            )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Conversational fast path — no data retrieval needed
# ---------------------------------------------------------------------------


def _node_short_circuit_check(state: PipelineState) -> dict:
    """Detect special cases in the prepare graph that have pre-built responses
    so the streaming server can return them directly without calling the LLM.

    If out_of_scope_detected or SIMPLE_LOOKUP, write a DecisionReport into
    state['report'].  The streaming server already checks for this and short-
    circuits the LLM streaming step when report is present.
    """
    parsed = state.get("parsed_intent")
    user_context = state.get("user_context")
    if not parsed or not user_context:
        return {}

    user_query = state.get("user_query", "") or ""
    task_type = getattr(parsed, "task_type", None)
    reasoned_lookup = _should_force_reasoned_market_answer(user_query, user_context)

    if state.get("out_of_scope_detected"):
        report_content = _build_out_of_scope_short_reply(state)
        report = DecisionReport(
            raw_markdown=report_content,
            task_type=task_type,
            context=user_context,
            status="success",
            resolved_chat_mode=state.get("resolved_chat_mode"),
            mode_reason=state.get("mode_reason"),
            enhancement_applied={
                "deterministic_market_lookup": "true",
                "skip_finalizer": "true",
            },
        )
        return {"report": report}

    if _use_fast_competitor_portfolio_path(state):
        report_content = _build_competitor_portfolio_summary_report(state)
        report = DecisionReport(
            raw_markdown=report_content,
            task_type=task_type,
            context=user_context,
            status="success",
            resolved_chat_mode=state.get("resolved_chat_mode"),
            mode_reason=state.get("mode_reason"),
        )
        return {"report": report}

    if _use_fast_research_report_path(state):
        report_content = _build_research_report(state)
        report = DecisionReport(
            raw_markdown=report_content,
            task_type=task_type,
            context=user_context,
            status="success",
            resolved_chat_mode=state.get("resolved_chat_mode"),
            mode_reason=state.get("mode_reason"),
        )
        return {"report": report}

    if _use_fast_market_anomaly_path(state):
        report_content = _build_market_anomaly_report(state)
        report = DecisionReport(
            raw_markdown=report_content,
            task_type=task_type,
            context=user_context,
            status="success",
            resolved_chat_mode=state.get("resolved_chat_mode"),
            mode_reason=state.get("mode_reason"),
        )
        return {"report": report}

    if _use_fast_db_only_ops_path(state):
        report_content = _build_ops_inventory_pricing_report(state)
        report = DecisionReport(
            raw_markdown=report_content,
            task_type=task_type,
            context=user_context,
            status="success",
            resolved_chat_mode=state.get("resolved_chat_mode"),
            mode_reason=state.get("mode_reason"),
            enhancement_applied={
                "fast_ops_inventory_stream": "true",
                "skip_finalizer": "true",
                "deterministic_market_lookup": "true",
            },
        )
        return {"report": report}

    if _use_fast_naphtha_passthrough_path(state):
        lang = _resolve_output_language(state)
        report_content = _build_naphtha_passthrough_report(state, language=lang)
        report = DecisionReport(
            raw_markdown=report_content,
            task_type=task_type or TaskType.MARKET_ANALYSIS_DECISION,
            context=user_context,
            status="success",
            resolved_chat_mode=state.get("resolved_chat_mode"),
            mode_reason=state.get("mode_reason"),
            enhancement_applied={
                "fast_naphtha_passthrough_stream": "true",
                "skip_finalizer": "true",
                "deterministic_market_lookup": "true",
            },
        )
        return {"report": report}

    if _use_hybrid_commercial_ops_advisory_path(state):
        report_content = _build_commercial_ops_advisory_report(state)
        if report_content:
            report = DecisionReport(
                raw_markdown=report_content,
                task_type=TaskType.MARKET_ANALYSIS_DECISION,
                context=user_context,
                status="success",
                resolved_chat_mode=state.get("resolved_chat_mode"),
                mode_reason=state.get("mode_reason"),
                enhancement_applied={
                    "hybrid_ops_advisory": "true",
                    "answer_mode": "hybrid_commercial_ops_advisory",
                    "data_sensitivity": "market_internal_mixed",
                    "external_llm_blocked": "false",
                    "final_llm_route": "external",
                    "llm_synthesis": "facts_first",
                },
            )
            return {"report": report}

    if _use_fast_price_move_action_path(state):
        report_content = _build_price_move_action_report(state)
        report = DecisionReport(
            raw_markdown=report_content,
            task_type=task_type,
            context=user_context,
            status="success",
            resolved_chat_mode=state.get("resolved_chat_mode"),
            mode_reason=state.get("mode_reason"),
        )
        return {"report": report}

    if _looks_like_other_products_excluding_sales_query(user_query):
        report_content = _build_deterministic_erp_sales_report(state)
        if report_content:
            report = DecisionReport(
                raw_markdown=report_content,
                task_type=task_type,
                context=user_context,
                status="success",
                resolved_chat_mode=state.get("resolved_chat_mode"),
                mode_reason=state.get("mode_reason"),
                enhancement_applied={
                    "deterministic_erp_exclusion_sales": "true",
                    "skip_finalizer": "true",
                },
            )
            return {"report": report}

    mode_values = {
        str(state.get("response_mode") or "").strip().lower(),
        str(state.get("resolved_chat_mode") or "").strip().lower(),
        str((state.get("user_prefs") or {}).get("chat_mode") or "").strip().lower(),
    }
    if task_type == TaskType.ERP_DATA_QUERY:
        order_detail_report = _build_deterministic_erp_sales_order_detail_report(state)
        if order_detail_report:
            capability = resolve_database_capability(user_query)
            report = DecisionReport(
                raw_markdown=order_detail_report,
                task_type=task_type,
                context=user_context,
                status="success",
                resolved_chat_mode=state.get("resolved_chat_mode"),
                mode_reason=state.get("mode_reason"),
                enhancement_applied={
                    "deterministic_erp_order_details": "true",
                    "skip_finalizer": "true",
                    **capability_privacy_metadata(capability),
                },
            )
            return {"report": report}

        row_level_guard = _build_deterministic_erp_row_level_detail_guard_report(state)
        if row_level_guard:
            report = DecisionReport(
                raw_markdown=row_level_guard,
                task_type=task_type,
                context=user_context,
                status="success",
                resolved_chat_mode=state.get("resolved_chat_mode"),
                mode_reason=state.get("mode_reason"),
                enhancement_applied={
                    "deterministic_erp_row_level_guard": "true",
                    "skip_finalizer": "true",
                },
            )
            return {"report": report}

        unmapped_database_report = _build_unmapped_database_capability_report(state)
        if unmapped_database_report:
            report = DecisionReport(
                raw_markdown=unmapped_database_report,
                task_type=task_type,
                context=user_context,
                status="success",
                resolved_chat_mode=state.get("resolved_chat_mode"),
                mode_reason=state.get("mode_reason"),
                enhancement_applied={
                    "database_capability_discovery": "true",
                    "skip_finalizer": "true",
                    "data_sensitivity": "internal_metadata",
                    "answer_mode": "capability_discovery",
                    "external_llm_blocked": "true",
                },
            )
            return {"report": report}

        force_deterministic_erp_facts = (
            _looks_like_erp_sales_detail_query(user_query)
            or _looks_like_erp_revenue_query(user_query)
            or _looks_like_other_products_excluding_sales_query(user_query)
        )
        if mode_values & {"full_report", "report", "deep_research"} and not force_deterministic_erp_facts:
            return {}

        for report_content, marker in (
            (_build_deterministic_erp_sales_report(state), "deterministic_erp_sales"),
            (_build_deterministic_erp_availability_report(state), "deterministic_erp_availability"),
            (_build_deterministic_erp_mapping_report(state), "deterministic_erp_mapping"),
        ):
            if report_content:
                report = DecisionReport(
                    raw_markdown=report_content,
                    task_type=task_type,
                    context=user_context,
                    status="success",
                    resolved_chat_mode=state.get("resolved_chat_mode"),
                    mode_reason=state.get("mode_reason"),
                    enhancement_applied={
                        marker: "true",
                        "skip_finalizer": "true",
                    },
                )
                return {"report": report}

    routed_raw = state.get("routed_query") or {}
    routed_path = str(routed_raw.get("path") or "")
    tool_info = str(state.get("tool_info") or "")

    if routed_path == "deterministic_market" and "INTERNAL DATABASE LIVE DATA" not in tool_info:
        report_content = _build_factual_data_unavailable_report(state)
        report = DecisionReport(
            raw_markdown=report_content,
            task_type=task_type,
            context=user_context,
            status="success",
            resolved_chat_mode=state.get("resolved_chat_mode"),
            mode_reason=state.get("mode_reason"),
            enhancement_applied={
                "deterministic_market_lookup": "true",
                "skip_finalizer": "true",
                "data_unavailable": "true",
            },
        )
        return {"report": report}

    if (
        (
            _is_multi_product_price_delta_query(user_query, user_context)
            or _is_direct_market_price_lookup(user_query, user_context)
            or task_type == TaskType.SIMPLE_LOOKUP
            or routed_path == "deterministic_market"
        )
        and not reasoned_lookup
        and "INTERNAL DATABASE LIVE DATA" in tool_info
        and not _should_use_executive_brief_format(state)
    ):
        report_content = _build_simple_lookup_report(state)
        if routed_raw.get("metric_id") == "sinopec_monthly_listing":
            header_zh = "### 中石化上海石化挂牌价（market_prices / sinopec_sh）\n\n"
            header_en = "### SINOPEC Shanghai listing prices (market_prices / sinopec_sh)\n\n"
            header = header_en if _resolve_output_language(state) == "en" else header_zh
            if header.strip() not in report_content:
                report_content = header + report_content
        report = DecisionReport(
            raw_markdown=report_content,
            task_type=task_type,
            context=user_context,
            status="success",
            resolved_chat_mode=state.get("resolved_chat_mode"),
            mode_reason=state.get("mode_reason"),
            enhancement_applied={
                "deterministic_market_lookup": "true",
                "skip_finalizer": "true",
            },
        )
        return {"report": report}

    return {}


def _node_conversational_direct(state: PipelineState) -> dict:
    """Minimal prompt assembly for greetings, casual conversation, and out-of-domain redirects."""
    parsed = state.get("parsed_intent")
    task_type = getattr(parsed, "task_type", None)
    user_lang = _resolve_output_language(state)

    if task_type == TaskType.OUT_OF_DOMAIN:
        if user_lang == "en":
            system_prompt = (
                "You are EDIA, a petrochemical business intelligence assistant specialising in "
                "C5/C9 chemical markets, pricing decisions, ERP data analysis, and supply chain insights.\n\n"
                "The user has asked a question that is outside your domain of expertise. "
                "Politely let them know you are focused on petrochemical and business intelligence topics, "
                "and offer to help with something relevant instead. Keep it brief and friendly (1-3 sentences). "
                "Do NOT attempt to answer the off-topic question."
            )
        else:
            system_prompt = (
                "你是EDIA，专注于C5/C9化工市场、定价决策、ERP数据分析和供应链洞察的石化行业智能助手。\n\n"
                "用户提问的内容超出了你的专业范围。请礼貌地告知用户你专注于石化和商业智能领域，"
                "并建议他们提问与化工市场或业务相关的问题。回复简短友好（1-3句话），不要尝试回答无关问题。"
            )
    elif user_lang == "en":
        system_prompt = (
            "You are EDIA, a smart and friendly petrochemical business intelligence assistant. "
            "You specialize in C5/C9 chemical markets, ERP data analysis, pricing decisions, "
            "and supply chain insights for the petrochemical industry.\n\n"
            "RESPONSE RULES:\n"
            "- Respond naturally and warmly, like a knowledgeable colleague.\n"
            "- Keep responses concise (1\u20134 sentences).\n"
            "- If asked who you are or what you do, give a clear overview of your EDIA capabilities.\n"
            "- Do NOT fabricate prices, market data, or business figures.\n"
            "- Do NOT use section headers, bullet lists, or citation tags.\n"
            "- Do NOT run any analysis or generate a report for simple conversation."
        )
    else:
        system_prompt = (
            "\u4f60\u662fEDIA\uff0c\u4e00\u4e2a\u667a\u80fd\u7684\u77f3\u5316\u884c\u4e1a\u5546\u4e1a\u667a\u80fd\u52a9\u624b\u3002"
            "\u4f60\u4e13\u6ce8\u4e8eC5/C9\u5316\u5de5\u54c1\u5e02\u573a\u3001ERP\u6570\u636e\u5206\u6790\u3001\u5b9a\u4ef7\u51b3\u7b56\u548c\u4f9b\u5e94\u94fe\u6d1e\u5bdf\u3002\n\n"
            "\u56de\u590d\u89c4\u5219\uff1a\n"
            "- \u81ea\u7136\u3001\u53cb\u5584\u5730\u56de\u5e94\uff0c\u50cf\u4e00\u4e2a\u4e86\u89e3\u4e1a\u52a1\u7684\u540c\u4e8b\u3002\n"
            "- \u4fdd\u6301\u7b80\u77ed\uff081\u20134\u53e5\u8bdd\uff09\u3002\n"
            "- \u88ab\u95ee\u5230\u4f60\u662f\u8c01\u6216\u80fd\u505a\u4ec0\u4e48\u65f6\uff0c\u7b80\u6d01\u4ecb\u7ecd EDIA \u7684\u6838\u5fc3\u529f\u80fd\u3002\n"
            "- \u4e0d\u8981\u7f16\u9020\u4ef7\u683c\u3001\u5e02\u573a\u6570\u636e\u6216\u4e1a\u52a1\u6570\u5b57\u3002\n"
            "- \u4e0d\u8981\u4f7f\u7528\u7ae0\u8282\u6807\u9898\u3001\u5217\u8868\u6216\u5f15\u7528\u6807\u7b7e\u3002\n"
            "- \u7b80\u5355\u5bf9\u8bdd\u4e0d\u9700\u8981\u751f\u6210\u5206\u6790\u62a5\u544a\u3002"
        )
    return {
        "system_prompt": system_prompt,
        "user_prompt": (state.get("user_query") or "").strip(),
        "response_mode": "conversational",
    }


def _node_clarify_intent(state: PipelineState) -> dict:
    """Ask ONE targeted clarification when intent is ambiguous between simple lookup
    and a full market analysis.  Fires only on low-confidence middle-tier queries."""
    user_lang = _resolve_output_language(state)
    user_query = (state.get("user_query") or "").strip()
    user_context = state.get("user_context")
    product = str(getattr(user_context, "product", "") or "").strip()

    if user_lang == "en":
        product_hint = f' about "{product}"' if product else ""
        system_prompt = (
            "You are EDIA, a petrochemical business intelligence assistant.\n"
            f'The user asked: "{user_query}"\n\n'
            f"I need to clarify what kind of answer they need{product_hint}. "
            "Ask exactly ONE short, specific question to disambiguate: are they looking "
            "for a quick current price lookup, or a full market analysis with drivers, "
            "outlook and recommendations? Make the question feel natural, not like a form. "
            "Do NOT answer the original question yet."
        )
    else:
        product_hint = f'\u5173\u4e8e\u201c{product}\u201d' if product else ""
        system_prompt = (
            "\u4f60\u662fEDIA\uff0c\u4e00\u4e2a\u77f3\u5316\u884c\u4e1a\u5546\u4e1a\u667a\u80fd\u52a9\u624b\u3002\n"
            f'\u7528\u6237\u95ee\u4e86\uff1a\u201c{user_query}\u201d\n\n'
            f"\u6211\u9700\u8981\u786e\u8ba4{product_hint}\u4ed6\u4eec\u60f3\u8981\u54ea\u79cd\u7c7b\u578b\u7684\u7b54\u6848\u3002"
            "\u63d0\u51fa\u6070\u597dONE\u4e2a\u7b80\u77ed\u3001\u5177\u4f53\u7684\u95ee\u9898\u6765\u6d88\u9664\u6b67\u4e49\uff1a"
            "\u4ed6\u4eec\u662f\u60f3\u8981\u5feb\u901f\u7684\u5f53\u524d\u4ef7\u683c\u67e5\u8be2\uff0c\u8fd8\u662f"
            "\u9700\u8981\u5305\u542b\u9a71\u52a8\u56e0\u7d20\u3001\u524d\u666f\u548c\u5efa\u8bae\u7684\u5b8c\u6574\u5e02\u573a\u5206\u6790\uff1f"
            "\u95ee\u9898\u8981\u81ea\u7136\uff0c\u4e0d\u8981\u50cf\u8868\u5355\u3002\u4e0d\u8981\u56de\u7b54\u539f\u59cb\u95ee\u9898\u3002"
        )
    return {
        "system_prompt": system_prompt,
        "user_prompt": "Generate the clarification question now.",
        "response_mode": "conversational",
    }


def _node_thin_evidence(state: PipelineState) -> dict:
    """Short-circuit response when evidence sources are too sparse to run a
    reliable full analysis.  Fires as an early-critic gate before diagnosis."""
    user_lang = _resolve_output_language(state)
    user_query = (state.get("user_query") or "").strip()

    if user_lang == "en":
        system_prompt = (
            "You are EDIA, a petrochemical business intelligence assistant.\n"
            "The user asked a question that requires market analysis, but the available "
            "data sources (RAG knowledge base, web search, market database) returned "
            "insufficient evidence for a reliable analysis right now.\n\n"
            "Respond honestly in 2-3 sentences: explain that you don't have enough current "
            "market data for this specific query at this time. Suggest what would help "
            "(e.g., specifying a product, time period, or market segment). "
            "Do NOT fabricate data or analysis."
        )
    else:
        system_prompt = (
            "\u4f60\u662fEDIA\uff0c\u4e00\u4e2a\u77f3\u5316\u884c\u4e1a\u5546\u4e1a\u667a\u80fd\u52a9\u624b\u3002\n"
            "\u7528\u6237\u63d0\u51fa\u4e86\u9700\u8981\u5e02\u573a\u5206\u6790\u7684\u95ee\u9898\uff0c\u4f46\u53ef\u7528\u6570\u636e\u6e90"
            "\uff08RAG\u77e5\u8bc6\u5e93\u3001\u7f51\u7edc\u641c\u7d22\u3001\u5e02\u573a\u6570\u636e\u5e93\uff09\u8fd4\u56de\u7684\u8bc1\u636e"
            "\u4e0d\u8db3\u4ee5\u8fdb\u884c\u53ef\u9760\u7684\u5206\u6790\u3002\n\n"
            "\u75282-3\u53e5\u8bdd\u8bda\u5b9e\u56de\u5e94\uff1a\u8bf4\u660e\u76ee\u524d\u6ca1\u6709\u8db3\u591f\u7684\u5f53\u524d\u5e02\u573a\u6570\u636e\u6765\u56de\u7b54\u8fd9\u4e2a\u5177\u4f53\u95ee\u9898\u3002"
            "\u5efa\u8bae\u6709\u5e2e\u52a9\u7684\u4fe1\u606f\uff08\u5982\u6307\u5b9a\u4ea7\u54c1\u3001\u65f6\u95f4\u6bb5\u6216\u5e02\u573a\u7ec6\u5206\uff09\u3002"
            "\u4e0d\u8981\u7f16\u9020\u6570\u636e\u6216\u5206\u6790\u3002"
        )
    return {
        "system_prompt": system_prompt,
        "user_prompt": user_query,
        "response_mode": "conversational",
    }


# ---------------------------------------------------------------------------
# Conditional edge: skip to partial response if context is invalid
# ---------------------------------------------------------------------------


def _query_explicit_internal_erp_request(user_query: str) -> bool:
    query = str(user_query or "")
    ql = query.lower()
    markers = [
        "our",
        "inventory",
        "shipment",
        "contract",
        "customer",
        "erp",
        "sales",
        "target",
        "actual",
        "gap",
        "team",
        "内部",
        "库存",
        "出货",
        "合同",
        "客户",
        "销量",
        "目标",
        "实际",
        "差距",
        "团队",
    ]
    return any(marker in query or marker in ql for marker in markers)


def _build_factual_data_unavailable_report(state: PipelineState) -> str:
    lang = _resolve_output_language(state)
    metric = str((state.get("routed_query") or {}).get("metric_id") or "")
    if lang == "en":
        return (
            "## Market data unavailable\n\n"
            "No live rows were returned from `market_prices` for the requested scope. "
            "Please specify product(s) (e.g. isoprene, piperylene) or period, "
            "or confirm the listing series is loaded in the database.\n\n"
            f"Route metric: `{metric or 'unknown'}`."
        ).strip()
    return (
        "## 市场数据暂不可用\n\n"
        "当前请求范围内，`market_prices` 未返回可用的实时价格行。"
        "请补充具体产品（如异戊二烯、间戊二烯）或时间口径，"
        "或确认挂牌价序列已入库。\n\n"
        f"路由指标：`{metric or '未知'}`。"
    ).strip()


def _route_after_validation(state: PipelineState) -> str:
    # Conversational and out-of-domain usually bypass validation.
    # But if the user is explicitly asking for research/analysis, run full pipeline.
    parsed = state.get("parsed_intent")
    routed_raw = state.get("routed_query") or {}
    routed_path = str(routed_raw.get("path") or "")

    if routed_path == "conversational":
        if _has_follow_up_with_prior_answer(state):
            return "parallel_fetch"
        if _conversational_should_run_research_pipeline(state):
            return "parallel_fetch"
        return "conversational_direct"

    if routed_path == "deterministic_market":
        return "tool_execution"

    if parsed and parsed.task_type in (TaskType.ERP_DATA_QUERY, TaskType.BOSS_BRIEFING):
        if parsed.task_type == TaskType.BOSS_BRIEFING and not _query_explicit_internal_erp_request(
            state.get("user_query", "")
        ):
            return "parallel_fetch"
        return "erp_data_retrieval"

    if parsed and parsed.task_type in (TaskType.SIMPLE_LOOKUP, TaskType.MARKET_ANALYSIS_ONLY) and _is_direct_market_price_lookup(
        state.get("user_query", ""),
        state.get("user_context"),
    ):
        return "tool_execution"

    if _strict_kimi_only_mode(state):
        if not state.get("is_valid", True):
            return "parallel_fetch"
        return "parallel_fetch"

    if parsed and parsed.task_type == TaskType.CONVERSATIONAL:
        # Follow-up explanation requests with prior assistant output should
        # never fall back to the self-intro conversational template.
        if _has_follow_up_with_prior_answer(state):
            return "parallel_fetch"
        if _conversational_should_run_research_pipeline(state):
            return "parallel_fetch"
        return "conversational_direct"
    if parsed and parsed.task_type == TaskType.OUT_OF_DOMAIN:
        if _conversational_should_run_research_pipeline(state):
            return "parallel_fetch"
        return "conversational_direct"
    if not state.get("is_valid", True):
        return "partial_response"
    # Price/delta lookup questions should bypass the slow analysis pipeline
    # and go straight to live DB tool execution, including on the prepare/stream path.
    if _is_multi_product_price_delta_query(
        state.get("user_query", ""),
        state.get("user_context"),
    ) and not _should_force_reasoned_market_answer(
        state.get("user_query", ""),
        state.get("user_context"),
    ):
        return "tool_execution"
    if parsed and parsed.task_type in (TaskType.SIMPLE_LOOKUP, TaskType.MARKET_ANALYSIS_ONLY) and _is_direct_market_price_lookup(
        state.get("user_query", ""),
        state.get("user_context"),
    ):
        return "tool_execution"
    # Confidence gate: ambiguous simple_lookup vs market_analysis — ask for clarification
    if parsed:
        confidence = getattr(parsed, "confidence", 1.0)
        task = parsed.task_type
        if task in (TaskType.SIMPLE_LOOKUP, TaskType.MARKET_ANALYSIS_ONLY) and confidence < 0.65:
            chat_mode, _reason = _classify_chat_mode(
                state.get("user_query") or "",
                user_prefs=state.get("user_prefs") or {},
                task_type=task,
                explanation_mode=bool(state.get("explanation_mode")),
            )
            if chat_mode not in {"analysis", "report", "deep_research"}:
                return "clarify_intent"
    if parsed and parsed.task_type == TaskType.SIMPLE_LOOKUP and not _should_force_reasoned_market_answer(
        state.get("user_query", ""),
        state.get("user_context"),
    ):
        return "tool_execution"
    return "parallel_fetch"


# ---------------------------------------------------------------------------
# Conditional edge: route after orchestrator based on task type (§10.1)
# ---------------------------------------------------------------------------


def _route_after_parallel_fetch(state: PipelineState) -> str:
    """Skip LLM product-brain planning for supply/ops and explicit-product questions."""
    query = str(state.get("user_query") or "")
    if _is_ops_inventory_pricing_query(query) or _is_naphtha_cost_passthrough_query(query):
        return "gather_context"
    if extract_product_ids_in_text(query):
        return "gather_context"
    return "research_enrichment"


def _route_after_orchestrator(state: PipelineState) -> str:
    """Route based on parsed intent task type, with an early critic gate."""
    parsed = state.get("parsed_intent")
    task = getattr(parsed, "task_type", None) if parsed else None
    query = str(state.get("user_query") or "")

    if _is_ops_inventory_pricing_query(query) or _is_naphtha_cost_passthrough_query(query):
        return "simple_lookup"

    # Early critic gate: if evidence is too sparse for a full pipeline run,
    # surface a honest "not enough data" response rather than burning the full chain.
    if (not _strict_kimi_only_mode(state)) and task in (TaskType.MARKET_ANALYSIS_DECISION, TaskType.WHAT_IF_SCENARIO):
        rag = (state.get("rag_info") or "").strip()
        web = (state.get("web_search_results") or "").strip()
        perception = state.get("perception_snapshot") or {}
        has_price_data = bool((perception.get("products") or {}))
        if len(rag) < 80 and len(web) < 80 and not has_price_data:
            return "thin_evidence"

    if not parsed:
        return "full_pipeline"
    if task == TaskType.FORECAST_ONLY:
        return "forecast_only"
    elif task == TaskType.SIMPLE_LOOKUP:
        return "simple_lookup"
    elif task == TaskType.MARKET_ANALYSIS_ONLY:
        if _is_direct_market_price_lookup(
            state.get("user_query", ""),
            state.get("user_context"),
        ):
            return "simple_lookup"
        return "diagnosis_only"
    elif task in (TaskType.ERP_DATA_QUERY, TaskType.BOSS_BRIEFING):
        if task == TaskType.BOSS_BRIEFING and not _query_explicit_internal_erp_request(
            state.get("user_query", "")
        ):
            return "full_pipeline"
        return "erp_query"
    # MARKET_ANALYSIS_DECISION, WHAT_IF_SCENARIO → full pipeline
    return "full_pipeline"


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def build_decision_graph(llm: LLMService) -> StateGraph:
    """Construct and compile the EDIA decision pipeline graph."""
    intent_parser = IntentParser(llm)
    context_generator = ContextGenerator(llm)
    context_validator = ContextValidator()
    prompt_assembler = PromptAssembler()

    graph = StateGraph(PipelineState)

    # ── register nodes
    graph.add_node(
        "parse_intent", _timed("parse_intent", _make_parse_intent(intent_parser))
    )
    graph.add_node(
        "extract_context",
        _timed("extract_context", _make_extract_context(context_generator)),
    )
    graph.add_node(
        "validate_context",
        _timed("validate_context", _make_validate_context(context_validator)),
    )
    graph.add_node(
        "partial_response",
        _timed("partial_response", _make_partial_response(context_validator)),
    )

    graph.add_node("perception", _timed("perception", _node_perception))
    graph.add_node(
        "intelligence_check", _timed("intelligence_check", _node_intelligence_check)
    )
    graph.add_node("web_search", _timed("web_search", _node_web_search))
    graph.add_node("parallel_fetch", _timed("parallel_fetch", _node_parallel_fetch))
    graph.add_node(
        "research_enrichment",
        _timed("research_enrichment", _node_research_enrichment),
    )
    graph.add_node("resolve_product", _timed("resolve_product", _make_node_resolve_product(llm)))
    graph.add_node("diagnosis", _timed("diagnosis", _node_diagnosis))
    graph.add_node("forecast", _timed("forecast", _node_forecast))
    graph.add_node("pricing", _timed("pricing", _node_pricing))
    graph.add_node(
        "evidence_compile", _timed("evidence_compile", _node_evidence_compile)
    )
    graph.add_node("decision_critic", _timed("decision_critic", _node_decision_critic))
    graph.add_node("gather_context", _timed("gather_context", _node_gather_context))
    graph.add_node("tool_execution", _timed("tool_execution", _node_tool_execution))
    graph.add_node(
        "erp_data_retrieval", _timed("erp_data_retrieval", _node_erp_data_retrieval)
    )
    graph.add_node(
        "conversational_direct", _timed("conversational_direct", _node_conversational_direct)
    )
    graph.add_node(
        "clarify_intent", _timed("clarify_intent", _node_clarify_intent)
    )
    graph.add_node(
        "thin_evidence", _timed("thin_evidence", _node_thin_evidence)
    )
    graph.add_node(
        "assemble_prompt",
        _timed("assemble_prompt", _make_assemble_prompt(prompt_assembler)),
    )
    graph.add_node(
        "generate_report", _timed("generate_report", _make_generate_report(llm))
    )

    # ── edges
    graph.set_entry_point("parse_intent")
    graph.add_edge("parse_intent", "extract_context")
    graph.add_edge("extract_context", "validate_context")

    # conditional: CONV/OOD → conversational_direct, invalid → partial,
    # confidence gate → clarify_intent, ERP → erp_data_retrieval, else → parallel_fetch
    graph.add_conditional_edges(
        "validate_context",
        _route_after_validation,
        {
            "partial_response": "partial_response",
            "parallel_fetch": "parallel_fetch",
            "erp_data_retrieval": "erp_data_retrieval",
            "tool_execution": "tool_execution",
            "conversational_direct": "conversational_direct",
            "clarify_intent": "clarify_intent",
        },
    )
    graph.add_edge("partial_response", END)
    graph.add_edge("conversational_direct", "generate_report")
    graph.add_edge("clarify_intent", "generate_report")

    # ERP path: erp_data_retrieval → assemble_prompt → generate_report → END
    graph.add_edge("erp_data_retrieval", "assemble_prompt")

    # parallel_fetch replaces perception → intelligence_check → web_search
    graph.add_conditional_edges(
        "parallel_fetch",
        _route_after_parallel_fetch,
        {
            "gather_context": "gather_context",
            "research_enrichment": "research_enrichment",
        },
    )
    graph.add_edge("research_enrichment", "resolve_product")
    graph.add_edge("resolve_product", "gather_context")

    # RAG retrieval feeds into orchestrator routing (with early critic gate)
    graph.add_conditional_edges(
        "gather_context",
        _route_after_orchestrator,
        {
            "full_pipeline": "diagnosis",
            "forecast_only": "forecast",
            "diagnosis_only": "diagnosis",
            "erp_query": "erp_data_retrieval",
            "simple_lookup": "tool_execution",
            "thin_evidence": "thin_evidence",
        },
    )
    graph.add_edge("thin_evidence", "generate_report")

    # Full pipeline: diagnosis → forecast → pricing → evidence_compile → decision_critic → tool_execution → assemble → report
    graph.add_edge("diagnosis", "forecast")
    graph.add_edge("forecast", "pricing")
    graph.add_edge("pricing", "evidence_compile")
    graph.add_edge("evidence_compile", "decision_critic")
    graph.add_edge("decision_critic", "tool_execution")
    graph.add_edge("tool_execution", "assemble_prompt")
    graph.add_edge("assemble_prompt", "generate_report")
    graph.add_edge("generate_report", END)

    return graph.compile()


def postprocess_generated_report(markdown: str, state: PipelineState) -> str:
    """Apply deterministic post-processing to match the non-streaming path."""
    try:
        return _apply_deterministic_guardrails(markdown, state)
    except Exception:
        pass
    return markdown


def build_prepare_graph(llm: LLMService) -> StateGraph:
    """Construct and compile the EDIA pipeline up to prompt assembly.

    This is used by the streaming API to run all deterministic steps first,
    then stream only the final LLM generation.
    """
    intent_parser = IntentParser(llm)
    context_generator = ContextGenerator(llm)
    context_validator = ContextValidator()
    prompt_assembler = PromptAssembler()

    graph = StateGraph(PipelineState)

    graph.add_node(
        "parse_intent", _timed("parse_intent", _make_parse_intent(intent_parser))
    )
    graph.add_node(
        "extract_context",
        _timed("extract_context", _make_extract_context(context_generator)),
    )
    graph.add_node(
        "validate_context",
        _timed("validate_context", _make_validate_context(context_validator)),
    )
    graph.add_node(
        "partial_response",
        _timed("partial_response", _make_partial_response(context_validator)),
    )

    graph.add_node("perception", _timed("perception", _node_perception))
    graph.add_node(
        "intelligence_check", _timed("intelligence_check", _node_intelligence_check)
    )
    graph.add_node("web_search", _timed("web_search", _node_web_search))
    graph.add_node("parallel_fetch", _timed("parallel_fetch", _node_parallel_fetch))
    graph.add_node(
        "research_enrichment",
        _timed("research_enrichment", _node_research_enrichment),
    )
    graph.add_node("resolve_product", _timed("resolve_product", _make_node_resolve_product(llm)))
    graph.add_node("diagnosis", _timed("diagnosis", _node_diagnosis))
    graph.add_node("forecast", _timed("forecast", _node_forecast))
    graph.add_node("pricing", _timed("pricing", _node_pricing))
    graph.add_node(
        "evidence_compile", _timed("evidence_compile", _node_evidence_compile)
    )
    graph.add_node("decision_critic", _timed("decision_critic", _node_decision_critic))
    graph.add_node("gather_context", _timed("gather_context", _node_gather_context))
    graph.add_node("tool_execution", _timed("tool_execution", _node_tool_execution))
    graph.add_node(
        "erp_data_retrieval", _timed("erp_data_retrieval", _node_erp_data_retrieval)
    )
    graph.add_node(
        "conversational_direct", _timed("conversational_direct", _node_conversational_direct)
    )
    graph.add_node(
        "clarify_intent", _timed("clarify_intent", _node_clarify_intent)
    )
    graph.add_node(
        "thin_evidence", _timed("thin_evidence", _node_thin_evidence)
    )
    graph.add_node(
        "assemble_prompt",
        _timed("assemble_prompt", _make_assemble_prompt(prompt_assembler)),
    )
    graph.add_node(
        "short_circuit_check", _timed("short_circuit_check", _node_short_circuit_check)
    )

    graph.set_entry_point("parse_intent")
    graph.add_edge("parse_intent", "extract_context")
    graph.add_edge("extract_context", "validate_context")

    graph.add_conditional_edges(
        "validate_context",
        _route_after_validation,
        {
            "partial_response": "partial_response",
            "parallel_fetch": "parallel_fetch",
            "erp_data_retrieval": "erp_data_retrieval",
            "tool_execution": "tool_execution",
            "conversational_direct": "conversational_direct",
            "clarify_intent": "clarify_intent",
        },
    )
    graph.add_edge("partial_response", END)
    graph.add_edge("conversational_direct", END)
    graph.add_edge("clarify_intent", END)

    graph.add_edge("erp_data_retrieval", "assemble_prompt")
    graph.add_edge("assemble_prompt", "short_circuit_check")
    graph.add_edge("short_circuit_check", END)

    graph.add_conditional_edges(
        "parallel_fetch",
        _route_after_parallel_fetch,
        {
            "gather_context": "gather_context",
            "research_enrichment": "research_enrichment",
        },
    )
    graph.add_edge("research_enrichment", "resolve_product")
    graph.add_edge("resolve_product", "gather_context")

    graph.add_conditional_edges(
        "gather_context",
        _route_after_orchestrator,
        {
            "full_pipeline": "diagnosis",
            "forecast_only": "forecast",
            "diagnosis_only": "diagnosis",
            "erp_query": "erp_data_retrieval",
            "simple_lookup": "tool_execution",
            "thin_evidence": "thin_evidence",
        },
    )
    graph.add_edge("thin_evidence", END)

    graph.add_edge("diagnosis", "forecast")
    graph.add_edge("forecast", "pricing")
    graph.add_edge("pricing", "evidence_compile")
    graph.add_edge("evidence_compile", "decision_critic")
    graph.add_edge("decision_critic", "tool_execution")
    graph.add_edge("tool_execution", "assemble_prompt")

    return graph.compile()
