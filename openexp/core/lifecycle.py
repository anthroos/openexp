"""Memory Status Lifecycle — 8 states with transition validation."""
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue
from .config import QDRANT_HOST, QDRANT_PORT, QDRANT_API_KEY, COLLECTION_NAME

logger = logging.getLogger(__name__)

LIFECYCLE_STATES = {
    "active", "confirmed", "outdated", "archived",
    "contradicted", "merged", "superseded", "deleted",
}

VALID_TRANSITIONS = {
    "active": {"confirmed", "outdated", "archived", "contradicted", "merged", "superseded", "deleted"},
    "confirmed": {"outdated", "archived", "superseded", "deleted"},
    "outdated": {"archived", "deleted", "active"},
    "archived": {"active", "deleted"},
    "contradicted": {"deleted", "active"},
    "merged": {"deleted"},
    "superseded": {"deleted"},
    "deleted": set(),
}

DEFAULT_STATUS = "active"


class MemoryLifecycle:
    """Memory lifecycle management with status tracking and transitions."""

    def __init__(self):
        self.qc = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, api_key=QDRANT_API_KEY)

    def transition(self, memory_id: str, from_status: str, to_status: str) -> bool:
        """Validate and execute a status transition."""
        if from_status not in VALID_TRANSITIONS:
            logger.warning("Invalid from_status: %s", from_status)
            return False
        if to_status not in VALID_TRANSITIONS[from_status]:
            logger.warning("Invalid transition: %s -> %s", from_status, to_status)
            return False

        try:
            result = self.qc.retrieve(collection_name=COLLECTION_NAME, ids=[memory_id])
            if not result:
                logger.warning("Memory %s not found", memory_id)
                return False

            current_payload = result[0].payload
            current_status = current_payload.get("status", DEFAULT_STATUS)

            if current_status != from_status:
                logger.warning("Status mismatch: expected %s, got %s", from_status, current_status)
                return False

            payload_update = {
                "status": to_status,
                "status_updated_at": datetime.now(timezone.utc).isoformat(),
            }

            if to_status in {"superseded", "contradicted", "merged"}:
                payload_update["lifecycle_metadata"] = current_payload.get("lifecycle_metadata", {})
                payload_update["lifecycle_metadata"]["transition_timestamp"] = datetime.now(timezone.utc).isoformat()

            self.qc.set_payload(
                collection_name=COLLECTION_NAME,
                payload=payload_update,
                points=[memory_id],
            )
            logger.info("Memory %s transitioned: %s -> %s", memory_id, from_status, to_status)
            return True
        except Exception as e:
            logger.error("Failed to transition memory %s: %s", memory_id, e)
            return False

    def get_status(self, memory_id: str) -> str:
        """Get the current status of a memory."""
        try:
            result = self.qc.retrieve(collection_name=COLLECTION_NAME, ids=[memory_id])
            if not result:
                return DEFAULT_STATUS
            return result[0].payload.get("status", DEFAULT_STATUS)
        except Exception as e:
            logger.error("Failed to get status for memory %s: %s", memory_id, e)
            return DEFAULT_STATUS

    def get_lifecycle_stats(self) -> Dict[str, int]:
        """Get counts of memories by lifecycle status."""
        stats = {}
        try:
            for status in LIFECYCLE_STATES:
                filter_condition = Filter(
                    must=[FieldCondition(key="status", match=MatchValue(value=status))]
                )
                count_result = self.qc.count(
                    collection_name=COLLECTION_NAME,
                    count_filter=filter_condition,
                    exact=True,
                )
                stats[status] = count_result.count

            total = self.qc.count(collection_name=COLLECTION_NAME, exact=True).count
            labeled = sum(stats.values())
            stats["unlabeled"] = max(0, total - labeled)
        except Exception as e:
            logger.error("Failed to get lifecycle stats: %s", e)
        return stats
