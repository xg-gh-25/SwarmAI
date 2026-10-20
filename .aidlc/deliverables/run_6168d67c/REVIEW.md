# Review — gates and experts

**Run** `run_6168d67c` · **profile** direct · **project** SwarmAI

> 把上面的P0， P1, P2 都修了啊 direct run

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

## Gates

| Gate | Verdict | Notes |
|---|---|---|
| Gate 0 — understanding skeptic | — | framing: is the claim observed, is the root the root |
| Gate 1 — plan skeptic | — | plan before BUILD |
| REVIEW — spec + conformance | — | 0 finding(s) |
| Gate 2 — adversarial experts | full · 0 finding(s), 0 open | Red Team: yes |
| Meta-review | — | 0 risk(s) |
| Push-ready | no | gate2: not finished (R1 review not answered); unverified: deliver: swarmai: the change differs from the tree recorded when the Gate-2 prompts were written (the landed code was not all reviewed) |

## Experts

| Expert | Findings | Blocking | Fixed | Refuted | Open |
|---|---|---|---|---|---|
| correctness | 0 | 0 | 0 | 0 | 0 |
| gate1-skeptic | 0 | 0 | 0 | 0 | 0 |
| security | 0 | 0 | 0 | 0 | 0 |

_Dispatch:_ gate2-next ledger: 0 finding(s), 0 proven, 2 expert spawn(s)

## Gate 2 — deterministic ledger

0 finding(s): none · 2 expert spawn(s) · next: done

### Notes (do not block)

- MED security: RP70 trust laundering. _review_requirement adds text the builder agent wrote (EVALUATE requirements[].statement, ruled_out, PLAN acceptance_criteria, boundaries.never) to the reviewer prompts. It goes in raw and multi-line, under the line '
- LOW security: _root_cause_prompt now inserts _review_requirement(...) with no neutralization at all, not even _prompt_data or _fence. The PLAN and EVALUATE data elsewhere in this prompt goes through json.dumps, which escapes newlines, so it cannot fake a
- MED correctness: The push_ready check looks for ac_refs_missing only on the newest full-scope report (`scope = next(... mode in (verify, regression))`, newest first per pv.load_test_evidence). But only `--mode regression` writes ac_refs_missing, because `_p
- LOW correctness: `_ac_name_status.names()` scans only the direct statements of the module or class body. A test defined, or a name imported, inside a module-level `if`/`try`/`with` block (for example `try: from helpers import test_x`, or `if sys.platform !=
- LOW correctness: RP47 gap: the tests cover `_review_requirement` and the 5000-char limit in `_write_expert_prompts`, but none of the call sites the change rewires: cmd_stage_start expert prompts, the meta-review slot, the Red Team re-check, fixspec and root

## Open items

- R1 notes fixed after round 1 (refinement fenced, a later verify no longer hides missing refs, names under if/try/with, call-site wiring test): the drift check names this change as not reviewed
- This run was driven by main's CLI
- [runner] Not tested in this run (regression): 508 test file(s) reach a changed module only through other modules and were not run: backend/tests/test_adaptive_timeout.py, backend/tests/test_admission_backfill.py, backend/tests/test_admission_band.py, backend/tests/test_admission_calibration.py, backend/tests/test_admission_is_noise.py, backend/tests/test_admission_rootfix.py, backend/tests/test_admission_routing.py, backend/tests/test_admission_trust_field.py, backend/tests/test_admission_trust_stamp.py, backend/tests/test_adversarial_commit_gate.py, backend/tests/test_adversarial_meta.py, ba…
