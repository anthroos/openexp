"""L4 — LLM-generated reward explanations.

L1 = Q-value scalar
L2 = reward_contexts (short summaries)
L3 = cold storage (full context)
L4 = human-readable explanation of WHY Q changed

Each reward event can optionally include an LLM-generated explanation
stored as the "explanation" field in the L3 cold storage record.
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Reuse enrichment's lazy client pattern
_anthropic_client = None


def generate_reward_explanation(
    reward_type: str,
    reward: float,
    context: Dict[str, Any],
    memory_contents: Optional[Dict[str, str]] = None,
    q_before: Optional[float] = None,
    q_after: Optional[float] = None,
    experience: str = "default",
) -> Optional[str]:
    """Generate human-readable explanation for a reward event via LLM.

    Args:
        reward_type: "session" | "prediction" | "business" | "calibration" | "summary"
        reward: Reward value applied
        context: L3 context dict (observations, predictions, etc.)
        memory_contents: Dict of {memory_id: content_text} for context
        q_before: Q-value before update (None if unknown)
        q_after: Q-value after update (None if unknown)
        experience: Experience name

    Returns:
        Explanation string or None on failure/disabled.
    """
    from .config import EXPLANATION_ENABLED, EXPLANATION_MODEL, ANTHROPIC_API_KEY

    if not EXPLANATION_ENABLED:
        return None

    if not ANTHROPIC_API_KEY:
        return None

    prompt = _build_explanation_prompt(
        reward_type=reward_type,
        reward=reward,
        context=context,
        memory_contents=memory_contents or {},
        q_before=q_before,
        q_after=q_after,
    )

    try:
        global _anthropic_client

        if _anthropic_client is None:
            import anthropic
            _anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        response = _anthropic_client.messages.create(
            model=EXPLANATION_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        explanation = response.content[0].text.strip()
        return explanation[:500]  # safety cap
    except Exception as e:
        logger.debug("Explanation generation failed: %s", e)
        return None


def _build_explanation_prompt(
    reward_type: str,
    reward: float,
    context: Dict[str, Any],
    memory_contents: Dict[str, str],
    q_before: Optional[float],
    q_after: Optional[float],
) -> str:
    """Build prompt for LLM based on reward_type."""
    contents_text = ""
    if memory_contents:
        for mid, text in list(memory_contents.items())[:5]:
            contents_text += f"- [{mid}]: {text[:200]}\n"

    # Q-value line: only show when both values are known
    q_line = ""
    if q_before is not None and q_after is not None:
        q_line = f"\nQ-value: {q_before:.2f} \u2192 {q_after:.2f}"

    if reward_type == "session":
        breakdown = context.get("reward_breakdown", {})
        return (
            f"\u0421\u0438\u0441\u0442\u0435\u043c\u0430 Q-learning \u0434\u043b\u044f \u043f\u0430\u043c'\u044f\u0442\u0456 AI-\u0430\u0441\u0438\u0441\u0442\u0435\u043d\u0442\u0430.\n\n"
            f"\u0426\u0456 \u043d\u043e\u0442\u0430\u0442\u043a\u0438 \u0431\u0443\u043b\u0438 \u0432\u0438\u043a\u043e\u0440\u0438\u0441\u0442\u0430\u043d\u0456 \u0432 \u0440\u043e\u0431\u043e\u0447\u0456\u0439 \u0441\u0435\u0441\u0456\u0457:\n{contents_text}\n"
            f"\u0420\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442 \u0441\u0435\u0441\u0456\u0457: {breakdown}\n"
            f"Reward: {reward:+.2f}{q_line}\n\n"
            f"\u041f\u043e\u044f\u0441\u043d\u0438 \u0447\u043e\u043c\u0443 \u0446\u0456 \u043d\u043e\u0442\u0430\u0442\u043a\u0438 \u043e\u0442\u0440\u0438\u043c\u0430\u043b\u0438 \u0442\u0430\u043a\u0443 \u043e\u0446\u0456\u043d\u043a\u0443. 2-3 \u0440\u0435\u0447\u0435\u043d\u043d\u044f, \u043a\u043e\u043d\u043a\u0440\u0435\u0442\u043d\u043e."
        )

    elif reward_type == "prediction":
        prediction = context.get("prediction", "")
        outcome = context.get("outcome", "")
        confidence = context.get("confidence", 0)
        return (
            f"\u0421\u0438\u0441\u0442\u0435\u043c\u0430 Q-learning \u0434\u043b\u044f \u043f\u0430\u043c'\u044f\u0442\u0456 AI-\u0430\u0441\u0438\u0441\u0442\u0435\u043d\u0442\u0430.\n\n"
            f"\u041d\u043e\u0442\u0430\u0442\u043a\u0438 \u0432\u0438\u043a\u043e\u0440\u0438\u0441\u0442\u0430\u043d\u0456 \u0434\u043b\u044f \u043f\u0435\u0440\u0435\u0434\u0431\u0430\u0447\u0435\u043d\u043d\u044f:\n{contents_text}\n"
            f"\u041f\u0435\u0440\u0435\u0434\u0431\u0430\u0447\u0435\u043d\u043d\u044f: \"{prediction[:200]}\"\n"
            f"\u0420\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442: \"{outcome[:200]}\"\n"
            f"\u0412\u043f\u0435\u0432\u043d\u0435\u043d\u0456\u0441\u0442\u044c: {confidence}, reward: {reward:+.2f}{q_line}\n\n"
            f"\u041f\u043e\u044f\u0441\u043d\u0438 \u0447\u043e\u043c\u0443 \u043f\u0435\u0440\u0435\u0434\u0431\u0430\u0447\u0435\u043d\u043d\u044f \u0441\u043f\u0440\u0430\u0432\u0434\u0438\u043b\u043e\u0441\u044c/\u043d\u0435 \u0441\u043f\u0440\u0430\u0432\u0434\u0438\u043b\u043e\u0441\u044c. 2-3 \u0440\u0435\u0447\u0435\u043d\u043d\u044f."
        )

    elif reward_type == "business":
        entity_id = context.get("entity_id", "")
        event_name = context.get("event_name", "")
        details = context.get("details", {})
        return (
            f"\u0421\u0438\u0441\u0442\u0435\u043c\u0430 Q-learning \u0434\u043b\u044f \u043f\u0430\u043c'\u044f\u0442\u0456 AI-\u0430\u0441\u0438\u0441\u0442\u0435\u043d\u0442\u0430.\n\n"
            f"\u041d\u043e\u0442\u0430\u0442\u043a\u0438 \u043f\u043e\u0432'\u044f\u0437\u0430\u043d\u0456 \u0437 \u043a\u043b\u0456\u0454\u043d\u0442\u043e\u043c:\n{contents_text}\n"
            f"\u0411\u0456\u0437\u043d\u0435\u0441-\u043f\u043e\u0434\u0456\u044f: {event_name} \u0434\u043b\u044f {entity_id}\n"
            f"\u0414\u0435\u0442\u0430\u043b\u0456: {details}\n"
            f"Reward: {reward:+.2f}{q_line}\n\n"
            f"\u041f\u043e\u044f\u0441\u043d\u0438 \u0437\u0432'\u044f\u0437\u043e\u043a \u043c\u0456\u0436 \u043d\u043e\u0442\u0430\u0442\u043a\u0430\u043c\u0438 \u0456 \u0446\u0456\u0454\u044e \u043f\u043e\u0434\u0456\u0454\u044e. 2-3 \u0440\u0435\u0447\u0435\u043d\u043d\u044f."
        )

    elif reward_type == "calibration":
        reason = context.get("reason", "manual calibration")
        old_q = context.get("old_q_value", q_before or 0.0)
        new_q = context.get("new_q_value", q_after or 0.0)
        return (
            f"\u0421\u0438\u0441\u0442\u0435\u043c\u0430 Q-learning \u0434\u043b\u044f \u043f\u0430\u043c'\u044f\u0442\u0456 AI-\u0430\u0441\u0438\u0441\u0442\u0435\u043d\u0442\u0430.\n\n"
            f"\u041d\u043e\u0442\u0430\u0442\u043a\u0438:\n{contents_text}\n"
            f"\u0420\u0443\u0447\u043d\u0430 \u043a\u0430\u043b\u0456\u0431\u0440\u0430\u0446\u0456\u044f Q-value: {old_q:.2f} \u2192 {new_q:.2f}\n"
            f"\u041f\u0440\u0438\u0447\u0438\u043d\u0430: {reason}\n\n"
            f"\u041f\u043e\u044f\u0441\u043d\u0438 \u0449\u043e \u043e\u0437\u043d\u0430\u0447\u0430\u0454 \u0446\u044f \u043a\u0430\u043b\u0456\u0431\u0440\u0430\u0446\u0456\u044f. 1-2 \u0440\u0435\u0447\u0435\u043d\u043d\u044f."
        )

    elif reward_type in ("daily_retrospective", "weekly_retrospective", "monthly_retrospective"):
        level = reward_type.replace("_retrospective", "")
        reason = context.get("reason", "")
        action = context.get("action", "")
        return (
            f"\u0421\u0438\u0441\u0442\u0435\u043c\u0430 Q-learning \u0434\u043b\u044f \u043f\u0430\u043c'\u044f\u0442\u0456 AI-\u0430\u0441\u0438\u0441\u0442\u0435\u043d\u0442\u0430.\n\n"
            f"\u041d\u043e\u0442\u0430\u0442\u043a\u0438:\n{contents_text}\n"
            f"{level.title()} \u0440\u0435\u0442\u0440\u043e\u0441\u043f\u0435\u043a\u0442\u0438\u0432\u0430, \u0434\u0456\u044f: {action}\n"
            f"\u041f\u0440\u0438\u0447\u0438\u043d\u0430: {reason[:200]}\n"
            f"Reward: {reward:+.2f}{q_line}\n\n"
            f"\u041f\u043e\u044f\u0441\u043d\u0438 \u0447\u043e\u043c\u0443 \u0446\u044f \u043f\u0430\u043c'\u044f\u0442\u044c \u0431\u0443\u043b\u0430 \u043f\u0435\u0440\u0435\u043e\u0446\u0456\u043d\u0435\u043d\u0430. 2-3 \u0440\u0435\u0447\u0435\u043d\u043d\u044f."
        )

    elif reward_type == "summary":
        total_events = context.get("total_events", 0)
        total_reward = context.get("total_reward", 0)
        events_summary = context.get("events_summary", [])
        return (
            f"\u0421\u0438\u0441\u0442\u0435\u043c\u0430 Q-learning \u0434\u043b\u044f \u043f\u0430\u043c'\u044f\u0442\u0456 AI-\u0430\u0441\u0438\u0441\u0442\u0435\u043d\u0442\u0430.\n\n"
            f"\u0417\u0430\u0433\u0430\u043b\u044c\u043d\u0438\u0439 \u043f\u0456\u0434\u0441\u0443\u043c\u043e\u043a \u0434\u043b\u044f \u043d\u043e\u0442\u0430\u0442\u043a\u0438:\n{contents_text}\n"
            f"\u0412\u0441\u044c\u043e\u0433\u043e reward-\u043f\u043e\u0434\u0456\u0439: {total_events}, \u0441\u0443\u043c\u0430\u0440\u043d\u0438\u0439 reward: {total_reward:+.2f}{q_line}\n"
            f"\u041e\u0441\u0442\u0430\u043d\u043d\u0456 \u043f\u043e\u0434\u0456\u0457: {events_summary}\n\n"
            f"\u041f\u043e\u044f\u0441\u043d\u0438 \u0437\u0430\u0433\u0430\u043b\u044c\u043d\u0443 \u0446\u0456\u043d\u043d\u0456\u0441\u0442\u044c \u0446\u0456\u0454\u0457 \u043d\u043e\u0442\u0430\u0442\u043a\u0438. 2-3 \u0440\u0435\u0447\u0435\u043d\u043d\u044f."
        )

    # fallback for unknown types
    q_fallback = f"\nQ: {q_before:.2f} \u2192 {q_after:.2f}" if q_before is not None and q_after is not None else ""
    return (
        f"\u0421\u0438\u0441\u0442\u0435\u043c\u0430 Q-learning. Reward event type={reward_type}, reward={reward:+.2f}.\n"
        f"Context: {str(context)[:300]}{q_fallback}\n"
        f"\u041f\u043e\u044f\u0441\u043d\u0438 \u043a\u043e\u0440\u043e\u0442\u043a\u043e. 2-3 \u0440\u0435\u0447\u0435\u043d\u043d\u044f."
    )


def fetch_memory_contents(memory_ids: List[str], limit: int = 5) -> Dict[str, str]:
    """Fetch memory texts from Qdrant for explanation context.

    Returns dict of {memory_id: content_text}. Graceful on failure.
    """
    if not memory_ids:
        return {}

    try:
        from .config import COLLECTION_NAME
        from .direct_search import _get_qdrant

        qc = _get_qdrant()
        ids_to_fetch = memory_ids[:limit]

        results = qc.retrieve(
            collection_name=COLLECTION_NAME,
            ids=ids_to_fetch,
            with_payload=True,
            with_vectors=False,
        )

        contents = {}
        for point in results:
            payload = point.payload or {}
            content = payload.get("content", payload.get("memory", ""))
            if content:
                contents[str(point.id)] = content[:300]
        return contents
    except Exception as e:
        logger.debug("Failed to fetch memory contents: %s", e)
        return {}


# Backward-compat alias (was private, now public)
_fetch_memory_contents = fetch_memory_contents
