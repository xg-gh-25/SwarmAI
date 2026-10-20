# Review — gates and experts

**Run** `run_2702d56e` · **profile** direct · **project** SwarmAI

> 把上面的P0， P1, P2 都修了啊 direct run

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

## Gates

| Gate | Verdict | Notes |
|---|---|---|
| Gate 0 — understanding skeptic | — | framing: is the claim observed, is the root the root |
| Gate 1 — plan skeptic | WARN | plan before BUILD |
| REVIEW — spec + conformance | — | 0 finding(s) |
| Gate 2 — adversarial experts | full · 11 finding(s), 0 open | Red Team: yes |
| Meta-review | — | 0 risk(s) |
| Push-ready | no | unverified: deliver: swarmai: the change differs from the tree recorded when the Gate-2 prompts were written (the landed code was not all reviewed); unverified: deliver: Gate-2 expert(s) have not rev… |

## Experts

| Expert | Findings | Blocking | Fixed | Refuted | Open |
|---|---|---|---|---|---|
| correctness | 4 | 4 | 4 | 0 | 0 |
| gate1-skeptic | 0 | 0 | 0 | 0 | 0 |
| integration | 2 | 2 | 2 | 0 | 0 |
| red-team | 3 | 3 | 3 | 0 | 0 |
| security | 2 | 2 | 2 | 0 | 0 |

_Dispatch:_ gate2-next ledger: 11 finding(s), 11 proven, 8 expert spawn(s)

## Gate 2 — deterministic ledger

11 finding(s): 11 proven · 8 expert spawn(s) · next: done

| Finding | Round | Expert | State | Break test | Claim |
|---|---|---|---|---|---|
| security-af5361 | R1 | security | proven | backend/tests/test_evidence_seal.py::test_a_symlinked_report_is_rejected | Seal bypass via symlink. run_of() calls os.path.realpath() on the path before matching runs/<id>/evidence/<subdir>, and check() looks up the seal by realpath too. So a symlink at evidence/tests/latest |
| correctness-f2da06 | R1 | correctness | proven | backend/tests/test_evidence_seal.py::test_a_symlinked_report_is_rejected | A symlink gets around the seal. `check()` looks up the seal by `os.path.realpath(path)` and hashes `Path(path).read_bytes()`, and both follow symlinks. Attack: the agent runs `ln -sf regression-<older |
| correctness-fddeea | R1 | correctness | proven | backend/tests/test_evidence_seal.py::test_merged_proofs_drops_a_forged_snapshot_list | `_merged_proofs` treats a rejected `snapshots.json` as if it were absent. It does `snaps = pv._load_evidence_json(...)[0] or {}`, which drops the 'present-but-unusable' reason. So `want` is None and p |
| integration-865216 | R1 | integration | proven | backend/tests/test_evidence_seal.py::test_a_write_after_the_store_vanished_warns | write_sealed has a silent case. If the run was enabled (ledger records.evidence_seal=True) but its store file has since gone missing, `not _store(rid).exists()` takes the plain-write branch and return |
| red-team-16e9f7 | R1 | red-team | proven | backend/tests/test_evidence_seal.py::test_a_symlinked_report_is_rejected | A symlink gets around the seal completely. run_of() and check() both resolve with os.path.realpath, so a run evidence file that is a symlink to a target outside any runs/<id>/evidence/<subdir>/ gets r |
| red-team-314fe1 | R1 | red-team | proven | backend/tests/test_evidence_seal.py::test_the_parsed_bytes_are_the_checked_bytes | The bytes that get hashed are not the bytes that get used (TOCTOU, check then use). _load_evidence_json parses path.read_text() first, and only afterwards does evidence_seal.check() re-read the file a |
| red-team-ad0d6d | R2 | red-team | proven | backend/tests/test_evidence_seal.py::test_an_unsealed_run_index_is_refused_not_reset | _read_json_kept turns a forged run_index.json into a sealed one, and the breakers that read it strictly fail open. Repro: overwrite evidence/tests/run_index.json with [] or with entries edited out. (1 |
| integration-9a58eb | R2 | integration | proven | backend/tests/test_evidence_seal.py::test_an_unsealed_run_index_is_refused_not_reset | _read_json_kept was wired into only two of the five run_index.json readers. The goal-run tdd cap in the same _circuit_breaker_check (l.3225), the once-per-run full-scope refusal (l.3168) and _continua |
| correctness-534e10 | R2 | correctness | proven | backend/tests/test_evidence_seal.py::test_an_unsealed_run_index_is_refused_not_reset | _read_json_kept makes the run_index.json seal pointless. When the seal rejects the file it still returns the hand-written content, and _append_run_index (line ~3027) appends to that content and re-sea |
| correctness-49809b | R2 | correctness | proven | backend/tests/test_evidence_seal.py::test_an_unsealed_run_index_is_refused_not_reset | The fix switched only the round breaker and the two writers to _read_json_kept. Three other readers of the same RUN_INDEX still use _read_json, which returns None for an unsealed index, so their check |
| security-1ead3f | R2 | security | proven | backend/tests/test_evidence_seal.py::test_an_unsealed_run_index_is_refused_not_reset | The new _read_json_kept keeps an unsealed run_index.json and re-seals it on the next _append_run_index. This launders a forged index: content like `[]` becomes a sealed file that _read_run_index and r |

### Notes (do not block)

- LOW security: prove_calls in _gate2_v2_inputs still counts every prove-R*-*.json by mtime, sealed or not. Hand-written prove files therefore still feed gate2_replay, where prove_calls moves the phase from review to prove (core/gate2_replay.py:108-111). T
- LOW correctness: `prove_calls` still counts every `prove-R*-*.json` by mtime, sealed or not. Hand-written files therefore still feed `gate2_replay`. A non-zero count moves the phase to 'prove' (gate2_replay.py:110), and a count above `BUDGETS['runner_contin
- LOW correctness: An unsealed `run_index.json` wipes the append-only history. `_append_run_index` reads it with `_read_json`, gets None when the seal rejects it, and rewrites it as `[entry]`. One seal failure is enough to cause this: `write_sealed` returns a
- LOW correctness: The fail-open reason never reaches anyone. `check()` returns `(True, 'evidence seal store unreadable …')`, but `_load_evidence_json`, `pipeline_test._read_json` and `_read_run_index` all ignore `why` when ok is True. The requirement says th
- MED integration: The prove_calls count in _gate2_v2_inputs never got the seal check. It still counts every prove-R*-*.json newer than the last fix by mtime, sealed or not. gate2_replay.replay (core/gate2_replay.py:108-111) moves phase review->prove when pro
- LOW integration: Some readers were left out of the seal. runner_executions reads tests/run_index.json with a raw json.loads, so a forged index still feeds run metrics. In artifact_cli._stage_budget (line 9975), a rejected index makes _read_run_index return
- LOW red-team: _gate2_v2_inputs still counts unsealed prove files. prove_calls counts every prove-R*-*.json whose mtime is later than last_fix, including hand-written ones that _merged_proofs now rejects. finding_ledger.next_action (line 265) switches fro
- LOW red-team: An unsealed run_index.json is treated as absent, not as present-but-unusable. The requirement says unsealed files must be present-but-unusable, but _read_json returns None. So after any edit to run_index.json, _circuit_breaker_check returns
- LOW integration: runner_executions now sets index=[] when the seal rejects run_index.json, so named is empty. Its fallback loop then counts every <mode>-<stamp>.json with a raw json.loads that skips the seal. The forged-index hole becomes a forged-file hole
- MED security: The fix makes a forged snapshots.json void every proof, but a deleted one still reads as absent (None, None). Then `want` is None and a sealed prove file from ANY snapshot counts for the round. That is the exact case the new test comment sa
- LOW security: _stage_budget's fallback path now skips any timestamped report the seal rejects. Before this change such a file still counted as one run. The agent can force the fallback by making _read_run_index return None (a 1-byte edit to run_index.jso

## Open items

- Test fixtures in test_pipeline_worktree.py, test_pipeline_worktree_repos.py and test_stage_boundary_commands.py now write evidence through pipeline_test._write_json (the new contract); changed after Gate-2 R2, covered by their own runs
- Residuals: a script the agent writes can still call write_sealed (same boundary as the review markers); SWARM_DATA_DIR pointed at an empty dir makes a run read as pre-seal (red-team UNCHECKED); a directory swapped to a symlink between the check and the open of a parent is a narrow race
- [runner] Not tested in this run (regression): 503 test file(s) reach a changed module only through other modules and were not run: backend/tests/test_adaptive_timeout.py, backend/tests/test_admission_backfill.py, backend/tests/test_admission_band.py, backend/tests/test_admission_calibration.py, backend/tests/test_admission_is_noise.py, backend/tests/test_admission_rootfix.py, backend/tests/test_admission_routing.py, backend/tests/test_admission_trust_field.py, backend/tests/test_admission_trust_stamp.py, backend/tests/test_adversarial_commit_gate.py, backend/tests/test_adversarial_meta.py, ba…
- [degraded] deliver completed with 20 open gate error(s): Fix by class: adversarial_review.findings[0] (security, HIGH) names no invariant of the design (got ''). Name the I<n> it violated, or add the missed invariant ; Fix by class: adversarial_review.findings[0] (security, HIGH) needs root_cause — why the DESIGN allowed it, not what the line did; Fix by class: adversarial_review.findings[0] (security, HIGH) needs class_fix — every place the invariant is now enforced, not only the reported line (+17)
