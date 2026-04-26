# THINK Stage

## Base Methodology

> **Reference:** `backend/skills/s_deep-research/SKILL.md`
>
> Follow the 3-alternatives framework defined there: research the requirement, summarize key findings, present Minimal / Ideal / Creative approaches with effort-risk-tradeoff for each, and recommend one with reasoning.

## Pipeline-Specific Behavior

### DDD Alignment

If DDD docs are available:
- Align with PRODUCT.md priorities
- Avoid IMPROVEMENT.md failures

### Artifact Publish

```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type research --producer s_autonomous-pipeline \
  --summary "3 alternatives for <topic>. Recommending: <approach>" \
  --data '{"key_findings":[...],"alternatives":[...],"recommendation":"...","sources":[...]}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state plan
```
