"""Phase 1.2 regression test: every `_PRODUCT_MAP` entry must point at a
`(product_id, price_type)` pair with at least 30 rows in `market_prices`.

Catches future mapping drift (a re-map to a sparser series, or a price_type
rename in the loader that orphans an entry) before it ships to production.

Threshold (30 rows) is intentionally low to allow for newly-onboarded
products. Anything below 30 rows means the model is essentially blind for
that product and the operator should be alerted.

Known low-data exception: `hydrogenated_c9` / `c9_resin` map to
`partner_forecast_price` which currently has only 2 rows. That is an
upstream data-loading issue, not a mapping bug, and is tracked separately.
This test allow-lists those entries so it doesn't fail on an unrelated
regression.
"""
from __future__ import annotations

import unittest

from sqlalchemy import text

from backend.src.db.sqlite_db import db
from backend.src.service.forecasting_engine import _PRODUCT_MAP


# Mapped (product_id, price_type) pairs known to have <30 rows due to upstream
# data-loading issues, not mapping bugs. Tracked separately.
KNOWN_SPARSE_EXCEPTIONS = {
    # 2 rows, partner data gap. Upstream data-loading issue.
    ("hydrogenated_c9", "partner_forecast_price"),
    # 2 rows in `partner_forecast_price` while a 1590-row
    # `erp_sale_order_weighted` series exists for the same product.
    # Re-mapping decision deferred to Phase 1.2b pending Ecisco call on
    # market-price vs internal-sale-price semantics. See
    # PHASE_1_2B_REMAP_CANDIDATES.md.
    ("trimethylbenzene", "partner_forecast_price"),
}

MIN_ROWS = 30


class TestProductMapInventory(unittest.TestCase):
    """Each `_PRODUCT_MAP` entry must point at a non-trivially-populated series."""

    def test_each_mapped_series_has_min_rows(self) -> None:
        with db.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT product_id, price_type, COUNT(*) n "
                    "FROM market_prices GROUP BY product_id, price_type"
                )
            ).fetchall()
        counts = {(r[0], r[1]): r[2] for r in rows}

        failures: list[str] = []
        for node_id, (pid, pt) in _PRODUCT_MAP.items():
            n = counts.get((pid, pt), 0)
            if (pid, pt) in KNOWN_SPARSE_EXCEPTIONS:
                continue
            if n < MIN_ROWS:
                failures.append(
                    f"{node_id} → ({pid}, {pt}) has {n} rows in market_prices "
                    f"(minimum {MIN_ROWS})"
                )

        self.assertFalse(
            failures,
            "Mapping drift detected — _PRODUCT_MAP entries point at sparse "
            "or missing market_prices series:\n  " + "\n  ".join(failures),
        )


if __name__ == "__main__":
    unittest.main()
