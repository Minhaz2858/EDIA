"""Tests for Phase 2.2 per-product ARIMA order loading and fallback logic.

Fast tests (no DB, no JSON required): always run.
Conditional tests (skip if arima_orders.json is absent): validate JSON format and bounds.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Tuple
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parent.parent


class TestArimaOrderFallback(unittest.TestCase):
    """Verify that _arima_order_for() always returns a valid 3-tuple and
    silently falls back to (2, 1, 2) on any error.
    """

    def _call_with_path(self, path: Path) -> Tuple[int, int, int]:
        """Import load helpers with _ARIMA_ORDERS_PATH patched to *path*."""
        from backend.src.service import forecasting_engine as eng

        # We need to re-run _load_arima_orders() with the patched path.
        # Patch the module-level constant and call the loader directly.
        with patch.object(eng, "_ARIMA_ORDERS_PATH", path):
            orders = eng._load_arima_orders()
        return orders

    def test_fallback_when_json_missing(self) -> None:
        """Missing arima_orders.json must produce {} (all products fall back to (2,1,2))."""
        nonexistent = ROOT / "backend" / "data" / "__does_not_exist__.json"
        orders = self._call_with_path(nonexistent)
        self.assertEqual(orders, {})

    def test_fallback_on_malformed_json(self) -> None:
        """Garbage JSON content must return {} without raising."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write("this is not json }{")
            tmp_path = Path(f.name)
        try:
            orders = self._call_with_path(tmp_path)
            self.assertEqual(orders, {})
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_arima_order_for_unknown_product(self) -> None:
        """_arima_order_for('not_a_product') must return the default (2,1,2)."""
        from backend.src.service.forecasting_engine import _arima_order_for

        result = _arima_order_for("not_a_product_xyzzy")
        self.assertEqual(result, (2, 1, 2))

    def test_meta_key_excluded_from_arima_orders(self) -> None:
        """The _meta key in JSON must not surface as a product order."""
        payload = {
            "_meta": {
                "generated_at": "2026-04-29T00:00:00Z",
                "n_adopted": 1,
            },
            "styrene": [3, 1, 0],
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(payload, f)
            tmp_path = Path(f.name)
        try:
            orders = self._call_with_path(tmp_path)
            self.assertNotIn("_meta", orders)
            self.assertIn("styrene", orders)
            self.assertEqual(orders["styrene"], (3, 1, 0))
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_fit_arima_forecast_accepts_order_param(self) -> None:
        """_fit_arima_forecast must accept an explicit order kwarg without error."""
        from backend.src.service.forecasting_engine import _fit_arima_forecast

        rng = np.random.default_rng(42)
        series_values = 100.0 + np.cumsum(rng.normal(0, 1, 80))
        import pandas as pd

        s = pd.Series(
            series_values,
            index=pd.date_range("2025-01-01", periods=80, freq="D"),
            dtype=float,
        )

        # Should return an array or None — must not raise
        result = _fit_arima_forecast(s, 7, order=(1, 1, 1))
        if result is not None:
            self.assertIsInstance(result, np.ndarray)
            self.assertEqual(len(result), 7)


# ── Conditional tests: require arima_orders.json to exist ─────────────────

_JSON_PATH = ROOT / "backend" / "data" / "arima_orders.json"
_json_exists = _JSON_PATH.exists()


@unittest.skipUnless(_json_exists, "arima_orders.json not yet generated; skip format checks")
class TestArimaOrdersJson(unittest.TestCase):
    """Validate format and grid-bounds of the generated arima_orders.json."""

    @classmethod
    def setUpClass(cls) -> None:
        with open(_JSON_PATH, encoding="utf-8") as f:
            cls.raw = json.load(f)
        cls.products = {k: v for k, v in cls.raw.items() if not k.startswith("_")}

    def test_json_format_valid(self) -> None:
        """Every non-_meta value must be a 3-element list of integers."""
        for pid, order in self.products.items():
            with self.subTest(product=pid):
                self.assertIsInstance(order, list, f"{pid}: expected list, got {type(order)}")
                self.assertEqual(len(order), 3, f"{pid}: expected 3 elements, got {len(order)}")
                for i, val in enumerate(order):
                    self.assertIsInstance(
                        val, int, f"{pid}[{i}]: expected int, got {type(val)}"
                    )

    def test_orders_in_grid_bounds(self) -> None:
        """Every adopted order must be within the approved grid bounds."""
        for pid, (p, d, q) in self.products.items():
            with self.subTest(product=pid):
                self.assertIn(p, range(4), f"{pid}: p={p} out of {{0-3}}")
                self.assertIn(d, range(2), f"{pid}: d={d} out of {{0-1}}")
                self.assertIn(q, range(4), f"{pid}: q={q} out of {{0-3}}")

    def test_adopted_products_differ_from_default_if_json_nonempty(self) -> None:
        """If the JSON is non-empty (minus _meta), at least one order must differ from (2,1,2)."""
        if not self.products:
            self.skipTest("JSON was generated but no products were adopted; nothing to check")
        non_default = [
            pid for pid, order in self.products.items() if tuple(order) != (2, 1, 2)
        ]
        self.assertGreater(
            len(non_default),
            0,
            f"All adopted orders are (2,1,2); expected at least one non-default. Got: {self.products}",
        )


if __name__ == "__main__":
    unittest.main()
