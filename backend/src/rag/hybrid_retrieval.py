"""Dense + lexical hybrid retrieval with reciprocal rank fusion (RRF)."""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional


def query_terms(query_text: str) -> List[str]:
    raw_terms = re.findall(
        r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]+", (query_text or "").lower()
    )
    terms: List[str] = []
    for term in raw_terms:
        if re.fullmatch(r"[\u4e00-\u9fff]+", term) and len(term) > 2:
            terms.append(term)
            terms.extend(term[i : i + 2] for i in range(len(term) - 1))
        else:
            terms.append(term)
    seen: set[str] = set()
    return [t for t in terms if t and not (t in seen or seen.add(t))]


def lexical_score(text: str, meta: Dict | None, terms: List[str]) -> int:
    hay = f"{text or ''}\n{meta or {}}".lower()
    score = 0
    for term in terms:
        if term in hay:
            score += max(1, min(len(term), 6))
    return score


def _hit_key(hit: Dict[str, Any]) -> str:
    return str(hit.get("id") or hit.get("text") or "")


def reciprocal_rank_fusion(
    dense_hits: List[Dict[str, Any]],
    sparse_hits: List[Dict[str, Any]],
    *,
    k: int = 60,
) -> List[Dict[str, Any]]:
    """Merge ranked lists with RRF; preserve text/metadata from first seen hit."""
    scores: Dict[str, float] = {}
    merged: Dict[str, Dict[str, Any]] = {}

    for rank, hit in enumerate(dense_hits):
        key = _hit_key(hit)
        if not key:
            continue
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
        merged.setdefault(key, {**hit, "source": hit.get("source") or "dense"})

    for rank, hit in enumerate(sparse_hits):
        key = _hit_key(hit)
        if not key:
            continue
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
        if key in merged:
            merged[key]["source"] = "hybrid"
        else:
            merged[key] = {**hit, "source": hit.get("source") or "sparse"}

    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    out: List[Dict[str, Any]] = []
    for key, score in ordered:
        row = dict(merged[key])
        row["score"] = score
        out.append(row)
    return out


def sparse_hits_from_collection(
    collection,
    query_text: str,
    n_results: int,
    *,
    predicate: Callable[[Dict], bool] | None = None,
    prefetch_limit: int | None = None,
) -> List[Dict[str, Any]]:
    """Lexical scoring over persisted collection documents."""
    try:
        total = collection.count()
        if total <= 0:
            return []
        limit = prefetch_limit if prefetch_limit is not None else total
        payload = collection.get(limit=limit, include=["documents", "metadatas"])
        docs = payload.get("documents") or []
        metas = payload.get("metadatas") or []
        ids = payload.get("ids") or [None] * len(docs)
        terms = query_terms(query_text)
        ranked: List[tuple[int, str, Dict, str | None]] = []
        for doc_id, doc, meta in zip(ids, docs, metas):
            meta = meta or {}
            if predicate and not predicate(meta):
                continue
            score = lexical_score(doc or "", meta, terms)
            if score > 0:
                ranked.append((score, doc or "", meta, doc_id))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "id": doc_id,
                "text": doc,
                "metadata": meta,
                "score": score,
                "source": "sparse",
            }
            for score, doc, meta, doc_id in ranked[:n_results]
        ]
    except Exception:
        return []


def dense_hits_from_query(
    collection,
    query_text: str,
    n_results: int,
    *,
    where: Optional[Dict] = None,
) -> List[Dict[str, Any]]:
    kwargs: dict = {
        "query_texts": [query_text],
        "n_results": n_results,
        "include": ["documents", "metadatas", "distances"],
    }
    if where:
        kwargs["where"] = where
    r = collection.query(**kwargs)
    docs = (r.get("documents") or [[]])[0]
    metas = (r.get("metadatas") or [[]])[0]
    ids = (r.get("ids") or [[]])[0]
    distances = (r.get("distances") or [[]])[0]
    hits: List[Dict[str, Any]] = []
    for doc_id, doc, meta, dist in zip(ids, docs, metas, distances):
        hits.append(
            {
                "id": doc_id,
                "text": doc,
                "metadata": meta or {},
                "distance": dist,
                "source": "dense",
            }
        )
    return hits


def hybrid_query_collection(
    collection,
    query_text: str,
    n_results: int,
    *,
    where: Optional[Dict] = None,
    predicate: Callable[[Dict], bool] | None = None,
    rrf_k: int = 60,
    sparse_prefetch: int = 500,
) -> List[Dict[str, Any]]:
    """Dense Chroma query + lexical sparse pass merged with RRF."""
    dense_n = max(n_results * 2, n_results)
    dense = dense_hits_from_query(collection, query_text, dense_n, where=where)
    if predicate:
        dense = [h for h in dense if predicate(h.get("metadata") or {})]

    sparse = sparse_hits_from_collection(
        collection,
        query_text,
        max(n_results * 2, n_results),
        predicate=predicate,
        prefetch_limit=sparse_prefetch,
    )
    merged = reciprocal_rank_fusion(dense, sparse, k=rrf_k)
    return merged[:n_results]
