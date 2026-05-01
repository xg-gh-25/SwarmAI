# Steeringify v2 — Instructions

Mine EVOLUTION.md corrections for recurring patterns and propose STEERING.md rules.
Uses structured Pattern-field extraction with C-entry cross-reference graph (no keyword clustering).

## When to Use

- **On-demand:** user says `/steeringify` or "extract steering rules"
- **Auto-triggered:** DistillationTriggerHook runs after writing new corrections → writes `steeringify_proposals.json` → next session briefing surfaces them

## How It Works

1. **Pattern-field extraction:** Parses C-entries, extracts bold prescriptive rules from `- **Pattern**:` fields
2. **Cross-reference graph:** Detects explicit references ("same as C007", "Related: C005", "C007's 4th recurrence") and builds a connected-component graph
3. **Group by graph:** Connected components become one proposal each. No keyword similarity — only explicit references count
4. **Effectiveness tracking:** Detects when a correction group re-raises an issue already covered by an existing STEERING.md rule → flags as violation

## Workflow

### Step 1: Extract Corrections

```python
from skills.s_steeringify.steeringify import extract_corrections

evolution_path = Path("~/.swarm-ai/SwarmWS/.context/EVOLUTION.md").expanduser()
entries = extract_corrections(evolution_path.read_text())
```

Returns `CorrectionEntry` objects with: id, date, correction text, pattern text, bold_rules, cross_refs, status.

### Step 2: Group and Propose

```python
from skills.s_steeringify.steeringify import group_and_propose

steering_text = steering_path.read_text() if steering_path.exists() else ""
proposals = group_and_propose(entries, min_group_size=2, steering_text=steering_text)
```

### Step 3: Present to User

Show each proposal with context:

```
📋 Steeringify found N rule proposals from recurring corrections:

1. **Tool failure → exhaust alternatives** (C007, C012)
   Confidence: 0.70 | ⚠️ Violation: rule exists but C012 re-raised the issue
   "ANY tool failure triggers a 3-attempt alternative search before reporting."

2. **Verify before asserting architecture facts** (C005, C008, C010)
   Confidence: 0.85
   "Architecture topology questions MUST be verified against code."

Approve which rules? (all / 1,2 / none)
```

### Step 4: Write Approved Rules

```python
from skills.s_steeringify.steeringify import write_approved_rules

approved = [p for i, p in enumerate(proposals) if i in approved_indices]
count = write_approved_rules(approved, steering_path)
```

## Rules

- Max 10 active steeringify rules in STEERING.md at any time
- Every rule must have C-entry provenance (Source: C-IDs)
- User approval is mandatory — never auto-write
- Violations (rule exists but correction recurred) are flagged, not auto-fixed
- Rules already in AGENT.md are flagged but can still be added to STEERING.md

## Output Format in STEERING.md

```markdown
### Tool failure → exhaust alternatives
> Source: C007, C012 | Added: 2026-04-30 | Confidence: 0.85

**ANY tool failure triggers a 3-attempt alternative search before reporting to the user.** When ANY tool or operation fails: (1) Try Bash/Python, (2) Try a different tool, (3) Try a workaround. Only after ALL alternatives exhausted, tell the user.
```

## Auto-Trigger Integration

The `DistillationTriggerHook._check_steeringify_proposals()` method:
1. Calls `extract_corrections()` + `group_and_propose()` after new C-entries written
2. Writes `steeringify_proposals.json` to `.context/` for session briefing
3. Best-effort — never blocks distillation on failure
