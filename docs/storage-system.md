# OpenExp Storage System — Complete Reference

> **Purpose:** This document describes the full storage architecture so that Claude
> doesn't have to re-read every source file each session. Read THIS instead of the code.
>
> **Last updated:** 2026-04-08 (added Path 5 retrospective, reward audit)

---

## 1. The 5-Level Storage Pyramid

Every memory gets a Q-value that rises when useful and falls when not.
A number alone doesn't explain itself — each level adds understanding.

| Level | What | Where | Size | Purpose |
|-------|------|-------|------|---------|
| **L0** | Raw observations | `~/.openexp/observations/*.jsonl` | ~50 KB/session | Everything that happened: tool calls, edits, commands |
| **L1** | Q-value scalar | `q_cache.json` → `q_value` field | 1 float | How useful is this memory? (−0.5 … 1.0) |
| **L2** | Reward contexts | `q_cache.json` → `reward_contexts[]` | Max 5 strings, 120 chars | Brief: `"Session +0.30: 2 commits, 1 PR [rwd_abc]"` |
| **L3** | Cold storage | `reward_log.jsonl` | Full JSON per event | Complete reward record: observations, breakdowns, predictions |
| **L4** | LLM explanation | `explanation` field in L3 record | Max 500 chars | Opus 4.6 writes WHY: "This note helped because…" |

### Data Flow

```
Session observations (L0)
    → compute_session_reward() → reward signal
        → read q_before from QCache
        → QValueUpdater.update_all_layers() → new Q-value (L1) + context (L2)
        → read q_after from QCache
        → generate_reward_explanation(q_before, q_after) → explanation (L4)
        → log_reward_event() → cold record (L3) with explanation
```

### Linking Across Levels

```
L2 context string:  "Session +0.30: 2 commits [rwd_abc12345]"
                                                ↑
L3 reward_log.jsonl: {"reward_id": "rwd_abc12345", ..., "explanation": "..."}
                                                                         ↑
L4 explanation:      "Ця нотатка допомогла бо містила архітектурне рішення..."
```

---

## 2. Five Reward Paths

Each path: reads q_before → updates Q-values → reads q_after → generates L4 explanation → logs L3 record.

| # | Path | Trigger | File | `reward_type` |
|---|------|---------|------|---------------|
| 1 | **Session** | Session end (hook) | `openexp/ingest/reward.py` → `apply_session_reward()` | `"session"` |
| 2 | **Prediction** | `log_outcome` MCP call | `openexp/reward_tracker.py` → `RewardTracker.log_outcome()` | `"prediction"` |
| 3 | **Business** | `resolve_outcomes` MCP call | `openexp/outcome.py` → `resolve_outcomes()` | `"business"` |
| 4 | **Calibration** | `calibrate_experience_q` MCP call | `openexp/mcp_server.py` | `"calibration"` |
| 5 | **Retrospective** | launchd daily/weekly/monthly | `openexp/retrospective.py` | `"daily_retrospective"` |

### Path 1: Session Reward (`ingest/reward.py`)

**Trigger:** `session-end.sh` hook → `ingest` CLI → `apply_session_reward()`

**Logic:**
1. `compute_session_reward(observations)` → heuristic score [−0.5, +0.5]
   - Positive signals: commits (+0.3), PRs (+0.2), writes (+0.02 each), deploys (+0.1), tests (+0.1), decisions (+0.1)
   - Negative: base (−0.1), few observations (−0.05), no output (−0.1)
   - Experience-specific weights override defaults
2. `_build_session_reward_context(obs, reward)` → L2 string: `"Session +0.30: 2 commits, 1 PR"`
3. Read `q_before` from first memory's Q-cache entry
4. `QValueUpdater.update_all_layers()` for each memory
5. Read `q_after` from first memory's Q-cache entry
6. `generate_reward_explanation(reward_type="session", q_before, q_after)` → L4
7. `log_reward_event()` → L3

**Also:** `reward_retrieved_memories()` — rewards memories recalled at session start (closed-loop). Delegates to `apply_session_reward()`.

### Path 2: Prediction Reward (`reward_tracker.py`)

**Trigger:** User calls `log_outcome` MCP tool with prediction_id + outcome + reward.

**Logic:**
1. Find pending prediction by ID
2. Build reward context: `"Pred +0.80: 'prediction snippet' -> 'outcome snippet'"`
3. Read `q_before` from first memory via `self.q_cache.get()`
4. Update Q-values for all `memory_ids_used`
5. Read `q_after`
6. Generate L4 explanation with `reward_type="prediction"`
7. Log L3 record

**Data stored:** prediction text, outcome, confidence, strategic_value, cause_category.

### Path 3: Business Reward (`outcome.py`)

**Trigger:** User calls `resolve_outcomes` MCP tool → runs all registered `OutcomeResolver` subclasses.

**Logic:**
1. Each resolver scans external data (e.g., CRM CSV diffs) → emits `OutcomeEvent`s
2. For each event: auto-resolve matching pending predictions
3. Find memories tagged with `entity_id` via Qdrant scroll
4. Read `q_before` from first memory via `q_updater.cache.get()`
5. Apply reward to all tagged memories
6. Read `q_after`
7. Generate L4 explanation with `reward_type="business"`
8. Log L3 record

**Resolver:** `CRMCSVResolver` diffs `deals.csv` / `leads.csv` against snapshot, detects stage transitions.

### Path 4: Calibration (`mcp_server.py`)

**Trigger:** User calls `calibrate_experience_q` MCP tool with memory_id + new q_value.

**Logic:**
1. Read `old_q` from cache
2. Set all Q-layers to `new_q` directly (no formula)
3. Generate L4 explanation with `reward_type="calibration"`, `q_before=old_q, q_after=new_q`
4. Log L3 record
5. Append L2 context: `"Cal 0.80: <reason>"`

### Path 5: Retrospective (`retrospective.py`)

**Trigger:** launchd daily at 23:30, weekly (Sundays), monthly (1st). Also: `openexp retrospective daily [YYYY-MM-DD]` CLI.

**Logic:**
1. `gather_daily_data()` — collects session summaries, reward events, and memories from Qdrant (source=decision_extraction) for the target date
2. `analyze_with_llm()` — calls `claude -p --model opus` (Max subscription) with prompt asking for cross-session attribution, over/under-rewarded memories, patterns
3. LLM returns JSON: `{adjustments[], insights[], summary, patterns[]}`
4. `apply_adjustments()` — validates memory_id exists in Q-cache (NOT Qdrant), then applies:
   - `promote`: positive reward via QValueUpdater
   - `demote`: negative reward via QValueUpdater
   - `override`: direct Q-value assignment (like calibration)
5. Max 20 adjustments per run (`MAX_ADJUSTMENTS`)
6. Saves full Q-cache after adjustments
7. Stores retrospective summary as a Qdrant memory
8. Idempotency via `watermark.json` (tracks last processed date per cadence)

**Data stored:** L3 records with `reward_type="daily_retrospective"`, retrospective memory in Qdrant.

**Known issues:** See `docs/reward-audit-2026-04-08.md` for orphan bug (test fixtures in Q-cache) and race condition with calibration path.

---

## 3. Q-Learning Engine (`core/q_value.py`)

### Formula

```
Q_new = clamp(Q_old + alpha * reward, q_floor, q_ceiling)
```

- `alpha = 0.25` (learning rate)
- `q_init = 0.0` (new memories start at zero)
- `q_floor = -0.5`, `q_ceiling = 1.0`

### Three Layers

| Layer | Weight | Reward | What it measures |
|-------|--------|--------|------------------|
| `q_action` | 50% | full reward | Was retrieving this memory useful? |
| `q_hypothesis` | 20% | reward × 0.8 | Is the hypothesis/insight valid? |
| `q_fit` | 30% | full if positive, ×0.5 if negative | Does this memory fit the experience? |

Combined: `Q = 0.5 * q_action + 0.2 * q_hypothesis + 0.3 * q_fit`

### QCache

- `OrderedDict` with LRU eviction (max 100K entries)
- **Nested format:** `{memory_id: {experience_name: {q_value, q_action, q_hypothesis, q_fit, q_visits, reward_contexts[], q_updated_at, last_reward, ...}}}`
- Auto-migrates from flat format on load
- **Delta persistence:** each session writes only changed entries to `~/.openexp/data/deltas/delta_<session_id>.json`. On startup, merges all deltas (newest wins) into main cache.
- `save()` writes full cache; `save_delta()` writes only dirty entries.

### Reward Contexts (L2)

- Max 5 per memory (FIFO eviction)
- Max 120 chars each
- Format: `"Session +0.30: 2 commits [rwd_abc12345]"` — the `[rwd_xxx]` suffix links to L3
- Stored inside `q_data.reward_contexts[]`

---

## 4. L4 Explanation Engine (`core/explanation.py`)

### `generate_reward_explanation()`

- **Model:** `claude-opus-4-6` (configurable via `OPENEXP_EXPLANATION_MODEL`)
- **Enabled:** `OPENEXP_EXPLANATION_ENABLED=true` (default)
- **max_tokens:** 200
- **Safety cap:** 500 chars
- **Graceful:** returns `None` on any error (disabled, no API key, API failure)
- **Lazy client:** singleton `_anthropic_client` (same pattern as enrichment.py)

### Prompt Types

| `reward_type` | Prompt focus | When used |
|---------------|-------------|-----------|
| `session` | Session observations + breakdown + memories used | Session end |
| `prediction` | Prediction text + outcome + confidence | log_outcome |
| `business` | Entity ID + event name + details | resolve_outcomes |
| `calibration` | Old Q → New Q + reason | calibrate_experience_q |
| `summary` | Aggregated events for a memory | explain_q regenerate=true |

### Q-line in Prompts

When both `q_before` and `q_after` are provided, the prompt includes:
```
Q-value: 0.30 → 0.58
```
When either is None, this line is omitted (graceful degradation).

### `fetch_memory_contents()`

Retrieves up to `limit` (default 5) memory texts from Qdrant by ID. Returns `{memory_id: content_text[:300]}`. Graceful on failure (returns `{}`).

---

## 5. Cold Storage (`core/reward_log.py`)

### File

`~/.openexp/data/reward_log.jsonl` — append-only JSONL, rotated at 100 MB.

### Record Format

```json
{
  "reward_id": "rwd_abc12345",
  "timestamp": "2026-03-26T12:00:00+00:00",
  "reward_type": "session",
  "reward": 0.30,
  "memory_ids": ["mem-1", "mem-2"],
  "experience": "default",
  "context": {
    "observations": [...],
    "observation_count": 15,
    "reward_breakdown": {"commits": 2, "prs": 1, "writes": 5},
    "session_id": "abc123"
  },
  "explanation": "Ця нотатка допомогла бо містила архітектурне рішення..."
}
```

### Access Functions

| Function | What | Used by |
|----------|------|---------|
| `generate_reward_id()` | `"rwd_<8hex>"` | All 5 paths |
| `log_reward_event()` | Append record | All 5 paths |
| `get_reward_detail(reward_id)` | Lookup by ID | `reward_detail` MCP tool |
| `get_reward_history(memory_id)` | All events for a memory | `memory_reward_history`, `explain_q` MCP tools |
| `compact_observation(obs)` | Strip to id/tool/summary/type/path/tags | Session path (L3 context) |

---

## 6. MCP Tools (16 total)

### Memory CRUD
| Tool | What |
|------|------|
| `search_memory` | FastEmbed + Qdrant + BM25 + Q-value reranking |
| `add_memory` | Store new memory with embedding |

### Prediction Loop
| Tool | What |
|------|------|
| `log_prediction` | Log prediction → returns `pred_id` |
| `log_outcome` | Resolve prediction → reward Q-values |

### Context & Reflection
| Tool | What |
|------|------|
| `get_agent_context` | memories + Q-scores + pending predictions |
| `reflect` | Pattern finding on recent memories |
| `memory_stats` | System statistics |

### Outcome & Cache
| Tool | What |
|------|------|
| `resolve_outcomes` | Run CRM resolvers → business rewards |
| `reload_q_cache` | Reload from disk |

### Experience Introspection
| Tool | What |
|------|------|
| `experience_info` | Current experience config |
| `experience_top_memories` | Top/bottom N by Q-value |
| `experience_insights` | Reward distribution, learning velocity |

### Q-Value Inspection
| Tool | What |
|------|------|
| `calibrate_experience_q` | Manually set Q-value + L4 explanation |
| `memory_reward_history` | Q + L2 contexts + L3 records |
| `reward_detail` | Full L3 record by reward_id |
| `explain_q` | Aggregated L4 explanations + optional LLM regeneration |

---

## 7. Experience System (`core/experience.py`)

Same memory can have different Q-values per experience (e.g., "default", "sales", "coding").

- Configs in `~/.openexp/experiences/<name>.yaml` or bundled defaults
- Each experience defines: reward weights, resolver configs, type boosts
- Active experience set via `OPENEXP_EXPERIENCE` env var (default: `"default"`)
- Q-cache stores: `{memory_id: {experience_name: {q_data...}, ...}}`

---

## 8. Search & Scoring

### Search Pipeline (`core/direct_search.py` + `hybrid_search.py`)

1. **FastEmbed** (BAAI/bge-small-en-v1.5, 384-dim, local) embeds query
2. **Qdrant** vector search with lifecycle + metadata filters
3. **BM25** pure-Python scoring on payload texts
4. **Hybrid merge:** vector 30% + BM25 10% + recency 15% + importance 15% + Q-value 30%

### Scoring Weights (`core/scoring.py`)

| Component | Weight | Source |
|-----------|--------|--------|
| Semantic similarity | 30% | FastEmbed cosine via Qdrant |
| Q-value | 30% | Q-cache |
| Recency | 15% | `created_at` exponential decay |
| Importance | 15% | Memory type + tags |
| BM25 keyword | 10% | Hybrid search |

---

## 9. Ingest Pipeline

### Flow

```
~/.openexp/observations/*.jsonl   (written by post-tool-use hook)
                ↓
        filters.py  (drops ~60-70% trivial obs)
                ↓
        observation.py  (batch embed via FastEmbed → upsert to Qdrant, experience-aware Q init)
                ↓
~/.openexp/sessions/*.md   (written by session-end hook)
                ↓
        session_summary.py  (parse markdown → higher-importance memories)
                ↓
        reward.py  (compute session reward → update Q-values)
                ↓
        watermark.py  (mark processed obs IDs for idempotency)
                ↓
~/.claude/projects/*/*.jsonl   (Claude Code transcripts)
                ↓
        extract_decisions.py  (Opus 4.6 via claude -p → decisions/insights → Qdrant)
```

### Decision Extraction (`ingest/extract_decisions.py`)

Runs as Phase 2c of SessionEnd (after ingest + reward). Uses Opus 4.6 to extract strategic decisions, insights, and commitments from the conversation transcript. See [Decision Extraction](decision-extraction.md) for details.

### Filters (`ingest/filters.py`)

Drops: read-only commands (cat, grep, ls), short summaries (<15 chars), Read/Glob/Grep tool calls.
Keeps: Write, Edit, Bash with side effects, decisions, valuable tags.

---

## 10. Hooks (Claude Code Integration)

| Hook | File | When | What |
|------|------|------|------|
| **SessionStart** | `session-start.sh` | Session begins | Search Qdrant → inject top-5 memories → log retrieval IDs |
| **UserPromptSubmit** | `user-prompt-recall.sh` | Each message | Context recall (skip trivial) → inject |
| **PostToolUse** | `post-tool-use.sh` | After Write/Edit/Bash | Write observation to JSONL (skip reads) |
| **SessionEnd** | `session-end.sh` | Session ends | Generate summary → async ingest → reward → decision extraction |

---

## 11. File Map

### Config

| File | Purpose |
|------|---------|
| `core/config.py` | All env-var-based settings (paths, models, keys, ports) |

### Core Engine

| File | Purpose |
|------|---------|
| `core/q_value.py` | QCache (LRU + delta), QValueUpdater (3-layer), QScorer, reward contexts |
| `core/direct_search.py` | FastEmbed embedding + Qdrant vector search |
| `core/hybrid_search.py` | Pure Python BM25 implementation |
| `core/scoring.py` | Composite scoring (semantic + recency + importance + Q) |
| `core/lifecycle.py` | 8-state memory lifecycle with transition validation |
| `core/enrichment.py` | LLM metadata extraction (Haiku) |
| `core/explanation.py` | L4 LLM reward explanations (Opus) |
| `core/reward_log.py` | L3 cold storage JSONL |
| `core/experience.py` | Per-experience Q-values + YAML configs |
| `core/compaction.py` | Cluster similar memories, merge, deduplicate |
| `core/v7_extensions.py` | Lifecycle filtering + hybrid scoring helpers |

### Ingest

| File | Purpose |
|------|---------|
| `ingest/filters.py` | Drop trivial observations |
| `ingest/observation.py` | Batch embed → Qdrant upsert (passes `experience` to Q-cache init) |
| `ingest/session_summary.py` | Parse session markdown → memories |
| `ingest/reward.py` | Session reward computation + Q-update + L3/L4 |
| `ingest/retrieval_log.py` | Track recalled memory IDs |
| `ingest/watermark.py` | Idempotent ingestion tracking |
| `ingest/extract_decisions.py` | Opus 4.6 decision extraction from transcripts |

### Reward Paths

| File | Purpose |
|------|---------|
| `ingest/reward.py` | Path 1: Session reward |
| `reward_tracker.py` | Path 2: Prediction → outcome |
| `outcome.py` | Path 3: Business events (+ OutcomeResolver ABC) |
| `mcp_server.py` | Path 4: Calibration (+ all 16 MCP tools) |
| `retrospective.py` | Path 5: LLM retrospective (daily/weekly/monthly) |
| `resolvers/crm_csv.py` | CRM CSV diff resolver |

### Other

| File | Purpose |
|------|---------|
| `mcp_server.py` | STDIO MCP server (init, tools, request handler) |
| `cli.py` | CLI: search, ingest, stats, viz |
| `viz.py` | Export data for visualization dashboard |

---

## 12. Data Files

| File | Path | Format |
|------|------|--------|
| Q-cache | `~/.openexp/data/q_cache.json` | Nested JSON: `{mem_id: {exp: {q_data}}}` |
| Q-cache deltas | `~/.openexp/data/deltas/delta_<session>.json` | Same format, dirty entries only |
| Reward log (L3) | `~/.openexp/data/reward_log.jsonl` | JSONL, rotated at 100 MB |
| Predictions | `~/.openexp/data/predictions.jsonl` | JSONL: pending/resolved predictions |
| Outcomes | `~/.openexp/data/outcomes.jsonl` | JSONL: prediction outcomes |
| Retrieval log | `~/.openexp/data/session_retrievals.jsonl` | Which memories recalled when |
| CRM snapshot | `~/.openexp/data/crm_snapshot.json` | Last CRM state for diffing |
| Ingest watermark | `~/.openexp/data/ingest_watermark.json` | Processed observation IDs |
| Observations (L0) | `~/.openexp/observations/obs-YYYYMMDD-*.jsonl` | Raw tool-use observations |
| Session summaries | `~/.openexp/sessions/*.md` | Markdown session summaries |

---

## 13. Environment Variables

| Variable | Default | What |
|----------|---------|------|
| `OPENEXP_DATA_DIR` | `~/.openexp/data` | Main data directory |
| `OPENEXP_OBSERVATIONS_DIR` | `~/.openexp/observations` | Raw observations |
| `OPENEXP_SESSIONS_DIR` | `~/.openexp/sessions` | Session summaries |
| `OPENEXP_COLLECTION` | `openexp_memories` | Qdrant collection name |
| `OPENEXP_EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | FastEmbed model |
| `OPENEXP_EMBEDDING_DIM` | `384` | Embedding dimensions |
| `OPENEXP_ENRICHMENT_MODEL` | `claude-haiku-4-5-20251001` | Enrichment LLM |
| `OPENEXP_EXPLANATION_MODEL` | `claude-opus-4-6` | L4 explanation LLM |
| `OPENEXP_EXPLANATION_ENABLED` | `true` | Enable/disable L4 |
| `OPENEXP_EXPERIENCE` | `default` | Active experience name |
| `OPENEXP_EXPERIENCES_DIR` | `~/.openexp/experiences` | Experience YAML configs |
| `OPENEXP_OUTCOME_RESOLVERS` | `""` | Resolver classes (module:Class) |
| `OPENEXP_CRM_DIR` | `""` | CRM directory for CSV resolver |
| `OPENEXP_INGEST_BATCH_SIZE` | `50` | Batch size for embedding |
| `QDRANT_HOST` | `localhost` | Qdrant host |
| `QDRANT_PORT` | `6333` | Qdrant port |
| `QDRANT_API_KEY` | `""` | Qdrant auth (optional) |
| `ANTHROPIC_API_KEY` | `""` | For enrichment + explanations |
| `OPENEXP_EXTRACT_MODEL` | `claude-opus-4-6` | Decision extraction model |
| `OPENEXP_EXTRACT_MAX_TOKENS` | `2048` | Max tokens for extraction |
| `OPENEXP_EXTRACT_CONTEXT_LIMIT` | `30000` | Max transcript chars sent to LLM |

---

## 14. Test Coverage

250 tests across 11 test files. Key test files for the storage system:

| File | Tests | What |
|------|-------|------|
| `test_explanation.py` | 21 | L4 prompts, generation, fetch, L3 field, explain_q, integration |
| `test_q_value.py` | 17 | QCache CRUD, LRU, delta, updater, scorer, reward contexts |
| `test_reward_log.py` | 11 | Reward ID, log/get, history, compact |
| `test_reward_context.py` | 11 | L2 context builders for all 3 paths |
| `test_outcome.py` | 15 | OutcomeEvent, matching, CRM resolver, resolve_outcomes |
| `test_session_end.py` | 7 | Session reward, retrieval log, closed-loop |
| `test_experience.py` | 16 | Experience loading, per-experience Q, migration |
