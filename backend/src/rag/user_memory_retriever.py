"""User Memory Retriever — semantic recall of per-user behavioral profiles.

Used at query time by the chat pipeline to inject personalised context
into the LLM prompt, making EDIA behave like a seasoned analyst who
remembers each user's trading style, goals, and recurring patterns.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.src.rag.vector_store import KnowledgeBase


@dataclass
class UserMemoryEntry:
    """A single user-memory entry returned by the retriever."""

    user_id: str
    session_id: str
    memory_type: str  # goal | trading_style | preference | pattern
    content: str
    confidence: float
    extracted_at: str

    def as_context_line(self) -> str:
        return (
            f"[{self.memory_type.upper()} | confidence={self.confidence:.0%}] "
            f"{self.content}"
        )


class UserMemoryRetriever:
    """Semantic retriever for user-memory entries stored in ChromaDB."""

    def __init__(self, kb: "KnowledgeBase"):
        self._kb = kb

    def retrieve(
        self,
        user_id: str,
        query: str,
        memory_type: str | None = None,
        top_k: int = 5,
    ) -> list[UserMemoryEntry]:
        """Query the user_memory collection filtered by user_id.

        Returns up to ``top_k`` entries most semantically similar to ``query``.
        If ``memory_type`` is provided, only entries of that type are returned.
        """
        raw = self._kb.query_user_memory(
            query_text=query,
            user_id=user_id,
            memory_type=memory_type,
            n_results=top_k,
        )
        return [self._parse(hit) for hit in raw]

    def retrieve_all(
        self,
        user_id: str,
        memory_type: str | None = None,
        limit: int = 50,
    ) -> list[UserMemoryEntry]:
        """Retrieve all memory entries for a user (no semantic filtering)."""
        raw = self._kb.get_user_memory_all(
            user_id=user_id,
            memory_type=memory_type,
            limit=limit,
        )
        return [self._parse(hit) for hit in raw]

    def build_context_block(
        self,
        user_id: str,
        query: str,
        memory_type: str | None = None,
        top_k: int = 5,
    ) -> str:
        """Build a markdown-formatted context block for LLM injection.

        If no entries are found, returns an empty string.
        """
        entries = self.retrieve(user_id=user_id, query=query, memory_type=memory_type, top_k=top_k)
        if not entries:
            return ""

        lines = ["## Known User Context", ""]
        # Group by type
        by_type: dict[str, list[UserMemoryEntry]] = {}
        for entry in entries:
            by_type.setdefault(entry.memory_type, []).append(entry)

        for mtype, items in by_type.items():
            lines.append(f"**{mtype.replace('_', ' ').title()}**")
            for item in items:
                lines.append(f"- {item.as_context_line()}")
            lines.append("")

        return "\n".join(lines).strip()

    def _parse(self, hit: dict) -> UserMemoryEntry:
        meta = hit.get("metadata") or {}
        return UserMemoryEntry(
            user_id=str(meta.get("user_id", "")),
            session_id=str(meta.get("session_id", "")),
            memory_type=str(meta.get("memory_type", "unknown")),
            content=hit.get("text", ""),
            confidence=float(meta.get("confidence", 0.0)),
            extracted_at=str(meta.get("extracted_at", "")),
        )
