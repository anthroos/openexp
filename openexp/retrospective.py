"""Multi-level retrospective system for OpenExp.

5th reward path: daily/weekly/monthly LLM-based re-evaluation of Q-values.
Session rewards see one session at a time — retrospectives see the full picture.

Uses claude -p pipe mode (free on Max subscription) for deep analysis,
following the same pattern as extract_decisions.py.
"""
import json
import logging
import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from .core.config import (
    COLLECTION_NAME,
    DATA_DIR,
    Q_CACHE_PATH,
    SESSIONS_DIR,
)
from .core.explanation import generate_reward_explanation, fetch_memory_contents
from .core.q_value import QCache, QValueUpdater, compute_layer_rewards
from .core.reward_log import (
    REWARD_LOG_PATH,
    generate_reward_id,
    log_reward_event,
)
from .retrospective_prompts import DAILY_PROMPT, WEEKLY_PROMPT, MONTHLY_PROMPT

logger = logging.getLogger(__name__)

WATERMARK_PATH = DATA_DIR / "retrospective_watermark.json"
Q_STATS_PATH = DATA_DIR / "q_stats_daily.jsonl"
MAX_ADJUSTMENTS = 20
CONTEXT_LIMIT = 30000


class RetroLevel(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


# ---------------------------------------------------------------------------
# Watermark (idempotency)
# ---------------------------------------------------------------------------

def _load_watermark() -> Dict:
    if WATERMARK_PATH.exists():
        try:
            return json.loads(WATERMARK_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"daily": {}, "weekly": {}, "monthly": {}}


def _save_watermark(wm: Dict) -> None:
    WATERMARK_PATH.parent.mkdir(parents=True, exist_ok=True)
    WATERMARK_PATH.write_text(json.dumps(wm, ensure_ascii=False, indent=2))


def _is_already_done(level: RetroLevel, period: str) -> bool:
    wm = _load_watermark()
    return period in wm.get(level.value, {})


def _mark_done(level: RetroLevel, period: str, memory_id: str) -> None:
    wm = _load_watermark()
    wm.setdefault(level.value, {})[period] = memory_id
    _save_watermark(wm)


# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------

def gather_daily_data(date_str: str) -> Dict[str, Any]:
    """Collect sessions, reward events, and key memories for a given date.

    Args:
        date_str: "YYYY-MM-DD"
    """
    data: Dict[str, Any] = {"date": date_str, "sessions": [], "reward_events": [], "memories": []}

    # 1. Session summaries
    for f in sorted(SESSIONS_DIR.glob(f"{date_str}-*.md")):
        try:
            content = f.read_text()[:2000]
            data["sessions"].append({"file": f.name, "content": content})
        except OSError:
            continue

    # 2. Reward events from reward_log.jsonl (filter by date) — stream line-by-line
    if REWARD_LOG_PATH.exists():
        try:
            with open(REWARD_LOG_PATH, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    if date_str not in line:
                        continue
                    try:
                        record = json.loads(line)
                        ts = record.get("timestamp", "")
                        if ts.startswith(date_str):
                            data["reward_events"].append({
                                "reward_id": record.get("reward_id"),
                                "reward_type": record.get("reward_type"),
                                "reward": record.get("reward"),
                                "memory_ids": record.get("memory_ids", [])[:5],
                                "explanation": record.get("explanation", "")[:200],
                            })
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass

    # 3. Key memories created/used today (from Qdrant)
    try:
        from .core.direct_search import _get_qdrant
        qc = _get_qdrant()
        # Scroll for memories created on this date
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        results = qc.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=Filter(must=[
                FieldCondition(key="source", match=MatchValue(value="decision_extraction")),
            ]),
            limit=50,
            with_payload=True,
            with_vectors=False,
        )
        points, _ = results
        q_cache = QCache()
        q_cache.load(Q_CACHE_PATH)
        for p in points:
            created = p.payload.get("created_at", "")
            if created.startswith(date_str):
                q_data = q_cache.get(str(p.id)) or {}
                data["memories"].append({
                    "memory_id": str(p.id),
                    "content": p.payload.get("memory", "")[:300],
                    "type": p.payload.get("type", p.payload.get("memory_type", "")),
                    "q_value": q_data.get("q_value", 0.0),
                    "q_visits": q_data.get("q_visits", 0),
                })
    except Exception as e:
        logger.warning("Failed to fetch memories for daily data: %s", e)

    return data


def gather_weekly_data(year: int, week: int) -> Dict[str, Any]:
    """Collect daily retrospectives and reward events for an ISO week."""
    data: Dict[str, Any] = {"year": year, "week": week, "daily_retrospectives": [], "reward_events": [], "q_value_changes": []}

    # Date range for ISO week (Monday=1 through Sunday=7)
    start = datetime.fromisocalendar(year, week, 1)
    dates = [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

    # 1. Daily retrospective memories from Qdrant
    try:
        from .core.direct_search import _get_qdrant
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        qc = _get_qdrant()
        results = qc.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=Filter(must=[
                FieldCondition(key="memory_type", match=MatchValue(value="retrospective_daily")),
            ]),
            limit=7,
            with_payload=True,
            with_vectors=False,
        )
        points, _ = results
        for p in points:
            created = p.payload.get("created_at", "")[:10]
            if created in dates:
                data["daily_retrospectives"].append({
                    "date": created,
                    "content": p.payload.get("memory", "")[:500],
                })
    except Exception as e:
        logger.warning("Failed to fetch daily retrospectives: %s", e)

    # 2. Reward events for the week — stream line-by-line
    dates_set = set(dates)
    if REWARD_LOG_PATH.exists():
        try:
            with open(REWARD_LOG_PATH, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        ts = record.get("timestamp", "")[:10]
                        if ts in dates_set:
                            data["reward_events"].append({
                                "reward_id": record.get("reward_id"),
                                "reward_type": record.get("reward_type"),
                                "reward": record.get("reward"),
                                "memory_ids": record.get("memory_ids", [])[:3],
                            })
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass

    # 3. Top Q-value changes this week (from q_stats_daily.jsonl if exists)
    if Q_STATS_PATH.exists():
        try:
            for line in Q_STATS_PATH.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    if record.get("date", "") in dates:
                        data["q_value_changes"].append(record)
                except json.JSONDecodeError:
                    continue
        except OSError:
            pass

    return data


def gather_monthly_data(year: int, month: int) -> Dict[str, Any]:
    """Collect weekly retrospectives and Q-value stats for a month."""
    data: Dict[str, Any] = {"year": year, "month": month, "weekly_retrospectives": [], "q_stats": [], "top_bottom_memories": []}
    month_prefix = f"{year}-{month:02d}"

    # 1. Weekly retrospective memories
    try:
        from .core.direct_search import _get_qdrant
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        qc = _get_qdrant()
        results = qc.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=Filter(must=[
                FieldCondition(key="memory_type", match=MatchValue(value="retrospective_weekly")),
            ]),
            limit=5,
            with_payload=True,
            with_vectors=False,
        )
        points, _ = results
        for p in points:
            created = p.payload.get("created_at", "")
            if created[:7] == month_prefix:
                data["weekly_retrospectives"].append({
                    "content": p.payload.get("memory", "")[:500],
                })
    except Exception as e:
        logger.warning("Failed to fetch weekly retrospectives: %s", e)

    # 2. Q-value stats from daily stats file — stream line-by-line
    if Q_STATS_PATH.exists():
        try:
            with open(Q_STATS_PATH, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        if record.get("date", "").startswith(month_prefix):
                            data["q_stats"].append(record)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass

    # 3. Top and bottom memories by Q-value
    try:
        q_cache = QCache()
        q_cache.load(Q_CACHE_PATH)
        all_q = q_cache.get_all_q_values()
        if all_q:
            data["q_stats_summary"] = {
                "count": len(all_q),
                "mean": round(sum(all_q) / len(all_q), 4),
                "min": round(min(all_q), 4),
                "max": round(max(all_q), 4),
            }
    except Exception:
        pass

    return data


# ---------------------------------------------------------------------------
# LLM analysis via claude -p
# ---------------------------------------------------------------------------

def _build_prompt(level: RetroLevel, data: Dict) -> str:
    """Build the LLM prompt for a given retrospective level."""
    if level == RetroLevel.DAILY:
        sessions_text = ""
        for s in data.get("sessions", [])[:10]:
            sessions_text += f"\n### {s['file']}\n{s['content'][:1000]}\n"
        rewards_text = json.dumps(data.get("reward_events", [])[:20], indent=2, default=str)
        memories_text = json.dumps(data.get("memories", [])[:30], indent=2, default=str)

        prompt = DAILY_PROMPT.format(
            sessions_data=sessions_text[:CONTEXT_LIMIT // 3] or "(no sessions)",
            reward_events=rewards_text[:CONTEXT_LIMIT // 3] or "(no reward events)",
            memories_data=memories_text[:CONTEXT_LIMIT // 3] or "(no memories)",
        )

    elif level == RetroLevel.WEEKLY:
        daily_text = json.dumps(data.get("daily_retrospectives", []), indent=2, default=str)
        rewards_text = json.dumps(data.get("reward_events", [])[:30], indent=2, default=str)
        changes_text = json.dumps(data.get("q_value_changes", []), indent=2, default=str)

        prompt = WEEKLY_PROMPT.format(
            daily_retrospectives=daily_text[:CONTEXT_LIMIT // 3] or "(no daily retrospectives)",
            reward_events=rewards_text[:CONTEXT_LIMIT // 3] or "(no reward events)",
            q_value_changes=changes_text[:CONTEXT_LIMIT // 3] or "(no Q-value data)",
        )

    elif level == RetroLevel.MONTHLY:
        weekly_text = json.dumps(data.get("weekly_retrospectives", []), indent=2, default=str)
        stats_text = json.dumps(data.get("q_stats", [])[-10:], indent=2, default=str)
        top_bottom = json.dumps(data.get("top_bottom_memories", []), indent=2, default=str)

        prompt = MONTHLY_PROMPT.format(
            weekly_retrospectives=weekly_text[:CONTEXT_LIMIT // 3] or "(no weekly retrospectives)",
            q_stats=stats_text[:CONTEXT_LIMIT // 3] or "(no Q-value stats)",
            top_bottom_memories=top_bottom[:CONTEXT_LIMIT // 3] or "(no memory data)",
        )
    else:
        raise ValueError(f"Unknown level: {level}")

    return prompt


def analyze_with_llm(prompt: str) -> Optional[Dict]:
    """Call claude -p (Max subscription pipe mode) for retrospective analysis.

    Returns parsed JSON or None on failure. Same pattern as extract_decisions.py.
    """
    try:
        env = {**os.environ, "OPENEXP_EXTRACT_RUNNING": "1"}
        # Remove ANTHROPIC_API_KEY so claude -p uses Max subscription, not API credits
        env.pop("ANTHROPIC_API_KEY", None)
        result = subprocess.run(
            ["claude", "-p", "--model", "opus"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=180,  # 3 min for retrospective analysis
            env=env,
        )

        if result.returncode != 0:
            logger.error("claude -p failed (exit=%d): %s", result.returncode, result.stderr[:500])
            return None

        response_text = result.stdout.strip()
        if not response_text:
            logger.error("claude -p returned empty response")
            return None

        # Extract JSON (may be wrapped in code block)
        json_text = response_text
        if "```json" in json_text:
            json_text = json_text.split("```json")[1].split("```")[0]
        elif "```" in json_text:
            json_text = json_text.split("```")[1].split("```")[0]

        parsed = json.loads(json_text.strip())
        if not isinstance(parsed, dict):
            logger.error("LLM returned non-dict: %s", type(parsed))
            return None

        logger.info("LLM analysis: %d adjustments, %d insights",
                     len(parsed.get("adjustments", [])),
                     len(parsed.get("insights", [])))
        return parsed

    except subprocess.TimeoutExpired:
        logger.error("claude -p timed out after 180s")
        return None
    except json.JSONDecodeError as e:
        logger.error("Failed to parse LLM response: %s", e)
        return None
    except FileNotFoundError:
        logger.error("claude CLI not found in PATH")
        return None
    except Exception as e:
        logger.error("LLM analysis failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Apply adjustments
# ---------------------------------------------------------------------------

def apply_adjustments(
    adjustments: List[Dict],
    level: RetroLevel,
    q_cache: QCache,
    q_updater: QValueUpdater,
    experience: str = "default",
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Apply LLM-suggested Q-value adjustments.

    Returns summary of applied changes.
    """
    applied = 0
    skipped = 0
    details = []

    # Validate memories exist in Qdrant (not just Q-cache)
    qdrant_client = None
    try:
        from .core.direct_search import _get_qdrant
        from .core.config import COLLECTION_NAME
        qdrant_client = _get_qdrant()
    except Exception as e:
        logger.warning("Qdrant unavailable for validation, using Q-cache only: %s", e)

    for adj in adjustments[:MAX_ADJUSTMENTS]:
        memory_id = adj.get("memory_id", "")
        action = adj.get("action", "")
        reward = adj.get("reward", 0.0)
        target_q = adj.get("target_q")
        reason = adj.get("reason", "")

        if not memory_id:
            skipped += 1
            continue

        # Validate memory_id exists in Q-cache
        existing = q_cache.get(memory_id, experience)
        if existing is None:
            logger.warning("Skipping unknown memory_id: %s", memory_id[:12])
            skipped += 1
            continue

        # Validate memory_id exists in Qdrant (prevents orphan rewards)
        if qdrant_client is not None:
            try:
                points = qdrant_client.retrieve(
                    collection_name=COLLECTION_NAME, ids=[memory_id],
                )
                if not points:
                    logger.warning("Memory %s in Q-cache but not in Qdrant, skipping", memory_id[:12])
                    skipped += 1
                    continue
            except Exception as e:
                logger.warning("Qdrant check failed for %s: %s", memory_id[:12], e)

        q_before = existing.get("q_value", 0.0)
        reward_type = f"{level.value}_retrospective"

        if dry_run:
            details.append({
                "memory_id": memory_id[:12],
                "action": action,
                "reward": reward,
                "q_before": q_before,
                "reason": reason[:100],
            })
            applied += 1
            continue

        rwd_id = generate_reward_id()
        reward_ctx = f"Retro {level.value}: {reason[:80]}"

        if action == "override" and target_q is not None:
            q_updater.set_q_value(
                memory_id, target_q, experience=experience,
                reward_context=reward_ctx, reward_id=rwd_id,
            )
        elif action in ("promote", "demote", "adjust"):
            r = abs(reward) if action == "promote" else -abs(reward) if action == "demote" else reward
            layer_rewards = compute_layer_rewards(r)
            q_updater.update_all_layers(
                memory_id, layer_rewards, experience=experience,
                reward_context=reward_ctx, reward_id=rwd_id,
            )
        else:
            logger.warning("Unknown action '%s' for memory %s", action, memory_id[:12])
            skipped += 1
            continue

        q_after_data = q_cache.get(memory_id, experience) or {}
        q_after = q_after_data.get("q_value", 0.0)

        # L4 explanation
        explanation = generate_reward_explanation(
            reward_type=reward_type,
            reward=reward,
            context={"reason": reason, "action": action, "level": level.value},
            memory_contents=fetch_memory_contents([memory_id], limit=1),
            q_before=q_before,
            q_after=q_after,
            experience=experience,
        )

        # L3 cold storage
        log_reward_event(
            reward_id=rwd_id,
            reward_type=reward_type,
            reward=reward,
            memory_ids=[memory_id],
            context={"reason": reason, "action": action, "level": level.value},
            experience=experience,
            explanation=explanation,
        )

        details.append({
            "memory_id": memory_id[:12],
            "action": action,
            "q_before": round(q_before, 3),
            "q_after": round(q_after, 3),
        })
        applied += 1

    if not dry_run:
        q_cache.save(Q_CACHE_PATH)

    return {"applied": applied, "skipped": skipped, "details": details}


# ---------------------------------------------------------------------------
# Store retrospective as memory + insights
# ---------------------------------------------------------------------------

def store_retrospective_memory(
    level: RetroLevel,
    period: str,
    analysis: Dict,
    experience: str = "default",
) -> str:
    """Store the retrospective itself as a Qdrant memory.

    Returns the point ID.
    """
    from .core.direct_search import _embed, _get_qdrant
    from qdrant_client.models import PointStruct

    summary = analysis.get("summary", f"{level.value} retrospective for {period}")
    patterns = analysis.get("patterns", [])
    content = f"{summary}\nPatterns: {'; '.join(patterns)}" if patterns else summary

    memory_type = f"retrospective_{level.value}"
    point_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    vector = _embed(content)
    payload = {
        "memory": content,
        "memory_type": memory_type,
        "type": "insight",
        "agent_id": "retrospective",
        "source": "retrospective",
        "importance": 0.8,
        "created_at": now,
        "status": "active",
        "metadata": {
            "level": level.value,
            "period": period,
            "experience": experience,
            "adjustments_count": len(analysis.get("adjustments", [])),
        },
    }

    qc = _get_qdrant()
    qc.upsert(collection_name=COLLECTION_NAME, points=[
        PointStruct(id=point_id, vector=vector, payload=payload),
    ])

    # Store insights as separate memories
    for insight in analysis.get("insights", [])[:5]:
        insight_content = insight.get("content", "")
        if not insight_content:
            continue
        insight_id = str(uuid.uuid4())
        insight_vec = _embed(insight_content)
        insight_payload = {
            "memory": insight_content,
            "memory_type": "insight",
            "type": "insight",
            "agent_id": "retrospective",
            "source": f"retrospective_{level.value}",
            "importance": insight.get("importance", 0.7),
            "tags": insight.get("tags", []),
            "created_at": now,
            "status": "active",
        }
        qc.upsert(collection_name=COLLECTION_NAME, points=[
            PointStruct(id=insight_id, vector=insight_vec, payload=insight_payload),
        ])

    logger.info("Stored %s retrospective memory %s + %d insights",
                 level.value, point_id[:8], len(analysis.get("insights", [])))
    return point_id


def save_daily_q_stats(date_str: str, experience: str = "default") -> None:
    """Append daily Q-value statistics to q_stats_daily.jsonl."""
    try:
        q_cache = QCache()
        q_cache.load(Q_CACHE_PATH)
        all_q = q_cache.get_all_q_values(experience)
        if not all_q:
            return

        stats = {
            "date": date_str,
            "experience": experience,
            "count": len(all_q),
            "mean": round(sum(all_q) / len(all_q), 4),
            "min": round(min(all_q), 4),
            "max": round(max(all_q), 4),
        }

        Q_STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(Q_STATS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(stats, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("Failed to save daily Q stats: %s", e)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def run_retrospective(
    level: RetroLevel,
    period: str,
    experience: str = "default",
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Run a retrospective for a given level and period.

    Args:
        level: DAILY, WEEKLY, or MONTHLY
        period: "YYYY-MM-DD" for daily, "YYYY-Www" for weekly, "YYYY-MM" for monthly
        experience: Experience name for Q-value operations
        dry_run: If True, run LLM analysis but don't apply changes

    Returns:
        Summary of the retrospective.
    """
    # 1. Idempotency check
    if not dry_run and _is_already_done(level, period):
        return {"status": "already_done", "level": level.value, "period": period}

    # 2. Gather data
    try:
        if level == RetroLevel.DAILY:
            # Validate YYYY-MM-DD
            datetime.strptime(period, "%Y-%m-%d")
            data = gather_daily_data(period)
        elif level == RetroLevel.WEEKLY:
            # Parse and validate "YYYY-Www" format
            parts = period.split("-W")
            if len(parts) != 2:
                return {"error": f"Invalid weekly period format: {period!r} (expected YYYY-Www)"}
            year, week = int(parts[0]), int(parts[1])
            datetime.fromisocalendar(year, week, 1)  # validate
            data = gather_weekly_data(year, week)
        elif level == RetroLevel.MONTHLY:
            # Parse and validate "YYYY-MM" format
            parts = period.split("-")
            if len(parts) != 2:
                return {"error": f"Invalid monthly period format: {period!r} (expected YYYY-MM)"}
            year, month = int(parts[0]), int(parts[1])
            if not (1 <= month <= 12):
                return {"error": f"Invalid month: {month}"}
            data = gather_monthly_data(year, month)
        else:
            return {"error": f"Unknown level: {level}"}
    except (ValueError, IndexError) as e:
        return {"error": f"Invalid period format: {period!r} — {e}"}

    # Check if there's enough data
    has_data = (
        data.get("sessions") or data.get("reward_events")
        or data.get("daily_retrospectives") or data.get("weekly_retrospectives")
    )
    if not has_data:
        return {"status": "no_data", "level": level.value, "period": period}

    # 3. Build prompt and run LLM analysis
    prompt = _build_prompt(level, data)
    logger.info("Running %s retrospective for %s (%d chars prompt)", level.value, period, len(prompt))

    if dry_run:
        return {
            "status": "dry_run",
            "level": level.value,
            "period": period,
            "data_summary": {
                "sessions": len(data.get("sessions", [])),
                "reward_events": len(data.get("reward_events", [])),
                "memories": len(data.get("memories", [])),
                "daily_retrospectives": len(data.get("daily_retrospectives", [])),
                "weekly_retrospectives": len(data.get("weekly_retrospectives", [])),
            },
            "prompt_length": len(prompt),
        }

    analysis = analyze_with_llm(prompt)
    if analysis is None:
        return {"status": "llm_failed", "level": level.value, "period": period}

    # 4. Apply Q-value adjustments
    q_cache = QCache()
    q_cache.load(Q_CACHE_PATH)
    q_updater = QValueUpdater(cache=q_cache)

    adjustments = analysis.get("adjustments", [])
    adj_result = apply_adjustments(
        adjustments, level, q_cache, q_updater,
        experience=experience, dry_run=False,
    )

    # 5. Store retrospective memory + insights
    memory_id = store_retrospective_memory(level, period, analysis, experience)

    # 6. Save daily Q stats (for monthly trajectory)
    if level == RetroLevel.DAILY:
        save_daily_q_stats(period, experience)

    # 7. Mark as done
    _mark_done(level, period, memory_id)

    return {
        "status": "completed",
        "level": level.value,
        "period": period,
        "summary": analysis.get("summary", ""),
        "patterns": analysis.get("patterns", []),
        "adjustments": adj_result,
        "insights_stored": len(analysis.get("insights", [])),
        "memory_id": memory_id,
    }
