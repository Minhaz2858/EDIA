from backend.src.rag.vector_store import (
    DisabledKnowledgeBase,
    KnowledgeBase,
    _DEFAULT_SENTENCE_TRANSFORMER_MODEL,
    embedding_model_id,
)


def test_default_embedding_model_is_bge_m3():
    assert _DEFAULT_SENTENCE_TRANSFORMER_MODEL == "BAAI/bge-m3"


def test_embedding_model_id_env_override(monkeypatch):
    monkeypatch.setenv("RAG_SENTENCE_TRANSFORMER_MODEL", "custom/model")
    monkeypatch.setenv("RAG_EMBEDDING_BACKEND", "hash")
    info = embedding_model_id()
    assert info["model"] == "custom/model"
    assert info["backend"] == "hash"


def test_product_catalog_knowledge_upsert_query_and_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_EMBEDDING_BACKEND", "hash")
    kb = KnowledgeBase(persist_directory=str(tmp_path / "chroma"))

    product = {
        "id": "hydrogenated_c5_resin",
        "org_id": "org-test",
        "name_en": "Hydrogenated C5 Resin",
        "name_cn": "加氢碳五树脂",
        "product_type": "downstream",
        "unit": "CNY/ton",
        "description": "C5 downstream resin used in adhesives and road-marking paint.",
        "aliases": ["HC5 resin", "氢化碳五树脂"],
        "data_table": "market_prices",
        "data_key": "hydrogenated_c5_resin",
        "display_in": ["dashboard", "reports"],
        "updated_at": "2026-05-12T00:00:00Z",
    }

    kb.upsert_product_catalog(product)

    hits = kb.query_product_catalog(
        "Tell me about HC5 resin adhesive applications",
        product="hydrogenated_c5_resin",
        n_results=3,
    )

    assert hits
    assert "Hydrogenated C5 Resin" in hits[0]["text"]
    assert "C5 downstream resin" in hits[0]["text"]
    assert hits[0]["metadata"]["product"] == "hydrogenated_c5_resin"

    kb.delete_product_catalog("hydrogenated_c5_resin", org_id="org-test")

    assert kb.query_product_catalog(
        "Hydrogenated C5 Resin",
        product="hydrogenated_c5_resin",
        n_results=3,
    ) == []


def test_disabled_knowledge_base_queries_product_catalog_from_sqlite(monkeypatch):
    import backend.src.db.sqlite_db as sqlite_module

    class FakeProductDb:
        def list_all_platform_products(self):
            return [
                {
                    "id": "hydrogenated_c5_resin",
                    "org_id": "org-test",
                    "name_en": "Hydrogenated C5 Resin",
                    "name_cn": "加氢碳五树脂",
                    "product_type": "downstream",
                    "unit": "CNY/ton",
                    "description": "C5 downstream resin used in adhesives.",
                    "aliases": ["HC5 resin"],
                    "data_table": "market_prices",
                    "data_key": "hydrogenated_c5_resin",
                    "display_in": ["dashboard"],
                }
            ]

    monkeypatch.setattr(sqlite_module, "db", FakeProductDb())
    kb = DisabledKnowledgeBase(reason="corrupt chroma")

    hits = kb.query_product_catalog(
        "HC5 resin adhesive",
        product="hydrogenated_c5_resin",
        n_results=2,
    )

    assert hits
    assert "Hydrogenated C5 Resin" in hits[0]["text"]
    assert hits[0]["metadata"]["source"] == "platform_products"