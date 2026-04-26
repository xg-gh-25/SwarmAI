# REFLECT Stage

Pipeline-owned stage (no sibling skill).

## Methodology

1. Extract lessons from this pipeline run
2. Write to IMPROVEMENT.md: what worked, what failed, patterns discovered
3. Update MEMORY.md if the lesson is cross-project
4. **Checklist maintenance** -- if any post-pipeline review (E2E, external,
   or user feedback) found bugs that the pipeline missed:
   a. Classify each missed bug: does it fit an existing RP pattern?
   b. If yes -- the checklist was applied but missed (investigate why)
   c. If no -- **add a new RP pattern** to the Runtime Pattern Checklist
      at `backend/skills/s_autonomous-pipeline/REVIEW_PATTERNS.md`.
      Include: trigger condition, what to verify, and the real bug as the example.
   d. If the bug is a resource type missing from the lifecycle table --
      **add the row** to the Resource Lifecycle table (BUILD Step 4 in
      `backend/skills/s_autonomous-pipeline/stages/build.md`).
   This ensures the pipeline learns from every review cycle. Without
   this step, lessons live in IMPROVEMENT.md but never reach the
   checklist that would prevent recurrence.
5. Record outcome for learning:

```bash
python backend/scripts/artifact_cli.py learn --project <PROJECT> \
  --evaluation-id <eval_artifact_id> --outcome success \
  --actual-effort "<T-shirt>" \
  --lessons "lesson 1;lesson 2"
```
