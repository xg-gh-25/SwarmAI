---
title: 每个 pipeline run 一个 worktree — 隔离、集成、清理
date: 2026-10-18
created: 2026-10-18
updated: 2026-10-19
project: SwarmAI
status: FINAL r4，P0–P2 已实现；任意 repo、owner gate 范围、开关持久化见 §16.9（开关 2026-10-21 起默认 on）— §11 三个决定 XG 2026-10-18 已确认；对抗审查（r1→r2 单人，r2→r3 四路并行，两轮外部审查，r4 XG 审查）见 §16
related: parallel-session-commit-isolation.md（本文是其中"方案二"的完整设计，取代该草稿）; pipeline-run-budget.md; pipeline-speed-systematic-fix.md
author: Kiro
---
# 每个 pipeline run 一个 worktree：隔离、集成、清理

## 1. 问题一句话

所有 session 共用一个 `swarmai` 工作区和一个 git index。一个 run 测到的是别人改了一半的代码；
提交时分不清哪些改动是自己的（`run-commit` 只能按文件提交，同一文件里两个 run 的改动分不开）；
一个 run 把 `artifact_cli.py` 改到一半时，其他 run 的每次 CLI 调用都会跟着出错。

## 2. 目标与边界

- **目标**：每个 run 在自己的 worktree 里改代码、跑测试；交付时由工具把它的改动集成回 main，冲突显式出现、
  由 run 自己解决；run 结束（任何结束方式）时 worktree 和分支被清零，不留残留、不丢工作。
- **不变的东西**：最终 commit 直接落在集成分支（SwarmAI 是 `main`）上，不 push；工具从 main checkout 运行；
  run.json、artifact、REPORT 仍在 SwarmWS。
- **原则**：确定性的工作全部由工具做（同步、`files_touched`、集成、清理），模型只做判断（解决冲突、审查）。
  新流程尽量折进现有的 `stage-start` / `stage-complete` / `run-commit`，不给模型增加新命令和来回。
- **运行环境**：isolated run 只能在带 hook 的 session 里跑，由 hook 每次签发的一次性 token 证明（§4.2；
  环境变量会被子进程继承，不算证明）；没有 hook 的 `--bare` 执行器上建 isolated run
  直接失败并报告。定时 / 后台 run 将来由带 hook 的 daemon 托管执行器承接。
- **范围外**：不给 SwarmWS 建 worktree（run.json 和 DDD 文档是全局共享状态）；不解决非 pipeline 聊天 session
  之间的摩擦（§10）；不做 hunk 级归属，也不做文件锁（§12）。

## 3. 总体结构

```
plan / build / goal_cycle 的第一次 stage-start ──► 定为 isolated；bindings 里的 repo 当场建 worktree，
   │                     其他 repo 第一次被写时经 run-repo-add 建（§16.9）
   │
   ├─ 模型在 worktree 里改代码；改动始终是"相对 base 未提交"的状态，HEAD 一直等于 base
   ├─ 写入 gate：拒绝写 main checkout、拒绝动共享 ref 和配置，提示对应的 worktree 路径
   ├─ 测试：pipeline_test.py 从 main 运行，ctx.repo = run 的 worktree
   │
deliver 的 stage-start ──► 自动同步：把改动 rebase 到最新 main（冲突在这里由 run 解决），HEAD 回到新的 base
   │                     → Gate-2 / meta-review 审的就是同步后的改动
deliver 的 stage-complete ──► push_ready 成立就直接落地（run-commit 变成幂等的确认）：
   │                     校验路径 → 一次带 hook 的提交 → 在 main checkout 执行原生 git merge --ff-only
   │                     → 改写 run.json；main 动过 → 回到同步；与未提交改动重叠 → 挡下，main 不动
   │
显式终止 ──► 由 owner 执行释放：已集成 → 删；未集成 → 快照进 ledger、校验可恢复后再删
兜底 ──► daemon 启动、每小时、run-create 时清扫；只按 git 登记 + 显式状态回收，不信任 run.json 里的路径
```

开关 `config.json["pipeline"]["worktree_mode"]`（`off` | `on`）只决定新 run 建不建 worktree；已经存在的 run 一律

> 2026-10-24（usable-by-anyone P7、S4b）：这个开关已删除，不再是设置项；`config.json` 里残留的 `pipeline.worktree_mode` 被忽略，新 run 一律建 worktree。`$SWARM_WORKTREE_MODE=off` 只留给测试，S6 随 shared 模式一起删。
> 2026-10-25（usable-by-anyone S6-5b）：shared 模式和 `$SWARM_WORKTREE_MODE` 已删除。下文关于 shared run、开关和 `in_place` 落地的内容只是历史。
按它自己 run.json 里的模式处理（§9）。P0–P2 验收期间默认 `off`；2026-10-21 起默认 `on`，只有显式写 `off` 才关闭。

## 4. 前置（P0）：run 锁与归属

worktree 的创建、同步、落地、释放都要知道"谁在改这个 run、现在能不能动它"。今天这两件事都没有：

### 4.1 run 锁

`artifact_cli` 里没有任何 run 级的锁。`cmd_run_update` 读开头、写整份 dict；两个回收器用非原子的
`write_text`；`PATCH /pipelines/{id}/cancel` 做无锁的读-改-写。结果是 cancel 夹在一次 `run-update` 中间时，
`running` 会被写回去。

- 新增 `runs/<id>/.lock`（`utils.file_lock`），**所有** run.json 写入者都拿它：`run-update`、`stage-start`、
  `stage-complete`、`run-checkpoint`、`run-resume`、`run-commit`、两个回收器、`purge_garbage_runs`、router
  的 cancel、`session_link_stamper`、手工脚本。写入一律临时文件 + `os.replace`。
- 锁内只做读-改-写，不在锁里跑 git 或测试；落地这种长操作在提交点之前重新拿锁、重新读 status
  （例如已被取消就停）。

### 4.2 归属与接管

`run.session_id` 是 owner。今天 `run-resume` 只接受 `paused`，而崩溃的 run 一直停在 `running`，新 session
直接接着调 `stage-start` / `run-update`；stamper 又从不覆盖已有的 `session_id`。于是新 session 不是 owner：
写入 gate 对它失效，它往自己的 worktree 写反而被拒。

- **接管由 daemon 做**（它能看到 SessionRouter 的状态）：PreToolUse 看到一条带 `--run-id R` 的
  `artifact_cli` / `pipeline_test` 命令，而 R 的 owner 不是当前 session 时：owner 不在 STREAMING /
  WAITING_INPUT → 把 `session_id` 改成当前 session，旧值追加到 `session_history[]`，重写 session→run 映射；
  owner 还活着 → deny，说明是谁在跑。`run-resume` 走同一个判断。
- **只有 owner 能动 worktree**：`run-sync`、落地、释放要求 `SWARM_APP_SESSION_ID == run.session_id`；
  daemon 的清扫例外（它先确认 owner 不活跃）。
- **只有带 hook 的 session 能有 isolated run**（不变量，为以后定时 / 后台 run 留接口）：归属判定和写入 gate 都默认
  run 跑在一个由 SessionRouter 建立、挂了 PreToolUse hook 的 session 里。现有的后台执行器用
  `claude --print --bare`（`jobs/executor.py`），没有 hook，也没有 `SWARM_APP_SESSION_ID`；在那条路径上建 isolated run，
  写入 gate 根本不存在。"有 hook"不能用环境变量证明：模型在带 hook 的 session 里起一个 `claude --print --bare`
  子进程，子进程会继承所有环境变量。证明必须来自 hook **这一次真的执行了**，所以用一次性 token：
  - **签发**：session-link 的 PreToolUse 部分（今天已经在 artifact_cli 调用前记录开始时间）识别出会改 worktree 状态的
    命令：每个 `stage-start`（建 worktree、从 ledger 重建、deliver 的同步都在这里发生）、接管（`run-resume` /
    自动接管）、deliver 的 `stage-complete`（落地）、owner 发起的释放。识别到后，它生成随机 nonce，把
    `{sha256(nonce), session_id, run_id, 子命令, issued_at}` **只记在 daemon 进程的内存里**，再用 `updatedInput` 把命令
    改写成 `SWARM_HOOK_TOKEN=<端口>.<nonce> <原命令>`。daemon 的 token 服务没起来时不签发，命令被 deny。
  - **只签单条命令**：命令必须是一条单独调用 artifact_cli 的简单命令（复用 session_link_gate 已有的分词），带管道、
    `;`、`&&`、子 shell 的一律 deny，提示"单独运行这条命令"。这样 nonce 只进那一个进程，不会被同一次调用里的别的
    命令（包括一个 `--bare` 子进程）拿到。模型自己写的 `SWARM_HOOK_TOKEN=` 前缀被 hook 的改写覆盖。
  - **核销**：CLI 是另一个进程，经 daemon 的 Unix socket `<app data>/run/hook-token-<端口>.sock`（文件 0600、目录
    0700、属主必须是本用户；不是网络监听，hive 的 HTTP 鉴权不受影响）把 `sha256(nonce)` 交给 daemon；daemon 取出并
    删掉记录（单次有效），校验 session_id 等于 `run.session_id`、run_id 和子命令一致、未超过 120 秒。daemon 不在 → 视为
    没有 token（fail closed），CLI 不回退到文件。没有 AF_UNIX 的平台（Windows）仍用旧的哈希文件方式。核销失败的处理：建 worktree 那一步且项目绑定了
    代码 repo、开关为 on → **拒绝并让 run 失败**（`failed`，理由"isolated run 需要带 hook 的 session"），不回退成
    shared、不在没有 gate 的情况下悄悄运行；同步、落地、接管、释放 → 拒绝，run 状态不变。daemon 的清扫在进程内调用释放
    函数，不经过 CLI，不需要 token。
  - **token 防的是什么**：签发记录只在 daemon 内存里，写文件造不出 token（实现审查 §16.7 指出的伪造路径已关，
    §16.8）。剩下的是同用户进程顶替 daemon 的 socket 自己当核销方，这需要有意写一个服务，超出 §7.3 的威胁模型
    （防误用和被注入的指令走正常命令绕过）。Windows 的文件方式仍可伪造，写明。
  - **剩余风险，写明不修**：hookless 子进程仍然可以直接改文件（worktree 里改的是本 run 自己的树，同步时进 Gate-2；
    用绝对路径改 main checkout 的，和聊天 session 的未提交改动一样被 ff-only 的重叠检查挡下）。它做不到的是建 run、
    接管、同步、落地。
  - **P0 实测通过（2026-10-18）**：用真实 CLI（bundled claude，Bedrock）跑一个 SwarmAI `HookRegistry`，Bash 组里
    先挂改写 hook、后挂一个只返回 approve 的 hook：模型执行 `printenv SWARM_HOOK_TOKEN` 得到改写注入的值，即改写后的
    命令真的被执行、链式合并保留了 `updatedInput`。合并是"后者覆盖"，所以约定：Bash 组里只有 `run_owner_gate` 设
    `updatedInput`（AGENTS.md 已写明）。
  - 以后做 daemon 托管的 headless 执行器时，让它以带 hook 的 session 运行，自然拿得到 token，不需要另开通道。
- **一个判定，所有 gate 共用**：`own_isolated_run(session)` = 本 session 拥有、状态不是终止、且有 git 登记的
  active worktree 的 run，没有时间窗口。写入 gate 和 `pipeline_test_gate` 都用它（今天 `pipeline_test_gate`
  的 3 小时窗口会在长 BUILD 后放行一次直接 pytest，测的却是 main）。

## 5. 创建与映射

### 5.1 什么时候、给哪些 repo 建

- 在 run 的第一次 `stage-start --stage plan|build|goal_cycle` 时建（同一个 run 只建一次）。research 这类只有
  evaluate / think / reflect 的 run 不建，省掉无用的 6 秒和 119 MB。这之前的 evaluate / think 不改代码；
  万一改了，同步时的兜底检测会把它们列出来（只报告，§7.2）。
- 建给项目 `Projects/<P>/bindings.yaml` 里 `kind: code-repo` 的资产（SwarmAI 是自托管，指向 main checkout）。
  没有绑定代码 repo 的项目不建，行为与今天一致。
- main checkout 处于 detached HEAD 或 merge / rebase / cherry-pick 进行中时不建，回退 `shared`。

### 5.2 位置、命名与校验（不信任 run.json 里的路径）

run.json、bindings.yaml、日志都是 agent 可以直接编辑的普通文件。如果工具按 run.json 里的 `path` 去
`worktree remove --force`、按 `integration_branch` 去 `update-ref`，改一个字段就能删掉别人的 worktree 或移动
任意分支。所以：

- **路径和分支只从 run_id 推导**：`WT_ROOT = ~/.swarm-ai/worktrees`，路径 `WT_ROOT/<run_id>/<repo_name>`，
  分支 `refs/heads/run/<run_id>`；`run_id` 先过已有的 `_validate_path_identifiers`。必须在 `$HOME` 下：
  前端 `isWorkspaceFilePath` 只认 `/Users|/home`，后端只允许 `$HOME` 下的绝对路径。
- **每次使用前校验**：路径的 realpath 等于推导值，且任何一级都不是 symlink（`lstat` 逐级查，`WT_ROOT` 本身也查）；
  该路径出现在 `git -C <repo> worktree list --porcelain` 里，检出的正是该分支；`repo` 的 `--git-common-dir`
  等于创建时记录的值；集成分支匹配 `refs/heads/*` 且等于 main checkout 当前的 `symbolic-ref HEAD`。
  任何一项不符 → 拒绝操作并报告，不"按 run.json 修正"。
- 创建命令：`git -C <main> -c core.hooksPath=/dev/null worktree add -b run/<run_id> <path> <main HEAD>`。
  基于 main HEAD，别人未提交的改动不会进来。由 `artifact_cli` 子进程执行，不经过 PreToolUse，
  `pipeline_test_gate` 对 `git worktree add` 的拒绝不变。`hooksPath` 覆盖只用于这一条命令（与基线 worktree
  相同），worktree 里的 commit 和 rebase 照常经过公司的 git-defender hook。

### 5.3 worktree 初始化

| 项 | 做法 | 原因 |
|---|---|---|
| `desktop/node_modules` | 软链到 main 的；Windows 用 junction，失败回退 `shared` | 前端测试和 tsc 需要；`worktree remove --force` 实测不跟随软链，不会删 main 的 node_modules |
| 软链不被当作改动 | `$GIT_COMMON_DIR/info/exclude` 加 `/desktop/node_modules` | `.gitignore` 的 `node_modules/` 带斜杠，不匹配软链（实测） |
| Python | 不建 `.venv`，统一用 main 的 venv python | 软链 `.venv` 会让 conftest 报 WRONG PYTHON；基线 worktree 今天就这么跑 |
| 依赖文件变更 | run 改了 `pyproject.toml` / `package-lock.json` → 同步时 warning：测试用的是 main 已安装的依赖；在 worktree 里 install 会经软链改掉共用的依赖 | 不为此加 Bash 命令识别（§6.1 的边界） |

### 5.4 run.json 里的记录（只作记录，校验以 5.2 为准）

```json
"worktree_mode": "isolated",       // isolated | shared（创建失败或开关关闭时）
"worktrees": [{
  "repo_name": "swarmai", "git_common_dir": "<创建时的值>",
  "integration_branch": "refs/heads/main",
  "base": "<最近一次同步时集成分支的 sha>",
  "pre_sync_tip": "<最近一次同步前的快照 commit，用于 range-diff>",
  "state": "active",               // active | integrated | released
  "integrated": {"sha": "...", "at": "..."}
}]
```

`stage-start` 的输出带上 worktree 绝对路径和一行规则（"本 run 的代码在 `<path>` 下改"），只在 build /
goal_cycle / deliver 的 stage-start 打印，不每次重复。

## 6. 运行期：让模型和工具都落在 worktree 里

### 6.1 写入 gate（新的 PreToolUse gate）

模型的记忆和上下文写的都是 main checkout 的绝对路径，只靠提示挡不住。gate 只在 `own_isolated_run` 存在时
生效（判定三除外，它对所有 session 生效）。

- **判定一：写 main checkout** → deny，消息给出替换后的 worktree 路径。
  - 作用于 Write、Edit、MultiEdit、NotebookEdit，以及 Bash 写入（复用 `file_change_classifier` 的
    redirect / tee / cp / mv / sed -i / 解释器 heredoc / 删除解析）。目标路径按 `input_data["cwd"]` 和命令里
    前置的 `cd` / `pushd` 补全，再 `realpath`。
  - main checkout 里改 git 状态的命令同样 deny：`commit / add / rm / mv / restore / checkout / switch / stash /
    apply / am / reset / clean / merge / pull / rebase / cherry-pick / revert / update-index`，识别 `git -C`、
    `--work-tree`、`GIT_DIR` / `GIT_WORK_TREE`。
- **判定二：写别人的 worktree** → deny：目标在 `WT_ROOT/<run>/` 下，而该 run 不是本 session 的、或其 worktree
  已不在 git 登记里（释放之后不会在旧路径上重新长出游离目录）。
- **判定三：动共享的 git 状态**（所有 session，按 git common dir 判断，不按 cwd）。worktree 和 main 共享 ref
  和配置，在自己的 worktree 里 `git update-ref refs/heads/main HEAD` 就能绕过整个集成。deny：
  `update-ref` / `symbolic-ref` / `branch -f|-D|-m` / `tag` 作用于 `refs/heads/run/<自己的 id>` 以外的 ref；
  `worktree add|remove|move|prune`；不带 `--worktree` 的 `config`（`core.hooksPath`、`filter.*.clean` 会影响所有
  session）；`gc --prune`、`reflog expire`、`replace`、`notes`、`push .`。
  - **`git stash`（全部子命令）在 `WT_ROOT` 下的任何 worktree 里 deny**。stash 存在 `refs/stash`，所有 worktree 共用
    一份。实测：在 worktree 里 stash 了 run 写到一半的改动，回到 main 执行 `git stash list` 能看到这条，`stash pop`
    之后半成品直接进了 main 的工作区。模型想临时回到 base 看一眼时用 `run-diff --mark` 留锚点。识别方式与上面相同
    （按目标工作区所属的 git common dir，含 `git -C <wt> stash`）。main checkout 里：有 isolated run 的 session 已被
    判定一拦下；其他 session 在 main 里 stash 照常允许——run 的 worktree 里无法再产生 stash，main 里的 stash 列表里
    就不会混进 run 的半成品。
- **硬链接**：worktree 下 `st_nlink > 1` 的文件拒绝 Write / Edit（`ln -f <main>/x.py <wt>/x.py` 之后，改 worktree
  路径实际写的是 main 的 inode）；同步时也拒绝带硬链接的已跟踪文件。
- **读不拦**：Read / Grep 读 main 时附加一句 additionalContext（"你在读 main；本 run 的副本在 X"）。
- **不拦的写入**：SwarmWS 里的 DDD 文档、artifact、`.context`；没有 worktree 的其他 repo。
- **注册**：matcher `Write|Edit|MultiEdit|NotebookEdit|Bash`，单独一组（不进 4 小时的 Bash 链），自带 try/except
  和超时，基础设施出错时 fail-open；运维旁路 `SWARM_WORKTREE_GATE_FORCE=1`（只在 daemon 环境里有效）。符合
  `build_hooks` 的准入三条：通用、对的层次、fail-safe。
- **边界固定，不再扩展**：结构化工具（Write / Edit / MultiEdit / NotebookEdit）精确拦截；Bash 只拦 git 写命令
  （判定一、三）和现有 `file_change_classifier` 已能识别的写入。`python -c`、heredoc 脚本、`$VAR` 目标这类漏网
  情况**不去补**解析器，交给同步时的兜底检测（§7.2 第 1 步）。补解析器是"每轮审查都冒出新漏洞"的循环
  （与已放弃的 pytest→tail 防护同形，C055）。
- **worktree 里的 commit**：只由工具在子进程里做（同步时的快照、落地前的最终提交）。模型在 worktree 里执行
  `git commit` 会被现有的 `adversarial_commit_gate` / `active_run_commit_gate` 拦下（它们只看命令字符串、不管是哪个
  repo），这正好符合 §7.1 的不变量；build 卡片写明"不要在 worktree 里 commit"，免得模型和 gate 较劲。
- **受限权限模式**：`global_user_mode=False` 或非 owner 的 session，把 `WT_ROOT/<run_id>/` 加进允许目录，
  并确认 `create_file_access_permission_handler` 覆盖 MultiEdit / NotebookEdit。

### 6.2 工具定位到 worktree

工具始终从 main checkout 运行：run 改不到正在约束它的工具代码。

- **命令一律用 main 的绝对路径**。卡片、`next_action`、各 gate 的 deny 文本今天都写
  `backend/.venv/bin/python backend/scripts/...`（假设 cwd 是 main）；模型被告知"去 worktree 改"之后会
  `cd <worktree>` 再跑，结果找不到 `.venv`，或跑到自己改了一半的工具副本。两个 CLI 发现 `__file__` 在
  `WT_ROOT` 下时拒绝运行，并给出 main 路径的同一条命令。
- 带 `--run-id` 时按 §5.2 推导并校验 worktree：

| 位置 | 改为 |
|---|---|
| `pipeline_test.execute()` | `ctx.repo = worktree`（校验通过才用，否则报错，不回退到 main） |
| `pipeline_test._python` | 默认 main 的 venv python，不再 warning |
| 主 venv 的 editable `.pth`（把 main 的 `backend` 放进 `sys.path`） | worktree 里删掉或改名的模块会从 main 回落导入。runner 插件目录里放 `sitecustomize`，`PIPELINE_TEST_STRIP_ROOT` 设置时把 main 的 `backend` 从 `sys.path` 去掉；子进程经 `PYTHONPATH` 继承 |
| `artifact_cli._evidence_roots`、`_repo_root_for_file`、`_changed_files_scope`（相对路径） | run 的 worktree 排第一 |
| `security_hooks._canonical_rel_bases` | 加上活跃 run 的 worktree |
| `_diff_pack` 给 Gate-2 的文件列表 | 用 worktree 绝对路径（sub-agent 读相对路径会读到 main） |
| `pipeline_test._repo_relative`（`--files` 建议） | 接受 worktree 绝对路径 |

- **本 run 的改动集 = 已跟踪文件的改动和删除 + 模型显式 `git add` 过的新文件**。已跟踪文件的修改、删除由工具
  自动纳入（`git add -u` 的范围）；**新文件不会自动进来**：模型调试时常在 cwd 留下探针脚本、输出文件、截图
  （main 里现在就有一个未跟踪的 `.aidlc/mockup_v4.png`），旧的 shared 模式靠模型登记的 `files_touched` 把它们挡在
  提交之外，worktree 模式不能因为"worktree 里只有本 run 的东西"就用 `add -A` 丢掉这层过滤。SwarmAI 是公开 repo，
  探针脚本里可能有本机路径、run id（R30#7）甚至 token，而 Gate-2 审查者盯的是代码，一个 `probe.py` 或 `out.txt`
  很容易被放过。
  - 新文件要进本 run，模型在 worktree 里 `git add <path>`（与解决冲突时用的是同一个动作，不增加新概念）。
  - **未 add 的新文件会阻断 deliver**，不只是 warning。pytest 跑的是磁盘上的文件，不管有没有 add：一个 run 新增了
    `foo_helper.py` 却忘了 add，TEST 全绿，Gate-2 的 diff 里看不到这个模块，commit N 里也没有它，main 上一 import 就失败；
    而模型漏 add 新文件比留下探针文件更常见。所以 deliver 的 `stage-start`（同步和 Gate-2 之前）和 `stage-complete`
    （落地之前）只要发现 worktree 里有**未忽略、也未 add** 的新文件，就返回 `untracked_new_files` 并逐个列出，要求模型
    对每个文件决定 `git add` 还是删除：add 了需要的文件 → `T` 变了，证据和审查的树不再匹配，现有的重测要求自动生效；
    删掉探针 → `T` 不变，但测试时它还在工作区里，所以仍要重测一次（下一条）。BUILD / TEST 阶段的 stage-start 只列出、
    不阻断（开发中途有临时文件是正常的）。
  - 落地用的测试证据还必须是在**没有未 add 新文件**的工作区上跑出来的：runner 在证据里记下运行时的未跟踪文件列表，
    §7.3 的树绑定要求这份列表为空。否则一个测试依赖了某个后来被删掉的文件，`T` 相同、测试却不能代表 N：模型把漏 add 的
    `foo_helper.py` 当探针删掉，就是这种情况，只靠 `T` 发现不了。代价是留过探针的 run 删掉探针后要多跑一次 runner。
    这样审查的、测试的、最终提交的，是同一棵树。
  - `files_touched` 由这个改动集推导（记 worktree 绝对路径），`stage-complete` 时更新。build.md 里"手工登记
    files_touched"一步删掉，"`git add` 你的文件"一步改成"新文件 `git add`"；deliver.md 的"暂存、取消暂存别人的文件"、
    test.md 的"每个修复一个原子 commit"删掉（在 worktree 里没意义，在 main 上会被 gate 拒绝）。
- **HEAD 一直等于 base**（§7.1），所以所有"diff vs HEAD"的现有代码（`_diff_pack`、`_changed_files_scope`、
  交付总结、runner 的失败分类基线、reviewed_paths 标记）都不用改，基线自动就是 base。
- srcgraph 缓存按 repo 路径分文件，每个 run 首次冷启动（重新解析，秒级）；释放时删掉该 worktree 的缓存。
  runner 的基线 worktree 按 base 分，每个并发 run 最多再占约 2×119 MB，计入磁盘预算。

### 6.3 卡片与 goal 流程

- 卡片几乎没有余量（deliver.md 4996 / 5000 字符，plan.md 4994，goal_cycle.md 4970，build.md 4940，
  INSTRUCTIONS.md 剩 409）。所以：命令名 `run-commit` 保留，模式差异放在工具输出里，不写进卡片；
  卡片净变化以删除为主（6.2 里删掉的四步）。
- goal_cycle 今天用 `git rev-parse HEAD` 记 `start_commit`、按 `<commit>..HEAD` 审查、用 `git checkout --` 回退，
  cwd 是 main 时这些命令拿到的是 main 的 HEAD。改为 stage-start 直接给出锚点和可执行的 diff 命令
  （新的只读 CLI `run-diff --since <anchor>`，在 worktree 里算），goal 卡片引用它；同步只在 deliver 的
  stage-start 发生，goal cycle 期间不同步，锚点保持有效。

### 6.4 Canvas 与交付总结

- 运行期：活跃 run 的 worktree 加进 `needs_human_review._worktree_roots`，live 的 file_changed 能正确归属。
- 落地后：重新生成 run 目录里的 REPORT.md（今天只在不存在时才写，会保留 worktree 路径），已有的 rail 行按
  worktree→main 路径映射刷新，避免释放后 404。

## 7. 同步与集成

### 7.1 两个原则

- **改动始终"相对 base 未提交"**：worktree 的 HEAD 一直等于 `base`，run 的改动是工作区 + index 里的未提交状态。
  所有今天"diff vs HEAD"的代码因此不用改（§6.2）。模型在自己的 worktree 里 `git commit` 会被现有 commit gate 拦下
  （§6.1），`git add` / `git rm` 允许（解决冲突要用）；goal cycle 需要的锚点用 `run-diff --mark <name>`：工具用临时
  index 做一个快照 commit，存为 `refs/run/<run_id>/marks/<name>`，之后 `run-diff --since <name>` 比较快照和当前状态。
- **折进现有命令**：同步发生在 deliver 的 `stage-start`，落地发生在 deliver 的 `stage-complete`。模型不需要学
  新命令；每个异常都是这两个命令的一个错误，并且只给出一条下一步命令。`run-commit` 保留：已集成就直接
  返回成功（幂等，`report-and-commit.md` 让模型在临时故障后重跑它），`shared` 模式走原逻辑。

### 7.2 同步（deliver 的 stage-start 自动执行）

只动本 run 的 worktree，拿本 run 的锁，不拿全局锁。

1. **兜底检测（只报告）**：本 run 的改动路径（worktree 相对 base 的 `git status`）与 main checkout 的
   `git status --porcelain` 求交，交集里修改时间晚于 `run.created_at` 的列出来，提示"main 里这些文件有未提交改动，
   可能来自 gate 看不见的写入，也可能是别的 session 的工作"。**只报告**：模型不得移动、复制或 restore 这些文件
   （同一时间段 Kiro 会话改过的文件也会被列出，搬走就是拿走别人的工作，R29），由人决定。
2. 集成分支当前 sha `C` 等于 `base` → 无需同步，跳到第 5 步。
3. **rebase**（编辑器一律 `GIT_EDITOR=true GIT_SEQUENCE_EDITOR=true`，否则 `rebase --continue` 会开编辑器失败或挂住）：
   - 先确认：没有 rebase 在进行（有就转到第 4 步的续跑）、`symbolic-ref HEAD == refs/heads/run/<run_id>`、
     HEAD 等于 `base`、`base` 是 `C` 的祖先。
   - 用本 run 的改动集（§6.2：已跟踪文件的改动 + 显式 add 的新文件）做快照 commit `S`（父为 base），记为
     `pre_sync_tip`；把本 run 的分支指向 `S`（工作区内容不变，未 add 的新文件留在工作区不动）；`git rebase C`。
     未 add 的新文件与 main 新进来的文件同名时 rebase 会拒绝（"untracked working tree files would be overwritten"），
     原样报给模型：add 进来或删掉。
   - 成功 → `git reset --soft C`：HEAD 回到新的 base，改动重新变成未提交状态；`base := C`。
4. **冲突**：保持 rebase 进行中，stage-start 返回 `conflict` 和未合并文件列表（`git diff --name-only --diff-filter=U`），
   下一步："在 worktree 里解决，逐个 `git add` / `git rm`，然后重跑 stage-start"。重跑时只有未合并列表为空才
   `rebase --continue`。**不以"没有冲突标记"判断已解决**：实测二进制冲突、改/删冲突都没有冲突标记，按标记判断会
   悄悄丢掉 run 的改动或替模型做了选择。
5. **树指纹** `T` = 本 run 改动集在临时 index 里的 `write-tree`（不含 `.aidlc/deliverables/<run_id>/`；未 add 的新文件
   不在内）。若这次确实
   rebase 了：用 `git patch-id --stable`（同样排除 B 的路径）比较 `pre_sync_tip` 和新状态——相同 → 提示先跑
   verify；不同 → stage-start 额外给出需要重审的专家列表和基于 `git range-diff` 的 prompt 文件（与今天生成 Gate-2
   prompt 的方式相同），只审变化的部分。
6. 之后才生成 Gate-2 prompt，所以审查者看到的永远是同步后的改动。

### 7.3 证据和审查绑定到树

- runner 的 evidence 记录它测试时的 `T`、`base` 和工作区里未 add 的新文件列表（落地要求这份列表为空，§6.2）；
  Gate-2 / meta-review 的 prompt 文件和完成标记记录生成时的 `T`。
- 落地时工具**自己重新计算** `T`，与 evidence 和审查记录里的 `T` 比较，不读 run.json 里可以被改写的字段。
  不一致 → `changed_since_review`（审查之后又改了代码）。
- 威胁模型说明：这些记录都在 `~/.swarm-ai` 下，一个有意篡改的 agent 仍能伪造。这里防的是模型的失误和被注入
  的指令走正常命令绕过，与现有 gate 的假设一致；硬保证来自 `T` 由工具现场重算、提交时 git-defender 扫描全部改动。

### 7.4 落地（deliver 的 stage-complete，`push_ready` 上界成立时）

落地用原生 `git merge --ff-only`，不自己实现 checkout / merge。实测（临时 repo，main checkout 里有别人的改动）：
- 别人暂存了 b、改了 c 未暂存，run 只改了 a 和新增 n → 合并成功，b 的暂存和 c 的未暂存改动原样保留；
- 别人在 run 也改了的文件 a 上有未提交改动 → git 拒绝（"would be overwritten"），main 不动，a 一个字节不变；
- main 在此期间被别人推进 → git 拒绝（"Not possible to fast-forward"），什么都不变。

git 在合并期间自己持有 index 锁，处理 mode、symlink、删除、大小写改名、filter，并在 checkout 时做 `.git` 路径保护
（`core.protectHFS` / `protectNTFS`）。r2 里手写的 index 锁协议、新 index 计算、逐路径三方合并、日志和崩溃恢复全部删掉。
代价只有一个：与别人未提交或已暂存的改动**重叠**时不做三方合并，直接挡下。这件事多常发生现在没有数据；
SwarmAI 里的代码改动按规则本来就走 run，主工作区里的未提交改动主要来自 Kiro 会话。P3 统计被挡的频率和文件，
数据说需要再加三方合并。

**前置条件**（任一不满足：不写任何东西，stage 不前进，返回对应错误）：
- 调用者是 owner；在 run 锁内重新读 status，不是终止状态。
- 原 `run-commit` 的 gate：D7 专家覆盖、Red Team。2026-10-19 起「能提交」和「能 push」分开：`push_ready` 为 false 也落地，每个 commit 标 `push_ready: false` 和原因（commit message 里写 `NOT PUSH-READY: …`），push 仍被挡住；`--force` 已删除。
- worktree 校验通过（§5.2）；HEAD 等于 base；没有 rebase 在进行；没有未合并条目。
- 重算的 `T` 等于 evidence 和审查记录里的 `T`，否则 `changed_since_review`。
- 集成分支当前 sha `C` 等于 `base`，否则 `needs_resync`。
- main checkout 检出的就是集成分支，没有 merge / rebase / cherry-pick 在进行（路径用 `git rev-parse --git-path`），
  否则 `blocked: main_checkout_busy`。

**步骤**：

1. **路径校验**（只读，便宜）：拒绝 gitlink（mode 160000）和 `.gitmodules` 的改动；拒绝目标是绝对路径或指向 repo 外的
   symlink；`.gitattributes` 新增 `filter=` / `diff=` / `merge=` → 需要重审。`.git` 路径分量由 git 的 checkout 保护挡住，
   这里只再查一次大小写变体。
2. **生成 B**（`.aidlc/deliverables/<run_id>/REPORT.md`，按最终的 main 路径），加入 index。
3. **最终提交**：在 worktree 里把本 run 的改动集（§6.2）加 B 提交，得到 `N`，父为 `C`（断言 `N^ == C`）。普通 commit，
   经过 git-defender 等全部 hook，扫描的是本 run 的完整改动（包括解决冲突时写的代码）。耗时见下面"提交耗时与落地路径"。
4. 在 run 锁内重新读 status（已被取消 → 撤销）；拿该 repo 的全局集成锁（等待不超过 30 秒，否则 `busy`）。
5. **落地**：`git -C <main checkout> merge --ff-only N`。
   - 成功 → 第 6 步。
   - "would be overwritten" → `blocked_by_uncommitted`，列出 git 报出的文件。
   - "Not possible to fast-forward" → `needs_resync`。
   - index 锁被占用 → `busy`。
   - 任何失败：worktree `git reset --soft C` 回到不变量（N 只是一个无人引用的 commit），什么都没落到 main。
6. **改写 run.json**（run 锁内）：`commits[]` 记 `{repo: main checkout, sha: N, files}`；`files_touched` 的 worktree 前缀
   换成 main 路径；`worktrees[].state = integrated`。不在任何 worktree 里的文件（SwarmWS 的 DDD 文档、未绑定的 repo）
   照旧用原来的按 pathspec 提交，同样记进 `commits[]`。
7. 重新生成 REPORT 和 Canvas 行（§6.4），然后释放（§8）。

**提交耗时（2026-10-18 实测）与落地路径**：AGENTS.md 说本机一次 commit 要预留约 600 秒（git-defender 扫描），而 Bash
工具单次调用最长 600 秒、后台运行被 `background_command_guard` 禁止，所以 P2 第一步先实测。做法：在本 repo 的临时 detached
worktree 里重放真实 commit 的 diff，用真实的 git-defender hook（`/usr/local/amazon/var/git-defender/hooks`，`GIT_TRACE`
确认 pre-commit、prepare-commit-msg、commit-msg、post-commit 都同步执行了）计时，机器 load average 13–15：

| 场景 | 规模 | 耗时 |
|---|---|---|
| commit（重放 00ed32a5） | 4 文件，+238/−79 | 0.43 s、0.42 s |
| commit（重放 2de1f799） | 11 文件，+1030/−24 | 0.62 s、0.80 s |
| commit（重放 08961eea） | 6 文件，+1449/−85 | 0.46 s、0.46 s |
| commit（HEAD~40..HEAD~10 压成一个） | 162 文件，+16424/−2145 | 0.68 s |
| `commit --amend`（本 repo 主工作区，docs 改动） | 1 文件 | 2.31 s |
| `rebase`（11 文件的 commit 换 base） | — | 1.44 s |
| `merge --ff-only`（另一个 checkout 里） | 4–11 文件 | 0.27–0.35 s |

最慢 2.3 秒，离 600 秒差两个数量级；AGENTS.md 的"约 600 秒"已按实测更新（37569fdf）。外部审查在临时 repo 里的独立测量
结论相同（4 个大 core 文件的 commit ≤ 1 秒，ff-only merge < 1 秒）。所以**默认、也是唯一的落地路径是前台**：

- stage-complete 里同步做完第 1–7 步。最终 commit 带 300 秒超时；超时就终止它、worktree `reset --soft C`，返回
  `busy: commit_slow`（模型按 §7.6 退避重跑）。
- 每次落地记录 commit / merge 耗时。P2 动手时先在真实 repo 的真实 run 上再测一遍；只有 p95 超过约 400 秒，才另行设计
  后台落地（届时要处理：提交前重算 `T`、后台进行期间写入 gate 拒绝写该 worktree、git-defender 在 launchd 环境下的
  PATH / HOME 是否正常）。P2 不实现任何后台落地机制。

**中断**：`merge --ff-only` 的原子性由 git 负责；它在更新工作区的过程中被杀的情况与人手动合并被打断相同，
`git status` 能看到，按 git 的常规方式处理。工具这一侧没有需要恢复的中间状态。

### 7.5 多个代码 repo

`base`、`T`、状态、`commits` 都按 worktree 分别记录。第 1–3 步对每个 repo 都做完，再按 repo 名顺序逐个
`merge --ff-only`。前一个已落地、后一个被挡时，run 记为 `integration: partial`，REPORT 按 repo 列出状态，
stage-complete 的错误给出剩余 repo 的下一步；完成门槛（§8.4）要求全部集成，或显式以 failed / abandoned 结束并写明原因。

### 7.6 被外部状态挡住时

按 XG 确认的决定 2：`blocked_by_uncommitted` / `busy` / `main_checkout_busy` 时 stage 停在 deliver，模型按卡片退避后
重跑 stage-complete（每次调用很快），累计 10 分钟仍不行，以"被 <文件> 里未提交的改动挡住"为真实理由 checkpoint。
是否改成 daemon 后台重试、让 REFLECT 先行，等 P3 有被挡频率的数据再定。

### 7.7 返回给模型的错误（都只给一条下一步命令）

| 错误 | 来自 | 下一步 |
|---|---|---|
| `conflict` | stage-start | 在 worktree 里解决、`git add` / `git rm`，重跑 stage-start |
| `needs_resync` | stage-complete | 重跑 deliver 的 stage-start（它会同步，并说明要重测还是重审） |
| `changed_since_review` | stage-complete | 重跑 deliver 的 stage-start |
| 路径校验失败 | stage-complete | 改掉被拒绝的路径（或说明后由人决定） |
| `blocked_by_uncommitted` / `busy` / `main_checkout_busy` | stage-complete | 退避后重跑 stage-complete，累计 10 分钟后 checkpoint |
| `busy: commit_slow`（最终 commit 超过 300 秒被终止） | stage-complete | 同 `busy`，退避后重跑 |
| `untracked_new_files` | deliver 的 stage-start / stage-complete | 逐个 `git add` 或删除，然后重跑（add 了文件会触发重测） |

## 8. 清理：和 run 的结束是同一个动作

### 8.1 释放函数

`release_run_worktrees(run_state, reason)`，只看**显式**终止状态（status 不是 running / paused 的一律算，包括磁盘上
已有的 `aborted` / `rejected` / `superseded`），不用 `is_terminal_run`：后者在 deliver 完成后就为真，而那时 run 可能正
停在 `needs_resync` 或 `blocked_by_uncommitted`，工作还没集成。

| worktree 状态 | 处理 |
|---|---|
| `integrated` | `git worktree remove --force`（实测不跟随 node_modules 软链）→ `git branch -d run/<id>`（已并入，`-d` 够用）→ 删 `refs/run/<id>/marks/*` 和该 worktree 的 srcgraph 缓存 → `released` |
| `active`，run 已显式终止 | 先快照、校验可恢复，再删（下面） |
| `active`，run 是 running / paused | 保留（长期不动的见 8.3） |

**快照与校验**（实测修正过的做法）：
1. 有 rebase 在进行时**不先 abort**（abort 会丢掉做了一半的冲突解决）：直接在当前状态做快照。
2. 快照 = 临时 index `add -A` → `write-tree` → `commit-tree -p HEAD` 得到 `snap`，覆盖所有未忽略的文件（含二进制、
   +x、新文件）。`commit-tree` 总会产生新 commit，所以 bundle 不会因"范围为空"被拒（实测普通 `git bundle` 在 run 没有
   commit 时会报 "Refusing to create empty bundle"）。
3. `git bundle create <ledger>/worktree.bundle <base>..snap`，另存一份 `git diff --binary <base> snap` 便于人看。
4. **校验通过才删**：`git bundle verify` 成功；在临时目录里从 bundle 恢复出 `snap`，其 tree 与快照一致；`base` 是 `snap`
   的祖先。任一失败 → 保留 worktree，报告，不删。
5. ledger 位置：`~/.swarm-ai/worktree-ledger/<run_id>/`，**不在任何 git repo 里**（放进 SwarmWS 会被
   `WorkspaceAutoCommitHook` 的 `add -A` 提交进历史），保留 90 天，`purge_garbage_runs` 不碰它。恢复方法写进 ledger 的
   README：在 `base` 处建 worktree，从 bundle 取 `snap` 的 tree。忽略的文件（`.gitignore` 里的）不在快照里，这一点也写明。

**git 登记的清理**：`git worktree prune` 没有单条形式，全局 prune 又会碰到 runner 的基线 worktree。所以找到
`$GIT_COMMON_DIR/worktrees/` 下 `gitdir` 文件指向该路径的那个管理目录（目录名按路径末段取，例如 `swarmai`、
`swarmai1`，不是 run_id），删掉它，再删分支。

### 8.2 谁来执行、什么时候执行

**真正删除只由 owner 或 daemon 做**。今天会把 run 写成终止状态的地方（`run-update`、两个回收器、router 的 cancel、
`purge_garbage_runs`、手工脚本）里，只有 owner session 自己的 `run-update` 会立即释放；其余的只写
`release_requested: true`，删除发生在：
- 该 run 下一次 owner 的 `artifact_cli` / `pipeline_test` 调用（看到终止就拒绝工作并释放）；或
- daemon 的清扫确认 owner 不在 STREAMING / WAITING_INPUT 之后。

原因：回收器以 `_run_tokens == 0` 判断"空壳"，而磁盘上 11 个活跃 run 里有 8 个 token 记为 0（测量失败时记 0），
BUILD 又可能 3 小时不写 run.json；另一个 session 在同项目 `run-create` 时就可能把一个正在干活的 run 判成 orphan。
所以对 isolated run，`_abandon_verdict` 的"空闲"还要看 worktree 本身的活动（工作区里最新的修改时间），而不是只看
run.json 和 token。

### 8.3 兜底清扫

- **时机**：daemon 启动时；每小时一次（新的 system job，替代"每天第一次 session 结束时"那一次的不确定性）；
  每次 `run-create`（同项目）。
- **范围**：只看 `WT_ROOT` 下、并且出现在 `git worktree list` 里的 worktree，以及 `run/*` 分支。
  runner 的基线 worktree 和其他手工 worktree 不在范围内。
- **规则**：
  - 对应 run 不存在，或 status 不是 running / paused → 按 8.1 释放（owner 不活跃时）。
  - running / paused、deliver 已完成却一直没集成、或 14 天没有活动 → 快照进 ledger 后释放，记进 Radar；
    `run-resume` 或 owner 的下一次调用从 ledger 重建 worktree。
  - `WT_ROOT` 下有目录但 git 没登记 → **不删**，改名移到 `WT_ROOT/.quarantine/` 并报告（防止有人把 symlink 放进
    `WT_ROOT` 引导清扫删掉别的目录）。
  - 没有 worktree 的 `run/*` 分支：run 已终止且已并入 → 删；未并入 → 快照后删。

### 8.4 完成门槛

`run-update --status completed`（以及 REFLECT 的 stage-complete）在任何 worktree 为 `active` 且相对 base 有改动时
拒绝：要么先集成，要么以 failed / abandoned 结束并写明原因（REPORT 写明"改动未集成，已存入 ledger"）。今天的完成门槛
只在"有 commit"时检查提交覆盖，一个从未集成的 run 能被标成 completed，工作随即被释放进 ledger，看起来却像已交付。

### 8.5 清零的验收口径

任何时刻：`WT_ROOT` 下 git 登记的 worktree 数 = status 为 running / paused、且未因长期不动被释放的 isolated run 数；
`run/*` 分支只属于这些 run；每个被删掉的未集成 worktree 都能从 ledger 恢复；`.quarantine/` 为空或已报告。

## 9. 开关与过渡

- 开关只决定新 run 是否建 worktree。每个 run 的行为由它自己 run.json 里的 `worktree_mode` 决定：
  - 开关打开时已经在跑的 shared run：照旧，没有 worktree；落地时 `merge --ff-only` 会保留它们在 main 里的未提交改动
    （不重叠时），重叠则挡下。
  - 开关关闭时仍在跑的 isolated run：继续按 isolated 处理到结束（工具继续定位到 worktree，释放和清扫照常）。
    否则 `pipeline_test` 会去测没有本 run 改动的 main（假绿），旧 `run-commit` 会把改动提交到 `run/<id>` 分支上再被
    清扫掉。
- `run-commit` 对有 active worktree 的 run 不走旧逻辑。

## 10. 不在 pipeline 里的 session

聊天 session（Kiro 会话、SwarmAI 里没在跑 run 的 tab）没有 worktree，照旧直接改 main checkout。它们和 run 之间的保护
是 `merge --ff-only` 本身：不重叠的改动保留，重叠的挡下，它们的改动不会被改写。它们彼此之间的摩擦本设计不解决。

- SwarmAI 聊天 tab 要改代码就开 `direct` profile 的 run。注意 `direct_commit_gate` 是在提交时才拦下、并指向
  `run-create --profile direct` 的，那时改动已经在 main 里了；新建的 run 基于 main HEAD，不包含这些改动。
  这些改动属于发起提交的这个 session，但工具无法可靠地证明这一点，所以**不提供自动搬运**：deny 文本改为提示"先在
  run 的 worktree 里重做这次改动，main 里这份由你自己或人处理"，同步时的兜底检测会把它们列出来（只报告）。
- 落地后，聊天 session 编辑的文件不会被工具改写（ff-only 遇到重叠就停），不存在"对方下一次 Edit 匹配失败"的问题。

## 11. 已确认的决定（XG 2026-10-18）

1. **本地临时分支**：每个 run 一个本地分支 `run/<id>`，不 push，集成后删除，最终 commit 仍直接落在集成分支上。
   不算违反 AGENTS.md 的"never auto-branch"；落地时据此更新那一句。
2. **被未提交改动挡住**：模型退避重试，累计 10 分钟，之后 checkpoint，理由写明被哪个文件挡住（§7.6）。
3. **未集成就结束的 run**：快照存进 ledger，校验可恢复后删除 worktree 和分支（§8.1）。

## 12. 明确不做

- **与重叠的未提交改动做三方合并**：先用 `merge --ff-only` 挡下，P3 统计被挡频率和文件，数据说需要再加。
- **扩展 Bash 写入解析**：边界见 §6.1。
- **daemon 后台重试落地**：先按决定 2 由模型退避，P3 有数据再定。
- **文件锁 / 认领**：`artifact_cli.py` 这类热点几乎每个 run 都要改，run 之间会长时间互相阻塞。
- **按 hunk 记录归属、提交时拆分**：解决不了"测到别人改了一半的代码"，相邻或重叠的改动无法归属。
- **给 SwarmWS 建 worktree**、**为每个 run 重装依赖**、集成时自动解决冲突、跳过重测、push。
- **工具改为从固定在 main HEAD 的快照运行**（防聊天 session 把 main 里的工具改到一半）：§1 的第三个问题在 run 之间
  已解决（run 不再改 main 里的工具），聊天 session 这一半留作后续。

## 13. 失败模式

| 情况 | 结果 |
|---|---|
| 建 worktree 失败 / main checkout 不在集成分支上 | run 以 `shared` 模式继续（今天的行为），warning 可见 |
| run.json 里的路径 / 分支被改 | 工具不读这些字段（§5.2），校验失败就拒绝 |
| 模型用 gate 看不见的方式写了 main | 同步时列出（只报告），由人决定 |
| 两个 run 改到同一段代码 | 后同步的一方 rebase 冲突，由它解决，然后重测、重审变化部分 |
| 两个 run 各自通过、合起来坏 | main 在审查后动过 → 回到同步，至少重测 |
| 审查之后又改了代码 | `changed_since_review` |
| 与别人未提交 / 已暂存的改动重叠 | `merge --ff-only` 拒绝，main 和那些文件都不变；退避 10 分钟后 checkpoint |
| 落地那一刻有人直接提交了 main | `merge --ff-only` 拒绝（不能快进）→ 回到同步 |
| `merge --ff-only` 中途被杀 | 与手动合并被打断相同，`git status` 可见，工具侧无中间状态 |
| run 被取消 / 回收 | 只写 `release_requested`，owner 停下后才释放；释放前快照并校验 |
| 快照校验失败 | 保留 worktree 并报告，不删 |
| deliver 完成但没集成就停住 | 不按 stage 进度回收；14 天后快照释放并进 Radar |
| `WT_ROOT` 下出现未登记目录（含 symlink） | 移入 `.quarantine/`，不删 |
| daemon 重启 | worktree 在磁盘上；启动时清扫一次 |
| run 改了依赖文件 | warning：测试用的是 main 已安装的依赖 |
| 模型在 worktree 里 `git stash` | deny（stash 是所有 worktree 共用的 `refs/stash`） |
| worktree 里留了探针脚本、输出文件、截图，或漏 add 了新模块 | deliver 阻断（`untracked_new_files`），逐个决定 add 或删除；证据必须来自没有未 add 文件的工作区 |
| 最终 commit 很慢（git-defender） | 实测 0.4–2.3 秒；300 秒超时护栏 → `busy: commit_slow`，退避重跑（§7.4） |
| 在没有 hook 的 `--bare` 执行器里建 isolated run | 拿不到 token，拒绝，run 失败并报告 |
| 带 hook 的 session 起的 `--bare` 子进程（继承了全部环境变量）去同步、落地或接管 | 拿不到 token，拒绝，run 不变；它直接改的文件见 §4.2 的剩余风险 |
| hook 签发后命令没跑（被别的 gate deny、超时） | token 120 秒后过期，下次调用重新签发；内存记录在下次签发时清掉（Windows 的文件由清扫删除） |
| daemon 重启（内存里的签发记录丢失） | 已签未用的 token 核销失败，命令被拒、可重试，下次签发走新进程 |

## 14. 分期

- **第一个可交付单元（P0 + P1 + P2）**，完成后打开开关：
  - **P0** run 锁（§4.1，先覆盖本功能涉及的写入者：run-update、stage-start / complete、run-commit、cancel、两个回收器、
    stamper）；归属与接管（§4.2）；hook 签发的一次性 token（§4.2，先实测 Bash 的 `updatedInput` 改写）；共用的
    `own_isolated_run` 判定。
  - **P1** 建 worktree 与校验（§5）；写入 gate（§6.1）；工具定位与 `.pth` 屏蔽、`files_touched` 由工具推导、命令用 main
    绝对路径（§6.2）；`run-diff` 与 goal 卡片锚点（§6.3）；释放、快照、ledger、完成门槛、清扫（§8）。
  - **P2**（commit 耗时已实测，选前台路径 + 300 秒护栏，§7.4）：
    deliver stage-start 里的未 add 新文件阻断和同步（§6.2、§7.2）、树指纹与证据绑定（§7.3）、stage-complete 里的 `merge --ff-only` 落地（§7.4）、
    多 repo（§7.5）、卡片删减（§6.2、§6.3）、Canvas 与 REPORT 刷新（§6.4）、AGENTS.md 分支规则和 Security
    Architecture 列表。
- **P3 观测与按数据决定**：统计被挡次数、被挡文件、等待时长、冲突次数、同步引起的重测 / 重审次数；据此决定是否加
  三方合并、是否改成 daemon 后台重试。

## 15. 验收

每条都要有自动化测试（临时 git repo + 临时 SwarmWS），交付后再用一个真实 run 验证：

1. 两个 run 并行改同一个文件的不同函数：各自测试只看到自己的改动；先后集成都成功；main 上各一个 commit，互不包含。
2. 两个 run 改同一段代码：后者得到 `conflict`；二进制冲突和改/删冲突也必须由模型显式 `git add` / `git rm` 才能继续。
3. main 在审查后被推进：补丁不变 → 只要求重测；补丁变了 → 要求重审变化部分；之前都不落地。
4. 主工作区里别人的改动：不重叠（暂存和未暂存各一个）→ 落地后原样保留；重叠 → `blocked_by_uncommitted`，main 和该文件都不变。
5. 审查后又改了代码 → `changed_since_review`。
6. 写入 gate：四种结构化工具写 main → deny 并给出 worktree 路径；在 worktree 里 `update-ref` main、`worktree remove`
   别人的、`config core.hooksPath` → deny；硬链接文件 → deny；没有 run 的 session 不受影响；gate 出错时 fail-open。
7. run.json 里改 `path` / `integration_branch` → 工具拒绝；`WT_ROOT` 下放 symlink → 清扫移入 quarantine，不删任何东西。
8. 被删除的模块在 worktree 里导入失败（不从 main 回落）。
9. 清零：完成、取消、回收、清除、手动删目录之后 §8.5 成立；未集成的工作（含 rebase 进行中的）能从 ledger 恢复。
10. 新 session 接着跑一个旧 session 崩溃留下的 run：自动接管，gate 对新 session 生效；旧 session 还活着时接管被拒。
11. 从未集成的 run 不能被标成 completed。
12. `worktree_mode: off`：现有测试全部不变地通过。
13. 在 worktree 里 `git stash` / `git -C <wt> stash pop` → deny；非 run session 在 main 里 stash 不受影响。
14. 未 add 的新文件阻断交付：
    - worktree 里有一个未 add 的 `probe.py` 和一张截图：BUILD / TEST 的 stage-start 只列出；deliver 的 stage-start 在
      同步和 Gate-2 之前以 `untracked_new_files` 阻断，两者都不进 Gate-2 diff、`T` 和 N。删掉它们后 `T` 不变，但证据的
      未 add 列表不为空，重测一次后才能落地。
    - 一个被导入的新模块 `foo_helper.py` 忘了 add（测试在 worktree 里能过）：deliver 同样阻断；`git add` 之后 `T` 变化，
      needs_verify 强制重测，证据里的未 add 列表为空才能落地。
    - 同一个 `foo_helper.py` 被当成探针删掉：`T` 不变，证据的未 add 列表不为空 → 强制重测，重测因导入失败变红。
    - 显式 `git add` 的新文件进入 N。
15. 最终 commit 超过超时（用一个故意 sleep 的 hook 模拟）：commit 被终止、worktree 回到 base、返回 `busy: commit_slow`，main 不动。
16. 一次性 token：
    - 没有 hook（模拟 `--bare` 执行器）：建 worktree 的 stage-start 不建 worktree，run 以 failed 结束并写明理由。
    - 带 hook 的 session 里，同一条命令带上 `SWARM_APP_SESSION_ID` 等全部环境变量、但不经 hook 直接执行（模拟继承环境
      的子进程）：建 worktree、同步、落地、接管全部拒绝。
    - token 重放（同一 nonce 第二次）、过期、换 run_id 或子命令、模型自己写的 `SWARM_HOOK_TOKEN=`：全部拒绝。
    - 带 `;` / `&&` / 管道的组合命令：hook deny，不签发。

## 16. 对抗审查记录

### 16.1 r1 → r2（单人 Gate-0）

主要修改：同步与落地拆成两步、`push_ready` 绑定树（r1 的冲突解决代码没被审查）；清理只认显式终止状态；runner 基线
改用 base；等待不超过工具超时。r2 的手写落地机制（index 锁、日志恢复）在 r3 被整体替换，见 16.3。

### 16.2 r2 → r3（四路并行：git 机制实测、生命周期与并发、流程契合、安全）

| 发现 | 级别 | r3 的处理 |
|---|---|---|
| 同步后提交成 commit，所有"diff vs HEAD"的代码（Gate-2 diff、范围统计、交付总结、失败基线）都变空 | HIGH | 改动始终相对 base 未提交，HEAD = base（§7.1） |
| 卡片和 deny 文本用相对路径，模型 `cd` 进 worktree 后跑错工具或找不到 venv | HIGH | 命令一律 main 绝对路径；CLI 在 worktree 里拒绝运行（§6.2） |
| 没有完成门槛，从未集成的 run 能被标成 completed，工作被释放进 ledger | HIGH | §8.4 |
| 同步、重测、重审、reattest、重试让模型多出 4–6 次调用 | HIGH | 折进 deliver 的 stage-start / stage-complete，`run-commit` 幂等（§7.1） |
| goal cycle 的 `git rev-parse HEAD` 在 main 上执行 | HIGH | `run-diff` 锚点，goal 期间不同步（§6.3） |
| direct_commit_gate 引导的 run 不含已在 main 里的改动 | HIGH | 不自动搬运，提示重做，兜底检测只报告（§10） |
| 没有 run 锁，cancel 与 run-update / 落地互相覆盖 | HIGH | P0 run 锁（§4.1） |
| 回收器以 token 为 0 判空壳，会删正在工作的 worktree；非 owner 直接删 | HIGH | 只有 owner / daemon 删，其余写 `release_requested`；看 worktree 活动（§8.2） |
| 崩溃后新 session 接着跑，不是 owner，gate 失效 | HIGH | daemon 自动接管（§4.2） |
| 在 worktree 里 `update-ref` main、`worktree remove` 别人的、改共享 config | HIGH | 判定三按 git common dir 拦（§6.1） |
| 工具按 run.json 里的路径 / 分支操作，可被改写；清扫跟随 symlink 删到 main | HIGH | 路径只从 run_id 推导并校验；未登记目录进 quarantine（§5.2、§8.3） |
| 自写的文件落地绕过 git 的 checkout 保护（`.GIT/hooks`、逃逸 symlink） | HIGH | 改用 `merge --ff-only`，加只读路径校验（§7.4） |
| "保存再删"可能悄悄丢工作（abort 丢冲突解决、空 bundle、非二进制 patch） | HIGH | 快照 commit + bundle，校验可恢复才删（§8.1） |
| 删 `index.lock` 的提示会让别人撤回刚落地的改动；崩溃留锁直到每日清扫 | HIGH | 不再自己持锁，用 git 原生合并（§7.4） |
| 以"没有冲突标记"判断已解决，会丢二进制改动、替模型选改/删 | HIGH | 只看未合并列表为空（§7.2） |
| `rebase --continue` 会开编辑器；大小写改名先写后删会把文件删没 | MED | 编辑器设为 true；大小写改名由 git 原生处理 |
| 多 repo 部分集成没有状态 | MED | `integration: partial`（§7.5） |
| SwarmWS / 未绑定 repo 的文件失去 run 的 commit | MED | 落地后仍按 pathspec 提交（§7.4 第 6 步） |
| 开关关闭时在跑的 isolated run 会假绿或丢提交 | MED | 行为按 run 自己的模式（§9） |
| ledger 放 SwarmWS 会被自动提交进历史，且被 30 天清除 | MED | 放在 git 之外，保留 90 天（§8.1） |
| 清扫只在每天第一次 session 结束时跑；其他状态值没有规则 | MED | daemon 启动 + 每小时；非 running / paused 一律算终止（§8.3） |
| `pipeline_test_gate` 的 3 小时窗口与写入 gate 不一致 | MED | 共用 `own_isolated_run`（§4.2） |
| 硬链接绕过 realpath 检查 | MED | 判定里加 nlink 检查（§6.1） |
| 单条 `worktree prune` 不存在 | LOW | 删对应管理目录（§8.1） |

### 16.3 r3 对 §7.4 的简化（外部审查，2026-10-18）

外部审查指出 r2 的落地等于手写一遍 git 的 `unpack_trees`：独占 index 锁、逐路径三方合并、六类路径矩阵、提交点后
靠日志向前恢复；每轮审查都会冒出新边界，与已放弃的 pytest→tail 防护同形（C055）。它比原生 `git merge --ff-only`
多出的只有一件事：与重叠的未提交改动做三方合并，而这件事多常发生没有数据。实测（§7.4 开头）确认 `--ff-only` 保留
不重叠的暂存 / 未暂存改动、挡下重叠的、在 main 被推进时拒绝。采纳：

- 落地改为原生 `merge --ff-only`，删掉 index 锁协议、日志与恢复、路径矩阵，以及约一半的验收项；三方合并推到 P3 按数据决定。
- 兜底检测只报告，模型不得移动或 restore 被列出的文件（防止搬走 Kiro 会话的工作，R29）。
- 写明现有 commit gate 会拦模型在 worktree 里的 commit，这符合不变量。
- Bash 写入 gate 的边界固定，不再扩展解析器。
- P0 + P1 + 简化后的 P2 作为第一个可交付单元，做完就能打开开关。

### 16.4 r3 补充（外部审查第二轮，2026-10-18）

| 发现 | 级别 | 处理 |
|---|---|---|
| stash 是所有 worktree 共用的 `refs/stash`：worktree 里 stash 的半成品能在 main 里 `stash pop` 出来（审查者实测，本文作者复测确认） | HIGH | 判定三加 stash，`WT_ROOT` 下所有 worktree 的全部子命令 deny；锚点用 `run-diff --mark`（§6.1） |
| 改动集用 `add -A`，worktree 里的探针脚本、输出、截图会被提交进公开 repo | HIGH | 已跟踪文件的改动和删除自动纳入，新文件必须显式 `git add`；未 add 的起初列为 warning，r4 改为阻断 deliver（§16.5） |
| 最终 commit 可能超过 Bash 工具 600 秒上限，原文只写"实测后配超时" | MED | 实测 0.4–2.3 秒（git-defender hook 同步执行）→ 前台路径 + 300 秒护栏；后台路径 r4 删除（§16.5） |
| 归属和写入 gate 默认 run 跑在带 hook 的 session 里，`--bare` 执行器上建 isolated run 等于没有 gate | 建议 | 不变量：只有带 hook 的 session 能有 isolated run，否则 run 失败并报告；证明方式 r4 从环境变量改为一次性 token（§16.5） |

判定三的 stash 规则比建议略窄：只在 `WT_ROOT` 下的 worktree 里对所有 session deny；main checkout 里，有 isolated run 的
session 已被判定一拦下，其他 session 照常可用。理由：run 的 worktree 里不能再产生 stash，main 的 stash 列表里就不会混进
run 的半成品；而在 main 里禁掉所有 session 的 stash 会误伤非 pipeline 的正常使用。

### 16.5 r3 → r4（XG 审查，2026-10-18）

| 发现 | 级别 | r4 的处理 |
|---|---|---|
| "默认走后台落地"没有依据：实测 commit 0.4–2.3 秒，后台队列只是多出一个 `integrating` 状态和三个新问题 | HIGH | 只保留前台落地 + 300 秒护栏，删掉后台路径和 `integrating` 状态。P2 第一步在真实 run 上复测，p95 超过约 400 秒才设计后台路径，届时必须满足：step 3 的 commit 前重新计算 `T`；集成期间写入 gate deny 对该 worktree 的写；确认 launchd 下 git-defender 的 PATH / HOME（R13）（§7.4） |
| 未 add 的新文件只给 warning 会造成假绿：漏 add 的 `foo_helper.py` 让 TEST 全绿、main 上导入失败 | HIGH | deliver 的 stage-start（同步和 Gate-2 之前）和 stage-complete 以 `untracked_new_files` 阻断，模型逐个决定 add 或删除；证据记录测试时的未 add 列表，落地要求为空，所以"把漏 add 的模块当探针删掉"也会被重测抓住（§6.2、§7.3、§7.7） |
| `SWARM_HOOKED_SESSION` 会被 `claude --print --bare` 子进程继承，证明不了 hook 存在 | MED | 改为 hook 每次签发的一次性 token：只存哈希、绑定 session / run / 子命令、120 秒、单次有效，只签单条 artifact_cli 命令；建 worktree、接管、同步、落地、释放都要求 token；P0 先实测 Bash 的 `updatedInput` 改写（§4.2） |

第二条比审查建议多收紧了一处：审查说"删掉探针 `T` 不变"，这没错，但 `T` 相同不代表测试能代表 N（测试可能依赖了被删的
文件），所以删掉测试时存在的未 add 文件后仍要重测一次。代价是留过探针的 run 多跑一次 runner。

### 16.6 P0–P2 实现记录（2026-10-18）

代码：`core/run_lock.py`、`core/hook_token.py`、`core/run_worktree.py`（git 机制）、`scripts/pipeline_worktree.py`
（artifact_cli 一侧的策略）、`core/run_owner_gate.py`、`core/worktree_write_gate.py`、`jobs/handlers/worktree_sweep.py`，
artifact_cli / pipeline_test / router / stamper / needs_human_review 只加调用点。开关 2026-10-21 起默认 `on`。

实现中发现并修掉的一个 git 细节：给临时 index 复制工作区 index 时必须保留 mtime（`copy2`）。git 用 index 文件本身的
mtime 判断条目是否"racy"；复制出的新 mtime 会让同一秒内、同大小的修改被当成未修改，`T` 漏掉改动（测试稳定复现）。

与设计的偏差，均为有意收窄：

| 位置 | 实现 | 原因 |
|---|---|---|
| 判定三（§6.1） | 对所有 session：run worktree 里的 stash、改别的 run 的 `run/*` 分支、指向 `WT_ROOT` 的 worktree 命令。只在 run worktree 里执行、或本 session 拥有 isolated run 时，才禁止动其他 ref / config / gc --prune 等 | 按 common dir 对所有 session 一律禁止，会挡掉主工作区里正常的 `git tag` / `git branch -D`（发布流程就要打 tag）。防的是"从 worktree 绕过集成"，这个收窄不影响它 |
| 接管与 token（§4.2） | 只在开关为 on、或该 run 是 isolated 时生效 | 开关关闭时行为与今天完全一致（不改写命令、不拒绝组合命令） |
| 运行期 `release`（§8.2） | owner 的 `run-update` 到终止状态、带 token 时立即释放；否则写 `release_requested`，由 daemon 清扫 | 与设计一致；补充：owner 在终止后再调 `stage-start` 会被拒绝并当场释放 |
| 卡片删减（§6.2、§6.3） | 卡片不改；isolated run 的 stage-start 输出 `card_override`，逐阶段覆盖卡片里不适用的步骤（§16.8） | shared 模式仍需要卡片里的这些步骤。开关 2026-10-21 起默认 on，但显式 off、hookless 执行器和 worktree 创建失败仍走 shared，所以卡片保留，card_override 继续生效 |
| Canvas 已有 rail 行的路径刷新（§6.4） | 不改前端存的行；读文件时把已释放 worktree 下的路径映射到 main（§16.8） | rail 行的持久化在前端，一处后端映射覆盖所有旧行 |
| 14 天闲置被释放后的重建（§8.3） | 下一次 stage-start 自动从 ledger 重建（§16.8） | 快照另记改动集的树和未 add 文件，重建后未 add 的仍是未 add |
| srcgraph 缓存清理（§8.1） | 释放时删除（§16.8） | |

自动化测试：`tests/test_run_worktree.py`、`test_pipeline_worktree.py`、`test_worktree_gates.py`、
`test_pipeline_test_worktree.py`，覆盖 §15 的 1、2、5、6、7（ff-only 部分）、8、9、10、11、13、14、15、16。
§15 的 3、4（两个 run 并发落地 / 同文件冲突）的端到端测试见 §16.8；真实 run 上的验证留到打开开关之前做。

### 16.7 实现的对抗审查（2026-10-18，独立 reviewer）

| 发现 | 级别 | 处理 |
|---|---|---|
| run-create 的清扫按调用方的 `SWARM_WORKSPACE` 判断"run 记录不存在"，测试或另一个工作区会释放本机所有活 worktree | HIGH | `WT_ROOT` / ledger 改为从 app data dir 推导（测试的 `SWARM_DATA_DIR` 自然隔离）；每个 `WT_ROOT/<run_id>/` 记下所属工作区，清扫只看自己工作区的；"记录不存在"的释放只在 daemon 清扫里做 |
| `_remove` 在 git 拒绝后 `rmtree`，路径未校验，一个被换成 symlink 的 run 目录能让它删掉 main checkout | HIGH | 动路径前要求无 symlink 且是 git 登记的 linked worktree；git 拒绝就报错，永不 `rmtree` |
| isolated run 落地绕过了 D7 专家覆盖检查 | HIGH | 落地前跑同一个 D7 检查（含 Red Team）；另外要求 deliver 派出的每个 Gate-2 专家在"被审的树"记录之后完成过（修复后改了代码就要重审） |
| 整条命令持有 run 锁：cancel 等 30 秒后 500；落地中途看不到 cancel | MED | cancel 等锁 5 秒，拿不到就写无锁的 `.cancel_requested` 并后台重试；下一个拿锁的命令先应用它；落地的 `still_live` 也看它 |
| 写入 gate 在每次工具调用都进线程池 | MED | 先内联判断有没有任何 run worktree，没有直接返回 |
| token 可伪造 | MED | 保持现状，按 §7.3 的威胁模型写明（§4.2） |
| token 发给任何提到 artifact_cli.py 的命令；参数重复/缩写时 gate 与 argparse 解读不同 | MED | 只认 python 解释器直接运行脚本；`--run-id` 等重复或缩写一律 deny；artifact_cli 的 argparse 关闭缩写 |
| gate fail-open 一次就让 run 永久 failed | MED | 前两次无 token 只拒绝、可重试，连续第三次才 failed |
| 多 repo 落地后后面的 repo 状态丢失 | MED | 每次按 repo 名重新取当前 entry；加了两 repo 落地测试 |
| 清扫在 git 出错或 worktree 被 detach 时把活 worktree 移进 quarantine | MED | 只有确定不是 worktree（没有 `.git`）或 git 成功列出且不含它时才 quarantine；git 说不清就保留并报告 |
| 同步被打断（超时、kill、`rebase --abort`）后 HEAD 停在快照上，之后永远 `worktree_invalid` | MED | 同步开头识别"HEAD == 快照、无 rebase"并 `reset --soft base`；rebase 超时会 abort 并恢复；落地后崩溃导致的"已合并未记录"在下次 stage-complete 自动补记 |
| 重跑 deliver stage-start 会把审查后的改动重新记成"已审" | MED | 被审的树只在变化时重写并记时间，落地要求专家在那之后完成（见 D7 一行） |
| 其余 LOW | LOW | 证据缺 worktree 记录时拒绝落地；接管只由 owner 类命令触发；B 只写一次（记下目标 repo）；stamper 等锁 1 秒；sitecustomize 放独立目录、只在 worktree run 时加入；`WT_ROOT` 只在开关打开时加进受限模式的允许目录；gate 无结论时返回 `{}` 而不是 approve |

P0 的 `updatedInput` 探针按真实拓扑重跑：Bash 链（改写 hook + approve hook）之外再挂 `Write|Edit|Bash`（返回 approve）
和写入 gate 同样的 matcher（返回 `{}`）两组，改写后的命令仍被执行。

实现中另一个发现：git 调用会继承 shell 里泄漏的 `GIT_CONFIG_COUNT/KEY_n/VALUE_n`（本 session 的审查子 agent 留下
`core.hooksPath=/tmp/...` 和 `user.name=t`），能让最终 commit 跳过 git-defender。`run_worktree.git()` 现在丢弃所有
`GIT_CONFIG*` 和 `GIT_DIR` 类变量。

### 16.8 已知问题修复（2026-10-18，开关仍为 off）
| 问题 | 修法 | 测试 |
|---|---|---|
| token 可伪造（§16.7 MED） | 签发记录只放 daemon 内存（`core/hook_token.py`）；CLI 经 owner-only 的 Unix socket（按端口区分，dev 与 daemon 可并存）核销；daemon 不在即拒绝；`main.py` 启停服务；服务没起来时 `run_owner_gate` 不签发并 deny。没有 AF_UNIX 的平台保留文件方式 | 手写的旧格式 token 文件不被接受；签发方停掉后核销失败、不再签发；socket 0600、目录 0700；gate 在服务停掉时 deny |
| isolated run 的 D7 预检没有覆盖开关 | 不再提供覆盖（2026-10-19 删除 `stage-complete --force --reason`：真实 run 里 force 被模型用来绕过检查）。预检的补救（补派专家、重审）不需要人 | 缺专家时拒绝，补派后落地 |
| 卡片的步骤在 isolated run 里不适用（§6.2、§6.3） | stage-start 输出 `card_override`（build：files_touched 自动推导；test：不做逐个修复的 commit；goal_cycle：用 `run-diff`；deliver：跳过暂存与 run-commit），卡片不动 | build / deliver 的输出 |
| 释放后 rail 行 404（§6.4） | `run_worktree.landed_path`：已不存在、位于某个 run worktree 下、且该 run 已落地的路径 → main 里的同一文件；只用于 `GET /workspace/file`（读），home 检查照旧 | 释放前不映射、释放后映射、不存在的文件和 `..` 不映射 |
| 闲置释放后不能自动重建（§8.3） | 快照另记改动集的树 `T` 和未 add 文件列表；下一次 owner 的 stage-start（每个 stage-start 都签 token）从 bundle 取回快照，在原 base 建 worktree，文件 = 快照、index = `T`，校验 `T` 和未 add 列表都一致才算成功，否则删掉重建的 worktree 并报告。重建前 deliver 的 `stage-complete` 拒绝落地（`worktree_released`），`completed` 也被挡（改动只在 ledger 里）——此前这两处会把只在 ledger 里的改动当成已交付放过去 | sweep 释放 → 完成和落地被拒 → stage-start 重建，`T` 相同、探针仍未 add |
| srcgraph 缓存不清（§8.1） | `_remove` 删掉该 worktree 路径的 `srcgraph-*.json` | 释放后缓存文件消失 |
| deliver 先记 completed 再落地，退避中的 run 会被 24 小时规则释放（§16.7 LOW） | 每次落地尝试记 `landing_attempt_at`，24 小时规则取活动时间和它的较大者 | 有近期尝试时不释放，没有时释放 |
| §15 的 3、4 只有单侧测试 | 两个线程同时落地：恰好一个成功，另一个 `needs_resync`、worktree 回到 base，同步后叠在上面落地；同文件冲突经 CLI 全程：冲突 → 在 worktree 里解决并 `git add` → 重跑 stage-start 同步 → 重测 → 落地 | 两个新测试 |

### 16.9 任意 repo 的 worktree、owner gate 的范围、开关持久化（2026-10-19）

开关从这一版起可以打开（XG 2026-10-19 指示：这是产品能力，要支持各种 repo）。三处改动：

**任意 repo，写的时候才建。** 开关打开时，run 第一次进 plan / build / goal_cycle 就定为 isolated（要 hook token 证明），
同时建好 `WT_ROOT/<run_id>/`，写入 gate 从这时开始对它生效。项目 bindings 里登记的代码 repo 照旧当场建 worktree，
但只是提示，不再是前提。owner 往别的 git repo 的 main checkout 写文件时（Write / Edit / 能解析的 Bash 写、
`git add` 这类改 index 的 git 命令），gate 拒绝这次写，给出一条单条命令
`<python> <artifact_cli.py> run-repo-add --project P --run-id R --path <repo>`；这条命令在 run 锁里、凭 token 建 worktree，
然后模型在 worktree 里的同一相对路径重做这次写。worktree 不在 gate 里建：gate 只有 3 秒，大 repo 建 worktree 要更久，
而且"先告诉路径、后台再建"会让模型抢先写出一个普通目录、让创建失败。

- **不建 worktree、照旧直接写的地方**（`run_worktree.repo_for_write`，只读文件不起 git）：不是 git repo；
  `.git` 是文件的（linked worktree、submodule）；任何 run worktree；SwarmWS 本身；home 目录或 app data 目录本身是 repo；
  带 `.gitmodules` 的 repo；Brazil workspace 里的 package（`<ws>/src/<Pkg>` 且 `<ws>/packageInfo` 存在）；main checkout 在
  detached HEAD，或有 rebase / merge / cherry-pick / revert / bisect 没做完。
- **main checkout 的位置以 git 为准**（`run_worktree.entry_main`）：worktree 在盘上时，由它自己的 git 指针推出 main，
  再和 entry 里记下的 `main`、`git_common_dir` 对上，并确认它是那个 main 登记的 linked worktree；worktree 已经不在
  （释放后）时用记下的 `main`，但它的 common dir 必须是记录的那个。旧 entry 没有 `main` 字段的，worktree 在盘上就从
  git 推，不在盘上才用 bindings 当提示。改 run.json 里的 `main` 或 common dir → `worktree_invalid`。
- **key**：`repo_name` 仍是目录名；同一个 run 里另一个同名但不同的 repo 用 `<名字>-<main 路径 sha256 前 8 位>`。
  集成锁改成按 repo 的 common dir 分，不再按名字。测试 runner 按 entry 记下的 `main` 找自己那个 repo 的 worktree。
- **依赖目录**：新 worktree 把 main 里已有的 `node_modules`、`.venv`、`venv`（以及 SwarmAI 的 `desktop/node_modules`）
  软链接进来，写进 info/exclude，不算未 add 文件。SwarmAI 用 main 的 venv 跑测试、去掉 main 的 backend 导入路径的做法不变。
- **没有 token 也没有 bindings repo 的 run 定为 shared**，并给出警告：它还没有任何东西被隔离，而没有 hook 的后台执行器
  不能因此让所有项目的 run 都失败。有 bindings repo 的 run 仍按 §4.2 处理（两次拒绝、第三次 failed）。

**owner gate 只管该管的 run。** 以前开关一开，`run_owner_gate` 对所有项目的所有 run 都要求单条命令和 token。
重放最近 60 个会话，394 条需要 token 的命令全是 `cd … && source … && python …` 组合写法，全会被拒。现在：
worktree_mode 已定为 `shared` 的 run，行为和开关关闭时完全一样；还没定模式的 run，只对决定模式的那次
plan / build / goal_cycle 的 stage-start 要求单条命令和 token；isolated 的 run 照旧，并且 `run-repo-add` 也要 token。
拒绝组合命令时，提示里直接给出同一条调用的单条写法。命令文本来自 CLI 自己写进 run.json 的 `cli`
（`sys.executable` 和 `artifact_cli.py` 的绝对路径；打包的 daemon 不知道源码在哪）。run.json 不可信，所以只有
`cli` 指向一个存在的 python 解释器和一个存在、且不在任何 run worktree 里的 `artifact_cli.py` 时才打印它，否则打印相对写法。
CLI 自己输出的后续命令（`next_action`、`complete_with`）也一律是绝对路径的单条命令。

残余风险（按 §7.3 的威胁模型接受）：模型能直接改 run.json，把 `isolated` 改成 `shared` 就能同时躲开写入 gate 和 owner gate。
这在本版之前就存在（写入 gate 一直信任 `worktree_mode`），owner gate 的收窄没有多开能力：shared 的 run 不做任何 worktree 操作，
token 在那里本来就没有要保护的东西。另一个已知缺口：gate 解析不了的 Bash 写入，进入一个还没有 worktree 的 repo 时不会被拦，
改动留在那个 repo 的 main 里，交付时照旧按文件提交（`run_commit_isolated` 交给原来的 pathspec 提交），不会丢。

**开关持久化。** `pipeline.worktree_mode` 进了 `DEFAULT_CONFIG`（2026-10-21 起默认 `on`：缺这个键或 config 读不出来都算 on，只有 `off` 关闭；已经写了 `off` 的安装保持不变），`PUT /api/settings` 接受并校验
`{"worktree_mode": "on" | "off"}`；`AppConfigManager.update()` 发现 config.json 在读入后被改过（手改）时，先重新读盘再合并，
不再用启动时的缓存覆盖手改。`mode_enabled()` 每次直接读 config.json，打开或关闭都不用重启。

未做（下一个 run）：测试 runner 只会测 SwarmAI，而 run 进行期间 `pipeline_test_gate` 拦掉所有直接跑测试的命令，所以非 SwarmAI
repo 的 run 现在没有被支持的测试路径；"worktree 里测试是否导入了 main 的代码"这项检查要挂在那条路径上，一起做。
另外，写入 gate 用整条命令最后一个 `cd` 解析所有相对重定向（`echo > a; cd 别处` 会把 `a` 当成写在别处），会误拦；
修它需要按段保留重定向的分词，本版不做。

