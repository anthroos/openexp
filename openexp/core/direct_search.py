"""Direct Qdrant Search with FastEmbed.

Two main functions:
  - search_memories(): embed query -> Qdrant search -> lifecycle filter -> hybrid+Q rerank
  - add_memory(): embed content -> enrich -> upsert to Qdrant -> update Q-cache
"""
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastembed import TextEmbedding
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue, PointStruct, Range

from .config import (
    QDRANT_HOST,
    QDRANT_PORT,
    QDRANT_API_KEY,
    COLLECTION_NAME,
    EMBEDDING_MODEL,
)
from .v7_extensions import apply_lifecycle_filter, apply_hybrid_scoring
from .q_value import QCache, DEFAULT_Q_CONFIG

logger = logging.getLogger(__name__)

_init_lock = threading.Lock()
_embedder: Optional[TextEmbedding] = None
_qdrant: Optional[QdrantClient] = None


def _get_embedder() -> TextEmbedding:
    global _embedder
    if _embedder is None:
        with _init_lock:
            if _embedder is None:
                _cache_dir = str(__import__("pathlib").Path.home() / ".cache" / "fastembed")
                _embedder = TextEmbedding(model_name=EMBEDDING_MODEL, cache_dir=_cache_dir)
                logger.info("FastEmbed model loaded: %s", EMBEDDING_MODEL)
    return _embedder


def _get_qdrant() -> QdrantClient:
    global _qdrant
    if _qdrant is None:
        with _init_lock:
            if _qdrant is None:
                _qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, api_key=QDRANT_API_KEY)
    return _qdrant


def _embed(text: str) -> List[float]:
    """Embed a single text string using FastEmbed."""
    embedder = _get_embedder()
    vectors = list(embedder.embed([text]))
    return vectors[0].tolist()


def search_memories(
    query: str,
    limit: int = 20,
    agent_id: Optional[str] = None,
    memory_type: Optional[str] = None,
    exclude_type: Optional[str] = None,
    client_id: Optional[str] = None,
    include_deleted: bool = False,
    q_cache: Optional[QCache] = None,
    experience: str = "default",
    role: Optional[str] = None,
    session_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    """Search memories via direct Qdrant + FastEmbed.

    1. Embed query with FastEmbed
    2. Search Qdrant with filters
    3. Apply lifecycle filter
    4. Apply hybrid scoring (BM25 + Q-value reranking)
    5. Return results

    Filters:
        role: "user" or "assistant" (conversation messages only)
        session_id: filter by session
        date_from/date_to: ISO date strings for date range (on created_at)
        source: "transcript" or "decision" etc.
    """
    qc = _get_qdrant()
    query_vector = _embed(query)

    must_conditions = []
    must_not_conditions = []
    if agent_id:
        must_conditions.append(
            FieldCondition(key="agent_id", match=MatchValue(value=agent_id))
        )
    if memory_type:
        must_conditions.append(
            FieldCondition(key="memory_type", match=MatchValue(value=memory_type))
        )
    if exclude_type:
        must_not_conditions.append(
            FieldCondition(key="memory_type", match=MatchValue(value=exclude_type))
        )
    if client_id:
        must_conditions.append(
            FieldCondition(key="metadata.client_id", match=MatchValue(value=client_id))
        )
    if role:
        must_conditions.append(
            FieldCondition(key="role", match=MatchValue(value=role))
        )
    if session_id:
        must_conditions.append(
            FieldCondition(key="session_id", match=MatchValue(value=session_id))
        )
    if source:
        must_conditions.append(
            FieldCondition(key="source", match=MatchValue(value=source))
        )
    if date_from or date_to:
        import re
        _date_re = re.compile(r'^\d{4}-\d{2}-\d{2}(T[\d:+Z.\-]+)?$')
        range_kwargs = {}
        if date_from:
            if not _date_re.match(date_from):
                return {"results": [], "count": 0, "error": "Invalid date_from format"}
            range_kwargs["gte"] = date_from
        if date_to:
            if not _date_re.match(date_to):
                return {"results": [], "count": 0, "error": "Invalid date_to format"}
            range_kwargs["lte"] = date_to
        must_conditions.append(
            FieldCondition(key="created_at", range=Range(**range_kwargs))
        )

    qdrant_filter = None
    if must_conditions or must_not_conditions:
        qdrant_filter = Filter(
            must=must_conditions or None,
            must_not=must_not_conditions or None,
        )

    fetch_limit = min(limit * 3, 100)
    search_result = qc.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        query_filter=qdrant_filter,
        limit=fetch_limit,
        with_payload=True,
    )

    results = []
    for point in search_result.points:
        payload = point.payload or {}
        record = {
            "id": str(point.id),
            "memory": payload.get("memory", payload.get("data", "")),
            "score": point.score,
            "agent_id": payload.get("agent_id", "unknown"),
            "memory_type": payload.get("memory_type", "fact"),
            "created_at": payload.get("created_at", ""),
            "source": payload.get("source", "unknown"),
            "status": payload.get("status", "active"),
            "metadata": payload.get("metadata", {}),
        }

        q_fallback = DEFAULT_Q_CONFIG["q_init"]
        if q_cache:
            q_data = q_cache.get(str(point.id), experience)
            if q_data:
                record["q_value"] = q_data.get("q_value", q_fallback)
                record["q_data"] = q_data
            else:
                record["q_value"] = q_fallback
        else:
            record["q_value"] = payload.get("q_value", q_fallback)

        results.append(record)

    results = apply_lifecycle_filter(results, include_deleted=include_deleted)
    results = apply_hybrid_scoring(query=query, results=results, top_k=limit)

    return {
        "results": results,
        "count": len(results),
        "scoring": "hybrid+q_value",
    }


def add_memory(
    content: str,
    user_id: str = "default",
    agent_id: str = "main",
    memory_type: str = "fact",
    metadata: Optional[dict] = None,
    q_cache: Optional[QCache] = None,
    experience: str = "default",
) -> Dict[str, Any]:
    """Add a memory directly to Qdrant with FastEmbed embedding.

    1. Embed with FastEmbed
    2. Enrich (try LLM, fallback to defaults)
    3. Upsert to Qdrant
    4. Update Q-cache with initial Q=0.0
    """
    try:
        from .enrichment import enrich_memory, compute_validity_end
        enrichment = enrich_memory(content)
    except Exception as e:
        logger.warning("Enrichment failed, using defaults: %s", e)
        enrichment = {
            "type": memory_type,
            "weight": 0.5,
            "title": content[:50],
            "summary": content[:200],
            "tags": [],
            "validity_hours": None,
            "triples": [],
        }

    enriched_type = memory_type if memory_type != "fact" else enrichment["type"]

    ts_valid_start = datetime.now(timezone.utc).isoformat()
    try:
        from .enrichment import compute_validity_end
        ts_valid_end = compute_validity_end(enrichment["validity_hours"])
    except Exception:
        ts_valid_end = None

    vector = _embed(content)

    point_id = str(uuid.uuid4())
    meta = metadata or {}
    source = meta.get("source", "api")

    payload = {
        "memory": content,
        "agent_id": agent_id,
        "memory_type": enriched_type,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "source": source,
        "metadata": {
            "agent": agent_id,
            "type": enriched_type,
            "source": source,
            "importance": enrichment["weight"],
            "title": enrichment["title"],
            "summary": enrichment["summary"],
            "tags": enrichment["tags"],
            "ts_valid_start": ts_valid_start,
            "ts_valid_end": ts_valid_end,
            **({"client_id": meta["client_id"]} if meta.get("client_id") else {}),
        },
        "importance": enrichment["weight"],
        "ts_valid_start": ts_valid_start,
        "ts_valid_end": ts_valid_end,
        "status": "active",
        # Preserve client_id at top level for Qdrant filtering
        **({"client_id": meta["client_id"]} if meta.get("client_id") else {}),
        "status_updated_at": datetime.now(timezone.utc).isoformat(),
    }

    qc = _get_qdrant()
    qc.upsert(
        collection_name=COLLECTION_NAME,
        points=[PointStruct(id=point_id, vector=vector, payload=payload)],
    )

    if q_cache:
        q_init = DEFAULT_Q_CONFIG["q_init"]
        q_cache.set(point_id, {
            "q_value": q_init,
            "q_action": q_init,
            "q_hypothesis": q_init,
            "q_fit": q_init,
            "q_visits": 0,
        }, experience=experience)

    return {
        "status": "ok",
        "id": point_id,
        "enrichment": enrichment,
        "validity": {"start": ts_valid_start, "end": ts_valid_end},
    }


def add_experience(
    experience_label: dict,
    thread_id: int,
    thread_name: str,
    q_cache: Optional[QCache] = None,
    experience: str = "default",
) -> Dict[str, Any]:
    """Store a structured experience label in Qdrant.

    The embedding is computed from the searchable parts (situation + insight +
    applies_when) so that search_memory finds this experience when the user
    faces a similar situation — not when they search for the raw actions.

    The full label JSON is stored in the payload for retrieval.
    """
    ctx = experience_label.get("context", {})
    lesson = experience_label.get("lesson", {})
    outcome = experience_label.get("outcome", {})

    # Build embedding text from the parts people will SEARCH for
    search_text = " ".join(filter(None, [
        ctx.get("situation", ""),
        lesson.get("insight", ""),
        lesson.get("applies_when", ""),
        outcome.get("result", ""),
    ]))

    # Build human-readable memory text for display
    memory_text = (
        f"EXPERIENCE: {lesson.get('insight', 'No insight')}\n"
        f"APPLIES WHEN: {lesson.get('applies_when', '?')}\n"
        f"CONTEXT: {ctx.get('situation', '?')}\n"
        f"OUTCOME: {outcome.get('result', '?')} "
        f"({'success' if outcome.get('success') else 'failure' if outcome.get('success') is False else 'unclear'})\n"
        f"ANTI-PATTERN: {lesson.get('anti_pattern', 'N/A')}"
    )

    vector = _embed(search_text)
    point_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Top-level fields (importance, ts_valid_*, status) are duplicated in metadata
    # intentionally — Qdrant filters use top-level keys, retrieval uses metadata.
    payload = {
        "memory": memory_text,
        "agent_id": "main",
        "memory_type": "experience",
        "created_at": now,
        "user_id": "default",
        "source": "experience_library",
        "metadata": {
            "agent": "main",
            "type": "experience",
            "source": "experience_library",
            "importance": 0.8,
            "title": lesson.get("insight", "")[:80],
            "summary": memory_text[:200],
            "tags": ["experience", f"thread_{thread_id}"],
            "ts_valid_start": now,
            "ts_valid_end": None,
            "thread_id": thread_id,
            "thread_name": thread_name,
            "experience_id": experience_label.get("experience_id", ""),
            "experience_label": experience_label,
        },
        "importance": 0.8,
        "ts_valid_start": now,
        "ts_valid_end": None,
        "status": "active",
        "status_updated_at": now,
    }

    qc = _get_qdrant()
    qc.upsert(
        collection_name=COLLECTION_NAME,
        points=[PointStruct(id=point_id, vector=vector, payload=payload)],
    )

    if q_cache:
        q_init = DEFAULT_Q_CONFIG["q_init"]
        q_cache.set(point_id, {
            "q_value": q_init,
            "q_action": q_init,
            "q_hypothesis": q_init,
            "q_fit": q_init,
            "q_visits": 0,
        }, experience=experience)

    return {
        "status": "ok",
        "id": point_id,
        "experience_id": experience_label.get("experience_id", ""),
        "insight": lesson.get("insight", ""),
    }
