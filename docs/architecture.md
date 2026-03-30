# Architecture

> **Full storage system docs:** See [storage-system.md](storage-system.md) for the complete
> 5-level pyramid (L0–L4), all 4 reward paths, Q-learning formulas, MCP tools, and file map.

## System Overview

```
┌──────────────────────────────────────────────────────────────┐
│                        Claude Code                            │
│                                                              │
│  ┌──────────┐  ┌───────────┐  ┌────────────┐  ┌──────────┐  │
│  │ Session  │  │ User      │  │ Post Tool  │  │ Session  │  │
│  │ Start    │  │ Prompt    │  │ Use        │  │ End      │  │
│  └────┬─────┘  └─────┬─────┘  └──────┬─────┘  └────┬─────┘  │
│       │              │               │              │        │
└───────┼──────────────┼───────────────┼──────────────┼────────┘
        │              │               │              │
        ▼              ▼               ▼              ▼
┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐
│ session-   │  │ user-      │  │ post-tool- │  │ session-   │
│ start.sh   │  │ prompt-    │  │ use.sh     │  │ end.sh     │
│            │  │ recall.sh  │  │            │  │            │
│ Search →   │  │ Search →   │  │ → Write    │  │ Summary →  │
│ Inject ctx │  │ Inject ctx │  │ observation│  │ Ingest →   │
└──────┬─────┘  └──────┬─────┘  └──────┬─────┘  │ Reward     │
       │               │               │        └──────┬─────┘
       ▼               ▼               ▼               ▼
┌──────────────────────────────┐    ┌────────────────────┐
│        OpenExp Core          │    │  Observations Dir  │
│                              │    │  ~/.openexp/       │
│  ┌──────────────────────┐   │    │  observations/     │
│  │   direct_search.py   │   │    └─────────┬──────────┘
│  │   FastEmbed + Qdrant │   │              │
│  └──────────┬───────────┘   │              │
│             │               │    ┌─────────▼──────────┐
│  ┌──────────▼───────────┐   │    │   Ingest Pipeline  │
│  │   hybrid_search.py   │   │    │                    │
│  │   BM25 + Vector      │   │    │  observation.py    │
│  └──────────┬───────────┘   │    │  session_summary.py│
│             │               │    │  reward.py         │
│  ┌──────────▼───────────┐   │    │  filters.py        │
│  │    q_value.py        │   │    └─────────┬──────────┘
│  │    Q-learning cache  │   │              │
│  └──────────────────────┘   │              │
│                              │              │
└──────────────┬───────────────┘              │
               │                              │
               ▼                              ▼
        ┌──────────────────────────────────────────┐
        │              Qdrant (Docker)              │
        │         Vector Database (port 6333)       │
        │                                          │
        │  Collection: openexp_memories             │
        │  Vectors: 384-dim (BAAI/bge-small-en-v1.5)│
        └──────────────────────────────────────────┘
```

## Key Components

### Core Engine (`openexp/core/`)

- **config.py** — All settings from environment variables
- **q_value.py** — Q-learning cache with LRU eviction, delta persistence, z-score normalization
- **direct_search.py** — FastEmbed embedding + Qdrant vector search
- **hybrid_search.py** — Pure Python BM25 implementation + hybrid scoring
- **scoring.py** — Composite relevance: semantic + recency + importance + type boost
- **lifecycle.py** — 8-state memory lifecycle with transition validation
- **enrichment.py** — Optional LLM-based metadata extraction
- **v7_extensions.py** — Lifecycle filtering + hybrid scoring helpers

### Ingest Pipeline (`openexp/ingest/`)

Converts raw observations (JSONL) into embedded vectors in Qdrant:

1. **filters.py** — Drops ~60-70% of trivial observations (read-only commands, short summaries)
2. **observation.py** — Batch embeds observations via FastEmbed, upserts to Qdrant
3. **session_summary.py** — Parses session markdown files, creates higher-importance memories
4. **reward.py** — Computes session productivity score, applies Q-value updates (all 3 layers)
5. **retrieval_log.py** — Tracks which memories were recalled (for closed-loop reward)
6. **watermark.py** — Idempotency: prevents duplicate ingestion

### Outcome Resolution (`openexp/outcome.py` + `openexp/resolvers/`)

Connects real-world business events to Q-value updates:

1. **outcome.py** — `OutcomeEvent` dataclass, `OutcomeResolver` ABC, `resolve_outcomes()` orchestrator
2. **resolvers/crm_csv.py** — `CRMCSVResolver`: diffs CRM CSVs, detects stage transitions, emits reward events
3. Pipeline: resolver detects events → find tagged memories by `client_id` → apply targeted rewards

### MCP Server (`openexp/mcp_server.py`)

STDIO-based MCP server exposing 9 tools (including `resolve_outcomes`). Runs as a long-lived process per Claude Code session. Initializes Q-cache on startup, saves delta on shutdown.

### Hooks (`openexp/hooks/`)

Shell scripts registered with Claude Code:

- **session-start.sh** — Builds contextual query, searches Qdrant, formats results, logs retrieval
- **user-prompt-recall.sh** — Per-message recall (skips trivial inputs), logs retrieval
- **post-tool-use.sh** — Captures Write/Edit/Bash observations, skips Read/Glob/Grep
- **session-end.sh** — Generates session summary, triggers async ingest + reward computation

## Data Persistence

| What | Where | Format |
|------|-------|--------|
| Vector embeddings | Qdrant (Docker volume) | 384-dim vectors + JSON payload |
| Q-value cache | `~/.openexp/data/q_cache.json` | `{memory_id: {q_value, q_action, ...}}` |
| Q-value deltas | `~/.openexp/data/deltas/` | Per-session delta files (merged on start) |
| Predictions | `~/.openexp/data/predictions.jsonl` | Agent predictions for outcome tracking |
| CRM snapshot | `~/.openexp/data/crm_snapshot.json` | Last-seen CRM state (for diffing) |
| Retrieval log | `~/.openexp/data/session_retrievals.jsonl` | Which memories were recalled when |
| Raw observations | `~/.openexp/observations/` | JSONL files per day |
| Session summaries | `~/.openexp/sessions/` | Markdown files per session |
| Ingest watermark | `~/.openexp/data/ingest_watermark.json` | Processed observation IDs |
