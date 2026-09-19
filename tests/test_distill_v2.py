"""Tests for the v2 compressor: dedup, junk filter, chunked extraction."""
import json
from unittest.mock import patch, MagicMock

import pytest

from openexp.ingest.transcript import ingest_transcript
from openexp.ingest.extract_decisions import (
    EXTRACT_CONTEXT_LIMIT,
    EXTRACT_MAX_CHUNKS,
    extract_and_store,
    read_transcript,
)
from openexp.core.hybrid_search import hybrid_search
from openexp.ingest.distill import DISTILL_VERSION


# Override autouse async fixture from conftest.py
@pytest.fixture(autouse=True)
def cleanup_test_memories():
    yield


def _msg(text, uuid, role="user"):
    return {"type": role, "message": {"content": [{"type": "text", "text": text}]},
            "timestamp": "2026-07-03T00:00:00Z", "uuid": uuid, "sessionId": "s1"}


def _write(path, entries):
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def test_distill_version_bumped():
    assert DISTILL_VERSION == 2


class TestIngestDedup:
    def test_dedup_counts(self, tmp_path):
        p = tmp_path / "s1.jsonl"
        _write(p, [
            _msg("a perfectly reasonable long message", "u1"),
            _msg("another distinct long message here", "u2"),
            _msg("a perfectly reasonable long message", "u3"),
        ])
        with patch("openexp.ingest.transcript._get_qdrant") as gq, \
             patch("openexp.ingest.transcript._embed", return_value=[0.0] * 384):
            client = MagicMock()
            gq.return_value = client
            result = ingest_transcript(p, "s1", force=True)

        assert result["stored"] == 2
        assert result["duplicates_skipped"] == 1


class TestSearchDedup:
    def test_identical_content_collapsed(self):
        results = [
            {"memory": "same text repeated across sessions", "score": 0.9,
             "payload": {}, "metadata": {}},
            {"memory": "same text repeated across sessions", "score": 0.8,
             "payload": {}, "metadata": {}},
            {"memory": "a unique different result", "score": 0.7,
             "payload": {}, "metadata": {}},
        ]
        out = hybrid_search("same text", results, top_k=10)
        texts = [r["memory"] for r in out]
        assert len(texts) == 2
        assert texts.count("same text repeated across sessions") == 1


class TestChunkedExtraction:
    def test_short_transcript_single_chunk(self, tmp_path):
        p = tmp_path / "s1.jsonl"
        _write(p, [_msg("short session with one meaningful exchange", "u1")])
        chunks = read_transcript(p)
        assert len(chunks) == 1

    def test_long_transcript_covers_start_and_end(self, tmp_path):
        p = tmp_path / "s1.jsonl"
        # Enough text for > EXTRACT_MAX_CHUNKS chunks
        block = "x" * 1000
        entries = [_msg(f"START marker message {block}", "u0")]
        entries += [_msg(f"middle {i} {block}", f"u{i+1}")
                    for i in range((EXTRACT_CONTEXT_LIMIT * (EXTRACT_MAX_CHUNKS + 2)) // 1000)]
        entries += [_msg(f"END marker message {block}", "u-end")]
        _write(p, entries)

        chunks = read_transcript(p)

        assert len(chunks) == EXTRACT_MAX_CHUNKS
        assert "START marker" in chunks[0]
        assert "END marker" in chunks[-1]
        assert all(len(c) <= EXTRACT_CONTEXT_LIMIT + 2000 for c in chunks)

    def test_extract_and_store_merges_and_dedupes(self, tmp_path):
        p = tmp_path / "s1.jsonl"
        block = "y" * 1000
        entries = [_msg(f"msg {i} {block}", f"u{i}")
                   for i in range((EXTRACT_CONTEXT_LIMIT * 2) // 1000 + 5)]
        _write(p, entries)

        # Each chunk "extracts" the same decision + one unique per chunk
        calls = []

        def fake_extract(chunk, session_id, experience):
            calls.append(chunk)
            return [
                {"type": "decision", "content": "shared decision", "importance": 0.5},
                {"type": "insight", "content": f"unique insight {len(calls)}", "importance": 0.5},
            ]

        with patch("openexp.ingest.extract_decisions.extract_decisions", side_effect=fake_extract):
            result = extract_and_store(p, "s1", dry_run=True)

        assert len(calls) >= 2  # whole transcript covered, not just the tail
        assert result["extracted"] == 1 + len(calls)  # shared deduped
