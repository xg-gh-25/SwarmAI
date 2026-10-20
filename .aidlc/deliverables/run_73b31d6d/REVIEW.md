# Review — gates and experts

**Run** `run_73b31d6d` · **profile** direct · **project** SwarmAI

> ‘3. 十几次被 gate 拦，多数是我自己的写法造成的。 这个会话被拦了 13 次以上，主要是在 heredoc 里写编辑脚本时撞上误判，这正是这次要修的问题。刚才排查时也一样：grep 的模式里写了 setsid，就被后台命令守卫拦了。这是一类新的误判，这次没修到。 ⏎ 4. 长时间不出声。 我遵守"调用工具时不写文字"的规则，结果出现过 5 分钟以上没有一句话的情况。你在界面上看不出我是在干活还是已经卡死。 ⏎ 5. 做完了没说清楚。 最后两次你问"为什么停"，答案其实是"已经提交，在等你批准重启"，但我第一次回复没有把"已经在 main 上了"放在最前面。另外，最后一次 run 的记录还留有两个毛病：状态是 verified=false，报告里写着"Gate-2 没有要修的"，这句是错的。’ 你修下这 还有 第4条 我们没这规则 这是别没有response 自己造response…

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

## Gates

| Gate | Verdict | Notes |
|---|---|---|
| Gate 0 — understanding skeptic | — | framing: is the claim observed, is the root the root |
| Gate 1 — plan skeptic | — | plan before BUILD |
| REVIEW — spec + conformance | — | 0 finding(s) |
| Gate 2 — adversarial experts | full · 6 finding(s), 0 open | Red Team: no |
| Meta-review | RISKS | 2 risk(s) |
| Push-ready | no | meta_review carries a HIGH; Red Team required: 3 specialist HIGH/CRITICAL finding(s) but adversarial_review shows no Red Team run — [HIGH by correctness] A detach keyword inside a double-quoted comma… |

## Experts

| Expert | Findings | Blocking | Fixed | Refuted | Open |
|---|---|---|---|---|---|
| correctness | 4 | 3 | 4 | 0 | 0 |
| meta-review | 0 | 0 | 0 | 0 | 0 |
| red-team | 0 | 0 | 0 | 0 | 0 |
| security | 2 | 2 | 2 | 0 | 0 |

_Dispatch:_ Agent tool: correctness, security, meta-review sent in one message, in parallel. All six findings share two causes and were fixed in one pass; 25 reviewer probes are deny tests, and a lenient mutant of the guard denies 0 of the 39 deny cases.

## Gate 2 findings

| Expert | Severity | Finding | Status |
|---|---|---|---|
| correctness | HIGH | A detach keyword inside a double-quoted command substitution (echo "$(setsid -f sleep 999)", X="$(nohup ./a)") was judged data, because the quoted token is one shlex word. | fixed · inv I1 · class fix: _bg_keyword_is_text replaces _bg_keyword_runs: a keyword is text only when the command parses, holds no backtick and no $( <( >( in any tok… |
| correctness | HIGH | nohup is stripped by _unwrap_program as a wrapper, so `nohup grep -r x /` and `sudo nohup echo hi` hit the text-program exemption and passed. | fixed · inv I1 · class fix: _bg_segment_is_text reads seg[0] with no unwrapping: a segment that starts with nohup, sudo, env, nice, timeout or an assignment is not a t… |
| security | HIGH | Wrappers with options (env -i, sudo -u, nice -n, command -p) left the option as the program, missing the sh -c check; find -exec sh -c, su -c, script -c were never looked into. | fixed · inv I1 · class fix: No unwrapping or exec modelling is needed any more: env, sudo, nice, command, find, xargs, su, script, ssh, docker, fish and every interpre… |
| security | MED | Quoted command substitution and assignment forms (printf %s "$(nohup ./x)", X="$(setsid ...)") counted as data. | fixed · inv I1 · class fix: _bg_keyword_is_text denies any token holding $( <( >( and any command holding a backtick; an assignment seg[0] is not a text program. |
| correctness | MED | Landing can lower the DELIVER record's push_ready after the payload's derived.push_ready was synced, so the printed payload still said true. | fixed · inv I2 · class fix: _sync_derived_push_ready is the one sync, called after degrade/gaps and again after landing from the final DELIVER record on disk; it only … |
| correctness | LOW | The fixed-finding count dropped refuted findings only by the 'Not a bug' text, not disposition=refuted. | fixed · inv I2 · class fix: doc_status counts findings whose _finding_status starts with 'fixed', the same predicate REVIEW.md renders with. |

### Fixed by class

- **correctness · HIGH** — A detach keyword inside a double-quoted command substitution (echo "$(setsid -f sleep 999)", X="$(nohup ./a)") was judged data, because the quoted token is one shlex word.
  - invariant: I1
  - root cause: The first design inverted the default: a keyword counted only where the walker could prove it runs, so every context the walker did not model (quoted substitution, wrappers with options, interpreter code, programs that run their arguments) failed open.
  - class fix: _bg_keyword_is_text replaces _bg_keyword_runs: a keyword is text only when the command parses, holds no backtick and no $( <( >( in any token, and EVERY segment is a text-only program; any other command keeps the raw-text deny. The rule has no per-context walker, so an unmodelled context denies.
  - break test: backend/tests/test_background_command_guard.py::test_a_detach_keyword_that_runs_is_still_caught
- **correctness · HIGH** — nohup is stripped by _unwrap_program as a wrapper, so `nohup grep -r x /` and `sudo nohup echo hi` hit the text-program exemption and passed.
  - invariant: I1
  - root cause: The text-program exemption read the program AFTER wrapper unwrapping, so a keyword that is itself the wrapper was treated as an argument of the program it wraps.
  - class fix: _bg_segment_is_text reads seg[0] with no unwrapping: a segment that starts with nohup, sudo, env, nice, timeout or an assignment is not a text program, so its keyword denies.
  - break test: backend/tests/test_background_command_guard.py::test_a_detach_keyword_that_runs_is_still_caught
- **security · HIGH** — Wrappers with options (env -i, sudo -u, nice -n, command -p) left the option as the program, missing the sh -c check; find -exec sh -c, su -c, script -c were never looked into.
  - invariant: I1
  - root cause: Same cause as the first finding: the guard only denied contexts it modelled, and option-aware unwrapping plus every exec-style program is an open-ended list.
  - class fix: No unwrapping or exec modelling is needed any more: env, sudo, nice, command, find, xargs, su, script, ssh, docker, fish and every interpreter are outside _BG_TEXT_PROGRAMS, so a keyword anywhere in such a command denies.
  - break test: backend/tests/test_background_command_guard.py::test_a_detach_keyword_that_runs_is_still_caught
- **security · MED** — Quoted command substitution and assignment forms (printf %s "$(nohup ./x)", X="$(setsid ...)") counted as data.
  - invariant: I1
  - root cause: Same cause as the first finding (merged): a quoted token was trusted as text without checking for substitution syntax.
  - class fix: _bg_keyword_is_text denies any token holding $( <( >( and any command holding a backtick; an assignment seg[0] is not a text program.
  - break test: backend/tests/test_background_command_guard.py::test_a_detach_keyword_that_runs_is_still_caught
- **correctness · MED** — Landing can lower the DELIVER record's push_ready after the payload's derived.push_ready was synced, so the printed payload still said true.
  - invariant: I2
  - root cause: The sync ran before landing, and landing is a third step that only lowers the record; the payload was built from the earlier copy.
  - class fix: _sync_derived_push_ready is the one sync, called after degrade/gaps and again after landing from the final DELIVER record on disk; it only ever lowers.
  - break test: backend/tests/test_stage_boundary_commands.py::test_sync_derived_push_ready_only_lowers
- **correctness · LOW** — The fixed-finding count dropped refuted findings only by the 'Not a bug' text, not disposition=refuted.
  - invariant: I2
  - root cause: A second refuted predicate was written instead of reusing _finding_status.
  - class fix: doc_status counts findings whose _finding_status starts with 'fixed', the same predicate REVIEW.md renders with.
  - break test: backend/tests/test_run_docs.py::test_fixing_spec_reason_counts_fixes_made_without_a_spec

## Meta-review risks

| Severity | Risk | Status |
|---|---|---|
| HIGH | _bg_keyword_runs re-implemented the command walk and dropped interpreter and exec-program coverage | OPEN |
| LOW | The hook runs in the daemon: the new guard takes effect only after a backend build and restart | OPEN |

## Open items

- [runner] Not tested in this run (verify): 500 test file(s) reach a changed module only through other modules and were not run: backend/tests/test_adaptive_timeout.py, backend/tests/test_admission_backfill.py, backend/tests/test_admission_band.py, backend/tests/test_admission_calibration.py, backend/tests/test_admission_is_noise.py, backend/tests/test_admission_rootfix.py, backend/tests/test_admission_routing.py, backend/tests/test_admission_trust_field.py, backend/tests/test_admission_trust_stamp.py, backend/tests/test_adversarial_meta.py, backend/tests/test_agents.py, backend/tests/test_ambi…
- [degraded] deliver completed with 9 open gate error(s): Red Team required: 3 specialist HIGH/CRITICAL finding(s) but adversarial_review shows no Red Team run — [HIGH by correctness] A detach keyword inside a double-q; Gate-2 findings: adversarial_review.findings[] must carry, one record each, every finding the experts reported (HIGH at conf >= 5, MED at conf >= 7) under its `; a fixed blocking correctness finding (correctness-5b7aa0) is not in the Gate-2 ledger, so no fixing-spec cluster can own it: record the findings from the ledger (+6)
