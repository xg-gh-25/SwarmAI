---
name: Evaluate
description: >
  Evaluate requirements, feature requests, and task intake against project DDD
  context. Produces a GO/DEFER/REJECT/ESCALATE recommendation with ROI scoring,
  scope definition, and acceptance criteria.
  TRIGGER: "evaluate this request", "should we build this", "assess this requirement",
  "triage this", "is this worth doing", "prioritize this".
  DO NOT USE: for existing tasks already in progress (just build), or for
  pure research without a decision gate (use deep-research).
  SIBLINGS: deep-research = investigate without judging | alternatives = compare approaches |
  evaluate = judge whether to proceed at all.
consumes_artifacts: [research]
produces_artifact: evaluation
tier: always
---
# Requirement Evaluation

The "should we?" gate for the lifecycle pipeline. Evaluates any incoming
requirement, feature request, or task against the 4 DDD questions before
committing pipeline resources.

Works at L0 (structures any request with effort/impact). Gets autonomous
judgment at L2 (DDD docs provide strategic alignment, feasibility, and history).

## The 4 Questions

Every evaluation answers these, in order:

| # | Question | Source | Without DDD |
|---|----------|--------|-------------|
| 1 | **Should we do this?** | PRODUCT.md (strategic alignment) | Ask the user |
| 2 | **Can we do this?** | TECH.md (feasibility, constraints) | Estimate from request |
| 3 | **Have we tried this?** | IMPROVEMENT.md (past lessons) | No historical context |
| 4 | **Should we do it now?** | PROJECT.md (current priorities) | Assume yes |

## Workflow

### Step 1: Parse the Request

From the user's message, extract:
- **What:** one-sentence description of the requirement
- **Why:** stated motivation or inferred business value
- **Who:** who benefits (end user, developer, internal team)
- **Constraints:** deadlines, dependencies, blockers mentioned

If the request is too vague to parse (e.g., "improve things"), ESCALATE immediately:
> "I need more specifics to evaluate this. What specifically should improve, and what would success look like?"

### Step 2: Score (L2 with DDD docs)

**Read the DDD docs and score each dimension 1-5:**

**Strategic Alignment** (PRODUCT.md):
- 5: Directly serves #1 priority
- 4: Serves top-3 priorities
- 3: Aligned but not priority
- 2: Tangentially related
- 1: Not aligned / conflicts with non-goals

**Feasibility** (TECH.md):
- 5: Trivial — existing pattern, < 1 session
- 4: Straightforward — known approach, 1-2 sessions
- 3: Moderate — some unknowns, 2-4 sessions
- 2: Hard — significant unknowns or new patterns, 4+ sessions
- 1: Very hard — architectural change, cross-cutting, weeks

**Historical Lessons** (IMPROVEMENT.md):
- Check "What Failed" for similar past attempts
- Check "What Worked" for applicable patterns
- Check "Known Issues" for related problems
- Score 1-5:
  - 5: Strong proven pattern — same approach succeeded before
  - 4: Related pattern exists — similar approach worked
  - 3: No history — neutral (default)
  - 2: Weak negative signal — partial failure or abandoned attempt
  - 1: Strong negative — same approach tried and failed

**Current Priority** (PROJECT.md):
- 5: Directly unblocks current focus
- 4: Supports current sprint
- 3: Important but not current
- 2: Nice to have
- 1: Conflicts with / distracts from current work

**ROI Formula:**
```
ROI = (Strategic * 0.35) + (Current_Priority * 0.25) + (Historical * 0.15) + (Feasibility * 0.25)
```

Range: [1.0, 5.0]. Higher feasibility = easier = higher ROI. All dimensions on 1-5 scale.

### Step 3.5: Pre-mortem (Mandatory for GO candidates)

**If the initial ROI >= 3.2 (GO candidate), run a pre-mortem before confirming:**

> "It's 2 weeks later. This feature shipped but is considered a failure.
> What are the 3 most likely reasons it failed?"

**Rules:**
- Each reason must be **specific** — not "it was too complex" (vague), but "the HTML structure changed and the regex parser returned 0 results for 3 days before anyone noticed" (specific)
- At least 1 reason must reference IMPROVEMENT.md "What Failed" — check if a similar approach was tried before
- At least 1 reason must challenge an **assumption** in the scoring — which dimension assumed something unverified?

**Output:**

| # | Failure Reason | Likelihood | Mitigation |
|---|---------------|-----------|------------|
| 1 | <specific scenario> | high/med/low | <how to prevent or detect> |
| 2 | <specific scenario> | high/med/low | <how to prevent or detect> |
| 3 | <specific scenario> | high/med/low | <how to prevent or detect> |

**Decision impact:**
- If any reason has **likelihood=HIGH and no mitigation exists** → downgrade to **ESCALATE**, surface the risk to user
- If pre-mortem reveals a scoring assumption was unverified → **reduce that dimension by 1** and recalculate ROI. If new ROI < 3.2 → DEFER
- If all reasons are med/low with clear mitigations → GO confirmed

**Why this exists:** EVALUATE has happy-path bias (LL09, 3 recurrences). Pre-mortem (Gary Klein) generates 30% more specific failure reasons than "argue against" because "imagine it failed" is concrete, "argue why not" is abstract. Same agent, same pass, one extra section — zero architecture change.

**Skip when:** ROI < 3.2 (already DEFER/REJECT — no need to argue against a NO).

### Step 3: At L0 (No DDD Docs)

Skip scoring. Instead, structure the request:

```markdown
## Evaluation: <requirement title>

### What
<one-sentence description>

### Effort Estimate
<T-shirt size: S/M/L/XL based on request complexity>

### Impact Estimate
<T-shirt size: S/M/L/XL based on stated motivation>

### Questions Before Proceeding
1. <what's unclear>
2. <what could go wrong>
3. <what's the success criteria>

### Recommendation
<GO / DEFER / ESCALATE with reasoning>
```

### Step 4: Produce Recommendation

Based on ROI score (L2) or structured analysis (L0):

| Recommendation | When | Action |
|---------------|------|--------|
| **GO** | ROI >= 3.2, no blockers | Define scope + acceptance criteria. Advance pipeline to THINK. |
| **DEFER** | ROI 2.0-3.1, or blocked by current priorities | Add to PROJECT.md backlog with reasoning. |
| **REJECT** | ROI < 2.0, or conflicts with non-goals | Explain why. Suggest alternative if one exists. |
| **ESCALATE** | Ambiguous scope, conflicting signals, or confidence < 0.6 | Surface specific questions to user. Don't guess. |

### Step 5: Output

**Present to user:**

```markdown
## Evaluation: <requirement title>

### Scores (L2 only)
| Dimension | Score | Rationale |
|-----------|-------|-----------|
| Strategic Alignment | 4/5 | Serves priority #2 (self-evolution) |
| Feasibility | 3/5 | Moderate — needs new pattern, ~3 sessions |
| Historical | +1 | Similar approach worked for context loading |
| Current Priority | 3/5 | Important but not blocking current focus |
| **ROI** | **3.5** | |

### Recommendation: GO

### Pre-mortem (GO candidates only)
| # | Failure Reason | Likelihood | Mitigation |
|---|---------------|-----------|------------|
| 1 | <specific scenario> | med | <mitigation> |
| 2 | <specific scenario> | low | <mitigation> |
| 3 | <specific scenario> | low | <mitigation> |

Score adjustment: none (no HIGH without mitigation)

### Scope
<what's included and what's excluded>

### Acceptance Criteria
1. <criterion 1>
2. <criterion 2>
3. <criterion 3>

### Suggested Pipeline
Think: research <topic>
Plan: design with 3 alternatives
Build: implement chosen approach
Test: QA against acceptance criteria
```

**Publish as artifact (L1+):**
```json
{
  "requirement": "...",
  "scores": {
    "strategic": 4,
    "feasibility": 3,
    "historical": 1,
    "priority": 3,
    "roi": 3.4
  },
  "recommendation": "GO",
  "pre_mortem": [
    {"reason": "...", "likelihood": "med", "mitigation": "..."},
    {"reason": "...", "likelihood": "low", "mitigation": "..."},
    {"reason": "...", "likelihood": "low", "mitigation": "..."}
  ],
  "scope": "...",
  "acceptance_criteria": ["...", "..."],
  "escalation_questions": [],
  "suggested_pipeline": ["think", "plan", "build", "test"]
}
```

## Escalation Protocol Integration

Use the escalation protocol (`backend/core/escalation.py`) for transparent
decision-making at every evaluation. Three levels, three outcomes:

### L0 INFORM — "Clear call, FYI"

**When:** ROI >= 3.2 AND confidence is high AND no blockers.

Emit an INFORM annotation and continue the pipeline:

```python
from core.escalation import inform, build_sse_event, save_escalation
esc = inform(
    title="GO: Build payment retry",
    situation="ROI 4.1, directly serves priority #1, proven pattern from IMPROVEMENT.md.",
    trigger="CLEAR_EVALUATION",
    pipeline_stage="evaluate",
    project="<PROJECT>",
    evidence=["PRODUCT.md: checkout reliability is #1", "IMPROVEMENT.md: saga pattern worked"],
)
save_escalation(WORKSPACE_ROOT, esc)
```

**Triggers:** `CLEAR_EVALUATION`

### L1 CONSULT — "I think X, override within 24h"

**When:** ROI 2.5-3.1 (borderline) OR confidence medium OR non-obvious tradeoff.

Swarm proceeds with its recommendation. Human has 24h to override via Radar todo.

```python
from core.escalation import consult, Option, build_sse_event, save_escalation, create_radar_todo
esc = consult(
    title="DEFER recommended: UI performance investigation",
    situation="ROI 2.8 — aligned with priorities but effort is high (architectural). Historical: no prior attempt. Proceeding with DEFER unless overridden.",
    options=[
        Option(label="DEFER (recommended)", description="Add to backlog, revisit next sprint", risk="low", is_recommendation=True),
        Option(label="GO", description="Start research phase now", risk="medium"),
        Option(label="Discuss", description="I need more context from you"),
    ],
    trigger="LOW_CONFIDENCE_ROI",
    recommendation="DEFER — effort-to-impact ratio doesn't justify immediate action",
    pipeline_stage="evaluate",
    project="<PROJECT>",
    evidence=["PRODUCT.md: priority #3", "TECH.md: requires new pattern"],
    timeout_hours=24,
)
save_escalation(WORKSPACE_ROOT, esc)
create_radar_todo(esc)
```

**Triggers:** `LOW_CONFIDENCE_ROI`, `CONFLICTING_PRIORITIES`

### L2 BLOCK — "I'm stuck, need your input"

**When:** ANY of these are true:
- **Ambiguous scope**: Can't determine what "done" looks like
- **Conflicting requirements**: PRODUCT.md says X, TECH.md says not-X
- **Missing information**: Can't answer 2+ of the 4 questions
- **High-risk decision**: Architecture change, data migration, public API change
- **Resource contention**: PROJECT.md shows too many open items

Pipeline PAUSES. Creates a high-priority Radar todo.

```python
from core.escalation import block, Option, build_sse_event, save_escalation, create_radar_todo
esc = block(
    title="Cannot evaluate: ambiguous scope",
    situation="'Improve performance' — of what? API latency, UI render, or build speed? Each leads to a different recommendation.",
    options=[
        Option(label="Focus on API latency", description="Proven pattern, 2 sessions", risk="low", is_recommendation=True),
        Option(label="Focus on UI render", description="Needs research first, 4+ sessions", risk="medium"),
        Option(label="Research both first", description="1-session investigation, then decide"),
        Option(label="Discuss", description="Let me explain more context"),
    ],
    trigger="AMBIGUOUS_SCOPE",
    recommendation="API latency — if forced to choose",
    pipeline_stage="evaluate",
    project="<PROJECT>",
    evidence=["PRODUCT.md: 'performance' listed but not specified"],
)
save_escalation(WORKSPACE_ROOT, esc)
create_radar_todo(esc)
```

**Triggers:** `AMBIGUOUS_SCOPE`, `CONFLICTING_PRIORITIES`, `MISSING_INFORMATION`

### Escalation to Artifact

When escalating with a project (L1/L2), also publish a partial evaluation
artifact so context is preserved for async resolution:

```bash
python backend/scripts/artifact_cli.py publish \
  --project <PROJECT> --type evaluation --producer s_evaluate \
  --summary "ESCALATE: <reason>" \
  --data '{"recommendation": "ESCALATE", "escalation_questions": ["..."], "partial_scores": {...}}'
```

## Rules

- **Never auto-GO on architectural changes** — always ESCALATE for human review
- **Never REJECT without explaining why** — the user deserves reasoning
- **DEFER is not REJECT** — deferred items get logged in PROJECT.md for future triage
- **L0 evaluation is still valuable** — structuring a vague request IS the evaluation
- **Don't over-score** — 3/5 is the default. 5/5 requires strong evidence.

## Artifact Operations

**Discover prior research (before scoring):**
```bash
python backend/scripts/artifact_cli.py discover --project <PROJECT> --types research --full
```

**Publish evaluation (after presenting to user):**
```bash
python backend/scripts/artifact_cli.py publish \
  --project <PROJECT> --type evaluation --producer s_evaluate \
  --summary "<GO/DEFER/REJECT/ESCALATE>: <one-line rationale>" \
  --data '<JSON of evaluation output>'
```

**Advance pipeline:**
```bash
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state think
```

## Verification

Before marking this task complete, show evidence for each:

- [ ] **ROI score calculated** — numeric ROI shown (or T-shirt sizing at L0) with per-dimension scores and rationale
- [ ] **Pre-mortem completed** — 3 specific failure reasons with likelihood and mitigation (for GO candidates; skip for DEFER/REJECT)
- [ ] **Recommendation stated** — explicit GO / DEFER / REJECT / ESCALATE with reasoning tied to scores (and pre-mortem if applicable)
- [ ] **Acceptance criteria defined** — numbered, testable criteria for what "done" looks like (GO) or clear rationale for deferral/rejection
- [ ] **Scope boundaries set** — what is included and what is explicitly excluded from the scope
- [ ] **Evaluation artifact published** — JSON artifact saved via artifact_cli (L1+) or structured output shown in chat (L0)
