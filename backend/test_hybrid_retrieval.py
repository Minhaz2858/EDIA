from backend.src.rag.hybrid_retrieval import (
    lexical_score,
    query_terms,
    reciprocal_rank_fusion,
)


def test_query_terms_includes_chinese_bigrams():
    terms = query_terms("装置检修对异戊二烯影响")
    assert "装置" in terms or "检修" in terms
    assert any(len(t) == 2 for t in terms if "\u4e00" <= t[0] <= "\u9fff")


def test_lexical_score_prefers_matching_terms():
    score = lexical_score("SINOPEC listing price isoprene", {"product": "isoprene"}, ["sinopec", "listing"])
    assert score >= 2


def test_rrf_merges_dense_and_sparse_without_duplicate_text():
    dense = [
        {"id": "a", "text": "doc alpha", "metadata": {}, "source": "dense"},
        {"id": "b", "text": "doc beta", "metadata": {}, "source": "dense"},
    ]
    sparse = [
        {"id": "b", "text": "doc beta", "metadata": {}, "source": "sparse"},
        {"id": "c", "text": "doc gamma", "metadata": {}, "source": "sparse"},
    ]
    merged = reciprocal_rank_fusion(dense, sparse, k=60)
    texts = [h["text"] for h in merged]
    assert texts[0] == "doc beta"
    assert len(texts) == len(set(texts))
    assert merged[0]["source"] == "hybrid"


def test_rrf_orders_by_combined_rank():
    dense = [{"id": "1", "text": "first", "metadata": {}}]
    sparse = [{"id": "2", "text": "second", "metadata": {}}]
    merged = reciprocal_rank_fusion(dense, sparse)
    assert len(merged) == 2
    assert merged[0]["score"] >= merged[1]["score"]
