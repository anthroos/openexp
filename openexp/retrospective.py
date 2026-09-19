"""Multi-level retrospective — daily/weekly/monthly reflection.

Gathers what happened in a period, runs LLM analysis (claude -p), and
stores the retrospective summary + insights as searchable memories.
Q-value adjustments were removed 2026-07-03; retrospectives now only
produce insights.
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
    SESSIONS_DIR,
)
from .retrospective_prompts import DAILY_PROMPT, WEEKLY_PROMPT, MONTHLY_PROMPT

logger = logging.getLogger(__name__)

WATERMARK_PATH = DATA_DIR / "retrospective_watermark.json"
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
    wm.setdefault(level.value, {})[period] = {
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "memory_id": memory_id,
    }
    _save_watermark(wm)


# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------

def gather_daily_data(date_str: str) -> Dict[str, Any]:
    """Collect session summaries and key memories for a given date."""
    data: Dict[str, Any] = {"date": date_str, "sessions": [], "memories": []}

    # 1. Session summaries (legacy artifact — dir may be frozen)
    for f in sorted(SESSIONS_DIR.glob(f"{date_str}-*.md")):
        try:
            content = f.read_text()[:2000]
            data["sessions"].append({"file": f.name, "content": content})
        except OSError:
            continue

    # 2. Key memories created today (extracted decisions)
    try:
        from .core.direct_search import _get_qdrant
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        qc = _get_qdrant()
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
        for p in points:
            created = p.payload.get("created_at", "")
            if created.startswith(date_str):
                data["memories"].append({
                    "memory_id": str(p.id),
                    "content": p.payload.get("memory", "")[:300],
                    "type": p.payload.get("type", p.payload.get("memory_type", "")),
                })
    except Exception as e:
        logger.warning("Failed to fetch memories for daily data: %s", e)

    return data


def gather_weekly_data(year: int, week: int) -> Dict[str, Any]:
    """Collect daily retrospectives for an ISO week."""
    data: Dict[str, Any] = {"year": year, "week": week, "daily_retrospectives": []}

    start = datetime.fromisocalendar(year, week, 1)
    dates = [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

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

    return data


def gather_monthly_data(year: int, month: int) -> Dict[str, Any]:
    """Collect weekly retrospectives for a month."""
    data: Dict[str, Any] = {"year": year, "month": month, "weekly_retrospectives": []}
    month_prefix = f"{year}-{month:02d}"

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
        memories_text = json.dumps(data.get("memories", [])[:30], indent=2, default=str)

        prompt = DAILY_PROMPT.format(
            sessions_data=sessions_text[:CONTEXT_LIMIT // 2] or "(no sessions)",
            memories_data=memories_text[:CONTEXT_LIMIT // 2] or "(no memories)",
        )

    elif level == RetroLevel.WEEKLY:
        daily_text = json.dumps(data.get("daily_retrospectives", []), indent=2, default=str)
        prompt = WEEKLY_PROMPT.format(
            daily_retrospectives=daily_text[:CONTEXT_LIMIT] or "(no daily retrospectives)",
        )

    elif level == RetroLevel.MONTHLY:
        weekly_text = json.dumps(data.get("weekly_retrospectives", []), indent=2, default=str)
        prompt = MONTHLY_PROMPT.format(
            weekly_retrospectives=weekly_text[:CONTEXT_LIMIT] or "(no weekly retrospectives)",
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

        logger.info("LLM analysis: %d insights", len(parsed.get("insights", [])))
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
        experience: Experience name tagged on stored memories
        dry_run: If True, gather data but don't run LLM or store anything

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
        data.get("sessions") or data.get("memories")
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
                "memories": len(data.get("memories", [])),
                "daily_retrospectives": len(data.get("daily_retrospectives", [])),
                "weekly_retrospectives": len(data.get("weekly_retrospectives", [])),
            },
            "prompt_length": len(prompt),
        }

    analysis = analyze_with_llm(prompt)
    if analysis is None:
        return {"status": "llm_failed", "level": level.value, "period": period}

    # 4. Store retrospective memory + insights
    memory_id = store_retrospective_memory(level, period, analysis, experience)

    # 5. Mark as done
    _mark_done(level, period, memory_id)

    return {
        "status": "completed",
        "level": level.value,
        "period": period,
        "summary": analysis.get("summary", ""),
        "patterns": analysis.get("patterns", []),
        "insights_stored": len(analysis.get("insights", [])),
        "memory_id": memory_id,
    }
