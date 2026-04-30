# Steeringify — Instructions

Mine EVOLUTION.md corrections for recurring patterns and propose STEERING.md rules.

## When to Use

- On-demand: user says `/steeringify` or "extract steering rules"
- Auto-triggered: distillation pipeline detects ≥3 C-entries with recurrence ≥2

## Workflow

### Step 1: Extract Candidates

```python
from skills.s_steeringify.steeringify import extract_rule_candidates

evolution_path = Path("~/.swarm-ai/SwarmWS/.context/EVOLUTION.md").expanduser()
evolution_text = evolution_path.read_text()
candidates = extract_rule_candidates(evolution_text)
```

This parses all active C-entries and extracts bold rules from their Pattern fields.
Quality gate: only prescriptive rules (contains must/should/always/never/verify/etc).

### Step 2: Cluster and Filter

```python
from skills.s_steeringify.steeringify import cluster_and_filter

steering_path = Path("~/.swarm-ai/SwarmWS/.context/STEERING.md").expanduser()
steering_text = steering_path.read_text() if steering_path.exists() else ""

# Also check AGENT.md for rules already structural
agent_path = Path("~/.swarm-ai/SwarmWS/.context/AGENT.md").expanduser()
agent_text = agent_path.read_text() if agent_path.exists() else ""

proposals = cluster_and_filter(
    candidates,
    min_recurrence=2,
    steering_text=steering_text,
    agent_text=agent_text,
)
```

### Step 3: Present to User

Show each proposal with context:

```
📋 Steeringify found N rule proposals from recurring corrections:

1. **Tool failure → exhaust alternatives** (C007, C012)
   Confidence: 0.85
   "ANY tool failure triggers a 3-attempt alternative search before reporting."
   ⚠️ Already in AGENT.md — may not need a STEERING.md duplicate

2. **Verify before asserting architecture facts** (C005, C008, C010)
   Confidence: 0.85
   "Architecture topology questions MUST be verified against code or KNOWLEDGE.md."

Approve which rules? (all / 1,2 / none)
```

### Step 4: Write Approved Rules

```python
from skills.s_steeringify.steeringify import write_approved_rules

approved = [p for i, p in enumerate(proposals) if i in approved_indices]
count = write_approved_rules(approved, steering_path)
```

Rules are appended to STEERING.md Standing Rules section with provenance metadata.

## Rules

- Max 10 active steeringify rules in STEERING.md at any time
- Every rule must have C-entry provenance (Source: C-IDs)
- Rules already in AGENT.md are flagged but can still be added to STEERING.md
  (STEERING.md overrides AGENT.md defaults, per context priority)
- User approval is mandatory — never auto-write

## Output Format in STEERING.md

```markdown
### Tool failure → exhaust alternatives
> Source: C007, C012 | Added: 2026-04-30 | Confidence: 0.85

ANY tool failure triggers a 3-attempt alternative search before reporting to
the user: (1) Same goal via Bash/curl/Python. (2) Different tool. (3) Different
approach entirely.
```
