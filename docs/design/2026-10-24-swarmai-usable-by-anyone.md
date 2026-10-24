---
title: SwarmAI 让别人装上就能用 — 安装包、pipeline 运行时、任意语言仓库（Sev-1）
created: 2026-10-24
updated: 2026-10-24
project: SwarmAI
status: r5 — pipeline 与语言无关（不逐个适配）；profile 来源：仓库声明 → agent 提议并在 base 上试跑 → 未验证。r4：XG 2026-10-24 定：pipeline 在任何仓库都不强制，方向是 autonomy；产品默认"相称"；只有显式开启的仓库才强制 commit 前审查，开关在 Settings；冻结"防 agent 作恶"类加固，先量成本和 gate 实效
---

# SwarmAI 让别人装上就能用（Sev-1）

## 0. 原则（XG 2026-10-24，r4，取代 r3 的 §0）

### 0.1 pipeline 的立场：它是 autonomy 的工具，不是必经之路

- **pipeline 的方向是 autonomy**：让一个较大的改动在没人盯着的时候也能做完、做对、证据可查（隔离 worktree、独立 review、测试证据、如实的报告）。它的价值在于"无人值守地交付"，不在于拦住不走它的人。
- **任何仓库、任何情况都不强制走 pipeline**，SwarmAI 自己的源码仓库也一样。LLM 和 native CLI 越来越强，大多数 coding task 直接在 native 里做更快。
- **不走 pipeline 照样能写代码、能 commit**；pipeline 不支持的情况（Windows、识别不出测试的仓库、缺工具）任何时候都不挡。

### 0.2 产品默认是"相称"（proportionate）

agent 按改动的风险选择做法，不一刀切：

| 改动 | 默认做法 |
|---|---|
| 小改动、单文件、文档、探索性修改 | native：直接改、跑、commit |
| 风险较大：跨模块、改公共接口 / 数据结构 / 迁移、安全相关、多会话可能同时改同一处、用户要求无人值守做完 | agent **主动建议**走 pipeline 并说明理由；用户同意才走 |
| 用户明确要求走 pipeline | 走 pipeline |

"建议走 pipeline"来自 agent 的判断和降级为提示的 gate 文本，不来自拒绝。

### 0.3 只有显式开启的仓库才强制 commit 前审查

- `adversarial_commit_gate`（commit 前必须有审查 sub-agent 审过这些文件）**只对显式开启的仓库生效**：
  - SwarmAI 源码仓库：默认开启，按仓库里提交的标记文件 `.swarmai/source-repo` 识别（fork、改名、新 clone 都能认出）；
  - 绑定了 DDD 项目、并在 Settings 里打开"提交前审查"开关的仓库。
- 其他仓库默认**不拦，只提示**（XG 选 B）：commit 未经审查时，gate 放行并附一句"建议先让审查 sub-agent 审一遍这些文件"。
- **开关放在 Settings**（按项目、按仓库列出），用户看得见、点得到；状态由 daemon 存在 app 设置里。不是 daemon 环境变量：`SWARM_GATE_FORCE` 只留给运维调试，不作为产品开关。
- 开启的仓库被拦时，提示写给用户看：这个仓库开了提交前审查；怎么满足（派审查 sub-agent 审这些文件，修掉发现的问题）；在 Settings 哪里关掉。

### 0.4 gate 分三类

| 类别 | gate | r4 处理 |
|---|---|---|
| 1. 为了把人赶进 pipeline 而拒绝 | worktree rule 4（主 checkout 写代码）、`direct_commit_gate`、`pipeline_test_gate` 的"去用 runner"、`active_run_commit_gate`（主 checkout 提交 run 改过的文件）、`pytest_command_guard`（未套时间上限的 pytest） | **全部改成只提示、不拒绝**，所有仓库都这样（含 SwarmAI），提示写明建议和原因 |
| 2. 用户主动开了 run 后保护这个 run | 不许写别的 run 的 worktree（rule 1）、run 的写入留在它自己的 worktree（rule 2-3）、run 的落地 / 签名 / owner / token | 保留；只对选择进 pipeline 的会话生效；随时可以 abandon 回到 native |
| 3. 与 pipeline 无关的安全类 | 危险命令和发布到远端要人批准、`session_link_gate`、`audit_marker_write_gate`、凭证文件读取限制 | 保留 |
| 显式开启才拦，其他只提示 | `adversarial_commit_gate` | 见 0.3 |

`active_run_commit_gate` 降为提示是安全的：主 checkout 先提交了 run 改过的文件，run 落地时 ff-only 失败，走已有的 rebase / 冲突流程，run 不会被弄坏。

代价：SwarmAI 仓库不再强制隔离后，多会话共用主 checkout 的老问题（2026-10-19：改动被卷进别的会话的 commit、commit 带上别人暂存的文件）可能回来。缓解：提示建议走 pipeline 或自己的 worktree；AGENTS.md 的工作流说明保留为指引。

### 0.5 冻结机制本身的投入

- **不再做"防 agent 作恶"类加固**：B-d（人工接口鉴权）、sandbox 子进程、首次批准、agent 进程隔离都不做；E7 遗留项 7 保持"已知、不修"。
- **下一轮先量，再决定加不加东西**：
  1. 每个 run 的成本：调用次数、token、墙钟时间、人工介入次数，按 profile 分，从签名事件和 `run_audit.json` 读；
  2. gate 的实际效果：每个 gate 一段时间内拦了多少次（`gate_decisions.jsonl`、`scripts/gate_audit.py`），抽样判断多少是真问题、多少是误拦。
- 没有这两组数字之前，不新增 gate，不新增加固。

### 0.6 其余取舍（沿用 r3）

- Windows 不启用 pipeline，默认 native；pipeline 命令在 Windows 上明确返回"不支持"。
- 第一版 adapter（r5 取代）：pipeline 与语言无关，不逐个语言适配，见 §3.3.2。
- 验证结论三档 `verified` / `failed` / `unverified(原因)`；只有 `failed` 挡，且只在 pipeline 里挡。
- 不做 sandbox 和首次批准，只清理凭证类环境变量（runner 跑在 agent 自己的权限里）。
- 不签名先发：macOS 下载页写明"系统设置 → 隐私与安全性 → 仍要打开"（macOS 15 起右键打开不再有效）；S1 检查 arm64 可执行文件有 ad-hoc 签名；Windows SmartScreen 点"更多信息 → 仍要运行"。

## 0.1 一句话

今天只有 XG 自己的机器能用 SwarmAI：正式安装包里**没有后端**；就算有，pipeline 的命令**跑不起来**；
就算跑起来，pipeline **只认 SwarmAI 自己的仓库结构**；而 worktree 模式（默认开）又会**强制**用户的
代码改动走 pipeline。四件事叠在一起，别人装上 SwarmAI 后，连改自己的代码都做不到。

本设计把这四层一次理顺，原则是：**能验证就验证，验证不了就不挡，并且如实标出"没验证"**。

## 1. 现状（每条都已核对，2026-10-24）

| 层 | 事实 | 证据 | 后果 |
|---|---|---|---|
| L0 安装包 | v2.3.0 的 DMG 12.5 MB、exe 9.4 MB、msi 12 MB；PyInstaller 后端 385 MB | `gh release view v2.3.0`；`du -sh ~/.swarm-ai/daemon` | 安装包里没有后端 |
| L0 | Tauri 只打包 `desktop/resources/`，`externalBin: []`；后端产物在 `desktop/src-tauri/binaries/`，不进包 | `tauri.conf.json:22-24,47`；`build-backend.sh:451-462` | 同上 |
| L0 | 本机能用，是因为 `prod.sh` 在 Tauri 打包后手工 rsync 后端进 `.app`，并部署到 `~/.swarm-ai/daemon`；CI 的 `release.yml` 只跑 `npm run tauri build` | `prod.sh:300-310`；`release.yml:76-103` | CI 产物缺后端 |
| L0 | `/Applications/SwarmAI.app` 也只有 26 MB；`find_backend_binary` 最后找 `~/.swarm-ai/daemon/python-backend` | `lib.rs:2570-2620` | 陌生机器上报 "Backend failed to start" |
| L0 | Windows 同样没把后端放到 exe 旁；没有 macOS x64、Linux 发布 job | `release.yml:32-176` | 同上 |
| L0 | 外置 daemon 的部署、升级、回滚机制**已经有了**：app 启动时把 `Resources/python-backend/` 拷到 `~/.swarm-ai/daemon/`，升级前整目录备份，起不来就回滚 | `lib.rs:1490-1580,1719-1745` | 缺的只是包里那份 `python-backend` |
| L0 | 没有 Apple Developer ID 签名 / 公证，也没有 Windows Authenticode；`TAURI_SIGNING_PRIVATE_KEY` 只签更新包 | `release.yml:88`，无 notary/证书配置 | 后端补上了也可能被 Gatekeeper / SmartScreen 拦 |
| L1 运行时 | pipeline 命令要：真 Python、源码 checkout 里的脚本、可导入的后端模块、psutil、pytest + 插件、git、perl（`alarm`）、node/npx | `worktree_write_gate.py:416-426`；`pipeline_test.py:342-355,1069,1645-1744,2102-2142` | 装好的 app 里 pipeline 起不来 |
| L1 | 冻结后的后端是 PyInstaller 归档，外部 Python 导入不了它的模块；MCP 脚本同样靠系统 Python 跑 | `bundle_paths.py:27-64`；`mcp_config_loader.py:415-420` | 陌生机器上 MCP 工具同样可能坏 |
| L1 | gate 只认 `python*` 解释器名 + 确切脚本路径 | `security_hooks.py:3963`；`run_owner_gate.py:107-121,534-558` | 换运行时要同步改 gate |
| L2 仓库 | 测试发现只认 `backend/**/*.py`、`backend/tests/**/test_*.py`、`desktop/src`；解释器只找 `<repo>/backend/.venv` | `pipeline_test.py:342-355,571-649,3810-3854` | 普通 Python 项目选不出测试 |
| L2 | JS 只认 `npx vitest related` + `tsc`（在 `desktop/`）；没有 Go/Cargo/Maven/Gradle 执行器 | `pipeline_test.py:2103-2251` | Node/Go/Rust/Java 项目跑不了 |
| L2 | 设计文档的测试引用只收 `.py/.ts/.tsx/.js/.jsx/.rs`；收集只认 `backend/...::...` | `schemas.py:83-97`；`pipeline_test.py:5420-5468` | PLAN/BUILD 卡住 |
| L2 | BX-2（direct）、DS-2b/4b/9b、BX-1、LG-2（每个 verdict 必须 green）、LG-5 | `checks.py:144-177,329-575`；`final.py:194-216`；`runner.py:525-550` | 非 SwarmAI 的代码 run **无法正常落地** |
| L2 | risk_selection 全是 SwarmAI 路径；worktree 只软链 `desktop/node_modules`；落地作者固定为 Swarm 并加 Swarm trailer | `risk_selection.py:16-95`；`run_worktree.py:48-53`；`landing.py:262-287` | 选测不准、依赖缺失、改写了别人仓库的提交身份 |
| L2 强制 | worktree 模式默认开；rule 4：会话在**项目仓库**（`Projects/<P>` 对应的 repo）主 checkout 里写代码，一律拒绝，要求建 pipeline run | `worktree_write_gate.py:276-284,326-351`；`run_worktree.mode_enabled` | 用户**不能用普通方式改自己的代码**，又走不通 pipeline |
| L2 强制 | worktree 模式开着时，`direct_commit_gate` 对**所有项目仓库**生效，门槛 0 行；`adversarial_commit_gate` 不分仓库 | `security_hooks.py:3371,6700-6710,6772` | 就算能写，`git commit` 也会被拒 |
| L2 强制 | "SwarmAI 源码仓库"在打包版里靠构建时路径或近期 run contract 判断；干净安装 + 新 clone 时两者都没有 | `security_hooks.py:3802-3811` | 认不出 SwarmAI 自己的仓库 |
| L3 Windows | daemon_exec / hook_token 依赖 Unix socket；`perl alarm`；进程清理用 POSIX 信号 | `hook_token.py:9-16,57-58`；`daemon_exec.py`；`pipeline_test.py:1250-1318` | Windows 上 pipeline 结构上不支持 |

没有任何一道 CI 检查在"装好的安装包"上跑过：CI 只在源码树里 import 和跑测试（`ci.yml:17-39,155-194`）。
这才是这个错误类能存在半年的原因（§7 的 G1 把它变成结构性 gate）。

## 2. 目标与非目标

**目标**
1. 陌生人在干净的 macOS（arm64/x64）、Windows、Linux 上装正式安装包，app 起得来，聊天能用。
2. 在这样的安装里，pipeline 命令能跑；用户把自己任意语言的仓库绑定到项目后：
   - 能识别测试方式的仓库：pipeline 正常验证、正常落地；
   - 识别不了或没有测试的仓库：**不挡**，照样能改、能落地，但 run 的报告、审计卡片、REPORT 都明确写"未经测试验证"。
3. 用户随时可以不走 pipeline，用 CLI 原生方式（直接在自己仓库里改、跑、`git commit`）工作；SwarmAI 不强制。
4. 让"安装包缺东西"这一类错误再也发生不了：发布前必须在真实安装产物上跑通冒烟测试。

**非目标**
- 不在 pipeline 里做任何语言相关的选测；通用选测只到"改动的测试文件 + 同目录测试 → 组件整套"。更细的选测由仓库自己的测试命令负责（SwarmAI 仓库就是这样）。
- 不改 Pipeline v1 的签名、隔离、owner、落地协议（B 类已定）；同用户残余仍是 E7 遗留项 7。
- 不在本设计里做 agent 进程隔离（单独立项）。

## 3. 设计

### 3.1 L0：安装包自带后端 —— 先修现有打包，不换运行时

skeptic 证伪了 r1 的"换成可搬移 CPython"：现有的 PyInstaller onedir 已经打进了 Claude CLI、原生依赖（`sqlite_vec`、`tree_sitter`、`awscrt`）和 `scripts`（`build-backend.sh:262,288-304,349-372`），外置 daemon 的部署、升级、回滚也都有了（§1）。缺的只是 CI 没把这份 onedir 放进安装包。换运行时会让体积（KiroCrew 654 MB / 19,096 个文件）、签名、跨平台 wheel 风险都变大。

**决定：保留 PyInstaller onedir，修打包；只有当 §3.2 的子命令方案实测走不通时才换运行时。**

- `build-backend.sh` 把 onedir 输出到 `desktop/src-tauri/resources-backend/python-backend/`，`tauri.conf.json` 的 `bundle.resources` 加一项映射到 `python-backend/`，`lib.rs` 现有的 `resource_dir()/python-backend` 读取路径不变。Windows 同理（Tauri resources 在 MSI/NSIS 里落到 exe 旁的资源目录）。
- `prod.sh` 的"打包后 rsync 注入"删除：本机和 CI 走同一条路径。
- **daemon 永远从外置的版本化目录运行，不从 app 包里直接跑**（skeptic #8：更新会在进程运行时替换文件）。现有 `~/.swarm-ai/daemon` + `daemon.backup` 机制保留并补强为 `~/.swarm-ai/runtime/<version>/` + `current` 原子切换 + 保留一个回滚版本；切换前先 drain 旧 daemon、核对文件哈希。
- 签名：macOS Developer ID 签名每个原生库和可执行文件 + 公证 + staple；Windows Authenticode。需要 XG 提供证书（§8 Q1）。
- 发布目标：macOS arm64、macOS x64、Windows x64、Linux x64（AppImage/deb）。

### 3.2 L1：pipeline 客户端由后端程序自己的子命令执行

- 冻结后的 `python-backend` 加子命令分发（在 `desktop_main.py` 里，导入 FastAPI 之前判断）：`python-backend daemon`（默认，现行为）、`python-backend pipeline-v1 <args>`、`python-backend pipeline-test <args>`、`python-backend mcp <name>`。模块都在包里，和 daemon 同版本，不需要外部 Python、源码 checkout、psutil 安装。
- 每个 agent 会话的环境注入 `SWARM_BACKEND_BIN=<外置 runtime 里 python-backend 的绝对路径>`。`v1_command` / `pipeline_test_command` 在打包版里生成 `"$SWARM_BACKEND_BIN" pipeline-v1 …`，dev 版保持 `backend/.venv/bin/python backend/scripts/pipeline_v1.py …`。`source_repo.json` 和 `utils/source_repo.py` 删除。
- gate 识别：在现有"python + 确切脚本路径"之外，加一个**确切可执行文件路径 + 子命令**的形式：`argv[0]` 解析后必须等于 daemon 自己的 `sys.executable`（外置 runtime 里那份），`argv[1]` 必须是 `pipeline-v1`/`pipeline-test`。不放宽 `_PY_INTERP_RE`。
- `perl -e 'alarm 110'` 外壳删除：`pipeline-test` 在进程内实现截止时间（POSIX 进程组 + 定时器；Windows 用 Job Object），`pytest_command_guard` 认它的自我截止。
- MCP 脚本改用 `python-backend mcp <name>`，不再依赖系统 Python（陌生机器上同样坏的那一类）。
- 用户仓库的测试**永远不用后端程序本身跑**：用 3.3 解析出的仓库自己的工具链。SwarmAI 自己的仓库在它的 `.swarmai/pipeline.toml` 里声明用 `backend/.venv`。
- 打包版里如果子命令方案在某平台走不通（例如 pytest 插件或原生依赖），再评估可搬移 CPython，那时要带实测的体积、冷启动、签名对比。

### 3.3 L2（r5）：pipeline 核心与语言无关

**定位（XG 2026-10-24）**：pipeline 是 SwarmAI 的高级自主编码能力，不是默认路径。它支持的仓库不按语言划界：核心不认识任何语言，只认三样东西。

#### 3.3.1 触发：用户选择或声明

| 方式 | 行为 |
|---|---|
| 用户在对话里要求（"用 pipeline 做"、"无人值守做完"） | 走 pipeline |
| 仓库声明 `.swarmai/pipeline.toml` 的 `[pipeline] trigger` | `manual`（默认）：只有用户要求才走；`suggest`：风险较大的改动 agent 主动建议；`auto`：该仓库的改动都走 |
| Settings 里按项目 / 仓库的同名开关 | 同上；与仓库声明不一致时取 Settings |
| 都没有 | `manual`，即默认 native |

#### 3.3.2 硬约束：pipeline 与语言无关，不逐个语言适配（XG 2026-10-24）

- pipeline 的代码里**不出现任何语言、框架、包管理器的名字**：没有 pytest / npm / go 分支，没有"manifest → 命令"的表，没有按后缀的白名单。
- "这个仓库怎么测"只有两个来源：**仓库自己声明**，或 **agent 读仓库后提议**。认识语言是 LLM 的事，不是 pipeline 的事。
- 验收里有一条结构测试：pipeline 核心模块（`core/run_protocol/`、`scripts/pipeline_v1.py`、`scripts/pipeline_test.py` 的通用部分）的源码里搜不到语言/工具名（白名单只有 `git`）。SwarmAI 自己仓库的专用逻辑搬出核心，见 3.3.5。

#### 3.3.3 核心只认三样东西

1. **组件**：一个根目录，加上"哪些文件是源码、哪些是测试"的 glob。
2. **命令**：一个 argv 列表（不经 shell），在组件根目录下跑。可选声明 `accepts_files = true`：选中的测试文件追加在末尾；否则每次跑整套。
3. **证据**：退出码，加可选的 JUnit XML 输出路径（`junit = "<路径>"`）。几乎所有测试框架都能输出 JUnit XML；有就按测试 id 记录每个测试的结果，没有就只看退出码。

#### 3.3.4 profile 从哪里来：仓库声明 → agent 提议 → 未验证

建 run 时按顺序取第一个能用的，写进受保护的 contract，run 中途不变：

1. **仓库声明**：base commit 里的 `.swarmai/pipeline.toml`（分支改不掉自己的验证方式）。
2. **agent 提议（XG 选 B）**：没有声明时，agent 在 `create` 时提交一份 profile 提议：组件、命令、依据（例如"README 写了 `make check`"、"CI 配置里跑的是 `./scripts/test.sh`"、"package.json 的 test 脚本"）。规则：
   - 只能在 run 的第一次代码改动之前提交，之后不可改；
   - 命令必须在 **base commit** 上就能跑：daemon 在 base 的 worktree 里试跑一次并记录（退出码、耗时、是否产出 JUnit）。试跑通过的才被采纳；试跑失败（包括本机缺工具）的不采纳，原因写进 contract；
   - 提议内容和试跑结果展示在 run 卡片和 REPORT 里；
   - 由它得到的结论标为 `verified(agent_profile)`，和仓库声明来源的 `verified` 区分开。
   - agent 可以把采纳的提议写成 `.swarmai/pipeline.toml` 一并提交，下次就是仓库声明。
3. **兜底（A）**：agent 也找不到能在 base 上跑通的命令时，该组件为 `unverified(no_signal)`（或 `unverified(tool_missing)`，当试跑失败是因为命令不存在），照样落地，REPORT 首行写明"未经测试验证"和原因。

unborn 仓库（没有任何 commit）：没有 base，直接 `unverified(no_base)`。

#### 3.3.5 核心里要改成通用的地方

| 现在（写死 SwarmAI / Python） | 改成 |
|---|---|
| 测试只在 `backend/tests`、`desktop/src` 下发现 | 组件的 `tests` glob |
| 选测靠 Python AST 依赖图 | 通用三级：改动的测试文件本身 + 与改动源码同目录的测试文件 → 组件整套；命令不接收文件参数时直接整套 |
| L4"只改了注释 / 格式"只支持 Python AST | 删除这类判断：组件 `sources` 里任何非空白的改动都算代码改动（只会多测，不会少测）。纯文档仍按 `doc_paths` |
| DS-2a 等测试引用按后缀白名单 | 引用在组件 `tests` glob 内，或是 JUnit 里出现过的测试 id |
| BX-2 先红后绿只支持 pytest | 通用：同一个测试（文件或 JUnit id）在 base tree 上失败、改动后通过，用组件命令分别在两个 tree 上跑；命令不接收文件参数时，退化为"改动后整套通过" |
| risk_selection 写死 SwarmAI 路径 | 通用：改动落在组件根目录、但不在 `sources` / `tests` 里的文件（构建配置、依赖清单等），一律跑该组件整套 |
| worktree 只软链 `desktop/node_modules` | 由组件 `link` 声明（agent 提议时一并提议） |
| 落地作者固定 Swarm + Swarm trailer | 默认用仓库自己的 git 身份、不加 trailer；由仓库声明覆盖 |

**SwarmAI 自己的仓库**：它现在的精细选测（AST 依赖图、BVT、风险扩展、vitest related）从 pipeline 核心搬出去，变成 SwarmAI 仓库自己的测试命令（`backend/scripts/swarm_select_tests.py`，在它的 `.swarmai/pipeline.toml` 里声明为组件命令，`accepts_files = true`）。对核心来说它和任何仓库的命令一样。冻结规则（`pipeline-test-speed.md` §11）随之约束这个脚本。

### 3.4 执行仓库自己的命令（r4：不做批准和 sandbox，只清理凭证类环境变量；下文只作记录）

skeptic #4：`npm test`、conftest、Makefile 本身就是仓库控制的代码。需要说清楚风险的真实范围：runner（`pipeline-test`）跑在 agent 的 Bash 进程里，和 agent 自己执行 `npm test` 是同一个用户、同一份权限，**不是新增的权限**；新增的是"SwarmAI 主动替用户跑了仓库的代码"。所以：

- **首次批准**：一个仓库第一次要执行测试/校验命令时，pipeline 发一张 decision 卡片，列出要执行的确切命令和 profile 摘要（digest）；人批准后记入该仓库的批准记录。digest 变了（`pipeline.toml` 或识别结果变了）要重新批准。未批准时这个仓库的验证状态是 `unverified(not_approved)`，**不挡落地**。
- **受限子进程**：环境变量里去掉凭证类（`AWS_*`、`*_TOKEN`、`*_SECRET*`、`GH_*`/`GITHUB_TOKEN`、`SSH_AUTH_SOCK`、`ANTHROPIC_*`、`CLAUDE_*` 等），cwd 固定在 run 的 worktree，进程数/时间/输出有上限；macOS 用 `sandbox-exec` 配置拒绝读 `~/.ssh`、`~/.aws`、`~/.swarm-ai/state`，写入只允许 worktree 和 scratch。网络默认放开（大多数测试要装依赖或连本地服务），profile 可声明 `network = false`。Linux 用 bubblewrap（可用时），Windows 第一版只做环境清理（§8 Q4）。

### 3.5 验证结论按改动路径计算，不按仓库一刀切

每个**非文档的改动路径**归到一个组件（按 `sources` 和 `root`），各自得到 daemon 算出的结论：

| 结论 | 含义 |
|---|---|
| `verified` | 组件的测试由 runner 跑过且全绿，覆盖这个路径；profile 来自仓库声明 |
| `verified(agent_profile)` | 同上，但 profile 是 agent 在动代码前提议、并在 base 上试跑通过的（§3.3.3） |
| `failed` | 跑了，有失败 |
| `unverified(tool_missing)` | 组件识别出来了，但本机缺工具（比如没有 Go） |
| `unverified(no_signal)` | 没有仓库声明，agent 也提不出能在 base 上跑通的命令 |
| `unverified(unmapped)` | 改动路径不属于任何组件 |
| `unverified(no_base)` | unborn 仓库 |

仓库结论：任一路径 `failed` → **挡**；全部 `verified` → `verified`；否则 `unverified`，列出每个原因和对应路径。**`unverified` 不挡落地**（XG 的要求），REPORT 首行、审计卡片、chat 里的 run 卡片都写"未经测试验证"和原因，`tool_missing` 还要写出缺什么、怎么装。

这守住了 fail-closed：结论只来自 contract 里的 profile、runner 签名的结果和 daemon 的环境探测；agent 写不了"测不了"。一个能测的组件，只有在真的跑过且通过时才是 `verified`；没跑就是 `unverified`，绝不会被当成通过。

检查改写（统一按上表判，不再各写一套）：
- **LG-2**：有 `failed` 挡；其余放行，`unverified` 记 Quality = WARN。
- **BX-2（direct）**：有可执行组件的改动路径要求 runner 跑过相关测试；`unverified` 的路径不要求。
- **DS-2a/9a**：测试引用改为"在所属组件 `tests` glob 内，或 JUnit 里出现过的测试 id"，不再按后缀白名单。
- **DS-2b/4b/9b、BX-1**：用组件命令执行；有 JUnit 就按测试 id 判，没有就按"所在测试文件跑过且通过"判。
- **LG-5**：校验项来自 `pipeline.toml [[validation]]`（`validations.json` 兼容读一版）。
- **risk_selection**：删除核心里的规则；改动落在组件根目录但不在 `sources`/`tests` 里，就跑组件整套。SwarmAI 的规则搬进它自己的测试命令。

### 3.6 不强制（r4：以 §0.3、§0.4 为准，下文只作记录）

skeptic #2/#3：只改 rule 4 不够，`direct_commit_gate`、`adversarial_commit_gate` 也会拦 commit；而且打包版认不出 SwarmAI 自己的仓库。

- **仓库策略** `required | optional`，由 daemon 决定：
  - **SwarmAI 源码仓库永远 `required`**，识别方式不靠路径：仓库 HEAD 的 tree 里有 SwarmAI 标记文件 `.swarmai/source-repo`（随 SwarmAI 仓库提交，内容是固定的仓库 UUID），**或** 当前目录就是 dev 模式下运行中后端自己的仓库。fork、改名、symlink、新 clone 都能认出来。
  - 其他仓库默认 `optional`；用户可以在项目设置里改成 `required`（只能通过 dashboard，存在 app 设置里，不在 agent 能改的 `bindings.yaml` 里；同用户残余见 E7 遗留项 7）。
- **只对 SwarmAI 源码仓库生效的 gate**（r3）：rule 4（主 checkout 写代码）、`direct_commit_gate`、`pipeline_test_gate` 的"去用 runner"提示。
- **`pytest_command_guard`**（r3 新增）：它要求 pytest 套 `perl alarm` 或 `timeout N`；Windows 没有 perl，等于挡死 native pytest。改为：SwarmAI 仓库照旧拒绝；其他仓库只给提示不拒绝；Windows 上不生效。
- **`adversarial_commit_gate`**：对所有仓库保留（XG 定），只改提示文字。
- **永远生效、不看策略的 gate**：worktree gate 的 rule 1-3（不许写别的 run 的 worktree、本 run 的写入留在自己的 worktree 等）、`active_run_commit_gate`（run 改过的文件只能由 run 落地）、`session_link_gate`、`audit_marker_write_gate`、危险命令和 HITL 类 gate。
- `optional` 仓库：会话直接在主 checkout 里改、跑、`git commit`；pipeline 是可选工具。

### 3.7 L3：Windows（r4：不做，Windows 不启用 pipeline）

- daemon_exec / hook_token 的传输层抽象成接口：POSIX 用 owner-only Unix socket；Windows 用命名管道，ACL 只给当前用户 SID，`GetNamedPipeClientProcessId` 取对端 pid，`GetProcessTimes` 取创建时间。token 格式统一为 `<transport>.<id>.<nonce>`。
- 进程组清理和截止时间用 Job Object。
- L3 完成前，Windows 上 pipeline 命令明确返回"pipeline 在 Windows 上尚不支持"；3.6 保证用户仍能用原生方式改代码。

## 4. 实施顺序（每步一个 commit，测试先行，变异验证）

skeptic #9：打包改动必须和证明它能用的测试同一步落地。

| 步 | 内容 | 关闭 |
|---|---|---|
| S1 | **先写安装产物验收测试（先红）**：§7 的 G1/G2 作为 CI job，在当前 release 产物上跑，必然失败 | 错误类的验收 |
| S2 | 打包修复（3.1）+ 外置版本化 runtime + 四个目标 + 签名公证；G1/G2 转绿后才允许发布 | L0 |
| S3 | 子命令分发 + `SWARM_BACKEND_BIN` + gate 确切路径识别 + 删 perl 外壳 + MCP 子命令（3.2） | L1 |
| S4 | 按 §0.4 改 gate：第 1 类全部降为提示；`adversarial_commit_gate` 改为按仓库开关生效（SwarmAI 标记文件默认开 + Settings 里按仓库的开关 + 用户看得懂的提示）；Windows 上 pipeline 命令返回"不支持" | 不强制 |
| S4b | 度量（§0.5）：每个 run 的成本报表 + 每个 gate 的拦截次数和抽样真假 | 决策依据 |
| S5 | profile 解析（仓库声明 → agent 提议 + base 试跑 → 未验证）+ contract 字段 + 触发方式（§3.3.1）| — |
| S6 | 按路径的验证结论 + 核心检查通用化（§3.3.4、§3.5）+ 清理凭证类环境变量 | L2 落地 |
| S7 | ~~首次批准 + 受限子进程~~（r3 不做） | — |
| S8 | SwarmAI 现有选测搬出核心，成为 `swarm_select_tests.py` + SwarmAI 仓库的 `pipeline.toml`（行为不变，冻结规则随之）+ 核心"无语言名"结构测试 + worktree 软链 + 落地身份 | 语言无关 |
| S9 | ~~Windows 传输层~~（r3 不做：Windows 不启用 pipeline，默认 native） | — |

**发布节奏**：S1–S4 一起构成第一个"别人装上能用"的版本（app 能起、能聊天、能用原生方式改代码、SwarmAI 仓库照旧受保护）；S5–S8 构成"pipeline 能用在任意语言的仓库上"的版本（能识别或 agent 能提议测试方式的仓库可验证，其余标"未验证"照样落地）。S4 不单独发版：单独发的安装包里仍然没有后端。

## 5. 五维评估

| 维度 | 影响 | 风险 | 缓解 | 预判 |
|---|---|---|---|---|
| Run Isolation | 签名、owner、worktree、落地协议不动；rule 1-3 和 active-run commit gate 不看策略 | `optional` 仓库的直接改动不在 run 里 | 这类改动本来就不属于 run；SwarmAI 仓库按内容识别，永远 `required` | PASS |
| Quality | 新增 `unverified` 会让未测试的改动落地 | 被当成"跳过测试"用 | 结论只来自 contract + runner 签名 + 环境探测；按路径算，monorepo 不会被一个组件的绿掩盖；`failed` 照样挡；首行标注 | PASS（有 WARN 档） |
| Autonomy | 非 SwarmAI 仓库从必然卡死变成能自主完成；首次批准多一次人工 | 批准卡片每个仓库一次 | 只在 digest 变化时重新批准 | 需样例矩阵 + NE-0 实测 |
| Speed | 通用选测多为整套 | 大仓库超时 | 现有续跑；仓库可声明更细的测试命令和 `timeout` | 需实测 |
| Token Cost | 基本不变 | 测试输出变长 | runner 只回摘要和失败项 | PASS |

## 6. 验收

- **样例仓库矩阵**（`backend/tests/fixtures/repos/`，测试时 `git init`）：plain-python（`src/`+`tests/`）、python-uv、node-jest、node-vitest-pnpm、go-module、rust-crate、java-maven、make-only、no-tests、monorepo（python+node）、unborn。每个跑 direct 和 full：断言落地结果、每条路径的结论、REPORT 首行。monorepo 里只改 node 部分且本机没有 node 时，结论必须是 `unverified(tool_missing)`，不能是 `verified`。
- **反作弊**：有测试的仓库，在分支上改 `pipeline.toml` 删测试 → 不影响（profile 来自 base SHA）；agent 无法写出 `unverified`。
- **策略**：`optional` 仓库直接 Write 和 `git commit` 都不被拒；SwarmAI 仓库在 fork、改名、symlink、新 clone、从未建过 run 的情况下都被认成 `required`。
- **执行安全**：未批准时不执行仓库命令；子进程环境里没有凭证变量；macOS 上测试读不到 `~/.ssh`。
- 现有 SwarmAI 的 pipeline 测试全部保持绿。

## 7. 让这一类错误不再发生

skeptic #10：GitHub 的 runner 本身装着 Python 和 Node，"干净"是假的；只看体积和路径字符串也能被绕过。

- **G1 真实安装验收**：每个目标在一次性的干净 VM/容器里（PATH 里没有 python/node/git 之外的开发工具，没有源码 checkout），安装刚打出的安装包，验证：签名和公证有效、app 启动、外置 runtime 部署成功、`/health`、自带 Claude CLI 可执行、seed 和迁移成功、onboarding 页面可达、更新到下一个版本再回滚成功；再对两个样例仓库各跑一个 direct run：一个必须得到 `verified`，一个必须得到 `unverified(no_signal)`。
- **G2 包内容清单**：构建时生成并签名一份清单（必需文件、哈希、架构、可执行文件、原生库），G1 安装后逐项核对；缺一项就失败。取代 r1 里不可靠的"体积下限"和"路径字符串"检查。
- G1、G2 是 release workflow 发布步骤的前置 job；不过不发布。

## 8. 待定问题（给 XG）

1. Apple Developer ID 证书、公证账号、Windows Authenticode 证书：有没有？没有的话第一版只能发"未签名，需要右键打开"的 macOS 包，Windows 会有 SmartScreen 警告。
2. ~~adapter 范围~~（r5：不做 adapter，pipeline 与语言无关）
3. `unverified` 落地时只标注不挡（本设计按你的要求）；`unverified(not_approved)` 是否也只标注？还是首次批准前不允许落地？
4. Windows 的测试子进程第一版只做环境清理、不做文件系统限制，可以接受吗？
