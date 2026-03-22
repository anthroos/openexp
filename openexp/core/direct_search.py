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
from qdrant_client.models import Filter, FieldCondition, MatchValue, PointStruct

from .config import (
    QDRANT_HOST,
    QDRANT_PORT,
    COLLECTION_NAME,
    EMBEDDING_MODEL,
)
from .v7_extensions import apply_lifecycle_filter, apply_hybrid_scoring
from .q_value import QCache

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
                _qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
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
) -> Dict[str, Any]:
    """Search memories via direct Qdrant + FastEmbed.

    1. Embed query with FastEmbed
    2. Search Qdrant
    3. Apply lifecycle filter
    4. Apply hybrid scoring (BM25 + Q-value reranking)
    5. Return results
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

        if q_cache:
            q_data = q_cache.get(str(point.id))
            if q_data:
                record["q_value"] = q_data.get("q_value", 0.5)
                record["q_data"] = q_data
            else:
                record["q_value"] = 0.5
        else:
            record["q_value"] = payload.get("q_value", 0.5)

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
) -> Dict[str, Any]:
    """Add a memory directly to Qdrant with FastEmbed embedding.

    1. Embed with FastEmbed
    2. Enrich (try LLM, fallback to defaults)
    3. Upsert to Qdrant
    4. Update Q-cache with initial Q=0.5
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
        },
        "importance": enrichment["weight"],
        "ts_valid_start": ts_valid_start,
        "ts_valid_end": ts_valid_end,
        "status": "active",
        "status_updated_at": datetime.now(timezone.utc).isoformat(),
    }

    qc = _get_qdrant()
    qc.upsert(
        collection_name=COLLECTION_NAME,
        points=[PointStruct(id=point_id, vector=vector, payload=payload)],
    )

    if q_cache:
        q_cache.set(point_id, {
            "q_value": 0.5,
            "q_action": 0.5,
            "q_hypothesis": 0.5,
            "q_fit": 0.5,
            "q_visits": 0,
        })

    return {
        "status": "ok",
        "id": point_id,
        "enrichment": enrichment,
        "validity": {"start": ts_valid_start, "end": ts_valid_end},
    }
