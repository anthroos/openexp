# How OpenExp Works

## The Problem

Claude Code is stateless. Every session starts from zero. It doesn't remember:
- What you worked on yesterday
- Which approaches worked and which failed
- Your project's architecture and conventions
- Decisions you made and why

## The Solution: Q-Learning Memory

OpenExp adds persistent, learning memory to Claude Code through three mechanisms:

### 1. Observation Capture (PostToolUse Hook)

Every time Claude Code uses a tool (writes a file, runs a command, edits code), the PostToolUse hook captures an observation:

```json
{
  "id": "obs-20260322-a1b2c3d4",
  "tool": "Edit",
  "summary": "Edited file: auth.py",
  "project": "my-app",
  "timestamp": "2026-03-22T10:30:00Z"
}
```

These observations are written to `~/.openexp/observations/` as JSONL files.

### 2. Memory Retrieval (SessionStart Hook)

When you start a new Claude Code session, the SessionStart hook:

1. Builds a contextual query from your project name + recent work
2. Searches Qdrant for relevant memories
3. Ranks results using hybrid scoring:
   - **30%** Semantic similarity (vector search)
   - **10%** Keyword match (BM25)
   - **15%** Recency (exponential decay, 90-day half-life)
   - **15%** Importance (auto-categorized by type)
   - **30%** Q-value (learned usefulness)
4. Injects top results as `additionalContext` before Claude sees your prompt

### 3. Q-Learning Reward Loop

This is the core innovation. After each session:

1. **Compute reward**: Did the session produce commits? PRs? Tests? → positive reward. Nothing useful? → negative reward.
2. **Update Q-values**: Memories that were recalled at session start get their Q-values updated based on the session's outcome.
3. **Better retrieval**: Next session, memories with higher Q-values float to the top.

The Q-update formula:
```
Q_new = (1 - 0.25) × Q_old + 0.25 × reward
```

Over time, this creates a natural ranking where useful memories (project conventions, working solutions, important decisions) rise to the top, while noise (trivial commands, one-off fixes) sinks.

## Reward Signals

| Signal | Reward | Why |
|--------|--------|-----|
| `git commit` | +0.3 | Code was shipped |
| `gh pr create` | +0.2 | Work was packaged for review |
| File writes | +0.02 each (max +0.2) | Building something |
| Tests passed | +0.1 | Quality verified |
| Deploy | +0.1 | Shipped to production |
| Decision made | +0.1 | Strategic progress |
| No writes + no commits | -0.1 | Unproductive session |
| Abandoned (< 3 obs) | -0.05 | Session didn't accomplish anything |
| Base | -0.1 | Must earn positive |

## Three Q-Layers

Each memory has three Q-value layers, capturing different aspects:

- **action** (50% weight): Did recalling this memory help get work done?
- **hypothesis** (20% weight): Was the information in this memory accurate?
- **fit** (30% weight): Was this memory relevant to the context it was recalled in?

Combined Q = 0.5 × Q_action + 0.2 × Q_hypothesis + 0.3 × Q_fit

## Memory Lifecycle

Memories go through 8 states:

```
active → confirmed → outdated → archived
  ↓         ↓           ↓          ↓
  └── contradicted ──── merged ── deleted
       superseded
```

- **active**: Default state for new memories
- **confirmed**: Accessed multiple times, still valid
- **outdated**: Older than 30 days, may need validation
- **deleted**: Filtered out of search results
