from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _severity_rank(severity: str) -> int:
    return {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(severity, 0)


def _market_signal(change_pct: Optional[float]) -> str:
    if change_pct is None:
        return "unknown"
    if change_pct >= 5.0:
        return "sharp_up"
    if change_pct >= 1.0:
        return "up"
    if change_pct <= -5.0:
        return "sharp_down"
    if change_pct <= -1.0:
        return "down"
    return "flat"


def _risk_from_erp(risk_level: str, inventory_days: Optional[float], sales_growth_pct: Optional[float]) -> tuple[str, str]:
    level = str(risk_level or "unknown").strip().lower()
    if level in {"critical_low_stock", "low_stock"}:
        severity = "critical" if level == "critical_low_stock" else "high"
        return severity, "inventory_shortage"
    if level == "high_stock":
        return "high", "inventory_overhang"
    if isinstance(inventory_days, (int, float)) and inventory_days <= 7:
        return "critical", "inventory_shortage"
    if isinstance(inventory_days, (int, float)) and inventory_days >= 45:
        return "high", "inventory_overhang"
    if isinstance(sales_growth_pct, (int, float)) and sales_growth_pct <= -20.0:
        return "medium", "demand_drop"
    return "low", "normal"


def _action_for(product_id: str, market_signal: str, risk_code: str, severity: str) -> str:
    if risk_code == "inventory_shortage":
        if market_signal in {"up", "sharp_up"}:
            return f"Protect {product_id} allocation and review spot quote discipline before accepting low-margin orders."
        return f"Check {product_id} replenishment and prioritize confirmed customer demand."
    if risk_code == "inventory_overhang":
        if market_signal in {"down", "sharp_down"}:
            return f"Reduce {product_id} high-cost exposure and use trigger-based discounts for slow-moving inventory."
        return f"Convert {product_id} inventory into firm orders before adding new production."
    if risk_code == "demand_drop":
        return f"Recheck {product_id} customer pipeline and separate repeat-order risk from one-off timing noise."
    if severity in {"critical", "high"}:
        return f"Escalate {product_id} to the daily commercial review with market and ERP evidence attached."
    return f"Keep {product_id} on watch; no immediate intervention required unless market or ERP signal changes."


def build_dashboard_insights(combined_bundle: Dict[str, Any]) -> Dict[str, Any]:
    """Build deterministic dashboard actions from the combined market/ERP cache."""
    by_product = combined_bundle.get("by_product") if isinstance(combined_bundle.get("by_product"), dict) else {}
    products: list[dict[str, Any]] = []
    data_gaps: list[dict[str, str]] = []

    for product_id, payload in by_product.items():
        if not isinstance(payload, dict):
            continue
        market = payload.get("market") if isinstance(payload.get("market"), dict) else None
        erp = payload.get("erp") if isinstance(payload.get("erp"), dict) else None
        normalized_id = str(product_id or payload.get("product_id") or "").strip().lower()
        if not normalized_id:
            continue

        if market is None:
            data_gaps.append({"product_id": normalized_id, "missing": "market", "message": "No market snapshot in current insight bundle."})
        if erp is None:
            data_gaps.append({"product_id": normalized_id, "missing": "erp", "message": "No ERP KPI snapshot in current insight bundle."})

        weekly_change_pct = _safe_float((market or {}).get("weekly_change_pct"))
        latest_price = _safe_float((market or {}).get("latest_price"))
        inventory_days = _safe_float((erp or {}).get("inventory_days"))
        sales_growth_pct = _safe_float((erp or {}).get("sales_growth_pct"))
        market_signal = _market_signal(weekly_change_pct)
        severity, risk_code = _risk_from_erp(str((erp or {}).get("risk_level") or ""), inventory_days, sales_growth_pct)

        if market_signal in {"sharp_up", "sharp_down"} and severity == "low":
            severity = "medium"
            risk_code = "market_volatility"
        if market_signal == "sharp_down" and risk_code == "inventory_overhang":
            severity = "critical"

        score = _severity_rank(severity) * 10
        if market_signal in {"sharp_up", "sharp_down"}:
            score += 4
        if isinstance(sales_growth_pct, (int, float)) and sales_growth_pct < 0:
            score += min(5, int(abs(sales_growth_pct) // 5))

        products.append(
            {
                "product_id": normalized_id,
                "severity": severity,
                "score": score,
                "risk_code": risk_code,
                "market_signal": market_signal,
                "weekly_change_pct": weekly_change_pct,
                "latest_price": latest_price,
                "inventory_days": inventory_days,
                "sales_growth_pct": sales_growth_pct,
                "action": _action_for(normalized_id, market_signal, risk_code, severity),
                "evidence": {
                    "market_as_of": (market or {}).get("as_of_date"),
                    "erp_period_start": (erp or {}).get("period_start"),
                    "erp_period_end": (erp or {}).get("period_end"),
                    "market_source": (market or {}).get("data_source"),
                    "erp_risk_level": (erp or {}).get("risk_level"),
                },
            }
        )

    products.sort(key=lambda item: (int(item.get("score") or 0), str(item.get("product_id") or "")), reverse=True)
    top_products = products[:5]
    actions = [item["action"] for item in top_products if str(item.get("action") or "").strip()]
    critical_count = sum(1 for item in products if item.get("severity") == "critical")
    high_count = sum(1 for item in products if item.get("severity") == "high")

    if critical_count:
        summary = f"{critical_count} critical dashboard signal(s) need same-day review."
    elif high_count:
        summary = f"{high_count} high-priority dashboard signal(s) need commercial follow-up."
    elif products:
        summary = "Dashboard signals are mostly stable; monitor ranked watchlist and data gaps."
    else:
        summary = "No dashboard insight inputs are available yet."

    return {
        "ok": bool(products),
        "contract": "dashboard_insight_skill_v1",
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "summary": summary,
        "priority_products": top_products,
        "actions": actions[:5],
        "data_gaps": data_gaps[:10],
        "source_counts": {
            "products": len(products),
            "critical": critical_count,
            "high": high_count,
            "data_gaps": len(data_gaps),
        },
    }