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

## How to use the pack

1. **Read `experience.yaml` first.** It carries the high-level shape: domain, duration, applies_when, terminal outcome and grade, searchable_summary.
2. **Read `trajectory.anonymized.yaml` for full detail.** 26 ordered steps from `relative_day: 0` (inbound discovery) to `relative_day: +57` (contract signed both sides).
3. **Search `steps.indexable.jsonl` for pattern-level matches.** Each line is one step, indexable by content. When the user describes a specific moment in their own arc, find the matching step in the author's trajectory and cite it.
4. **Cite specific steps when advising.** Name the `relative_day` and what happened at that point in the author's trajectory ("On day +25 of Ivan's trajectory, the counterparty went silent..."). Do not generalize beyond what is recorded.
5. **Surface the terminal grade and reason.** The author rated this trajectory 1.0 with the comment: "Successful case. Exactly how my business should look — a clean, typical good case." Use that as a calibration anchor for how this kind of arc is *supposed* to feel.

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
| `steps.indexable.jsonl` | Per-step records for retrieval (one JSON object per line) |
| `README.md` | Human-readable face |
| `SKILL.md` | This file — Claude entry point |

## License

MIT. Attribute the author when surfacing material from the pack.
