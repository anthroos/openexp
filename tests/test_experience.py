"""Tests for Experience system — per-domain Q-value contexts."""
import json
import os
import tempfile
from pathlib import Path

import pytest

from openexp.core.experience import (
    Experience,
    DEFAULT_EXPERIENCE,
    load_experience,
    get_active_experience,
    list_experiences,
    _parse_yaml,
)
from openexp.core.q_value import (
    QCache,
    QValueUpdater,
    QValueScorer,
    _is_flat_format,
    _migrate_flat_to_nested,
)


# --- Experience loading ---

def test_default_experience_constant():
    exp = DEFAULT_EXPERIENCE
    assert exp.name == "default"
    assert exp.session_reward_weights["commit"] == 0.3
    assert exp.outcome_resolvers == []


def test_load_default_experience():
    exp = load_experience("default")
    assert exp.name == "default"
    assert "commit" in exp.session_reward_weights


def test_load_bundled_sales_experience():
    exp = load_experience("sales")
    assert exp.name == "sales"
    assert exp.session_reward_weights["email_sent"] == 0.15
    assert len(exp.outcome_resolvers) == 1
    assert exp.retrieval_boosts["decision"] == 1.3
    assert exp.q_config_overrides["alpha"] == 0.3


def test_load_nonexistent_falls_back_to_default():
    exp = load_experience("nonexistent_experience_xyz")
    assert exp.name == "default"


def test_load_yaml_from_user_dir(tmp_path, monkeypatch):
    """Test that user-dir YAML takes priority over bundled."""
    yaml_content = """
name: custom
description: Custom test experience
session_reward_weights:
  commit: 0.9
outcome_resolvers: []
retrieval_boosts: {}
q_config_overrides: {}
"""
    (tmp_path / "custom.yaml").write_text(yaml_content)
    monkeypatch.setattr("openexp.core.config.EXPERIENCES_DIR", tmp_path)

    exp = load_experience("custom")
    assert exp.name == "custom"
    assert exp.session_reward_weights["commit"] == 0.9


def test_list_experiences():
    exps = list_experiences()
    names = [e.name for e in exps]
    assert "default" in names
    assert "sales" in names


def test_get_active_experience_default(monkeypatch):
    monkeypatch.setattr("openexp.core.config.ACTIVE_EXPERIENCE", "default")
    exp = get_active_experience()
    assert exp.name == "default"


def test_get_active_experience_sales(monkeypatch):
    monkeypatch.setattr("openexp.core.config.ACTIVE_EXPERIENCE", "sales")
    exp = get_active_experience()
    assert exp.name == "sales"


# --- QCache per-experience ---

def test_qcache_experience_get_set():
    cache = QCache(max_size=10)
    cache.set("mem1", {"q_value": 0.6}, experience="default")
    cache.set("mem1", {"q_value": 0.9}, experience="sales")

    assert cache.get("mem1", "default")["q_value"] == 0.6
    assert cache.get("mem1", "sales")["q_value"] == 0.9
    assert cache.get("mem1", "coding") is None
    assert len(cache) == 1  # one memory, two experiences


def test_qcache_get_default_experience():
    """get() without experience param defaults to 'default'."""
    cache = QCache()
    cache.set("mem1", {"q_value": 0.5})
    assert cache.get("mem1")["q_value"] == 0.5


def test_qcache_get_all_q_values_per_experience():
    cache = QCache()
    cache.set("a", {"q_value": 0.3}, experience="default")
    cache.set("b", {"q_value": 0.7}, experience="default")
    cache.set("a", {"q_value": 0.9}, experience="sales")

    default_vals = cache.get_all_q_values("default")
    assert len(default_vals) == 2
    assert 0.3 in default_vals and 0.7 in default_vals

    sales_vals = cache.get_all_q_values("sales")
    assert len(sales_vals) == 1
    assert 0.9 in sales_vals


def test_qcache_get_experiences_for_memory():
    cache = QCache()
    cache.set("mem1", {"q_value": 0.5}, experience="default")
    cache.set("mem1", {"q_value": 0.8}, experience="sales")

    exps = cache.get_experiences_for_memory("mem1")
    assert set(exps) == {"default", "sales"}
    assert cache.get_experiences_for_memory("nonexistent") == []


def test_qcache_experience_stats():
    cache = QCache()
    cache.set("a", {"q_value": 0.2}, "default")
    cache.set("b", {"q_value": 0.4}, "default")
    cache.set("c", {"q_value": 0.6}, "default")

    stats = cache.get_experience_stats("default")
    assert stats["count"] == 3
    assert abs(stats["mean"] - 0.4) < 0.001
    assert stats["min"] == 0.2
    assert stats["max"] == 0.6

    empty_stats = cache.get_experience_stats("nonexistent")
    assert empty_stats["count"] == 0


# --- Flat → Nested migration ---

def test_is_flat_format_detection():
    flat = {"mem1": {"q_value": 0.5, "q_action": 0.5}}
    assert _is_flat_format(flat) is True

    nested = {"mem1": {"default": {"q_value": 0.5, "q_action": 0.5}}}
    assert _is_flat_format(nested) is False

    assert _is_flat_format({}) is False


def test_migrate_flat_to_nested():
    flat = {
        "mem1": {"q_value": 0.5, "q_action": 0.6},
        "mem2": {"q_value": 0.3, "q_action": 0.4},
    }
    nested = _migrate_flat_to_nested(flat)
    assert nested["mem1"]["default"]["q_value"] == 0.5
    assert nested["mem2"]["default"]["q_action"] == 0.4


def test_qcache_load_auto_migrates_flat():
    """Loading a flat Q-cache file should auto-migrate to nested."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "q_cache.json"
        flat_data = {
            "mem1": {"q_value": 0.5, "q_action": 0.6, "q_hypothesis": 0.4, "q_fit": 0.5},
            "mem2": {"q_value": 0.3, "q_action": 0.3, "q_hypothesis": 0.3, "q_fit": 0.3},
        }
        path.write_text(json.dumps(flat_data))

        cache = QCache()
        cache.load(path)

        # Should be accessible under "default" experience
        assert cache.get("mem1", "default")["q_value"] == 0.5
        assert cache.get("mem2", "default")["q_action"] == 0.3
        # Old flat access should return None (no experience key)
        assert cache.get("mem1", "sales") is None

        # Backup should have been created
        assert (Path(td) / "q_cache.json.bak").exists()


def test_qcache_save_load_nested():
    """Save and reload in nested format."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "q_cache.json"

        cache1 = QCache()
        cache1.set("x", {"q_value": 0.7}, "default")
        cache1.set("x", {"q_value": 0.9}, "sales")
        cache1.save(path)

        cache2 = QCache()
        cache2.load(path)
        assert cache2.get("x", "default")["q_value"] == 0.7
        assert cache2.get("x", "sales")["q_value"] == 0.9


def test_qcache_delta_merge_nested():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        main_path = td / "q_cache.json"
        deltas_dir = td / "deltas"

        cache1 = QCache()
        cache1.set("existing", {"q_value": 0.5}, "default")
        cache1.save(main_path)

        cache2 = QCache()
        cache2.set("new", {"q_value": 0.8, "q_updated_at": "2026-01-01"}, "sales")
        cache2.save_delta(deltas_dir, "session1")

        cache3 = QCache()
        cache3.load_and_merge(main_path, deltas_dir)
        assert cache3.get("existing", "default")["q_value"] == 0.5
        assert cache3.get("new", "sales")["q_value"] == 0.8
        assert len(list(deltas_dir.glob("*.json"))) == 0


# --- QValueUpdater with experience ---

def test_updater_with_experience():
    cache = QCache()
    updater = QValueUpdater(cache=cache)

    r1 = updater.update("mem1", reward=0.8, experience="sales")
    assert r1["q_value"] > 0.0
    assert cache.get("mem1", "sales") is not None
    assert cache.get("mem1", "default") is None  # not touched

    r2 = updater.update("mem1", reward=0.3, experience="default")
    assert cache.get("mem1", "default") is not None
    # Different Q-values for different experiences
    assert cache.get("mem1", "sales")["q_value"] != cache.get("mem1", "default")["q_value"]


def test_updater_update_all_layers_with_experience():
    cache = QCache()
    updater = QValueUpdater(cache=cache)

    rewards = {"action": 0.5, "hypothesis": 0.3, "fit": 0.4}
    r = updater.update_all_layers("mem1", rewards, experience="coding")
    assert r["q_value"] > 0.0
    assert cache.get("mem1", "coding") is not None
    assert cache.get("mem1", "default") is None


def test_batch_update_with_experience():
    cache = QCache()
    updater = QValueUpdater(cache=cache)

    results = updater.batch_update(["a", "b"], reward=0.5, experience="sales")
    assert len(results) == 2
    assert cache.get("a", "sales") is not None
    assert cache.get("a", "default") is None


# --- QValueScorer with experience ---

def test_scorer_rerank_with_experience():
    cache = QCache()
    cache.set("high_q", {"q_value": 0.9, "q_action": 0.9, "q_hypothesis": 0.9, "q_fit": 0.9}, "sales")
    cache.set("low_q", {"q_value": 0.1, "q_action": 0.1, "q_hypothesis": 0.1, "q_fit": 0.1}, "sales")

    scorer = QValueScorer(cache=cache)
    candidates = [
        {"id": "low_q", "score": 0.9},
        {"id": "high_q", "score": 0.5},
    ]

    reranked = scorer.rerank(candidates, top_k=2, experience="sales")
    assert len(reranked) == 2
    assert all("combined_score" in r for r in reranked)


# --- Session reward with custom weights ---

def test_compute_session_reward_with_weights():
    from openexp.ingest.reward import compute_session_reward

    observations = [
        {"summary": "git commit -m 'fix'", "tool": "Bash"},
        {"summary": "wrote email", "tool": "Write"},
        {"summary": "follow up sent", "tool": "Bash"},
    ]

    # Default weights
    reward_default = compute_session_reward(observations)
    assert isinstance(reward_default, float)

    # Custom sales weights
    sales_weights = {
        "commit": 0.05,
        "pr": 0.05,
        "writes": 0.01,
        "deploy": 0.0,
        "tests": 0.0,
        "decisions": 0.2,
        "email_sent": 0.15,
        "follow_up": 0.1,
        "base": -0.05,
        "min_obs_penalty": -0.05,
        "no_output_penalty": -0.1,
    }
    reward_sales = compute_session_reward(observations, weights=sales_weights)
    assert isinstance(reward_sales, float)
