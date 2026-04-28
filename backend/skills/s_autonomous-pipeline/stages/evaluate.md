# EVALUATE Stage

## Base Methodology

> **Reference:** `backend/skills/s_evaluate/SKILL.md`
>
> Follow the full evaluation workflow defined there: parse requirement, score against DDD docs, calculate ROI, classify scope, recommend GO/DEFER/REJECT/ESCALATE, and define acceptance criteria.

## Pipeline-Specific Behavior

### Pre-mortem Gate

After scoring, if the initial recommendation is GO, the base methodology's
Step 3.5 (Pre-mortem) is **mandatory** in the pipeline. The pre-mortem output
(`pre_mortem` array) MUST be included in the evaluation artifact JSON.

If the pre-mortem triggers a score adjustment or escalation, update the
artifact accordingly before publishing.

### Artifact Publish

```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type evaluation --producer s_autonomous-pipeline \
  --summary "<GO/DEFER/REJECT>: <one-line>" \
  --data '{"requirement":"...","scores":{...},"recommendation":"GO","scope":"standard","acceptance_criteria":[...]}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state think
```

### Exit Routing

- **DEFER or REJECT** -- pipeline ends. Log reason and exit.
- **ESCALATE** -- L2 BLOCK -- checkpoint. Human review required before pipeline can continue.
