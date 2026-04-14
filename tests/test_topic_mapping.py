"""Tests for topic mapping pipeline."""
import json
import pytest
from unittest.mock import patch, MagicMock
from openexp.ingest.topic_mapping import _format_chunk_for_llm, _extract_topics_llm


class TestFormatChunkForLLM:
    def test_formats_messages(self):
        chunk = {
            "sessions": [{
                "session_id": "abc123",
                "messages": [
                    {"role": "user", "memory": "hello", "created_at": "2026-04-01"},
                    {"role": "assistant", "memory": "hi there", "created_at": "2026-04-01"},
                ],
            }],
        }
        text = _format_chunk_for_llm(chunk)
        assert "USER: hello" in text
        assert "ASSISTANT: hi there" in text
        assert "SESSION abc123" in text

    def test_truncates_at_max_chars(self):
        chunk = {
            "sessions": [{
                "session_id": "s1",
                "messages": [{"role": "user", "memory": "x" * 1000, "created_at": ""}
                             for _ in range(10)],
            }],
        }
        text = _format_chunk_for_llm(chunk, max_chars=3000)
        assert len(text) <= 3500  # some overhead for labels
        assert "truncated" in text

    def test_empty_chunk(self):
        text = _format_chunk_for_llm({"sessions": []})
        assert text == ""

    def test_skips_empty_messages(self):
        chunk = {
            "sessions": [{
                "session_id": "s1",
                "messages": [
                    {"role": "user", "memory": "", "created_at": ""},
                    {"role": "user", "memory": "actual content", "created_at": ""},
                ],
            }],
        }
        text = _format_chunk_for_llm(chunk)
        assert "actual content" in text


class TestExtractTopicsLLM:
    @patch("openexp.ingest.topic_mapping.subprocess.run")
    def test_parses_json_response(self, mock_run):
        topics = [{"name": "Test Topic", "description": "desc", "session_ids": ["s1"], "message_count": 10}]
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(topics),
            stderr="",
        )
        result = _extract_topics_llm("some long text " * 50, chunk_id=1)
        assert len(result) == 1
        assert result[0]["name"] == "Test Topic"

    @patch("openexp.ingest.topic_mapping.subprocess.run")
    def test_handles_markdown_wrapped_json(self, mock_run):
        topics = [{"name": "Topic", "description": "d"}]
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=f"Here are the topics:\n```json\n{json.dumps(topics)}\n```",
            stderr="",
        )
        result = _extract_topics_llm("some text " * 50, chunk_id=1)
        assert len(result) == 1

    @patch("openexp.ingest.topic_mapping.subprocess.run")
    def test_returns_empty_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        result = _extract_topics_llm("some text " * 50, chunk_id=1)
        assert result == []

    def test_returns_empty_for_short_text(self):
        result = _extract_topics_llm("short", chunk_id=1)
        assert result == []

    @patch("openexp.ingest.topic_mapping.subprocess.run")
    def test_handles_invalid_json(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json at all", stderr="")
        result = _extract_topics_llm("some text " * 50, chunk_id=1)
        assert result == []
