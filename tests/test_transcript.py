"""Tests for transcript ingest pipeline."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, call
from collections import namedtuple

import pytest

from openexp.ingest.transcript import (
    parse_transcript,
    ingest_transcript,
    _session_already_ingested,
    MAX_MESSAGE_CHARS,
    MIN_MESSAGE_CHARS,
)


# Override autouse async fixture from conftest.py
@pytest.fixture(autouse=True)
def cleanup_test_memories():
    yield


def _write_jsonl(path: Path, entries: list):
    """Write a list of dicts as JSONL."""
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


# ── parse_transcript ────────────────────────────────────────


class TestParseTranscript:
    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty.jsonl"
        p.write_text("")
        assert parse_transcript(p) == []

    def test_nonexistent_file(self, tmp_path):
        p = tmp_path / "nope.jsonl"
        assert parse_transcript(p) == []

    def test_user_message_string_content(self, tmp_path):
        p = tmp_path / "t.jsonl"
        _write_jsonl(p, [
            {"type": "user", "message": {"content": "Hello world"}, "timestamp": "2026-04-08T10:00:00Z", "uuid": "u1", "sessionId": "sess-1"},
        ])
        msgs = parse_transcript(p)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert msgs[0]["text"] == "Hello world"
        assert msgs[0]["session_id"] == "sess-1"

    def test_user_message_list_content(self, tmp_path):
        p = tmp_path / "t.jsonl"
        _write_jsonl(p, [
            {"type": "user", "message": {"content": [{"type": "text", "text": "How are you?"}]}, "timestamp": "2026-04-08T10:00:00Z", "uuid": "u2", "sessionId": "sess-2"},
        ])
        msgs = parse_transcript(p)
        assert len(msgs) == 1
        assert msgs[0]["text"] == "How are you?"

    def test_assistant_message(self, tmp_path):
        p = tmp_path / "t.jsonl"
        _write_jsonl(p, [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "I'm fine, thanks!"}]}, "timestamp": "2026-04-08T10:01:00Z", "uuid": "a1", "sessionId": "sess-1"},
        ])
        msgs = parse_transcript(p)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "assistant"
        assert msgs[0]["text"] == "I'm fine, thanks!"

    def test_filters_system_reminders(self, tmp_path):
        p = tmp_path / "t.jsonl"
        _write_jsonl(p, [
            {"type": "user", "message": {"content": [
                {"type": "text", "text": "<system-reminder>injected stuff</system-reminder>"},
                {"type": "text", "text": "actual user text here"},
            ]}, "timestamp": "2026-04-08T10:00:00Z", "uuid": "u3", "sessionId": "s1"},
        ])
        msgs = parse_transcript(p)
        assert len(msgs) == 1
        assert "system-reminder" not in msgs[0]["text"]
        assert "actual user text" in msgs[0]["text"]

    def test_skips_short_messages(self, tmp_path):
        p = tmp_path / "t.jsonl"
        _write_jsonl(p, [
            {"type": "user", "message": {"content": "hi"}, "timestamp": "", "uuid": "u4", "sessionId": "s1"},
        ])
        msgs = parse_transcript(p)
        assert len(msgs) == 0  # "hi" is < MIN_MESSAGE_CHARS (10)

    def test_truncates_long_messages(self, tmp_path):
        p = tmp_path / "t.jsonl"
        long_text = "x" * (MAX_MESSAGE_CHARS + 1000)
        _write_jsonl(p, [
            {"type": "user", "message": {"content": long_text}, "timestamp": "", "uuid": "u5", "sessionId": "s1"},
        ])
        msgs = parse_transcript(p)
        assert len(msgs) == 1
        assert len(msgs[0]["text"]) == MAX_MESSAGE_CHARS

    def test_skips_non_text_blocks(self, tmp_path):
        """Tool use blocks and thinking blocks should not appear in text."""
        p = tmp_path / "t.jsonl"
        _write_jsonl(p, [
            {"type": "assistant", "message": {"content": [
                {"type": "thinking", "thinking": "let me think..."},
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
                {"type": "text", "text": "Here are the files."},
            ]}, "timestamp": "", "uuid": "a2", "sessionId": "s1"},
        ])
        msgs = parse_transcript(p)
        assert len(msgs) == 1
        assert msgs[0]["text"] == "Here are the files."

    def test_skips_invalid_json_lines(self, tmp_path):
        p = tmp_path / "t.jsonl"
        p.write_text('{"type": "user", "message": {"content": "valid message here"}, "uuid": "u6", "sessionId": "s1"}\n{broken json\n')
        msgs = parse_transcript(p)
        assert len(msgs) == 1

    def test_mixed_user_assistant(self, tmp_path):
        p = tmp_path / "t.jsonl"
        _write_jsonl(p, [
            {"type": "user", "message": {"content": "What is OpenExp?"}, "timestamp": "t1", "uuid": "u1", "sessionId": "s1"},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "OpenExp is a memory system."}]}, "timestamp": "t2", "uuid": "a1", "sessionId": "s1"},
            {"type": "user", "message": {"content": "Tell me more about it"}, "timestamp": "t3", "uuid": "u2", "sessionId": "s1"},
        ])
        msgs = parse_transcript(p)
        assert len(msgs) == 3
        assert [m["role"] for m in msgs] == ["user", "assistant", "user"]

    def test_skips_tool_result_type(self, tmp_path):
        """Entries with type != user/assistant are ignored."""
        p = tmp_path / "t.jsonl"
        _write_jsonl(p, [
            {"type": "tool_result", "content": "some result"},
            {"type": "user", "message": {"content": "actual message here"}, "uuid": "u1", "sessionId": "s1"},
        ])
        msgs = parse_transcript(p)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"


# ── _session_already_ingested ────────────────────────────────


class TestSessionAlreadyIngested:
    def test_returns_true_when_exists(self):
        mock_client = MagicMock()
        CountResult = namedtuple("CountResult", ["count"])
        mock_client.count.return_value = CountResult(count=42)

        result = _session_already_ingested(mock_client, "sess-123")
        assert result is True

    def test_returns_false_when_empty(self):
        mock_client = MagicMock()
        CountResult = namedtuple("CountResult", ["count"])
        mock_client.count.return_value = CountResult(count=0)

        result = _session_already_ingested(mock_client, "sess-456")
        assert result is False

    def test_returns_false_on_error(self):
        mock_client = MagicMock()
        mock_client.count.side_effect = Exception("connection refused")

        result = _session_already_ingested(mock_client, "sess-789")
        assert result is False


# ── ingest_transcript ────────────────────────────────────────


class TestIngestTranscript:
    def test_dry_run(self, tmp_path):
        p = tmp_path / "t.jsonl"
        _write_jsonl(p, [
            {"type": "user", "message": {"content": "Hello world test"}, "uuid": "u1", "sessionId": "s1"},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Hi there, how can I help?"}]}, "uuid": "a1", "sessionId": "s1"},
        ])
        result = ingest_transcript(p, session_id="s1", dry_run=True)
        assert result["dry_run"] is True
        assert result["parsed"] == 2
        assert result["user_messages"] == 1
        assert result["assistant_messages"] == 1

    def test_no_messages(self, tmp_path):
        p = tmp_path / "t.jsonl"
        p.write_text("")
        result = ingest_transcript(p, session_id="s1")
        assert result["stored"] == 0
        assert result["reason"] == "no_messages"

    @patch("openexp.ingest.transcript._get_qdrant")
    @patch("openexp.ingest.transcript._embed")
    def test_stores_messages(self, mock_embed, mock_get_qdrant, tmp_path):
        mock_embed.return_value = [0.1] * 384
        mock_client = MagicMock()
        mock_get_qdrant.return_value = mock_client
        CountResult = namedtuple("CountResult", ["count"])
        mock_client.count.return_value = CountResult(count=0)

        p = tmp_path / "t.jsonl"
        _write_jsonl(p, [
            {"type": "user", "message": {"content": "Test message one here"}, "uuid": "u1", "sessionId": "s1"},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Response message here now"}]}, "uuid": "a1", "sessionId": "s1"},
        ])

        result = ingest_transcript(p, session_id="s1", experience="test")
        assert result["stored"] == 2
        assert result["user_messages"] == 1
        assert result["assistant_messages"] == 1
        assert mock_client.upsert.called

    @patch("openexp.ingest.transcript._get_qdrant")
    @patch("openexp.ingest.transcript._embed")
    def test_skips_already_ingested(self, mock_embed, mock_get_qdrant, tmp_path):
        mock_client = MagicMock()
        mock_get_qdrant.return_value = mock_client
        CountResult = namedtuple("CountResult", ["count"])
        mock_client.count.return_value = CountResult(count=50)  # already exists

        p = tmp_path / "t.jsonl"
        _write_jsonl(p, [
            {"type": "user", "message": {"content": "This should not be stored"}, "uuid": "u1", "sessionId": "s1"},
        ])

        result = ingest_transcript(p, session_id="s1")
        assert result["stored"] == 0
        assert result["reason"] == "already_ingested"
        assert not mock_embed.called  # never even embedded

    @patch("openexp.ingest.transcript._get_qdrant")
    @patch("openexp.ingest.transcript._embed")
    def test_force_reingests(self, mock_embed, mock_get_qdrant, tmp_path):
        mock_embed.return_value = [0.1] * 384
        mock_client = MagicMock()
        mock_get_qdrant.return_value = mock_client
        CountResult = namedtuple("CountResult", ["count"])
        mock_client.count.return_value = CountResult(count=50)  # already exists

        p = tmp_path / "t.jsonl"
        _write_jsonl(p, [
            {"type": "user", "message": {"content": "Force reingest this message"}, "uuid": "u1", "sessionId": "s1"},
        ])

        result = ingest_transcript(p, session_id="s1", force=True)
        assert result["stored"] == 1
        assert mock_embed.called

    @patch("openexp.ingest.transcript._get_qdrant")
    @patch("openexp.ingest.transcript._embed")
    def test_batch_upsert(self, mock_embed, mock_get_qdrant, tmp_path):
        """Verify batch upsert happens at UPSERT_BATCH_SIZE boundary."""
        mock_embed.return_value = [0.1] * 384
        mock_client = MagicMock()
        mock_get_qdrant.return_value = mock_client
        CountResult = namedtuple("CountResult", ["count"])
        mock_client.count.return_value = CountResult(count=0)

        p = tmp_path / "t.jsonl"
        # Create 75 messages (50 batch + 25 remainder)
        entries = []
        for i in range(75):
            entries.append({
                "type": "user",
                "message": {"content": f"Message number {i} with enough text"},
                "uuid": f"u{i}",
                "sessionId": "s1",
            })
        _write_jsonl(p, entries)

        result = ingest_transcript(p, session_id="s1")
        assert result["stored"] == 75
        # Should have 2 upsert calls: batch of 50 + remainder of 25
        assert mock_client.upsert.call_count == 2

    @patch("openexp.ingest.transcript._get_qdrant")
    @patch("openexp.ingest.transcript._embed")
    def test_payload_structure(self, mock_embed, mock_get_qdrant, tmp_path):
        """Verify stored payload has correct fields."""
        mock_embed.return_value = [0.1] * 384
        mock_client = MagicMock()
        mock_get_qdrant.return_value = mock_client
        CountResult = namedtuple("CountResult", ["count"])
        mock_client.count.return_value = CountResult(count=0)

        p = tmp_path / "t.jsonl"
        _write_jsonl(p, [
            {"type": "user", "message": {"content": "Check payload structure here"}, "timestamp": "2026-04-08T10:00:00Z", "uuid": "u1", "sessionId": "s1"},
        ])

        ingest_transcript(p, session_id="s1", experience="sales")

        # Get the points that were upserted
        upsert_call = mock_client.upsert.call_args
        points = upsert_call.kwargs.get("points") or upsert_call[1].get("points") or upsert_call[0][0] if not upsert_call.kwargs else None
        if points is None:
            points = upsert_call.kwargs["points"]

        assert len(points) == 1
        payload = points[0].payload
        assert payload["type"] == "conversation"
        assert payload["role"] == "user"
        assert payload["source"] == "transcript"
        assert payload["session_id"] == "s1"
        assert payload["experience"] == "sales"
        assert payload["status"] == "active"
        assert payload["importance"] == 0.5  # user message
        assert "Check payload" in payload["memory"]
