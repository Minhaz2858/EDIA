from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Dict, Any, List, Optional

import numpy as np
from sklearn.ensemble import IsolationForest

from backend.src.domain.knowledge_graph import domain_graph
from backend.src.rag.vector_store import kb
from backend.src.service.market_data_service import market_data


# ---------------------------------------------------------------------------
# IsolationForest anomaly detection helpers
# ---------------------------------------------------------------------------

_ANOMALY_WINDOW = 90  # days of history to fit the model
_CONTAMINATION = 0.05  # expected fraction of anomalies


def _detect_anomalies(
    price_history: List[Dict], window: int = _ANOMALY_WINDOW
) -> Dict[str, Any]:
    """Run IsolationForest on recent price history and flag anomalous days.

    Returns a dict with:
      - is_anomaly: bool — whether the latest data point is anomalous
      - anomaly_score: float — isolation score (-1 = most anomalous, 1 = normal)
      - anomalous_days: list of dates flagged as anomalies in the window
    """
    if len(price_history) < 10:
        return {"is_anomaly": False, "anomaly_score": 0.0, "anomalous_days": []}

    recent = price_history[-window:]
    prices = np.array([h["price"] for h in recent]).reshape(-1, 1)

    # Build features: price, daily pct change, 5-day rolling std
    pct_changes = (
        np.diff(prices.flatten(), prepend=prices[0, 0])
        / np.maximum(prices.flatten(), 1)
        * 100
    )
    rolling_std = np.array(
        [
            np.std(prices[max(0, i - 5) : i + 1]) if i >= 1 else 0.0
            for i in range(len(prices))
        ]
    )
    features = np.column_stack([prices.flatten(), pct_changes, rolling_std])

    model = IsolationForest(
        contamination=_CONTAMINATION,
        random_state=42,
        n_estimators=100,
    )
    labels = model.fit_predict(features)  # -1 = anomaly, 1 = normal
    scores = model.decision_function(features)

    anomalous_days = [recent[i]["date"] for i in range(len(labels)) if labels[i] == -1]

    return {
        "is_anomaly": bool(labels[-1] == -1),
        "anomaly_score": round(float(scores[-1]), 4),
        "anomalous_days": anomalous_days[-5:],  # last 5 flagged dates
    }


class PerceptionService:
    """Stage 1 — Perception: build a market snapshot with anomaly detection.

    - Pull latest prices and short-term change from MySQL-backed market data.
    - Run IsolationForest anomaly detection on 90-day price windows.
    - Query ChromaDB for historical pattern matches (§4.1.4).
    - Merge macro regime signals from override, weekly reports, anomalies (§4.1.5).
    - Return a structured snapshot with per-product anomaly flags.
    """

    def build_snapshot(self, cutoff_date: Optional[str] = None) -> Dict[str, Any]:
        products: Dict[str, Any] = {}
        anomaly_labels: List[str] = []

        # Phase D: Check for recent high-impact intelligence events
        intelligence_events = self._query_recent_intelligence_events(hours=4)
        intelligence_alert_active = bool(intelligence_events)

        for node in domain_graph.nodes:
            history = market_data.get_price_history(node.id, cutoff_date=cutoff_date)
            if not history:
                continue

            latest = history[-1]
            latest_price = latest["price"]
            latest_date = latest["date"]

            # 7-day change (or from first available point if less history)
            if len(history) > 7:
                base_price = history[-8]["price"]
            else:
                base_price = history[0]["price"]

            if base_price:
                change_7d_pct = round((latest_price - base_price) / base_price * 100, 2)
            else:
                change_7d_pct = 0.0

            # IsolationForest anomaly detection
            try:
                anomaly = _detect_anomalies(history)
            except Exception:
                anomaly = {
                    "is_anomaly": False,
                    "anomaly_score": 0.0,
                    "anomalous_days": [],
                }

            if anomaly.get("is_anomaly"):
                anomaly_labels.append(f"{node.id}_price_anomaly")

            # Naphtha spike check
            if node.id == "naphtha" and abs(change_7d_pct) > 2.5:
                direction = "spike" if change_7d_pct > 0 else "drop"
                anomaly_labels.append(f"naphtha_{direction}_{change_7d_pct:+.1f}%")

            products[node.id] = {
                "id": node.id,
                "name_en": node.name_en,
                "name_cn": node.name_cn,
                "unit": node.unit,
                "latest_price": latest_price,
                "latest_date": latest_date,
                "change_7d_pct": change_7d_pct,
                "anomaly": anomaly,
                "data_source": (
                    "real" if market_data.is_real_data(node.id) else "missing"
                ),
            }

        # ── Historical pattern matching (§4.1.4) via ChromaDB market_signals
        historical_matches = self._find_historical_matches(products)

        # ── Macro regime integration (§4.1.5)
        crude_trend, naphtha_trend, market_phase = self._compute_macro_regime(products)

        # ── Alert level based on anomaly count + naphtha movement
        alert_level = self._compute_alert_level(anomaly_labels, products)

        # Phase D: Elevate alert level if high-impact intelligence events are active
        if intelligence_alert_active:
            alert_level = "ALERT"

        snapshot: Dict[str, Any] = {
            "reference_date": (
                cutoff_date if cutoff_date else datetime.now().strftime("%Y-%m-%d")
            ),
            "products": products,
            "anomalies": anomaly_labels,
            "historical_matches": historical_matches,
            "alert_level": alert_level,
            "market_phase": market_phase,
            "crude_trend": crude_trend,
            "naphtha_trend": naphtha_trend,
        }

        if intelligence_events:
            snapshot["intelligence_events"] = intelligence_events

        return snapshot

    def _find_historical_matches(
        self, products: Dict[str, Any], n_results: int = 3
    ) -> List[str]:
        """Query market_signals for similar historical weeks (§4.1.4)."""
        # Build a query string describing the current situation
        naphtha = products.get("naphtha", {})
        crude = products.get("crude_oil") or products.get("naphtha", {})
        naphtha_chg = naphtha.get("change_7d_pct", 0)
        crude_chg = crude.get("change_7d_pct", 0)

        query = (
            f"naphtha {naphtha_chg:+.1f}% crude {crude_chg:+.1f}% "
            f"C5 petrochemical market week"
        )

        try:
            hits = kb.query_market_signals(query, n_results=n_results)
            return [h.get("text", "")[:200] for h in hits if h.get("text")]
        except Exception:
            return []

    def _compute_macro_regime(self, products: Dict[str, Any]) -> tuple[str, str, str]:
        """Merge macro signals from override → weekly reports → price data (§4.1.5).

        Returns (crude_trend, naphtha_trend, market_phase).
        """
        # Priority 1: Check macro_override.json
        try:
            from backend.src.service.macro_override_service import get_active_override

            override = get_active_override()
            if override:
                return (
                    override.get("crude_trend", "flat"),
                    override.get("naphtha_trend", "flat"),
                    override.get("market_phase", "normal"),
                )
        except Exception:
            pass

        # Priority 2: Latest weekly report macro signals from RAG
        try:
            weekly_hits = kb.query_weekly(
                "crude oil naphtha trend macro market phase", n_results=1
            )
            if weekly_hits:
                meta = weekly_hits[0].get("metadata", {})
                if meta.get("market_phase"):
                    return (
                        meta.get("crude_trend", "flat"),
                        meta.get("naphtha_trend", "flat"),
                        meta.get("market_phase", "normal"),
                    )
        except Exception:
            pass

        # Priority 3: Derive from quantitative price data
        crude = products.get("crude_oil") or products.get("naphtha", {})
        naphtha = products.get("naphtha", {})
        crude_chg = crude.get("change_7d_pct", 0)
        naphtha_chg = naphtha.get("change_7d_pct", 0)

        crude_trend = "up" if crude_chg > 1 else ("down" if crude_chg < -1 else "flat")
        naphtha_trend = (
            "up" if naphtha_chg > 1 else ("down" if naphtha_chg < -1 else "flat")
        )

        # Market phase heuristic from calendar
        month = datetime.now(UTC).month
        if month in (1, 2):
            market_phase = "post_holiday_restart"
        elif month == 12:
            market_phase = "pre_holiday"
        else:
            market_phase = "normal"

        return crude_trend, naphtha_trend, market_phase

    def _compute_alert_level(
        self, anomaly_labels: List[str], products: Dict[str, Any]
    ) -> str:
        """Compute alert level: NORMAL / WATCH / ALERT (§4.1.6)."""
        naphtha = products.get("naphtha", {})
        naphtha_chg = abs(naphtha.get("change_7d_pct", 0))

        anomaly_count = len(anomaly_labels)

        if anomaly_count >= 3 or naphtha_chg >= 5.0:
            return "ALERT"
        elif anomaly_count >= 1 or naphtha_chg >= 2.5:
            return "WATCH"
        return "NORMAL"

    def _query_recent_intelligence_events(
        self, hours: int = 4
    ) -> List[Dict[str, Any]]:
        """Query the intelligence registry for recent high-impact events."""
        try:
            from backend.src.intelligence.event_registry import (
                intelligence_event_registry,
            )

            events = intelligence_event_registry.list_recent_events(
                hours=hours,
                min_impact=0.50,
                limit=10,
            )
            return [
                {
                    "event_id": e.event_id,
                    "headline": e.headline,
                    "event_type": e.event_type,
                    "certainty": e.certainty,
                    "source_credibility": e.source_credibility,
                    "direction": e.direction,
                    "impact_magnitude": e.impact_magnitude,
                    "detected_at": e.detected_at,
                    "source_url": e.source_url,
                    "raw_quote": e.raw_quote,
                }
                for e in events
                if e.certainty in {"confirmed", "likely"}
            ]
        except Exception:
            return []


# Singleton instance
perception_service = PerceptionService()
