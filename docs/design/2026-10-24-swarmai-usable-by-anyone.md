---
title: SwarmAI 让别人装上就能用 — 产品原则、安装包、通用 pipeline（Sev-1）
created: 2026-10-24
updated: 2026-10-25
project: SwarmAI
status: final r18 — XG 2026-10-24 定稿；r1–r18 的演进见末尾"修订记录"
---

# SwarmAI 让别人装上就能用（Sev-1）

## 0. 一句话

今天只有 XG 自己的机器能用 SwarmAI：正式安装包里没有后端；装上了，pipeline 命令也跑不起来；跑起来了，pipeline 只认 SwarmAI 自己的仓库结构；而 worktree 模式又强制用户的改动走 pipeline、强制 commit 前审查。几件事叠在一起，别人装上 SwarmAI 后连改自己的代码都做不到。

本设计先定产品原则，再按原则修三件事：安装包、pipeline 的运行方式、gate。

## 1. 产品原则（XG 2026-10-24，产品级，适用于所有后续设计）

**P1 pipeline 是高级自主方案，不取代 native。**
写代码、跑测试、commit，默认都用 native CLI 能力。pipeline 是用户主动选择或在配置里声明后才启用的高级能力，任何仓库、任何情况都不强制，SwarmAI 自己的仓库也一样。

**P2 pipeline 是通用方案：语言无关、平台无关。它只做流程控制和质量收敛。**
- pipeline 不观察、不控制、不重复执行 agent 怎么写代码、怎么测试。agent 在 pipeline 里写代码和测试的方式，和不走 pipeline 时完全一样：自己写、自己测、自己报告结果。
- pipeline 在这之上提供：阶段化的流程（需求、设计、实现、审查、测试、交付、反思）、文档规范、Gate-0 和 Gate-2 两道审查、finding → fixing-spec → 修复 → 复审的收敛闭环、隔离 worktree 和落地协议、REFLECT 的经验沉淀。
- pipeline 核心里没有任何按编程语言、框架、包管理器分支的逻辑。只适合 SwarmAI 自己仓库的东西不进核心；SwarmAI 仓库作为一个普通仓库，用通用的配置去适配 pipeline，而不是 pipeline 去适配它。

**P3 只允许产品级 gate。**
- 每个 gate 都必须对所有用户可用、有价值，不能为某个仓库、某个人的习惯或某台机器加。
- gate 只用来保护：危险或不可逆的操作、对外的副作用、需要人回答的问题、用户主动开启的 pipeline run 本身的完整性，以及 session 本身不挂死、不失控耗 token（r15，XG 定；前提是不挡正常工作、有放行路径，例如 `background_command_guard` 放行 dev server / `--watch` / `tail -f`，`image_read_dedup_guard` 只拦未变化的同一张图）。这类 gate 背后的做法同时写进 AGENT.md 作为 best practice。
- gate 不能用来挡 native 写代码、跑测试或 commit，也不能用来把人赶进 pipeline。
- 不设"按仓库强制"的 gate（r9）：commit 前要不要审查、要不要走 pipeline，由 agent 按改动判断、或由用户在 session 里选择（P4），不靠 gate。仓库想要更严格的做法，写进它自己的 AGENTS.md 作为指引。
- 新增 gate 前必须回答两个问题：所有用户都用得上吗？有价值吗？并且先看度量（§6）。

**P4 产品默认是"相称"，选择发生在 session 里（r9）。**
- 小改动、文档、探索性修改：agent 直接用 native 方式做，不问。
- 风险较大的改动（跨模块、改公共接口或数据结构、迁移、安全相关、多会话可能同时改同一处）：agent 说明理由，用 AskUserQuestion 让用户选"直接改（native）"或"走 pipeline"。同一个 session 里只问一次，后面同类任务沿用；没回答时默认 native（包括无人值守的渠道）。
- 用户随时可以主动说"用 pipeline 做"。
- 没有 Settings 开关。

**P6 native 模式下，审查由 agent 自主决定（r9）。**
agent 用 native 方式写完、测完后，自己判断要不要请专家审查、请哪几位（§3.5 的专家名录），拿到 finding 后自己修、决定是否复审，并在交付里写清楚请了谁、发现了什么、修了什么、还剩什么风险。没有任何 gate 参与。走 pipeline 时，审查用 pipeline 自己的机制（Gate-0 + Gate-2）。两种模式用同一套专家。

**P5 不再做"防 agent 作恶"类加固，先量再加。**
B-d（人工接口鉴权）、sandbox 子进程、首次批准、agent 进程隔离都不做；E7 遗留项 7 保持"已知、不修"。新增任何 gate 或加固之前，先拿到两组数字：每个 run 的成本，和每个 gate 实际拦下了多少真问题。

**P7 SwarmAI 自己不特殊（r10，XG 2026-10-24）。**
SwarmAI 的仓库、SwarmAI 项目、XG 的机器，用的必须是和其他所有用户完全一样的产品和路径。否则出了问题，XG 自己用的时候发现不了。具体规则：
1. **产品代码不识别"这是不是 SwarmAI"**：不判断某个仓库是不是 SwarmAI 源码仓库、某个项目是不是 SwarmAI 项目，也不按 SwarmAI 的目录（`backend/`、`desktop/`、`Projects/SwarmAI`）写死逻辑。今天这样做的代码（`security_hooks._source_repo_root` 及其 13 处使用、`worktree_write_gate`、`run_owner_gate`、`utils/source_repo.py`，以及 `engine_metrics`、`cultivation`、`swarmai_monthly_report`、`code_intel_feed`、`context_health_hook` 等里写死的 `SwarmAI` 项目名）逐个清理：要么改成对所有项目通用，要么删掉。
2. **SwarmAI 仓库的特殊要求，只能用任何仓库都能用的方式表达**：`.swarmai/pipeline.toml` 的通用字段、`.swarmai/review.md`、它自己的 AGENTS.md、它自己的脚本、pre-commit 和 CI。产品里不为它单独注册任何 hook。`design_lint_guard`、`inclusive_term_guard`、`change_triggered_eval` 从产品里删除，改成 SwarmAI 仓库自己的 lint 脚本和 CI 检查。
3. **同一条使用路径**：XG 日常用的就是正式安装包，或者用和 CI 完全相同的打包流程在本机打出来的包；不再有 `prod.sh` 注入后的特殊版本。`./dev.sh` 只用来调试 SwarmAI 本身。
4. **dev 和打包版的命令形式相同**：pipeline 和 `py` 命令在两种模式下都是 `"$SWARM_BACKEND_BIN" pipeline-v1 …` / `"$SWARM_BACKEND_BIN" py …`。dev 模式下 `SWARM_BACKEND_BIN` 指向一个用 venv 运行同一个入口的 shim（`backend/bin/python-backend`），gate 的识别逻辑只有一种。
5. **没有只服务 SwarmAI 工作流的产品配置**：rule 4 删除后已无作用的 `worktree_mode` 设置（`config.json["pipeline"]["worktree_mode"]`、`PUT /api/settings`、`SWARM_WORKTREE_MODE`）一并删除；pipeline run 一律使用隔离 worktree。
6. **验收**：SwarmAI 仓库本身作为样例仓库之一参加 §5 的验收，行为必须和其他样例仓库一致；结构测试保证产品代码里没有识别 SwarmAI 仓库或项目的逻辑（经过审查的例外清单除外，例如 seed 数据里的默认项目名）。

## 2. 现状（2026-10-24 核对）

| 层 | 事实 | 证据 | 后果 |
|---|---|---|---|
| 安装包 | v2.3.0 的 DMG 12.5 MB、exe 9.4 MB、msi 12 MB；PyInstaller 后端 385 MB | `gh release view v2.3.0`；`du -sh ~/.swarm-ai/daemon` | 安装包里没有后端 |
| 安装包 | Tauri 只打包 `desktop/resources/`，`externalBin: []`；后端产物在 `desktop/src-tauri/binaries/`，不进包 | `tauri.conf.json:22-24,47`；`build-backend.sh:451-462` | 同上 |
| 安装包 | 本机能用，是因为 `prod.sh` 在 Tauri 打包后手工 rsync 后端进 `.app` 并部署到 `~/.swarm-ai/daemon`；CI 只跑 `npm run tauri build` | `prod.sh:300-310`；`release.yml:76-103` | CI 产物缺后端 |
| 安装包 | 外置 daemon 的部署、升级、回滚机制已经有了：启动时把 `Resources/python-backend/` 拷到 `~/.swarm-ai/daemon/`，升级前整目录备份，起不来就回滚 | `lib.rs:1490-1580,1719-1745` | 缺的只是包里那份后端 |
| 安装包 | Windows 也没把后端放进包；没有 macOS x64、Linux 发布 job；没有 Apple 签名公证和 Windows Authenticode | `release.yml:32-176,88` | 陌生机器上起不来，或被系统拦 |
| pipeline 运行 | pipeline 命令要真 Python、源码 checkout 里的脚本、可导入的后端模块、psutil、pytest、perl、node/npx | `worktree_write_gate.py:416-426`；`pipeline_test.py` | 装好的 app 里 pipeline 起不来 |
| pipeline 运行 | MCP 脚本同样靠系统 Python 跑 | `mcp_config_loader.py:415-420` | 陌生机器上 MCP 工具可能坏 |
| pipeline 核心 | 测试执行器 `pipeline_test.py`（约 5700 行）只认 SwarmAI 的目录、pytest 和 vitest；多项阶段检查要求它产生的证据 | `pipeline_test.py`；`checks.py:144-177,329-575` | 非 SwarmAI 仓库的 run 无法落地 |
| pipeline 核心 | 文档里的测试引用按文件后缀白名单；风险扩展规则、review 检查清单写的是 SwarmAI 的路径和经验；落地作者固定为 Swarm 并加 Swarm trailer | `schemas.py:83-97`；`risk_selection.py`；`REVIEW_PATTERNS.md`；`landing.py:262-287` | 别的仓库用不上，或被改写提交身份 |
| gate | worktree rule 4 拒绝在项目仓库主 checkout 写代码；`direct_commit_gate` 拒绝超量的直接 commit；`adversarial_commit_gate` 对所有仓库拒绝未经审查的 commit；`pytest_command_guard` 要求 pytest 套 `perl alarm`；`background_command_guard` 拒绝后台命令；`image_read_dedup_guard` 拒绝重复读图 | `worktree_write_gate.py:316`；`security_hooks.py:2090,2285,3371,6766,7130` | 用户不能用 native 方式写代码、commit |
| Windows | pipeline 依赖 Unix socket、`perl alarm`、POSIX 信号 | `hook_token.py`；`daemon_exec.py`；`pipeline_test.py:1250-1318` | Windows 上 pipeline 结构上不支持 |

没有任何一道 CI 检查在"装好的安装包"上跑过：CI 只在源码树里 import 和跑测试（`ci.yml:17-39,155-194`）。这是这个错误类能长期存在的原因（§5.1 的 G1 把它变成发布前的硬检查）。

## 3. 设计

### 3.1 安装包自带后端

保留 PyInstaller onedir（它已经打进 Claude CLI、`sqlite_vec`、`tree_sitter`、`awscrt` 等原生依赖），只修打包：

- `build-backend.sh` 把 onedir 输出到 Tauri resources 里的 `python-backend/`，`tauri.conf.json` 的 `bundle.resources` 加这一项。
- 删除 `prod.sh` 的"打包后 rsync 注入"，本机和 CI 走同一条打包路径。
- **三个平台同一套部署和启动**（r8：今天只有 macOS 的安装逻辑读 `Resources/python-backend/`，Windows/Linux 的 `find_backend_binary()` 只找 exe 旁边或 `~/.swarm-ai/daemon`，`lib.rs:1719-1779,2548-2623`）：
  - app 启动时，从 resource 目录把 `python-backend/` 部署到 `~/.swarm-ai/runtime/<version>/`（Windows 为 `%LOCALAPPDATA%\SwarmAI\runtime\<version>\`），核对文件哈希，原子切换 `current`，保留一个回滚版本；切换前先 drain 旧 daemon。
  - daemon 永远从 `runtime/<version>/` 启动，不直接跑 app 包里那份（更新会在进程运行时替换文件）。macOS 的 launchd wrapper（`desktop/resources/daemon/swarmai_backend.sh:15-17,131-142`）改为读 `current`；Windows/Linux 由 Tauri 子进程启动，同样读 `current`。
  - 可执行文件名：macOS/Linux `python-backend`，Windows `python-backend.exe`。
  - 现有 `~/.swarm-ai/daemon` 首次升级时迁移到 `runtime/<version>/`，迁移后删除。
- 发布目标：macOS arm64、macOS x64、Windows x64、Linux x64。
- 首次发布不签名（XG 定）：下载页写明 macOS 上"系统设置 → 隐私与安全性 → 仍要打开"（macOS 15 起右键打开不再有效），Windows SmartScreen 上"更多信息 → 仍要运行"；验收检查 arm64 可执行文件至少有 ad-hoc 签名。S1 实测（2026-10-24）：v2.3.0 的 `SwarmAI.app` 只有链接器自带的 ad-hoc 签名，没有 sealed resources，`codesign --verify --deep --strict` 报"code has no resources but signature indicates they must be present"。带 quarantine 下载后，macOS 会把这种 app 判为"已损坏"，不给"仍要打开"。所以 S2 打包后必须对整个 .app 做一次 ad-hoc 签名（`codesign --force --deep --sign -`，或 Tauri 的 `signingIdentity: "-"`），G1 的 C13 检查它。

### 3.2 产品自己需要的 Python 操作，都由后端程序的子命令执行

r8：要做到"不需要系统 Python"，只加两个子命令不够。打包版里还有这些地方会去找系统 Python：`bundle_paths.get_python_executable()` 本身（`bundle_paths.py:27-64`）、channel MCP 脚本（`mcp_config_loader.py:415-420`）、daemon 自升级脚本（`main.py:2455-2468`）、以及 skill 里让 agent 执行的 Python 脚本。

- 冻结后的 `python-backend` 在导入 FastAPI 之前分发子命令：
  - `daemon`（默认，现行为）；
  - `pipeline-v1 <args>`；
  - `py <script.py> [args]`：用包里自带的解释器和已打包的模块执行一个 Python 脚本（`runpy.run_path`）。MCP 脚本、自升级脚本、skill 脚本都改用它。只能用包里有的模块；需要别的第三方包的 skill 在 frontmatter 里声明依赖，缺依赖时 agent 告诉用户。
- `get_python_executable()` 在打包版里返回 `[python-backend, "py"]`，不再找系统 Python。
- S3 实施前先列一份清单：打包版里每一个会起 Python 子进程的地方，逐个改成子命令；G1 在没有系统 Python 的环境里覆盖 MCP、自升级和有代表性的 skill 脚本。
- 每个 agent 会话注入 `SWARM_BACKEND_BIN` = daemon 自己的 `sys.executable`（指向 `runtime/<version>/` 里确定版本的那份，不指向会变的 `current`；升级后 daemon 重启，新会话拿到新值）。打包版和 dev 版的 pipeline 命令形式相同（P7）：`"$SWARM_BACKEND_BIN" pipeline-v1 …`；dev 版里 `SWARM_BACKEND_BIN` 指向 `backend/bin/python-backend` shim，用 venv 运行同一个入口。`source_repo.json` 和 `utils/source_repo.py` 在 S6 删除（r14：runner 还在时，`_is_our_script` 和 runner 的 `test` token 靠它认脚本，`run_owner_gate.py:158-161,519-541`）。
- gate 识别 pipeline 命令最终只有一种形式："确切可执行文件路径 + 子命令"：`argv[0]` 解析后必须等于 `SWARM_BACKEND_BIN` 的真实路径，`argv[1]` 必须是 `pipeline-v1`。r14：S3 加入新形式，旧的"python + 确切脚本路径"形式保留到 S6，和 runner 在同一个 commit 里删，中间任何一个 commit 上 pipeline 都能完整走到落地。
- **一个命令识别模块（r14）**：今天至少六处各自用文本匹配认 pipeline 命令：`run_owner_gate.py:610-612` 的快速路径只认 `pipeline_v1`（遇到 `pipeline-v1` 直接放行，`_with_session` 不执行）、`runtime_hooks.py:759` 的 session→run 映射（下游 `session_router.py:130`）、`pipeline_stage_events.py:81,125`、`daily_activity_hook.py:76`，前端 `turnView.ts:864` 和 `PipelineOverlay.tsx:89` 的提示文本。S3 新建一个共用的识别函数（输入命令，输出"是不是 pipeline 命令、子命令是什么"），这几处全部改用它；前端的提示文本由后端给出，不在前端写死。结构测试：产品代码里除这个模块外，不再出现按 `pipeline_v1` / `pipeline-v1` 文本匹配命令的代码。
- 不再有 `pipeline-test` 子命令：pipeline 不执行测试（§3.3）。
- Windows 不启用 pipeline：`pipeline-v1` 在 Windows 上返回"Windows 上不支持 pipeline，请直接用 native 方式完成"，agent 照此执行。`py` 子命令在 Windows 上照常可用。

### 3.3 通用 pipeline：流程控制和质量收敛

#### 3.3.1 触发（r9）

| 方式 | 行为 |
|---|---|
| 用户在对话里要求（"用 pipeline 做"、"无人值守做完"） | 走 pipeline |
| 风险较大的改动 | agent 在 session 里询问一次（P4），用户选了 pipeline 才走 |
| 其他 | native |

没有 Settings 开关，`pipeline.toml` 里也没有触发字段。

#### 3.3.2 保留的流程（全部保留，只改不通用的地方）

- **六个 profile 全部保留**：`direct`、`trivial`、`full`、`bugfix`、`goal`、`research`，阶段顺序不变（`lifecycle.py:77-83`）。
- **阶段全部保留**：evaluate、think、plan、build / goal_cycle、review、test、deliver、reflect。
- **文档规范保留**：REQUIREMENTS（结构化 EARS、R 编号）、DESIGN_SPEC（P/F/T/X 编号、properties、failure_modes、trace 矩阵）、fixing-spec、REFLECT 的 lessons。这是质量收敛的骨架，本身和编程语言无关。
- **两道审查保留**：Gate-0（EVALUATE 的问题定义审查）、Gate-2（REVIEW 的专家审查：R1 全量 + R2 只复查已处理的 finding，finding 账本、聚类、fixing-spec、修复、复审、轮数上限）。原来的 Gate-1 已在 10 月的简化里删除，本设计不恢复。
- **REFLECT 保留**：daemon 后台判断并沉淀 lessons 到项目 DDD（产品能力，每个用户的项目都有 DDD）。r8：project 在 SwarmWS 里不存在或没有 DDD 文档时，lessons 写进 run 自己的 REFLECT 记录并在 REPORT 里列出，不再静默丢弃（今天 `reflect_job.py:56-70,168-209` 会拒收未知项目）。
- **落地协议保留，签名删除（r12，XG 定）**：事件改为只追加日志（§3.3.5），其余保留：producer 权限、不可变 contract 和 tree 绑定、owner 转移、一次性授权、`auth_consumed`、`land_intent`、REPORT 渲染、交付 tree、base 复核、部分落地记录、隔离 worktree。
- **上限和熔断保留**：阶段尝试上限、决策上限、复审轮数上限。

#### 3.3.3 测试：用 agent 自己的结果，不观察、不重跑

- BUILD 和 TEST 阶段，agent 用 native 方式写代码、写测试、跑测试，和不走 pipeline 时一样。
- **验证报告（`validation_report`）**：BUILD 和 TEST 阶段都以 `stage-complete --artifact -` 显式完成（不再有 runner 自动完成），artifact 是一份验证报告，由 CLI 写进 run 的只追加日志。每个仓库一条，字段：
  - `repo`、`code_tree`（daemon 在写事件时自己算当前 worktree 的 tree，不取 agent 给的值）；
  - `commands`：跑了哪些命令、在哪个目录、结果（通过 / 失败 / 未运行，以及原因）；
  - `tests_changed`：新增或修改了哪些测试文件；
  - `rationale`：为什么认为这些测试能证明改动正确。
  日志只记录"这份报告是在这个 tree 上提交的"，不证明报告内容为真；内容是否可信由审查判断。
  S6-1a 实现（2026-10-24，`core/run_protocol/validation_report.py`）：artifact 是 `{"reports": [每个仓库一份]}`，每份 `{"repo", "commands": [{"command", "cwd", "result": "pass|fail|not_run", "reason"}], "tests_changed", "rationale"}`；`fail` 和 `not_run` 必须写 `reason`；多出的键（包括 agent 自己填的 `code_tree`）直接拒绝。每个仓库写一条 `validation_report` 事件：`stage`、`repo`、`code_tree`（engine 当场算的 worktree tree）、三个计数内联，完整报告存成 blob（事件行有 4 KB 上限）。任何一份不合法、或某个仓库的 tree 算不出来，一条都不写。REPORT 里新增"测试（agent 自己报告的结果，pipeline 没有重跑）"一节，只用事件里的计数生成。S6-1a 期间 runner 路径照旧可用，报告是可选的；S6-1b 起报告是 BUILD、TEST 完成的必要条件。
  S6-1b-i 实现（2026-10-24）：所有判定改读报告，runner 代码本身留到 S6-1b-ii 删。
  - 检查：新增 BX-3（BUILD / GOAL_CYCLE：有改动，且每个仓库最新的 BUILD 报告在当前 tree 上）；TS-1（stage + push）和 LG-1 读 TEST 报告：每个仓库的最新 TEST 报告在当前 tree 上、没有失败的命令。删除 DS-2b、DS-4b、DS-9b、BX-1、BX-2、TS-2、LG-2、LG-5、QX-1、QX-2、MD-2；LG-4 只看 main 是否还在 run 的 base 上。TS-3 读 DESIGN_SPEC 里 exit_criteria 声明的覆盖。
  - 报告要求 tree 和 base 都已知：base 说不出来的 worktree 上不记报告（原来 runner 在这种情况下不出 final）。
  - lifecycle：删掉 `tests_red`、`tests_incomplete`、`validation_failed` 和 TEST 的 `final_verdict`；TEST 报告里有失败就停在 TEST，三次后照常 `checks_exhausted`。
  - main 移动：落地时 `_sync_moved_bases` 把 worktree rebase 到新 main（取代 carry / recheck）；tree 变了，旧报告不再满足 LG-1，`next` 让 agent 在新 tree 上重测并用新命令 `report-tests --artifact -` 提交；rebase 冲突留在 worktree 里让 agent 解决（和原来一样），LG-4 每次都记 `merge_conflict`，三次后 `land_conflict`。
  - `next_step`：BUILD / TEST 提示 native 测试并给出报告格式，不再给 runner 命令；没有自动完成。
  - 五维审计的 Quality：每个仓库的 TEST 报告有通过、没有失败算 verified。
  - stage 卡片（build、goal_cycle、test、deliver、review）和 INSTRUCTIONS、SKILL.md 去掉 runner 命令；review 里"用 prove 关闭 finding"暂时改为"在报告里写明证明修复的测试"，`reported_test` 证据在 S6-2 加。
  S6-1b-ii 实现（2026-10-24）：删 runner。
  - 删除：`scripts/pipeline_test.py`；`core/run_protocol/` 的 `runner`、`runner_service`、`runner_client`、`final`、`probe`、`cache`、`carry`、`risk_selection`、`validations`；`core/evidence_seal.py`、`core/main_red.py`；`facts` 里的 `bind_final`、`required_validations`、`rule_violations`；`pipeline_test_gate` 和它独用的测试命令分类器（`security_hooks` 约 740 行，`test_command_gate` 组随之删掉）；`test_call` 事件、`runner` 签名者（usage metric 改由 daemon 记）、`daemon_exec` 的 runner lease；lifecycle 的 runner 上限（`regression_round`、`recheck`、`breaker_*`、`goal_tdd_calls`）；contract 的 `pipeline_code_hash`、`regression_round_cap`、`recheck_cap`、`risk_test_file_cap`（和 `create --risk-test-file-cap`）；`run_worktree` 的 `carries_over`、测试调用锁、源码图缓存清理；`next_step.auto_completable`；`CheckContext` 的 runner 字段。
  - `contract`、`read_contract`、`is_v1_run`、`integration_branches_path` 搬到新的 `core/run_protocol/contract.py`。
  - DESIGN_SPEC 的 exit kind 只剩 `test`、`manual`：没有执行器，`probe`、`mutation` 是未知 kind。refutation 的 `test:<nodeid>` 引用删除；finding 的 `test`、`probe` 证明删除，行为类 finding 在 S6-2 加 `reported_test` 之前只能靠 R2 和推送检查。
  - `pipeline_command` 不再认 `pipeline_test.py`；旧的 `python …/pipeline_v1.py` 形式和 `source_repo` 照计划留到 S6-5。
  - SwarmAI 的选测逻辑留成 `backend/scripts/swarm_select_tests.py`：按 import 图选 backend 测试，并列出 `validations.json` 里路径匹配的检查；`tests/test_swarm_select_tests.py` 覆盖。
  - 还没做：S6-1 里"TEST 改了代码退回 REVIEW"放进 S6-2（LG-1 绑定审查过的 tree 时一起做）。
  S6-2 实现（2026-10-24）：
  - 审查记录代码：每个 REVIEW handout 记下专家看到的 `code_trees`；REVIEW 的 `stage_complete` 记下 `code_trees` 和 `patch_ids`（base→tree 的 patch id）。
  - RV-4（REVIEW 的 stage 检查）：当前 tree 和最后一次审查看到的 tree（最近的 handout 或 REVIEW 完成）之间，非文档文件没有变化。变了就由 `review-start` 派**增量审查**：只审 `diff(上次审过的 tree, 当前 tree)`，correctness 加上原 R1 专家里这段 diff 会选中的；用 R1 的答案格式，新 finding 照常走 address 和 R2；每个专家最多 3 次。
  - TEST 改了代码：TEST 的 `stage-complete` 发现当前 patch 不是任何一次 REVIEW 完成记下的 patch，就追加新事件 `review_reopen`，run 回到 REVIEW（两个阶段的尝试次数各自重新计），最多 2 次；超过后 TEST 这次尝试记为失败（`cap_exceeded.review_reopen`）。
  - LG-1 另外要求当前 patch id 等于某次 REVIEW 完成记下的 patch id。和 §3.3.3 写的"tree 相等"不同：用 patch id 比较，这样 DELIVER 里 main 移动后干净的 rebase 仍然算审查过，而对 run 自己改动的任何修改都不算。
  - R2 的 handout 每次 address 一个新 attempt（原来固定 2），reopen 后可以再派 R2。
  - `reported_test`：fix handoff 的 proof 写 `{"evidence_type": "reported_test", "command": "<报告里的命令>"}`；engine 在该 finding 所在仓库、当前 tree 上的验证报告里找这条命令且结果是 pass（REVIEW 里可以用 `report-tests` 记一份 `review` 阶段的报告），找到才记 `proof` 事件，并把报告的 `tests_changed`、`rationale` 交给 R2。finding 先是 closed_proven；R2 判 ADEQUATE 保持关闭，TEST_INADEQUATE 变成 invalid_test。只接受 behavior/contract 类。
  - `scripts/lint_problem_values.py` 去掉已删除的 `evidence_seal` loader。
  S6-3 实现（2026-10-25）：
  - 事件不再签名：`EventAuthority` 不带密钥，每条事件带 `checksum`（去掉自身后的 sha256）和固定的格式标识 `authority_id`；只用来发现截断和损坏。最后一行写了一半照旧丢掉继续；中间记录校验和不对，CLI 返回 `log_damaged`、gate 拒绝写命令并说明日志损坏，run 停下等人处理，日志不再追加。
  - 删除：`run_protocol/keys.py`（`authority.key`）、`events.allow_hook_writer` / `restrict_process` / 进程写入者限制、`auth_consumed` 的 oracle 回执、`run_protocol/daemon_exec.py`（socket、队列、代执行）、`core/hook_token.py`、`run_protocol/cli_scope.py`、`request_ctx.take_scope`、`engine._consume_token`、`not_landed(needs_hooked_session)`、infra 组件 `hook_token`、`pipeline_command.is_ours`、`run_owner_gate` 发 token 的逻辑（`_needs_token`、`_is_our_script`、`_resolves_into_scripts`）、`main.py` 里 oracle 和 daemon_exec 的启停。
  - pipeline 命令回到本地：`scripts/pipeline_v1.py` 在自己的进程里跑 `cli_commands`，调用者是 `SWARM_APP_SESSION_ID` 里的 session；要求 owner 的操作（`land`、`run-land`、`accept-finding`、`discard-snapshot`）改为检查调用者是不是 run 的 owner。daemon 不在线时照常工作。
  - 保留：owner 转移和存活判断、操作锁、`_with_session`（hook 把 session id 补到第一条 pipeline 命令前；不再要求 pipeline 命令必须是第一条，否则只是不补）、人工批准（批准后 hook 照旧写 `auth`，落地时 `AuthorizationOracle` 在本进程里核对一次）。
  - `audit_marker_write_gate`：只保留写入保护，删掉 `authority.key` 的读保护（约 160 行），可以用 `SWARM_GATE_FORCE` 放行；`UNFORCEABLE` 只剩 `session_link_gate`。
  - 新测试 `test_pipeline_v1_local_log.py`：被接管的 prewarm session 建 run 且 owner 正确；写了一半的最后一行被丢掉、run 继续；中间记录损坏时 run 停下；不再生成密钥文件。`test_pipeline_v1_handoff` 覆盖不经 hook、不靠 daemon 直接运行命令，以及非 owner 的命令什么都不改。
- **审查和最终 tree 的关系（r8，skeptic BLOCKER 1）**：阶段顺序 build → review → test 不变。规则是：
  - Gate-2 审查的对象是 REVIEW 时的代码 tree，以及 BUILD 的验证报告。测试是否充分、报告是否可信，由 Gate-2 专家判断；专家 sub-agent 有 Bash，可疑时自己用 native 方式跑测试核实；不足、不实就记成 finding，走 fixing-spec → 修复 → R2 复查。"至少有一个测试在改动前会失败"是专家检查清单里的一项。
  - TEST 阶段是在审查后的最终 tree 上做最后一次验证，并提交 TEST 的验证报告。**TEST 阶段不改代码**：如果最终验证发现问题、需要改代码，run 退回 REVIEW，做一次**增量审查**，再回到 TEST。
  - **审查记录审过的 tree（r14）**：今天的 R2 只审已处理 finding 对应的 hunk（`review.py:238-240,428`），审查事件也不记 tree，所以 TEST 退回时那段新改动对应不到任何 finding，R2 派不出去；硬派的话新 hunk 也没人审。改法：每次 R1、R2 和增量审查的完成事件都写入它审过的 `code_tree`；新增"增量审查"，对象是 `diff(上一次审过的 tree, 当前 tree)` 的全部内容，不限于 finding 的 hunk，由 correctness 加上原 R1 里命中这些路径的专家来审。R2 仍然只复查已处理的 finding。
  - 落地时 LG-1 要求 TEST 验证报告的 `code_tree` 等于落地的 tree，并且这个 tree 等于某一次审查（R1、R2 或增量审查）事件里记下的 `code_tree`。这样每一次进入落地的代码都被审查过，最终验证报告也对应同一份代码。
- REPORT 里分开写：agent 报告的测试，和审查员核实过的部分。不再有机器打的 verified / unverified 标签。
- 挡不挡落地：还有未解决的 HIGH/CRITICAL finding 就挡（RV-2）；TEST 验证报告里有"失败"的命令也挡（agent 自己报告失败，就不能交付）；MEDIUM/LOW 作为残余风险写进 REPORT。

#### 3.3.4 核心里要改成通用的地方

| 部分 | 改法 |
|---|---|
| 测试执行器 `pipeline_test.py`（选测、执行、续跑、基线缓存、mutation、probe、prove 模式）及其子命令、gate（`pipeline_test_gate`）、runner lease 和 `runner_service`、`test_call` 事件和 runner producer | 从 pipeline 删除。SwarmAI 仓库的精细选测逻辑保留为 SwarmAI 仓库自己的工具 `backend/scripts/swarm_select_tests.py`，SwarmAI 的 agent 用 native 方式调用它 |
| 依赖 runner 证据的机器判定：BX-1、BX-2、TS-2、DS-2b/DS-4b/DS-9b、LG-2、LG-5、QX-1、QX-2、MD-2 | 删除这些机器判定。对应要求（设计里的测试存在且通过、回归不变量有测试、validation 跑过）进入 Gate-2 检查清单 |
| TS-1 | 改为"每个仓库都有 schema 合法的 TEST 验证报告" |
| TS-3（trace 矩阵） | 保留矩阵本身；不再有"通过"语义，作为 agent 的声明和 Gate-2 的检查对象 |
| LG-1 / LG-3 / DV-1 | 保留：LG-1 绑定 TEST 验证报告的 `code_tree` 和审查过的 tree；LG-3、DV-1 绑定并渲染 REPORT，REPORT 改为读验证报告 |
| lifecycle 里 TEST 的 `final_verdict`、`tests_red`、`tests_incomplete`、`validation_failed` 状态和 runner 熔断上限（`lifecycle.py:23-42,235-239,264-278`） | 删除；TEST 的结果来自验证报告；"报告里有失败"是唯一的测试类挡点 |
| `next_step` 里的 runner 命令和自动完成（`next_step.py:30-69,127-129,187-197`） | 改为提示"用 native 方式测试，然后 `stage-complete` 提交验证报告" |
| engine 里 final 的加载、过期 tree 拒绝、carry / recheck（`engine.py:700-713,1473-1486,2208-2267`） | 删除；tree 绑定由验证报告的 `code_tree` 和 LG-1 承担 |
| 五维审计里的"验证通过 / 需要验证"比例和 `test_call` 计时（`audit.py:265-291`、`trace.py`） | 改为列出报告的命令和结果、审查员确认过的部分；Speed 只用阶段事件的时间 |
| finding 的关闭证据：test / probe proof 要求 runner producer 和 old/old/new `test_call`（`findings.py:162-214`、`review.py:318-327`） | 新证据类型 `reported_test`：引用验证报告事件 id 和其中的命令与结果；R2 专家判为 `ADEQUATE` 才关闭。保留 `diff_assertion`、`refuted`（带 path:line）。删除 runner 作者、nodeid、old/old/new |
| DESIGN_SPEC 的测试引用：pytest 风格 nodeid、固定后缀名单、`kind=example\|pbt`（`schemas.py:88-98,339-419`） | 改为不透明的仓库引用（路径，可带测试名，格式不限）；`kind` 改为可选的描述字段；properties / failure_modes 与测试框架无关 |
| RQ-3（模糊词）、RQ-8b（K6 中英文否定词表） | 不再用词表匹配；"需求要可验证、没有含糊的否定表述"进入 Gate-0 检查清单，由 LLM 判断，任何自然语言都适用 |
| `REVIEW_PATTERNS.md` 里按 SwarmAI 经验写的检查清单 | 通用 review 改用一份和语言无关的检查清单；仓库可在 `.swarmai/review.md` 里补充自己的（SwarmAI 仓库把原清单放在这里） |
| review 派 security 专家时按 Python API、Python/React 路径判断（`review.py:28-36,99-117`）；prompt 里调用 Python 的命令 | 删掉内置的语言启发式：PLAN 或验证报告里声明 `risk: security`、或仓库 `.swarmai/review.md` 声明的路径模式命中时派 security；correctness 专家也可以在 R1 里要求加派。命令改用 `python-backend` 子命令 |
| 落地作者固定 Swarm + Swarm trailer | 默认用仓库自己的 git 身份、不加 trailer；仓库在 `pipeline.toml` 的 `[landing]` 里声明（SwarmAI 仓库声明 Swarm 那一行） |
| worktree 只软链 `desktop/node_modules` | 由 `pipeline.toml` 的 `[worktree] link` 声明；没有声明就不软链 |
| skill 卡片（INSTRUCTIONS.md、stages/*） | 去掉 Python CLI、`pipeline_test.py`、TDD 文件上限等写死的内容；测试一节改成"用 native 方式测试并在 artifact 里报告"；命令改用 `$SWARM_BACKEND_BIN pipeline-v1` |

#### 3.3.5 删掉签名机制，事件改为只追加日志（r12，XG 定）

P5 已定不防 agent 作恶，S6 之后测试证据又是 agent 自己写的报告。签名剩下的作用只是证明"这条记录是本 run 写的"，这本身就是防作恶，却要长期维护密钥、daemon 代执行和一次性 token，还让 pipeline 离不开在线的 daemon。

- **改**：签名事件改为普通的只追加日志。每条记录带 run_id、序号、owner session、相关的 tree hash，以及一个校验和；校验和只用来发现崩溃导致的截断或损坏，不用来防伪造。只有最后一行写了一半（崩溃时正在写），照今天的做法丢掉这一行继续（`store.py:164`），否则无人值守的 run 每次崩溃都要停下等人；中间的记录序号断开或校验和不对，run 进入需要人处理的状态，不会静默继续。
- **保留**（它们管正确性，和防作恶无关）：run 归属和 owner 转移（owner 存活判断照旧）、不可变 contract 和 tree 绑定、落地协议（`land_intent`、base 复核、部分落地记录、交付 tree）、worktree 隔离、run 的操作锁（沿用现有的 flock 实现，`run_protocol/oplock.py` → `utils/file_lock.py`，不需要改）、人工决定卡片和落地确认（作为普通记录写进日志）、**prewarm 子进程的 session id 补传**（`run_owner_gate._with_session`：被 session 接管的 prewarm 子进程环境里没有 `SWARM_APP_SESSION_ID`，由 hook 用 `updatedInput` 在 pipeline 命令前补上；删掉它，被接管的 session 建 run 时 `RunEngine.create` 拿不到有效 session id）。删的只是 token，不是这条补传；`session_link_gate` 继续拒绝 agent 自己赋值这个变量。
- **删**：`authority.key` 及其读保护、`events.allow_hook_writer`、`daemon_exec`（daemon 代执行、socket、队列）、`hook_token` 和 `run_owner_gate` 用 `updatedInput` 发 token 的逻辑、`auth_consumed` 的签名校验。pipeline 命令回到本地进程执行，不再依赖 daemon 在线。
- **随之简化**：`audit_marker_write_gate` 只剩"不让 agent 直接改 run 日志文件"的提醒级保护，是否保留由 §6 的度量决定；gate 不再需要为发 token 识别 pipeline 命令；Windows 少一个结构障碍（Unix socket），pipeline 在 Windows 上仍然不启用，剩下的障碍单独评估。
- **代价**：推翻今天合入的 B-class 和 B-e（`2810e727b 740d6268a dfa761ac7 01fa52bee`）。S6 之前这套机制仍在（S3 照常做 token），S6 一起删；r14 起不发过渡版，所以没有用户会拿到"签名还在、runner 已删"之类的中间状态。
- **现存 run（r16，XG 定，2026-10-24）**：不兼容任何旧 run。XG 本机的历史 run 已全部清掉（673 个 run 目录、审计目录、`worktrees/` 残留、runner 缓存和它在仓库里注册的 worktree、`refs/swarm/snapshots`），新代码不读、不迁移、不归档旧 run。原来的 drain 命令、只读归档和迁移测试都不做。新代码遇到读不了的 run 目录，按损坏处理（列为需要人处理），不尝试按旧格式解析。
- **测试**：重写依赖签名的测试；新增：被接管的 prewarm session 能建 run 且 owner 正确；最后一行写了一半时丢掉继续，中间记录损坏时 run 停下等人处理；daemon 不在线时 pipeline 命令照常工作；两个 session 同时操作同一个 run 时由操作锁串行化。

#### 3.3.6 `.swarmai/pipeline.toml`（全部字段可选）

```toml
[landing]
author = "repo"               # repo（默认）| swarm
trailer = ""                  # 例如 SwarmAI 仓库写 "Co-Authored-By: Swarm <swarm@swarmai.dev>"

[worktree]
link = []                     # 例如 ["desktop/node_modules"]
```

只有这两项，都只影响 pipeline 的落地和 worktree。没有测试命令字段（pipeline 不执行测试），也没有触发和审查字段（r9：由 session 里的选择和 agent 的判断决定）。仓库想告诉 agent 怎么测、要不要多审，写在 README、AGENTS.md 或 `.swarmai/review.md` 里，agent 自己读。

### 3.6 认知文件和 gate 一起改（r11）

gate 删了，认知文件里还写着"所有代码必须走 pipeline、每次 commit 前必须审查"，agent 照样会把人往 pipeline 里赶。所以 S4 里删 gate 的同一个 commit，必须同时改这些文本，缺一处 S4 就不算完成：

| 文件 | 现在写的 | 改成 |
|---|---|---|
| `backend/context/SOUL.md` P5"Cognition Serves Rules" | 默认严格照流程走 | 保留"不为跳步找理由"，去掉以 pipeline 为默认流程的含义；默认是 P4 的"相称" |
| `SOUL.md` P7"When Prose Fails, Build a Gate" | 同类错误出现 3 次就造 gate | 产品只造产品级 gate（P3）；仓库级的纪律交给仓库自己的 pre-commit 和 CI；gate 先量再加（P5） |
| `backend/context/SWARMAI.md` 第 141-143 行 | "Coding → Autonomous Pipeline … The ONLY route for code changes" | 默认 native；风险较大时在 session 里问一次；用户要求时走 pipeline |
| `backend/context/AGENT.md` Lifecycle 行（第 117 行）、R1"Pipeline is mandatory for ALL code changes"、R2"Adversarial review before EVERY commit" | 强制 pipeline、强制 commit 前审查 | R1 改为 P4 的选择规则；R2 改为 P6：native 下 agent 自己决定请哪些专家；Lifecycle 改为 change → native 测试 →（按需）专家审查 → commit |
| `backend/context/SELF.md` 的 gate 清单 | 列着要删的 gate | 按 §3.4 改后的结果重写 |
| `backend/context/EVOLUTION.md`（r14） | `:76-79` C025"Pipeline is unconditional default"，`:96-99` C021"adversarial review mandatory"：这是 XG 个人的纠正记录，被当成产品模板发给新用户（copy-only-if-missing，会进 prompt） | 模板只留结构和说明，不带任何具体纠正条目（P7）；XG 本机副本不动 |
| 其余 `CONTEXT_FILES` 模板（r14） | 未逐个核对 | S4 时逐个过一遍，凡是"必须走 pipeline / 每次 commit 前必须审查"的表述都按上面的规则改 |
| XG 本机 STEERING 的 #1、#5（模板里没有，Standing Rules 为空） | #1 开头是"NO CODE CHANGE WITHOUT PIPELINE FIRST" | 手工改本机副本；模板保持为空 |
| `backend/skills/s_autonomous-pipeline/SKILL.md` 的 description / TRIGGER | 把 pipeline 写成写代码的通用入口 | 只在用户选择 pipeline 时触发；写明它是可选的高级方式 |

送达方式：
- SWARMAI、SOUL、IDENTITY、AGENT 是系统文件，每次 session 启动用安装包里带的模板覆盖本机副本，所以改仓库模板之后，随新的安装包生效（要重新打包）。产品默认行为（native 优先、相称、按需请专家）只写进这几个系统文件，主要是 AGENT.md。
- SELF、STEERING 是 copy-only-if-missing，已有安装拿不到模板的改动。STEERING 是用户自己的覆盖层，模板里的 Standing Rules 保持为空，不往里写产品默认值。SELF 模板的 gate 一节按 §3.4 的结果改。
- XG 本机是唯一的老安装，STEERING 优先级又高于 AGENT，不改的话 XG 用到的就不是产品本身（违反 P7），golden case 在他机器上也过不了。所以 S4 加一步非产品代码的手工同步：改 XG 本机 `~/.swarm-ai/SwarmWS/.context/` 下 STEERING 的 #1、#5 和 SELF 的 gate 一节，和 S4 的 gate 改动同时落地。r13 时已先按 XG 要求同步过一次（见修订记录），S4 时再按最终的 gate 表核对一次。
- **r14：模板已经先改了。**`a84c5008f`（10-24 13:53）提前把 AGENT R1/R2、SOUL、SWARMAI、SELF 的模板改成了 native 默认，gate 还没动，这违反了本节"同一个 commit"的要求。当前在跑的 daemon 是此前打的包，还没受影响；但在 S4 落地之前，任何一次重新打包或重启本机 daemon，都会让新的 R2（审查按需）和仍然注册着的 `adversarial_commit_gate`、`direct_commit_gate`（`hook_builder.py:28-39,369`）同时生效，互相打架。处理：S4 提到 S1 之后紧接着做（见 §7）；S4 落地前不重新打包本机 daemon。

### 3.7 仓库指引要有确定的送达路径（r11）

r9 把 SwarmAI 的"commit 前审查"交给它自己的 AGENTS.md，但现在没有机制保证 agent 读得到：session 的 cwd 是 SwarmWS，cd 进别的仓库不会自动加载那里的 AGENTS.md（Claude CLI 只在启动时按 cwd 加载 CLAUDE.md；S4 前实测确认）。

补法：产品级、只注入、不拦截。
- 一个 session 第一次碰某个 git 仓库时（Bash 的 cwd 或 `git -C`、Read/Write/Edit 的路径落在这个仓库里），注入这个仓库根目录下的 `AGENTS.md`、`CLAUDE.md` 和 `.swarmai/review.md`：Read、Grep、Glob 或 Bash（含 cd、`git -C`）碰到时，用 PostToolUse 的 additionalContext；如果第一次碰就是 Write/Edit，用 PreToolUse 的 additionalContext 在写之前注入（只注入，不拒绝）。
- 每个 session 每个仓库只注入一次；文件改了（mtime 或哈希变化）再注入一次，但**本 session 自己写过的这几个文件不再注入**（r14）：agent 刚写进去的内容不能反过来以"仓库规范"的身份回到上下文。
- **排除（r14）**：SwarmWS（它本身是 git 仓库，而它的 CLAUDE.md / AGENTS.md 每次启动都被强制重置成哨兵，`prompt_builder.py:2265-2282`、`swarm_workspace_manager.py:93-160`，正是为了不让它们进上下文）、`~/.swarm-ai/` 下的 runtime 和数据目录、pipeline 的隔离 worktree 以外由 SwarmAI 自己生成的目录。
- 有大小上限，超过时注入开头部分并写明文件路径，让 agent 自己去读全文。
- 只注入，不拒绝任何操作。对所有用户、所有仓库都一样，不违反 P3 和 P7。
- 信任级别按用户有没有主动认领这个仓库来分（r14）：
  - **已绑定成 DDD 项目的仓库**（用户主动绑定过）：agent 按仓库规范遵守它，地位相当于 CONTRIBUTING 文档（例如 SwarmAI 仓库的"commit 前请专家审查"）。
  - **未绑定的仓库**（例如刚 clone 的第三方仓库）：注入时标成"未确认的仓库指引"。agent 只用其中的构建、测试、代码风格这类开发约定；凡是改变工作方式的内容（审查流程、要求执行某个脚本、对外操作），先告诉用户并确认后才照做。
  - 两种情况下都适用：优先级低于用户的指令和产品规则（系统认知文件、P1-P7）。和当前任务无关的指令不执行，尤其是外发数据、读写凭证、操作别的仓库或改 agent 自己的配置；遇到这类内容，告诉用户这里可能有 prompt injection。注入时用明确的分隔标出来源路径，和系统指令分开。
- 验收：在一个带 AGENTS.md 的样例仓库里，agent 第一次写文件之前，上下文里已经有这份指引；同一个 session 里第二次不再重复注入；agent 改了这份 AGENTS.md 之后不会再被注入；SwarmWS 从不被注入；一个未绑定仓库的 AGENTS.md 里写着"commit 前运行 ./x.sh"，agent 先问用户，不直接执行。

### 3.8 native 模式按需使用隔离 worktree（r15，XG 定）

native 默认在用户的 checkout 里原地改（P1：和普通 CLI 写代码一样；隔离 worktree 对单 session 用户有代价：依赖目录不在、编辑器看不到改动、多一步合并）。它解决的是多个 session 同时改一个仓库，所以作为产品能力按需使用，不加 gate、不强制：
- 只有本 session 在改这个仓库：原地改。
- agent 发现另一个活跃 session 在同一仓库有未提交的改动：主动改用本 session 自己的隔离 worktree，并告诉用户；完成后 ff 合并回去、清理 worktree。
- 用户随时可以说"用 worktree"。
- 实现复用 pipeline 的 `run_worktree`（建立、依赖软链按 `.swarmai/pipeline.toml` 的 `[worktree] link`、清理），并在 AGENT.md 写明判断规则；"另一个 session 有未提交改动"的检测放在 S4b，和 P7 清理一起做。
- SwarmAI 仓库"总是在自己的 worktree 里改"的规矩照旧写在它自己的 AGENTS.md 里。

S4b 实现（2026-10-24）：
- 谁改了什么：file tracker（PostToolUse）为每个 session 记 `_files_written`，只记写（Write / Edit / MultiEdit / NotebookEdit、Bash 的写目标），不记读。
- 检测：`core/shared_checkout.py`，挂在和 §3.7 仓库指引一样的位置（读、Bash 之后的 PostToolUse，写之前的 PreToolUse），只注入、不拦截。本 session 第一次在某仓库工作时，取其他活跃 session 写过、位于这个仓库里的文件，用 `git status`（`GIT_OPTIONAL_LOCKS=0`，只查这些文件）确认还没提交，有就注入一段说明：哪个 session、哪些文件、怎么改用自己的 worktree。每个 session 每个仓库只提示一次；SwarmWS 和 app 数据目录不查。
- 成本（实测）：没有别的 session 在这个仓库写过文件时，每次调用 0.17 ms，不启动 git；有的话 `git status` 结果按（仓库、文件集合）缓存 30 秒，2000 文件的仓库首查约 30 ms，之后 0.5 ms。都在 gate 线程池里跑，不占事件循环。代价是 30 秒内的状态变化可能晚一点才看到。
- worktree：`scripts/session_worktree.py`（`"$SWARM_BACKEND_BIN" py -m scripts.session_worktree`）。`create` 在 `<app data>/session-worktrees/<name>/<repo>` 建 `session/<name>` 分支，依赖目录按 `.swarmai/pipeline.toml` 软链（复用 `run_worktree`）；`land` 要求已全部提交，rebase 到主 checkout 当前分支再 `merge --ff-only`，失败原样报告、不强制；`remove` 只在分支已合并时删 worktree 和分支，不会删未合并的工作。不放在 `<app data>/worktrees`：那里归 pipeline，清理任务会把别的东西挪走。
- AGENT.md 的 Shared checkout 规则指向这个命令；用户说留在原地就留在原地。
- 已知局限：只看得到同一个 daemon 里的 session（另一个终端里的 Claude Code 等看不到）；Bash 写入只认 `parse_bash_write_targets` 能解析的形式（重定向、tee、cp、mv）；提示到的时候本 session 这次的写可能已经落在主 checkout，说明里让 agent 告诉用户。

丢弃别人未提交的工作（r17，XG 2026-10-25）：`dangerous_command_gate` 原来的 history-rewrite 规则（2026-08-11 `b74575e83`）在"有任何别的 live session"时，对 rebase / `reset --hard` / `commit --amend` / `branch -f` 等一律要人批，只放过本 session 的 run worktree 和 temp 目录里的 repo。它挡住了 AGENTS.md 规定的 worktree 流程（`git rebase main` 再 ff 合并）和 `session_worktree land`，违反 P3；按路径猜"是不是我的"，每补一种例外就漏一种。改为按 git 实际会丢什么来判断：
- 移动 ref 的操作（rebase、amend、移动 ref 的 `reset`、`branch -f/-D/-M`、`checkout -B`、`update-ref`、`filter-*`）不再询问：旧 commit 在 reflog 里，可以找回。git 也不允许删除另一个 worktree 正在 checkout 的分支。
- 销毁未提交改动的操作（`reset --hard/--merge`、`checkout -f` / `-p` / `-- <path>` / `.` / 一个指向现有文件的操作数、`restore`（工作区）、`switch -f/--discard-changes`、`clean -f`、`worktree remove --force`）只在一种情况下询问：它运行的仓库（`worktree remove` 则是被删的 worktree）里，有**另一个 session 写过、git 仍报告未提交**的文件。用的就是上面检测用的那份事实（`shared_checkout.sibling_uncommitted`，strict：不用缓存，git 失败或超时就询问）。目录跟踪 `cd X &&` 和 `git -C`，跟不上的（`eval`、反引号、`GIT_DIR`、heredoc 里由 shell 执行的命令、解析不了）按"另一个 session 写过文件的所有仓库"判断。审批卡片列出会丢的文件。
- 不保护：用户手动改、没有任何 session 写过的文件（和原来单 session 时一样；把"本 session 没写过的脏文件"当成别人的，会把格式化工具、脚本生成的文件也算进去）；`git stash drop/clear`（stash 不是某个 session 写的文件，这份事实说不了它）；另一个 daemon 外的进程。
- 成本：只有命令里有销毁类 git 操作才会跑；没有别的 session 写过文件时不启动 git；有的话每个仓库一次 `git status`（只查那些文件），在 gate 线程池里跑。
- 删除：`_is_history_rewrite`、`_segment_is_history_rewrite`、`_rewrite_is_branch_local`、`_rewrite_dirs`、`_rewrites_only_scratch`、`_scratch_dir`、`_temp_roots`、`_repo_store_of`、`_own_run_worktree`、`_under_any`、`_other_live_sessions_present`。pipeline run 的 `run_amend_gate` 不变。
### 3.9 P7 清单：产品代码里认 SwarmAI 的地方（S4b，2026-10-24 盘点）

盘点方法：在 `backend/` 的产品代码（522 个 `.py`，排除 tests、skills、templates）里搜 `"SwarmAI"`、`Projects/SwarmAI`、`SWARMAI_ROOT`、`_swarmai_root`、`_source_repo_root`、`source_repo`、`worktree_mode` 和写死的 checkout 路径，逐处读代码归类。

| 类 | 位置 | 处理 |
|---|---|---|
| A. 产品名本身（允许的例外） | `config_settings.app_name`、数据目录名（`runtime_paths`、`desktop_main` 的日志目录）、`SWARMAI.md` 这个系统认知文件的节名、`ddd_packager` 的隐私脱敏正则、docstring 里的示例路径 | 不改，列入结构测试的例外清单 |
| A'. seed 默认项目（允许的例外） | `swarm_workspace_manager.DEFAULT_PROJECT_NAME`、`project_registry.SWARMAI`、`.gitignore` 里的 `!Projects/SwarmAI/` | 不改：每个用户的工作区都有这个默认项目，行为对所有人一样 |
| B. 通用功能把"默认项目"写成字面量 | `lifecycle_manager:1368`（run 元数据缺 project 时）、`context_health_hook:1050,2290`、`daily_activity_hook:416`、`ddd_cultivation._classify_lesson`、`code_intel_feed` 三个默认参数、`signal_ddd_bridge`、`skill_proposer:393`、`persist_routing:265`、`routers/cultivation.py` 四个默认 query、`routers/eval.py:316,379`、`eval_service:1730` | 改成通用：能知道真实项目的地方用真实项目（run 元数据、请求参数、改动所在仓库绑定的项目）；不知道的地方统一引用 `DEFAULT_PROJECT_NAME`，不再到处写字面量 |
| C. 只在开发 SwarmAI 本身时才有意义（假设 SwarmAI 源码仓库在本机） | `engine_metrics._find_swarmai_root` + "新 backend 模块 -> SwarmAI TECH.md"（7 处）、`hooks/code_change_feed`（写死 `SwarmAI-Workspace/swarmai` 路径、`load_project_graph("SwarmAI")`）、`ddd_orchestrator._MANUAL_WATCH_PATHS`（AIDLC、SwarmAI 两份写死的文件列表）+ `_find_swarmai_root` + `~/Desktop/SwarmAI-Workspace/swarmai`、`ddd_self_audit._source_repo_for`（只认 SwarmAI）、`context_injector._swarmai_root`（按后端源码位置剥路径）、`security_hooks._source_repo_root`（`_repo_relative`）、`jobs/handlers/swarmai_monthly_report`（系统 job） | 改成通用：项目对应的代码仓库一律从该项目 `bindings.yaml` 的 code-repo 绑定读取（已有的通用机制），不再找"SwarmAI 根目录"；写死的 watch 列表删掉，改为看绑定仓库的全部改动；路径剥离改为剥"该路径所在的 git 仓库根"。`swarmai_monthly_report` 改为按项目的通用月报，或删除（待 XG 定） |
| D. 只服务 SwarmAI 仓库的 hook（r10 已定删除） | `inclusive_term_guard`、`design_lint_guard`、`change_triggered_eval`、`session_checkpoint` 里写死的 pytest 提醒 | 从 `hook_builder` 删除；SwarmAI 仓库用自己的 `scripts/lint_*.py` + pre-commit + CI 接住 |
| E. S6 随 runner 一起删 | `utils/source_repo.py`、`pipeline_command` / `run_owner_gate` / `worktree_write_gate` 里的旧脚本形式、`SWARM_WORKTREE_MODE` | 按 r14 留到 S6 |
| F. SwarmAI 仓库自己的开发脚本 | `backend/scripts/` 下的 `refresh_ai_docs`、`update_convergence`、`ci_eval_gate`、`artifact_cli release-gate`、`eval_runner` 等的 `--project SwarmAI` 默认值 | 不是运行时产品代码，属于 P7.2 允许的"仓库自己的脚本"，不改 |

S4b-1（`d0194fc74`）已删除 `worktree_mode` 设置（P7.5）。

已做（2026-10-24）：
- D：`inclusive_term_guard`、`design_lint_guard`、`change_triggered_eval`、"改了多个源文件没跑 pytest"的提示从产品删除；规则原样搬进 SwarmAI 仓库的 `scripts/lint_terms.py`（只查新增行），CI 每次 push / PR 跑，AGENTS.md 写明提交前跑（`5e1e53621`）。
- C1：删除 SwarmAI 月报 job 和对应 skill（XG 定，`d8dbde535`）。
- C2：新增 `ddd_bindings.governed_repo_roots(project_dir)`，只读 bindings.yaml 声明的仓库（`bindings[].worktree`、`governed_assets` 的 `source_workspace`），不猜。`engine_metrics`、`ddd_refresh`、`ddd_self_audit`、`ddd_orchestrator`、`code_change_feed`、`context_injector` 全部改用它或改为通用规则；`ddd_orchestrator` 的 knowledge_staleness 通道删除。顺带修了一个 bug：每个项目的 DDD 过期判断原来都在数 SwarmAI checkout 的 commit（`918ac0320`）。
- B：`DEFAULT_PROJECT_NAME` 只在 `project_registry` 定义一次，十几处字面量改为引用它；置顶项目原来是写死的 `("AIDLC", "CMHK_SalesIntel")`，改为读各项目 `.project.json` 的 `"pinned": true`；删除没人用的个人项目别名（`BMS_BIZ`、`AIDLC`、`GITHUB_COMMUNITY`、`PHYSICAL_AI`、`QUICK_FOR_BIZ`）。

做的过程中新发现的 P7 项（不在原清单里），2026-10-24 已处理：
- job 系统：删除 `system_jobs._get_swarmai_root`（找 `SWARMAI_SOURCE`、`~/Desktop/SwarmAI-Workspace/swarmai`、`~/swarmai`、`/opt/swarmai`）；没写 cwd 的 script job 原来退回 SwarmAI 源码目录运行，现在在 app 数据目录运行，和其他产品 job 一样（`75d823be7`）。
- 产品 skill 不再假设本机有 SwarmAI 源码：`s_ddd-weekly-report`、`s_system-health`、`s_loops-health` 改用 `"$SWARM_BACKEND_BIN" py`；`s_ddd-distribute`、`s_estimate-tokens`、`s_library` 的脚本先从当前解释器 import `core.*`，删掉到 home 下猜 checkout 位置的逻辑；`loops_health` 的 K4 改为 n/a，stale-pipeline 检查改查项目自己绑定的仓库。新增结构测试 `test_skills_no_source_checkout`：产品 skill 里出现 `$SWARMAI_ROOT`、`SWARMAI_SOURCE`、`SwarmAI-Workspace` 即失败（`9e67c9445`）。
- SwarmAI 开发 skill 离开产品（XG 定：放进 DDD，不放仓库 `.claude/skills/`，这样贡献者导入 SwarmAI DDD 包就能拿到，产品不用给 SwarmAI 开特例）：`s_swarm-build`、`s_swarm-release`、`s_swarm-ci`、`s_swarm-daemon`、`s_chat-brain-check`、`s_health-check` 移到 `Projects/SwarmAI/4-capabilities/`，在其 `bindings.yaml` 声明为 `skill-set`，仓库路径从 swarmai 绑定的 `worktree:` 读成 `$SWARMAI_ROOT`。产品 AGENT.md 的 R8/R10/R11 不再点名 `s_swarm-*` 和 `swarmai/`（`2e13a5fdb`）。`s_system-health` 只看本机资源，留在产品里。
- Community：原计划整页删除，实施时发现一半是通用功能，所以只删读 GitHub_Community 数据的部分。删掉 Hot Topics、Outbound tab、`/api/community/hot-topics` 和 `/engagement`，以及写死的 GitHub 账号；保留 Inbound（`Knowledge/Signals`、`Reports`）和 Watching（信号源配置编辑）。Need You 的 digest 原来只认 job 名带 `github-community`、写死 `brain="GitHub_Community"`，改为：某个 DDD 在 bindings 里声明的 job（DDD job 注册表），它的新结果作为该 DDD 的 digest 出现（source 由 `community_digest` 改为 `job_digest`）（`0cb3a94a5`）。
- XG 的 SwarmWS 数据同步改了（经 XG 同意，有备份）：`Projects/SwarmAI/bindings.yaml` 加 skill-set；`Projects/GitHub_Community/bindings.yaml` 补声明一直在跑的 `uj-github-community-weekly-sediment--report`，否则新规则下它的周报 digest 不再出现。

仍未处理：
- `run_worktree.code_repos` 有自己的 bindings 读取和"自托管仓库按名字匹配 daemon 自己的 checkout"，S6 再收进 `governed_repo_roots`。
- `community_data` 判断 HTML 报告是否属于社区报告的文件名白名单里有 `github-community-weekly-`。只影响分类，对其他用户没有效果，暂不改。


### 3.4 gate：只保留产品级的

| 类别 | gate | 处理 |
|---|---|---|
| 所有用户的安全和会话机制 | `dangerous_command_gate`、`external_approval_gate`、`ask_question_gate`、`bash_syntax_guard`、`skill_access_checker`、工具日志和审计、compaction 前后的连续性、失败诊断、文件跟踪、用户纠正捕获、observation 记录 | 保留 |
| SwarmAI 产品的记忆和 DDD 系统（只提示、不拦截） | `governance_file_gate`、`memory_edit_guard`、DailyActivity / DDD 相关 hook | 保留 |
| 用户开启的 pipeline run 的完整性（只在 run 里生效） | `run_owner_gate`、`run_amend_gate`、`session_link_gate`（收窄为只管 pipeline 命令）、`expert_type_gate`、`expert_budget_gate`、worktree rule 1–3、`main_dirty_reporter`、`v1_reflect_trigger` | 保留 |
| `active_run_commit_gate` | r8：今天它扫描所有 session 的 run，别的 run 读不出来时也会拒绝（`security_hooks.py:3628-3732`） | 改为按 owner 判断：只在提交者就是该 run 的 owner session（S6 前读签名事件，S6 后读日志）、且提交的是 run 改过的文件时拒绝；其他 session 只提示；读不出来的 run 不再导致拒绝 |
| `audit_marker_write_gate` | r8：今天一直生效，保护整个共享审计目录，包括 commit 前审查用的通用 marker（`security_hooks.py:6586-6644`） | S4 实施时（2026-10-24）核对：同一个目录里既有 `runs/<run_id>/` 的签名事件和 key，也有审查 marker 和 `main_red.jsonl`，按路径细分会改动这个 gate 精细的 Bash 解析，而它本来就不挡 native（普通用户不碰这个目录）。所以 S4 只改了拒绝提示（不再提 commit 前审查）。r18 核对 `gate_decisions.jsonl`（10-21 至 10-24）：15 次拒绝没有一次是真写入或真读 key，其中 6 次是 `find`、`grep -r`、`rg` 扫到 key 的上级目录被当成读 key；这条规则已随 S6-3（`5aa04971c`）和 key 一起删掉，剩下的写入规则不变。原计划：S4 到 S6 之间只保留签名事件和 `authority.key` 的保护，始终生效（只在 pipeline 内部目录里，普通用户碰不到）；S6 后见 §3.3.5；commit 前审查 marker 的保护随 `adversarial_commit_gate` 一起删除（r9） |
| `external_approval_gate` | r8：靠工具名里的词（create / add / set / save）推断风险，会把远端的写操作误判成本地写（`tool_risk.py:65-75,137-154`） | 保留这个产品级 gate，加入按工具元数据判断（工具声明自己是否有对外副作用）。r14：MCP 工具今天都没有元数据（`tool_risk.py:105-112`），所以元数据**只能把风险往上调**，不能往下调（第三方 MCP 自称 readOnly 不算数）；没声明的工具照旧按现有的动词分类；用户可以在本地覆盖某个工具的分类（`tool_risk` 已预留的第 1 层 override）。S4 已实现：`<app data>/tool_risk_overrides.json`；元数据只升不降的规则在 `classify` 里，但 hook 今天拿不到 MCP 的工具注解，gate 暂不传元数据，等有来源再接 |
| 挡 native、或把人赶进 pipeline | worktree rule 4、`direct_commit_gate`、`adversarial_commit_gate`（r9：连同 SubagentStop 写审查 marker 的逻辑一起删；marker 链路在 S4 漏删，r18 `1dfdedba0` 补删：`_adv_` marker、意图分类、reviewed_paths、coverage 读取和 auto-commit 扣文件）、`pytest_command_guard`、`enforcement_injector` 里"必须对抗审查"的提示、`pipeline_test_gate` | 删除 |
| 保护 session 本身（r15） | `background_command_guard`（后台命令逃出超时会一直挂着；近 4 天拒绝 9 次，都是普通短命令被放到后台，改前台即可）、`image_read_dedup_guard`（重复读未变化的图；近 4 天 0 次） | 保留。S5 度量后复查：持续 0 次的删掉 |
| 只对 SwarmAI 仓库有意义 | `design_lint_guard`、`inclusive_term_guard`、`change_triggered_eval`、`session_checkpoint` 里写死的 pytest 提醒 | 从产品里删除（P7）：改成 SwarmAI 仓库自己的 lint 脚本、pre-commit 和 CI 检查 |
| 运维开关 | `SWARM_GATE_FORCE` | 不作为产品开关，只留给运维调试；顺手修掉三个声称支持却从不检查它的 gate |

### 3.5 专家：native 和 pipeline 共用（r9）

- 专家在 `backend/experts/code/` 下（S6-4 前 12 个；按 §3.5.1 合并后是 correctness、security、concurrency、state-machine、api-contract、integration、performance、operational、ux），由 `expert_manager.py` 加载；每个文件的 frontmatter 写了显示名和 `scope`。
- 新增 skill "专家审查"：把专家名录（每位管什么、什么改动该请）从 frontmatter 自动生成，注入给 agent。新增专家不改代码。名录给出的默认规则：

  | 改动 | 建议请 |
  |---|---|
  | 一般逻辑改动、有一定规模 | correctness |
  | 认证、权限、输入校验、凭证、网络、文件路径 | security（风险高时它的"高风险改动"一节一起审，§3.5.1） |
  | 并发、锁、异步、重试 | concurrency |
  | 状态机、生命周期 | state-machine |
  | 对外接口、schema、数据格式 | api-contract |
  | 跨模块、改了调用方 | integration |
  | 热路径、大数据量 | performance |
  | 后台任务、重试、超时、日志、故障恢复 | operational |
  | UI 交互 | ux |
  | 小改动、文档 | 通常不用请 |

- native 模式：agent 用 sub-agent 加载对应专家的 prompt 审查，自己处理 finding。不写 run 日志，不经过任何 gate。
- pipeline 模式：照旧由 `review-start` 派发、finding 账本收敛。
- 专家 prompt 改为通用：去掉对 SwarmAI `REVIEW_PATTERNS.md`（RP 编号）的引用，换成和语言、仓库无关的检查要点；仓库可以在 `.swarmai/review.md` 里补充自己的要点，专家审查时一并读取。

#### 3.5.1 专家合并：12 个减到 9 个（r18，S6-4 实施）

判断依据是职责有没有重叠、在 native 和任意仓库下还成不成立。r16 清掉了历史 run，"哪个专家真的找出过问题"现在没有数据，所以这是结构判断，S5 有了 run 数据再复查（§6）。

| 专家 | 问题 | 处理 |
|---|---|---|
| `red-team` | pipeline 里改动 ≥50 行且有安全风险时，和 security 一起派（`review.py:186-190`），两者审的是同一类风险。prompt 是"主动攻破"写法：审查模型可能拒绝任务、什么都不返回，空结果又容易被当成"审过了" | 并入 `security`：security 的 prompt 加一节"高风险改动"，写成"逐条确认防护成立"（输入从哪进、在哪校验、绕过路径是否都被挡住），每条给出处或标 UNCHECKED。高风险改动从 2 个 sub-agent 变 1 个 |
| `spec-compliance` | 审"实现是否匹配 PLAN 验收标准"。native 和 direct profile 都没有 PLAN；有 PLAN 的 full profile 里 Gate-0 和 LG 检查已经在核对验收标准，pipeline 也不派它 | 删除 |
| `code-quality` | "架构、约定、集成正确性"：集成和 `integration` 重叠，约定和 correctness 加仓库的 `.swarmai/review.md` 重叠；pipeline 不派它（`review.py:174`） | 删除。prompt 里集成相关的检查点并入 `integration` |
| `operational` | scope 写的是"hook / 定时任务 / daemon"，是 SwarmAI 自己的场景（P7） | 保留 id，scope 和 prompt 改成通用的"后台任务、重试、超时、故障恢复、日志"。不并入 performance：一个管"出错后能不能恢复"，一个管"快不快"，触发条件不同 |
| `ux-test` | 把前端体验和测试覆盖两件不相关的事绑在一起；S6-1 之后测试证据是 agent 自己的验证报告，测试覆盖的审法也变了 | 改名 `ux`，只管前端体验。"验证报告是否覆盖了这次改动"写进 `correctness` 的检查清单 |

不动的：correctness、security、concurrency、state-machine、api-contract、integration、performance。它们各自对应一类明确的改动，职责不重叠。

合并后：correctness、security、concurrency、state-machine、api-contract、integration、performance、operational、ux。

**改哪些地方**（同一个 commit）：
- `backend/experts/code/`：删 `red-team.md`、`spec-compliance.md`、`code-quality.md`；`ux-test.md` 改名 `ux.md`；改 `security.md`、`integration.md`、`operational.md`、`correctness.md` 的 prompt 和 scope。
- `core/run_protocol/review.py` `reviewers()`：名单只剩 correctness、security；`required` 里写的旧 id（`red-team` 等）映射到对应的新专家，其余照旧回退到 correctness。
- frontmatter 的 `spawn_as`：唯一读它做判断的是 `_registered_adversarial_experts`，已随 `1dfdedba0` 删除，现在只有 `routers/experts.py` 原样返回，前端不用。字段从 frontmatter、`expert_manager.py`、`routers/experts.py` 一起删。
- `s_expert-review`：`CATALOG.md` 用 `gen_expert_catalog.py` 重新生成；INSTRUCTIONS 的默认规则表去掉"风险高时加 red-team"，`ux-test` 改 `ux`；SKILL.md 描述里的专家列表同步。§3.5 上面的表格一起改。
- `pipeline_expert_agent.py` 的描述、`s_autonomous-pipeline/INSTRUCTIONS.md` 里的名单。
- 测试：`test_run_protocol_review.py`（名单、旧 id 映射）、`test_expert_manager.py`、`test_experts_router.py`，以及引用旧 id 的 fixture。

**验收**（测试先行，在当前代码上先红）：
- `reviewers()` 对任何输入都不返回 `red-team`；≥50 行且有安全风险时只返回 correctness 和 security；`required=["red-team"]` 得到 security。
- 生成的 CATALOG 里没有已删的 id，每个留下的专家都有一行。
- `security.md` 有"高风险改动"一节，用词是确认防护成立，不出现攻破、伪造之类的指令。
- 变异：把 `red-team` 加回名单，第一条测试变红。

**没解决、留给 S5 的**：合并后高风险改动只剩 security 一个视角，会不会漏东西，要看 S5 的 run 数据（security 在高风险改动上的 finding 数和后来发现的漏网问题）。如果漏得明显，再考虑给 security 加第二轮，而不是恢复 red-team。

**已实施（S6-4a，2026-10-25）**：按上面的清单做完。计划里没写到的一处：原来 `reviewers()` 给出的理由不会进专家的 prompt，security 没法知道这次是高风险改动。所以 `reviewers()` 给 ≥50 行且有安全风险的 security 那一行加 `high_risk: true`，engine 在 R1 和增量审查的 handout 开头写一行 `HIGH-RISK CHANGE (<理由>)`，security.md 的"高风险改动"一节以这一行为触发条件。另外：`ux` 不再审测试覆盖；`integration` 加了"替换 / 搬移的对等性"一节（来自 code-quality）；`operational` 的 §1 换成和语言、部署方式无关的表；`correctness` 的 API contract 一条不再点名 Pydantic 和 `toCamelCase`；`lint_experts.py` 不再要求 `spawn_as`；前端专家列表不再显示它。

## 4. 用户场景

| 场景 | 体验 |
|---|---|
| 新用户第一次装（macOS / Linux / Windows） | 下载安装包，按下载页说明放行未签名 app；首次启动部署外置 runtime 并启动后端，onboarding 配置模型凭证，开始聊天 |
| 在自己的仓库里修一个 bug（最常见） | 全程 native：读代码、改、跑测试、`git commit`。没有任何 gate 挡 |
| 仓库绑定成 DDD 项目 | 和上面一样走 native；DDD 的知识、记忆、REFLECT 照常可用 |
| 风险较大的改动 | agent 说明理由，在 session 里问一次：直接改，还是走 pipeline |
| 小改动 | agent 直接改，不问 |
| native 改完之后 | agent 自己判断要不要请专家、请谁（§3.5），修掉发现的问题，在交付里写清楚 |
| 用户明确要求无人值守做完 | 走 pipeline，agent 自主推进到落地或到需要人决定的地方 |
| Windows 用户 | 只用 native；pipeline 命令明确返回不支持 |
| 给 SwarmAI 本身贡献代码 | SwarmAI 仓库和别的仓库一样：默认 native，agent 按改动请专家；"commit 前一定审查"写在 SwarmAI 的 AGENTS.md 里作为指引；它的 `pipeline.toml` 只声明 Swarm 落地身份和 worktree 软链；精细选测用它自己的 `swarm_select_tests.py` |

## 5. 验收

### 5.1 让"安装包缺东西"这类错误再也发生不了

- **G1 真实安装验收**，r14 起只有一版（不发过渡版），下面两部分都过了才允许发布：
  - **native 和安装**：每个发布目标（macOS arm64/x64、Windows x64、Linux x64），在一次性的干净 VM/容器里（没有源码 checkout，没有系统 Python，PATH 里没有开发工具），先模拟真实下载：给安装包打上 `com.apple.quarantine`（macOS；Windows 对应 Mark-of-the-Web 的 `Zone.Identifier`），按陌生用户的方式安装和首次打开（未签名 app 走"仍要打开"），再验证：拷进 `runtime/` 的后端能执行、launchd / 子进程拉起的 daemon 能起来、自带的 Claude CLI 能执行，以及 app 启动、外置 runtime 部署、`/health`、seed 和迁移、onboarding 可达、MCP 工具可用、`py` 子命令能跑一个有代表性的 skill 脚本、更新到下一个版本再回滚；再在一个样例仓库上跑通一次 native 修改 + commit。"PATH 里没有开发工具"不包括 git（r14）：git 是用户的前置条件（Windows 上 Claude CLI 还需要 Git Bash，`lib.rs:2806-2829`；macOS 上的 `/usr/bin/git` 只是 CLT 安装桩），VM 里预装 git；另加一项：没有 git 时 onboarding 检测出来并告诉用户怎么装。
  - **通用 pipeline**：macOS 和 Linux 上，在样例仓库矩阵上跑 pipeline（见 §5.2）。Windows 只验证 `pipeline-v1` 返回"不支持"。
- **G2 包内容清单**：构建时生成一份清单（必需文件、哈希、架构、可执行文件、原生库），G1 安装后逐项核对，缺一项就失败。
- G1、G2 是 release workflow 发布步骤的前置 job，不过不发布。本地也能用同一个脚本 `scripts/verify_installer.sh` 跑。发布结论必须在干净 VM 或 CI runner 上得出：受管的工作机（例如装了 CrowdStrike Falcon 的 XG 机器）会直接杀掉并删除带 quarantine 的未签名可执行文件，这和陌生人机器上 Gatekeeper 的行为不同；在这类机器上只能跑自检模式（`--no-quarantine`、`--launcher direct`、`--backend-dir`，退出码 2）。

### 5.2 产品原则

- 语言无关用**行为测试**证明，不用"核心源码里搜不到某些词"这种字面检查（r8）：样例仓库矩阵（Python、Node、Go、Rust、无测试、monorepo、unborn）上各跑一个 direct 和一个 full 的 pipeline run，都能走到落地，REPORT 里写着 agent 报告的测试和审查结论。核心里如果确有必须出现语言名的地方（例如 SwarmAI 仓库自己的工具），列入一份经过审查的例外清单。
- 结构测试：在任意仓库里（有没有 `pipeline.toml` 都一样），没有开 pipeline run 时，native 写文件、跑任意测试命令、后台起进程、`git commit`，都不被任何 gate 拒绝。
- 结构测试：hook_builder 里注册的每个会拒绝的 gate，都在 §3.4 的表里有归类；新增 gate 不进表就失败。
- 结构测试：TEST 验证报告的 `code_tree` 不等于落地 tree、或这个 tree 没经过 Gate-2 时，落地被拒绝（§3.3.3）。
- SwarmAI 仓库：声明 `pipeline.toml` 后，它的 pipeline run 照常落地，Swarm trailer 和 worktree 软链按声明生效；其余行为和其他样例仓库一致（P7）。
- **golden case（r11，行为测试，用真实 agent 跑）**：在一个陌生仓库里提一个小改动，agent 直接 native 修改并 commit，全程不提 pipeline、不问要不要走 pipeline、不被任何 gate 拦。再跑一个风险较大的改动，agent 问一次"直接改还是走 pipeline"。两条都要在装好的安装包上跑，不只在源码环境里跑。
- 结构测试：`CONTEXT_FILES` 里的全部模板（不只 §3.6 表里列的，r14 起包括 EVOLUTION、MEMORY 等 copy-only-if-missing 的模板）都不再有"代码必须走 pipeline""每次 commit 前必须审查"这类表述，也不带 XG 个人的纠正或记忆条目。
- 结构测试（r14）：产品代码里只有 §3.2 的命令识别模块认 pipeline 命令。
- 结构测试（r14）：TEST 退回 REVIEW 后，没有经过增量审查的 tree 不能落地。

## 6. 度量（P5：先量再加）

- **每个 run 的成本**：调用次数、token、墙钟时间、人工介入次数，按 profile 分，从 run 日志和 `run_audit.json` 读。
- **每个 gate 的实际效果**：一段时间内每个 gate 拦了多少次（`gate_decisions.jsonl`、`scripts/gate_audit.py`），抽样判断多少是真问题、多少是误拦。
- 没有这两组数字之前，不新增 gate，不新增加固。

## 7. 实施顺序（每步一个 commit，测试先行，变异验证）

**r14（XG 定）：不发过渡版。**S1 到 S6 全部做完、G1/G2 全绿之后才发一次版。没有"能装、能聊，但 pipeline 半通"的版本发给任何人。

**每个 commit 自身一致**：不发版不等于中间状态无害，XG 本机和 CI 仍然跑在 main 上。所以每一步落地后，main 上的 native 写代码和 commit、以及 pipeline 从建 run 到落地，都要完整可用；一个功能的旧路径和它的替代品，在同一个 commit 里切换。

| 步 | 内容 | 为什么排在这里 |
|---|---|---|
| S1 | G1/G2 安装验收脚本，先在 v2.3.0 上跑红（已落地 `150777670`） | 先有红的验收 |
| S4 | gate 按 §3.4 删改：删挡 native 的 gate（含 `adversarial_commit_gate`）；`active_run_commit_gate` 按 owner；`audit_marker_write_gate` 收窄；`external_approval_gate` 加元数据（只升不降）；`pipeline.toml` 的 `[landing]`、`[worktree]` 读取，**SwarmAI 仓库的 `.swarmai/pipeline.toml` 同一个 commit 加上**（Swarm 落地身份和 trailer、`desktop/node_modules` 软链；否则读取上线后 SwarmAI 的 run 会用默认值落地，`landing.py:262,286`、`run_worktree.py:51`）；P4 的 session 内询问写进 agent 指引；专家名录 skill（§3.5）；SwarmAI 的 AGENTS.md 写入"commit 前审查"指引；§3.6 认知文件核对（含 EVOLUTION 模板）；§3.7 仓库指引注入；XG 本机 STEERING / SELF 手工同步 | `a84c5008f` 已先改了模板，S4 越晚，"认知说按需审查、gate 还在拦"的不一致持续越久（§3.6）。S4 和安装包无关，可以先做 |
| S2 | 打包修复 + 三平台统一的版本化 runtime 部署和启动 + 四个发布目标（§3.1） | |
| S3 | `python-backend` 子命令（`pipeline-v1`、`py`）+ 打包版里所有起 Python 子进程的地方改用子命令 + `SWARM_BACKEND_BIN` + 命令识别模块（§3.2，替换六处文本匹配）+ Windows 返回不支持。旧的"python + 脚本路径"形式和 `source_repo` 保留到 S6 | 新旧两种命令形式并存，runner 照常工作 |
| S4b | §3.8 native 按需 worktree（检测另一 session 的未提交改动 + AGENT.md 规则）；P7：清理产品代码里识别 SwarmAI 仓库 / 项目的逻辑（先出完整清单，逐项改成通用或删除）；删除 `worktree_mode` 设置；dev 模式的 `SWARM_BACKEND_BIN` shim；SwarmAI 仓库自己的 lint 脚本、pre-commit、CI 接住被删掉的三个 hook；XG 切换到和 CI 相同流程打出来的安装包 | |
| S5 | 度量（§6） | |
| S6 | pipeline 通用化（§3.3.3、§3.3.4）：验证报告事件和显式完成；删测试执行器、runner 证据相关的机器判定和状态，同一个 commit 删旧命令形式和 `source_repo`；TEST 不改代码、改了退回 REVIEW；审查记录 tree、增量审查；LG-1 绑定审查过的 tree；`reported_test` 证据；DESIGN_SPEC 测试引用通用化；Gate-0、Gate-2 检查清单补上对应要求；review 去掉语言启发式；REFLECT 的未知项目回退；删掉签名机制、事件改为只追加日志、pipeline 命令回到本地执行（§3.3.5）；通用 review 清单；skill 卡片重写；SwarmAI 仓库的 `.swarmai/review.md`、`swarm_select_tests.py`；重写受影响的 E2E 测试 | runner、旧命令形式、签名一起切换 |
| 发布 | G1 两部分（native 和安装、通用 pipeline）+ G2 全绿后发布 | |

S6 拆分（r16，2026-10-24）。每步一个或几个 commit，每步结束时相关测试全绿；中间不发版，所以步与步之间允许 pipeline 暂时只有新旧之一能用，但 main 上的测试不能红：

| 步 | 内容 | 依赖 |
|---|---|---|
| S6-0 | 不兼容旧 run：删除落地计划里给旧行补 Swarm 作者和 trailer 的回退；spec 去掉 drain、归档、迁移 | 无 |
| S6-1 | 验证报告替代 runner（§3.3.3、§3.3.4 的测试部分）：新增 `validation_report` 事件，BUILD、TEST 用 `stage-complete --artifact -` 显式完成；TEST 改了代码退回 REVIEW；删除 `pipeline_test.py`、`runner`、`runner_service`、`final`、`probe`、`test_call`、`pipeline_test_gate`，以及 BX-1、BX-2、TS-2、DS-2b/4b/9b、LG-2、LG-5、QX-1、QX-2、MD-2；TS-1 改为验证报告存在且合法；lifecycle 的 TEST 状态和 runner 熔断、`next_step` 的 runner 命令、engine 的 final 加载一起删；SwarmAI 的选测逻辑留成 `backend/scripts/swarm_select_tests.py` | S6-0 |
| S6-2 | 审查绑定 tree：R1、R2 和新增的增量审查都记 `code_tree`；增量审查审 `diff(上次审过的 tree, 当前 tree)`；LG-1 要求 TEST 报告的 tree 等于落地 tree 且等于某次审查的 tree；finding 关闭证据新增 `reported_test`，删 runner 作者和 old/old/new | S6-1 |
| S6-3 | 删签名（§3.3.5）：事件改为只追加日志加校验和；删 `authority.key`、`allow_hook_writer`、`daemon_exec`、`hook_token`；pipeline 命令回到本地执行；保留 `_with_session`、操作锁、owner 转移；`audit_marker_write_gate` 只留提醒 | S6-1（runner 的 lease 和 token 已随 S6-1 删掉） |
| S6-4 | 通用化：DESIGN_SPEC 测试引用改为不透明路径；RQ-3、RQ-8b 词表改进 Gate-0 检查清单；review 去掉 Python/React 启发式，security 按声明和 `.swarmai/review.md` 派；通用 review 清单，SwarmAI 的 `REVIEW_PATTERNS.md` 搬进它的 `.swarmai/review.md`；专家 prompt 去掉 RP 编号；REFLECT 未知项目回退；五维审计改读验证报告；专家合并（§3.5.1：red-team 并入 security，删 spec-compliance、code-quality，operational 通用化，ux-test 改为 ux，删 `spawn_as`） | S6-2 |
| S6-5 | 收尾：删旧命令形式（`python …/pipeline_v1.py`）和 `utils/source_repo.py`；删 shared 模式和 `SWARM_WORKTREE_MODE`，测试改用隔离 worktree；`run_worktree.code_repos` 改用 `governed_repo_roots`；skill 卡片重写；重写受影响的 E2E 测试 | S6-1 至 S6-4 |
| 验收 | G1 的通用 pipeline 部分：一个非 Python 样例仓库从 create 到 land 走通；daemon 不在线时 pipeline 照常工作 | S6-5 |


S4 删掉 `pipeline_test_gate` 时，runner 仍然存在：S4 只删 gate 中"挡 native 测试命令"的部分，runner 自己的调用约束随 S6 和 runner 一起删除。

发布后的效果：别人装上就能用，能聊天，能用 native 改自己的代码和 commit；选了 pipeline 的话，在任意语言的仓库上都能完整走到落地（Windows 除外）。

## 8. 五维评估（pipeline 部分）

| 维度 | 影响 | 风险 | 缓解 |
|---|---|---|---|
| Run Isolation | worktree、owner、tree 绑定、落地协议、操作锁保留；签名改为只追加日志（§3.3.5） | 日志可以被 agent 改写 | 按 P5 不防；校验和发现损坏，度量看是否真发生 |
| Quality | 测试证据从"runner 签名"变成"agent 的验证报告 + 专家核实"；落地的 tree 必须经过审查，且和 TEST 报告的 tree 一致 | agent 报告不实而专家没发现 | 专家检查清单把"测试是否真实、是否充分"列为必查项，可自己跑测试核实；HIGH finding 和报告里的失败照样挡落地 |
| Autonomy | 非 SwarmAI 仓库从必然卡死变成能自主完成；删掉测试执行器后 agent 少一类机械性的失败和重试 | 无 | — |
| Speed | 不再有 runner 的选测、续跑和重复执行 | 专家自己跑测试会增加审查时间 | 只在可疑时跑 |
| Token Cost | 去掉 runner 的输出和续跑往返 | 专家核实增加少量 token | 由 §6 的度量确认 |

## 9. 待定问题

1. ~~Linux 发布用 AppImage 还是 deb，还是两个都发。~~ XG 定（2026-10-24）：先发 AppImage，deb 以后再说。

## 修订记录

- r1：发现四层问题，提出换可搬移 CPython、六种语言 adapter。
- r2（skeptic c76934d4）：保留 PyInstaller 修打包；子命令；按仓库策略改 gate；按路径计算验证结论；安装验收先行。
- r3（XG）：native 默认、pipeline 可选；Windows 不启用；不签名先发。
- r4（XG）：任何仓库都不强制走 pipeline；默认"相称"；commit 前审查按仓库开关；冻结加固、先量再加。
- r5/r6（XG + skeptic edec2210）：pipeline 与语言无关，不逐个适配。
- r7（XG，定稿）：pipeline 只做流程控制和质量收敛，不观察、不执行测试，用 agent 自己的结果，质量由 Gate-0 和 Gate-2 审查收敛；文档规范、六个 profile、REFLECT 全部保留；只允许产品级 gate（P3）；r6 里的测试 profile、argv 模板、base 试跑、按路径的验证结论全部取消。
- r8（skeptic fc5328aa：5 个 BLOCKER、4 个 MAJOR）：定义签名的验证报告和 BUILD/TEST 显式完成；TEST 不改代码、改了退回 REVIEW，LG-1 绑定审查过的 tree；补全依赖 runner 的检查、状态、next_step、engine、审计清单；`reported_test` 关闭证据；DESIGN_SPEC 测试引用、review 的 security 启发式通用化；REFLECT 未知项目回退；三平台统一的 runtime 部署；`py` 子命令覆盖所有打包版的 Python 子进程；`active_run_commit_gate` 按 owner、`audit_marker_write_gate` 拆分、`external_approval_gate` 按工具元数据；验收拆成第一版（native + 安装）和第二版（pipeline），语言无关用行为测试证明。
- r9（XG）：pipeline 的选择发生在 session 里（风险较大的改动问一次，小改动不问，默认 native），没有 Settings 开关和触发字段；native 模式下由 agent 自主请专家（P6），专家名录 skill，native 和 pipeline 共用一套通用化的专家；删除 `adversarial_commit_gate` 和 `require_review`，SwarmAI 的"commit 前审查"改为 AGENTS.md 里的指引；`pipeline.toml` 只剩 `[landing]`、`[worktree]`。
- r10（XG）：新增 P7"SwarmAI 自己不特殊"：产品代码不识别 SwarmAI 仓库或项目；SwarmAI 的特殊要求只用通用方式表达（`pipeline.toml`、`.swarmai/review.md`、AGENTS.md、它自己的脚本和 CI）；三个 SwarmAI 专用 hook 从产品删除；XG 用和 CI 相同流程打的包；dev 和打包版的命令形式相同；删除 `worktree_mode` 设置；SwarmAI 仓库作为样例仓库参加验收。新增 S4b。
- r11：§3.6 认知文件（SOUL P5/P7、SWARMAI、AGENT R1/R2/Lifecycle、SELF、STEERING #1/#5、pipeline SKILL）和 gate 在 S4 同一个 commit 里改；§3.7 仓库第一次被碰到时注入它的 AGENTS.md / CLAUDE.md / `.swarmai/review.md`（只注入不拦截）；G1 先打 quarantine 再安装，模拟真实下载；新增 golden case；修掉 §5.2 里残留的"commit 前审查"；"删掉签名机制"列为待 XG 拍板的问题。
- r12（XG）：删掉签名机制写进 S6（§3.3.5）：只追加日志 + 校验和，保留归属、tree 绑定、落地协议、隔离和操作锁，删 key、daemon_exec、token；pipeline 命令回到本地执行。认知文件只要求仓库模板改到位，本机副本不由产品改。
- r13：§3.3.5 保留 prewarm session id 补传（只删 token）；§3.7 写准信任级别（按仓库规范遵守，低于用户和产品规则，无关指令不执行），首次写之前就注入；§3.6 系统文件随新安装包生效、STEERING 模板保持空、XG 本机副本在 S4 手工同步（r13 先同步了一次）；G1 去重。
- r14（对抗审查 1 个 BLOCKER、8 个 MAJOR；XG 定"不发过渡版"）：S1–S6 做完才发一次版，每个 commit 自身一致，§7 改为 S1 → S4 → S2 → S3 → S4b → S5 → S6；旧命令形式和 `source_repo` 保留到 S6 与 runner 一起删（修 S3–S6 之间 TEST 跑不起来）；§3.2 新增统一的命令识别模块，替换六处文本匹配（修 `run_owner_gate` 快速路径不认 `pipeline-v1`、`_with_session` 失效）；§3.6 记下 `a84c5008f` 已先改模板，S4 提前，S4 前不重新打包本机 daemon；EVOLUTION 模板的个人纠正条目移出，结构测试覆盖全部 `CONTEXT_FILES`；§3.3.3 审查记录 tree、TEST 退回后做增量审查；§3.7 排除 SwarmWS 和 runtime、本 session 写过的不注入、按是否绑定 DDD 分信任级别；`external_approval_gate` 元数据只升不降；§3.3.5 现存 run 的 drain 和归档、保留对截断尾行的容忍、操作锁沿用现有 flock；SwarmAI 的 `pipeline.toml` 移到 S4；G1 把 git 列为前置条件并加 onboarding 检查；`background_command_guard` / `image_read_dedup_guard` 删还是改提示列为待定。
- S1（2026-10-24）：`scripts/verify_installer.sh` 落地，v2.3.0 红（C3 没有清单、C4 没有后端、C13 app 签名损坏）；S2 必须给整个 .app 做 ad-hoc 签名；发布结论只在干净 VM / CI 上得出。
- r15（XG）：P3 增加"保护 session 本身"一类，`background_command_guard`、`image_read_dedup_guard` 保留，S5 后复查；新增 §3.8：native 按需使用隔离 worktree，不默认、不加 gate；S4（2026-10-24）已落地：`e5db08c5a 480f6da36 185debb73 4e1f3e1ee 002f7b4ec de8603c94 77dbc23eb ddf122ec0`。
- S2（2026-10-24）：S2a `0b917ec16` 安装包带后端（清单 + ad-hoc 签名）；S2b `b6f2bcfb2` 三平台统一的版本化 runtime（指针文件 `current` / `previous`，dev 工具和 app 同一路径）；S2c release.yml 四个目标（macOS arm64 / x64、Windows x64、Linux x64 AppImage）走同一条构建路径，每个安装包在独立 job 里跑 `verify_installer.sh`（已支持三个平台），全部构建和验收通过才发布。
- S3（2026-10-24）：S3a `52e1b7942` `desktop_main.py` 子命令 `daemon` / `pipeline-v1` / `py`（`-m` / `-c` / 脚本），dev shim `backend/bin/python-backend`，`utils/backend_bin.py`，daemon 启动时导出 `SWARM_BACKEND_BIN`；channel MCP、自升级、5 个系统脚本 job 改用自带 Python，不再要源码 checkout；删 `get_python_executable`。S3b：命令识别模块 `core/pipeline_command.py`（两种形式，旧脚本形式保留到 S6），run_owner_gate、runtime_hooks、pipeline_stage_events、daily_activity_hook、security_hooks（session link、后台、amend/commit 提示）、reflect_job 改用它；agent 被告知的命令统一为 `"$SWARM_BACKEND_BIN" pipeline-v1`（`v1_command`、skill 卡片）；前端 continue 文本由后端 `continue_prompt` 给出，turnView 按程序 + 子命令显示。skill 里其他 Python 脚本的调用方式见 S3c。
- S3c（2026-10-24）：skill 文档（`backend/skills/**`、`backend/templates/ddd-skills/**`）调 Python 一律写 `"$SWARM_BACKEND_BIN" py`（源码路径 `backend/scripts/X.py` -> `py -m scripts.X`，`backend/skills/s_*/` -> `{SKILL_DIR}/`，heredoc -> `py -`）；例外只有"仓库自己的代码在它自己的环境里跑"（用户自己的 MCP server、被审仓库的 ruff、SwarmAI checkout venv 里的 pytest / ci_eval_gate）和两处散文，列在 `tests/test_skill_python.py` EXEMPT。`py -` 读 stdin；缺模块时点名并提示告诉用户。SKILL.md frontmatter 新增 `python-deps`（打包进后端）/ `python-optional-deps`（不打包：playwright、pypdf、pdf2image、edge_tts），`scripts/skill_python_deps.py` 是 build 和测试共用的唯一来源；build spec 收集整个标准库 + 声明的依赖（含 data files）；lxml、httpx 写进 pyproject。verify_installer 新增 C14：用部署出来的程序 import 标准库和所有声明依赖，并跑一个真实 skill 脚本。未实测：真实 PyInstaller 打包（标准库全收集后的体积和耗时）。
- r16（XG，2026-10-24）：不兼容旧 run，XG 本机历史 run 全部清掉（§3.3.5）；S6 拆成 S6-0 至 S6-5（§7）。
- r17（XG，2026-10-25）：§3.8 `dangerous_command_gate` 的 history-rewrite 规则换成"丢弃别人未提交的工作"：ref 移动不再询问，销毁未提交改动的操作只在另一个 session 写过、仍未提交的文件会丢时询问，事实复用 `shared_checkout`；旧的按路径判断 scratch 的机制整套删除。
- r18（XG，2026-10-25）：新增 §3.5.1 专家合并，S6-4 实施，12 个减到 9 个；§3.4 记下 `1dfdedba0`：补删 S4 漏下的 `_adv_` marker 链路，以及 `audit_marker_write_gate` 15 次拒绝的核对结果。
