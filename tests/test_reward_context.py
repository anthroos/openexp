"""Tests for reward context builders across all reward paths."""

from openexp.ingest.reward import _build_session_reward_context
from openexp.reward_tracker import _build_prediction_reward_context
from openexp.outcome import _build_outcome_reward_context, OutcomeEvent


def test_build_session_reward_context_with_commits():
    obs = [
        {"tool": "Bash", "summary": "git commit -m 'fix bug'"},
        {"tool": "Write", "summary": "wrote file"},
        {"tool": "Edit", "summary": "edited file"},
    ]
    ctx = _build_session_reward_context(obs, 0.30)
    assert ctx.startswith("Session +0.30:")
    assert "1 commit" in ctx
    assert "2 writes" in ctx


def test_build_session_reward_context_with_pr():
    obs = [
        {"tool": "Bash", "summary": "gh pr create"},
        {"tool": "Bash", "summary": "git commit -m 'feat'"},
        {"tool": "Bash", "summary": "git commit -m 'test'"},
    ]
    ctx = _build_session_reward_context(obs, 0.50)
    assert "1 PR" in ctx
    assert "2 commits" in ctx


def test_build_session_reward_context_no_output():
    obs = [{"tool": "Read", "summary": "read file"}]
    ctx = _build_session_reward_context(obs, -0.10)
    assert ctx.startswith("Session -0.10:")
    assert "no output" in ctx


def test_build_session_reward_context_negative():
    obs = []
    ctx = _build_session_reward_context(obs, -0.15)
    assert ctx.startswith("Session -0.15:")


def test_build_session_reward_context_with_decisions():
    obs = [
        {"tool": "Write", "summary": "wrote config", "type": "decision"},
    ]
    ctx = _build_session_reward_context(obs, 0.20)
    assert "1 decision" in ctx
    assert "1 write" in ctx


def test_build_prediction_reward_context_positive():
    ctx = _build_prediction_reward_context(
        "SQUAD closes by Friday",
        "closed Wednesday",
        0.80,
    )
    assert ctx.startswith("Pred +0.80:")
    assert "SQUAD closes by Friday" in ctx
    assert "closed Wednesday" in ctx


def test_build_prediction_reward_context_negative():
    ctx = _build_prediction_reward_context(
        "Deal will close",
        "Deal fell through",
        -0.50,
        "strategy_failure",
    )
    assert ctx.startswith("Pred -0.50:")
    assert "[strategy_failure]" in ctx


def test_build_prediction_reward_context_truncates_long_text():
    long_pred = "x" * 100
    long_out = "y" * 100
    ctx = _build_prediction_reward_context(long_pred, long_out, 0.30)
    # Snippets are max 40 chars each
    assert len(ctx) < 200


def test_build_outcome_reward_context_basic():
    event = OutcomeEvent(
        entity_id="comp-squad",
        event_name="deal_closed",
        reward=0.50,
    )
    ctx = _build_outcome_reward_context(event)
    assert ctx.startswith("Biz +0.50:")
    assert "deal_closed" in ctx
    assert "comp-squad" in ctx


def test_build_outcome_reward_context_with_details():
    event = OutcomeEvent(
        entity_id="comp-squad",
        event_name="deal_closed",
        reward=0.50,
        details={"amount": "$8000", "stage": "won"},
    )
    ctx = _build_outcome_reward_context(event)
    assert "amount=$8000" in ctx
    assert "stage=won" in ctx


def test_build_outcome_reward_context_negative():
    event = OutcomeEvent(
        entity_id="comp-xyz",
        event_name="deal_lost",
        reward=-0.30,
    )
    ctx = _build_outcome_reward_context(event)
    assert ctx.startswith("Biz -0.30:")
