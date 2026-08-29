"""Seasonal decomposition analytics.

Decomposes a product's price series into trend + seasonal + residual components
using a simple centred moving average approach (no external scipy/statsmodels).

This module is:
- Pure Python (no numpy/scipy required for basic operation)
- Callable from server.py as an async executor task
- Cached per product with a 24-hour TTL

Results are used by the chatbot (T4-SEASONAL evidence tier) and
the /api/v2/market/seasonal/{product_id} endpoint.
"""
from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

_SEASONAL_CACHE: Dict[str, Dict] = {}
_SEASONAL_CACHE_TTL = 86400  # 24 hours


def _centred_moving_average(values: List[float], window: int) -> List[Optional[float]]:
    """Compute centred moving average with specified window length."""
    n = len(values)
    half = window // 2
    result: List[Optional[float]] = [None] * n
    for i in range(half, n - half):
        segment = values[i - half : i + half + 1]
        result[i] = sum(segment) / len(segment)
    return result


def _safe_mean(vals: List[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def decompose(
    dates: List[str],
    prices: List[float],
    period: int = 52,  # weeks ~= 1 year
) -> Dict:
    """Additive STL-like decomposition: price = trend + seasonal + residual.

    Args:
        dates: ISO date strings (YYYY-MM-DD)
        prices: Corresponding prices
        period: Seasonal period in data points (52 ≈ weekly data over 1 year)

    Returns:
        Dict with keys: dates, prices, trend, seasonal, residual,
        seasonal_factors (one per period offset), stats
    """
    n = len(prices)
    if n < period * 2:
        return {
            "dates": dates,
            "prices": prices,
            "trend": [None] * n,
            "seasonal": [0.0] * n,
            "residual": prices[:],
            "seasonal_factors": {},
            "stats": {"insufficient_data": True, "n": n, "required": period * 2},
        }

    # Step 1: Trend via centred moving average
    trend = _centred_moving_average(prices, period)

    # Step 2: Detrended series
    detrended = [
        prices[i] - trend[i] if trend[i] is not None else None
        for i in range(n)
    ]

    # Step 3: Seasonal factors per period index (0..period-1)
    period_buckets: Dict[int, List[float]] = {k: [] for k in range(period)}
    for i, v in enumerate(detrended):
        if v is not None:
            period_buckets[i % period].append(v)

    seasonal_factors = {k: _safe_mean(v) for k, v in period_buckets.items()}

    # Normalise so factors sum to ~0
    mean_factor = _safe_mean(list(seasonal_factors.values()))
    seasonal_factors = {k: v - mean_factor for k, v in seasonal_factors.items()}

    # Step 4: Construct seasonal and residual series
    seasonal = [seasonal_factors[i % period] for i in range(n)]
    residual = [
        prices[i] - (trend[i] or prices[i]) - seasonal[i]
        for i in range(n)
    ]

    # Stats
    residuals_clean = [r for r in residual if math.isfinite(r)]
    mean_r = _safe_mean(residuals_clean)
    std_r = math.sqrt(_safe_mean([(r - mean_r) ** 2 for r in residuals_clean])) if residuals_clean else 0

    # Current seasonal factor (based on latest date's week-of-year)
    current_period_idx = None
    current_seasonal_factor = None
    if dates:
        try:
            dt = datetime.strptime(dates[-1][:10], "%Y-%m-%d")
            current_period_idx = (n - 1) % period
            current_seasonal_factor = round(seasonal_factors.get(current_period_idx, 0), 1)
        except Exception:
            pass

    return {
        "dates": dates,
        "prices": prices,
        "trend": [round(t, 1) if t is not None else None for t in trend],
        "seasonal": [round(s, 1) for s in seasonal],
        "residual": [round(r, 1) for r in residual],
        "seasonal_factors": {str(k): round(v, 1) for k, v in seasonal_factors.items()},
        "stats": {
            "n": n,
            "period": period,
            "residual_std": round(std_r, 1),
            "current_period_idx": current_period_idx,
            "current_seasonal_factor": current_seasonal_factor,
            "current_seasonal_label": (
                "季节性上涨压力" if (current_seasonal_factor or 0) > 100
                else "季节性下跌压力" if (current_seasonal_factor or 0) < -100
                else "季节性影响中性"
            ),
        },
    }


def get_seasonal_analysis(product_id: str) -> Dict:
    """Public entry point — returns cached decomposition for product_id."""
    now = time.time()
    cached = _SEASONAL_CACHE.get(product_id)
    if cached and (now - cached["ts"]) < _SEASONAL_CACHE_TTL:
        return cached["data"]

    result = _compute_seasonal(product_id)
    _SEASONAL_CACHE[product_id] = {"data": result, "ts": now}
    return result


def _compute_seasonal(product_id: str) -> Dict:
    """Fetch price history from market_data and run decomposition."""
    try:
        from backend.src.service.market_data_service import market_data
        history = market_data.get_price_history(product_id)
        if not history:
            return {"error": f"No price history for {product_id}"}

        dates = [h["date"] for h in history]
        prices = [float(h["price"]) for h in history]

        # Determine period: weekly data → 52, daily data → 365
        if len(dates) >= 2:
            try:
                d1 = datetime.strptime(dates[0][:10], "%Y-%m-%d")
                d2 = datetime.strptime(dates[1][:10], "%Y-%m-%d")
                gap_days = abs((d2 - d1).days)
                period = 52 if gap_days >= 6 else 365
            except Exception:
                period = 52
        else:
            period = 52

        result = decompose(dates, prices, period=period)
        result["product_id"] = product_id
        return result

    except Exception as e:
        return {"error": str(e), "product_id": product_id}


def get_seasonal_insight_text(product_id: str, language: str = "zh") -> str:
    """Returns a 1-2 sentence natural language insight for the chatbot (T4-SEASONAL)."""
    try:
        analysis = get_seasonal_analysis(product_id)
        stats = analysis.get("stats", {})
        if stats.get("insufficient_data"):
            return ""

        factor = stats.get("current_seasonal_factor", 0) or 0
        label = stats.get("current_seasonal_label", "")
        std = stats.get("residual_std", 0) or 0

        if language == "en":
            direction = "upward" if factor > 0 else "downward" if factor < 0 else "neutral"
            return (
                f"[T4-SEASONAL] Seasonal decomposition shows a {direction} seasonal bias "
                f"of {factor:+.0f} CNY/ton at this time of year "
                f"(residual std dev: ±{std:.0f})."
            )
        else:
            direction = "↑上涨" if factor > 100 else "↓下跌" if factor < -100 else "中性"
            return (
                f"[T4-SEASONAL] 季节性分解：当前时点历史季节因子 {factor:+.0f}元/吨（{label}），"
                f"残差标准差 ±{std:.0f}。"
            )
    except Exception:
        return ""
