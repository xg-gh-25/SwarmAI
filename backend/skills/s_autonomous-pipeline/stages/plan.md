# PLAN — execution card

Turn the chosen alternative into a design BUILD can execute with zero project context, plus the exit contract that ends verification.

**Enter**: `python backend/scripts/artifact_cli.py stage-start --project <P> --run-id <R> --stage plan` (upstream ACs, `cross_boundary`, `exit_criteria_default`, valid examples).

## Steps

1. **Pick + spike.** Take the chosen alternative; read the code it touches. Needs uncommitted deps or API changes → judgment decision.
2. **Design spec** `[GATE·validator]` (`ref/plan/design-spec.md`): `design` = `user_workflow`, mermaid `architecture` + `flow` (with a failure branch), `states` (`created_by`, `removed_by`, `crosses`), `invariants` `I<n>` (`root_invariant` = EVALUATE's missed one), `failure_modes` `F<n>`. Can't fill it → don't plan code.
3. **File discovery** `[MUST]`: seed files → grep every importer/caller → categorize MODIFY / TEST / VERIFY / IRRELEVANT → read all MODIFY+VERIFY. Code contradicts the approach → back to THINK. <!-- R-plan-001 -->
4. **Change spec** `[MUST]`: dependency-ordered atomic sub-changes, one file + one function/class/endpoint each, each with `Depends on`/`AC`/`Current`/`Target`/`Verify` (trivial profile: last three optional when self-explanatory). <!-- R-plan-003 -->
5. **Test strategy** `[MUST]`: one row per AC (how, mock boundary, inputs). No row → AC too vague, rewrite it. <!-- R-plan-009 -->
6. **Boundaries** `[MUST]`: Always / Ask First / Never. <!-- R-plan-004 -->
7. **Success criteria** `[MUST]`: each checkable by a test, command output or visual check. <!-- R-plan-007 -->
8. **Security boundaries**: only when `cross_boundary.value == true` (Gates).
9. **Required experts**: `python backend/scripts/artifact_cli.py run-update --project <P> --run-id <R> --add-required-expert <id>` per `backend/experts/code/` domain touched (endpoint ⇒ `security`, async ⇒ `concurrency`, state machine ⇒ `state-machine`). Append-only, never `spec-compliance`.
10. **Impact projection**: only if the project has `code_intel.db`.
11. **Exit contract**: `exit_criteria[]` = `[{id, kind, ref, expect}]`; kind ∈ test|probe|mutation|manual, expect ∈ pass|no_new_findings|all_killed|done. Start from `exit_criteria_default`, real refs (manual may omit). A `test` ref is ONE file/nodeid or `mode:<m>`, never a directory or glob. Met per `stage-complete` → stop verifying.

PLAN runs no tests; any test goes only through `perl -e 'alarm 110; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py --project <P> --run-id <R> --mode <m>`.

## Gates

- `[GATE·validator]` `design` on strict profiles (bugfix/direct: flow, states, invariants, failure_modes).
- `[MUST]` file_discovery / change_spec / test_strategy: discipline, not schema-checked; boundaries / success_criteria WARN if absent; the design_doc carries all five + `security_boundaries`. <!-- R-plan-002 --> <!-- R-plan-005 R-plan-008 --> <!-- R-plan-010 -->
- `[GATE·validator]` Security Boundaries required WHEN `cross_boundary.value == true`: a non-empty `security_boundaries` list, each entry `boundary`, `trust_assumption`, and either `enforcement` = `"path:line"` OR `escalate: true` + `reason`. BLOCKS a true plan without it or citing a missing file. OMIT it when false. <!-- R-plan-006 -->
- `[GATE·cli]` `exit_criteria` required at plan stage-complete on strict profiles (not trivial/research). A replan after a later stage completed may only ADD criteria, each with a `reason`, after `stage-start --project <P> --run-id <R> --stage plan`.
- `[GATE·cli]` D7: `run-commit` refuses unless every declared expert left an adversarial-review marker this run (so it must run in DELIVER Gate-2).

## Read only if

- You need the template/rules for steps 3–7, or `code_intel.db` exists → `stages/ref/plan/design-sections.md`
- `cross_boundary.value == true` → `stages/ref/plan/security-boundaries.md`
- Choosing experts, or a declared expert did not satisfy D7 → `stages/ref/plan/required-experts.md`
- Writing a probe/mutation/manual criterion, picking a work-type default, or replanning → `stages/ref/plan/exit-contract.md`
- stage-complete rejects the artifact shape, or you are on the legacy publish/advance path → `stages/ref/plan/artifact.md`

## Exit

`python backend/scripts/artifact_cli.py stage-complete --project <P> --run-id <R> --stage plan --data @/tmp/<R>/plan.json` with `{"artifact": {<design_doc>}, "record": {"decisions": [...]}}` (shape: see stage-start examples; no `--check` first).

> **⏭️ Continuation contract — advance is not the end of your turn.** stage-complete advances to the profile's next stage (build; goal → goal_cycle). Read `next_card` and execute it **in this same turn**. Legal hand-backs only: an L2 or unclear-requirement question (ask, or checkpoint), a user stop, or a terminal verdict (`ref/instructions/escalation.md`). `next_action` is AUTHORITATIVE when it withholds the continue instruction.
