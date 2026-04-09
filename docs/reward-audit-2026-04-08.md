# Reward System Audit — 2026-04-08

> Full code audit of all 5 reward paths. Every claim verified against code with file:line references.

## Current State Summary

| Path | Name | Status | Rewards logged | Q-values actually changed |
|------|------|--------|---------------|--------------------------|
| 1 | Session Reward | Working | 23 | Yes, but tiny (max q=0.031 in default) |
| 2 | Prediction | Code works, unused | 1 (test) | 0 real |
| 3 | CRM Business | Code works, misconfigured | 1 | ~0 |
| 4 | Calibration | Working | 62 | Yes, but race condition loses some |
| 5 | Retrospective | Working, orphan bug | 88 | Mostly wasted on test IDs |

**Total Qdrant points:** 269,744
**Q-cache entries:** 98,793
**Non-zero Q-values:** 235 (0.24%)

---

## Path 1: Session Reward

**Files:** `ingest/reward.py`, `ingest/__init__.py`, `hooks/session-end.sh`

### How it works

1. `session-end.sh` Phase 2a (line 168) calls `python -m openexp.cli ingest --session-id <ID>`
2. `ingest_session()` (`ingest/__init__.py:46`) orchestrates the pipeline
3. `compute_session_reward(observations, weights)` (`reward.py:47`) scores session by tool calls:
   - Base: -0.1
   - git commit: +0.3, PR: +0.2, writes: +0.02 each (max 0.2), deploy: +0.1, tests: +0.1, decisions: +0.1
   - <3 observations: -0.05, no output: -0.1
   - Sales signals (email_sent, proposal_sent, etc.) have **weight 0.0** in defaults (reward.py:101-132)
   - Experience-specific weights override via `experience.session_reward_weights` (ingest/__init__.py:101)
   - Result clamped to [-0.5, 0.5]
4. `reward_retrieved_memories()` (`reward.py:219`) retrieves IDs from `session_retrievals.jsonl` (field: `memory_ids`, NOT `retrieved_ids`)
5. If `experience.reward_memory_types` is set, filters by type (reward.py:240-255)
6. `apply_session_reward()` (`reward.py:137`) updates Q-values equally for ALL retrieved memories
7. Fallback (session-end.sh:175-234): identical logic, runs if main path didn't fire

### Verified behavior

- **23 session rewards** in reward_log.jsonl (type="session")
- Reward values range: -0.20 to +0.50
- Memories targeted: 20 to 2,721 per session (early bug rewarded ALL memories)
- Bug fixed 2026-03-29: now rewards only recalled memories
- **Default experience Q-values:** max 0.031 after rewards — too small to influence ranking
- **Sales experience Q-values:** 0.04-0.15 from recent sessions

### Problems

1. **Evaluation is dumb.** Strategic conversation without commits = negative reward. Typo fix commit = positive. (`reward.py:80-82`)
2. **No differentiation.** All recalled memories get equal reward. Memory that was actually used vs noise both get same Q update. (`reward.py:183-187` — loops over all point_ids with same layer_rewards)
3. **Sales signals all weight 0.0.** Email_sent, proposal_sent, invoice_sent — all default to 0.0. Only work if experience overrides weights. (`reward.py:101-116`)

### Decision

**Ivan requested removal** (2026-04-08). Reason: heuristic doesn't reflect real session value.

---

## Path 2: Prediction -> Outcome

**Files:** `reward_tracker.py`, `mcp_server.py:98-131,351-369`

### How it works

1. `log_prediction` MCP tool (mcp_server.py:98) → `RewardTracker.log_prediction()` (reward_tracker.py:104)
   - Stores: prediction text, confidence [0,1], strategic_value [0,1], memory_ids_used, client_id
   - Writes to `~/.openexp/data/predictions.jsonl`
   - Returns `pred_<8-hex>` ID
2. `log_outcome` MCP tool (mcp_server.py:118) → `RewardTracker.log_outcome()` (reward_tracker.py:133)
   - Takes: prediction_id, outcome text, reward [-1,1], cause_category
   - Updates Q-values for ALL memory_ids_used from the prediction (reward_tracker.py:198-203)
   - Logs L3/L4 records
   - Categories: execution_failure, strategy_failure, qualification_failure, hypothesis_failure, external, competition

### Verified behavior

- **1 prediction exists** in predictions.jsonl: test prediction from 2026-03-23 (resolved, reward=0.8)
- **0 real business predictions** ever logged
- **100% manual** — no hooks, no automation, no prompts tell Claude to use this

### Problems

1. **Nobody told Claude to use it.** Not in CLAUDE.md, not in dispatcher, not in any hook. The tools exist but are never invoked.
2. **memory_ids_used must be passed explicitly.** Agent must know which memories influenced the prediction and pass their IDs. No automatic attribution.

---

## Path 3: CRM Business Outcome

**Files:** `outcome.py`, `resolvers/crm_csv.py`, `ingest/__init__.py:129-153`

### How it works

1. `CRMCSVResolver.detect_outcomes()` (crm_csv.py:124):
   - Reads current state from `$OPENEXP_CRM_DIR/relationships/deals.csv` and `leads.csv`
   - Loads last snapshot from `~/.openexp/data/crm_snapshot.json`
   - Diffs stage transitions against reward table:
     - Deal: negotiation→won = +0.8, invoiced→paid = +1.0, *→lost = -0.5
     - Lead: new→qualified = +0.4, qualified→proposal = +0.6, *→dead = -0.5
   - Saves new snapshot
2. `resolve_outcomes()` (outcome.py:110) finds memories by `client_id` in Qdrant
3. Applies reward to all tagged memories

### Configuration

- `.env` sets: `OPENEXP_OUTCOME_RESOLVERS=openexp.resolvers.crm_csv:CRMCSVResolver`
- `.env` sets: `OPENEXP_CRM_DIR=/Users/ivanpasichnyk/welababeldata/sales/crm`
- `crm_snapshot.json` exists (14KB, last modified 2026-04-08)
- Snapshot contains real deal data (deal-dt-001 through deal-dt-003, etc.)

### Triggers

1. **SessionEnd:** `ingest_session()` calls `resolve_outcomes()` after observations (ingest/__init__.py:131)
2. **MCP tool:** `resolve_outcomes` tool in mcp_server.py:430
3. **No cron/launchd** for standalone execution

### Verified behavior

- **1 business reward** in reward_log.jsonl total
- Snapshot IS populated with real CRM data
- Resolver IS configured in .env

### Problems

1. **session-end.sh may not load .env.** The shell hook doesn't explicitly source `~/openexp/.env`. The Python code uses `python-dotenv` but only if the module loads it. Need to verify if `OPENEXP_CRM_DIR` is available in the session-end.sh subprocess. (`config.py:55` reads from os.getenv)
2. **Runs only on SessionEnd.** CRM changes happen independently of Claude sessions. If deal stage changes and no session runs, reward never fires.
3. **Stage changes are rare.** Most sessions don't coincide with CRM stage transitions.
4. **Snapshot resets on every run.** Even if no changes detected, snapshot is saved (crm_csv.py:133). No diff = no events, but any race condition could miss transitions.

---

## Path 4: Calibration

**Files:** `mcp_server.py:557-619`

### How it works

1. `calibrate_experience_q` MCP tool (mcp_server.py:217)
2. **Direct Q-value assignment** — NOT alpha-scaled (mcp_server.py:571-574):
   ```python
   q_data["q_value"] = new_q
   q_data["q_action"] = new_q
   q_data["q_hypothesis"] = new_q
   q_data["q_fit"] = new_q
   ```
3. Sets in-memory cache immediately via `q_cache.set()` (mcp_server.py:610)
4. Persists via `save_delta()` at session exit (mcp_server.py:63, atexit hook)
5. Logs L3 with `reward_type="calibration"` (mcp_server.py:598-606)

### Verified behavior

- **62 calibrations** in reward_log.jsonl
- All in `sales` experience
- Examples: DT pilot paid q=0.8, SQUAD Drive+BambooHR q=0.8, DT OOO auto-reply q=0.0
- Values range: 0.0 to 0.9

### Race condition bug (CONFIRMED)

**Evidence:** Memory `fc5aa213` calibrated to q=0.8 (logged in reward_log.jsonl), but Q-cache shows q=0.5.

**Root cause:** Calibration uses `save_delta()` on session exit (mcp_server.py:63). Retrospective uses full `save()` (retrospective.py:507). If retrospective runs between calibration and session exit:

1. Calibration sets q=0.8 in memory, queues delta
2. Retrospective loads q_cache.json (still q=0.0), makes adjustments, saves full cache
3. Calibration session exits, writes delta
4. Next `load_and_merge()` reads retrospective's full cache + delta → but `_is_newer()` timestamp comparison may not resolve correctly

**Impact:** Some calibration Q-values are lost or overwritten.

---

## Path 5: Retrospective

**Files:** `retrospective.py`, `retrospective_prompts.py`

### How it works

1. **Trigger:** launchd daily at 23:30 (`~/Library/LaunchAgents/com.openexp.retrospective.daily.plist`)
   - Also: weekly, monthly launchd agents
   - Also: manual CLI: `openexp retrospective daily [YYYY-MM-DD]`
2. **Gather data** (retrospective.py:81-155):
   - Session summaries from `~/.openexp/sessions/YYYY-MM-DD-*.md` (max 2000 chars each)
   - Reward events from `reward_log.jsonl` filtered by date
   - Memories from Qdrant with source="decision_extraction", created on that date
   - Q-values from QCache
3. **LLM analysis** (retrospective.py:343-398):
   - Calls `claude -p --model opus` (Max subscription, free)
   - Prompt asks for: cross-session attribution, over/under-rewarded memories, false progress, patterns
   - Output: JSON with `adjustments[]`, `insights[]`, `summary`, `patterns[]`
4. **Apply adjustments** (retrospective.py:405-509):
   - Validates memory_id exists in **Q-cache only** (line 433), NOT Qdrant
   - Actions: `promote` (+reward), `demote` (-reward), `override` (set target_q)
   - Max 20 adjustments per run (MAX_ADJUSTMENTS, line 38)
   - Saves full Q-cache after (line 507)
5. **Store retrospective** as Qdrant memory (retrospective.py:516-584)
6. **Idempotency** via watermark.json (line 634-715)

### Verified behavior

- **88 daily_retrospective rewards** in reward_log.jsonl
- **Watermark:** only daily/2026-04-07 processed. Weekly/monthly never run.
- **Reward distribution:** 84 rewards → mem-0001, 4 rewards → mem-0002

### Orphan bug (ROOT CAUSE FOUND)

**mem-0001 through mem-0004** are **test fixtures** from `tests/test_retrospective.py:45-58`:
```python
for i in range(5):
    mem_id = f"mem-{i:04d}"
    cache.set(mem_id, {...})
```

Tests ran apply_adjustments() with these IDs. Test Q-cache state **leaked into production** `q_cache.json`. 

LLM retrospective prompt says: "memory_id MUST be an exact UUID from the data above" (retrospective_prompts.py:72). But the LLM received test IDs in Q-cache data → used them in adjustments → validation passed (they exist in Q-cache) → rewards applied to non-existent memories.

**Impact:** 84 of 88 retrospective rewards (95%) went to test fixtures that don't exist in Qdrant.

---

## Cross-Cutting Issues

### Q-Cache Concurrency

Multiple writers to `q_cache.json`:

| Writer | Method | Locking |
|--------|--------|---------|
| ingest (Path 1) | `q_cache.save()` | fcntl.flock |
| retrospective (Path 5) | `q_cache.save()` | fcntl.flock |
| MCP server (Path 4) | `q_cache.save_delta()` | None |
| compaction | `q_cache.save()` | fcntl.flock |

`save_delta()` has no locking. Delta files are merged on next `load_and_merge()`, but `_is_newer()` comparison (q_value.py:278) uses timestamps which may not resolve conflicts correctly.

### Environment Loading

`session-end.sh` does NOT source `~/openexp/.env`. Python subprocess may or may not load dotenv depending on import chain. This could cause `OPENEXP_CRM_DIR` to be None in the session-end context, preventing CRM resolver from running.

**Verified:** `config.py:1` does `from dotenv import load_dotenv; load_dotenv()` — but this loads `.env` from CWD, which in session-end.sh is set to `$OPENEXP_DIR` (line 141). Since `~/openexp/.env` exists, dotenv SHOULD find it when CWD is `~/openexp`.

---

## Action Items

1. **Remove Path 1 session reward** — Ivan's decision. Heuristic doesn't reflect real value.
2. **Clean test fixtures from Q-cache** — Remove mem-0000 through mem-0004 entries.
3. **Add Qdrant existence check to retrospective** — `apply_adjustments()` should verify memory exists in Qdrant, not just Q-cache.
4. **Fix calibration persistence** — Use `save()` with locking instead of `save_delta()`, or merge deltas before retrospective runs.
5. **Add prediction logging instructions** — Add to CLAUDE.md: when making predictions/recommendations, use `log_prediction` tool.
6. **Add CRM resolver cron** — Standalone daily job to run `resolve_outcomes` independent of sessions.
7. **Verify .env loading in session-end.sh** — Add explicit dotenv loading or source .env in the hook.
