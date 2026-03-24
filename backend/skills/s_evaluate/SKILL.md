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
- Score: +1 if proven pattern exists, -1 if past attempt failed, 0 if no history

**Current Priority** (PROJECT.md):
- 5: Directly unblocks current focus
- 4: Supports current sprint
- 3: Important but not current
- 2: Nice to have
- 1: Conflicts with / distracts from current work

**ROI Formula:**
```
ROI = (Strategic * 0.35) + (Current_Priority * 0.25) + (Historical * 0.15)
      - (Inverse_Feasibility * 0.25)

where Inverse_Feasibility = 6 - Feasibility (higher cost = lower ROI)
```

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
| **GO** | ROI >= 3.5, no blockers | Define scope + acceptance criteria. Advance pipeline to THINK. |
| **DEFER** | ROI 2.0-3.4, or blocked by current priorities | Add to PROJECT.md backlog with reasoning. |
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
| **ROI** | **3.4** | |

### Recommendation: GO

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
  "scope": "...",
  "acceptance_criteria": ["...", "..."],
  "escalation_questions": [],
  "suggested_pipeline": ["think", "plan", "build", "test"]
}
```

## Escalation Protocol Integration

Use the escalation protocol levels (L0/L2) for transparent decision-making:

### L0 INFORM (pipeline continues, FYI only)

When the evaluation is clear and confident (ROI unambiguous), prefix your
recommendation with `[INFORM]` and proceed:

```markdown
> [INFORM] **GO: Build payment retry** — ROI 4.1, directly serves priority #1,
> proven pattern from IMPROVEMENT.md, 2-session effort.
> Evidence: PRODUCT.md "checkout reliability is #1", IMPROVEMENT.md "saga pattern worked for order flow"
```

### L2 BLOCK (pipeline pauses, needs human input)

ESCALATE instead of GO/DEFER/REJECT when ANY of these are true:

- **Ambiguous scope**: Can't determine what "done" looks like
- **Conflicting requirements**: PRODUCT.md says X, TECH.md says not-X
- **High-risk decision**: Architecture change, data migration, public API change
- **Low confidence**: ROI score between 2.0-3.5 AND historical score is negative
- **Resource contention**: PROJECT.md shows too many open items already
- **Missing information**: Can't answer 2+ of the 4 questions

When escalating, use this format:

```markdown
> [BLOCK] **Cannot evaluate: ambiguous scope**
>
> I need your input before proceeding. Here's what I know and what I don't:
>
> **Known:** Strategic alignment is high (4/5), PRODUCT.md confirms this is priority #2.
> **Unknown:** "Improve performance" — of what? API latency? UI render time? Build speed?
> **What would change the answer:** If API latency, ROI = GO (proven pattern). If UI render, ROI = DEFER (needs research first).
>
> **Options:**
> 1. Focus on API latency (my recommendation if forced to choose)
> 2. Focus on UI render time
> 3. Let me research both before deciding
```

### Escalation to Artifact (L1+ with project)

When escalating with a project, also publish a partial evaluation artifact
so the context is preserved for async resolution:

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
