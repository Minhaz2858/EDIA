from __future__ import annotations

"""Stage 3 — Forecasting Service.

Thin orchestration layer that calls the real forecasting_engine (ARIMA +
XGBoost + monthly STL) and returns the canonical 3-scenario, 3-horizon dict
used by all downstream services.

External API (product_id, diagnosis → dict) is unchanged for backward compat.
"""

from typing import Dict, Any, List, Optional
import statistics

from backend.src.service.market_data_service import market_data
from backend.src.domain.knowledge_graph import domain_graph
from backend.src.intelligence.product_intelligence import summarize_product_intelligence
from backend.src.service.erp_kpi_service import (
    compute_kpi_forecast_adjustment,
    erp_kpi_service,
)
from backend.src.service.forecasting_engine import (
    ensemble_forecast,
    monthly_stl_forecast,
)
from backend.src.service.forecast_policy_service import forecast_policy_service
from backend.src.service.forecast_supervisor import forecast_supervisor

# ---------------------------------------------------------------------------
# Seasonal adjustment table (from EDIA Build Instructions §8.2 Layer 3)
# keyed by (product_id, month) → pct_adjustment
# ---------------------------------------------------------------------------
_SEASONAL_RULES: Dict[tuple, float] = {
    # C5 Resin (road marking / adhesive)
    ("c5_resin", 11): -2.5,
    ("c5_resin", 12): -2.5,
    ("c5_resin", 1): -2.5,
    ("c5_resin", 2): -2.5,
    ("c5_resin", 3): 1.0,
    ("c5_resin", 4): 1.0,
    ("c5_resin", 5): 1.0,
    ("c5_resin", 6): 1.0,
    ("c5_resin", 7): 1.0,
    ("c5_resin", 8): 1.0,
    ("c5_resin", 9): 1.0,
    # DCPD
    ("dcpd", 11): -3.0,
    ("dcpd", 12): -3.0,
    ("dcpd", 1): -3.0,
    ("dcpd", 2): -3.0,
    ("dcpd", 3): 1.5,
    ("dcpd", 4): 1.5,
    ("dcpd", 5): 1.5,
    ("dcpd", 6): 1.5,
    ("dcpd", 7): 1.5,
    ("dcpd", 8): 1.5,
    # Isoprene — SIS plant restart
    ("isoprene", 2): 0.8,
    ("isoprene", 3): 0.8,
    # Cracked C5 — post-holiday lull / restart
    ("cracked_c5", 1): -1.0,
    ("cracked_c5", 2): -1.0,
    ("cracked_c5", 3): 0.5,
    ("cracked_c5", 4): 0.5,
    ("cracked_c5", 5): 0.5,
    # Piperylene (similar post-holiday)
    ("piperylene", 2): 1.2,
    ("piperylene", 3): 1.2,
}

# Causal-chain elasticities: naphtha → C5 → downstream
# Values derived from the spec's causal-propagation section.
_ELASTICITIES: Dict[str, float] = {
    "naphtha": 1.00,
    "cracked_c5": 0.75,  # naphtha → C5
    "cracked_c9": 0.65,
    "isoprene": 0.60,  # C5 → isoprene (dampened)
    "piperylene": 0.55,
    "dcpd": 0.50,
    "raffinate_c5": 0.55,
    "sis": 0.45,  # further downstream
    "sis_rubber": 0.45,
    "blowing_agent": 0.45,
    "styrene": 0.47,
    "c5_resin": 0.45,
    "hydrogenated_c9": 0.50,
    "trimethylbenzene": 0.50,
    "mixed_c5": 0.75,
    "mixed_c9": 0.65,
    "c9_resin": 0.50,
}


class ForecastingService:
    """Produces 3-scenario, 3-horizon price forecasts from real MySQL price history."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def forecast(
        self,
        product_id: str,
        diagnosis: Optional[Dict[str, Any]] = None,
        reference_month: Optional[int] = None,
        intelligence_context: Optional[Dict[str, Any]] = None,
        as_of_date: Optional[str] = None,
        policy_mode: str = "adaptive",
        hermes_plan: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Return the full forecast block for a product.

        Returns
        -------
        dict with keys:
          product_id, latest_price, scenarios (3-day, 7-day, 30-day),
          method_notes, seasonal_adj_pct
        """
        # ── Short-term: ARIMA + XGBoost ensemble (3-day and 7-day)
        fc_3d = ensemble_forecast(
            product_id,
            horizon_days=3,
            diagnosis=diagnosis,
            cutoff_date=as_of_date,
        )
        fc_7d = ensemble_forecast(
            product_id,
            horizon_days=7,
            diagnosis=diagnosis,
            cutoff_date=as_of_date,
        )

        # ── Monthly: 4-layer STL forecaster
        fc_30d = monthly_stl_forecast(
            product_id,
            diagnosis=diagnosis,
            reference_month=reference_month,
            cutoff_date=as_of_date,
        )

        latest = fc_7d.get("latest") or fc_3d.get("latest") or 0.0
        if latest == 0.0:
            history = market_data.get_price_history(product_id, cutoff_date=as_of_date)
            if history:
                latest = history[-1]["price"]
        if latest == 0.0:
            return self._empty(product_id, hermes_plan=hermes_plan)

        erp_kpi_signal = compute_kpi_forecast_adjustment(
            erp_kpi_service.get_product_kpi(product_id)
        )
        erp_kpi_adj = erp_kpi_signal["adjustment_pct"]

        intelligence_signal = summarize_product_intelligence(
            product_id, intelligence_context
        )
        intelligence_adj = {"3d": 0.0, "7d": 0.0, "30d": 0.0}
        if intelligence_signal["active"]:
            weighted_pct = (
                intelligence_signal["net_price_pct"] * intelligence_signal["confidence"]
            )
            intelligence_adj = {
                "3d": round(weighted_pct * 0.8, 2),
                "7d": round(weighted_pct, 2),
                "30d": round(weighted_pct * 0.6, 2),
            }

        raw_horizons = {
            "3d": {
                "base_pct": fc_3d["base_pct"] + intelligence_adj["3d"] + erp_kpi_adj["3d"],
                "vol_pct": fc_3d["vol_pct"],
                "arima_ok": fc_3d.get("arima_ok", False),
                "xgb_ok": fc_3d.get("xgb_ok", False),
                "lstm_ok": fc_3d.get("lstm_ok", False),
            },
            "7d": {
                "base_pct": fc_7d["base_pct"] + intelligence_adj["7d"] + erp_kpi_adj["7d"],
                "vol_pct": fc_7d["vol_pct"],
                "arima_ok": fc_7d.get("arima_ok", False),
                "xgb_ok": fc_7d.get("xgb_ok", False),
                "lstm_ok": fc_7d.get("lstm_ok", False),
            },
            "30d": {
                "base_pct": fc_30d["base_pct"] + intelligence_adj["30d"] + erp_kpi_adj["30d"],
                "vol_pct": fc_30d["vol_pct"],
                "layers_used": fc_30d.get("layers_used", []),
            },
        }
        if policy_mode == "adaptive":
            policy_result = forecast_policy_service.apply(
                product_id,
                raw_horizons,
                diagnosis=diagnosis,
                as_of_date=as_of_date,
            )
            adjusted_horizons = policy_result["horizons"]
        else:
            policy_result = {
                "policy_id": "baseline",
                "model_version": "v1",
                "metrics": {
                    "sample_count": 0,
                    "recent_mape": None,
                    "mean_signed_error_pct": 0.0,
                    "recent_volatility_pct": 0.0,
                    "trailing_14d_change_pct": 0.0,
                    "regime": "baseline",
                    "bias_correction_pct": 0.0,
                    "volatility_multiplier": 1.0,
                },
                "horizons": raw_horizons,
            }
            adjusted_horizons = raw_horizons

        # ── Build scenarios for each horizon
        scenarios = {
            "3d": self._three_scenarios(
                latest,
                adjusted_horizons["3d"]["base_pct"],
                adjusted_horizons["3d"]["vol_pct"],
            ),
            "7d": self._three_scenarios(
                latest,
                adjusted_horizons["7d"]["base_pct"],
                adjusted_horizons["7d"]["vol_pct"],
            ),
            "30d": self._three_scenarios(
                latest,
                adjusted_horizons["30d"]["base_pct"],
                adjusted_horizons["30d"]["vol_pct"],
            ),
        }

        seasonal_adj = fc_30d.get("seasonal_pct", 0.0)
        layers = fc_30d.get("layers_used", [])

        method_notes = (
            f"3d/7d: ARIMA({'✓' if fc_7d['arima_ok'] else '✗'}) + "
            f"XGBoost({'✓' if fc_7d['xgb_ok'] else '✗'}) + "
            f"LSTM({'✓' if fc_7d.get('lstm_ok') else '✗'}); "
            f"30d: {'+'.join(layers) if layers else 'baseline'} "
            f"(stl={fc_30d['stl_pct']:+.1f}%, "
            f"causal={fc_30d['causal_pct']:+.1f}%, "
            f"seasonal={fc_30d['seasonal_pct']:+.1f}%)"
        )
        if intelligence_signal["active"]:
            method_notes += (
                f"; intelligence={intelligence_signal['bias']} "
                f"{intelligence_signal['net_price_pct']:+.2f}%"
            )
        if erp_kpi_signal["active"]:
            method_notes += (
                f"; erp_kpi={erp_kpi_signal['bias']} " f"{erp_kpi_adj['7d']:+.2f}%"
            )
        policy_metrics = policy_result.get("metrics", {})
        method_notes += (
            f"; policy={policy_result.get('policy_id', 'adaptive')}"
            f" regime={policy_metrics.get('regime', 'unknown')}"
            f" bias={policy_metrics.get('bias_correction_pct', 0):+.2f}%"
            f" volx={policy_metrics.get('volatility_multiplier', 1.0):.2f}"
        )

        result = {
            "product_id": product_id,
            "latest_price": round(latest, 2),
            "as_of_date": as_of_date,
            "scenarios": scenarios,
            "seasonal_adj_pct": round(seasonal_adj, 2),
            "intelligence_adj_pct": intelligence_adj,
            "intelligence_signal": intelligence_signal,
            "erp_kpi_adj_pct": erp_kpi_adj,
            "erp_kpi_signal": erp_kpi_signal,
            "policy": policy_result,
            "model_version": policy_result.get("model_version", "v1"),
            "method_notes": method_notes,
        }
        result["forecast_supervisor"] = forecast_supervisor.supervise(
            result,
            hermes_plan=hermes_plan,
        )
        return result

    def to_prompt_text(self, fc: Dict[str, Any]) -> str:
        """Render forecast dict as a compact block for the LLM prompt."""
        pid = fc["product_id"]
        lp = fc["latest_price"]
        lines = [f"FORECAST (STAGE 3) — {pid} (latest: {lp}):"]
        for horizon, sc in fc.get("scenarios", {}).items():
            lines.append(
                f"  {horizon.upper():4s}  Bear {sc['bear']['low']:.0f}–{sc['bear']['high']:.0f} | "
                f"Base {sc['base']['low']:.0f}–{sc['base']['high']:.0f} | "
                f"Bull {sc['bull']['low']:.0f}–{sc['bull']['high']:.0f}"
            )
        if fc.get("seasonal_adj_pct"):
            lines.append(f"  Seasonal adj this month: {fc['seasonal_adj_pct']:+.1f}%")
        intelligence_signal = fc.get("intelligence_signal") or {}
        if intelligence_signal.get("active"):
            lines.append(
                "  Live intelligence adj: "
                f"{intelligence_signal.get('bias', 'neutral')} "
                f"{intelligence_signal.get('net_price_pct', 0):+.2f}%"
            )
        erp_kpi_signal = fc.get("erp_kpi_signal") or {}
        if erp_kpi_signal.get("active"):
            lines.append(
                "  ERP KPI adj: "
                f"{erp_kpi_signal.get('bias', 'neutral')} "
                f"{fc.get('erp_kpi_adj_pct', {}).get('7d', 0):+.2f}%"
            )
        supervisor = fc.get("forecast_supervisor") if isinstance(fc.get("forecast_supervisor"), dict) else {}
        if supervisor:
            lines.append(
                "  Supervisor: "
                f"{supervisor.get('status', 'unknown')} "
                f"confidence={supervisor.get('confidence_label', 'unknown')}"
            )
            for disclosure in (supervisor.get("required_disclosures") or [])[:2]:
                lines.append(f"  Disclosure: {disclosure}")
        return "\n".join(lines)

    def what_if_forecast(
        self,
        product_id: str,
        perception_snapshot: Dict[str, Any],
        overrides: Dict[str, float],
    ) -> Dict[str, Any]:
        """Run a lightweight causal what-if using current snapshot + overrides.

        Supported overrides include crude_price_usd and naphtha_price_usd.
        """

        products = (perception_snapshot or {}).get("products", {})
        focal = products.get(product_id, {})
        if not focal:
            return {
                "scenario": "what_if",
                "status": "unavailable",
                "reason": "No focal product snapshot available",
            }

        current_price = float(focal.get("latest_price") or 0.0)
        if current_price <= 0:
            return {
                "scenario": "what_if",
                "status": "unavailable",
                "reason": "No current product price available",
            }

        crude_now = float((products.get("crude_oil") or {}).get("latest_price") or 0.0)
        naphtha_now = float((products.get("naphtha") or {}).get("latest_price") or 0.0)
        crude_target = overrides.get("crude_price_usd")
        naphtha_target = overrides.get("naphtha_price_usd")

        if crude_target is None and naphtha_target is None:
            return {
                "scenario": "what_if",
                "status": "unavailable",
                "reason": "No recognized override variable",
            }

        # Causal propagation: crude -> naphtha (1.35) -> C5 feedstock (0.82) -> product elasticity
        if crude_target is not None and crude_now > 0:
            crude_change_pct = (float(crude_target) - crude_now) / crude_now * 100.0
            naphtha_change_pct = crude_change_pct * 1.35
            scenario_label = f"If crude moves to ${float(crude_target):.2f}"
        elif naphtha_target is not None and naphtha_now > 0:
            naphtha_change_pct = (
                (float(naphtha_target) - naphtha_now) / naphtha_now * 100.0
            )
            crude_change_pct = naphtha_change_pct / 1.35
            scenario_label = f"If naphtha moves to ${float(naphtha_target):.2f}"
        else:
            return {
                "scenario": "what_if",
                "status": "unavailable",
                "reason": "Current upstream price unavailable for override",
            }

        c5_change_pct = naphtha_change_pct * 0.82
        elasticity = _ELASTICITIES.get(product_id, 0.5)
        product_change_pct = c5_change_pct * elasticity

        new_est = current_price * (1 + product_change_pct / 100.0)
        confidence = "Medium"
        if abs(product_change_pct) >= 12:
            confidence = "Low"
        elif abs(product_change_pct) <= 5:
            confidence = "Medium-High"

        return {
            "scenario": scenario_label,
            "status": "ok",
            "product_id": product_id,
            "current_price": round(current_price, 2),
            "implied_crude_change_pct": round(crude_change_pct, 2),
            "implied_naphtha_change_pct": round(naphtha_change_pct, 2),
            "implied_c5_change_pct": round(c5_change_pct, 2),
            "implied_product_change_pct": round(product_change_pct, 2),
            "new_price_estimate": round(new_est, 2),
            "confidence": confidence,
            "assumption": "Linear elasticity approximation",
        }

    def what_if_to_prompt_text(self, result: Dict[str, Any]) -> str:
        if not result or result.get("status") != "ok":
            reason = (
                result.get("reason", "What-if scenario unavailable")
                if result
                else "What-if scenario unavailable"
            )
            return f"WHAT-IF SCENARIO:\n  {reason}"

        return (
            "WHAT-IF SCENARIO:\n"
            f"  Scenario: {result.get('scenario')}\n"
            f"  Implied crude change: {result.get('implied_crude_change_pct', 0):+.2f}%\n"
            f"  Implied naphtha change: {result.get('implied_naphtha_change_pct', 0):+.2f}%\n"
            f"  Implied product change: {result.get('implied_product_change_pct', 0):+.2f}%\n"
            f"  New price estimate: {result.get('new_price_estimate', 0):,.0f}\n"
            f"  Confidence: {result.get('confidence')} ({result.get('assumption')})"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _linear_trend_pct(prices: List[float], window: int = 14) -> float:
        """Annualised daily % change estimated by linear regression over `window` days.

        Returns pct-per-day (positive = uptrend).
        """
        series = prices[-window:] if len(prices) >= window else prices
        n = len(series)
        if n < 2:
            return 0.0
        # Ordinary least squares manually (no numpy needed)
        xs = list(range(n))
        mean_x = sum(xs) / n
        mean_y = sum(series) / n
        num = sum((xs[i] - mean_x) * (series[i] - mean_y) for i in range(n))
        den = sum((xs[i] - mean_x) ** 2 for i in range(n))
        slope = num / den if den else 0.0
        # Convert slope (price/day) → % per day relative to current price
        base = series[-1] if series[-1] else 1
        return round(slope / base * 100, 4)

    @staticmethod
    def _rolling_vol(prices: List[float], window: int = 14) -> float:
        """Return the standard deviation of % daily changes over the last `window` days."""
        series = prices[-window:] if len(prices) >= window else prices
        if len(series) < 2:
            return 1.0
        pct_changes = []
        for i in range(1, len(series)):
            if series[i - 1]:
                pct_changes.append((series[i] - series[i - 1]) / series[i - 1] * 100)
        if not pct_changes:
            return 1.0
        return round(
            (
                statistics.stdev(pct_changes)
                if len(pct_changes) > 1
                else abs(pct_changes[0])
            ),
            4,
        )

    @staticmethod
    def _three_scenarios(
        latest: float, base_pct: float, vol_pct: float
    ) -> Dict[str, Dict[str, float]]:
        """Build bull / base / bear scenario price ranges.

        base_pct   : expected % change for the horizon
        vol_pct    : 1-sigma volatility for the horizon (used as spread width)
        """
        # 1 sigma = ~68% confidence spread
        bull_mid = latest * (1 + (base_pct + vol_pct) / 100)
        base_mid = latest * (1 + base_pct / 100)
        bear_mid = latest * (1 + (base_pct - vol_pct) / 100)

        half_band = latest * vol_pct / 100 * 0.5  # tighter inner band

        def band(mid: float) -> Dict[str, float]:
            return {"low": round(mid - half_band, 2), "high": round(mid + half_band, 2)}

        return {
            "bull": band(bull_mid),
            "base": band(base_mid),
            "bear": band(bear_mid),
        }

    @staticmethod
    def _empty(product_id: str, hermes_plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        result = {
            "product_id": product_id,
            "latest_price": 0,
            "as_of_date": None,
            "scenarios": {},
            "seasonal_adj_pct": 0,
            "erp_kpi_adj_pct": {"3d": 0.0, "7d": 0.0, "30d": 0.0},
            "erp_kpi_signal": {"active": False, "bias": "neutral"},
            "method_notes": "No price history available.",
        }
        result["forecast_supervisor"] = forecast_supervisor.supervise(result, hermes_plan=hermes_plan)
        return result


# Singleton
forecasting_service = ForecastingService()
