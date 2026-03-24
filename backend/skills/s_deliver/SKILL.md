---
name: Deliver
description: >
  Package pipeline outputs into structured deliverables: artifact bundles,
  PR descriptions, decision logs, attention flags, and delivery reports.
  Bridges .artifacts/ (working memory) to Knowledge/ (long-term memory).
  TRIGGER: "deliver this", "package for review", "create delivery report",
  "wrap up this feature", "prepare for handoff", "what did we build".
  DO NOT USE: for ongoing work (just keep building), or for shipping/deploying
  code (future ship skill).
  SIBLINGS: code-review = review code quality | qa = test quality |
  deliver = package everything for handoff.
consumes_artifacts: [evaluation, research, alternatives, design_doc, changeset, review, test_report]
produces_artifact: delivery
---

# Delivery Packaging

The terminal stage of the lifecycle pipeline. Assembles all artifacts from a
pipeline run into a structured deliverable that humans (or future pipeline runs)
can review, approve, and act on.

Works at L0 (generates a session summary report). Full artifact bundling at L1+.

## What Delivery Produces

| Output | Where It Goes | Who It's For |
|--------|-------------|-------------|
| Delivery Report (markdown) | Chat + optional `Knowledge/Reports/` | Human review |
| PR Description | Clipboard / chat | Code reviewers |
| Decision Log | Appended to `PROJECT.md` | Future sessions |
| Attention Flags | Chat + optional Radar Todo | Human action items |
| Updated PROJECT.md | `Projects/<name>/PROJECT.md` | Next session context |
| Updated IMPROVEMENT.md | `Projects/<name>/IMPROVEMENT.md` | Learning loop |
| delivery artifact | `.artifacts/delivery-*.json` | Pipeline completion marker |

## Workflow

### Step 1: Gather Artifacts

Collect all artifacts from the current pipeline run:

**L0 (no project):**
- Scan the current session for: code changes, decisions made, issues found
- No artifacts to read — derive from conversation

**L1+ (project with .artifacts/):**
- Read `manifest.json` for all artifacts in the current pipeline run
- Load each active artifact's summary and key data points
- Check pipeline state — delivery should be the terminal state

```
Artifacts to collect (in pipeline order):
  evaluation  -> scope, acceptance criteria, ROI score
  research    -> key findings, sources
  alternatives -> chosen approach, rejected approaches + reasons
  design_doc  -> decisions, API contract, data model
  changeset   -> files changed, commits, branch
  review      -> findings, approval status, security issues
  test_report -> pass/fail, bugs fixed, remaining issues
```

### Step 2: Assemble Delivery Report

```markdown
## Delivery Report: <feature/task title>

### Summary
<2-3 sentences: what was built, why, and current status>

### Pipeline Path
EVALUATE -> THINK -> PLAN -> BUILD -> REVIEW -> TEST -> DELIVER
(checkmarks for completed stages, X for skipped)

### What Was Built
- <key change 1: file/component + what changed>
- <key change 2>
- <key change 3>

### Key Decisions
| Decision | Rationale | Alternative Considered |
|----------|-----------|----------------------|
| <decision 1> | <why> | <what was rejected> |

### Quality Summary
- **Tests:** X passed, Y failed, Z fixed during QA
- **Security:** N findings (M auto-fixed, K reported)
- **Review:** <approval status>

### Unresolved Issues
1. <issue + file:line + severity>
2. <issue + diagnosis + suggested fix>

### Attention Flags (Human Review Needed)
- [ ] <item requiring human decision>
- [ ] <item requiring human review>

### Suggested Next Actions
1. <action 1>
2. <action 2>
```

### Step 3: Generate PR Description (if changeset exists)

```markdown
## Summary
<1-3 bullet points from delivery report>

## Changes
<file list from changeset artifact, grouped by domain>

## Test Results
<from test_report artifact>

## Design Decisions
<from design_doc artifact — key choices and rationale>

## Review Notes
<from review artifact — addressed findings>
```

### Step 4: Update Project Context

**PROJECT.md** — append to Recent Decisions:
```
- YYYY-MM-DD: <feature> delivered. Key decisions: <list>. Follow-ups: <list>.
```

**IMPROVEMENT.md** — the writeback hook handles this automatically, but
delivery can add high-level lessons:
```
- YYYY-MM-DD (delivery): <pattern that worked / failed across the full pipeline>
```

### Step 5: Publish Delivery Artifact

```json
{
  "title": "Feature X delivery",
  "status": "complete",
  "artifacts_included": ["evaluation", "design_doc", "changeset", "review", "test_report"],
  "summary": "...",
  "decisions": [{"decision": "...", "rationale": "..."}],
  "quality": {
    "tests_passed": 45,
    "tests_failed": 0,
    "security_findings": 2,
    "review_approved": true
  },
  "unresolved": [{"issue": "...", "severity": "medium", "suggestion": "..."}],
  "attention_flags": ["..."],
  "next_actions": ["..."]
}
```

### Step 6: Advance Pipeline State

```
advance_pipeline(project, "reflect")  # Terminal state
```

### Step 7: Offer to Save Report

Ask the user:
> "Want me to save this delivery report to `Knowledge/Reports/`?"

If yes, save as `Knowledge/Reports/YYYY-MM-DD-<feature>.md`.

## L0 Behavior (No Project)

Without artifacts, delivery is a **session summary report**:

1. Scan the conversation for: files created/modified, decisions made, issues found
2. Generate a lighter version of the delivery report (no artifact references)
3. Offer to save to `Knowledge/Reports/`

Still valuable — structures the session's output for future reference.

## Rules

- **Never skip attention flags** — if review had unresolved findings or QA had remaining bugs, they MUST appear as attention flags
- **Don't duplicate IMPROVEMENT.md writeback** — the hook handles per-lesson extraction. Delivery adds pipeline-level insights only.
- **PR description is optional** — only generate if changeset artifact exists
- **Keep the report scannable** — tables and bullet points over paragraphs. A busy human should understand the delivery in 30 seconds.
- **Delivery is the pipeline's receipt** — it marks completion and provides the audit trail for what was built, why, and what remains.

## Escalation Protocol

### L0 INFORM (clean delivery)
When all upstream stages passed without issues:
```markdown
> [INFORM] **Delivery ready: <feature>** — all tests pass, review clean,
> no open escalations. PR description generated.
```

### L2 BLOCK (unresolved issues)
When upstream stages have unresolved escalations or critical findings:
```markdown
> [BLOCK] **Cannot deliver: N unresolved items from upstream stages**
>
> These must be addressed before delivery:
> 1. [REVIEW] Critical security finding in auth.py (confidence 9/10)
> 2. [TEST] WTF gate halted — 3 unfixed bugs remain
>
> **Options:**
> 1. Fix the issues first (re-run review + QA)
> 2. Deliver with known issues (add to attention flags)
> 3. Defer delivery — needs more work
```

## Artifact Operations

**Discover all upstream artifacts (first step):**
```bash
python backend/scripts/artifact_cli.py discover --project <PROJECT> \
  --types evaluation,research,alternatives,design_doc,changeset,review,test_report --full
```

**Publish delivery artifact:**
```bash
python backend/scripts/artifact_cli.py publish \
  --project <PROJECT> --type delivery --producer s_deliver \
  --summary "Feature complete: <title>" \
  --data '<JSON of delivery output>'
```

**Advance pipeline to reflect:**
```bash
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state reflect
```
