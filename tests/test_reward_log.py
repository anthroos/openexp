"""Tests for L3 cold storage reward log."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from openexp.core.reward_log import (
    generate_reward_id,
    log_reward_event,
    get_reward_detail,
    get_reward_history,
    compact_observation,
    REWARD_LOG_PATH,
)


def test_generate_reward_id_format():
    rid = generate_reward_id()
    assert rid.startswith("rwd_")
    assert len(rid) == 12  # "rwd_" + 8 hex chars


def test_generate_reward_id_unique():
    ids = {generate_reward_id() for _ in range(100)}
    assert len(ids) == 100


def test_log_and_get_reward_detail(tmp_path):
    log_path = tmp_path / "reward_log.jsonl"
    with patch("openexp.core.reward_log.REWARD_LOG_PATH", log_path):
        rid = "rwd_test1234"
        log_reward_event(
            reward_id=rid,
            reward_type="session",
            reward=0.30,
            memory_ids=["mem1", "mem2"],
            context={"session_id": "abc", "observations": [{"tool": "Edit"}]},
        )

        record = get_reward_detail(rid)
        assert record is not None
        assert record["reward_id"] == rid
        assert record["reward_type"] == "session"
        assert record["reward"] == 0.30
        assert record["memory_ids"] == ["mem1", "mem2"]
        assert record["context"]["session_id"] == "abc"


def test_get_reward_detail_not_found(tmp_path):
    log_path = tmp_path / "reward_log.jsonl"
    with patch("openexp.core.reward_log.REWARD_LOG_PATH", log_path):
        assert get_reward_detail("rwd_nonexist") is None


def test_get_reward_detail_empty_file(tmp_path):
    log_path = tmp_path / "reward_log.jsonl"
    log_path.touch()
    with patch("openexp.core.reward_log.REWARD_LOG_PATH", log_path):
        assert get_reward_detail("rwd_anything") is None


def test_get_reward_history(tmp_path):
    log_path = tmp_path / "reward_log.jsonl"
    with patch("openexp.core.reward_log.REWARD_LOG_PATH", log_path):
        log_reward_event("rwd_a", "session", 0.30, ["mem1", "mem2"], {"s": 1})
        log_reward_event("rwd_b", "prediction", 0.80, ["mem1"], {"p": 2})
        log_reward_event("rwd_c", "business", 0.50, ["mem3"], {"b": 3})

        history = get_reward_history("mem1")
        assert len(history) == 2
        assert history[0]["reward_id"] == "rwd_a"
        assert history[1]["reward_id"] == "rwd_b"

        history3 = get_reward_history("mem3")
        assert len(history3) == 1
        assert history3[0]["reward_id"] == "rwd_c"

        history_none = get_reward_history("mem_nonexistent")
        assert history_none == []


def test_get_reward_history_no_file(tmp_path):
    log_path = tmp_path / "reward_log.jsonl"
    with patch("openexp.core.reward_log.REWARD_LOG_PATH", log_path):
        assert get_reward_history("mem1") == []


def test_large_context_preserved(tmp_path):
    log_path = tmp_path / "reward_log.jsonl"
    with patch("openexp.core.reward_log.REWARD_LOG_PATH", log_path):
        large_context = {
            "observations": [{"id": f"obs_{i}", "tool": "Edit", "summary": f"edit #{i}"} for i in range(50)],
            "extra_data": "x" * 5000,
        }
        log_reward_event("rwd_big", "session", 0.40, ["m1"], large_context)

        record = get_reward_detail("rwd_big")
        assert record is not None
        assert len(record["context"]["observations"]) == 50
        assert len(record["context"]["extra_data"]) == 5000


def test_compact_observation():
    full_obs = {
        "id": "obs-123",
        "tool": "Edit",
        "summary": "Edited q_value.py",
        "type": "code_change",
        "context": {"file_path": "/foo/bar.py", "other_stuff": "ignored"},
        "tags": ["python", "core"],
        "raw_content": "lots of content that should be dropped",
    }
    compact = compact_observation(full_obs)
    assert compact == {
        "id": "obs-123",
        "tool": "Edit",
        "summary": "Edited q_value.py",
        "type": "code_change",
        "file_path": "/foo/bar.py",
        "tags": ["python", "core"],
    }


def test_compact_observation_missing_fields():
    compact = compact_observation({})
    assert compact["id"] is None
    assert compact["tool"] is None
    assert compact["file_path"] is None
    assert compact["tags"] == []


def test_multiple_reward_events_append(tmp_path):
    log_path = tmp_path / "reward_log.jsonl"
    with patch("openexp.core.reward_log.REWARD_LOG_PATH", log_path):
        for i in range(10):
            log_reward_event(f"rwd_{i:08x}", "session", 0.1 * i, [f"mem_{i}"], {"i": i})

        # Verify all 10 lines
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 10

        # Verify first and last
        first = json.loads(lines[0])
        assert first["reward_id"] == "rwd_00000000"
        last = json.loads(lines[9])
        assert last["reward_id"] == "rwd_00000009"
