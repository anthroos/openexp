"""Tests for the raw transcript capture store (Layer 1).

Capture never thinks: copies must be byte-exact, with no truncation
or filtering of any kind.
"""
import json
from pathlib import Path

import pytest

from openexp.ingest.raw_store import archive_transcript, backfill, read_raw


# Override autouse async fixture from conftest.py
@pytest.fixture(autouse=True)
def cleanup_test_memories():
    yield


def _write_transcript(path: Path, n_messages: int = 3, text_len: int = 100) -> bytes:
    """Write a synthetic transcript and return its exact bytes."""
    lines = []
    for i in range(n_messages):
        lines.append(json.dumps({
            "type": "user" if i % 2 == 0 else "assistant",
            "message": {"content": [{"type": "text", "text": "x" * text_len}]},
            "uuid": f"msg-{i}",
            "sessionId": "sess-1",
        }))
    # Include a tool result and a malformed line — raw store must keep both
    lines.append(json.dumps({"type": "tool_result", "content": "raw tool output " * 50}))
    lines.append("{not valid json")
    data = ("\n".join(lines) + "\n").encode("utf-8")
    path.write_bytes(data)
    return data


class TestArchiveTranscript:
    def test_byte_exact_copy(self, tmp_path):
        src = tmp_path / "src" / "sess-1.jsonl"
        src.parent.mkdir()
        data = _write_transcript(src, text_len=20000)  # far past MAX_MESSAGE_CHARS
        raw_dir = tmp_path / "raw"

        dest = archive_transcript(src, "sess-1", raw_dir=raw_dir)

        assert dest == raw_dir / "sess-1.jsonl"
        assert dest.read_bytes() == data

    def test_missing_source_returns_none(self, tmp_path):
        assert archive_transcript(tmp_path / "nope.jsonl", "sess-x", raw_dir=tmp_path / "raw") is None

    def test_idempotent_skip(self, tmp_path):
        src = tmp_path / "sess-1.jsonl"
        _write_transcript(src)
        raw_dir = tmp_path / "raw"

        dest = archive_transcript(src, "sess-1", raw_dir=raw_dir)
        first_mtime = dest.stat().st_mtime_ns
        archive_transcript(src, "sess-1", raw_dir=raw_dir)

        assert dest.stat().st_mtime_ns == first_mtime

    def test_recopy_when_source_grew(self, tmp_path):
        src = tmp_path / "sess-1.jsonl"
        _write_transcript(src)
        raw_dir = tmp_path / "raw"
        archive_transcript(src, "sess-1", raw_dir=raw_dir)

        with open(src, "a") as f:
            f.write(json.dumps({"type": "user", "message": {"content": "later message"}}) + "\n")
        dest = archive_transcript(src, "sess-1", raw_dir=raw_dir)

        assert dest.read_bytes() == src.read_bytes()

    def test_no_tmp_leftovers(self, tmp_path):
        src = tmp_path / "sess-1.jsonl"
        _write_transcript(src)
        raw_dir = tmp_path / "raw"

        archive_transcript(src, "sess-1", raw_dir=raw_dir)

        assert [p.name for p in raw_dir.iterdir()] == ["sess-1.jsonl"]

    def test_archives_subagent_transcripts(self, tmp_path):
        project = tmp_path / "project"
        src = project / "sess-1.jsonl"
        subagents = project / "sess-1" / "subagents"
        subagents.mkdir(parents=True)
        _write_transcript(src)
        agent_data = _write_transcript(subagents / "agent-abc.jsonl", n_messages=1)
        raw_dir = tmp_path / "raw"

        archive_transcript(src, "sess-1", raw_dir=raw_dir)

        archived_agent = raw_dir / "sess-1" / "subagents" / "agent-abc.jsonl"
        assert archived_agent.read_bytes() == agent_data


class TestReadRaw:
    def _make_raw(self, tmp_path, n=10):
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        lines = [json.dumps({"type": "user", "message": {"content": f"message number {i}"}})
                 for i in range(n)]
        (raw_dir / "sess-1.jsonl").write_text("\n".join(lines) + "\n")
        return raw_dir, lines

    def test_missing_session(self, tmp_path):
        result = read_raw("nope", raw_dir=tmp_path)
        assert result == {"session_id": "nope", "found": False, "lines": []}

    def test_full_read(self, tmp_path):
        raw_dir, lines = self._make_raw(tmp_path)
        result = read_raw("sess-1", raw_dir=raw_dir)
        assert result["found"] is True
        assert result["lines"] == lines
        assert result["total_lines"] == 10

    def test_grep_with_context(self, tmp_path):
        raw_dir, lines = self._make_raw(tmp_path)
        result = read_raw("sess-1", raw_dir=raw_dir, grep="NUMBER 5", context=1)
        assert result["lines"] == lines[4:7]

    def test_grep_overlapping_matches_no_duplicates(self, tmp_path):
        raw_dir, lines = self._make_raw(tmp_path)
        result = read_raw("sess-1", raw_dir=raw_dir, grep="number", context=2)
        assert result["lines"] == lines  # every line matches, each returned once

    def test_grep_no_matches(self, tmp_path):
        raw_dir, _ = self._make_raw(tmp_path)
        result = read_raw("sess-1", raw_dir=raw_dir, grep="zzz-absent")
        assert result["lines"] == []


class TestBackfill:
    def _make_projects(self, tmp_path) -> Path:
        projects = tmp_path / "projects"
        for proj, sessions in [("proj-a", ["s1", "s2"]), ("proj-b", ["s3"])]:
            d = projects / proj
            d.mkdir(parents=True)
            for s in sessions:
                _write_transcript(d / f"{s}.jsonl")
        # Nested subagent transcript
        sub = projects / "proj-a" / "s1" / "subagents"
        sub.mkdir(parents=True)
        _write_transcript(sub / "agent-xyz.jsonl", n_messages=1)
        return projects

    def test_backfill_copies_all(self, tmp_path):
        projects = self._make_projects(tmp_path)
        raw_dir = tmp_path / "raw"

        stats = backfill(projects_dir=projects, raw_dir=raw_dir)

        assert stats == {"found": 4, "archived": 4, "skipped": 0, "errors": 0}
        for rel in ["s1.jsonl", "s2.jsonl", "s3.jsonl", "s1/subagents/agent-xyz.jsonl"]:
            assert (raw_dir / rel).exists()
        src = projects / "proj-a" / "s1.jsonl"
        assert (raw_dir / "s1.jsonl").read_bytes() == src.read_bytes()

    def test_backfill_rerun_skips(self, tmp_path):
        projects = self._make_projects(tmp_path)
        raw_dir = tmp_path / "raw"
        backfill(projects_dir=projects, raw_dir=raw_dir)

        stats = backfill(projects_dir=projects, raw_dir=raw_dir)

        assert stats == {"found": 4, "archived": 0, "skipped": 4, "errors": 0}

    def test_dry_run_copies_nothing(self, tmp_path):
        projects = self._make_projects(tmp_path)
        raw_dir = tmp_path / "raw"

        stats = backfill(projects_dir=projects, raw_dir=raw_dir, dry_run=True)

        assert stats["found"] == 4
        assert stats["archived"] == 0
        assert not raw_dir.exists()

    def test_missing_projects_dir(self, tmp_path):
        stats = backfill(projects_dir=tmp_path / "nope", raw_dir=tmp_path / "raw")
        assert stats == {"found": 0, "archived": 0, "skipped": 0, "errors": 0}
