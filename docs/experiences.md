# Experiences

An **Experience** is a domain-specific reward profile that tells OpenExp what "productive" means for your workflow.

The default experience rewards coding outputs (commits, PRs, tests). But if your work is sales, devops, content creation, or research — the signals are different. Experiences let you define that.

## How It Works

After each Claude Code session, OpenExp computes a reward score: did this session accomplish something useful?

The reward depends on **which signals were detected** and **how much each signal is worth**. An Experience defines both.

```
Session ends → detect signals (commits? emails? proposals?)
    ↓
Apply weights from active Experience
    ↓
reward = sum(signal × weight) + base + penalties
    ↓
Update Q-values for all memories from this session
    ↓
Next session → memories from productive sessions rank higher
```

## Shipped Experiences

### `default` — Software Engineering

Optimized for coding workflows. Commits and PRs are the primary success signals.

| Signal | Weight | What triggers it |
|--------|--------|-----------------|
| `commit` | **+0.30** | `git commit` in session |
| `pr` | **+0.20** | `gh pr create` in session |
| `deploy` | +0.10 | "deploy" mentioned |
| `tests` | +0.10 | "test" + "pass" mentioned |
| `decisions` | +0.10 | Recorded decisions (type=decision) |
| `writes` | +0.02/file | Write/Edit calls (max +0.20) |
| `base` | -0.10 | Every session starts negative |
| `min_obs_penalty` | -0.05 | Session has < 3 observations |
| `no_output_penalty` | -0.10 | No writes and no commits |

**Good session:** edit files → commit → PR = **+0.42**
**Empty session:** just read files = **-0.20**

### `sales` — Sales & Deal Closing

Optimized for outreach, follow-ups, and deal progression.

| Signal | Weight | What triggers it |
|--------|--------|-----------------|
| `decisions` | **+0.20** | Strategic decisions recorded |
| `email_sent` | **+0.15** | "email" + "sent" in session |
| `follow_up` | **+0.10** | "follow" + "up" in session |
| `commit` | +0.05 | Git commit (minor) |
| `pr` | +0.05 | Pull request (minor) |
| `writes` | +0.01/file | File edits (minor) |
| `base` | -0.05 | Mild start penalty |

Also enables CRM outcome resolver and boosts decision/outcome memories in retrieval.

### `dealflow` — Deal Pipeline (Lead → Payment)

Optimized for the full deal lifecycle: outreach → discovery → NDA → proposal → negotiation → invoice → payment.

| Signal | Weight | What triggers it |
|--------|--------|-----------------|
| `payment_received` | **+0.30** | "payment" + "received" — terminal reward |
| `proposal_sent` | **+0.25** | "proposal" mentioned |
| `invoice_sent` | **+0.20** | "invoice" mentioned |
| `call_scheduled` | **+0.15** | "calendar" or "scheduled" mentioned |
| `email_sent` | **+0.15** | "email" + "sent" |
| `follow_up` | **+0.15** | "follow" + "up" |
| `decisions` | **+0.15** | Recorded decisions |
| `nda_exchanged` | **+0.10** | "nda" or "agreement" mentioned |
| `commit` | +0.05 | Git commit (support) |
| `pr` | +0.02 | Pull request (support) |
| `base` | -0.05 | Mild start penalty |
| `min_obs_penalty` | -0.03 | Very mild — sales sessions are often short |
| `no_output_penalty` | -0.05 | Mild — an email counts more than a file |

Learning rate `alpha=0.30` (faster than default 0.25) because deals move fast and old context loses relevance quickly.

## Activating an Experience

Set the environment variable before starting Claude Code:

```bash
# In your .env or shell profile
export OPENEXP_EXPERIENCE=dealflow
```

Or per-session:
```bash
OPENEXP_EXPERIENCE=dealflow claude
```

Check active experience:
```bash
openexp experience list
openexp experience info          # shows active + weights
```

## Creating Your Own Experience

### Step 1: Answer These Questions

**What is a "productive session" for you?**

Rate each action 0–10 (how important is it as a signal of real progress):

| Action | Your Rating |
|--------|-------------|
| Committed code to git | ___ |
| Created a Pull Request | ___ |
| Edited/created files | ___ |
| Deployed to production | ___ |
| Tests passed | ___ |
| Recorded a decision | ___ |
| Sent an email | ___ |
| Made a follow-up | ___ |
| Sent a proposal | ___ |
| Sent an invoice | ___ |
| Scheduled a call | ___ |
| Exchanged NDA/agreement | ___ |
| Payment received | ___ |

**How strict should penalties be?**

- **Lenient** (research, exploration sessions are normal) → `base: -0.03`
- **Moderate** (most sessions should produce something) → `base: -0.05`
- **Strict** (no output = wasted time) → `base: -0.10` or more

**How fast does your domain change?**

- **Fast** (sales, news) → `alpha: 0.30` — learn fast, forget fast
- **Normal** (engineering) → `alpha: 0.25` — balanced
- **Slow** (research, legal) → `alpha: 0.15` — accumulate gradually

**Which memory types matter most?**

- `decision` — strategic choices (boost: 1.2–1.3×)
- `outcome` — results of past actions (boost: 1.1–1.2×)
- `fact` — domain knowledge (boost: 1.0–1.1×)
- `action` — what was done (usually no boost needed)

### Step 2: Create the YAML

Save as `~/.openexp/experiences/{name}.yaml` (user-level) or contribute to `openexp/data/experiences/` (shipped).

```yaml
name: my-experience
description: One-line description of what this optimizes for
session_reward_weights:
  # Map your 0-10 ratings to weights (0.0 to 0.30 range)
  # 10 → 0.30, 8 → 0.25, 5 → 0.15, 3 → 0.05, 0 → 0.0
  commit: 0.05
  pr: 0.02
  writes: 0.01
  deploy: 0.0
  tests: 0.0
  decisions: 0.20
  email_sent: 0.15
  follow_up: 0.10
  proposal_sent: 0.25
  invoice_sent: 0.20
  call_scheduled: 0.15
  nda_exchanged: 0.10
  payment_received: 0.30
  base: -0.05
  min_obs_penalty: -0.03
  no_output_penalty: -0.05
outcome_resolvers: []           # or ["openexp.resolvers.crm_csv:CRMCSVResolver"]
retrieval_boosts:
  decision: 1.3                 # boost decision memories in search
  outcome: 1.2
q_config_overrides:
  alpha: 0.25                   # learning rate
```

### Step 3: Activate

```bash
export OPENEXP_EXPERIENCE=my-experience
```

Verify:
```bash
openexp experience list
# Should show your experience in the list
```

### Rating → Weight Conversion

| Your Rating (0–10) | Weight | Meaning |
|---------------------|--------|---------|
| 10 | 0.30 | This IS the goal |
| 8 | 0.25 | Major success signal |
| 6 | 0.15 | Important but not primary |
| 4 | 0.10 | Contributes to progress |
| 2 | 0.05 | Minor, supporting action |
| 0 | 0.00 | Not relevant to this workflow |

**Constraint:** Total positive weights should sum to roughly 0.8–1.2. Too high → everything is max reward. Too low → nothing registers as productive.

## Available Signals

These are the signals OpenExp can detect from Claude Code sessions:

| Signal Key | Detection Logic | Example |
|------------|----------------|---------|
| `commit` | `"git commit"` in tool output | `git commit -m "fix auth"` |
| `pr` | `"gh pr"` in tool output | `gh pr create --title "..."` |
| `writes` | Count of Write/Edit tool calls | Edited 5 files |
| `deploy` | `"deploy"` in tool output | `gcloud deploy`, `npm run deploy` |
| `tests` | `"test"` + `"pass"` in tool output | `pytest: 42 passed` |
| `decisions` | Observations with type=`decision` | `add_memory("chose X", type="decision")` |
| `email_sent` | `"email"` + `"sent"` in tool output | `send_email.py --to client` |
| `follow_up` | `"follow"` + `"up"` in tool output | Follow-up email sent |
| `proposal_sent` | `"proposal"` in tool output | Created and sent proposal PDF |
| `invoice_sent` | `"invoice"` in tool output | Generated invoice #101 |
| `call_scheduled` | `"calendar"` or `"scheduled"` in tool output | Created calendar event |
| `nda_exchanged` | `"nda"` or `"agreement"` in tool output | Reviewed and signed NDA |
| `payment_received` | `"payment"` + `"received"` in tool output | Payment $3120 received |

### Adding Custom Signals

To add a new signal, edit `openexp/ingest/reward.py`:

```python
# In compute_session_reward(), add after existing signals:
if any("your_keyword" in s.lower() for s in summaries):
    score += weights.get("your_signal_key", 0.0)
```

Then reference `your_signal_key` in your experience YAML with a weight.

## Examples

### DevOps Engineer

Focus: deploys, monitoring, infrastructure reliability.

```yaml
name: devops
description: Infrastructure reliability — deploys and tests are the goal
session_reward_weights:
  deploy: 0.30
  tests: 0.25
  commit: 0.10
  decisions: 0.10
  pr: 0.05
  writes: 0.01
  base: -0.10
  min_obs_penalty: -0.05
  no_output_penalty: -0.10
retrieval_boosts:
  outcome: 1.2
q_config_overrides: {}
```

### Content Creator

Focus: writing, publishing, audience engagement.

```yaml
name: content
description: Content production — writing and publishing are the goal
session_reward_weights:
  writes: 0.05           # higher per-file (content = files)
  commit: 0.10           # publishing to repo
  decisions: 0.15        # editorial decisions
  email_sent: 0.10       # distribution
  deploy: 0.20           # publishing live
  base: -0.03            # mild — research sessions are OK
  min_obs_penalty: -0.02
  no_output_penalty: -0.03
retrieval_boosts:
  decision: 1.2
q_config_overrides:
  alpha: 0.20            # content knowledge ages slowly
```

### Researcher

Focus: reading, understanding, recording insights.

```yaml
name: research
description: Research and analysis — decisions and insights are the goal
session_reward_weights:
  decisions: 0.30        # insights = primary output
  writes: 0.03           # notes, papers
  commit: 0.05           # version control for papers
  tests: 0.05            # experiment validation
  base: -0.02            # very mild — reading sessions are normal
  min_obs_penalty: 0.0   # short sessions are fine
  no_output_penalty: -0.02
retrieval_boosts:
  decision: 1.3
  fact: 1.2              # domain knowledge matters
q_config_overrides:
  alpha: 0.15            # research knowledge is durable
```

## How Experiences Affect Q-Values

Different experiences maintain **separate Q-values** for the same memory. A memory about "project uses PostgreSQL" might have:

- `default` experience: Q=0.7 (useful for coding sessions)
- `sales` experience: Q=0.1 (rarely useful for sales)
- `dealflow` experience: Q=0.0 (never relevant)

When you switch experiences, the retrieval ranking changes because Q-values (30% of the score) come from the active experience.

## File Locations

| Location | Priority | Use |
|----------|----------|-----|
| `~/.openexp/experiences/` | 1st (highest) | User-created experiences |
| `openexp/data/experiences/` | 2nd | Shipped with OpenExp |
| Hardcoded `DEFAULT_EXPERIENCE` | 3rd (fallback) | Always available |

User-level files override shipped ones with the same name.
