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
from .core.explanation import generate_reward_explanation, _fetch_memory_contents
from .core.q_value import QCache, QValueUpdater, compute_layer_rewards
from .core.reward_log import generate_reward_id, log_reward_event

logger = logging.getLogger(__name__)


def _build_outcome_reward_context(event: "OutcomeEvent") -> str:
    """Build a human-readable reward context for a business outcome event.

    Format: "Biz +0.50: deal_closed for comp-squad {amount=$8000}"
    """
    sign = "+" if event.reward >= 0 else ""
    ctx = f"Biz {sign}{event.reward:.2f}: {event.event_name} for {event.entity_id}"
    if event.details:
        details_str = ", ".join(f"{k}={v}" for k, v in list(event.details.items())[:3])
        ctx += f" {{{details_str}}}"
    return ctx


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
    experience: str = "default",
) -> Dict[str, Any]:
    """Run all outcome resolvers and apply rewards.

    1. Each resolver detects new OutcomeEvents
    2. For each event: resolve matching pending predictions (if reward_tracker)
    3. Find all memories with matching entity_id
    4. Apply reward to found memories via Q-value updates

    Returns summary of all actions taken.
    """
    all_events: List[tuple] = []  # (event, resolver_name)
    resolver_results = {}

    for resolver in resolvers:
        try:
            events = resolver.detect_outcomes()
            all_events.extend((e, resolver.name) for e in events)
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

    for event, resolver_name in all_events:
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
            reward_ctx = _build_outcome_reward_context(event)

            # L3 cold storage
            rwd_id = generate_reward_id()
            cold_context = {
                "entity_id": event.entity_id,
                "event_name": event.event_name,
                "details": event.details,
                "resolver": resolver_name,
            }

            # L4: read first memory's Q before update
            q_before = None
            first_q_data = q_updater.cache.get(memory_ids[0], experience)
            if first_q_data:
                q_before = first_q_data.get("q_value", 0.0)

            layer_rewards = compute_layer_rewards(event.reward)
            for mem_id in memory_ids:
                q_updater.update_all_layers(
                    mem_id, layer_rewards, experience=experience,
                    reward_context=reward_ctx, reward_id=rwd_id,
                )

            # L4: read first memory's Q after update
            q_after = None
            first_q_after = q_updater.cache.get(memory_ids[0], experience)
            if first_q_after:
                q_after = first_q_after.get("q_value", 0.0)

            # L4: generate explanation with q_before/q_after
            explanation = generate_reward_explanation(
                reward_type="business",
                reward=event.reward,
                context=cold_context,
                memory_contents=_fetch_memory_contents(memory_ids[:5]),
                q_before=q_before,
                q_after=q_after,
                experience=experience,
            )

            log_reward_event(
                reward_id=rwd_id,
                reward_type="business",
                reward=event.reward,
                memory_ids=memory_ids,
                context=cold_context,
                experience=experience,
                explanation=explanation,
            )
            total_memories_rewarded += len(memory_ids)
            logger.info(
                "Event %s for %s: rewarded %d memories (reward=%.2f, reward_id=%s)",
                event.event_name, event.entity_id, len(memory_ids), event.reward, rwd_id,
            )

    return {
        "total_events": len(all_events),
        "memories_rewarded": total_memories_rewarded,
        "predictions_resolved": total_predictions_resolved,
        "resolvers": resolver_results,
    }
