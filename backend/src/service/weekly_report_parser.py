"""
Weekly Report Parser — Section 5 of EDIA Build Spec

Parses a C5/C9 weekly market report (.md) into per-product × per-week
chunks with rich structured metadata for ChromaDB ingestion.

Each chunk covers:
  - narrative (Market Update)
  - price data text
  - forecast text
Metadata includes:
  - product (canonical id)
  - period / week_id
  - doc_type = "weekly_report"
  - forecast_direction: up / flat / down / biased_upward / stable_to_up / stable_to_down
  - action_bias: accelerate_shipments / flexible_pricing / biased_upward /
                 production_planning / watchful_hold / none
  - crude_trend / naphtha_trend: up / down / flat
  - market_phase: post_holiday_restart / pre_holiday / holiday / normal
  - net_macro_bias: bullish / bearish / neutral
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Product normalisation
# ---------------------------------------------------------------------------

_PRODUCT_NAME_MAP: dict[str, str] = {
    "cracked c5": "cracked_c5",
    "cracked c9": "cracked_c9",
    "isoprene": "isoprene",
    "sis": "sis",
    "styrene-isoprene-styrene": "sis",
    "butadiene": "butadiene",
    "styrene": "styrene",
    "sbs": "sbs",
    "styrene-butadiene-styrene": "sbs",
    "sebs": "sebs",
    "piperylene": "piperylene",
    "1,3-pentadiene": "piperylene",
    "hardener": "mthpa_hardener",
    "mthpa": "mthpa_hardener",
    "methyltetrahydrophthalic anhydride": "mthpa_hardener",
    "road marking": "road_marking_resin",
    "road marking c5 resin": "road_marking_resin",
    "adhesive c5 resin": "adhesive_resin",
    "adhesive resin": "adhesive_resin",
    "dicyclopentadiene": "dcpd",
    "dcpd": "dcpd",
    "n-pentane": "pentane_blowing_agent",
    "isopentane": "pentane_blowing_agent",
    "cyclopentane": "pentane_blowing_agent",
    "pentane blowing agent": "pentane_blowing_agent",
    "pentane": "pentane_blowing_agent",
    "raffinate c5": "raffinate_c5",
    "light hydrocarbons": "raffinate_c5",
}

# Ecisco's core product ids (used for action_bias assignment)
_ECISCO_PRODUCTS = {
    "isoprene",
    "piperylene",
    "dcpd",
    "cracked_c5",
    "cracked_c9",
    "sis",
    "mthpa_hardener",
    "road_marking_resin",
    "adhesive_resin",
    "raffinate_c5",
    "pentane_blowing_agent",
}


def _normalise_product(raw: str) -> str:
    key = raw.lower().strip()
    # Direct match
    if key in _PRODUCT_NAME_MAP:
        return _PRODUCT_NAME_MAP[key]
    # Partial match
    for k, v in _PRODUCT_NAME_MAP.items():
        if k in key:
            return v
    return key.replace(" ", "_").replace("/", "_")


# ---------------------------------------------------------------------------
# Forecast direction
# ---------------------------------------------------------------------------

_UP_PATTERNS = re.compile(
    r"biased upward|ris(e|ing|en)|increas|upward|firm|bullish|raise|optimist",
    re.IGNORECASE,
)
_DOWN_PATTERNS = re.compile(
    r"soften|declin|decreas|fall|drop|bearish|weak|downward|pressure",
    re.IGNORECASE,
)
_STABLE_UP_PATTERNS = re.compile(
    r"stable.*bias.*(up|ris)|bias.*(up|ris).*stable|stable.*(optimis|positive)",
    re.IGNORECASE,
)
_STABLE_DOWN_PATTERNS = re.compile(
    r"stable.*pressure|narrow.*oscil|oscil.*narrow",
    re.IGNORECASE,
)


def _extract_forecast_direction(forecast_text: str) -> str:
    if not forecast_text:
        return "flat"
    if _STABLE_UP_PATTERNS.search(forecast_text):
        return "stable_to_up"
    if _STABLE_DOWN_PATTERNS.search(forecast_text):
        return "stable_to_down"
    if _UP_PATTERNS.search(forecast_text):
        return "up"
    if _DOWN_PATTERNS.search(forecast_text):
        return "down"
    return "flat"


# ---------------------------------------------------------------------------
# Action bias (from Summary section)
# ---------------------------------------------------------------------------

_ACCELERATE_PAT = re.compile(
    r"accelerate.{0,30}(shipment|resin|post-holiday)|avoid inventory build",
    re.IGNORECASE,
)
_FLEXIBLE_PAT = re.compile(r"flexible pricing", re.IGNORECASE)
_PLAN_PAT = re.compile(r"plan.{0,30}(production|shipment|scheduling)", re.IGNORECASE)
_BIASED_UP_PAT = re.compile(
    r"biased upward|recommend.{0,20}(raise|increas)", re.IGNORECASE
)
_MONITOR_PAT = re.compile(
    r"continue monitoring|continue to monitor|watchful", re.IGNORECASE
)


def _extract_action_bias(summary_text: str) -> str:
    if not summary_text:
        return "none"
    if _ACCELERATE_PAT.search(summary_text):
        return "accelerate_shipments"
    if _FLEXIBLE_PAT.search(summary_text):
        return "flexible_pricing"
    if _BIASED_UP_PAT.search(summary_text):
        return "biased_upward"
    if _PLAN_PAT.search(summary_text):
        return "production_planning"
    if _MONITOR_PAT.search(summary_text):
        return "watchful_hold"
    return "none"


# ---------------------------------------------------------------------------
# Macro signal extraction
# ---------------------------------------------------------------------------

_CRUDE_UP = re.compile(
    r"crude.{0,50}(\+[\d.]+%|ris|up|increas|high|broke.*above|continu.*rise)",
    re.IGNORECASE,
)
_CRUDE_DN = re.compile(
    r"crude.{0,50}(-[\d.]+%|fell|drop|declin|lower|down)", re.IGNORECASE
)
_NAPH_UP = re.compile(r"naphtha.{0,50}(\+[\d.]+%|ris|up|increas|high)", re.IGNORECASE)
_NAPH_DN = re.compile(
    r"naphtha.{0,50}(-[\d.]+%|fell|drop|declin|lower|down)", re.IGNORECASE
)

_PHASE_POST_HOLIDAY = re.compile(
    r"post.?(holiday|spring festival|chinese new year)", re.IGNORECASE
)
_PHASE_PRE_HOLIDAY = re.compile(
    r"pre.?(holiday|spring festival|chinese new year)", re.IGNORECASE
)
_PHASE_HOLIDAY = re.compile(
    r"during.{0,20}(spring festival|holiday|golden week)", re.IGNORECASE
)

_RISK_FLAGS = {
    "geopolitical_tension": re.compile(
        r"geopolit|iran|russia|sanction|tariff risk|conflict", re.IGNORECASE
    ),
    "tariff_risk": re.compile(r"tariff|trade war|import duties|ustr", re.IGNORECASE),
    "weather_disruption": re.compile(
        r"cold weather|hurricane|typhoon|storm|weather disrupt", re.IGNORECASE
    ),
    "supply_shock": re.compile(
        r"supply.{0,30}(shock|disrupt|outage|force majeure)", re.IGNORECASE
    ),
    "demand_shock": re.compile(r"demand.{0,30}(shock|collapse|disrupt)", re.IGNORECASE),
}


def _extract_macro_signal(macro_text: str) -> dict:
    crude_trend = (
        "up"
        if _CRUDE_UP.search(macro_text)
        else ("down" if _CRUDE_DN.search(macro_text) else "flat")
    )
    naphtha_trend = (
        "up"
        if _NAPH_UP.search(macro_text)
        else ("down" if _NAPH_DN.search(macro_text) else "flat")
    )

    if _PHASE_POST_HOLIDAY.search(macro_text):
        market_phase = "post_holiday_restart"
    elif _PHASE_PRE_HOLIDAY.search(macro_text):
        market_phase = "pre_holiday"
    elif _PHASE_HOLIDAY.search(macro_text):
        market_phase = "holiday"
    else:
        market_phase = "normal"

    risk_flags = [flag for flag, pat in _RISK_FLAGS.items() if pat.search(macro_text)]

    if crude_trend == "up" and naphtha_trend == "up":
        net_macro_bias = "bullish"
    elif crude_trend == "down" and naphtha_trend == "down":
        net_macro_bias = "bearish"
    else:
        net_macro_bias = "neutral"

    return {
        "crude_trend": crude_trend,
        "naphtha_trend": naphtha_trend,
        "market_phase": market_phase,
        "risk_flags": ",".join(risk_flags) if risk_flags else "",
        "net_macro_bias": net_macro_bias,
    }


# ---------------------------------------------------------------------------
# Numeric price extraction
# ---------------------------------------------------------------------------


def _extract_crude_pct(text: str) -> Optional[float]:
    m = re.search(r"crude[^.]*?\+\s*([\d.]+)\s*%", text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r"crude[^.]*?-\s*([\d.]+)\s*%", text, re.IGNORECASE)
    if m:
        return -float(m.group(1))
    return None


def _extract_naphtha_pct(text: str) -> Optional[float]:
    m = re.search(r"naphtha[^.]*?\+\s*([\d.]+)\s*%", text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r"naphtha[^.]*?-\s*([\d.]+)\s*%", text, re.IGNORECASE)
    if m:
        return -float(m.group(1))
    return None


# ---------------------------------------------------------------------------
# Week ID from filename
# ---------------------------------------------------------------------------

_WEEK_ID_RE = re.compile(r"week(\d+)?_?([a-z]+\d+)_([a-z]+\d+)?", re.IGNORECASE)

_MONTH_MAP = {
    "jan": "01",
    "feb": "02",
    "mar": "03",
    "apr": "04",
    "may": "05",
    "jun": "06",
    "jul": "07",
    "aug": "08",
    "sep": "09",
    "oct": "10",
    "nov": "11",
    "dec": "12",
}


def _derive_week_id(path: Path) -> str:
    """Derive a sortable week_id like '2026-w06' from filename."""
    stem = path.stem.lower()
    # Pattern: week6_feb10_feb14 or week1_jan6_jan12
    numbered = re.match(r"week(\d+)_([a-z]+)(\d+)_([a-z]+)(\d+)", stem)
    if numbered:
        week_num = numbered.group(1).zfill(2)
        month_abbr = numbered.group(2)
        year = path.parent.name  # e.g. "2026"
        return f"{year}-w{week_num}"

    # Pattern: week_nov4_nov10 (no week number)
    named = re.match(r"week_([a-z]+)(\d+)_([a-z]+)(\d+)", stem)
    if named:
        month_abbr = named.group(1)
        day = int(named.group(2))
        year = path.parent.name
        month_num = _MONTH_MAP.get(month_abbr[:3], "01")
        # Approximate ISO week
        import datetime

        try:
            dt = datetime.date(int(year), int(month_num), day)
            iso_week = dt.isocalendar()[1]
            return f"{year}-w{str(iso_week).zfill(2)}"
        except Exception:
            return f"{year}-w{month_num}{str(day).zfill(2)}"

    return path.stem


def _derive_period_label(path: Path) -> str:
    """Human-readable period like '2026 Feb 10–14'."""
    stem = path.stem
    # Replace underscores with spaces, strip 'week' prefix
    label = re.sub(r"^week\d*_?", "", stem, flags=re.IGNORECASE)
    label = label.replace("_", "–").replace("-", " ")
    year = path.parent.name
    return f"{year} {label.title()}"


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------


@dataclass
class ReportChunk:
    text: str
    metadata: dict


def parse_weekly_report(path: Path) -> list[ReportChunk]:
    """
    Parse a weekly report .md file into a list of ReportChunks.
    Returns per-product chunks + 1 macro chunk + 1 summary chunk.
    """
    content = path.read_text(encoding="utf-8")
    week_id = _derive_week_id(path)
    period_label = _derive_period_label(path)
    source = path.name

    chunks: list[ReportChunk] = []

    # ------------------------------------------------------------------
    # 1. Extract macro signal block
    # ------------------------------------------------------------------
    macro_text = ""
    macro_match = re.search(
        r"> \*\*Key Macro Driver:\*\*(.*?)(?:\n---|\n## |\Z)", content, re.DOTALL
    )
    if macro_match:
        macro_text = macro_match.group(1).strip()

    macro_signal = _extract_macro_signal(macro_text)
    crude_pct = _extract_crude_pct(macro_text)
    naphtha_pct = _extract_naphtha_pct(macro_text)

    base_meta = {
        "source": source,
        "week_id": week_id,
        "period": period_label,
        "doc_type": "weekly_report",
        "crude_trend": macro_signal["crude_trend"],
        "naphtha_trend": macro_signal["naphtha_trend"],
        "market_phase": macro_signal["market_phase"],
        "net_macro_bias": macro_signal["net_macro_bias"],
        "risk_flags": macro_signal["risk_flags"],
        "crude_change_pct": str(crude_pct) if crude_pct is not None else "",
        "naphtha_change_pct": str(naphtha_pct) if naphtha_pct is not None else "",
    }

    # Add macro signal as its own chunk (for Perception Agent queries)
    if macro_text:
        macro_chunk_text = (
            f"MACRO SIGNAL — {period_label} ({week_id})\n"
            f"Crude trend: {macro_signal['crude_trend']}, "
            f"Naphtha trend: {macro_signal['naphtha_trend']}, "
            f"Market phase: {macro_signal['market_phase']}, "
            f"Net bias: {macro_signal['net_macro_bias']}.\n"
            f"{macro_text}"
        )
        chunks.append(
            ReportChunk(
                text=macro_chunk_text,
                metadata={
                    **base_meta,
                    "product": "all",
                    "doc_subtype": "macro_signal",
                    "action_bias": "none",
                    "forecast_direction": "flat",
                },
            )
        )

    # ------------------------------------------------------------------
    # 2. Extract Summary & action biases per product
    # ------------------------------------------------------------------
    summary_text = ""
    summary_match = re.search(
        r"## Summary.*?Recommendations\s*\n(.*?)(?:\n---|\Z)",
        content,
        re.DOTALL | re.IGNORECASE,
    )
    if summary_match:
        summary_text = summary_match.group(1).strip()

    # Split summary into per-product paragraphs
    summary_paragraphs: dict[str, str] = {}
    for para in re.split(r"\n\s*\n|\*\*\(\d+\)\*\*", summary_text):
        para = para.strip()
        if not para:
            continue
        for k, v in _PRODUCT_NAME_MAP.items():
            if k.lower() in para.lower() and v in _ECISCO_PRODUCTS:
                if v not in summary_paragraphs:
                    summary_paragraphs[v] = para

    # Also add summary as a combined chunk
    if summary_text:
        chunks.append(
            ReportChunk(
                text=f"WEEKLY SUMMARY & RECOMMENDATIONS — {period_label}\n{summary_text}",
                metadata={
                    **base_meta,
                    "product": "all",
                    "doc_subtype": "summary",
                    "action_bias": "none",
                    "forecast_direction": "flat",
                },
            )
        )

    # ------------------------------------------------------------------
    # 3. Split into product sections
    # ------------------------------------------------------------------
    # Sections start with "## N. Product Name" or "## Product Name"
    section_pattern = re.compile(r"^## \d*\.?\s*(.+)", re.MULTILINE)
    section_starts = [
        (m.start(), m.group(1).strip()) for m in section_pattern.finditer(content)
    ]

    for i, (start_pos, section_title) in enumerate(section_starts):
        # Skip the Summary section (already handled)
        if re.search(
            r"summary|recommendation|agenda|meeting|price summary|product price",
            section_title,
            re.IGNORECASE,
        ):
            continue

        end_pos = (
            section_starts[i + 1][0] if i + 1 < len(section_starts) else len(content)
        )
        section_text = content[start_pos:end_pos].strip()

        product_id = _normalise_product(section_title)

        # Extract per-section forecast blockquote
        forecast_match = re.search(
            r"> \*\*Forecast:\*\*(.+?)(?:\n---|\n## |\Z)", section_text, re.DOTALL
        )
        forecast_text = forecast_match.group(1).strip() if forecast_match else ""

        forecast_direction = _extract_forecast_direction(forecast_text)

        # Action bias: look in summary first, then forecast text
        action_bias = summary_paragraphs.get(product_id, "")
        action_bias = (
            _extract_action_bias(action_bias)
            if action_bias
            else _extract_action_bias(forecast_text)
        )

        # Build chunk text: section title + narrative + forecast
        chunk_text = (
            f"PRODUCT: {section_title} | WEEK: {period_label} ({week_id})\n"
            f"Macro: crude {macro_signal['crude_trend']}, naphtha {macro_signal['naphtha_trend']}, "
            f"phase {macro_signal['market_phase']}\n\n"
            f"{section_text}"
        )

        # Truncate to ~2000 chars to keep embeddings focused
        if len(chunk_text) > 2200:
            chunk_text = chunk_text[:2200] + "…"

        chunks.append(
            ReportChunk(
                text=chunk_text,
                metadata={
                    **base_meta,
                    "product": product_id,
                    "doc_subtype": "product_section",
                    "forecast_direction": forecast_direction,
                    "action_bias": action_bias,
                },
            )
        )

    return chunks


def parse_all_weekly_reports(base_dir: Path) -> list[ReportChunk]:
    """Parse all .md files under the configured weekly report directories."""
    explicit_dir = str(os.getenv("WEEKLY_REPORT_DIRECTORY", "") or "").strip()
    candidate_dirs = []
    if explicit_dir:
        candidate_dirs.append(Path(explicit_dir))
    candidate_dirs.extend(
        [
            base_dir / "data" / "weekly_report",
            base_dir / "data" / "weekly_reports",
            base_dir / "weekly_report",
            base_dir / "weekly_reports",
            base_dir / "docs" / "weekly_report",
            base_dir / "docs" / "weekly_reports",
        ]
    )

    weekly_dir = next((path for path in candidate_dirs if path.exists() and path.is_dir()), None)
    if weekly_dir is None:
        print(
            "Weekly report directory not found. Checked: "
            + ", ".join(str(path) for path in candidate_dirs)
        )
        return []

    paths = sorted(weekly_dir.rglob("*.md"))
    if not paths:
        print(f"Weekly report directory is empty: {weekly_dir}")
        return []

    all_chunks: list[ReportChunk] = []
    for path in paths:
        try:
            chunks = parse_weekly_report(path)
            all_chunks.extend(chunks)
            print(f"  Parsed {path.name}: {len(chunks)} chunks")
        except Exception as e:
            print(f"  WARNING: Failed to parse {path.name}: {e}")

    return all_chunks
