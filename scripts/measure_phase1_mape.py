"""Phase 1.1 MAPE measurement.

Runs the backtest replay against the current forecasting pipeline so we can
compare per-product 7-day MAPE before and after the get_price_series() window
fix. Output is a JSON line per product written to stdout plus a JSON dump.

Usage:
    python3 scripts/measure_phase1_mape.py --label pre_fix --out /tmp/pre.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta

# Ensure repo root on sys.path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PRODUCTS = [
    "cracked_c5",
    "cracked_c9",
    "isoprene",
    "piperylene",
    "dcpd",
    "sis",
    "c5_resin",
    "blowing_agent",
    "styrene",
    "trimethylbenzene",
    "raffinate_c5",
]

# Backtest schedule: weekly steps over the last ~3 months of data we know exists.
START = "2026-01-15"
END = "2026-04-15"
STEP_DAYS = 14
HORIZON = 7


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="e.g. pre_fix or post_fix")
    ap.add_argument("--out", required=True, help="path to write JSON results")
    ap.add_argument("--products", nargs="*", default=PRODUCTS)
    args = ap.parse_args()

    from backend.src.validation.backtest_runner import BacktestRunner

    runner = BacktestRunner()
    results = {}
    for pid in args.products:
        t0 = time.time()
        try:
            summary = runner.run_backtest_series(
                product_id=pid,
                start_date=START,
                end_date=END,
                horizon_days=HORIZON,
                step_days=STEP_DAYS,
                policy_mode="adaptive",
            )
        except Exception as exc:  # noqa: BLE001
            summary = {"error": str(exc)}
        dt = round(time.time() - t0, 1)
        mape = summary.get("mape") if isinstance(summary, dict) else None
        n = summary.get("total_backtests") if isinstance(summary, dict) else 0
        results[pid] = {
            "mape": mape,
            "n_backtests": n,
            "elapsed_s": dt,
            "error": summary.get("error") if isinstance(summary, dict) else None,
        }
        print(
            json.dumps(
                {"label": args.label, "product": pid, **results[pid]},
                ensure_ascii=False,
            ),
            flush=True,
        )

    payload = {
        "label": args.label,
        "start": START,
        "end": END,
        "step_days": STEP_DAYS,
        "horizon_days": HORIZON,
        "results": results,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
