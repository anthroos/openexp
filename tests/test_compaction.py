"""Tests for memory compaction module."""
import numpy as np
import pytest

from openexp.core.compaction import (
    _cosine_similarity,
    find_clusters,
    compute_merged_content,
    compute_merged_q,
)
from openexp.core.q_value import QCache


DIM = 384


def _make_similar_memories(base, count=5, noise=0.01):
    """Create count memories similar to base vector."""
    memories = []
    for i in range(count):
        rng = np.random.RandomState(i)
        n = rng.randn(DIM) * noise
        v = base + n
        v /= np.linalg.norm(v)
        memories.append({
            "id": f"sim-{i}",
            "vector": v.tolist(),
            "memory": f"similar memory {i}",
            "payload": {"status": "active", "memory_type": "fact"},
        })
    return memories


def _make_random_memories(count=3, seed=100):
    """Create count random (dissimilar) memories."""
    memories = []
    for i in range(count):
        rng = np.random.RandomState(seed + i)
        v = rng.randn(DIM)
        v /= np.linalg.norm(v)
        memories.append({
            "id": f"diff-{i}",
            "vector": v.tolist(),
            "memory": f"different memory {i}",
            "payload": {"status": "active", "memory_type": "action"},
        })
    return memories


class TestCosineSimilarity:
    def test_identical_vectors(self):
        a = np.array([1.0, 0.0, 0.0])
        assert abs(_cosine_similarity(a, a) - 1.0) < 1e-6

    def test_orthogonal_vectors(self):
        a = np.array([1.0, 0.0, 0.0])
        b = np.array([0.0, 1.0, 0.0])
        assert abs(_cosine_similarity(a, b)) < 1e-6

    def test_opposite_vectors(self):
        a = np.array([1.0, 0.0])
        b = np.array([-1.0, 0.0])
        assert abs(_cosine_similarity(a, b) + 1.0) < 1e-6

    def test_zero_vector(self):
        a = np.zeros(3)
        b = np.array([1.0, 0.0, 0.0])
        assert _cosine_similarity(a, b) == 0.0


class TestFindClusters:
    def test_similar_memories_cluster_together(self):
        rng = np.random.RandomState(42)
        base = rng.randn(DIM)
        base /= np.linalg.norm(base)

        memories = _make_similar_memories(base, count=5) + _make_random_memories(3)
        clusters = find_clusters(memories, max_distance=0.15, min_cluster_size=3)

        assert len(clusters) >= 1
        cluster_ids = {m["id"] for m in clusters[0]}
        # All similar memories should be in the same cluster
        for i in range(5):
            assert f"sim-{i}" in cluster_ids

    def test_no_clusters_when_all_different(self):
        memories = _make_random_memories(count=8, seed=200)
        clusters = find_clusters(memories, max_distance=0.15, min_cluster_size=3)
        assert len(clusters) == 0

    def test_min_cluster_size_respected(self):
        rng = np.random.RandomState(42)
        base = rng.randn(DIM)
        base /= np.linalg.norm(base)

        memories = _make_similar_memories(base, count=2)
        clusters = find_clusters(memories, max_distance=0.15, min_cluster_size=3)
        assert len(clusters) == 0

    def test_empty_input(self):
        clusters = find_clusters([], max_distance=0.15, min_cluster_size=3)
        assert clusters == []

    def test_strict_distance_splits_clusters(self):
        rng = np.random.RandomState(42)
        base = rng.randn(DIM)
        base /= np.linalg.norm(base)

        # Very strict distance should find fewer clusters
        memories = _make_similar_memories(base, count=5, noise=0.02)
        strict = find_clusters(memories, max_distance=0.01, min_cluster_size=3)
        loose = find_clusters(memories, max_distance=0.20, min_cluster_size=3)
        assert len(loose) >= len(strict)


class TestComputeMergedContent:
    def test_short_cluster(self):
        cluster = [
            {"memory": "fact A", "payload": {}},
            {"memory": "fact B", "payload": {}},
        ]
        merged = compute_merged_content(cluster)
        assert "fact A" in merged
        assert "fact B" in merged

    def test_deduplication(self):
        cluster = [
            {"memory": "same content", "payload": {}},
            {"memory": "same content", "payload": {}},
            {"memory": "different", "payload": {}},
        ]
        merged = compute_merged_content(cluster)
        assert merged.count("same content") == 1

    def test_long_cluster_truncates(self):
        cluster = [{"memory": f"memory {i}", "payload": {}} for i in range(10)]
        merged = compute_merged_content(cluster)
        assert "[+5 merged]" in merged

    def test_empty_memories_skipped(self):
        cluster = [
            {"memory": "", "payload": {}},
            {"memory": "real content", "payload": {}},
            {"memory": "  ", "payload": {}},
        ]
        merged = compute_merged_content(cluster)
        assert "real content" in merged


class TestComputeMergedQ:
    def test_basic_q_merge(self):
        rng = np.random.RandomState(42)
        base = rng.randn(DIM)
        base /= np.linalg.norm(base)

        cluster = _make_similar_memories(base, count=3)
        q_cache = QCache()

        # Set Q-values for originals
        for i, mem in enumerate(cluster):
            q_cache.set(mem["id"], {
                "q_value": 0.5 + i * 0.1,
                "q_action": 0.5 + i * 0.1,
                "q_hypothesis": 0.5,
                "q_fit": 0.5,
                "q_visits": 2,
                "last_reward": 0.1,
            })

        result = compute_merged_q(cluster, q_cache, "default")
        assert 0.0 <= result["q_value"] <= 1.0
        assert result["q_visits"] == 6  # Sum of visits
        assert result["kappa"] > 0  # Stiffness should be positive
        assert "q_action" in result
        assert "q_hypothesis" in result
        assert "q_fit" in result

    def test_no_q_data_defaults(self):
        rng = np.random.RandomState(42)
        base = rng.randn(DIM)
        base /= np.linalg.norm(base)

        cluster = _make_similar_memories(base, count=3)
        q_cache = QCache()  # Empty cache

        result = compute_merged_q(cluster, q_cache, "default")
        # Should default to 0.5
        assert abs(result["q_value"] - 0.5) < 0.1

    def test_kappa_high_when_consistent(self):
        rng = np.random.RandomState(42)
        base = rng.randn(DIM)
        base /= np.linalg.norm(base)

        cluster = _make_similar_memories(base, count=3)
        q_cache = QCache()

        # Same reward for all
        for mem in cluster:
            q_cache.set(mem["id"], {
                "q_action": 0.6, "q_hypothesis": 0.5, "q_fit": 0.5,
                "q_value": 0.56, "q_visits": 1, "last_reward": 0.2,
            })

        result = compute_merged_q(cluster, q_cache, "default")
        assert result["kappa"] >= 50  # Low variance → high kappa
