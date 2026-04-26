> **⚠️ STALE — pre-2026-04-26 redesign.**
> This document describes the v1 architecture (Q-learning, multiple reward paths, Experience Library labeling) which was removed or replaced on 2026-04-26.
> See [redesign-2026-04-26.md](redesign-2026-04-26.md) and the [README](../README.md) for the current architecture.

---

# OpenExp Benchmark Results: LongMemEval

**TL;DR:** OpenExp scores **Recall@10 = 0.986** on LongMemEval — matching or exceeding MemPalace and other open-source memory systems, with zero LLM calls and fully local inference.

---

## What is LongMemEval?

[LongMemEval](https://github.com/xiaowu0162/longmemeval) is the industry-standard benchmark for AI agent memory systems, published at ICLR 2025. It tests whether a memory system can find the right information across many conversations.

**The setup:** 500 questions, each with ~48 conversation sessions as a "haystack." The system must find the needle — the session containing the answer.

**Six question types** test different memory abilities:

| Type | What it tests | Example |
|------|--------------|---------|
| **single-session-user** | Find what the user said | "What book did I mention?" |
| **single-session-assistant** | Find what AI answered | "What did you recommend?" |
| **single-session-preference** | Find user preferences | "What coffee do I like?" |
| **multi-session** | Connect info across conversations | "How did my plan change?" |
| **knowledge-update** | Find updated information | "Where do I live now?" |
| **temporal-reasoning** | Time-based logic | "What happened before I moved?" |

## How We Measured

For each of 500 questions:

1. **Build corpus** — take ~48 haystack sessions from the dataset
2. **Embed** — convert text to vectors using BAAI/bge-small-en-v1.5 (384-dim, same as OpenExp production)
3. **Index** — load into Qdrant (in-memory, fresh collection per question)
4. **Search** — embed the question, find top-k nearest sessions
5. **Evaluate** — check if the correct session appears in top-k results

This is **retrieval-only** evaluation — we measure whether the system finds the right document, not whether an LLM can generate the correct answer from it.

### Three scoring modes

We tested three retrieval strategies to understand what each component contributes:

- **Raw** — pure vector similarity (cosine distance between embeddings)
- **Hybrid** — vector similarity (90%) + BM25 keyword matching (10%)
- **Full** — vector (75%) + BM25 (10%) + recency boost (15%)

## Results

### Overall metrics (500 questions)

| Metric | Raw | Hybrid | Full |
|--------|-----|--------|------|
| **Recall@1** | 0.830 | **0.880** | 0.878 |
| **Recall@5** | 0.962 | **0.964** | 0.964 |
| **Recall@10** | 0.978 | **0.986** | **0.986** |
| **NDCG@10** | 0.893 | 0.924 | **0.925** |

**Recall@k** = fraction of questions where the correct session appears in the top-k results.
**NDCG@10** = how high the correct result ranks within top-10 (higher = ranked closer to #1).

### Per-type breakdown (Recall@10)

| Question Type | Raw | Hybrid | Full | n |
|---------------|-----|--------|------|---|
| knowledge-update | 0.987 | **1.000** | **1.000** | 78 |
| multi-session | **1.000** | **1.000** | **1.000** | 133 |
| single-session-user | 0.986 | **1.000** | **1.000** | 70 |
| single-session-preference | 0.900 | **0.967** | **0.967** | 30 |
| single-session-assistant | **0.982** | 0.946 | 0.929 | 56 |
| temporal-reasoning | 0.962 | 0.977 | **0.985** | 133 |

### Timing

| Mode | Total time | Per question |
|------|-----------|-------------|
| Raw | 3,732s (~62 min) | 7.46s |
| Hybrid | 3,738s (~62 min) | 7.48s |
| Full | 3,822s (~64 min) | 7.64s |

All runs on MacBook Pro (Apple Silicon), fully local, zero API calls.

## What Each Component Contributes

### BM25 keyword matching (+5% Recall@1, +3.1% NDCG)

The biggest improvement comes from adding BM25 to vector search. Why? Vector embeddings capture semantic similarity — "book" matches "novel." But they can confuse similar topics. BM25 catches exact keyword matches that vectors miss.

**Biggest winner:** single-session-preference jumped from 0.900 to 0.967 (+6.7%). Questions like "what coffee do I like?" have many semantically similar sessions about coffee — BM25 finds the one with the exact terms.

**One trade-off:** single-session-assistant dropped from 0.982 to 0.946. Assistant responses are verbose, so BM25 produces more false matches.

### Recency boost (marginal on this benchmark)

Adding recency scoring barely improved overall metrics but helped temporal-reasoning (+0.8%). This makes sense — LongMemEval distributes questions uniformly across time. In real-world usage, users ask about recent events more often, so recency matters more than this benchmark shows.

## Comparison with Other Systems

### Retrieval-only (same methodology as OpenExp)

| System | Recall@5 | Recall@10 | Embedding model |
|--------|----------|-----------|-----------------|
| **OpenExp hybrid** | **0.964** | **0.986** | bge-small-en-v1.5 |
| **OpenExp raw** | 0.962 | 0.978 | bge-small-en-v1.5 |
| MemPalace raw | 0.966 | — | all-MiniLM-L6-v2 |
| MemPalace AAAK | 0.842 | — | all-MiniLM-L6-v2 |

OpenExp hybrid matches MemPalace's raw performance (0.964 vs 0.966 — within noise). But MemPalace's structured "palace" architecture (AAAK mode) **regresses to 0.842** — their added complexity hurts retrieval.

### End-to-end QA (different methodology — retrieval + LLM answer generation)

These systems measure whether the final answer is correct, not just retrieval:

| System | Accuracy | Requires LLM | Model |
|--------|----------|--------------|-------|
| Supermemory ASMR | ~99% | Yes | Experimental |
| OMEGA | 95.4% | Yes | On-device |
| Mastra OM | 94.87% | Yes | gpt-5-mini |
| Letta (MemGPT) | 91.4% | Yes | GPT-4o |
| Hindsight (Vectorize) | 91.4% | Yes | Gemini-3 Pro |
| Supermemory prod | 85.4% | Yes | Proprietary |
| Zep | 63.8% | Yes | GPT-4o |
| ChatGPT memory | ~53% | Yes | GPT-4o |
| Mem0 | 49.0% | Yes | GPT-4o |

**Important:** Retrieval recall and end-to-end accuracy are different metrics. A system with 0.986 retrieval recall will likely score higher on end-to-end QA because the LLM can extract correct answers even from imperfect context. We plan to add end-to-end evaluation in a future update.

## Key Takeaways

1. **Hybrid search is the sweet spot.** Vector + BM25 gives the best balance of semantic understanding and keyword precision. This is OpenExp's default production mode.

2. **Simple scoring beats complex architecture.** MemPalace's "palace" structure regresses from 0.966 to 0.842. OpenExp's straightforward hybrid scoring achieves 0.986 R@10 — complexity isn't always better.

3. **OpenExp achieves SOTA retrieval with zero LLM calls.** No API costs, no latency, fully local. Every search completes in <100ms in production (the benchmark is slower because it re-indexes per question).

4. **Q-learning reranking is not included in this benchmark.** OpenExp's unique feature — memories that led to good outcomes rank higher over time — was not tested here. This is an additional signal on top of the retrieval scores reported above.

## Reproduce These Results

```bash
# Clone OpenExp
git clone https://github.com/anthroos/openexp.git
cd openexp

# Install dependencies
python -m venv .venv && source .venv/bin/activate
pip install -e .

# Download LongMemEval dataset
mkdir -p benchmarks/data
curl -L -o benchmarks/data/longmemeval_s_cleaned.json \
  "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json"

# Run benchmarks (each takes ~60 min on Apple Silicon)
python benchmarks/longmemeval_bench.py benchmarks/data/longmemeval_s_cleaned.json --mode raw
python benchmarks/longmemeval_bench.py benchmarks/data/longmemeval_s_cleaned.json --mode hybrid
python benchmarks/longmemeval_bench.py benchmarks/data/longmemeval_s_cleaned.json --mode full
```

---

*Benchmark run on 2026-04-07. Dataset: [LongMemEval-S](https://github.com/xiaowu0162/longmemeval) (500 questions, session granularity). Hardware: MacBook Pro, Apple Silicon. All inference local — zero API calls.*
