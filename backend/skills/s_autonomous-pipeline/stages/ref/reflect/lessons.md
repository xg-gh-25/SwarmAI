# REFLECT — writing and finalizing lessons
Read this when the card's step 1 or Exit needs detail: refining the seeded lessons, adding
a new one, choosing its `[type]`, or deciding there is "nothing to learn".
## Lessons were SEEDED in DELIVER — reuse them, do not re-extract
DELIVER's "Pipeline Report" step already extracted this run's lessons and wrote them into
THIS reflect stage record's `lessons` field. That is what makes the git-committed audit
report carry §9 before run-commit (see `stages/deliver.md`). So REFLECT's step 1 is a
REUSE + finalize, not a fresh extraction:
- read the lessons already in the reflect record (`stage-start` returns them);
- refine one only if REFLECT surfaces something genuinely new;
- do NOT author a DIFFERENT set. Two sources of §9 re-introduce the drift this split
  removed.
REFLECT's distinct job is everything DELIVER did not do: the IMPROVEMENT.md / MEMORY.md
writes, the ADR gate, DDD cultivation, and finalizing the reflect record to `completed`.
## Recording them (the Exit)
The REFLECT stage record MUST include a `lessons` list so REPORT.md can inline them.
`/tmp/<R>/reflect.json`:
```json
{"record": {
  "lessons": [
    "[pitfall] Lesson 1 — concise, actionable, one sentence",
    "[decision] Lesson 2 — what worked, what failed, what to do differently"
  ],
  "decisions": []
}}
```
Then `artifact_cli.py stage-complete --stage reflect --data @/tmp/<R>/reflect.json`.
REFLECT has no artifact, so the file has no `"artifact"` key. `stage-complete` fills
`status`, `token_cost`, `stage_doc_consumed` itself. Each lesson must be a string longer
than 20 characters, or the record is rejected (`E_RECORD_SCHEMA`, path `lessons[i]`).
The older equivalent was `run-update --stage-json '{"stage":"reflect","status":
"completed","token_cost":<tokens>,"lessons":[...],"decisions":[]}'`; it still works, but
the card teaches only `stage-complete`.
## Declare the type — you KNOW it, don't make the classifier guess
Prefix every lesson with `[type]`, one of the 7: `guideline` `pitfall` `decision`
`principle` `correction` `process` `model`. At author-time you know whether a lesson is a
*pitfall* (a bug/trap that bit you), a *decision* (a choice you made + why), a *principle*
(a first-principle belief), or a *correction* (a wrong→right on your own behavior).
Cultivation HONORS the declared type. It drives both the entry's `[type]` tag AND its
destination, instead of a keyword guess that structurally over-produces
pitfall/guideline:
| Declared type | Destination |
|---|---|
| `[decision]` | PROJECT.md § Recent Decisions |
| `[principle]` | PRODUCT.md § Design Philosophy |
| `[correction]` | IMPROVEMENT.md § What Failed |
| others | routed by the cultivation engine |
An undeclared or invalid prefix falls back to the keyword guess. That is safe, but it
skews the corpus, so declare.
## Quality bar
Each lesson must be specific and self-contained.
- Bad: "3 lessons captured" / "Tests pass" / "Report written"
- Good: "[pitfall] SMOKE is highest ROI — caught 2 runtime crashes that unit tests missed"
- Good: "[principle] setTimeout for state propagation is always wrong — use event-driven
  transitions"
A good lesson cites the file path, line, exact mistake, the structural fix, and which
future scenario it prevents. "Be careful with async" is bad; "asyncio.to_thread for
subprocess in async context" is good. Generic means you have not gone deep enough.
## "Nothing went wrong"
- Still capture WHY: which structural decisions prevented errors. Positive lessons
  compound ("vertical TDD eliminated all rework" reinforces the pattern). An empty
  reflect is missed compounding.
- If nothing in IMPROVEMENT.md changed, either the run was trivial (fine) or insights
  were not extracted (not fine). At minimum update PROJECT.md "Recent Decisions" with
  this run's key choices.
- Reflect now, not next session: in-context you have the full reasoning chain; a later
  session has only summaries.
