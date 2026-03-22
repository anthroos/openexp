"""Session reward computation and Q-value updates.

Computes a reward signal based on session productivity heuristics,
then applies Q-learning updates to all memories ingested from that session.
"""
import logging
from typing import Dict, List

from ..core.config import Q_CACHE_PATH
from ..core.q_value import QCache, QValueUpdater

logger = logging.getLogger(__name__)


def compute_session_reward(observations: List[Dict]) -> float:
    """Compute reward signal based on session productivity.

    Heuristic: productive sessions (commits, PRs, file writes) get positive reward.
    Returns float in [-0.5, 0.5].
    """
    score = -0.1

    summaries = [o.get("summary", "") for o in observations]
    tools = [o.get("tool", "") for o in observations]

    if len(observations) < 3:
        score -= 0.05

    writes = sum(1 for t in tools if t in ("Write", "Edit"))
    has_commits = any("git commit" in s for s in summaries)
    if writes == 0 and not has_commits:
        score -= 0.1

    if has_commits:
        score += 0.3
    if any("gh pr" in s for s in summaries):
        score += 0.2
    if writes > 0:
        score += min(0.2, writes * 0.02)
    if any("deploy" in s.lower() for s in summaries):
        score += 0.1
    if any("test" in s.lower() and "pass" in s.lower() for s in summaries):
        score += 0.1

    decisions = sum(1 for o in observations if o.get("type") == "decision")
    if decisions > 0:
        score += 0.1

    return max(-0.5, min(0.5, score))


def apply_session_reward(
    point_ids: List[str],
    reward: float,
    q_cache: QCache | None = None,
) -> int:
    """Apply reward to all memories from a session."""
    if not point_ids:
        return 0

    if q_cache is None:
        q_cache = QCache()
        q_cache.load(Q_CACHE_PATH)

    updater = QValueUpdater(cache=q_cache)
    updated = updater.batch_update(point_ids, reward, layer="action")

    q_cache.save(Q_CACHE_PATH)
    logger.info("Applied session reward=%.2f to %d memories", reward, len(updated))
    return len(updated)


def reward_retrieved_memories(session_id: str, reward: float) -> int:
    """Reward memories that were retrieved at session start.

    Closes the loop: memories retrieved -> session outcome -> Q-value update.
    """
    from .retrieval_log import get_session_retrievals

    memory_ids = get_session_retrievals(session_id)
    if not memory_ids:
        return 0

    updated = apply_session_reward(memory_ids, reward)
    logger.info(
        "Rewarded %d retrieved memories for session %s (reward=%.2f)",
        updated, session_id[:8], reward,
    )
    return updated
