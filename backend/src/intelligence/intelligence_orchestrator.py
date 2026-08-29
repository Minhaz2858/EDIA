"""Intelligence Orchestrator — central coordinator for the intelligence pipeline.

Ties together:
  1. NewsIngestionAgent (event detection)
  2. CausalNarrativeEngine (multi-hop reasoning)
  3. ScenarioProbabilityEngine (scenario generation)
  4. AutoOverrideDecisionEngine (auto/human override decisions)
  5. IntelligenceLearningLoop (feedback calibration)

Provides a single entry point for the decision pipeline to query
intelligence-enriched context.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Lazy references — set during init
_news_agent = None
_causal_engine = None
_scenario_engine = None
_override_engine = None
_learning_loop = None
_llm_service = None
_initialized = False


def _database_only_mode() -> bool:
    return (os.getenv("DATABASE_ONLY_MODE", "true") or "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _news_ingestion_enabled() -> bool:
    return (os.getenv("ENABLE_NEWS_INGESTION_AGENT", "false") or "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _should_initialize_news_agent() -> bool:
    return (not _database_only_mode()) or _news_ingestion_enabled()


def _intelligence_review_status() -> str:
    raw_value = (os.getenv("INTELLIGENCE_RETRIEVAL_REVIEW_STATUS", "auto") or "auto").strip().lower()
    if raw_value in {"pending", "approved", "rejected", "all", "auto"}:
        return raw_value
    return "auto"


def _intelligence_min_usefulness_score() -> int:
    raw_value = (os.getenv("INTELLIGENCE_MIN_USEFULNESS_SCORE", "0") or "0").strip()
    try:
        return max(0, min(5, int(raw_value)))
    except ValueError:
        return 0


def _event_review_status(event: Any) -> str:
    if isinstance(event, dict):
        raw_value = str(event.get("review_status") or "").strip().lower()
    else:
        raw_value = str(getattr(event, "review_status", "") or "").strip().lower()
    return raw_value or "approved"


def _event_allowed_by_review_policy(event: Any, review_status: str) -> bool:
    event_status = _event_review_status(event)
    if review_status == "auto":
        return event_status != "rejected"
    if review_status == "all":
        return True
    return event_status == review_status


def _is_noise_event(event: Any) -> bool:
    text_parts = []
    if isinstance(event, dict):
        text_parts = [
            str(event.get("headline") or ""),
            str(event.get("key_information") or ""),
            str(event.get("full_text") or ""),
        ]
    else:
        text_parts = [
            str(getattr(event, "headline", "") or ""),
            str(getattr(event, "key_information", "") or ""),
            str(getattr(event, "full_text", "") or ""),
        ]

    combined = "\n".join(text_parts).lower()
    if not combined.strip():
        return True

    noisy_markers = [
        "request blocked",
        "被拦截",
        "waf",
        "403",
        "404",
        "500",
        "502",
        "503",
        "504",
        "gateway timeout",
        "error page",
        "访问受限",
        "forbidden",
        "cloudflare",
    ]
    if any(marker in combined for marker in noisy_markers):
        return True

    # Avoid low-information navigation pages being treated as market events.
    if re.search(r"(门户|首页|财经门户|网站导航)", combined):
        return True

    return False


def _ensure_event_object(event: Any):
    if hasattr(event, "headline") and hasattr(event, "to_dict"):
        return event

    if isinstance(event, dict):
        from backend.src.intelligence.event_types import ExtractedEvent

        payload = {
            "time_horizon": "short",
            "relevance_to_c5_c9": "indirect",
            "source_credibility": "tier2",
            "affected_commodities": [],
            "key_information": str(event.get("key_information") or event.get("raw_quote") or event.get("full_text") or ""),
            **{
                key: value
                for key, value in event.items()
                if key in ExtractedEvent.__dataclass_fields__
            },
        }
        return ExtractedEvent(**payload)

    return None


def _build_fast_causal_analysis(event: Any) -> dict[str, Any] | None:
    event_obj = _ensure_event_object(event)
    if event_obj is None or _causal_engine is None:
        return None

    try:
        from backend.src.intelligence.causal_narrative_engine import (
            _APPROX_PRICES_CNY,
            _NAPHTHA_ELASTICITIES,
        )
        from backend.src.intelligence.second_order_effects import (
            compute_total_second_order_pct,
            get_product_specific_effects,
        )

        entry_commodity = _causal_engine._map_event_to_entry(event_obj)
        primary_pct = _causal_engine._quantify_primary_impact(event_obj)
        naphtha_base_pct = _causal_engine._crude_to_naphtha_pct(primary_pct, event_obj)
        naphtha_total_pct = naphtha_base_pct + compute_total_second_order_pct(event_obj.event_type) * 100

        product_impacts = {}
        product_specific_effects = get_product_specific_effects(event_obj.event_type)
        for product_id, elasticity in _NAPHTHA_ELASTICITIES.items():
            pct_impact = naphtha_total_pct * elasticity / 100
            product_extra = product_specific_effects.get(product_id, 0.0)
            total_pct = pct_impact + product_extra
            approx_price = _APPROX_PRICES_CNY.get(product_id, 5000)
            product_impacts[product_id] = {
                "pct_impact": round(total_pct * 100, 2),
                "cny_impact": round(approx_price * total_pct, 0),
                "causal_path": f"event → {entry_commodity} → naphtha → {product_id}",
                "elasticity_used": round(elasticity, 3),
                "second_order_pct": round(product_extra * 100, 2),
            }

        narrative = str(
            getattr(event_obj, "causal_chain_hint", "")
            or getattr(event_obj, "key_information", "")
            or getattr(event_obj, "raw_quote", "")
            or getattr(event_obj, "full_text", "")
            or getattr(event_obj, "headline", "")
        ).strip()

        return {
            "event_id": event_obj.event_id,
            "event_headline": event_obj.headline,
            "event_type": event_obj.event_type,
            "entry_commodity": entry_commodity,
            "primary_impact_pct": round(primary_pct, 2),
            "naphtha_total_pct": round(naphtha_total_pct, 2),
            "product_impacts": product_impacts,
            "narrative": narrative,
            "confidence_by_horizon": _causal_engine._compute_confidence_decay(event_obj),
            "recommended_override_type": _causal_engine._suggest_override_type(event_obj),
            "detected_at": event_obj.detected_at,
            "review_status": event_obj.review_status,
            "source_url": event_obj.source_url,
            "raw_quote": event_obj.raw_quote,
        }
    except Exception as exc:
        logger.debug("Fast intelligence causal analysis failed: %s", exc)
        return None


def init_intelligence(llm_service) -> None:
    """Initialize all intelligence components. Call once at server startup."""
    global _news_agent, _causal_engine, _scenario_engine, _override_engine
    global _learning_loop, _llm_service, _initialized

    if _initialized:
        return

    _llm_service = llm_service

    if not _should_initialize_news_agent():
        logger.info(
            "Database-only mode active: external news ingestion disabled; using persisted intelligence registry only"
        )
    else:
        try:
            from backend.src.intelligence.news_ingestion_agent import NewsIngestionAgent
            from backend.src.rag.vector_store import kb

            _news_agent = NewsIngestionAgent(llm_service, kb, override_engine=_override_engine)
            # Don't auto-start the background scan — let server.py decide
            logger.info("NewsIngestionAgent initialized")
        except Exception as e:
            logger.warning("NewsIngestionAgent init failed: %s", e)

    try:
        from backend.src.intelligence.causal_narrative_engine import init_causal_engine

        _causal_engine = init_causal_engine(llm_service)
        logger.info("CausalNarrativeEngine initialized")
    except Exception as e:
        logger.warning("CausalNarrativeEngine init failed: %s", e)

    try:
        from backend.src.intelligence.scenario_probability_engine import (
            ScenarioProbabilityEngine,
        )

        _scenario_engine = ScenarioProbabilityEngine(llm_service)
        logger.info("ScenarioProbabilityEngine initialized")
    except Exception as e:
        logger.warning("ScenarioProbabilityEngine init failed: %s", e)

    try:
        from backend.src.intelligence.auto_override_decision import auto_override_engine

        _override_engine = auto_override_engine
        logger.info("AutoOverrideDecisionEngine initialized")
    except Exception as e:
        logger.warning("AutoOverrideDecisionEngine init failed: %s", e)

    try:
        from backend.src.intelligence.intelligence_learning_loop import learning_loop

        _learning_loop = learning_loop
        logger.info("IntelligenceLearningLoop initialized")
    except Exception as e:
        logger.warning("IntelligenceLearningLoop init failed: %s", e)

    _initialized = True
    logger.info("Intelligence pipeline fully initialized")


def get_intelligence_context(
    hours: int = 4,
    review_status: Optional[str] = None,
    min_usefulness_score: Optional[int] = None,
    event_limit: int = 20,
    analysis_limit: Optional[int] = 3,
    fast_mode: bool = False,
) -> dict[str, Any]:
    """Build an intelligence context block for the LLM prompt.

    Returns a dict with recent events, causal analyses, scenarios,
    and override decisions — ready to be injected into the prompt assembler.
    """
    context: dict[str, Any] = {
        "has_intelligence": False,
        "checked": True,
        "checked_hours": hours,
        "events": [],
        "causal_analyses": [],
        "scenarios": [],
        "override_decisions": [],
        "intelligence_summary": "",
    }

    database_only_mode = _database_only_mode()

    events = []
    effective_review_status = (review_status or _intelligence_review_status()).strip().lower()
    if effective_review_status not in {"pending", "approved", "rejected", "all", "auto"}:
        effective_review_status = "auto"
    effective_min_usefulness_score = (
        _intelligence_min_usefulness_score()
        if min_usefulness_score is None
        else max(0, min(5, int(min_usefulness_score)))
    )
    review_filter = None if effective_review_status in {"all", "auto"} else effective_review_status

    # Always consult the persisted intelligence registry first so chat requests
    # still consider ingested events even if the background agent instance is unavailable.
    try:
        from backend.src.intelligence.event_registry import intelligence_event_registry

        events = intelligence_event_registry.list_recent_events(
            hours=hours,
            min_impact=0.50,
            review_status=review_filter,
            min_usefulness_score=effective_min_usefulness_score,
            limit=event_limit,
        )
        if not events:
            events = intelligence_event_registry.list_recent_events(
                hours=hours * 2,
                review_status=review_filter,
                min_usefulness_score=effective_min_usefulness_score,
                limit=event_limit,
            )[:3]
        events = [
            e
            for e in events
            if _event_allowed_by_review_policy(e, effective_review_status)
            and not _is_noise_event(e)
        ]
    except Exception as e:
        logger.warning("Intelligence registry lookup failed: %s", e)

    # Fall back to the in-memory/background agent path if the registry is empty.
    if not events and (not database_only_mode) and _news_agent:
        try:
            events = _news_agent.get_high_impact_events(hours=hours)
            if not events:
                events = _news_agent.get_recent_events(hours=hours * 2)[:3]
            events = [
                e
                for e in events
                if _event_allowed_by_review_policy(e, effective_review_status)
                and not _is_noise_event(e)
            ]
        except Exception:
            events = []

    if not events:
        if database_only_mode:
            context["intelligence_summary"] = (
                f"DATABASE_ONLY_MODE active: checked persisted intelligence registry only for the last {hours}h; no recent usable events found."
            )
        else:
            context["intelligence_summary"] = (
                f"CHECKED NEWS & EVENT INGESTION (last {hours}h): no recent high-impact events found."
            )
        return context

    context["has_intelligence"] = True
    normalized_events = [
        evt for evt in (_ensure_event_object(e) for e in events) if evt
    ]
    if not normalized_events:
        return context

    context["events"] = [e.to_dict() for e in normalized_events]

    # Run causal reasoning on each high-impact event
    causal_events = normalized_events
    if isinstance(analysis_limit, int) and analysis_limit > 0:
        causal_events = normalized_events[:analysis_limit]

    for event in causal_events:
        try:
            if fast_mode:
                fast_analysis = _build_fast_causal_analysis(event)
                if fast_analysis is not None:
                    context["causal_analyses"].append(fast_analysis)
                continue

            if _causal_engine:
                chain = _causal_engine.reason_through_event(event)
                context["causal_analyses"].append(
                    {
                        "event_id": event.event_id,
                        "event_headline": event.headline,
                        "event_type": event.event_type,
                        "entry_commodity": chain.entry_commodity,
                        "primary_impact_pct": chain.primary_impact_pct,
                        "naphtha_total_pct": chain.naphtha_total_pct,
                        "product_impacts": chain.product_impacts,
                        "narrative": chain.causal_narrative,
                        "confidence_by_horizon": chain.confidence_by_horizon,
                        "recommended_override_type": chain.recommended_override_type,
                        "detected_at": event.detected_at,
                        "review_status": event.review_status,
                        "source_url": event.source_url,
                        "raw_quote": event.raw_quote,
                    }
                )

                # Generate scenarios
                if _scenario_engine:
                    scenarios = _scenario_engine.generate_scenarios(event, chain)
                    context["scenarios"].extend([s.to_dict() for s in scenarios])

                # Decide override action
                if _override_engine:
                    decision = _override_engine.decide(event, chain)
                    context["override_decisions"].append(decision.to_dict())

        except Exception as e:
            logger.debug("Intelligence processing error for event: %s", e)

    # Build a compact summary for prompt injection
    summary = _build_summary(context)
    if database_only_mode:
        summary = "DATABASE_ONLY_MODE active: using persisted intelligence registry only.\n" + summary
    context["intelligence_summary"] = summary

    return context


def _build_summary(ctx: dict, summary_only: bool = False) -> str:
    """Build a compact text summary of intelligence findings for LLM prompt."""
    parts = []

    if ctx["events"]:
        parts.append(f"DETECTED {len(ctx['events'])} RECENT MARKET EVENT(S):")
        for evt in ctx["events"][:3]:
            headline = evt.get("headline", "Unknown")
            direction = evt.get("direction", "?")
            certainty = evt.get("certainty", "?")
            parts.append(f"  • [{certainty.upper()}] {headline} → {direction}")
            key_info = str(evt.get("key_information") or evt.get("raw_quote") or evt.get("full_text") or "").strip()
            if key_info:
                compact = " ".join(key_info.split())[:220]
                parts.append(f"    key information: {compact}")

    if summary_only:
        return "\n".join(parts)

    if ctx["causal_analyses"]:
        parts.append("\nCAUSAL IMPACT ANALYSIS:")
        for ca in ctx["causal_analyses"][:3]:
            parts.append(
                f"  • {ca['event_headline'][:60]}: "
                f"naphtha impact {ca['naphtha_total_pct']:+.2f}%, "
                f"entry via {ca['entry_commodity']}"
            )
            if ca.get("product_impacts"):
                top_impacts = sorted(
                    ca["product_impacts"].items(),
                    key=lambda x: abs(x[1].get("pct_impact", 0)),
                    reverse=True,
                )[:5]
                for pid, imp in top_impacts:
                    parts.append(
                        f"    → {pid}: {imp.get('pct_impact', 0):+.2f}% "
                        f"({imp.get('cny_impact', 0):+.0f} CNY/ton)"
                    )
            if ca.get("narrative"):
                parts.append(f"  Narrative: {ca['narrative'][:200]}...")

    if ctx["scenarios"]:
        parts.append("\nSCENARIO PROBABILITIES:")
        for sc in ctx["scenarios"][:3]:
            parts.append(
                f"  • {sc['label']}: {sc['probability']:.0%} "
                f"— {sc['causal_narrative'][:80]}..."
            )

    if ctx["override_decisions"]:
        parts.append("\nOVERRIDE DECISIONS:")
        for od in ctx["override_decisions"]:
            parts.append(
                f"  • {od['action'].upper()}: {od['event_headline'][:50]} "
                f"(conf={od['injection_confidence']:.0%}, "
                f"shock={od['naphtha_shock_pct']:+.2f}%)"
            )

    return "\n".join(parts)


def get_news_agent():
    return _news_agent


def news_ingestion_enabled() -> bool:
    return _news_ingestion_enabled()


def should_initialize_news_ingestion_agent() -> bool:
    return _should_initialize_news_agent()


def get_override_engine():
    return _override_engine


def get_learning_loop():
    return _learning_loop
