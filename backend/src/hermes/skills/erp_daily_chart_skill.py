"""Generate interactive daily ERP sales bar-chart visualization for chat.

This skill queries daily ERP sales data and formats it into a ChatVisualization
JSON dict that the frontend renders as an interactive Recharts bar chart.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def build_erp_daily_bar_visualization(
    product_id: Optional[str] = None,
    period: Optional[str] = None,
    language: str = "zh",
) -> Optional[Dict[str, Any]]:
    """Query daily ERP sales and build a ChatVisualization JSON dict.

    Args:
        product_id: Optional product ID to filter by (e.g., "c5", "c9").
        period: Optional period string (e.g., "2025-05-04 to 2025-05-10",
                "last 7 days", "this week").
        language: "zh" or "en" for localization.

    Returns:
        ChatVisualization-compatible dict with kind="bar", x_key="date",
        or None if no data is available.
    """
    try:
        from backend.src.tools.erp_tools import query_sales_daily
    except ImportError:
        logger.error("Failed to import query_sales_daily")
        return None

    is_en = str(language or "zh").lower().startswith("en")

    # Query daily sales data
    raw_result = query_sales_daily(product_id=product_id, period=period)
    raw_text = str(raw_result or "")

    if not raw_text or raw_text.startswith("Error") or "No ERP data" in raw_text:
        logger.info("No daily sales data returned for product=%s period=%s", product_id, period)
        return None

    # Parse the DAILY SALES text table
    # Expected format:
    # DAILY SALES (start to end):
    # Date         Volume(ton)      Avg Price(CNY/ton)
    # -------------------------------------------------
    # 2025-05-04     12.50            8,500.00
    # ...
    data = _parse_daily_sales_text(raw_text)
    if not data:
        logger.info("Failed to parse daily sales text")
        return None

    if len(data) < 2:
        logger.info("Insufficient daily data points: %d", len(data))
        return None

    # Build product display name
    product_display = _resolve_product_display_name(product_id, is_en)

    # Format dates for display (show MM-DD for compactness)
    formatted_data = []
    for row in data:
        date_str = str(row["date"])
        # Compact date: "05-04" from "2025-05-04"
        if len(date_str) >= 10:
            display_date = date_str[5:10] if "-" in date_str else date_str[:5]
        else:
            display_date = date_str

        formatted_data.append({
            "date": display_date,
            "full_date": date_str[:10] if len(date_str) >= 10 else date_str,
            "volume": round(float(row["volume"]), 2),
            "avg_price": round(float(row["avg_price"]), 2),
        })

    # Build series based on language
    if is_en:
        title = f"{product_display} — Daily Sales"
        volume_label = "Sales (ton)"
        price_label = "Avg Price (CNY/ton)"
    else:
        title = f"{product_display} — 日销售统计"
        volume_label = "销量(吨)"
        price_label = "均价(元/吨)"

    return {
        "kind": "bar",
        "title": title,
        "subtitle": f"{data[0]['date'][:10]}" + " ~ " + f"{data[-1]['date'][:10]}" if len(data) > 1 else "",
        "unit": "ton",
        "x_key": "date",
        "series": [
            {
                "key": "volume",
                "label": volume_label,
                "color": "#2563eb",
            },
        ],
        "data": formatted_data,
        "summary": {
            "total_volume": round(sum(r["volume"] for r in formatted_data), 2),
            "avg_daily_volume": round(
                sum(r["volume"] for r in formatted_data) / len(formatted_data), 2
            ),
            "avg_price": round(
                sum(r["avg_price"] for r in formatted_data) / len(formatted_data), 2
            ),
        },
    }


def build_erp_daily_line_visualization(
    product_id: Optional[str] = None,
    period: Optional[str] = None,
    language: str = "zh",
) -> Optional[Dict[str, Any]]:
    """Query daily ERP sales and build a line chart visualization.

    Similar to build_erp_daily_bar_visualization, but returns kind="line"
    for a time-series line chart instead of a bar chart.
    """
    bar_viz = build_erp_daily_bar_visualization(product_id, period, language)
    if bar_viz is None:
        return None

    # Convert to line chart
    bar_viz["kind"] = "line"
    is_en = str(language or "zh").lower().startswith("en")
    product_display = _resolve_product_display_name(product_id, is_en)

    if is_en:
        bar_viz["title"] = f"{product_display} — Daily Sales Trend"
    else:
        bar_viz["title"] = f"{product_display} — 日销售趋势"

    # Add line-specific series config
    for s in bar_viz.get("series", []):
        s["stroke_width"] = 2
        s["dot"] = True

    return bar_viz


def _parse_daily_sales_text(text: str) -> List[Dict[str, Any]]:
    """Parse the text output of query_sales_daily() into structured rows.

    Expected format:
        DAILY SALES (start to end):
        Date         Volume(ton)      Avg Price(CNY/ton)
        -------------------------------------------------
        2025-05-04     12.50            8,500.00
    """
    rows = []
    in_table = False

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        # Detect table header
        if "Date" in line and ("Volume" in line or "Volume(ton)" in line):
            in_table = True
            continue

        # Skip separator line
        if re.match(r"^-+$", line.replace(" ", "-")):
            continue

        if in_table:
            # Try to parse date + volume + price
            # Date format: 2025-05-04
            date_match = re.match(r"(\d{4}-\d{2}-\d{2})", line)
            if not date_match:
                continue

            date_str = date_match.group(1)
            rest = line[date_match.end():].strip()

            # Extract numbers (volume and price)
            numbers = re.findall(r"[\d,]+\.?\d*", rest)
            if len(numbers) < 2:
                continue

            try:
                volume = float(numbers[0].replace(",", ""))
                avg_price = float(numbers[1].replace(",", ""))
                rows.append({
                    "date": date_str,
                    "volume": volume,
                    "avg_price": avg_price,
                })
            except (ValueError, IndexError):
                logger.debug("Failed to parse daily sales row: %s", line)
                continue

    return rows


def _resolve_product_display_name(product_id: Optional[str], is_en: bool) -> str:
    """Resolve product ID to display name."""
    if not product_id:
        return "C5/C9 Petroleum Resin" if is_en else "C5/C9石油树脂"

    try:
        from backend.src.domain.product_resolver import PRODUCTS
        product_info = PRODUCTS.get(product_id)
        if product_info:
            return product_info.get("name_en" if is_en else "name_cn", product_id)
    except ImportError:
        pass

    # Fallback: format the ID
    display_map = {
        "c5": "C5 Petroleum Resin" if is_en else "C5石油树脂",
        "c9": "C9 Petroleum Resin" if is_en else "C9石油树脂",
        "c5_c9": "C5/C9 Petroleum Resin" if is_en else "C5/C9共聚树脂",
    }
    return display_map.get(product_id, product_id)
