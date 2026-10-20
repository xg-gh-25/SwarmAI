# Review — gates and experts

**Run** `run_1cce404a` · **profile** direct · **project** SwarmAI

> 把上面的P0， P1, P2 都修了啊 direct run

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

## Gates

| Gate | Verdict | Notes |
|---|---|---|
| Gate 0 — understanding skeptic | — | framing: is the claim observed, is the root the root |
| Gate 1 — plan skeptic | — | plan before BUILD |
| REVIEW — spec + conformance | — | 0 finding(s) |
| Gate 2 — adversarial experts | full · 2 finding(s), 1 open | Red Team: no |
| Meta-review | — | 0 risk(s) |
| Push-ready | no | findings: 1 unresolved HIGH / MEDIUM(conf>=7); gate2: correctness-c99cf5 untestable: RP47 test-theater for requirement (2), 'no legacy delta prompts'. In test_deliver_reentry_hands_out_no_spawn no ex… |

## Experts

| Expert | Findings | Blocking | Fixed | Refuted | Open |
|---|---|---|---|---|---|
| correctness | 2 | 2 | 1 | 0 | 1 |
| gate1-skeptic | 0 | 0 | 0 | 0 | 0 |
| security | 0 | 0 | 0 | 0 | 0 |

_Dispatch:_ gate2-next ledger: 2 finding(s), 1 proven, 4 expert spawn(s)

## Gate 2 — deterministic ledger

2 finding(s): 1 proven, 1 untestable · 4 expert spawn(s) · next: done

| Finding | Round | Expert | State | Break test | Claim |
|---|---|---|---|---|---|
| correctness-c99cf5 | R1 | correctness | untestable | — | RP47 test-theater for requirement (2), 'no legacy delta prompts'. In test_deliver_reentry_hands_out_no_spawn no expert marker is written between the first and second `stage-start --stage deliver`. So |
| correctness-124056 | R2 | correctness | proven | backend/tests/test_gate2_termination_cli.py::test_an_indentation_only_edit_is_drift | change_patch_id hashes with `git patch-id --stable`, and git strips whitespace before hashing (`git patch-id -h`: '--verbatim  don't strip whitespace from the patch'). An edit after review that only c |

### Notes (do not block)

- MED security: A re-entered deterministic DELIVER whose code changed after round 1 can land push_ready true with nobody having reviewed the change. Three things this diff does combine to cause it. (1) `_stale_gate2_reviewers` returns [] for a deterministi
- LOW correctness: The AC4 run-commit half tests the new helper `_stale_gate2_reviewers` on its own, never through `_land_isolated_deliver`'s precheck (artifact_cli.py ~10905), which is where the requirement says the error must not appear. If that call site w
- MED security: The drift check uses rw.change_tree, which is built with `add -u` and so leaves out untracked files. But run-commit stages brand-new untracked files listed in files_touched (artifact_cli.py around 5895-5907: 'Existence covers ... a brand-ne
- LOW security: The new drift gate takes `_load_evidence_json(...)[0] or {}`, which throws away the problem value. The loader's own contract says an existing-but-unusable or sealed-then-removed snapshots.json must not be treated as absent. Here it is: `lab
- MED correctness: Drift is a permanent push_ready reason with no step that clears it. Scenario: round 1 has no findings, so the action is `done` and only R1 exists. A DELIVER re-entry then grows the change, which is the requirement (1) case. Every later view
- LOW correctness: In cmd_gate2_next the view is built before the F2 snapshot is taken (line 12593). The `--fixed 2` call therefore compares fix 2's edits with R2 and, when the next action is `prove`, reports a stale drift reason ('differs from the R2 snapsho

### Lowers push_ready

- gate2: correctness-c99cf5 untestable: RP47 test-theater for requirement (2), 'no legacy delta prompts'. In test_deliver_reentry_hands_out_no_spawn no expert m

## Open items

- This run was driven by main's gate2-next, which predates F2: no F2 snapshot was taken here
- A drift after done has no review round left: it is a named push_ready stop, landing stays on the tests
- goal_cycle fix-by-class is unchanged (Gate-1 note)
- [runner] Not tested in this run (regression): 514 test file(s) reach a changed module only through other modules and were not run: backend/tests/test_adaptive_timeout.py, backend/tests/test_admission_backfill.py, backend/tests/test_admission_band.py, backend/tests/test_admission_calibration.py, backend/tests/test_admission_is_noise.py, backend/tests/test_admission_rootfix.py, backend/tests/test_admission_routing.py, backend/tests/test_admission_trust_field.py, backend/tests/test_admission_trust_stamp.py, backend/tests/test_adversarial_commit_gate.py, backend/tests/test_adversarial_meta.py, ba…
- [degraded] deliver completed with 3 open gate error(s): Fix by class: adversarial_review.findings[1] (correctness, MED) names no invariant of the design (got ''). Name the I<n> it violated, or add the missed invarian; Fix by class: adversarial_review.findings[1] (correctness, MED) needs root_cause — why the DESIGN allowed it, not what the line did; Fix by class: adversarial_review.findings[1] (correctness, MED) needs class_fix — every place the invariant is now enforced, not only the reported line
