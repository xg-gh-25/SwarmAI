# PLAN

Write `artifacts/design_spec.json` (lite for trivial). Markdown is renderer-owned. direct has no PLAN. Read `artifacts/knowledge_design.md` first (no THINK in trivial; search the DDD as THINK says when the change touches the domain). Ids the design relies on, from the knowledge files or from `cognition find` / `get`, go in its optional `knowledge_refs`; searches in `knowledge_checked`.

Required checks: DS-2a, DS-4a (bugfix), DS-5, DS-6 (full), DS-7, DS-9a. DS-1 and DS-3 affect audit/push only.

Evidence for the reviewer (full): give each alternative an id (`A1`, `A2`, ...), name the chosen one's id in `chosen`, and say `why_not` for every alternative not taken, so a reviewer sees whether their objection was already weighed. Record each judgment call the plan makes in the optional `decisions` list, one entry each (`{id: D1, question, choice, reason}`), so one can be overturned without rereading the rest. Both appear in DESIGN_SPEC.md and the REPORT.

Shapes the checks refuse (both measured runs had their first PLAN refused for one of these):
- DS-6 (full): `design.flow` has at least one separate failure edge, a `;`- or line-separated `<failure condition> -> <outcome>` whose condition (the text left of its first `->`) says fail, error, invalid, reject or deny, or one of their forms (fails, failing, failure, errors, rejected, denied), e.g. `request -> validate; invalid input -> error returned; valid -> result`. A failure named only in the outcome does not count.
- DS-6 (full): `design.states` is a non-empty list, each `{"name", "created_by", "removed_by"}` with text in all three (a change that keeps no new state names the state it touches, e.g. `{"name": "the event log", "created_by": "the run's create", "removed_by": "never: append-only"}`).
- DS-6 (full): `alternatives` non-empty, `chosen` names one of them, and `properties`, `failure_modes` and `security_boundaries` are present (an empty list is fine).
- DS-9a: every acceptance with `"negative": true` or `"universal": true` is in the `covers` of an exit with `"kind": "test"`; a test `ref` names a test file of the repo or one a task lists in `creates_test_files`.
- DS-4a (bugfix): every `bugfix.unchanged` id is covered by a test exit. Plan the fail-to-pass test too: an exit whose test fails on the run's base and passes on the fix (BUILD reports it with `base_result`).
- DS-1: every acceptance id is in some task's `requirements` and some exit's `covers`.

Complete with `"$SWARM_BACKEND_BIN" pipeline-v1 stage-complete --project <P> --run-id <R> --artifact <json>` and continue. A PLAN refused three times does not end the run: PLAN is marked degraded and the run goes on to BUILD without a DESIGN_SPEC; REVIEW and TEST still decide, and the audit says the PLAN was refused.

A valid example of this stage's document, with placeholders for your own values: `"$SWARM_BACKEND_BIN" pipeline-v1 template design_spec` (trivial: `design_spec-lite`).
