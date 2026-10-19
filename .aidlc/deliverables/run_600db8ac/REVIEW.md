# Review — gates and experts

**Run** `run_600db8ac` · **profile** direct · **project** SwarmAI

> 看一下这个 是不是安这个实现 而且你直接做 不要用pipeline '整体方案写好了： ⏎ description ⏎ Knowledge/Designs/2026-10-19-pipeline-deterministic-convergence.md。目前是我一个人写的草稿，还没有经过对抗审查。 ⏎ 设计哲学：模型负责发现，确定性检查负责判定，不可逆的那一步交给人 ⏎ 这是 SELF 里"model proposes, OS disposes"在质量门上的延伸，落成六条： ⏎ D1 判定权：凡是会阻断的判定，都只依据确定性证据，包括测试结果、退出码、计数和哈希。任何 LLM 的结论都不能阻断，也不能放行。 ⏎ D2 转化：模型的产出只有一个价值，就是能转化成确定性检查，也就是测试或 probe。转化不了的记为 advisory，不阻断，只把 push_ready 设为 false，交给…

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

## Gates

| Gate | Verdict | Notes |
|---|---|---|
| Gate 0 — understanding skeptic | — | framing: is the claim observed, is the root the root |
| Gate 1 — plan skeptic | — | plan before BUILD |
| REVIEW — spec + conformance | — | 0 finding(s) |
| Gate 2 — adversarial experts | full · 6 finding(s), 6 open | Red Team: no |
| Meta-review | RISKS | 4 risk(s) |
| Push-ready | no | DELIVER completed degraded after 3 refused attempts: 1 unresolved blocking finding(s) — HIGH (any confidence) or MEDIUM with confidence >= 7 (missing confidence = fail-closed). Fix each and set 'reso… |

## Experts

| Expert | Findings | Blocking | Fixed | Refuted | Open |
|---|---|---|---|---|---|
| meta-review | 4 | 0 | 0 | 0 | 4 |
| security | 2 | 1 | 0 | 0 | 2 |

_Dispatch:_ Agent tool: 5 sub-agents (EXPERT-ID: correctness, security, integration, red-team, meta-review) spawned in one parallel batch; correctness, integration and red-team were stopped by the user before answering; XG directed landing on test results without further review

## Gate 2 findings

| Expert | Severity | Finding | Status |
|---|---|---|---|
| security | MEDIUM | backend/core/finding_ledger.py record(review): the row is built as {defaults, **f}, so an expert's finding can set proof/kind/round/expert and count as proven with no test run. Open: the ledger has no caller yet; fix before step 3 wires it (allowlist id/file/claim/test/declared/reason). | OPEN |
| security | LOW | backend/core/finding_ledger.py record(review): a duplicate finding id raises after may_spawn, the spawn is not counted, and next_action asks for it again. Open: namespace ids per expert/round, or count a duplicate as unparsable, before step 3. | OPEN |
| meta-review | MEDIUM | backend/scripts/pipeline_test.py run_prove: overlay checkouts share the test-baseline cache (keep 2), so frequent break-test edits create many full checkouts and can evict the HEAD baseline. Open: separate overlay cache dir. | OPEN |
| meta-review | LOW | backend/core/run_worktree.py drop_snapshots has no caller: snapshot refs accumulate. Open: call it on run close in step 3. | OPEN |
| meta-review | LOW | backend/scripts/pipeline_test.py _old_cache_key omits RUNNER_VERSION. Open. | OPEN |
| meta-review | LOW | backend/scripts/pipeline_test.py run_prove writes old-side.json without the lock when _try_lock times out. Open. | OPEN |

## Open items

- [runner] Not tested in this run (verify): 551 test file(s) reach a changed module only through other modules and were not run: backend/tests/test_active_run_commit_gate.py, backend/tests/test_adaptive_timeout.py, backend/tests/test_admission_backfill.py, backend/tests/test_admission_band.py, backend/tests/test_admission_calibration.py, backend/tests/test_admission_is_noise.py, backend/tests/test_admission_rootfix.py, backend/tests/test_admission_routing.py, backend/tests/test_admission_trust_field.py, backend/tests/test_admission_trust_stamp.py, backend/tests/test_advance_continuation.py, bac…
- [degraded] deliver completed after 3 refused attempts with open gate errors: 1 unresolved blocking finding(s) — HIGH (any confidence) or MEDIUM with confidence >= 7 (missing confidence = fail-closed). Fix each and set 'resolved': true, o; Depth: adversarial_review has 1 unresolved blocking finding(s) — HIGH (any confidence) or MEDIUM with confidence >= 7 (missing confidence = fail-closed). Fix be
