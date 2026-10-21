# Review — gates and experts

**Run** `run_578cc64e` · **profile** direct · **project** SwarmAI

> 已执行 restore，status 只剩 cli.json

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

## Gates

| Gate | Verdict | Notes |
|---|---|---|
| Gate 0 — understanding skeptic | — | framing: is the claim observed, is the root the root |
| Gate 1 — plan skeptic | — | plan before BUILD |
| REVIEW — spec + conformance | — | 0 finding(s) |
| Gate 2 — adversarial experts | full · 3 finding(s), 1 open | Red Team: no |
| Meta-review | — | 0 risk(s) |
| Push-ready | no | findings: 1 unresolved HIGH / MEDIUM(conf>=7); completion_audit.all_green is not true; exit criteria unmet: ['X3']; gate2: correctness-3d33b3 untestable: The change does not meet the requirement. The… |

## Experts

| Expert | Findings | Blocking | Fixed | Refuted | Open |
|---|---|---|---|---|---|
| correctness | 2 | 2 | 1 | 0 | 1 |
| security | 1 | 1 | 1 | 0 | 0 |

_Dispatch:_ gate2-next ledger: 3 finding(s), 2 proven, 4 expert spawn(s)

## Gate 2 — deterministic ledger

3 finding(s): 2 proven, 1 untestable · 4 expert spawn(s) · next: done

| Finding | Round | Expert | State | Break test | Claim |
|---|---|---|---|---|---|
| correctness-3d33b3 | R1 | correctness | untestable | — | The change does not meet the requirement. The requirement says every new run gets convergence=deterministic, --convergence legacy is removed, and the legacy loop is deleted. The run's own AC1 rewrote |
| security-95e4fd | R2 | security | proven | backend/tests/test_pipeline_run.py::test_publishing_an_early_stage_never_switches_a_deterministic_run | publish now writes convergence=legacy to disk whenever a deterministic run's worktree_mode is not 'isolated', and that includes when it is unset. worktree_mode is only decided at the first stage-start |
| correctness-ae1dfb | R2 | correctness | proven | backend/tests/test_pipeline_run.py::test_publishing_an_early_stage_never_switches_a_deterministic_run | The on-disk convergence->legacy switch fires on publish of ANY stage while worktree_mode is still unset (it is set only at the first plan/build/goal_cycle stage-start), so a normal deterministic run's |

### Notes (do not block)

- MED security: The change does not do what the requirement asks. The requirement says every new run gets convergence=deterministic, --convergence legacy is removed, and the legacy model-verdict loop is deleted from stage-complete, run-commit and landing.
- MED correctness: Publish and the later steps judge the same run by different flows (RP89). publish drops convergence only from its in-memory copy `_own` and never writes it back. run.json still says deterministic, so every later reader takes the determinist
- MED correctness: The default convergence now depends on the machine running the tests. `_convergence_for` reads `mode_enabled()`, which falls back to the real ~/.swarm-ai/SwarmWS/config.json. On this host that file has worktree_mode 'on'. `_run_cli` here do
- LOW correctness: Stale docstring (RP48). `_fixspec_recheck_check` still says 'the caller refuses on an enforced run and warns on an older one'. The caller now refuses only when owner is None (the marker store is unreadable). Also, on a run created before th
- LOW security: The update_run mutate sets legacy without checking the run.json it reads under the lock: it acts on the copy read earlier, outside the lock (check then act). Also, update_run can raise RunLockTimeout or ValueError, which the inner except (F
- MED correctness: update_run failure (RunLockTimeout/ValueError) is swallowed by the surrounding except-pass before _pub_convergence/_pub_run are set, so the non-isolated run is validated with convergence='deterministic' (the exact case this branch exists to

### Lowers push_ready

- gate2: correctness-3d33b3 untestable: The change does not meet the requirement. The requirement says every new run gets convergence=deterministic, --convergen

## Open items

- Scope differs from the requirement: the legacy Gate-2 code and --convergence legacy are kept, not deleted. Worktree mode is off by default, so legacy is the only Gate-2 a fresh install can run; deleting it first needs the deterministic flow to work without a worktree. Decision for the user.
- BUILD completed degraded: the PLAN consumers declaration first listed sub-directories instead of the tree root; corrected in a replan.
- [runner] Not tested in this run (regression): 523 test file(s) reach a changed module only through other modules and were not run: backend/tests/test_adaptive_timeout.py, backend/tests/test_admission_backfill.py, backend/tests/test_admission_band.py, backend/tests/test_admission_calibration.py, backend/tests/test_admission_is_noise.py, backend/tests/test_admission_rootfix.py, backend/tests/test_admission_routing.py, backend/tests/test_admission_trust_field.py, backend/tests/test_admission_trust_stamp.py, backend/tests/test_adversarial_commit_gate.py, backend/tests/test_adversarial_meta.py, ba…
- [degraded] deliver completed with 2 open gate error(s): completion_audit.all_green=false (gaps=1). Fix gaps before declaring push-ready.; Depth: completion_audit has 1 fixable gap(s) — fix them or mark as unfixable_gaps before delivery
