"""Request-scoped context for EDIA tools invoked by Hermes."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
import threading
from typing import Any, Callable, Dict, List, Optional


@dataclass
class EdiaRequestContext:
    user_query: str
    session_id: str = ""
    user_id: str = ""
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    user_prefs: Dict[str, Any] = field(default_factory=dict)
    hermes_plan: Dict[str, Any] = field(default_factory=dict)
    turn_state: Dict[str, Any] = field(default_factory=dict)
    decision_service: Any = None
    llm_service: Any = None
    stream_delta_callback: Optional[Callable[[str], None]] = None


_edia_context: ContextVar[Optional[EdiaRequestContext]] = ContextVar(
    "edia_request_context",
    default=None,
)
_fallback_context_lock = threading.RLock()
_fallback_context: Optional[EdiaRequestContext] = None


def set_edia_context(ctx: EdiaRequestContext):
    global _fallback_context
    with _fallback_context_lock:
        _fallback_context = ctx
    return _edia_context.set(ctx)


def reset_edia_context(token) -> None:
    global _fallback_context
    _edia_context.reset(token)
    with _fallback_context_lock:
        _fallback_context = None


def get_edia_context() -> Optional[EdiaRequestContext]:
    ctx = _edia_context.get()
    if ctx is not None:
        return ctx
    with _fallback_context_lock:
        return _fallback_context


def require_edia_context() -> EdiaRequestContext:
    ctx = get_edia_context()
    if ctx is None:
        raise RuntimeError("EDIA request context is not set for Hermes tool execution.")
    return ctx
