from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


_VALID_INTENTS = {
    "commercial_ops_advisory",
    "market_analysis",
    "erp_data_query",
    "database_catalog",
    "forecast",
    "document_generation",
    "presentation_generation",
    "frontend_task",
    "conversation",
    "unknown",
}


@dataclass(frozen=True)
class AgentIntent:
    intent: str = "unknown"
    confidence: float = 0.0
    product_ids: List[str] = field(default_factory=list)
    domains: List[str] = field(default_factory=list)
    capabilities: List[str] = field(default_factory=list)
    data_required: List[str] = field(default_factory=list)
    answer_style: str = "general_business_answer"
    should_not_use: List[str] = field(default_factory=list)
    privacy: Dict[str, str] = field(default_factory=dict)
    route_reason: str = ""
    source: str = "fallback"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "confidence": round(float(self.confidence or 0.0), 4),
            "product_ids": list(self.product_ids),
            "domains": list(self.domains),
            "capabilities": list(self.capabilities),
            "data_required": list(self.data_required),
            "answer_style": self.answer_style,
            "should_not_use": list(self.should_not_use),
            "privacy": dict(self.privacy),
            "route_reason": self.route_reason,
            "source": self.source,
        }


def _clean_string_list(value: Any, *, allowed: Optional[set[str]] = None) -> List[str]:
    if not isinstance(value, list):
        return []
    cleaned: List[str] = []
    for item in value:
        text = str(item or "").strip()
        if not text:
            continue
        normalized = text.lower()
        if allowed is not None and normalized not in allowed:
            continue
        if normalized not in cleaned:
            cleaned.append(normalized)
    return cleaned


def _coerce_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.0


def _normalize_agent_intent(raw: Any, *, source: str) -> Optional[AgentIntent]:
    if not isinstance(raw, dict):
        return None
    payload = raw.get("agent_intent") if isinstance(raw.get("agent_intent"), dict) else raw
    if not isinstance(payload, dict):
        return None

    intent = str(payload.get("intent") or "unknown").strip().lower()
    if intent not in _VALID_INTENTS:
        intent = "unknown"

    privacy_raw = payload.get("privacy") if isinstance(payload.get("privacy"), dict) else {}
    privacy = {
        str(key): str(value)
        for key, value in dict(privacy_raw or {}).items()
        if str(key).strip() and str(value).strip()
    }

    return AgentIntent(
        intent=intent,
        confidence=_coerce_confidence(payload.get("confidence")),
        product_ids=_clean_string_list(payload.get("product_ids")),
        domains=_clean_string_list(payload.get("domains")),
        capabilities=_clean_string_list(payload.get("capabilities")),
        data_required=_clean_string_list(payload.get("data_required")),
        answer_style=str(payload.get("answer_style") or "general_business_answer").strip()
        or "general_business_answer",
        should_not_use=_clean_string_list(payload.get("should_not_use")),
        privacy=privacy,
        route_reason=str(payload.get("route_reason") or "").strip(),
        source=source,
    )


def build_agent_intent_prompt(user_query: str, product_ids: List[str]) -> tuple[str, str]:
    system_prompt = """
You are EDIA's petrochemical agent router.

Classify the user's business intent by meaning, not by keyword matching. EDIA is an AI
commercial agent for petrochemical decisions. If the user asks what to do about price,
inventory, quotes, sales execution, shipment pace, customer conversion, or commercial
risk for a recognized petrochemical product, classify it as commercial_ops_advisory.

Do not choose database_catalog unless the user is asking about tables, schemas,
available fields, data coverage, or engineering capability registration.

Return valid JSON only with this shape:
{
  "agent_intent": {
    "intent": "commercial_ops_advisory|market_analysis|erp_data_query|database_catalog|forecast|document_generation|presentation_generation|frontend_task|conversation|unknown",
    "confidence": 0.0,
    "product_ids": [],
    "domains": [],
    "capabilities": [],
    "data_required": [],
    "answer_style": "business_memo|erp_table|capability_discovery|general_business_answer",
    "should_not_use": [],
    "privacy": {
      "data_sensitivity": "market_internal_mixed|erp_confidential|internal_metadata|public",
      "external_llm_blocked": "true|false",
      "answer_mode": "hybrid_commercial_ops_advisory|deterministic_first|capability_discovery|general"
    },
    "route_reason": "brief reason"
  }
}
""".strip()
    user_prompt = json.dumps(
        {
            "user_query": str(user_query or "").strip(),
            "recognized_product_ids": list(product_ids or []),
            "routing_policy": {
                "business_advice_must_not_use": ["database_capability_discovery"],
                "verified_numbers_must_come_from": [
                    "market_prices",
                    "erp_v_stk_inventory",
                    "erp_v_sale_orderentry",
                    "pricing_service",
                ],
            },
        },
        ensure_ascii=False,
    )
    return system_prompt, user_prompt


def plan_agent_intent(
    user_query: str,
    *,
    product_ids: Optional[List[str]] = None,
    llm_service: Any = None,
) -> Optional[AgentIntent]:
    if llm_service is None or not hasattr(llm_service, "generate_json"):
        return None
    system_prompt, user_prompt = build_agent_intent_prompt(user_query, product_ids or [])
    try:
        raw = llm_service.generate_json(
            system_prompt,
            user_prompt,
            route_hint="local",
            sanitized=True,
        )
    except Exception:
        return None
    intent = _normalize_agent_intent(raw, source="llm")
    if intent is None or intent.intent == "unknown" or intent.confidence < 0.55:
        return None
    return intent


def normalize_agent_intent(raw: Any, *, source: str = "provided") -> Optional[AgentIntent]:
    return _normalize_agent_intent(raw, source=source)
