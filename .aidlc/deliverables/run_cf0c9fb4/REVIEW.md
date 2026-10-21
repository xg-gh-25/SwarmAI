# Review — gates and experts

**Run** `run_cf0c9fb4` · **profile** direct · **project** SwarmAI

> 把 worktree 模式改成默认开启

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

## Gates

| Gate | Verdict | Notes |
|---|---|---|
| Gate 0 — understanding skeptic | — | framing: is the claim observed, is the root the root |
| Gate 1 — plan skeptic | — | plan before BUILD |
| REVIEW — spec + conformance | — | 0 finding(s) |
| Gate 2 — adversarial experts | full · 1 finding(s), 1 open | Red Team: no |
| Meta-review | — | 0 risk(s) |
| Push-ready | no | findings: 1 unresolved HIGH / MEDIUM(conf>=7); gate2: correctness-eafcd8 untestable: RP47 test-theater after the default flip: test_put_on_persists_and_is_read_back and test_an_unrelated_save_keeps_t… |

## Experts

| Expert | Findings | Blocking | Fixed | Refuted | Open |
|---|---|---|---|---|---|
| correctness | 1 | 1 | 0 | 0 | 1 |
| security | 0 | 0 | 0 | 0 | 0 |

_Dispatch:_ gate2-next ledger: 1 finding(s), 0 proven, 5 expert spawn(s)

## Gate 2 — deterministic ledger

1 finding(s): 1 untestable · 5 expert spawn(s) · next: spawn

| Finding | Round | Expert | State | Break test | Claim |
|---|---|---|---|---|---|
| correctness-eafcd8 | R1 | correctness | untestable | — | RP47 test-theater after the default flip: test_put_on_persists_and_is_read_back and test_an_unrelated_save_keeps_the_switch PUT {'worktree_mode':'on'} and assert 'on' on GET and on disk. 'on' is now D |

### Notes (do not block)

- LOW correctness: RP48 stale doc, AC3 not fully met: the front-matter status still says '开关默认 off' (switch off by default), and line 682 still says '开关默认 `off`'. They contradict the corrected lines 54 and 191.
- LOW correctness: The decision table says the stage-card trimming is deferred until the switch becomes default on ('开关转为默认 on 时再删卡片'). This change makes it default on but neither does that work nor updates the row, so a follow-up that is now due goes unrecor
- LOW correctness: `str(pipe.get('worktree_mode') or '')` turns a hand-edited JSON false (a natural way to write off) into '' and so into on. Values like 'disabled', 'no', '0', or a non-dict pipeline value also mean on. The old code treated these as off. The
- LOW security: This change does not touch prompt_builder.py, but it changes what that code does. mode_enabled() is now True by default, so every session with global_user_mode=False gets wt_root() (<app data>/worktrees) added to allowed_directories on ever
- LOW correctness: The mode_enabled docstring still says True unless worktree_mode is "off", but the fix also returns False for JSON false (line 368). The comment no longer matches the behaviour (RP48).

### Lowers push_ready

- gate2: correctness-eafcd8 untestable: RP47 test-theater after the default flip: test_put_on_persists_and_is_read_back and test_an_unrelated_save_keeps_the_swi
- gate2: not finished (R2 review not answered)

## Open items

- Every git repo a session writes to (other than SwarmWS) is now guarded by the worktree write gate by default: a code edit outside a run is denied with a run-create hint, and a direct commit is limited to 0 lines. This was already the behaviour on this machine.
- Sessions now get read and write access to the worktree root by default (prompt_builder adds wt_root to allowed directories); the write gate keeps a session out of other runs' trees, but reads of other runs' worktrees are allowed. Same as on this machine before; narrowing it to the session's own runs is a follow-up.
- Gate-2 security R2 answer was not counted: runtime_hooks._expert_answer only accepts NO FINDINGS on the last line, and the reviewer wrote it at the top (twice), which used up the round budget. Fixing the parser is a follow-up (hook code, needs a daemon restart).
- Tests that start the CLI as a subprocess with their own env and no SWARM_WORKTREE_MODE now see worktree mode on on a machine without a config.json (CI); on this machine they already did.
- [runner] Not tested in this run (regression): 507 test file(s) reach a changed module only through other modules and were not run: backend/tests/test_adaptive_timeout.py, backend/tests/test_admission_backfill.py, backend/tests/test_admission_band.py, backend/tests/test_admission_calibration.py, backend/tests/test_admission_is_noise.py, backend/tests/test_admission_rootfix.py, backend/tests/test_admission_routing.py, backend/tests/test_admission_trust_field.py, backend/tests/test_admission_trust_stamp.py, backend/tests/test_adversarial_commit_gate.py, backend/tests/test_adversarial_meta.py, ba…
