"""Anomaly Pattern Detector – Background Skill #6.

Identifies abnormal price structures that often precede major market
moves — divergences, breakouts, unusual volume/volatility clusters,
and historical pattern matches.

Petrochemical context:  C5/C9 markets are thinly traded relative to
crude or naphtha.  When three products move in lock-step for weeks and
one suddenly diverges, it often signals a supply disruption or demand
shock 2-5 days before broad market reaction.  This skill raises an
early warning.
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


class AnomalyPatternDetector(BackgroundSkill):
    name = "anomaly_pattern_detector"
    interval_minutes = 90  # every 1.5 hours

    def run(self) -> SkillResult:
        try:
            from backend.src.service.forecasting_engine import get_price_series
            import numpy as np
        except Exception as exc:
            return SkillResult(
                skill_name=self.name,
                status="error",
                summary=f"import failed: {exc}",
            )

        alerts: list[str] = []
        patterns: list[dict[str, Any]] = []

        # Gather 30-day returns for all products
        product_returns: dict[str, Any] = {}
        for pid in _PRODUCTS:
            try:
                series = get_price_series(pid, "price", days=60)
                if series is not None and len(series) >= 14:
                    product_returns[pid] = series.pct_change().dropna()
            except Exception:
                pass

        if not product_returns:
            return SkillResult(
                skill_name=self.name,
                status="skipped",
                summary="insufficient price data for anomaly detection",
            )

        # Pattern 1: Bollinger Band breakout (2-sigma from 20-day mean)
        for pid, returns in product_returns.items():
            try:
                series = get_price_series(pid, "price", days=30)
                if series is None or len(series) < 20:
                    continue
                prices = series.tail(20)
                mean_20 = float(prices.mean())
                std_20 = float(prices.std())
                if std_20 == 0:
                    continue
                latest = float(prices.iloc[-1])
                z_score = (latest - mean_20) / std_20

                if abs(z_score) > 2.0:
                    direction = "upper" if z_score > 0 else "lower"
                    patterns.append({
                        "type": "bollinger_breakout",
                        "product_id": pid,
                        "z_score": round(z_score, 2),
                        "direction": direction,
                        "latest": round(latest, 0),
                        "mean_20": round(mean_20, 0),
                    })
                    alerts.append(
                        f"[BREAKOUT] {pid} at {z_score:+.1f}σ from 20d mean "
                        f"({latest:.0f} vs {mean_20:.0f}) — potential {direction} breakout"
                    )
            except Exception:
                pass

        # Pattern 2: Volatility regime shift (current 5d vol vs 30d vol)
        for pid, returns in product_returns.items():
            try:
                if len(returns) < 30:
                    continue
                vol_5d = float(returns.tail(5).std() * 100)
                vol_30d = float(returns.tail(30).std() * 100)
                if vol_30d == 0:
                    continue
                vol_ratio = vol_5d / vol_30d

                if vol_ratio > 2.0:
                    patterns.append({
                        "type": "volatility_spike",
                        "product_id": pid,
                        "vol_5d": round(vol_5d, 2),
                        "vol_30d": round(vol_30d, 2),
                        "ratio": round(vol_ratio, 2),
                    })
                    alerts.append(
                        f"[VOL SPIKE] {pid}: 5d vol ({vol_5d:.1f}%) is {vol_ratio:.1f}x "
                        f"the 30d norm ({vol_30d:.1f}%) — regime shift possible"
                    )
                elif vol_ratio < 0.3 and vol_30d > 0.5:
                    patterns.append({
                        "type": "volatility_compression",
                        "product_id": pid,
                        "vol_5d": round(vol_5d, 2),
                        "vol_30d": round(vol_30d, 2),
                        "ratio": round(vol_ratio, 2),
                    })
                    alerts.append(
                        f"[VOL COMPRESS] {pid}: 5d vol ({vol_5d:.1f}%) compressed to "
                        f"{vol_ratio:.1f}x of 30d — breakout may be imminent"
                    )
            except Exception:
                pass

        # Pattern 3: Cross-product divergence (correlation breakdown)
        keys = list(product_returns.keys())
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                try:
                    r_i = product_returns[keys[i]].tail(14)
                    r_j = product_returns[keys[j]].tail(14)
                    # Align indices
                    common = r_i.index.intersection(r_j.index)
                    if len(common) < 7:
                        continue
                    corr = float(np.corrcoef(
                        r_i.loc[common].values,
                        r_j.loc[common].values
                    )[0, 1])

                    # These products should normally be positively correlated
                    # (same supply chain).  Negative correlation is suspicious.
                    if corr < -0.3:
                        patterns.append({
                            "type": "correlation_breakdown",
                            "pair": [keys[i], keys[j]],
                            "correlation_14d": round(corr, 3),
                        })
                        alerts.append(
                            f"[DIVERGENCE] {keys[i]} vs {keys[j]}: 14d corr={corr:.2f} — "
                            "unusual divergence within the C5/C9 chain"
                        )
                except Exception:
                    pass

        # Pattern 4: Consecutive directional streaks (≥5 days same direction)
        for pid, returns in product_returns.items():
            try:
                recent = returns.tail(7)
                if len(recent) < 5:
                    continue
                signs = [1 if r > 0 else -1 if r < 0 else 0 for r in recent.values]
                streak = 1
                for k in range(1, len(signs)):
                    if signs[k] == signs[k - 1] and signs[k] != 0:
                        streak += 1
                    else:
                        streak = 1
                if streak >= 5:
                    direction = "up" if signs[-1] > 0 else "down"
                    patterns.append({
                        "type": "directional_streak",
                        "product_id": pid,
                        "streak_days": streak,
                        "direction": direction,
                    })
                    alerts.append(
                        f"[STREAK] {pid}: {streak}-day consecutive {direction} streak — "
                        "mean reversion or acceleration watch"
                    )
            except Exception:
                pass

        return SkillResult(
            skill_name=self.name,
            summary=f"detected {len(patterns)} anomaly pattern(s) across {len(product_returns)} products",
            data={"patterns": patterns},
            alerts=alerts,
        )
