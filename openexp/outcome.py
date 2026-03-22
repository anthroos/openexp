"""Outcome-based reward resolution.

Connects real-world business events (CRM stage changes, payments, etc.)
to Q-value updates on the memories that contributed to those outcomes.

This replaces the session-level "count git commits" heuristic with
targeted, outcome-based rewards that flow back to specific memories.
"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from qdrant_client.models import Filter, FieldCondition, MatchValue

from .core.config import COLLECTION_NAME
from .core.direct_search import _get_qdrant
from .core.q_value import QCache, QValueUpdater

logger = logging.getLogger(__name__)


@dataclass
class OutcomeEvent:
    """A detected business outcome that should reward/penalize memories."""
    entity_id: str          # client/company ID (e.g., "comp-squad")
    event_name: str         # e.g., "deal_closed", "payment_received"
    reward: float           # [-1.0, 1.0]
    details: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.reward = max(-1.0, min(1.0, self.reward))


class OutcomeResolver(ABC):
    """Abstract base for outcome detection.

    Subclasses scan external data sources (CRM, payment systems, etc.)
    and return OutcomeEvents when they detect meaningful changes.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable resolver name."""
        ...

    @abstractmethod
    def detect_outcomes(self) -> List[OutcomeEvent]:
        """Scan for new outcomes since last check.

        Returns list of OutcomeEvents. Each event will be matched to
        memories by entity_id and used to update Q-values.
        """
        ...


def _find_memories_for_entity(entity_id: str) -> List[str]:
    """Find all memory IDs tagged with a given entity/client ID.

    Uses Qdrant scroll (no vector search needed — just payload filter).
    """
    qc = _get_qdrant()

    qdrant_filter = Filter(
        must=[
            FieldCondition(
                key="metadata.client_id",
                match=MatchValue(value=entity_id),
            )
        ]
    )

    memory_ids = []
    offset = None
    while True:
        results = qc.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=qdrant_filter,
            limit=100,
            offset=offset,
            with_payload=False,
            with_vectors=False,
        )
        points, next_offset = results
        for point in points:
            memory_ids.append(str(point.id))
        if next_offset is None:
            break
        offset = next_offset

    return memory_ids


def resolve_outcomes(
    resolvers: List[OutcomeResolver],
    reward_tracker: Optional[Any] = None,
    q_cache: Optional[QCache] = None,
    q_updater: Optional[QValueUpdater] = None,
) -> Dict[str, Any]:
    """Run all outcome resolvers and apply rewards.

    1. Each resolver detects new OutcomeEvents
    2. For each event: resolve matching pending predictions (if reward_tracker)
    3. Find all memories with matching entity_id
    4. Apply reward to found memories via Q-value updates

    Returns summary of all actions taken.
    """
    all_events: List[OutcomeEvent] = []
    resolver_results = {}

    for resolver in resolvers:
        try:
            events = resolver.detect_outcomes()
            all_events.extend(events)
            resolver_results[resolver.name] = {
                "events": len(events),
                "details": [
                    {"entity": e.entity_id, "event": e.event_name, "reward": e.reward}
                    for e in events
                ],
            }
            logger.info(
                "Resolver %s detected %d outcomes", resolver.name, len(events)
            )
        except Exception as e:
            logger.error("Resolver %s failed: %s", resolver.name, e)
            resolver_results[resolver.name] = {"error": str(e)}

    if not all_events:
        return {
            "total_events": 0,
            "memories_rewarded": 0,
            "predictions_resolved": 0,
            "resolvers": resolver_results,
        }

    total_memories_rewarded = 0
    total_predictions_resolved = 0

    for event in all_events:
        # 1. Resolve matching predictions
        if reward_tracker:
            pending = reward_tracker.get_pending_predictions(client_id=event.entity_id)
            for pred in pending:
                result = reward_tracker.log_outcome(
                    prediction_id=pred["id"],
                    outcome=f"Auto-detected: {event.event_name}",
                    reward=event.reward,
                    source="outcome_resolver",
                )
                if "error" not in result:
                    total_predictions_resolved += 1

        # 2. Find and reward tagged memories
        memory_ids = _find_memories_for_entity(event.entity_id)
        if memory_ids and q_updater:
            for mem_id in memory_ids:
                q_updater.update_all_layers(mem_id, {
                    "action": event.reward,
                    "hypothesis": event.reward * 0.8,
                    "fit": event.reward if event.reward > 0 else event.reward * 0.5,
                })
            total_memories_rewarded += len(memory_ids)
            logger.info(
                "Event %s for %s: rewarded %d memories (reward=%.2f)",
                event.event_name, event.entity_id, len(memory_ids), event.reward,
            )

    return {
        "total_events": len(all_events),
        "memories_rewarded": total_memories_rewarded,
        "predictions_resolved": total_predictions_resolved,
        "resolvers": resolver_results,
    }
