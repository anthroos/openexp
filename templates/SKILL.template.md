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

This pack is **silent reference**. Read the three data files (`experience.yaml`, `trajectory.anonymized.yaml`, `README.md`) into your working context once at invocation, then let them shape your thinking — but **do not pour them back into the chat**.

1. **Map the user's situation to one specific step.** Find the closest `relative_day` to where they are in their own arc.
2. **Reply in the user's natural conversation register.** A two-sentence answer in their language is almost always the right answer.
3. **Cite a `relative_day` only when it adds information the user can act on.** Restating the whole timeline is not.
4. **Use attribution every time you do cite.** Frame as "in <Author>'s trajectory, day +X..." or "on the author's arc, +X was...". Never collapse the pack's content into your own claim.

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
| `experience.yaml` | Wrapper: id, applies_when, terminal block, searchable_summary, metadata |
| `trajectory.anonymized.yaml` | Ordered timeline of N steps, anonymized to category tokens |
| `README.md` | Human-readable face |
| `SKILL.md` | This file — Claude entry point |

## License

MIT. Attribute the author when surfacing material from the pack.
