# GOAL_CYCLE ref — Final Quality Gate (DoD met → before stage-complete)
Read when every DoD criterion passes (card step 1). Run steps 0-4 in order.
`RUN` = `perl -e 'alarm 110; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py --project <P> --run-id <R>`
**Final Quality Gate (before exiting goal_cycle stage):** `[MUST]` + `[GATE·validator]` (findings block at DELIVER) <!-- R-goal_cycle-005 -->
> The adversarial spawn + cross-path prompt are `[MUST]` (behavioral). The RESULTING
> findings are `[GATE·validator]`: goal_cycle's own schema requires an `adversarial_review`
> DICT with `findings` (a bare `true` FAILS depth validation), and unresolved HIGH/blocking
> findings are code-blocked by `_blocked_findings` at the DELIVER stage. <!-- R-goal_cycle-006 -->
## 0. Cross-Cycle Re-Judgment, one final time on the total diff
Re-judge every ledger finding against `git diff <start_commit>..HEAD`
(`ref/goal_cycle/findings-ledger.md` § 10.5). Carry every APPLICABLE-and-unresolved
finding into the DELIVER `adversarial_review.findings[]` (with severity + confidence) so
the `_blocked_findings` confidence gate sees it. A finding that was RESOLVED in an early
cycle but is APPLICABLE again here is a caught cross-cycle regression; it blocks exactly
like a fresh one.
## 1. Full ADVERSARIAL review on the total changeset
```bash
git diff <start_commit>..HEAD
```
Spawn a sub-agent (`run_in_background: false` — keep the stream visible) with this diff
+ the DoD criteria + the requirement, and the cross-path prompt of step 2. MERGE its new
findings into the ledger (they too become APPLICABLE rows) so the ledger is the complete
set the DELIVER gate enforces.
## 2. Cross-path adversarial prompt (MANDATORY)
Give the adversarial sub-agent this additional instruction, verbatim:
> "Beyond reviewing the diff for correctness, answer these cross-path questions:
> 1. List ALL distinct code paths (branches, flag values, scope options) in the
>    delivered code. For EACH path: was it exercised by the DoD criteria? If no → it's
>    an untested path. Flag it.
> 2. For each function that calls another function: what happens if the callee FAILS
>    (returns error, throws, exits non-zero)? Is the failure propagated, handled, or
>    silently swallowed?
> 3. If this code runs repeatedly (N times), does anything accumulate without cleanup?
>    (sessions, files, locks, temp data)
> 4. Are there any code paths that exist in the diff but cannot be reached by any DoD
>    criterion? Those are dead-on-arrival paths."
Standard adversarial asks "is this code correct?"; cross-path adversarial asks "is this
code correct FOR ALL ITS USES?" (incident: `stages/history/goal_cycle.md`).
## 2.5. Class-Completeness Gate (MANDATORY when `migration_class` is declared): `[MUST]` → `[GATE·validator]` at DELIVER <!-- R-goal_cycle-007 -->
> ⚠️ **Enforcement nuance (adversarially corrected):** the completeness COMPUTATION is
> `[MUST]` — `check_migration_class.py` is NOT called by any validator/CLI/hook (only by
> its own test); the agent must import it, run it, and `adversarial_review["findings"].append()`
> by hand. The TEETH are two SEPARATE real gates around it: (a) the upstream `migration_class`
> DECLARATION is `[GATE·validator]` at EVALUATE (`_check_migration_class_declared`, publish-time
> BLOCK on a migration keyword); (b) any finding the agent DOES append is `[GATE·validator]` via
> `_blocked_findings` at DELIVER. The class-enumeration step BETWEEN them is behavioral. <!-- R-goal_cycle-008 -->
Runs AFTER the cross-path adversarial (step 2), BEFORE marking the stage complete. The
cross-path adversarial is DIFF-SCOPED: it only sees code the cycles TOUCHED, so a class
sibling that NO cycle touched is invisible to it (that is how an ungated write path
shipped while every per-cycle adversarial passed). This gate is CLASS-SCOPED: it
enumerates the FULL class from live source and blocks any member neither migrated nor
carved out.
```python
from scripts.check_migration_class import check_migration_class, to_delivery_finding
mc = evaluation_artifact.get("migration_class")   # None on a non-migration goal
res = check_migration_class(mc, cwd=Path.cwd())
print(res.coverage_table)                          # AC7: always emit the audit table
if not res.passed:
    finding = to_delivery_finding(res)             # HIGH, confidence 9
    # AC6 TEETH: carry into the DELIVER adversarial_review.findings[] so the existing
    # _blocked_findings gate blocks COMPLETE exactly like a fresh adversarial finding.
    adversarial_review["findings"].append(finding)
```
- `migration_class` absent → `check_migration_class` returns a no-op PASS (AC2): a
  feature/bugfix goal is unaffected. If the goal's REQUIREMENT contained a migration
  keyword, EVALUATE Gate-0 already BLOCKED the run until `migration_class` was declared
  (evaluate.md AC11), so a real migration cannot reach here undeclared.
- A MISSED / BAD_ENUMERATION / UNJUSTIFIED_CARVEOUT member → the HIGH finding blocks
  COMPLETE. Fix = migrate the member (or declare an honest carve-out) and re-run.
## 2.6. Cross-Boundary Layer-4 E2E (MANDATORY when `cross_boundary.value == true`): `[GATE·artifact_cli]` <!-- R-goal_cycle-009 -->
The goal profile has NO standalone `test` stage, so TEST's Layer 4 (cross-boundary E2E,
`stages/ref/test/cross-boundary.md`) is otherwise UNREACHABLE under goal: a
cross_boundary goal run would skip the real-seam E2E entirely. Close it HERE: if the
evaluation artifact's `cross_boundary.value == true`, this gate is MANDATORY before the
stage completes, with the SAME contract as TEST Layer 4 (no new mechanism):
- **Drive the REAL seam end-to-end**: fire the real event through the real listener/
  registry; run the real reader against the real writer's output; smoke each subsystem
  through the real shared path. Do NOT mock the thing-under-change (that is the CLASS-A
  test theater Layer 4 exists to prevent).
- **Mutation-verify non-vacuous**: revert the one contract line the change added → the
  E2E MUST go RED. Run it as `RUN --mode mutation --mutations <spec.json>` (one call, an
  isolated worktree, `--maxfail=0`, every mutation in the batch; spec format in
  `stages/ref/test/cross-boundary.md`). State the mutation you ran + that it went RED.
- **Record it in the goal_cycle changeset artifact** (goal_cycle's produced artifact),
  same field shape as TEST:
  ```json
  "cross_boundary_e2e": {"run": true, "test_file": "...", "drives_real": "what real wiring it exercises", "mutation": "reverted X -> RED"}
  ```
- **TEETH (code-enforced):** the completion gate (`artifact_cli.py`
  `run-update --status completed`) scans ALL stage artifacts for a truthy
  `cross_boundary_e2e.run` whenever `cross_boundary.value == true`, and BLOCKS completion
  if none is found, profile-agnostically, so goal cannot silently skip it. A
  `cross_boundary.value == false` (or absent) run is exempt (no ceremony tax).
## 3-4. Fix, then stop
3. If the adversarial finds issues → execute up to 3 more fix cycles (card steps 4-7;
   after each fix `RUN --mode recheck`), re-judging the ledger after each.
4. If issues persist after 3 fix cycles → CHECKPOINT with the findings
   (`ref/goal_cycle/exits.md`).
Done = you tried to break it and failed: the adversarial ran, its findings are fixed or
carried, and any heavy probe converged. Do not add rounds after that.
## After the final adversarial passes: the artifact shape
Complete the goal_cycle stage with an `adversarial_review` **object** (NOT a bare `true`)
carrying its `findings[]`, the same dict shape the DELIVER adversarial records
(`adversarial_review["findings"].append(finding)` above). The validator's STAGE_SCHEMAS +
STAGE_DEPTH for `goal_cycle` (D4) require `dod_met` plus an `adversarial_review` **dict**
with a `findings` key; a bare `adversarial_review: true` FAILS depth validation (a scalar
cannot carry `findings`). This governs the goal_cycle artifact's SHAPE.
**Where the findings actually BLOCK:** the unresolved-finding gate (`_blocked_findings`)
runs at the DELIVER stage, so the final cross-path findings must also be carried into the
DELIVER `adversarial_review.findings[]`, where an unresolved HIGH/blocking finding blocks
completion exactly like a fresh deliver adversarial. goal_cycle's own schema enforces
shape + presence; DELIVER enforces the block. Do not expect goal_cycle-stage validation
alone to reject an unresolved finding.
Also put a truthy `adversarial_review` on the stage RECORD: the goal-profile completion
gate (`run-update --status completed`) reads the goal_cycle stage record, not the
artifact, and BLOCKS "goal profile requires Final Adversarial Review" without it.
Shape (stage-start prints a validator-passing example; no `--check` first — a failing call writes nothing):
```json
{"artifact": {"dod_met": true, "cycles_run": 4, "progress_path": "...", "review_cadence": "every 2 cycles",
  "adversarial_review": {"spawned": true, "profile_tier": "full",
    "findings": [{"severity": "HIGH", "confidence": 8, "resolved": true, "finding": "file.py fn() line N: what. Fixed: how."}],
    "evidence": "cross-path adversarial sub-agent on git diff <start>..HEAD"},
  "cross_boundary_e2e": {"run": false, "reason": "not a cross-boundary change"}},
 "record": {"adversarial_review": true, "decisions": [...], "exit_evidence": {...}}}
```
`stage-complete` fills `findings_total` / `findings_fixed` / `findings_remaining` itself.
The pipeline then proceeds to DELIVER → REFLECT as normal stages.
