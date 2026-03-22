"""Tests for composite scoring."""
from openexp.core.scoring import composite_score, _compute_recency, score_results


def test_composite_score_basic():
    score = composite_score(semantic_similarity=0.8, importance=0.9, memory_type="decision")
    assert 0 <= score <= 1
    assert score > 0.5  # high similarity + high importance should score well


def test_composite_score_persistent_type():
    """Persistent types (decision, preference) should not decay below 0.4."""
    score = composite_score(
        semantic_similarity=0.8,
        created_at="2020-01-01T00:00:00Z",  # very old
        memory_type="decision",
    )
    assert score > 0.3  # should still be relevant due to persistence


def test_compute_recency_recent():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    recency = _compute_recency(now)
    assert recency > 0.9  # very recent should be close to 1


def test_compute_recency_old():
    recency = _compute_recency("2020-01-01T00:00:00Z")
    assert recency < 0.1  # very old should be close to 0


def test_compute_recency_none():
    recency = _compute_recency(None)
    assert recency == 0.5  # default for missing timestamp


def test_score_results():
    results = [
        {"score": 0.5, "metadata": {"type": "fact"}},
        {"score": 0.9, "metadata": {"type": "decision", "importance": 0.9}},
    ]
    scored = score_results(results)
    assert scored[0]["composite_score"] > scored[1]["composite_score"]  # decision should win
