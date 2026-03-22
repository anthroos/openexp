"""Log which memories were retrieved at session start.

Enables closed-loop reward: retrieved memories get Q-value updates
based on session outcome.
"""
import json
import logging
from datetime import datetime, timezone
from typing import List, Optional

from ..core.config import DATA_DIR

logger = logging.getLogger(__name__)

RETRIEVALS_PATH = DATA_DIR / "session_retrievals.jsonl"


def log_retrieval(
    session_id: str,
    query: str,
    memory_ids: List[str],
    scores: Optional[List[float]] = None,
) -> None:
    """Append retrieval record to JSONL."""
    record = {
        "session_id": session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "query": query,
        "memory_ids": memory_ids,
        "scores": scores or [],
    }
    with open(RETRIEVALS_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")
    logger.info("Logged %d retrieved memories for session %s", len(memory_ids), session_id[:8])


def get_session_retrievals(session_id: str) -> List[str]:
    """Return memory_ids retrieved for a given session."""
    if not RETRIEVALS_PATH.exists():
        return []

    memory_ids = []
    for line in RETRIEVALS_PATH.read_text().strip().split("\n"):
        if not line:
            continue
        try:
            record = json.loads(line)
            if record.get("session_id") == session_id:
                memory_ids.extend(record.get("memory_ids", []))
        except json.JSONDecodeError:
            continue

    return list(dict.fromkeys(memory_ids))
