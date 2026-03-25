"""L3 Cold Storage — full-context reward event log.

L1 = Q-value scalar (instant ranking)
L2 = reward_contexts (short summaries in Q-cache)
L3 = cold storage (full context: observations, predictions, business events)

Each reward event gets a unique reward_id (rwd_<8hex>) that links
L2 summary → L3 full record. Access on-demand via MCP tools.

Storage: JSONL append-only log at DATA_DIR/reward_log.jsonl
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import DATA_DIR

logger = logging.getLogger(__name__)

REWARD_LOG_PATH = DATA_DIR / "reward_log.jsonl"
MAX_LOG_SIZE = 100 * 1024 * 1024  # 100 MB rotation threshold


def generate_reward_id() -> str:
    """Generate unique reward ID: rwd_<8hex>."""
    return f"rwd_{uuid.uuid4().hex[:8]}"


def log_reward_event(
    reward_id: str,
    reward_type: str,
    reward: float,
    memory_ids: List[str],
    context: Dict[str, Any],
    experience: str = "default",
) -> None:
    """Append full reward event to cold storage JSONL.

    Args:
        reward_id: Unique ID (rwd_XXXXXXXX)
        reward_type: "session" | "prediction" | "business" | "calibration"
        reward: Reward value
        memory_ids: Memory IDs that received this reward
        context: Full context dict (no size limit)
        experience: Experience name
    """
    record = {
        "reward_id": reward_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "reward_type": reward_type,
        "reward": reward,
        "memory_ids": memory_ids,
        "experience": experience,
        "context": context,
    }

    try:
        REWARD_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

        # Check rotation threshold
        if REWARD_LOG_PATH.exists():
            try:
                size = REWARD_LOG_PATH.stat().st_size
                if size > MAX_LOG_SIZE:
                    rotated = REWARD_LOG_PATH.with_suffix(".jsonl.1")
                    REWARD_LOG_PATH.rename(rotated)
                    logger.info("Rotated reward log (%d bytes) to %s", size, rotated)
            except OSError:
                pass

        with open(REWARD_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError as e:
        logger.error("Failed to write reward log: %s", e)


def get_reward_detail(reward_id: str) -> Optional[Dict]:
    """Retrieve full reward event by ID from cold storage.

    Scans JSONL from the end for faster lookup of recent events.
    """
    if not REWARD_LOG_PATH.exists():
        return None

    try:
        with open(REWARD_LOG_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if reward_id not in line:
                    continue
                try:
                    record = json.loads(line)
                    if record.get("reward_id") == reward_id:
                        return record
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        logger.error("Failed to read reward log: %s", e)

    return None


def get_reward_history(memory_id: str) -> List[Dict]:
    """Get all reward events that touched a specific memory."""
    if not REWARD_LOG_PATH.exists():
        return []

    results = []
    try:
        with open(REWARD_LOG_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if memory_id not in line:
                    continue
                try:
                    record = json.loads(line)
                    if memory_id in record.get("memory_ids", []):
                        results.append(record)
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        logger.error("Failed to read reward log: %s", e)

    return results


def compact_observation(obs: Dict) -> Dict:
    """Keep only fields needed for cold storage context."""
    return {
        "id": obs.get("id"),
        "tool": obs.get("tool"),
        "summary": obs.get("summary"),
        "type": obs.get("type"),
        "file_path": obs.get("context", {}).get("file_path"),
        "tags": obs.get("tags", []),
    }
