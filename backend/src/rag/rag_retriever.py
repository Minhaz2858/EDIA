"""RAG retriever wrapper: query the Chroma knowledge base and return scored chunks.

Public API:
    retrieve(query: str, n_results: int = 3) -> List[RetrievedChunk]
    retrieve_with_context(query: str, n_results: int = 3) -> str
        # Returns a formatted string ready to inject into LLM prompt
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from backend.src.rag.vector_store import kb


@dataclass
class RetrievedChunk:
    """A single retrieved document chunk with its metadata and relevance score."""
    text: str
    source: str          # e.g. "docs/EDIA_AI_REBUILD_SPEC.md"
    file_stem: str       # e.g. "EDIA_AI_REBUILD_SPEC"
    chunk_index: int
    total_chunks: int
    score: float = 0.0   # Normalized 0–1 relevance (1 = most relevant)
    source_label: str = ""

    def formatted(self, max_chars: int = 600) -> str:
        """Return a compact single-line representation for prompt injection."""
        excerpt = self.text[:max_chars].replace("\n", " ").strip()
        if len(self.text) > max_chars:
            excerpt += "…"
        label = self.source_label or self.source
        return f"[Source: {label}]({self.chunk_index + 1}/{self.total_chunks}) {excerpt}"


def _normalize_scores(results: List[dict]) -> List[dict]:
    """Convert Chroma distances to 0–1 relevance scores (higher = more relevant)."""
    if not results:
        return results
    # Chroma returns L2 distances; convert to similarity via exponential decay
    # or simple inverse. Use min-max normalization if we have multiple results.
    distances = [r.get("distance", 0.0) for r in results]
    min_d, max_d = min(distances), max(distances)
    span = max_d - min_d
    if span <= 0:
        # All same distance → treat as 1.0 relevance
        for r in results:
            r["score"] = 1.0
    else:
        for r in results:
            # Exponential decay: score = exp(-distance), normalized to [0,1]
            import math
            r["score"] = math.exp(-r["distance"])
    return results


def retrieve(
    query: str,
    n_results: int = 3,
    collection: str = "industry_reports",
) -> List[RetrievedChunk]:
    """Query the RAG knowledge base and return the top-n scored chunks.

    Args:
        query: Natural-language query string.
        n_results: Number of chunks to retrieve (default 3).
        collection: Which Chroma collection to query.
                   Options: "industry_reports" (default), "weekly_reports",
                   "past_decisions", "market_signals", "news_events".

    Returns:
        List of RetrievedChunk objects sorted by descending relevance score.
    """
    if not query or not query.strip():
        return []

    try:
        raw_results: List[str] = kb.query(query_text=query, n_results=n_results)
    except Exception:
        return []

    if not raw_results:
        return []

    # Wrap raw text results with minimal metadata.
    # Note: kb.query() returns plain text strings (no scores/metadata).
    # For richer metadata + scores, use the collection-specific query methods.
    chunks: List[RetrievedChunk] = []
    for text in raw_results:
        if not text or not text.strip():
            continue
        # Try to extract metadata from text prefix (format: "[Source: path]")
        source_label = ""
        remaining = text
        import re
        m = re.match(r"^\[Source:\s*([^\]]+)\]", text)
        if m:
            source_label = m.group(1).strip()
        # Simple score based on position (best effort without dense distances)
        score = 1.0 / (len(chunks) + 1)
        chunks.append(RetrievedChunk(
            text=text,
            source=source_label,
            file_stem=source_label.split("/")[-1] if source_label else "unknown",
            chunk_index=0,
            total_chunks=1,
            score=score,
            source_label=source_label,
        ))

    return chunks


def retrieve_rich(
    query: str,
    n_results: int = 3,
    collection: str = "industry_reports",
) -> List[RetrievedChunk]:
    """Query with full metadata and scores using direct Chroma access.

    Uses the collection's query method directly to get distances → normalized scores.
    Falls back gracefully if Chroma is unavailable.
    """
    try:
        from backend.src.rag.vector_store import KnowledgeBase, LexicalKnowledgeBase
    except Exception:
        return retrieve(query, n_results, collection)

    try:
        kb_obj = KnowledgeBase()
    except Exception:
        # Fall back to lexical (no scores)
        return retrieve(query, n_results, collection)

    collection_map = {
        "industry_reports": kb_obj.collection,
        "weekly_reports": kb_obj._weekly_collection,
        "past_decisions": kb_obj._decisions_collection,
        "market_signals": kb_obj._market_signals_collection,
        "news_events": kb_obj._news_events_collection,
    }

    coll = collection_map.get(collection)
    if not coll:
        return retrieve(query, n_results, collection)

    try:
        result = coll.query(
            query_texts=[query],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )
    except Exception:
        return retrieve(query, n_results, collection)

    raw_docs = (result.get("documents") or [[]])[0]
    raw_metas = (result.get("metadatas") or [[]])[0]
    raw_dists = (result.get("distances") or [[]])[0]

    hits: List[dict] = []
    for doc, meta, dist in zip(raw_docs, raw_metas, raw_dists):
        if not doc:
            continue
        hit = {
            "text": doc,
            "metadata": meta or {},
            "distance": dist if dist is not None else 0.0,
        }
        hits.append(hit)

    hits = _normalize_scores(hits)

    chunks: List[RetrievedChunk] = []
    for hit in hits:
        meta = hit.get("metadata") or {}
        chunks.append(RetrievedChunk(
            text=hit["text"],
            source=str(meta.get("source", "")),
            file_stem=str(meta.get("file_stem", "unknown")),
            chunk_index=int(meta.get("chunk_index", 0)),
            total_chunks=int(meta.get("total_chunks", 1)),
            score=hit.get("score", 0.0),
            source_label=str(meta.get("source_label", meta.get("source", ""))),
        ))

    return chunks


def retrieve_with_context(
    query: str,
    n_results: int = 3,
    max_chars: int = 1800,
    collection: str = "industry_reports",
) -> str:
    """Build a RAG context string for injection into LLM prompts.

    Retrieves top chunks and formats them as:
        [Knowledge Base - Top {n} Results]
        [Source: path] excerpt...
        ---
        [Source: path] excerpt...
        ---

    Returns empty string if no relevant results found.
    """
    chunks = retrieve_rich(query, n_results=n_results, collection=collection)
    if not chunks:
        return ""

    total_chars = 0
    lines: List[str] = []
    for chunk in chunks:
        formatted = chunk.formatted(max_chars=500)
        if total_chars + len(formatted) + 5 > max_chars:
            break
        lines.append(formatted)
        total_chars += len(formatted) + 5

    if not lines:
        return ""

    header = f"[Knowledge Base - Top {len(lines)} Results]"
    return header + "\n" + "\n---\n".join(lines)
