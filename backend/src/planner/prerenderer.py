"""Pre-render planner execution output into compact grounding text."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _extract_latest_date(sample_rows: list[dict]) -> str:
    if not sample_rows:
        return "N/A"
    candidates = []
    for row in sample_rows:
        for key in ("date", "PLANDATE", "FDATE", "Unnamed: 0"):
            val = row.get(key)
            if val is not None:
                candidates.append(str(val))
    if not candidates:
        return "N/A"
    return max(candidates)


def _extract_price_values(sample_rows: list[dict]) -> list[float]:
    values: list[float] = []
    for row in sample_rows:
        for key in ("price", "avg_price", "weighted_price", "FTAXPRICE", "FPRICE"):
            if key in row:
                parsed = _to_float(row.get(key))
                if parsed is not None:
                    values.append(parsed)
    return values


def _extract_metric_values(sample_rows: list[dict]) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for row in sample_rows:
        for key in ("avg_value", "total_value", "min_value", "max_value", "row_count", "avg_price"):
            if key not in row:
                continue
            parsed = _to_float(row.get(key))
            if parsed is not None:
                metrics[key] = round(parsed, 2)
    return metrics


def _compact_sample_row(row: dict) -> dict:
    compact: dict = {}
    for key, value in list(row.items())[:8]:
        if value is None or value == "":
            continue
        compact[str(key)] = value
    return compact


def render_metrics(execution: dict) -> dict:
    by_product: dict[str, dict] = {}
    data_sources = execution.get("data_sources") or []

    for bucket in execution.get("rows", []):
        product = str(bucket.get("product") or "")
        intent = str(bucket.get("intent") or "")
        table = str(bucket.get("table") or "")
        rows = list(bucket.get("rows") or [])

        product_entry = by_product.setdefault(product, {})
        product_entry[intent] = {
            "table": table,
            "row_count": len(rows),
            "latest_date": _extract_latest_date(rows),
            "avg_price": None,
            "metrics": _extract_metric_values(rows),
            "sample_row": rows[0] if rows else {},
        }

        price_values = _extract_price_values(rows)
        if price_values:
            product_entry[intent]["avg_price"] = round(sum(price_values) / len(price_values), 2)

    return {
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "plan": execution.get("plan") or {},
        "by_product": by_product,
        "errors": execution.get("errors") or [],
        "data_sources": data_sources,
    }


def format_for_prompt(metrics: dict) -> str:
    plan = metrics.get("plan") or {}
    lines = [
        "PLANNER GROUNDING CONTEXT:",
        f"Products: {', '.join(plan.get('products') or []) or 'N/A'}",
        f"Intents: {', '.join(plan.get('intents') or []) or 'N/A'}",
    ]

    tr = plan.get("time_range") or {}
    if tr.get("start") and tr.get("end"):
        lines.append(f"Time range: {tr['start']} to {tr['end']}")

    lines.append("")
    lines.append("FOCUSED DATA SUMMARY:")

    by_product = metrics.get("by_product") or {}
    if not by_product:
        lines.append("- No planner data rows returned. Fallback context may be required.")
    else:
        for product, intents in by_product.items():
            lines.append(f"- Product: {product}")
            for intent, detail in intents.items():
                row_count = int(detail.get("row_count") or 0)
                latest = str(detail.get("latest_date") or "N/A")
                avg_price = detail.get("avg_price")
                metrics = detail.get("metrics") or {}
                facts = [f"{row_count} rows", f"latest={latest}"]
                if avg_price is not None:
                    facts.append(f"avg_price={avg_price}")
                for key, value in metrics.items():
                    facts.append(f"{key}={value}")
                lines.append(f"  - {intent}: {', '.join(facts)}")

                sample = _compact_sample_row(detail.get("sample_row") or {})
                if sample:
                    sample_text = "; ".join(f"{key}={value}" for key, value in sample.items())
                    lines.append(f"    sample: {sample_text}")

    data_sources = metrics.get("data_sources") or []
    if data_sources:
        lines.append("")
        lines.append("Data sources:")
        for source in data_sources:
            lines.append(f"- {source}")

    errors = metrics.get("errors") or []
    if errors:
        lines.append("")
        lines.append("Planner fetch notes:")
        for err in errors[:5]:
            lines.append(
                f"- {err.get('intent')}:{err.get('table')} -> {err.get('error')}"
            )

    return "\n".join(lines).strip()
