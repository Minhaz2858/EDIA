"""Sentiment Reversal Detector – Background Skill #12.

Detects early sentiment shifts that may precede price reversals by
analysing the trajectory of market signals: when momentum, weekly-report
tone, and intelligence event direction start changing *before* the price
itself moves, a reversal may be forming.

Petrochemical context:  C5/C9 markets are not as liquid as crude oil.
Price moves lag sentiment changes by 2-5 days.  If weekly reports start
mentioning "下游询盘增加" while prices are still falling, that's a
bottom-formation signal the pricing team should act on early.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import BackgroundSkill, SkillResult

logger = logging.getLogger(__name__)

_PRODUCTS = [
    "cracked_c5", "isoprene", "piperylene", "dcpd",
    "cracked_c9", "sis", "c5_resin",
]

# Sentiment reversal keywords (Chinese + English)
_BULLISH_REVERSAL_KEYWORDS = [
    "询盘增加", "成交好转", "下游补货", "需求回暖", "触底反弹",
    "库存下降", "开工率提升", "供应偏紧",
    "demand recovery", "restocking", "bottoming", "tightening supply",
]

_BEARISH_REVERSAL_KEYWORDS = [
    "询盘减少", "成交清淡", "下游观望", "需求走弱", "库存积压",
    "开工率下降", "供应过剩", "恐慌抛售",
    "demand weakening", "destocking", "oversupply", "inventory buildup",
]


class SentimentReversalDetector(BackgroundSkill):
    name = "sentiment_reversal_detector"
    interval_minutes = 120  # every 2 hours

    def run(self) -> SkillResult:
        try:
            from backend.src.service.forecasting_engine import get_price_series
        except Exception as exc:
            return SkillResult(
                skill_name=self.name,
                status="error",
                summary=f"import failed: {exc}",
            )

        alerts: list[str] = []
        reversals: list[dict[str, Any]] = []

        # 1. Price momentum reversal detection (across products)
        for pid in _PRODUCTS:
            try:
                series = get_price_series(pid, "price", days=30)
                if series is None or len(series) < 14:
                    continue

                reversal = self._detect_momentum_reversal(pid, series)
                if reversal:
                    reversals.append(reversal)
                    alerts.append(
                        f"[REVERSAL] {pid}: {reversal['type']} — "
                        f"{reversal['description']}"
                    )
            except Exception:
                pass

        # 2. Sentiment from intelligence events (direction changing)
        try:
            from backend.src.intelligence.event_registry import intelligence_event_registry

            events = intelligence_event_registry.list_recent_events(hours=72, limit=30)
            if len(events) >= 4:
                # Split events into recent (24h) and older (24-72h)
                recent_events = [
                    e for e in events
                    if self._event_age_hours(e) <= 24
                ]
                older_events = [
                    e for e in events
                    if 24 < self._event_age_hours(e) <= 72
                ]

                recent_bullish = sum(
                    1 for e in recent_events
                    if str(e.get("direction", "")).lower() == "bullish"
                )
                recent_bearish = sum(
                    1 for e in recent_events
                    if str(e.get("direction", "")).lower() == "bearish"
                )
                older_bullish = sum(
                    1 for e in older_events
                    if str(e.get("direction", "")).lower() == "bullish"
                )
                older_bearish = sum(
                    1 for e in older_events
                    if str(e.get("direction", "")).lower() == "bearish"
                )

                # Sentiment shift detection
                if older_bearish > older_bullish and recent_bullish > recent_bearish:
                    reversals.append({
                        "type": "event_sentiment_turning_bullish",
                        "description": (
                            f"Intelligence sentiment shifting: older events were "
                            f"{older_bearish}B/{older_bullish}L but recent are "
                            f"{recent_bearish}B/{recent_bullish}L"
                        ),
                        "product_id": "portfolio",
                    })
                    alerts.append(
                        "[SENTIMENT SHIFT] Intelligence events turning bullish — "
                        "possible market bottom forming"
                    )
                elif older_bullish > older_bearish and recent_bearish > recent_bullish:
                    reversals.append({
                        "type": "event_sentiment_turning_bearish",
                        "description": (
                            f"Intelligence sentiment shifting: older events were "
                            f"{older_bearish}B/{older_bullish}L but recent are "
                            f"{recent_bearish}B/{recent_bullish}L"
                        ),
                        "product_id": "portfolio",
                    })
                    alerts.append(
                        "[SENTIMENT SHIFT] Intelligence events turning bearish — "
                        "possible market top forming"
                    )
        except Exception:
            pass

        # 3. RAG weekly-report keyword sentiment scan
        try:
            from backend.src.rag.vector_store import kb
            recent_docs = kb.similarity_search(
                "市场成交 供需 库存 价格走势",
                k=5,
                collection_name="weekly_reports",
            )
            bullish_count = 0
            bearish_count = 0
            for doc in recent_docs:
                content = (doc.page_content if hasattr(doc, 'page_content') else str(doc)).lower()
                bullish_count += sum(1 for kw in _BULLISH_REVERSAL_KEYWORDS if kw in content)
                bearish_count += sum(1 for kw in _BEARISH_REVERSAL_KEYWORDS if kw in content)

            if bullish_count > 0 or bearish_count > 0:
                dominant = "bullish" if bullish_count > bearish_count else "bearish"
                reversals.append({
                    "type": f"weekly_report_sentiment_{dominant}",
                    "description": (
                        f"Weekly reports sentiment: {bullish_count} bullish keywords, "
                        f"{bearish_count} bearish keywords → dominant: {dominant}"
                    ),
                    "product_id": "portfolio",
                    "bullish_keywords": bullish_count,
                    "bearish_keywords": bearish_count,
                })
        except Exception:
            pass

        return SkillResult(
            skill_name=self.name,
            summary=f"detected {len(reversals)} reversal signal(s), {len(alerts)} alert(s)",
            data={"reversals": reversals},
            alerts=alerts,
        )

    def _detect_momentum_reversal(
        self, product_id: str, series: Any
    ) -> dict[str, Any] | None:
        """Detect when short-term momentum diverges from medium-term trend.

        Bullish reversal: 14d trend is down but 3d momentum is positive.
        Bearish reversal: 14d trend is up but 3d momentum is negative.
        """
        if len(series) < 14:
            return None

        prices = series.tail(14)
        price_start_14d = float(prices.iloc[0])
        price_end = float(prices.iloc[-1])
        price_3d_ago = float(prices.iloc[-4]) if len(prices) >= 4 else price_start_14d

        if price_start_14d == 0 or price_3d_ago == 0:
            return None

        trend_14d_pct = (price_end - price_start_14d) / price_start_14d * 100
        momentum_3d_pct = (price_end - price_3d_ago) / price_3d_ago * 100

        # Meaningful divergence thresholds
        if trend_14d_pct < -1.5 and momentum_3d_pct > 0.5:
            return {
                "type": "bullish_momentum_divergence",
                "product_id": product_id,
                "trend_14d_pct": round(trend_14d_pct, 2),
                "momentum_3d_pct": round(momentum_3d_pct, 2),
                "description": (
                    f"{product_id}: 14d trend {trend_14d_pct:+.1f}% (down) but "
                    f"3d momentum {momentum_3d_pct:+.1f}% (up) — possible bottom"
                ),
            }
        elif trend_14d_pct > 1.5 and momentum_3d_pct < -0.5:
            return {
                "type": "bearish_momentum_divergence",
                "product_id": product_id,
                "trend_14d_pct": round(trend_14d_pct, 2),
                "momentum_3d_pct": round(momentum_3d_pct, 2),
                "description": (
                    f"{product_id}: 14d trend {trend_14d_pct:+.1f}% (up) but "
                    f"3d momentum {momentum_3d_pct:+.1f}% (down) — possible top"
                ),
            }
        return None

    @staticmethod
    def _event_age_hours(event: dict) -> float:
        """Compute how many hours ago an event was detected."""
        detected = event.get("detected_at", "")
        if not detected:
            return 999
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(detected.replace("Z", "+00:00"))
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        except Exception:
            return 999
