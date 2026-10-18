---
title: 每个 pipeline run 一个 worktree — 隔离、集成、清理
date: 2026-10-18
created: 2026-10-18
updated: 2026-10-18
project: SwarmAI
status: APPROVED r2 — §9 三个决定 XG 2026-10-18 已确认；Gate-0 修订见 §14
related: parallel-session-commit-isolation.md（本文是其中"方案二"的完整设计，取代该草稿）; pipeline-run-budget.md; pipeline-speed-systematic-fix.md
author: Kiro
---
# 每个 pipeline run 一个 worktree：隔离、集成、清理

## 1. 问题一句话

所有 session 共用一个 `swarmai` 工作区和一个 git index。一个 run 测到的是别人改了一半的代码，
提交时分不清哪些改动是自己的（`run-commit` 只能按文件提交，同一文件里两个 run 的改动分不开），
一个 run 把 `artifact_cli.py` 改到一半时，其他 run 的每次 CLI 调用都会跟着出错。

## 2. 目标与边界

- **目标**：每个 run 在自己的 worktree 里改代码、跑测试；交付时由工具把它的改动集成回 main，
  冲突显式出现、由 run 自己解决；run 结束（任何结束方式）时 worktree 和分支被清零，不留残留、不丢工作。
- **不变的东西**：最终 commit 仍然直接落在 `main` 上，不 push；工具（`artifact_cli.py`、
  `pipeline_test.py`）从 main checkout 运行；run.json、artifact、REPORT 仍在 SwarmWS。
- **范围外**：不给 SwarmWS（工作区 repo，存 run.json 和 DDD 文档）建 worktree；不处理非 pipeline
  的聊天 session 之间的冲突（§7）；不做 hunk 级归属，也不做文件锁（§8）。

## 3. 总体结构

```
run-create ──► 建 worktree（每个绑定的代码 repo 一个）──► run.json.worktrees[]
   │
   ├─ 模型在 worktree 里改代码 ◄── 写入 gate：拒绝写 main checkout，提示对应的 worktree 路径
   ├─ 测试：pipeline_test.py 从 main 运行，ctx.repo = run 的 worktree
   │
DELIVER 开头 ──► run-sync：压成一个 commit → rebase 到最新 main（冲突在这里由 run 解决）
   │                → 在 rebase 后的树上 verify → Gate-2 / meta-review 审的是 rebase 后的 diff
   │                → stage-complete 的 push_ready 绑定这棵树（tree 指纹）
DELIVER 末尾 ──► run-integrate（取代 worktree 模式下的 run-commit 位置）
   │                main 没动 → 直接落地；main 动过 → 重新 sync，补丁不变只需重测，补丁变了要重审
   │                落地：锁 main 的 index → 先比较交换推进 main（提交点）→ 再写主工作区 → 换上新 index
   │                → 改写 run.json 的 commits[] / files_touched
   │
显式终止状态 ──► release_run_worktrees()：已集成 → 删；未集成 → 存 patch + bundle 后删
每日 / run-create ──► 兜底清扫：只按显式状态回收残留 worktree 和 run/* 分支
```

开关：`config.json["pipeline"]["worktree_mode"]`（`off` | `on`）。P1、P2 全部落地并验收前保持 `off`，
`off` 时所有行为与今天完全一致。

## 4. 创建与映射

### 4.1 什么时候、给哪些 repo 建

- 在 `run-create` 时建（模型还没开始改任何文件）。profile 此时可能还没定（EVALUATE 后才分类），
  research / docs 这类不改代码的 run 也会建一个，代价是一次 `git worktree add`（实测 5.9 秒、119 MB），
  可以接受；换来的是"没有任何时间窗口让模型在 main 上改代码"。
- 建给哪些 repo：项目 `Projects/<P>/bindings.yaml` 里 `kind: code-repo` 的资产（SwarmAI 是自托管，
  `worktree: <source-repo>` 指向 main checkout）。没有绑定代码 repo 的项目不建，行为与今天一致。
- 建在 `~/.swarm-ai/worktrees/<run_id>/<repo_name>/`。必须在 `$HOME` 下：前端 `isWorkspaceFilePath`
  只认 `/Users|/home`，后端 `_resolve_file_path` 只允许 `$HOME` 下的绝对路径，`/tmp` 下的文件在 Canvas
  里打不开。
- 命令：`git -C <main> -c core.hooksPath=/dev/null worktree add -b run/<run_id> <path> <main HEAD>`。
  基于 main 的 HEAD，所以别的 session 没提交的改动不会进来。由 `artifact_cli`（子进程）执行，
  不经过 PreToolUse，因此 `pipeline_test_gate` 里"禁止 `git worktree add`"的规则不受影响、也不放宽。
  `core.hooksPath=/dev/null` 只用于 `worktree add`（与今天的基线 worktree 一致）；worktree 里的
  commit 和 rebase 照常经过公司的 git-defender hook，不覆盖。
- **集成分支**：创建时记录 main checkout 当前检出的分支（`git symbolic-ref HEAD`，SwarmAI 是 `main`，
  别的 repo 可能是 `mainline`），之后集成都对这个分支做，不写死 `refs/heads/main`。main checkout 处于
  detached HEAD 或 merge / rebase 进行中时不建 worktree，回退 `shared`。
- Windows 上软链需要权限：用 junction，失败则回退 `shared`。

### 4.2 worktree 初始化

| 项 | 做法 | 原因 |
|---|---|---|
| `desktop/node_modules` | 软链到 main 的 `desktop/node_modules` | 前端测试和 tsc 需要；vite 按 realpath 解析，单份 React |
| 软链不被 git 当作改动 | 在 `$GIT_COMMON_DIR/info/exclude` 加 `/desktop/node_modules` | `.gitignore` 的 `node_modules/` 带斜杠，不匹配软链，否则它会显示成未跟踪文件进入 change set |
| Python | 不建 `.venv`，统一用 main 的 `backend/.venv/bin/python` | 软链 `.venv` 会让 conftest 报 WRONG PYTHON；基线 worktree 今天就是这么跑的 |
| 依赖变更 | run 改了 `pyproject.toml` / `package-lock.json` 时 warning：测试用的是 main 的已安装依赖 | 不为每个 run 重装依赖 |

### 4.3 run.json 里的映射

```json
"worktree_mode": "isolated",            // isolated | shared（创建失败时的回退）
"worktrees": [{
  "repo": "/Users/gawan/Desktop/SwarmAI-Workspace/swarmai",   // main checkout，realpath
  "path": "/Users/gawan/.swarm-ai/worktrees/run_x/swarmai",
  "branch": "run/run_x",
  "integration_branch": "refs/heads/main",
  "base": "<最近一次 sync 时集成分支的 sha；创建时 = main HEAD>",
  "state": "active",                    // active | integrating | integrated | released
  "created_at": "..."
}]
```

- 创建失败（磁盘、git 锁、repo 不存在）时 run 照常创建，`worktree_mode: "shared"`，打印 warning，
  之后全部走今天的共享模式。失败可见，但不阻塞 run。
- `run-create` 的输出带上 worktree 路径和一行规则："本 run 的代码在 `<path>` 下改，不要改 `<repo>`"；
  `stage-start` 对 build / goal_cycle / review / test / deliver 再打印一次。

## 5. 运行期：让模型和工具都落在 worktree 里

### 5.1 写入 gate（新的 PreToolUse gate）

模型的记忆和上下文（`.context/KNOWLEDGE.md` 等）写的都是 main checkout 的绝对路径，只靠提示挡不住。

- **作用对象**：Write、Edit、MultiEdit、NotebookEdit，以及 Bash 写入（复用 `file_change_classifier`
  的 redirect / tee / cp / mv / sed -i / 解释器 heredoc / 删除解析）。目标路径先按 `input_data["cwd"]`
  以及同一条命令里前面的 `cd X &&` 补全，再 `realpath`（经软链写进 main 的情况也能识别）。
- **判定一（写 main）**：本 session 拥有一个非终止状态、且有 `state=active` worktree 的 run，目标落在
  该 worktree 对应的 main checkout 下 → **deny**，消息给出替换后的 worktree 路径。
  - 不复用 `_session_run_state` 的"只认 running、3 小时内有活动"条件：BUILD 里 3 小时不写 run.json
    很正常，那样 gate 会悄悄失效。这里按"run 状态不是终止 + 有 active worktree"判定，没有时间窗口。
  - Bash 在 main checkout 里执行会改 git 状态的命令同样 deny：`commit / add / rm / mv / restore /
    checkout / switch / stash / apply / am / reset / clean / merge / pull / rebase / cherry-pick / revert /
    update-index`；识别 `git -C <dir>`、`--work-tree`、`GIT_DIR` / `GIT_WORK_TREE` 以及前置的 `cd`。
- **判定二（写别人的 worktree）**：目标落在 `~/.swarm-ai/worktrees/<run>/` 下，而该 run 不属于本
  session，或已不处于 active → **deny**（任何 session 都适用）。这也防止 release 之后模型在已删除的路径
  上重新建出一个游离目录。
- **不拦的写入**：SwarmWS 里的 DDD 文档、artifact、`.context`；没有 worktree 的其他 repo。
- **读不拦**：Read / Grep 读 main 时附加一句 additionalContext（"你在读 main；本 run 的副本在 X"），
  不 deny。读本身无害，但读 main 再去改 worktree 会导致 Edit 的 old_string 对不上。
- **注册**：matcher `Write|Edit|MultiEdit|NotebookEdit|Bash`（单独一组，不进 4 小时的 Bash 链），
  自带 try/except 和超时，fail-open；运维旁路 `SWARM_WORKTREE_GATE_FORCE=1`（只在 daemon 环境里有效）。
  符合 `build_hooks` 的准入三条：通用（任何用户的 run 都需要隔离）、对的层次（每次写入才拦得住）、
  fail-safe。
- **漏网检测**：解释器代码（`python -c` 里 `open(...,'w')`）、脚本文件这类 gate 看不见的写入，
  在 run-sync 时兜底（§6.2 第 1 步）。
- **受限权限模式**：`global_user_mode=False` 或非 owner 的 session 由 `can_use_tool` 限定可写目录；
  有 worktree 的 run 把 `~/.swarm-ai/worktrees/<run_id>/` 加入该 session 的允许目录。

### 5.2 工具定位到 worktree

工具始终从 main checkout 运行：run 改不到正在约束它的 gate 代码，也不会被别的 run 改了一半的工具拖垮。
带 `--run-id` 时按 run.json 定位 worktree：

| 位置 | 今天 | 改为 |
|---|---|---|
| `pipeline_test.execute()` | `ctx = _Ctx(_repo_root())`，即脚本所在 repo | 读 `run_state.worktrees`，校验它是同一 git common dir 下登记的 worktree，`ctx.repo = worktree` |
| `pipeline_test._python` | `repo/backend/.venv` 不存在时回退 `sys.executable` 并 warning | 默认用 main 的 venv python（`_BACKEND_DIR/.venv`），不再 warning |
| 主 venv 的 editable `.pth` | 把 main 的 `backend` 放进 `sys.path`（排在 site-packages 之后） | worktree 里删掉或改名的模块会从 main"回落"导入，测试通过了一个已不存在的模块。runner 插件目录里放一个 `sitecustomize`，在 `PIPELINE_TEST_STRIP_ROOT` 设置时把 main 的 `backend` 从 `sys.path` 去掉；插件目录在 `PYTHONPATH` 上，子进程也继承 |
| `artifact_cli._evidence_roots` | `[SWARM_TEST_REPO_ROOT, main, SwarmWS]` | 带 run 时 worktree 排第一 |
| `_repo_root_for_file`（相对路径） | 先试 cwd（= main） | 先试 run 的 worktree |
| `_changed_files_scope`（相对路径） | 锚定到脚本所在 repo | 锚定到 run 的 worktree |
| `security_hooks._canonical_rel_bases` | SwarmWS + main | 加上活跃 run 的 worktree |
| `files_touched` | 自由格式，多为绝对路径 | 运行期记 worktree 绝对路径（build.md 本来就要求绝对路径）；集成时改写为 main 路径（§6.4 落地第 6 步） |
| 证据新鲜度比较 | `pv.evidence_freshness` 按路径字符串比 digest key | 两边都按"repo + repo 内相对路径"归一后比较；集成之后的检查固定比对 `N:path` 的 blob，不比主工作区文件（那里可能混着别人的改动） |
| 失败分类的基线 | `_baseline` 用 `_head_sha(ctx.repo)` | 用 run 的 `base`（最近一次 sync 时集成分支的 sha）。run-sync 把改动提交进 worktree 后，worktree 的 HEAD 已经包含本 run 的改动，拿它当基线会把本 run 造成的回归都判成"既有失败" |
| "要不要重测"的判断 | — | 按树的内容指纹判断，不按 HEAD：把改动提交成 commit 不改变树，不应触发重测 |

`pipeline_test` 的 srcgraph 缓存按 repo 路径分文件：每个 run 首次是冷缓存（重新解析 backend 下全部 .py，
秒级）。release 时删掉该 worktree 的 srcgraph 缓存文件，不让它们堆积。其余缓存（results、durations、
slots）按内容或全局共享，不受影响；`slots` 本身就是跨 worktree 的全局并发上限。

### 5.3 换 session 恢复

`run-resume` 今天不检查也不改 `session_id`，换一个 session 恢复后，写入 gate 和 `pipeline_test_gate`
都不会对新 session 生效。改为：`run-resume` 把 `session_id` 改成当前 session（旧值记进
`session_history[]`），并重写 session→run 映射。worktree 已不在（长期暂停被回收、被手动删除）时，
`run-resume` 从 ledger 里的 bundle + patch 重建它，再恢复 run。

## 6. 同步与集成

### 6.1 为什么分成两步

今天 DELIVER 的顺序是：审查（Gate-2、meta-review、收敛）→ `stage-complete`（算出 `push_ready` 上界、
写 REPORT、推进到 REFLECT）→ `run-commit`。如果 rebase 和冲突解决只发生在最后那一步，解决冲突时写的代码
既没被审查，也不在 `push_ready` 证明的范围里。所以拆成两步：

- **run-sync**（DELIVER 开头，审查之前）：把 run 的改动对齐到最新 main，冲突在这里解决，之后的
  verify 和审查都针对 rebase 后的代码。
- **run-integrate**（今天 `run-commit` 的位置）：只落地被审查过、被 `push_ready` 证明过的那棵树。

### 6.2 run-sync

只动本 run 的 worktree，不碰 main checkout，所以只拿本 run 的锁，不拿全局锁。也可以在 DELIVER 之前
随时调用（例如 TEST 之前先对齐 main）。

1. **漏网检查**。`files_touched` 里有指向 main checkout 的路径 → 拒绝并列出，要求把改动移到 worktree。
   另外列出 main checkout 里修改时间晚于 `run.created_at`、且在本 run 改动范围内的未提交文件，标为
   "可能是你的"（gate 看不见的写入），避免到集成时被自己的残留挡住。
2. **压成一个 commit**。`git reset --soft <base>`，暂存 worktree 里全部已跟踪改动和未忽略的新文件
   （worktree 里只有本 run 的改动，不需要按 `files_touched` 挑），提交为一个 commit（信息和 trailer
   与 `run-commit` 相同）。不在 `files_touched` 里的文件补记进去并 warning。模型在 worktree 里自己做过的
   commit 也一起被压进来，不会绕过审查或缺 trailer。
3. **rebase 到集成分支当前的 sha `C`**。有冲突：保持 rebase 进行中，返回 `conflict` 和文件列表；再次调用时
   冲突文件里已没有冲突标记才 `git add` + `rebase --continue`。成功后 `base := C`。
4. **返回树指纹** `T`（worktree HEAD 的 tree，不含 `.aidlc/deliverables/<run_id>/`），以及最新证据的
   tree digest 是否与之一致；不一致返回 `needs_verify` 和可直接执行的 runner 命令。

### 6.3 审查和 push_ready 绑定到树

- Gate-2 / meta-review 的输入是 worktree 里 `base..HEAD` 的 diff（rebase 之后的）。
- `stage-complete --stage deliver` 在算 `push_ready` 上界时额外记录 `push_ready_tree = T` 和
  `push_ready_base = base`。证据新鲜度照旧检查（按 tree digest）。

### 6.4 run-integrate

**前置条件**（任一不满足就返回对应状态，不做任何写入）：
- 原 `run-commit` 的全部 gate：`push_ready`、`files_touched` 非空、D7、Red Team；`--force` 语义和审计记录不变。
- worktree 没有未提交改动，且 HEAD 的树等于 `push_ready_tree`。否则返回 `changed_since_review`：审查之后又改了
  代码，需要回到 sync → verify → 审查。
- 集成分支当前 sha 等于 `push_ready_base`。否则 main 在审查之后动过，本次调用内部做一次 rebase：
  - 有冲突 → 返回 `conflict`（同 6.2 第 3 步），解决后要重新 verify 和审查；
  - 无冲突、rebase 前后 `git patch-id` 相同（补丁内容没变）→ 返回 `needs_verify`，重测通过后用
    `run-integrate --reattest` 按新证据重算 `push_ready` 上界并更新 `push_ready_tree / base`；
  - 无冲突但补丁变了 → 返回 `needs_review` 和 `git range-diff` 命令，只审变化的部分，然后 `--reattest`。
  - 一次调用不在内部循环重试，main 持续在动时每次都返回一个确定的下一步。
- main checkout 当前检出的就是集成分支，没有 `MERGE_HEAD` / `CHERRY_PICK_HEAD` / rebase 目录，index 里
  没有未合并条目。否则返回 `blocked: main_checkout_busy`。

**落地**（每个代码 repo 一把全局锁，等待不超过 30 秒，否则 `blocked: integrate_lock_busy`）：

1. **生成 B**：此时最终路径已确定，按改写后的 main 路径生成 `.aidlc/deliverables/<run_id>/REPORT.md`，
   amend 进那个唯一的 commit，得到 `N`。树指纹不含 B 的路径，所以 B 不影响上面的树比对。
2. **锁住 main 的 index**：按 git 自己的协议独占创建 `.git/index.lock`（别的 git 进程这时写 index 会失败
   重试，不能把中间状态提交出去）。已存在就重试不超过 10 秒，否则 `blocked: index_busy`；绝不删除别人的锁。
   拿到锁之前先写日志 `~/.swarm-ai/locks/integrate-<run_id>.journal`（`phase: prepared`，pid）。
3. **计算，不写 main**（`C..N` 用 `--no-renames`，覆盖新增、删除、mode 变化、symlink）：
   - **新 index**（写进 `index.lock`）：从当前 index 出发，对每个路径 P：index 条目等于 C 的（或两边都没有）
     → 换成 N 的条目（mode + blob，删除则去掉）；别人暂存了不同内容 → 对暂存内容做三方合并
     （base = C，ours = 暂存内容，theirs = N），干净则用合并结果、别人的暂存改动得以保留，冲突则
     `blocked_by_uncommitted`。主工作区里别人暂存的东西是常态（`artifact_cli.py` 就经常被暂存），所以不能
     一遇到暂存就停。
   - **主工作区计划**：P 的工作区内容（用 `git hash-object --path` 比较，经过 filter）等于 C → 落地时写 N；
     已等于 N → 不动；是别人未提交的改动 → 两边都是普通文本文件才做三方合并，干净则写合并结果（保留 mode），
     冲突、二进制、symlink、改/删冲突、文件与目录冲突 → `blocked_by_uncommitted`；run 新增的文件在主工作区里
     已作为未跟踪文件存在，内容相同则不动，不同则 blocked。被挡住的文件里修改时间晚于 `run.created_at` 的，
     标为"可能是你的"。
   - 任何 blocked：删除 `index.lock`、删日志，main 不动，没有任何写入。
4. **提交点**：`git update-ref -m "run-integrate <run_id>" <集成分支> N C`（比较交换）。失败（这一瞬间有人
   直接提交了 main）→ 删除 `index.lock` 和日志，返回 `main_moved`，下一次调用按前置条件重新判断。
   成功 → 日志写 `phase: committed`。
5. **写主工作区**：逐个路径，写之前再确认内容仍是第 3 步看到的样子；变了（有人刚刚编辑）就按第 3 步的规则
   重新合并；原子写入，保留 mode，symlink 按 symlink 写，删除就删除。然后把 `index.lock` 改名为 `index`，
   `git update-index -q --refresh`，删除日志。
6. **改写 run.json**（重新读取、只改这些字段、原子写）：`commits[]` 改为
   `{repo: main checkout, sha: N, files}`；`files_touched` 的 worktree 前缀改为 main 路径；
   `worktrees[].state = integrated`，记录 `integrated_at`、`base`。REPORT、聊天交付总结、Canvas OUTPUTS
   读到的都是 main 上真实存在的路径和 commit，`#base=<sha>^` 链接在 main 上可解析。
7. **释放**：`release_run_worktrees(run, "integrated")`（§7）。

**崩溃和中断**：
- 第 4 步之前崩溃：日志是 `prepared` 且记录的 pid 已不在 → 恢复时删除我们自己建的 `index.lock`，什么都没写过。
- 第 4 步之后崩溃：日志是 `committed` → 只向前完成：对剩余路径按第 3 步的规则重新合并后写入（不能盲写，
  崩溃期间别人可能又改了这些文件），再换上 index。提交点之后从不回滚。
- 恢复由下一次 run-integrate 或兜底清扫执行。
- SIGTERM：提交点之前释放锁直接退出；之后先把剩余的写入做完（只是写文件，几秒）再退出。
- 第 4 步到第 5 步完成之间的几秒里，main checkout 的 `git status` 会显示本 run 的改动像被"撤回"；这期间
  index 锁在我们手里，别人无法把这个中间状态提交出去。

### 6.5 返回给模型的状态

`integrated` | `conflict` | `needs_verify` | `needs_review` | `changed_since_review` |
`blocked_by_uncommitted`（文件列表，含"可能是你的"标记）| `blocked: integrate_lock_busy / index_busy /
main_checkout_busy` | `main_moved` | gate 拒绝（与 run-commit 相同）。

每次调用都在 60 秒内返回（Bash 工具默认 120 秒超时，后台运行又被 `background_command_guard` 禁止），
内部不做长时间等待。每个状态都对应卡片上写明的下一步。`blocked_by_uncommitted` 和各种 busy 由模型按卡片
退避重试（累计不超过 10 分钟，聊天 session 一般很快会提交），仍不行则以这个外部状态作为真实理由 checkpoint。

## 7. 清理：和 run 的结束是同一个动作

### 7.1 一个函数，所有结束路径都调它

`release_run_worktrees(run_state, reason)`。**只看显式的终止状态**（`status ∈ _TERMINAL_STATUSES`：
completed / complete / failed / cancelled / abandoned），不用 `is_terminal_run`：后者在 deliver 阶段
完成后就返回 true，而这时 run 可能正停在 `needs_verify` 或 `blocked_by_uncommitted`，工作还没集成。

| worktree 状态 | 处理 |
|---|---|
| `integrated` | `git worktree remove --force` → `git branch -d run/<id>`（已并入集成分支，`-d` 就够；`merge-base --is-ancestor` 不成立时不删，记 warning）→ 删该 worktree 的 srcgraph 缓存 → `state = released` |
| `active`，run 已显式终止但未集成 | 有 rebase 进行中先 `rebase --abort`；把未集成的工作存进 ledger：`runs/<id>/worktree.patch`（`git diff <base>` 加未跟踪文件，含二进制）和 `worktree.bundle`（分支上的提交）；然后删 worktree 和分支。工作可以从 ledger 恢复，磁盘上不留 worktree |
| `active`，run `running` / `paused` | 保留 |
| 有集成日志 | 先按 §6.4 的崩溃规则恢复，再按结果处理 |

**调用点**（今天每一个写 run 终止状态的地方）：
- `cmd_run_update` 写入任何终止状态之后；
- 两个回收器 `_auto_abandon_stale_runs` 和 `cleanup_orphans`（共用 `_abandon_verdict`，在同一处加）；
- `purge_garbage_runs` 删 run 目录之前；
- 手工脚本 `pipeline_cleanup.py`、`reconcile_mislabeled_runs.py`。

**取消要等 session 停下**：`PATCH /pipelines/{id}/cancel` 由 daemon 写 run.json，不停 session、不拿 run 锁，
直接删 worktree 会删到模型正在编辑或正在跑测试的目录下。改为：cancel 写 `status = cancelled` 和
`release_requested: true`，真正的 release 发生在 (a) 该 run 下一次任何 `artifact_cli` / `pipeline_test`
调用（看到已取消就拒绝工作并 release），或 (b) 兜底清扫确认该 session 已不在 STREAMING / WAITING_INPUT。
release 之后，写入 gate 的判定二拒绝再写这个路径。

每次 release 拿该 run 的锁，与 run-sync / run-integrate 互斥。release 失败不影响状态写入（记 warning），
由 7.2 补上。

### 7.2 兜底清扫

有的写入者会绕过任何单一调用点（崩溃后一直停在 `running` 的 run、手改的 run.json），所以还需要
按显式状态回收：

- **时机**：每次 `run-create`（同项目），以及每日一次（`ContextHealthHook` 的 deep check，和
  `cleanup_orphans` 放在一起，它在 daemon 里，能看到 session 状态）。
- **范围**：只看 `~/.swarm-ai/worktrees/` 下的 worktree 和 `run/*` 分支。runner 自己的基线 worktree
  （`~/.swarm-ai/cache/pipeline_test/test-baseline/`）和其他手工 worktree 不在范围内。
- **做法**：
  - 对应的 run 不存在，或 `status` 是显式终止状态 → `release_run_worktrees`；
  - run 是 running / paused → 保留；
  - 目录在但 git 没登记 → 存 patch 后删目录；git 登记了但目录不在 → 对这一项 `git worktree prune`；
  - 没有 worktree 的 `run/*` 分支：run 已终止且分支已并入 → 删；未并入 → 存 bundle 后删；
  - 有集成日志 → 按 §6.4 恢复。
- **长期停滞**：deliver 已完成但没集成、或崩溃后一直停在 running 的 run，今天不会被自动回收
  （`_abandon_verdict` 跳过 tokens > 0 的 run），worktree 跟着保留，在 Radar 里可见。清扫把"worktree
  存在超过 7 天且 run 无活动"写进 deep check 的 findings；暂停超过 14 天的 run 把工作存进 ledger 后释放
  worktree（`run-resume` 会从 ledger 重建，§5.3）。

### 7.3 清零的验收口径

任何时刻：`~/.swarm-ai/worktrees/` 下的 worktree 数 = 状态为 running / paused、且未因长期暂停被释放的
isolated run 数；`git branch --list 'run/*'` 只包含这些 run 的分支；每个被删掉的未集成 worktree 都能在
ledger 里找到 patch 和 bundle，并能用它们重建。

## 8. 不在 pipeline 里的 session

聊天 session（Kiro 会话、SwarmAI 里没在跑 run 的 tab）没有 worktree，照旧直接改 main checkout。
它们和 run 之间的保护只有 §6.4 落地第 3 步：run 集成时，如果和它们没提交的改动改到同一段代码，集成停下，
main 不动，它们的改动不丢。它们彼此之间的摩擦本设计不解决。缓解办法：

- SwarmAI 聊天 tab 要改代码就开 `direct` profile 的 run。`direct_commit_gate` 已经把超过 20 行源码的
  直接提交挡下并指向 `run-create --profile direct`，开了 worktree 模式之后这些改动自然被隔离。
- 被集成写回的文件，聊天 session 下一次按旧内容 Edit 会匹配失败，重新读一遍即可，不会丢数据。

## 9. 已确认的决定（XG 2026-10-18）

1. **本地临时分支**。AGENTS.md 写的是"Commit directly to main — never auto-branch"。本设计每个 run 有
   一个本地分支 `run/<id>`：不 push，集成后删除，最终 commit 仍直接落在 main。已确认不算违反该规则；
   P2 落地时据此更新 AGENTS.md 那一句。
2. **集成被未提交改动挡住时**：模型按卡片退避重试，累计 10 分钟，之后 DELIVER checkpoint，理由写明
   被哪个文件、哪类改动挡住。
3. **未集成就结束的 run**：工作存成 ledger 里的 patch + bundle，然后删除 worktree 和分支（不在磁盘上
   保留 worktree）。

## 10. 明确不做

- **文件锁 / 认领**（草稿方案一的 H1）：`artifact_cli.py` 这类热点几乎每个 run 都要改，run 之间会长时间
  互相阻塞。
- **按 hunk 记录归属、提交时拆分**：解决不了"测到别人改了一半的代码"，相邻或重叠的改动无法归属。
- **给 SwarmWS 建 worktree**：run.json 和 DDD 文档是全局共享状态，多个 session 必须看到同一份。
- **集成时自动解决冲突**、跳过重测直接推进 main、push。
- **为每个 run 重装依赖**：依赖文件有改动时只 warning。

## 11. 失败模式

| 情况 | 结果 |
|---|---|
| 建 worktree 失败 / main checkout 不在集成分支上 | run 以 `shared` 模式继续（今天的行为），warning 可见 |
| 模型用 gate 看不见的方式写了 main | run-sync 第 1 步按 `files_touched` 和修改时间发现并拒绝 / 标为"可能是你的" |
| 两个 run 改到同一段代码 | 后集成的一方 rebase 冲突，由它解决，然后重测、重审变化部分 |
| 两个 run 各自通过、合起来坏 | main 在审查之后动过 → 至少要求在 rebase 后的树上重测 |
| 审查之后又改了代码 | `changed_since_review`，回到 sync → verify → 审查 |
| 聊天 session 未提交 / 已暂存的改动 | 不重叠 → 三方合并保留；重叠 → `blocked_by_uncommitted`，main 不动，退避重试后 checkpoint |
| 提交点那一瞬间有人直接提交了 main | 比较交换失败，什么都没写；下一次调用重新判断 |
| 集成中途崩溃 / 被 SIGTERM | 提交点之前：删自己的 index 锁即可；之后：只向前完成，剩余文件重新合并后写入 |
| main 的 `.git/index.lock` 被别人占用 | 重试不超过 10 秒，返回 `index_busy`，绝不删别人的锁 |
| run 被取消 | 先标记 `release_requested`，等 session 停下或下一次 CLI 调用时再 release |
| deliver 完成但未集成就停住 | 不回收（只认显式终止状态），deep check 报告，人决定 |
| 长期暂停 | 14 天后工作存进 ledger、释放 worktree；resume 时重建 |
| daemon 重启 | worktree 在磁盘上，run.json 有映射，无需额外处理 |
| worktree 目录被手动删掉 | 清扫时 prune 该项；分支未并入则存 bundle；resume 时重建 |
| run 改了依赖文件 | warning：测试用的是 main 已安装的依赖 |

## 12. 分期

开关 `worktree_mode` 在 P1 + P2 都验收之前保持 `off`：只有 P1 会把 run 的提交落在 `run/<id>` 分支上，
不能单独打开。

- **P1 隔离与生命周期**：run-create 建 worktree 和映射（含 node_modules 软链、info/exclude）；写入 gate；
  `pipeline_test` / `artifact_cli` / `security_hooks` 按 run 定位 worktree（§5.2 表格全部行）；
  `.pth` 回落屏蔽；`run-resume` 改写 session；`release_run_worktrees` 接入全部结束路径；兜底清扫。
- **P2 同步与集成**：`run-sync`、`push_ready` 绑定树、`run-integrate`（§6 全部步骤，含冲突续跑、重测和
  重审要求、预演、index 锁、提交点、日志恢复、run.json 改写）；runner 基线改用 run 的 base；deliver 卡片和 build 卡片改成新流程（卡片尺寸上限内）；AGENTS.md 的分支规则和
  Security Architecture 列表更新。
- **P3 观测**：profiler 和 REPORT 记录集成耗时、冲突次数、重试次数、被挡住的时长；deep check 报告
  长期停滞的 worktree。

## 13. 验收

每条都要有自动化测试（临时 git repo + 临时 SwarmWS），P2 结束后再用一个真实 run 验证：

1. 两个 run 并行改同一个文件的不同函数：各自测试只看到自己的改动；先后集成都成功；main 上两处改动都在，
   各自一个 commit，提交内容不含对方的改动。
2. 两个 run 改同一段代码：后者得到 `conflict`，解决后重测、集成成功。
3. main 在 run 期间被别人推进：`needs_verify` 之前不推进 main。
4. 主工作区里聊天 session 未提交的改动：不同位置 → 合并后保留；同一位置 → `blocked_by_uncommitted`，
   main 和该文件都不变。
5. run-integrate 在落地各步之间被杀（每个位置一个测试）：提交点之前恢复后完全回到原样；之后恢复后完整
   集成，且崩溃期间别人对同一文件的改动不丢。
5a. 主工作区里别人暂存了本 run 也改的文件（不重叠）：集成后别人的暂存改动仍在 index 里。
5b. 路径种类：新增、删除、改名、mode 变化（+x）、symlink、二进制、run 新增但主工作区已有同名未跟踪文件，
   各一个测试。
5c. 解决冲突后、或审查之后又改了代码：不经重审不能落地（`needs_review` / `changed_since_review`）。
5d. deliver 已完成但未集成的 run：同项目新建 run 和每日清扫都不回收它的 worktree。
5e. 失败分类：run 引入的回归在 sync 之后仍被判为 regression，而不是既有失败。
6. 写入 gate：Write / Edit / MultiEdit / NotebookEdit / 各种 Bash 写入 / 经软链的路径 → deny 并给出
   worktree 路径；没有 run 的 session 不受影响；gate 出错时 fail-open。
7. 被删除的模块在 worktree 里导入失败（不从 main 回落）。
8. 清零：完成、取消、回收、清除、手动删目录五种路径之后，§7.3 的口径成立；未集成的工作能从 patch
   恢复出来。
9. `worktree_mode: off`：现有测试全部不变地通过。

## 14. Gate-0 对抗审查（r1 → r2 的修改）

| 发现 | 级别 | r2 的修改 |
|---|---|---|
| 先写文件和 index、后推进 ref：中间窗口里别人 `git commit` 会提交出本 run 的改动，回滚又会覆盖别人的新改动 | HIGH | 先独占 `.git/index.lock`、新 index 写在锁文件里；比较交换推进 ref 作为唯一提交点，之后才写主工作区；提交点前失败什么都没写，之后只向前完成（§6.4） |
| 集成发生在 DELIVER 收尾之后，冲突解决的代码没被审查，`push_ready` 证明的是 rebase 之前的树 | HIGH | 拆成 run-sync（审查之前）和 run-integrate；`push_ready` 绑定树指纹；main 在审查后动过时，补丁不变要重测、补丁变了要重审（§6.1–6.4） |
| `is_terminal_run` 在 deliver 完成后就为真，清扫会把还没集成的 worktree 回收掉 | HIGH | release 和清扫只认显式终止状态（§7.1、§7.2） |
| 提交进 worktree 后，runner 拿 worktree HEAD 当基线，本 run 的回归会被判成既有失败；按 HEAD 判断重测会每次都触发 | HIGH | 基线用 run 的 `base`；重测按树内容判断（§5.2） |
| 等待 10 分钟超过 Bash 工具超时，进程会在落地中途被杀 | HIGH | 每次调用 ≤ 60 秒，锁等待 ≤ 30 秒，由模型按卡片退避重试；SIGTERM 处理（§6.4、§6.5） |
| 只考虑了普通文件修改：删除、改名、mode、symlink、二进制、filter、同名未跟踪文件 | MED | 逐类定义处理，不确定就 blocked；用 `hash-object --path` 比较；验收 5b |
| 主工作区里别人已暂存的内容是常态，"暂存就冲突"会让热点文件几乎总被挡 | MED | 对 index 条目也做三方合并，保留别人的暂存改动；main 上的 git 写命令列表补全（§5.1、§6.4） |
| 写入 gate 沿用"running + 3 小时活动"条件会悄悄失效；不防写别人的 worktree | MED | 按非终止状态 + active worktree 判定；新增判定二（§5.1） |
| gate 看不见的写入会让 run 被自己的残留挡住 | MED | sync 第 1 步按修改时间标"可能是你的"（§6.2） |
| 取消时 daemon 直接删 worktree，会删到正在编辑的目录 | MED | `release_requested`，等 session 停下或下一次 CLI 调用（§7.1） |
| 集成分支写死为 `main`；main checkout 可能正处于 merge / rebase | MED | 记录 `integration_branch`；落地前检查 main checkout 状态（§4.1、§6.4） |
| B 在 rebase 和路径改写之前生成，里面是 worktree 路径 | MED | B 在落地第 1 步按最终路径生成并 amend 进唯一的 commit（§6.4） |
| 模型在 worktree 里自己的 commit 会被 rebase 原样带进 main，缺 trailer、缺 B | MED | sync 时压成一个 commit（§6.2 第 2 步） |
| 崩溃后向前完成时盲写会覆盖别人的新改动 | MED | 向前完成时剩余文件重新合并（§6.4） |
| resume 时 worktree 可能已不在；长期暂停一直占磁盘 | MED | resume 从 ledger 重建；暂停 14 天释放（§5.3、§7.2） |
| `core.hooksPath=/dev/null` 绕过公司 hook | LOW | 只用于 `worktree add`，commit / rebase 不覆盖（§4.1） |
| 受限权限模式下写不了 worktree；Windows 软链 | LOW | 加入允许目录；junction 或回退 shared（§5.1、§4.1） |
| 比较交换的"调用内重试 3 次"不可能成功 | LOW | 删除调用内重试，每次返回确定的下一步（§6.4） |
| 集成后新鲜度检查比的是可能混有别人改动的主工作区文件 | LOW | 固定比对 `N:path` 的 blob（§5.2） |
| "SwarmWS/Projects/SwarmAI 是指向 main 的软链"不成立（它是普通目录） | LOW | 删除该说法，realpath 仍保留（防其他软链） |
| 清扫的 prune 是全局的，会影响 runner 的基线 worktree | LOW | 清扫范围限定在 `~/.swarm-ai/worktrees/`，prune 只针对该项（§7.2） |
