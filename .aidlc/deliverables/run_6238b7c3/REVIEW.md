# Review — gates and experts

**Run** `run_6238b7c3` · **profile** full · **project** SwarmAI

> 先落 convergence step 3

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

## Gates

| Gate | Verdict | Notes |
|---|---|---|
| Gate 0 — understanding skeptic | WRONG-FRAME | framing: is the claim observed, is the root the root |
| Gate 1 — plan skeptic | WARN | plan before BUILD |
| REVIEW — spec + conformance | approved · litmus PASS | 6 finding(s) |
| Gate 2 — adversarial experts | full · 7 finding(s), 4 open | Red Team: no |
| Meta-review | — | 0 risk(s) |
| Push-ready | no | findings: 4 unresolved HIGH / MEDIUM(conf>=7); completion_audit.all_green is not true; gate2: security-29ab16 open: Requirement (3), 'a proof must be one the runner wrote', is not implemented. No hun… |

## Experts

| Expert | Findings | Blocking | Fixed | Refuted | Open |
|---|---|---|---|---|---|
| code-quality | 0 | 0 | 0 | 0 | 0 |
| correctness | 2 | 2 | 1 | 0 | 1 |
| gate0-skeptic | 0 | 0 | 0 | 0 | 0 |
| gate1-skeptic | 0 | 0 | 0 | 0 | 0 |
| integration | 3 | 3 | 2 | 0 | 1 |
| red-team | 0 | 0 | 0 | 0 | 0 |
| security | 2 | 2 | 0 | 0 | 2 |
| spec-compliance | 0 | 0 | 0 | 0 | 0 |

_Dispatch:_ gate2-next ledger: R1 correctness, security, integration in one parallel batch; fix 1; R2 the same three on the fix diff only; fix 2; prove R1 (2 break tests valid). 7 findings: 3 proven, 4 open (all one class: proof files are agent-writable, out of this change's scope)

## Gate 2 findings

| Expert | Severity | Finding | Status |
|---|---|---|---|
| integration | HIGH | artifact_cli.py cmd_run_create: the new deterministic default runs a flow a shared (non-isolated) run cannot finish (no snapshot, so no proof). Fixed: the default stays legacy. [proven; test: test_run_create_default_stays_legacy] | fixed |
| correctness | MEDIUM | artifact_cli.py: with the new default, a small change with no dispatched experts could never be push-ready. Fixed by keeping the default legacy. [proven; test: test_run_create_default_stays_legacy] | fixed |
| integration | MEDIUM | artifact_cli.py _project_ledger_review: projected findings carried no severity, so METRICS and REPORT HIGH counts read zero. Fixed: the expert's severity/confidence are looked up from its answer. [proven; test: test_deterministic_findings_carry_reported_severity] | fixed |
| security | HIGH | artifact_cli.py _merged_proofs: a hand-written prove file in the agent-writable run dir can close a finding (3a known issue). Open: out of this change's scope once the default stays legacy; the class fix (runner evidence bound in the protected hand-out ledger) is its own run. [open] | OPEN |
| integration | HIGH | Same class as security-29ab16. [open] | OPEN |
| correctness | HIGH | Same class as security-29ab16. [open] | OPEN |
| security | HIGH | R2: security-29ab16 not resolved by fix 1 (same class). [open] | OPEN |

## REVIEW findings

| Expert | Severity | Finding | Status |
|---|---|---|---|
| code-quality | MEDIUM | artifact_cli.py cmd_publish: a deterministic run's DELIVER published by publish --stage deliver skipped the ledger projection, so model-written findings reached the artifact. Fixed: cmd_publish projects the ledger for the named run before validating. | fixed · inv I2 · class fix: both DELIVER doors (stage-complete via _derive_stage_fields, publish via cmd_publish) call _project_ledger_review |
| code-quality | MEDIUM | artifact_cli.py: the legacy _gate2_outcome verdict and _expand_gate2_ledger still ran for a deterministic DELIVER and could add legacy errors or force push_ready false. Fixed: both are skipped when _deterministic(run_state). | fixed · inv I1 · class fix: every legacy Gate-2 reader on the DELIVER path (validation, ledger expansion, convergence verdict) asks _deterministic |
| code-quality | MEDIUM | artifact_cli.py _project_ledger_review docstring said an unrebuildable ledger only lowers push_ready; on a strict profile the spawned check completes DELIVER degraded. Fixed: the docstring states the degraded outcome; the projection also drops the legacy findings_from key. | fixed · inv I2 · class fix: docstring matches the validator path; test pins the projected shape |
| spec-compliance | LOW | ref/deliver/gate2-deterministic.md said gate2-ledger refuses; only gate2-fixspec and gate2-recheck do. Fixed: the card says gate2-ledger is not used. | fixed |
| code-quality | LOW | deliver.md 'steps 3-5' read as including 4b. Fixed: 'steps 3, 4, 5, 7'; the ref card says 4b stays. | fixed |
| code-quality | LOW | artifact_cli.py: stage-complete reads the gate2-next view twice (projection and push_ready bound); a prove file landing between the two reads could make them disagree. Known issue, not fixed: the reads are milliseconds apart and both sides are recomputed on the next stage-complete. | OPEN |

## Open items

- AC1 reversed: the default stays legacy. Before it can flip: proofs must be runner-written (security-29ab16 class) and a shared run must get a snapshot or stay legacy
- The roster freeze (_gate2_round_roster) was written after both Gate-2 rounds, so no model reviewed it; it is covered by two tests only
- This run's own DELIVER ran on main's 3a code: its stage-complete has no ledger projection and still applies the legacy model-verdict checks, so DELIVER completes degraded; the change itself is what removes that
- Known issues not fixed: an empty DELIVER roster in a deterministic run reports 'no review ran'; run.json convergence is an agent-editable string; stage-start re-entry still hands out legacy delta prompts (the rule text now says to follow gate2-next only); the gate2-next view is read twice per stage-complete; an R2 re-report shows R1's severity
- [runner] Not tested in this run (regression): 512 test file(s) reach a changed module only through other modules and were not run: backend/tests/test_adaptive_timeout.py, backend/tests/test_admission_backfill.py, backend/tests/test_admission_band.py, backend/tests/test_admission_calibration.py, backend/tests/test_admission_is_noise.py, backend/tests/test_admission_rootfix.py, backend/tests/test_admission_routing.py, backend/tests/test_admission_trust_field.py, backend/tests/test_admission_trust_stamp.py, backend/tests/test_adversarial_commit_gate.py, backend/tests/test_adversarial_meta.py, ba…
- [degraded] deliver completed with 17 open gate error(s): Fix by class: adversarial_review.findings[0] (integration, HIGH) names no invariant of the design (got ''). Name the I<n> it violated, or add the missed invaria; Fix by class: adversarial_review.findings[0] (integration, HIGH) needs root_cause — why the DESIGN allowed it, not what the line did; Fix by class: adversarial_review.findings[0] (integration, HIGH) needs class_fix — every place the invariant is now enforced, not only the reported line (+14)
