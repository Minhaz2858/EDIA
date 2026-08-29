"""ERP report building, hybrid local synthesis, and access-denial guards."""

from __future__ import annotations

import logging
import os
import re
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterator, Optional

logger = logging.getLogger(__name__)

_ERP_ACCESS_DENIAL_PATTERNS = (
    r"don'?t have access",
    r"do not have access",
    r"cannot access (?:your |the )?erp",
    r"can'?t access (?:your |the )?erp",
    r"no access to (?:your |the )?erp",
    r"unable to access (?:your |the )?erp",
    r"cannot connect to (?:your |the )?erp",
    r"upload (?:your |the )?(?:data|file|extract)",
    r"provide (?:the )?(?:data|extract|export)",
    r"没有.*erp.*权限",
    r"无法访问.*erp",
    r"没有接入.*erp",
    r"无法获取.*erp",
    r"无法连接.*erp",
)


def _env_enabled(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default)).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def erp_hybrid_synthesis_enabled() -> bool:
    return _env_enabled("EDIA_ERP_HYBRID_SYNTHESIS", "true")


def erp_fast_stream_enabled() -> bool:
    return _env_enabled("EDIA_FAST_ERP_STREAM", "true")


def erp_synthesis_route() -> str:
    """Final narrative route for ERP: external (DeepSeek) or local."""
    route = str(os.getenv("EDIA_ERP_SYNTHESIS", "external") or "external").strip().lower()
    if route in {"local", "internal"}:
        return "local"
    # kimi / deepseek / external → use configured external LLM (EXTERNAL_LLM_*)
    return "external"


def should_bypass_erp_fast_path(
    message: str,
    hermes_plan: Optional[Dict[str, Any]] = None,
) -> bool:
    """Market/forecast/weekly research must not use the ERP sales fast path."""
    query = str(message or "").strip()
    if not query:
        return False
    try:
        from backend.src.engine.decision_graph import (
            _is_broad_research_report_query,
            _looks_like_erp_report_query,
            _looks_like_erp_revenue_query,
            _looks_like_erp_sales_query,
            _looks_like_weekly_report_request,
        )
        from backend.src.nlu.intent_parser import _fast_intent
        from backend.src.prompts.task_prompt import TaskType

        explicit_erp = (
            _looks_like_erp_report_query(query)
            or _looks_like_erp_revenue_query(query)
            or _looks_like_erp_sales_query(query)
        )
        task_type, _confidence = _fast_intent(query)
        task_value = str(getattr(task_type, "value", task_type) or "")
        if explicit_erp or task_value == TaskType.ERP_DATA_QUERY.value:
            return False

        lower = query.lower()
        if _looks_like_weekly_report_request(query) or _is_broad_research_report_query(query):
            return True
        if any(
            marker in lower
            for marker in (
                "market weekly",
                "c5c9 report",
                "generate weekly",
                "weekly summary",
            )
        ):
            return True
        if any(
            marker in query
            for marker in (
                "周报",
                "生成周报",
                "市场研究报告",
                "深度市场研究",
                "深度市场研究报告",
                "经营启示",
                "短期预测",
            )
        ):
            return True

        plan = hermes_plan if isinstance(hermes_plan, dict) else {}
        domains = {str(item or "").strip().lower() for item in (plan.get("domains") or [])}
        capabilities = {
            str(item or "").strip().lower() for item in (plan.get("capabilities") or [])
        }
        erp_capabilities = capabilities & {
            "erp_sales_summary",
            "erp_customer_sales",
            "erp_order_details",
        } or any(cap.startswith("erp_") for cap in capabilities)
        if (domains & {"forecast", "market", "executive"}) and not (domains & {"erp"}):
            if not erp_capabilities:
                return True
    except Exception:
        logger.debug("ERP fast-path bypass check failed", exc_info=True)
    return False


def is_fast_erp_stream_eligible(
    message: str,
    hermes_plan: Optional[Dict[str, Any]] = None,
    *,
    deterministic_erp_stream: bool = False,
) -> bool:
    """True when the query should use the fast ERP data plane (+ optional Kimi synthesis)."""
    if not erp_fast_stream_enabled():
        return False
    query = str(message or "").strip()
    if not query:
        return False
    if should_bypass_erp_fast_path(query, hermes_plan):
        return False
    if deterministic_erp_stream:
        return True
    try:
        from backend.src.engine.decision_graph import (
            _looks_like_erp_report_query,
            _looks_like_erp_revenue_query,
            _looks_like_erp_sales_query,
        )
        from backend.src.nlu.intent_parser import _fast_intent
        from backend.src.prompts.task_prompt import TaskType

        if (
            _looks_like_erp_report_query(query)
            or _looks_like_erp_revenue_query(query)
            or _looks_like_erp_sales_query(query)
        ):
            return True
        task_type, _confidence = _fast_intent(query)
        if getattr(task_type, "value", task_type) == TaskType.ERP_DATA_QUERY.value:
            return True
        plan = hermes_plan if isinstance(hermes_plan, dict) else {}
        domains = {str(item or "").strip().lower() for item in (plan.get("domains") or [])}
        if domains & {"erp", "database"}:
            capabilities = {
                str(item or "").strip().lower() for item in (plan.get("capabilities") or [])
            }
            if capabilities & {"erp_sales_summary", "erp_customer_sales", "erp_order_details"} or any(
                cap.startswith("erp_") for cap in capabilities
            ):
                return True
    except Exception:
        logger.debug("ERP fast-stream eligibility check failed", exc_info=True)
    return False


def build_erp_no_data_markdown(message: str, *, language: str = "zh") -> str:
    lang = "en" if str(language or "").strip().lower() in {"en", "english"} else "zh"
    if lang == "en":
        return (
            "## ERP data status\n\n"
            f"No verified ERP sales rows were found for: **{str(message or '').strip()}**.\n\n"
            "The internal ERP database was queried successfully, but the selected period "
            "returned no matching transactions. Try a different month or product scope."
        )
    return (
        "## ERP 数据状态\n\n"
        f"未在内部 ERP 数据库中找到与以下问题匹配的销售记录：**{str(message or '').strip()}**。\n\n"
        "系统已成功连接 ERP，但所选时间范围内无成交数据。请尝试调整月份或产品范围。"
    )


def looks_like_erp_access_denial(text: str) -> bool:
    source = str(text or "").strip()
    if not source:
        return False
    lowered = source.lower()
    for pattern in _ERP_ACCESS_DENIAL_PATTERNS:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            return True
    return False


def guard_erp_markdown_against_denial(
    markdown: str,
    fallback: str,
    *,
    has_erp_data: bool,
) -> str:
    if has_erp_data and looks_like_erp_access_denial(markdown):
        return str(fallback or "").strip()
    return str(markdown or "").strip()


def erp_observations_have_data(execution: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(execution, dict):
        return False
    for observation in execution.get("observations") or []:
        if not isinstance(observation, dict):
            continue
        tool_name = str(observation.get("tool") or "").strip().lower()
        status = str(observation.get("status") or "").strip().lower()
        if status == "ok" and tool_name.startswith("erp"):
            return True
    return False


def build_erp_sales_report_markdown(
    message: str,
    prefs: Optional[Dict[str, object]] = None,
) -> Optional[str]:
    """Fetch ERP context and render deterministic sales/report markdown."""
    try:
        from backend.src.domain.product_resolver import extract_product_ids_in_text
        from backend.src.engine.decision_graph import (
            TaskType,
            _build_deterministic_erp_sales_report,
            _looks_like_erp_report_query,
            _looks_like_erp_revenue_query,
            _looks_like_erp_sales_query,
        )
        from backend.src.nlu.intent_parser import _fast_intent
        from backend.src.tools.erp_tools import build_erp_context

        query = str(message or "").strip()
        if not query:
            return None

        task_type, _confidence = _fast_intent(query)
        task_value = str(getattr(task_type, "value", task_type) or "")
        if not (
            _looks_like_erp_report_query(query)
            or _looks_like_erp_revenue_query(query)
            or _looks_like_erp_sales_query(query)
            or task_value == TaskType.ERP_DATA_QUERY.value
        ):
            return None

        product_ids = extract_product_ids_in_text(query)
        product_id = product_ids[0] if product_ids else None
        erp_data = build_erp_context(
            product_id=product_id,
            period=query,
            query_type="sales",
            user_query=query,
        )
        state: Dict[str, Any] = {
            "parsed_intent": SimpleNamespace(task_type=TaskType.ERP_DATA_QUERY),
            "user_query": query,
            "user_prefs": dict(prefs or {}),
            "user_context": SimpleNamespace(product=product_id, period=query),
            "resolved_chat_mode": str((prefs or {}).get("chat_mode") or "analysis"),
            "erp_data": erp_data,
        }
        report = str(_build_deterministic_erp_sales_report(state) or "").strip()
        if report:
            return report
        erp_text = str(erp_data or "").strip()
        return erp_text or None
    except Exception:
        logger.warning("ERP sales report markdown build failed", exc_info=True)
        return None


def build_erp_kimi_synthesis_prompts(
    message: str,
    facts_markdown: str,
    *,
    language: str = "zh",
) -> tuple[str, str]:
    """Prompts for Kimi (external) final ERP report from verified facts only."""
    lang = "en" if str(language or "").strip().lower() in {"en", "english"} else "zh"
    if lang == "en":
        system_prompt = (
            "You are EDIA assistant producing the final user-facing ERP business report. "
            "Hermes has already fetched verified INTERNAL ERP database facts for you. "
            "Use ONLY the VERIFIED_ERP_FACTS block below. "
            "Never claim you lack ERP, database, or live data access. "
            "Never ask the user to upload files or provide extracts. "
            "Include key figures (revenue, volume, orders, period) and a short executive summary. "
            "If facts mention a product or period, keep them exactly. "
            "Do NOT generate ASCII art, text-based charts, or illustrative diagrams. "
            "All charts are rendered interactively by the frontend. "
            "IMPORTANT: Output raw markdown directly. Do NOT wrap the response in triple backticks or code blocks."
        )
        user_prompt = (
            f"USER_QUERY:\n{str(message or '').strip()}\n\n"
            "VERIFIED_ERP_FACTS (authoritative — from internal ERP database):\n"
            f"{str(facts_markdown or '').strip()}\n\n"
            "Write the final ERP report in clear markdown for the user. "
            "Do NOT generate ASCII art, text-based charts, or illustrative diagrams. "
            "Output raw markdown text directly — do NOT wrap in ``` or any code block."
        )
    else:
        system_prompt = (
            "你是 EDIA 助手，负责输出面向用户的最终 ERP 业务报告。"
            "Hermes 已从内部 ERP 数据库取得下方已核实事实。"
            "只能使用 VERIFIED_ERP_FACTS 中的数据。"
            "禁止声称无法访问 ERP/数据库或没有实时数据。"
            "禁止要求用户上传文件。"
            "需包含关键指标（销售额、销量、订单数、周期）和简短执行摘要。"
            "禁止生成 ASCII 字符画、文本表格图或示意性图表。"
            "所有图表由系统根据真实数据自动渲染为前端交互式图表。"
            "重要：直接输出原始 Markdown 文本，禁止用 ``` 或代码块包裹整个回答。"
        )
        user_prompt = (
            f"用户问题：\n{str(message or '').strip()}\n\n"
            "已核实 ERP 事实（来自内部数据库，具有权威性）：\n"
            f"{str(facts_markdown or '').strip()}\n\n"
            "请用清晰的中文 Markdown 输出最终 ERP 报告。"
            "禁止生成 ASCII 字符画、文本表格图或示意性图表。"
            "直接输出原始 Markdown 文本，不要用 ``` 或代码块包裹。"
        )
    return system_prompt, user_prompt


def build_erp_hybrid_prompts(
    message: str,
    facts_markdown: str,
    *,
    language: str = "zh",
) -> tuple[str, str]:
    return build_erp_kimi_synthesis_prompts(message, facts_markdown, language=language)


def stream_erp_synthesis(
    llm: Any,
    message: str,
    facts_markdown: str,
    *,
    language: str = "zh",
    on_delta: Optional[Callable[[str], None]] = None,
) -> str:
    """Stream final ERP narrative using Kimi (external) or local LLM per EDIA_ERP_SYNTHESIS."""
    facts = str(facts_markdown or "").strip()
    if not facts or llm is None:
        return facts

    route_hint = erp_synthesis_route()
    if route_hint == "local":
        return apply_erp_hybrid_synthesis(llm, message, facts, language=language)

    system_prompt, user_prompt = build_erp_kimi_synthesis_prompts(
        message,
        facts,
        language=language,
    )
    parts: list[str] = []
    stream_fn = getattr(llm, "generate_completion_stream", None)
    if callable(stream_fn):
        for delta in stream_fn(
            system_prompt,
            user_prompt,
            route_hint="external",
            sanitized=True,
        ):
            piece = str(delta or "")
            if not piece:
                continue
            parts.append(piece)
            if on_delta:
                on_delta(piece)
    else:
        full = str(
            llm.generate_completion(
                system_prompt,
                user_prompt,
                route_hint="external",
                sanitized=True,
            )
            or ""
        ).strip()
        if full:
            parts.append(full)
            if on_delta:
                on_delta(full)

    narrative = "".join(parts).strip()
    if not narrative or narrative.startswith("Error generating response:"):
        return facts
    if looks_like_erp_access_denial(narrative):
        return facts
    return narrative


def iter_erp_synthesis_deltas(
    llm: Any,
    message: str,
    facts_markdown: str,
    *,
    language: str = "zh",
) -> Iterator[str]:
    """Yield synthesis deltas for SSE streaming."""
    collected: list[str] = []

    def _on_delta(piece: str) -> None:
        collected.append(piece)

    result = stream_erp_synthesis(
        llm,
        message,
        facts_markdown,
        language=language,
        on_delta=_on_delta,
    )
    if collected:
        for piece in collected:
            yield piece
        return
    if result and result != facts_markdown:
        yield result


def apply_erp_hybrid_synthesis(
    llm: Any,
    message: str,
    facts_markdown: str,
    *,
    language: str = "zh",
) -> str:
    facts = str(facts_markdown or "").strip()
    if not facts or llm is None or not hasattr(llm, "generate_completion"):
        return facts
    if not erp_hybrid_synthesis_enabled():
        return facts

    system_prompt, user_prompt = build_erp_hybrid_prompts(
        message,
        facts,
        language=language,
    )
    narrative = str(
        llm.generate_completion(
            system_prompt,
            user_prompt,
            route_hint="local",
            sanitized=True,
        )
        or ""
    ).strip()
    if not narrative or narrative.startswith("Error generating response:"):
        return facts
    if looks_like_erp_access_denial(narrative):
        return facts
    divider = "\n\n---\n\n"
    heading = "## Executive Summary\n\n" if language in {"en", "english"} else "## 执行摘要\n\n"
    return f"{facts}{divider}{heading}{narrative}".strip()


def resolve_erp_grounded_markdown(
    message: str,
    prefs: Optional[Dict[str, object]],
    *,
    llm: Any = None,
    hermes_plan: Optional[Dict[str, Any]] = None,
    candidate_markdown: str = "",
) -> Optional[str]:
    """Prefer DB-backed ERP markdown when agent output denies access or is empty."""
    from backend.src.hermes.execution import execute_hermes_plan

    facts = build_erp_sales_report_markdown(message, prefs)
    if not facts:
        execution = execute_hermes_plan(hermes_plan, user_query=message)
        if not erp_observations_have_data(execution):
            return None
        facts = build_erp_sales_report_markdown(message, prefs)
    if not facts:
        return None

    candidate = str(candidate_markdown or "").strip()
    if candidate and not looks_like_erp_access_denial(candidate):
        return candidate

    language = str((prefs or {}).get("language") or "zh")
    if llm is not None and erp_hybrid_synthesis_enabled():
        try:
            return apply_erp_hybrid_synthesis(llm, message, facts, language=language)
        except Exception:
            logger.warning("ERP hybrid synthesis failed", exc_info=True)
    return facts
