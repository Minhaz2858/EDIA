"""
ingest_data.py — Weekly report ingestion helper.

Provides:
  ingest_file(path, reset_weekly=False) -> int
    Parse a single .md weekly report and upsert its chunks into the
    ChromaDB `weekly_reports` collection.

  ingest_all_weekly_reports(base_dir, reset_weekly=False) -> int
    Parse all .md reports under data/weekly_report/ and upsert them.
"""
from __future__ import annotations

from pathlib import Path
from backend.src.service.weekly_report_parser import (
    parse_weekly_report,
    parse_all_weekly_reports,
)
from backend.src.rag.vector_store import kb


def _chunks_to_kb_format(chunks, source_name: str = "") -> list[dict]:
    """Convert ReportChunk list to the format expected by kb.add_weekly_chunks()."""
    result = []
    for idx, chunk in enumerate(chunks):
        product = chunk.metadata.get("product", "unknown")
        week_id = chunk.metadata.get("week_id", "unknown")
        # Stable, deterministic ID so upsert is idempotent
        chunk_id = f"{week_id}__{product}__{idx}"
        result.append({
            "text": chunk.text,
            "metadata": chunk.metadata,
            "id": chunk_id,
        })
    return result


def ingest_file(path: Path, reset_weekly: bool = False) -> int:
    """
    Parse a single .md weekly report file and upsert its chunks into ChromaDB.

    Args:
        path: Path to the .md file.
        reset_weekly: If True, drop and recreate the weekly_reports collection
                      before ingesting (full reset).

    Returns:
        Number of chunks upserted.
    """
    if reset_weekly:
        try:
            kb.client.delete_collection("weekly_reports")
            from chromadb.utils import embedding_functions
            kb._weekly_collection = kb.client.get_or_create_collection(
                name="weekly_reports", embedding_function=kb.embedding_fn
            )
            print("weekly_reports collection reset.")
        except Exception as e:
            print(f"Warning: could not reset weekly_reports collection: {e}")

    chunks = parse_weekly_report(path)
    if not chunks:
        return 0

    kb_chunks = _chunks_to_kb_format(chunks, source_name=path.name)
    added = kb.add_weekly_chunks(kb_chunks)
    print(f"[ingest_data] {path.name}: upserted {added} chunks into weekly_reports")
    return added


def ingest_all_weekly_reports(base_dir: Path, reset_weekly: bool = False) -> int:
    """
    Parse all .md reports under {base_dir}/data/weekly_report/ and upsert
    them into the ChromaDB weekly_reports collection.

    Args:
        base_dir: Project root directory (contains the `data/` folder).
        reset_weekly: If True, wipe and rebuild the collection from scratch.

    Returns:
        Total chunks upserted.
    """
    if reset_weekly:
        try:
            kb.client.delete_collection("weekly_reports")
            kb._weekly_collection = kb.client.get_or_create_collection(
                name="weekly_reports", embedding_function=kb.embedding_fn
            )
            print("weekly_reports collection reset.")
        except Exception as e:
            print(f"Warning: could not reset weekly_reports collection: {e}")

    print(f"[ingest_data] Scanning weekly reports under {base_dir / 'data' / 'weekly_report'} ...")
    all_chunks = parse_all_weekly_reports(base_dir)
    if not all_chunks:
        print("[ingest_data] No chunks parsed — check weekly_report directory.")
        return 0

    kb_chunks = _chunks_to_kb_format(all_chunks)
    total = kb.add_weekly_chunks(kb_chunks)
    print(f"[ingest_data] Total: upserted {total} chunks from {len(set(c['metadata'].get('week_id','') for c in kb_chunks))} reports.")
    return total
