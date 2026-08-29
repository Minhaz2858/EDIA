"""Phase 2.2: Per-product ARIMA order selection via AIC grid search + backtest validation.

Usage (from repo root):
    /opt/anaconda3/bin/python3 backend/scripts/refit_arima_orders.py

Stop conditions:
  - Any product exceeds MAX_SECONDS_PER_PRODUCT during AIC search → no JSON written; halt.
  - count(NAIVE_EQUIVALENT) >= 2 → write report; halt before engine changes; show user.

Output:
  - Console: markdown table with adoption decisions.
  - backend/data/arima_orders.json  (only if no timeouts; only ADOPTED orders).
"""
from __future__ import annotations

import json
import os
import sys
import time
import warnings
from datetime import date, datetime, timedelta
from itertools import product as itertools_product
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ── repo root on sys.path ──────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.src.service.forecasting_engine import (  # noqa: E402
    _BELOW_NAIVE_PRODUCTS,
    _PRODUCT_MAP,
    get_price_series,
)

# ── constants ──────────────────────────────────────────────────────────────
START = "2026-01-15"
END = "2026-04-15"
STEP_DAYS = 14
HORIZON = 7

PRODUCTS: List[str] = [
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
    "mixed_c9",
]

# Phase 1 Final MAPE (from MAPE_BASELINE.md Phase 1.4 table; None = no measurement)
PHASE1_FINAL_MAPE: Dict[str, Optional[float]] = {
    "cracked_c5":     0.0431,
    "cracked_c9":     0.0779,
    "isoprene":       0.0200,
    "piperylene":     0.0559,
    "dcpd":           0.0384,
    "sis":            0.0124,
    "c5_resin":       0.1043,
    "blowing_agent":  0.0393,
    "styrene":        0.0477,
    "trimethylbenzene": 0.2244,
    "raffinate_c5":   None,     # no backtests; actuals ended 2026-02-28
    "mixed_c9":       0.0754,
}

# Naive MAPE from PHASE_1_WIN_VALIDATION.md (same backtest window; None = not computed)
NAIVE_MAPE: Dict[str, Optional[float]] = {
    "cracked_c5":     0.0370,
    "cracked_c9":     0.0410,
    "isoprene":       0.0417,
    "piperylene":     0.0672,
    "dcpd":           0.0351,
    "sis":            None,     # sparse; n<3 reliable steps
    "c5_resin":       0.1055,
    "blowing_agent":  0.0407,
    "styrene":        0.0390,
    "trimethylbenzene": None,   # sparse; n<3 reliable steps
    "raffinate_c5":   None,
    "mixed_c9":       0.0410,   # same series as cracked_c9
}

DEFAULT_ORDER: Tuple[int, int, int] = (2, 1, 2)
ADOPT_THRESHOLD = 0.95          # candidate_mape must be <= phase1_mape * 0.95
MAX_SECONDS_PER_PRODUCT = 300   # abort AIC search if exceeded
MIN_BACKTEST_STEPS = 3          # fewer valid steps → INSUFFICIENT_BACKTESTS

# AIC grid: p∈{0,1,2,3} × d∈{0,1} × q∈{0,1,2,3} = 32 combinations
GRID: List[Tuple[int, int, int]] = [
    (p, d, q)
    for p in range(4)
    for d in range(2)
    for q in range(4)
]

OUTPUT_DIR = ROOT / "backend" / "data"
OUTPUT_PATH = OUTPUT_DIR / "arima_orders.json"

NAIVE_EQUIVALENT_ORDER = (0, 1, 0)  # random walk = naive forecast


# ── AIC grid search ────────────────────────────────────────────────────────

def _aic_grid_search(
    series: pd.Series, t_product_start: float
) -> Tuple[Tuple[int, int, int], float]:
    """Return (best_order, best_aic) from the 32-combo grid.

    Raises TimeoutError if elapsed > MAX_SECONDS_PER_PRODUCT.
    """
    from statsmodels.tsa.arima.model import ARIMA  # noqa: PLC0415

    best_order = DEFAULT_ORDER
    best_aic = float("inf")

    for order in GRID:
        if time.time() - t_product_start > MAX_SECONDS_PER_PRODUCT:
            raise TimeoutError(
                f"Exceeded {MAX_SECONDS_PER_PRODUCT}s during AIC grid search"
            )
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                res = ARIMA(series.values, order=order).fit(
                    method_kwargs={"warn_convergence": False}
                )
            if np.isfinite(res.aic) and res.aic < best_aic:
                best_aic = res.aic
                best_order = order
        except Exception:  # noqa: BLE001
            continue

    return best_order, best_aic


# ── Mini-backtest ──────────────────────────────────────────────────────────

def _mini_backtest(
    series_full: pd.Series,
    order: Tuple[int, int, int],
) -> Tuple[Optional[float], Optional[float], int]:
    """Replay Phase 1 backtest window with *order*.

    Uses series_full sliced to as_of_date for each step (simulates cutoff).
    Actuals are looked up from series_full (which extends past the window end).

    Returns:
        (candidate_mape, naive_mape, n_valid_steps)
    """
    from statsmodels.tsa.arima.model import ARIMA  # noqa: PLC0415

    errors_model: List[float] = []
    errors_naive: List[float] = []

    current = datetime.strptime(START, "%Y-%m-%d")
    end_dt = datetime.strptime(END, "%Y-%m-%d")

    while current <= end_dt:
        as_of_ts = pd.Timestamp(current.date())
        target_ts = as_of_ts + pd.Timedelta(days=HORIZON)

        # Series visible at as_of_date (simulates cutoff)
        series_as_of = series_full[series_full.index <= as_of_ts]
        if len(series_as_of) < 20:
            current += timedelta(days=STEP_DAYS)
            continue

        # Actual price: first available on or after target_ts
        future = series_full[series_full.index >= target_ts]
        if future.empty:
            current += timedelta(days=STEP_DAYS)
            continue
        actual = float(future.iloc[0])
        if actual == 0.0:
            current += timedelta(days=STEP_DAYS)
            continue

        # Naive forecast: last known price predicts price in HORIZON days
        naive_fc = float(series_as_of.iloc[-1])
        errors_naive.append(abs(naive_fc - actual) / actual)

        # Model forecast with candidate order
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                res = ARIMA(series_as_of.values, order=order).fit(
                    method_kwargs={"warn_convergence": False}
                )
            fc = res.forecast(steps=HORIZON)
            model_fc = float(np.asarray(fc)[-1])
            errors_model.append(abs(model_fc - actual) / actual)
        except Exception:  # noqa: BLE001
            # Conservative: treat failed fit as naive-level error for that step
            errors_model.append(abs(naive_fc - actual) / actual)

        current += timedelta(days=STEP_DAYS)

    n = min(len(errors_model), len(errors_naive))
    if n == 0:
        return None, None, 0

    candidate_mape = round(float(np.mean(errors_model[:n])), 4)
    naive_mape = round(float(np.mean(errors_naive[:n])), 4)
    return candidate_mape, naive_mape, n


# ── Adoption decision ──────────────────────────────────────────────────────

def _adoption_decision(
    product_id: str,
    selected_order: Tuple[int, int, int],
    candidate_mape: Optional[float],
    naive_mape_measured: Optional[float],
    n: int,
) -> Tuple[str, str]:
    """Return (decision_code, note)."""
    if n < MIN_BACKTEST_STEPS:
        return "INSUFFICIENT_BACKTESTS", f"only {n} valid backtest step(s)"

    if candidate_mape is None:
        return "NO_ACTUALS", "no actual prices in backtest window"

    if selected_order == NAIVE_EQUIVALENT_ORDER:
        return (
            "NAIVE_EQUIVALENT",
            "AIC selected (0,1,0) — random walk; data has no exploitable "
            "autocorrelation at daily resolution",
        )

    if selected_order == DEFAULT_ORDER:
        return "UNCHANGED", "AIC selected default (2,1,2)"

    p1_mape = PHASE1_FINAL_MAPE.get(product_id)
    if p1_mape is None:
        return "NO_ACTUALS", "Phase 1 MAPE was null; no adoption baseline"

    # Standard ≥5% relative improvement rule
    if candidate_mape > p1_mape * ADOPT_THRESHOLD:
        note = (
            f"candidate {candidate_mape:.4f} not ≥5% better than "
            f"Phase 1 {p1_mape:.4f} (threshold {p1_mape * ADOPT_THRESHOLD:.4f})"
        )
        return "REJECTED_MAPE", note

    # Skill-aware rule for below-naive products
    if product_id in _BELOW_NAIVE_PRODUCTS:
        p1_naive = NAIVE_MAPE.get(product_id)
        if p1_naive is not None and naive_mape_measured is not None:
            p1_skill = p1_mape - p1_naive          # positive = worse than naive
            cand_skill = candidate_mape - naive_mape_measured
            if cand_skill >= p1_skill:
                still_below = candidate_mape > naive_mape_measured
                note = (
                    f"skill_vs_naive did not improve: candidate "
                    f"{cand_skill:+.4f} vs Phase 1 {p1_skill:+.4f}"
                )
                if still_below:
                    note += "; still below naive baseline"
                return "REJECTED_NAIVE", note

    # All checks passed
    note = f"MAPE {p1_mape:.4f} → {candidate_mape:.4f} (≥5% improvement)"
    if product_id in _BELOW_NAIVE_PRODUCTS and naive_mape_measured is not None:
        p1_naive = NAIVE_MAPE.get(product_id)
        cand_skill = candidate_mape - naive_mape_measured
        p1_skill = (p1_mape - p1_naive) if p1_naive is not None else None
        note += f"; skill_vs_naive: {cand_skill:+.4f}"
        if candidate_mape > naive_mape_measured:
            note += " (still below naive baseline)"
        if p1_skill is not None:
            note += f" (was {p1_skill:+.4f})"
        note += f"; mixed_c9 flag: not API-reachable (404)" if product_id == "mixed_c9" else ""
    return "ADOPTED", note


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> int:
    print("\n=== Phase 2.2: ARIMA order selection ===")
    print(f"Grid: p∈{{0-3}}, d∈{{0-1}}, q∈{{0-3}} ({len(GRID)} combos per product)")
    print(f"Backtest window: {START} → {END}, step {STEP_DAYS}d, horizon {HORIZON}d")
    print(f"Adopt threshold: candidate_mape ≤ Phase1 MAPE × {ADOPT_THRESHOLD}\n")

    results: Dict[str, dict] = {}
    has_timeout = False
    naive_equivalent_count = 0

    for product_id in PRODUCTS:
        if product_id not in _PRODUCT_MAP:
            results[product_id] = {"decision": "SKIPPED", "note": "not in _PRODUCT_MAP"}
            continue

        pid, pt = _PRODUCT_MAP[product_id]
        t_start = time.time()
        print(f"[{product_id}] Fetching series ({pid}/{pt})...", end=" ", flush=True)

        series = get_price_series(pid, pt, days=500)
        if series.empty:
            results[product_id] = {"decision": "ERROR", "note": "empty series from DB"}
            print("EMPTY — skipping")
            continue

        print(f"{len(series)} pts | AIC grid search...", end=" ", flush=True)

        # ── Step 1: AIC grid search
        try:
            selected_order, selected_aic = _aic_grid_search(series, t_start)
        except TimeoutError as e:
            results[product_id] = {"decision": "TIMEOUT", "note": str(e)}
            print(f"TIMEOUT after {time.time() - t_start:.0f}s")
            has_timeout = True
            continue

        aic_elapsed = round(time.time() - t_start, 1)
        print(f"done ({aic_elapsed}s) → {selected_order} AIC={selected_aic:.1f}")

        # ── Step 2: Mini-backtest (candidate order only)
        print(f"[{product_id}]   Backtest with order {selected_order}...", end=" ", flush=True)
        candidate_mape, naive_mape_measured, n = _mini_backtest(series, selected_order)
        bt_elapsed = round(time.time() - t_start - aic_elapsed, 1)
        print(f"done ({bt_elapsed}s) | n={n} | candidate_mape={candidate_mape} | naive_mape={naive_mape_measured}")

        # ── Step 3: Adoption decision
        decision, note = _adoption_decision(
            product_id, selected_order, candidate_mape, naive_mape_measured, n
        )

        if decision == "NAIVE_EQUIVALENT":
            naive_equivalent_count += 1

        total_elapsed = round(time.time() - t_start, 1)
        results[product_id] = {
            "selected_order": list(selected_order),
            "selected_aic": round(selected_aic, 2) if np.isfinite(selected_aic) else None,
            "candidate_mape": candidate_mape,
            "naive_mape_measured": naive_mape_measured,
            "n_backtest_steps": n,
            "elapsed_s": total_elapsed,
            "decision": decision,
            "note": note,
        }

    # ── Report table ────────────────────────────────────────────────────────
    print("\n" + "=" * 120)
    print("## Phase 2.2 Results\n")
    _print_report(results)

    # ── Stop condition checks ───────────────────────────────────────────────
    if has_timeout:
        print("\n⛔ STOP: at least one product timed out (>5 min). JSON not written.")
        print("   Investigate the timed-out product(s) before proceeding to engine changes.")
        return 1

    if naive_equivalent_count >= 2:
        print(
            f"\n⚠️  STOP: {naive_equivalent_count} product(s) selected (0,1,0) (random walk)."
        )
        print(
            "   This means the data has no exploitable autocorrelation at daily resolution"
            " for multiple products."
        )
        print("   JSON written below for review. Halting before engine changes.")
        print("   Review findings and confirm before proceeding to Phase B (engine edits).")
        _write_json(results)
        return 2

    # ── Write JSON ──────────────────────────────────────────────────────────
    _write_json(results)
    return 0


def _print_report(results: Dict[str, dict]) -> None:
    """Print a markdown table of adoption decisions."""
    hdr = (
        f"| {'Product':<20} | {'P1 MAPE':>9} | {'P2.2 MAPE':>9} | "
        f"{'Naive MAPE':>10} | {'Old Order':>9} | {'New Order':>9} | "
        f"{'skill_vs_naive':>14} | {'Decision':<22} | Note"
    )
    sep = "|" + "|".join(["-" * (w + 2) for w in [20, 9, 9, 10, 9, 9, 14, 22, 50]]) + "|"
    print(hdr)
    print(sep)

    for pid in PRODUCTS:
        r = results.get(pid, {})
        p1 = PHASE1_FINAL_MAPE.get(pid)
        naive_p1 = NAIVE_MAPE.get(pid)
        cand = r.get("candidate_mape")
        naive_m = r.get("naive_mape_measured")
        sel = r.get("selected_order")
        decision = r.get("decision", "—")
        note = r.get("note", "")

        p1_str = f"{p1:.4f}" if p1 is not None else "null"
        cand_str = f"{cand:.4f}" if cand is not None else "—"
        naive_str = f"{naive_m:.4f}" if naive_m is not None else "—"
        old_ord = str(DEFAULT_ORDER)
        new_ord = str(tuple(sel)) if sel else "—"

        # skill_vs_naive for the candidate
        if cand is not None and naive_m is not None:
            svn = cand - naive_m
            svn_str = f"{svn:+.4f}"
        elif naive_p1 is not None and p1 is not None:
            svn_str = f"{p1 - naive_p1:+.4f} (P1)"
        else:
            svn_str = "n/a"

        # Mark mixed_c9 as not API-reachable
        api_note = " [not API-reachable]" if pid == "mixed_c9" else ""

        print(
            f"| {pid:<20} | {p1_str:>9} | {cand_str:>9} | {naive_str:>10} | "
            f"{old_ord:>9} | {new_ord:>9} | {svn_str:>14} | {decision:<22} | {note[:60]}{api_note}"
        )


def _write_json(results: Dict[str, dict]) -> None:
    """Write adopted orders to backend/data/arima_orders.json."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    adopted = {
        pid: r["selected_order"]
        for pid, r in results.items()
        if r.get("decision") == "ADOPTED"
    }

    payload = {
        "_meta": {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "backtest_window": f"{START} to {END}",
            "step_days": STEP_DAYS,
            "horizon_days": HORIZON,
            "grid": f"p∈{{0-3}}, d∈{{0-1}}, q∈{{0-3}}",
            "adopt_threshold": ADOPT_THRESHOLD,
            "n_adopted": len(adopted),
            "n_products": len(PRODUCTS),
            "decisions": {pid: r.get("decision") for pid, r in results.items()},
        },
        **adopted,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print(f"\n✓ JSON written → {OUTPUT_PATH}")
    print(f"  Adopted: {len(adopted)}/{len(PRODUCTS)} products")
    if adopted:
        for pid, order in adopted.items():
            print(f"    {pid}: {tuple(order)}")
    else:
        print("  No products adopted (all rejected, unchanged, or special-cased).")


if __name__ == "__main__":
    sys.exit(main())
