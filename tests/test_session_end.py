"""Tests for SessionEnd hook: ingest pipeline + reward computation.

Tests the Python side (ingest_session, reward, retrieval reward) with mock data.
Does NOT test the bash script directly.
"""
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from openexp.ingest.reward import compute_session_reward, reward_retrieved_memories
from openexp.ingest.retrieval_log import log_retrieval, get_session_retrievals


# Override autouse async fixture from conftest.py
@pytest.fixture(autouse=True)
def cleanup_test_memories():
    yield


class TestComputeSessionReward:
    def test_empty_session_negative(self):
        """Sessions with < 3 observations get extra negative reward."""
        reward = compute_session_reward([])
        assert reward < 0

    def test_commit_positive(self):
        """Git commits earn positive reward."""
        obs = [
            {"summary": "git commit -m 'fix bug'", "tool": "Bash"},
            {"summary": "Edited main.py", "tool": "Edit"},
            {"summary": "Read main.py", "tool": "Read"},
        ]
        reward = compute_session_reward(obs)
        assert reward > 0

    def test_pr_created(self):
        """PR creation adds reward on top of commits."""
        obs = [
            {"summary": "git commit -m 'feat'", "tool": "Bash"},
            {"summary": "gh pr create --title 'Add feature'", "tool": "Bash"},
            {"summary": "Edited file.py", "tool": "Edit"},
        ]
        reward = compute_session_reward(obs)
        assert reward >= 0.3  # commit + PR + write

    def test_readonly_session_negative(self):
        """Sessions with no writes and no commits are negative."""
        obs = [
            {"summary": "Read README.md", "tool": "Read"},
            {"summary": "git status", "tool": "Bash"},
            {"summary": "grep pattern", "tool": "Grep"},
        ]
        reward = compute_session_reward(obs)
        assert reward < 0

    def test_reward_clamped(self):
        """Reward is clamped to [-0.5, 0.5]."""
        # Many productive signals
        obs = [
            {"summary": "git commit -m 'big'", "tool": "Bash"},
            {"summary": "gh pr create", "tool": "Bash"},
            {"summary": "deploy prod", "tool": "Bash"},
            {"summary": "test pass all", "tool": "Bash"},
        ] + [{"summary": f"Edited f{i}.py", "tool": "Edit"} for i in range(20)]
        obs += [{"type": "decision", "summary": "chose approach A", "tool": "Bash"}]

        reward = compute_session_reward(obs)
        assert -0.5 <= reward <= 0.5


class TestRetrievalLog:
    def test_log_and_get(self, tmp_path):
        """Logged retrievals can be retrieved by session ID."""
        with patch("openexp.ingest.retrieval_log.RETRIEVALS_PATH", tmp_path / "ret.jsonl"):
            log_retrieval("sess-abc", "test query", ["mem-1", "mem-2"], [0.9, 0.8])
            log_retrieval("sess-xyz", "other query", ["mem-3"], [0.7])

            result = get_session_retrievals("sess-abc")
            assert "mem-1" in result
            assert "mem-2" in result
            assert "mem-3" not in result

    def test_dedup_retrievals(self, tmp_path):
        """Duplicate memory IDs within a session are deduplicated."""
        with patch("openexp.ingest.retrieval_log.RETRIEVALS_PATH", tmp_path / "ret.jsonl"):
            log_retrieval("sess-abc", "q1", ["mem-1", "mem-2"], [0.9, 0.8])
            log_retrieval("sess-abc", "q2", ["mem-2", "mem-3"], [0.85, 0.7])

            result = get_session_retrievals("sess-abc")
            assert result == ["mem-1", "mem-2", "mem-3"]

    def test_missing_file_returns_empty(self, tmp_path):
        """Non-existent retrieval file returns empty list."""
        with patch("openexp.ingest.retrieval_log.RETRIEVALS_PATH", tmp_path / "nope.jsonl"):
            result = get_session_retrievals("sess-abc")
            assert result == []


class TestRewardRetrievedMemories:
    def test_rewards_retrieved_memories(self, tmp_path):
        """Retrieved memories get Q-value updates."""
        ret_path = tmp_path / "ret.jsonl"
        q_cache_path = tmp_path / "q_cache.json"

        # Write retrieval log
        record = {
            "session_id": "sess-test",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "query": "test",
            "memory_ids": ["mem-a", "mem-b"],
            "scores": [0.9, 0.8],
        }
        ret_path.write_text(json.dumps(record) + "\n")

        # Write Q-cache with initial values (q_init=0.0)
        q_cache_path.write_text(json.dumps({
            "mem-a": {"q_value": 0.0, "q_action": 0.0, "q_hypothesis": 0.0, "q_fit": 0.0, "q_visits": 0},
            "mem-b": {"q_value": 0.0, "q_action": 0.0, "q_hypothesis": 0.0, "q_fit": 0.0, "q_visits": 0},
        }))

        with patch("openexp.ingest.retrieval_log.RETRIEVALS_PATH", ret_path), \
             patch("openexp.ingest.reward.Q_CACHE_PATH", q_cache_path):
            updated = reward_retrieved_memories("sess-test", reward=0.3)

        assert updated == 2

        # Verify Q-values changed
        q_data = json.loads(q_cache_path.read_text())
        assert q_data["mem-a"]["q_action"] != 0.0  # updated by reward
        assert q_data["mem-b"]["q_action"] != 0.0

    def test_no_retrievals_no_update(self, tmp_path):
        """If no retrievals for session, returns 0."""
        ret_path = tmp_path / "ret.jsonl"
        ret_path.write_text("")  # empty

        with patch("openexp.ingest.retrieval_log.RETRIEVALS_PATH", ret_path):
            updated = reward_retrieved_memories("sess-nope", reward=0.3)

        assert updated == 0
