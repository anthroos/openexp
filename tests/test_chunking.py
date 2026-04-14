"""Tests for chunking pipeline."""
import pytest
from openexp.ingest.chunking import (
    _group_by_session,
    _sort_sessions_chronologically,
    _split_large_session,
    _session_char_count,
    build_chunks,
)


def _msg(text, session_id="s1", created_at="2026-04-01T10:00:00Z", role="user"):
    return {"id": "1", "memory": text, "session_id": session_id, "created_at": created_at, "role": role}


class TestGroupBySession:
    def test_groups_by_session_id(self):
        points = [_msg("a", session_id="s1"), _msg("b", session_id="s2"), _msg("c", session_id="s1")]
        groups = _group_by_session(points)
        assert len(groups) == 2
        assert len(groups["s1"]) == 2
        assert len(groups["s2"]) == 1

    def test_sorts_messages_within_session(self):
        points = [
            _msg("second", session_id="s1", created_at="2026-04-01T11:00:00Z"),
            _msg("first", session_id="s1", created_at="2026-04-01T10:00:00Z"),
        ]
        groups = _group_by_session(points)
        assert groups["s1"][0]["memory"] == "first"
        assert groups["s1"][1]["memory"] == "second"


class TestSortSessions:
    def test_sorts_by_earliest_message(self):
        sessions = {
            "s2": [_msg("b", session_id="s2", created_at="2026-04-02T10:00:00Z")],
            "s1": [_msg("a", session_id="s1", created_at="2026-04-01T10:00:00Z")],
            "s3": [_msg("c", session_id="s3", created_at="2026-04-03T10:00:00Z")],
        }
        order = _sort_sessions_chronologically(sessions)
        assert order == ["s1", "s2", "s3"]


class TestSplitLargeSession:
    def test_splits_at_boundary(self):
        msgs = [_msg("a" * 100) for _ in range(10)]  # 1000 chars total
        parts = _split_large_session(msgs, max_chars=300)
        assert len(parts) == 4  # 3x300 + 1x100
        assert all(len(p) > 0 for p in parts)

    def test_single_message_exceeding_limit(self):
        msgs = [_msg("a" * 500)]
        parts = _split_large_session(msgs, max_chars=100)
        assert len(parts) == 1  # can't split a single message further


class TestBuildChunks:
    def test_packs_sessions_into_chunks(self):
        sessions = {
            "s1": [_msg("a" * 100, session_id="s1")],
            "s2": [_msg("b" * 100, session_id="s2")],
            "s3": [_msg("c" * 100, session_id="s3")],
        }
        chunks = build_chunks(sessions, ["s1", "s2", "s3"], max_chunk_chars=250)
        assert len(chunks) == 2  # s1+s2 = 200 < 250, s3 = new chunk
        assert chunks[0]["session_count"] == 2
        assert chunks[1]["session_count"] == 1

    def test_large_session_gets_own_chunks(self):
        sessions = {
            "s1": [_msg("a" * 50, session_id="s1")],
            "s2": [_msg("b" * 100, session_id="s2") for _ in range(5)],  # 500 chars
            "s3": [_msg("c" * 50, session_id="s3")],
        }
        chunks = build_chunks(sessions, ["s1", "s2", "s3"], max_chunk_chars=200)
        # s1 fits in one chunk, s2 splits into parts, s3 in last chunk
        assert len(chunks) >= 3

    def test_chunk_has_metadata(self):
        sessions = {"s1": [_msg("hello world", session_id="s1")]}
        chunks = build_chunks(sessions, ["s1"], max_chunk_chars=100000)
        assert len(chunks) == 1
        c = chunks[0]
        assert c["chunk_id"] == 1
        assert c["session_count"] == 1
        assert c["total_messages"] == 1
        assert c["total_chars"] == 11
        assert "date_range" in c

    def test_empty_input(self):
        chunks = build_chunks({}, [], max_chunk_chars=100000)
        assert chunks == []

    def test_never_exceeds_max_chars(self):
        # 10 sessions of 100 chars each, max 250
        sessions = {f"s{i}": [_msg("x" * 100, session_id=f"s{i}")] for i in range(10)}
        sorted_ids = [f"s{i}" for i in range(10)]
        chunks = build_chunks(sessions, sorted_ids, max_chunk_chars=250)
        for c in chunks:
            assert c["total_chars"] <= 250

    def test_chunk_ids_sequential(self):
        sessions = {f"s{i}": [_msg("x" * 100, session_id=f"s{i}")] for i in range(5)}
        sorted_ids = [f"s{i}" for i in range(5)]
        chunks = build_chunks(sessions, sorted_ids, max_chunk_chars=150)
        ids = [c["chunk_id"] for c in chunks]
        assert ids == list(range(1, len(chunks) + 1))
