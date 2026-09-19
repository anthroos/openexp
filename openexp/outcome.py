"""Outcome resolution.

Connects real-world business events (CRM stage changes, payments, etc.)
to pending predictions: resolvers detect events, matching predictions get
resolved with the observed signal. Q-value rewards were removed 2026-07-03.
"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class OutcomeEvent:
    """A detected business outcome that should reward/penalize memories."""
    entity_id: str          # client/company ID (e.g., "comp-acme")
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

        Returns list of OutcomeEvents. Each event resolves matching
        pending predictions.
        """
        ...


def resolve_outcomes(
    resolvers: List[OutcomeResolver],
    reward_tracker: Optional[Any] = None,
    experience: str = "default",
) -> Dict[str, Any]:
    """Run all outcome resolvers.

    1. Each resolver detects new OutcomeEvents
    2. For each event: resolve matching pending predictions (if reward_tracker)

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
            "predictions_resolved": 0,
            "resolvers": resolver_results,
        }

    total_predictions_resolved = 0

    for event, resolver_name in all_events:
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
                    logger.info(
                        "Event %s for %s resolved prediction %s",
                        event.event_name, event.entity_id, pred["id"],
                    )

    return {
        "total_events": len(all_events),
        "predictions_resolved": total_predictions_resolved,
        "resolvers": resolver_results,
    }
