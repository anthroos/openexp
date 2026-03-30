"""Session reward computation and Q-value updates.

Computes a reward signal based on session productivity heuristics,
then applies Q-learning updates to all memories ingested from that session.
"""
import logging
from typing import Dict, List, Optional

from ..core.config import Q_CACHE_PATH
from ..core.explanation import generate_reward_explanation, _fetch_memory_contents
from ..core.q_value import QCache, QValueUpdater, compute_layer_rewards
from ..core.reward_log import generate_reward_id, log_reward_event, compact_observation

logger = logging.getLogger(__name__)


def _build_session_reward_context(observations: List[Dict], reward: float) -> str:
    """Build a human-readable reward context summarizing session productivity.

    Format: "Session +0.30: 2 commits, 1 PR, 5 writes"
    """
    tools = [o.get("tool", "") for o in observations]
    summaries = [o.get("summary", "") for o in observations]

    parts = []
    commits = sum(1 for s in summaries if "git commit" in s)
    if commits:
        parts.append(f"{commits} commit{'s' if commits > 1 else ''}")
    prs = sum(1 for s in summaries if "gh pr" in s)
    if prs:
        parts.append(f"{prs} PR{'s' if prs > 1 else ''}")
    writes = sum(1 for t in tools if t in ("Write", "Edit"))
    if writes:
        parts.append(f"{writes} write{'s' if writes > 1 else ''}")
    deploys = sum(1 for s in summaries if "deploy" in s.lower())
    if deploys:
        parts.append(f"{deploys} deploy{'s' if deploys > 1 else ''}")
    decisions = sum(1 for o in observations if o.get("type") == "decision")
    if decisions:
        parts.append(f"{decisions} decision{'s' if decisions > 1 else ''}")

    sign = "+" if reward >= 0 else ""
    summary = ", ".join(parts) if parts else "no output"
    return f"Session {sign}{reward:.2f}: {summary}"


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

    # Communication signals
    if any("telegram" in s.lower() and "sent" in s.lower() for s in summaries):
        score += weights.get("telegram_sent", 0.0)
    if any("slack" in s.lower() and ("sent" in s.lower() or "post" in s.lower()) for s in summaries):
        score += weights.get("slack_sent", 0.0)

    # Engineering signals
    if any("gh pr" in s and "merge" in s.lower() for s in summaries):
        score += weights.get("pr_merged", 0.0)
    if any("ticket" in s.lower() and ("closed" in s.lower() or "resolved" in s.lower()) for s in summaries):
        score += weights.get("ticket_closed", 0.0)
    if any("review" in s.lower() and ("approved" in s.lower() or "lgtm" in s.lower()) for s in summaries):
        score += weights.get("review_approved", 0.0)
    if any("release" in s.lower() and ("tag" in s.lower() or "publish" in s.lower() or "v" in s.lower()) for s in summaries):
        score += weights.get("release", 0.0)

    return max(-0.5, min(0.5, score))


def apply_session_reward(
    point_ids: List[str],
    reward: float,
    q_cache: QCache | None = None,
    experience: str = "default",
    reward_context: Optional[str] = None,
    observations: Optional[List[Dict]] = None,
    session_id: Optional[str] = None,
) -> int:
    """Apply reward to all memories from a session.

    If observations provided, writes full context to L3 cold storage.
    """
    if not point_ids:
        return 0

    if q_cache is None:
        q_cache = QCache()
        q_cache.load(Q_CACHE_PATH)

    # Generate reward_id and write L3 cold storage
    rwd_id = generate_reward_id()
    cold_context: Dict = {}
    if observations:
        cold_context["observations"] = [compact_observation(o) for o in observations]
        cold_context["observation_count"] = len(observations)
        # Build reward breakdown
        tools = [o.get("tool", "") for o in observations]
        summaries = [o.get("summary", "") for o in observations]
        cold_context["reward_breakdown"] = {
            "commits": sum(1 for s in summaries if "git commit" in s),
            "prs": sum(1 for s in summaries if "gh pr" in s),
            "writes": sum(1 for t in tools if t in ("Write", "Edit")),
            "deploys": sum(1 for s in summaries if "deploy" in s.lower()),
            "decisions": sum(1 for o in observations if o.get("type") == "decision"),
        }
    if session_id:
        cold_context["session_id"] = session_id

    # L4: read first memory's Q before update
    first_q_data = q_cache.get(point_ids[0], experience)
    q_before = first_q_data.get("q_value", 0.0) if first_q_data else None

    updater = QValueUpdater(cache=q_cache)
    layer_rewards = compute_layer_rewards(reward)
    updated = {}
    for mem_id in point_ids:
        updated[mem_id] = updater.update_all_layers(
            mem_id, layer_rewards, experience=experience,
            reward_context=reward_context, reward_id=rwd_id,
        )

    # L4: read first memory's Q after update
    first_q_after = q_cache.get(point_ids[0], experience)
    q_after = first_q_after.get("q_value", 0.0) if first_q_after else None

    # L4: generate explanation with q_before/q_after
    explanation = generate_reward_explanation(
        reward_type="session",
        reward=reward,
        context=cold_context,
        memory_contents=_fetch_memory_contents(point_ids[:5]),
        q_before=q_before,
        q_after=q_after,
        experience=experience,
    )

    log_reward_event(
        reward_id=rwd_id,
        reward_type="session",
        reward=reward,
        memory_ids=point_ids,
        context=cold_context,
        experience=experience,
        explanation=explanation,
    )

    q_cache.save(Q_CACHE_PATH)
    logger.info("Applied session reward=%.2f to %d memories (experience=%s, reward_id=%s)", reward, len(updated), experience, rwd_id)
    return len(updated)


def reward_retrieved_memories(
    session_id: str,
    reward: float,
    experience: str = "default",
    reward_context: Optional[str] = None,
) -> int:
    """Reward memories that were retrieved at session start.

    Closes the loop: memories retrieved -> session outcome -> Q-value update.
    """
    from .retrieval_log import get_session_retrievals

    memory_ids = get_session_retrievals(session_id)
    if not memory_ids:
        return 0

    updated = apply_session_reward(memory_ids, reward, experience=experience, reward_context=reward_context)
    logger.info(
        "Rewarded %d retrieved memories for session %s (reward=%.2f, experience=%s)",
        updated, session_id[:8], reward, experience,
    )
    return updated
