from __future__ import annotations

from typing import Any, Dict, List


COMPETITOR_BENCHMARKS: Dict[str, List[Dict[str, Any]]] = {
    "c5_resin": [
        {
            "name": "金海晨光",
            "small_lot": 12000,
            "key_account": 11500,
            "position": "premium",
        },
        {
            "name": "鲁华",
            "quoted": 11200,
            "key_account": 10800,
            "position": "mid",
        },
        {
            "name": "新疆天利",
            "range_low": 10500,
            "range_high": 11000,
            "position": "value",
        },
        {
            "name": "浙江德荣",
            "range_low": 10500,
            "range_high": 11000,
            "position": "new_entrant",
        },
    ],
    "road_marking_resin": [
        {
            "name": "新疆天利",
            "range_low": 10500,
            "range_high": 11000,
            "position": "reference",
        },
        {"name": "鲁华", "quoted": 11000, "position": "reference"},
    ],
    "hydrogenated_c9": [
        {"name": "恒河", "price": 5930, "position": "major"},
    ],
    "isoprene": [
        {"name": "中石化上海", "listed": 11100, "position": "anchor"},
        {
            "name": "中石化武汉",
            "price": 10600,
            "position": "discount",
            "vs_east_china": -500,
        },
    ],
    "piperylene": [
        {"name": "中石化上海", "listed": 5700, "position": "anchor"},
        {"name": "武汉", "price": 5100, "position": "discount"},
    ],
}


_COMPETITOR_QUERY_MARKERS = [
    "金海",
    "晨光",
    "鲁华",
    "天利",
    "德荣",
    "恒河",
    "竞品",
    "竞争对手",
    "价差",
    "定位",
    "spread",
    "competitor",
    "benchmark",
]


def looks_like_competitor_query(user_query: str) -> bool:
    q = (user_query or "").lower()
    return any(m in q for m in _COMPETITOR_QUERY_MARKERS)


def _reference_price(row: Dict[str, Any]) -> float:
    if row.get("key_account") is not None:
        return float(row["key_account"])
    if row.get("quoted") is not None:
        return float(row["quoted"])
    if row.get("listed") is not None:
        return float(row["listed"])
    if row.get("price") is not None:
        return float(row["price"])
    if row.get("range_low") is not None and row.get("range_high") is not None:
        return (float(row["range_low"]) + float(row["range_high"])) / 2.0
    if row.get("range_low") is not None:
        return float(row["range_low"])
    return 0.0


def get_competitor_comparison(product_id: str, ecisco_price: float | None) -> str:
    comps = COMPETITOR_BENCHMARKS.get(product_id)
    if not comps:
        return ""

    base_price = float(ecisco_price or 0.0)
    lines = [
        "COMPETITOR BENCHMARK (STRUCTURED):",
        "| 竞争对手 | 参考价(元/吨) | 伊斯科价差 | 定位 |",
        "|---------|------------:|----------:|------|",
    ]

    for c in comps:
        ref = _reference_price(c)
        spread = base_price - ref if base_price > 0 else 0.0
        lines.append(
            f"| {c.get('name', '-') } | {ref:,.0f} | {spread:+,.0f} | {c.get('position', '-')} |"
        )

    return "\n".join(lines)
