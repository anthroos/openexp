"""Tests for outcome-based reward resolution.

Tests OutcomeEvent, OutcomeResolver, CRMCSVResolver, resolve_outcomes,
and client matching logic.
"""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from openexp.outcome import OutcomeEvent, OutcomeResolver, resolve_outcomes, _find_memories_for_entity
from openexp.resolvers.crm_csv import (
    CRMCSVResolver,
    client_matches,
    _extract_core,
    _match_transition,
    DEAL_TRANSITIONS,
    LEAD_TRANSITIONS,
)


# Override autouse async fixture from conftest.py
@pytest.fixture(autouse=True)
def cleanup_test_memories():
    yield


class TestOutcomeEvent:
    def test_basic_construction(self):
        event = OutcomeEvent(
            entity_id="comp-squad",
            event_name="deal_closed",
            reward=0.8,
        )
        assert event.entity_id == "comp-squad"
        assert event.event_name == "deal_closed"
        assert event.reward == 0.8
        assert event.details == {}

    def test_reward_clamping_high(self):
        event = OutcomeEvent(entity_id="x", event_name="y", reward=2.0)
        assert event.reward == 1.0

    def test_reward_clamping_low(self):
        event = OutcomeEvent(entity_id="x", event_name="y", reward=-3.0)
        assert event.reward == -1.0

    def test_details_preserved(self):
        event = OutcomeEvent(
            entity_id="x",
            event_name="y",
            reward=0.5,
            details={"from_stage": "new", "to_stage": "qualified"},
        )
        assert event.details["from_stage"] == "new"


class TestClientMatching:
    def test_exact_match(self):
        assert client_matches("comp-squad", "comp-squad")

    def test_cross_prefix_match(self):
        assert client_matches("cli-dt-001", "comp-dt-001")

    def test_short_core_match(self):
        assert client_matches("comp-dt", "cli-dt")

    def test_no_match_different_suffix(self):
        assert not client_matches("comp-a-1", "cli-a-2")

    def test_single_char_core_rejected(self):
        assert not client_matches("comp-a", "cli-a")

    def test_no_prefix_exact(self):
        assert client_matches("squad", "squad")

    def test_no_prefix_different(self):
        assert not client_matches("squad", "other")

    def test_extract_core_cli(self):
        assert _extract_core("cli-dt-001") == "dt-001"

    def test_extract_core_comp(self):
        assert _extract_core("comp-squad") == "squad"

    def test_extract_core_lead(self):
        assert _extract_core("lead-squad-001") == "squad-001"

    def test_extract_core_no_prefix(self):
        assert _extract_core("custom-id") == "custom-id"


class TestTransitionMatching:
    def test_exact_deal_transition(self):
        result = _match_transition("invoiced", "paid", DEAL_TRANSITIONS)
        assert result is not None
        event, reward = result
        assert event == "payment_received"
        assert reward == 1.0

    def test_wildcard_deal_transition(self):
        result = _match_transition("anything", "lost", DEAL_TRANSITIONS)
        assert result is not None
        event, reward = result
        assert event == "deal_lost"
        assert reward == -0.5

    def test_no_match(self):
        result = _match_transition("new", "qualified", DEAL_TRANSITIONS)
        assert result is None

    def test_lead_qualified(self):
        result = _match_transition("new", "qualified", LEAD_TRANSITIONS)
        assert result is not None
        event, reward = result
        assert event == "meaningful_response"
        assert reward == 0.4


class TestCRMCSVResolver:
    def _setup_crm(self, tmp_path, deals=None, leads=None):
        """Helper to create CRM CSV files."""
        rel_dir = tmp_path / "relationships"
        rel_dir.mkdir(exist_ok=True)

        if deals is not None:
            with open(rel_dir / "deals.csv", "w") as f:
                if deals:
                    f.write(",".join(deals[0].keys()) + "\n")
                    for deal in deals:
                        f.write(",".join(str(v) for v in deal.values()) + "\n")

        if leads is not None:
            with open(rel_dir / "leads.csv", "w") as f:
                if leads:
                    f.write(",".join(leads[0].keys()) + "\n")
                    for lead in leads:
                        f.write(",".join(str(v) for v in lead.values()) + "\n")

    def test_no_crm_dir(self, tmp_path):
        resolver = CRMCSVResolver(
            crm_dir=tmp_path / "nonexistent",
            snapshot_dir=tmp_path,
        )
        events = resolver.detect_outcomes()
        assert events == []

    def test_no_changes(self, tmp_path):
        deals = [{"deal_id": "d-1", "stage": "negotiation", "client_id": "comp-x", "name": "X", "value": "100", "paid_date": ""}]
        self._setup_crm(tmp_path, deals=deals, leads=[])

        resolver = CRMCSVResolver(crm_dir=tmp_path, snapshot_dir=tmp_path)

        # First run — establishes baseline
        events1 = resolver.detect_outcomes()
        assert events1 == []  # no old snapshot → no transitions

        # Second run — no changes
        events2 = resolver.detect_outcomes()
        assert events2 == []

    def test_deal_stage_transition(self, tmp_path):
        # Set up initial state
        deals_v1 = [{"deal_id": "d-1", "stage": "negotiation", "client_id": "comp-x", "name": "X", "value": "100", "paid_date": ""}]
        self._setup_crm(tmp_path, deals=deals_v1, leads=[])

        resolver = CRMCSVResolver(crm_dir=tmp_path, snapshot_dir=tmp_path)
        resolver.detect_outcomes()  # establish baseline

        # Change stage
        deals_v2 = [{"deal_id": "d-1", "stage": "won", "client_id": "comp-x", "name": "X", "value": "100", "paid_date": ""}]
        self._setup_crm(tmp_path, deals=deals_v2, leads=[])

        events = resolver.detect_outcomes()
        assert len(events) == 1
        assert events[0].event_name == "deal_closed"
        assert events[0].reward == 0.8
        assert events[0].entity_id == "comp-x"

    def test_lead_stage_transition(self, tmp_path):
        leads_v1 = [{"lead_id": "l-1", "stage": "new", "company_id": "comp-y", "estimated_value": "500"}]
        self._setup_crm(tmp_path, deals=[], leads=leads_v1)

        resolver = CRMCSVResolver(crm_dir=tmp_path, snapshot_dir=tmp_path)
        resolver.detect_outcomes()  # baseline

        leads_v2 = [{"lead_id": "l-1", "stage": "qualified", "company_id": "comp-y", "estimated_value": "500"}]
        self._setup_crm(tmp_path, deals=[], leads=leads_v2)

        events = resolver.detect_outcomes()
        assert len(events) == 1
        assert events[0].event_name == "meaningful_response"
        assert events[0].reward == 0.4

    def test_paid_date_detection(self, tmp_path):
        deals_v1 = [{"deal_id": "d-1", "stage": "invoiced", "client_id": "comp-z", "name": "Z", "value": "200", "paid_date": ""}]
        self._setup_crm(tmp_path, deals=deals_v1, leads=[])

        resolver = CRMCSVResolver(crm_dir=tmp_path, snapshot_dir=tmp_path)
        resolver.detect_outcomes()

        # paid_date now set — stage auto-detected as "paid"
        deals_v2 = [{"deal_id": "d-1", "stage": "invoiced", "client_id": "comp-z", "name": "Z", "value": "200", "paid_date": "2026-03-22"}]
        self._setup_crm(tmp_path, deals=deals_v2, leads=[])

        events = resolver.detect_outcomes()
        assert len(events) == 1
        assert events[0].event_name == "payment_received"
        assert events[0].reward == 1.0

    def test_snapshot_persistence(self, tmp_path):
        deals = [{"deal_id": "d-1", "stage": "new", "client_id": "comp-a", "name": "A", "value": "50", "paid_date": ""}]
        self._setup_crm(tmp_path, deals=deals, leads=[])

        resolver = CRMCSVResolver(crm_dir=tmp_path, snapshot_dir=tmp_path)
        resolver.detect_outcomes()

        # Verify snapshot was saved
        snapshot_file = tmp_path / "crm_snapshot.json"
        assert snapshot_file.exists()
        snapshot = json.loads(snapshot_file.read_text())
        assert "d-1" in snapshot["deals"]
        assert snapshot["deals"]["d-1"]["stage"] == "new"


class TestResolveOutcomes:
    def test_no_resolvers(self):
        result = resolve_outcomes(resolvers=[])
        assert result["total_events"] == 0
        assert result["memories_rewarded"] == 0

    def test_with_mock_resolver(self):
        """Mock resolver + mock Qdrant → memories get rewarded."""
        class MockResolver(OutcomeResolver):
            @property
            def name(self):
                return "mock"

            def detect_outcomes(self):
                return [
                    OutcomeEvent(entity_id="comp-test", event_name="deal_closed", reward=0.8),
                ]

        from openexp.core.q_value import QCache, QValueUpdater

        q_cache = QCache()
        q_updater = QValueUpdater(cache=q_cache)

        # Mock _find_memories_for_entity to return some IDs
        with patch("openexp.outcome._find_memories_for_entity", return_value=["mem-1", "mem-2"]):
            result = resolve_outcomes(
                resolvers=[MockResolver()],
                q_cache=q_cache,
                q_updater=q_updater,
            )

        assert result["total_events"] == 1
        assert result["memories_rewarded"] == 2

        # Verify Q-values were updated
        q1 = q_cache.get("mem-1")
        assert q1 is not None
        assert q1["q_action"] != 0.5  # updated from default
        assert q1["q_hypothesis"] != 0.5
        assert q1["q_fit"] != 0.5

    def test_resolver_failure_handled(self):
        """Failed resolver doesn't crash the pipeline."""
        class FailingResolver(OutcomeResolver):
            @property
            def name(self):
                return "failing"

            def detect_outcomes(self):
                raise RuntimeError("CRM is down")

        result = resolve_outcomes(resolvers=[FailingResolver()])
        assert result["total_events"] == 0
        assert "error" in result["resolvers"]["failing"]

    def test_predictions_resolved(self):
        """Pending predictions matching entity_id get resolved."""
        class MockResolver(OutcomeResolver):
            @property
            def name(self):
                return "mock"

            def detect_outcomes(self):
                return [
                    OutcomeEvent(entity_id="comp-test", event_name="deal_closed", reward=0.8),
                ]

        from openexp.core.q_value import QCache, QValueUpdater

        q_cache = QCache()
        q_updater = QValueUpdater(cache=q_cache)

        mock_tracker = MagicMock()
        mock_tracker.get_pending_predictions.return_value = [
            {"id": "pred_abc123", "client_id": "comp-test", "prediction": "SQUAD will close"}
        ]
        mock_tracker.log_outcome.return_value = {"prediction_id": "pred_abc123", "reward": 0.8}

        with patch("openexp.outcome._find_memories_for_entity", return_value=[]):
            result = resolve_outcomes(
                resolvers=[MockResolver()],
                reward_tracker=mock_tracker,
                q_cache=q_cache,
                q_updater=q_updater,
            )

        assert result["predictions_resolved"] == 1
        mock_tracker.log_outcome.assert_called_once()


class TestMultiLayerReward:
    """Test that session reward updates all 3 Q-layers."""

    def test_apply_session_reward_multi_layer(self, tmp_path):
        """apply_session_reward now updates action, hypothesis, and fit."""
        from openexp.ingest.reward import apply_session_reward
        from openexp.core.q_value import QCache

        q_cache_path = tmp_path / "q_cache.json"
        q_cache_path.write_text(json.dumps({
            "mem-1": {"q_value": 0.0, "q_action": 0.0, "q_hypothesis": 0.0, "q_fit": 0.0, "q_visits": 0},
        }))

        with patch("openexp.ingest.reward.Q_CACHE_PATH", q_cache_path):
            updated = apply_session_reward(["mem-1"], reward=0.3)

        assert updated == 1

        q_data = json.loads(q_cache_path.read_text())
        entry = q_data["mem-1"]["default"]

        # All layers should be updated (additive: 0.0 + 0.25 * reward)
        assert entry["q_action"] != 0.0
        assert entry["q_hypothesis"] != 0.0
        assert entry["q_fit"] != 0.0

        # action gets full reward, hypothesis gets discounted
        assert entry["q_action"] > entry["q_hypothesis"]

    def test_negative_reward_fit_discounted(self, tmp_path):
        """Negative reward: fit layer gets 50% penalty (less harsh)."""
        from openexp.ingest.reward import apply_session_reward

        q_cache_path = tmp_path / "q_cache.json"
        q_cache_path.write_text(json.dumps({
            "mem-1": {"q_value": 0.0, "q_action": 0.0, "q_hypothesis": 0.0, "q_fit": 0.0, "q_visits": 0},
        }))

        with patch("openexp.ingest.reward.Q_CACHE_PATH", q_cache_path):
            apply_session_reward(["mem-1"], reward=-0.4)

        q_data = json.loads(q_cache_path.read_text())
        entry = q_data["mem-1"]["default"]

        # Additive: Q_new = 0.0 + 0.25 * reward
        # action gets full -0.4, fit gets -0.2 (discounted)
        expected_action = 0.0 + 0.25 * (-0.4)  # -0.1
        expected_fit = 0.0 + 0.25 * (-0.2)      # -0.05

        assert abs(entry["q_action"] - expected_action) < 0.01
        assert abs(entry["q_fit"] - expected_fit) < 0.01
        assert entry["q_fit"] > entry["q_action"]  # fit less harsh
