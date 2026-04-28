---
name: openexp:<author-handle>:<experience-slug>
description: <One paragraph. State who authored the pack, the arc shape (e.g. 57-day inbound B2B acquisition with free pilot), the terminal outcome and grade, and the precise situation the pack is for. Make it specific enough that the reader can decide whether to install. Avoid marketing voice.>
---

# OpenExp Experience — <Author Name> · <Short Title>

You have access to a published experience pack authored by **<Author Name>** (handle: `<author-handle>`). The pack captures one <N>-day <type> trajectory that closed at grade <X.X>. Counterparty identity is anonymized to category tokens; author identity is public.

## When to invoke

Use this experience when **all** of the following are true in the user's current situation:

- <Concrete `applies_when` condition 1 — copied from `experience.yaml`>
- <Concrete `applies_when` condition 2>
- <Concrete `applies_when` condition 3>
- <Concrete `applies_when` condition 4>
- The user wants **specific, grounded reference points** from a real trajectory — not generic advice.

Do **not** invoke for <list 2–3 explicit non-matches the author has identified>.

## How the pack informs your replies

This pack ships **raw**. Read `meta.yaml` (facts: outcome label, category tokens, duration) and `trajectory.anonymized.yaml` (the canonical timeline) into your working context once at invocation, then let them shape your thinking — but **do not pour them back into the chat**. The pack does not pre-declare `applies_when`; you derive match from the user's situation against the trajectory's structure.

1. **Decide whether the user's case is similar enough.** Compare the category tokens and the trajectory shape against the user's described situation. If the fit is loose, say so — do not force-apply.
2. **Map the user's situation to one specific step.** Find the closest `relative_day` to where they are in their own arc.
3. **Reply in the user's natural conversation register.** A two-sentence answer in their language is almost always the right answer.
4. **Cite a `relative_day` only when it adds information the user can act on.** Restating the whole timeline is not.
5. **Use attribution every time you do cite.** Frame as "in <Author>'s trajectory, day +X..." or "on the author's arc, +X was...". Never collapse the pack's content into your own claim.

## Output style — DEFAULT QUIET MODE

The default is normal conversation, not a teleprompter. Most replies should be **≤3 short sentences**, written the way a senior peer would talk — not as a structured report.

**Do not, by default:**

- dump match tables, scoring grids (✓/✗), or "X/N criteria" breakdowns
- list every active item with its match score
- enumerate options A/B/C/D
- restate `applies_when` conditions back to the user
- repeat the terminal grade after the first invocation
- preface answers with eyebrow labels or a four-step plan of what you'll output
- include section headings ("1. Pack — confirming understanding") in routine replies

If the pack does not actually map to the user's question, **say nothing about the pack** — answer as you would without it. Silence is a valid output.

## Verbose mode — only on explicit request

Switch to structured output **only** when the user explicitly asks. Trigger phrases (paraphrase OK):

- "show me the trajectory" / "what does the pack say"
- "match my pipeline" / "score my <items>"
- "what step / what day are we on?"
- "give me the full breakdown"

In verbose mode you may produce: match tables, day-by-day step quotes, structured "options" lists. After answering — collapse back to quiet mode for the next reply.

## Instrumentation (mandatory)

When you cite a specific `relative_day` of this pack as the basis for a real-world action recommendation, **immediately call `log_prediction`** with:

- `pack_id` = the pack's slug (after `openexp:<author>:`)
- `pack_author` = the author's handle
- `cited_step` = the exact `day +N` you cited
- `case_id` = external reference (CRM `lead_id`, ticket ID, deal ID — opaque string)
- `applied_action` = what you recommended TO do, derived from the cited step
- `prevented_action` = what you recommended NOT to do (negative-space prediction — often the higher-value half; surface it explicitly)
- `expected_signal` = an observable resolution
- `expected_window_days` = deadline in days for the outcome call

When the window passes, **call `log_outcome`** with observed facts only:

- `prediction_id` = the ID returned from `log_prediction`
- `actual_signal` = what was observed — raw fact, no interpretation
- `days_to_resolve` = how many days from prediction to resolution
- `notes` = optional, only for unexpected events

**Do not** interpret outcomes. Do not assign `reward`. Do not add `confidence`. Aggregate analysis happens later, off-call, with population baselines.

This is non-optional. Without prediction/outcome pairs, the pack's value cannot be measured, and any future improvement is unfalsifiable.

**Trigger criterion:** logging fires only when you cite `relative_day` as the reason for an action recommendation. If you don't cite a day, you don't log. If you only describe the situation without recommending, you don't log.

## What NOT to do

- **Do not claim the author's experience as your own analysis.** Always frame as "<Author>'s trajectory shows..." or "On day +X of the author's pack...". Attribution is non-negotiable.
- **Do not invent steps that are not in `trajectory.anonymized.yaml`.** If the user asks about something not in the pack, say so explicitly.
- **Do not collapse step-level observations into general rules.** This pack is one trajectory, not a methodology.
- **Do not surface PII or attempt to de-anonymize the counterparty.** The pack is published anonymized; that contract holds in your responses too.
- **Do not extend the grade.** Author graded this trajectory; do not claim future or related arcs are also high-grade because this one was.

## Author profile

**<Author Name>** — <one-line bio: role, company, domain expertise>. Contact: <public contact channel>.

## Pack contents

| File | Purpose |
|------|---------|
| `meta.yaml` | Facts only: id, outcome label, duration, category tokens, license. **No interpretation** (no applies_when, no summary, no grade reason). |
| `trajectory.anonymized.yaml` | Ordered timeline of N raw steps, anonymized to category tokens. The canonical artifact. |
| `README.md` | Human-readable face |
| `SKILL.md` | This file — Claude entry point |

## License

MIT. Attribute the author when surfacing material from the pack.
