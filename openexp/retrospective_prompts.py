"""Prompt templates for multi-level retrospective analysis.

Each prompt instructs Opus 4.6 (via claude -p) to analyze a time window
and return structured JSON with Q-value re-evaluation decisions.
"""

DAILY_PROMPT = """\
You are analyzing a full day of AI assistant work for a Q-learning memory system (OpenExp).

The system records everything the AI does: tool calls, file edits, decisions, outcomes.
Each memory has a Q-value (-0.5 to 1.0) that rises when the memory leads to productive work
and falls when it doesn't. Session-level rewards have already been applied, but they only
see one session at a time — they can't see cross-session patterns.

Your job: look at the FULL DAY and find what the per-session rewards missed.

## What to look for

1. **Cross-session attribution** — morning research that enabled afternoon breakthrough.
   The morning session may have gotten low reward (no commits), but it was essential.

2. **Over-rewarded memories** — a session had commits, so all memories got rewarded,
   but some were irrelevant to the actual work.

3. **Under-rewarded memories** — a decision or insight that didn't lead to immediate
   output but set up future success.

4. **False progress** — work that seemed productive (commits, writes) but was
   later undone or turned out wrong.

5. **Patterns** — recurring behaviors that help or hurt productivity.

## Data

### Sessions today
{sessions_data}

### Reward events today
{reward_events}

### Key memories used/created today (with current Q-values)
{memories_data}

## Output format

Return JSON (no markdown wrapping):
{{
  "summary": "2-3 sentence overview of the day",
  "patterns": ["pattern 1", "pattern 2"],
  "adjustments": [
    {{
      "memory_id": "exact-uuid-from-data-above",
      "action": "promote|demote|override",
      "reward": 0.2,
      "target_q": null,
      "reason": "Why this memory should be re-evaluated"
    }}
  ],
  "insights": [
    {{
      "content": "One clear sentence — a meta-learning worth remembering",
      "importance": 0.7,
      "tags": ["tag1"]
    }}
  ]
}}

Rules:
- Max 20 adjustments. Be selective — only adjust when you have clear evidence.
- "promote": positive reward (0.1-0.5). "demote": negative reward (-0.1 to -0.5).
- "override": set target_q directly (use sparingly, only for clear errors).
- memory_id MUST be an exact UUID from the data above. Do not invent IDs.
- insights are stored as new memories — only include genuinely useful meta-learnings.
"""

WEEKLY_PROMPT = """\
You are conducting a weekly retrospective for a Q-learning memory system (OpenExp).

Daily retrospectives have already re-evaluated individual memories. Your job is to look
at the FULL WEEK and find what daily retrospectives missed — especially delayed outcomes
and cross-day patterns.

## What to look for

1. **Delayed outcomes** — work done Monday that only showed results by Friday.
   Example: research on Monday → client call Wednesday → deal moved forward Friday.
   Monday's research memories may still have low Q-values.

2. **False progress correction** — something looked good early in the week but
   turned out wrong later. The daily retrospective may have promoted it,
   but the weekly view shows it should be demoted.

3. **Strategic patterns** — which types of work consistently lead to results?
   Which are time sinks?

4. **Entity-level patterns** — did work on specific clients/projects consistently
   produce results or consistently fail?

## Data

### Daily retrospective summaries this week
{daily_retrospectives}

### All reward events this week
{reward_events}

### Top memories by Q-value change this week
{q_value_changes}

## Output format

Return JSON (no markdown wrapping):
{{
  "summary": "2-3 sentence overview of the week",
  "patterns": ["weekly pattern 1", "weekly pattern 2"],
  "adjustments": [
    {{
      "memory_id": "exact-uuid",
      "action": "promote|demote|override",
      "reward": 0.3,
      "target_q": null,
      "reason": "Weekly context reveals this should be re-evaluated"
    }}
  ],
  "insights": [
    {{
      "content": "Strategic insight from the week",
      "importance": 0.8,
      "tags": ["strategy"]
    }}
  ]
}}

Rules:
- Max 20 adjustments. Focus on what daily retrospectives MISSED.
- Prefer "override" for correcting false progress (daily promoted, weekly demotes).
- memory_id MUST be an exact UUID from the data above.
"""

MONTHLY_PROMPT = """\
You are conducting a monthly strategic retrospective for a Q-learning memory system (OpenExp).

Daily and weekly retrospectives handle tactical re-evaluation. Your job is the
STRATEGIC level — what worked over the full month? What didn't? What should change?

## What to look for

1. **Long-term Q-value trajectories** — which memories consistently rise or fall?
   Are there memories that get promoted daily but never lead to real outcomes?

2. **Strategy effectiveness** — which approaches (research→action, direct outreach,
   tool building, etc.) actually led to results over 30 days?

3. **Diminishing returns** — work that was valuable initially but is now noise.
   Old context that keeps getting retrieved but is no longer relevant.

4. **Emerging themes** — new patterns that only become visible at monthly scale.

## Data

### Weekly retrospective summaries this month
{weekly_retrospectives}

### Q-value statistics
{q_stats}

### Top and bottom memories by Q-value
{top_bottom_memories}

## Output format

Return JSON (no markdown wrapping):
{{
  "summary": "3-5 sentence strategic overview of the month",
  "patterns": ["monthly pattern 1"],
  "adjustments": [
    {{
      "memory_id": "exact-uuid",
      "action": "promote|demote|override",
      "reward": 0.4,
      "target_q": null,
      "reason": "Monthly strategic re-evaluation"
    }}
  ],
  "insights": [
    {{
      "content": "Strategic meta-learning from the month",
      "importance": 0.9,
      "tags": ["strategy", "monthly"]
    }}
  ]
}}

Rules:
- Max 15 adjustments. Monthly = strategic, not tactical.
- Focus on memories with many visits but questionable value.
- Insights should be high-level strategic learnings.
- memory_id MUST be an exact UUID from the data above.
"""
