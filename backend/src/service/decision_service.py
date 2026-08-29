import os

from backend.src.nlu.llm_service import LLMService
from backend.src.service.types import DecisionReport
from backend.src.engine.decision_graph import build_decision_graph, build_prepare_graph
from typing import Any, List, Optional


class DecisionService:
    def __init__(self, llm_service: LLMService):
        self.llm = llm_service
        self._graph = build_decision_graph(llm_service)
        self._prepare_graph = build_prepare_graph(llm_service)

    def process_request(
        self,
        user_query: str,
        conversation_history: Optional[List[dict[str, Any]]] = None,
        user_prefs: Optional[dict[str, Any]] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        hermes_plan: Optional[dict[str, Any]] = None,
    ) -> DecisionReport:
        """Run the LangGraph decision pipeline and return the final report."""
        prefs = user_prefs or {}
        route_hint = self._resolve_graph_route_hint(prefs)
        sanitized = bool(prefs.get("external_payload_sanitized", False))

        with self.llm.request_scope(route_hint=route_hint, sanitized=sanitized):
            result = self._graph.invoke(
                {
                    "user_query": user_query,
                    "conversation_history": conversation_history or [],
                    "user_prefs": prefs,
                    "user_id": user_id,
                    "session_id": session_id,
                    "hermes_plan": hermes_plan or {},
                }
            )
        return result["report"]

    def prepare_request(
        self,
        user_query: str,
        conversation_history: Optional[List[dict[str, Any]]] = None,
        user_prefs: Optional[dict[str, Any]] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        stage_callback: Optional[Any] = None,
        hermes_plan: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Run the pipeline up to prompt assembly (no final report generation)."""
        prefs = user_prefs or {}
        route_hint = self._resolve_graph_route_hint(prefs)
        sanitized = bool(prefs.get("external_payload_sanitized", False))

        with self.llm.request_scope(route_hint=route_hint, sanitized=sanitized):
            return self._prepare_graph.invoke(
                {
                    "user_query": user_query,
                    "conversation_history": conversation_history or [],
                    "user_prefs": prefs,
                    "user_id": user_id,
                    "session_id": session_id,
                    "stage_callback": stage_callback,
                    "hermes_plan": hermes_plan or {},
                }
            )

    @staticmethod
    def _resolve_graph_route_hint(user_prefs: dict[str, Any]) -> str:
        """Route used by graph/prep stages.

        Default policy keeps graph computation on local model so deterministic
        logic and structured stage outputs remain stable. If needed, callers can
        explicitly opt-in to full-graph external routing via `graph_llm_route`.
        """
        local_disabled = (
            os.getenv("DISABLE_LOCAL_LLM", "false").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        from backend.src.nlu.external_llm_config import strict_external_llm_only

        if strict_external_llm_only():
            return "external"

        graph_explicit = str(user_prefs.get("graph_llm_route") or "").strip().lower()
        if graph_explicit in {"local", "external"}:
            if local_disabled and graph_explicit == "local":
                return "external"
            return graph_explicit

        if bool(user_prefs.get("use_external_graph_reasoning", False)):
            return "external"

        # For latency-sensitive analysis/report flows, follow the same default
        # external policy as final generation when enabled by env.
        mode = str(user_prefs.get("chat_mode") or "").strip().lower()
        default_external = (
            os.getenv("CHAT_EXTERNAL_REASONING_DEFAULT", "false").lower()
            == "true"
        )
        if default_external and mode in {"analysis", "report", "deep_research"}:
            return "external"

        return "external" if local_disabled else "local"

    @staticmethod
    def _resolve_llm_route_hint(user_prefs: dict[str, Any]) -> str:
        local_disabled = (
            os.getenv("DISABLE_LOCAL_LLM", "false").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        from backend.src.nlu.external_llm_config import strict_external_llm_only

        if strict_external_llm_only():
            return "external"

        explicit = str(user_prefs.get("llm_route") or "").strip().lower()
        if explicit in {"local", "external"}:
            if local_disabled and explicit == "local":
                return "external"
            return explicit

        if bool(user_prefs.get("use_external_reasoning", False)):
            return "external"

        return "external" if local_disabled else "local"
