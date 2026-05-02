#!/usr/bin/env python3
"""
OpenExp × LongMemEval Benchmark
================================

Evaluates OpenExp's retrieval against the LongMemEval benchmark.
Apples-to-apples comparison with MemPalace's benchmark.

For each of the 500 questions:
1. Ingest all haystack sessions into a fresh Qdrant collection
2. Query with OpenExp's multi-signal scoring (vector + BM25)
3. Score retrieval against ground-truth answer sessions

Modes:
    raw         — pure vector similarity (baseline, like MemPalace raw)
    hybrid      — vector + BM25 keyword scoring (OpenExp default)
    full        — vector + BM25 + recency + importance (full OpenExp pipeline)

Usage:
    python benchmarks/longmemeval_bench.py /tmp/mempalace/data/longmemeval_s_cleaned.json
    python benchmarks/longmemeval_bench.py /tmp/mempalace/data/longmemeval_s_cleaned.json --mode hybrid
    python benchmarks/longmemeval_bench.py /tmp/mempalace/data/longmemeval_s_cleaned.json --limit 20
"""

import json
import math
import argparse
import sys
import uuid
from pathlib import Path
from collections import defaultdict
from datetime import datetime

from fastembed import TextEmbedding
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
)

# Add openexp to path
sys.path.insert(0, str(Path(__file__).parent.parent))


# =============================================================================
# METRICS (same as MemPalace for fair comparison)
# =============================================================================

def dcg(relevances, k):
    score = 0.0
    for i, rel in enumerate(relevances[:k]):
        score += rel / math.log2(i + 2)
    return score


def ndcg(rankings, correct_ids, corpus_ids, k):
    relevances = [1.0 if corpus_ids[idx] in correct_ids else 0.0 for idx in rankings[:k]]
    ideal = sorted(relevances, reverse=True)
    idcg = dcg(ideal, k)
    if idcg == 0:
        return 0.0
    return dcg(relevances, k) / idcg


def evaluate_retrieval(rankings, correct_ids, corpus_ids, k):
    top_k_ids = set(corpus_ids[idx] for idx in rankings[:k])
    recall_any = float(any(cid in top_k_ids for cid in correct_ids))
    recall_all = float(all(cid in top_k_ids for cid in correct_ids))
    ndcg_score = ndcg(rankings, correct_ids, corpus_ids, k)
    return recall_any, recall_all, ndcg_score


def session_id_from_corpus_id(corpus_id):
    if "_turn_" in corpus_id:
        return corpus_id.rsplit("_turn_", 1)[0]
    return corpus_id


# =============================================================================
# BM25 SCORING (from OpenExp's hybrid scoring)
# =============================================================================

def bm25_score(query_tokens, doc_tokens, avg_dl, k1=1.5, b=0.75):
    """Simple BM25 score for a single document."""
    dl = len(doc_tokens)
    score = 0.0
    doc_freq = {}
    for t in doc_tokens:
        doc_freq[t] = doc_freq.get(t, 0) + 1
    for qt in set(query_tokens):
        tf = doc_freq.get(qt, 0)
        if tf == 0:
            continue
        numerator = tf * (k1 + 1)
        denominator = tf + k1 * (1 - b + b * dl / avg_dl)
        score += numerator / denominator
    return score


def tokenize(text):
    """Simple whitespace + lowercasing tokenizer."""
    import re
    return re.findall(r'\w+', text.lower())


# =============================================================================
# EMBEDDING MODEL
# =============================================================================

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"  # same as OpenExp production
_embedder = None


def get_embedder():
    global _embedder
    if _embedder is None:
        print(f"  Loading embedding model: {EMBEDDING_MODEL}...")
        _embedder = TextEmbedding(model_name=EMBEDDING_MODEL)
        print("  Model ready.")
    return _embedder


def embed_texts(texts):
    embedder = get_embedder()
    return list(embedder.embed(texts))


# =============================================================================
# QDRANT EPHEMERAL
# =============================================================================

_qdrant = QdrantClient(":memory:")
COLLECTION = "bench"
VECTOR_DIM = 384


def fresh_collection():
    """Delete and recreate collection for clean slate."""
    try:
        _qdrant.delete_collection(COLLECTION)
    except Exception:
        pass
    _qdrant.create_collection(
        collection_name=COLLECTION,
        vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
    )


# =============================================================================
# RETRIEVAL MODES
# =============================================================================

def retrieve_raw(entry, granularity="session", n_results=50):
    """Raw mode: pure vector similarity search (baseline)."""
    corpus, corpus_ids, corpus_timestamps = build_corpus(entry, granularity)
    if not corpus:
        return [], corpus, corpus_ids, corpus_timestamps

    fresh_collection()

    # Embed and ingest
    vectors = embed_texts(corpus)
    points = [
        PointStruct(id=i, vector=v.tolist(), payload={"corpus_id": cid, "timestamp": ts})
        for i, (v, cid, ts) in enumerate(zip(vectors, corpus_ids, corpus_timestamps))
    ]
    _qdrant.upsert(collection_name=COLLECTION, points=points)

    # Query
    query = entry["question"]
    query_vec = embed_texts([query])[0].tolist()
    results = _qdrant.query_points(
        collection_name=COLLECTION,
        query=query_vec,
        limit=min(n_results, len(corpus)),
        with_payload=True,
    )

    ranked_indices = [p.id for p in results.points]

    # Fill missing
    seen = set(ranked_indices)
    for i in range(len(corpus)):
        if i not in seen:
            ranked_indices.append(i)

    return ranked_indices, corpus, corpus_ids, corpus_timestamps


def retrieve_hybrid(entry, granularity="session", n_results=50, bm25_weight=0.10):
    """Hybrid mode: vector similarity + BM25 keyword scoring."""
    corpus, corpus_ids, corpus_timestamps = build_corpus(entry, granularity)
    if not corpus:
        return [], corpus, corpus_ids, corpus_timestamps

    fresh_collection()

    vectors = embed_texts(corpus)
    points = [
        PointStruct(id=i, vector=v.tolist(), payload={"corpus_id": cid, "timestamp": ts})
        for i, (v, cid, ts) in enumerate(zip(vectors, corpus_ids, corpus_timestamps))
    ]
    _qdrant.upsert(collection_name=COLLECTION, points=points)

    # Vector search
    query = entry["question"]
    query_vec = embed_texts([query])[0].tolist()
    results = _qdrant.query_points(
        collection_name=COLLECTION,
        query=query_vec,
        limit=min(n_results, len(corpus)),
        with_payload=True,
    )

    # BM25 scoring
    query_tokens = tokenize(query)
    doc_tokens_list = [tokenize(doc) for doc in corpus]
    avg_dl = sum(len(dt) for dt in doc_tokens_list) / max(len(doc_tokens_list), 1)

    bm25_scores = {}
    for i, dt in enumerate(doc_tokens_list):
        bm25_scores[i] = bm25_score(query_tokens, dt, avg_dl)

    # Normalize BM25
    max_bm25 = max(bm25_scores.values()) if bm25_scores else 1.0
    if max_bm25 > 0:
        for k in bm25_scores:
            bm25_scores[k] /= max_bm25

    # Combine: vector_score * (1 - bm25_weight) + bm25 * bm25_weight
    combined = []
    for point in results.points:
        idx = point.id
        vec_score = point.score  # cosine similarity
        bm25_s = bm25_scores.get(idx, 0.0)
        final = vec_score * (1 - bm25_weight) + bm25_s * bm25_weight
        combined.append((idx, final))

    # Add any docs not in vector results (with bm25-only score)
    seen = {idx for idx, _ in combined}
    for i in range(len(corpus)):
        if i not in seen:
            bm25_s = bm25_scores.get(i, 0.0)
            combined.append((i, bm25_s * bm25_weight))

    combined.sort(key=lambda x: x[1], reverse=True)
    ranked_indices = [idx for idx, _ in combined]

    return ranked_indices, corpus, corpus_ids, corpus_timestamps


def retrieve_full(entry, granularity="session", n_results=50,
                  bm25_weight=0.10, recency_weight=0.15):
    """Full mode: vector + BM25 + recency boost (simulates OpenExp scoring)."""
    corpus, corpus_ids, corpus_timestamps = build_corpus(entry, granularity)
    if not corpus:
        return [], corpus, corpus_ids, corpus_timestamps

    fresh_collection()

    vectors = embed_texts(corpus)
    points = [
        PointStruct(id=i, vector=v.tolist(), payload={"corpus_id": cid, "timestamp": ts})
        for i, (v, cid, ts) in enumerate(zip(vectors, corpus_ids, corpus_timestamps))
    ]
    _qdrant.upsert(collection_name=COLLECTION, points=points)

    query = entry["question"]
    query_vec = embed_texts([query])[0].tolist()
    results = _qdrant.query_points(
        collection_name=COLLECTION,
        query=query_vec,
        limit=min(n_results, len(corpus)),
        with_payload=True,
    )

    # BM25
    query_tokens = tokenize(query)
    doc_tokens_list = [tokenize(doc) for doc in corpus]
    avg_dl = sum(len(dt) for dt in doc_tokens_list) / max(len(doc_tokens_list), 1)

    bm25_scores = {}
    for i, dt in enumerate(doc_tokens_list):
        bm25_scores[i] = bm25_score(query_tokens, dt, avg_dl)
    max_bm25 = max(bm25_scores.values()) if bm25_scores else 1.0
    if max_bm25 > 0:
        for k in bm25_scores:
            bm25_scores[k] /= max_bm25

    # Recency scoring
    # Parse dates and compute recency relative to question_date
    question_date = entry.get("question_date", "")
    recency_scores = {}
    try:
        q_date = datetime.strptime(question_date, "%Y-%m-%d") if question_date else None
    except ValueError:
        q_date = None

    for i, ts in enumerate(corpus_timestamps):
        if q_date and ts:
            try:
                doc_date = datetime.strptime(ts[:10], "%Y-%m-%d")
                days_ago = (q_date - doc_date).days
                # Exponential decay: recent = higher score
                recency_scores[i] = max(0, 1.0 - days_ago / 365.0)
            except (ValueError, TypeError):
                recency_scores[i] = 0.5
        else:
            recency_scores[i] = 0.5

    # Combine
    vec_weight = 1.0 - bm25_weight - recency_weight
    combined = []
    for point in results.points:
        idx = point.id
        vec_s = point.score
        bm25_s = bm25_scores.get(idx, 0.0)
        rec_s = recency_scores.get(idx, 0.5)
        final = vec_s * vec_weight + bm25_s * bm25_weight + rec_s * recency_weight
        combined.append((idx, final))

    seen = {idx for idx, _ in combined}
    for i in range(len(corpus)):
        if i not in seen:
            bm25_s = bm25_scores.get(i, 0.0)
            rec_s = recency_scores.get(i, 0.5)
            combined.append((i, bm25_s * bm25_weight + rec_s * recency_weight))

    combined.sort(key=lambda x: x[1], reverse=True)
    ranked_indices = [idx for idx, _ in combined]

    return ranked_indices, corpus, corpus_ids, corpus_timestamps


# =============================================================================
# CORPUS BUILDER
# =============================================================================

def build_corpus(entry, granularity="session"):
    """Build corpus from haystack sessions."""
    corpus = []
    corpus_ids = []
    corpus_timestamps = []

    sessions = entry["haystack_sessions"]
    session_ids = entry["haystack_session_ids"]
    dates = entry["haystack_dates"]

    for session, sess_id, date in zip(sessions, session_ids, dates):
        if granularity == "session":
            user_turns = [t["content"] for t in session if t["role"] == "user"]
            if user_turns:
                doc = "\n".join(user_turns)
                corpus.append(doc)
                corpus_ids.append(sess_id)
                corpus_timestamps.append(date)
        else:
            turn_num = 0
            for turn in session:
                if turn["role"] == "user":
                    corpus.append(turn["content"])
                    corpus_ids.append(f"{sess_id}_turn_{turn_num}")
                    corpus_timestamps.append(date)
                    turn_num += 1

    return corpus, corpus_ids, corpus_timestamps


# =============================================================================
# BENCHMARK RUNNER
# =============================================================================

def run_benchmark(data_file, granularity="session", limit=0, mode="raw", skip=0, out_file=None):
    with open(data_file) as f:
        data = json.load(f)

    if skip > 0:
        data = data[skip:]
    if limit > 0:
        data = data[:limit]

    print(f"\n{'=' * 60}")
    print("  OpenExp × LongMemEval Benchmark")
    print(f"{'=' * 60}")
    print(f"  Data:        {Path(data_file).name}")
    print(f"  Questions:   {len(data)}")
    print(f"  Granularity: {granularity}")
    print(f"  Mode:        {mode}")
    print(f"  Embedding:   {EMBEDDING_MODEL}")
    print(f"  Vector DB:   Qdrant (in-memory)")
    print(f"{'─' * 60}\n")

    ks = [1, 3, 5, 10, 30, 50]
    metrics_session = {f"recall_any@{k}": [] for k in ks}
    metrics_session.update({f"ndcg_any@{k}": [] for k in ks})
    per_type = defaultdict(lambda: defaultdict(list))
    results_log = []
    start_time = datetime.now()

    for i, entry in enumerate(data):
        qid = entry["question_id"]
        qtype = entry["question_type"]
        question = entry["question"]
        answer_sids = set(entry["answer_session_ids"])

        if mode == "hybrid":
            rankings, corpus, corpus_ids, corpus_timestamps = retrieve_hybrid(
                entry, granularity=granularity
            )
        elif mode == "full":
            rankings, corpus, corpus_ids, corpus_timestamps = retrieve_full(
                entry, granularity=granularity
            )
        else:
            rankings, corpus, corpus_ids, corpus_timestamps = retrieve_raw(
                entry, granularity=granularity
            )

        if not rankings:
            print(f"  [{i+1:4}/{len(data)}] {qid[:30]:30} SKIP (empty corpus)")
            continue

        session_level_ids = [session_id_from_corpus_id(cid) for cid in corpus_ids]
        session_correct = answer_sids

        for k in ks:
            ra, rl, nd = evaluate_retrieval(rankings, session_correct, session_level_ids, k)
            metrics_session[f"recall_any@{k}"].append(ra)
            metrics_session[f"ndcg_any@{k}"].append(nd)

        per_type[qtype]["recall_any@5"].append(metrics_session["recall_any@5"][-1])
        per_type[qtype]["recall_any@10"].append(metrics_session["recall_any@10"][-1])

        r5 = metrics_session["recall_any@5"][-1]
        r10 = metrics_session["recall_any@10"][-1]
        status = "HIT" if r5 > 0 else "miss"
        print(f"  [{i+1:4}/{len(data)}] {qid[:30]:30} R@5={r5:.0f} R@10={r10:.0f}  {status}")

    elapsed = (datetime.now() - start_time).total_seconds()

    print(f"\n{'=' * 60}")
    print(f"  RESULTS — OpenExp ({mode} mode, {granularity} granularity)")
    print(f"{'=' * 60}")
    print(f"  Time: {elapsed:.1f}s ({elapsed / max(len(data), 1):.2f}s per question)\n")

    print("  SESSION-LEVEL METRICS:")
    for k in ks:
        key = f"recall_any@{k}"
        if metrics_session[key]:
            ra = sum(metrics_session[key]) / len(metrics_session[key])
            nd = sum(metrics_session[f"ndcg_any@{k}"]) / len(metrics_session[f"ndcg_any@{k}"])
            print(f"    Recall@{k:2}: {ra:.3f}    NDCG@{k:2}: {nd:.3f}")

    print("\n  PER-TYPE BREAKDOWN (session recall_any@10):")
    for qtype, vals in sorted(per_type.items()):
        if vals["recall_any@10"]:
            r10 = sum(vals["recall_any@10"]) / len(vals["recall_any@10"])
            n = len(vals["recall_any@10"])
            print(f"    {qtype:35} R@10={r10:.3f}  (n={n})")

    print(f"\n{'=' * 60}\n")

    if out_file:
        with open(out_file, "w") as f:
            for entry in results_log:
                f.write(json.dumps(entry) + "\n")
        print(f"  Results saved to: {out_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OpenExp × LongMemEval Benchmark")
    parser.add_argument("data_file", help="Path to longmemeval_s_cleaned.json")
    parser.add_argument("--granularity", choices=["session", "turn"], default="session")
    parser.add_argument("--mode", choices=["raw", "hybrid", "full"], default="raw")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of questions (0=all)")
    parser.add_argument("--skip", type=int, default=0, help="Skip first N questions")
    parser.add_argument("--out", type=str, default=None, help="Output JSONL file")
    args = parser.parse_args()

    run_benchmark(
        data_file=args.data_file,
        granularity=args.granularity,
        limit=args.limit,
        mode=args.mode,
        skip=args.skip,
        out_file=args.out,
    )
