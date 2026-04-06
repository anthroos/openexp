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
from ..core.q_value import QCache, DEFAULT_Q_CONFIG
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
    client_id = obs.get("client_id") or _detect_client_id(obs)

    return {
        "memory": summary,
        "memory_id": obs.get("id", ""),
        "memory_type": _TYPE_MAP.get(obs_type, "action"),
        "agent_id": "session",
        "user_id": "default",
        "created_at": obs.get("timestamp", now),
        "source": "observation",
        "hash": hashlib.sha256(summary.encode()).hexdigest(),
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
            **({"client_id": client_id} if client_id else {}),
        },
    }


MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

# --- Client auto-tagging from CRM ---
_CLIENT_LOOKUP: Optional[Dict] = None


def _load_client_lookup() -> Dict[str, str]:
    """Load company name → company_id lookup from CRM CSV.

    Returns {lowercase_name: company_id} for auto-tagging observations.
    Cached on first call. Returns empty dict if CRM not configured.
    """
    global _CLIENT_LOOKUP
    if _CLIENT_LOOKUP is not None:
        return _CLIENT_LOOKUP

    from ..core.config import CRM_DIR
    _CLIENT_LOOKUP = {}
    if not CRM_DIR or not CRM_DIR.exists():
        return _CLIENT_LOOKUP

    companies_path = CRM_DIR / "contacts" / "companies.csv"
    if not companies_path.exists():
        return _CLIENT_LOOKUP

    import csv
    try:
        with open(companies_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                cid = row.get("company_id", "").strip()
                name = row.get("name", "").strip()
                if cid and name and len(name) >= 3:
                    _CLIENT_LOOKUP[name.lower()] = cid
    except Exception as e:
        logger.warning("Failed to load CRM companies for auto-tagging: %s", e)

    logger.info("Loaded %d companies for client auto-tagging", len(_CLIENT_LOOKUP))
    return _CLIENT_LOOKUP


def _detect_client_id(obs: Dict) -> Optional[str]:
    """Detect client_id from observation content by matching CRM company names."""
    lookup = _load_client_lookup()
    if not lookup:
        return None

    # Build searchable text from observation
    text = (obs.get("summary", "") + " " + obs.get("context", {}).get("file_path", "")).lower()
    if len(text) < 5:
        return None

    for name, cid in lookup.items():
        if name in text:
            return cid

    return None


def _load_observations(obs_dir: Path, processed_ids: set = None) -> List[Dict]:
    """Load all observations from JSONL files in directory.

    Handles both true JSONL (one JSON per line) and multi-line pretty-printed
    JSON objects (caused by jq without -c flag). Streams line-by-line for
    JSONL, falls back to json.JSONDecoder for multi-line.
    """
    all_obs = []
    for f in sorted(obs_dir.glob("observations-*.jsonl")):
        try:
            file_size = f.stat().st_size
        except OSError:
            continue
        if file_size > MAX_FILE_SIZE:
            logger.warning("Skipping oversized observation file %s (%d bytes > %d limit)", f, file_size, MAX_FILE_SIZE)
            continue

        content = f.read_text(encoding="utf-8")
        file_obs = []

        # Try JSONL first (fast path: first non-empty line is valid JSON)
        first_line = ""
        for line in content.split("\n"):
            line = line.strip()
            if line:
                first_line = line
                break

        is_jsonl = False
        if first_line:
            try:
                json.loads(first_line)
                is_jsonl = True
            except json.JSONDecodeError:
                pass

        if is_jsonl:
            for line in content.split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    obs = json.loads(line)
                except json.JSONDecodeError:
                    continue
                file_obs.append(obs)
        else:
            # Multi-line JSON: use decoder to extract consecutive objects
            decoder = json.JSONDecoder()
            idx = 0
            while idx < len(content):
                # Skip whitespace
                while idx < len(content) and content[idx] in " \t\n\r":
                    idx += 1
                if idx >= len(content):
                    break
                try:
                    obj, end_idx = decoder.raw_decode(content, idx)
                    file_obs.append(obj)
                    idx = end_idx
                except json.JSONDecodeError:
                    # Skip to next line
                    next_nl = content.find("\n", idx)
                    idx = next_nl + 1 if next_nl != -1 else len(content)

        # Filter already-processed IDs
        for obs in file_obs:
            if processed_ids and obs.get("id", "") in processed_ids:
                continue
            all_obs.append(obs)

    return all_obs


def ingest_observations(
    max_count: int = 0,
    dry_run: bool = False,
    obs_dir: Optional[Path] = None,
    experience: str = "default",
) -> Dict:
    """Ingest observations into Qdrant."""
    obs_dir = obs_dir or OBSERVATIONS_DIR
    if not obs_dir.exists():
        return {"error": f"Observations directory not found: {obs_dir}"}

    watermark = IngestWatermark(INGEST_WATERMARK_PATH)
    all_obs = _load_observations(obs_dir, processed_ids=watermark.processed_obs)
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
            watermark.mark_obs_processed(obs_id, ingested=False)
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

            q_init = DEFAULT_Q_CONFIG["q_init"]
            q_cache.set(point_id, {
                "q_value": q_init,
                "q_action": q_init,
                "q_hypothesis": q_init,
                "q_fit": q_init,
                "q_visits": 0,
            }, experience=experience)

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
