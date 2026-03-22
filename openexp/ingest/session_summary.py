"""SessionIngester: session summary .md files -> Qdrant.

Each session summary becomes one memory with higher importance (0.7).
"""
import hashlib
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from qdrant_client.models import PointStruct

from ..core.config import (
    SESSIONS_DIR,
    COLLECTION_NAME,
    INGEST_WATERMARK_PATH,
    Q_CACHE_PATH,
)
from ..core.direct_search import _get_embedder, _get_qdrant
from ..core.q_value import QCache
from .watermark import IngestWatermark

logger = logging.getLogger(__name__)


def _parse_session_md(text: str) -> Dict:
    """Extract structured data from session summary markdown."""
    result = {
        "session_id": "",
        "project": "",
        "what_was_done": "",
        "decisions": "",
        "files_changed": "",
    }

    m = re.search(r"\*\*Session ID:\*\*\s*(\S+)", text)
    if m:
        result["session_id"] = m.group(1)

    m = re.search(r"\*\*Project:\*\*\s*(.+)", text)
    if m:
        result["project"] = m.group(1).strip()

    m = re.search(r"## What was done\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    if m:
        result["what_was_done"] = m.group(1).strip()

    m = re.search(r"## Key decisions\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    if m:
        result["decisions"] = m.group(1).strip()

    m = re.search(r"## Files changed\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    if m:
        result["files_changed"] = m.group(1).strip()

    return result


def _session_to_text(parsed: Dict, filename: str) -> str:
    """Build embedding text from parsed session data."""
    parts = []
    if parsed["what_was_done"]:
        lines = [
            line.lstrip("- ").strip()
            for line in parsed["what_was_done"].splitlines()
            if line.strip()
        ]
        parts.append(" ".join(lines))
    if parsed["decisions"]:
        parts.append(f"decisions: {parsed['decisions']}")
    if parsed["project"]:
        parts.append(f"project:{parsed['project']}")
    return " | ".join(parts) if parts else filename


def ingest_sessions(
    dry_run: bool = False,
    sessions_dir: Optional[Path] = None,
) -> Dict:
    """Ingest session summary .md files into Qdrant."""
    sessions_dir = sessions_dir or SESSIONS_DIR
    if not sessions_dir.exists():
        return {"error": f"Sessions directory not found: {sessions_dir}"}

    watermark = IngestWatermark(INGEST_WATERMARK_PATH)

    md_files = sorted(sessions_dir.glob("*.md"))
    total = len(md_files)

    new_files = [
        f for f in md_files
        if not watermark.is_session_processed(f.name)
    ]
    to_ingest = len(new_files)

    if dry_run:
        return {
            "dry_run": True,
            "total_sessions": total,
            "already_processed": total - to_ingest,
            "would_ingest": to_ingest,
        }

    if to_ingest == 0:
        return {
            "total_sessions": total,
            "already_processed": total,
            "ingested": 0,
        }

    embedder = _get_embedder()
    qc = _get_qdrant()
    q_cache = QCache()
    q_cache.load(Q_CACHE_PATH)

    texts = []
    parsed_list = []
    filenames = []

    for f in new_files:
        try:
            content = f.read_text()
        except OSError:
            continue
        parsed = _parse_session_md(content)
        text = _session_to_text(parsed, f.name)
        texts.append(text)
        parsed_list.append(parsed)
        filenames.append(f.name)

    if not texts:
        return {"total_sessions": total, "already_processed": total, "ingested": 0}

    vectors = list(embedder.embed(texts))
    now = datetime.now(timezone.utc).isoformat()

    points = []
    ingested = 0
    for filename, parsed, vec in zip(filenames, parsed_list, vectors):
        point_id = str(uuid.uuid4())
        summary_text = _session_to_text(parsed, filename)

        payload = {
            "memory": summary_text,
            "memory_id": f"session-{parsed['session_id'] or filename}",
            "memory_type": "insight",
            "agent_id": "session",
            "user_id": "default",
            "created_at": now,
            "source": "session_summary",
            "hash": hashlib.md5(summary_text.encode()).hexdigest(),
            "importance": 0.7,
            "status": "active",
            "status_updated_at": now,
            "metadata": {
                "agent": "session",
                "type": "insight",
                "source": "session_summary",
                "session_id": parsed["session_id"],
                "project": parsed["project"],
                "filename": filename,
                "files_changed": parsed["files_changed"],
            },
        }

        points.append(PointStruct(
            id=point_id,
            vector=vec.tolist(),
            payload=payload,
        ))

        q_cache.set(point_id, {
            "q_value": 0.5,
            "q_action": 0.5,
            "q_hypothesis": 0.5,
            "q_fit": 0.5,
            "q_visits": 0,
        })

        watermark.mark_session_processed(filename)
        ingested += 1

    qc.upsert(collection_name=COLLECTION_NAME, points=points)
    logger.info("Ingested %d session summaries", ingested)

    q_cache.save(Q_CACHE_PATH)
    watermark.save()

    return {
        "total_sessions": total,
        "already_processed": total - to_ingest,
        "ingested": ingested,
    }
