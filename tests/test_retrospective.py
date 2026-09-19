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
    analyze_with_llm,
    run_retrospective,
)


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """Set up temp dirs for all data paths."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()

    monkeypatch.setattr("openexp.retrospective.DATA_DIR", data_dir)
    monkeypatch.setattr("openexp.retrospective.WATERMARK_PATH", data_dir / "retrospective_watermark.json")
    monkeypatch.setattr("openexp.retrospective.SESSIONS_DIR", sessions_dir)

    return tmp_path


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

class TestIdempotency:
    def test_already_done_skips(self, tmp_data_dir):
        _mark_done(RetroLevel.DAILY, "2026-04-07", "mem-existing")

        with patch("openexp.retrospective.gather_daily_data") as mock_gather:
            result = run_retrospective(RetroLevel.DAILY, "2026-04-07")

        assert result["status"] == "already_done"
        mock_gather.assert_not_called()

    def test_no_data_returns_early(self, tmp_data_dir):
        empty = {"date": "2026-04-07", "sessions": [], "memories": []}
        with patch("openexp.retrospective.gather_daily_data", return_value=empty):
            result = run_retrospective(RetroLevel.DAILY, "2026-04-07")
        assert result["status"] == "no_data"
