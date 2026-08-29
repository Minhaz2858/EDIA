"""Walk-forward forecast quality report for champion/challenger model selection.

Runs product-level backtests across model modes and writes JSON metrics that can
be used to decide which forecast model should be promoted for each product.

Usage:
    PYTHONPATH=. python scripts/measure_forecast_quality.py --out /tmp/forecast_quality.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PRODUCTS = [
    "crude_oil",
    "naphtha",
    "cracked_c5",
    "mixed_c5",
    "cracked_c9",
    "mixed_c9",
    "isoprene",
    "piperylene",
    "dcpd",
    "raffinate_c5",
    "sis",
    "sis_rubber",
    "c5_resin",
    "blowing_agent",
    "styrene",
    "trimethylbenzene",
]

DEFAULT_MODEL_MODES = ["naive", "arima_residual_daily", "champion", "champion_event_adjusted", "champion_event_routed"]
DEFAULT_HORIZONS = [1, 3, 7, 14, 30]
DEFAULT_START = "2026-01-15"
DEFAULT_END = "2026-04-15"
DEFAULT_STEP_DAYS = 14


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="Path to write JSON report")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--step-days", type=int, default=DEFAULT_STEP_DAYS)
    parser.add_argument("--products", nargs="*", default=PRODUCTS)
    parser.add_argument("--horizons", nargs="*", type=int, default=DEFAULT_HORIZONS)
    parser.add_argument("--model-modes", nargs="*", default=DEFAULT_MODEL_MODES)
    parser.add_argument("--spike-threshold-pct", type=float, default=0.03)
    args = parser.parse_args()

    from backend.src.validation.backtest_runner import BacktestRunner

    runner = BacktestRunner()
    report: dict[str, object] = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "start": args.start,
        "end": args.end,
        "step_days": args.step_days,
        "horizons": args.horizons,
        "model_modes": args.model_modes,
        "spike_threshold_pct": args.spike_threshold_pct,
        "products": {},
    }
    products_payload = report["products"]
    assert isinstance(products_payload, dict)

    for product_id in args.products:
        product_start = time.time()
        product_result: dict[str, object] = {"horizons": {}}
        horizon_payload = product_result["horizons"]
        assert isinstance(horizon_payload, dict)

        for horizon_days in args.horizons:
            try:
                comparison = runner.compare_model_modes(
                    product_id=product_id,
                    start_date=args.start,
                    end_date=args.end,
                    horizon_days=horizon_days,
                    step_days=args.step_days,
                    model_modes=args.model_modes,
                    spike_threshold_pct=args.spike_threshold_pct,
                )
            except Exception as exc:  # noqa: BLE001
                comparison = {"error": str(exc)}
            horizon_payload[str(horizon_days)] = comparison

        product_result["elapsed_s"] = round(time.time() - product_start, 1)
        products_payload[product_id] = product_result
        champions = {
            horizon: payload.get("champion_model")
            for horizon, payload in horizon_payload.items()
            if isinstance(payload, dict)
        }
        print(
            json.dumps(
                {
                    "product": product_id,
                    "champions": champions,
                    "elapsed_s": product_result["elapsed_s"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
