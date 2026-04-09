"""Ingest full conversation transcript into Qdrant.

Parses Claude Code transcript JSONL, extracts every user and assistant
message, embeds and stores each as a separate point in Qdrant.

This captures the FULL conversation — not just tool calls or decisions,
but every word exchanged between user and assistant.
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from qdrant_client.models import PointStruct

from ..core.config import COLLECTION_NAME
from ..core.direct_search import _embed, _get_qdrant

logger = logging.getLogger(__name__)

# Max characters per message to store (very long tool outputs get truncated)
MAX_MESSAGE_CHARS = 5000
# Minimum message length worth storing
MIN_MESSAGE_CHARS = 10
# Batch size for Qdrant upserts
UPSERT_BATCH_SIZE = 50


def parse_transcript(transcript_path: Path) -> List[Dict]:
    """Parse a Claude Code transcript JSONL into a list of messages.

    Returns list of dicts with keys: role, text, timestamp, message_id.
    Filters out system messages, tool results, and hook injections.
    """
    if not transcript_path.exists():
        return []

    messages = []
    session_id = None

    for line in transcript_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_type = entry.get("type")

        # Capture session ID from any entry
        if not session_id:
            session_id = entry.get("sessionId") or entry.get("session_id")

        if msg_type == "user":
            content = entry.get("message", {}).get("content")
            timestamp = entry.get("timestamp", "")
            message_id = entry.get("uuid", "")

            # content can be string or list of blocks
            if isinstance(content, str):
                text = content.strip()
            elif isinstance(content, list):
                texts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        t = block.get("text", "").strip()
                        # Skip system-reminder injections
                        if t and not t.startswith("<system-reminder>"):
                            texts.append(t)
                    elif isinstance(block, str):
                        texts.append(block.strip())
                text = "\n".join(texts)
            else:
                continue

            if len(text) >= MIN_MESSAGE_CHARS:
                messages.append({
                    "role": "user",
                    "text": text[:MAX_MESSAGE_CHARS],
                    "timestamp": timestamp,
                    "message_id": message_id,
                    "session_id": session_id or "",
                })

        elif msg_type == "assistant":
            content = entry.get("message", {}).get("content", [])
            timestamp = entry.get("timestamp", "")
            message_id = entry.get("uuid", "")

            texts = []
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        t = block.get("text", "").strip()
                        if t:
                            texts.append(t)
            elif isinstance(content, str):
                texts = [content.strip()]

            text = "\n".join(texts)
            if len(text) >= MIN_MESSAGE_CHARS:
                messages.append({
                    "role": "assistant",
                    "text": text[:MAX_MESSAGE_CHARS],
                    "timestamp": timestamp,
                    "message_id": message_id,
                    "session_id": session_id or "",
                })

    return messages


def ingest_transcript(
    transcript_path: Path,
    session_id: str,
    experience: str = "default",
    dry_run: bool = False,
) -> Dict:
    """Full pipeline: parse transcript → embed → store in Qdrant.

    Each user/assistant message becomes a separate Qdrant point with:
    - memory: the message text
    - type: "conversation"
    - role: "user" or "assistant"
    - session_id, timestamp, experience

    Returns summary dict.
    """
    messages = parse_transcript(transcript_path)
    if not messages:
        return {"stored": 0, "reason": "no_messages"}

    if dry_run:
        return {
            "parsed": len(messages),
            "user_messages": sum(1 for m in messages if m["role"] == "user"),
            "assistant_messages": sum(1 for m in messages if m["role"] == "assistant"),
            "dry_run": True,
        }

    client = _get_qdrant()
    stored = 0
    points_batch = []

    for msg in messages:
        try:
            vector = _embed(msg["text"])
            point_id = str(uuid.uuid4())

            # Importance: user messages slightly higher (they contain intent)
            importance = 0.5 if msg["role"] == "user" else 0.4

            payload = {
                "memory": msg["text"],
                "type": "conversation",
                "memory_type": "conversation",
                "role": msg["role"],
                "agent": "session",
                "source": "transcript",
                "importance": importance,
                "tags": [],
                "session_id": msg.get("session_id") or session_id,
                "message_id": msg.get("message_id", ""),
                "experience": experience,
                "created_at": msg.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                "status": "active",
            }

            points_batch.append(PointStruct(
                id=point_id,
                vector=vector,
                payload=payload,
            ))

            # Batch upsert
            if len(points_batch) >= UPSERT_BATCH_SIZE:
                client.upsert(
                    collection_name=COLLECTION_NAME,
                    points=points_batch,
                )
                stored += len(points_batch)
                points_batch = []

        except Exception as e:
            logger.error("Failed to embed/store message: %s", e)

    # Flush remaining
    if points_batch:
        try:
            client.upsert(
                collection_name=COLLECTION_NAME,
                points=points_batch,
            )
            stored += len(points_batch)
        except Exception as e:
            logger.error("Failed to flush batch: %s", e)

    logger.info(
        "Transcript ingested: %d messages stored (%d user, %d assistant) for session %s",
        stored,
        sum(1 for m in messages if m["role"] == "user"),
        sum(1 for m in messages if m["role"] == "assistant"),
        session_id[:8],
    )

    return {
        "stored": stored,
        "user_messages": sum(1 for m in messages if m["role"] == "user"),
        "assistant_messages": sum(1 for m in messages if m["role"] == "assistant"),
        "session_id": session_id,
        "experience": experience,
    }
