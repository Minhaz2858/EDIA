import json
import os
import subprocess
import sys
from pathlib import Path

import chromadb

from backend.src.rag.vector_store import (
    CHROMA_COLLECTION_NAMES,
    KnowledgeBase,
    LocalHashEmbeddingFunction,
    _DEFAULT_SENTENCE_TRANSFORMER_MODEL,
)


def test_embedding_model_default_is_bge_m3():
    assert _DEFAULT_SENTENCE_TRANSFORMER_MODEL == "BAAI/bge-m3"


def test_export_reembed_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_EMBEDDING_BACKEND", "hash")
    persist = tmp_path / "chroma"
    kb = KnowledgeBase(persist_directory=str(persist))
    kb.collection.add(
        documents=["weekly market outlook for isoprene"],
        metadatas=[{"product": "isoprene", "week_id": "2026-W01"}],
        ids=["w1"],
    )

    export_dir = tmp_path / "export"
    export_dir.mkdir()
    client = chromadb.PersistentClient(path=str(persist))
    col = client.get_collection("industry_reports")
    payload = col.get(include=["documents", "metadatas"])
    rows = [
        {
            "id": doc_id,
            "document": doc,
            "metadata": meta or {},
        }
        for doc_id, doc, meta in zip(
            payload["ids"], payload["documents"], payload["metadatas"]
        )
    ]
    path = export_dir / "industry_reports.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")

    client.delete_collection("industry_reports")
    col2 = client.get_or_create_collection(
        name="industry_reports", embedding_function=LocalHashEmbeddingFunction()
    )
    col2.upsert(
        ids=[r["id"] for r in rows],
        documents=[r["document"] for r in rows],
        metadatas=[r["metadata"] for r in rows],
    )
    assert col2.count() == 1
    hits = col2.query(query_texts=["isoprene outlook"], n_results=1)
    assert hits["documents"][0]


def test_rebuild_script_check_on_empty_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_EMBEDDING_BACKEND", "hash")
    persist = tmp_path / "chroma_empty"
    persist.mkdir()
    script = Path(__file__).resolve().parent / "scripts" / "rebuild_chroma_index.py"
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--check",
            "--persist-dir",
            str(persist),
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    assert result.returncode in (0, 1), result.stderr
    assert "Chroma persist" in result.stdout + result.stderr


def test_rebuild_script_check_uses_configured_embedding_function(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_EMBEDDING_BACKEND", "hash")
    persist = tmp_path / "chroma"
    kb = KnowledgeBase(persist_directory=str(persist))
    kb.add_weekly_chunks(
        [
            {
                "id": "2026-W01__isoprene__0",
                "text": "装置检修影响异戊二烯供应。",
                "metadata": {"product": "isoprene", "week_id": "2026-W01"},
            }
        ]
    )

    script = Path(__file__).resolve().parent / "scripts" / "rebuild_chroma_index.py"
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--check",
            "--persist-dir",
            str(persist),
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env={**os.environ, "RAG_EMBEDDING_BACKEND": "hash"},
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "weekly_reports: 1 documents" in output
    assert "dimension" not in output.lower()


def test_chroma_collection_names_complete():
    assert "weekly_reports" in CHROMA_COLLECTION_NAMES
    assert "industry_reports" in CHROMA_COLLECTION_NAMES
    assert len(CHROMA_COLLECTION_NAMES) == 8
