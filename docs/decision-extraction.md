# Decision Extraction

> Extract strategic decisions, insights, and commitments from session transcripts.
> The system records "chose to lead with social proof because enterprise clients trust references" — not "edited proposal.html".

## Why This Matters

Without decision extraction, OpenExp records **actions** (tool calls, file edits, commands). Actions are useful for reward computation but have low strategic value — "Edited file.html" tells you nothing about **why** that edit was made or **what alternative was considered**.

Decision extraction uses Opus 4.6 to read the full conversation transcript and extract:

1. **Decisions** — choice points with reasoning. What was chosen, why, and what was the alternative?
2. **Insights** — things learned about clients, markets, patterns. Why does it matter for future work?
3. **Commitments** — promises or agreements. Who committed to what, by when?

These extracted items become first-class memories in Qdrant, searchable and Q-value-ranked like any other memory.

## How It Works

Decision extraction runs automatically as **Phase 2c** of the SessionEnd hook (async, after ingest + reward):

```
Session ends
    ↓
Phase 2a: Ingest observations + session reward
Phase 2b: Fallback reward for pre-ingested obs
Phase 2c: Decision extraction from transcript (NEW)
    ↓
Find transcript JSONL for this session
    ↓
Read and condense transcript (skip tool results, system noise)
    ↓
Send to Opus 4.6 via claude -p (Max subscription)
    ↓
Parse JSON response → store each item in Qdrant with embedding
```

### Transcript Processing

The transcript reader (`read_transcript()`) processes Claude Code JSONL transcripts:

- Reads only `user` and `assistant` message types
- Extracts text blocks, skips `tool_result` and `system-reminder` content
- Prioritizes recent messages (builds from end, respects context limit)
- Default context limit: 30,000 chars (configurable via `OPENEXP_EXTRACT_CONTEXT_LIMIT`)

### LLM Extraction

Uses `claude -p --model opus` (pipe mode) to leverage Claude Max subscription — zero API cost.

The extraction prompt instructs Opus 4.6 to:
- Think strategically: "helicopter view + details"
- Be selective: 3-8 items per session
- Focus on what would be valuable in a FUTURE conversation
- Skip file edits, tool calls, code changes (already captured as observations)

### Storage

Each extracted item is stored in Qdrant with:

```json
{
  "memory": "Chose to remove advertising from scope because we're not a marketing agency — client needs automation, not ads",
  "type": "decision",
  "source": "decision_extraction",
  "importance": 0.8,
  "tags": ["client-name", "scoping"],
  "session_id": "abc-123",
  "experience": "sales",
  "status": "active"
}
```

Memory types are mapped: `decision` → `decision`, `insight` → `insight`, `commitment` → `action`.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENEXP_EXTRACT_MODEL` | `claude-opus-4-6` | LLM model for extraction (do not downgrade) |
| `OPENEXP_EXTRACT_MAX_TOKENS` | `2048` | Max response tokens |
| `OPENEXP_EXTRACT_CONTEXT_LIMIT` | `30000` | Max chars of transcript sent to LLM |

### Model Quality

Opus 4.6 is mandatory for extraction. The quality of extracted decisions determines the quality of the entire memory system. This is the annotation layer — not a place to save money.

### Recursion Guard

Decision extraction runs inside the SessionEnd hook and spawns `claude -p` as a subprocess. To prevent the subprocess from triggering its own SessionEnd → extraction → subprocess loop:

1. The `extract_decisions()` function sets `OPENEXP_EXTRACT_RUNNING=1` in the subprocess environment
2. `session-end.sh` checks this variable at startup and exits immediately if set

## API

### `read_transcript(transcript_path, session_id=None) -> str`

Read and condense a Claude Code JSONL transcript. Returns formatted text with `IVAN:` and `ASSISTANT:` prefixes.

### `extract_decisions(transcript_text, session_id="", experience="default") -> List[Dict]`

Extract decisions from transcript text using Opus 4.6. Returns list of items:

```python
[
    {
        "type": "decision",
        "content": "One clear sentence describing what happened and WHY",
        "importance": 0.8,
        "tags": ["domain", "client"],
        "client_id": "comp-xxx"  # or null
    }
]
```

### `extract_and_store(transcript_path, session_id, experience="default", dry_run=False) -> Dict`

Full pipeline: read transcript → extract → store in Qdrant.

```python
# Dry run (extract without storing)
result = extract_and_store(path, session_id, dry_run=True)
# {"extracted": 6, "items": [...], "dry_run": True}

# Real run
result = extract_and_store(path, session_id, experience="sales")
# {"extracted": 6, "stored": 6, "experience": "sales", "model": "claude-opus-4-6"}
```

## Example Output

From a real session about a client proposal:

```json
[
  {
    "type": "decision",
    "content": "Removed advertising from Modecks scope because we're not a marketing agency — client needs CRM+email+follow-up automation, not Google Ads management",
    "importance": 0.9,
    "tags": ["modecks", "scoping", "pricing"]
  },
  {
    "type": "insight",
    "content": "For small contractors (decks/fencing), semi-automatic approach (Claude Code + one click) is more valuable than full automation: follow-up semi-auto = 2-3 hrs vs full auto = 8-12 hrs. Client needs control, not full autonomy.",
    "importance": 0.8,
    "tags": ["product-strategy", "semi-auto-vs-auto"]
  },
  {
    "type": "insight",
    "content": "All won clients came through network/referrals — zero presence on freelance platforms despite strong fit. Untapped channel.",
    "importance": 0.8,
    "tags": ["sales-channel", "growth"]
  },
  {
    "type": "commitment",
    "content": "TODO: finalize scope, update price in HTML proposal, send to client by tomorrow",
    "importance": 0.6,
    "tags": ["follow-up"]
  }
]
```

## Files

| File | Purpose |
|------|---------|
| `openexp/ingest/extract_decisions.py` | Core module: read, extract, store |
| `openexp/hooks/session-end.sh` | Phase 2c integration (lines 235-272) |
