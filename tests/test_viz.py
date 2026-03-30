"""Tests for OpenExp visualization data export."""
import argparse
import json
import re
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from openexp.viz import (
    _histogram, _parse_date, _sanitize, _redact, _classify_step,
    _build_conversation, _build_beats, _summarize_actions, _truncate,
    export_viz_data, export_replay_data, generate_demo_replay,
)


class TestHistogram:
    def test_basic_binning(self):
        values = [0.0, 0.1, 0.2, 0.5, 0.9, 1.0]
        result = _histogram(values, bin_start=0, bin_end=1.0, num_bins=10)
        assert len(result["histogram"]) == 10
        assert sum(b["count"] for b in result["histogram"]) == len(values)

    def test_stats(self):
        values = [0.0, 0.5, 1.0]
        result = _histogram(values)
        assert result["stats"]["min"] == 0.0
        assert result["stats"]["max"] == 1.0
        assert result["stats"]["count"] == 3

    def test_empty_values(self):
        result = _histogram([])
        assert result["histogram"] == []
        assert result["stats"] == {}

    def test_single_value(self):
        result = _histogram([0.5])
        assert result["stats"]["mean"] == 0.5
        assert result["stats"]["std"] == 0

    def test_negative_values(self):
        values = [-0.5, -0.3, 0.0, 0.5]
        result = _histogram(values, bin_start=-0.5, bin_end=1.0, num_bins=15)
        assert sum(b["count"] for b in result["histogram"]) == len(values)

    def test_all_same_value(self):
        values = [0.5, 0.5, 0.5]
        result = _histogram(values)
        assert sum(b["count"] for b in result["histogram"]) == 3
        assert result["stats"]["mean"] == 0.5


class TestParseDate:
    def test_iso_timestamp(self):
        assert _parse_date("2026-03-20T17:41:11.837715+00:00") == "2026-03-20"

    def test_date_only(self):
        assert _parse_date("2026-03-20") == "2026-03-20"

    def test_none(self):
        assert _parse_date(None) is None

    def test_empty(self):
        assert _parse_date("") is None


class TestSanitize:
    def test_clean_data_passes(self):
        data = {"key": "hello", "nested": {"list": [1, 2, "safe"]}}
        _sanitize(data)

    def test_file_path_caught(self):
        with pytest.raises(ValueError, match="Sensitive data"):
            _sanitize({"key": "/Users/someone/secret"})

    def test_api_key_caught(self):
        with pytest.raises(ValueError, match="Sensitive data"):
            _sanitize({"key": "sk-ant-abc123"})

    def test_long_api_key_caught(self):
        with pytest.raises(ValueError, match="Sensitive data"):
            _sanitize({"key": "sk-abcdefghijklmnopqrstuvwxyz"})

    def test_numeric_values_ok(self):
        data = {"q": 0.5, "count": 100, "nested": [1, 2, 3]}
        _sanitize(data)

    def test_deep_nesting(self):
        with pytest.raises(ValueError):
            _sanitize({"a": {"b": {"c": ["/Users/test/path"]}}})


class TestExportVizData:
    def _make_q_cache(self, tmp_path, entries=None):
        """Write a Q-cache JSON file and return its path."""
        cache_path = tmp_path / "q_cache.json"
        cache_path.write_text(json.dumps(entries or {}))
        return cache_path

    def test_empty_q_cache(self, tmp_path):
        """Export with empty Q-cache should produce valid structure."""
        cache_path = self._make_q_cache(tmp_path)
        obs_dir = tmp_path / "obs"
        obs_dir.mkdir()
        sess_dir = tmp_path / "sess"
        sess_dir.mkdir()

        with patch("openexp.core.config.Q_CACHE_PATH", cache_path), \
             patch("openexp.core.config.DATA_DIR", tmp_path), \
             patch("openexp.core.config.OBSERVATIONS_DIR", obs_dir), \
             patch("openexp.core.config.SESSIONS_DIR", sess_dir):
            data = export_viz_data(no_qdrant=True)

        assert data["meta"]["total_memories"] == 0
        assert data["q_distribution"]["combined"]["histogram"] == []
        assert data["q_evolution"] == []
        assert data["lifecycle"] == {}

    def test_with_q_values(self, tmp_path):
        """Export with sample Q-values produces correct distribution."""
        entries = {
            "id1": {"default": {"q_value": 0.5, "q_action": 0.6, "q_hypothesis": 0.4, "q_fit": 0.5,
                    "q_visits": 2, "q_updated_at": "2026-03-20T10:00:00", "calibration": "neutral"}},
            "id2": {"default": {"q_value": 0.3, "q_action": 0.3, "q_hypothesis": 0.3, "q_fit": 0.3,
                    "q_visits": 1, "q_updated_at": "2026-03-21T10:00:00", "calibration": "valuable"}},
        }
        cache_path = self._make_q_cache(tmp_path, entries)
        obs_dir = tmp_path / "obs"
        obs_dir.mkdir()
        sess_dir = tmp_path / "sess"
        sess_dir.mkdir()

        with patch("openexp.core.config.Q_CACHE_PATH", cache_path), \
             patch("openexp.core.config.DATA_DIR", tmp_path), \
             patch("openexp.core.config.OBSERVATIONS_DIR", obs_dir), \
             patch("openexp.core.config.SESSIONS_DIR", sess_dir):
            data = export_viz_data(no_qdrant=True)

        assert data["meta"]["total_memories"] == 2
        assert data["q_distribution"]["combined"]["stats"]["count"] == 2
        assert len(data["q_evolution"]) == 2
        assert data["calibration_counts"]["neutral"] == 1
        assert data["calibration_counts"]["valuable"] == 1

    def test_output_is_json_serializable(self, tmp_path):
        """Exported data must be JSON-serializable."""
        cache_path = self._make_q_cache(tmp_path)
        obs_dir = tmp_path / "obs"
        obs_dir.mkdir()
        sess_dir = tmp_path / "sess"
        sess_dir.mkdir()

        with patch("openexp.core.config.Q_CACHE_PATH", cache_path), \
             patch("openexp.core.config.DATA_DIR", tmp_path), \
             patch("openexp.core.config.OBSERVATIONS_DIR", obs_dir), \
             patch("openexp.core.config.SESSIONS_DIR", sess_dir):
            data = export_viz_data(no_qdrant=True)

        json_str = json.dumps(data, default=str)
        assert len(json_str) > 0

    def test_with_observations(self, tmp_path):
        """Observation files should be counted by line."""
        cache_path = self._make_q_cache(tmp_path)
        obs_dir = tmp_path / "obs"
        obs_dir.mkdir()
        sess_dir = tmp_path / "sess"
        sess_dir.mkdir()

        # Create a fake observations file
        obs_file = obs_dir / "observations-2026-03-20.jsonl"
        obs_file.write_text('{"a":1}\n{"b":2}\n{"c":3}\n')

        with patch("openexp.core.config.Q_CACHE_PATH", cache_path), \
             patch("openexp.core.config.DATA_DIR", tmp_path), \
             patch("openexp.core.config.OBSERVATIONS_DIR", obs_dir), \
             patch("openexp.core.config.SESSIONS_DIR", sess_dir):
            data = export_viz_data(no_qdrant=True)

        assert len(data["observations_timeline"]) == 1
        assert data["observations_timeline"][0]["observations_count"] == 3
        assert data["meta"]["total_observations"] == 3


class TestCLIIntegration:
    def test_viz_subparser_exists(self):
        """CLI should have cmd_viz function."""
        import openexp.cli as cli_mod
        assert hasattr(cli_mod, "cmd_viz")

    def test_viz_output_file(self, tmp_path):
        """cmd_viz should create output HTML file."""
        output = tmp_path / "test-viz.html"
        cache_path = tmp_path / "q_cache.json"
        cache_path.write_text("{}")
        obs_dir = tmp_path / "obs"
        obs_dir.mkdir()
        sess_dir = tmp_path / "sess"
        sess_dir.mkdir()

        with patch("openexp.core.config.Q_CACHE_PATH", cache_path), \
             patch("openexp.core.config.DATA_DIR", tmp_path), \
             patch("openexp.core.config.OBSERVATIONS_DIR", obs_dir), \
             patch("openexp.core.config.SESSIONS_DIR", sess_dir), \
             patch("webbrowser.open"):
            from openexp.cli import cmd_viz
            args = argparse.Namespace(output=str(output), no_open=True, no_qdrant=True, replay=None)
            cmd_viz(args)

        assert output.exists()
        content = output.read_text()
        assert "VIZ_DATA" in content
        assert "OpenExp" in content
        assert not re.search(r"/Users/\w+", content)

    def test_viz_replay_flag(self, tmp_path):
        """cmd_viz with --replay should use replay template."""
        cache_path = tmp_path / "q_cache.json"
        cache_path.write_text("{}")
        obs_dir = tmp_path / "obs"
        obs_dir.mkdir()
        sess_dir = tmp_path / "sess"
        sess_dir.mkdir()

        # Create fake observation for session abc12345
        obs_file = obs_dir / "observations-2026-03-20.jsonl"
        obs_file.write_text(json.dumps({
            "id": "obs-1", "timestamp": "2026-03-20T10:00:00Z",
            "session_id": "abc12345-xxxx", "type": "feature",
            "tool": "Bash", "summary": "Ran: echo hello", "project": "test",
        }) + "\n")

        output = tmp_path / "test-replay.html"

        with patch("openexp.core.config.Q_CACHE_PATH", cache_path), \
             patch("openexp.core.config.DATA_DIR", tmp_path), \
             patch("openexp.core.config.OBSERVATIONS_DIR", obs_dir), \
             patch("openexp.core.config.SESSIONS_DIR", sess_dir), \
             patch("webbrowser.open"):
            from openexp.cli import cmd_viz
            args = argparse.Namespace(
                output=str(output), no_open=True, no_qdrant=True, replay="abc12345",
            )
            cmd_viz(args)

        # Output goes to the specified path when --output is given
        assert output.exists()
        content = output.read_text()
        assert "REPLAY_DATA" in content
        assert "Session Replay" in content


class TestRedact:
    def test_redact_file_path(self):
        assert "/~/..." in _redact("Ran: cat /Users/someone/file.txt")

    def test_redact_email(self):
        result = _redact("from:anna@example.com")
        assert "anna@" not in result
        assert "an***@example.com" in result

    def test_redact_api_key(self):
        assert "sk-***" in _redact("key: sk-ant-abc123def456")

    def test_clean_text_unchanged(self):
        assert _redact("hello world") == "hello world"

    def test_empty(self):
        assert _redact("") == ""
        assert _redact(None) == ""


class TestClassifyStep:
    def test_scan_inbox(self):
        assert _classify_step({"summary": "read_emails.py 15 is:unread"})[0] == "scan_inbox"

    def test_send_email(self):
        assert _classify_step({"summary": "send_email.py --to someone"})[0] == "send_email"

    def test_search_email(self):
        assert _classify_step({"summary": "read_emails.py subject:meeting"})[0] == "search_email"

    def test_crm(self):
        assert _classify_step({"summary": "grep crm/leads.csv"})[0] == "crm"

    def test_generic(self):
        assert _classify_step({"summary": "ls -la", "tool": "Bash"})[0] == "action"


class TestExportReplayData:
    def test_with_observations(self, tmp_path):
        """Replay export should build timeline from observations."""
        cache_path = tmp_path / "q_cache.json"
        cache_path.write_text("{}")
        obs_dir = tmp_path / "obs"
        obs_dir.mkdir()
        sess_dir = tmp_path / "sess"
        sess_dir.mkdir()

        obs = [
            {"id": "obs-1", "timestamp": "2026-03-20T10:00:00Z",
             "session_id": "test1234-abcd", "type": "feature",
             "tool": "Bash", "summary": "Ran: read_emails.py is:unread", "project": "test"},
            {"id": "obs-2", "timestamp": "2026-03-20T10:01:00Z",
             "session_id": "test1234-abcd", "type": "outreach",
             "tool": "Bash", "summary": "Ran: send_email.py --to x@test.com", "project": "test"},
        ]
        obs_file = obs_dir / "observations-2026-03-20.jsonl"
        obs_file.write_text("\n".join(json.dumps(o) for o in obs) + "\n")

        with patch("openexp.core.config.Q_CACHE_PATH", cache_path), \
             patch("openexp.core.config.DATA_DIR", tmp_path), \
             patch("openexp.core.config.OBSERVATIONS_DIR", obs_dir), \
             patch("openexp.core.config.SESSIONS_DIR", sess_dir):
            data = export_replay_data("test1234")

        assert "error" not in data
        assert data["meta"]["total_observations"] == 2
        assert data["meta"]["session_id"] == "test1234"
        # Steps: session_start(if retrievals) + 2 obs + session_end = 3 (no retrievals)
        assert data["steps"][-1]["type"] == "session_end"
        assert "beats" in data
        assert isinstance(data["beats"], list)
        assert len(data["beats"]) >= 2  # at least start + end

    def test_no_observations(self, tmp_path):
        """Missing session should return error."""
        cache_path = tmp_path / "q_cache.json"
        cache_path.write_text("{}")
        obs_dir = tmp_path / "obs"
        obs_dir.mkdir()
        sess_dir = tmp_path / "sess"
        sess_dir.mkdir()

        with patch("openexp.core.config.Q_CACHE_PATH", cache_path), \
             patch("openexp.core.config.DATA_DIR", tmp_path), \
             patch("openexp.core.config.OBSERVATIONS_DIR", obs_dir), \
             patch("openexp.core.config.SESSIONS_DIR", sess_dir):
            data = export_replay_data("nonexistent")

        assert "error" in data

    def test_sanitization(self, tmp_path):
        """Replay output should not contain file paths."""
        cache_path = tmp_path / "q_cache.json"
        cache_path.write_text("{}")
        obs_dir = tmp_path / "obs"
        obs_dir.mkdir()
        sess_dir = tmp_path / "sess"
        sess_dir.mkdir()

        obs = [
            {"id": "obs-1", "timestamp": "2026-03-20T10:00:00Z",
             "session_id": "sanitize-test", "type": "feature",
             "tool": "Bash", "summary": "Ran: cat /Users/someone/secret.txt", "project": "test"},
        ]
        obs_file = obs_dir / "observations-2026-03-20.jsonl"
        obs_file.write_text(json.dumps(obs[0]) + "\n")

        with patch("openexp.core.config.Q_CACHE_PATH", cache_path), \
             patch("openexp.core.config.DATA_DIR", tmp_path), \
             patch("openexp.core.config.OBSERVATIONS_DIR", obs_dir), \
             patch("openexp.core.config.SESSIONS_DIR", sess_dir):
            data = export_replay_data("sanitize-test")

        # Should pass sanitization (paths redacted)
        json_str = json.dumps(data, default=str)
        assert "/Users/someone" not in json_str


class TestBuildConversation:
    def test_basic_conversation(self):
        """Should produce user + assistant messages from retrievals and observations."""
        retrievals = [
            {"timestamp": "2026-03-20T10:00:00Z", "query": "session start context",
             "memory_ids": [], "scores": []},
            {"timestamp": "2026-03-20T10:01:00Z", "query": "check inbox for new emails",
             "memory_ids": [], "scores": []},
        ]
        steps = [
            {"index": 0, "timestamp": "2026-03-20T10:00:00Z", "type": "session_start",
             "label": "Session Start", "phase": "recall"},
            {"index": 1, "timestamp": "2026-03-20T10:01:30Z", "type": "scan_inbox",
             "label": "Scanning inbox", "phase": "work", "tool": "Bash"},
            {"index": 2, "timestamp": "2026-03-20T10:02:00Z", "type": "session_end",
             "label": "Session End", "phase": "reward"},
        ]
        obs = [
            {"summary": "Ran: read_emails.py 15 is:unread", "tool": "Bash", "type": "feature"},
        ]

        result = _build_conversation(retrievals, steps, obs)

        roles = [m["role"] for m in result]
        assert "system" in roles
        assert "user" in roles
        assert "assistant" in roles

    def test_empty_retrievals(self):
        """No retrievals should produce only system messages."""
        steps = [
            {"index": 0, "timestamp": "2026-03-20T10:00:00Z", "type": "scan_inbox",
             "label": "Scanning", "phase": "work", "tool": "Bash"},
        ]
        obs = [{"summary": "Ran: ls", "tool": "Bash", "type": "feature"}]

        result = _build_conversation([], steps, obs)
        # Should have system start + assistant action + system end
        assert any(m["role"] == "system" for m in result)

    def test_redaction_in_conversation(self):
        """File paths and emails should be redacted in conversation."""
        retrievals = [
            {"timestamp": "2026-03-20T10:00:00Z", "query": "auto",
             "memory_ids": [], "scores": []},
            {"timestamp": "2026-03-20T10:01:00Z",
             "query": "read /Users/someone/secret.txt and email alice@example.com",
             "memory_ids": [], "scores": []},
        ]
        steps = [
            {"index": 0, "timestamp": "2026-03-20T10:00:00Z", "type": "session_start",
             "label": "Start", "phase": "recall"},
            {"index": 1, "timestamp": "2026-03-20T10:02:00Z", "type": "action",
             "label": "Working", "phase": "work", "tool": "Bash"},
        ]
        obs = [{"summary": "Ran: cat file", "tool": "Bash", "type": "feature"}]

        result = _build_conversation(retrievals, steps, obs)
        all_text = " ".join(m["text"] for m in result)
        assert "/Users/someone" not in all_text
        assert "alice@example.com" not in all_text

    def test_conversation_in_replay_output(self, tmp_path):
        """export_replay_data should include conversation field."""
        cache_path = tmp_path / "q_cache.json"
        cache_path.write_text("{}")
        obs_dir = tmp_path / "obs"
        obs_dir.mkdir()
        sess_dir = tmp_path / "sess"
        sess_dir.mkdir()

        obs = [
            {"id": "obs-1", "timestamp": "2026-03-20T10:00:00Z",
             "session_id": "conv-test-1234", "type": "feature",
             "tool": "Bash", "summary": "Ran: read_emails.py is:unread", "project": "test"},
        ]
        obs_file = obs_dir / "observations-2026-03-20.jsonl"
        obs_file.write_text(json.dumps(obs[0]) + "\n")

        with patch("openexp.core.config.Q_CACHE_PATH", cache_path), \
             patch("openexp.core.config.DATA_DIR", tmp_path), \
             patch("openexp.core.config.OBSERVATIONS_DIR", obs_dir), \
             patch("openexp.core.config.SESSIONS_DIR", sess_dir):
            data = export_replay_data("conv-test")

        assert "conversation" in data
        assert isinstance(data["conversation"], list)


class TestTruncate:
    def test_short_text(self):
        assert _truncate("hello", 10) == "hello"

    def test_long_text(self):
        result = _truncate("a" * 200, 50)
        assert len(result) == 50
        assert result.endswith("…")

    def test_none(self):
        assert _truncate(None) == ""

    def test_empty(self):
        assert _truncate("") == ""


class TestSummarizeActions:
    def test_single_action(self):
        result = _summarize_actions(["scan_inbox"])
        assert "checking the inbox" in result
        assert result.startswith("I'll handle this by")

    def test_multiple_actions(self):
        result = _summarize_actions(["scan_inbox", "read_email", "check_sent"])
        assert "checking the inbox" in result
        assert "reading the email thread" in result
        assert " and " in result

    def test_empty(self):
        assert _summarize_actions([]) == "Working on it."

    def test_deduplication(self):
        result = _summarize_actions(["scan_inbox", "scan_inbox", "read_email"])
        assert result.count("checking the inbox") == 1


class TestBuildBeats:
    def _make_steps_and_conv(self, num_obs=3, user_msgs=None):
        """Helper to create steps and conversation for beat testing."""
        steps = [
            {"index": 0, "timestamp": "2026-03-20T10:00:00Z", "type": "session_start",
             "label": "Session Start", "phase": "recall",
             "memories_recalled": [{"id": "mem1", "score": 0.8, "q_combined": 0.5}]},
        ]
        obs = []
        for i in range(num_obs):
            steps.append({
                "index": i + 1, "timestamp": f"2026-03-20T10:0{i+1}:00Z",
                "type": "scan_inbox" if i == 0 else "read_email" if i == 1 else "send_email",
                "label": "Scanning inbox" if i == 0 else "Reading email" if i == 1 else "Sending email",
                "description": f"action {i}", "tool": "Bash", "phase": "work",
                "memories_recalled": [{"id": f"mem{i+2}", "score": 0.7, "q_combined": 0.4}] if i == 0 else [],
            })
            obs.append({"summary": f"action {i}", "tool": "Bash", "type": "feature"})

        steps.append({
            "index": len(steps), "timestamp": "2026-03-20T10:10:00Z",
            "type": "session_end", "label": "Session End", "phase": "reward",
            "reward_info": {"memories_updated": 5, "alpha": 0.25},
        })

        conversation = [
            {"step_index": 0, "role": "system", "text": "Session started."},
        ]
        if user_msgs:
            for step_idx, text in user_msgs:
                conversation.append({"step_index": step_idx, "role": "user", "text": text})
        conversation.append({"step_index": len(steps) - 1, "role": "system",
                             "text": "Session complete."})
        return steps, conversation, obs

    def test_basic_beat_grouping(self):
        """Steps group around user messages, has start/end."""
        steps, conv, obs = self._make_steps_and_conv(
            num_obs=3, user_msgs=[(1, "Check the inbox?")])
        beats = _build_beats(steps, conv, obs)

        assert beats[0]["type"] == "system_start"
        assert beats[-1]["type"] == "system_end"
        assert any(b["type"] == "user_turn" for b in beats)

    def test_two_user_messages_create_two_beats(self):
        """Each user msg = new beat."""
        steps, conv, obs = self._make_steps_and_conv(
            num_obs=4, user_msgs=[(1, "Check inbox?"), (3, "OK, send it.")])
        beats = _build_beats(steps, conv, obs)

        user_beats = [b for b in beats if b["type"] == "user_turn"]
        assert len(user_beats) == 2
        assert user_beats[0]["conversation"][0]["text"] == "Check inbox?"
        assert user_beats[1]["conversation"][0]["text"] == "OK, send it."

    def test_empty_conversation(self):
        """Still produces start + end beats even with no user messages."""
        steps, conv, obs = self._make_steps_and_conv(num_obs=2, user_msgs=None)
        beats = _build_beats(steps, conv, obs)

        assert len(beats) >= 2
        assert beats[0]["type"] == "system_start"
        assert beats[-1]["type"] == "system_end"

    def test_beat_memories_deduplicated(self):
        """Same memory across steps counted once per beat."""
        steps = [
            {"index": 0, "type": "session_start", "timestamp": "T0", "phase": "recall",
             "memories_recalled": [{"id": "m1", "score": 0.9, "q_combined": 0.5}]},
            {"index": 1, "type": "scan_inbox", "timestamp": "T1", "phase": "work",
             "label": "Scan", "description": "scan", "tool": "Bash",
             "memories_recalled": [{"id": "m2", "score": 0.8, "q_combined": 0.4}]},
            {"index": 2, "type": "read_email", "timestamp": "T2", "phase": "work",
             "label": "Read", "description": "read", "tool": "Bash",
             "memories_recalled": [{"id": "m2", "score": 0.8, "q_combined": 0.4}]},
            {"index": 3, "type": "session_end", "timestamp": "T3", "phase": "reward",
             "label": "End", "reward_info": {"memories_updated": 2, "alpha": 0.25}},
        ]
        conv = [
            {"step_index": 0, "role": "system", "text": "Started."},
            {"step_index": 3, "role": "system", "text": "Done."},
        ]
        obs = [{"summary": "scan", "tool": "Bash"}, {"summary": "read", "tool": "Bash"}]

        beats = _build_beats(steps, conv, obs)
        # The auto beat should have m2 only once
        auto_beat = [b for b in beats if b["type"] == "auto"][0]
        mem_ids = [m["id"] for m in auto_beat["memories_recalled"]]
        assert mem_ids.count("m2") == 1

    def test_beat_actions_preserve_order(self):
        """Actions match step order."""
        steps, conv, obs = self._make_steps_and_conv(
            num_obs=3, user_msgs=[(1, "Do it")])
        beats = _build_beats(steps, conv, obs)

        user_beat = [b for b in beats if b["type"] == "user_turn"][0]
        indices = [a["step_index"] for a in user_beat["actions"]]
        assert indices == sorted(indices)

    def test_sanitization_of_beats(self, tmp_path):
        """Beat data should pass _sanitize()."""
        steps, conv, obs = self._make_steps_and_conv(
            num_obs=2, user_msgs=[(1, "Check it")])
        beats = _build_beats(steps, conv, obs)
        # Should not raise
        _sanitize({"beats": beats})

    def test_summarize_actions_readable(self):
        """Summary should produce readable English."""
        result = _summarize_actions(["scan_inbox", "read_email"])
        assert "I'll" in result
        assert result.endswith(".")

    def test_duration_hint_scales(self):
        """More actions = longer hint."""
        steps_short, conv_s, obs_s = self._make_steps_and_conv(
            num_obs=1, user_msgs=[(1, "Go")])
        steps_long, conv_l, obs_l = self._make_steps_and_conv(
            num_obs=5, user_msgs=[(1, "Go")])
        beats_short = _build_beats(steps_short, conv_s, obs_s)
        beats_long = _build_beats(steps_long, conv_l, obs_l)

        # Find user_turn beats
        short_beat = [b for b in beats_short if b["type"] == "user_turn"][0]
        long_beat = [b for b in beats_long if b["type"] == "user_turn"][0]
        assert long_beat["duration_hint"] >= short_beat["duration_hint"]


class TestDemoReplay:
    def test_generate_demo_replay_structure(self):
        data = generate_demo_replay()
        assert data["meta"]["demo"] is True
        assert data["meta"]["session_id"] == "demo0001"
        assert len(data["beats"]) == 4
        assert data["beats"][0]["type"] == "system_start"
        assert data["beats"][1]["type"] == "user_turn"
        assert data["beats"][2]["type"] == "user_turn"
        assert data["beats"][3]["type"] == "system_end"

    def test_demo_has_rich_conversation(self):
        data = generate_demo_replay()
        beat1 = data["beats"][1]
        conv = beat1["conversation"]
        assert len(conv) >= 5
        types = [c.get("content_type", "text") for c in conv]
        assert "email_card" in types
        assert "memory_results" in types

    def test_demo_has_flow_events(self):
        data = generate_demo_replay()
        beat1 = data["beats"][1]
        for c in beat1["conversation"]:
            assert "flow" in c

    def test_demo_has_q_values(self):
        data = generate_demo_replay()
        assert len(data["memory_q_values"]) == 5
        for mid, q in data["memory_q_values"].items():
            assert "combined" in q
            assert "combined_before" in q
            assert q["reward_direction"] == "positive"

    def test_demo_is_json_serializable(self):
        data = generate_demo_replay()
        json.dumps(data, default=str)

    def test_demo_no_sensitive_data(self):
        data = generate_demo_replay()
        _sanitize(data)
