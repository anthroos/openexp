"""Tests for multi-level retrospective system."""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from openexp.retrospective import (
    RetroLevel,
    _load_watermark,
    _save_watermark,
    _is_already_done,
    _mark_done,
    gather_daily_data,
    apply_adjustments,
    analyze_with_llm,
    run_retrospective,
    save_daily_q_stats,
)
from openexp.core.q_value import QCache, QValueUpdater


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """Set up temp dirs for all data paths."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()

    monkeypatch.setattr("openexp.retrospective.DATA_DIR", data_dir)
    monkeypatch.setattr("openexp.retrospective.WATERMARK_PATH", data_dir / "retrospective_watermark.json")
    monkeypatch.setattr("openexp.retrospective.Q_STATS_PATH", data_dir / "q_stats_daily.jsonl")
    monkeypatch.setattr("openexp.retrospective.Q_CACHE_PATH", data_dir / "q_cache.json")
    monkeypatch.setattr("openexp.retrospective.SESSIONS_DIR", sessions_dir)
    monkeypatch.setattr("openexp.retrospective.REWARD_LOG_PATH", data_dir / "reward_log.jsonl")

    return tmp_path


@pytest.fixture
def q_cache_with_memories():
    """Create a QCache with some test memories."""
    cache = QCache()
    for i in range(5):
        mem_id = f"mem-{i:04d}"
        cache.set(mem_id, {
            "q_value": 0.1 * i,
            "q_action": 0.1 * i,
            "q_hypothesis": 0.1 * i,
            "q_fit": 0.1 * i,
            "q_visits": i,
            "q_updated_at": datetime.now(timezone.utc).isoformat(),
        })
    return cache


# ---------------------------------------------------------------------------
# Watermark tests
# ---------------------------------------------------------------------------

class TestWatermark:
    def test_empty_watermark(self, tmp_data_dir):
        wm = _load_watermark()
        assert wm == {"daily": {}, "weekly": {}, "monthly": {}}

    def test_save_and_load(self, tmp_data_dir):
        _mark_done(RetroLevel.DAILY, "2026-04-07", "mem-001")
        assert _is_already_done(RetroLevel.DAILY, "2026-04-07")
        assert not _is_already_done(RetroLevel.DAILY, "2026-04-06")
        assert not _is_already_done(RetroLevel.WEEKLY, "2026-W15")

    def test_multiple_levels(self, tmp_data_dir):
        _mark_done(RetroLevel.DAILY, "2026-04-07", "mem-d")
        _mark_done(RetroLevel.WEEKLY, "2026-W15", "mem-w")
        _mark_done(RetroLevel.MONTHLY, "2026-03", "mem-m")

        assert _is_already_done(RetroLevel.DAILY, "2026-04-07")
        assert _is_already_done(RetroLevel.WEEKLY, "2026-W15")
        assert _is_already_done(RetroLevel.MONTHLY, "2026-03")


# ---------------------------------------------------------------------------
# set_q_value tests
# ---------------------------------------------------------------------------

class TestSetQValue:
    def test_set_q_value_basic(self):
        cache = QCache()
        cache.set("mem-1", {
            "q_value": 0.0, "q_action": 0.0, "q_hypothesis": 0.0, "q_fit": 0.0,
            "q_visits": 0,
        })
        updater = QValueUpdater(cache=cache)
        result = updater.set_q_value("mem-1", 0.5)

        assert result["q_value"] == pytest.approx(0.5, abs=0.05)
        assert result["q_visits"] == 1

    def test_set_q_value_respects_ceiling(self):
        cache = QCache()
        cache.set("mem-1", {
            "q_value": 0.8, "q_action": 0.8, "q_hypothesis": 0.8, "q_fit": 0.8,
            "q_visits": 0,
        })
        updater = QValueUpdater(cache=cache)
        result = updater.set_q_value("mem-1", 2.0)  # above ceiling
        assert result["q_value"] <= 1.0

    def test_set_q_value_respects_floor(self):
        cache = QCache()
        cache.set("mem-1", {
            "q_value": 0.0, "q_action": 0.0, "q_hypothesis": 0.0, "q_fit": 0.0,
            "q_visits": 0,
        })
        updater = QValueUpdater(cache=cache)
        result = updater.set_q_value("mem-1", -2.0)  # below floor
        assert result["q_value"] >= -0.5

    def test_set_q_value_no_change(self):
        cache = QCache()
        cache.set("mem-1", {
            "q_value": 0.5, "q_action": 0.5, "q_hypothesis": 0.5, "q_fit": 0.5,
            "q_visits": 3,
        })
        updater = QValueUpdater(cache=cache)
        result = updater.set_q_value("mem-1", 0.5)
        assert result["q_visits"] == 3  # no change, no visit increment

    def test_set_q_value_adds_context(self):
        cache = QCache()
        cache.set("mem-1", {
            "q_value": 0.0, "q_action": 0.0, "q_hypothesis": 0.0, "q_fit": 0.0,
            "q_visits": 0,
        })
        updater = QValueUpdater(cache=cache)
        result = updater.set_q_value("mem-1", 0.5, reward_context="test override")
        contexts = result.get("reward_contexts", [])
        assert len(contexts) == 1
        assert "[override]" in contexts[0]


# ---------------------------------------------------------------------------
# Apply adjustments tests
# ---------------------------------------------------------------------------

class TestApplyAdjustments:
    def test_promote(self, q_cache_with_memories):
        updater = QValueUpdater(cache=q_cache_with_memories)
        adjustments = [
            {"memory_id": "mem-0001", "action": "promote", "reward": 0.3, "reason": "test"},
        ]
        result = apply_adjustments(
            adjustments, RetroLevel.DAILY,
            q_cache_with_memories, updater,
        )
        assert result["applied"] == 1
        assert result["skipped"] == 0

        q_data = q_cache_with_memories.get("mem-0001")
        assert q_data["q_value"] > 0.1  # was 0.1, should be higher

    def test_demote(self, q_cache_with_memories):
        updater = QValueUpdater(cache=q_cache_with_memories)
        adjustments = [
            {"memory_id": "mem-0003", "action": "demote", "reward": 0.2, "reason": "false progress"},
        ]
        result = apply_adjustments(
            adjustments, RetroLevel.WEEKLY,
            q_cache_with_memories, updater,
        )
        assert result["applied"] == 1
        q_data = q_cache_with_memories.get("mem-0003")
        assert q_data["q_value"] < 0.3  # was 0.3, should be lower

    def test_override(self, q_cache_with_memories):
        updater = QValueUpdater(cache=q_cache_with_memories)
        adjustments = [
            {"memory_id": "mem-0002", "action": "override", "reward": 0, "target_q": 0.8, "reason": "manual"},
        ]
        result = apply_adjustments(
            adjustments, RetroLevel.DAILY,
            q_cache_with_memories, updater,
        )
        assert result["applied"] == 1
        q_data = q_cache_with_memories.get("mem-0002")
        assert q_data["q_value"] == pytest.approx(0.8, abs=0.05)

    def test_skip_unknown_memory(self, q_cache_with_memories):
        updater = QValueUpdater(cache=q_cache_with_memories)
        adjustments = [
            {"memory_id": "nonexistent-id", "action": "promote", "reward": 0.3, "reason": "test"},
        ]
        result = apply_adjustments(
            adjustments, RetroLevel.DAILY,
            q_cache_with_memories, updater,
        )
        assert result["applied"] == 0
        assert result["skipped"] == 1

    def test_max_adjustments_cap(self, q_cache_with_memories):
        updater = QValueUpdater(cache=q_cache_with_memories)
        # Create 25 adjustments (over MAX_ADJUSTMENTS=20)
        adjustments = [
            {"memory_id": "mem-0001", "action": "promote", "reward": 0.01, "reason": f"test-{i}"}
            for i in range(25)
        ]
        result = apply_adjustments(
            adjustments, RetroLevel.DAILY,
            q_cache_with_memories, updater,
        )
        assert result["applied"] == 20  # capped

    def test_dry_run(self, q_cache_with_memories):
        updater = QValueUpdater(cache=q_cache_with_memories)
        original_q = q_cache_with_memories.get("mem-0001")["q_value"]
        adjustments = [
            {"memory_id": "mem-0001", "action": "promote", "reward": 0.5, "reason": "test"},
        ]
        result = apply_adjustments(
            adjustments, RetroLevel.DAILY,
            q_cache_with_memories, updater,
            dry_run=True,
        )
        assert result["applied"] == 1
        # Q-value should NOT have changed
        assert q_cache_with_memories.get("mem-0001")["q_value"] == original_q


# ---------------------------------------------------------------------------
# LLM response parsing
# ---------------------------------------------------------------------------

class TestAnalyzeWithLLM:
    def test_valid_json_response(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({
            "summary": "Good day",
            "patterns": ["p1"],
            "adjustments": [],
            "insights": [],
        })

        with patch("subprocess.run", return_value=mock_result):
            result = analyze_with_llm("test prompt")

        assert result is not None
        assert result["summary"] == "Good day"

    def test_json_in_code_block(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '```json\n{"summary": "test", "adjustments": []}\n```'

        with patch("subprocess.run", return_value=mock_result):
            result = analyze_with_llm("test")

        assert result is not None
        assert result["summary"] == "test"

    def test_malformed_json(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not json at all"

        with patch("subprocess.run", return_value=mock_result):
            result = analyze_with_llm("test")

        assert result is None

    def test_empty_response(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            result = analyze_with_llm("test")

        assert result is None

    def test_nonzero_exit(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error"

        with patch("subprocess.run", return_value=mock_result):
            result = analyze_with_llm("test")

        assert result is None

    def test_timeout(self):
        import subprocess as sp
        with patch("subprocess.run", side_effect=sp.TimeoutExpired("claude", 180)):
            result = analyze_with_llm("test")
        assert result is None

    def test_claude_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = analyze_with_llm("test")
        assert result is None


# ---------------------------------------------------------------------------
# Daily Q stats
# ---------------------------------------------------------------------------

class TestDailyQStats:
    def test_save_stats(self, tmp_data_dir):
        cache = QCache()
        for i in range(10):
            cache.set(f"m-{i}", {"q_value": 0.1 * i, "q_action": 0, "q_hypothesis": 0, "q_fit": 0, "q_visits": 0})
        cache_path = tmp_data_dir / "data" / "q_cache.json"
        cache.save(cache_path)

        save_daily_q_stats("2026-04-07")

        stats_path = tmp_data_dir / "data" / "q_stats_daily.jsonl"
        assert stats_path.exists()
        record = json.loads(stats_path.read_text().strip())
        assert record["date"] == "2026-04-07"
        assert record["count"] == 10


# ---------------------------------------------------------------------------
# Idempotency integration
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_already_done_skips(self, tmp_data_dir):
        _mark_done(RetroLevel.DAILY, "2026-04-07", "mem-existing")

        with patch("openexp.retrospective.gather_daily_data") as mock_gather:
            result = run_retrospective(RetroLevel.DAILY, "2026-04-07")

        assert result["status"] == "already_done"
        mock_gather.assert_not_called()

    def test_no_data_returns_early(self, tmp_data_dir):
        result = run_retrospective(RetroLevel.DAILY, "2026-04-07")
        assert result["status"] == "no_data"
