# REFLECT Stage

Pipeline-owned stage (no sibling skill).

## Goal Loop Mode (Two-Tier REFLECT)

When the pipeline profile is `goal`, REFLECT operates differently:

### Mini-Reflect (per cycle — handled in goal_cycle.md Step 9)

Not run here. Each cycle appends a one-line insight to the progress file.
No DDD writes. Accumulates raw material for full REFLECT.

### Full REFLECT (at goal completion — runs HERE)

Triggered when goal_cycle exits with SUCCESS (all DoD met + adversarial clean):

1. **Read all mini-reflects** from the progress file "Cycle Log" section
2. **Read the goal requirement** and DoD criteria from evaluation artifact
3. **Distill patterns:**
   - Which DoD criteria were hardest? Why?
   - Which cycle actions had highest leverage? (most progress per cycle)
   - Any recurring blockers across cycles?
   - What was the velocity curve? (accelerating, decelerating, flat)
4. **Write to IMPROVEMENT.md:**
   - "What Worked" entry: the effective patterns from this goal
   - "What Failed" entry (if cycles stalled): the anti-patterns
   - Goal completion metadata: total cycles, wall time, criteria count
5. **Update PROJECT.md:** goal completed, date, cycles taken, key insight
6. **DDD Cultivation** (Step 7 below): run normally on distilled lessons

Then continue with the Standard Methodology below, starting from Step 4
(checklist maintenance → ADR gate → dead code → cultivation → record → learn → report).

**For non-goal profiles:** skip this section, start directly at Step 1 below.

---

## Methodology (Standard — all profiles except goal's per-cycle mini-reflect) `[MUST]`

> REFLECT has NO validator-required artifact fields (`schema --stage reflect` →
> required=[]). Every step below (lessons, ADR gate, cultivation, learn, report) is
> agent-discipline `[MUST]` — the cultivation/learn/report are real scripts the agent
> invokes, not publish-blocking gates. "Do NOT skip" here means honor the discipline.

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

   ```bash
   python backend/scripts/artifact_cli.py run-cultivate \
     --project <PROJECT> --run-id <RUN_ID>
   ```

   This reads the `lessons` field from the reflect stage in run.json and
   routes them through the cultivation engine. Output:
   ```json
   {"applied": N, "escalated": M, "rejected": K}
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
      "[pitfall] Lesson 1 — concise, actionable, one sentence",
      "[decision] Lesson 2 — what worked, what failed, what to do differently"
    ],
    "decisions": []
  }'
```

   **Declare the type — you KNOW it, don't make the classifier guess.** Prefix every
   lesson with `[type]` (one of the 7: `guideline` `pitfall` `decision` `principle`
   `correction` `process` `model`). You know at author-time whether a lesson is a
   *pitfall* (a bug/trap that bit you), a *decision* (a choice you made + why), a
   *principle* (a first-principle belief), or a *correction* (a wrong→right on your own
   behavior). Cultivation HONORS the declared type — it drives both the entry's `[type]`
   tag AND its destination (a `[decision]` → PROJECT § Recent Decisions, `[principle]` →
   PRODUCT § Design Philosophy, `[correction]` → IMPROVEMENT § What Failed), instead of a
   keyword guess that structurally over-produces pitfall/guideline. An undeclared or
   invalid prefix falls back to the keyword guess (safe, but skews the corpus — so declare).

   **Lesson quality bar:** Each lesson must be specific and self-contained.
   Bad: "3 lessons captured" / "Tests pass" / "Report written"
   Good: "[pitfall] SMOKE is highest ROI — caught 2 runtime crashes that unit tests missed"
   Good: "[principle] setTimeout for state propagation is always wrong — use event-driven transitions"

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

---

## Common Rationalizations

| Rationalization | Reality | Source |
|---|---|---|
| "Nothing went wrong — no lessons to capture" | If nothing went wrong, capture WHY. What structural decisions prevented errors? Positive lessons compound: "vertical TDD eliminated all rework" is a lesson that reinforces the pattern. Empty reflect = missed compounding. | Pipeline design |
| "Lessons are generic — 'be careful' isn't worth recording" | Generic = sign you haven't gone deep enough. Good lessons cite: file path, line number, exact mistake, structural fix, which future scenario it prevents. "Be careful with async" → bad. "asyncio.to_thread for subprocess in async context (LL17)" → good. | C024 |
| "DDD docs are fine, no update needed" | Every pipeline run generates knowledge. If nothing in IMPROVEMENT.md changed, either: (1) the run was trivial (fine), or (2) you're not extracting insights (not fine). At minimum update PROJECT.md "Recent Decisions" with this run's key choices. | DDD Cultivation |
| "I'll reflect later / next session" | Reflection quality degrades with time. In-context you have the full reasoning chain. Next session you have summaries. The difference between "I traced this specific code path and found X" vs "something about async was tricky" is the difference between useful and useless. | LL19 |
