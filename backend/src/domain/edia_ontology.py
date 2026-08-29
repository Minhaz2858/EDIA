"""Load EDIA domain ontology from YAML for routing and metric resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from backend.src.domain.product_resolver import (
    default_sinopec_listing_product_ids,
    extract_product_ids_in_text,
    rebuild_product_resolver,
    resolve_product_id,
)


_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_ONTOLOGY_PATH = _REPO_ROOT / "backend" / "config" / "edia_ontology.yaml"


@dataclass(frozen=True)
class MetricSpec:
    metric_id: str
    price_type: Optional[str] = None
    capability_id: Optional[str] = None
    default_products: List[str] = field(default_factory=list)
    change_days_default: int = 7
    skip_rag: bool = False
    skip_llm_finalizer: bool = False
    discovery: bool = False
    markers_zh: tuple[str, ...] = ()
    markers_en: tuple[str, ...] = ()


@dataclass
class OntologyBundle:
    products: Dict[str, dict]
    price_types: Dict[str, dict]
    metrics: Dict[str, MetricSpec]
    market_data_source_labels_zh: tuple[str, ...]
    market_data_source_labels_en: tuple[str, ...]


def _ontology_path() -> Path:
    import os

    configured = str(os.getenv("EDIA_ONTOLOGY_PATH", "") or "").strip()
    if configured:
        return Path(configured)
    return _DEFAULT_ONTOLOGY_PATH


@lru_cache(maxsize=1)
def load_ontology() -> OntologyBundle:
    path = _ontology_path()
    if not path.exists():
        return OntologyBundle(
            products={},
            price_types={},
            metrics={},
            market_data_source_labels_zh=(),
            market_data_source_labels_en=(),
        )

    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    metrics: Dict[str, MetricSpec] = {}
    for metric_id, spec in (raw.get("metrics") or {}).items():
        if not isinstance(spec, dict):
            continue
        metrics[str(metric_id)] = MetricSpec(
            metric_id=str(metric_id),
            price_type=spec.get("price_type"),
            capability_id=spec.get("capability_id"),
            default_products=list(spec.get("default_products") or []),
            change_days_default=int(spec.get("change_days_default") or 7),
            skip_rag=bool(spec.get("skip_rag")),
            skip_llm_finalizer=bool(spec.get("skip_llm_finalizer")),
            discovery=bool(spec.get("discovery")),
            markers_zh=tuple(spec.get("markers_zh") or []),
            markers_en=tuple(spec.get("markers_en") or []),
        )

    return OntologyBundle(
        products=dict(raw.get("products") or {}),
        price_types=dict(raw.get("price_types") or {}),
        metrics=metrics,
        market_data_source_labels_zh=tuple(raw.get("market_data_source_labels_zh") or []),
        market_data_source_labels_en=tuple(raw.get("market_data_source_labels_en") or []),
    )


def register_ontology_aliases() -> None:
    """Merge ontology product aliases into product_resolver."""
    bundle = load_ontology()
    if not bundle.products:
        return
    try:
        from backend.src.domain import product_resolver as pr

        for pid, spec in bundle.products.items():
            if not isinstance(spec, dict):
                continue
            pr._register(pid, pid)
            pr._register(str(spec.get("name_en") or ""), pid)
            pr._register(str(spec.get("name_cn") or ""), pid)
            for alias in spec.get("aliases") or []:
                pr._register(str(alias), pid)
    except Exception:
        pass


def resolve_products(query: str) -> List[str]:
    explicit = extract_product_ids_in_text(query)
    if explicit:
        return explicit
    bundle = load_ontology()
    q_norm = (query or "").strip().lower()
    if not q_norm:
        return []
    found: List[str] = []
    seen: set[str] = set()
    for pid, spec in bundle.products.items():
        if not isinstance(spec, dict):
            continue
        candidates = [pid, str(spec.get("name_en") or ""), str(spec.get("name_cn") or "")]
        candidates.extend(spec.get("aliases") or [])
        for alias in candidates:
            alias_s = str(alias or "").strip().lower()
            if alias_s and alias_s in q_norm and pid not in seen:
                seen.add(pid)
                found.append(pid)
                break
    return found


def resolve_metric(query: str) -> Optional[str]:
    q = (query or "").strip()
    if not q:
        return None
    ql = q.lower()
    bundle = load_ontology()

    best_id: Optional[str] = None
    best_score = 0
    for metric_id, spec in bundle.metrics.items():
        score = 0
        for marker in spec.markers_zh:
            if marker in q:
                score += len(marker)
        for marker in spec.markers_en:
            if marker in ql:
                score += len(marker)
        if score > best_score:
            best_score = score
            best_id = metric_id

    if best_score > 0:
        return best_id

    from backend.src.domain.product_resolver import looks_like_sinopec_listing_query

    if looks_like_sinopec_listing_query(q):
        return "sinopec_monthly_listing"
    return None


def resolve_price_type(query: str, metric_id: Optional[str] = None) -> Optional[str]:
    mid = metric_id or resolve_metric(query)
    if not mid:
        return None
    spec = load_ontology().metrics.get(mid)
    if spec and spec.price_type:
        return spec.price_type
    return None


def metric_spec(metric_id: str) -> Optional[MetricSpec]:
    return load_ontology().metrics.get(metric_id)


def default_products_for_metric(metric_id: str) -> List[str]:
    spec = metric_spec(metric_id)
    if not spec:
        return []
    if spec.default_products:
        return list(spec.default_products)
    if metric_id == "sinopec_monthly_listing":
        return default_sinopec_listing_product_ids()
    return []


def is_market_data_source_label(query: str) -> bool:
    bundle = load_ontology()
    q = str(query or "")
    ql = q.lower()
    return any(label in q for label in bundle.market_data_source_labels_zh) or any(
        label in ql for label in bundle.market_data_source_labels_en
    )


def should_skip_rag_for_metric(metric_id: Optional[str]) -> bool:
    if not metric_id:
        return False
    spec = metric_spec(metric_id)
    return bool(spec and spec.skip_rag)


def should_skip_llm_finalizer_for_metric(metric_id: Optional[str]) -> bool:
    if not metric_id:
        return False
    spec = metric_spec(metric_id)
    return bool(spec and spec.skip_llm_finalizer)


def change_days_for_metric(metric_id: Optional[str], query: str) -> int:
    spec = metric_spec(metric_id) if metric_id else None
    default = int(spec.change_days_default) if spec else 7
    q = query or ""
    ql = q.lower()
    if any(k in q for k in ["月度", "本月", "月环比"]) or any(
        k in ql for k in ["monthly", "mom", "this month", "released"]
    ):
        return 30
    if any(k in q for k in ["本周", "周环比", "最近一周", "7天"]) or any(
        k in ql for k in ["this week", "weekly", "wow", "7 days"]
    ):
        return 7
    return default


# Register aliases on import when ontology file exists
if _DEFAULT_ONTOLOGY_PATH.exists():
    try:
        register_ontology_aliases()
    except Exception:
        pass
