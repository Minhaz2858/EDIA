from __future__ import annotations

"""Macro Override Service — real-time shock injection layer (EDIA Build Instructions §16).

Reads / writes a `macro_override.json` file at the project root.
An active override adjusts the naphtha monthly forecast input by
`override.naphtha_shock_pct` (hard-capped at ±4% per spec).

Usage:
  # From any service
  override = get_active_override()   # dict | None
  if override:
      print(override_summary(override))

  # From API endpoint
  set_override(naphtha_shock_pct=2.5, event_type="crude_spike",
               description="Brent +$5 on geopolitical tensions", ttl_hours=24)

  clear_override()
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

# Path is 4 levels up from this file: service → src → backend → project-root
_OVERRIDE_PATH = Path(__file__).parent.parent.parent.parent / "macro_override.json"

# Hard cap from spec §16 — ±4% on naphtha shock
_NAPHTHA_SHOCK_CAP = 4.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_active_override() -> dict | None:
    """Return the active override dict, or None if absent / expired / inactive."""
    if not _OVERRIDE_PATH.exists():
        return None
    try:
        data = json.loads(_OVERRIDE_PATH.read_text(encoding="utf-8"))
        if not data.get("active", False):
            return None
        expires_at = datetime.fromisoformat(data["expires_at"])
        if datetime.utcnow() > expires_at:
            _deactivate()
            return None
        return data
    except Exception:
        return None


def set_override(
    naphtha_shock_pct: float,
    crude_shock_pct: float = 0.0,
    event_type: str = "manual",
    description: str = "",
    ttl_hours: int = 24,
    product_shocks: dict | None = None,
) -> dict:
    """Write a new macro override (replaces any existing one).

    Args:
        naphtha_shock_pct:  % change to apply to naphtha forecast  (e.g. +2.5 means +2.5%)
        crude_shock_pct:    % change in crude price (for logging / propagation)
        event_type:         "crude_spike" | "tariff_shock" | "force_majeure" | "manual"
        description:        Free-text description of the event
        ttl_hours:          How many hours until the override auto-expires (default 24)
        product_shocks:     Optional dict of {product_id: extra_pct_adj} for direct product overrides
    """
    # Enforce hard cap
    naphtha_shock_pct = max(
        -_NAPHTHA_SHOCK_CAP, min(_NAPHTHA_SHOCK_CAP, naphtha_shock_pct)
    )
    now = datetime.utcnow()
    # Confidence penalty: larger shock = larger penalty (up to 15%)
    confidence_penalty = min(0.15, abs(naphtha_shock_pct) * 0.025)

    payload = {
        "event_type": event_type,
        "description": description,
        "naphtha_shock_pct": round(naphtha_shock_pct, 2),
        "crude_shock_pct": round(crude_shock_pct, 2),
        "product_shocks": product_shocks or {},
        "confidence_penalty": round(confidence_penalty, 3),
        "active": True,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=ttl_hours)).isoformat(),
    }
    _OVERRIDE_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return payload


def clear_override() -> bool:
    """Manually deactivate the current override."""
    _deactivate()
    return True


def override_summary(override: dict) -> str:
    """One-line summary suitable for inclusion in LLM prompt context."""
    shock = override.get("naphtha_shock_pct", 0)
    crude = override.get("crude_shock_pct", 0)
    evt = override.get("event_type", "unknown").replace("_", " ").upper()
    desc = override.get("description", "")
    pen = override.get("confidence_penalty", 0)
    expires = override.get("expires_at", "")[:16]
    return (
        f"[MACRO OVERRIDE ACTIVE — {evt}] {desc} | "
        f"Naphtha shock: {shock:+.1f}% | Crude shock: {crude:+.1f}% | "
        f"Confidence penalty: -{pen*100:.0f}% | Expires: {expires} UTC"
    )


def apply_macro_shock_to_naphtha_mom(arima_mom: float, override: dict) -> float:
    """Add the override naphtha shock (capped already stored) to the ARIMA forecast.

    arima_mom:  ARIMA naphtha MoM % (e.g. 1.2 means +1.2%)
    Returns adjusted MoM (still capped globally at ±8% per spec §8.2 Layer 2).
    """
    adjusted = arima_mom + override.get("naphtha_shock_pct", 0.0)
    # Hard global cap from spec §8.2 Layer 2
    return max(-8.0, min(8.0, adjusted))


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _deactivate():
    if _OVERRIDE_PATH.exists():
        try:
            data = json.loads(_OVERRIDE_PATH.read_text(encoding="utf-8"))
            data["active"] = False
            _OVERRIDE_PATH.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass
