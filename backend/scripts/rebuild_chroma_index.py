#!/usr/bin/env python3
"""Chroma health check, backup, export, re-embed, and weekly-report rebuild."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Avoid initializing the module-level kb singleton against a possibly corrupt
# production Chroma directory while this maintenance script imports helpers.
os.environ.setdefault("DISABLE_RAG", "true")

from backend.src.rag.vector_store import (  # noqa: E402
    CHROMA_COLLECTION_NAMES,
    KnowledgeBase,
    _persist_directory,
    embedding_model_id,
)


def _export_dir(base: Path | None = None) -> Path:
    root = base or _REPO_ROOT
    return root / "backend" / "data" / "chroma_export"


def _load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _looks_corrupt(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "malformed" in msg or "disk image" in msg or "rustbindingsapi" in msg


def _corruption_message() -> str:
    return (
        "ERROR: Chroma database is corrupted. Restore from a backup, or run "
        "--backup --export if readable; if export fails, delete chroma_db/ and "
        "run --weekly-only after source reports are available."
    )


def _current_embedding_function():
    with tempfile.TemporaryDirectory(prefix="edia_chroma_embedding_") as tmp:
        kb = KnowledgeBase(persist_directory=tmp)
        return kb.embedding_fn, kb.embedding_backend


def cmd_check(persist_directory: str) -> int:
    print(f"Chroma persist: {persist_directory}")
    print(f"Embedding config: {embedding_model_id()}")
    import chromadb

    try:
        client = chromadb.PersistentClient(path=persist_directory)
    except Exception as exc:
        if _looks_corrupt(exc):
            print(_corruption_message())
            return 2
        print(f"ERROR: cannot open Chroma: {exc}")
        return 1

    try:
        embedding_fn, embedding_backend = _current_embedding_function()
    except Exception as exc:
        print(f"ERROR: cannot initialize configured embedding function: {exc}")
        return 1

    ok = True
    for name in CHROMA_COLLECTION_NAMES:
        try:
            col = client.get_collection(name, embedding_function=embedding_fn)
            count = col.count()
            print(f"  {name}: {count} documents")
            if count > 0:
                col.query(query_texts=["health check"], n_results=1)
        except Exception as exc:
            ok = False
            msg = str(exc).lower()
            if "embedding function" in msg and "conflict" in msg:
                print(
                    f"  {name}: EMBEDDING MISMATCH — run --reembed after --export. ({exc})"
                )
            elif "malformed" in msg or "disk image" in msg:
                print(f"  {name}: CORRUPTED — {exc}")
            else:
                print(f"  {name}: ERROR — {exc}")

    try:
        kb = KnowledgeBase(persist_directory=persist_directory)
        print(
            "KnowledgeBase init OK "
            f"(backend={kb.embedding_backend}, check_backend={embedding_backend})"
        )
    except Exception as exc:
        ok = False
        print(f"KnowledgeBase init FAILED: {exc}")

    return 0 if ok else 1


def cmd_backup(persist_directory: str) -> Path:
    src = Path(persist_directory)
    if not src.exists():
        raise FileNotFoundError(f"Chroma directory not found: {src}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = src.parent / f"{src.name}_backup_{stamp}"
    shutil.copytree(src, dest)
    print(f"Backup created: {dest}")
    return dest


def cmd_export(persist_directory: str, export_root: Path | None = None) -> int:
    import chromadb

    out_dir = _export_dir(export_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        client = chromadb.PersistentClient(path=persist_directory)
    except Exception as exc:
        if _looks_corrupt(exc):
            print(_corruption_message())
            raise SystemExit(2) from exc
        raise
    total = 0
    for name in CHROMA_COLLECTION_NAMES:
        path = out_dir / f"{name}.jsonl"
        rows: list[dict] = []
        try:
            col = client.get_collection(name)
            count = col.count()
            if count <= 0:
                _write_jsonl(path, [])
                print(f"  {name}: 0 (empty)")
                continue
            payload = col.get(limit=count, include=["documents", "metadatas"])
            for doc_id, doc, meta in zip(
                payload.get("ids") or [],
                payload.get("documents") or [],
                payload.get("metadatas") or [],
            ):
                rows.append(
                    {
                        "id": doc_id,
                        "document": doc,
                        "metadata": meta or {},
                    }
                )
        except Exception as exc:
            print(f"  {name}: export failed — {exc}")
            continue
        _write_jsonl(path, rows)
        total += len(rows)
        print(f"  {name}: exported {len(rows)} → {path}")
    print(f"Export complete: {total} documents in {out_dir}")
    return total


def cmd_reembed(
    persist_directory: str,
    export_root: Path | None = None,
    *,
    wipe_all: bool = False,
) -> int:
    import chromadb

    out_dir = _export_dir(export_root)
    embedding_fn, embedding_backend = _current_embedding_function()
    try:
        client = chromadb.PersistentClient(path=persist_directory)
    except Exception as exc:
        if _looks_corrupt(exc):
            print(_corruption_message())
            raise SystemExit(2) from exc
        raise

    if wipe_all:
        for name in CHROMA_COLLECTION_NAMES:
            try:
                client.delete_collection(name)
                print(f"  deleted collection: {name}")
            except Exception:
                pass

    upserted = 0
    for name in CHROMA_COLLECTION_NAMES:
        path = out_dir / f"{name}.jsonl"
        rows = _load_jsonl(path)
        if not rows:
            continue
        try:
            client.delete_collection(name)
        except Exception:
            pass
        col = client.get_or_create_collection(name=name, embedding_function=embedding_fn)
        batch_size = 64
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            col.upsert(
                ids=[r["id"] for r in batch],
                documents=[r["document"] for r in batch],
                metadatas=[r.get("metadata") or {} for r in batch],
            )
        upserted += len(rows)
        print(f"  {name}: re-embedded {len(rows)} documents")
    print(f"Re-embed complete: {upserted} documents (backend={embedding_backend})")
    return upserted


def cmd_weekly_only(base_dir: Path, persist_directory: str, reset: bool = True) -> int:
    from backend.ingest_data import _chunks_to_kb_format
    from backend.src.service.weekly_report_parser import parse_all_weekly_reports

    import chromadb

    embedding_fn, _embedding_backend = _current_embedding_function()
    try:
        client = chromadb.PersistentClient(path=persist_directory)
    except Exception as exc:
        if _looks_corrupt(exc):
            print(_corruption_message())
            raise SystemExit(2) from exc
        raise
    if reset:
        try:
            client.delete_collection("weekly_reports")
            print("weekly_reports collection deleted.")
        except Exception as exc:
            print(f"Warning: could not delete weekly_reports: {exc}")

    collection = client.get_or_create_collection(
        name="weekly_reports", embedding_function=embedding_fn
    )
    chunks = parse_all_weekly_reports(base_dir)
    if not chunks:
        print("[rebuild_chroma_index] No weekly report chunks parsed.")
        return 0
    rows = _chunks_to_kb_format(chunks)
    batch_size = 64
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        collection.upsert(
            ids=[r["id"] for r in batch],
            documents=[r["text"] for r in batch],
            metadatas=[r.get("metadata") or {} for r in batch],
        )
    print(f"Weekly ingest: {len(rows)} chunks")
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="EDIA Chroma index maintenance")
    parser.add_argument(
        "--persist-dir",
        default=None,
        help="Chroma persist directory (default: CHROMA_PERSIST_DIRECTORY or repo/chroma_db)",
    )
    parser.add_argument("--base-dir", default=str(_REPO_ROOT), help="Project root for weekly ingest")
    parser.add_argument("--export-dir", default=None, help="Override chroma_export directory")
    parser.add_argument("--check", action="store_true", help="Health check only")
    parser.add_argument("--backup", action="store_true", help="Backup chroma_db")
    parser.add_argument("--export", action="store_true", help="Export collections to JSONL")
    parser.add_argument("--reembed", action="store_true", help="Re-embed from JSONL exports")
    parser.add_argument("--weekly-only", action="store_true", help="Re-ingest weekly reports from data/")
    parser.add_argument(
        "--full",
        action="store_true",
        help="backup → export → wipe collections → reembed → weekly-only (if data exists)",
    )
    args = parser.parse_args()

    persist = _persist_directory(args.persist_dir)
    export_root = Path(args.export_dir) if args.export_dir else _REPO_ROOT
    base_dir = Path(args.base_dir)

    if not any([args.check, args.backup, args.export, args.reembed, args.weekly_only, args.full]):
        parser.print_help()
        return 1

    if args.full:
        args.backup = True
        args.export = True
        args.reembed = True
        weekly_dir = base_dir / "data" / "weekly_report"
        if weekly_dir.exists() and any(weekly_dir.glob("**/*.md")):
            args.weekly_only = True

    code = 0
    if args.backup:
        cmd_backup(persist)
    if args.export:
        cmd_export(persist, export_root)
    if args.full and args.reembed:
        persist_path = Path(persist)
        if persist_path.exists():
            shutil.rmtree(persist_path)
        persist_path.mkdir(parents=True, exist_ok=True)
        print(f"Recreated Chroma persist directory: {persist_path}")
    if args.reembed:
        cmd_reembed(persist, export_root, wipe_all=args.full)
    if args.weekly_only:
        cmd_weekly_only(base_dir, persist, reset=not args.reembed)
    if args.check:
        code = cmd_check(persist)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
