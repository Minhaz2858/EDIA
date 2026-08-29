import chromadb
from chromadb.utils import embedding_functions
import os
import re
from typing import List, Dict
from datetime import datetime, timezone, timedelta
from pathlib import Path
import hashlib
import math


_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_SENTENCE_TRANSFORMER_MODEL = "BAAI/bge-m3"

CHROMA_COLLECTION_NAMES = (
    "industry_reports",
    "weekly_reports",
    "past_decisions",
    "market_signals",
    "causal_graph_embeddings",
    "news_events",
    "decision_outcomes",
    "product_catalog",
    "user_memory",
)


def _persist_directory(explicit: str | None = None) -> str:
    if explicit:
        return explicit

    configured = os.getenv("CHROMA_PERSIST_DIRECTORY", "").strip()
    if configured:
        return configured

    return str(_REPO_ROOT / "chroma_db")


def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _warehouse_only_mode() -> bool:
    return _env_flag("WAREHOUSE_ONLY_MODE", "true")


def _rag_embedding_backend_mode() -> str:
    raw = str(os.getenv("RAG_EMBEDDING_BACKEND", "auto") or "auto").strip().lower()
    if raw in {"sentence-transformer", "sentence_transformer", "st"}:
        return "sentence-transformer"
    if raw in {"hash", "local-hash", "local_hash"}:
        return "hash"
    if raw in {"disabled", "off", "none"}:
        return "disabled"
    return "auto"


def _sentence_transformer_model_name() -> str:
    return str(
        os.getenv("RAG_SENTENCE_TRANSFORMER_MODEL", _DEFAULT_SENTENCE_TRANSFORMER_MODEL)
        or _DEFAULT_SENTENCE_TRANSFORMER_MODEL
    ).strip()


def _sentence_transformer_cache_candidates(model_name: str) -> List[Path]:
    safe_name = model_name.replace("/", "_")
    short_name = model_name.split("/")[-1]
    org_name = model_name.split("/")[0] if "/" in model_name else ""
    candidates: List[Path] = []

    home = Path.home()
    candidates.extend(
        [
            home / ".cache" / "torch" / "sentence_transformers" / safe_name,
            home / ".cache" / "torch" / "sentence_transformers" / f"sentence-transformers_{safe_name}",
            home / ".cache" / "huggingface" / "hub" / f"models--sentence-transformers--{short_name}",
            home / ".cache" / "huggingface" / "hub" / f"models--sentence-transformers--{safe_name}",
        ]
    )
    if org_name:
        candidates.append(
            home / ".cache" / "huggingface" / "hub" / f"models--{org_name}--{short_name}"
        )

    env_cache = str(os.getenv("SENTENCE_TRANSFORMERS_HOME", "") or os.getenv("HF_HOME", "")).strip()
    if env_cache:
        root = Path(env_cache)
        candidates.extend(
            [
                root / safe_name,
                root / f"sentence-transformers_{safe_name}",
                root / "hub" / f"models--sentence-transformers--{short_name}",
                root / "hub" / f"models--sentence-transformers--{safe_name}",
            ]
        )
        if org_name:
            candidates.append(root / "hub" / f"models--{org_name}--{short_name}")

    return candidates


def embedding_model_id() -> Dict[str, str]:
    """Return active embedding configuration for health checks and logging."""
    mode = _rag_embedding_backend_mode()
    model = _sentence_transformer_model_name()
    if mode == "hash":
        backend = "hash"
    elif mode == "disabled":
        backend = "disabled"
    elif _has_local_sentence_transformer_model(model) or _env_flag(
        "RAG_ALLOW_MODEL_DOWNLOAD", "false"
    ):
        backend = "sentence-transformer"
    else:
        backend = "hash"
    return {"model": model, "backend": backend}


def _hybrid_weekly_enabled() -> bool:
    return _env_flag("RAG_HYBRID_WEEKLY", "true")


def _has_local_sentence_transformer_model(model_name: str) -> bool:
    for candidate in _sentence_transformer_cache_candidates(model_name):
        if candidate.exists():
            return True
    return False


class LocalHashEmbeddingFunction:
    """Small deterministic embedding fallback for offline/local recovery."""

    def __init__(self, dimensions: int = 256):
        self.dimensions = dimensions

    def name(self) -> str:
        # Chroma embedding interfaces may inspect a stable function name.
        return "local-hash-embedding-v1"

    def _tokens(self, text: str) -> List[str]:
        raw_terms = re.findall(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]+", (text or "").lower())
        terms: List[str] = []
        for term in raw_terms:
            if re.fullmatch(r"[\u4e00-\u9fff]+", term) and len(term) > 2:
                terms.append(term)
                terms.extend(term[i : i + 2] for i in range(len(term) - 1))
            else:
                terms.append(term)
        return terms or [""]

    def _embed_one(self, text: str) -> List[float]:
        vector = [0.0] * self.dimensions
        for token in self._tokens(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign

        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def __call__(self, input):
        return [self._embed_one(text) for text in input]

    def embed_documents(self, input) -> List[List[float]]:
        texts = list(input) if not isinstance(input, str) else [input]
        return [self._embed_one(text) for text in texts]

    def embed_query(self, input) -> List[List[float]]:
        if isinstance(input, str):
            return [self._embed_one(input)]
        texts = list(input)
        return [self._embed_one(text) for text in texts]


def _product_catalog_doc_and_meta(product: Dict) -> tuple[str, Dict]:
    product_id = str(product.get("id") or "").strip()
    org_id = str(product.get("org_id") or "org-default").strip() or "org-default"
    name_en = str(product.get("name_en") or "").strip()
    name_cn = str(product.get("name_cn") or "").strip()
    product_type = str(product.get("product_type") or "").strip()
    unit = str(product.get("unit") or "").strip()
    description = str(product.get("description") or "").strip()
    aliases = [str(alias).strip() for alias in (product.get("aliases") or []) if str(alias).strip()]
    data_table = str(product.get("data_table") or "").strip()
    data_key = str(product.get("data_key") or "").strip()
    display_in = [str(item).strip() for item in (product.get("display_in") or []) if str(item).strip()]

    doc_text = "\n".join(
        [
            f"Product ID: {product_id}",
            f"English name: {name_en}",
            f"Chinese name: {name_cn}",
            f"Module: {product_type}",
            f"Unit: {unit}",
            f"Aliases: {', '.join(aliases)}",
            f"Description: {description}",
            f"Data mapping: {data_table}/{data_key}",
            f"Display in: {', '.join(display_in)}",
        ]
    )
    metadata = {
        "product": product_id,
        "org_id": org_id,
        "name_en": name_en,
        "name_cn": name_cn,
        "product_type": product_type,
        "aliases": ", ".join(aliases),
        "data_table": data_table,
        "data_key": data_key,
        "display_in": ", ".join(display_in),
        "source": "platform_products",
        "updated_at": str(product.get("updated_at") or ""),
    }
    return doc_text, metadata


class KnowledgeBase:
    def __init__(self, persist_directory: str | None = None):
        persist_directory = _persist_directory(persist_directory)
        self.client = chromadb.PersistentClient(path=persist_directory)

        model_name = _sentence_transformer_model_name()
        self.embedding_model_name = model_name
        mode = _rag_embedding_backend_mode()
        remote_download_allowed = _env_flag("RAG_ALLOW_MODEL_DOWNLOAD", "false")
        offline_requested = str(
            os.getenv("HF_HUB_OFFLINE", "") or os.getenv("TRANSFORMERS_OFFLINE", "")
        ).strip().lower() in {"1", "true", "yes", "on"}
        local_model_available = _has_local_sentence_transformer_model(model_name)

        if mode == "disabled":
            raise RuntimeError("RAG embedding backend disabled by environment")

        use_sentence_transformer = False
        local_files_only = True

        if mode == "sentence-transformer":
            use_sentence_transformer = True
            local_files_only = not remote_download_allowed
        elif mode == "hash":
            use_sentence_transformer = False
        else:
            use_sentence_transformer = local_model_available or remote_download_allowed
            local_files_only = not remote_download_allowed

        if use_sentence_transformer:
            if offline_requested or local_files_only:
                os.environ.setdefault("HF_HUB_OFFLINE", "1")
                os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            try:
                self.embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                    model_name=model_name,
                    local_files_only=(offline_requested or local_files_only),
                )
                self.embedding_backend = "sentence-transformer"
            except Exception as embedding_error:
                if mode == "sentence-transformer":
                    raise RuntimeError(
                        f"Sentence-transformer embedding backend requested but unavailable for model '{model_name}': {embedding_error}"
                    ) from embedding_error
                print(
                    "RAG embeddings: sentence-transformer backend unavailable; using local hash fallback. Reason: "
                    + str(embedding_error)
                )
                self.embedding_fn = LocalHashEmbeddingFunction()
                self.embedding_backend = "hash"
        else:
            if mode == "auto" and not local_model_available and not remote_download_allowed:
                print(
                    f"RAG embeddings: local model '{model_name}' is not cached; using local hash fallback."
                )
            self.embedding_fn = LocalHashEmbeddingFunction()
            self.embedding_backend = "hash"

        try:
            self.collection = self.client.get_or_create_collection(
                name="industry_reports", embedding_function=self.embedding_fn
            )

            # Weekly reports collection (product × week chunks with rich metadata)
            self._weekly_collection = self.client.get_or_create_collection(
                name="weekly_reports", embedding_function=self.embedding_fn
            )

            # Past pricing decisions — institutional memory (Phase 4, §9)
            self._decisions_collection = self.client.get_or_create_collection(
                name="past_decisions", embedding_function=self.embedding_fn
            )

            # Market signals — historical pattern matching for forecast calibration
            self._market_signals_collection = self.client.get_or_create_collection(
                name="market_signals", embedding_function=self.embedding_fn
            )

            # Causal graph embeddings — knowledge graph edge/node embeddings
            self._causal_graph_collection = self.client.get_or_create_collection(
                name="causal_graph_embeddings", embedding_function=self.embedding_fn
            )

            # News events — external news and macro event signals
            self._news_events_collection = self.client.get_or_create_collection(
                name="news_events", embedding_function=self.embedding_fn
            )

            # Decision outcomes — feedback loop for decision quality tracking
            self._decision_outcomes_collection = self.client.get_or_create_collection(
                name="decision_outcomes", embedding_function=self.embedding_fn
            )

            # Product catalog — admin-maintained product profiles and aliases
            self._product_catalog_collection = self.client.get_or_create_collection(
                name="product_catalog", embedding_function=self.embedding_fn
            )

            # User memory — behavioral profiles per user (auto-learned from chat logs)
            self._user_memory_collection = self.client.get_or_create_collection(
                name="user_memory", embedding_function=self.embedding_fn
            )
        except Exception as collection_error:
            message = str(collection_error)
            if "embedding function" in message.lower() and "conflict" in message.lower():
                raise RuntimeError(
                    "Persisted Chroma collections use a different embedding backend than the current runtime. "
                    f"Current backend={self.embedding_backend}, model={model_name}. "
                    "Run: python backend/scripts/rebuild_chroma_index.py --backup --export --reembed. "
                    f"Original error: {collection_error}"
                ) from collection_error
            if "malformed" in message.lower() or "disk image" in message.lower():
                raise RuntimeError(
                    "Chroma database appears corrupted. "
                    "Run: python backend/scripts/rebuild_chroma_index.py --backup --full "
                    f"(persist_directory={persist_directory}). "
                    f"Original error: {collection_error}"
                ) from collection_error
            raise

    def add_documents(
        self, documents: List[str], metadatas: List[Dict], ids: List[str]
    ):
        """Add documents to the strategy/industry_reports collection."""
        self.collection.add(documents=documents, metadatas=metadatas, ids=ids)

    def query(self, query_text: str, n_results: int = 3) -> List[str]:
        """Retrieve relevant docs from both strategy + weekly collections."""

        results: List[str] = []

        # Query strategy/industry_reports collection
        try:
            r1 = self.collection.query(query_texts=[query_text], n_results=n_results)
            if r1.get("documents"):
                results.extend(r1["documents"][0])
        except Exception:
            pass

        # Query weekly_reports collection (product-level chunks)
        try:
            weekly_count = self._weekly_collection.count()
            if weekly_count > 0:
                if _hybrid_weekly_enabled():
                    weekly_hits = self.query_weekly(query_text, product=None, n_results=n_results)
                    results.extend(h["text"] for h in weekly_hits if h.get("text"))
                else:
                    r2 = self._weekly_collection.query(
                        query_texts=[query_text], n_results=n_results
                    )
                    if r2.get("documents"):
                        results.extend(r2["documents"][0])
        except Exception:
            pass

        return results[: n_results * 2]

    def add_weekly_chunks(self, chunks: List[Dict]) -> int:
        """Upsert weekly report chunks into the weekly_reports collection."""
        if not chunks:
            return 0
        documents = [c["text"] for c in chunks]
        metadatas = [c["metadata"] for c in chunks]
        ids = [c["id"] for c in chunks]
        self._weekly_collection.upsert(
            documents=documents, metadatas=metadatas, ids=ids
        )
        return len(chunks)

    def query_weekly(
        self,
        query_text: str,
        product: str | None = None,
        n_results: int = 5,
    ) -> List[Dict]:
        """Query weekly_reports with optional product filter."""

        try:
            if _hybrid_weekly_enabled():
                from backend.src.rag.hybrid_retrieval import hybrid_query_collection

                where = {"product": product} if product else None
                predicate = (
                    (lambda meta: str(meta.get("product") or "") == product)
                    if product
                    else None
                )
                return hybrid_query_collection(
                    self._weekly_collection,
                    query_text,
                    n_results,
                    where=where,
                    predicate=predicate,
                )

            where = {"product": product} if product else None
            kwargs: dict = {"query_texts": [query_text], "n_results": n_results}
            if where:
                kwargs["where"] = where
            r = self._weekly_collection.query(
                **kwargs, include=["documents", "metadatas"]
            )
            if r.get("documents") and r["documents"][0]:
                return [
                    {"text": doc, "metadata": meta}
                    for doc, meta in zip(r["documents"][0], r["metadatas"][0])
                ]
        except Exception:
            pass

        return []

    # ------------------------------------------------------------------
    # Past decisions — institutional memory
    # ------------------------------------------------------------------

    def store_decision(
        self,
        decision: Dict,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """Store a pricing decision in the past_decisions collection."""
        import uuid as _uuid

        pid = decision.get("product_id", "unknown")
        rec_price = decision.get("recommended_price", 0)
        action = decision.get("action", "hold")
        action_bias = decision.get("action_bias", "neutral")
        confidence = decision.get("confidence", 0.0)
        action_cn = decision.get("action_cn", "")
        dissent = decision.get("dissenting_view", "")
        price_range = decision.get("price_range", [0, 0])
        market_price = decision.get("latest_market_price", 0)
        price_delta = decision.get("price_delta_vs_market", 0)
        triggers = decision.get("triggers", []) or []

        now = datetime.now(timezone.utc)
        now_iso = now.strftime("%Y-%m-%d")
        now_ts = int(now.timestamp())
        user = str(user_id or "anonymous")[:64]
        session = str(session_id or "")[:128]
        trigger_head = ""
        if triggers and isinstance(triggers, list):
            first_trigger = triggers[0] or {}
            trigger_head = str(first_trigger.get("condition", "")).strip()

        doc_text = (
            f"Product: {pid} | Recommended: {rec_price} CNY/ton | "
            f"Market: {market_price} CNY/ton | Delta: {price_delta:+} | "
            f"Action: {action_cn} | Bias: {action_bias} | "
            f"Confidence: {confidence} | Range: {price_range[0]}-{price_range[1]} | "
            f"Trigger: {trigger_head} | Dissent: {dissent}"
        )

        meta = {
            "product": pid,
            "date": now_iso,
            "decision_ts": now_ts,
            "user_id": user,
            "session_id": session,
            "action": action,
            "action_bias": action_bias,
            "price_recommended": int(rec_price),
            "latest_market_price": int(market_price) if market_price else 0,
            "price_delta": int(price_delta) if price_delta else 0,
            "trigger_head": trigger_head,
            "confidence": float(confidence),
        }

        doc_id = (
            f"decision_{pid}_{now.strftime('%Y%m%d_%H%M%S')}_"
            f"{_uuid.uuid4().hex[:6]}"
        )

        try:
            self._decisions_collection.add(
                documents=[doc_text], metadatas=[meta], ids=[doc_id]
            )
        except Exception:
            pass

    def query_recent_decision_for_user(
        self,
        product_id: str,
        user_id: str,
        days: int = 7,
        limit: int = 1,
    ) -> List[Dict]:
        """Retrieve recent decisions for same user+product within a day window."""

        if not product_id or not user_id:
            return []

        cutoff_ts = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
        where = {
            "$and": [
                {"product": product_id},
                {"user_id": str(user_id)[:64]},
                {"decision_ts": {"$gte": cutoff_ts}},
            ]
        }

        try:
            r = self._decisions_collection.query(
                query_texts=["recent pricing decision"],
                n_results=max(1, limit),
                where=where,
                include=["documents", "metadatas"],
            )
            if r.get("documents") and r["documents"][0]:
                rows = [
                    {"text": doc, "metadata": meta}
                    for doc, meta in zip(r["documents"][0], r["metadatas"][0])
                ]
                rows.sort(
                    key=lambda x: int((x.get("metadata") or {}).get("decision_ts", 0)),
                    reverse=True,
                )
                return rows[:limit]
        except Exception:
            pass

        return []

    def query_past_decisions(
        self,
        query_text: str,
        product: str | None = None,
        n_results: int = 3,
    ) -> List[Dict]:
        """Retrieve similar past decisions for institutional memory."""

        try:
            where = {"product": product} if product else None
            kwargs: dict = {"query_texts": [query_text], "n_results": n_results}
            if where:
                kwargs["where"] = where
            r = self._decisions_collection.query(
                **kwargs, include=["documents", "metadatas"]
            )
            if r.get("documents") and r["documents"][0]:
                return [
                    {"text": doc, "metadata": meta}
                    for doc, meta in zip(r["documents"][0], r["metadatas"][0])
                ]
        except Exception:
            pass

        return []

    # ------------------------------------------------------------------
    # Market signals — historical pattern matching
    # ------------------------------------------------------------------

    def add_market_signal(self, signal: Dict) -> None:
        """Store a market signal (price spike, macro event, pattern match)."""

        import uuid as _uuid
        from datetime import datetime as _dt

        product = signal.get("product", "unknown")
        signal_type = signal.get("signal_type", "price_move")
        description = signal.get("description", "")
        magnitude = signal.get("magnitude", 0.0)
        date = signal.get("date", _dt.utcnow().strftime("%Y-%m-%d"))

        doc_text = (
            f"Signal: {signal_type} | Product: {product} | "
            f"Magnitude: {magnitude}% | Date: {date} | {description}"
        )
        meta = {
            "product": product,
            "signal_type": signal_type,
            "magnitude": float(magnitude),
            "date": date,
        }
        doc_id = f"signal_{product}_{date}_{_uuid.uuid4().hex[:6]}"

        try:
            self._market_signals_collection.add(
                documents=[doc_text], metadatas=[meta], ids=[doc_id]
            )
        except Exception:
            pass

    def query_market_signals(
        self,
        query_text: str,
        product: str | None = None,
        signal_type: str | None = None,
        n_results: int = 5,
    ) -> List[Dict]:
        """Retrieve similar historical market signals for pattern matching."""

        try:
            where_clauses = []
            if product:
                where_clauses.append({"product": product})
            if signal_type:
                where_clauses.append({"signal_type": signal_type})

            kwargs: dict = {"query_texts": [query_text], "n_results": n_results}
            if len(where_clauses) == 1:
                kwargs["where"] = where_clauses[0]
            elif len(where_clauses) > 1:
                kwargs["where"] = {"$and": where_clauses}

            r = self._market_signals_collection.query(
                **kwargs, include=["documents", "metadatas"]
            )
            if r.get("documents") and r["documents"][0]:
                return [
                    {"text": doc, "metadata": meta}
                    for doc, meta in zip(r["documents"][0], r["metadatas"][0])
                ]
        except Exception:
            pass

        return []

    # ------------------------------------------------------------------
    # Causal graph embeddings — knowledge graph traversal support
    # ------------------------------------------------------------------

    def add_causal_embedding(self, node_or_edge: Dict) -> None:
        """Store a knowledge graph node or edge as a vector embedding."""

        import uuid as _uuid

        entity_type = node_or_edge.get("type", "node")
        entity_id = node_or_edge.get("id", "unknown")
        description = node_or_edge.get("description", "")
        properties = node_or_edge.get("properties", {})

        doc_text = (
            f"{entity_type.upper()}: {entity_id} | {description} | "
            f"Properties: {properties}"
        )
        meta = {
            "entity_type": entity_type,
            "entity_id": entity_id,
        }
        doc_id = f"causal_{entity_type}_{entity_id}_{_uuid.uuid4().hex[:6]}"

        try:
            self._causal_graph_collection.add(
                documents=[doc_text], metadatas=[meta], ids=[doc_id]
            )
        except Exception:
            pass

    def query_causal_graph(
        self,
        query_text: str,
        entity_type: str | None = None,
        n_results: int = 5,
    ) -> List[Dict]:
        """Retrieve relevant causal graph embeddings."""

        try:
            where = {"entity_type": entity_type} if entity_type else None
            kwargs: dict = {"query_texts": [query_text], "n_results": n_results}
            if where:
                kwargs["where"] = where
            r = self._causal_graph_collection.query(
                **kwargs, include=["documents", "metadatas"]
            )
            if r.get("documents") and r["documents"][0]:
                return [
                    {"text": doc, "metadata": meta}
                    for doc, meta in zip(r["documents"][0], r["metadatas"][0])
                ]
        except Exception:
            pass

        return []

    # ------------------------------------------------------------------
    # News events — external news and macro signals
    # ------------------------------------------------------------------

    def add_news_event(self, event: Dict) -> None:
        """Store a news or macro event signal."""

        import uuid as _uuid
        from datetime import datetime as _dt

        headline = event.get("headline", "")
        source = event.get("source", "unknown")
        category = event.get("category", "macro")
        impact = event.get("impact", "neutral")
        date = event.get("date", _dt.utcnow().strftime("%Y-%m-%d"))
        body = event.get("body", "")

        doc_text = f"{headline}\n{body}" if body else headline
        meta = {
            "source": source,
            "category": category,
            "impact": impact,
            "date": date,
        }
        doc_id = f"news_{date}_{_uuid.uuid4().hex[:8]}"

        try:
            self._news_events_collection.add(
                documents=[doc_text], metadatas=[meta], ids=[doc_id]
            )
        except Exception:
            pass

    def query_news_events(
        self,
        query_text: str,
        category: str | None = None,
        n_results: int = 5,
    ) -> List[Dict]:
        """Retrieve relevant news/macro events."""

        try:
            where = {"category": category} if category else None
            kwargs: dict = {"query_texts": [query_text], "n_results": n_results}
            if where:
                kwargs["where"] = where
            r = self._news_events_collection.query(
                **kwargs, include=["documents", "metadatas"]
            )
            if r.get("documents") and r["documents"][0]:
                return [
                    {"text": doc, "metadata": meta}
                    for doc, meta in zip(r["documents"][0], r["metadatas"][0])
                ]
        except Exception:
            pass

        return []

    # ------------------------------------------------------------------
    # Decision outcomes — feedback loop for decision quality tracking
    # ------------------------------------------------------------------

    def store_decision_outcome(self, outcome: Dict) -> None:
        """Store the actual outcome of a past decision for feedback analysis."""

        import uuid as _uuid
        from datetime import datetime as _dt

        decision_id = outcome.get("decision_id", "unknown")
        product = outcome.get("product", "unknown")
        recommended_price = outcome.get("recommended_price", 0)
        actual_price = outcome.get("actual_price", 0)
        deviation_pct = outcome.get("deviation_pct", 0.0)
        quality_label = outcome.get("quality", "unknown")
        date = outcome.get("date", _dt.utcnow().strftime("%Y-%m-%d"))

        doc_text = (
            f"Outcome: {product} | Recommended: {recommended_price} | "
            f"Actual: {actual_price} | Deviation: {deviation_pct}% | "
            f"Quality: {quality_label} | Date: {date}"
        )
        meta = {
            "product": product,
            "decision_id": decision_id,
            "deviation_pct": float(deviation_pct),
            "quality": quality_label,
            "date": date,
        }
        doc_id = f"outcome_{product}_{date}_{_uuid.uuid4().hex[:6]}"

        try:
            self._decision_outcomes_collection.add(
                documents=[doc_text], metadatas=[meta], ids=[doc_id]
            )
        except Exception:
            pass

    def query_decision_outcomes(
        self,
        query_text: str,
        product: str | None = None,
        quality: str | None = None,
        n_results: int = 5,
    ) -> List[Dict]:
        """Retrieve past decision outcomes for feedback analysis."""

        try:
            where_clauses = []
            if product:
                where_clauses.append({"product": product})
            if quality:
                where_clauses.append({"quality": quality})

            kwargs: dict = {"query_texts": [query_text], "n_results": n_results}
            if len(where_clauses) == 1:
                kwargs["where"] = where_clauses[0]
            elif len(where_clauses) > 1:
                kwargs["where"] = {"$and": where_clauses}

            r = self._decision_outcomes_collection.query(
                **kwargs, include=["documents", "metadatas"]
            )
            if r.get("documents") and r["documents"][0]:
                return [
                    {"text": doc, "metadata": meta}
                    for doc, meta in zip(r["documents"][0], r["metadatas"][0])
                ]
        except Exception:
            pass

        return []

    # ------------------------------------------------------------------
    # Product catalog — custom product knowledge profiles
    # ------------------------------------------------------------------

    def upsert_product_catalog(self, product: Dict) -> None:
        product_id = str(product.get("id") or "").strip()
        org_id = str(product.get("org_id") or "org-default").strip() or "org-default"
        if not product_id:
            return
        doc_text, metadata = _product_catalog_doc_and_meta(product)
        doc_id = f"product_catalog::{org_id}::{product_id}"

        try:
            self._product_catalog_collection.upsert(
                documents=[doc_text], metadatas=[metadata], ids=[doc_id]
            )
        except Exception:
            pass

    def delete_product_catalog(self, product_id: str, org_id: str | None = None) -> None:
        product = str(product_id or "").strip()
        org = str(org_id or "org-default").strip() or "org-default"
        if not product:
            return
        try:
            self._product_catalog_collection.delete(ids=[f"product_catalog::{org}::{product}"])
        except Exception:
            pass

    def query_product_catalog(
        self,
        query_text: str,
        product: str | None = None,
        n_results: int = 3,
    ) -> List[Dict]:
        try:
            where = {"product": product} if product else None
            if where:
                exact = self._product_catalog_collection.get(
                    where=where,
                    limit=max(1, n_results),
                    include=["documents", "metadatas"],
                )
                docs = exact.get("documents") or []
                metas = exact.get("metadatas") or []
                if docs:
                    return [
                        {"text": doc, "metadata": meta}
                        for doc, meta in zip(docs, metas)
                    ]

            kwargs: dict = {"query_texts": [query_text], "n_results": n_results}
            if where:
                kwargs["where"] = where
            r = self._product_catalog_collection.query(
                **kwargs, include=["documents", "metadatas"]
            )
            if r.get("documents") and r["documents"][0]:
                return [
                    {"text": doc, "metadata": meta}
                    for doc, meta in zip(r["documents"][0], r["metadatas"][0])
                ]
        except Exception:
            pass

        return []

    # ------------------------------------------------------------------
    # User memory — auto-learned behavioral profiles per user
    # ------------------------------------------------------------------

    def upsert_user_memory(
        self,
        documents: List[str],
        metadatas: List[Dict],
        ids: List[str],
    ) -> None:
        """Upsert user memory documents into the user_memory collection."""
        if not documents:
            return
        try:
            self._user_memory_collection.upsert(
                documents=documents,
                metadatas=metadatas,
                ids=ids,
            )
        except Exception:
            pass

    def query_user_memory(
        self,
        query_text: str,
        user_id: str,
        memory_type: str | None = None,
        n_results: int = 5,
    ) -> List[Dict]:
        """Retrieve semantically relevant user memory entries filtered by user_id."""
        try:
            where: Dict | None = {"user_id": str(user_id)[:64]}
            if memory_type:
                where = {"user_id": str(user_id)[:64], "memory_type": memory_type}
            r = self._user_memory_collection.query(
                query_texts=[query_text],
                where=where,
                n_results=n_results,
                include=["documents", "metadatas"],
            )
            if r.get("documents") and r["documents"][0]:
                return [
                    {"text": doc, "metadata": meta}
                    for doc, meta in zip(r["documents"][0], r["metadatas"][0])
                ]
        except Exception:
            pass
        return []

    def get_user_memory_all(
        self,
        user_id: str,
        memory_type: str | None = None,
        limit: int = 50,
    ) -> List[Dict]:
        """Retrieve all memory entries for a user (no semantic query)."""
        try:
            where: Dict | None = {"user_id": str(user_id)[:64]}
            if memory_type:
                where = {"user_id": str(user_id)[:64], "memory_type": memory_type}
            r = self._user_memory_collection.get(
                where=where,
                limit=limit,
                include=["documents", "metadatas"],
            )
            if r.get("documents") and r["documents"][0]:
                return [
                    {"text": doc, "metadata": meta}
                    for doc, meta in zip(r["documents"], r["metadatas"])
                ]
        except Exception:
            pass
        return []


class DisabledKnowledgeBase:
    """Fallback KB that disables RAG gracefully.

    This keeps the backend usable even when the sentence-transformers model
    cannot be loaded (e.g., HF Hub unreachable and model not cached locally).
    """

    def __init__(self, reason: str = ""):
        self.disabled_reason = reason

    def add_documents(
        self, documents: List[str], metadatas: List[Dict], ids: List[str]
    ) -> None:
        return None

    def query(self, query_text: str, n_results: int = 3) -> List[str]:
        return []

    def add_weekly_chunks(self, chunks: List[Dict]) -> int:
        return 0

    def query_weekly(
        self,
        query_text: str,
        product: str | None = None,
        n_results: int = 5,
    ) -> List[Dict]:
        return []

    def store_decision(
        self,
        decision: Dict,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        return None

    def query_past_decisions(
        self,
        query_text: str,
        product: str | None = None,
        n_results: int = 3,
    ) -> List[Dict]:
        return []

    def query_recent_decision_for_user(
        self,
        product_id: str,
        user_id: str,
        days: int = 7,
        limit: int = 1,
    ) -> List[Dict]:
        return []

    def add_market_signal(self, signal: Dict) -> None:
        return None

    def query_market_signals(
        self,
        query_text: str,
        product: str | None = None,
        signal_type: str | None = None,
        n_results: int = 5,
    ) -> List[Dict]:
        return []

    def add_causal_embedding(self, node_or_edge: Dict) -> None:
        return None

    def query_causal_graph(
        self,
        query_text: str,
        entity_type: str | None = None,
        n_results: int = 5,
    ) -> List[Dict]:
        return []

    def add_news_event(self, event: Dict) -> None:
        return None

    def query_news_events(
        self,
        query_text: str,
        category: str | None = None,
        n_results: int = 5,
    ) -> List[Dict]:
        return []

    def store_decision_outcome(self, outcome: Dict) -> None:
        return None

    def query_decision_outcomes(
        self,
        query_text: str,
        product: str | None = None,
        quality: str | None = None,
        n_results: int = 5,
    ) -> List[Dict]:
        return []

    def upsert_product_catalog(self, product: Dict) -> None:
        return None

    def delete_product_catalog(self, product_id: str, org_id: str | None = None) -> None:
        return None

    def query_product_catalog(
        self,
        query_text: str,
        product: str | None = None,
        n_results: int = 3,
    ) -> List[Dict]:
        try:
            from backend.src.db.sqlite_db import db as _sqlite_db

            if product:
                rows = []
                for row in _sqlite_db.list_all_platform_products():
                    if str(row.get("id") or "") == str(product):
                        rows.append(row)
            else:
                rows = _sqlite_db.list_all_platform_products()

            terms = [
                term.lower()
                for term in re.findall(
                    r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]+", str(query_text or "").lower()
                )
                if term
            ]
            ranked = []
            for row in rows:
                text, metadata = _product_catalog_doc_and_meta(row)
                haystack = text.lower()
                score = sum(1 for term in terms if term in haystack)
                if product or score > 0:
                    ranked.append((score, text, metadata))
            ranked.sort(key=lambda item: item[0], reverse=True)
            return [
                {"text": text, "metadata": metadata, "score": score}
                for score, text, metadata in ranked[: max(1, n_results)]
            ]
        except Exception:
            return []

    def upsert_user_memory(
        self,
        documents: List[str],
        metadatas: List[Dict],
        ids: List[str],
    ) -> None:
        return

    def query_user_memory(
        self,
        query_text: str,
        user_id: str,
        memory_type: str | None = None,
        n_results: int = 5,
    ) -> List[Dict]:
        return []

    def get_user_memory_all(
        self,
        user_id: str,
        memory_type: str | None = None,
        limit: int = 50,
    ) -> List[Dict]:
        return []


class LexicalKnowledgeBase(DisabledKnowledgeBase):
    """Read-only fallback KB that queries persisted Chroma documents lexically.

    This keeps RAG usable on servers where embedding models cannot be loaded
    offline but the persisted Chroma collections already exist.
    """

    def __init__(self, persist_directory: str | None = None, reason: str = ""):
        super().__init__(reason=reason)
        persist_directory = _persist_directory(persist_directory)
        self.client = chromadb.PersistentClient(path=persist_directory)
        self.collection = self.client.get_collection("industry_reports")
        self._weekly_collection = self.client.get_collection("weekly_reports")
        self._decisions_collection = self.client.get_collection("past_decisions")
        self._market_signals_collection = self.client.get_collection("market_signals")
        self._causal_graph_collection = self.client.get_collection(
            "causal_graph_embeddings"
        )
        self._news_events_collection = self.client.get_collection("news_events")
        self._decision_outcomes_collection = self.client.get_collection(
            "decision_outcomes"
        )
        self._product_catalog_collection = self.client.get_or_create_collection(
            "product_catalog"
        )
        self._user_memory_collection = self.client.get_or_create_collection(
            "user_memory"
        )

    def _query_terms(self, query_text: str) -> List[str]:
        from backend.src.rag.hybrid_retrieval import query_terms

        return query_terms(query_text)

    def _score(self, text: str, meta: Dict | None, terms: List[str]) -> int:
        hay = f"{text or ''}\n{meta or {}}".lower()
        score = 0
        for term in terms:
            if term in hay:
                score += max(1, min(len(term), 6))
        return score

    def _filtered_rows(
        self,
        collection,
        query_text: str,
        n_results: int,
        predicate=None,
    ) -> List[Dict]:
        try:
            total = collection.count()
            if total <= 0:
                return []
            payload = collection.get(limit=total, include=["documents", "metadatas"])
            docs = payload.get("documents") or []
            metas = payload.get("metadatas") or []
            terms = self._query_terms(query_text)
            ranked = []
            for doc, meta in zip(docs, metas):
                if predicate and not predicate(meta or {}):
                    continue
                score = self._score(doc or "", meta or {}, terms)
                if score > 0:
                    ranked.append((score, doc, meta))
            ranked.sort(key=lambda item: item[0], reverse=True)
            return [
                {"text": doc, "metadata": meta, "score": score}
                for score, doc, meta in ranked[:n_results]
            ]
        except Exception:
            return []

    def query(self, query_text: str, n_results: int = 3) -> List[str]:
        rows = self._filtered_rows(self.collection, query_text, n_results)
        rows.extend(self._filtered_rows(self._weekly_collection, query_text, n_results))
        rows.sort(key=lambda item: item.get("score", 0), reverse=True)
        return [row["text"] for row in rows[: n_results * 2]]

    def query_weekly(
        self,
        query_text: str,
        product: str | None = None,
        n_results: int = 5,
    ) -> List[Dict]:
        return self._filtered_rows(
            self._weekly_collection,
            query_text,
            n_results,
            predicate=(
                lambda meta: not product or str(meta.get("product") or "") == product
            ),
        )

    def query_past_decisions(
        self,
        query_text: str,
        product: str | None = None,
        n_results: int = 3,
    ) -> List[Dict]:
        return self._filtered_rows(
            self._decisions_collection,
            query_text,
            n_results,
            predicate=(
                lambda meta: not product or str(meta.get("product") or "") == product
            ),
        )

    def query_recent_decision_for_user(
        self,
        product_id: str,
        user_id: str,
        days: int = 7,
        limit: int = 1,
    ) -> List[Dict]:
        cutoff_ts = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
        return self._filtered_rows(
            self._decisions_collection,
            f"recent pricing decision {product_id}",
            limit,
            predicate=lambda meta: str(meta.get("product") or "") == product_id
            and str(meta.get("user_id") or "") == str(user_id)[:64]
            and int(meta.get("decision_ts") or 0) >= cutoff_ts,
        )

    def query_market_signals(
        self,
        query_text: str,
        product: str | None = None,
        signal_type: str | None = None,
        n_results: int = 5,
    ) -> List[Dict]:
        return self._filtered_rows(
            self._market_signals_collection,
            query_text,
            n_results,
            predicate=lambda meta: (
                not product or str(meta.get("product") or "") == product
            )
            and (not signal_type or str(meta.get("signal_type") or "") == signal_type),
        )

    def query_causal_graph(
        self,
        query_text: str,
        entity_type: str | None = None,
        n_results: int = 5,
    ) -> List[Dict]:
        return self._filtered_rows(
            self._causal_graph_collection,
            query_text,
            n_results,
            predicate=lambda meta: not entity_type
            or str(meta.get("entity_type") or "") == entity_type,
        )

    def query_news_events(
        self,
        query_text: str,
        category: str | None = None,
        n_results: int = 5,
    ) -> List[Dict]:
        return self._filtered_rows(
            self._news_events_collection,
            query_text,
            n_results,
            predicate=lambda meta: not category
            or str(meta.get("category") or "") == category,
        )

    def query_decision_outcomes(
        self,
        query_text: str,
        product: str | None = None,
        quality: str | None = None,
        n_results: int = 5,
    ) -> List[Dict]:
        return self._filtered_rows(
            self._decision_outcomes_collection,
            query_text,
            n_results,
            predicate=lambda meta: (
                not product or str(meta.get("product") or "") == product
            )
            and (not quality or str(meta.get("quality") or "") == quality),
        )

    def query_product_catalog(
        self,
        query_text: str,
        product: str | None = None,
        n_results: int = 3,
    ) -> List[Dict]:
        return self._filtered_rows(
            self._product_catalog_collection,
            query_text,
            n_results,
            predicate=lambda meta: not product
            or str(meta.get("product") or "") == product,
        )

    def query_user_memory(
        self,
        query_text: str,
        user_id: str,
        memory_type: str | None = None,
        n_results: int = 5,
    ) -> List[Dict]:
        try:
            where: Dict | None = {"user_id": str(user_id)[:64]}
            if memory_type:
                where = {"user_id": str(user_id)[:64], "memory_type": memory_type}
            return self._filtered_rows(
                self._user_memory_collection,
                query_text,
                n_results,
                predicate=lambda meta: (
                    str(meta.get("user_id") or "") == str(user_id)[:64]
                    and (not memory_type or str(meta.get("memory_type") or "") == memory_type)
                ),
            )
        except Exception:
            return []

    def get_user_memory_all(
        self,
        user_id: str,
        memory_type: str | None = None,
        limit: int = 50,
    ) -> List[Dict]:
        try:
            where: Dict | None = {"user_id": str(user_id)[:64]}
            if memory_type:
                where = {"user_id": str(user_id)[:64], "memory_type": memory_type}
            return self._filtered_rows(
                self._user_memory_collection,
                "",
                limit,
                predicate=lambda meta: (
                    str(meta.get("user_id") or "") == str(user_id)[:64]
                    and (not memory_type or str(meta.get("memory_type") or "") == memory_type)
                ),
            )
        except Exception:
            return []


# Singleton instance for easy import
if _env_flag("DISABLE_RAG", "false") or not _env_flag("RAG_ENABLED", "true"):
    kb = DisabledKnowledgeBase(reason="disabled by environment")
else:
    try:
        kb = KnowledgeBase()
    except Exception as e:
        err_text = str(e)
        if _warehouse_only_mode():
            print(
                "RAG disabled for this run: " + err_text
            )
            kb = DisabledKnowledgeBase(reason=str(e))
        else:
            print(
                "RAG KnowledgeBase initialization failed (non-fatal). "
                "Attempting lexical fallback. Reason: " + err_text
            )
            try:
                kb = LexicalKnowledgeBase(reason=str(e))
                print(
                    "RAG fallback active: lexical retrieval over persisted Chroma collections"
                )
            except Exception as fallback_error:
                print(
                    "RAG lexical fallback also failed (non-fatal). "
                    "RAG will be disabled for this run. Reason: " + str(fallback_error)
                )
                kb = DisabledKnowledgeBase(reason=str(fallback_error))
