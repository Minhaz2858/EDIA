from __future__ import annotations

from typing import Any


def build_fx_rate_payload(row: Any | None) -> dict[str, Any]:
    if not row:
        return {
            "price": None,
            "unit": "USD/CNY",
            "as_of_date": None,
            "series_product_id": "",
            "series_price_type": "",
            "data_source": "market_prices",
            "available": False,
        }

    as_of_date, product_id, price_type, price = row
    return {
        "price": float(price),
        "unit": "USD/CNY",
        "as_of_date": str(as_of_date),
        "series_product_id": str(product_id),
        "series_price_type": str(price_type),
        "data_source": "market_prices",
        "available": True,
    }