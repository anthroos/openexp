"""Hybrid Search — BM25 keyword scoring alongside vector search.

Pure Python implementation with no external dependencies.
Combines semantic similarity, keyword relevance, recency, and importance.
"""
import math
import re
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any
from collections import Counter, defaultdict

logger = logging.getLogger(__name__)

# Default BM25 parameters
DEFAULT_K1 = 1.5
DEFAULT_B = 0.75

# Default hybrid search weights
# Base importance multiplier by memory type (moved from core/scoring.py when
# its dead composite scorer was removed).
TYPE_BOOST = {
    "decision": 1.0,
    "preference": 0.95,
    "relationship": 0.9,
    "insight": 0.9,
    "procedure": 0.85,
    "fact": 0.8,
    "event": 0.7,
    "intention": 0.75,
    "plan": 0.85,
    "commitment": 0.9,
    "action": 0.8,
    "outcome": 0.85,
    "cancellation": 0.6,
}

RECENCY_HALF_LIFE_DAYS = 90.0


def _compute_recency(created_str=None, half_life_days: float = RECENCY_HALF_LIFE_DAYS) -> float:
    """Recency factor from ISO date string, exponential decay (0.5 fallback)."""
    if not created_str:
        return 0.5
    try:
        created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        age_days = (now - created_dt.replace(tzinfo=timezone.utc if created_dt.tzinfo is None else created_dt.tzinfo)).days
        age_days = max(age_days, 0)
        return math.exp(-0.693 * age_days / half_life_days)
    except (ValueError, TypeError):
        return 0.5


# Q-value was dropped from the formula on 2026-07-03: after 8 months the mean
# Q across the cache was 0.006, so its 0.10 weight contributed ~0.0006 to every
# score — dead weight that also contradicted the documented ranking.
DEFAULT_HYBRID_WEIGHTS = {
    "w_semantic": 0.45,
    "w_keyword": 0.15,
    "w_recency": 0.25,
    "w_importance": 0.15,
}

# Status weight multipliers for lifecycle integration
STATUS_WEIGHTS = {
    "confirmed": 1.2,
    "active": 1.0,
    "outdated": 0.5,
    "archived": 0.3,
    "contradicted": 0.1,
    "merged": 0.2,
    "superseded": 0.2,
    "deleted": 0.0,
}


def tokenize(text: str) -> List[str]:
    """Simple tokenization for BM25."""
    if not text:
        return []
    tokens = re.findall(r'\b[a-zA-Z0-9]+\b', text.lower())
    return [token for token in tokens if len(token) >= 2]


def compute_tf(tokens: List[str]) -> Dict[str, float]:
    """Compute term frequency for a document."""
    if not tokens:
        return {}
    counts = Counter(tokens)
    doc_length = len(tokens)
    return {term: count / doc_length for term, count in counts.items()}


def compute_idf(documents: List[List[str]]) -> Dict[str, float]:
    """Compute inverse document frequency for a corpus."""
    if not documents:
        return {}
    N = len(documents)
    term_doc_count = defaultdict(int)
    for doc_tokens in documents:
        unique_terms = set(doc_tokens)
        for term in unique_terms:
            term_doc_count[term] += 1
    idf = {}
    for term, df in term_doc_count.items():
        idf[term] = math.log(N / df) if df > 0 else 0.0
    return idf


def bm25_score(
    query: str,
    document: str,
    corpus_stats: Dict[str, Any] = None,
    k1: float = DEFAULT_K1,
    b: float = DEFAULT_B,
) -> float:
    """Compute BM25 score for a query-document pair."""
    if not query or not document:
        return 0.0
    query_tokens = tokenize(query)
    doc_tokens = tokenize(document)
    if not query_tokens or not doc_tokens:
        return 0.0

    doc_length = len(doc_tokens)
    doc_tf = compute_tf(doc_tokens)

    if corpus_stats is None:
        idf_scores = {term: 2.0 for term in set(query_tokens)}
        avgdl = doc_length
    else:
        idf_scores = corpus_stats.get("idf", {})
        avgdl = corpus_stats.get("avgdl", doc_length)

    score = 0.0
    for term in query_tokens:
        if term not in doc_tf:
            continue
        tf = doc_tf[term] * len(doc_tokens)
        idf = idf_scores.get(term, 1.0)
        numerator = tf * (k1 + 1)
        denominator = tf + k1 * (1 - b + b * (doc_length / avgdl))
        score += idf * (numerator / denominator)

    return score


def prepare_corpus_stats(documents: List[str]) -> Dict[str, Any]:
    """Pre-compute corpus statistics for efficient BM25 scoring."""
    if not documents:
        return {"idf": {}, "avgdl": 0}
    tokenized_docs = [tokenize(doc) for doc in documents]
    idf = compute_idf(tokenized_docs)
    doc_lengths = [len(tokens) for tokens in tokenized_docs]
    avgdl = sum(doc_lengths) / len(doc_lengths) if doc_lengths else 0
    return {"idf": idf, "avgdl": avgdl, "doc_count": len(documents)}


def hybrid_search(
    query: str,
    vector_results: List[Dict[str, Any]],
    top_k: int = 20,
    weights: Dict[str, float] = None,
    corpus_stats: Dict[str, Any] = None,
) -> List[Dict[str, Any]]:
    """Combine vector search results with BM25 keyword scoring."""
    if not vector_results:
        return []

    if weights is None:
        weights = DEFAULT_HYBRID_WEIGHTS.copy()

    if corpus_stats is None:
        documents = []
        for result in vector_results:
            doc_text = result.get("memory", result.get("content", ""))
            if doc_text:
                documents.append(doc_text)
        corpus_stats = prepare_corpus_stats(documents)

    scored_results = []
    for result in vector_results:
        doc_content = result.get("memory", result.get("content", ""))
        semantic_score = result.get("score", result.get("composite_score", 0.5))
        keyword_score = bm25_score(query, doc_content, corpus_stats)
        normalized_keyword = 1.0 / (1.0 + math.exp(-keyword_score / 3.0))

        metadata = result.get("metadata", {})
        payload = result.get("payload", metadata)

        created_at = payload.get("created_at", metadata.get("created_at"))
        importance = payload.get("importance", metadata.get("importance", 0.8))
        memory_type = payload.get("memory_type", metadata.get("type", "fact"))

        recency = _compute_recency(created_at)
        type_weight = TYPE_BOOST.get(memory_type, 0.8)
        weighted_importance = importance * type_weight

        status = payload.get("status", result.get("status", "active"))
        status_multiplier = STATUS_WEIGHTS.get(status, 1.0)

        hybrid_score = (
            weights["w_semantic"] * semantic_score +
            weights["w_keyword"] * normalized_keyword +
            weights["w_recency"] * recency +
            weights["w_importance"] * weighted_importance
        ) * status_multiplier

        hybrid_score = max(0.0, min(1.0, hybrid_score))

        enhanced_result = result.copy()
        enhanced_result.update({
            "hybrid_score": hybrid_score,
            "keyword_score": normalized_keyword,
            "raw_bm25": keyword_score,
            "recency_score": recency,
            "importance_score": weighted_importance,
            "status_multiplier": status_multiplier,
            "status": status,
        })
        scored_results.append(enhanced_result)

    scored_results.sort(key=lambda x: x["hybrid_score"], reverse=True)

    # Collapse duplicates (identical content ingested from resumed sessions
    # or subagent transcripts) — keep the highest-scored copy of each text.
    seen_texts = set()
    deduped = []
    for r in scored_results:
        key = r.get("memory", r.get("content", ""))[:500]
        if key in seen_texts:
            continue
        seen_texts.add(key)
        deduped.append(r)

    return deduped[:top_k]
