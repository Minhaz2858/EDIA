"""Regression tests for Phase 1.1 forecast input windows.

These tests protect the get_price_series() SQL-window fix from regressing back
to oldest-row selection. Long mapped product histories must end at the newest
raw market_prices observation, or the model silently trains on stale history.
"""
from __future__ import annotations

import unittest

import pandas as pd
from sqlalchemy import text

from backend.src.db.sqlite_db import db
from backend.src.service.forecasting_engine import _PRODUCT_MAP, get_price_series


LONG_HISTORY_PRODUCTS = ("c5_resin", "blowing_agent")
MIN_RAW_POINTS_FOR_WINDOW_REGRESSION = 500


class TestForecastingDataWindows(unittest.TestCase):
    """Mapped long-history products must fetch the latest source window."""

    def test_long_history_products_end_at_latest_observation(self) -> None:
        with db.engine.connect() as conn:
            for node_id in LONG_HISTORY_PRODUCTS:
                product_id, price_type = _PRODUCT_MAP[node_id]
                with self.subTest(node_id=node_id, product_id=product_id, price_type=price_type):
                    row = conn.execute(
                        text(
                            "SELECT COUNT(*) AS n, MAX(date) AS latest_date "
                            "FROM market_prices "
                            "WHERE product_id = :pid "
                            "AND price_type = :pt "
                            "AND price IS NOT NULL"
                        ),
                        {"pid": product_id, "pt": price_type},
                    ).fetchone()

                    self.assertIsNotNone(row)
                    raw_points = int(row[0] or 0)
                    latest_raw_date = pd.Timestamp(row[1]).normalize()
                    self.assertGreater(
                        raw_points,
                        MIN_RAW_POINTS_FOR_WINDOW_REGRESSION,
                        f"{node_id} must remain a >500-row series for this regression test",
                    )

                    series = get_price_series(product_id, price_type, days=500)

                    self.assertFalse(series.empty, f"{node_id} returned no forecast input series")
                    self.assertEqual(
                        pd.Timestamp(series.index.max()).normalize(),
                        latest_raw_date,
                        f"{node_id} forecast input does not end at the latest raw observation",
                    )


if __name__ == "__main__":
    unittest.main()