"""Tests for Q-value learning engine."""
import json
import tempfile
from pathlib import Path

from openexp.core.q_value import QCache, QValueUpdater, QValueScorer, _is_newer


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
    assert first_q > 0.5  # positive reward should increase Q
    assert result["q_visits"] == 1

    result2 = updater.update("mem1", reward=0.8)
    assert result2["q_value"] > first_q  # another positive should increase more
    assert result2["q_visits"] == 2


def test_q_updater_negative_reward():
    cache = QCache()
    updater = QValueUpdater(cache=cache)

    result = updater.update("mem1", reward=-0.5)
    assert result["q_value"] < 0.5  # negative reward should decrease Q


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
    assert all(v["q_value"] > 0.5 for v in results.values())


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
