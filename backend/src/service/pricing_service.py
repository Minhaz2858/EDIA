from __future__ import annotations

"""Stage 4 — Pricing Decision Engine (EDIA Build Instructions §9).

Applies the 9-step pricing decision ladder per product:

  Step 1: Base anchor (Formula A cost-plus / B competitor-ref / C SINOPEC-anchor)
  Step 2: Market realization (blend formula base toward actual market)
  Step 3: Macro regime adjustment from naphtha / crude trend
  Step 4: Seasonal phase adjustment
  Step 5: Downstream chain demand signal (+50 per bullish signal, cap +200)
  Step 6: ERP inventory pressure (erp_v_stk_inventory → weeks-of-stock)
  Step 7: Action bias from weekly-report recommendation override (from diagnosis)
  Step 8: Active macro override (macro_override.json naphtha shock propagation)
  Step 9: Synthesize triggers, watch signals, dissenting view

Design aligned with EDIA Build Instructions §9.
"""

import logging
import os
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from backend.src.db.engine_factory import get_erp_engine
from backend.src.intelligence.product_intelligence import summarize_product_intelligence
from backend.src.service.macro_override_service import (
    get_active_override,
    override_summary,
)
from backend.src.service.weekly_plan_service import weekly_plan_service

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration — Pricing Formulas (§9.2)
# ---------------------------------------------------------------------------

# Which formula per product (C = SINOPEC-anchor, B = competitor-referenced, A = cost-plus)
PRICING_FORMULA: dict[str, str | None] = {
    "isoprene": "C",  # SINOPEC Shanghai national anchor
    "piperylene": "C",  # SINOPEC Shanghai national anchor
    "dcpd": "C",  # SINOPEC Shanghai national anchor
    "cracked_c5": "C",  # Cracker price (Yangzi / Maoming)
    "mixed_c5": "C",  # legacy alias
    "cracked_c9": "C",  # ERP forecast price anchor
    "mixed_c9": "C",  # legacy alias
    "raffinate_c5": "C",  # market-tracked ERP feedstock
    "sis": "B",  # Competitor-referenced (Baling SIS)
    "sis_rubber": "B",  # legacy alias
    "c5_resin": "B",  # Competitor-referenced (Jinhai, Luhua, Tianli)
    "blowing_agent": "C",
    "styrene": "C",
    "hydrogenated_c9": "B",
    "trimethylbenzene": "B",
    "c9_resin": "B",  # legacy alias
    "mthpa_hardener": "A",  # Cost-plus: (raw_material + processing) × (1 + 15%)
    # External / upstream — no Ecisco pricing needed
    "crude_oil": None,
    "naphtha": None,
}

# How much SINOPEC Shanghai typically lists ABOVE market price (Formula C anchor)
SINOPEC_PREMIUM_CNY: dict[str, int] = {
    "isoprene": 200,
    "piperylene": 150,
    "dcpd": 200,
    "cracked_c5": 100,
    "mixed_c5": 100,
    "cracked_c9": 100,
    "mixed_c9": 100,
    "raffinate_c5": 100,
    "styrene": 150,
    "blowing_agent": 120,
    "sis": 0,
    "sis_rubber": 0,
    "c5_resin": 0,
    "hydrogenated_c9": 0,
    "trimethylbenzene": 0,
    "c9_resin": 0,
}

# Ecisco default discount to SINOPEC for Formula C products (CNY/ton)
_ECISCO_DEFAULT_DISCOUNT = 200

# Formula B: typical competitor baseline prices (CNY/ton) per §9.2
_COMPETITOR_BASELINE: dict[str, int] = {
    "c5_resin": 8500,  # Jinhai ~8500, Luhua ~8200 → Ecisco at -400
    "hydrogenated_c9": 6800,
    "trimethylbenzene": 5700,
    "c9_resin": 6800,
    "sis": 15800,
    "sis_rubber": 15800,
}
_COMPETITOR_DISCOUNT: dict[str, int] = {
    "c5_resin": 400,  # Ecisco = competitor min - 400
    "hydrogenated_c9": 500,
    "trimethylbenzene": 300,
    "c9_resin": 500,
    "sis": 800,
    "sis_rubber": 800,
}

# Formula A: Cost-plus parameters (§9.2)
_FORMULA_A_PROCESSING_COST: dict[str, int] = {
    "mthpa_hardener": 2500,  # processing cost CNY/ton
}
_FORMULA_A_TARGET_MARGIN = 0.15  # 15% target margin

# Causal elasticities: naphtha → product (for macro shock propagation in Step 8)
_NAPHTHA_ELASTICITY: dict[str, float] = {
    "cracked_c5": 0.75,
    "mixed_c5": 0.75,
    "cracked_c9": 0.65,
    "mixed_c9": 0.65,
    "isoprene": 0.60,
    "piperylene": 0.55,
    "dcpd": 0.50,
    "raffinate_c5": 0.55,
    "sis": 0.45,
    "sis_rubber": 0.45,
    "blowing_agent": 0.45,
    "styrene": 0.47,
    "c5_resin": 0.45,
    "hydrogenated_c9": 0.50,
    "trimethylbenzene": 0.50,
    "c9_resin": 0.50,
}

# ---------------------------------------------------------------------------
# ERP Inventory — Material name → product_id mapping & weekly consumption
# ---------------------------------------------------------------------------

_ERP_MATERIAL_TO_PRODUCT: dict[str, str] = {
    "异戊二烯": "isoprene",
    "间戊二烯": "piperylene",
    "双环戊二烯": "dcpd",
    "工业用裂解碳五": "cracked_c5",
    "工业用裂解碳九": "cracked_c9",
    "抽余碳五": "raffinate_c5",
    "SIS": "sis",
    "碳五石油树脂": "c5_resin",
    "戊烷发泡剂": "blowing_agent",
    "苯乙烯": "styrene",
    "加氢碳九": "hydrogenated_c9",
    "混三甲苯": "trimethylbenzene",
    "顺式间戊二烯产品": "piperylene",  # cis variant → piperylene
}

# Approximate weekly consumption (tons/week) based on plant capacity
_WEEKLY_CONSUMPTION: dict[str, float] = {
    "isoprene": 500.0,
    "piperylene": 400.0,
    "dcpd": 450.0,
    "mixed_c5": 350.0,
    "mixed_c9": 200.0,
    "c5_resin": 120.0,
    "c9_resin": 100.0,
    "sis_rubber": 80.0,
}

_DB_URL = os.getenv("DATABASE_URL")
if not _DB_URL:
    raise RuntimeError("DATABASE_URL must be set for cloud DB mode.")


def _query_erp_inventory() -> dict[str, float]:
    """Query erp_v_stk_inventory → {product_id: total_tons}."""
    try:
        with get_erp_engine().connect() as conn:
            rows = conn.execute(
                text("SELECT material_name, FBASEQTY FROM erp_v_stk_inventory")
            )
            from collections import defaultdict

            totals: dict[str, float] = defaultdict(float)
            for name, qty in rows:
                pid = _ERP_MATERIAL_TO_PRODUCT.get(name)
                if pid and qty:
                    totals[pid] += float(qty)
            return dict(totals)
    except Exception as exc:
        logger.warning("ERP inventory query failed: %s", exc)
        return {}


def _query_latest_raw_material_cost(product_id: str) -> float | None:
    """Query latest raw material cost from ERP for Formula A products.

    Returns None when ERP does not expose the required cost signal.
    """
    try:
        with get_erp_engine().connect() as conn:
            r = conn.execute(
                text(
                    "SELECT material_name, FREALQTY, FDATE "
                    "FROM erp_v_raw_material_receiving "
                    "ORDER BY FDATE DESC LIMIT 5"
                )
            )
            rows = list(r)
            if rows:
                logger.warning(
                    "Raw material receiving data exists for %s but ERP price column is unavailable",
                    product_id,
                )
    except Exception as exc:
        logger.warning("Raw material cost query failed: %s", exc)

    return None


# ---------------------------------------------------------------------------
# Seasonal adjustments (CNY/ton), aligned with §8.2 Layer 3
# ---------------------------------------------------------------------------
_SEASONAL_ADJ: dict[tuple, int] = {
    # C5 Resin (road marking / adhesive): off-season Nov–Feb, peak Mar–Sep
    ("c5_resin", 11): -100,
    ("c5_resin", 12): -100,
    ("c5_resin", 1): -100,
    ("c5_resin", 2): -100,
    ("c5_resin", 3): 0,
    ("c5_resin", 4): 0,  # pre-construction: hold
    ("c5_resin", 5): 50,
    ("c5_resin", 6): 50,
    ("c5_resin", 7): 50,
    ("c5_resin", 8): 50,
    ("c5_resin", 9): 50,
    # C9 Resin
    ("c9_resin", 11): -100,
    ("c9_resin", 12): -100,
    ("c9_resin", 1): -50,
    ("c9_resin", 2): -50,
    # DCPD: UPR deep off-season Nov–Feb, recovery Mar–Aug
    ("dcpd", 11): -100,
    ("dcpd", 12): -100,
    ("dcpd", 1): -100,
    ("dcpd", 2): -50,
    ("dcpd", 3): 0,
    ("dcpd", 4): 50,
    ("dcpd", 5): 50,
    ("dcpd", 6): 50,
    ("dcpd", 7): 50,
    ("dcpd", 8): 50,
    # Isoprene — SIS plant restart Feb–Mar
    ("isoprene", 2): 50,
    ("isoprene", 3): 50,
    # Piperylene — post-holiday demand recovery Feb–Mar
    ("piperylene", 2): 50,
    ("piperylene", 3): 50,
    # Mixed C5 — post-holiday lull Jan–Feb, restart Mar–May
    ("mixed_c5", 1): -50,
    ("mixed_c5", 2): -50,
    ("mixed_c5", 3): 0,
    ("mixed_c5", 4): 0,
}


# ---------------------------------------------------------------------------
# PricingService
# ---------------------------------------------------------------------------


class PricingService:
    """Computes Stage 4 pricing decisions using the 9-step ladder."""

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def compute_pricing_decision(
        self,
        product_id: str,
        perception_snapshot: dict,
        diagnosis: dict | None = None,
        forecast: dict | None = None,
        intelligence_context: dict | None = None,
    ) -> dict:
        """Run the 9-step ladder and return a full pricing decision dict.

        Parameters
        ----------
        product_id:          Graph node id (e.g. "isoprene")
        perception_snapshot: Output of PerceptionService.build_snapshot()
        diagnosis:           Output of DiagnosisService.diagnose()  (may be None)
        forecast:            Output of ForecastingService.forecast() (may be None)
        """
        formula = PRICING_FORMULA.get(product_id)
        if not formula:
            return self._no_pricing(product_id, "No pricing formula for this product")

        products = perception_snapshot.get("products", {})
        product_data = products.get(product_id)
        if not product_data:
            return self._no_pricing(product_id, "Product not in perception snapshot")

        latest_price: float = product_data["latest_price"]
        product_name_cn: str = product_data.get("name_cn", product_id)
        product_name_en: str = product_data.get("name_en", product_id)
        unit: str = product_data.get("unit", "元/吨")
        current_month: int = datetime.now(UTC).month

        # Naphtha data for macro regime analysis
        naphtha_data = products.get("naphtha", {})
        naphtha_7d_pct: float = naphtha_data.get("change_7d_pct", 0.0)
        intelligence_signal = summarize_product_intelligence(
            product_id, intelligence_context
        )

        adjustment_steps: list[dict] = []

        # ── Step 1+2: Base anchor (Formula C or B) + Market realization ──────
        sinopec_anchor: float | None = None
        base_price: float

        if formula == "C":
            sinopec_anchor = latest_price + SINOPEC_PREMIUM_CNY.get(product_id, 150)
            # Ecisco formula start: SINOPEC − default discount
            base_price = sinopec_anchor - _ECISCO_DEFAULT_DISCOUNT
            sinopec_label = f"SINOPEC锚价: {sinopec_anchor:,.0f} {unit}"
            adjustment_steps.append(
                {
                    "step": 1,
                    "label": f"基准锚价 (公式C: SINOPEC±折扣)",
                    "value": base_price,
                    "delta": 0,
                    "note": f"{sinopec_label}，Ecisco默认折扣 -{_ECISCO_DEFAULT_DISCOUNT} {unit}",
                }
            )
        elif formula == "A":
            # Formula A: cost-plus = (raw_material + processing) × (1 + margin)
            raw_cost = _query_latest_raw_material_cost(product_id)
            if raw_cost is None:
                return self._no_pricing(
                    product_id,
                    "No ERP raw material cost available for Formula A pricing",
                )
            processing = _FORMULA_A_PROCESSING_COST.get(product_id, 2500)
            base_price = round((raw_cost + processing) * (1 + _FORMULA_A_TARGET_MARGIN))
            sinopec_anchor = None
            adjustment_steps.append(
                {
                    "step": 1,
                    "label": "基准锚价 (公式A: 成本加成)",
                    "value": base_price,
                    "delta": 0,
                    "note": (
                        f"原料成本 {raw_cost:,.0f} + 加工费 {processing:,.0f} = "
                        f"{raw_cost + processing:,.0f}，× (1+{_FORMULA_A_TARGET_MARGIN:.0%}) = "
                        f"{base_price:,.0f} {unit}"
                    ),
                }
            )
        else:  # Formula B
            competitor_base = _COMPETITOR_BASELINE.get(product_id, latest_price)
            discount = _COMPETITOR_DISCOUNT.get(product_id, 400)
            base_price = competitor_base - discount
            sinopec_label = ""
            adjustment_steps.append(
                {
                    "step": 1,
                    "label": f"基准锚价 (公式B: 竞品参考)",
                    "value": base_price,
                    "delta": 0,
                    "note": f"竞品参考价 {competitor_base:,.0f}，Ecisco折扣 -{discount} {unit}",
                }
            )

        working_price = base_price
        # Step 2: Market realization (blend formula base toward actual market)
        # If market is clearing significantly below formula, we need to reflect that.
        realization_delta = int(latest_price - base_price)
        if abs(realization_delta) > 50:
            blend_factor = 0.4  # 40% weight on market vs 60% formula
            blended_adj = round(realization_delta * blend_factor)
            working_price += blended_adj
            adjustment_steps.append(
                {
                    "step": 2,
                    "label": "市场实现折价校正",
                    "value": working_price,
                    "delta": blended_adj,
                    "note": (
                        f"当前市场价 {latest_price:,.0f}，公式价 {base_price:,.0f}，"
                        f"差价 {realization_delta:+,d}，取40%权重校正"
                    ),
                }
            )

        # ── Step 3: Macro regime adjustment ────────────────────────────────
        macro_delta, macro_note = _compute_macro_adj(
            naphtha_7d_pct,
            diagnosis.get("market_regime", "neutral") if diagnosis else "neutral",
        )
        if macro_delta != 0:
            working_price += macro_delta
            adjustment_steps.append(
                {
                    "step": 3,
                    "label": "宏观成本调整",
                    "value": working_price,
                    "delta": macro_delta,
                    "note": macro_note,
                }
            )

        intelligence_delta = 0
        if intelligence_signal["active"]:
            intelligence_delta = round(
                latest_price
                * (intelligence_signal["net_price_pct"] / 100)
                * max(intelligence_signal["confidence"], 0.25)
                * 0.5
            )
            if intelligence_delta != 0:
                working_price += intelligence_delta
                adjustment_steps.append(
                    {
                        "step": "3A",
                        "label": "实时情报事件调整",
                        "value": working_price,
                        "delta": intelligence_delta,
                        "note": (
                            f"{intelligence_signal['bias']} event signal "
                            f"{intelligence_signal['net_price_pct']:+.2f}% "
                            f"({intelligence_signal['net_price_cny']:+.0f} CNY/ton), "
                            f"applied with confidence {intelligence_signal['confidence']:.2f}"
                        ),
                    }
                )

        # ── Step 4: Seasonal phase adjustment ──────────────────────────────
        seasonal_delta = _SEASONAL_ADJ.get((product_id, current_month), 0)
        seasonal_note = _seasonal_note(product_id, current_month, seasonal_delta)
        if seasonal_delta != 0:
            working_price += seasonal_delta
            adjustment_steps.append(
                {
                    "step": 4,
                    "label": "季节性调整",
                    "value": working_price,
                    "delta": seasonal_delta,
                    "note": seasonal_note,
                }
            )

        # ── Step 5: Downstream chain demand signals ─────────────────────────
        downstream_delta, downstream_note = _compute_downstream_adj(diagnosis)
        if downstream_delta != 0:
            working_price += downstream_delta
            adjustment_steps.append(
                {
                    "step": 5,
                    "label": "下游需求链信号",
                    "value": working_price,
                    "delta": downstream_delta,
                    "note": downstream_note,
                }
            )

        # ── Step 6: ERP inventory pressure ────────────────────────────────
        inv_delta, inv_note, weeks_of_stock = _compute_inventory_pressure(product_id)
        if inv_delta != 0:
            working_price += inv_delta
        adjustment_steps.append(
            {
                "step": 6,
                "label": "ERP库存压力",
                "value": working_price,
                "delta": inv_delta,
                "note": inv_note,
            }
        )

        # ── Step 6A: Weekly production / sales plan pressure ─────────────────
        plan_delta, plan_note, weekly_plan = _compute_weekly_plan_pressure(product_id)
        if plan_delta != 0:
            working_price += plan_delta
        if weekly_plan:
            adjustment_steps.append(
                {
                    "step": "6A",
                    "label": "周计划供需压力",
                    "value": working_price,
                    "delta": plan_delta,
                    "note": plan_note,
                }
            )

        # ── Step 7: Action bias from diagnosis / weekly report recommendation ──
        action_bias, override_delta, bias_note = _compute_action_bias(
            diagnosis, forecast, working_price, product_id=product_id
        )
        if override_delta != 0:
            working_price += override_delta
            adjustment_steps.append(
                {
                    "step": 7,
                    "label": "周报推荐覆盖",
                    "value": working_price,
                    "delta": override_delta,
                    "note": bias_note,
                }
            )

        # ── Step 8: Active macro override shock propagation ─────────────────
        override = get_active_override()
        override_applied = False
        override_notes = None
        confidence_penalty = 0.0

        if override:
            elasticity = _NAPHTHA_ELASTICITY.get(product_id, 0.5)
            shock_pct = override.get("naphtha_shock_pct", 0.0)
            override_delta_price = round(working_price * (shock_pct / 100) * elasticity)
            # Also check product-specific shock
            prod_shock = override.get("product_shocks", {}).get(product_id, 0.0)
            if prod_shock:
                override_delta_price += round(working_price * (prod_shock / 100))
            working_price += override_delta_price
            confidence_penalty = override.get("confidence_penalty", 0.0)
            override_applied = True
            override_notes = override_summary(override)
            adjustment_steps.append(
                {
                    "step": 8,
                    "label": "宏观Override注入",
                    "value": working_price,
                    "delta": override_delta_price,
                    "note": override_notes,
                }
            )

        # ── Step 9: Round, compute range, action, triggers, watch, dissent ──
        recommended_price = round(working_price / 50) * 50  # round to nearest 50 CNY

        # Price range: ±100 CNY, widened by confidence penalty
        range_width = 100 + int(confidence_penalty * 500)
        price_low = recommended_price - range_width
        price_high = recommended_price + range_width

        # Action signal
        price_delta_vs_market = recommended_price - latest_price
        if price_delta_vs_market > 100:
            action = "raise_price"
            action_cn = "看涨 — 建议上调"
        elif price_delta_vs_market < -100:
            action = "reduce_price"
            action_cn = "看跌 — 建议下调"
        elif price_delta_vs_market > 0:
            action = "hold_upper"
            action_cn = "偏强 — 维持高位或小幅上调"
        elif price_delta_vs_market < 0:
            action = "hold_lower"
            action_cn = "偏弱 — 维持低位或小幅下调"
        else:
            action = "hold"
            action_cn = "稳定 — 维持现价"

        # Confidence: base 0.72, reduced by abs(price_delta) > expectations and penalty
        base_confidence = 0.72
        if abs(price_delta_vs_market) > 300:
            base_confidence -= 0.08  # large swing → lower confidence
        base_confidence -= confidence_penalty
        confidence = round(max(0.45, min(0.92, base_confidence)), 2)

        # Triggers (Step 9)
        triggers = _build_triggers(
            product_id,
            recommended_price,
            price_high,
            price_low,
            naphtha_7d_pct,
            diagnosis,
        )

        # Watch signals
        watch_signals = _build_watch_signals(product_id, override_applied)

        # Dissenting view
        dissenting_view = _build_dissenting_view(
            product_id, action, diagnosis, forecast, current_month
        )

        # ── Apply recommendation override based on action bias (§5.4) ──────
        volume_recommendation = 1.0  # default: no volume change
        override_note = None

        if action_bias == "accelerate_shipments":
            volume_recommendation = 1.20
            override_note = (
                f"OVERRIDE: 建议加速出货，已折扣 −100 元/吨 并增加20%出货量目标"
            )
        elif action_bias == "flexible_pricing":
            # Widen the price range for flexible pricing
            flex_base = recommended_price
            price_low = flex_base - 150
            price_high = flex_base + 150
            override_note = (
                f"FLEXIBLE PRICING: 区间报价 {price_low:,}–{price_high:,} 元/吨，"
                f"根据订单规模灵活调整"
            )

        return {
            "product_id": product_id,
            "product_name_cn": product_name_cn,
            "product_name_en": product_name_en,
            "unit": unit,
            "latest_market_price": round(latest_price),
            "pricing_formula": formula,
            "sinopec_anchor": round(sinopec_anchor) if sinopec_anchor else None,
            "discount_to_sinopec": (
                round(sinopec_anchor - recommended_price) if sinopec_anchor else None
            ),
            "recommended_price": recommended_price,
            "price_range": [price_low, price_high],
            "price_delta_vs_market": round(price_delta_vs_market),
            "action": action,
            "action_cn": action_cn,
            "action_bias": action_bias,
            "confidence": confidence,
            "adjustment_steps": adjustment_steps,
            "triggers": triggers,
            "watch_signals": watch_signals,
            "dissenting_view": dissenting_view,
            "intelligence_signal": intelligence_signal,
            "override_applied": override_applied,
            "override_notes": override_notes,
            "weekly_plan": weekly_plan,
            "volume_recommendation": volume_recommendation,
            "override_note": override_note,
        }

    # ------------------------------------------------------------------
    # Prompt text (compact — token budget aware)
    # ------------------------------------------------------------------

    def to_prompt_text(self, decision: dict) -> str:
        """Return compact PRICING DECISION block for LLM context (< 280 tokens)."""
        if decision.get("no_pricing"):
            return ""

        pid = decision["product_id"]
        name = decision["product_name_cn"]
        rec = decision["recommended_price"]
        low, high = decision["price_range"]
        formula = decision["pricing_formula"]
        action_cn = decision["action_cn"]
        conf = int(decision["confidence"] * 100)
        delta = decision["price_delta_vs_market"]
        mkt = decision["latest_market_price"]
        unit = decision["unit"]

        sinopec_line = ""
        if decision.get("sinopec_anchor"):
            sinopec_line = f"SINOPEC锚价: {decision['sinopec_anchor']:,} {unit} | "

        steps_summary = (
            " | ".join(
                f"Step{s['step']}:{s['delta']:+,d}" if s["delta"] != 0 else ""
                for s in decision["adjustment_steps"]
            )
            .replace(" |  | ", " | ")
            .strip(" |")
        )

        triggers_text = "\n".join(
            f"  • {t['condition']} → {t['action']}" for t in decision["triggers"][:2]
        )
        watch_text = " | ".join(decision["watch_signals"][:3])
        dissent = decision["dissenting_view"]

        override_line = ""
        if decision["override_applied"]:
            override_line = f"\n⚠️  {decision['override_notes']}"

        intelligence_line = ""
        intelligence_signal = decision.get("intelligence_signal") or {}
        if intelligence_signal.get("active"):
            intelligence_line = (
                f"\nLive Intelligence: {intelligence_signal.get('bias', 'neutral')} "
                f"{intelligence_signal.get('net_price_pct', 0):+.2f}% "
                f"({intelligence_signal.get('net_price_cny', 0):+.0f} CNY/ton)"
            )

        return f"""PRICING DECISION (STAGE 4):
Product: {name} ({pid}) | Formula: {formula}
Current Market: {mkt:,} {unit} | {sinopec_line}Recommended: {rec:,} {unit} ({delta:+,d} vs market)
Range: {low:,} – {high:,} {unit} | Action: {action_cn} | Confidence: {conf}%
Adjustments: {steps_summary}
Key Triggers:
{triggers_text}
Watch: {watch_text}
Dissenting View: {dissent}{intelligence_line}{override_line}"""

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _no_pricing(self, product_id: str, reason: str) -> dict:
        return {"product_id": product_id, "no_pricing": True, "reason": reason}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _compute_macro_adj(naphtha_7d_pct: float, market_regime: str) -> tuple[int, str]:
    """Step 3: translate naphtha trend + regime into CNY adjustment."""
    if naphtha_7d_pct >= 3.0:
        return 150, f"石脑油7日涨幅{naphtha_7d_pct:+.1f}%，强成本支撑，收窄折扣+150"
    elif naphtha_7d_pct >= 1.5:
        return 100, f"石脑油7日涨幅{naphtha_7d_pct:+.1f}%，成本上行压力+100"
    elif naphtha_7d_pct >= 0.5:
        return 50, f"石脑油小幅上涨{naphtha_7d_pct:+.1f}%，轻微成本支撑+50"
    elif naphtha_7d_pct <= -3.0:
        return -150, f"石脑油7日跌幅{naphtha_7d_pct:+.1f}%，成本强力下行-150"
    elif naphtha_7d_pct <= -1.5:
        return -100, f"石脑油7日跌幅{naphtha_7d_pct:+.1f}%，成本下行-100"
    elif naphtha_7d_pct <= -0.5:
        return -50, f"石脑油小幅下跌{naphtha_7d_pct:+.1f}%，轻微承压-50"
    else:
        return 0, f"石脑油7日变动{naphtha_7d_pct:+.1f}%，宏观中性，不调整"


def _seasonal_note(product_id: str, month: int, delta: int) -> str:
    if delta < 0:
        return f"{month}月为{product_id}淡季，价格承压 {delta:+,d} 元/吨"
    elif delta > 0:
        return f"{month}月为{product_id}旺季/需求恢复期，价格支撑 {delta:+,d} 元/吨"
    return f"{month}月季节性中性，不调整"


def _compute_downstream_adj(diagnosis: dict | None) -> tuple[int, str]:
    """Step 5: count bullish downstream signals → +50 each, cap +200."""
    if not diagnosis:
        return 0, "无诊断数据，下游信号不调整"
    downstream = diagnosis.get("downstream_propagated_signals", {})
    if not downstream:
        return 0, "无下游信号"
    bullish_count = sum(
        1
        for v in downstream.values()
        if "supportive" in str(v).lower()
        or "bullish" in str(v).lower()
        or "上行" in str(v)
    )
    if bullish_count == 0:
        return 0, "下游信号中性，不调整"
    adj = min(200, bullish_count * 50)
    products_str = ", ".join(list(downstream.keys())[:3])
    return adj, f"下游{bullish_count}个看涨信号 ({products_str}), +{adj} 元/吨"


def _compute_inventory_pressure(product_id: str) -> tuple[int, str, float | None]:
    """Step 6: ERP inventory → weeks-of-stock → pricing pressure.

    Returns (cny_delta, note, weeks_of_stock).
    Per §9.1:  > 4 weeks stock → −200 CNY;  < 1 week stock → +50 CNY.
    """
    inventory = _query_erp_inventory()
    tons = inventory.get(product_id)
    weekly = _WEEKLY_CONSUMPTION.get(product_id)

    if tons is None or weekly is None or weekly <= 0:
        return 0, f"{product_id} 无ERP库存数据或无消耗基准，库存中性", None

    weeks = round(tons / weekly, 1)

    if weeks > 4.0:
        return (
            -200,
            f"库存 {tons:,.0f}吨 ≈ {weeks}周 (>4周)，库存偏高压力 −200 元/吨",
            weeks,
        )
    elif weeks < 1.0:
        return (
            50,
            f"库存 {tons:,.0f}吨 ≈ {weeks}周 (<1周)，库存偏低支撑 +50 元/吨",
            weeks,
        )
    else:
        return (
            0,
            f"库存 {tons:,.0f}吨 ≈ {weeks}周，库存正常范围，不调整",
            weeks,
        )


def _compute_action_bias(
    diagnosis: dict | None,
    forecast: dict | None,
    working_price: float,
    product_id: str = "",
) -> tuple[str, int, str]:
    """Step 7: derive action bias from weekly report RAG + diagnosis signals.

    Priority: RAG weekly report action_bias metadata (§9.1 Step 7, §7 chunking)
    overrides quantitative regime-based logic if present.

    Action bias taxonomy (§5.4):
      accelerate_shipments → −100 CNY (push volume to avoid inventory build)
      flexible_pricing     → 0 CNY (but signals range-based decision)
      biased_upward        → +75 CNY (lean toward upper range)
      watchful_hold        → 0 CNY (hold, no adjustment)
      production_planning  → 0 CNY (ops flag only)
      neutral              → 0 CNY

    Returns (action_bias_code, cny_delta, note_str).
    """
    # ── Step 7a: Check latest weekly report RAG for action_bias metadata
    _RAG_BIAS_TO_DELTA = {
        "accelerate_shipments": -100,
        "biased_upward": 75,
        "flexible_pricing": 0,
        "watchful_hold": 0,
        "production_planning": 0,
        "neutral": 0,
    }
    if product_id:
        try:
            from backend.src.rag.vector_store import kb as _kb

            query = f"{product_id} pricing recommendation action bias"
            hits = _kb.query_weekly(query, n_results=2)
            for hit in hits:
                meta = hit.get("metadata", {})
                rag_product = meta.get("product", "")
                rag_bias = meta.get("action_bias", "")
                if rag_product == product_id and rag_bias in _RAG_BIAS_TO_DELTA:
                    delta = _RAG_BIAS_TO_DELTA[rag_bias]
                    note = (
                        f"OVERRIDE: Weekly report action_bias='{rag_bias}' "
                        f"for {product_id}. "
                    )
                    if rag_bias == "accelerate_shipments":
                        note += f"Applied −100 CNY. Quantitative base suggested {working_price:,.0f}."
                    elif rag_bias == "flexible_pricing":
                        note += "Return range, not point."
                    elif rag_bias == "biased_upward":
                        note += f"+{delta} CNY bias."
                    return rag_bias, delta, note
        except Exception:
            pass

    # ── Step 7b: Quantitative fallback from diagnosis signals
    if not diagnosis:
        return "neutral", 0, ""

    regime = diagnosis.get("market_regime", "neutral")
    net_signal = diagnosis.get("net_signal", "neutral")
    primary_driver = diagnosis.get("primary_driver")
    primary_hyp = (
        primary_driver.get("hypothesis", "") if isinstance(primary_driver, dict) else ""
    )

    # Check downstream propagated signals for demand context
    downstream = diagnosis.get("downstream_propagated_signals", {})
    supportive_downstream_count = sum(
        1 for v in downstream.values() if "supportive" in str(v).lower()
    )

    # Bearish regime with high inventory concern → accelerate shipments
    if regime == "bearish" and net_signal == "bearish":
        return (
            "accelerate_shipments",
            -100,
            "看跌行情，建议加速出货避免库存积压，折扣 −100 元/吨",
        )

    # Bullish regime with cost-push driver → biased upward
    if regime == "bullish" and primary_hyp == "naphtha_cost_push":
        return (
            "biased_upward",
            75,
            "看涨行情 + 石脑油成本驱动，偏强策略 +75 元/吨",
        )

    # Bullish with downstream support → biased upward (stronger)
    if regime == "bullish" and supportive_downstream_count >= 2:
        return (
            "biased_upward",
            100,
            f"看涨 + {supportive_downstream_count}个下游需求支撑信号，偏强策略 +100 元/吨",
        )

    # Bullish generic
    if regime == "bullish":
        return (
            "biased_upward",
            50,
            "市场看涨，偏强策略 +50 元/吨",
        )

    # Neutral but with mixed signals → flexible pricing
    if regime == "neutral" and supportive_downstream_count >= 1:
        return (
            "flexible_pricing",
            0,
            "市场中性但下游有支撑信号，建议灵活定价（区间报价）",
        )

    # Bearish but mild
    if regime == "bearish":
        return "watchful_hold", 0, "市场偏弱，建议观望，不主动上调"

    return "neutral", 0, "市场中性"


def _compute_weekly_plan_pressure(
    product_id: str,
) -> tuple[int, str, dict[str, Any] | None]:
    plan = weekly_plan_service.get_product_plan(product_id)
    if not plan:
        return 0, f"{product_id} 无周计划数据，计划压力中性", None

    pressure_signal = str(plan.get("pressure_signal", "balanced"))
    net_gap = float(plan.get("net_supply_gap_qty") or 0)
    inventory_days = plan.get("inventory_coverage_days")
    avg_tax_price = plan.get("average_tax_price")
    source = plan.get("source", "unknown")

    if pressure_signal == "tight":
        delta = 100 if net_gap >= 200 else 50
        note = (
            f"周计划来源 {source}：计划销售高于计划生产 {net_gap:,.0f} 吨"
            + (
                f"，计划库存覆盖 {inventory_days:.1f} 天"
                if isinstance(inventory_days, (int, float))
                else ""
            )
            + f"，供给偏紧，价格支撑 +{delta} 元/吨"
        )
        return delta, note, plan

    if pressure_signal == "long":
        delta = -100 if net_gap <= -200 else -50
        note = (
            f"周计划来源 {source}：计划生产高于计划销售 {abs(net_gap):,.0f} 吨"
            + (
                f"，计划库存覆盖 {inventory_days:.1f} 天"
                if isinstance(inventory_days, (int, float))
                else ""
            )
            + f"，供给偏宽松，价格压力 {delta} 元/吨"
        )
        return delta, note, plan

    note = f"周计划来源 {source}：供需基本平衡"
    if isinstance(avg_tax_price, (int, float)):
        note += f"，计划税价约 {avg_tax_price:,.0f} 元/吨"
    return 0, note, plan


def _build_triggers(
    product_id: str,
    rec_price: int,
    price_high: int,
    price_low: int,
    naphtha_7d_pct: float,
    diagnosis: dict | None,
) -> list[dict]:
    """Step 9: generate 3 conditional triggers."""
    triggers = []

    # Trigger 1: Naphtha continuation
    if naphtha_7d_pct > 0:
        threshold = round(naphtha_7d_pct + 1.0, 1)
        triggers.append(
            {
                "condition": f"石脑油7日涨幅持续 > {threshold}%",
                "action": f"上调至 {price_high:,} 元/吨",
                "probability": "medium",
            }
        )
    else:
        triggers.append(
            {
                "condition": f"石脑油7日跌幅扩大至 > {abs(naphtha_7d_pct) + 1.0:.1f}%",
                "action": f"调整至 {price_low:,} 元/吨并提示客户",
                "probability": "medium",
            }
        )

    # Trigger 2: Order velocity check
    triggers.append(
        {
            "condition": "本周订单量环比下降 ≥ 10%",
            "action": f"维持 {rec_price:,} 元/吨不变，观察 3–5 天后再调整",
            "probability": "low",
        }
    )

    # Trigger 3: Downstream / SINOPEC listing
    triggers.append(
        {
            "condition": f"SINOPEC上海3月正式挂牌或下游开工率回升至 ≥ 70%",
            "action": f"重新锚定价格，参考挂牌重算公式C折扣",
            "probability": "medium",
        }
    )

    return triggers


def _PRODUCT_WATCH_SIGNALS() -> dict[str, list[str]]:
    return {
        "isoprene": ["SINOPEC上海3月异戊二烯挂牌", "SIS开工率周报", "石脑油周一开盘"],
        "piperylene": [
            "SINOPEC上海3月间戊二烯挂牌",
            "硬化剂开工率",
            "道路划线旺季启动",
        ],
        "dcpd": ["SINOPEC上海DCPD挂牌", "UPR需求恢复信号", "氢化树脂库存"],
        "mixed_c5": ["扬子石化/茂名石化月度挂牌", "石脑油周一开盘", "华南裂解C5南调"],
        "mixed_c9": ["扬子/扬巴C9月度挂牌", "氢化C9库存压力", "石脑油走势"],
        "c5_resin": ["锦海C5树脂报价", "山东鲁华报价", "道路划线市场季节性启动"],
        "c9_resin": ["恒河C9氢化树脂报价", "新疆天利报价", "涂料/油墨市场需求"],
        "sis_rubber": ["巴陵石化SIS挂牌", "卫生材料/热熔胶需求", "异戊二烯库存"],
    }


def _build_watch_signals(product_id: str, override_applied: bool) -> list[str]:
    base = _PRODUCT_WATCH_SIGNALS().get(
        product_id, ["石脑油价格走势", "下游开工率", "SINOPEC挂牌"]
    )
    if override_applied:
        base = ["宏观Override到期时间确认"] + base
    return base[:4]


def _build_dissenting_view(
    product_id: str,
    action: str,
    diagnosis: dict | None,
    forecast: dict | None,
    current_month: int,
) -> str:
    """Generate a specific dissenting view from the evidence."""

    # Pull bear-scenario context if available
    bear_range = ""
    if forecast and "scenarios" in forecast:
        s7 = forecast["scenarios"].get("7d", {})
        bear = s7.get("bear", {})
        if bear:
            bear_range = f"看跌情景下7日价格区间 {bear.get('low',0):,.0f}–{bear.get('high',0):,.0f}，"

    contradictions = ""
    if diagnosis:
        contra = diagnosis.get("contradictions", [])
        if contra:
            contradictions = contra[0] if isinstance(contra[0], str) else str(contra[0])

    # Post-holiday caution
    if current_month in (2, 3) and action in ("raise_price", "hold_upper"):
        return (
            f"{bear_range}节后下游复工进度不确定，追价存在假突破风险；"
            f"建议分批调价，观察3-5天下游开工率后再确认上调幅度。"
            + (f" {contradictions}" if contradictions else "")
        )
    elif action in ("reduce_price", "hold_lower"):
        return (
            f"若SINOPEC月度挂牌未下调，主动降价可能损害盈利；"
            f"先观察竞品动作，再决定是否跟进。"
            + (f" {contradictions}" if contradictions else "")
        )
    else:
        return (
            f"{bear_range}存在市场情绪逆转风险，建议设立价格下限 "
            f"触发止损信号。" + (f" {contradictions}" if contradictions else "")
        )


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

pricing_service = PricingService()
