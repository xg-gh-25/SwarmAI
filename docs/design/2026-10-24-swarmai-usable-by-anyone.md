---
title: SwarmAI 让别人装上就能用 — 安装包、pipeline 运行时、任意语言仓库（Sev-1）
created: 2026-10-24
updated: 2026-10-24
project: SwarmAI
status: draft r2 — skeptic 第一轮（c76934d4）的 7 个 BLOCKER / 3 个 MAJOR 已并入，待 XG 审
---

# SwarmAI 让别人装上就能用（Sev-1）

## 0. 一句话

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
- 不追求对每种语言都做 SwarmAI 那样细的按依赖选测；通用语言默认"跑整套或按包跑"，精细选测是 adapter 的可选增强。
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
- 用户仓库的测试**永远不用后端程序本身跑**：用 3.3 解析出的仓库自己的工具链。只有 `swarmai` adapter（SwarmAI 自己的仓库）用它自己的 `backend/.venv`。
- 打包版里如果子命令方案在某平台走不通（例如 pytest 插件或原生依赖），再评估可搬移 CPython，那时要带实测的体积、冷启动、签名对比。

### 3.3 L2：仓库的验证方式由 daemon 从确切的 base commit 解析

建 run 时，daemon 对每个仓库、按**确切的 base commit SHA**解析出 `test_profile`，连同 SHA 一起写进受保护的 contract，run 中途不变。解析顺序：

1. 该 commit 里的 `.swarmai/pipeline.toml`（分支改不掉自己的验证）；
2. 内置 adapter 按 manifest 识别，**每个命中的组件都保留**（monorepo 会有多个组件，各自有自己的根目录）；
3. 都没有：该仓库没有组件。

unborn 仓库（还没有任何 commit）：daemon 生成"空 base"profile，所有改动都是 `unverified(no_base)`，在第一个可信 commit 之前不执行仓库提供的任何命令。

`.swarmai/pipeline.toml` 形状：

```toml
[[component]]
name = "web"
root = "web"                          # 相对仓库根
sources = ["src/**/*.ts"]             # 改了这些算这个组件的代码改动
tests = ["src/**/*.test.ts"]
run = ["npm", "test", "--"]           # argv，不经 shell；选中的测试文件追加在后面
select = "files"                      # files | all
timeout = 300

[[validation]]
name = "build"
run = ["npm", "run", "build"]

[worktree]
link = ["node_modules"]

[landing]
author = "repo"                       # repo（默认）= 仓库自己的 git user；swarm = Swarm 身份
trailer = ""                          # SwarmAI 自己的仓库写 Swarm 那一行
```

内置 adapter（第一版；每个都要在 §6 的样例仓库上跑出 `verified`）：

| adapter | 识别 | 默认命令 | 选测 | 证据 |
|---|---|---|---|---|
| `swarmai` | 仓库在 base commit 里有 SwarmAI 标记文件（见 3.5） | 现有 pytest + vitest | 现有按依赖选测 | 现有 node 级结果 |
| `pytest` | `pyproject.toml`/`setup.cfg`/`pytest.ini`/`tox.ini` + 存在 `test_*.py`/`*_test.py` | `<仓库 python> -m pytest` | 改了的测试文件 + 同包测试；不确定就整套 | JUnit XML |
| `node` | `package.json` 有 `scripts.test` | 按 lockfile 选 npm/pnpm/yarn | jest/vitest 用 related；否则整套 | 退出码，可得时 JUnit |
| `go` | `go.mod` | `go test ./<改动的包>/...` | 按包 | `go test -json` |
| `cargo` | `Cargo.toml` | `cargo test -p <改动的 crate>` | 按 crate | 退出码 + 计数 |
| `maven`/`gradle` | `pom.xml` / `build.gradle(.kts)` | `mvn -q test` / `./gradlew test` | 整套 | surefire XML |
| `make` | `Makefile` 有 `test` 目标、且无上述 manifest | `make test` | 整套 | 退出码 |

仓库 Python（仅 `pytest` adapter）：`pipeline.toml` 的 `python` → 仓库 `.venv`/`venv` → `uv run`/`poetry run`（有 lockfile 时）。

### 3.4 执行仓库自己的命令：先批准，再在受限子进程里跑

skeptic #4：`npm test`、conftest、Makefile 本身就是仓库控制的代码。需要说清楚风险的真实范围：runner（`pipeline-test`）跑在 agent 的 Bash 进程里，和 agent 自己执行 `npm test` 是同一个用户、同一份权限，**不是新增的权限**；新增的是"SwarmAI 主动替用户跑了仓库的代码"。所以：

- **首次批准**：一个仓库第一次要执行测试/校验命令时，pipeline 发一张 decision 卡片，列出要执行的确切命令和 profile 摘要（digest）；人批准后记入该仓库的批准记录。digest 变了（`pipeline.toml` 或识别结果变了）要重新批准。未批准时这个仓库的验证状态是 `unverified(not_approved)`，**不挡落地**。
- **受限子进程**：环境变量里去掉凭证类（`AWS_*`、`*_TOKEN`、`*_SECRET*`、`GH_*`/`GITHUB_TOKEN`、`SSH_AUTH_SOCK`、`ANTHROPIC_*`、`CLAUDE_*` 等），cwd 固定在 run 的 worktree，进程数/时间/输出有上限；macOS 用 `sandbox-exec` 配置拒绝读 `~/.ssh`、`~/.aws`、`~/.swarm-ai/state`，写入只允许 worktree 和 scratch。网络默认放开（大多数测试要装依赖或连本地服务），profile 可声明 `network = false`。Linux 用 bubblewrap（可用时），Windows 第一版只做环境清理（§8 Q4）。

### 3.5 验证结论按改动路径计算，不按仓库一刀切

每个**非文档的改动路径**归到一个组件（按 `sources` 和 `root`），各自得到 daemon 算出的结论：

| 结论 | 含义 |
|---|---|
| `verified` | 组件的测试由 runner 跑过且全绿，覆盖这个路径 |
| `failed` | 跑了，有失败 |
| `unverified(tool_missing)` | 组件识别出来了，但本机缺工具（比如没有 Go） |
| `unverified(not_approved)` | 还没批准执行仓库命令 |
| `unverified(no_signal)` | 仓库里识别不出任何测试方式 |
| `unverified(unmapped)` | 改动路径不属于任何组件 |
| `unverified(no_base)` | unborn 仓库 |

仓库结论：任一路径 `failed` → **挡**；全部 `verified` → `verified`；否则 `unverified`，列出每个原因和对应路径。**`unverified` 不挡落地**（XG 的要求），REPORT 首行、审计卡片、chat 里的 run 卡片都写"未经测试验证"和原因，`tool_missing` 还要写出缺什么、怎么装。

这守住了 fail-closed：结论只来自 contract 里的 profile、runner 签名的结果和 daemon 的环境探测；agent 写不了"测不了"。一个能测的组件，只有在真的跑过且通过时才是 `verified`；没跑就是 `unverified`，绝不会被当成通过。

检查改写（统一按上表判，不再各写一套）：
- **LG-2**：有 `failed` 挡；其余放行，`unverified` 记 Quality = WARN。
- **BX-2（direct）**：有可执行组件的改动路径要求 runner 跑过相关测试；`unverified` 的路径不要求。
- **DS-2a/9a**：测试引用改为"在所属组件 `tests` 范围内或 adapter 能识别的测试 id"，不再按后缀白名单。
- **DS-2b/4b/9b、BX-1**：收集与执行交给 adapter；不支持按 id 收集的，按"所在测试文件跑过且通过"算。
- **LG-5**：校验项来自 `pipeline.toml [[validation]]`（`validations.json` 兼容读一版）。
- **risk_selection**：SwarmAI 规则只对 `swarmai` adapter；其他组件改了 manifest/lockfile/构建配置就整套跑。

### 3.6 不强制：按仓库的 pipeline 策略，所有相关 gate 一起看同一个策略

skeptic #2/#3：只改 rule 4 不够，`direct_commit_gate`、`adversarial_commit_gate` 也会拦 commit；而且打包版认不出 SwarmAI 自己的仓库。

- **仓库策略** `required | optional`，由 daemon 决定：
  - **SwarmAI 源码仓库永远 `required`**，识别方式不靠路径：仓库 HEAD 的 tree 里有 SwarmAI 标记文件 `.swarmai/source-repo`（随 SwarmAI 仓库提交，内容是固定的仓库 UUID），**或** 当前目录就是 dev 模式下运行中后端自己的仓库。fork、改名、symlink、新 clone 都能认出来。
  - 其他仓库默认 `optional`；用户可以在项目设置里改成 `required`（只能通过 dashboard，存在 app 设置里，不在 agent 能改的 `bindings.yaml` 里；同用户残余见 E7 遗留项 7）。
- **按策略生效的 gate**：rule 4（主 checkout 写代码）、`direct_commit_gate`、`adversarial_commit_gate`、`pipeline_test_gate` 的"去用 runner"提示 —— 都只对 `required` 仓库生效。
- **永远生效、不看策略的 gate**：worktree gate 的 rule 1-3（不许写别的 run 的 worktree、本 run 的写入留在自己的 worktree 等）、`active_run_commit_gate`（run 改过的文件只能由 run 落地）、`session_link_gate`、`audit_marker_write_gate`、危险命令和 HITL 类 gate。
- `optional` 仓库：会话直接在主 checkout 里改、跑、`git commit`；pipeline 是可选工具。

### 3.7 L3：Windows

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
| S4 | 仓库策略 + SwarmAI 标记文件 + 所有相关 gate 改看策略（3.6） | L2 强制 |
| S5 | test_profile 解析 + contract 字段 + `swarmai` adapter 原样包进去（行为不变） | — |
| S6 | 按路径的验证结论 + 检查统一改写（3.5） | L2 落地 |
| S7 | 首次批准 + 受限子进程（3.4） | 执行安全 |
| S8 | 通用 adapter：pytest、node、go、cargo、maven/gradle、make；worktree link；landing 身份 | L2 语言 |
| S9 | Windows 传输层 + Job Object（3.7） | L3 |

**发布节奏**：S1–S4 一起构成第一个"别人装上能用"的版本（app 能起、能聊天、能用原生方式改代码、SwarmAI 仓库照旧受保护）；S5–S8 构成"任意语言仓库能走 pipeline"的版本；S9 单独发。S4 不单独发版：单独发的安装包里仍然没有后端。

## 5. 五维评估

| 维度 | 影响 | 风险 | 缓解 | 预判 |
|---|---|---|---|---|
| Run Isolation | 签名、owner、worktree、落地协议不动；rule 1-3 和 active-run commit gate 不看策略 | `optional` 仓库的直接改动不在 run 里 | 这类改动本来就不属于 run；SwarmAI 仓库按内容识别，永远 `required` | PASS |
| Quality | 新增 `unverified` 会让未测试的改动落地 | 被当成"跳过测试"用 | 结论只来自 contract + runner 签名 + 环境探测；按路径算，monorepo 不会被一个组件的绿掩盖；`failed` 照样挡；首行标注 | PASS（有 WARN 档） |
| Autonomy | 非 SwarmAI 仓库从必然卡死变成能自主完成；首次批准多一次人工 | 批准卡片每个仓库一次 | 只在 digest 变化时重新批准 | 需样例矩阵 + NE-0 实测 |
| Speed | 通用 adapter 多为整套或按包跑 | 大仓库超时 | 现有续跑；`select`/`timeout` 可配 | 需实测 |
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
2. 第一版 adapter 范围是否就是 3.3 的表？.NET、Swift、Ruby 放第二版？
3. `unverified` 落地时只标注不挡（本设计按你的要求）；`unverified(not_approved)` 是否也只标注？还是首次批准前不允许落地？
4. Windows 的测试子进程第一版只做环境清理、不做文件系统限制，可以接受吗？
