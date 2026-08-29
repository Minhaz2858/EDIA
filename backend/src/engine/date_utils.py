"""Date utility — Historical / Backtest date scope detection.

When a user query contains an explicit past date (e.g. "基于截至2026年2月26日"),
the pipeline must:
  1. Treat that date as the analysis cutoff — never "correct" it.
  2. Filter all market data / ERP / perception to that cutoff.
  3. Inject a backtest instruction into the system prompt.
"""

from __future__ import annotations

import re
from typing import Optional, Dict, Any


def detect_historical_date_scope(query: str) -> Optional[Dict[str, Any]]:
    """Parse a user query and return a date_scope dict if a historical cutoff
    date is detected, otherwise return None.

    Recognised patterns (Chinese & ISO):
      - 基于截至2026年2月26日
      - 截至2026-02-26
      - 假设今天是2026年2月26日
      - 2026年2月26日的市场数据
      - as of 2026-02-26
      - based on data up to February 26, 2026
    """
    patterns = [
        # "基于截至" / "截至"
        r"截至\s*(\d{4})\s*[年\-]\s*(\d{1,2})\s*[月\-]\s*(\d{1,2})",
        # "假设今天是"
        r"假设今天是\s*(\d{4})\s*[年\-]\s*(\d{1,2})\s*[月\-]\s*(\d{1,2})",
        # "YYYY年M月D日的数据/市场"
        r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*(?:的|前)",
        # ISO-ish "as of YYYY-MM-DD" or "up to YYYY-MM-DD"
        r"(?:as of|up to|based on data.*?)\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})",
    ]

    for pattern in patterns:
        m = re.search(pattern, query, re.IGNORECASE)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            cutoff = f"{y}-{mo:02d}-{d:02d}"
            return {
                "type": "historical_backtest",
                "cutoff_date": cutoff,
                "label": f"{y}年{mo}月{d}日",
            }

    return None
