"""Deprecated: Hermes agent runtime removed from EDIA.

Route planning lives in ``backend.src.routing.chat_route_plan``.
ERP grounding lives in ``backend.src.hermes.erp_grounding``.
Skill observation execution lives in ``backend.src.hermes.execution``.
"""

from __future__ import annotations

import warnings
from typing import Any, Dict, List, Optional


def hermes_main_enabled() -> bool:
    return False


def hermes_lite_stream_eligible(
    hermes_plan: Optional[Dict[str, Any]],
    *,
    hermes_followup: bool,
) -> bool:
    return False


class HermesRuntime:
    """Removed. Use decision_service + chat_route_plan + erp_grounding instead."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        warnings.warn(
            "HermesRuntime is removed from EDIA; chat uses the standard pipeline.",
            DeprecationWarning,
            stacklevel=2,
        )

    def build_plan_metadata(self, message: str) -> Dict[str, Any]:
        from backend.src.routing.chat_route_plan import build_route_plan_metadata

        return build_route_plan_metadata(message)

    def process_chat(self, **kwargs: Any) -> Dict[str, Any]:
        raise RuntimeError("Hermes agent removed; use /api/chat or /api/chat/stream.")

    def process_chat_stream(self, **kwargs: Any) -> Any:
        raise RuntimeError("Hermes agent removed; use /api/chat/stream.")
        yield  # pragma: no cover
