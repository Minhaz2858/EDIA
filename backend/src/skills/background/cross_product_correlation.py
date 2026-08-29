"""Cross-Product Correlation Engine – Background Skill #10.

Monitors correlation structure across the C5/C9 product portfolio to
detect portfolio-level risk, arbitrage opportunities, and contagion paths.

Petrochemical context:  Ecisco's C5 derivatives all come from the same
cracker feed.  Normally isoprene, piperylene, DCPD, and raffinate C5
move together.  When one product breaks away — e.g. isoprene rises while
piperylene falls — it signals a demand-side structural shift that affects
portfolio-level P&L differently than a simple feedstock move.

This skill maintains a live correlation matrix and flags:
  1. Decorrelation events (pairs that usually move together but diverge)
  2. Contagion risk (when upstream move hasn't yet hit downstream)
  3. Portfolio concentration risk (when all products move in same direction)
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

# Expected pairwise correlations (30d rolling norm, from domain knowledge)
_EXPECTED_CORRELATIONS = {
    ("cracked_c5", "isoprene"): 0.75,
    ("cracked_c5", "piperylene"): 0.70,
    ("cracked_c5", "dcpd"): 0.70,
    ("isoprene", "piperylene"): 0.60,
    ("isoprene", "dcpd"): 0.55,
    ("piperylene", "dcpd"): 0.60,
    ("isoprene", "sis"): 0.55,
    ("piperylene", "c5_resin"): 0.50,
}


class CrossProductCorrelationEngine(BackgroundSkill):
    name = "cross_product_correlation"
    interval_minutes = 120  # every 2 hours

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

        # Gather returns
        returns_map: dict[str, Any] = {}
        for pid in _PRODUCTS:
            try:
                series = get_price_series(pid, "price", days=45)
                if series is not None and len(series) >= 14:
                    returns_map[pid] = series.pct_change().dropna().tail(30)
            except Exception:
                pass

        if len(returns_map) < 3:
            return SkillResult(
                skill_name=self.name,
                status="skipped",
                summary="insufficient data — need at least 3 products with 14+ days",
            )

        # Build correlation matrix
        correlation_matrix: dict[str, dict[str, float]] = {}
        decorrelation_alerts: list[dict[str, Any]] = []
        contagion_alerts: list[dict[str, Any]] = []

        keys = list(returns_map.keys())
        for i in range(len(keys)):
            correlation_matrix[keys[i]] = {}
            for j in range(len(keys)):
                if i == j:
                    correlation_matrix[keys[i]][keys[j]] = 1.0
                    continue
                try:
                    r_i = returns_map[keys[i]]
                    r_j = returns_map[keys[j]]
                    common = r_i.index.intersection(r_j.index)
                    if len(common) < 7:
                        correlation_matrix[keys[i]][keys[j]] = float("nan")
                        continue
                    corr = float(np.corrcoef(
                        r_i.loc[common].values,
                        r_j.loc[common].values,
                    )[0, 1])
                    correlation_matrix[keys[i]][keys[j]] = round(corr, 3)

                    # Check decorrelation
                    pair_key = (
                        (keys[i], keys[j])
                        if (keys[i], keys[j]) in _EXPECTED_CORRELATIONS
                        else (keys[j], keys[i])
                    )
                    expected = _EXPECTED_CORRELATIONS.get(pair_key)
                    if expected is not None and corr < expected - 0.4:
                        alert_entry = {
                            "pair": [keys[i], keys[j]],
                            "current_corr": round(corr, 3),
                            "expected_corr": expected,
                            "gap": round(expected - corr, 3),
                        }
                        decorrelation_alerts.append(alert_entry)
                        alerts.append(
                            f"[DECORRELATION] {keys[i]}–{keys[j]}: "
                            f"corr={corr:.2f} vs expected {expected:.2f} — "
                            "structural shift or demand divergence"
                        )
                except Exception:
                    correlation_matrix.setdefault(keys[i], {})[keys[j]] = float("nan")

        # Contagion detection: upstream moved but downstream hasn't yet
        try:
            c5_ret = returns_map.get("cracked_c5")
            if c5_ret is not None and len(c5_ret) >= 5:
                c5_5d_move = float(c5_ret.tail(5).sum() * 100)
                for downstream in ["isoprene", "piperylene", "dcpd"]:
                    ds_ret = returns_map.get(downstream)
                    if ds_ret is None or len(ds_ret) < 5:
                        continue
                    ds_5d_move = float(ds_ret.tail(5).sum() * 100)
                    if abs(c5_5d_move) > 2.0 and abs(ds_5d_move) < 0.5:
                        contagion_alerts.append({
                            "upstream": "cracked_c5",
                            "downstream": downstream,
                            "upstream_5d_move_pct": round(c5_5d_move, 2),
                            "downstream_5d_move_pct": round(ds_5d_move, 2),
                        })
                        alerts.append(
                            f"[CONTAGION LAG] cracked_c5 moved {c5_5d_move:+.1f}% over 5d "
                            f"but {downstream} only {ds_5d_move:+.1f}% — "
                            "downstream catch-up likely"
                        )
        except Exception:
            pass

        # Portfolio concentration: all products same direction
        try:
            directions = {}
            for pid, ret in returns_map.items():
                if len(ret) >= 3:
                    cum_3d = float(ret.tail(3).sum() * 100)
                    directions[pid] = "up" if cum_3d > 0.5 else "down" if cum_3d < -0.5 else "flat"

            up_count = sum(1 for d in directions.values() if d == "up")
            down_count = sum(1 for d in directions.values() if d == "down")
            total = len(directions)

            if total >= 4 and (up_count >= total - 1 or down_count >= total - 1):
                dominant = "upward" if up_count > down_count else "downward"
                alerts.append(
                    f"[CONCENTRATION] {up_count + down_count}/{total} products moving "
                    f"{dominant} — portfolio-level directional exposure is high"
                )
        except Exception:
            pass

        return SkillResult(
            skill_name=self.name,
            summary=(
                f"correlation matrix for {len(keys)} products | "
                f"{len(decorrelation_alerts)} decorrelation(s), "
                f"{len(contagion_alerts)} contagion lag(s)"
            ),
            data={
                "correlation_matrix": correlation_matrix,
                "decorrelation_alerts": decorrelation_alerts,
                "contagion_alerts": contagion_alerts,
            },
            alerts=alerts,
        )
