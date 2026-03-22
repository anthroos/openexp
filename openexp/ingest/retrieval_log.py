"""Log which memories were retrieved at session start.

Enables closed-loop reward: retrieved memories get Q-value updates
based on session outcome.
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

from ..core.config import DATA_DIR

logger = logging.getLogger(__name__)

RETRIEVALS_PATH = DATA_DIR / "session_retrievals.jsonl"

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
# Read from end of file: scan at most this many bytes for recent sessions
_TAIL_BYTES = 512 * 1024  # 512 KB


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
    """Return memory_ids retrieved for a given session.

    Reads from the end of the file since recent sessions are most likely
    near the tail. Skips files larger than MAX_FILE_SIZE.
    """
    if not RETRIEVALS_PATH.exists():
        return []

    try:
        file_size = RETRIEVALS_PATH.stat().st_size
    except OSError:
        return []

    if file_size > MAX_FILE_SIZE:
        logger.warning("Retrieval log too large, skipping: %s (%d bytes)", RETRIEVALS_PATH, file_size)
        return []

    memory_ids = []

    # For large files, only read the tail where recent sessions are likely found
    if file_size > _TAIL_BYTES:
        with open(RETRIEVALS_PATH, "rb") as f:
            f.seek(-_TAIL_BYTES, os.SEEK_END)
            # Discard partial first line
            f.readline()
            tail_data = f.read().decode("utf-8", errors="replace")
        lines = tail_data.strip().split("\n")
    else:
        with open(RETRIEVALS_PATH, encoding="utf-8") as f:
            lines = f.read().strip().split("\n")

    for line in lines:
        if not line:
            continue
        try:
            record = json.loads(line)
            if record.get("session_id") == session_id:
                memory_ids.extend(record.get("memory_ids", []))
        except json.JSONDecodeError:
            continue

    return list(dict.fromkeys(memory_ids))
