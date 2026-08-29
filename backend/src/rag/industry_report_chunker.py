"""Document reading and chunking for industry report ingestion.

Supports: .docx, .pdf, .md, .txt
Chunking strategy: recursive character split at ~500 tokens with 50-token overlap.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

# ── Document readers ──────────────────────────────────────────────────────────────

def _read_docx(path: Path) -> str:
    try:
        from docx import Document
    except ImportError:
        return ""
    try:
        doc = Document(str(path))
        return "\n".join(p.text.strip() for p in doc.paragraphs if p.text.strip())
    except Exception:
        return ""


def _read_pdf(path: Path) -> str:
    try:
        import pypdf
    except ImportError:
        return ""
    try:
        reader = pypdf.PdfReader(str(path))
        parts: List[str] = []
        for page in reader.pages:
            text = (page.extract_text() or "").strip()
            if text:
                parts.append(text)
        return "\n\n".join(parts)
    except Exception:
        return ""


def _read_txt(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        try:
            return path.read_text(encoding="gbk", errors="replace").strip()
        except Exception:
            return ""


_READERS = {
    ".docx": _read_docx,
    ".pdf":  _read_pdf,
    ".md":   _read_txt,
    ".txt":  _read_txt,
}


def read_document(path: Path) -> str:
    """Return raw text content of a supported document."""
    content = ""
    for ext, reader in _READERS.items():
        if path.suffix.lower() == ext:
            content = reader(path)
            break
    return content


# ── Chunking ────────────────────────────────────────────────────────────────────

# Rough token estimator: 1 token ≈ 1.5 characters for mixed Chinese/English.
_CHARS_PER_TOKEN = 1.5

# Default chunk parameters
DEFAULT_CHUNK_SIZE_CHARS = int(500 * _CHARS_PER_TOKEN)   # ~500 tokens
DEFAULT_CHUNK_OVERLAP_CHARS = int(50 * _CHARS_PER_TOKEN)  # ~50 tokens overlap


def _split_by_paragraph(text: str, max_chars: int, overlap: int) -> Iterator[str]:
    """Split text into paragraph-level chunks within max_chars, with overlap."""
    # Paragraph boundaries
    paragraphs = re.split(r"\n\s*\n", text)
    current: List[str] = []
    current_len = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        para_len = len(para)

        # If single paragraph exceeds max_chars, split it further by sentence
        if para_len > max_chars:
            if current:
                yield "\n\n".join(current)
                # Keep tail for overlap
                tail = current[-1] if current else ""
                current = [tail] if tail else []
                current_len = len(tail)
            sub_chunks = _split_by_sentence(para, max_chars, overlap)
            for chunk in sub_chunks:
                yield chunk
            continue

        if current_len + para_len + 2 > max_chars and current:
            yield "\n\n".join(current)
            # Overlap: keep last paragraph(s) to maintain continuity
            overlap_parts: List[str] = []
            overlap_len = 0
            for p in reversed(current):
                if overlap_len + len(p) + 2 > overlap:
                    break
                overlap_parts.insert(0, p)
                overlap_len += len(p) + 2
            current = overlap_parts
            current_len = overlap_len
            # If we still can't fit the new paragraph, flush and start fresh
            if current_len + para_len + 2 > max_chars and current:
                current = []
                current_len = 0

        current.append(para)
        current_len += para_len + 2

    if current:
        yield "\n\n".join(current)


def _split_by_sentence(text: str, max_chars: int, overlap: int) -> Iterator[str]:
    """Split a long paragraph into sentence-level chunks with overlap."""
    # Sentence-end punctuation: Chinese 。！？； or English .!?;
    sentence_pattern = re.compile(
        r"(?<=[。！？；.!?;])\s*"
    )
    sentences = sentence_pattern.split(text)
    current: List[str] = []
    current_len = 0

    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        sent_len = len(sent)

        if current_len + sent_len + 1 > max_chars and current:
            yield " ".join(current)
            # Keep tail for overlap
            tail = current[-1] if current else ""
            current = [tail] if tail else []
            current_len = len(tail)

        current.append(sent)
        current_len += sent_len + 1

    if current:
        yield " ".join(current)


def chunk_text(
    text: str,
    chunk_size_chars: int = DEFAULT_CHUNK_SIZE_CHARS,
    chunk_overlap_chars: int = DEFAULT_CHUNK_OVERLAP_CHARS,
) -> List[str]:
    """Split document text into overlapping chunks."""
    if not text or not text.strip():
        return []
    chunks: List[str] = list(_split_by_paragraph(text, chunk_size_chars, chunk_overlap_chars))
    # Deduplicate while preserving order
    seen: set[str] = set()
    result: List[str] = []
    for c in chunks:
        if c and c not in seen:
            seen.add(c)
            result.append(c)
    return result


# ── Public API ──────────────────────────────────────────────────────────────────

SourceDir = Dict[str, Path]  # alias


def chunk_file(
    path: Path,
    source_label: str = "file",
    chunk_size_chars: int = DEFAULT_CHUNK_SIZE_CHARS,
    chunk_overlap_chars: int = DEFAULT_CHUNK_OVERLAP_CHARS,
) -> List[Dict[str, Any]]:
    """Read a document file and return a list of chunk dicts ready for Chroma.

    Returns a list of {"id", "text", "metadata"} dicts.
    """
    content = read_document(path)
    if not content.strip():
        return []

    chunks = chunk_text(content, chunk_size_chars, chunk_overlap_chars)
    file_stem = path.stem
    results: List[Dict[str, Any]] = []
    for i, chunk_text_str in enumerate(chunks):
        if not chunk_text_str.strip():
            continue
        results.append({
            "id": f"{source_label}::{file_stem}::chunk{i:04d}::{uuid.uuid4().hex[:8]}",
            "text": chunk_text_str,
            "metadata": {
                "source": str(path),
                "source_label": source_label,
                "file_stem": file_stem,
                "file_ext": path.suffix.lower(),
                "chunk_index": i,
                "total_chunks": len(chunks),
            },
        })
    return results
