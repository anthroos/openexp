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
    from .reward import compute_session_reward, reward_retrieved_memories, _build_session_reward_context
    from ..core.experience import get_active_experience

    # Load active experience so weights/config are used throughout
    experience = get_active_experience()

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

    # Clean up internal fields from observation result
    obs_data = result.get("observations", {})
    obs_data.pop("_point_ids", [])
    raw_obs = obs_data.pop("_raw_observations", [])

    # --- Session Reward: reward RECALLED memories, not ingested ones ---
    # Filter observations to THIS session only (fixes cumulative counting bug)
    if session_id and raw_obs:
        session_obs = [o for o in raw_obs if session_id in o.get("session_id", "")]
    else:
        session_obs = raw_obs

    if session_id and session_obs:
        # BUG FIX: pass experience weights instead of hardcoded defaults
        reward = compute_session_reward(session_obs, weights=experience.session_reward_weights)
        if reward != 0.0:
            reward_ctx = _build_session_reward_context(session_obs, reward)
            # Reward only memories that were RECALLED at session start (closed loop)
            retrieved_updated = reward_retrieved_memories(
                session_id, reward,
                experience=experience.name,
                reward_context=reward_ctx,
                reward_memory_types=experience.reward_memory_types,
            )
            result["reward"] = {
                "applied": True,
                "value": reward,
                "retrieved_memories_rewarded": retrieved_updated,
                "session_observations": len(session_obs),
                "experience": experience.name,
            }
            logger.info(
                "Session reward=%.2f applied to %d retrieved memories (from %d session obs, experience=%s)",
                reward, retrieved_updated, len(session_obs), experience.name,
            )
        else:
            result["reward"] = {"applied": False, "value": 0.0, "reason": "neutral session", "retrieved_memories_rewarded": 0}
    elif not session_id:
        result["reward"] = {"applied": False, "reason": "no session_id provided", "retrieved_memories_rewarded": 0}
    else:
        result["reward"] = {"applied": False, "reason": "no observations for this session", "retrieved_memories_rewarded": 0}

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
                experience=experience.name,
            )
            result["outcomes"] = outcome_result

            if outcome_result.get("total_events", 0) > 0:
                q_cache.save(Q_CACHE_PATH)
    except Exception as e:
        logger.error("Outcome resolution failed: %s", e, exc_info=True)
        result["outcomes"] = {"error": "outcome_resolution_failed"}

    return result
