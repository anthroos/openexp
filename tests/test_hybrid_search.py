"""Tests for hybrid search (BM25 + vector)."""
from openexp.core.hybrid_search import (
    tokenize,
    compute_tf,
    compute_idf,
    bm25_score,
    prepare_corpus_stats,
)


def test_tokenize():
    tokens = tokenize("Hello world, this is a test!")
    assert "hello" in tokens
    assert "world" in tokens
    assert "a" not in tokens  # single char filtered out (< 2 chars)


def test_tokenize_empty():
    assert tokenize("") == []
    assert tokenize(None) == []


def test_compute_tf():
    tokens = ["hello", "world", "hello"]
    tf = compute_tf(tokens)
    assert tf["hello"] > tf["world"]  # hello appears twice


def test_compute_idf():
    docs = [
        ["hello", "world"],
        ["hello", "python"],
        ["world", "code"],
    ]
    idf = compute_idf(docs)
    assert idf["python"] > idf["hello"]  # python is rarer


def test_bm25_score_basic():
    score = bm25_score("python code", "python programming with code examples")
    assert score > 0

    score_irrelevant = bm25_score("python code", "cooking recipes for dinner")
    assert score > score_irrelevant


def test_bm25_score_empty():
    assert bm25_score("", "some document") == 0.0
    assert bm25_score("query", "") == 0.0


def test_prepare_corpus_stats():
    docs = ["hello world", "python coding", "machine learning"]
    stats = prepare_corpus_stats(docs)
    assert "idf" in stats
    assert "avgdl" in stats
    assert stats["doc_count"] == 3


def test_prepare_corpus_stats_empty():
    stats = prepare_corpus_stats([])
    assert stats["avgdl"] == 0


def test_default_weights_sum_to_1():
    from openexp.core.hybrid_search import DEFAULT_HYBRID_WEIGHTS
    total = sum(DEFAULT_HYBRID_WEIGHTS.values())
    assert abs(total - 1.0) < 1e-9, f"Weights sum to {total}, expected 1.0"


def test_q_value_weight_is_active():
    """Q-value weight enabled at 10% for experience labeling."""
    from openexp.core.hybrid_search import DEFAULT_HYBRID_WEIGHTS
    assert DEFAULT_HYBRID_WEIGHTS["w_q_value"] == 0.10
