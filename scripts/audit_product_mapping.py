"""Phase 1.2 audit — product mapping vs market_prices inventory.

Read-only health check. Produces:
  1. Diff of `_PRODUCT_MAP` (forecast engine) vs `REAL_DATA_MAP` (chart).
  2. Per-product `(product_id, price_type)` row counts in market_prices.
  3. Audit table: mapped series rows vs richest available alternative,
     flagging products where a much richer series sits unused.

Useful as an ongoing health check whenever a new product is onboarded or
the loader behavior changes. Does not modify any data.

Usage:
    python3 scripts/audit_product_mapping.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main() -> int:
    from sqlalchemy import text

    from backend.src.db.sqlite_db import db
    from backend.src.service.forecasting_engine import _PRODUCT_MAP
    from backend.src.service.market_data_service import REAL_DATA_MAP

    keys_real = set(REAL_DATA_MAP.keys())
    keys_model = set(_PRODUCT_MAP.keys())

    print("== Map key diff ==")
    print(f"In REAL_DATA_MAP only: {sorted(keys_real - keys_model)}")
    print(f"In _PRODUCT_MAP only:  {sorted(keys_model - keys_real)}")
    print(f"In both: {len(keys_real & keys_model)} keys")
    print()

    print("== Per-key value comparison ==")
    for k in sorted(keys_real | keys_model):
        rv = REAL_DATA_MAP.get(k)
        mv = _PRODUCT_MAP.get(k)
        flag = "OK   " if rv == mv else "DIFF "
        print(f"{flag} {k:22s}  chart={rv}   model={mv}")

    print()
    print("== market_prices inventory ==")
    with db.engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT product_id, price_type, COUNT(*) n, MIN(date) mn, MAX(date) mx "
                "FROM market_prices GROUP BY product_id, price_type "
                "ORDER BY product_id, n DESC"
            )
        ).fetchall()
    inventory: dict[str, list[tuple]] = {}
    for r in rows:
        pid, pt, n, mn, mx = r
        inventory.setdefault(pid, []).append((pt, n, mn, mx))

    print()
    print("== Audit: model-mapped series vs richest available ==")
    header = (
        f"{'node_id':22s} {'mapped pt':32s} {'mapped n':>9s} "
        f"{'best pt':32s} {'best n':>9s} {'gap':>9s}  notes"
    )
    print(header)
    for k in sorted(keys_model | keys_real):
        mv = _PRODUCT_MAP.get(k) or REAL_DATA_MAP.get(k)
        if not mv:
            continue
        pid, pt = mv
        series_for_pid = inventory.get(pid, [])
        if not series_for_pid:
            print(f"{k:22s} {pt:32s} {'NO DATA':>9s} -- product_id missing from market_prices")
            continue
        mapped_n = next((n for _pt, n, _, _ in series_for_pid if _pt == pt), 0)
        best_pt, best_n, _, _ = series_for_pid[0]
        gap = best_n - mapped_n
        if mapped_n == 0:
            note = "MAPPED SERIES ABSENT"
        elif gap > 0 and best_n >= 500:
            note = "RICHER UNUSED >=500"
        elif gap > 0:
            note = "richer unused"
        else:
            note = ""
        print(
            f"{k:22s} {pt:32s} {mapped_n:>9d} "
            f"{best_pt:32s} {best_n:>9d} {gap:>9d}  {note}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
