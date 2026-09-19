"""Regression tests for get_prediction_stats.

New-path outcomes (schema_version 2) carry no reward key; stats must not
KeyError on them (broke MCP memory_stats with -32603 until 2026-07-03).
"""
import json

import pytest

from openexp.reward_tracker import RewardTracker


# Override autouse async fixture from conftest.py
@pytest.fixture(autouse=True)
def cleanup_test_memories():
    yield


def _write_outcomes(data_dir, rows):
    with open(data_dir / "outcomes.jsonl", "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def test_stats_survive_rewardless_outcomes(tmp_path):
    tracker = RewardTracker(data_dir=tmp_path)
    _write_outcomes(tmp_path, [
        {"prediction_id": "pred_1", "reward": 0.8, "outcome": "closed_won"},
        {"prediction_id": "pred_2", "schema_version": 2,
         "actual_signal": "reply received", "days_to_resolve": 3},
    ])

    stats = tracker.get_prediction_stats()

    assert stats["positive_outcomes"] == 1
    assert stats["negative_outcomes"] == 0
    assert stats["observation_only_outcomes"] == 1
    assert stats["avg_reward"] == 0.8


def test_stats_all_outcomes_rewardless(tmp_path):
    tracker = RewardTracker(data_dir=tmp_path)
    _write_outcomes(tmp_path, [
        {"prediction_id": "pred_1", "schema_version": 2, "actual_signal": "no reply"},
    ])

    stats = tracker.get_prediction_stats()

    assert stats["positive_outcomes"] == 0
    assert stats["observation_only_outcomes"] == 1
    assert stats["avg_reward"] is None


def test_stats_no_outcomes(tmp_path):
    tracker = RewardTracker(data_dir=tmp_path)
    stats = tracker.get_prediction_stats()
    assert stats == {"total": 0, "resolved": 0, "pending": 0}
