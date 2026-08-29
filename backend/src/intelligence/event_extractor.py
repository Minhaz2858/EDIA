"""Event Extractor — uses LLM to extract structured ExtractedEvent from raw news text.

Given raw article text and a category hint, the LLM returns structured JSON
that maps onto the ExtractedEvent dataclass.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from backend.src.intelligence.event_types import ExtractedEvent

logger = logging.getLogger(__name__)

_ALLOWED_EVENT_TYPES = {
    "geopolitical_supply_disruption",
    "plant_outage",
    "sinopec_price_change",
    "tariff_policy",
    "demand_signal",
    "shipping_disruption",
    "opec_decision",
    "weather_event",
    "currency_move",
    "sanctions",
}

_ALLOWED_DIRECTIONS = {"bullish", "bearish", "neutral", "uncertain"}
_ALLOWED_MAGNITUDES = {"small", "medium", "large", "extreme"}
_ALLOWED_CERTAINTY = {"confirmed", "likely", "rumored", "speculative"}
_ALLOWED_HORIZON = {"immediate", "short", "medium"}
_ALLOWED_SCOPE = {"global", "asia_pacific", "china", "middle_east", "europe"}
_ALLOWED_RELEVANCE = {"direct", "indirect", "minimal"}


def _keyword_event_type(text: str, category: str) -> str:
    lower = f"{category} {text}".lower()
    if any(k in lower for k in ["opec", "production cut", "quota"]):
        return "opec_decision"
    if any(k in lower for k in ["hormuz", "red sea", "shipping", "tanker", "freight"]):
        return "shipping_disruption"
    if any(k in lower for k in ["outage", "shutdown", "force majeure", "maintenance"]):
        return "plant_outage"
    if any(k in lower for k in ["sanction", "embargo"]):
        return "sanctions"
    if any(k in lower for k in ["tariff", "duty", "trade war"]):
        return "tariff_policy"
    if any(k in lower for k in ["sinopec", "listing price"]):
        return "sinopec_price_change"
    if any(k in lower for k in ["typhoon", "hurricane", "storm", "weather"]):
        return "weather_event"
    if any(
        k in lower for k in ["fx", "usd", "rmb", "yuan", "currency", "exchange rate"]
    ):
        return "currency_move"
    if any(k in lower for k in ["geopolitical", "conflict", "war", "iran", "russia"]):
        return "geopolitical_supply_disruption"
    return "demand_signal"


def _norm_str(value: object, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip().lower()
    return text or default


def _normalize_event_payload(raw: dict, article_text: str, category: str) -> dict:
    payload = dict(raw or {})

    event_type = _norm_str(payload.get("event_type"), "")
    if event_type not in _ALLOWED_EVENT_TYPES:
        event_type = _keyword_event_type(article_text, category)
    payload["event_type"] = event_type

    payload["direction"] = _norm_str(payload.get("direction"), "uncertain")
    if payload["direction"] not in _ALLOWED_DIRECTIONS:
        payload["direction"] = "uncertain"

    payload["magnitude_estimate"] = _norm_str(
        payload.get("magnitude_estimate"), "medium"
    )
    if payload["magnitude_estimate"] not in _ALLOWED_MAGNITUDES:
        payload["magnitude_estimate"] = "medium"

    payload["certainty"] = _norm_str(payload.get("certainty"), "likely")
    if payload["certainty"] not in _ALLOWED_CERTAINTY:
        payload["certainty"] = "likely"

    payload["time_horizon"] = _norm_str(payload.get("time_horizon"), "short")
    if payload["time_horizon"] not in _ALLOWED_HORIZON:
        payload["time_horizon"] = "short"

    payload["geographic_scope"] = _norm_str(payload.get("geographic_scope"), "global")
    if payload["geographic_scope"] not in _ALLOWED_SCOPE:
        payload["geographic_scope"] = "global"

    payload["relevance_to_c5_c9"] = _norm_str(
        payload.get("relevance_to_c5_c9"), "indirect"
    )
    if payload["relevance_to_c5_c9"] not in _ALLOWED_RELEVANCE:
        payload["relevance_to_c5_c9"] = "indirect"

    if not isinstance(payload.get("affected_commodities"), list):
        payload["affected_commodities"] = []
    if not isinstance(payload.get("key_entities"), list):
        payload["key_entities"] = []

    headline = str(payload.get("headline") or "").strip()
    if not headline:
        headline = "Market event extracted from unstructured article"
    payload["headline"] = headline[:180]

    payload["causal_chain_hint"] = str(payload.get("causal_chain_hint") or "").strip()
    payload["raw_quote"] = str(payload.get("raw_quote") or "").strip()[:180]
    payload["source_credibility"] = _norm_str(
        payload.get("source_credibility"), "tier2"
    )
    if payload["source_credibility"] not in {"tier1", "tier2", "tier3"}:
        payload["source_credibility"] = "tier2"

    return payload


# ── Extraction prompt ─────────────────────────────────────────────────────

_EXTRACTION_PROMPT = """You are extracting structured market intelligence from a news article
about the petrochemical / energy industry.

Article:
{article_text}

Category hint: {category}

Extract ONLY if the article describes a concrete event (not opinion/analysis/advertisement).

Return JSON (and NOTHING else):
{{
    "event_type": "<geopolitical_supply_disruption | plant_outage | sinopec_price_change | tariff_policy | demand_signal | shipping_disruption | opec_decision | weather_event | currency_move | sanctions>",
    "headline": "<one sentence summary>",
    "affected_commodities": ["crude_oil", "naphtha", "c5", "isoprene", "piperylene", "dcpd", "c9_resin", "c5_resin", "sis"],
    "geographic_scope": "<global | asia_pacific | china | middle_east | europe>",
    "direction": "<bullish | bearish | neutral | uncertain>",
    "magnitude_estimate": "<small | medium | large | extreme>",
    "certainty": "<confirmed | likely | rumored | speculative>",
    "time_horizon": "<immediate | short | medium>",
    "key_entities": ["entity1", "entity2"],
    "causal_chain_hint": "<one sentence describing causal chain from event to C5/C9 impact>",
    "source_credibility": "<tier1 | tier2 | tier3>",
    "relevance_to_c5_c9": "<direct | indirect | minimal>",
    "raw_quote": "<most relevant direct quote under 20 words>"
}}

If article is NOT a concrete market-moving event, return exactly: {{"skip": true}}
"""

_SYSTEM_PROMPT = (
    "You are a petrochemical market intelligence extraction system. "
    "You ONLY output valid JSON. No markdown, no explanation."
)


class EventExtractor:
    """Extracts structured ExtractedEvent from raw news text using the LLM."""

    def __init__(self, llm_service):
        self.llm = llm_service

    def extract(
        self,
        article_text: str,
        category: str = "general",
        source_url: str = "",
    ) -> Optional[ExtractedEvent]:
        """Extract a structured event from raw article text.

        Returns None if the article is not a concrete market event.
        """
        if not article_text or len(article_text.strip()) < 30:
            return None

        # Truncate very long articles to avoid token waste
        truncated = article_text[:3000]

        prompt = _EXTRACTION_PROMPT.format(
            article_text=truncated,
            category=category,
        )

        try:
            result = self.llm.generate_json(_SYSTEM_PROMPT, prompt)
        except Exception as e:
            logger.warning("LLM extraction failed: %s", e)
            return None

        if not result or result.get("skip"):
            return None

        result = _normalize_event_payload(result, truncated, category)

        try:
            event = ExtractedEvent(
                event_type=result.get("event_type", "demand_signal"),
                headline=result.get("headline", "Unknown event"),
                affected_commodities=result.get("affected_commodities", []),
                direction=result.get("direction", "uncertain"),
                magnitude_estimate=result.get("magnitude_estimate", "medium"),
                certainty=result.get("certainty", "likely"),
                time_horizon=result.get("time_horizon", "short"),
                geographic_scope=result.get("geographic_scope", "global"),
                relevance_to_c5_c9=result.get("relevance_to_c5_c9", "indirect"),
                key_entities=result.get("key_entities", []),
                causal_chain_hint=result.get("causal_chain_hint", ""),
                source_url=source_url,
                source_credibility=result.get("source_credibility", "tier2"),
                full_text=article_text[:2000],
                raw_quote=result.get("raw_quote", ""),
            )
            return event
        except Exception as e:
            logger.warning("Failed to construct ExtractedEvent: %s", e)
            return None
