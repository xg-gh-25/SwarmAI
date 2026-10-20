# Requirements

**Run** `run_6238b7c3` · **profile** full · **project** SwarmAI

> 先落 convergence step 3

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

**Verdict** GO · **Scope** standard · **Scores** strategic 9, priority 9, feasibility 8

## Requirements

| ID | Requirement | Source | Status |
|---|---|---|---|
| FR-1 | New runs use the deterministic Gate-2 flow by default; the legacy flow only when asked for | user-stated | confirmed |
| FR-2 | In a deterministic run only tests and budget stops block DELIVER (design section 2.5); model verdicts lower push_ready | derived | confirmed |
| FR-3 | The DELIVER card tells the agent how to run the deterministic flow | derived | confirmed |

**FR-1 acceptance**
- Given run-create without --convergence · when run.json is written · then convergence is deterministic
- Given run-create --convergence legacy · when gate2-ledger / gate2-fixspec run · then they work as today

**FR-2 acceptance**
- Given a deterministic run whose ledger has an open HIGH finding · when stage-complete --stage deliver and run-update --status completed · then neither fails on that finding; push_ready is false with the finding as a reason
- Given a legacy run with the same artifact · when stage-complete --stage deliver · then it still fails as today

**FR-3 acceptance**
- Given an agent in a deterministic run reading stages/deliver.md · when it reaches Gate-2 · then it loops gate2-next and follows its next step, and skips fixspec / recheck / meta-review

### Acceptance criteria

- AC1: run-create without --convergence records convergence=deterministic in run.json; --convergence legacy records the legacy flow and the legacy commands still work for it
- AC2: in a run with convergence=deterministic, stage-complete --stage deliver does not fail on a model verdict: an adversarial_review finding that is not resolved (pipeline_validator.py:2496), a specialist HIGH that would require the Red Team (pipeline_validator.py:2520, artifact_cli.py:10353), a missing meta_review and a missing convergence block (:2553-2575) are not errors; the same artifact in a legacy run still fails as today
- AC3: in a deterministic run, adversarial_review.findings in the published DELIVER artifact is filled by the CLI from the gate2-next ledger (id, expert, round, claim, ledger state, break test), so REPORT shows each finding beside its test; findings that are not proven stay push_ready reasons
- AC4: the same rule holds at run-update --status completed (_check_depth :3182 and :3205): a not-proven finding of a deterministic run does not block completion
- AC5: stages/deliver.md has a deterministic branch: loop gate2-next and do what it prints; it states that gate2-ledger / gate2-fixspec / gate2-recheck / meta-review blocking do not apply there, and keeps the legacy steps for legacy runs
- AC6: this run (run_6238b7c3, convergence=deterministic) completes DELIVER through gate2-next and lands a local commit on the test result

## Understanding (Gate 0)

- **Present state:** No run follows the deterministic Gate-2 flow today, and a run that opts in cannot finish DELIVER on its rule: run-create stores convergence only for --convergence deterministic, the DELIVER card describes only the legacy flow, and the validator still blocks a deterministic DELIVER artifact on model verdicts (unresolved HIGH finding, missing meta_review, missing convergence).
- **Evidence:** artifact_cli.py:2412-2414 writes convergence only when args.convergence == 'deterministic'; :14262 --convergence default=None (help: default legacy). grep over ~/.swarm-ai/SwarmWS/Projects/*/.artifacts/runs/run_*/run.json for '"convergence": "deterministic"': 0 files. grep 'deterministic|gate2-next' in stages/deliver.md: 0 hits. artifact_cli.py:10358-10362 skips only the CLI-side legacy checks for a deterministic run. Probe /tmp/run_6238b7c3/probe_validator.py: pipeline_validator.validate_artifact_data('deliver', {adversarial_review with one open HIGH}, profile='full') returns 9 errors includ…
- **Alternative framing considered:** Only flip the default and document the branch, and let the agent publish an empty adversarial_review.findings in deterministic runs so the validator has nothing to block on. Loses: the artifact would then say no findings while the ledger holds open ones, and REPORT reads adversarial_review.findings, so the human would not see the unproven rows the design says REPORT must list (section 6).
- **Gate-0 skeptic:** WRONG-FRAME

## Root cause

W1. No real run uses the deterministic flow because run-create defaults to legacy and the DELIVER card only describes the legacy steps  
   _evidence:_ artifact_cli.py:2412-2414, :14262; deliver.md steps 3-5; run.json grep 0 hits
W2. A run that opts in still cannot complete DELIVER on test results alone, because the validator applies model-verdict blockers to every full-profile DELIVER artifact  
   _evidence:_ probe: 9 errors; pipeline_validator.py:2496 (_blocked_findings at publish), :2553-2575 (meta_review, convergence), :3182 (_blocked_findings at completion)
W3. Step 3a put the opt-in check (_deterministic(run_state)) only in artifact_cli; the validator's DELIVER checks receive the artifact and profile, never the run state, so the switch never reached the second door that judges DELIVER  
   _evidence:_ artifact_cli.py:10358 vs pipeline_validator.py:2338 / :3109 signatures; git show --stat 243358ea touches no pipeline_validator.py

- **Root layer:** module-design
- **Missed invariant:** Every check that can block DELIVER reads the run's convergence mode; under convergence=deterministic only tests and budget stops block (design section 2.5), every model verdict only lowers push_ready
- **Searched:** git show --stat 243358ea; grep -n meta_review|_blocked_findings backend/scripts/pipeline_validator.py; grep -n _deterministic backend/scripts/artifact_cli.py; IMPROVEMENT.md What Failed: one-door fixes (P8); grep -n _blocked_findings backend/scripts/pipeline_validator.py; grep -n _red_team_required_now|_red_team_marker_errors|_require_red_team_expert backend/scripts/artifact_cli.py; grep -n _red_team_gate backend/scripts/pipeline_validator.py
- **Prior instances:** 1:
  - 243358ea: the opt-in reached artifact_cli's stage-complete (10358) but not pipeline_validator
- **Siblings (same shape elsewhere):** 6:
  - C0 pipeline_validator.py:2496 _blocked_findings in validate_artifact_data (publish time) — an unresolved model finding blocks stage-complete --stage deliver
  - C1 pipeline_validator.py:3182 _check_depth at run-update --status completed — the same unresolved-finding block at the completion door
  - C3 pipeline_validator.py:2553-2575 meta_review and convergence required — a model review's presence/verdict decides a block
  - C2 pipeline_validator.py:2520 and :3205 _red_team_gate (publish and completion) — a model finding's severity (specialist HIGH) makes a Red Team run a blocking requirement
  - C4 artifact_cli.py:10353 _red_team_marker_errors via _red_team_required_now (:4607), before the deterministic branch at :10358 — the same severity trigger blocks stage-complete --stage deliver in a deterministic run
  - C5 artifact_cli.py:4621 _require_red_team_expert (required_experts injection, read by run-commit D7) — the same trigger; D7 only lowers push_ready, so it does not block landing, but it is driven by a model finding's severity

## Pre-mortem

- Flipping the default before the flow has landed one real run breaks every new run if the flow has a dead end; mitigated because this run itself runs deterministic and the flip lands only if this run completes
- Relaxing validator checks by a run-state flag opens a bypass: a legacy run edits convergence into run.json after run-create to skip the blockers (3a known issue); mitigated only partly: landing still needs tests; recorded as push_ready-visible
- The validator relaxation misses a sibling check (C2/C3) and the first deterministic DELIVER still fails on a model verdict, which this run would hit itself
