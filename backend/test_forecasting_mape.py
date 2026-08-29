"""Slow Phase 1.4 MAPE guardrail for forecast correctness regressions.

This DB-dependent test replays the same backtest methodology as
scripts/measure_phase1_mape.py and compares each product to the locked Phase 1
Final baseline with the approved 10% margin. It catches the Phase 1 SQL-window
and mapping fixes being undone before deployment.
"""
from __future__ import annotations

import os
import unittest

from backend.src.validation.backtest_runner import BacktestRunner
from scripts.measure_phase1_mape import END, HORIZON, START, STEP_DAYS


def _slow_db_tests_enabled() -> bool:
    return os.getenv("RUN_SLOW_DB_TESTS", "").strip().lower() in {"1", "true", "yes", "on"}


PHASE_1_FINAL_THRESHOLDS = {
    "cracked_c5": 0.04741,
    "cracked_c9": 0.08569,
    "isoprene": 0.0220,
    "piperylene": 0.06149,
    "dcpd": 0.04224,
    "sis": 0.01364,
    "c5_resin": 0.11473,
    "blowing_agent": 0.04323,
    "styrene": 0.05247,
    "trimethylbenzene": 0.24684,
}


class TestPhase1MapeRegression(unittest.TestCase):
    """Phase 1 Final MAPE must not degrade beyond the approved 10% margin."""

    @unittest.skipUnless(
        _slow_db_tests_enabled(),
        "slow DB-dependent MAPE regression; set RUN_SLOW_DB_TESTS=1 for pre-deploy validation",
    )
    def test_phase1_final_mape_thresholds(self) -> None:
        runner = BacktestRunner()
        failures: list[str] = []

        for product_id, threshold in PHASE_1_FINAL_THRESHOLDS.items():
            with self.subTest(product_id=product_id):
                summary = runner.run_backtest_series(
                    product_id=product_id,
                    start_date=START,
                    end_date=END,
                    horizon_days=HORIZON,
                    step_days=STEP_DAYS,
                    policy_mode="adaptive",
                )
                mape = summary.get("mape") if isinstance(summary, dict) else None
                if mape is None:
                    failures.append(f"{product_id}: MAPE is null, threshold {threshold:.4f}")
                    continue
                if mape > threshold:
                    failures.append(f"{product_id}: MAPE {mape:.4f} > threshold {threshold:.4f}")

        self.assertFalse(failures, "Phase 1 MAPE regression detected:\n  " + "\n  ".join(failures))

    @unittest.skip(
        "raffinate_c5 skipped: Phase 1 Final MAPE is null because actuals end "
        "2026-02-28; possible upstream data loading issue, see Phase 1.1 notes"
    )
    def test_raffinate_c5_mape_skipped_until_actuals_resume(self) -> None:
        pass


if __name__ == "__main__":
    unittest.main()