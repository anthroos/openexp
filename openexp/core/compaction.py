"""Memory Compaction — convergence-based memory clustering and merging.

Finds clusters of semantically related memories and merges them into
single compressed memories with Q-value weighted centroids.

The convergence equation: V(t+1) = V(t) + α·[R(t) − P(V(t))]
Applied here: the merged memory's Q-value is a weighted average of
originals, weighted by similarity to the cluster centroid.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Filter, FieldCondition, MatchValue, PointStruct,
)

from .config import (
    QDRANT_HOST, QDRANT_PORT, QDRANT_API_KEY, COLLECTION_NAME,
    Q_CACHE_PATH,
)
from .q_value import QCache

logger = logging.getLogger(__name__)


def _get_qdrant() -> QdrantClient:
    return QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, api_key=QDRANT_API_KEY)


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two vectors."""
    dot = np.dot(a, b)
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    if norm == 0:
        return 0.0
    return float(dot / norm)


def fetch_active_memories(
    qc: QdrantClient,
    client_id: Optional[str] = None,
    project: Optional[str] = None,
    memory_type: Optional[str] = None,
    limit: int = 10000,
) -> List[Dict]:
    """Fetch active memories from Qdrant with their vectors."""
    must_conditions = [
        FieldCondition(key="status", match=MatchValue(value="active")),
    ]
    if client_id:
        must_conditions.append(
            FieldCondition(key="client_id", match=MatchValue(value=client_id))
        )
    if memory_type:
        must_conditions.append(
            FieldCondition(key="memory_type", match=MatchValue(value=memory_type))
        )

    memories = []
    offset = None
    while True:
        result = qc.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=Filter(must=must_conditions),
            limit=min(limit - len(memories), 100),
            with_vectors=True,
            with_payload=True,
            offset=offset,
        )
        points, next_offset = result
        for point in points:
            payload = point.payload or {}
            # Filter by project if specified
            if project:
                meta = payload.get("metadata", {})
                obs_project = meta.get("project", payload.get("project", ""))
                if obs_project and project.lower() not in obs_project.lower():
                    continue
            memories.append({
                "id": str(point.id),
                "vector": list(point.vector) if point.vector else [],
                "memory": payload.get("memory", ""),
                "payload": payload,
            })
        if next_offset is None or len(memories) >= limit:
            break
        offset = next_offset

    return memories


def find_clusters(
    memories: List[Dict],
    max_distance: float = 0.25,
    min_cluster_size: int = 3,
) -> List[List[Dict]]:
    """Find clusters of similar memories using greedy centroid clustering.

    Uses cosine distance. Memories within max_distance of a cluster centroid
    are grouped together.
    """
    if len(memories) < min_cluster_size:
        return []

    vectors = np.array([m["vector"] for m in memories])
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    normalized = vectors / norms

    assigned = set()
    clusters = []

    for i in range(len(memories)):
        if i in assigned:
            continue

        # Start new cluster with this memory as seed
        cluster_indices = [i]
        assigned.add(i)
        centroid = normalized[i].copy()

        for j in range(i + 1, len(memories)):
            if j in assigned:
                continue
            sim = float(np.dot(centroid, normalized[j]))
            if sim >= (1.0 - max_distance):
                cluster_indices.append(j)
                assigned.add(j)
                # Update centroid incrementally
                n = len(cluster_indices)
                centroid = (centroid * (n - 1) + normalized[j]) / n
                centroid /= np.linalg.norm(centroid)

        if len(cluster_indices) >= min_cluster_size:
            clusters.append([memories[idx] for idx in cluster_indices])

    return clusters


def compute_merged_content(cluster: List[Dict]) -> str:
    """Create merged content from a cluster of memories.

    Takes unique content lines, ordered by recency.
    """
    seen = set()
    lines = []
    for mem in reversed(cluster):  # newest first after reverse
        text = mem["memory"].strip()
        if text and text not in seen:
            seen.add(text)
            lines.append(text)

    if len(lines) <= 5:
        return " | ".join(lines)

    # Truncate to top 5 + count
    return " | ".join(lines[:5]) + f" [+{len(lines)-5} merged]"


def compute_merged_q(
    cluster: List[Dict],
    q_cache: QCache,
    experience: str = "default",
) -> Dict:
    """Compute Q-value for merged memory using similarity-weighted average.

    Q_merged = Σ(q_i × sim_i) / Σ(sim_i)
    where sim_i = cosine similarity to cluster centroid.
    """
    vectors = np.array([m["vector"] for m in cluster])
    centroid = np.mean(vectors, axis=0)
    centroid_norm = np.linalg.norm(centroid)
    if centroid_norm > 0:
        centroid = centroid / centroid_norm

    # Compute per-memory similarity to centroid
    sims = []
    for m in cluster:
        v = np.array(m["vector"])
        norm = np.linalg.norm(v)
        if norm > 0:
            sims.append(float(np.dot(centroid, v / norm)))
        else:
            sims.append(0.0)

    total_sim = sum(sims)
    if total_sim == 0:
        total_sim = 1.0

    # Weighted Q-values per layer
    q_action_sum = 0.0
    q_hypothesis_sum = 0.0
    q_fit_sum = 0.0
    visits_sum = 0

    for mem, sim in zip(cluster, sims):
        q_data = q_cache.get(mem["id"], experience)
        if q_data:
            q_action_sum += q_data.get("q_action", 0.5) * sim
            q_hypothesis_sum += q_data.get("q_hypothesis", 0.5) * sim
            q_fit_sum += q_data.get("q_fit", 0.5) * sim
            visits_sum += q_data.get("q_visits", 0)
        else:
            q_action_sum += 0.5 * sim
            q_hypothesis_sum += 0.5 * sim
            q_fit_sum += 0.5 * sim

    q_action = q_action_sum / total_sim
    q_hypothesis = q_hypothesis_sum / total_sim
    q_fit = q_fit_sum / total_sim
    q_combined = 0.5 * q_action + 0.2 * q_hypothesis + 0.3 * q_fit

    # κ (stiffness) = inverse variance of rewards
    rewards = []
    for mem in cluster:
        q_data = q_cache.get(mem["id"], experience)
        if q_data and "last_reward" in q_data:
            rewards.append(q_data["last_reward"])
    kappa = 1.0 / max(np.var(rewards), 0.01) if rewards else 1.0

    return {
        "q_value": round(q_combined, 4),
        "q_action": round(q_action, 4),
        "q_hypothesis": round(q_hypothesis, 4),
        "q_fit": round(q_fit, 4),
        "q_visits": visits_sum,
        "kappa": round(kappa, 2),
        "q_updated_at": datetime.now(timezone.utc).isoformat(),
        "last_layer_updated": "compaction",
    }


def compact_cluster(
    cluster: List[Dict],
    qc: QdrantClient,
    q_cache: QCache,
    experience: str = "default",
    dry_run: bool = False,
) -> Optional[Dict]:
    """Merge a cluster into a single compressed memory.

    Returns the new merged memory info, or None if dry_run.
    """
    from .direct_search import _embed
    from .lifecycle import MemoryLifecycle

    merged_content = compute_merged_content(cluster)
    merged_q = compute_merged_q(cluster, q_cache, experience)
    original_ids = [m["id"] for m in cluster]

    # Inherit metadata from the memory with highest Q-value
    best_mem = max(cluster, key=lambda m: (
        q_cache.get(m["id"], experience) or {}
    ).get("q_value", 0.0))
    best_payload = best_mem["payload"]

    result = {
        "merged_content": merged_content,
        "original_count": len(cluster),
        "original_ids": original_ids,
        "q_value": merged_q["q_value"],
        "kappa": merged_q["kappa"],
    }

    if dry_run:
        return result

    # Create merged memory
    new_id = str(uuid.uuid4())
    vector = _embed(merged_content)
    now = datetime.now(timezone.utc).isoformat()

    payload = {
        "memory": merged_content,
        "agent_id": best_payload.get("agent_id", "session"),
        "memory_type": best_payload.get("memory_type", "fact"),
        "created_at": now,
        "source": "compaction",
        "status": "confirmed",
        "status_updated_at": now,
        "importance": best_payload.get("importance", 0.5),
        "metadata": {
            "agent": best_payload.get("agent_id", "session"),
            "type": best_payload.get("memory_type", "fact"),
            "source": "compaction",
            "merged_from": original_ids,
            "merge_count": len(original_ids),
            "kappa": merged_q["kappa"],
            "tags": best_payload.get("metadata", {}).get("tags", []),
            "client_id": best_payload.get("metadata", {}).get("client_id"),
        },
        "client_id": best_payload.get("client_id"),
    }

    # Upsert to Qdrant
    qc.upsert(
        collection_name=COLLECTION_NAME,
        points=[PointStruct(id=new_id, vector=vector, payload=payload)],
    )

    # Set Q-values for merged memory
    q_cache.set(new_id, merged_q, experience)

    # Mark originals as merged
    lifecycle = MemoryLifecycle()
    for mem in cluster:
        mem_status = mem["payload"].get("status", "active")
        if mem_status in ("active", "confirmed"):
            lifecycle.transition(mem["id"], mem_status, "merged")

    result["new_id"] = new_id
    logger.info(
        "Compacted %d memories into %s (Q=%.3f, κ=%.1f)",
        len(cluster), new_id[:8], merged_q["q_value"], merged_q["kappa"],
    )
    return result


def compact_memories(
    max_distance: float = 0.25,
    min_cluster_size: int = 3,
    client_id: Optional[str] = None,
    project: Optional[str] = None,
    experience: str = "default",
    dry_run: bool = False,
    max_clusters: int = 50,
) -> Dict:
    """Run full compaction pipeline.

    1. Fetch active memories
    2. Find clusters
    3. Merge each cluster
    4. Return summary
    """
    qc = _get_qdrant()
    q_cache = QCache()
    q_cache.load(Q_CACHE_PATH)

    logger.info("Fetching active memories...")
    memories = fetch_active_memories(qc, client_id=client_id, project=project)
    logger.info("Found %d active memories", len(memories))

    if len(memories) < min_cluster_size:
        return {"memories_found": len(memories), "clusters": 0, "compacted": 0}

    logger.info("Finding clusters (max_distance=%.2f, min_size=%d)...", max_distance, min_cluster_size)
    clusters = find_clusters(memories, max_distance, min_cluster_size)
    logger.info("Found %d clusters", len(clusters))

    results = []
    for cluster in clusters[:max_clusters]:
        result = compact_cluster(cluster, qc, q_cache, experience, dry_run)
        if result:
            results.append(result)

    if not dry_run and results:
        q_cache.save(Q_CACHE_PATH)

    total_merged = sum(r["original_count"] for r in results)
    return {
        "memories_found": len(memories),
        "clusters": len(clusters),
        "compacted": len(results),
        "memories_merged": total_merged,
        "dry_run": dry_run,
        "details": results,
    }
