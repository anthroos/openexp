<p align="center">
  <h1 align="center">OpenExp</h1>
  <p align="center">
    <strong>Q-learning memory for Claude Code</strong><br>
    Your AI learns from experience.
  </p>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> &middot;
  <a href="#how-it-works">How It Works</a> &middot;
  <a href="#mcp-tools">MCP Tools</a> &middot;
  <a href="#configuration">Configuration</a> &middot;
  <a href="#architecture">Architecture</a>
</p>

---

Every Claude Code session starts from zero. OpenExp changes that.

It gives Claude Code **persistent memory that learns**. Not just storage — actual reinforcement learning. Memories that lead to productive sessions (commits, PRs, passing tests) get higher Q-values and surface first next time. Bad memories sink.

The same idea behind AlphaGo, applied to your coding assistant's context window.

## The Problem

Claude Code forgets everything between sessions. You re-explain your project structure, your preferences, your past decisions — every single time.

Existing memory tools just store and retrieve. They treat a two-month-old note about a deleted feature the same as yesterday's critical architecture decision.

## The Solution

OpenExp adds a **closed-loop learning system**:

```
Session starts → recall memories (ranked by Q-value)
    ↓
Claude works → observations captured automatically
    ↓
Session ends → productive? (commits, PRs, tests)
    ↓
    YES → reward recalled memories (Q-values go up)
    NO  → penalize them (Q-values go down)
    ↓
Next session → better memories surface first
```

After a few sessions, OpenExp learns what context actually helps you get work done.

## Quick Start

```bash
git clone https://github.com/anthroos/openexp.git
cd openexp
./setup.sh
```

That's it. Open Claude Code in any project — it now has memory.

**Prerequisites:** Python 3.11+, Docker, jq

## What You'll See

When you open Claude Code after a few sessions:

```
# OpenExp Memory (Q-value ranked)
Query: my-project | Monday 2026-03-22

## Relevant Context
[sim=0.82 q=0.73] Fixed auth bug by adding token refresh logic in api/auth.py
[sim=0.76 q=0.65] Project uses FastAPI + PostgreSQL, deployed on Railway
[sim=0.71 q=0.58] User prefers pytest with fixtures, not unittest
```

`q=0.73` means this memory consistently leads to productive sessions. `q=0.31` means it's been recalled but didn't help — it'll rank lower next time.

## How It Works

Three hooks integrate with Claude Code automatically:

| Hook | When | What |
|------|------|------|
| **SessionStart** | Session opens | Searches Qdrant for relevant memories, injects top results as context |
| **UserPromptSubmit** | Every message | Lightweight recall — adds relevant memories to each prompt |
| **PostToolUse** | After Write/Edit/Bash | Captures what Claude does as observations (JSONL) |

The MCP server provides 8 tools for explicit memory operations (search, add, predict, reflect).

### The Learning Loop

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│   ┌─────────┐    search     ┌────────┐    inject    ┌─────┐ │
│   │ Qdrant  │──────────────→│ Scorer │────────────→│ LLM │ │
│   │ (384d)  │               │        │              │     │ │
│   └────┬────┘               └────────┘              └──┬──┘ │
│        │                    BM25 10%                    │    │
│        │                    Vector 30%                  │    │
│   Q-values                  Recency 15%            observations
│   updated                   Importance 15%             │    │
│        │                    Q-value 30%                 │    │
│        │                                               │    │
│   ┌────┴────┐   reward    ┌──────────┐   ingest   ┌───┴──┐ │
│   │ Q-Cache │←────────────│ Reward   │←───────────│ JSONL│ │
│   │  (LRU)  │             │ Tracker  │            │ obs  │ │
│   └─────────┘             └──────────┘            └──────┘ │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### Q-Learning Details

Every memory has a Q-value (starts at 0.5). Three layers capture different aspects:

| Layer | Weight | Measures |
|-------|--------|----------|
| **action** | 50% | Did recalling this help get work done? |
| **hypothesis** | 20% | Was the information accurate? |
| **fit** | 30% | Was it relevant to the context? |

Update rule:

```
Q_new = (1 - α) × Q_old + α × reward

α = 0.25 (learning rate)
reward ∈ [-0.5, 0.5] (session productivity signal)
```

Retrieval scoring combines five signals:

```
score = 0.30 × vector_similarity    # semantic match
      + 0.10 × bm25_score           # keyword match
      + 0.15 × recency              # exponential decay (90-day half-life)
      + 0.15 × importance           # type-weighted metadata
      + 0.30 × q_value              # learned quality
```

With 10% epsilon-greedy exploration — occasionally surfaces low-Q memories to give them another chance.

## MCP Tools

| Tool | Description |
|------|-------------|
| `search_memory` | Hybrid search: BM25 + vector + Q-value reranking |
| `add_memory` | Store memory with auto-enrichment (type, tags, validity) |
| `log_prediction` | Track a prediction for later outcome resolution |
| `log_outcome` | Resolve prediction with reward → updates Q-values |
| `get_agent_context` | Full context: memories + pending predictions |
| `reflect` | Review recent memories for patterns |
| `memory_stats` | Q-cache size, prediction accuracy stats |
| `reload_q_cache` | Hot-reload Q-values from disk |

## CLI

```bash
# Search memories
openexp search -q "authentication flow" -n 5

# Ingest observations into Qdrant
openexp ingest

# Preview what would be ingested (dry run)
openexp ingest --dry-run

# Show Q-cache statistics
openexp stats
```

## Configuration

All settings via environment variables (`.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `QDRANT_HOST` | `localhost` | Qdrant server host |
| `QDRANT_PORT` | `6333` | Qdrant server port |
| `OPENEXP_COLLECTION` | `openexp_memories` | Qdrant collection name |
| `OPENEXP_DATA_DIR` | `~/.openexp/data` | Q-cache, predictions, retrieval logs |
| `OPENEXP_OBSERVATIONS_DIR` | `~/.openexp/observations` | Where hooks write observations |
| `OPENEXP_SESSIONS_DIR` | `~/.openexp/sessions` | Session summary files |
| `OPENEXP_EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Embedding model (local, free) |
| `OPENEXP_EMBEDDING_DIM` | `384` | Embedding dimensions |
| `OPENEXP_INGEST_BATCH_SIZE` | `50` | Batch size for ingestion |
| `ANTHROPIC_API_KEY` | *(none)* | Optional: enables LLM-based enrichment |
| `OPENEXP_ENRICHMENT_MODEL` | `claude-haiku-4-5-20251001` | Model for auto-enrichment |

**Anthropic API key is optional.** Without it, memories get default metadata. With it, each memory is automatically classified (type, importance, tags, validity window).

## Architecture

```
openexp/
├── core/                       # Q-learning memory engine
│   ├── q_value.py              # Q-learning: QCache, QValueUpdater, QValueScorer
│   ├── direct_search.py        # FastEmbed (384d) + Qdrant vector search
│   ├── hybrid_search.py        # BM25 keyword + vector + Q-value hybrid scoring
│   ├── scoring.py              # Composite relevance: similarity × recency × importance
│   ├── lifecycle.py            # 8-state memory lifecycle (active→confirmed→archived→...)
│   ├── enrichment.py           # Auto-metadata extraction (LLM or defaults)
│   ├── v7_extensions.py        # Lifecycle filter + hybrid scoring integration
│   └── config.py               # Environment-based configuration
│
├── ingest/                     # Observation → Qdrant pipeline
│   ├── observation.py          # JSONL observations → embeddings → Qdrant
│   ├── session_summary.py      # Session .md files → memory objects
│   ├── reward.py               # Session productivity → reward signal
│   ├── retrieval_log.py        # Closed-loop: which memories were recalled
│   ├── watermark.py            # Idempotent ingestion tracking
│   └── filters.py              # Filter trivial observations
│
├── hooks/                      # Claude Code integration
│   ├── session-start.sh        # Inject Q-ranked memories at startup
│   ├── user-prompt-recall.sh   # Per-message context recall
│   └── post-tool-use.sh        # Capture observations from tool calls
│
├── mcp_server.py               # MCP STDIO server (JSON-RPC 2.0)
├── reward_tracker.py           # Prediction → outcome → Q-value updates
└── cli.py                      # CLI: search, ingest, stats
```

### Memory Lifecycle

Memories move through 8 states to prevent stale context:

```
active ──→ confirmed ──→ outdated ──→ archived ──→ deleted
  │            │                          ↑
  ├──→ contradicted ──────────────────────┘
  ├──→ merged
  └──→ superseded
```

Only `active` and `confirmed` memories are returned in searches. Status weights affect scoring: `confirmed=1.2×`, `active=1.0×`, `outdated=0.5×`, `archived=0.3×`.

### Data Flow

```
PostToolUse hook                                  SessionStart hook
      │                                                 ↑
      ↓                                                 │
~/.openexp/observations/*.jsonl                Qdrant search (top 10)
      │                                          + Q-value reranking
      ↓                                                 ↑
openexp ingest ──→ FastEmbed ──→ Qdrant ─────────────────┘
      │                            ↑
      ↓                            │
Q-Cache (q_cache.json) ←── reward signal ←── session productivity
```

## Technical Details

| Component | Choice | Why |
|-----------|--------|-----|
| **Embeddings** | FastEmbed (BAAI/bge-small-en-v1.5) | Local, free, no API key, 384 dimensions |
| **Vector DB** | Qdrant | Fast ANN search, payload filtering, Docker-ready |
| **Q-Cache** | In-memory LRU (100K entries) | Fast lookup, delta-based persistence for concurrent sessions |
| **Transport** | MCP STDIO (JSON-RPC 2.0) | Native Claude Code integration |
| **Hooks** | Bash scripts | Minimal dependencies, shell-level integration |

## Contributing

This project is in early stages. Key areas where help is welcome:

- **Reward signals** — beyond commits/PRs, what indicates a productive session?
- **Compaction** — merging duplicate or outdated memories automatically
- **Multi-project learning** — sharing relevant context across projects
- **Benchmarks** — measuring retrieval quality improvement over time
- **More lifecycle transitions** — automated contradiction detection

## Research

OpenExp implements value-driven memory retrieval inspired by [MemRL](https://arxiv.org/abs/2404.09560), adapted for episodic memory in AI coding assistants.

Core insight: treating memory retrieval as a reinforcement learning problem — where the reward signal comes from real session outcomes — produces better context selection than similarity-only search.

## License

[MIT](LICENSE) &copy; Ivan Pasichnyk
