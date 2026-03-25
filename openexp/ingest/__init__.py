"""OpenExp Ingest — Observation pipeline into Qdrant.

Public API:
    ingest_session()  — full pipeline: observations + sessions + reward
"""
import importlib
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _load_configured_resolvers() -> List:
    """Load outcome resolvers from OPENEXP_OUTCOME_RESOLVERS env var.

    Format: "module:ClassName,module2:ClassName2"
    Example: "openexp.resolvers.crm_csv:CRMCSVResolver"
    """
    from ..core.config import OUTCOME_RESOLVERS

    if not OUTCOME_RESOLVERS:
        return []

    ALLOWED_PREFIX = "openexp.resolvers."

    resolvers = []
    for entry in OUTCOME_RESOLVERS.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            module_path, class_name = entry.rsplit(":", 1)
            if not module_path.startswith(ALLOWED_PREFIX):
                logger.error("Rejected resolver %s: must start with %s", module_path, ALLOWED_PREFIX)
                continue
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
            resolvers.append(cls())
            logger.info("Loaded outcome resolver: %s", entry)
        except Exception as e:
            logger.error("Failed to load resolver %s: %s", entry, e)

    return resolvers


def ingest_session(
    max_count: int = 0,
    dry_run: bool = False,
    sessions_only: bool = False,
    session_id: Optional[str] = None,
) -> Dict:
    """Full ingest pipeline: observations + sessions + reward."""
    from .observation import ingest_observations
    from .session_summary import ingest_sessions
    from .reward import compute_session_reward, apply_session_reward, reward_retrieved_memories, _build_session_reward_context

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
            reward_ctx = _build_session_reward_context(raw_obs, reward)
            updated = apply_session_reward(
                point_ids, reward, reward_context=reward_ctx,
                observations=raw_obs, session_id=session_id,
            )
            result["reward"] = {"applied": True, "value": reward, "updated": updated}
            logger.info("Session reward=%.2f applied to %d memories", reward, updated)
        else:
            result["reward"] = {"applied": False, "value": 0.0, "reason": "neutral session"}
            reward_ctx = None
    else:
        result["reward"] = {"applied": False, "reason": "no new observations"}
        reward_ctx = None

    if session_id:
        reward_val = result.get("reward", {}).get("value", 0.0)
        if reward_val and reward_val != 0.0:
            retrieved_updated = reward_retrieved_memories(session_id, reward_val, reward_context=reward_ctx)
            result["reward"]["retrieved_memories_rewarded"] = retrieved_updated
        else:
            result["reward"]["retrieved_memories_rewarded"] = 0

    # Run outcome resolvers (CRM stage transitions, etc.)
    try:
        resolvers = _load_configured_resolvers()
        if resolvers:
            from ..outcome import resolve_outcomes
            from ..core.config import Q_CACHE_PATH
            from ..core.q_value import QCache, QValueUpdater

            q_cache = QCache()
            q_cache.load(Q_CACHE_PATH)
            q_updater = QValueUpdater(cache=q_cache)

            outcome_result = resolve_outcomes(
                resolvers=resolvers,
                q_cache=q_cache,
                q_updater=q_updater,
            )
            result["outcomes"] = outcome_result

            if outcome_result.get("total_events", 0) > 0:
                q_cache.save(Q_CACHE_PATH)
    except Exception as e:
        logger.error("Outcome resolution failed: %s", e, exc_info=True)
        result["outcomes"] = {"error": "outcome_resolution_failed"}

    return result
