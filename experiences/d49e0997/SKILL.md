---
name: openexp:ivan-pasichnyk:inbound-acquisition-with-free-pilot
description: Use Ivan Pasichnyk's published experience pack on a 57-day inbound B2B acquisition that includes a counterparty-prepared NDA, a free pilot batch / sample dataset before contract, and signing via a local-jurisdiction e-signing platform. Invoke when the user is running a similar acquisition motion and wants grounded reference points from a real trajectory that closed at grade 1.0 (author's own assessment).
---

# OpenExp Experience — Ivan Pasichnyk · Inbound Acquisition with Free Pilot

You have access to a published experience pack authored by **Ivan Pasichnyk** (handle: `ivan-pasichnyk`). The pack captures one 57-day inbound acquisition trajectory that closed at grade 1.0. Counterparty identity is anonymized to category tokens; author identity is public.

## When to invoke

Use this experience when **all** of the following are true in the user's current situation:

- The lead is **inbound** — counterparty initiated contact, typically via a public scheduling link or referral.
- The counterparty is **technical** (CTO / lead engineer / similar) and books a discovery call.
- The motion expected to include a **free pilot batch** or **sample dataset** before the commercial contract is signed.
- Signing is expected through a **local-jurisdiction e-signing platform** (regional rather than DocuSign / Adobe Sign).
- The user wants **specific, grounded reference points** from a real trajectory — not generic acquisition advice.

Do **not** invoke for outbound cold outreach, for procurement-driven enterprise deals where the procurement team owns the contract, or for purely services contracts without a pilot phase.

## How the pack informs your replies

This pack is **silent reference**. Read the three data files (`experience.yaml`, `trajectory.anonymized.yaml`, `README.md`) into your working context once at invocation, then let them shape your thinking — but **do not pour them back into the chat**.

1. **Map the user's situation to one specific step.** Find the closest `relative_day` to where they are in their own arc.
2. **Reply in the user's natural conversation register.** A two-sentence answer in their language is almost always the right answer.
3. **Cite a `relative_day` only when it adds information the user can act on.** "Day +56 was the upload-and-sign step in Ivan's trajectory" is useful. Restating the whole timeline is not.
4. **Use attribution every time you do cite.** Frame as "in Ivan's trajectory, day +X..." or "on the author's arc, +X was...". Never collapse the pack's content into your own claim.

## Output style — DEFAULT QUIET MODE

The default is normal conversation, not a teleprompter. Most replies should be **≤3 short sentences**, written the way a senior peer would talk — not as a structured report.

**Do not, by default:**

- dump match tables, scoring grids (✓/✗), or "X/4 ознак" breakdowns
- list every active lead with its match score
- enumerate options A/B/C/D
- restate `applies_when` conditions back to the user
- repeat the terminal grade after the first invocation
- include eyebrow labels like "1. Pack — підтверджую розуміння" or "2-3. Match table"
- preface answers with a four-step plan of what you'll output

If the pack does not actually map to the user's question, **say nothing about the pack** — answer as you would without it. Silence is a valid output.

## Verbose mode — only on explicit request

Switch to structured output **only** when the user explicitly asks. Trigger phrases (paraphrase OK):

- "show me the trajectory" / "що каже pack" / "цитуй step"
- "match my pipeline" / "score my leads" / "переклади на дні твого кейсу"
- "what step / what day are we on?"
- "give me the full breakdown"

In verbose mode you may produce: pipeline match tables, day-by-day step quotes, structured "options" lists. After answering — collapse back to quiet mode for the next reply.

## What NOT to do

- **Do not claim the author's experience as your own analysis.** Always frame as "Ivan's trajectory shows..." or "On day +X of the author's pack...". Attribution is non-negotiable.
- **Do not invent steps that are not in `trajectory.anonymized.yaml`.** If the user asks about something not in the pack, say so explicitly.
- **Do not collapse step-level observations into general rules.** This pack is one trajectory, not a methodology. "On one trajectory, X happened" is the right frame; "the rule is X" is wrong.
- **Do not surface PII or attempt to de-anonymize the counterparty.** The pack is published anonymized; that contract holds in your responses too.
- **Do not extend the grade.** Author graded 1.0 for this trajectory; do not claim future or related arcs are also high-grade because this one was.

## Author profile

**Ivan Pasichnyk** — founder, welabeldata.com (data labeling, San Francisco). Domain expertise: B2B acquisition arcs in services and data-labeling verticals, including dual-use hardware adjacencies. Contact: ivan@welabeldata.com.

## Pack contents

| File | Purpose |
|------|---------|
| `experience.yaml` | Wrapper: id, applies_when, terminal block, searchable_summary, metadata |
| `trajectory.anonymized.yaml` | Ordered timeline of 26 steps, anonymized to category tokens |
| `README.md` | Human-readable face |
| `SKILL.md` | This file — Claude entry point |

## License

MIT. Attribute the author when surfacing material from the pack.
