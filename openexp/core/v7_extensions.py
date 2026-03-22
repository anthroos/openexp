"""Extension functions for lifecycle filtering and hybrid scoring."""
import logging
from typing import List, Dict, Any, Optional

from .lifecycle import MemoryLifecycle, DEFAULT_STATUS
from .hybrid_search import hybrid_search, DEFAULT_HYBRID_WEIGHTS, STATUS_WEIGHTS

logger = logging.getLogger(__name__)


def apply_lifecycle_filter(
    results: List[Dict[str, Any]],
    include_deleted: bool = False,
    include_contradicted: bool = True,
    include_superseded: bool = False,
) -> List[Dict[str, Any]]:
    """Filter search results by lifecycle status."""
    if not results:
        return []

    filtered = []
    for result in results:
        metadata = result.get("metadata", {})
        payload = result.get("payload", metadata)
        status = payload.get("status", DEFAULT_STATUS)

        if status == "deleted" and not include_deleted:
            continue
        elif status == "contradicted" and not include_contradicted:
            continue
        elif status == "superseded" and not include_superseded:
            continue

        filtered.append(result)

    return filtered


def apply_hybrid_scoring(
    query: str,
    results: List[Dict[str, Any]],
    weights: Optional[Dict[str, float]] = None,
    top_k: int = 20,
) -> List[Dict[str, Any]]:
    """Apply hybrid scoring (BM25 + lifecycle + composite) to search results."""
    if not results or not query.strip():
        return results[:top_k]

    try:
        hybrid_results = hybrid_search(
            query=query,
            vector_results=results,
            top_k=top_k,
            weights=weights or DEFAULT_HYBRID_WEIGHTS,
        )
        return hybrid_results
    except Exception as e:
        logger.error("Hybrid scoring failed: %s", e)
        return results[:top_k]
