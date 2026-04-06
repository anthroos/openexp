"""Tests for Q-value learning engine."""
import json
import tempfile
from pathlib import Path

from openexp.core.q_value import (
    QCache, QValueUpdater, QValueScorer, _is_newer,
    _append_reward_context, MAX_REWARD_CONTEXTS, MAX_CONTEXT_LENGTH,
)


def test_qcache_basic():
    cache = QCache(max_size=10)
    assert len(cache) == 0

    cache.set("mem1", {"q_value": 0.6, "q_action": 0.6})
    assert len(cache) == 1
    assert cache.get("mem1")["q_value"] == 0.6
    assert cache.get("nonexistent") is None


def test_qcache_lru_eviction():
    cache = QCache(max_size=3)
    cache.set("a", {"q_value": 0.5})
    cache.set("b", {"q_value": 0.5})
    cache.set("c", {"q_value": 0.5})
    assert len(cache) == 3

    cache.set("d", {"q_value": 0.5})
    assert len(cache) == 3
    assert cache.get("a") is None  # evicted
    assert cache.get("d") is not None


def test_qcache_save_load():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "q_cache.json"

        cache1 = QCache()
        cache1.set("x", {"q_value": 0.7, "q_action": 0.8})
        cache1.save(path)

        cache2 = QCache()
        cache2.load(path)
        assert cache2.get("x")["q_value"] == 0.7


def test_qcache_delta_merge():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        main_path = td / "q_cache.json"
        deltas_dir = td / "deltas"

        # Write main cache
        cache1 = QCache()
        cache1.set("existing", {"q_value": 0.5})
        cache1.save(main_path)

        # Write a delta
        cache2 = QCache()
        cache2.set("new_from_delta", {"q_value": 0.8, "q_updated_at": "2026-01-01"})
        cache2.save_delta(deltas_dir, "session1")

        # Load and merge
        cache3 = QCache()
        cache3.load_and_merge(main_path, deltas_dir)
        assert cache3.get("existing") is not None
        assert cache3.get("new_from_delta")["q_value"] == 0.8

        # Delta file should be cleaned up
        assert len(list(deltas_dir.glob("*.json"))) == 0


def test_q_updater_basic():
    cache = QCache()
    updater = QValueUpdater(cache=cache)

    result = updater.update("mem1", reward=0.8)
    first_q = result["q_value"]
    assert first_q > 0.0  # positive reward should increase Q from 0
    assert result["q_visits"] == 1

    result2 = updater.update("mem1", reward=0.8)
    assert result2["q_value"] > first_q  # another positive should increase more
    assert result2["q_visits"] == 2


def test_q_updater_negative_reward():
    cache = QCache()
    updater = QValueUpdater(cache=cache)

    result = updater.update("mem1", reward=-0.5)
    assert result["q_value"] < 0.0  # negative reward should decrease Q below 0


def test_q_updater_floor():
    cache = QCache()
    updater = QValueUpdater(cache=cache)

    # Apply many negative rewards
    for _ in range(20):
        result = updater.update("mem1", reward=-1.0)

    assert result["q_value"] >= -0.5  # should not go below floor


def test_q_updater_batch():
    cache = QCache()
    updater = QValueUpdater(cache=cache)

    results = updater.batch_update(["a", "b", "c"], reward=0.8)
    assert len(results) == 3
    assert all(v["q_value"] > 0.0 for v in results.values())


def test_q_scorer_rerank():
    cache = QCache()
    cache.set("high_q", {"q_value": 0.9, "q_action": 0.9, "q_hypothesis": 0.9, "q_fit": 0.9})
    cache.set("low_q", {"q_value": 0.1, "q_action": 0.1, "q_hypothesis": 0.1, "q_fit": 0.1})

    scorer = QValueScorer(cache=cache)
    candidates = [
        {"id": "low_q", "score": 0.9, "memory": "low q but high sim"},
        {"id": "high_q", "score": 0.5, "memory": "high q but low sim"},
    ]

    reranked = scorer.rerank(candidates, top_k=2)
    assert len(reranked) == 2
    # Both should have combined_score set
    assert all("combined_score" in r for r in reranked)


def test_is_newer():
    assert _is_newer({"q_updated_at": "2026-01-02"}, {"q_updated_at": "2026-01-01"}) is True
    assert _is_newer({"q_updated_at": "2026-01-01"}, {"q_updated_at": "2026-01-02"}) is False
    assert _is_newer({}, {"q_updated_at": "2026-01-01"}) is False  # no timestamp = not newer
    assert _is_newer({"q_updated_at": "2026-01-01"}, {}) is True


def test_q_updater_with_experience():
    """Verify updater respects experience parameter."""
    cache = QCache()
    updater = QValueUpdater(cache=cache)

    updater.update("mem1", reward=0.8, experience="default")
    updater.update("mem1", reward=0.3, experience="sales")

    default_q = cache.get("mem1", "default")["q_value"]
    sales_q = cache.get("mem1", "sales")["q_value"]
    assert default_q != sales_q


def test_q_scorer_rerank_with_experience():
    """Verify scorer uses experience-specific Q-values."""
    cache = QCache()
    cache.set("mem1", {"q_value": 0.9, "q_action": 0.9, "q_hypothesis": 0.9, "q_fit": 0.9}, "sales")
    cache.set("mem1", {"q_value": 0.1, "q_action": 0.1, "q_hypothesis": 0.1, "q_fit": 0.1}, "default")

    scorer = QValueScorer(cache=cache)
    candidates = [{"id": "mem1", "score": 0.5}]

    sales_result = scorer.rerank(candidates, top_k=1, experience="sales")
    default_result = scorer.rerank(candidates, top_k=1, experience="default")

    assert sales_result[0]["q_estimate"] == 0.9
    assert default_result[0]["q_estimate"] == 0.1


def test_append_reward_context_basic():
    q_data = {"q_value": 0.5}
    _append_reward_context(q_data, "Session +0.30: 2 commits")
    assert q_data["reward_contexts"] == ["Session +0.30: 2 commits"]


def test_append_reward_context_with_reward_id():
    q_data = {"q_value": 0.5}
    _append_reward_context(q_data, "Session +0.30: 2 commits", reward_id="rwd_abc12345")
    assert q_data["reward_contexts"] == ["Session +0.30: 2 commits [rwd_abc12345]"]


def test_append_reward_context_reward_id_none_no_pointer():
    q_data = {"q_value": 0.5}
    _append_reward_context(q_data, "Session +0.30: 2 commits", reward_id=None)
    assert q_data["reward_contexts"] == ["Session +0.30: 2 commits"]
    assert "[rwd_" not in q_data["reward_contexts"][0]


def test_append_reward_context_fifo_eviction():
    q_data = {"reward_contexts": [f"ctx_{i}" for i in range(MAX_REWARD_CONTEXTS)]}
    _append_reward_context(q_data, "new_context")
    assert len(q_data["reward_contexts"]) == MAX_REWARD_CONTEXTS
    assert q_data["reward_contexts"][-1] == "new_context"
    assert q_data["reward_contexts"][0] == "ctx_1"  # ctx_0 evicted


def test_append_reward_context_none_noop():
    q_data = {"q_value": 0.5}
    _append_reward_context(q_data, None)
    assert "reward_contexts" not in q_data
    _append_reward_context(q_data, "")
    assert "reward_contexts" not in q_data


def test_append_reward_context_truncation():
    q_data = {}
    long_ctx = "x" * 200
    _append_reward_context(q_data, long_ctx)
    assert len(q_data["reward_contexts"][0]) == MAX_CONTEXT_LENGTH


def test_q_updater_update_with_reward_context():
    cache = QCache()
    updater = QValueUpdater(cache=cache)
    result = updater.update("mem1", reward=0.8, reward_context="Session +0.30: 2 commits")
    assert result["reward_contexts"] == ["Session +0.30: 2 commits"]


def test_q_updater_update_all_layers_with_reward_context():
    cache = QCache()
    updater = QValueUpdater(cache=cache)
    result = updater.update_all_layers(
        "mem1", {"action": 0.5, "hypothesis": 0.3, "fit": 0.4},
        reward_context="Pred +0.80: deal closed",
    )
    assert result["reward_contexts"] == ["Pred +0.80: deal closed"]


def test_q_updater_backward_compat_no_context():
    """Without reward_context param, entries work as before (no reward_contexts key added)."""
    cache = QCache()
    updater = QValueUpdater(cache=cache)
    result = updater.update("mem1", reward=0.8)
    assert "reward_contexts" not in result


def test_qcache_save_load_with_contexts():
    """reward_contexts survive save/load cycle."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "q_cache.json"

        cache1 = QCache()
        q_data = {"q_value": 0.7, "q_action": 0.8, "reward_contexts": ["ctx1", "ctx2"]}
        cache1.set("x", q_data)
        cache1.save(path)

        cache2 = QCache()
        cache2.load(path)
        loaded = cache2.get("x")
        assert loaded["reward_contexts"] == ["ctx1", "ctx2"]


def test_q_updater_batch_with_reward_context():
    cache = QCache()
    updater = QValueUpdater(cache=cache)
    results = updater.batch_update(["a", "b"], reward=0.5, reward_context="Session +0.20: 1 commit")
    assert results["a"]["reward_contexts"] == ["Session +0.20: 1 commit"]
    assert results["b"]["reward_contexts"] == ["Session +0.20: 1 commit"]


def test_protected_memory_skips_negative_reward():
    """Protected memories should not decrease Q-value on negative reward."""
    cache = QCache()
    updater = QValueUpdater(cache=cache)

    # First give it a positive reward
    result = updater.update("mem1", reward=0.8)
    q_after_positive = result["q_value"]
    assert q_after_positive > 0

    # Mark as protected
    q_data = cache.get("mem1")
    q_data["protected"] = True
    cache.set("mem1", q_data)

    # Negative reward should NOT decrease Q
    result = updater.update("mem1", reward=-0.5)
    assert result["q_value"] == q_after_positive  # unchanged
    assert result["q_visits"] == 2  # visit still counted
    assert any("protected" in c for c in result.get("reward_contexts", []))


def test_protected_memory_accepts_positive_reward():
    """Protected memories should still increase Q-value on positive reward."""
    cache = QCache()
    updater = QValueUpdater(cache=cache)

    # Give initial positive reward and protect
    result = updater.update("mem1", reward=0.5)
    q_data = cache.get("mem1")
    q_data["protected"] = True
    cache.set("mem1", q_data)
    q_before = q_data["q_value"]

    # Positive reward should still work
    result = updater.update("mem1", reward=0.5)
    assert result["q_value"] > q_before


def test_protected_memory_update_all_layers_skips_negative():
    """Protected memories skip negative rewards in update_all_layers."""
    cache = QCache()
    updater = QValueUpdater(cache=cache)

    # Set up with positive Q and protect
    updater.update_all_layers("mem1", {"action": 0.5, "hypothesis": 0.3, "fit": 0.4})
    q_data = cache.get("mem1")
    q_before = q_data["q_value"]
    q_data["protected"] = True
    cache.set("mem1", q_data)

    # Negative rewards across all layers should be skipped
    result = updater.update_all_layers("mem1", {"action": -0.5, "hypothesis": -0.3, "fit": -0.4})
    assert result["q_value"] == q_before  # unchanged


def test_unprotected_memory_takes_negative_reward():
    """Non-protected memories should decrease Q-value normally."""
    cache = QCache()
    updater = QValueUpdater(cache=cache)

    result = updater.update("mem1", reward=0.8)
    q_after_positive = result["q_value"]

    # Without protection, negative reward decreases Q
    result = updater.update("mem1", reward=-0.5)
    assert result["q_value"] < q_after_positive
