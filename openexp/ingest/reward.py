"""Session reward computation and Q-value updates.

Computes a reward signal based on session productivity heuristics,
then applies Q-learning updates to all memories ingested from that session.
"""
import logging
from typing import Dict, List, Optional

from ..core.config import Q_CACHE_PATH
from ..core.q_value import QCache, QValueUpdater, compute_layer_rewards

logger = logging.getLogger(__name__)


def compute_session_reward(
    observations: List[Dict],
    weights: Optional[Dict[str, float]] = None,
) -> float:
    """Compute reward signal based on session productivity.

    Heuristic: productive sessions (commits, PRs, file writes) get positive reward.
    Returns float in [-0.5, 0.5].

    If weights dict is provided (from an Experience), uses those instead of defaults.
    """
    if weights is None:
        weights = {
            "commit": 0.3,
            "pr": 0.2,
            "writes": 0.02,
            "deploy": 0.1,
            "tests": 0.1,
            "decisions": 0.1,
            "base": -0.1,
            "min_obs_penalty": -0.05,
            "no_output_penalty": -0.1,
        }

    score = weights.get("base", -0.1)

    summaries = [o.get("summary", "") for o in observations]
    tools = [o.get("tool", "") for o in observations]

    if len(observations) < 3:
        score += weights.get("min_obs_penalty", -0.05)

    writes = sum(1 for t in tools if t in ("Write", "Edit"))
    has_commits = any("git commit" in s for s in summaries)
    if writes == 0 and not has_commits:
        score += weights.get("no_output_penalty", -0.1)

    if has_commits:
        score += weights.get("commit", 0.3)
    if any("gh pr" in s for s in summaries):
        score += weights.get("pr", 0.2)
    if writes > 0:
        w = weights.get("writes", 0.02)
        score += min(0.2, writes * w)
    if any("deploy" in s.lower() for s in summaries):
        score += weights.get("deploy", 0.1)
    if any("test" in s.lower() and "pass" in s.lower() for s in summaries):
        score += weights.get("tests", 0.1)

    decisions = sum(1 for o in observations if o.get("type") == "decision")
    if decisions > 0:
        score += weights.get("decisions", 0.1)

    # Sales-specific signals
    if any("email" in s.lower() and "sent" in s.lower() for s in summaries):
        score += weights.get("email_sent", 0.0)
    if any("follow" in s.lower() and "up" in s.lower() for s in summaries):
        score += weights.get("follow_up", 0.0)

    # Dealflow signals
    if any("proposal" in s.lower() for s in summaries):
        score += weights.get("proposal_sent", 0.0)
    if any("invoice" in s.lower() for s in summaries):
        score += weights.get("invoice_sent", 0.0)
    if any("calendar" in s.lower() or "scheduled" in s.lower() for s in summaries):
        score += weights.get("call_scheduled", 0.0)
    if any("nda" in s.lower() or "agreement" in s.lower() for s in summaries):
        score += weights.get("nda_exchanged", 0.0)
    if any("payment" in s.lower() and "received" in s.lower() for s in summaries):
        score += weights.get("payment_received", 0.0)

    return max(-0.5, min(0.5, score))


def apply_session_reward(
    point_ids: List[str],
    reward: float,
    q_cache: QCache | None = None,
    experience: str = "default",
) -> int:
    """Apply reward to all memories from a session."""
    if not point_ids:
        return 0

    if q_cache is None:
        q_cache = QCache()
        q_cache.load(Q_CACHE_PATH)

    updater = QValueUpdater(cache=q_cache)
    layer_rewards = compute_layer_rewards(reward)
    updated = {}
    for mem_id in point_ids:
        updated[mem_id] = updater.update_all_layers(mem_id, layer_rewards, experience=experience)

    q_cache.save(Q_CACHE_PATH)
    logger.info("Applied session reward=%.2f to %d memories (experience=%s)", reward, len(updated), experience)
    return len(updated)


def reward_retrieved_memories(
    session_id: str,
    reward: float,
    experience: str = "default",
) -> int:
    """Reward memories that were retrieved at session start.

    Closes the loop: memories retrieved -> session outcome -> Q-value update.
    """
    from .retrieval_log import get_session_retrievals

    memory_ids = get_session_retrievals(session_id)
    if not memory_ids:
        return 0

    updated = apply_session_reward(memory_ids, reward, experience=experience)
    logger.info(
        "Rewarded %d retrieved memories for session %s (reward=%.2f, experience=%s)",
        updated, session_id[:8], reward, experience,
    )
    return updated
