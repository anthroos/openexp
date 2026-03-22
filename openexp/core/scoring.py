"""Composite relevance scoring for memory retrieval.

Replaces simple cosine similarity with a weighted composite:
  score = (w_semantic * similarity + w_recency * decay + w_importance * weight) * access_boost
"""
import math
from datetime import datetime, timezone

# Base importance by memory type
TYPE_BOOST = {
    "decision": 1.0,
    "preference": 0.95,
    "relationship": 0.9,
    "insight": 0.9,
    "procedure": 0.85,
    "fact": 0.8,
    "event": 0.7,
    "intention": 0.75,
    "plan": 0.85,
    "commitment": 0.9,
    "action": 0.8,
    "outcome": 0.85,
    "cancellation": 0.6,
}

# Types that resist decay (floor at 40%)
PERSISTENT_TYPES = {"decision", "preference", "relationship", "commitment"}

# Default tuning parameters
DEFAULT_WEIGHTS = {
    "w_semantic": 0.50,
    "w_recency": 0.30,
    "w_importance": 0.20,
}

DEFAULT_HALF_LIFE_DAYS = 90.0


def composite_score(
    semantic_similarity: float,
    created_at: str | None = None,
    importance: float = 0.8,
    access_count: int = 0,
    memory_type: str = "fact",
    *,
    w_semantic: float = DEFAULT_WEIGHTS["w_semantic"],
    w_recency: float = DEFAULT_WEIGHTS["w_recency"],
    w_importance: float = DEFAULT_WEIGHTS["w_importance"],
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
) -> float:
    """Compute composite relevance score for a memory."""
    recency = _compute_recency(created_at, half_life_days)

    if memory_type in PERSISTENT_TYPES:
        recency = max(recency, 0.4)

    type_weight = TYPE_BOOST.get(memory_type, 0.8)
    weighted_importance = importance * type_weight

    access_boost = min(1.0 + (access_count * 0.05), 1.5)

    score = (
        w_semantic * semantic_similarity
        + w_recency * recency
        + w_importance * weighted_importance
    ) * access_boost

    return min(max(score, 0.0), 1.0)


def score_results(results: list[dict], query_similarity_key: str = "score") -> list[dict]:
    """Re-score a list of search results using composite scoring."""
    scored = []
    for r in results:
        meta = r.get("metadata", {})
        sim = r.get(query_similarity_key, 0.5)

        importance = (
            meta.get("importance") or
            r.get("importance") or
            0.8
        )

        cs = composite_score(
            semantic_similarity=sim,
            created_at=meta.get("extracted_at", meta.get("created_at", meta.get("date"))),
            importance=importance,
            access_count=meta.get("access_count", 0),
            memory_type=meta.get("type", "fact"),
        )
        r["composite_score"] = cs
        scored.append(r)

    scored.sort(key=lambda x: x["composite_score"], reverse=True)
    return scored


def _compute_recency(created_str: str | None, half_life_days: float = DEFAULT_HALF_LIFE_DAYS) -> float:
    """Compute recency factor from ISO date string using exponential decay."""
    if not created_str:
        return 0.5
    try:
        created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        age_days = (now - created_dt.replace(tzinfo=timezone.utc if created_dt.tzinfo is None else created_dt.tzinfo)).days
        age_days = max(age_days, 0)
        return math.exp(-0.693 * age_days / half_life_days)
    except (ValueError, TypeError):
        return 0.5
