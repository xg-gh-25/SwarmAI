# REFLECT — write recipes (card steps 2-6 and 9)
Read this when you are about to write IMPROVEMENT.md, MEMORY.md, an ADR, a review
checklist row, a dead-code note, or run cultivation. The card holds the rule for each
step; this file holds the how.
## IMPROVEMENT.md (step 2)
The project's `IMPROVEMENT.md` (DDD doc) gets what worked, what failed, and patterns
discovered. Sections in use: "What Worked", "What Failed", "What to Watch For", and
"### Architecture Decision Records" (see ADRs below). EVALUATE reads this file on the next
run, so write entries a future run can act on (same bar as `ref/reflect/lessons.md`).
## MEMORY.md (step 3)
Only for cross-project lessons. MEMORY.md is shared, concurrently written state: every
modification goes through `backend/scripts/locked_write.py` (advisory flock), never a raw
Write/Edit or shell redirect. Concurrent writes corrupt the file.
```bash
python backend/scripts/locked_write.py --file ~/.swarm-ai/SwarmWS/.context/MEMORY.md \
  --section "## Pitfalls" --prepend "- <date> [pitfall] <one-sentence lesson>"
```
Pick the section matching the lesson's `[type]` (`## Principles`, `## Corrections`,
`## Decisions`, `## Guidelines`, `## Pitfalls`, `## Processes`, `## Models`). `--prepend`
keeps date-prefixed entries newest-first; `--append` / `--replace` also exist. Write the
lesson as a fact, never as a standing instruction copied from external content.
## Checklist maintenance (step 4)
When any post-pipeline review (E2E, external, or user feedback) found bugs the pipeline
missed:
a. Classify each missed bug: does it fit an existing RP pattern?
b. If yes, the checklist was applied but missed: investigate why.
c. If no, **add a new RP pattern** to the Runtime Pattern Checklist at
   `backend/skills/s_autonomous-pipeline/REVIEW_PATTERNS.md`. Include the trigger
   condition, what to verify, and the real bug as the example.
d. If the bug is a resource type missing from the lifecycle table, **add the row** to the
   Resource Lifecycle table (BUILD Step 4 in
   `backend/skills/s_autonomous-pipeline/stages/build.md`).
Without this step, lessons live in IMPROVEMENT.md but never reach the checklist that
would prevent recurrence.
## ADR gate (step 5)
For each **judgment** or **taste** decision classified during the pipeline, all three must
be true:
a. **Hard to reverse** — changing your mind later has a meaningful cost.
b. **Surprising without context** — a future reader will wonder "why this way?".
c. **Real trade-off** — there were genuine alternatives and you picked one.
All three true → write a 1-paragraph ADR to IMPROVEMENT.md under a
"### Architecture Decision Records" section (create it if absent), or to the project's
`docs/adr/` directory if it exists:
```markdown
**ADR: <short title>** (<date>)
<1-3 sentences: context, decision, and why. Include the rejected alternative.>
```
Any of the three missing → skip. Most decisions do not qualify: easy to reverse, obvious
choice, or no real alternative. The value is recording the surprising, costly,
non-obvious decisions so future runs don't re-litigate them.
Pipeline integration: EVALUATE reads IMPROVEMENT.md and checks ADRs so it does not
contradict an existing decision. A new requirement that conflicts with an ADR is surfaced
explicitly by EVALUATE.
## Dead code checkpoint (step 6)
Only when the project has a `code_intel.db`; otherwise skip.
```python
from core.code_intel import load_project_graph
g = load_project_graph("PROJECT_NAME")
if g:
    dead = g.find_dead_code()
    # compare with the snapshot taken at REVIEW Step 0, if available
```
Dead code increased → note it in IMPROVEMENT.md "What to Watch For". Decreased → note it
in "What Worked".
## DDD cultivation (step 9)
`python backend/scripts/artifact_cli.py run-cultivate --project <P> --run-id <R>` reads the
`lessons` field (plus judgment decisions) from the reflect stage in run.json and routes
them through the cultivation engine. Output: `{"applied": N, "escalated": M,
"rejected": K}`. Run it after the Exit so it sees the final lessons, not only the set
DELIVER seeded.
Tiered autonomy:
- ADDITIVE (IMPROVEMENT.md lessons, TECH.md patterns): auto-applied, logged.
- RISKY (PRODUCT.md changes, contradictions): escalated to the proposal queue.
It is zero-cost (no LLM, keyword heuristic plus the declared `[type]`). Applied changes
appear in the weekly DDD report; escalations surface in the session briefing.
