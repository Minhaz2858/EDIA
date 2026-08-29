from __future__ import annotations

from typing import Any, Dict


_PRODUCT_ALIASES: dict[str, set[str]] = {
    "cracked_c5": {"cracked_c5", "mixed_c5"},
    "mixed_c5": {"cracked_c5", "mixed_c5"},
    "cracked_c9": {"cracked_c9", "mixed_c9"},
    "mixed_c9": {"cracked_c9", "mixed_c9"},
    "sis": {"sis", "sis_rubber"},
    "sis_rubber": {"sis", "sis_rubber"},
    "c9_resin": {"c9_resin", "hydrogenated_c9"},
    "hydrogenated_c9": {"c9_resin", "hydrogenated_c9"},
}


def summarize_product_intelligence(
    product_id: str,
    intelligence_context: Dict[str, Any] | None,
) -> Dict[str, Any]:
    if not intelligence_context or not intelligence_context.get("has_intelligence"):
        return {
            "active": False,
            "event_count": 0,
            "bias": "neutral",
            "confidence": 0.0,
            "net_price_pct": 0.0,
            "net_price_cny": 0.0,
            "dominant_event": None,
            "events": [],
        }

    aliases = _PRODUCT_ALIASES.get(product_id, {product_id})
    matched_events: list[dict[str, Any]] = []

    for analysis in intelligence_context.get("causal_analyses", []):
        impacts = analysis.get("product_impacts", {})
        matched_impact = None
        matched_alias = None
        for alias in aliases:
            if alias in impacts:
                matched_alias = alias
                matched_impact = impacts[alias]
                break
        if not matched_impact:
            continue

        confidence_by_horizon = analysis.get("confidence_by_horizon", {})
        confidence = float(confidence_by_horizon.get("7d", 0.6) or 0.6)
        matched_events.append(
            {
                "event_id": analysis.get("event_id"),
                "headline": analysis.get("event_headline"),
                "event_type": analysis.get("event_type"),
                "entry_commodity": analysis.get("entry_commodity"),
                "naphtha_total_pct": float(
                    analysis.get("naphtha_total_pct", 0.0) or 0.0
                ),
                "product_alias": matched_alias,
                "pct_impact": float(matched_impact.get("pct_impact", 0.0) or 0.0),
                "cny_impact": float(matched_impact.get("cny_impact", 0.0) or 0.0),
                "causal_path": matched_impact.get("causal_path", ""),
                "confidence": confidence,
                "narrative": analysis.get("narrative", ""),
                "detected_at": analysis.get("detected_at"),
                "review_status": analysis.get("review_status"),
                "source_url": analysis.get("source_url"),
                "raw_quote": analysis.get("raw_quote"),
            }
        )

    if not matched_events:
        return {
            "active": False,
            "event_count": 0,
            "bias": "neutral",
            "confidence": 0.0,
            "net_price_pct": 0.0,
            "net_price_cny": 0.0,
            "dominant_event": None,
            "events": [],
        }

    total_weight = sum(max(event["confidence"], 0.1) for event in matched_events)
    weighted_pct = sum(
        event["pct_impact"] * max(event["confidence"], 0.1) for event in matched_events
    )
    weighted_cny = sum(
        event["cny_impact"] * max(event["confidence"], 0.1) for event in matched_events
    )
    weighted_naphtha = sum(
        event["naphtha_total_pct"] * max(event["confidence"], 0.1)
        for event in matched_events
    )

    net_price_pct = weighted_pct / total_weight if total_weight else 0.0
    net_price_cny = weighted_cny / total_weight if total_weight else 0.0
    net_naphtha_pct = weighted_naphtha / total_weight if total_weight else 0.0
    confidence = max(event["confidence"] for event in matched_events)
    dominant_event = max(
        matched_events,
        key=lambda event: abs(event["cny_impact"]) * max(event["confidence"], 0.1),
    )

    if net_price_pct > 0.25:
        bias = "bullish"
    elif net_price_pct < -0.25:
        bias = "bearish"
    else:
        bias = "neutral"

    return {
        "active": True,
        "event_count": len(matched_events),
        "bias": bias,
        "confidence": round(confidence, 3),
        "net_price_pct": round(net_price_pct, 2),
        "net_price_cny": round(net_price_cny, 0),
        "net_naphtha_pct": round(net_naphtha_pct, 2),
        "dominant_event": dominant_event,
        "events": matched_events,
    }
