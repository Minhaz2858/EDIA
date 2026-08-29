"""EDIA chat route planning (domains, capabilities, privacy) — no Hermes agent."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.src.hermes.hermes_agent import (
    HermesPlan,
    build_hermes_plan,
    resolve_followup_standalone_query,
)

# Public aliases (Hermes agent removed; plan builder retained for routing).
ChatRoutePlan = HermesPlan
build_chat_route_plan = build_hermes_plan


def _conversation_history_to_messages(
    prior_messages: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, str]]:
    history: List[Dict[str, str]] = []
    for item in prior_messages or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or item.get("markdown") or "").strip()
        if role in {"user", "assistant"} and content:
            history.append({"role": role, "content": content})
    return history


def is_history_dependent_followup(
    message: str,
    prior_messages: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    followup = resolve_followup_standalone_query(
        message,
        conversation_history=_conversation_history_to_messages(prior_messages),
    )
    return bool(followup.get("is_follow_up"))


def build_route_plan_metadata(message: str, *, llm_service: Any = None) -> Dict[str, Any]:
    try:
        return build_chat_route_plan(message, llm_service=llm_service).to_dict()
    except Exception:
        return {}
