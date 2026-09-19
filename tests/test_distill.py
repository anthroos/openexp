"""Tests for the distill layer (Layer 2).

Distill reads only from the raw store, stamps DISTILL_VERSION, and is
idempotent by (session_id, version): current sessions are skipped, stale
ones are deleted and rewritten.
"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from openexp.ingest.distill import (
    DISTILL_VERSION,
    distill_all,
    distill_session,
)


# Override autouse async fixture from conftest.py
@pytest.fixture(autouse=True)
def cleanup_test_memories():
    yield


def _write_raw(raw_dir, session_id="sess-1"):
    raw_dir.mkdir(parents=True, exist_ok=True)
    p = raw_dir / f"{session_id}.jsonl"
    p.write_text(json.dumps({"type": "user", "message": {"content": "hello there"}}) + "\n")
    return p


def _client_with_version(version):
    """Mock Qdrant client whose scroll reports a session at the given
    distill version (None = no points at all)."""
    client = MagicMock()
    if version is None:
        client.scroll.return_value = ([], None)
    else:
        point = SimpleNamespace(payload={"distill_version": version} if version else {})
        client.scroll.return_value = ([point], None)
    return client


@patch("openexp.ingest.distill.ingest_transcript")
@patch("openexp.ingest.distill.extract_and_store")
@patch("openexp.ingest.distill._get_qdrant")
class TestDistillSession:
    def test_no_raw(self, get_qdrant, extract, ingest, tmp_path):
        result = distill_session("missing", raw_dir=tmp_path)
        assert result["status"] == "no_raw"
        extract.assert_not_called()
        ingest.assert_not_called()

    def test_fresh_session_distills_without_delete(self, get_qdrant, extract, ingest, tmp_path):
        raw = _write_raw(tmp_path)
        client = _client_with_version(None)
        get_qdrant.return_value = client

        result = distill_session("sess-1", raw_dir=tmp_path, experience="exp-x")

        assert result["status"] == "distilled"
        assert result["distill_version"] == DISTILL_VERSION
        client.delete.assert_not_called()
        extract.assert_called_once_with(
            transcript_path=raw, session_id="sess-1",
            experience="exp-x", distill_version=DISTILL_VERSION,
        )
        ingest.assert_called_once_with(
            transcript_path=raw, session_id="sess-1",
            experience="exp-x", force=True, distill_version=DISTILL_VERSION,
        )

    def test_current_version_skipped(self, get_qdrant, extract, ingest, tmp_path):
        _write_raw(tmp_path)
        get_qdrant.return_value = _client_with_version(DISTILL_VERSION)

        result = distill_session("sess-1", raw_dir=tmp_path)

        assert result["status"] == "current"
        extract.assert_not_called()
        ingest.assert_not_called()

    def test_stale_version_deleted_and_redistilled(self, get_qdrant, extract, ingest, tmp_path):
        _write_raw(tmp_path)
        client = _client_with_version(0)  # pre-distill points, no version stamp
        get_qdrant.return_value = client

        result = distill_session("sess-1", raw_dir=tmp_path)

        assert result["status"] == "distilled"
        client.delete.assert_called_once()
        extract.assert_called_once()
        ingest.assert_called_once()

    def test_force_redistills_current(self, get_qdrant, extract, ingest, tmp_path):
        _write_raw(tmp_path)
        client = _client_with_version(DISTILL_VERSION)
        get_qdrant.return_value = client

        result = distill_session("sess-1", raw_dir=tmp_path, force=True)

        assert result["status"] == "distilled"
        client.delete.assert_called_once()

    def test_no_extract_keeps_decisions(self, get_qdrant, extract, ingest, tmp_path):
        _write_raw(tmp_path)
        client = _client_with_version(0)
        get_qdrant.return_value = client

        distill_session("sess-1", raw_dir=tmp_path, extract=False)

        extract.assert_not_called()
        ingest.assert_called_once()
        # Only transcript points deleted — extracted decisions are kept
        delete_filter = client.delete.call_args.kwargs["points_selector"].filter
        source_cond = [c for c in delete_filter.must if c.key == "source"][0]
        assert source_cond.match.any == ["transcript"]

    def test_dry_run_writes_nothing(self, get_qdrant, extract, ingest, tmp_path):
        _write_raw(tmp_path)
        client = _client_with_version(0)
        get_qdrant.return_value = client

        result = distill_session("sess-1", raw_dir=tmp_path, dry_run=True)

        assert result["status"] == "would_distill"
        assert result["existing_version"] == 0
        client.delete.assert_not_called()
        extract.assert_not_called()
        ingest.assert_not_called()


class TestDistillLock:
    def test_locked_session_skipped(self, tmp_path):
        import fcntl
        raw = _write_raw(tmp_path)
        holder = open(raw, "rb")
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            # flock is per-process on the same fd family — simulate the
            # contender via a subprocess so the lock actually conflicts
            import subprocess, sys, json
            code = (
                "import json;"
                "from openexp.ingest.distill import distill_session;"
                f"print(json.dumps(distill_session('sess-1', raw_dir={str(tmp_path)!r})))"
            )
            out = subprocess.run(
                [sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                cwd="/".join(__file__.split("/")[:-2]),
            )
            result = json.loads(out.stdout.strip().splitlines()[-1])
            assert result["status"] == "locked"
        finally:
            fcntl.flock(holder, fcntl.LOCK_UN)
            holder.close()


class TestDistillAll:
    def test_missing_raw_dir(self, tmp_path):
        stats = distill_all(raw_dir=tmp_path / "nope")
        assert stats["found"] == 0

    @patch("openexp.ingest.distill.distill_session")
    def test_iterates_sessions(self, distill_mock, tmp_path):
        _write_raw(tmp_path, "s1")
        _write_raw(tmp_path, "s2")
        # Nested subagent files must not be treated as sessions
        sub = tmp_path / "s1" / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-x.jsonl").write_text("{}\n")
        distill_mock.side_effect = [
            {"status": "distilled"},
            {"status": "current"},
        ]

        stats = distill_all(raw_dir=tmp_path)

        assert stats == {"found": 2, "distilled": 1, "current": 1,
                         "would_distill": 0, "locked": 0, "errors": 0}

    @patch("openexp.ingest.distill.distill_session")
    def test_errors_counted(self, distill_mock, tmp_path):
        _write_raw(tmp_path, "s1")
        distill_mock.side_effect = RuntimeError("qdrant down")

        stats = distill_all(raw_dir=tmp_path)

        assert stats["errors"] == 1

    @patch("openexp.ingest.distill.distill_session")
    def test_since_filters_by_mtime(self, distill_mock, tmp_path):
        import os
        from datetime import datetime, timedelta, timezone
        old = _write_raw(tmp_path, "old")
        _write_raw(tmp_path, "new")
        past = (datetime.now(timezone.utc) - timedelta(days=10)).timestamp()
        os.utime(old, (past, past))
        distill_mock.return_value = {"status": "distilled"}

        cutoff = datetime.now(timezone.utc) - timedelta(days=1)
        stats = distill_all(raw_dir=tmp_path, since=cutoff)

        assert stats["found"] == 1
        assert distill_mock.call_args.args[0] == "new"
