"""Prompt templates for multi-level retrospective analysis.

Each prompt instructs the LLM (via claude -p) to analyze a time window
and return a structured JSON summary with patterns and insights.
"""

DAILY_PROMPT = """\
You are analyzing a full day of AI assistant work for a memory system (OpenExp).

The system records everything the AI does: decisions, file edits, conversations.
Per-session decision extraction has already run, but it only sees one session at
a time — it can't see cross-session patterns.

Your job: look at the FULL DAY and find what per-session extraction missed.

## What to look for

1. **Cross-session threads** — morning research that enabled an afternoon
   breakthrough; a decision in one session that shaped another.

2. **False progress** — work that seemed productive (commits, writes) but was
   later undone or turned out wrong.

3. **Patterns** — recurring behaviors that help or hurt productivity.

## Data

### Sessions today
{sessions_data}

### Key memories created today
{memories_data}

## Output format

Return JSON (no markdown wrapping):
{{
  "summary": "2-3 sentence overview of the day",
  "patterns": ["pattern 1", "pattern 2"],
  "insights": [
    {{
      "content": "One clear sentence — a meta-learning worth remembering",
      "importance": 0.7,
      "tags": ["tag1"]
    }}
  ]
}}

Rules:
- insights are stored as new memories — only include genuinely useful meta-learnings.
- Max 5 insights. Be selective.
"""

WEEKLY_PROMPT = """\
You are conducting a weekly retrospective for a memory system (OpenExp).

Daily retrospectives have already summarized individual days. Your job is to look
at the FULL WEEK and find what daily retrospectives missed — especially delayed
outcomes and cross-day patterns.

## What to look for

1. **Delayed outcomes** — work done Monday that only showed results by Friday.

2. **False progress correction** — something looked good early in the week but
   turned out wrong later.

3. **Strategic patterns** — which types of work consistently lead to results?
   Which are time sinks?

4. **Entity-level patterns** — did work on specific clients/projects consistently
   produce results or consistently fail?

## Data

### Daily retrospective summaries this week
{daily_retrospectives}

## Output format

Return JSON (no markdown wrapping):
{{
  "summary": "2-3 sentence overview of the week",
  "patterns": ["weekly pattern 1", "weekly pattern 2"],
  "insights": [
    {{
      "content": "Strategic insight from the week",
      "importance": 0.8,
      "tags": ["strategy"]
    }}
  ]
}}

Rules:
- Focus on what daily retrospectives MISSED.
- Max 5 insights.
"""

MONTHLY_PROMPT = """\
You are conducting a monthly strategic retrospective for a memory system (OpenExp).

Daily and weekly retrospectives handle the tactical level. Your job is the
STRATEGIC level — what worked over the full month? What didn't? What should change?

## What to look for

1. **Strategy effectiveness** — which approaches (research→action, direct outreach,
   tool building, etc.) actually led to results over 30 days?

2. **Diminishing returns** — work that was valuable initially but is now noise.

3. **Emerging themes** — new patterns that only become visible at monthly scale.

## Data

### Weekly retrospective summaries this month
{weekly_retrospectives}

## Output format

Return JSON (no markdown wrapping):
{{
  "summary": "3-5 sentence strategic overview of the month",
  "patterns": ["monthly pattern 1"],
  "insights": [
    {{
      "content": "Strategic meta-learning from the month",
      "importance": 0.9,
      "tags": ["strategy", "monthly"]
    }}
  ]
}}

Rules:
- Insights should be high-level strategic learnings.
- Max 5 insights.
"""
