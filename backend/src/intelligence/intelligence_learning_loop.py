"""Intelligence Learning Loop — self-calibrating feedback system.

After an event resolves, record the actual outcome and use it to:
1. Update elasticity calibration (was the price impact ±X% as predicted?)
2. Update scenario probability base rates (did the event resolve fast/slowly?)
3. Add the outcome to the historical event impact database
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class IntelligenceLearningLoop:
    """Records event outcomes and calibrates future predictions."""

    def __init__(self):
        self._outcome_log: list[dict] = []

    def record_event_outcome(
        self,
        event_id: str,
        event_type: str,
        predicted_naphtha_pct: float,
        actual_naphtha_pct: float,
        resolution_days: int,
        resolution_type: str = "gradual",  # "rapid" | "gradual" | "structural"
        notes: str = "",
    ) -> dict:
        """Record what actually happened after an event was detected.

        This is the critical feedback signal for calibration.
        """
        prediction_error = actual_naphtha_pct - predicted_naphtha_pct
        accuracy_score = max(
            0, 1.0 - abs(prediction_error) / max(abs(predicted_naphtha_pct), 0.5)
        )

        outcome = {
            "event_id": event_id,
            "event_type": event_type,
            "predicted_naphtha_pct": predicted_naphtha_pct,
            "actual_naphtha_pct": actual_naphtha_pct,
            "prediction_error": round(prediction_error, 3),
            "accuracy_score": round(accuracy_score, 3),
            "resolution_days": resolution_days,
            "resolution_type": resolution_type,
            "notes": notes,
            "recorded_at": datetime.utcnow().isoformat(),
        }

        self._outcome_log.append(outcome)

        # Update subsystems
        self._update_elasticity_calibration(outcome)
        self._update_scenario_base_rates(outcome)
        self._update_event_history(outcome)
        self._store_in_chromadb(outcome)

        logger.info(
            "Outcome recorded: event=%s predicted=%+.2f%% actual=%+.2f%% accuracy=%.0f%%",
            event_id[:16],
            predicted_naphtha_pct,
            actual_naphtha_pct,
            accuracy_score * 100,
        )

        return outcome

    def _update_elasticity_calibration(self, outcome: dict) -> None:
        """Adjust elasticity estimates based on observed vs predicted impact."""
        try:
            from backend.src.intelligence.causal_narrative_engine import (
                get_causal_engine,
            )

            engine = get_causal_engine()
            if engine is None:
                return

            error = outcome["prediction_error"]
            event_type = outcome["event_type"]

            # Apply Exponential Moving Average calibration
            # If we consistently under-predict, increase elasticities by small amount
            alpha = 0.1  # learning rate
            if abs(error) > 0.3:  # only calibrate if meaningful error
                adjustment = error * alpha
                for product_id, elasticity in engine._NAPHTHA_ELASTICITIES.items():
                    current = elasticity.get("elasticity", 0.5)
                    new_val = max(0.1, min(1.0, current + adjustment * 0.1))
                    elasticity["elasticity"] = round(new_val, 3)

                logger.info(
                    "Elasticity calibration: event_type=%s error=%+.2f%% adjustment=%+.4f",
                    event_type,
                    error,
                    adjustment,
                )
        except Exception as e:
            logger.debug("Elasticity calibration skipped: %s", e)

    def _update_scenario_base_rates(self, outcome: dict) -> None:
        """Update scenario probability base rates based on resolution speed."""
        try:
            from backend.src.intelligence.scenario_probability_engine import (
                _HISTORICAL_BASE_RATES,
            )

            event_type = outcome["event_type"]
            if event_type not in _HISTORICAL_BASE_RATES:
                return

            rates = _HISTORICAL_BASE_RATES[event_type]
            resolution = outcome["resolution_type"]
            alpha = 0.05  # conservative learning rate

            # Shift base rates toward observed resolution type
            if resolution == "rapid":
                rates["resolves_in_7d"] += alpha
                rates["structural"] -= alpha * 0.5
                rates["resolves_in_14d"] -= alpha * 0.5
            elif resolution == "structural":
                rates["structural"] += alpha
                rates["resolves_in_7d"] -= alpha * 0.5
                rates["resolves_in_14d"] -= alpha * 0.5

            # Normalize
            total = sum(rates.values())
            for k in rates:
                rates[k] = round(max(0.05, rates[k] / total), 3)

            logger.info(
                "Scenario base rates updated for %s: %s",
                event_type,
                rates,
            )
        except Exception as e:
            logger.debug("Scenario calibration skipped: %s", e)

    def _update_event_history(self, outcome: dict) -> None:
        """Add the outcome to the historical event impact database."""
        try:
            from backend.src.intelligence.event_impact_db import event_impact_db

            event_impact_db.add_observed_outcome(
                {
                    "event_id": outcome["event_id"],
                    "event_type": outcome["event_type"],
                    "naphtha_impact_pct": outcome["actual_naphtha_pct"],
                    "duration_days": outcome["resolution_days"],
                    "resolution": outcome["resolution_type"],
                }
            )
        except Exception as e:
            logger.debug("Event history update skipped: %s", e)

    def _store_in_chromadb(self, outcome: dict) -> None:
        """Persist outcome in ChromaDB for future RAG retrieval."""
        try:
            from backend.src.rag.vector_store import kb

            kb.store_decision_outcome(
                {
                    "decision_id": outcome["event_id"],
                    "product": "naphtha",
                    "recommended_price": 0,
                    "actual_price": 0,
                    "deviation_pct": outcome["prediction_error"],
                    "quality": (
                        "good"
                        if outcome["accuracy_score"] > 0.7
                        else (
                            "acceptable" if outcome["accuracy_score"] > 0.4 else "poor"
                        )
                    ),
                }
            )
        except Exception as e:
            logger.debug("ChromaDB outcome storage skipped: %s", e)

    def get_calibration_summary(self) -> dict:
        """Return summary of calibration state and recent outcomes."""
        if not self._outcome_log:
            return {
                "total_outcomes": 0,
                "avg_accuracy": None,
                "avg_prediction_error": None,
                "recent_outcomes": [],
            }

        accuracies = [o["accuracy_score"] for o in self._outcome_log]
        errors = [o["prediction_error"] for o in self._outcome_log]

        return {
            "total_outcomes": len(self._outcome_log),
            "avg_accuracy": round(sum(accuracies) / len(accuracies), 3),
            "avg_prediction_error": round(sum(errors) / len(errors), 3),
            "recent_outcomes": [
                {
                    "event_id": o["event_id"][:16],
                    "event_type": o["event_type"],
                    "predicted": f"{o['predicted_naphtha_pct']:+.2f}%",
                    "actual": f"{o['actual_naphtha_pct']:+.2f}%",
                    "accuracy": f"{o['accuracy_score']:.0%}",
                }
                for o in self._outcome_log[-10:]
            ],
        }


# Module-level singleton
learning_loop = IntelligenceLearningLoop()
