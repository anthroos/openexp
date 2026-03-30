"""Tests for L4 — LLM-generated reward explanations."""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from openexp.core.explanation import (
    generate_reward_explanation,
    _build_explanation_prompt,
    fetch_memory_contents,
    _fetch_memory_contents,
)


@pytest.fixture(autouse=True)
def cleanup_test_memories():
    yield


class TestBuildExplanationPrompt:
    def test_session_prompt(self):
        prompt = _build_explanation_prompt(
            reward_type="session",
            reward=0.30,
            context={"reward_breakdown": {"commits": 2, "prs": 1}},
            memory_contents={"mem-1": "architecture note about Q-cache"},
            q_before=0.50,
            q_after=0.58,
        )
        assert "Q-value: 0.50 → 0.58" in prompt
        assert "Reward: +0.30" in prompt
        assert "architecture note" in prompt
        assert "commits" in prompt

    def test_prediction_prompt(self):
        prompt = _build_explanation_prompt(
            reward_type="prediction",
            reward=0.80,
            context={
                "prediction": "SQUAD will sign contract",
                "outcome": "Contract signed",
                "confidence": 0.7,
            },
            memory_contents={"mem-1": "SQUAD meeting notes"},
            q_before=0.30,
            q_after=0.50,
        )
        assert "SQUAD will sign contract" in prompt
        assert "Contract signed" in prompt
        assert "0.7" in prompt

    def test_business_prompt(self):
        prompt = _build_explanation_prompt(
            reward_type="business",
            reward=0.50,
            context={
                "entity_id": "comp-squad",
                "event_name": "deal_closed",
                "details": {"amount": 8000},
            },
            memory_contents={},
            q_before=0.20,
            q_after=0.33,
        )
        assert "deal_closed" in prompt
        assert "comp-squad" in prompt

    def test_calibration_prompt(self):
        prompt = _build_explanation_prompt(
            reward_type="calibration",
            reward=0.80,
            context={
                "old_q_value": 0.30,
                "new_q_value": 0.80,
                "reason": "high value insight",
            },
            memory_contents={"mem-1": "important decision"},
            q_before=0.30,
            q_after=0.80,
        )
        assert "0.30 → 0.80" in prompt
        assert "high value insight" in prompt

    def test_summary_prompt(self):
        prompt = _build_explanation_prompt(
            reward_type="summary",
            reward=0.80,
            context={
                "total_events": 5,
                "total_reward": 0.80,
                "events_summary": [{"type": "session", "reward": 0.30}],
            },
            memory_contents={"mem-1": "important note"},
            q_before=None,
            q_after=0.65,
        )
        assert "reward-" in prompt  # "reward-подій"
        assert "important note" in prompt
        # q_line should NOT appear (q_before is None)
        assert "Q-value:" not in prompt

    def test_q_line_omitted_when_unknown(self):
        prompt = _build_explanation_prompt(
            reward_type="session",
            reward=0.30,
            context={"reward_breakdown": {"commits": 2}},
            memory_contents={},
            q_before=None,
            q_after=None,
        )
        assert "Q-value:" not in prompt
        assert "Reward: +0.30" in prompt

    def test_unknown_type_fallback(self):
        prompt = _build_explanation_prompt(
            reward_type="unknown_future_type",
            reward=0.10,
            context={"foo": "bar"},
            memory_contents={},
            q_before=0.0,
            q_after=0.03,
        )
        assert "unknown_future_type" in prompt

    def test_memory_contents_truncated(self):
        long_content = "x" * 500
        prompt = _build_explanation_prompt(
            reward_type="session",
            reward=0.10,
            context={},
            memory_contents={"mem-1": long_content},
            q_before=0.0,
            q_after=0.03,
        )
        # Content should be truncated to 200 chars in prompt
        assert "x" * 200 in prompt
        assert "x" * 201 not in prompt

    def test_max_5_memories_in_prompt(self):
        contents = {f"mem-{i}": f"content-{i}" for i in range(10)}
        prompt = _build_explanation_prompt(
            reward_type="session",
            reward=0.10,
            context={},
            memory_contents=contents,
            q_before=0.0,
            q_after=0.03,
        )
        # Only first 5 should appear
        assert "mem-4" in prompt
        assert "mem-5" not in prompt


class TestGenerateRewardExplanation:
    def test_returns_explanation_with_mock_api(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="This memory helped because it contained architecture decisions.")]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("openexp.core.explanation._anthropic_client", mock_client), \
             patch("openexp.core.explanation.generate_reward_explanation.__module__", "openexp.core.explanation"):
            # Patch config values
            with patch("openexp.core.config.EXPLANATION_ENABLED", True), \
                 patch("openexp.core.config.ANTHROPIC_API_KEY", "sk-test-key"):
                result = generate_reward_explanation(
                    reward_type="session",
                    reward=0.30,
                    context={"reward_breakdown": {"commits": 2}},
                    memory_contents={"mem-1": "arch note"},
                )

        assert result is not None
        assert "architecture decisions" in result

    def test_disabled_returns_none(self):
        with patch("openexp.core.config.EXPLANATION_ENABLED", False):
            result = generate_reward_explanation(
                reward_type="session",
                reward=0.30,
                context={},
            )
        assert result is None

    def test_no_api_key_returns_none(self):
        with patch("openexp.core.config.EXPLANATION_ENABLED", True), \
             patch("openexp.core.config.ANTHROPIC_API_KEY", ""):
            result = generate_reward_explanation(
                reward_type="session",
                reward=0.30,
                context={},
            )
        assert result is None

    def test_api_failure_returns_none(self):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API error")

        with patch("openexp.core.explanation._anthropic_client", mock_client), \
             patch("openexp.core.config.EXPLANATION_ENABLED", True), \
             patch("openexp.core.config.ANTHROPIC_API_KEY", "sk-test-key"):
            result = generate_reward_explanation(
                reward_type="session",
                reward=0.30,
                context={},
            )
        assert result is None

    def test_explanation_capped_at_500_chars(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="a" * 1000)]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("openexp.core.explanation._anthropic_client", mock_client), \
             patch("openexp.core.config.EXPLANATION_ENABLED", True), \
             patch("openexp.core.config.ANTHROPIC_API_KEY", "sk-test-key"):
            result = generate_reward_explanation(
                reward_type="session",
                reward=0.30,
                context={},
            )
        assert result is not None
        assert len(result) == 500


class TestFetchMemoryContents:
    def test_public_alias_works(self):
        """fetch_memory_contents and _fetch_memory_contents are the same."""
        assert fetch_memory_contents is _fetch_memory_contents

    def test_empty_ids_returns_empty(self):
        assert _fetch_memory_contents([]) == {}

    def test_qdrant_failure_returns_empty(self):
        with patch("openexp.core.direct_search._get_qdrant", side_effect=Exception("connection refused")):
            result = _fetch_memory_contents(["mem-1", "mem-2"])
        assert result == {}

    def test_fetches_from_qdrant(self):
        mock_point = MagicMock()
        mock_point.id = "mem-1"
        mock_point.payload = {"content": "important decision about architecture"}

        mock_qc = MagicMock()
        mock_qc.retrieve.return_value = [mock_point]

        with patch("openexp.core.direct_search._get_qdrant", return_value=mock_qc):
            result = _fetch_memory_contents(["mem-1"])

        assert "mem-1" in result
        assert "important decision" in result["mem-1"]

    def test_limit_respected(self):
        mock_qc = MagicMock()
        mock_qc.retrieve.return_value = []

        with patch("openexp.core.direct_search._get_qdrant", return_value=mock_qc):
            _fetch_memory_contents(["m1", "m2", "m3", "m4", "m5", "m6", "m7"], limit=3)

        # Should only request 3 IDs
        call_args = mock_qc.retrieve.call_args
        assert len(call_args.kwargs.get("ids", call_args[1].get("ids", []))) == 3


class TestL3RecordExplanationField:
    def test_explanation_in_l3_record(self, tmp_path):
        from openexp.core.reward_log import log_reward_event, get_reward_detail

        log_path = tmp_path / "reward_log.jsonl"
        with patch("openexp.core.reward_log.REWARD_LOG_PATH", log_path):
            log_reward_event(
                reward_id="rwd_test0001",
                reward_type="session",
                reward=0.30,
                memory_ids=["mem1"],
                context={"session_id": "abc"},
                explanation="Memory helped with architecture decision.",
            )

            record = get_reward_detail("rwd_test0001")
            assert record is not None
            assert record["explanation"] == "Memory helped with architecture decision."

    def test_no_explanation_backward_compat(self, tmp_path):
        from openexp.core.reward_log import log_reward_event, get_reward_detail

        log_path = tmp_path / "reward_log.jsonl"
        with patch("openexp.core.reward_log.REWARD_LOG_PATH", log_path):
            # Old-style call without explanation
            log_reward_event(
                reward_id="rwd_old00001",
                reward_type="session",
                reward=0.20,
                memory_ids=["mem1"],
                context={},
            )

            record = get_reward_detail("rwd_old00001")
            assert record is not None
            assert "explanation" not in record

    def test_explanation_none_not_stored(self, tmp_path):
        from openexp.core.reward_log import log_reward_event, get_reward_detail

        log_path = tmp_path / "reward_log.jsonl"
        with patch("openexp.core.reward_log.REWARD_LOG_PATH", log_path):
            log_reward_event(
                reward_id="rwd_none0001",
                reward_type="session",
                reward=0.20,
                memory_ids=["mem1"],
                context={},
                explanation=None,
            )

            record = get_reward_detail("rwd_none0001")
            assert record is not None
            assert "explanation" not in record


class TestExplainQTool:
    """Test explain_q MCP tool handler logic."""

    def test_explain_q_collects_explanations(self, tmp_path):
        from openexp.core.reward_log import log_reward_event, get_reward_history

        log_path = tmp_path / "reward_log.jsonl"
        with patch("openexp.core.reward_log.REWARD_LOG_PATH", log_path):
            log_reward_event("rwd_a", "session", 0.30, ["mem1"], {}, explanation="First explanation")
            log_reward_event("rwd_b", "prediction", 0.50, ["mem1"], {}, explanation="Second explanation")
            log_reward_event("rwd_c", "session", 0.10, ["mem1"], {})  # no explanation

            history = get_reward_history("mem1")

        explanations = [r.get("explanation") for r in history if r.get("explanation")]
        assert len(explanations) == 2
        assert "First explanation" in explanations
        assert "Second explanation" in explanations

    def test_explain_q_regenerate_calls_llm(self, tmp_path):
        """Test that explain_q with regenerate=true calls LLM to generate overall_summary."""
        from openexp.core.reward_log import log_reward_event, get_reward_history
        from openexp.core.explanation import generate_reward_explanation

        log_path = tmp_path / "reward_log.jsonl"
        with patch("openexp.core.reward_log.REWARD_LOG_PATH", log_path):
            log_reward_event("rwd_x", "session", 0.30, ["mem1"], {}, explanation="Sess explanation")
            log_reward_event("rwd_y", "prediction", 0.50, ["mem1"], {}, explanation="Pred explanation")

            cold_records = get_reward_history("mem1")

        # Mock LLM call for summary regeneration
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Overall: this memory was consistently valuable.")]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("openexp.core.explanation._anthropic_client", mock_client), \
             patch("openexp.core.config.EXPLANATION_ENABLED", True), \
             patch("openexp.core.config.ANTHROPIC_API_KEY", "sk-test-key"), \
             patch("openexp.core.explanation.fetch_memory_contents", return_value={"mem1": "test content"}):
            summary = generate_reward_explanation(
                reward_type="summary",
                reward=0.80,
                context={
                    "total_events": len(cold_records),
                    "total_reward": 0.80,
                    "events_summary": [
                        {"type": r.get("reward_type"), "reward": r.get("reward")}
                        for r in cold_records
                    ],
                },
                memory_contents={"mem1": "test content"},
                q_after=0.65,
                experience="default",
            )

        assert summary is not None
        assert "consistently valuable" in summary
        # Verify LLM was called with summary prompt
        call_args = mock_client.messages.create.call_args
        prompt = call_args.kwargs.get("messages", call_args[1].get("messages", []))[0]["content"]
        assert "reward-" in prompt  # Ukrainian "reward-подій"


class TestIntegrationSessionRewardExplanation:
    """Integration: apply_session_reward generates and stores explanation."""

    def test_session_reward_generates_explanation(self, tmp_path):
        from openexp.core.q_value import QCache
        from openexp.ingest.reward import apply_session_reward

        q_cache = QCache()
        log_path = tmp_path / "reward_log.jsonl"

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Session was productive with 2 commits.")]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("openexp.core.explanation._anthropic_client", mock_client), \
             patch("openexp.core.config.EXPLANATION_ENABLED", True), \
             patch("openexp.core.config.ANTHROPIC_API_KEY", "sk-test-key"), \
             patch("openexp.core.explanation.fetch_memory_contents", return_value={}), \
             patch("openexp.core.reward_log.REWARD_LOG_PATH", log_path), \
             patch("openexp.core.config.Q_CACHE_PATH", tmp_path / "q_cache.json"):
            apply_session_reward(
                point_ids=["mem-1", "mem-2"],
                reward=0.30,
                q_cache=q_cache,
                observations=[
                    {"tool": "Bash", "summary": "git commit -m 'fix'"},
                    {"tool": "Write", "summary": "wrote file.py"},
                ],
                session_id="test-session",
            )

        # Verify explanation was generated (LLM was called)
        assert mock_client.messages.create.called

        # Verify L3 record has explanation
        from openexp.core.reward_log import get_reward_history
        with patch("openexp.core.reward_log.REWARD_LOG_PATH", log_path):
            records = get_reward_history("mem-1")
        assert len(records) >= 1
        assert records[0].get("explanation") == "Session was productive with 2 commits."

    def test_session_reward_passes_q_before_q_after(self, tmp_path):
        """Verify that q_before/q_after are passed to explanation generator."""
        from openexp.core.q_value import QCache
        from openexp.ingest.reward import apply_session_reward

        q_cache = QCache()
        # Pre-seed a Q-value so q_before is not None
        q_cache.set("mem-1", {"q_value": 0.40, "q_action": 0.40, "q_hypothesis": 0.40, "q_fit": 0.40, "q_visits": 1}, "default")

        log_path = tmp_path / "reward_log.jsonl"
        captured_kwargs = {}

        def capture_explanation(**kwargs):
            captured_kwargs.update(kwargs)
            return "test explanation"

        with patch("openexp.ingest.reward.generate_reward_explanation", side_effect=capture_explanation), \
             patch("openexp.core.reward_log.REWARD_LOG_PATH", log_path), \
             patch("openexp.core.config.Q_CACHE_PATH", tmp_path / "q_cache.json"):
            apply_session_reward(
                point_ids=["mem-1"],
                reward=0.30,
                q_cache=q_cache,
            )

        assert captured_kwargs.get("q_before") == 0.40
        # q_after should be different from q_before (Q was updated)
        assert captured_kwargs.get("q_after") is not None
        assert captured_kwargs["q_after"] != 0.40


class TestIntegrationPredictionRewardExplanation:
    """Integration: RewardTracker.log_outcome generates and stores explanation."""

    def test_prediction_outcome_generates_explanation(self, tmp_path):
        from openexp.reward_tracker import RewardTracker
        from openexp.core.q_value import QCache, QValueUpdater

        q_cache = QCache()
        q_updater = QValueUpdater(cache=q_cache)
        tracker = RewardTracker(
            data_dir=tmp_path,
            q_cache=q_cache,
            q_updater=q_updater,
        )

        pred_id = tracker.log_prediction(
            prediction="Client will sign",
            confidence=0.7,
            strategic_value=0.8,
            memory_ids_used=["mem-pred-1"],
        )

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Prediction was accurate.")]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        log_path = tmp_path / "reward_log.jsonl"
        with patch("openexp.core.explanation._anthropic_client", mock_client), \
             patch("openexp.core.config.EXPLANATION_ENABLED", True), \
             patch("openexp.core.config.ANTHROPIC_API_KEY", "sk-test-key"), \
             patch("openexp.core.explanation.fetch_memory_contents", return_value={}), \
             patch("openexp.core.reward_log.REWARD_LOG_PATH", log_path):
            result = tracker.log_outcome(pred_id, "Client signed", reward=0.80)

        assert "error" not in result
        assert mock_client.messages.create.called

        from openexp.core.reward_log import get_reward_history
        with patch("openexp.core.reward_log.REWARD_LOG_PATH", log_path):
            records = get_reward_history("mem-pred-1")
        assert len(records) >= 1
        assert records[0].get("explanation") == "Prediction was accurate."

    def test_prediction_passes_q_before_q_after(self, tmp_path):
        """Verify prediction path passes q_before/q_after."""
        from openexp.reward_tracker import RewardTracker
        from openexp.core.q_value import QCache, QValueUpdater

        q_cache = QCache()
        q_cache.set("mem-pred-1", {"q_value": 0.30, "q_action": 0.30, "q_hypothesis": 0.30, "q_fit": 0.30, "q_visits": 1}, "default")
        q_updater = QValueUpdater(cache=q_cache)
        tracker = RewardTracker(data_dir=tmp_path, q_cache=q_cache, q_updater=q_updater)

        pred_id = tracker.log_prediction(
            prediction="Test pred",
            confidence=0.5,
            strategic_value=0.5,
            memory_ids_used=["mem-pred-1"],
        )

        captured_kwargs = {}

        def capture_explanation(**kwargs):
            captured_kwargs.update(kwargs)
            return "test"

        log_path = tmp_path / "reward_log.jsonl"
        with patch("openexp.reward_tracker.generate_reward_explanation", side_effect=capture_explanation), \
             patch("openexp.core.reward_log.REWARD_LOG_PATH", log_path):
            tracker.log_outcome(pred_id, "Outcome", reward=0.50)

        assert captured_kwargs.get("q_before") == 0.30
        assert captured_kwargs.get("q_after") is not None
        assert captured_kwargs["q_after"] != 0.30
