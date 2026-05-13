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
5. **ADR gate** -- for each **judgment** or **taste** decision classified during
   the pipeline, check whether it qualifies for an ADR. All three must be true:

   a. **Hard to reverse** — the cost of changing your mind later is meaningful
   b. **Surprising without context** — a future reader will wonder "why this way?"
   c. **Real trade-off** — there were genuine alternatives and you picked one

   If all three are true → write a 1-paragraph ADR to IMPROVEMENT.md under a new
   "### Architecture Decision Records" section (or the project's `docs/adr/`
   directory if it exists). Format:

   ```markdown
   **ADR: <short title>** (<date>)
   <1-3 sentences: context, decision, and why. Include rejected alternative.>
   ```

   If any of the three is missing, skip. Most decisions don't qualify — easy to
   reverse (skip), obvious choice (skip), no real alternative (skip). The value
   is recording the surprising, costly, non-obvious decisions so future pipeline
   runs don't re-litigate them.

   **Pipeline integration:** When EVALUATE reads IMPROVEMENT.md, it checks ADRs
   to avoid contradicting existing decisions. If a new requirement conflicts with
   an ADR, EVALUATE surfaces it explicitly.

6. **Dead Code Checkpoint** (if code_intel.db exists)

   Compare dead code count before vs after this pipeline run:
   ```python
   from core.code_intel import load_project_graph
   g = load_project_graph("PROJECT_NAME")
   if g:
       dead = g.find_dead_code()
       # Compare with snapshot taken at REVIEW Step 0 (if available)
   ```
   - If dead code increased: note in IMPROVEMENT.md "What to Watch For"
   - If dead code decreased: note in "What Worked"

   **Skip** when no `code_intel.db` exists.

7. **DDD Cultivation** — After extracting lessons, cultivate DDD documents:

   ```python
   from core.ddd_cultivation import cultivate_from_reflect
   from pathlib import Path

   project_dir = workspace / "Projects" / "<PROJECT>"
   result = cultivate_from_reflect(lessons, "<RUN_ID>", "<PROJECT>", project_dir)
   # result: {"applied": N, "escalated": M, "rejected": K}
   ```

   **Tiered autonomy model:**
   - ADDITIVE (IMPROVEMENT.md lessons, TECH.md patterns): auto-applied, logged
   - RISKY (PRODUCT.md changes, contradictions): escalated to proposal queue

   This is zero-cost (no LLM, keyword heuristic only). Applied changes appear
   in the weekly DDD report. Escalations surface in session briefing.
   Do NOT skip this step — it closes the REFLECT → DDD feedback loop.

8. **Record stage with structured lessons in run.json** — the REFLECT stage record
   MUST include a `lessons` list so REPORT.md can inline them:

```bash
python backend/scripts/artifact_cli.py run-update --project <PROJECT> \
  --run-id <RUN_ID> --stage-json '{
    "stage": "reflect",
    "status": "completed",
    "token_cost": <tokens>,
    "lessons": [
      "Lesson 1 — concise, actionable, one sentence",
      "Lesson 2 — what worked, what failed, what to do differently"
    ],
    "decisions": []
  }'
```

   **Lesson quality bar:** Each lesson must be specific and self-contained.
   Bad: "3 lessons captured" / "Tests pass" / "Report written"
   Good: "SMOKE is highest ROI — caught 2 runtime crashes that unit tests missed"
   Good: "setTimeout for state propagation is always wrong — use event-driven transitions"

9. Record outcome for learning feedback (calibration):

```bash
python backend/scripts/artifact_cli.py learn --project <PROJECT> \
  --evaluation-id <eval_artifact_id> --outcome success \
  --actual-effort "<T-shirt>" \
  --lessons "lesson 1;lesson 2"
```

10. **Regenerate REPORT.md** — DELIVER generated the report before REFLECT ran,
   so lessons were missing. Regenerate to inline them:

```bash
python backend/scripts/artifact_cli.py run-report --project <PROJECT> \
  --run-id <RUN_ID> --force
```

   This is the final version of the report. Section 9 will now contain the
   actual lessons from step 7 above.
