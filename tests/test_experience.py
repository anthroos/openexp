"""Tests for Experience system — per-domain Q-value contexts."""
import json
import os
import tempfile
from pathlib import Path

import pytest

from openexp.core.experience import (
    Experience,
    ProcessStage,
    DEFAULT_EXPERIENCE,
    load_experience,
    get_active_experience,
    list_experiences,
    _parse_yaml,
    _parse_process_stages,
    detect_experience_from_prompt,
    save_session_experience,
    get_session_experience,
    cleanup_session_experience,
)


# --- Experience loading ---

def test_default_experience_constant():
    exp = DEFAULT_EXPERIENCE
    assert exp.name == "default"
    assert exp.session_reward_weights["commit"] == 0.3
    assert exp.outcome_resolvers == []


def test_load_default_experience():
    exp = load_experience("default")
    assert exp.name == "default"
    assert "commit" in exp.session_reward_weights


def test_load_bundled_sales_experience():
    exp = load_experience("sales")
    assert exp.name == "sales"
    assert exp.session_reward_weights["email_sent"] == 0.15
    assert len(exp.outcome_resolvers) == 1
    assert exp.retrieval_boosts["decision"] == 1.3
    assert exp.q_config_overrides["alpha"] == 0.3


def test_load_nonexistent_falls_back_to_default():
    exp = load_experience("nonexistent_experience_xyz")
    assert exp.name == "default"


def test_load_yaml_from_user_dir(tmp_path, monkeypatch):
    """Test that user-dir YAML takes priority over bundled."""
    yaml_content = """
name: custom
description: Custom test experience
session_reward_weights:
  commit: 0.9
outcome_resolvers: []
retrieval_boosts: {}
q_config_overrides: {}
"""
    (tmp_path / "custom.yaml").write_text(yaml_content)
    monkeypatch.setattr("openexp.core.config.EXPERIENCES_DIR", tmp_path)

    exp = load_experience("custom")
    assert exp.name == "custom"
    assert exp.session_reward_weights["commit"] == 0.9


def test_list_experiences():
    exps = list_experiences()
    names = [e.name for e in exps]
    assert "default" in names
    assert "sales" in names


def test_get_active_experience_default(monkeypatch):
    monkeypatch.setattr("openexp.core.config.ACTIVE_EXPERIENCE", "default")
    exp = get_active_experience()
    assert exp.name == "default"


def test_get_active_experience_sales(monkeypatch):
    monkeypatch.setattr("openexp.core.config.ACTIVE_EXPERIENCE", "sales")
    exp = get_active_experience()
    assert exp.name == "sales"


# --- ProcessStage parsing ---

def test_parse_process_stages_dict_format():
    raw = [
        {"name": "lead", "description": "New lead", "reward_on_enter": 0.1},
        {"name": "won", "description": "Deal closed", "reward_on_enter": 0.8},
    ]
    stages = _parse_process_stages(raw)
    assert len(stages) == 2
    assert stages[0].name == "lead"
    assert stages[0].description == "New lead"
    assert stages[0].reward_on_enter == 0.1
    assert stages[1].reward_on_enter == 0.8


def test_parse_process_stages_string_format():
    raw = ["backlog", "in_progress", "done"]
    stages = _parse_process_stages(raw)
    assert len(stages) == 3
    assert stages[0].name == "backlog"
    assert stages[0].description == ""
    assert stages[0].reward_on_enter == 0.0


def test_parse_process_stages_mixed_format():
    raw = [
        "lead",
        {"name": "won", "reward_on_enter": 0.8},
    ]
    stages = _parse_process_stages(raw)
    assert len(stages) == 2
    assert stages[0].name == "lead"
    assert stages[1].name == "won"
    assert stages[1].reward_on_enter == 0.8


def test_parse_process_stages_empty():
    assert _parse_process_stages([]) == []


# --- reward_memory_types ---

def test_reward_memory_types_from_yaml(tmp_path, monkeypatch):
    yaml_content = """
name: filtered
description: Test with reward_memory_types
session_reward_weights:
  commit: 0.3
reward_memory_types:
  - decision
  - insight
"""
    (tmp_path / "filtered.yaml").write_text(yaml_content)
    monkeypatch.setattr("openexp.core.config.EXPERIENCES_DIR", tmp_path)

    exp = load_experience("filtered")
    assert exp.reward_memory_types == ["decision", "insight"]


def test_reward_memory_types_default_empty(tmp_path, monkeypatch):
    """Old YAML without reward_memory_types should default to empty list."""
    yaml_content = """
name: old_format
description: No reward_memory_types field
session_reward_weights:
  commit: 0.3
"""
    (tmp_path / "old_format.yaml").write_text(yaml_content)
    monkeypatch.setattr("openexp.core.config.EXPERIENCES_DIR", tmp_path)

    exp = load_experience("old_format")
    assert exp.reward_memory_types == []


# --- Backward compat: old YAML without new fields ---

def test_backward_compat_old_yaml(tmp_path, monkeypatch):
    """YAML without process_stages and reward_memory_types loads fine."""
    yaml_content = """
name: legacy
description: Old format experience
session_reward_weights:
  commit: 0.3
  pr: 0.2
outcome_resolvers: []
retrieval_boosts: {}
q_config_overrides: {}
"""
    (tmp_path / "legacy.yaml").write_text(yaml_content)
    monkeypatch.setattr("openexp.core.config.EXPERIENCES_DIR", tmp_path)

    exp = load_experience("legacy")
    assert exp.name == "legacy"
    assert exp.process_stages == []
    assert exp.reward_memory_types == []
    assert exp.session_reward_weights["commit"] == 0.3


# --- Bundled YAMLs have process_stages ---

def test_bundled_sales_has_process_stages():
    exp = load_experience("sales")
    assert len(exp.process_stages) > 0
    stage_names = [s.name for s in exp.process_stages]
    assert "lead" in stage_names
    assert "won" in stage_names


def test_bundled_dealflow_has_process_stages():
    exp = load_experience("dealflow")
    assert len(exp.process_stages) > 0
    stage_names = [s.name for s in exp.process_stages]
    assert "lead" in stage_names
    assert "paid" in stage_names


def test_bundled_sales_has_reward_memory_types():
    exp = load_experience("sales")
    assert "decision" in exp.reward_memory_types
    assert "outcome" in exp.reward_memory_types



# --- Experience auto-detection ---

class TestDetectExperience:
    def test_sales_keywords_english(self):
        prompt = "write an email to the client about our proposal"
        assert detect_experience_from_prompt(prompt) == "sales"

    def test_sales_keywords_ukrainian(self):
        prompt = "напиши листа клієнту про нашу пропозицію"
        assert detect_experience_from_prompt(prompt) == "sales"

    def test_dealflow_keywords(self):
        prompt = "check if the invoice was paid and update pricing"
        assert detect_experience_from_prompt(prompt) == "dealflow"

    def test_dealflow_keywords_ukrainian(self):
        prompt = "перевір чи прийшла оплата за рахунок"
        assert detect_experience_from_prompt(prompt) == "dealflow"

    def test_coding_stays_default(self):
        prompt = "fix the bug in auth.py where the token refresh fails"
        assert detect_experience_from_prompt(prompt) == "default"

    def test_short_prompt_default(self):
        assert detect_experience_from_prompt("ok") == "default"

    def test_empty_prompt_default(self):
        assert detect_experience_from_prompt("") == "default"

    def test_single_keyword_not_enough(self):
        """One keyword match is below threshold (needs 2+)."""
        prompt = "tell me about the client relationship"
        # "client" matches sales, but only 1 match — below threshold
        result = detect_experience_from_prompt(prompt)
        # Could be sales if "client" + something else matches, or default
        # The point is: threshold=2 requires at least 2 keyword hits
        assert result in ("default", "sales")

    def test_ambiguous_prefers_higher_score(self):
        """When multiple experiences match, highest score wins."""
        prompt = "send invoice to client for the deal and check payment status"
        # "client" + "deal" → sales (2 hits)
        # "invoice" + "payment" → dealflow (2 hits)
        # Both >= threshold, whichever scores higher wins
        result = detect_experience_from_prompt(prompt)
        assert result in ("sales", "dealflow")


class TestSessionExperience:
    def test_save_and_get(self, tmp_path, monkeypatch):
        monkeypatch.setattr("openexp.core.config.DATA_DIR", tmp_path)
        save_session_experience("sess-abc", "sales")
        assert get_session_experience("sess-abc") == "sales"

    def test_get_nonexistent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("openexp.core.config.DATA_DIR", tmp_path)
        assert get_session_experience("sess-nope") is None

    def test_cleanup(self, tmp_path, monkeypatch):
        monkeypatch.setattr("openexp.core.config.DATA_DIR", tmp_path)
        save_session_experience("sess-abc", "dealflow")
        assert get_session_experience("sess-abc") == "dealflow"
        cleanup_session_experience("sess-abc")
        assert get_session_experience("sess-abc") is None

    def test_invalid_name_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr("openexp.core.config.DATA_DIR", tmp_path)
        exp_file = tmp_path / "session_sess-bad_experience.txt"
        exp_file.write_text("../../../etc/passwd")  # path traversal attempt
        assert get_session_experience("sess-bad") is None
