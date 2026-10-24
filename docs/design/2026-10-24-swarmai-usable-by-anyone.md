---
title: SwarmAI 让别人装上就能用 — 产品原则、安装包、通用 pipeline（Sev-1）
created: 2026-10-24
updated: 2026-10-24
project: SwarmAI
status: final r8 — XG 2026-10-24 定稿；skeptic fc5328aa 的发现已并入；r1–r8 的演进见末尾"修订记录"
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
- gate 只用来保护：危险或不可逆的操作、对外的副作用、需要人回答的问题、用户主动开启的 pipeline run 本身的完整性。
- gate 不能用来挡 native 写代码、跑测试或 commit，也不能用来把人赶进 pipeline。
- 想对某个仓库更严格（比如 commit 前必须审查），只能通过所有用户都能用的开关来做：Settings 或仓库的 `.swarmai/pipeline.toml`。
- 新增 gate 前必须回答两个问题：所有用户都用得上吗？有价值吗？并且先看度量（§6）。

**P4 产品默认是"相称"。**
小改动、文档、探索性修改直接用 native。风险较大的改动（跨模块、改公共接口或数据结构、迁移、安全相关、多会话可能同时改同一处、用户要求无人值守做完），由 agent 主动建议走 pipeline 并说明理由，用户同意才走。

**P5 不再做"防 agent 作恶"类加固，先量再加。**
B-d（人工接口鉴权）、sandbox 子进程、首次批准、agent 进程隔离都不做；E7 遗留项 7 保持"已知、不修"。新增任何 gate 或加固之前，先拿到两组数字：每个 run 的成本，和每个 gate 实际拦下了多少真问题。

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
- 第一版不签名（XG 定）：下载页写明 macOS 上"系统设置 → 隐私与安全性 → 仍要打开"（macOS 15 起右键打开不再有效），Windows SmartScreen 上"更多信息 → 仍要运行"；验收检查 arm64 可执行文件至少有 ad-hoc 签名。

### 3.2 产品自己需要的 Python 操作，都由后端程序的子命令执行

r8：要做到"不需要系统 Python"，只加两个子命令不够。打包版里还有这些地方会去找系统 Python：`bundle_paths.get_python_executable()` 本身（`bundle_paths.py:27-64`）、channel MCP 脚本（`mcp_config_loader.py:415-420`）、daemon 自升级脚本（`main.py:2455-2468`）、以及 skill 里让 agent 执行的 Python 脚本。

- 冻结后的 `python-backend` 在导入 FastAPI 之前分发子命令：
  - `daemon`（默认，现行为）；
  - `pipeline-v1 <args>`；
  - `py <script.py> [args]`：用包里自带的解释器和已打包的模块执行一个 Python 脚本（`runpy.run_path`）。MCP 脚本、自升级脚本、skill 脚本都改用它。只能用包里有的模块；需要别的第三方包的 skill 在 frontmatter 里声明依赖，缺依赖时 agent 告诉用户。
- `get_python_executable()` 在打包版里返回 `[python-backend, "py"]`，不再找系统 Python。
- S3 实施前先列一份清单：打包版里每一个会起 Python 子进程的地方，逐个改成子命令；G1 在没有系统 Python 的环境里覆盖 MCP、自升级和有代表性的 skill 脚本。
- 每个 agent 会话注入 `SWARM_BACKEND_BIN` = daemon 自己的 `sys.executable`（指向 `runtime/<version>/` 里确定版本的那份，不指向会变的 `current`；升级后 daemon 重启，新会话拿到新值）。打包版的 pipeline 命令是 `"$SWARM_BACKEND_BIN" pipeline-v1 …`；dev 版保持 `backend/.venv/bin/python backend/scripts/pipeline_v1.py …`。`source_repo.json` 和 `utils/source_repo.py` 删除。
- gate 识别 pipeline 命令：在现有"python + 确切脚本路径"之外，加"确切可执行文件路径 + 子命令"：`argv[0]` 解析后必须等于 daemon 自己的 `sys.executable`，`argv[1]` 必须是 `pipeline-v1`。
- 不再有 `pipeline-test` 子命令：pipeline 不执行测试（§3.3）。
- Windows 不启用 pipeline：`pipeline-v1` 在 Windows 上返回"Windows 上不支持 pipeline，请直接用 native 方式完成"，agent 照此执行。`py` 子命令在 Windows 上照常可用。

### 3.3 通用 pipeline：流程控制和质量收敛

#### 3.3.1 触发

| 方式 | 行为 |
|---|---|
| 用户在对话里要求（"用 pipeline 做"、"无人值守做完"） | 走 pipeline |
| 仓库 `.swarmai/pipeline.toml` 的 `[pipeline] trigger` | `manual`（默认）：只有用户要求才走；`suggest`：风险较大的改动 agent 主动建议；`auto`：该仓库的改动都走 |
| Settings 里按项目 / 仓库的同名开关 | 同上；与仓库声明不一致时以 Settings 为准 |
| 都没有 | `manual`，默认 native |

#### 3.3.2 保留的流程（全部保留，只改不通用的地方）

- **六个 profile 全部保留**：`direct`、`trivial`、`full`、`bugfix`、`goal`、`research`，阶段顺序不变（`lifecycle.py:77-83`）。
- **阶段全部保留**：evaluate、think、plan、build / goal_cycle、review、test、deliver、reflect。
- **文档规范保留**：REQUIREMENTS（结构化 EARS、R 编号）、DESIGN_SPEC（P/F/T/X 编号、properties、failure_modes、trace 矩阵）、fixing-spec、REFLECT 的 lessons。这是质量收敛的骨架，本身和编程语言无关。
- **两道审查保留**：Gate-0（EVALUATE 的问题定义审查）、Gate-2（REVIEW 的专家审查：R1 全量 + R2 只复查已处理的 finding，finding 账本、聚类、fixing-spec、修复、复审、轮数上限）。原来的 Gate-1 已在 10 月的简化里删除，本设计不恢复。
- **REFLECT 保留**：daemon 后台判断并沉淀 lessons 到项目 DDD（产品能力，每个用户的项目都有 DDD）。r8：project 在 SwarmWS 里不存在或没有 DDD 文档时，lessons 写进 run 自己的 REFLECT 记录并在 REPORT 里列出，不再静默丢弃（今天 `reflect_job.py:56-70,168-209` 会拒收未知项目）。
- **落地协议和签名保留**：签名事件、producer 权限、不可变 contract 和 tree 绑定、owner 转移、一次性授权、`auth_consumed`、`land_intent`、REPORT 渲染、交付 tree、base 复核、部分落地记录、隔离 worktree。
- **上限和熔断保留**：阶段尝试上限、决策上限、复审轮数上限。

#### 3.3.3 测试：用 agent 自己的结果，不观察、不重跑

- BUILD 和 TEST 阶段，agent 用 native 方式写代码、写测试、跑测试，和不走 pipeline 时一样。
- **验证报告（`validation_report`）**：BUILD 和 TEST 阶段都以 `stage-complete --artifact -` 显式完成（不再有 runner 自动完成），artifact 是一份验证报告，由 CLI 写成签名事件。每个仓库一条，字段：
  - `repo`、`code_tree`（daemon 在写事件时自己算当前 worktree 的 tree，不取 agent 给的值）；
  - `commands`：跑了哪些命令、在哪个目录、结果（通过 / 失败 / 未运行，以及原因）；
  - `tests_changed`：新增或修改了哪些测试文件；
  - `rationale`：为什么认为这些测试能证明改动正确。
  签名只证明"这份报告是本 run 在这个 tree 上提交的"，不证明报告内容为真；内容是否可信由审查判断。
- **审查和最终 tree 的关系（r8，skeptic BLOCKER 1）**：阶段顺序 build → review → test 不变。规则是：
  - Gate-2 审查的对象是 REVIEW 时的代码 tree，以及 BUILD 的验证报告。测试是否充分、报告是否可信，由 Gate-2 专家判断；专家 sub-agent 有 Bash，可疑时自己用 native 方式跑测试核实；不足、不实就记成 finding，走 fixing-spec → 修复 → R2 复查。"至少有一个测试在改动前会失败"是专家检查清单里的一项。
  - TEST 阶段是在审查后的最终 tree 上做最后一次验证，并提交 TEST 的验证报告。**TEST 阶段不改代码**：如果最终验证发现问题、需要改代码，run 退回 REVIEW（R2 复查这次改动），再回到 TEST。
  - 落地时 LG-1 要求 TEST 验证报告的 `code_tree` 等于落地的 tree，并且这个 tree 经过了 Gate-2（R1 或 R2）。这样每一次进入落地的代码都被审查过，最终验证报告也对应同一份代码。
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

#### 3.3.5 `.swarmai/pipeline.toml`（全部字段可选）

```toml
[pipeline]
trigger = "manual"            # manual | suggest | auto

[commit]
require_review = false        # true: 这个仓库的 commit 前必须有审查 sub-agent 审过（§3.4）

[landing]
author = "repo"               # repo（默认）| swarm
trailer = ""                  # 例如 SwarmAI 仓库写 "Co-Authored-By: Swarm <swarm@swarmai.dev>"

[worktree]
link = []                     # 例如 ["desktop/node_modules"]
```

没有测试命令字段：pipeline 不执行测试。仓库想告诉 agent 怎么测，写在 README、AGENTS.md 或 `.swarmai/review.md` 里，agent 自己读。

### 3.4 gate：只保留产品级的

| 类别 | gate | 处理 |
|---|---|---|
| 所有用户的安全和会话机制 | `dangerous_command_gate`、`external_approval_gate`、`ask_question_gate`、`bash_syntax_guard`、`skill_access_checker`、工具日志和审计、compaction 前后的连续性、失败诊断、文件跟踪、用户纠正捕获、observation 记录 | 保留 |
| SwarmAI 产品的记忆和 DDD 系统（只提示、不拦截） | `governance_file_gate`、`memory_edit_guard`、DailyActivity / DDD 相关 hook | 保留 |
| 用户开启的 pipeline run 的完整性（只在 run 里生效） | `run_owner_gate`、`run_amend_gate`、`session_link_gate`（收窄为只管 pipeline 命令）、`expert_type_gate`、`expert_budget_gate`、worktree rule 1–3、`main_dirty_reporter`、`v1_reflect_trigger` | 保留 |
| `active_run_commit_gate` | r8：今天它扫描所有 session 的 run，别的 run 读不出来时也会拒绝（`security_hooks.py:3628-3732`） | 改为按 owner 判断：只在提交者就是该 run 的签名 owner session、且提交的是 run 改过的文件时拒绝；其他 session 只提示；读不出来的 run 不再导致拒绝 |
| `audit_marker_write_gate` | r8：今天一直生效，保护整个共享审计目录，包括 commit 前审查用的通用 marker（`security_hooks.py:6586-6644`） | 拆成两部分：签名事件和 `authority.key` 的保护保留，始终生效（它们只在 pipeline 内部目录里，普通用户碰不到）；审查 marker 的保护只在仓库开启了 `require_review` 时生效 |
| `external_approval_gate` | r8：靠工具名里的词（create / add / set / save）推断风险，会把远端的写操作误判成本地写（`tool_risk.py:65-75,137-154`） | 保留这个产品级 gate，但改为按每个工具的元数据判断（工具声明自己是否有对外副作用），不再靠名字猜 |
| 产品功能：按仓库开启的 commit 前审查 | `adversarial_commit_gate` | 默认关闭、只提示；仓库在 `pipeline.toml` 的 `[commit] require_review = true` 或 Settings 里开启后才拦截。SwarmAI 仓库用同一个声明开启。拦截提示写给用户看（为什么拦、怎么满足、在哪里关） |
| 挡 native、或把人赶进 pipeline | worktree rule 4、`direct_commit_gate`、`pytest_command_guard`、`background_command_guard`、`image_read_dedup_guard`、`enforcement_injector` 里"必须对抗审查"的提示、`pipeline_test_gate` | 删除 |
| 只对 SwarmAI 仓库有意义 | `design_lint_guard`、`inclusive_term_guard`、`change_triggered_eval`、`session_checkpoint` 里写死的 pytest 提醒 | 移出产品：改由 SwarmAI 仓库自己的配置或脚本提供 |
| 运维开关 | `SWARM_GATE_FORCE` | 不作为产品开关，只留给运维调试；顺手修掉三个声称支持却从不检查它的 gate |

## 4. 用户场景

| 场景 | 体验 |
|---|---|
| 新用户第一次装（macOS / Linux / Windows） | 下载安装包，按下载页说明放行未签名 app；首次启动部署外置 runtime 并启动后端，onboarding 配置模型凭证，开始聊天 |
| 在自己的仓库里修一个 bug（最常见） | 全程 native：读代码、改、跑测试、`git commit`。没有任何 gate 挡 |
| 仓库绑定成 DDD 项目 | 和上面一样走 native；DDD 的知识、记忆、REFLECT 照常可用 |
| 风险较大的改动 | agent 说明理由并建议走 pipeline；用户同意后走完整流程：文档、Gate-0、实现和测试（native）、Gate-2 专家审查和收敛、隔离 worktree 落地、REFLECT |
| 用户明确要求无人值守做完 | 走 pipeline，agent 自主推进到落地或到需要人决定的地方 |
| 想对某个仓库更严格 | 在 Settings 或 `pipeline.toml` 里打开 `require_review`、把 `trigger` 设成 `suggest` / `auto` |
| Windows 用户 | 只用 native；pipeline 命令明确返回不支持 |
| 给 SwarmAI 本身贡献代码 | SwarmAI 仓库和别的仓库一样：默认 native；它的 `pipeline.toml` 声明 `require_review = true`、Swarm 落地身份和 worktree 软链；精细选测用它自己的 `swarm_select_tests.py` |

## 5. 验收

### 5.1 让"安装包缺东西"这类错误再也发生不了

- **G1 真实安装验收**，分两版（r8：第一版还没有通用 pipeline，Windows 也不启用 pipeline）：
  - **第一版（native 和安装）**：每个发布目标（macOS arm64/x64、Windows x64、Linux x64），在一次性的干净 VM/容器里（没有源码 checkout，没有系统 Python，PATH 里没有开发工具），安装刚打出的安装包，验证：app 启动、外置 runtime 部署、`/health`、自带 Claude CLI 可执行、seed 和迁移、onboarding 可达、MCP 工具可用、`py` 子命令能跑一个有代表性的 skill 脚本、更新到下一个版本再回滚；再在一个样例仓库上跑通一次 native 修改 + commit。
  - **第二版（通用 pipeline）**：macOS 和 Linux 上，在样例仓库矩阵上跑 pipeline（见 §5.2）。Windows 只验证 `pipeline-v1` 返回"不支持"。
- **G2 包内容清单**：构建时生成一份清单（必需文件、哈希、架构、可执行文件、原生库），G1 安装后逐项核对，缺一项就失败。
- G1、G2 是 release workflow 发布步骤的前置 job，不过不发布。本地也能用同一个脚本 `scripts/verify_installer.sh` 跑。

### 5.2 产品原则

- 语言无关用**行为测试**证明，不用"核心源码里搜不到某些词"这种字面检查（r8）：样例仓库矩阵（Python、Node、Go、Rust、无测试、monorepo、unborn）上各跑一个 direct 和一个 full 的 pipeline run，都能走到落地，REPORT 里写着 agent 报告的测试和审查结论。核心里如果确有必须出现语言名的地方（例如 SwarmAI 仓库自己的工具），列入一份经过审查的例外清单。
- 结构测试：默认配置下（没有 `pipeline.toml`、没有 Settings 开关），在任意仓库里 native 写文件、跑任意测试命令、后台起进程、`git commit`，都不被任何 gate 拒绝。
- 结构测试：hook_builder 里注册的每个会拒绝的 gate，都在 §3.4 的表里有归类；新增 gate 不进表就失败。
- 结构测试：TEST 验证报告的 `code_tree` 不等于落地 tree、或这个 tree 没经过 Gate-2 时，落地被拒绝（§3.3.3）。
- SwarmAI 仓库：声明 `pipeline.toml` 后，它的 pipeline run 照常落地，Swarm trailer、commit 前审查、worktree 软链都按声明生效。

## 6. 度量（P5：先量再加）

- **每个 run 的成本**：调用次数、token、墙钟时间、人工介入次数，按 profile 分，从签名事件和 `run_audit.json` 读。
- **每个 gate 的实际效果**：一段时间内每个 gate 拦了多少次（`gate_decisions.jsonl`、`scripts/gate_audit.py`），抽样判断多少是真问题、多少是误拦。
- 没有这两组数字之前，不新增 gate，不新增加固。

## 7. 实施顺序（每步一个 commit，测试先行，变异验证）

| 步 | 内容 | 发布 |
|---|---|---|
| S1 | 第一版 G1/G2 安装验收脚本，先在 v2.3.0 上跑红 | 第一版 |
| S2 | 打包修复 + 三平台统一的版本化 runtime 部署和启动 + 四个发布目标（§3.1） | 第一版 |
| S3 | `python-backend` 子命令（`pipeline-v1`、`py`）+ 打包版里所有起 Python 子进程的地方改用子命令 + `SWARM_BACKEND_BIN` + gate 确切路径识别 + Windows 返回不支持（§3.2）。S2、S3 完成后第一版 G1/G2 转绿，才允许发布 | 第一版 |
| S4 | gate 按 §3.4 删改：删挡 native 的 gate；`adversarial_commit_gate` 改为按仓库开关；`active_run_commit_gate` 按 owner；`audit_marker_write_gate` 拆分；`external_approval_gate` 按工具元数据；`pipeline.toml` 的 `[commit]`、`[landing]`、`[worktree]`、`[pipeline]` 读取；Settings 开关 | 第一版 |
| S5 | 度量（§6） | 第一版 |
| S6 | pipeline 通用化（§3.3.3、§3.3.4）：验证报告事件和显式完成；删测试执行器、runner 证据相关的机器判定和状态；TEST 不改代码、改了退回 REVIEW；LG-1 绑定审查过的 tree；`reported_test` 证据；DESIGN_SPEC 测试引用通用化；Gate-0、Gate-2 检查清单补上对应要求；review 去掉语言启发式；REFLECT 的未知项目回退；通用 review 清单；skill 卡片重写；SwarmAI 仓库的 `pipeline.toml`、`.swarmai/review.md`、`swarm_select_tests.py`；重写受影响的 E2E 测试；第二版 G1 | 第二版 |

S4 删掉 `pipeline_test_gate` 时，S6 之前的 pipeline run 里 runner 仍然存在：S4 只删 gate 中"挡 native 测试命令"的部分，runner 自己的调用约束随 S6 和 runner 一起删除。

第一版（S1–S5）：别人装上就能用，能聊天，能用 native 改自己的代码和 commit，SwarmAI 仓库按自己的声明受保护。第二版（S6）：pipeline 在任意语言的仓库上都能完整走到落地。

## 8. 五维评估（pipeline 部分）

| 维度 | 影响 | 风险 | 缓解 |
|---|---|---|---|
| Run Isolation | 不变：worktree、签名、owner、落地协议都保留 | 无 | — |
| Quality | 测试证据从"runner 签名"变成"agent 的签名验证报告 + 专家核实"；落地的 tree 必须经过审查，且和 TEST 报告的 tree 一致 | agent 报告不实而专家没发现 | 专家检查清单把"测试是否真实、是否充分"列为必查项，可自己跑测试核实；HIGH finding 和报告里的失败照样挡落地 |
| Autonomy | 非 SwarmAI 仓库从必然卡死变成能自主完成；删掉测试执行器后 agent 少一类机械性的失败和重试 | 无 | — |
| Speed | 不再有 runner 的选测、续跑和重复执行 | 专家自己跑测试会增加审查时间 | 只在可疑时跑 |
| Token Cost | 去掉 runner 的输出和续跑往返 | 专家核实增加少量 token | 由 §6 的度量确认 |

## 9. 待定问题

1. Settings 里"按仓库开关"放在哪个页面、长什么样（S4 实施前出 mockup 给 XG 看）。
2. Linux 发布用 AppImage 还是 deb，还是两个都发。

## 修订记录

- r1：发现四层问题，提出换可搬移 CPython、六种语言 adapter。
- r2（skeptic c76934d4）：保留 PyInstaller 修打包；子命令；按仓库策略改 gate；按路径计算验证结论；安装验收先行。
- r3（XG）：native 默认、pipeline 可选；Windows 不启用；不签名先发。
- r4（XG）：任何仓库都不强制走 pipeline；默认"相称"；commit 前审查按仓库开关；冻结加固、先量再加。
- r5/r6（XG + skeptic edec2210）：pipeline 与语言无关，不逐个适配。
- r7（XG，定稿）：pipeline 只做流程控制和质量收敛，不观察、不执行测试，用 agent 自己的结果，质量由 Gate-0 和 Gate-2 审查收敛；文档规范、六个 profile、REFLECT 全部保留；只允许产品级 gate（P3）；r6 里的测试 profile、argv 模板、base 试跑、按路径的验证结论全部取消。
- r8（skeptic fc5328aa：5 个 BLOCKER、4 个 MAJOR）：定义签名的验证报告和 BUILD/TEST 显式完成；TEST 不改代码、改了退回 REVIEW，LG-1 绑定审查过的 tree；补全依赖 runner 的检查、状态、next_step、engine、审计清单；`reported_test` 关闭证据；DESIGN_SPEC 测试引用、review 的 security 启发式通用化；REFLECT 未知项目回退；三平台统一的 runtime 部署；`py` 子命令覆盖所有打包版的 Python 子进程；`active_run_commit_gate` 按 owner、`audit_marker_write_gate` 拆分、`external_approval_gate` 按工具元数据；验收拆成第一版（native + 安装）和第二版（pipeline），语言无关用行为测试证明。
