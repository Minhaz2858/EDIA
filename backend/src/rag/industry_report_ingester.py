"""Bulk ingestion pipeline for industry reports into Chroma (industry_reports collection).

Usage:
    # Ingest all supported docs under /docs and /data/strategy
    python -m backend.src.rag.industry_report_ingester

    # Ingest a specific directory
    python -m backend.src.rag.industry_report_ingester /path/to/docs

    # Dry-run (print what would be ingested without writing to Chroma)
    python -m backend.src.rag.industry_report_ingester --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure repo root is on path
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


SUPPORTED_EXTENSIONS = {".docx", ".pdf", ".md", ".txt"}

# Default source directories to scan
DEFAULT_SOURCE_DIRS = [
    _REPO_ROOT / "docs",
    _REPO_ROOT / "data" / "strategy",
]


def _discover_files(directories: List[Path]) -> List[Path]:
    """Recursively find all supported document files in given directories."""
    files: List[Path] = []
    for directory in directories:
        if not directory.exists():
            print(f"[WARN] Source directory not found, skipping: {directory}", file=sys.stderr)
            continue
        for ext in SUPPORTED_EXTENSIONS:
            files.extend(sorted(directory.rglob(f"*{ext}")))  # type: ignore[arg-type]
    # Deduplicate
    seen: set[Path] = set()
    unique: List[Path] = []
    for f in files:
        if f not in seen:
            seen.add(f)
            unique.append(f)
    return unique


def _build_source_label(path: Path, repo_root: Path) -> str:
    """Build a human-readable source label from a file path."""
    try:
        rel = path.relative_to(repo_root)
        return str(rel).replace(os.sep, "/")
    except ValueError:
        return str(path)


def ingest_directory(
    directory: Path,
    kb_instance: Any,
    dry_run: bool = False,
    chunk_size_chars: int = 750,
    chunk_overlap_chars: int = 75,
    batch_size: int = 50,
) -> Dict[str, int]:
    """Ingest all supported documents from a directory into Chroma.

    Returns a summary dict: {"files_found", "files_processed", "chunks_created", "chunks_ingested"}.
    """
    from backend.src.rag.industry_report_chunker import chunk_file

    files = _discover_files([directory])
    repo_root = _REPO_ROOT

    files_found = len(files)
    files_processed = 0
    chunks_created = 0
    chunks_ingested = 0

    for file_path in files:
        source_label = _build_source_label(file_path, repo_root)
        chunks: List[Dict[str, Any]] = chunk_file(
            file_path,
            source_label=source_label,
            chunk_size_chars=chunk_size_chars,
            chunk_overlap_chars=chunk_overlap_chars,
        )
        if not chunks:
            print(f"  [SKIP] No content extracted: {file_path}")
            continue

        files_processed += 1
        chunks_created += len(chunks)

        if dry_run:
            print(f"  [DRY RUN] Would ingest {len(chunks)} chunks from: {source_label}")
            for c in chunks:
                preview = c["text"][:120].replace("\n", " ").strip()
                print(f"    - chunk {c['metadata']['chunk_index']}: {preview}...")
            chunks_ingested += len(chunks)
            continue

        # Batch ingest to avoid Chroma payload limits
        for batch_start in range(0, len(chunks), batch_size):
            batch = chunks[batch_start : batch_start + batch_size]
            try:
                kb_instance.add_documents(
                    documents=[c["text"] for c in batch],
                    metadatas=[c["metadata"] for c in batch],
                    ids=[c["id"] for c in batch],
                )
                chunks_ingested += len(batch)
            except Exception as e:
                print(f"  [ERROR] Failed to ingest batch from {source_label}: {e}", file=sys.stderr)

        print(f"  [OK] {source_label}: {len(chunks)} chunks")

    return {
        "files_found": files_found,
        "files_processed": files_processed,
        "chunks_created": chunks_created,
        "chunks_ingested": chunks_ingested,
    }


def run(directories: List[Path], dry_run: bool = False) -> Dict[str, Any]:
    """Main entry point: scan directories and ingest into Chroma."""
    # Import here so the CLI works even if Chroma isn't configured
    from backend.src.rag.vector_store import KnowledgeBase, DisabledKnowledgeBase, LexicalKnowledgeBase

    print(f"[INFO] Starting industry report ingestion")
    print(f"[INFO] Dry run: {dry_run}")
    print(f"[INFO] Source dirs: {[str(d) for d in directories]}")

    try:
        kb = KnowledgeBase()
        print(f"[INFO] Chroma backend: {kb.embedding_backend} ({kb.embedding_model_name})")
    except Exception as e:
        print(f"[WARN] Could not initialize KnowledgeBase: {e}", file=sys.stderr)
        print("[INFO] Falling back to DisabledKnowledgeBase (no actual writes)")
        kb = DisabledKnowledgeBase(reason=str(e))
        if dry_run:
            # For dry-run with no Chroma, just walk and chunk
            pass
        else:
            # Still proceed so summary is printed
            pass

    total_summary: Dict[str, int] = {
        "files_found": 0,
        "files_processed": 0,
        "chunks_created": 0,
        "chunks_ingested": 0,
    }

    for directory in directories:
        print(f"\n[INFO] Scanning: {directory}")
        summary = ingest_directory(
            directory=directory,
            kb_instance=kb,
            dry_run=dry_run,
        )
        for k, v in summary.items():
            total_summary[k] += v

    print(f"\n{'='*60}")
    print(f"[SUMMARY] Total files found:     {total_summary['files_found']}")
    print(f"[SUMMARY] Files processed:       {total_summary['files_processed']}")
    print(f"[SUMMARY] Chunks created:        {total_summary['chunks_created']}")
    print(f"[SUMMARY] Chunks ingested:       {total_summary['chunks_ingested']}")
    if dry_run:
        print(f"[SUMMARY] (dry-run — no actual Chroma writes)")
    print(f"{'='*60}")

    return total_summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest industry reports into Chroma RAG vector store."
    )
    parser.add_argument(
        "directories",
        nargs="*",
        default=[],
        help="Directories to scan (default: docs/ data/strategy/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and chunk files but do not write to Chroma",
    )
    args = parser.parse_args()

    directories: List[Path]
    if args.directories:
        directories = [Path(d) for d in args.directories]
    else:
        directories = DEFAULT_SOURCE_DIRS

    run(directories, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
