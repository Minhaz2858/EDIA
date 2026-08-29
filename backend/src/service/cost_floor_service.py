"""Cost floor model for C5/C9 petrochemical products.

Computes theoretical minimum viable prices based on naphtha feedstock cost
and product-specific yield factors from the steam-cracking process.
"""
from __future__ import annotations

from typing import Dict, Optional

# Yield factors (kg product / kg naphtha)
# Based on typical steam-cracker outputs for Chinese plants
YIELD_FACTORS: Dict[str, float] = {
    "cracked_c5": 0.300,     # Bulk C5 fraction ~30% of naphtha
    "isoprene": 0.048,       # Isoprene ~16% of C5 fraction × 30%
    "piperylene": 0.042,     # Piperylene ~14% of C5 fraction × 30%
    "dcpd": 0.030,           # DCPD ~10% of C5 fraction × 30%
    "raffinate_c5": 0.150,   # Raffinate after isoprene/piperylene removal
    "blowing_agent": 0.050,  # Pentane blowing agent fraction
    "cracked_c9": 0.120,     # Bulk C9 fraction from ethylene cracker
    "styrene": 0.060,        # Styrene ~50% of C9 × 12%
}

# Minimum processing margin (% above raw feedstock cost)
# Reflects energy, catalyst, labor, depreciation
MIN_PROCESSING_MARGIN_PCT = 0.18  # 18%

# Human-readable labels
PRODUCT_LABELS: Dict[str, str] = {
    "isoprene": "异戊二烯",
    "piperylene": "间戊二烯",
    "dcpd": "双环戊二烯",
    "cracked_c5": "裂解碳五",
    "raffinate_c5": "抽余碳五",
    "blowing_agent": "戊烷发泡剂",
    "cracked_c9": "裂解碳九",
    "styrene": "苯乙烯",
}


def compute_floor(product_id: str, naphtha_price: float) -> Optional[Dict]:
    """Compute the theoretical cost floor for a product given naphtha price.

    Args:
        product_id: Canonical product ID (e.g. "isoprene")
        naphtha_price: Naphtha spot price in CNY/ton

    Returns:
        Dict with floor_price, yield_factor, min_margin_pct, naphtha_price
        or None if product is not mapped.
    """
    yield_factor = YIELD_FACTORS.get(product_id)
    if not yield_factor or not naphtha_price or naphtha_price <= 0:
        return None

    # Raw cost = naphtha_price / yield_factor (CNY/ton of product)
    raw_cost = naphtha_price / yield_factor
    floor_price = round(raw_cost * (1 + MIN_PROCESSING_MARGIN_PCT), 0)

    return {
        "product_id": product_id,
        "naphtha_price": round(naphtha_price, 0),
        "yield_factor": yield_factor,
        "raw_cost": round(raw_cost, 0),
        "min_margin_pct": MIN_PROCESSING_MARGIN_PCT,
        "floor_price": floor_price,
    }


def compute_all_floors(naphtha_price: float) -> Dict[str, Dict]:
    """Compute cost floors for all mapped products."""
    result = {}
    for product_id in YIELD_FACTORS:
        floor = compute_floor(product_id, naphtha_price)
        if floor:
            result[product_id] = floor
    return result


class CostFloorService:
    """Wraps cost floor calculation with live naphtha price from market_data."""

    def get_naphtha_price(self) -> Optional[float]:
        try:
            from backend.src.service.market_data_service import market_data
            price = market_data.get_latest_price("naphtha")
            return price if price and price > 0 else None
        except Exception:
            return None

    def get_floor(self, product_id: str) -> Optional[Dict]:
        naphtha_price = self.get_naphtha_price()
        if naphtha_price is None:
            return None
        floor = compute_floor(product_id, naphtha_price)
        if floor is None:
            return None

        # Get current market price to check if below floor
        try:
            from backend.src.service.market_data_service import market_data
            current_price = market_data.get_latest_price(product_id)
            if current_price and current_price > 0:
                floor["current_price"] = round(current_price, 0)
                floor["below_floor"] = current_price < floor["floor_price"]
                floor["distance_from_floor"] = round(current_price - floor["floor_price"], 0)
                floor["margin_pct"] = round(
                    (current_price - floor["floor_price"]) / floor["floor_price"] * 100, 1
                )
        except Exception:
            pass

        return floor

    def get_all_floors(self) -> Dict[str, Dict]:
        naphtha_price = self.get_naphtha_price()
        if naphtha_price is None:
            return {}

        from backend.src.service.market_data_service import market_data
        result = {}
        for product_id in YIELD_FACTORS:
            floor = compute_floor(product_id, naphtha_price)
            if not floor:
                continue
            current_price = market_data.get_latest_price(product_id)
            if current_price and current_price > 0:
                floor["current_price"] = round(current_price, 0)
                floor["below_floor"] = current_price < floor["floor_price"]
                floor["distance_from_floor"] = round(current_price - floor["floor_price"], 0)
                floor["margin_pct"] = round(
                    (current_price - floor["floor_price"]) / floor["floor_price"] * 100, 1
                )
            result[product_id] = floor
        return result

    def get_inventory_signal(self, product_id: str) -> Optional[Dict]:
        """Returns inventory / operating rate signal for a product."""
        try:
            from backend.src.service.erp_kpi_service import ErpKPIService
            kpi = ErpKPIService().get_product_kpi(product_id)
            if kpi is None:
                return None

            inventory_days = kpi.inventory_days
            if inventory_days is None:
                return None

            if inventory_days < 15:
                signal = "VERY_LOW"
                label = "库存紧张 (低于15天)"
                bias = "bullish"
            elif inventory_days < 30:
                signal = "NORMAL"
                label = "库存正常 (15-30天)"
                bias = "neutral"
            elif inventory_days < 45:
                signal = "ELEVATED"
                label = "库存偏多 (30-45天)"
                bias = "slightly_bearish"
            else:
                signal = "HIGH"
                label = "库存过剩 (超过45天)"
                bias = "bearish"

            return {
                "product_id": product_id,
                "inventory_days": inventory_days,
                "inventory_tons": kpi.inventory_tons,
                "sales_30d_ton": kpi.sales_30d_ton,
                "signal": signal,
                "label": label,
                "bias": bias,
            }
        except Exception:
            return None


# Module-level singleton
cost_floor_service = CostFloorService()
