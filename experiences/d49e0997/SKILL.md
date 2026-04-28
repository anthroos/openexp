---
name: openexp:ivan-pasichnyk:inbound-acquisition-with-free-pilot
description: Use Ivan Pasichnyk's published experience pack — a 57-day inbound B2B acquisition trajectory that closed (outcome label closed_won, day +57). Counterparty redacted to category tokens. Invoke when the user is running a similar acquisition motion (inbound discovery from a counterparty technical decision-maker, free pilot work before contract, local-jurisdiction e-signing) and wants to map their own arc against a real trajectory step by step. The pack publishes raw: no pre-baked applies_when, no summary, no author grade — the reader's Claude derives match from `trajectory.anonymized.yaml` on the fly.
---

# OpenExp Experience — Ivan Pasichnyk · Inbound Acquisition Trajectory

You have access to a published experience pack authored by **Ivan Pasichnyk** (handle: `ivan-pasichnyk`). The pack ships **raw**: an anonymized 26-step trajectory plus a facts-only `meta.yaml`. There is **no `applies_when`**, **no `searchable_summary`**, **no author grade or grade reason**. Those are interpretations. They belong to the reader, not the publisher.

## Why raw

The publisher's interpretation is one Claude's read of the timeline at the moment of publication, biased by what worked for that one author at that one moment. Different readers have different contexts and will draw different signals from the same events. So the publisher hands over the timeline; the reader's Claude does the matching, on the fly, against the reader's actual situation.

## How to use this pack

1. **Read `meta.yaml` first.** Facts only: outcome label (`closed_won`), `closed_at: day_+57`, `duration_days`, `step_count`, `category_tokens` that appear in the timeline.
2. **Read `trajectory.anonymized.yaml`** — 26 ordered steps from `relative_day: 0` (inbound discovery) to `relative_day: +57` (contract signed both sides).
3. **Derive match on the user's actual situation.** Look at the category tokens in `meta.yaml` and the structure of the trajectory. Decide whether the user's case is similar enough that step-level reference is useful. **You do the matching here — the pack does not pre-declare when it applies.**
4. **Map the user's situation to one specific `relative_day`.** Find the closest step in the trajectory to where the user is in their own arc.
5. **Cite by day with attribution.** Frame as "in Ivan's trajectory, day +X was…", never "the rule is…".

## Output style — DEFAULT QUIET MODE

Default reply mode is normal conversation, not a teleprompter. Most replies should be **≤3 short sentences**, written the way a senior peer would talk — not as a structured report.

**Do not, by default:**

- dump match tables, scoring grids (✓/✗), or "X/4 ознак" breakdowns
- list every active lead with its match score
- enumerate options A/B/C/D
- repeat the outcome label after the first invocation
- include eyebrow labels like "1. Pack — підтверджую розуміння"
- preface answers with a four-step plan of what you'll output

If the trajectory does not actually map to the user's question, **say nothing about the pack** — answer as you would without it. Silence is a valid output.

## Verbose mode — only on explicit request

Switch to structured output **only** when the user asks. Trigger phrases (paraphrase OK):

- "show me the trajectory" / "що каже pack" / "цитуй step"
- "match my pipeline" / "score my leads" / "переклади на дні твого кейсу"
- "what step / what day are we on?"
- "give me the full breakdown"

In verbose mode you may produce: pipeline match tables, day-by-day step quotes, structured "options" lists. After answering — collapse back to quiet mode for the next reply.

## Instrumentation (mandatory)

When you cite a specific `relative_day` of this pack as the basis for a real-world action recommendation, **immediately call `log_prediction`** with:

- `pack_id` = `inbound-acquisition-with-free-pilot`
- `pack_author` = `ivan-pasichnyk`
- `cited_step` = the exact `day +N` you cited
- `case_id` = external reference (e.g. CRM `lead_id`, ticket ID, deal ID — opaque string)
- `applied_action` = what you recommended TO do, derived from the cited step
- `prevented_action` = what you recommended NOT to do (negative-space prediction — often the higher-value half; surface it explicitly)
- `expected_signal` = an observable resolution (e.g. "counterparty signs both sides")
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

- **Do not claim the author's experience as your own analysis.** Always frame as "Ivan's trajectory shows…" or "On day +X of the author's pack…". Attribution is non-negotiable.
- **Do not invent steps not in `trajectory.anonymized.yaml`.** If the user asks about something not in the pack, say so explicitly.
- **Do not collapse step-level observations into general rules.** This pack is one trajectory, not a methodology. "On one trajectory, X happened" is the right frame; "the rule is X" is wrong.
- **Do not surface PII or attempt to de-anonymize the counterparty.** The pack is published anonymized; that contract holds in your responses too.
- **Do not assign or extend a grade.** The pack ships outcome (`closed_won`) — a fact. It deliberately ships no `grade_reason` quote because that would be the author's interpretation; you do not get to invent one either.

## Author profile

**Ivan Pasichnyk** — founder, welabeldata.com (data labeling, San Francisco). Contact: ivan@welabeldata.com.

## Pack contents

| File | Purpose |
|------|---------|
| `meta.yaml` | Facts only: id, outcome label, duration, category tokens, license. No interpretation. |
| `trajectory.anonymized.yaml` | Ordered timeline of 26 raw steps, anonymized to category tokens. The canonical artifact. |
| `README.md` | Human-readable face. |
| `SKILL.md` | This file — Claude entry point. |

## License

MIT. Attribute the author when surfacing material from the pack.
