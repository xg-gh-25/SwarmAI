# PLAN — execution card

Turn the chosen alternative into a design BUILD can execute with zero project context, plus the exit contract that ends verification.

**Enter**: `python backend/scripts/artifact_cli.py stage-start --project <P> --run-id <R> --stage plan` (upstream ACs, `cross_boundary`, `exit_criteria_default`, valid examples).

## Steps

1. **Pick + spike.** Take the recommended (or user-chosen) alternative; read the code it touches to confirm feasibility. Needs uncommitted deps or API changes → taste/judgment decision.
2. **File discovery** `[MUST]`, before the design: seed files → grep every importer/caller → categorize MODIFY / TEST / VERIFY / IRRELEVANT → read all MODIFY+VERIFY. Code contradicts the approach → back to THINK. <!-- R-plan-001 -->
3. **Change spec** `[MUST]`: dependency-ordered atomic sub-changes, one file + one function/class/endpoint each, each with `Depends on`/`AC`/`Current`/`Target`/`Verify` (trivial profile: last three optional when self-explanatory). <!-- R-plan-003 -->
4. **Test strategy** `[MUST]`: one row per AC (how to test, mock boundary, input construction). No row possible → AC too vague, rewrite it. <!-- R-plan-009 -->
5. **Boundaries** `[MUST]`: Always / Ask First / Never tiers. <!-- R-plan-004 -->
6. **Success criteria** `[MUST]`: each verifiable by a test, command output or visual check. <!-- R-plan-007 -->
7. **Security boundaries**: only when `cross_boundary.value == true` (Gates).
8. **Required experts**: `python backend/scripts/artifact_cli.py run-update --project <P> --run-id <R> --add-required-expert <id>` (repeatable) per `backend/experts/code/` domain touched (endpoint ⇒ `security`, async ⇒ `concurrency`, state machine ⇒ `state-machine`). Append-only. Never `spec-compliance`.
9. **Impact projection**: only if the project has `code_intel.db`.
10. **Exit contract**: `exit_criteria[]` = `[{id, kind, ref, expect}]`; kind ∈ test|probe|mutation|manual, expect ∈ pass|no_new_findings|all_killed|done. Start from `exit_criteria_default`, replace every `<…>` ref with a real target (manual may omit ref). A `test` ref is ONE file/nodeid or `mode:<m>`; a directory or glob is rejected. When `stage-complete` (build/test/deliver) says they are met, stop verifying and move on.

PLAN runs no tests; any test goes only through `perl -e 'alarm 110; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py --project <P> --run-id <R> --mode <m>`.

## Gates

- `[MUST]` file_discovery / change_spec / test_strategy are agent discipline, NOT schema-checked (the validator requires only `acceptance_criteria`). <!-- R-plan-002 -->
- `[MUST]` boundaries / success_criteria are recommended fields: absence WARNs, does not BLOCK. <!-- R-plan-005 R-plan-008 -->
- `[MUST]` design_doc SHOULD carry boundaries, success_criteria, file_discovery, change_spec, test_strategy, + `security_boundaries` (next line). <!-- R-plan-010 -->
- `[GATE·validator]` Security Boundaries required WHEN `cross_boundary.value == true`: a non-empty `security_boundaries` list, each entry `boundary`, `trust_assumption`, and either `enforcement` = `"path:line"` OR `escalate: true` + `reason`. BLOCKS a true plan that lacks it or cites a locus whose file does not exist. OMIT the field when false (no-op). <!-- R-plan-006 -->
- `[GATE·cli]` `exit_criteria` required at plan stage-complete on strict profiles (all except trivial/docs/research; `direct` is strict). A replan after a later stage completed may only ADD criteria, each with a `reason`.
- `[GATE·cli]` D7: `run-commit` refuses unless every declared expert left an adversarial-review marker this run (so it must run in DELIVER Gate-2).

## Read only if

- You need the template/rules for steps 2–6, or `code_intel.db` exists → `stages/ref/plan/design-sections.md`
- `cross_boundary.value == true` → `stages/ref/plan/security-boundaries.md`
- Choosing experts, or a declared expert did not satisfy D7 → `stages/ref/plan/required-experts.md`
- Writing a probe/mutation/manual criterion, picking a work-type default, or replanning → `stages/ref/plan/exit-contract.md`
- stage-complete rejects the artifact shape, or you are on the legacy publish/advance path → `stages/ref/plan/artifact.md`

## Exit

`python backend/scripts/artifact_cli.py stage-complete --project <P> --run-id <R> --stage plan --data @/tmp/<R>/plan.json` with `{"artifact": {<design_doc>}, "record": {"decisions": [...]}}` (shape: see stage-start examples; `--check` preflights without writing).

> **⏭️ Continuation contract — advance is not the end of your turn.** stage-complete advances to the profile's next stage (build; goal → goal_cycle). Read `next_card` and execute it **in this same turn**. Only legal hand-backs: a stage-boundary **L2 Judgment** via the Escalation Routing Protocol, a **true checkpoint trigger** (`should_checkpoint=true`, retries exhausted, or a mid-stage L2), a pending user question, or an abandon verdict. `next_action` is AUTHORITATIVE when it withholds the continue instruction.
