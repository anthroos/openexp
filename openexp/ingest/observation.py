"""ObservationIngester: JSONL observations -> Qdrant.

Reads observation JSONL files, filters trivial ones, batch-embeds via FastEmbed,
and upserts to Qdrant.
"""
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from qdrant_client.models import PointStruct

from ..core.config import (
    OBSERVATIONS_DIR,
    COLLECTION_NAME,
    INGEST_BATCH_SIZE,
    INGEST_WATERMARK_PATH,
    Q_CACHE_PATH,
)
from ..core.direct_search import _get_embedder, _get_qdrant
from ..core.q_value import QCache
from .watermark import IngestWatermark
from .filters import should_keep

logger = logging.getLogger(__name__)

_TYPE_MAP = {
    "feature": "action",
    "bugfix": "action",
    "refactor": "action",
    "decision": "decision",
    "retrospective": "insight",
    "config": "action",
    "deploy": "action",
    "strategy": "decision",
    "client_interaction": "action",
    "pricing": "decision",
    "insight": "insight",
}

_IMPORTANCE_MAP = {
    "Write": 0.5,
    "Edit": 0.5,
    "Bash": 0.3,
    "Read": 0.2,
    "Glob": 0.1,
    "Grep": 0.1,
    "transcript_extraction": 0.7,
}


def _obs_to_text(obs: Dict) -> str:
    """Build embedding text from observation fields."""
    parts = [obs.get("summary", "")]
    project = obs.get("project", "")
    if project:
        parts.append(f"project:{project}")
    tags = obs.get("tags", [])
    if tags:
        parts.append(f"tags:{','.join(tags)}")
    file_path = obs.get("context", {}).get("file_path", "")
    if file_path:
        parts.append(f"file:{Path(file_path).name}")
    return " | ".join(parts)


def _obs_to_payload(obs: Dict) -> Dict:
    """Convert observation to Qdrant payload."""
    now = datetime.now(timezone.utc).isoformat()
    obs_type = obs.get("type", "feature")
    tool = obs.get("tool", "")
    summary = obs.get("summary", "")

    return {
        "memory": summary,
        "memory_id": obs.get("id", ""),
        "memory_type": _TYPE_MAP.get(obs_type, "action"),
        "agent_id": "session",
        "user_id": "default",
        "created_at": obs.get("timestamp", now),
        "source": "observation",
        "hash": hashlib.md5(summary.encode()).hexdigest(),
        "importance": obs.get("context", {}).get("importance") or _IMPORTANCE_MAP.get(tool, 0.3),
        "status": "active",
        "status_updated_at": now,
        "metadata": {
            "agent": "session",
            "type": _TYPE_MAP.get(obs_type, "action"),
            "source": "observation",
            "obs_id": obs.get("id", ""),
            "session_id": obs.get("session_id", ""),
            "project": obs.get("project", ""),
            "tool": tool,
            "tags": obs.get("tags", []),
            "file_path": obs.get("context", {}).get("file_path", ""),
        },
    }


def _load_observations(obs_dir: Path) -> List[Dict]:
    """Load all observations from JSONL files in directory."""
    all_obs = []
    for f in sorted(obs_dir.glob("observations-*.jsonl")):
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                all_obs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return all_obs


def ingest_observations(
    max_count: int = 0,
    dry_run: bool = False,
    obs_dir: Optional[Path] = None,
) -> Dict:
    """Ingest observations into Qdrant."""
    obs_dir = obs_dir or OBSERVATIONS_DIR
    if not obs_dir.exists():
        return {"error": f"Observations directory not found: {obs_dir}"}

    watermark = IngestWatermark(INGEST_WATERMARK_PATH)
    all_obs = _load_observations(obs_dir)
    total = len(all_obs)

    new_obs = []
    filtered = 0
    skipped_dup = 0
    for obs in all_obs:
        obs_id = obs.get("id", "")
        if not obs_id:
            filtered += 1
            continue
        if watermark.is_obs_processed(obs_id):
            skipped_dup += 1
            continue
        if not should_keep(obs):
            filtered += 1
            watermark.mark_obs_skipped()
            watermark.mark_obs_processed(obs_id)
            continue
        new_obs.append(obs)

    if max_count > 0:
        new_obs = new_obs[:max_count]

    to_ingest = len(new_obs)

    if dry_run:
        return {
            "dry_run": True,
            "total_observations": total,
            "already_processed": skipped_dup,
            "filtered_trivial": filtered,
            "would_ingest": to_ingest,
        }

    if to_ingest == 0:
        watermark.save()
        return {
            "total_observations": total,
            "already_processed": skipped_dup,
            "filtered_trivial": filtered,
            "ingested": 0,
        }

    embedder = _get_embedder()
    qc = _get_qdrant()
    q_cache = QCache()
    q_cache.load(Q_CACHE_PATH)

    ingested = 0
    ingested_point_ids = []
    batch_size = INGEST_BATCH_SIZE

    for i in range(0, to_ingest, batch_size):
        batch = new_obs[i:i + batch_size]
        texts = [_obs_to_text(obs) for obs in batch]
        vectors = list(embedder.embed(texts))

        points = []
        for obs, vec in zip(batch, vectors):
            point_id = str(uuid.uuid4())
            payload = _obs_to_payload(obs)

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

            ingested_point_ids.append(point_id)
            watermark.mark_obs_processed(obs.get("id", ""))
            ingested += 1

        qc.upsert(collection_name=COLLECTION_NAME, points=points)
        logger.info("Ingested batch %d-%d (%d points)", i, i + len(batch), len(points))

    q_cache.save(Q_CACHE_PATH)
    watermark.save()

    return {
        "total_observations": total,
        "already_processed": skipped_dup,
        "filtered_trivial": filtered,
        "ingested": ingested,
        "_point_ids": ingested_point_ids,
        "_raw_observations": new_obs,
    }
