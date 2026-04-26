> **⚠️ STALE — pre-2026-04-26 redesign.**
> This document describes the v1 architecture (Q-learning, multiple reward paths, Experience Library labeling) which was removed or replaced on 2026-04-26.
> See [redesign-2026-04-26.md](redesign-2026-04-26.md) and the [README](../README.md) for the current architecture.

---

# Experience Library

> Extract structured experience from conversation data. Not topic grouping — outcome-driven labeling.

## Overview

The Experience Library turns raw conversation transcripts into searchable, structured lessons. Each lesson captures what happened (context), what was done (actions), and what resulted (outcome) — the same format needed for LLM fine-tuning.

```
Qdrant (26K conversation memories)
    ↓ openexp chunk
18 chunks (~200K tokens each)
    ↓ openexp topics
170 topics per chunk
    ↓ Opus groups across chunks
36 work threads
    ↓ Opus extracts experience labels
269 structured labels
    ↓ stored in Qdrant (type="experience")
Searchable via search_memory
```

## Pipeline Steps

### Step 1: Chunking

Group all Qdrant transcripts by session, sort chronologically, pack into ~200K token chunks.

```bash
openexp chunk [--max-tokens 200000] [--output DIR]
```

Output: `~/.openexp/data/chunks/chunk_001.json` ... `chunk_NNN.json` + `manifest.json`

Source: `openexp/ingest/chunking.py`

### Step 2: Topic Extraction

Per chunk, LLM identifies all distinct work topics (projects, deals, initiatives).

```bash
openexp topics [--chunks 1 2 3] [--force]
```

Output: `chunk_001_topics.json` ... per chunk, with topic name, description, session_ids, message count, category, outcome_hint.

Source: `openexp/ingest/topic_mapping.py`

### Step 3: Thread Grouping

Opus groups topics across chunks into continuous work threads. Same project in chunks 3 and 12 = one thread.

Output: `threads.json` — array of threads with topic_names, chunks, date_range, status.

### Step 4: Experience Labeling

For each thread, Opus extracts:
1. **Timeline** — chronological events
2. **Experience labels** — structured context→actions→outcome triplets
3. **Summary** — status, key decisions, financial data

Output: `threads/thread_004_mpuv.json` per thread.

Source: `openexp/ingest/experience_extractor.py`, `scripts/batch_label.py`

### Step 5: Qdrant Storage

Experience labels are stored in Qdrant with:
- `memory_type: "experience"`
- `source: "experience_library"`
- Embedding computed from `situation + insight + applies_when` (search-optimized)
- Full label JSON in metadata for retrieval

Source: `add_experience()` in `openexp/core/direct_search.py`

## Experience Label Format

```json
{
  "experience_id": "exp_001",
  "context": {
    "situation": "What was the situation when this started",
    "constraints": ["Time pressure", "Budget limit"],
    "stakeholders": ["Who was involved and their role"],
    "prior_knowledge": "What we knew going in"
  },
  "actions": [
    {
      "what": "Specific action taken",
      "why": "Reasoning behind it",
      "when": "2026-03-14"
    }
  ],
  "outcome": {
    "result": "What happened",
    "success": true,
    "metrics": "Numbers if available",
    "surprise": "What was unexpected"
  },
  "lesson": {
    "insight": "One-sentence transferable insight",
    "applies_when": "When to use this lesson",
    "anti_pattern": "What NOT to do"
  }
}
```

The `applies_when` field is critical — it determines when the experience is retrieved. The embedding is computed from `situation + insight + applies_when`, so search matches by **pattern**, not by project name.

## Usage

### Search for experience

```bash
openexp search -q "client wants document automation" -n 5 -t experience
```

### Via MCP

```
search_memory(query="multi-agent pipeline design", type="experience", limit=5)
```

### Batch labeling

```bash
cd ~/openexp
.venv/bin/python3 scripts/batch_label.py [--force] [--thread-ids 1 2 3]
```

## Three-Level Architecture

| Level | How | When |
|-------|-----|------|
| **Prompt injection** | Search Qdrant → inject relevant experiences into system prompt | Now |
| **Compression** | Compress all 269 labels via compresr.ai to fit in context | Soon |
| **Fine-tuning** | LoRA on context→actions→outcome triplets | When model supports it |

The data format is the same for all three levels. Label once, use three ways.

## Files

| What | Path |
|------|------|
| Chunking | `openexp/ingest/chunking.py` |
| Topic mapping | `openexp/ingest/topic_mapping.py` |
| Experience extraction | `openexp/ingest/experience_extractor.py` |
| Batch labeling | `scripts/batch_label.py` |
| Qdrant storage | `openexp/core/direct_search.py` (`add_experience()`) |
| Chunk data | `~/.openexp/data/chunks/` |
| Thread data | `~/.openexp/data/chunks/threads/` |
