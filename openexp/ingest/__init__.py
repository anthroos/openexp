"""OpenExp Ingest — Observation pipeline into Qdrant.

Public API:
    ingest_session()  — full pipeline: observations + sessions + reward
"""
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def ingest_session(
    max_count: int = 0,
    dry_run: bool = False,
    sessions_only: bool = False,
    session_id: Optional[str] = None,
) -> Dict:
    """Full ingest pipeline: observations + sessions + reward."""
    from .observation import ingest_observations
    from .session_summary import ingest_sessions
    from .reward import compute_session_reward, apply_session_reward, reward_retrieved_memories

    result = {}

    if not sessions_only:
        obs_result = ingest_observations(max_count=max_count, dry_run=dry_run)
        result["observations"] = obs_result
    else:
        result["observations"] = {"skipped": True}

    session_result = ingest_sessions(dry_run=dry_run)
    result["sessions"] = session_result

    if dry_run:
        return result

    obs_data = result.get("observations", {})
    point_ids = obs_data.pop("_point_ids", [])
    raw_obs = obs_data.pop("_raw_observations", [])

    if point_ids and raw_obs:
        reward = compute_session_reward(raw_obs)
        if reward != 0.0:
            updated = apply_session_reward(point_ids, reward)
            result["reward"] = {"applied": True, "value": reward, "updated": updated}
            logger.info("Session reward=%.2f applied to %d memories", reward, updated)
        else:
            result["reward"] = {"applied": False, "value": 0.0, "reason": "neutral session"}
    else:
        result["reward"] = {"applied": False, "reason": "no new observations"}

    if session_id:
        reward_val = result.get("reward", {}).get("value", 0.0)
        if reward_val and reward_val != 0.0:
            retrieved_updated = reward_retrieved_memories(session_id, reward_val)
            result["reward"]["retrieved_memories_rewarded"] = retrieved_updated
        else:
            result["reward"]["retrieved_memories_rewarded"] = 0

    return result
