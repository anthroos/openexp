# Benchmarks

Reproducible scripts for the numbers cited in `docs/benchmark-results.md`.

## LongMemEval

`longmemeval_bench.py` evaluates OpenExp's retrieval against the
[LongMemEval](https://github.com/xiaowu0162/LongMemEval) benchmark — 500
questions over long multi-session histories.

### Get the data

The dataset (`longmemeval_s_cleaned.json`, ~277 MB) is not committed. Download
it from the upstream MemPalace repo:

```bash
mkdir -p benchmarks/data
curl -L -o benchmarks/data/longmemeval_s_cleaned.json \
  https://github.com/mem-palace/mempalace/raw/main/data/longmemeval_s_cleaned.json
```

Or any other LongMemEval mirror that publishes the cleaned variant in the same
schema (`question`, `haystack_sessions`, `haystack_session_ids`,
`haystack_dates`, `answer_session_ids`, `question_type`).

### Run

```bash
# Raw mode — pure vector similarity (baseline)
python benchmarks/longmemeval_bench.py benchmarks/data/longmemeval_s_cleaned.json --mode raw

# Hybrid — vector + BM25 (OpenExp default)
python benchmarks/longmemeval_bench.py benchmarks/data/longmemeval_s_cleaned.json --mode hybrid

# Full — vector + BM25 + recency
python benchmarks/longmemeval_bench.py benchmarks/data/longmemeval_s_cleaned.json --mode full

# Smoke test — 20 questions only
python benchmarks/longmemeval_bench.py benchmarks/data/longmemeval_s_cleaned.json --mode hybrid --limit 20
```

Embedding model: `BAAI/bge-small-en-v1.5` (same as production). Vector store:
in-memory Qdrant (each question gets a fresh collection). Run takes a few hours
end-to-end on the full 500 questions; use `--limit` for quick checks.

Last published numbers (hybrid mode, session granularity, 500/500 questions):
R@1 = 0.880, R@10 = 0.986, NDCG@10 = 0.924. See `docs/benchmark-results.md` for
the full table and per-type breakdown.
