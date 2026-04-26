# PLAN Stage

Pipeline-owned stage (no sibling skill).

## Methodology

1. Take the recommended (or user-chosen) alternative
2. Produce a design document:
   - Architecture/approach description
   - Data model or API contract (if applicable)
   - Acceptance criteria (carry forward from evaluate + refine)
   - Edge cases and error handling
   - Estimated files to change
3. If design requires uncommitted dependencies or API changes -- taste/judgment decision

## Artifact Publish

```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type design_doc --producer s_autonomous-pipeline \
  --summary "Design: <approach> for <requirement>" \
  --data '{"approach":"...","acceptance_criteria":[...],"data_model":"...","api_contract":"...","files_to_change":[...]}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state build
```
