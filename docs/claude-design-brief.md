# Claude Design Brief — OpenExp v2 Landing Page

**Purpose of this document:** A self-contained briefing for a Claude Design agent (or any external collaborator) tasked with rewriting the OpenExp landing page. You start cold. Read this end-to-end before producing any output.

**Scope is deliberately narrow:** one static HTML landing page. Nothing else. Promotion (HN posts, essays, DMs) and funding applications are NOT in scope — those require founder voice and founder numbers and stay with the founder.

---

## What OpenExp Is (v2, as of 2026-04-26)

OpenExp is a **hippocampus for AI agents**. It captures every decision a human + AI agent make together, links those decisions into trajectories, and grades the trajectories retroactively when a real-world outcome arrives — a deal closes, an email gets a reply, a sprint ships, a payment lands.

The output is a **continuously growing labeled dataset** of human-AI decisions tied to grounded outcomes. The eventual product is a model fine-tuned on this dataset that develops intuition about how decisions in a given domain typically play out.

**Why this matters:** Today's AI agents don't learn from their outcomes. You can write skills and CLAUDE.md instructions all day — but if approach A closed a deal yesterday and approach B failed, your agent does not know that. There is no feedback loop from result back to instruction. OpenExp builds that feedback loop, but does it the right way: by collecting raw trajectories first and grading them only when reality returns its verdict.

## What OpenExp Is NOT

- Not Mem0 / Zep / Letta — those are storage layers. Storage is the easy part.
- Not a Q-learning system. We tried Q-values for 8 months. They didn't work — mean Q-value across 27,000 memories was 0.006, and 90% of memories never received any reward signal. We removed it on 2026-04-26.
- Not a magic-number scoring system. The new architecture is explicit and human-readable: trajectories with terminal grades, no implicit weights to tune.
- Not a replacement for skills or CLAUDE.md. Those say *how* to do something. OpenExp captures *what happened* and *how it ended*.

## The Core Methodological Principle

**No pre-labeling.** When we capture a step in a trajectory, we store it raw — the actual message, the actual action, the actual context. We do NOT annotate it with hand-crafted features like "this email had urgent tone" or "this signal was probably positive." Pre-labeling injects the labeler's biases into the dataset and corrupts the eventual training signal.

Only the terminal outcome gets a label: did the trajectory end well or badly, and on a 0–1 scale, how well?

This is the same hygiene a credit-scoring team uses: collect rich features per loan applicant, then label only the terminal outcome (paid / didn't pay), then let the model learn what predicts repayment. We don't pre-label "this applicant *seems* trustworthy."

Analogy that works in casual writing: kids in school don't get annotations on every homework problem ("this approach is right"). They turn in work, get a grade at the end of the term, and through hundreds of grades develop intuition about what works.

## Current Status (be honest in copy)

- Pilot stage. Architecture freeze landed 2026-04-26.
- 26,964 memories already in Qdrant from 6+ months of real usage.
- First end-to-end graded trajectory: not yet built (planned within 1 week of freeze).
- v1 site (current `welabeldata.com/openexp/`) describes deprecated architecture and should be considered out of date.

## CRITICAL: Sources of Truth

This brief is the ONLY source of truth for the page content. Two existing artifacts are out of date and must NOT be used as factual sources:

- **GitHub README** at `github.com/anthroos/openexp` — still describes the v1 Q-learning architecture removed on 2026-04-26. Do NOT pull facts, copy, or framing from it.
- **Existing `welabeldata.com/openexp/` page** — same problem. Match its cadence/voice if useful, but replace its substance entirely.

If you need a fact that is not in this brief, ask the founder — do not pull from the repo or the existing page.

---

## The Deliverable: One Landing Page

A single static HTML page that replaces `welabeldata.com/openexp/`. Static markup, ready to drop into the existing Cloud Build deploy. One file ideally; if assets are needed, list them.

### Sections (top to bottom)

1. **Hero.** One sentence: "How did this happen? — a hippocampus for AI agents." One supporting paragraph framing the trajectory + grade idea.
2. **The problem.** AI agents don't learn from outcomes. Skills and instructions are static. Storage tools (Mem0, Zep) don't solve this — they just remember more.
3. **The approach.** Trajectories + terminal grading + raw data, no pre-labeling. School-grade analogy works here.
4. **What's actually built.** Honest current state: 4 hooks, 5 MCP tools (`search_memory`, `add_memory`, `log_prediction`, `log_outcome`, `memory_stats`), 26,964 raw memories, pilot stage.
5. **The schema.** A code block showing the trajectory YAML from the redesign doc, verbatim. This is the credibility anchor — concrete, readable, not handwaved.
6. **Where it's going.** ≥30 graded trajectories → first scorer baseline (logistic regression) → eventually fine-tuned domain models.
7. **CTA.** GitHub link (`github.com/anthroos/openexp`) + a simple contact form ("Interested? Leave your email.") that posts to a collector the founder will set up.

### Tone

Technical credibility, no hype. The v1 page overshoots on claims; v2 should err the other direction. Direct, willing-to-name-what-didn't-work. Founder voice (Ivan): blunt, conversational, occasionally self-deprecating about what we got wrong.

Calibrate voice against:
- `welabeldata.com/blog/openexp-memory-benchmark/` (long-form Ivan voice)
- `github.com/anthroos/openexp` README (technical baseline)
- Current `welabeldata.com/openexp/` (recognizable cadence, but copy substance is wrong — match cadence, replace substance)

### Visuals

One main diagram: sources → trajectory linker → grading → dataset → eventual fine-tuned model. Not a marketing flowchart — something that would survive a technical review.

### Brand constraints

- Stay consistent with `welabeldata.com` — quiet, type-led, near-monochrome with a single accent
- Do NOT copy the existing `welabeldata.com/openexp/` page — it was engineered for the v1 Q-learning narrative we're killing
- Reference `welabeldata.com` homepage and blog for tone, not the existing OpenExp page

### Constraints on copy

- DO NOT use "memory" or "AI memory" as primary framing — those words are commoditized
- DO use "hippocampus", "decisioning data layer", "trajectory grading"
- Code snippets showing the trajectory schema are encouraged (use the schema verbatim from the redesign doc)
- No animated gradients, no product-launch hype, no "we are excited to announce" energy
- No multi-page information architecture
- No hi-fi mockup — actual static HTML markup is the deliverable

---

## Operating Mode

Start with hero + problem section as the first small batch. Show the founder before going further. Surface decisions that need founder input as questions — not as completed work he has to override.

When you're unsure whether a claim can be backed by code, default to leaving it out. The v1 site overpromised; we are recovering trust.

## Key References

- Repo: `github.com/anthroos/openexp`
- Architecture freeze: `docs/redesign-2026-04-26.md` (full technical detail; the trajectory schema lives there — read before drafting technical copy)
- Current (out-of-date) site: `welabeldata.com/openexp/`
- Founder: Ivan Pasichnyk — `ivan@welabeldata.com`
- Founder context: runs welabeldata.com (data labeling company, 7+ active clients), based in San Francisco, deep expertise in supervised dataset construction
