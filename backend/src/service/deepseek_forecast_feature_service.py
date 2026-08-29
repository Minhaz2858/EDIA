from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable

from sqlalchemy import text

from backend.src.db.sqlite_db import db
from backend.src.service.market_data_service import market_data

logger = logging.getLogger(__name__)


FORECAST_FEATURE_NODE_IDS: tuple[str, ...] = (
    "crude_oil",
    "naphtha",
    "cracked_c5",
    "mixed_c5",
    "cracked_c9",
    "mixed_c9",
    "isoprene",
    "piperylene",
    "dcpd",
    "raffinate_c5",
    "sis",
    "sis_rubber",
    "blowing_agent",
    "c5_resin",
    "hydrogenated_c9",
    "trimethylbenzene",
)

FEATURE_WINDOWS_HOURS: tuple[int, ...] = (6, 12, 24, 72, 168)
DEFAULT_TTL_SECONDS = 6 * 60 * 60


def _utcnow() -> datetime:
    return datetime.utcnow()


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: Any, *, low: float, high: float, default: float = 0.0) -> float:
    numeric = _safe_float(value, default)
    if numeric is None:
        numeric = default
    return round(max(low, min(high, numeric)), 4)


class DeepSeekForecastFeatureService:
    """Build and cache 6-hour DeepSeek market features for daily forecasts.

    DeepSeek is used as a structured feature extractor over all forecast nodes and
    recent event windows. It does not directly set final forecast prices.
    """

    def __init__(self, llm_service: Any | None = None, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self.llm = llm_service
        self.ttl_seconds = int(ttl_seconds)
        self.engine = db.engine
        self._lock = threading.Lock()
        self._init_tables()

    def _init_tables(self) -> None:
        ddl = text(
            """
            CREATE TABLE IF NOT EXISTS forecast_feature_snapshots (
                snapshot_id VARCHAR(64) NOT NULL PRIMARY KEY,
                generated_at VARCHAR(32) NOT NULL,
                expires_at VARCHAR(32) NOT NULL,
                model VARCHAR(120),
                status VARCHAR(32) NOT NULL,
                node_scope TEXT,
                input_fingerprint VARCHAR(64),
                input_json TEXT,
                feature_json TEXT,
                error TEXT
            )
            """
        )
        idx = text(
            "CREATE INDEX IF NOT EXISTS idx_forecast_feature_snapshots_generated_at "
            "ON forecast_feature_snapshots (generated_at)"
        )
        with self.engine.begin() as conn:
            conn.execute(ddl)
            for ddl_statement in (
                "ALTER TABLE forecast_feature_snapshots MODIFY COLUMN input_json LONGTEXT",
                "ALTER TABLE forecast_feature_snapshots MODIFY COLUMN feature_json LONGTEXT",
                "ALTER TABLE forecast_feature_snapshots MODIFY COLUMN node_scope TEXT",
                "ALTER TABLE forecast_feature_snapshots MODIFY COLUMN error TEXT",
            ):
                try:
                    conn.execute(text(ddl_statement))
                except Exception:
                    pass
            try:
                conn.execute(idx)
            except Exception:
                pass

    @staticmethod
    def _history_change(history: list[dict[str, Any]], days_back: int) -> float | None:
        if len(history) < 2:
            return None
        latest = _safe_float(history[-1].get("price"))
        if latest is None:
            return None
        idx = max(0, len(history) - 1 - days_back)
        previous = _safe_float(history[idx].get("price"))
        if previous in (None, 0.0):
            return None
        return round((latest - previous) / previous * 100.0, 4)

    def _product_payload(self, product_id: str) -> dict[str, Any]:
        from backend.src.service.forecasting_engine import get_cadence_info, get_price_series_quality

        history = market_data.get_price_history(product_id)
        history = sorted(history, key=lambda item: str(item.get("date") or ""))[-45:]
        latest = history[-1] if history else {}
        latest_price = _safe_float(latest.get("price"))

        return {
            "product_id": product_id,
            "latest_date": latest.get("date"),
            "latest_price": latest_price,
            "change_1d_pct": self._history_change(history, 1),
            "change_7d_pct": self._history_change(history, 7),
            "change_30d_pct": self._history_change(history, 30),
            "cadence_info": get_cadence_info(product_id),
            "data_quality": get_price_series_quality(product_id, days=90),
        }

    @staticmethod
    def _event_to_dict(event: Any) -> dict[str, Any]:
        if isinstance(event, dict):
            payload = dict(event)
        else:
            to_dict = getattr(event, "to_dict", None)
            payload = to_dict() if callable(to_dict) else {}
            if not isinstance(payload, dict):
                payload = {}

        return {
            "event_id": payload.get("event_id"),
            "event_type": payload.get("event_type"),
            "headline": payload.get("headline"),
            "affected_commodities": payload.get("affected_commodities", []),
            "direction": payload.get("direction"),
            "certainty": payload.get("certainty"),
            "time_horizon": payload.get("time_horizon"),
            "geographic_scope": payload.get("geographic_scope"),
            "relevance_to_c5_c9": payload.get("relevance_to_c5_c9"),
            "impact_magnitude": payload.get("impact_magnitude"),
            "relevance_score": payload.get("relevance_score"),
            "review_status": payload.get("review_status"),
            "usefulness_score": payload.get("usefulness_score"),
            "detected_at": payload.get("detected_at"),
            "source_url": payload.get("source_url"),
            "key_information": payload.get("key_information") or payload.get("raw_quote"),
            "causal_chain_hint": payload.get("causal_chain_hint"),
        }

    def _event_windows_payload(self) -> dict[str, list[dict[str, Any]]]:
        try:
            from backend.src.intelligence.event_registry import intelligence_event_registry
        except Exception as exc:
            logger.warning("DeepSeek feature event registry unavailable: %s", exc)
            return {f"{hours}h": [] for hours in FEATURE_WINDOWS_HOURS}

        windows: dict[str, list[dict[str, Any]]] = {}
        for hours in FEATURE_WINDOWS_HOURS:
            try:
                events = intelligence_event_registry.list_recent_events(hours=hours, limit=80)
            except Exception as exc:
                logger.warning("DeepSeek feature event query failed for %sh: %s", hours, exc)
                events = []
            windows[f"{hours}h"] = [self._event_to_dict(event) for event in events]
        return windows

    def build_input_payload(self) -> dict[str, Any]:
        products = {product_id: self._product_payload(product_id) for product_id in FORECAST_FEATURE_NODE_IDS}
        return {
            "generated_at": _utcnow().isoformat(),
            "node_scope": list(FORECAST_FEATURE_NODE_IDS),
            "lookback_windows_hours": list(FEATURE_WINDOWS_HOURS),
            "products": products,
            "events_by_window": self._event_windows_payload(),
            "instructions": {
                "role": "DeepSeek is a structured market feature extractor, not the final price calculator.",
                "output": "Return JSON features only. Do not invent events or prices.",
            },
        }

    @staticmethod
    def input_fingerprint(payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _storage_input_payload(payload: dict[str, Any]) -> dict[str, Any]:
        products = payload.get("products") if isinstance(payload.get("products"), dict) else {}
        windows = payload.get("events_by_window") if isinstance(payload.get("events_by_window"), dict) else {}
        return {
            "generated_at": payload.get("generated_at"),
            "node_scope": payload.get("node_scope", []),
            "lookback_windows_hours": payload.get("lookback_windows_hours", []),
            "product_inputs": {
                product_id: {
                    "latest_date": product_payload.get("latest_date"),
                    "latest_price": product_payload.get("latest_price"),
                    "change_1d_pct": product_payload.get("change_1d_pct"),
                    "change_7d_pct": product_payload.get("change_7d_pct"),
                    "change_30d_pct": product_payload.get("change_30d_pct"),
                    "cadence_class": (product_payload.get("cadence_info") or {}).get("cadence_class"),
                    "native_step_days": (product_payload.get("cadence_info") or {}).get("native_step_days"),
                    "quality_status": (product_payload.get("data_quality") or {}).get("status"),
                }
                for product_id, product_payload in products.items()
                if isinstance(product_payload, dict)
            },
            "event_counts_by_window": {
                window: len(events) if isinstance(events, list) else 0
                for window, events in windows.items()
            },
        }

    @staticmethod
    def system_prompt() -> str:
        return """
SANITIZED_FOR_EXTERNAL_LLM=YES
You are DeepSeek acting as EDIA's professional petrochemical forecasting feature extractor.
You read all 16 forecast nodes, recent market events, sentiment clues, supply/demand signals,
feedstock relationships, and event decay windows. You MUST output valid JSON only.

Important rules:
- Do not invent source prices, event IDs, dates, or news.
- Do not output final forecast prices.
- Convert the provided facts into structured numeric features usable by ARIMA/XGBoost.
- Consider event decay: 6h events matter most, but 12h/24h/72h/168h events may still affect forecasts.
- Use low confidence when evidence is weak or source data is sparse.
- Every product in node_scope must have one feature object.

Return schema:
{
  "global_market_regime": "bullish|bearish|mixed|neutral|uncertain",
  "global_risk_score": 0.0,
  "products": {
    "product_id": {
      "supply_disruption_score": 0.0,
      "demand_strength_score": 0.0,
      "sentiment_6h": 0.0,
      "sentiment_24h": 0.0,
      "sentiment_7d": 0.0,
      "recent_event_impact": 0.0,
      "event_decay_score": 0.0,
      "feedstock_pressure_score": 0.0,
      "inventory_pressure_score": 0.0,
      "geopolitical_risk_score": 0.0,
      "directional_bias_pct": 0.0,
      "horizon_bias_pct": {"1d": 0.0, "3d": 0.0, "7d": 0.0, "14d": 0.0, "30d": 0.0},
      "confidence": 0.0,
      "evidence_event_ids": [],
      "reason_codes": []
    }
  }
}
""".strip()

    @staticmethod
    def user_prompt(input_payload: dict[str, Any]) -> str:
        return "INPUT_JSON:\n" + json.dumps(input_payload, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def _neutral_product_features(product_id: str, reason: str = "no_deepseek_signal") -> dict[str, Any]:
        return {
            "product_id": product_id,
            "supply_disruption_score": 0.0,
            "demand_strength_score": 0.0,
            "sentiment_6h": 0.0,
            "sentiment_24h": 0.0,
            "sentiment_7d": 0.0,
            "recent_event_impact": 0.0,
            "event_decay_score": 0.0,
            "feedstock_pressure_score": 0.0,
            "inventory_pressure_score": 0.0,
            "geopolitical_risk_score": 0.0,
            "directional_bias_pct": 0.0,
            "horizon_bias_pct": {"1d": 0.0, "3d": 0.0, "7d": 0.0, "14d": 0.0, "30d": 0.0},
            "confidence": 0.0,
            "evidence_event_ids": [],
            "reason_codes": [reason],
        }

    def _fallback_features(self, reason: str) -> dict[str, Any]:
        return {
            "global_market_regime": "uncertain",
            "global_risk_score": 0.0,
            "products": {
                product_id: self._neutral_product_features(product_id, reason)
                for product_id in FORECAST_FEATURE_NODE_IDS
            },
        }

    def _normalise_product_features(self, product_id: str, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            return self._neutral_product_features(product_id, "invalid_deepseek_product_features")

        horizon_raw = raw.get("horizon_bias_pct") if isinstance(raw.get("horizon_bias_pct"), dict) else {}
        evidence = raw.get("evidence_event_ids") if isinstance(raw.get("evidence_event_ids"), list) else []
        reason_codes = raw.get("reason_codes") if isinstance(raw.get("reason_codes"), list) else []

        return {
            "product_id": product_id,
            "supply_disruption_score": _clamp(raw.get("supply_disruption_score"), low=-1.0, high=1.0),
            "demand_strength_score": _clamp(raw.get("demand_strength_score"), low=-1.0, high=1.0),
            "sentiment_6h": _clamp(raw.get("sentiment_6h"), low=-1.0, high=1.0),
            "sentiment_24h": _clamp(raw.get("sentiment_24h"), low=-1.0, high=1.0),
            "sentiment_7d": _clamp(raw.get("sentiment_7d"), low=-1.0, high=1.0),
            "recent_event_impact": _clamp(raw.get("recent_event_impact"), low=-1.0, high=1.0),
            "event_decay_score": _clamp(raw.get("event_decay_score"), low=0.0, high=1.0),
            "feedstock_pressure_score": _clamp(raw.get("feedstock_pressure_score"), low=-1.0, high=1.0),
            "inventory_pressure_score": _clamp(raw.get("inventory_pressure_score"), low=-1.0, high=1.0),
            "geopolitical_risk_score": _clamp(raw.get("geopolitical_risk_score"), low=0.0, high=1.0),
            "directional_bias_pct": _clamp(raw.get("directional_bias_pct"), low=-8.0, high=8.0),
            "horizon_bias_pct": {
                "1d": _clamp(horizon_raw.get("1d"), low=-8.0, high=8.0),
                "3d": _clamp(horizon_raw.get("3d"), low=-8.0, high=8.0),
                "7d": _clamp(horizon_raw.get("7d"), low=-8.0, high=8.0),
                "14d": _clamp(horizon_raw.get("14d"), low=-8.0, high=8.0),
                "30d": _clamp(horizon_raw.get("30d"), low=-8.0, high=8.0),
            },
            "confidence": _clamp(raw.get("confidence"), low=0.0, high=1.0),
            "evidence_event_ids": [str(item) for item in evidence if str(item or "").strip()][:20],
            "reason_codes": [str(item) for item in reason_codes if str(item or "").strip()][:12],
        }

    def normalise_features(self, raw_features: Any) -> dict[str, Any]:
        if not isinstance(raw_features, dict):
            return self._fallback_features("invalid_deepseek_response")

        raw_products = raw_features.get("products") if isinstance(raw_features.get("products"), dict) else {}
        return {
            "global_market_regime": str(raw_features.get("global_market_regime") or "uncertain"),
            "global_risk_score": _clamp(raw_features.get("global_risk_score"), low=0.0, high=1.0),
            "products": {
                product_id: self._normalise_product_features(product_id, raw_products.get(product_id))
                for product_id in FORECAST_FEATURE_NODE_IDS
            },
        }

    def _call_llm(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        if not self.llm:
            raise RuntimeError("DeepSeek feature extractor has no LLM service configured")
        return self.llm.generate_json(
            self.system_prompt(),
            self.user_prompt(input_payload),
            route_hint="external",
            sanitized=True,
        )

    def _persist_snapshot(
        self,
        *,
        snapshot_id: str,
        generated_at: str,
        expires_at: str,
        model: str,
        status: str,
        input_payload: dict[str, Any],
        input_fingerprint: str,
        features: dict[str, Any],
        error: str = "",
    ) -> None:
        stmt = text(
            """
            INSERT INTO forecast_feature_snapshots
                (snapshot_id, generated_at, expires_at, model, status, node_scope,
                 input_fingerprint, input_json, feature_json, error)
            VALUES
                (:snapshot_id, :generated_at, :expires_at, :model, :status, :node_scope,
                 :input_fingerprint, :input_json, :feature_json, :error)
            """
        )
        with self.engine.begin() as conn:
            conn.execute(
                stmt,
                {
                    "snapshot_id": snapshot_id,
                    "generated_at": generated_at,
                    "expires_at": expires_at,
                    "model": model,
                    "status": status,
                    "node_scope": json.dumps(list(FORECAST_FEATURE_NODE_IDS), ensure_ascii=False),
                    "input_fingerprint": input_fingerprint,
                    "input_json": json.dumps(self._storage_input_payload(input_payload), ensure_ascii=False, default=str),
                    "feature_json": json.dumps(features, ensure_ascii=False, default=str),
                    "error": error[:2000],
                },
            )

    def refresh_snapshot(self) -> dict[str, Any]:
        with self._lock:
            generated_dt = _utcnow()
            generated_at = generated_dt.isoformat()
            expires_at = (generated_dt + timedelta(seconds=self.ttl_seconds)).isoformat()
            input_payload = self.build_input_payload()
            fingerprint = self.input_fingerprint(input_payload)
            model = str(getattr(self.llm, "external_model", None) or getattr(self.llm, "model", "deepseek"))
            snapshot_id = f"fs_{uuid.uuid4().hex[:16]}"
            status = "success"
            error = ""

            try:
                raw_features = self._call_llm(input_payload)
                features = self.normalise_features(raw_features)
            except Exception as exc:
                status = "fallback"
                error = str(exc)
                logger.warning("DeepSeek feature snapshot fallback: %s", exc)
                features = self._fallback_features("deepseek_unavailable")

            self._persist_snapshot(
                snapshot_id=snapshot_id,
                generated_at=generated_at,
                expires_at=expires_at,
                model=model,
                status=status,
                input_payload=input_payload,
                input_fingerprint=fingerprint,
                features=features,
                error=error,
            )
            return {
                "snapshot_id": snapshot_id,
                "generated_at": generated_at,
                "expires_at": expires_at,
                "model": model,
                "status": status,
                "input_fingerprint": fingerprint,
                "features": features,
                "error": error,
            }

    def get_latest_snapshot(self, *, include_expired: bool = True) -> dict[str, Any] | None:
        stmt = text(
            """
            SELECT snapshot_id, generated_at, expires_at, model, status,
                   input_fingerprint, feature_json, error
            FROM forecast_feature_snapshots
            ORDER BY generated_at DESC
            LIMIT 1
            """
        )
        with self.engine.connect() as conn:
            row = conn.execute(stmt).mappings().first()
        if not row:
            return None

        if not include_expired:
            try:
                if datetime.fromisoformat(str(row.get("expires_at"))) < _utcnow():
                    return None
            except Exception:
                return None

        try:
            features = json.loads(row.get("feature_json") or "{}")
        except Exception:
            features = self._fallback_features("stored_feature_json_invalid")

        return {
            "snapshot_id": row.get("snapshot_id"),
            "generated_at": row.get("generated_at"),
            "expires_at": row.get("expires_at"),
            "model": row.get("model"),
            "status": row.get("status"),
            "input_fingerprint": row.get("input_fingerprint"),
            "features": features,
            "error": row.get("error") or "",
        }

    def get_product_features(self, product_id: str, *, include_expired: bool = True) -> dict[str, Any]:
        snapshot = self.get_latest_snapshot(include_expired=include_expired)
        if not snapshot:
            return {
                "snapshot_id": None,
                "status": "missing",
                "generated_at": None,
                "expires_at": None,
                "model": None,
                "feature": self._neutral_product_features(product_id, "no_feature_snapshot"),
            }
        products = (snapshot.get("features") or {}).get("products") or {}
        feature = products.get(product_id) or self._neutral_product_features(product_id, "product_missing_from_snapshot")
        return {
            "snapshot_id": snapshot.get("snapshot_id"),
            "status": snapshot.get("status"),
            "generated_at": snapshot.get("generated_at"),
            "expires_at": snapshot.get("expires_at"),
            "model": snapshot.get("model"),
            "feature": feature,
        }


deepseek_forecast_feature_service = DeepSeekForecastFeatureService()