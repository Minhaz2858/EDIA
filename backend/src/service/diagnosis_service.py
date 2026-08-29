from __future__ import annotations

import os
import re
from typing import Dict, List, Any, Optional

from backend.src.domain.knowledge_graph import domain_graph
from backend.src.domain.models import RelationshipType
from backend.src.intelligence.product_intelligence import summarize_product_intelligence
from backend.src.intelligence.event_impact_db import event_impact_db
from backend.src.service.erp_kpi_service import (
    compute_kpi_forecast_adjustment,
    erp_kpi_service,
)
from backend.src.rag.vector_store import kb

# ---------------------------------------------------------------------------
# Cross-product signal propagation tables (§5.3)
# ---------------------------------------------------------------------------

PRODUCT_DEPENDENCY_GRAPH: Dict[str, List[str]] = {
    "sis": ["isoprene"],
    "sis_rubber": ["isoprene"],
    "c5_resin": ["piperylene", "cracked_c5"],
    "hydrogenated_c9": ["cracked_c9"],
    "c9_resin": ["cracked_c9"],
    "mthpa_hardener": ["piperylene"],
    "dcpd": ["cracked_c5"],
    "blowing_agent": ["cracked_c5", "raffinate_c5"],
    "trimethylbenzene": ["cracked_c9"],
}

DOWNSTREAM_BULLISH_SIGNALS: List[re.Pattern] = [
    re.compile(r"SIS operating rate.*(6[0-9]|7[0-9])%", re.I),
    re.compile(r"hardener.*(?:production|plant).*restart", re.I),
    re.compile(r"hardener.*operating rate.*recover", re.I),
    re.compile(r"C5 resin.*production.*support", re.I),
    re.compile(r"hydrogenated resin.*(9,?000|support)", re.I),
    re.compile(r"UPR.*demand.*recover", re.I),
    re.compile(r"road.?marking.*construction season", re.I),
    re.compile(r"downstream.*(?:restart|resume)", re.I),
    re.compile(r"post.?holiday.*demand.*recover", re.I),
    re.compile(r"biased upward", re.I),
    re.compile(r"expected to restart in March", re.I),
    re.compile(r"需求.*恢复|开工率.*回升|下游.*复工", re.I),
]

# ---------------------------------------------------------------------------
# Seasonal demand status (§5.2)
# ---------------------------------------------------------------------------

PRODUCT_SEASONAL_PHASES: Dict[str, Dict[int, str]] = {
    "c5_resin": {
        1: "off_season",
        2: "off_season",
        3: "peak_for_infra",
        4: "peak_for_infra",
        5: "peak_for_infra",
        6: "peak_for_infra",
        7: "peak_for_infra",
        8: "peak_for_infra",
        9: "peak_for_infra",
        11: "off_season",
        12: "off_season",
    },
    "dcpd": {
        1: "off_season",
        2: "off_season",
        3: "peak",
        4: "peak",
        5: "peak",
        6: "peak",
        7: "peak",
        8: "peak",
        11: "off_season",
        12: "off_season",
    },
    "sis": {
        11: "off_season",
        12: "off_season",
        1: "off_season",
        2: "off_season",
        3: "off_season",
        9: "peak",
        10: "peak",
    },
    "sis_rubber": {
        11: "off_season",
        12: "off_season",
        1: "off_season",
        2: "off_season",
        3: "off_season",
        9: "peak",
        10: "peak",
    },
    "mthpa_hardener": {
        11: "off_season",
        12: "off_season",
        1: "off_season",
        2: "off_season",
        3: "peak",
        4: "peak",
        5: "peak",
        6: "peak",
        7: "peak",
        8: "peak",
        9: "peak",
    },
    "cracked_c5": {
        1: "post_holiday_lull",
        2: "post_holiday_lull",
        3: "post_holiday_restart",
        4: "post_holiday_restart",
        5: "post_holiday_restart",
    },
    "mixed_c5": {
        1: "post_holiday_lull",
        2: "post_holiday_lull",
        3: "post_holiday_restart",
        4: "post_holiday_restart",
        5: "post_holiday_restart",
    },
    "isoprene": {2: "sis_restart", 3: "sis_restart"},
}

_DEMAND_OVERRIDE_PATTERNS: List[tuple[re.Pattern, str]] = [
    (
        re.compile(r"demand recover|需求回暖|需求恢复|demand improving", re.I),
        "recovering",
    ),
    (
        re.compile(r"inventory build.?up|库存累积|inventory pressure|库存压力", re.I),
        "weak_with_inventory_pressure",
    ),
    (re.compile(r"biased upward|偏强", re.I), "improving"),
    (re.compile(r"demand weak|需求偏弱|demand slow", re.I), "weak"),
]


def _warehouse_only_mode() -> bool:
    return os.getenv("WAREHOUSE_ONLY_MODE", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def compute_demand_status(
    product: str,
    current_month: int,
    rag_text: str = "",
) -> Dict[str, Any]:
    """Compute seasonal demand phase + RAG-based demand override (§5.2).

    Returns:
        {
          "seasonal_phase": str,
          "demand_override": str | None,
          "effective_demand_status": str,
          "pricing_note": str,
        }
    """
    base_phase = PRODUCT_SEASONAL_PHASES.get(product, {}).get(current_month, "normal")
    demand_override = None
    note = ""

    # Scan RAG text for demand status phrases
    for pattern, override_label in _DEMAND_OVERRIDE_PATTERNS:
        if pattern.search(rag_text):
            demand_override = override_label
            break

    # Key rule: pre-construction season → hold price; do not over-discount
    if base_phase == "peak_for_infra" and demand_override is None:
        note = "Pre-construction season. Demand will improve. Hold price; do not over-discount."

    if not note:
        effective = demand_override or base_phase
        if effective == "recovering":
            note = "Demand recovering — hold or raise price."
        elif effective == "weak_with_inventory_pressure":
            note = "Weak demand + inventory pressure — consider accelerated shipments."
        elif effective == "off_season":
            note = "Off-season phase — standard seasonal discount may apply."
        elif effective == "improving":
            note = "Demand improving (biased upward) — lean toward higher range."
        else:
            note = ""

    return {
        "seasonal_phase": base_phase,
        "demand_override": demand_override,
        "effective_demand_status": demand_override or base_phase,
        "pricing_note": note,
    }


# ---------------------------------------------------------------------------
# Confidence scoring helpers
# ---------------------------------------------------------------------------


def _regime_confidence(change_7d_pct: float) -> float:
    """Map 7-day % change to a 0.0–1.0 confidence for the regime label.

    |change| >= 5% → confidence 1.0 (strong signal)
    |change| < 1%  → confidence 0.2 (weak/noisy)
    Linear interpolation between.
    """
    abs_chg = abs(change_7d_pct)
    if abs_chg >= 5.0:
        return 1.0
    if abs_chg < 1.0:
        return 0.2
    return round(0.2 + (abs_chg - 1.0) * (0.8 / 4.0), 3)


def _driver_confidence(
    driver_change: float, focal_change: float, edge_weight: float = 1.0
) -> float:
    """Score how much a driver explains the focal product's movement.

    Higher score when:
    - Driver has large absolute change
    - Driver direction aligns with expected causal effect
    - Edge weight is high (strong causal link)
    """
    if focal_change == 0:
        return 0.3
    # Directional alignment: upstream rising should push focal costs up
    alignment = (
        1.0
        if (driver_change > 0 and focal_change > 0)
        or (driver_change < 0 and focal_change < 0)
        else 0.4
    )
    magnitude = min(abs(driver_change) / 5.0, 1.0)  # cap at 5%
    return round(min(alignment * magnitude * edge_weight, 1.0), 3)


def _anomaly_boost(anomaly_data: Dict[str, Any]) -> float:
    """Extra confidence adjustment if anomaly detected."""
    if anomaly_data.get("is_anomaly"):
        return 0.15
    return 0.0


class DiagnosisService:
    """Stage 2 — Diagnosis: causal chain traversal on the knowledge graph.

    Given a focal product and the Stage-1 perception snapshot this service:
    - Finds upstream suppliers and downstream consumers (multi-hop).
    - Ranks them by 7-day price change (biggest movers = key drivers).
    - Computes numeric confidence scores (0.0–1.0) per hypothesis.
    - Propagates directional impact signals along the chain.
    - Returns a compact structured dict for the LLM prompt.
    """

    def _get_multi_hop_upstream(
        self, product_id: str, max_hops: int = 3
    ) -> List[Dict[str, Any]]:
        """Traverse upstream through the knowledge graph up to max_hops."""
        visited = set()
        result = []
        frontier = [product_id]
        hop = 0

        while frontier and hop < max_hops:
            next_frontier = []
            for pid in frontier:
                for e in domain_graph.edges:
                    if (
                        e.target_id == pid
                        and e.relation == RelationshipType.PRODUCES
                        and e.source_id not in visited
                    ):
                        visited.add(e.source_id)
                        weight = getattr(e, "yield_rate", 1.0) or 1.0
                        result.append(
                            {
                                "id": e.source_id,
                                "hop": hop + 1,
                                "edge_weight": weight,
                            }
                        )
                        next_frontier.append(e.source_id)
            frontier = next_frontier
            hop += 1

        return result

    def _get_multi_hop_downstream(
        self, product_id: str, max_hops: int = 3
    ) -> List[Dict[str, Any]]:
        """Traverse downstream through the knowledge graph up to max_hops."""
        visited = set()
        result = []
        frontier = [product_id]
        hop = 0

        while frontier and hop < max_hops:
            next_frontier = []
            for pid in frontier:
                for e in domain_graph.edges:
                    if e.source_id == pid and e.target_id not in visited:
                        visited.add(e.target_id)
                        weight = getattr(e, "yield_rate", 1.0) or 1.0
                        result.append(
                            {
                                "id": e.target_id,
                                "hop": hop + 1,
                                "edge_weight": weight,
                                "edge_type": e.relation,
                            }
                        )
                        next_frontier.append(e.target_id)
            frontier = next_frontier
            hop += 1

        return result

    def _structural_chain_fallback(self, product_id: str) -> List[str]:
        """Build a graph-only causal chain explanation when live data is sparse."""
        impacts: List[str] = []

        upstream = [
            e.source_id for e in domain_graph.edges if e.target_id == product_id
        ]
        downstream = [
            e.target_id for e in domain_graph.edges if e.source_id == product_id
        ]

        for up in upstream[:4]:
            up_node = domain_graph.get_node(up)
            cur_node = domain_graph.get_node(product_id)
            if up_node and cur_node:
                impacts.append(
                    f"结构传导：{up_node.name_en} supply/cost shock → {cur_node.name_en} pricing bias"
                )

        for dn in downstream[:4]:
            cur_node = domain_graph.get_node(product_id)
            dn_node = domain_graph.get_node(dn)
            if cur_node and dn_node:
                impacts.append(
                    f"结构传导：{cur_node.name_en} move → {dn_node.name_en} margin/demand response"
                )

        if not impacts:
            impacts.append(
                "结构传导：当前缺少足够实时价格切片，已回退到知识图谱因果链进行方向判断。"
            )
        return impacts

    def _no_live_data_result(self, product_id: str, reason: str) -> Dict[str, Any]:
        return {
            "focal_product": product_id,
            "market_regime": "neutral",
            "regime_confidence": 0.0,
            "primary_driver": None,
            "secondary_drivers": [],
            "chain_impact": [],
            "evidence_sources": [{"text": reason, "score": 0.0}],
            "contradicting_factors": [],
            "downstream_propagated_signals": {},
            "net_signal": "neutral",
            "demand_status": {
                "seasonal_phase": "unknown",
                "demand_override": None,
                "effective_demand_status": "unknown",
                "pricing_note": "缺少仓库实时数据，诊断结果不可用。",
            },
            "intelligence_signal": {
                "active": False,
                "bias": "neutral",
                "confidence": 0.0,
                "net_price_pct": 0.0,
                "net_price_cny": 0.0,
            },
            "erp_kpi_signal": {
                "active": False,
                "bias": "neutral",
                "adjustment_pct": {"3d": 0.0, "7d": 0.0, "30d": 0.0},
                "summary": "",
            },
            "geopolitical_driver": None,
            "divergence_detected": None,
            "scored_hypotheses": [],
            "status": "insufficient_live_data",
        }

    def _derive_geopolitical_driver(
        self,
        products_data: Dict[str, Any],
        intelligence_context: Dict[str, Any] | None,
    ) -> Optional[Dict[str, Any]]:
        """Infer upstream move cause/durability from intelligence events.

        Triggered only when crude/naphtha weekly change is abnormally large.
        """

        crude_7d = float(
            (products_data.get("crude_oil") or {}).get("change_7d_pct", 0.0) or 0.0
        )
        naphtha_7d = float(
            (products_data.get("naphtha") or {}).get("change_7d_pct", 0.0) or 0.0
        )

        # Threshold: event-like shock, not routine fluctuation.
        if max(abs(crude_7d), abs(naphtha_7d)) < 10.0:
            return None

        events = []
        if isinstance(intelligence_context, dict):
            events = intelligence_context.get("events") or []

        geo_keywords = [
            "hormuz",
            "red sea",
            "iran",
            "israel",
            "sanction",
            "opec",
            "war",
            "shipping",
            "地缘",
            "霍尔木兹",
            "红海",
            "制裁",
            "战争",
            "航运",
            "中东",
        ]

        def _classify_event_type(evt: Dict[str, Any], headline: str) -> str:
            et = str(evt.get("event_type") or evt.get("category") or "").strip().lower()
            if et:
                return et
            h = (headline or "").lower()
            if any(k in h for k in ["opec"]):
                return "opec_decision"
            if any(
                k in h
                for k in ["shipping", "red sea", "hormuz", "航运", "红海", "霍尔木兹"]
            ):
                return "shipping_disruption"
            if any(k in h for k in ["sanction", "制裁"]):
                return "sanctions"
            if any(
                k in h
                for k in ["shutdown", "outage", "maintenance", "停工", "停车", "检修"]
            ):
                return "plant_outage"
            return "geopolitical_supply_disruption"

        chosen_evt: Optional[Dict[str, Any]] = None
        chosen_headline = ""
        chosen_type = "geopolitical_supply_disruption"

        for e in events:
            if not isinstance(e, dict):
                continue
            headline = str(e.get("headline") or "").strip()
            h = headline.lower()
            if any(k in h for k in geo_keywords):
                chosen_evt = e
                chosen_headline = headline
                chosen_type = _classify_event_type(e, headline)
                break

        if not chosen_evt:
            # Fallback: detect an abnormal upstream move pattern, but do not
            # frame it as a verified external event when none exists.
            chosen_headline = (
                f"Unverified upstream move pattern: crude {crude_7d:+.1f}%w / "
                f"naphtha {naphtha_7d:+.1f}%w"
            )
            chosen_type = "demand_signal"

        precedents = event_impact_db.find_similar_events(chosen_type, k=3)
        weighted = (
            precedents.get("weighted_historical_impact", {}) if precedents else {}
        )
        duration_days = int(weighted.get("weighted_duration_days", 14) or 14)
        conf = float(precedents.get("confidence_from_precedents", 0.35) or 0.35)

        high_reversal_types = {
            "geopolitical_supply_disruption",
            "shipping_disruption",
            "sanctions",
            "supply_shock_upstream",
            "weather_event",
        }
        if chosen_type in high_reversal_types:
            reversal_risk = "HIGH"
        elif chosen_type in {"opec_decision", "demand_signal"}:
            reversal_risk = "MEDIUM"
        else:
            reversal_risk = "LOW"

        implication = (
            "Event-driven upstream premium detected; follow short-cycle pricing and avoid locking long-dated fixed quotes at peak levels."
            if reversal_risk == "HIGH"
            else "Upstream move may be more persistent; use staged price adjustments with trigger-based contract terms."
        )

        return {
            "triggered": True,
            "verified_event": bool(chosen_evt),
            "event": chosen_headline,
            "event_type": chosen_type,
            "crude_weekly_change_pct": round(crude_7d, 2),
            "naphtha_weekly_change_pct": round(naphtha_7d, 2),
            "duration_estimate_days": duration_days,
            "reversal_risk": reversal_risk,
            "pricing_implication": implication,
            "confidence_from_precedents": round(conf, 2),
        }

    def _detect_crude_naphtha_divergence(
        self,
        products_data: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        crude_7d = float(
            (products_data.get("crude_oil") or {}).get("change_7d_pct", 0.0) or 0.0
        )
        naphtha_7d = float(
            (products_data.get("naphtha") or {}).get("change_7d_pct", 0.0) or 0.0
        )
        gap = crude_7d - naphtha_7d

        if abs(crude_7d) <= 1.5 or abs(gap) <= 2.0:
            return None

        concept_queries = [
            "crude oil naphtha divergence supply demand Asian market olefin margin",
            "naphtha did not follow crude rise weak olefin margin import pressure",
            "原油上涨 石脑油 供需宽松 烯烃亏损",
            "原油 石脑油 传导不畅 亚洲市场 供应压力",
        ]

        merged: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for q in concept_queries:
            try:
                rows = kb.query_weekly(q, product=None, n_results=3)
            except Exception:
                rows = []
            for row in rows:
                text = str((row or {}).get("text") or "").strip()
                if not text:
                    continue
                key = text[:180]
                if key in seen:
                    continue
                seen.add(key)
                merged.append(row)
                if len(merged) >= 6:
                    break
            if len(merged) >= 6:
                break

        return {
            "crude_7d": round(crude_7d, 2),
            "naphtha_7d": round(naphtha_7d, 2),
            "gap": round(gap, 2),
            "explanation_hint": "naphtha supply ample, olefin margins weak, crude rise not fully transmitted",
            "evidence": merged,
        }

    def diagnose(
        self,
        product_id: str,
        perception_snapshot: Dict[str, Any],
        intelligence_context: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        products_data = perception_snapshot.get("products", {})

        def snap(pid: str) -> Optional[Dict[str, Any]]:
            return products_data.get(pid)

        # ── multi-hop upstream traversal
        upstream_nodes = self._get_multi_hop_upstream(product_id)
        # ── multi-hop downstream traversal
        downstream_nodes = self._get_multi_hop_downstream(product_id)

        # ── also keep direct downstream edges for chain impact text
        downstream_edges = [e for e in domain_graph.edges if e.source_id == product_id]

        # ── gather candidates with price data and confidence scores
        focal = snap(product_id)
        focal_change = focal.get("change_7d_pct", 0) if focal else 0
        focal_anomaly = focal.get("anomaly", {}) if focal else {}

        # Hard fallback: if focal product is missing in snapshot, still return a
        # graph-based diagnosis skeleton instead of an empty/no-data result.
        if not focal:
            if _warehouse_only_mode():
                return self._no_live_data_result(
                    product_id,
                    "Live warehouse-backed market snapshot for this product is unavailable; graph-only diagnosis fallback is disabled.",
                )
            structural_chain = self._structural_chain_fallback(product_id)
            return {
                "focal_product": product_id,
                "market_regime": "neutral",
                "regime_confidence": 0.25,
                "primary_driver": {
                    "id": "knowledge_graph_fallback",
                    "name_en": "Knowledge Graph Structural Causality",
                    "role": "graph_fallback",
                    "change_7d_pct": 0.0,
                    "confidence": 0.5,
                    "hop": 1,
                    "hypothesis": "causal_graph_fallback",
                    "hypothesis_confidence": 0.5,
                },
                "secondary_drivers": [],
                "chain_impact": structural_chain,
                "evidence_sources": [
                    {
                        "text": "Live market snapshot for focal product is unavailable; diagnosis uses structural relationships from the domain graph.",
                        "score": 0.5,
                    }
                ],
                "contradicting_factors": [],
                "downstream_propagated_signals": {},
                "net_signal": "neutral",
                "demand_status": {
                    "seasonal_phase": "normal",
                    "demand_override": None,
                    "effective_demand_status": "normal",
                    "pricing_note": "需要补充实时行情与库存切片以提高判断置信度。",
                },
                "intelligence_signal": {
                    "active": False,
                    "bias": "neutral",
                    "confidence": 0.0,
                    "net_price_pct": 0.0,
                    "net_price_cny": 0.0,
                },
                "erp_kpi_signal": {
                    "active": False,
                    "bias": "neutral",
                    "adjustment_pct": {"3d": 0.0, "7d": 0.0, "30d": 0.0},
                    "summary": "",
                },
                "geopolitical_driver": None,
                "scored_hypotheses": [
                    {
                        "hypothesis": "causal_graph_fallback",
                        "confidence": 0.5,
                        "direction": "neutral",
                    }
                ],
            }

        related: List[Dict[str, Any]] = []
        for node_info in upstream_nodes:
            s = snap(node_info["id"])
            if s:
                conf = _driver_confidence(
                    s.get("change_7d_pct", 0), focal_change, node_info["edge_weight"]
                )
                conf += _anomaly_boost(s.get("anomaly", {}))
                related.append(
                    {
                        **s,
                        "_role": "upstream",
                        "_hop": node_info["hop"],
                        "_confidence": round(min(conf, 1.0), 3),
                    }
                )

        for node_info in downstream_nodes:
            s = snap(node_info["id"])
            if s:
                conf = _driver_confidence(
                    s.get("change_7d_pct", 0), focal_change, node_info["edge_weight"]
                )
                conf += _anomaly_boost(s.get("anomaly", {}))
                related.append(
                    {
                        **s,
                        "_role": "downstream",
                        "_hop": node_info["hop"],
                        "_confidence": round(min(conf, 1.0), 3),
                    }
                )

        # ── sort by absolute 7d change to find biggest movers
        related_sorted = sorted(
            related, key=lambda x: abs(x.get("change_7d_pct", 0)), reverse=True
        )

        primary_driver: Optional[Dict[str, Any]] = (
            related_sorted[0] if related_sorted else None
        )
        secondary_drivers: List[Dict[str, Any]] = related_sorted[1:3]

        # ── determine market regime with numeric confidence
        regime = "neutral"
        regime_conf = 0.2
        if focal:
            chg = focal.get("change_7d_pct", 0)
            if chg >= 2:
                regime = "bullish"
            elif chg <= -2:
                regime = "bearish"
            regime_conf = _regime_confidence(chg)
            # Boost confidence if anomaly detected on focal product
            regime_conf = round(
                min(regime_conf + _anomaly_boost(focal_anomaly), 1.0), 3
            )

        # ── build chain impact sentences (multi-hop)
        chain_impact: List[str] = []
        focal_name = focal["name_en"] if focal else product_id

        for node_info in upstream_nodes:
            s = snap(node_info["id"])
            if not s:
                continue
            direction = "rising" if s.get("change_7d_pct", 0) > 0 else "falling"
            cost_dir = "pressure ▲" if direction == "rising" else "relief ▼"
            hop_label = f" (hop {node_info['hop']})" if node_info["hop"] > 1 else ""
            chain_impact.append(
                f"{s['name_en']} {direction} ({s['change_7d_pct']:+.1f}%w){hop_label} → "
                f"{focal_name} input cost {cost_dir}"
            )

        for e in downstream_edges:
            s = snap(e.target_id)
            if not s:
                continue
            focal_dir = "rising" if focal_change > 0 else "falling"
            demand_dir = "softer ▼" if focal_dir == "rising" else "firmer ▲"
            chain_impact.append(
                f"{focal_name} {focal_dir} → {s['name_en']} demand {demand_dir}"
            )

        # ── RAG hypothesis scoring (§4.2.2)
        hypotheses = self._generate_hypotheses(
            product_id, focal, regime, upstream_nodes, products_data
        )
        scored_hypotheses = self._score_hypotheses(hypotheses, product_id)

        # Extract top evidence and contradictions from scored hypotheses
        evidence_sources: List[Dict[str, Any]] = []
        contradicting_factors: List[str] = []
        for hyp in scored_hypotheses:
            for ev in hyp.get("pro_evidence", [])[:2]:
                evidence_sources.append(ev)
            for ev in hyp.get("con_evidence", [])[:1]:
                contradicting_factors.append(ev.get("text", ""))

        # ── Cross-product signal propagation (§5.3)
        downstream_propagated_signals = self._propagate_downstream_signals(product_id)
        intelligence_signal = summarize_product_intelligence(
            product_id, intelligence_context
        )
        geopolitical_driver = self._derive_geopolitical_driver(
            products_data, intelligence_context
        )
        divergence_detected = self._detect_crude_naphtha_divergence(products_data)
        erp_kpi_signal = compute_kpi_forecast_adjustment(
            erp_kpi_service.get_product_kpi(product_id)
        )

        # Determine primary_driver label from hypotheses if available
        primary_hypothesis = scored_hypotheses[0] if scored_hypotheses else None
        primary_driver_label = (
            primary_hypothesis["hypothesis"] if primary_hypothesis else None
        )
        primary_hypothesis_conf = (
            primary_hypothesis["confidence"] if primary_hypothesis else 0.0
        )

        # Net signal from regime + hypothesis confidence
        if intelligence_signal["active"] and intelligence_signal["bias"] != "neutral":
            regime = intelligence_signal["bias"]
            regime_conf = round(max(regime_conf, intelligence_signal["confidence"]), 3)
            net_signal = intelligence_signal["bias"]
        elif regime == "bullish" and primary_hypothesis_conf >= 0.6:
            net_signal = "bullish"
        elif regime == "bearish" and primary_hypothesis_conf >= 0.6:
            net_signal = "bearish"
        else:
            net_signal = "neutral"

        if erp_kpi_signal["active"] and erp_kpi_signal["bias"] != "neutral":
            kpi_conf = round(
                min(
                    0.45 + abs(erp_kpi_signal["adjustment_pct"].get("7d", 0.0)) / 2.0,
                    0.9,
                ),
                3,
            )
            if net_signal == "neutral":
                net_signal = erp_kpi_signal["bias"]
                regime = erp_kpi_signal["bias"]
                regime_conf = round(max(regime_conf, kpi_conf), 3)
            if primary_driver is None or primary_hypothesis_conf < 0.55:
                primary_driver = {
                    "id": "erp_kpi_signal",
                    "name_en": "ERP Inventory / Sales Signal",
                    "change_7d_pct": erp_kpi_signal["adjustment_pct"].get("7d", 0.0),
                    "_role": "erp_kpi",
                    "_confidence": kpi_conf,
                    "_hop": 0,
                }
                primary_driver_label = "erp_kpi_signal"
                primary_hypothesis_conf = kpi_conf

            chain_impact.insert(
                0,
                "ERP KPI tilt: "
                f"{erp_kpi_signal.get('summary', 'internal sales/inventory signal')} "
                f"→ forecast bias {erp_kpi_signal['adjustment_pct'].get('7d', 0.0):+.2f}%",
            )
            evidence_sources.insert(
                0,
                {
                    "text": "ERP KPI signal: "
                    + (
                        erp_kpi_signal.get("summary")
                        or "internal sales and inventory data support the short-term view"
                    ),
                    "score": kpi_conf,
                },
            )

        if intelligence_signal["active"]:
            dominant = intelligence_signal["dominant_event"] or {}
            event_driver = {
                "id": "live_event_intelligence",
                "name_en": dominant.get("headline", "Live Intelligence Event"),
                "_role": "intelligence",
                "change_7d_pct": intelligence_signal["net_price_pct"],
                "_confidence": intelligence_signal["confidence"],
                "_hop": 0,
            }
            primary_driver = event_driver
            primary_driver_label = "live_event_intelligence"
            primary_hypothesis_conf = intelligence_signal["confidence"]
            chain_impact.insert(
                0,
                f"Live event impact: {dominant.get('headline', 'Recent event')} → {product_id} {intelligence_signal['net_price_pct']:+.2f}% ({intelligence_signal['net_price_cny']:+.0f} CNY/ton)",
            )
            evidence_sources.insert(
                0,
                {
                    "text": dominant.get("narrative")
                    or dominant.get("causal_path")
                    or dominant.get("headline", ""),
                    "score": intelligence_signal["confidence"],
                },
            )

        if geopolitical_driver and geopolitical_driver.get("triggered"):
            chain_impact.insert(
                0,
                "Geopolitical cause: "
                f"{geopolitical_driver.get('event', 'unknown event')} "
                f"→ crude {geopolitical_driver.get('crude_weekly_change_pct', 0):+.1f}%w "
                f"→ naphtha {geopolitical_driver.get('naphtha_weekly_change_pct', 0):+.1f}%w",
            )
            evidence_sources.insert(
                0,
                {
                    "text": "Geopolitical durability hint: "
                    f"duration≈{geopolitical_driver.get('duration_estimate_days', 14)}d, "
                    f"reversal_risk={geopolitical_driver.get('reversal_risk', 'MEDIUM')}.",
                    "score": geopolitical_driver.get("confidence_from_precedents", 0.5),
                },
            )
            if geopolitical_driver.get("reversal_risk") == "HIGH":
                contradicting_factors.insert(
                    0,
                    "Geopolitical premium may reverse quickly if the event de-escalates; avoid locking long-dated fixed quotes at peak.",
                )

        if divergence_detected:
            chain_impact.insert(
                0,
                "Cost-chain divergence: "
                f"crude {divergence_detected.get('crude_7d', 0):+.1f}%w vs "
                f"naphtha {divergence_detected.get('naphtha_7d', 0):+.1f}%w "
                f"(gap {divergence_detected.get('gap', 0):+.1f}%)",
            )
            evidence_sources.insert(
                0,
                {
                    "text": "Divergence hint: "
                    + str(divergence_detected.get("explanation_hint") or ""),
                    "score": 0.78,
                },
            )
            for hit in (divergence_detected.get("evidence") or [])[:2]:
                txt = str((hit or {}).get("text") or "").strip()
                if txt:
                    evidence_sources.append({"text": txt[:280], "score": 0.72})

        # ── Regional price spread signal (multi-source intelligence)
        try:
            from backend.src.service.market_data_service import market_data as _mds

            regional_prices = _mds.get_regional_prices(product_id)
            if len(regional_prices) >= 2:
                prices = list(regional_prices.values())
                spread = max(prices) - min(prices)
                if spread >= 500:
                    high_type = max(regional_prices, key=regional_prices.get)
                    low_type = min(regional_prices, key=regional_prices.get)
                    high_val = regional_prices[high_type]
                    low_val = regional_prices[low_type]
                    chain_impact.append(
                        f"Regional spread: {high_type} ¥{high_val:,.0f}/t vs "
                        f"{low_type} ¥{low_val:,.0f}/t "
                        f"(gap={spread:,.0f} CNY/t) → regional supply/demand imbalance"
                    )
                    evidence_sources.append(
                        {
                            "text": (
                                f"{product_id.replace('_', ' ').title()} shows "
                                f"{spread:,.0f} CNY/ton spread between {high_type} "
                                f"(¥{high_val:,.0f}) and {low_type} (¥{low_val:,.0f}); "
                                "elevated spread indicates localized tightness or logistics premium."
                            ),
                            "score": round(min(0.60 + spread / 5000.0, 0.85), 3),
                        }
                    )
        except Exception:
            pass

        # ── Seasonal demand status (§5.2)
        import datetime as _dt

        _rag_text = " ".join(ev.get("text", "") for ev in evidence_sources)
        demand_status = compute_demand_status(
            product_id,
            _dt.date.today().month,
            rag_text=_rag_text,
        )

        if not chain_impact:
            if _warehouse_only_mode():
                evidence_sources.insert(
                    0,
                    {
                        "text": "No warehouse-backed causal chain evidence was available for this diagnosis window.",
                        "score": 0.0,
                    },
                )
            else:
                chain_impact = self._structural_chain_fallback(product_id)

        if not evidence_sources:
            if _warehouse_only_mode():
                evidence_sources = [
                    {
                        "text": "No live warehouse-backed evidence matched this diagnosis request.",
                        "score": 0.0,
                    }
                ]
            else:
                evidence_sources = [
                    {
                        "text": "No strong live/RAG evidence hit; used knowledge-graph structural propagation as fallback.",
                        "score": 0.45,
                    }
                ]

        return {
            "focal_product": product_id,
            "market_regime": regime,
            "regime_confidence": regime_conf,
            "primary_driver": (
                {
                    "id": primary_driver["id"],
                    "name_en": primary_driver["name_en"],
                    "role": primary_driver["_role"],
                    "change_7d_pct": primary_driver["change_7d_pct"],
                    "confidence": primary_driver["_confidence"],
                    "hop": primary_driver.get("_hop", 1),
                    "hypothesis": primary_driver_label,
                    "hypothesis_confidence": primary_hypothesis_conf,
                }
                if primary_driver
                else None
            ),
            "secondary_drivers": [
                {
                    "id": d["id"],
                    "name_en": d["name_en"],
                    "role": d["_role"],
                    "change_7d_pct": d["change_7d_pct"],
                    "confidence": d["_confidence"],
                    "hop": d.get("_hop", 1),
                }
                for d in secondary_drivers
            ],
            "chain_impact": chain_impact,
            "evidence_sources": evidence_sources,
            "contradicting_factors": contradicting_factors,
            "downstream_propagated_signals": downstream_propagated_signals,
            "net_signal": net_signal,
            "demand_status": demand_status,
            "intelligence_signal": intelligence_signal,
            "erp_kpi_signal": erp_kpi_signal,
            "geopolitical_driver": geopolitical_driver,
            "divergence_detected": divergence_detected,
            "scored_hypotheses": [
                {
                    "hypothesis": h["hypothesis"],
                    "confidence": h["confidence"],
                    "direction": h.get("direction", "neutral"),
                }
                for h in scored_hypotheses[:4]
            ],
        }

    # ------------------------------------------------------------------
    # RAG hypothesis scoring helpers (§4.2.2)
    # ------------------------------------------------------------------

    def _generate_hypotheses(
        self,
        product_id: str,
        focal_data: Optional[Dict[str, Any]],
        regime: str,
        upstream_nodes: List[Dict[str, Any]],
        products_data: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Generate candidate driver hypotheses from market context."""
        hypotheses: List[Dict[str, Any]] = []

        # Naphtha cost push — if naphtha data available and rising
        naphtha = products_data.get("naphtha", {})
        if naphtha and naphtha.get("change_7d_pct", 0) > 0.5:
            hypotheses.append(
                {
                    "hypothesis": "naphtha_cost_push",
                    "direction": "bullish",
                    "query_pro": f"naphtha cost push price increase {product_id} C5 petrochemical",
                    "query_con": f"why naphtha cost push may not affect prices weak demand {product_id}",
                }
            )

        # Supply increase pressure — if upstream prices are falling
        falling_upstream = [
            n
            for n in upstream_nodes
            if products_data.get(n["id"], {}).get("change_7d_pct", 0) < -1.0
        ]
        if falling_upstream:
            hypotheses.append(
                {
                    "hypothesis": "supply_increase_pressure",
                    "direction": "bearish",
                    "query_pro": f"supply increase pressure oversupply {product_id} petrochemical",
                    "query_con": f"supply tightness shortage {product_id} demand strong",
                }
            )

        # Post-holiday demand recovery — seasonal (Feb–Mar)
        from datetime import UTC, datetime

        month = datetime.now(UTC).month
        if month in (2, 3):
            hypotheses.append(
                {
                    "hypothesis": "post_holiday_demand_recovery",
                    "direction": "bullish",
                    "query_pro": f"post holiday demand recovery restart {product_id} downstream",
                    "query_con": f"post holiday slow recovery weak demand downstream not started",
                }
            )

        # SINOPEC price leadership — if focal is a Formula C product
        if product_id in ("isoprene", "piperylene", "dcpd", "cracked_c5", "cracked_c9"):
            hypotheses.append(
                {
                    "hypothesis": "sinopec_price_leadership",
                    "direction": "bullish" if regime == "bullish" else "bearish",
                    "query_pro": f"SINOPEC raised listed price {product_id} anchor",
                    "query_con": f"SINOPEC discount market below listed price {product_id}",
                }
            )

        # Downstream demand recovery — generic
        if regime != "bearish":
            hypotheses.append(
                {
                    "hypothesis": "downstream_demand_recovery",
                    "direction": "bullish",
                    "query_pro": f"downstream demand recovery operating rate increase {product_id}",
                    "query_con": f"downstream weak demand inventory pressure {product_id}",
                }
            )

        return hypotheses

    def _score_hypotheses(
        self, hypotheses: List[Dict[str, Any]], product_id: str
    ) -> List[Dict[str, Any]]:
        """Score each hypothesis using RAG pro/con evidence retrieval."""
        scored: List[Dict[str, Any]] = []

        for hyp in hypotheses:
            try:
                pro_chunks = kb.query(hyp["query_pro"], n_results=3)
                con_chunks = kb.query(hyp["query_con"], n_results=2)
            except Exception:
                pro_chunks, con_chunks = [], []

            # Score: pro evidence boosts confidence, con evidence reduces it
            pro_count = len(pro_chunks)
            con_count = len(con_chunks)
            # Base confidence from evidence ratio
            if pro_count + con_count > 0:
                raw_score = pro_count / (pro_count + con_count)
            else:
                raw_score = 0.5
            # Scale to 0.3–0.95 range
            confidence = round(0.3 + raw_score * 0.65, 2)

            scored.append(
                {
                    "hypothesis": hyp["hypothesis"],
                    "direction": hyp["direction"],
                    "confidence": confidence,
                    "pro_evidence": [
                        {"text": chunk[:300], "score": round(0.7 + 0.1 * i, 2)}
                        for i, chunk in enumerate(pro_chunks)
                    ],
                    "con_evidence": [
                        {"text": chunk[:300], "score": round(0.6 + 0.1 * i, 2)}
                        for i, chunk in enumerate(con_chunks)
                    ],
                }
            )

        # Sort by confidence descending
        scored.sort(key=lambda x: x["confidence"], reverse=True)
        return scored

    def _propagate_downstream_signals(self, focal_product_id: str) -> Dict[str, str]:
        """Detect bullish downstream signals from RAG and propagate upstream (§5.3)."""
        signals: Dict[str, str] = {}

        # Find downstream products that depend on the focal product
        dependents: Dict[str, str] = {}
        for downstream, upstreams in PRODUCT_DEPENDENCY_GRAPH.items():
            if focal_product_id in upstreams:
                dependents[downstream] = downstream

        if not dependents:
            return signals

        # Query RAG for each dependent downstream product's latest reports
        for downstream_id, downstream_name in dependents.items():
            try:
                chunks = kb.query_weekly(
                    f"{downstream_name} demand operating rate restart",
                    product=downstream_id,
                    n_results=3,
                )
            except Exception:
                chunks = []

            # Scan chunks for bullish signal patterns
            matched_signals: List[str] = []
            for chunk in chunks:
                text = chunk.get("text", "")
                for pattern in DOWNSTREAM_BULLISH_SIGNALS:
                    if pattern.search(text):
                        matched_signals.append(pattern.pattern[:50])
                        break

            if matched_signals:
                deduped_signals = list(dict.fromkeys(matched_signals))[:2]
                signals[downstream_id] = f"supportive ({', '.join(deduped_signals)})"

        return signals

    def to_prompt_text(self, diagnosis: Dict[str, Any]) -> str:
        """Render the diagnosis dict as a compact prompt block."""
        lines = [
            f"DIAGNOSIS (STAGE 2) — {diagnosis['focal_product']}:",
            f"  Market Regime : {diagnosis['market_regime'].upper()} (confidence: {diagnosis.get('regime_confidence', '?')})",
            f"  Net Signal    : {diagnosis.get('net_signal', 'neutral').upper()}",
        ]
        pd = diagnosis.get("primary_driver")
        if pd:
            hop_label = f", hop {pd['hop']}" if pd.get("hop", 1) > 1 else ""
            hyp_label = (
                f", hypothesis: {pd.get('hypothesis', '?')} "
                f"(conf: {pd.get('hypothesis_confidence', '?')})"
                if pd.get("hypothesis")
                else ""
            )
            lines.append(
                f"  Primary Driver: {pd['name_en']} ({pd['role']}{hop_label}, "
                f"{pd['change_7d_pct']:+.1f}%w, conf: {pd['confidence']}{hyp_label})"
            )
        for sd in diagnosis.get("secondary_drivers", []):
            hop_label = f", hop {sd['hop']}" if sd.get("hop", 1) > 1 else ""
            lines.append(
                f"  Secondary     : {sd['name_en']} ({sd['role']}{hop_label}, "
                f"{sd['change_7d_pct']:+.1f}%w, conf: {sd['confidence']})"
            )
        if diagnosis.get("chain_impact"):
            lines.append("  Chain Impact  :")
            for ci in diagnosis["chain_impact"]:
                lines.append(f"    • {ci}")
        intelligence_signal = diagnosis.get("intelligence_signal") or {}
        if intelligence_signal.get("active"):
            lines.append(
                "  Intelligence  : "
                f"{intelligence_signal.get('bias', 'neutral').upper()} "
                f"{intelligence_signal.get('net_price_pct', 0):+.2f}% "
                f"({intelligence_signal.get('net_price_cny', 0):+.0f} CNY/ton), "
                f"conf: {intelligence_signal.get('confidence', 0):.2f}"
            )
        geo = diagnosis.get("geopolitical_driver") or {}
        if geo.get("triggered"):
            lines.append(
                "  Geopolitical  : "
                f"{geo.get('event', 'unknown')} | "
                f"crude {geo.get('crude_weekly_change_pct', 0):+.1f}%w, "
                f"naphtha {geo.get('naphtha_weekly_change_pct', 0):+.1f}%w | "
                f"duration~{geo.get('duration_estimate_days', '?')}d | "
                f"reversal_risk={geo.get('reversal_risk', 'MEDIUM')}"
            )
            if geo.get("pricing_implication"):
                lines.append(f"    • {geo['pricing_implication']}")
        divergence = diagnosis.get("divergence_detected") or {}
        if divergence:
            lines.append(
                "  Divergence    : "
                f"crude {divergence.get('crude_7d', 0):+.1f}%w vs "
                f"naphtha {divergence.get('naphtha_7d', 0):+.1f}%w "
                f"(gap {divergence.get('gap', 0):+.1f}%)"
            )
            hint = str(divergence.get("explanation_hint") or "").strip()
            if hint:
                lines.append(f"    • {hint}")
            for idx, ev in enumerate((divergence.get("evidence") or [])[:2], start=1):
                txt = str((ev or {}).get("text") or "").strip()
                if txt:
                    lines.append(f"    • weekly_evidence_{idx}: {txt[:140]}")
        erp_kpi_signal = diagnosis.get("erp_kpi_signal") or {}
        if erp_kpi_signal.get("active"):
            lines.append(
                "  ERP KPI       : "
                f"{erp_kpi_signal.get('bias', 'neutral').upper()} "
                f"{erp_kpi_signal.get('adjustment_pct', {}).get('7d', 0):+.2f}%"
            )
            if erp_kpi_signal.get("summary"):
                lines.append(f"    • {erp_kpi_signal['summary']}")
        # Evidence sources
        evidence = diagnosis.get("evidence_sources", [])
        if evidence:
            lines.append("  Evidence      :")
            for ev in evidence[:3]:
                lines.append(f"    ✓ {ev.get('text', '')[:120]}")
        # Contradicting factors
        contradictions = diagnosis.get("contradicting_factors", [])
        if contradictions:
            lines.append("  Contradictions:")
            for cf in contradictions[:2]:
                lines.append(f"    ✗ {cf[:120]}")
        # Downstream propagated signals
        downstream = diagnosis.get("downstream_propagated_signals", {})
        if downstream:
            lines.append("  Downstream Signals:")
            for prod, sig in downstream.items():
                lines.append(f"    → {prod}: {sig}")
        return "\n".join(lines)


# Singleton instance
diagnosis_service = DiagnosisService()
