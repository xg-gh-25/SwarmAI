---
title: SwarmAI 让别人装上就能用 — 产品原则、安装包、通用 pipeline（Sev-1）
created: 2026-10-24
updated: 2026-10-24
project: SwarmAI
status: final r11 — XG 2026-10-24 定稿；r1–r11 的演进见末尾"修订记录"
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
- 第一版不签名（XG 定）：下载页写明 macOS 上"系统设置 → 隐私与安全性 → 仍要打开"（macOS 15 起右键打开不再有效），Windows SmartScreen 上"更多信息 → 仍要运行"；验收检查 arm64 可执行文件至少有 ad-hoc 签名。

### 3.2 产品自己需要的 Python 操作，都由后端程序的子命令执行

r8：要做到"不需要系统 Python"，只加两个子命令不够。打包版里还有这些地方会去找系统 Python：`bundle_paths.get_python_executable()` 本身（`bundle_paths.py:27-64`）、channel MCP 脚本（`mcp_config_loader.py:415-420`）、daemon 自升级脚本（`main.py:2455-2468`）、以及 skill 里让 agent 执行的 Python 脚本。

- 冻结后的 `python-backend` 在导入 FastAPI 之前分发子命令：
  - `daemon`（默认，现行为）；
  - `pipeline-v1 <args>`；
  - `py <script.py> [args]`：用包里自带的解释器和已打包的模块执行一个 Python 脚本（`runpy.run_path`）。MCP 脚本、自升级脚本、skill 脚本都改用它。只能用包里有的模块；需要别的第三方包的 skill 在 frontmatter 里声明依赖，缺依赖时 agent 告诉用户。
- `get_python_executable()` 在打包版里返回 `[python-backend, "py"]`，不再找系统 Python。
- S3 实施前先列一份清单：打包版里每一个会起 Python 子进程的地方，逐个改成子命令；G1 在没有系统 Python 的环境里覆盖 MCP、自升级和有代表性的 skill 脚本。
- 每个 agent 会话注入 `SWARM_BACKEND_BIN` = daemon 自己的 `sys.executable`（指向 `runtime/<version>/` 里确定版本的那份，不指向会变的 `current`；升级后 daemon 重启，新会话拿到新值）。打包版和 dev 版的 pipeline 命令形式相同（P7）：`"$SWARM_BACKEND_BIN" pipeline-v1 …`；dev 版里 `SWARM_BACKEND_BIN` 指向 `backend/bin/python-backend` shim，用 venv 运行同一个入口。`source_repo.json` 和 `utils/source_repo.py` 删除。
- gate 识别 pipeline 命令只有一种形式："确切可执行文件路径 + 子命令"：`argv[0]` 解析后必须等于 `SWARM_BACKEND_BIN` 的真实路径，`argv[1]` 必须是 `pipeline-v1`。原来"python + 确切脚本路径"的形式删除。
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
| `backend/skills/s_autonomous-pipeline/SKILL.md` 的 description / TRIGGER | 把 pipeline 写成写代码的通用入口 | 只在用户选择 pipeline 时触发；写明它是可选的高级方式 |

送达方式：
- SWARMAI、SOUL、IDENTITY、AGENT 是系统文件，每次 session 启动会用模板覆盖，改模板就生效。
- SELF、STEERING 是 copy-only-if-missing，改模板只影响新安装。现有安装只有 XG 一台机器（CI 安装包一直装不起来），所以不写自动迁移：S4 时把 XG 本机 `~/.swarm-ai/SwarmWS/.context/` 下 SELF.md 的 gate 清单、STEERING.md Standing Rules 的 #1、#5 改掉，diff 先给 XG 看，确认后再写。

### 3.7 仓库指引要有确定的送达路径（r11）

r9 把 SwarmAI 的"commit 前审查"交给它自己的 AGENTS.md，但现在没有机制保证 agent 读得到：session 的 cwd 是 SwarmWS，cd 进别的仓库不会自动加载那里的 AGENTS.md（Claude CLI 只在启动时按 cwd 加载 CLAUDE.md；S4 前实测确认）。

补法：产品级、只注入、不拦截。
- 一个 session 第一次碰某个 git 仓库时（Bash 的 cwd 或 `git -C`、Read/Write/Edit 的路径落在这个仓库里），用 PostToolUse 的 additionalContext 把这个仓库根目录下的 `AGENTS.md`、`CLAUDE.md` 和 `.swarmai/review.md` 注入上下文。
- 每个 session 每个仓库只注入一次；文件改了（mtime 或哈希变化）再注入一次。
- 有大小上限，超过时注入开头部分并写明文件路径，让 agent 自己去读全文。
- 只注入，不拒绝任何操作。对所有用户、所有仓库都一样，不违反 P3 和 P7。
- 注入的内容是仓库作者写的，当作参考资料，不当作系统指令。
- 验收：在一个带 AGENTS.md 的样例仓库里，agent 第一次改文件之后，上下文里能看到这份指引；同一个 session 里第二次不再重复注入。

### 3.4 gate：只保留产品级的

| 类别 | gate | 处理 |
|---|---|---|
| 所有用户的安全和会话机制 | `dangerous_command_gate`、`external_approval_gate`、`ask_question_gate`、`bash_syntax_guard`、`skill_access_checker`、工具日志和审计、compaction 前后的连续性、失败诊断、文件跟踪、用户纠正捕获、observation 记录 | 保留 |
| SwarmAI 产品的记忆和 DDD 系统（只提示、不拦截） | `governance_file_gate`、`memory_edit_guard`、DailyActivity / DDD 相关 hook | 保留 |
| 用户开启的 pipeline run 的完整性（只在 run 里生效） | `run_owner_gate`、`run_amend_gate`、`session_link_gate`（收窄为只管 pipeline 命令）、`expert_type_gate`、`expert_budget_gate`、worktree rule 1–3、`main_dirty_reporter`、`v1_reflect_trigger` | 保留 |
| `active_run_commit_gate` | r8：今天它扫描所有 session 的 run，别的 run 读不出来时也会拒绝（`security_hooks.py:3628-3732`） | 改为按 owner 判断：只在提交者就是该 run 的签名 owner session、且提交的是 run 改过的文件时拒绝；其他 session 只提示；读不出来的 run 不再导致拒绝 |
| `audit_marker_write_gate` | r8：今天一直生效，保护整个共享审计目录，包括 commit 前审查用的通用 marker（`security_hooks.py:6586-6644`） | 只保留签名事件和 `authority.key` 的保护，始终生效（只在 pipeline 内部目录里，普通用户碰不到）；commit 前审查 marker 的保护随 `adversarial_commit_gate` 一起删除（r9） |
| `external_approval_gate` | r8：靠工具名里的词（create / add / set / save）推断风险，会把远端的写操作误判成本地写（`tool_risk.py:65-75,137-154`） | 保留这个产品级 gate，但改为按每个工具的元数据判断（工具声明自己是否有对外副作用），不再靠名字猜 |
| 挡 native、或把人赶进 pipeline | worktree rule 4、`direct_commit_gate`、`adversarial_commit_gate`（r9：连同 SubagentStop 写审查 marker 的逻辑一起删）、`pytest_command_guard`、`background_command_guard`、`image_read_dedup_guard`、`enforcement_injector` 里"必须对抗审查"的提示、`pipeline_test_gate` | 删除 |
| 只对 SwarmAI 仓库有意义 | `design_lint_guard`、`inclusive_term_guard`、`change_triggered_eval`、`session_checkpoint` 里写死的 pytest 提醒 | 从产品里删除（P7）：改成 SwarmAI 仓库自己的 lint 脚本、pre-commit 和 CI 检查 |
| 运维开关 | `SWARM_GATE_FORCE` | 不作为产品开关，只留给运维调试；顺手修掉三个声称支持却从不检查它的 gate |

### 3.5 专家：native 和 pipeline 共用（r9）

- 专家在 `backend/experts/code/` 下（correctness、security、red-team、concurrency、state-machine、api-contract、integration、performance、operational、code-quality、spec-compliance、ux-test），由 `expert_manager.py` 加载；每个文件的 frontmatter 写了显示名和 `scope`。
- 新增 skill "专家审查"：把专家名录（每位管什么、什么改动该请）从 frontmatter 自动生成，注入给 agent。新增专家不改代码。名录给出的默认规则：

  | 改动 | 建议请 |
  |---|---|
  | 一般逻辑改动、有一定规模 | correctness |
  | 认证、权限、输入校验、凭证、网络、文件路径 | security，风险高时加 red-team |
  | 并发、锁、异步、重试 | concurrency |
  | 状态机、生命周期 | state-machine |
  | 对外接口、schema、数据格式 | api-contract |
  | 跨模块、改了调用方 | integration |
  | 热路径、大数据量 | performance |
  | 部署、配置、日志、故障恢复 | operational |
  | UI 交互 | ux-test |
  | 小改动、文档 | 通常不用请 |

- native 模式：agent 用 sub-agent 加载对应专家的 prompt 审查，自己处理 finding。不写签名事件，不经过任何 gate。
- pipeline 模式：照旧由 `review-start` 派发、finding 账本收敛。
- 专家 prompt 改为通用：去掉对 SwarmAI `REVIEW_PATTERNS.md`（RP 编号）的引用，换成和语言、仓库无关的检查要点；仓库可以在 `.swarmai/review.md` 里补充自己的要点，专家审查时一并读取。

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

- **G1 真实安装验收**，分两版（r8：第一版还没有通用 pipeline，Windows 也不启用 pipeline）：
  - **第一版（native 和安装）**：每个发布目标（macOS arm64/x64、Windows x64、Linux x64），在一次性的干净 VM/容器里（没有源码 checkout，没有系统 Python，PATH 里没有开发工具），先模拟真实下载：给安装包打上 `com.apple.quarantine`（macOS；Windows 对应 Mark-of-the-Web 的 `Zone.Identifier`），按陌生用户的方式安装和首次打开（未签名 app 走"仍要打开"），再验证：拷进 `runtime/` 的后端能执行、launchd / 子进程拉起的 daemon 能起来、自带的 Claude CLI 能执行，以及app 启动、外置 runtime 部署、`/health`、自带 Claude CLI 可执行、seed 和迁移、onboarding 可达、MCP 工具可用、`py` 子命令能跑一个有代表性的 skill 脚本、更新到下一个版本再回滚；再在一个样例仓库上跑通一次 native 修改 + commit。
  - **第二版（通用 pipeline）**：macOS 和 Linux 上，在样例仓库矩阵上跑 pipeline（见 §5.2）。Windows 只验证 `pipeline-v1` 返回"不支持"。
- **G2 包内容清单**：构建时生成一份清单（必需文件、哈希、架构、可执行文件、原生库），G1 安装后逐项核对，缺一项就失败。
- G1、G2 是 release workflow 发布步骤的前置 job，不过不发布。本地也能用同一个脚本 `scripts/verify_installer.sh` 跑。

### 5.2 产品原则

- 语言无关用**行为测试**证明，不用"核心源码里搜不到某些词"这种字面检查（r8）：样例仓库矩阵（Python、Node、Go、Rust、无测试、monorepo、unborn）上各跑一个 direct 和一个 full 的 pipeline run，都能走到落地，REPORT 里写着 agent 报告的测试和审查结论。核心里如果确有必须出现语言名的地方（例如 SwarmAI 仓库自己的工具），列入一份经过审查的例外清单。
- 结构测试：在任意仓库里（有没有 `pipeline.toml` 都一样），没有开 pipeline run 时，native 写文件、跑任意测试命令、后台起进程、`git commit`，都不被任何 gate 拒绝。
- 结构测试：hook_builder 里注册的每个会拒绝的 gate，都在 §3.4 的表里有归类；新增 gate 不进表就失败。
- 结构测试：TEST 验证报告的 `code_tree` 不等于落地 tree、或这个 tree 没经过 Gate-2 时，落地被拒绝（§3.3.3）。
- SwarmAI 仓库：声明 `pipeline.toml` 后，它的 pipeline run 照常落地，Swarm trailer 和 worktree 软链按声明生效；其余行为和其他样例仓库一致（P7）。
- **golden case（r11，行为测试，用真实 agent 跑）**：在一个陌生仓库里提一个小改动，agent 直接 native 修改并 commit，全程不提 pipeline、不问要不要走 pipeline、不被任何 gate 拦。再跑一个风险较大的改动，agent 问一次"直接改还是走 pipeline"。两条都要在装好的安装包上跑，不只在源码环境里跑。
- 结构测试：系统级认知文件模板（§3.6 列出的）里不再有"代码必须走 pipeline""每次 commit 前必须审查"这类表述。

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
| S4 | gate 按 §3.4 删改：删挡 native 的 gate（含 `adversarial_commit_gate`）；`active_run_commit_gate` 按 owner；`audit_marker_write_gate` 收窄；`external_approval_gate` 按工具元数据；`pipeline.toml` 的 `[landing]`、`[worktree]` 读取；P4 的 session 内询问写进 agent 指引；专家名录 skill（§3.5）；SwarmAI 的 AGENTS.md 写入"commit 前审查"指引；§3.6 认知文件和 gate 同一个 commit 一起改；§3.7 仓库指引注入 | 第一版 |
| S4b | P7：清理产品代码里识别 SwarmAI 仓库 / 项目的逻辑（先出完整清单，逐项改成通用或删除）；删除 `worktree_mode` 设置；dev 模式的 `SWARM_BACKEND_BIN` shim；SwarmAI 仓库自己的 lint 脚本、pre-commit、CI 接住被删掉的三个 hook；XG 切换到和 CI 相同流程打出来的安装包 | 第一版 |
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

1. Linux 发布用 AppImage 还是 deb，还是两个都发。
2. **是否在 S6 删掉签名这套机制（待 XG 拍板）**。P5 已定不防 agent 作恶，S6 后测试证据又是 agent 自己写的报告，签名剩下的作用只是证明"这份记录是本 run 写的"，这本身就是防作恶。删掉的做法：签名事件改为普通的只追加日志（每条带 tree hash、owner session、和防截断用的校验和）；run 归属、tree 绑定、落地协议、worktree 隔离、run 的操作锁保留（它们管正确性）；`authority.key`、`daemon_exec`、一次性 hook token、B-class socket 删掉，pipeline 命令回到本地执行。好处：pipeline 不再依赖 daemon 在线，Windows 少一个结构障碍（Unix socket），P7 的 gate 命令识别也随之简化。代价：推翻今天刚合入的 B-class（`2810e727b 740d6268a dfa761ac7 01fa52bee`）。

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
