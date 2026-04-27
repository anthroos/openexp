# OpenExp — Development Instructions

## Memory Protocol (MANDATORY)

OpenExp gives Claude Code persistent memory: hybrid retrieval (vector + BM25), prediction-outcome feedback, and the experience-pack pipeline. For the system to learn from your work, follow this protocol **every task**:

### Before starting any task:
```
search_memory("relevant context for this task")
```
Find prior experience, decisions, mistakes. Hooks do auto-recall on each message, but you MUST do a targeted search before complex tasks.

### After completing a task:
```
add_memory("what was decided/done and why", type="decision")
```
Capture outcomes, not just actions. The prediction loop (below) cannot reward retrieval if the outcome was never written down.

### When the user shares context:
```
add_memory("the context", type="fact")
```
Immediately. Don't wait. Every piece of context improves future retrieval.

### Prediction loop (build judgment over time):
When you make a prediction or recommendation (deal outcome, approach success, client reaction):
```
log_prediction("prediction text", confidence=0.7, memory_ids=["ids-that-informed-this"])
```
Later, when the outcome is known:
```
log_outcome(prediction_id="pred_xxx", outcome="what happened", reward=0.8)
```
This is the active feedback path — verified outcomes flow back to the memories that informed them. Use for: deal predictions, strategy recommendations, client behavior forecasts, technical approach bets.

## Architecture

**Full reference:** `docs/storage-system.md` for retrieval/scoring details, `docs/experience-library.md` for the Experience Library pipeline.

- `openexp/core/` — search, scoring, lifecycle, storage
- `openexp/ingest/` — Transcript ingest + Experience Library pipeline (chunking, topic mapping, experience extraction)
- `openexp/mcp_server.py` — MCP STDIO server (5 tools: search_memory, add_memory, log_prediction, log_outcome, memory_stats)
- `openexp/cli.py` — CLI (search, ingest, chunk, topics, stats, compact, experience, viz)
- `scripts/batch_label.py` — Batch experience labeling across all threads
- `tests/` — 300 tests across 13 files

## Q-values — status

Public README states Q-learning was removed on 2026-04-26 because mean Q across 27k memories was 0.006 and 90% of memories never received any reward signal. **What that actually meant:** the session-heuristic reward path (commit +0.3, PR +0.2, etc.) was removed; the storage-level `q_value` field is still present but no longer the primary ranking signal. The active feedback path is the **prediction → outcome loop** (`log_prediction` / `log_outcome`) — verified, not heuristic.

If you find a code path that still relies on heuristic Q-updates from session events, treat it as a deletion candidate, not as canonical behavior.

## Development Workflow

Two remotes: `origin` (private), `public` (open-source).

```bash
# Branch from main
git checkout -b feat/my-feature

# Test
.venv/bin/python3 -m pytest tests/ -v

# Verify no private data
grep -rn "sk-ant\|welababeldata\|ivanpasichnyk" $(git ls-files)

# Push to private first, public when ready
git push origin feat/my-feature    # daily work
git push public main               # releases
```

## Rules

- No hardcoded paths. Everything via env vars.
- No personal data in code (API keys, usernames, company names).
- `.env` is gitignored — never commit it.
- Always branch → PR → squash merge. Never push to main directly.
