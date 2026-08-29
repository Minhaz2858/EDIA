"""LLM router for planner JSON extraction and streaming synthesis (DeepSeek external)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

from backend.src.nlu.external_llm_config import (
    use_external_for_planner,
    use_external_for_synthesis,
)

DISABLE_LOCAL_LLM = os.getenv("DISABLE_LOCAL_LLM", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

_PLANNER_SYSTEM = (
    "You are EDIA's intent planner. Return valid JSON only. "
    "Do not include Markdown fences."
)
_SYNTHESIS_SYSTEM = (
    "You are EDIA final synthesis engine. Follow instructions exactly. "
    "Return the final user-facing answer only."
)


class PlannerRouterError(RuntimeError):
    pass


def _llm_service():
    from backend.src.nlu.llm_service import RealLLMService

    return RealLLMService()


def _external_route_enabled() -> bool:
    service = _llm_service()
    return bool(
        use_external_for_planner()
        and os.getenv("EXTERNAL_LLM_API_KEY")
        and getattr(service, "external_ready", False)
    )


def _synthesis_external_route_enabled() -> bool:
    service = _llm_service()
    return bool(
        use_external_for_synthesis()
        and os.getenv("EXTERNAL_LLM_API_KEY")
        and getattr(service, "external_ready", False)
    )


def route_planner_call(prompt: str) -> str:
    llm = _llm_service()
    if _external_route_enabled():
        try:
            content = llm.generate_completion(
                _PLANNER_SYSTEM,
                prompt,
                route_hint="external",
                sanitized=True,
            )
            if content and not str(content).startswith("Error"):
                return str(content)
        except Exception:
            pass
    if DISABLE_LOCAL_LLM:
        raise PlannerRouterError(
            "Planner DeepSeek route unavailable and local LLM is globally disabled."
        )
    content = llm.generate_completion(_PLANNER_SYSTEM, prompt, route_hint="local")
    if not str(content or "").strip():
        raise PlannerRouterError("Empty planner response")
    return str(content)


def _stream_completion(
    llm: object,
    *,
    route_hint: str,
    prompt: str,
) -> Iterator[str]:
    stream_fn = getattr(llm, "generate_completion_stream", None)
    if not callable(stream_fn):
        text = getattr(llm, "generate_completion")(
            _SYNTHESIS_SYSTEM,
            prompt,
            route_hint=route_hint,
            sanitized=route_hint == "external",
        )
        if text:
            yield str(text)
        return
    for chunk in stream_fn(
        _SYNTHESIS_SYSTEM,
        prompt,
        route_hint=route_hint,
        sanitized=route_hint == "external",
    ):
        if chunk:
            yield str(chunk)


async def synthesize_streaming(prompt: str) -> AsyncIterator[str]:
    llm = _llm_service()
    if _synthesis_external_route_enabled():
        try:
            for chunk in _stream_completion(llm, route_hint="external", prompt=prompt):
                yield chunk
            return
        except Exception:
            pass

    if DISABLE_LOCAL_LLM:
        raise PlannerRouterError(
            "Synthesis DeepSeek route unavailable and local LLM is globally disabled."
        )

    for chunk in _stream_completion(llm, route_hint="local", prompt=prompt):
        yield chunk
