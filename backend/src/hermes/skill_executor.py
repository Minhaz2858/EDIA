"""Execute EDIA skills registered in skill_registry."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from backend.src.hermes.skill_registry import EdiaSkill, get_skill, iter_skills, route_skills


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _period_from_plan(plan: Dict[str, Any], user_query: str = "") -> str:
    normalized = _as_dict(plan.get("normalized_query"))
    start = str(normalized.get("period_start") or "").strip()
    end = str(normalized.get("period_end") or "").strip()
    if start and end:
        return f"{start} to {end}"
    return str(normalized.get("period") or user_query or plan.get("user_query") or "").strip()


def _product_id_from_plan(plan: Dict[str, Any]) -> str:
    normalized = _as_dict(plan.get("normalized_query"))
    product_ids = _as_list(normalized.get("product_ids")) or _as_list(plan.get("product_ids"))
    if product_ids:
        return str(product_ids[0])
    return ""


def execute_skill(skill_id: str, plan: Dict[str, Any], *, user_query: str = "") -> Dict[str, Any]:
    """Run a single skill handler and return observation payload."""
    skill = get_skill(skill_id)
    if skill is None:
        return {
            "skill_id": skill_id,
            "status": "error",
            "summary": f"Unknown skill: {skill_id}",
            "payload": {},
        }

    context = {
        "plan": plan,
        "user_query": user_query or str(plan.get("user_query") or ""),
        "product_id": _product_id_from_plan(plan),
        "period": _period_from_plan(plan, user_query=user_query),
        "session_id": str(plan.get("session_id") or ""),
        "language": str(plan.get("language") or "zh"),
    }

    if skill.handler is not None:
        try:
            payload = skill.handler(context, plan)
            status = str(payload.get("status") or "ok")
            return {
                "skill_id": skill_id,
                "status": status,
                "summary": str(payload.get("summary") or skill.description),
                "payload": payload,
                "tool": skill.tool_name or skill_id,
            }
        except Exception as exc:
            return {
                "skill_id": skill_id,
                "status": "error",
                "summary": f"Skill {skill_id} failed: {exc}",
                "payload": {},
                "tool": skill.tool_name or skill_id,
            }

    excerpt = skill.load_instruction_excerpt()
    if excerpt or skill.instruction_excerpt:
        return {
            "skill_id": skill_id,
            "status": "ok",
            "summary": f"Loaded instructions for {skill_id}.",
            "payload": {
                "instruction_excerpt": excerpt or skill.instruction_excerpt,
                "category": skill.category,
            },
            "tool": "agent_skill_router",
        }

    return {
        "skill_id": skill_id,
        "status": "skipped",
        "summary": f"No executable handler for {skill_id}.",
        "payload": {},
        "tool": skill.tool_name or skill_id,
    }


def execute_routed_skills(
    plan: Dict[str, Any],
    *,
    user_query: str = "",
    skill_ids: List[str] | None = None,
) -> Dict[str, Any]:
    """Execute all routed skills and return combined trace."""
    plan = _as_dict(plan)
    query = user_query or str(plan.get("user_query") or "")
    domains = [str(d) for d in _as_list(plan.get("domains")) if d]
    routed = skill_ids or _as_list(plan.get("agent_skills")) or route_skills(query, domains)

    # Map legacy agent skill names to registry IDs where needed
    legacy_map = {
        "edia-market-research": "edia-market-research",
        "docx": "docx",
        "minimax-docx": "minimax-docx",
        "presentation-creator": "presentation-creator",
        "html-ppt": "html-ppt",
        "pptx-generator": "pptx-generator",
        "beautiful-mermaid": "beautiful-mermaid",
        "mysql": "mysql",
        "vision-analysis": None,
        "markdown-converter": None,
        "frontend-design": None,
        "frontend-dev": None,
    }

    executable_ids: list[str] = []
    for item in routed:
        mapped = legacy_map.get(item, item)
        if mapped and get_skill(mapped):
            if mapped not in executable_ids:
                executable_ids.append(mapped)
        elif get_skill(item) and item not in executable_ids:
            executable_ids.append(item)

    # Always add domain-default executable skills
    if "forecast" in domains and "forecast-run" not in executable_ids:
        if any(marker in query for marker in ("预测", "forecast", "走势", "outlook")):
            executable_ids.append("forecast-run")
    if any(marker in query for marker in ("图表", "chart", "走势", "plot", "可视化")):
        if "chart-render" not in executable_ids:
            executable_ids.append("chart-render")
    if any(marker in query for marker in ("销售额", "销售金额", "sales revenue", "revenue")):
        if "erp-revenue-guardrail" not in executable_ids:
            executable_ids.append("erp-revenue-guardrail")

    observations: list[dict[str, Any]] = []
    for skill_id in executable_ids:
        skill = get_skill(skill_id)
        if skill is None or skill.category == "guardrail":
            continue
        observations.append(execute_skill(skill_id, plan, user_query=query))

    instructions: list[dict[str, str]] = []
    for skill_id in executable_ids:
        skill = get_skill(skill_id)
        if skill and (skill.skill_md_path or skill.instruction_excerpt) and skill.category != "guardrail":
            excerpt = skill.load_instruction_excerpt() or skill.instruction_excerpt
            if excerpt:
                instructions.append({"skill_id": skill_id, "excerpt": excerpt[:2000]})

    return {
        "routed_skills": routed,
        "executed_skills": [obs.get("skill_id") for obs in observations],
        "observations": observations,
        "instruction_blocks": instructions,
    }


def format_skill_observations_for_prompt(execution: Dict[str, Any]) -> str:
    observations = _as_list(execution.get("observations"))
    if not observations:
        return ""

    lines = [
        "HERMES SKILL OBSERVATIONS:",
        "Use verified skill outputs below. Do not invent numbers or chart values.",
    ]
    for index, obs in enumerate(observations, start=1):
        if not isinstance(obs, dict):
            continue
        lines.append(
            f"{index}. skill={obs.get('skill_id')}; tool={obs.get('tool')}; "
            f"status={obs.get('status')}; summary={obs.get('summary')}"
        )
        payload = _as_dict(obs.get("payload"))
        if payload:
            compact = json.dumps(payload, ensure_ascii=False, default=str)
            if len(compact) > 4000:
                compact = compact[:3997] + "..."
            lines.append(f"   payload={compact}")
    return "\n".join(lines)
