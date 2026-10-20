# Review — gates and experts

**Run** `run_86a8fcbb` · **profile** direct · **project** SwarmAI

> direct run 直接做 1,2,3,4 因为你现在的pipeline 不ready.

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

## Gates

| Gate | Verdict | Notes |
|---|---|---|
| Gate 0 — understanding skeptic | — | framing: is the claim observed, is the root the root |
| Gate 1 — plan skeptic | — | plan before BUILD |
| REVIEW — spec + conformance | — | 0 finding(s) |
| Gate 2 — adversarial experts | full · 13 finding(s), 0 open | Red Team: yes |
| Meta-review | RISKS | 1 risk(s) |
| Push-ready | no | Fix by class: adversarial_review.findings[0] (security, HIGH) names no invariant of the design (got ''). Name the I<n> it violated, or add the missed invariant to design_updates [{id, statement, why_… |

## Experts

| Expert | Findings | Blocking | Fixed | Refuted | Open |
|---|---|---|---|---|---|
| correctness | 4 | 1 | 4 | 0 | 0 |
| meta-review | 2 | 0 | 2 | 0 | 0 |
| red-team | 6 | 4 | 6 | 0 | 0 |
| security | 1 | 1 | 1 | 0 | 0 |

_Dispatch:_ Agent tool: correctness, security, red-team, meta-review 一轮并行派出; 同因 finding 合并为一份修复方案一次修完, 每条都有 deny 测试钉住

## Gate 2 findings

| Expert | Severity | Finding | Status |
|---|---|---|---|
| security | HIGH | 任何 run worktree 都算 scratch, 不只本会话的: 可在别的会话的 worktree 里无审批 reset。现在只有 run.json 的 session_id 等于本会话 sdk_session_id 的 worktree 才豁免, 别的 run worktree 不再落到临时目录判断。 | fixed |
| red-team | HIGH | GIT_DIR / env / export 能把 git 指到 SwarmWS, 而 cwd 是 /tmp。命令里出现 GIT_DIR / GIT_WORK_TREE / GIT_COMMON_DIR / GIT_INDEX_FILE 就不豁免 (correctness、security 同一条 MED 合并于此)。 | fixed |
| red-team | HIGH | worktree 和主 checkout 共用 refs: update-ref、filter-branch --all、branch -f、checkout -B、带分支名或 --update-refs 的 rebase 能动别人的分支。豁免只给只动当前分支的 reset / commit --amend / 单参数 rebase (security、meta-review、correctness 的同类 finding 合并于此)。 | fixed |
| red-team | MEDIUM | 临时目录里的 SwarmWS worktree (.git 文件指向 SwarmWS) 被当成 scratch。现在临时目录只有在 repo 的 .git 或 gitdir: 目标也在临时目录时才算。 | fixed |
| correctness | MEDIUM | stdin 判定把选项值当脚本参数: bash -euo pipefail / -o x / +x / --rcfile f 的 heredoc 不被当命令判。现在跳过选项值, -s 一律算 stdin, 含 c 的组合是内联代码 (meta-review 的 pipefail LOW、security 同条合并)。 | fixed |
| red-team | MEDIUM | bash /dev/stdin、sh /dev/fd/0、source /dev/stdin、. /dev/stdin、python3 /dev/stdin 读 stdin 但没被识别。_STDIN_PATHS 统一处理 shell、source/. 和解释器。 | fixed |
| red-team | MEDIUM | 反引号或 eval/source 里的 cd 不被跟踪, 目录判断可被骗。出现反引号或 eval / source / . 段就不豁免。 | fixed |
| correctness | LOW | cd X; 之后 cd 失败时仍在原目录, 却按 X 判。只有 && 才把当前目录移到 cd 目标, ; 之后目录未知。 | fixed |
| correctness | LOW | heredoc 里的 runner 调用和外面的不合计, 外一次加里一次不算 repeat。_runner_counts 把 shell body 的计数并入外层。 | fixed |
| red-team | LOW | pytest \| $(which tail) 的 pager 在替换里, 被当成无 pytest 的替换删掉。含 tail / head 的替换保留。 | fixed |
| meta-review | LOW | token 前的 quoted heredoc 能改写 artifact_cli.py 本身, 再拿 token 跑它。heredoc 目标文件名是 CLI 脚本时拒绝。 | fixed |
| correctness | MEDIUM | 豁免范围比需求说的窄 (只有本会话 worktree 和临时 repo, 不是所有非 SwarmWS repo)。按决定保留窄范围, 不加宽, 理由见 decisions。 | fixed |
| meta-review | LOW | 同上, 需求范围: 同一决定。 | fixed |

## Meta-review risks

| Severity | Risk | Status |
|---|---|---|
| LOW | 修复后没有第二轮专家审查, 每条 finding 都由一个 deny 测试钉住; 豁免故意保持窄 (个人 repo 不放), 这类命令仍会要审批 | OPEN |

## Open items

- [runner] Not tested in this run (verify): 551 test file(s) reach a changed module only through other modules and were not run: backend/tests/test_adaptive_timeout.py, backend/tests/test_admission_backfill.py, backend/tests/test_admission_band.py, backend/tests/test_admission_calibration.py, backend/tests/test_admission_is_noise.py, backend/tests/test_admission_rootfix.py, backend/tests/test_admission_routing.py, backend/tests/test_admission_trust_field.py, backend/tests/test_admission_trust_stamp.py, backend/tests/test_advance_continuation.py, backend/tests/test_adversarial_meta.py, backend/t…
- [degraded] deliver completed with 20 open gate error(s): Fix by class: adversarial_review.findings[0] (security, HIGH) names no invariant of the design (got ''). Name the I<n> it violated, or add the missed invariant ; Fix by class: adversarial_review.findings[0] (security, HIGH) needs root_cause — why the DESIGN allowed it, not what the line did; Fix by class: adversarial_review.findings[0] (security, HIGH) needs class_fix — every place the invariant is now enforced, not only the reported line (+17)
