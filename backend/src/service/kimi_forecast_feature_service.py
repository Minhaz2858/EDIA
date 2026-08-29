from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from backend.src.service.deepseek_forecast_feature_service import (
    DEFAULT_TTL_SECONDS,
    FORECAST_FEATURE_NODE_IDS,
    DeepSeekForecastFeatureService,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.utcnow()


class KimiForecastFeatureService(DeepSeekForecastFeatureService):
    """Kimi-backed feature service kept compatible with the former import path."""

    def __init__(self, llm_service: Any | None = None, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        super().__init__(llm_service=llm_service, ttl_seconds=ttl_seconds)

    def refresh_snapshot(self) -> dict[str, Any]:
        with self._lock:
            generated_dt = _utcnow()
            generated_at = generated_dt.isoformat()
            expires_at = (generated_dt + timedelta(seconds=self.ttl_seconds)).isoformat()
            input_payload = self.build_input_payload()
            fingerprint = self.input_fingerprint(input_payload)
            model = str(
                getattr(self.llm, "external_model", None)
                or getattr(self.llm, "model", "deepseek-chat")
            )
            snapshot_id = f"fs_{uuid.uuid4().hex[:16]}"
            status = "success"
            error = ""

            try:
                raw_features = self._call_llm(input_payload)
                features = self.normalise_features(raw_features)
            except Exception as exc:
                status = "fallback"
                error = str(exc)
                logger.warning("External forecast feature snapshot fallback: %s", exc)
                features = self._fallback_features("external_llm_unavailable")

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


kimi_forecast_feature_service = KimiForecastFeatureService()
