"""Backfill subagent transcripts into Qdrant.

Subagent jsonl files live at:
  ~/.claude/projects/<project>/<parent_session_id>/subagents/agent-<short>.jsonl

Each file is a transcript of a Task-tool subagent run. Schema mirrors the
main transcript jsonl (type=user/assistant, message.role, message.content).
We ingest each file as a separate "subagent_transcript" source, with composite
session_id = "<parent>#<agent_id>" for idempotent re-runs.

Usage:
    .venv/bin/python3 scripts/backfill_subagents.py [--dry-run] [--limit N]
"""
import argparse
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from qdrant_client.models import (
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openexp.core.config import COLLECTION_NAME
from openexp.core.direct_search import _embed, _get_qdrant
from openexp.ingest.transcript import (
    MAX_MESSAGE_CHARS,
    MIN_MESSAGE_CHARS,
    UPSERT_BATCH_SIZE,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_subagents")

PROJECTS_DIR = Path.home() / ".claude" / "projects"
SUBAGENT_SOURCE = "subagent_transcript"


def parse_subagent_file(path: Path):
    """Parse subagent jsonl. Returns (parent_session_id, agent_id, messages)."""
    parent_sid = None
    agent_id = None
    messages = []

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        if not parent_sid:
            parent_sid = entry.get("sessionId") or entry.get("session_id")
        if not agent_id:
            agent_id = entry.get("agentId")

        msg_type = entry.get("type")
        if msg_type not in ("user", "assistant"):
            continue

        msg = entry.get("message", {})
        content = msg.get("content")
        timestamp = entry.get("timestamp", "")
        msg_id = entry.get("uuid", "")

        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    t = block.get("text", "").strip()
                    if t and not t.startswith("<system-reminder>"):
                        parts.append(t)
                elif isinstance(block, str):
                    parts.append(block.strip())
            text = "\n".join(parts)
        else:
            continue

        if len(text) < MIN_MESSAGE_CHARS:
            continue

        messages.append({
            "role": msg_type,
            "text": text[:MAX_MESSAGE_CHARS],
            "timestamp": timestamp,
            "message_id": msg_id,
        })

    if not agent_id:
        agent_id = path.stem  # fallback: agent-XXXXXX
    return parent_sid, agent_id, messages


def composite_sid(parent_sid: str, agent_id: str) -> str:
    return f"{parent_sid}#{agent_id}"


def already_ingested(client, comp_sid: str) -> bool:
    try:
        r = client.count(
            collection_name=COLLECTION_NAME,
            count_filter=Filter(must=[
                FieldCondition(key="session_id", match=MatchValue(value=comp_sid)),
                FieldCondition(key="source", match=MatchValue(value=SUBAGENT_SOURCE)),
            ]),
            exact=True,
        )
        return r.count > 0
    except Exception as e:
        logger.warning("count failed for %s: %s", comp_sid, e)
        return False


def ingest_one(client, path: Path, dry_run: bool) -> dict:
    parent_sid, agent_id, messages = parse_subagent_file(path)
    if not parent_sid or not messages:
        return {"path": str(path), "stored": 0, "reason": "empty_or_no_parent"}

    comp_sid = composite_sid(parent_sid, agent_id)
    if already_ingested(client, comp_sid):
        return {"path": str(path), "stored": 0, "reason": "already_ingested"}

    if dry_run:
        return {"path": str(path), "stored": 0, "would_store": len(messages), "comp_sid": comp_sid}

    points = []
    stored = 0
    for msg in messages:
        try:
            vector = _embed(msg["text"])
            payload = {
                "memory": msg["text"],
                "type": "conversation",
                "memory_type": "conversation",
                "role": msg["role"],
                "agent": "subagent",
                "source": SUBAGENT_SOURCE,
                "importance": 0.35,
                "tags": [],
                "session_id": comp_sid,
                "parent_session_id": parent_sid,
                "agent_id": agent_id,
                "message_id": msg.get("message_id", ""),
                "experience": "default",
                "created_at": msg.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                "status": "active",
            }
            points.append(PointStruct(id=str(uuid.uuid4()), vector=vector, payload=payload))
            if len(points) >= UPSERT_BATCH_SIZE:
                client.upsert(collection_name=COLLECTION_NAME, points=points)
                stored += len(points)
                points = []
        except Exception as e:
            logger.error("embed/store failed (%s): %s", path.name, e)

    if points:
        try:
            client.upsert(collection_name=COLLECTION_NAME, points=points)
            stored += len(points)
        except Exception as e:
            logger.error("flush failed (%s): %s", path.name, e)

    return {"path": str(path), "stored": stored, "comp_sid": comp_sid}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="Stop after N files (0 = no limit)")
    args = ap.parse_args()

    client = _get_qdrant()
    files = sorted(PROJECTS_DIR.rglob("subagents/agent-*.jsonl"))
    logger.info("Found %d subagent files", len(files))

    processed = 0
    skipped = 0
    stored_total = 0
    for i, path in enumerate(files, 1):
        try:
            if path.stat().st_size < 200:
                skipped += 1
                continue
        except OSError:
            skipped += 1
            continue

        result = ingest_one(client, path, dry_run=args.dry_run)
        if result.get("reason") == "already_ingested":
            skipped += 1
        else:
            processed += 1
            stored_total += result.get("stored", 0) or result.get("would_store", 0)

        if i % 200 == 0:
            logger.info("progress: %d/%d  processed=%d  skipped=%d  stored=%d",
                        i, len(files), processed, skipped, stored_total)

        if args.limit and processed >= args.limit:
            break

    logger.info("DONE: processed=%d  skipped=%d  stored=%d  dry_run=%s",
                processed, skipped, stored_total, args.dry_run)


if __name__ == "__main__":
    main()
