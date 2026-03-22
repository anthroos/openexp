"""Tests for observation filters."""
from openexp.ingest.filters import should_keep


def test_keep_write_operations():
    obs = {"tool": "Write", "summary": "Wrote auth.py"}
    assert should_keep(obs) is True


def test_keep_edit_operations():
    obs = {"tool": "Edit", "summary": "Edited config.py"}
    assert should_keep(obs) is True


def test_filter_readonly_bash():
    obs = {"tool": "Bash", "summary": "Ran: git status", "context": {"command": "git status"}}
    assert should_keep(obs) is False


def test_keep_meaningful_bash():
    obs = {"tool": "Bash", "summary": "Ran: git commit -m 'fix'", "context": {"command": "git commit -m 'fix'"}}
    assert should_keep(obs) is True


def test_filter_short_summary():
    obs = {"tool": "Bash", "summary": "ok"}
    assert should_keep(obs) is False


def test_keep_decisions():
    obs = {"type": "decision", "summary": "Decided to use FastAPI"}
    assert should_keep(obs) is True


def test_keep_valuable_tags():
    obs = {"tool": "Bash", "summary": "some command", "tags": ["deployment"]}
    assert should_keep(obs) is True


def test_filter_grep_command():
    obs = {"tool": "Bash", "summary": "Ran: grep -r 'pattern' .", "context": {"command": "grep -r 'pattern' ."}}
    assert should_keep(obs) is False
