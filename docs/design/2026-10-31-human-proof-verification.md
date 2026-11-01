---
created: 2026-10-31
updated: 2026-11-01
title: "第二阶段验证：'只有人能做'在什么前提下能成为边界"
status: r2 — 验证方案，已过一次零上下文审查（§9）；§8 两件都已做；S0–S4 未开始。注意：XG 2026-10-31 定了方向，安全工作要保留 agent 代表他的完整权限，只防危险/不可逆动作和外部内容劫持；本文 S1 里"把 agent 和自己的配置、凭证、工作区隔开"的部分与此冲突，开工前要按这个方向重写
related: docs/design/2026-10-31-swarm-out-ops-registry.md（§2.4 绕过路径、§6 第二阶段）· docs/design/2026-10-31-swarm-run.md（P0-c）
---

# 第二阶段验证："只有人能做"在什么前提下能成为边界

## 1. 结论先说

登记表设计 §6 的想法是：App 签名，Host 和 Hive 验签，agent 即使读得到 Hive 密码和 AWS 凭证也伪造不了人的签名。
签名本身确实伪造不了，但这不是难点。难点有两个：

1. **验签的一方今天能被 agent 改掉。** daemon 的二进制和数据、App bundle 都属于当前用户；管 Hive 用的 AWS
   凭证就是用户默认的凭证，产品本身就用它 SSM 进 Hive 跑命令。
2. **agent 能驱动的通道不止 Bash。** 文件工具、MCP 和 hooks 的配置、它能调用的 daemon 路由、它能写进 webview
   的内容，各是一条路（§4）。其中四条今天完全绕开任何验证方：
   - `PUT /api/settings` 能直接关掉 agent 自己的沙箱；
   - 下一个 session 会在沙箱外运行 agent 写进工作区的 MCP 配置；
   - webview 有权限以任意参数运行 `python3` / `node`，以及启动能跑任意 Python 的 sidecar（2026-10-31 已删，§8）；
   - 文件工具不受沙箱管。

所以第二阶段不是"半天验证一下签名"，而是一项平台安全工程：每条通道都要关上，人的签名才有意义。这份方案的作用，是用尽量少的成本判断"能不能全部关上、要付出多少"，再决定做不做。

**顺序：** 先花约 2 小时做 S0（读 CLI 文档、列清单，不改产品），它决定这件事能不能成立；能成立，才开始改代码做 S1–S4。

## 2. 要防什么

- **攻击者：** owner 的 agent 能用的一切：
  - Bash；
  - 文件工具（Read / Write / Edit / Grep）和 MCP 工具；
  - 它能写的文件，以及下一个 session 会执行这些文件里的什么（MCP 配置、hooks）；
  - 它能经本机 HTTP 调用的 daemon 路由；
  - 它能写进 App 界面的内容（Markdown、HTML、文件名、Hive 带回的文字）。

  agent 和 daemon 是同一个 OS 用户，可能被 Hive 带回的文字、网页、文档注入。Hive 上也有一个 agent，和 Hive 的 daemon 是同一个用户。
- **不防：** 有 root 的恶意软件；用户本人。
- **要守住：** 人专用的动作只在人做了一个手势之后发生，并且做的就是人看到的那件事。人专用动作的范围不只是 Swarm Out：所有改变 agent 自己能力的动作也算，包括它的沙箱、工具、MCP、插件和可写路径。否则 agent 先给自己松绑，再做其他事。

## 3. 现状（2026-10-31 读过、测过；审查逐条复核过）

| 事实 | 出处 |
|---|---|
| App 是 ad-hoc 签名，没有 Team ID，designated requirement 是 cdhash（每次构建都变） | `codesign -dv /Applications/SwarmAI.app` |
| App bundle、`~/.swarm-ai`、`runtime/`、`data.db`（644）、`~/.aws` 都属于当前用户 | `ls -ld` |
| `config.json` 和存 AWS 密钥、Bedrock token 的 `secrets.json` 放在 SwarmWS，也就是 agent 的工作目录 | `core/app_config_manager.py:181-187`（`get_swarmws() / "config.json"`） |
| `PUT /api/settings` 能写除密钥外的所有配置项，包括 `sandbox_enabled_default`、`sandbox_excluded_commands`、`sandbox_additional_write_paths`、`sandbox_allowed_hosts`；桌面模式下不认调用方 | `routers/settings.py:36`、`:277` |
| 同类的路由还有：加 MCP 服务器（`routers/mcp.py:186`）、装插件（`routers/plugins.py:203`） | 审查读过；`/api/workspace` 的路径限制没核实 |
| CLI 从 SwarmWS 读 MCP 配置和 project 设置（可带 hooks / env），它们在 agent 沙箱外运行 | `SwarmWS/.claude/mcps/mcp-dev.json`；`core/prompt_builder.py:2197`（`setting_sources`） |
| 沙箱只管 Bash；owner session 用 `bypassPermissions`，没有文件路径检查（那个检查只给 channel session 用） | `core/prompt_builder.py:2360`；`security_hooks.create_file_access_permission_handler` |
| 沙箱默认关；开了也给写整个 `~/.swarm-ai/`；9 个命令在沙箱外跑：`docker,ps,pgrep,pkill,top,open,screencapture,osascript,launchctl` | `core/app_config_manager.py:100-103`；`core/prompt_builder.py:2363` |
| 沙箱配置只有 `enabled`、`excludedCommands`、`network`，没有"禁止读 / 写某路径"；它是全局设置，不能按 session 设 | `core/prompt_builder.py:859-868` |
| webview 有权限以任意参数执行 `node` / `python` / `python3`，也能以任意参数启动 `python-backend` sidecar（`py -c` 能跑任意 Python）；前端代码没有一处用到（自 initial commit 起就有；§8 已删） | `desktop/src-tauri/capabilities/default.json`；`desktop/src` 里没有任何 `@tauri-apps/plugin-shell` 引用 |
| webview 能开集成终端（PTY，在沙箱外） | `desktop/src-tauri/src/terminal.rs:146` `pty_spawn` |
| 发布版编进了 devtools；webview 的 CSP 只限了 `img-src` | `desktop/src-tauri/Cargo.toml:16`；`tauri.conf.json:15` |
| 这台机器的 Hive 账户没存自己的密钥，走默认凭证链（用户的 `~/.aws`） | `hive_accounts`：`access_keys`、`auth_config` 为 `{}`；`hive/provisioner.py:186-196` |
| 产品用账户凭证 SSM 到 Hive 执行命令；Hive 实例挂了 `AmazonSSMManagedInstanceCore` | `hive/provisioner.py:730`、`:1390`、`:1571`、`:1690`；`:396-398` |
| Hive 登录密码明文存在 `data.db` | `database/sqlite.py:2966` `auth_password` |
| Hive 上接受批准 / 回答的三处 | `core/swarm_out_bridge.py:258` `_valid`；`routers/chat.py:1564`、`:641` |
| Host 代理按 `ROUTES` 转发；Hive 版本更新后新增的人专用路由，Host 的登记表不认识 | `core/swarm_out_remote.py` `ROUTES`、`routers/swarm_out_remote.py:120` |

## 4. 七条通道，每条怎么关、要验证什么

| # | 通道 | 今天能做什么 | 关上需要 | 验证问题 |
|---|---|---|---|---|
| 1 | Bash | 读写所有用户文件 | CLI 沙箱 + 按路径禁止读写（需要改代码，见 S1）；沙箱外命令清单清空 | CLI 支持"允许目录里再禁止子路径"和"禁止读"吗？ |
| 2 | 文件工具、MCP 工具 | 不受沙箱管，读写所有用户文件 | owner session 也要有拒绝规则或路径检查 | `bypassPermissions` 下，CLI 的拒绝规则还对文件工具生效吗？ |
| 3 | 下个 session 会执行的配置 | 往 SwarmWS 写 MCP 配置或 hooks，下个 session 在沙箱外跑 | 这些配置移出 agent 可写的目录，或禁止写 | CLI 从哪些路径读 MCP、hooks、settings？能不能指到别处？ |
| 4 | daemon 当副手 | 经本机 HTTP 改沙箱设置、加 MCP、装插件、做人专用的 Swarm Out 动作 | 所有改变 agent 能力的路由和所有人专用路由都要求"人的证明"（§5） | 这类路由一共有多少？（用第一阶段登记表的办法，枚举 `app.routes`） |
| 5 | webview | 一处脚本注入就能以任意参数运行 python / node（在沙箱外，以 App 身份）、开终端、开检查器 | 删掉没用的 shell 权限（已做，§8）；发布版检查器默认关（已做，§8）；限 `script-src`；终端和签名都要原生手势 | 现有渲染有没有可执行脚本的注入点？ |
| 6 | 系统和界面 | `osascript`、`screencapture`、剪贴板（`pbpaste`）、`docker -v $HOME` | 沙箱外命令清单清空；剪贴板和截屏列为读的通道 | 逐个命令在沙箱里试 |
| 7 | Hive 侧 | Hive 自己的 agent 和 Hive daemon 同用户；Host 用户的凭证有 Hive root | Hive 账户用专用凭证、放 agent 读不到的地方，泄露过的都要轮换；Hive 侧同样需要沙箱；Hive 拒绝转发它不认识的人专用路径 | agent 能用的凭证真的碰不到 Hive 吗？（用 dry-run 实测，不只看策略模拟） |

## 5. "人的证明"用什么形态（审查后改）

r1 说沙箱过了以后共享密钥（A）和签名（B）一样强。审查推翻了这一点：

- **A 的密钥就是签名用的密钥。** daemon 那份只要通过日志、报错、某个读接口泄露一次，就能永远伪造。B 里 daemon 只有公钥。
- **密钥在 webview 里的话，一次脚本注入就能复制走，修好注入之后仍能用。** 钥匙串或 Secure Enclave 里的密钥导不出来，攻击者只能在注入还在时让它签。
- **Touch ID（C）本身就是一把钥匙串密钥。** 选 C 就等于选了 B。

所以改为：**B，签名在 Rust 侧完成，前面先过一个原生手势（系统对话框显示"要做的事"，或 Touch ID），密钥永不进 webview；daemon 只存公钥，公钥放在 agent 改不了的位置。** A 只作为退路，前提是密钥不进 webview、不出现在任何 daemon 响应或日志里，并且 HMAC 绑定"操作 + 参数摘要 + 一次性随机数 + 过期时间"、拒绝重放；这几条都写成测试。

## 6. 步骤

### S0 判断能不能成立（约 2 小时，不改产品）

1. 读 CLI 的沙箱和权限文档，回答三个问题：
   - 能不能在允许写的目录里再禁止子路径、能不能禁止读；
   - `bypassPermissions` 下拒绝规则对文件工具生不生效；
   - project 目录里的 hooks 和 MCP 是不是一定在沙箱外跑，能不能改从别处读。
2. 枚举 daemon 里"改变 agent 能力"的路由和人专用路由（第一阶段登记表的完整性测试，范围扩到整个 `app.routes`），得到总数。
3. 用 agent 日常的凭证链做 dry-run（`ec2 … --dry-run`、对一个空 key 的 `s3api put-object`、`ssm describe-instance-information`），看它碰不碰得到 Hive 账户。

**S0 的判定：** 第 1 条任何一问答"不能"，第二阶段停（§7 第一行）。

### S1 沙箱和秘密的改造，加探测（需要改代码，开关默认关）

- **改造：**
  - 按 session 生成沙箱配置，支持按路径禁止读写；
  - `secrets.json`、Hive 密码、Hive 账户凭证移到 agent 读不到的地方；
  - MCP、hooks 配置移出 agent 可写目录；
  - 沙箱外命令清单清空。
- **探测环境：** 在临时 HOME、临时端口上另起一个实例（第一阶段用过的办法），不动用户的 daemon。
- **探测项：** 第 4 节第 1、2、3、4、6 行的每一项，用 Bash、文件工具和 MCP 三种方式各试一次。还要试经本机 HTTP 调第 4 行的路由：没有人的证明时必须被拒。
- **必须照常的：**
  - 在仓库和 worktree 里改代码、commit，跑 pytest、vitest 和 build；
  - `scripts.swarm_out` 派单、`knowledge find`、recall；
  - 用户日常的 AWS 工作。

  坏掉的每一项都要有不碰边界的修法。

### S2 webview（要打包后的 App）

检查器打不打得开；找脚本注入点（Markdown、HTML 预览、文件名、Hive 文字、run 卡片）。

**过的标准：** 没有任何 webview 脚本够得着进程执行、PTY 或文件写。找到注入点算 S1 不过，不是"选 C"的理由。

### S3 钥匙串和签名（选 B）

做一个最小的 Rust 命令：建钥匙串条目或 Secure Enclave 密钥，过原生手势后签名。然后测：
- agent 读不读得到：在沙箱内、外各试一次，记下是哪一层拒的；
- App 重新构建（cdhash 变了）以后怎样；
- App bundle 被换了以后怎样。

ad-hoc 签名下每次升级都要重新授权的话，就需要 Developer ID 签名。这是分发上的决定，单独问 XG。

### S4 Hive 侧和代价

- **Hive 侧：** 从 Hive 自己的 agent 试同样的探测；Hive 拒绝转发它不认识的人专用路径；轮换所有已经暴露过的密码和密钥。
- **代价：** 从 settle 记录和审批记录数出每天的人专用动作次数，乘以单次手势的时间。代价太高，就只给不可逆的动作加手势。

## 7. 结果怎么走

| 结果 | 决定 |
|---|---|
| S0 第 1 条有"不能" | 第二阶段停。产品安全说明写清楚"只有人能做"是减速带；§8 的两件独立的事照做。 |
| S0 过，S1 的改造代价太大（例如 MCP、hooks 配置改不了位置） | 同上；把 S1 能做的部分（秘密移出工作区、清空沙箱外命令）单独评估。 |
| S0–S4 都过 | 按 §5 的 B 做第二阶段；人专用的范围扩到所有改变 agent 能力的路由。 |

## 8. 和第二阶段无关、现在就值得做的两件事

1. **删掉 webview 的全部 shell 权限（已做）。** 一共四条：
   - `shell:allow-execute`：以任意参数运行 node / python / python3；
   - `shell:allow-spawn`：以任意参数启动 `python-backend` sidecar，而 `python-backend py -c <代码>` 能执行任意 Python（`backend/desktop_main.py` `_run_py`），所以它和上一条一样危险；
   - `shell:allow-kill`；
   - `shell:allow-open`。

   这四条前端都没有用到：打开链接和文件走 `plugin-opener`，后端由 Rust 用 `std::process::Command` 启动，终端用 App 自己的 `pty_*` 命令。今天任何一处脚本注入，都能借它们在沙箱外运行任意代码；删掉不影响任何功能。`desktop/src/__tests__/tauriCapabilities.test.ts` 防止它们被加回来。shell 插件本身（`lib.rs` 的初始化、`tauri.conf.json` 的配置、Cargo 和 npm 依赖）随后也删掉了。r2 里写的"sidecar 的 `shell:allow-spawn` 不动"是错的。
2. **发布版的检查器默认关、按需开（已做）。** `devtools` 特性保留，主窗口只在 debug 构建或带 `SWARMAI_DEBUG`（非空、非 `0`）启动时打开检查器（`desktop/src-tauri/src/lib.rs` `devtools_wanted`）。要排查时退出 App，再 `open --env SWARMAI_DEBUG=1 -a SwarmAI`；线上诊断（如 `diagFlags.ts` 的开关）照常可用。第二阶段做人签名时，签名命令还要求原生手势，不依赖检查器关着。

**要 XG 定：**
- ~~这两件事现在做吗？~~ 已做（§8）。
- 第二阶段要不要开始 S0？
- 如果 S0 显示 Hive 要用单独的账户，你自己的 Hive 账户愿意换吗？

## 9. 审查记录

r1 经过一次零上下文的 security 审查：4 条 CRITICAL、7 条 HIGH、6 条 MEDIUM、3 条 LOW。r2 全部吸收：

- **前提扩大：** 从"Bash 的沙箱"扩到七条通道；
- **新的事实：** settings 路由、工作区里的 MCP 和 hooks 配置、webview 的 shell 执行权限、文件工具不受沙箱管、`secrets.json` 的位置；
- **改正 A ≈ B 的结论**；
- **改正"Hive 侧不用验签"：** Hive 自己的 agent、版本差异、泄露过的密钥要轮换；
- **修正测试：** IAM 策略模拟不可信，改用 dry-run；钥匙串探测分沙箱内外；V1 按原写法跑不起来，因为沙箱配置是全局的、没有路径规则；
- **改正引用行号。**

审查没核实的：`/api/workspace` 的路径限制；CLI 沙箱的实际能力（S0 第 1 步）；`ps eww` 能不能看到同用户其他进程的环境变量。
