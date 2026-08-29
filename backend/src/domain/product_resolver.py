from __future__ import annotations

import re
from typing import Dict, List, Optional

from backend.src.domain.knowledge_graph import domain_graph


_SPLIT_PATTERN = r"\s*(?:,|，|/|&|\band\b|和|及|与)\s*"
_OUT_OF_SCOPE_IDS = {"ethylene", "hex", "eth_carbon"}


def _norm(text: str) -> str:
    return (text or "").strip().lower()


_NODE_BY_ID: Dict[str, object] = {
    str(getattr(node, "id", "") or "").lower(): node for node in domain_graph.nodes
}

_ALIASES: Dict[str, str] = {}


def _register(alias: str, product_id: str) -> None:
    key = _norm(alias)
    if key:
        _ALIASES[key] = product_id


def _register_graph_nodes() -> None:
    """Register all nodes in the current domain_graph into _ALIASES / _NODE_BY_ID."""
    from backend.src.domain.knowledge_graph import domain_graph as _dg
    for _node in _dg.nodes:
        _pid = str(getattr(_node, "id", "") or "").lower()
        if not _pid:
            continue
        _NODE_BY_ID[_pid] = _node
        _register(_pid, _pid)
        _register(str(getattr(_node, "name_en", "") or ""), _pid)
        _register(str(getattr(_node, "name_cn", "") or ""), _pid)


def _register_static_aliases() -> None:
    """Register hand-crafted business aliases that are always present."""
    # C5/C9 and common business aliases
    _register("mixed c5", "cracked_c5")
    _register("c5", "cracked_c5")
    _register("裂解c5", "cracked_c5")
    _register("裂解碳五", "cracked_c5")
    _register("混合碳五", "cracked_c5")
    _register("碳五", "cracked_c5")
    _register("pyrolytic c5", "cracked_c5")
    _register("pyrolysis c5", "cracked_c5")

    _register("mixed c9", "cracked_c9")
    _register("c9", "cracked_c9")
    _register("裂解c9", "cracked_c9")
    _register("裂解碳九", "cracked_c9")
    _register("混合碳九", "cracked_c9")
    _register("碳九", "cracked_c9")

    _register("sis rubber", "sis")
    _register("sis橡胶", "sis")
    _register("sis thermoplastic elastomer", "sis")
    _register("styrene-isoprene-styrene", "sis")
    _register("sis热塑性弹性体", "sis")
    _register("热塑性弹性体", "sis")

    _register("c5 petroleum resin", "c5_resin")
    _register("c5 resin", "c5_resin")
    _register("c5树脂", "c5_resin")
    _register("碳五石油树脂", "c5_resin")

    _register("间戊", "piperylene")
    _register("piperlyene", "piperylene")
    _register("piperyene", "piperylene")
    _register("piperlene", "piperylene")
    _register("piperyline", "piperylene")

    _register("抽余c5", "raffinate_c5")
    _register("抽余碳五", "raffinate_c5")

    _register("发泡剂", "blowing_agent")
    _register("戊烷发泡剂", "blowing_agent")
    _register("blowing agent", "blowing_agent")
    _register("pentane blowing agent", "blowing_agent")
    _register("pentane foaming agent", "blowing_agent")

    _register("加氢碳九", "hydrogenated_c9")
    _register("混三甲苯", "trimethylbenzene")
    _register("工业己烷", "hex")
    _register("己烷", "hex")
    _register("industrial hexane", "hex")
    _register("hexane", "hex")
    _register("乙烯炭黑料", "eth_carbon")
    _register("ethylene carbon black", "eth_carbon")

    # Recognized in NLU/DB logic as a known but out-of-pricing-scope product.
    _register("ethylene", "ethylene")
    _register("ethene", "ethylene")
    _register("乙烯", "ethylene")


def _register_platform_aliases() -> None:
    """Register extra aliases stored on each admin-registered platform product."""
    try:
        from backend.src.db.sqlite_db import db as _sqlite_db
        rows = _sqlite_db.list_platform_products("org-default")
        for row in rows:
            pid = str(row.get("id") or "").strip()
            if not pid:
                continue
            _register(str(row.get("name_en") or ""), pid)
            _register(str(row.get("name_cn") or ""), pid)
            for alias in (row.get("aliases") or []):
                _register(str(alias), pid)
    except Exception:
        pass


# Initial build
_register_graph_nodes()
_register_static_aliases()
_register_platform_aliases()


def rebuild_product_resolver() -> None:
    """Rebuild aliases and node map after platform products change.

    Call refresh_domain_graph() first so the updated domain_graph is in place,
    then call this to sync the NLU alias tables.
    """
    _ALIASES.clear()
    _NODE_BY_ID.clear()
    _register_graph_nodes()
    _register_static_aliases()
    _register_platform_aliases()


_CONTEXT_LABELS: Dict[str, str] = {
    "crude_oil": "Crude Oil",
    "naphtha": "Naphtha",
    "cracked_c5": "Mixed C5",
    "cracked_c9": "Mixed C9",
    "isoprene": "Isoprene",
    "piperylene": "Piperylene",
    "dcpd": "DCPD",
    "raffinate_c5": "Raffinate C5",
    "sis": "SIS Rubber",
    "c5_resin": "C5 Petroleum Resin",
    "blowing_agent": "Blowing Agent",
    "styrene": "Styrene",
    "hydrogenated_c9": "Hydrogenated C9",
    "trimethylbenzene": "Trimethylbenzene",
    "ethylene": "Ethylene",
}


def resolve_product_id(text: str) -> Optional[str]:
    token = _norm(text)
    if not token:
        return None

    mapped = _ALIASES.get(token)
    if mapped:
        return mapped

    # Soft fallback for noisy tokens like "裂解C5价格".
    for alias, pid in _ALIASES.items():
        if alias and alias in token:
            return pid
    return None


def split_product_tokens(text: str) -> List[str]:
    raw = str(text or "")
    if not raw.strip():
        return []
    return [t.strip() for t in re.split(_SPLIT_PATTERN, raw, flags=re.IGNORECASE) if t and t.strip()]


def extract_product_ids_in_text(text: str) -> List[str]:
    content = _norm(text)
    if not content:
        return []

    def _is_ascii_word_char(ch: str) -> bool:
        return ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch == "_"

    def _ascii_boundary_ok(src: str, start: int, end: int) -> bool:
        prev_ch = src[start - 1] if start > 0 else ""
        next_ch = src[end] if end < len(src) else ""
        return (not prev_ch or not _is_ascii_word_char(prev_ch)) and (
            not next_ch or not _is_ascii_word_char(next_ch)
        )

    matches: List[tuple[int, int, str]] = []
    for alias, pid in _ALIASES.items():
        if not alias:
            continue
        search_from = 0
        while True:
            start = content.find(alias, search_from)
            if start < 0:
                break
            end = start + len(alias)
            # For compact ASCII aliases (e.g. "sis"), enforce boundaries so
            # words like "analysis" do not spuriously match.
            if not (alias.isascii() and _ascii_boundary_ok(content, start, end) is False):
                matches.append((start, -len(alias), pid))
            search_from = start + 1

    if not matches:
        return []

    matches.sort()
    out: List[str] = []
    seen: set[str] = set()
    for _pos, _neg_len, pid in matches:
        if pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out


def product_id_to_context_label(product_id: str) -> str:
    pid = _norm(product_id)
    if not pid:
        return ""
    label = _CONTEXT_LABELS.get(pid)
    if label:
        return label
    node = _NODE_BY_ID.get(pid)
    if node is not None:
        return str(getattr(node, "name_en", "") or pid)
    return product_id


def get_product_node_by_id(product_id: str):
    return _NODE_BY_ID.get(_norm(product_id))


def list_supported_product_ids(include_out_of_scope: bool = False) -> List[str]:
    ids = sorted({pid for pid in _ALIASES.values() if pid})
    if include_out_of_scope:
        return ids
    return [pid for pid in ids if pid not in _OUT_OF_SCOPE_IDS]


def get_alias_mapping() -> Dict[str, str]:
    return dict(_ALIASES)


_SINOPEC_LISTING_MARKERS_ZH = (
    "中石化",
    "sinopec",
    "上海石化",
    "石化挂牌",
)
_SINOPEC_LISTING_MARKERS_EN = (
    "sinopec",
    "shanghai petrochemical",
)
_LISTING_PRICE_MARKERS_ZH = (
    "挂牌价",
    "挂牌",
    "月度挂牌",
)
_LISTING_PRICE_MARKERS_EN = (
    "listing price",
    "listed price",
    "monthly listing",
)


def looks_like_sinopec_listing_query(text: str) -> bool:
    """True when the user asks about SINOPEC / 中石化 monthly listed prices."""
    q = (text or "").strip()
    if not q:
        return False
    lower = q.lower()
    has_sinopec = any(m in q for m in _SINOPEC_LISTING_MARKERS_ZH) or any(
        m in lower for m in _SINOPEC_LISTING_MARKERS_EN
    )
    has_listing = any(m in q for m in _LISTING_PRICE_MARKERS_ZH) or any(
        m in lower for m in _LISTING_PRICE_MARKERS_EN
    )
    return has_sinopec and has_listing


def default_sinopec_listing_product_ids() -> List[str]:
    """Products with 上海石化 / sinopec_sh rows in market_prices."""
    return ["isoprene", "piperylene", "dcpd", "styrene"]
