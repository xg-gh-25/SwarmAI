---
created: 2026-10-31
updated: 2026-10-31
title: "SwarmAI 账号：一个 AWS 账号，产品底层统一管理"
status: r4.4 — §5.3 改为 CLI 的 awsCredentialExport（实施第 2 步时发现）；认证方式按真实场景重排（§6.2），向导"先探测、永不挡在外面"（§6.1），Hive 最多 8 台；r4 已过审查（§11）；待 XG 定（§10）
related: docs/design/2026-10-31-human-proof-verification.md · docs/design/2026-10-31-swarm-out-ops-registry.md
---

# SwarmAI 账号：一个 AWS 账号，产品底层统一管理

## 1. 一句话

**XG 2026-10-31：** SwarmAI 只有一个 AWS 账号，Bedrock、Hive、云存储和整个产品生态都用它；每个用户安装时配好，
由产品底层管理。

**XG 2026-10-31（r3 的输入）：** 从用户的角度想安装向导和设置页：新装和升级、内部和外部用户。从 AWS 账号 ID 开始；
内部账号用 ADA 或 AWS SSO 认证，外部账号用 AWS SSO；安装只给这两种方式。Hive 默认开一台，数量可调，但至少一台。
然后把这些当产品来管。

**XG 2026-10-31（r4 的输入）：** Hive 要有上限，最多 8 台，防误操作。认证方式不局限于 SSO 和 ADA：外部用户普遍
怎么认证、内部除了 ADA 还有什么通用场景，一起考虑。整个流程要顺，尤其不能让用户因为向导直接被挡在外面。

今天的问题是两套互不相干的身份、十来处各自解析凭证（§2）。底层的解法是**一条账号记录 + 一个解析入口 + 一条交付
凭证的通道**（§5，r2 已定形）。r3 在上面加两层：用户在什么时刻、看到什么、做什么（§6）；产品怎么持续管理这个
账号和它的 Hive（§7）。

## 2. 用户和时刻

| | 内部用户（Amazon 员工） | 外部用户（自己或公司的 AWS 账号） |
|---|---|---|
| **第一次安装** | 先探测本机已有的凭证；有就一键用，没有就 ADA 或 SSO 登录 → 核实 → Hive | 先探测；有就一键用，没有就 AWS 登录或 SSO → 核实 → Hive |
| **升级到这个版本** | 老配置自动生成账号记录；一致就不打扰；不一致才问（§6.4） | 同左 |
| **之后（设置页）** | 一页"SwarmAI 账号"：状态、能力、Hive 数量（§6.5） | 同左 |

还有一种运行方式是 **Hive 本身**。今天 Hive 也走同一个向导（`OnboardingPage.tsx` 的 `isHive` 分支）；r4 里 Hive
模式（`SWARMAI_MODE=hive`）跳过 ②–⑤：daemon 启动时用实例角色自动建记录（§5.1），不做 Hive 数量对齐（§7.3）。

内部和外部的差别只在"推荐哪几种登录方式"和"权限从哪来"，流程本身一样（具体场景见 §6.2）：

| | 内部 | 外部 |
|---|---|---|
| 怎么判断 | `~/.midway` 或 `~/.ada` 存在 → 内部（今天的 `/system/auth-hint` 已经这样判断）；没有就默认外部，向导上一键切换 | 同左 |
| 推荐的登录方式 | ADA、AWS SSO | AWS 登录（控制台账号）、AWS SSO |
| 权限 | Isengard / Conduit 的角色（通常 Admin 就够） | 用户自己的 IAM 用户、角色或权限集；产品提供权限模板（§7.4） |

## 3. 现状（2026-10-31 读过，审查逐条复核）

**两套身份**
- **Bedrock 和本机其余 AWS 使用：** 不存账号，不存 profile；只有 `auth_method`（ada / sso / apikey / iam_role /
  bedrock_api_key）、`deployment_context` 和 `aws_region`（`core/app_config_manager.py:71-99`）。凭证全靠进程环境里
  的默认链；CLI 子进程继承 daemon 的环境，在 `_env_lock` 下改 `os.environ`（`core/claude_environment.py:206-256`）。
- **Hive：** `hive_accounts` 表一行一个账号（`database/sqlite.py:2935`），静态密钥明文存在 SQLite，或者存一个
  profile 名；`hive/provisioner.py:184-199` 的 `_get_session` 其余情况（包括密钥为空）悄悄退回默认链。
  `hive_instances.account_ref` 必填，约 10 处经 `_get_account` 读它。
- 两者没有关联。这台机器上两边恰好都落在 533267412361，是默认链凑出来的。

**各自解析凭证或区域的地方**

| 地方 | 现状 |
|---|---|
| CLI 子进程 | 继承 daemon 环境里的默认链 |
| `jobs/bedrock.py:153-191` | 默认链失败后自带 SSO 缓存兜底：名字带 "default" 的段排前面，取第一个带 `sso_account_id` 的账号，token 取任意 start URL 里最长寿的一个；账号和 token 可能对不上 |
| `jobs/executor.py:1685-1696` | 解析默认链后把密钥冻结注入子进程，删 `AWS_PROFILE`；区域用 `setdefault(…, "us-west-2")`，所以 daemon 自启动以来起没起过会话决定 job 用哪个区域；脚本 job 反而把 AWS 密钥去掉（`:1727-1740`） |
| 思考网关 `bedrock_thinking_gateway.py:314,359` | daemon 默认链重新签名（SigV4） |
| 凭证检查、就绪采样、设置页模型列表、`verify-auth`（`system.py:526`，在 `os.environ` 换值里建裸 client） | 各自默认链 |
| 语音 | Polly 裸 `boto3.client`（`voice_synthesize.py:69`）；Transcribe 用 `amazon_transcribe`（awscrt 自己的凭证链，`voice_transcribe.py:18,143`），区域取 `TRANSCRIBE_REGION` |
| eval | 走 `jobs.bedrock`，区域先读环境变量（`scripts/eval_runner.py:1326`） |
| Hive 侧 | 桥（`swarm_out_bridge.py:82`）、Hive 上的 CLI、`user_data.py:37` 和 SSM 更新脚本（`provisioner.py:1282`）里的 `aws s3 cp`：实例角色，区域取 IMDS |
| 独立脚本 | `skills/s_pollinate/.../polly.py:59` 在 daemon 外运行 |

**其他问题**
- 账号 ID 从不核对：Hive 的账号 ID 是手填的，verify 读了 STS 却不比（`routers/hive.py:331-346`），而它决定 S3 桶名
  （`provisioner.py:281-282`）。
- 区域有六个来源。
- 安装向导填的 ada 账号 / 角色被丢掉（`AuthConfigPanel.tsx:104-105`）；SSO profile 列出来了选不了。
- `SECRET_KEYS` 里的静态 AWS 密钥名没人用。


## 4. 目标和非目标

**目标**
1. **一条账号记录**（§5.1），替代 `auth_method` 作为唯一来源。
2. **一个解析入口**（§5.2）：daemon 里要 AWS 会话只从这里拿。
3. **一条交付通道**（§5.3）：daemon 起的每个子进程都通过同一个 SwarmAI profile 拿凭证，自动刷新，长会话不断。
4. **账号钉死**：每次解析出新凭证都核对账号，对不上就明确报错，绝不悄悄打到别的账号。
5. **认证方式覆盖真实场景，但界面上只推荐两种**（§6.2）：本机已有的凭证优先一键用；内部推荐 ADA、SSO，外部推荐
   AWS 登录、SSO；其余方式收在"更多方式"里；实在连不上 AWS 也能先只用对话进产品。
6. **Hive 是账号的一部分**：默认一台，数量可调，1 到 8 台；数量是用户定的期望值，产品负责对齐（§7.3）。
7. **向导不把人挡在外面**：每一个失败页都有出路（重试、换方式、先只用对话、稍后配置），每一步的等待都有明确的
   终点（§6.1）。
8. **升级不打扰**：老用户的配置自动接上，只有不一致时才问。
9. **按能力看就绪**：Bedrock、Hive、云存储、语音各自就绪与否、缺什么权限、怎么补。
10. **对每个用户都成立（P7）**：内部和外部；macOS、Linux、Windows。

**非目标**
- 不支持多个账号（XG 已定）。
- 不加花费上限（STEERING #2）。花费只显示估算，不拦。Hive 最多 8 台是 XG 定的防误操作上限，限的是台数不是钱。
- 不改 Hive 上的身份：仍是实例角色，在同一个账号里。
- 不动用户自己的 `~/.aws/config` 和 `~/.aws/credentials`。
- 不在 App 里替用户开通 IAM Identity Center 或改权限集：那是账号管理员的操作，产品只说明缺什么、给模板。

## 5. 底层机制

### 5.1 账号记录

`config.json` 里一个对象（非密钥部分）。SSO 的刷新令牌、客户端密钥和访问密钥（`keys` 来源）放**系统密钥库**（macOS Keychain、Windows
Credential Manager、Linux Secret Service；`backup_manager` 已经这样存它的 token），只有 daemon 读；系统没有密钥库
时退回 `secrets.json`（0600）并在设置页说明。静态密钥不再进 SQLite。

```
aws_account: {
  context: "internal" | "external",
  source: "ada" | "sso" | "login"                    # 推荐的登录方式（§6.2）
        | "profile" | "keys" | "instance_role",       # 本机已有的凭证、访问密钥、实例角色
  account_id: "533267412361",          # STS 核实，钉死
  ada: { role, provider: "isengard" | "conduit", bin },   # ada 的绝对路径在配置时记下
  sso: { start_url, sso_region, role },              # App 自己登录（§6.3）；令牌在系统密钥库
  login: { session },                                # AWS 登录（§6.3b）；会话在 SwarmAI 自己的配置和缓存里
  profile: "name",                                   # 用户已有的 profile（SAML 工具、aws-vault、老配置等）
  region: "us-east-1",                               # 主区域：Bedrock、云存储、语音、新 Hive 的默认区域
  caller_arn, verified_at,
  state: 见 §7.1
}
chat: {
  via: "aws" | "bedrock_key" | "anthropic"   # 对话走哪条路；默认 aws（用上面的账号调 Bedrock）
}
hive_fleet: {
  desired_count: 1,                    # 1..8
  instance_type: "m7g.xlarge",         # 新 Hive 的默认规格
  region: null                         # null = 用主区域
}
```

- `source` 替代 `auth_method`。过渡期由 `source` 推出 `auth_method` 给旧读者（`auth_remediation`、凭证横幅、
  会话启动前检查），第二步把这些读者改读 `source`，然后删掉 `auth_method`。
- **对话的路和 AWS 账号分开记**：`chat.via` 是 `bedrock_key` 或 `anthropic` 时，对话用那把 key（存系统密钥库），
  AWS 账号记录可以有也可以没有。没有时 Hive、云存储和语音标为"需要连接 AWS 账号"，一个按钮回到 ②；有时它们照常
  用账号。这样"Bedrock 暂时不通、先用 key 对话"不会丢掉已经连好的账号。r3 的 `bedrock_key` / `none` 来源改成这里。
- `instance_role`：Hive 上（`SWARMAI_MODE=hive`）由 daemon 启动时用 STS 和 IMDS 区域**自动建**。
- **`aws_account` 和 `hive_fleet` 不能经通用的 `PUT /api/settings` 写**（`routers/settings.py:35` 的
  `WRITABLE_KEYS` 把它们排除），只能经 §7.7 的专用路由写。`ada.bin` 只接受配置时找到的那个路径（属于用户、不在
  工作区里），否则 daemon 和每个 `credential_process` 都会去执行一个被改过的程序。

### 5.2 解析入口 `core/aws_account.py`

- `record()`：读记录；Hive 模式下没有就自动建。
- `credentials() -> 短期凭证`：按 `source` 解析：ada 调记录里的 `ada credentials print --account --role`；sso 用
  App 自己的 Identity Center 令牌调 `GetRoleCredentials`（§6.3）；login 调 AWS CLI 的
  `aws configure export-credentials --profile swarmai-login --format process`（指向 SwarmAI 自己的配置和缓存目录，
  由 CLI 自己续期，§6.3b）；profile 直接用；keys 读系统密钥库；instance_role
  读 IMDS。令牌的刷新只在 daemon 里做（§5.3），不会有两个进程同时刷新。**每次拿到一个新的
  access key ID 就做一次 STS 核对**，账号和记录不符就抛 `AccountMismatch`（带两个账号 ID）。结果缓存到过期前几分钟；
  同一时间只有一次解析（锁）。
- `session(region=None) -> boto3.Session`：用 `credentials()` 的可刷新凭证建会话，每个线程一个；**不在调用方线程里
  发网络请求**，核对在工作线程里做并缓存。默认主区域；Hive 实例的操作显式传实例区域。
- `cli_settings() -> dict`：给 CLI 的 `awsCredentialExport`（见 §5.3）；`boto_session()`：daemon 自己用的会话。
- `identity()`：缓存的 STS 结果，凭证检查和就绪采样读它。
- `capabilities(mode)`：桌面检查 Bedrock、EC2 / SSM / IAM / S3 / CloudFront（Hive 用）、Polly / Transcribe；
  Hive 模式只检查 Bedrock 和它自己那几个 S3 前缀（实例角色本来就只有这些权限）。

### 5.3 交付通道：CLI 的 `awsCredentialExport`，agent 的 shell 不动

子进程（CLI、job）都是长时间运行的，环境变量在启动后改不了，所以不往环境里塞短期凭证。改为：

- `python-backend aws-credentials` 是一个子命令：**向 daemon 要**凭证（本机回环接口，带 daemon 每次启动生成的
  token，`<data dir>/aws/daemon.json`，0600），daemon 调 §5.2 的 `credentials()`（同样核对账号），子命令按
  `credential_process` 格式输出短期凭证和过期时间。子命令自己不读令牌、不刷新：刷新只在 daemon 里，多个子进程
  不会互相把对方的刷新令牌作废，也不会同时写密钥库。daemon 不在时子命令返回明确错误。
- **CLI 用 Claude Code 的 `awsCredentialExport` 设置**（每个会话的 `--settings` 里），值就是这个子命令。CLI 在会话
  开始和凭证过期前五分钟调它，凭证出错时清缓存重取；配了它，CLI 不再走默认凭证链
  （[Claude Code 文档](https://code.claude.com/docs/en/amazon-bedrock)，本机实测：`AWS_PROFILE` 指向不存在的
  profile 时 CLI 照样经它调通 Bedrock，`--bare` 的 job 模式也一样）。
- **只有 CLI 自己的 AWS 调用用 SwarmAI 账号；agent 跑的命令的环境一个变量都不改**，用户自己的 profile 照常可用
  （例如 `AWS_PROFILE=ivt-beta aws …`）。r4 写的是给所有子进程设 `AWS_CONFIG_FILE` + `AWS_PROFILE=swarmai`，实施时
  发现它会让 agent 的命令看不到用户的 `~/.aws/config`，用户自己的 profile 全部失效，所以改成这样。agent 代用户
  做事时用用户自己的 AWS 配置，产品自己的功能（Bedrock、Hive、云存储、语音）用 SwarmAI 账号。
- 对话的路（`chat.via`）：`aws` 且有可解析的记录时才给 `awsCredentialExport`；`bedrock_key` / `anthropic`
  照今天的做法给对应的 key。没有可解析的记录（没找到 AWS 身份、本版本不支持的来源、Hive）时什么都不给，CLI 走
  默认凭证链，和今天一样（旧路径在新路径覆盖之前保留）。
- 思考网关校验 CLI 的签名时用同一份凭证（`aws_account.resolve`），两边的 key 一致。
- daemon 自己的 AWS 调用（job 的 Bedrock、语音、设置页的模型列表）用 `boto_session()`：凭证经 `resolve` 自动刷新；
  有记录但解析不了时报错，**绝不退回默认链**（那可能是另一个账号）。
- `<data dir>/aws/config` 里的 `swarmai` profile 仍然写着，`credential_process` 是同一个子命令，给明确想用
  SwarmAI 账号的工具用（`AWS_CONFIG_FILE=… AWS_PROFILE=swarmai`）；默认没有任何东西设它。
- 非 owner 的渠道会话（Slack 等）同样拿到 `awsCredentialExport`（它们的对话本来就走产品的 Bedrock），它只在
  CLI 内部用。**这类会话的限制由 CLI 执行，不靠文字匹配**（`prompt_builder.restricted_session_policy` /
  `restricted_tool_gate`）：
  - 没有 Bash。沙箱里的 Bash 实测仍能写 `~/.swarm-ai`、看到 daemon 的凭证环境变量、查钥匙串，读的黑名单也列不全
    本机所有密钥；
  - 会话跑在 `bypassPermissions`，CLI 在这个模式下从不调 `can_use_tool`（实测：会话目录外的文件 Read 照样成功，
    原来的目录限制一直没生效）。改为一个 PreToolUse hook，CLI 在任何模式、对子 agent 也会调：文件工具只放行
    会话目录和共享的 DDD 文档，相对路径按会话目录解析，`~` 开头的路径拒绝（CLI 会把它展开成主目录），Glob / Grep
    的路径模式不能是绝对路径或带 `..`，目录里配置 CLI 的文件（`.claude/`、`.mcp.json`、`CLAUDE.md`、`AGENTS.md`）
    不能写；WebFetch 只能访问公网地址（本机回环上的 daemon、局域网、云的元数据地址，以及解析到这些地址的公网
    域名都拒绝，解析不了、带用户名的 URL（两种 URL 解析器会读出不同的主机）也拒绝；检查和 CLI 自己解析之间 DNS
    答案变了的情况（DNS rebinding）由出口代理挡住，见下一条）；
  - 出口代理（`core/egress_proxy.py`）：这类会话的 CLI 所有对外连接都走 daemon 里的一个代理（`HTTPS_PROXY`，
    回环地址不走代理，会话取凭证要连回 daemon）。代理只接受 `CONNECT host:port`，自己解析主机，所有地址都是公网才连，
    而且连的就是它检查过的那个地址，CLI 选不了地址。实测：经代理 WebFetch example.com 正常；`127.0.0.1.nip.io`
    （公网域名，解析到本机）被代理拒绝。CLI 要自己登录（没有 SwarmAI 账号、也没有 API key，走 ADA / Midway 默认链，
    可能要连内网）时不走代理，这时会话没有 WebFetch；代理起不来时同样没有 WebFetch；只放行列出的其他工具和已按级别筛过的 MCP；其余工具和 hook 自己出错都拒绝；
  - MCP 是白名单：MCP 在会话的限制之外、带着 owner 自己的登录运行，非 owner 用到它就是以 owner 的身份做事。public
    用户只有渠道自己的工具；trusted 用户另外只有 owner 逐个共享的 MCP（MCP 条目里写 `"share_with_trusted": true`）；
    其他级别、以后新加的 MCP 默认都没有；提示词里"可以启用"的 MCP 列表也按同样的规则筛；
  - 不加载任何 setting source、关掉 skill 里的内联 shell：会话写进自己目录的文件不会变成 CLI 的配置或 hook；
  - 不给 add_dirs；对 SwarmAI 账号的 token 目录、`~/.aws`、ADA、Midway、SSH、`~/.config`、`~/.claude` 和密钥库
    加 `Read` 拒绝规则作为第二层（任何模式下都生效）。
  2026-10-31 用真 CLI 实测：目录外 Read（含子 agent 发起的）、`~/.zshrc`、`~/.swarm-ai` 下的 Grep、绝对路径 Glob、
  写 `.claude/settings.json` 都被拒，目录内读写正常，没有 Bash。
  `owner_command_denial` 的文字匹配仍在，现在只是提示。
- 用户自己的 `~/.aws` 一个字都不改。

### 5.4 消费方改走入口（按风险从低到高）

| 消费方 | 改成 | 删掉 |
|---|---|---|
| 设置页模型列表、`aws_credentials_configured` | `session()` / `identity()` | 各自的默认链 |
| 凭证检查、就绪采样、`verify-auth` | `identity()`；检查的就是实际要用的身份 | `verify-auth` 的 `os.environ` 换值 |
| `jobs/bedrock` 的所有调用方（summarization、scorer、CDF 抽取、eval 判官） | `session()` | SSO 缓存兜底 |
| `jobs/executor.py`（agent job） | `cli_settings()`（`--settings`），区域取主区域 | 冻结密钥、删 `AWS_PROFILE`、us-west-2 |
| CLI 子进程（`prompt_builder` 的 `--settings`） | `cli_settings()` | 依赖 daemon 环境碰巧对 |
| 思考网关 | `session()` | 默认链会话 |
| Polly | `session().client("polly")` | 裸 client、`TRANSCRIBE_REGION` |
| Transcribe（awscrt） | 给 `TranscribeStreamingClient` 一个从 `credentials()` 取值并会刷新的凭证解析器 | 它自己的凭证链 |
| eval | 区域取记录 | 先读环境变量 |
| 脚本 job、独立脚本、skill（如 pollinate 的 polly） | 不改：在 agent 的环境里以用户身份运行（§5.3） | — |
| Hive：`provisioner` 的 `_get_account` / `_get_session`（全部约 10 处）、`swarm_out._s3`、`routers/hive.py` verify | `session(region=实例区域)` | `hive_accounts` 的凭证字段、verify 里重复的会话代码 |
| Hive 侧：桥、Hive 上的 CLI | 记录自动建（`instance_role`），`boto_session()`；CLI 走实例角色 | 默认链 |
| Hive 侧 shell 里的 `aws s3 cp`（`user_data.py`、SSM 更新脚本） | 不改，实例角色 | —（列入完整性测试的例外，写明原因） |

### 5.5 Hive 账号表的迁移

迁移前备份 `hive_accounts`（0600，且备份里去掉明文密钥：密钥先挪进系统密钥库再备份），STEERING #20。

| 情况 | 处理 |
|---|---|
| 一行，STS 核实是 SwarmAI 账号 | 合并：实例的 `account_ref` 指向 SwarmAI 账号；密钥（如有）挪进系统密钥库 |
| 一行，是另一个账号 | 不合并。这个账号的凭证保留，**只用于这些实例的停止、终止、清理**，直到用户把实例迁走或销毁；设置页显示"这台 Hive 在另一个账号" |
| 多行 | 与 SwarmAI 账号相同的合并；其余按上一行 |
| 零行但有实例 | 实例按 SwarmAI 账号处理，第一次操作前核对实例确实在这个账号里，不在就提示 |
| 迁移时 STS 失败（断网、凭证过期） | 不迁移，记录标"未核实"，设置页提示，下次启动再试 |

表保留一个版本（只读），下个版本再删。

**第 3 步的实现**（`backend/hive/accounts.py`）：
- 一个判断决定模式：记录能解析、且有账号 ID，就是"SwarmAI 模式"；否则一切照旧（每行用自己的凭证，可以加账号）。
- SwarmAI 模式下有一行 `id='swarmai'`（`auth_method='swarmai'`，没有自己的凭证），会话取 `aws_account.boto_session`，
  钉在记录的账号上；这一行的账号 ID 和记录不一致时任何操作都拒绝，不会落到别的账号。这一行已经有 Hive 时不改它的
  账号 ID（换账号是 §7.5 的事）。
- 迁移在 daemon 启动时跑（只在 SwarmAI 模式，幂等）：逐行用这一行自己的凭证调 STS，是 SwarmAI 账号就把它的 Hive
  指到 `swarmai` 行、这一行标 `merged`；改之前把"哪一行、原来的认证方式、挪了哪些 Hive"追加到
  `<data dir>/aws/hive-accounts-migration.json`（0600，不含密钥）。行不删，`auth_config` 不动（所以也没有密钥要挪进
  密钥库）；STS 失败的行原样留着，下次启动再试，设置页对这一行点"Verify"也会再试一次。
- 另一个账号的行：它的凭证必须还能到达这一行记录的账号（每个 access key 用 STS 核对一次；没有自己凭证、走默认链的
  行，默认链后来可能指向别处），不是就什么都不做并说明；凭证只用于停止、清理、读（`instance_exists`、Swarm Out 显示它的问题）；启动、更新、改密码、
  Swarm Out 派单和回答、重试都拒绝并说明"这台 Hive 在另一个账号"。
- 账号够不着（另一个账号、登录过期、SwarmAI 账号断开）时：删除 Hive 或账号返回 409、什么都不删（否则表里的行没了，
  实例还在跑、还在计费）；停止、启动不会卡在 `stopping` / `starting`，转成 `error` 并写明原因。
- 不会把 Hive 挪到一个写着别的账号的 `swarmai` 行上（换过账号、那一行还有旧 Hive 时），否则这些 Hive 谁都操作不了。
  已合并的行不能删（留着以便撤销）；备份文件读不了时先改名留着，不覆盖。
- SwarmAI 账号断开时 `swarmai` 行显示为 `disconnected`：不能往里建 Hive，其他账号恢复原来的用法。
- 路由：SwarmAI 模式下不能加账号、不能删 `swarmai` 行，新 Hive 只建在 `swarmai` 行；`GET /accounts` 给每行带
  `role`（swarmai / other / merged / own）和 Hive 数。
- 最多 8 台在这一步就挡住：创建和重试在同一把机群锁里先数再写（计入规则同 §7.3），期望数量留到第 5 步。
- 界面：Hive 设置页和 Fleet 的账号页在 SwarmAI 模式下没有"加账号"，只列 SwarmAI 账号和还有 Hive 的其他账号；
  部署对话框直接显示 SwarmAI 账号；到 8 台"部署"置灰并写明。

### 5.6 现有记录怎么生成

第一次启动新版本时，从 `auth_method` 和实际在用的来源推出 `source`（ada：从 `~/.aws/config` 的默认段或
`ada profile print` 读账号和角色；sso：用户选过的 profile，记为 `profile` 来源，§6.4；apikey / bedrock_api_key：记成 `chat.via` 的 `anthropic` / `bedrock_key`，再看本机默认链能不能解析出 AWS 账号，
能就一起记上），用 STS 核实。
推不出来，或者 launchd 环境里找不到 ada：记录标"未核实"，**不猜**，设置页请用户确认。


## 6. 用户旅程

### 6.1 新装：安装向导

今天的向导是：系统检查 → 认证（唯一必需的一步）→ 恢复备份（有备份时）→ 渠道（可选）→ 完成
（`desktop/src/pages/OnboardingPage.tsx`）。r3 把"认证"换成"SwarmAI 账号"三步，在渠道前加"Hive"：

```
① 系统检查   ①b 恢复备份（有备份时）
② 账号 ID 和认证方式 ── ③ 登录 ── ④ 核实（账号钉死 + 能力检查）
⑤ Hive（默认 1 台）
⑥ 渠道（可选）  ⑦ 完成
```

恢复备份挪到账号之前：备份里带着 `hive_instances`、`hive_accounts` 和 `app_settings`
（`core/backup_manager.py:102-107`），放在账号之后会盖掉刚建的状态。恢复出来的 Hive 按升级处理（§6.4、§5.5）：
② 预填它们所在的账号，⑤ 显示"已有 N 台"，不再默认新建。Hive 模式跳过 ②–⑤（§2）。

**② 连接 AWS 账号：先探测，再问**

进入这一步时，App 分两段探测本机已有的 AWS 凭证。

**第一段：只读配置，不执行任何程序（毫秒级）。** 大多数 profile 的账号 ID 就写在配置里：
- ADA 的 `credential_process` 命令行里的 `--account`、`--role`、`--provider`；
- SSO profile 的 `sso_account_id`、`sso_role_name`；
- 登录会话的 `login_session`、assume-role 的 `role_arn` 里的账号 ID；
- ADA 自己的 profile（`ada profile print`，读 ADA 本地配置，今天的 `_probe_ada_details` 已经这样做）。

第一段一出结果页面就显示，每行标"验证中"。只有静态密钥的 profile 读不出账号 ID，那一行先写"需要验证"。

**第二段：在后台并行验证，结果逐行更新。** 只自动验证不会弹窗、不会写用户文件的几类：
- 静态密钥：调 STS；
- 命令就是探测到的那个 ada 的 `credential_process`：和今天的 ADA 探测是同一个程序，Midway 有效时不弹窗；
- SSO / 登录会话：缓存里的令牌还有效（离过期十分钟以上）才用它取一次身份，**不刷新**（刷新会改写用户的
  `~/.aws/sso/cache`、`~/.aws/login/cache`，还可能和用户自己的 CLI 抢同一个令牌）；
- 实例角色（在 EC2 或云桌面上时）。

其余 profile 不自动跑，那一行一个按钮由用户点：命令不是 ada 的 `credential_process`（aws-vault、1Password 之类，
可能弹 Touch ID、YubiKey、钥匙串，或者挂住）和带 `mfa_serial` 的 assume-role → "检测"；令牌过期的 SSO / 登录
profile → "登录"（走 App 内登录，§6.3、§6.3b）。

每个验证在**自己的子进程**里跑，同时最多几个，到时间上限就连子进程一起杀掉（防挂住，不是业务超时）；要输入的
地方一律拒绝而不是等待（没有终端时 botocore 默认会用 `getpass` 等输入）。一行出错或超时只影响那一行。账号和
身份都相同的几个 profile 合成一行（"2 个 profile"）。环境变量里的凭证不探测：daemon 由 launchd 或 App 启动，通常
没有用户 shell 里的变量；有的也多是会过期的临时凭证（§6.2 不支持）。

**在这台机器上实测（2026-10-31）：** `~/.aws/config` 有 7 个 profile、`~/.aws/credentials` 有 1 个静态密钥段。
- 第一段从配置里直接读出 6 个 profile 的账号和角色（5 个不同的账号，含一个 PowerUser）；
- 剩下 2 个：一个 `credential_process` 的程序不是 ada → "检测"；静态密钥段经 STS 验证约 3 秒，结果是密钥无效，
  那一行显示"密钥无效"；
- 第二段用 ada 验证一个 profile 约 8 秒，并行跑时整页约 8–10 秒验证完；
- 4 个 SSO 缓存令牌都已过期 → 不自动用。

今天的 `/system/auth-hint` 只给出 ADA 自己 profile 的那一个账号：`_probe_aws_profiles` 只列 SSO profile
（`routers/system.py:808`，这台机器上返回 `null`），ADA 的路径写死成 `~/.toolbox/bin/ada`（`routers/system.py:771`）。
所以另外 4 个账号今天的向导看不到，第 4 步要换成上面的两段探测，ADA 的路径按平台查找并记下。

探测结果决定这一页长什么样：

| 探测结果 | 页面 |
|---|---|
| 找到能用的凭证 | 列出"找到的 AWS 账号"，每行：账号 ID、身份（如 `Admin` 角色、IAM 用户名）、来源（ADA、profile 名）、验证状态（验证中、已验证、密钥无效、要检测、要登录）。默认选中最合适的已验证的一个（内部优先 ADA 自己的 profile，其次和它同账号的 profile）。一个按钮"用这个账号"；还在验证中的行也能选，选了就等它验证完再往下 |
| 只找到需要登录的 profile | 列出来，每行一个"登录"按钮（SSO profile 走 §6.3 的 App 内登录，读它的起始地址、区域、账号和角色） |
| 什么都没找到 | 账号 ID 输入框 + 推荐的两种登录方式（内部 ADA、SSO；外部 AWS 登录、SSO） |

不管哪种，下面都有两行：
- "更多方式"（展开）：用已有的 profile（手选）、粘贴访问密钥、实例角色；
- "先只用对话"：粘贴 Bedrock API key 或 Anthropic key，马上能用对话，Hive、云存储、语音以后连上 AWS 账号再开。

选中一个**探测到的 SSO profile** 时，记录不记成 `profile`，而是读出它的起始地址、区域、账号和角色，记成 `sso`，
之后由 App 自己的令牌续期（§6.3），不再和用户的 CLI 共用 `~/.aws/sso/cache`。选中探测到的**登录会话** profile 时
同理，在 SwarmAI 自己的登录配置里重新登录一次（§6.3b）。其余 profile（静态密钥、`credential_process`、assume-role）
记成 `profile`，由 daemon 按用户的配置解析。

账号 ID 是锚点：用户填了 ID，就只把这个账号的结果标成可选，别的账号灰掉并写明"不是你填的账号"；用户没填，
选中哪个就钉哪个。内部 / 外部按 §2 判断，显示为一行小字"Amazon 员工？切换"，只影响推荐哪两种方式。

**③ 登录**（只有没选到现成凭证时才有这一步）
- **ADA：** 账号 ID、角色（ADA 已配置时预填，否则默认 `Admin`）、提供方（Isengard 默认，可选 Conduit）。App 直接调
  `ada credentials print --account <ID> --role <角色> --provider <提供方>`（只读，不写任何凭证文件；和 §5.3 的
  `credential_process` 是同一个调用）。Midway 过期时显示"在终端运行 `mwinit -f`，完成后自动继续"，App 每几秒重试，
  成功就进下一步。其他失败各给一句话：角色不对或你没有这个角色（"改角色"）；账号不在这个提供方（"换提供方或改用
  SSO"）；找不到 ada（"安装 ada"）。
- **AWS SSO：** 起始地址（`https://xxx.awsapps.com/start`）和 SSO 区域（本机已有 sso 配置时预填，只读，不改它）。
  App 自己走设备授权（§6.3）：点"登录"→ 浏览器打开 AWS 登录页（验证码在 App 里同时显示）→ 用户登录并允许 →
  App 列出这个用户能进的账号和角色；填了账号 ID 就只看这个账号，只有一个角色就直接选中，多个就让用户选（优先
  显示名字带 Admin 的）。看不到填的账号 → "这次 SSO 登录看不到账号 X"，列出能看到的账号。
- **AWS 登录（外部推荐）：** 用 AWS 控制台的账号密码在浏览器里登录，拿短期凭证（§6.3b）。适合一个人一个账号、
  没有 Identity Center 的用户，也是 AWS 推荐用来替代长期访问密钥的方式。

②、③、④ 每个失败页都有同一组出路：重试、换一种方式、先只用对话、稍后配置；也都能返回上一步。

**④ 核实**
- STS `GetCallerIdentity` 的账号必须等于输入的账号 ID，否则停在这一步，说明两边各是哪个账号。
- 通过后记录写入，状态"已连接"，然后跑能力检查（§7.2），一行一个能力：Bedrock、Hive、云存储、语音。
- Bedrock 不可用时说明缺什么（模型访问没开、区域里没有该模型、权限缺哪个动作），给两条路：补好后"重新检查"，
  或者"先只用对话"（`chat.via` 换成 Bedrock API key 或 Anthropic key；账号记录保留，Hive、云存储照常用它；补好后
  在设置页切回）。其余能力缺权限只标出来，
  不挡。

**⑤ Hive**
- 默认"创建 1 台"，数量 1 到 8，规格默认 m7g.xlarge，区域默认主区域；按钮上写清后果："创建 1 台 Hive，
  约 $119/月，计入账号 533267412361"。
- 点了就提交创建，**不等它建完**（一台 Hive 要建 EC2、IAM 角色、S3、CloudFront，CloudFront 部署最长等 20 分钟，
  `hive/provisioner.py:1006`），向导直接往下走；建造进度在设置页和 Swarm Out 里看。
- 能力检查按**这一步选的区域**跑（区域一改就重跑）。没过（例如外部用户的权限集没有 EC2）时显示缺什么，按钮
  变成"补好权限后在设置里创建"，向导可以继续。这时 Hive 是一条待办（§7.1 的 Hive 状态），不是账号状态。
- 为什么不挡：本机对话只需要 Bedrock。为了一台云上的 Hive 把用户挡在整个产品外面，代价不对等。"至少一台"由
  §7.3 的期望数量来保证，而不是由向导卡住。

**"稍后配置"** 在 ②–④ 都有；走了它，进产品，对话不可用，设置页和凭证横幅显示"未连接"并一键回到 ②。"先只用
对话"的用户 ⑤ 显示"连接 AWS 账号后再建"，跳过。

**为什么这样不会把人挡在外面：** 最常见的情况（本机已经有能用的 AWS 凭证）一键就过；真没有凭证的人至少能用
Bedrock API key 或 Anthropic key 先用起来；所有失败都有出路；会挂住的调用都有时间上限。

### 6.2 真实场景和认证方式

按用户实际怎么拿到 AWS 凭证排（常见程度是判断，不是测量）。"界面"一列：**推荐** = ②里的卡片；**探测** = 本机有就
出现在"找到的 AWS 账号"里；**更多** = 收在"更多方式"里；**对话** = "先只用对话"。

**外部用户**

| 场景 | 典型用户 | 记为 | 界面 | 能用 Hive |
|---|---|---|---|---|
| IAM 用户的长期访问密钥，已在 `~/.aws/credentials` | 个人、小团队，最常见 | `profile` | 探测 | 能 |
| AWS 控制台账号登录（`aws login`，短期凭证） | 个人、一个账号、没有 Identity Center | `login` | 推荐 | 能 |
| IAM Identity Center（SSO） | 有 AWS Organizations 的公司 | `sso` | 推荐 | 能 |
| 公司 SSO（Okta、Entra 等）经 SAML 工具、aws-vault、Granted 生成的 profile | 中大型公司 | `profile` | 探测 / 更多 | 能 |
| 手上只有访问密钥，本机还没配 | 个人 | `keys` | 更多 | 能 |
| 在 EC2、云桌面上运行 | 工程师 | `instance_role` | 探测 | 看能力检查 |
| 只有 Bedrock API key | 想先试试的人 | `chat.via = bedrock_key` | 对话 | 不能（要再连 AWS 账号） |
| 只有 Anthropic key，没有 AWS | 不用 AWS 的人 | `chat.via = anthropic` | 对话 | 不能（要再连 AWS 账号） |

**内部用户（Amazon）**

| 场景 | 记为 | 界面 | 能用 Hive |
|---|---|---|---|
| ADA + Isengard 账号（最常见） | `ada` | 推荐 / 探测 | 能 |
| ADA + Conduit 账号 | `ada`（提供方 conduit） | 推荐（选提供方） | 能 |
| `~/.aws/config` 里已经有 ADA 的 `credential_process` profile | `ada`（从 profile 读出账号和角色；命令里的 ada 路径必须就是探测到的那个，否则记成 `profile`） | 探测（点"检测"） | 能 |
| 团队的 IAM Identity Center | `sso` | 推荐 | 能 |
| Cloud Desktop、开发 EC2 上的实例角色 | `instance_role` | 探测 | 看能力检查（这类角色常常没有 EC2、IAM 权限） |
| Isengard 账号上的 Bedrock API key | `chat.via = bedrock_key` | 对话 | 不能（要再连 AWS 账号） |

**不支持，写明原因：** 从 Isengard 或控制台复制粘贴的临时凭证（一小时左右就过期，过期后 SwarmAI 无法自己续，
用户会以为产品坏了）。

**收敛的方式：** 记录只有一种形状（§5.1），所有 AWS 来源都经同一个入口出凭证（§5.2），对话的路单独一个字段，
只是界面按场景分了层。新增一种
来源 = 加一个 `source` 值和它的解析函数，不动别的。

### 6.3 App 内的 SSO 登录

IAM Identity Center 的设备授权是公开 API，App 自带的 botocore 里就有（`sso-oidc` 的 `RegisterClient`、
`StartDeviceAuthorization`、`CreateToken`，`sso` 的 `ListAccounts`、`ListAccountRoles`、`GetRoleCredentials`），
所以不需要 AWS CLI。

- 注册客户端（`RegisterClient`，申请 `sso:account:access` 范围和刷新令牌）。客户端信息和令牌存系统密钥库
  （§5.1），不写 `~/.aws/sso/cache`，不和用户自己的 CLI 登录互相影响。客户端本身会过期（`clientSecretExpiresAt`），
  过期或遇到 `InvalidClientException` 时重新注册，不当成"需要重新登录"。
- 登录只由用户在 App 里点按钮发起；App 只打开主机是 AWS 登录域名的验证地址。
- 注意刷新令牌的范围：它能进这个用户在这个 Identity Center 里的**所有**账号和角色，不只是钉住的那个，所以它只放
  在系统密钥库、只由 daemon 读、不进备份、不进配置缓存。
- §5.2 的 `credentials()` 对 `sso` 来源：用令牌调 `GetRoleCredentials` 拿短期凭证；令牌快过期时用刷新令牌续；
  续不了就进入"需要重新登录"（§7.1），设置页和凭证横幅上一个按钮重新走设备授权。
- 令牌能活多久由 Identity Center 管理员设定（通常 8 小时到几天），产品不改它，只在过期前提示。

### 6.3b AWS 登录（`aws login`）

AWS 给控制台账号（IAM 用户或根用户）提供了浏览器登录拿短期凭证的方式，CLI 的命令是 `aws login`，SDK 用 profile
里的 `login_session` 自动续期。**登录和续期都交给 AWS CLI**：
- 登录用的 OAuth 客户端是 AWS 给 CLI 注册的，App 不冒用。
- App 自带的 botocore 虽然有续期代码（`botocore/credentials.py:2721` 的 `LoginProvider`），但它要 `awscrt` 里的
  `EC` 做签名，App 里的 `awscrt` 是 0.26.1，没有 `EC`（实测 `botocore.compat.EC` 为 `None`）；`amazon-transcribe`
  又把 `awscrt` 钉在 0.26.x，升上去会弄坏语音。所以 daemon 不在进程里续期，而是调
  `aws configure export-credentials`，CLI 用它自己带的依赖续期。

- App 用 SwarmAI 自己的配置文件和缓存目录跑登录和导出（`AWS_CONFIG_FILE=~/.swarm-ai/aws/login-config`、
  `AWS_LOGIN_CACHE_DIRECTORY=~/.swarm-ai/aws/login-cache`），先把 `[profile swarmai-login]` 和 `region` 写好再跑
  `aws login --profile swarmai-login`，免得 CLI 在没有终端时追问。不改用户的 `~/.aws`。
- 缓存目录 0700、文件 0600，不进备份；里面的刷新令牌由 CLI 管，放不进系统密钥库，设置页写明。
- 本机没有 AWS CLI，或版本太旧（本机现在是 2.24.10，没有这个命令）：这张卡写明"需要较新的 AWS CLI"，给安装链接，
  并提示可以先用 SSO 或"更多方式"。不让这张卡成为死路。
**已核实（2026-10-31，读 [AWS 文档](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html)
和 [aws-cli v2 源码](https://github.com/aws/aws-cli/tree/v2/awscli/customizations/login)）：**

| 前提 | 结论 | 依据 |
|---|---|---|
| CLI 版本 | 最低 2.32.0；本机 2.24.10 要更新 | 文档 Prerequisites |
| 谁能用 | 根用户、IAM 用户、经 IAM 联合登录的身份（Isengard 的控制台登录应属于这一类，未实测）；用 IAM Identity Center 的走 SSO | 文档 Prerequisites |
| 权限 | IAM 用户、角色要有托管策略 `SignInLocalDevelopmentAccess`（`AdministratorAccess` 的 `*` 应已包含，未实测）；根用户不需要 | 文档 Prerequisites |
| 自定义配置文件 | 认：`login_session` 写进会话的 `config_file`，即 `AWS_CONFIG_FILE` | `login.py` 的 `_update_profile_with_login_session` |
| 自定义缓存目录 | 认 `AWS_LOGIN_CACHE_DIRECTORY` | 文档 Cached Credentials；`get_login_token_cache_directory` |
| 导出给别的进程 | `aws configure export-credentials --profile <p> --format process` 是官方给出的用法，CLI 负责续期 | 文档 Sharing Login credentials as process credentials |
| 没有终端时会不会追问 | 区域：配置里有或传 `--region` 就不问；"覆盖已有会话"只在 profile 已绑别的会话时问；Agent Toolkit 提示在 stdin 不是终端时跳过 | `login.py` 的 `_resolve_region`、`accept_change_to_existing_profile_if_needed`；`agenttoolkit/hint.py` 的 `_is_eligible` |
| 会不会一直挂着 | 浏览器回调最多等 10 分钟，超时报错退出 | `sso/utils.py` 的 `_OVERALL_TIMEOUT` |
| 会话多久 | 每 15 分钟自动续一次，整段会话最长 12 小时（受该身份的会话时长限制），之后要重新 `aws login` | 文档 Run a command with your profile |

**对设计的影响：**
- 12 小时上限意味着 AWS 登录的用户大约每天要重新登录一次（ADA 的 Midway 约 20 小时，SSO 由管理员定）。卡片上写明
  "每次最长 12 小时"；过期按 §7.6 进"需要重新登录"，设置页一个按钮重新跑 `aws login`。
- App 调 `aws login --profile swarmai-login --region <主区域>`，配置文件里先写好这个 profile，就不会有任何追问。
- 浏览器打不开时用 `aws login --remote`（浏览器里拿到授权码，粘回 App）。
- **还没有端到端跑过**（需要 2.32 以上的 CLI 和一次真实的浏览器登录），列在第 1 步。

### 6.4 升级：老用户第一次启动新版本

升级不进向导。新版本第一次启动时，后台按 §5.6 从老配置生成记录并用 STS 核实；然后按结果分三种：

| 情况 | 用户看到什么 |
|---|---|
| 核实通过，Hive（如有）都在同一个账号 | 什么都不弹。设置页"SwarmAI 账号"直接显示结果；一次性的一行提示"SwarmAI 账号已接上：533267412361" |
| 核实通过，但：没有 Hive / 有 Hive 在另一个账号 | 设置页顶部一条待办，点开是一张确认卡，只问不一致的那一项（例："你还没有 Hive。创建 1 台，约 $119/月？"） |
| 推不出来或核实失败（ada 在 launchd 下找不到、登录过期、断网） | 记录标"未核实"；凭证横幅按来源提示重新登录（§7.6）；登录后自动重新核实，不需要用户重配 |

- **已有的 Hive 直接接管**：同账号的按 §5.5 合并，期望数量 = max(1, 同账号里计入数量的台数，§7.3)；另一个账号的
  Hive 单独列出，不计入。
- **SSO 老用户怎么推出来**：今天后端不存用户选的 profile（§3：列出来了选不了），所以升级时解析一次默认链，从
  `AWS_PROFILE` 或 `[default]` 段得到在用的 profile，记为 `profile` 来源；推不出来就给一张确认卡，让用户选
  profile 或改成 App 内登录。
- **没有 Hive 的老用户不自动建。** 创建 EC2 是花钱、离开本机的动作，用户没有在新版本里同意过；所以只提示，
  一键创建。
- **老用户不必重新登录**：ADA 和 profile 的老配置照常解析；SSO profile 的老配置照常用用户自己的 CLI 登录，
  设置页建议改成 App 内登录（改了以后不再依赖 CLI）。

### 6.5 设置页"SwarmAI 账号"

一页，替代 AI & Models 里的认证块和 Hive 页的账号块。用户来这里做的决定只有三个：**要不要重新登录**、**缺的
能力怎么补**、**要几台 Hive**。页面按这三个排，其余放进展开。

```
SwarmAI 账号  533267412361 · ADA (Admin) · us-east-1          ● 已连接
                                                              [重新核实]
登录      Midway 有效，凭证自动续期                          [更换账号]
能力      Bedrock ✓   云存储 ✓   语音 ✓   Hive ✓
          （有缺口时只显示缺的那一项和怎么补，例：
           Hive ✗ 缺 ec2:RunInstances — 把权限模板交给账号管理员 [复制模板]）
Hive      2 台（期望 2，1–8 台）    约 $238/月                [− ] 2 [+]
          hive-a  m7g.xlarge  us-east-1  运行中
          hive-b  m7g.xlarge  us-east-1  创建中 12 分钟
▸ 详细    调用身份 ARN · 核实时间 · 凭证来源 · 区域
```

- 状态只有一个突出的地方（右上角），颜色按 §7.1 的状态。
- 能力全部就绪时一行 ✓；有缺口时只展开缺口，每个缺口带"怎么补"。
- Hive 数量改小到不能再小（1）时减号置灰；要换掉最后一台用"替换"（先建新的、再删旧的）。
- Hive 那一行在账号状态旁边单独显示自己的状态（§7.1），不影响右上角的账号状态。
- **更换账号**：走一遍 ②–④。旧账号里有 Hive 时先说清后果（§7.5），用户选完才换。

## 7. 产品怎么管

### 7.1 状态

两个互相独立的状态：账号状态和 Hive 状态。一个用户可以同时"需要重新登录"和"少 1 台 Hive"，两者分开算、分开显示。

**账号状态**

| 状态 | 含义 | 谁转出去 |
|---|---|---|
| 未连接 | 没有记录（新装走了"稍后配置"） | 用户走 ②–④ |
| 已连接 | 记录核实过，凭证能解析 | — |
| 需要重新登录 | ADA：Midway 过期；SSO：令牌和刷新令牌都过期；AWS 登录：会话过期（CLI 报要重新登录）；profile：用户自己的凭证过期 | 用户登录后自动回到"已连接" |
| 账号不符 | 解析出的凭证不是记录里的账号（例如 ada 账号被别处改了） | 用户在设置页确认：改回，或更换账号 |
| 未核实 | 升级时推不出来，或 STS 暂时调不通 | 下次成功解析时自动核实 |

对话的路另显示一行：走账号的 Bedrock，还是"只用对话"的 key。没有账号记录、只用 key 对话时，账号状态是"未连接"，
对话照常。

**Hive 状态**（Hive 模式下没有）

| 状态 | 含义 |
|---|---|
| 就绪 | 计入数量的 Hive ≥ 期望数量 |
| 少 N 台 | 计入数量的 Hive < 期望数量（§7.3） |
| 不能建 | 能力检查没过（缺权限、没有默认 VPC），写明缺什么 |

- 两个状态都由 `core/aws_account.py` 算，`GET /api/aws-account` 给前端；设置页、凭证横幅、Swarm Out、Need-You
  读的是同一份，不各自判断。
- 账号状态的"需要重新登录""账号不符"进凭证横幅和 Need-You（用户当前的窗口里）。
- Hive 状态只是一条待办：显示在设置页和 Swarm Out，进 Need-You 一次；用户点"以后再说"后 Need-You 不再提醒，
  设置页仍显示。期望数量仍是至少 1，"以后再说"不改它。

### 7.2 能力检查

一次检查一个能力，结果记在记录里，设置页显示；账号核实、更换、登录恢复后各跑一次，用户也可以手动跑。

| 能力 | 检查什么 | 缺了怎么说 |
|---|---|---|
| Bedrock | 用默认模型发一个最小请求 | 模型访问没开 → 给控制台链接；区域里没有 → 建议区域；权限 → 缺的动作 |
| Hive | 能不能**建**，不是能不能读：`RunInstances`、`CreateSecurityGroup` 用 `DryRun=True`；IAM、S3、CloudFront 的创建动作用 `iam:SimulatePrincipalPolicy`（模拟本身被拒时标"无法确认"，不标通过）。今天的 verify 只调只读 API（`routers/hive.py:353-389`），只读权限也能通过，然后在创建到一半时失败。默认 VPC 今天已查（警告），改为在所选区域查、没有就算缺 | 缺的动作；没有默认 VPC → 说明，并提供"创建默认 VPC"（用户点了才做） |
| 云存储 | 能建或能读写 SwarmAI 的桶（还没有 Hive 时桶不存在，查"能建"）。今天桶名只用账号 ID 后 4 位（`swarmai-hive-<后4位>-<区域>`，`provisioner.py:281-282`），在全局命名空间里会撞；新桶改用完整账号 ID，老桶照旧，S3 调用都带 `ExpectedBucketOwner` | 缺的动作 |
| 语音 | Polly、Transcribe 各一个最小调用 | 缺的动作；不挡任何东西 |

Hive 模式（`SWARMAI_MODE=hive`）只检查 Bedrock 和它自己那几个 S3 前缀。要检查的动作和 §7.4 的权限模板从同一份
动作清单生成。

### 7.3 Hive 数量：期望值和对齐

- `hive_fleet.desired_count` 是用户定的（向导、设置页），1 到 8。
- **最多 8 台**（XG 定，防误操作）。所有会让 Hive 变多的路由——改期望数量、`POST /api/hive/instances`
  （`routers/hive.py:446-486`）、重试（`routers/hive.py:651-678`）——都在同一把机群锁里**先数再插入（或改状态）**，
  数和写之间没有别的 await；今天 `create_instance` 的检查和插入在两个分开的数据库块里，中间有 await，照这样加检查
  并发两个请求能一起过。规则：
  - 创建：计入数量 + 1 ≤ 8；
  - 重试：不算这一行自己的计入数量 + 1 ≤ 8（出错但 EC2 还在的那一行本来就计入，不能因此拒绝重试它）；
  - 替换：新旧一对算一台，所以 8 台时也能替换；
  - 已经超过 8 台（老用户、恢复备份）：不终止任何一台，期望数量记成 8，显示"已有 N 台，超过上限 8，只能减少"，
    上限只挡增加。
  界面上加号到 8 置灰，旁边写"最多 8 台"。
- **对齐**：daemon 定期比较期望数量和这台 SwarmAI 记录的 Hive（`hive_instances`）。今天没有任何东西把 EC2 的实际
  状态同步回这张表，所以对齐要按实例所在的账号和区域调 `describe_instances`，把结果写回表；查不到（断网、
  权限）当作还在，和 `_ec2_present`（`provisioner.py:1717`）一样。
- **计入数量的是 EC2 还在的 Hive**：有 `ec2_instance_id` 且 EC2 还在，不管表里的状态是什么（停止、启动、更新失败
  时表里会写 `error`，但实例还在、还在计费，`provisioner.py:1105-1109`）；正在建的（还没有 EC2 的 `pending` 行）
  也算。不算的只有 `deleted`，和没有 EC2 的 `error`（创建失败）。
- **少了**（用户在设置页调大、创建失败、在 AWS 控制台里被删掉）：Hive 状态"少 N 台"，设置页和 Swarm Out 一个
  按钮"创建"。用户调大数量本身就是同意，直接创建；**别的原因少了不自动补**：在控制台里删掉可能是用户故意的，
  而重新创建要花钱。
- **创建只按差额，且在一把锁里**：改期望数量、创建、对齐都拿同一把 Hive 机群锁；锁里先算"期望 − 计入"，插入
  这么多 `pending` 行并写期望数量（一个事务），再开始建。重复请求、连点、轮询都只会得到差额，不会多建。
- **替换**：新旧两台记成一对，对齐期间不把多出来的那台当"多了"。
- **多了**（用户调小）：让用户选删哪台；不能删到 0。
- **最后一台**：不能删除，只能替换（先建好新的，再终止旧的）。
- 花费：每台按规格显示月度估算，数量旁边显示合计；不设花费上限。

### 7.4 外部用户的权限模板

产品附一份 IAM 权限模板（JSON）。能力检查报缺口时，"复制模板"给的就是这份；用户（或账号管理员）在 Identity
Center 里把它挂到权限集上。模板和能力检查从同一份动作清单生成。只按名字前缀限 IAM 和标签是不够的：能建
`SwarmAI-*` 角色并给它挂策略，就能给自己造一个管理员角色；能随便打标签，就能把任何实例标成 SwarmAI 的。所以：

| 服务 | 限法 |
|---|---|
| Bedrock、Polly、Transcribe | 调用动作 |
| IAM | 建角色必须带 SwarmAI 的权限边界（`iam:PermissionsBoundary`）；挂策略只允许 `AmazonSSMManagedInstanceCore`（`iam:PolicyARN`）；不给 `UpdateAssumeRolePolicy`；`PassRole` 只允许传给 EC2（`iam:PassedToService`）；资源名按 provisioner 真实的命名（`SwarmAI-Hive-<name>`，`provisioner.py:380,408`） |
| EC2 | 建资源时就打标签（`TagSpecifications`），并加固定的 `swarmai:managed` 标签；`CreateTags` 只在创建动作里允许（`ec2:CreateAction`）；`RunInstances` 按资源类型分开写；停止、启动、终止限 `swarmai:managed` 标签 |
| SSM | `SendCommand` 限 `swarmai:managed` 标签的实例和 SwarmAI 用的文档 |
| S3 | `swarmai-hive-*` 桶 |
| CloudFront | 分发管理不能按资源限，模板里写明这一点 |

这要求 provisioner 改成建时打标签（今天是单独调 `CreateTags`，`provisioner.py:517`），列入第 5 步。

### 7.5 更换账号和旧账号里的 Hive

更换账号前，如果旧账号里有 Hive，确认卡上让用户选：
- **终止旧 Hive**：用旧凭证终止并清理这台 SwarmAI 在 `hive_instances` 里记录的、并在旧账号里核对过的资源（EC2、
  CloudFront、IAM 角色、这几台 Hive 自己的前缀），**不按标签或前缀扫**：同一个账号里可能还有同事的 SwarmAI，
  桶是共用的。全部完成后再切换；中途失败（CloudFront 可能要 20 分钟或失败）就不切换，保留旧凭证，按"先保留"处理；
- **先保留**：按 §5.5 的"另一个账号"处理，设置页一直提示。旧凭证的解析路径在代码里只接受停止、终止、清理三种
  操作，且只对记录里的实例 ID。

新账号里没有 Hive 时，按 §7.3 显示"少 1 台"，一键创建。

### 7.6 登录过期和长时间运行的会话

- 子进程通过 §5.3 的 profile 拿凭证，每次过期前自己再取；登录有效期内长会话不断。
- 登录过期（Midway 约 20 小时；SSO 由管理员设定）时，`aws-credentials` 子命令返回明确的错误，CLI 的调用失败；
  用户重新登录后，下一次取凭证就恢复，不需要重启会话或 daemon。
- 凭证横幅今天不读认证方式，读的是 `/auth-hint` 的环境猜测（`CredentialBanner.tsx:97-106`），文案是
  "`aws sso login`"、"`ada credentials update`"（`CredentialBanner.tsx:60-62`、`core/auth_remediation.py:41-47`），
  按钮打开 AI & Models 页。r3 改为：横幅和 `auth_remediation` 读 `GET /api/aws-account` 的状态和来源；App 内 SSO
  给"重新登录"按钮（App 自己走设备授权，`aws sso login` 刷新不了 App 的令牌）；ADA 给 `mwinit -f`；老的 profile
  来源给 `aws sso login --profile <名字>`；按钮打开"SwarmAI 账号"页。列入第 2 步的读者迁移。
- 状态转成"需要重新登录"并进 Need-You。后台 job 遇到过期，记失败原因，不重试到天亮。

### 7.7 谁能做这些操作

这些操作花钱、离开本机或不可撤销，和今天的 Hive 创建、删除一样只由人做（`core/swarm_out_ops.py` 里 `hive.create`
是 `off_machine`、`hive.delete` 是 `irreversible`，调用方都是人）。新路由放在 `/api/hive/...` 和 `/api/aws-account/...`
下，全部登记进这张表，`/api/aws-account` 前缀加进表的覆盖测试：

| 操作 | 调用方 | 分量 |
|---|---|---|
| 读账号状态、能力、Hive 状态 | 人和 agent | read |
| 重新核实、跑能力检查 | 人和 agent | read |
| 发起 SSO 登录、AWS 登录、ADA 登录 | 人 | change |
| 自动探测本机凭证 | 人和 agent | read（只做 §6.1 ② 里不弹窗、不写文件的那部分） |
| 逐行"检测"一个 profile | 人 | change（会执行用户 profile 里的程序，可能弹窗） |
| 更换账号、改认证方式或角色 | 人 | change |
| 改 Hive 期望数量（调大会创建） | 人 | off_machine |
| 替换 Hive、终止旧账号里的 Hive | 人 | irreversible |
| 创建默认 VPC | 人 | off_machine |

agent 照常按"代我做事"的授权工作，读得到这些状态，需要时告诉用户该做什么；花钱和不可撤销的这几步由人点。今天
这张表在命令行一侧是减速带不是边界（agent 和 daemon 是同一个系统用户，表的说明里写着）；把它变成边界是
`2026-10-31-human-proof-verification.md` 的事，本设计不重复做。不加花费上限（STEERING #2）。

## 8. 会让它失败的事

- **`awsCredentialExport` 失败时 CLI 退回它自己的默认凭证链（2026-10-31 实测）：** 导出命令非零退出时，CLI 不报错，
  改用默认链（`AWS_PROFILE` / `default`）。所以 CLI 上的钉死不是绝对的。补位：会话启动前的检查（`session_unit`）对
  过期、账号不符、设置坏了直接拦下；桌面会话经思考网关，网关只认钉住的 key；readiness 横幅提示用户。没有补位的是
  渠道会话和 job 中途凭证失效的那段时间，它们这时用用户自己的默认 profile（多数用户就是同一个账号）。试过让导出
  命令失败时返回一个无效凭证来强制失败：CLI 会带着它重试一百多秒不返回，比退回默认链更糟，没有采用。

- **CLI 不认 `AWS_CONFIG_FILE` + `credential_process`：** 第 1 步先验证 CLI（Claude Code 用的是 JS 版 AWS SDK）和
  awscrt 都按这个 profile 取凭证、过期自己刷新。不认就退到 daemon 提供的容器凭证端点
  （`AWS_CONTAINER_CREDENTIALS_FULL_URI` + 授权 token），同样可刷新。
- **SSO 设备授权：** 要验证 Identity Center 给 App 注册的客户端发刷新令牌（AWS CLI v2 的 `sso_session` 就是这样
  做的）。不给的话，令牌到期就要重新登录，功能不变，只是登录更频繁。
- **外部单账号用户没有 Identity Center 组织实例：** 只在单个账号里开的 Identity Center（账号实例）不能给 AWS
  账号分配权限集（[AWS 文档](https://docs.aws.amazon.com/singlesignon/latest/userguide/account-instances-identity-center.html)），
  这类用户要先开 AWS Organizations 才能用 SSO。见 §10 第 1 条。
- **探测会执行用户配置里的程序：** 所以自动探测不碰 `credential_process`、MFA 和需要刷新的 profile，这些由用户
  逐行点"检测"（§6.1 ②）；每个探测在可杀的子进程里，到时间上限连子进程杀掉。
- **`aws login` 依赖 AWS CLI，且不能在 daemon 进程里续期**（`awscrt` 版本冲突，§6.3b）：没有 CLI 或核实不过时，
  这张卡不进推荐，不是死路。
- **用户自己的 profile 背后是 SSO 或登录会话时**，daemon 和用户的 CLI 会刷新同一份缓存。所以探测到这类 profile
  时改记成 App 自己的 `sso` / `login`（§6.1 ②）；用户手选这类 profile 记成 `profile` 时，接受这个竞争，设置页提示。
- **钉的是账号不是角色：** ada 换角色账号不变，权限缺口由能力检查报。
- **断网：** 核对结果缓存；断网时用上次结果并标"未核实"，不阻塞。钉死是防打错账号，不是安全边界。
- **ada 在 launchd 下找不到：** 配置时记绝对路径。
- **账号没有默认 VPC：** Hive 创建今天依赖默认 VPC；能力检查先报，不让用户在创建到一半时才失败。

## 9. 分步和验证计划

**分步（旧路径在新路径通过测试前保留）**
1. 先验证 §8 前两条，并在一台装了 AWS CLI 2.32+ 的机器上把 AWS 登录端到端跑一次（§6.3b）。然后 `core/aws_account.py`、记录、§5.6 生成、`aws-credentials` 子命令、
   `~/.swarm-ai/aws/config`、`GET /api/aws-account`（状态 + 能力）、设置页"SwarmAI 账号"（只看）。
2. 本机消费方改走入口（§5.4 前十行），`auth_method` 的读者改读 `source`，删 `jobs/bedrock` 兜底和 executor 冻结密钥。
   CLI 经 `awsCredentialExport` 拿凭证，agent 的环境不动（§5.3）；无法解析记录时各消费方走原来的路。
3. Hive：消费方改走入口，Hive 模式自动建记录，§5.5 迁移，Hive 设置页去掉账号块。
4. 向导 ②–④ 和设置页的登录、更换账号：探测、ADA（Isengard、Conduit）、App 内 SSO 登录（§6.3）、AWS 登录
   （§6.3b）、更多方式、先只用对话。
5. Hive 数量：`hive_fleet`、向导 ⑤、设置页数量、对齐（§7.3）、权限模板（§7.4）和 provisioner 建时打标签、
   新桶名、升级确认卡（§6.4）。

**最终测试集（不变量）**
1. **完整性：** 对 `backend/` 做 AST 检查，import `boto3` / `botocore` / `aiobotocore` / `amazon_transcribe` 的模块只能是
   `core/aws_account.py` 和一份写明原因的例外清单；区域字面量和 `AWS_REGION` / `TRANSCRIBE_REGION` 的读取同样只在入口里。
2. **钉死：** 解析出的凭证账号和记录不符时抛错、状态"账号不符"，不退回默认链；access key ID 变了就重新核对。
3. **交付：** `cli_settings()` 在哪些情况下给、哪些不给；agent 的环境不变；真实冒烟：CLI 实际签名用的 key 属于记录的账号。
4. **迁移和升级：** §5.5 每种情况；§6.4 三种情况各显示什么；没有 Hive 的老用户不会被自动建。
5. **向导：** 输入的账号 ID 和 STS 不符时停在 ④；探测到的凭证一键可用，不是填的账号的灰掉；每个失败页都有
   重试、换方式、先只用对话、稍后配置；自动探测不执行 `credential_process`、不刷新、不写 `~/.aws`；一个挂住的
   `credential_process` 或要 MFA 的 profile 点"检测"后在时间上限内结束、不留子进程；Bedrock 不可用时能"先只用
   对话"进产品；Hive 能力不够时向导仍能完成。
6. **SSO：** 设备授权、看不到输入账号时的报错、令牌续期、续不了进"需要重新登录"、重新登录后恢复；客户端过期后
   重新注册；令牌不在 `config.json` / `secrets.json` / 备份里（有系统密钥库时）；AWS 登录的缓存目录 0700、不进备份。
   **对话的路：** `chat.via` 三种值下 `cli_settings()` 给什么；只用 key 对话时有账号记录的 Hive 照常。
7. **Hive 数量：** 不能删到 0，不能超过 8（期望数量、直接创建、重试三条路由都挡；7 台时并发 N 个创建请求只成功
   一个；第 8 台出错后能重试；8 台时能替换；已有 10 台的老用户不被终止，只能减少）；调大直接创建；别的原因少了只提示不创建；最后一台只能替换；表里 `error` 但 EC2 还在
   的计入；同一次调大并发两个请求只建差额；替换期间不报"多了"。
10. **谁能做：** §7.7 的每个路由都在操作表里，调用方和分量如表；`PUT /api/settings` 写 `aws_account` / `hive_fleet`
    不生效；`ada.bin` 改成别的路径被拒。
11. **权限模板挡得住提权：** 用 IAM 策略模拟（离线的策略求值或 `SimulateCustomPolicy`）验证：建不带边界的角色、
    挂别的策略、改信任策略、给非 SwarmAI 实例打标签、对非 SwarmAI 实例 `SendCommand` 都被拒；SwarmAI 自己的创建
    流程的每个动作都允许。
12. **恢复备份：** 恢复出来的 Hive 按升级接管，不默认新建。
8. **Hive 模式：** 自动建记录；向导跳过 ②–⑤；不做数量对齐；桥、Bedrock、CLI 照常；就绪检查不报 Hive 本来就没有
   的权限。
9. **权限模板和能力检查同源：** 从同一份动作清单生成，改一边另一边的测试变红。

**审查：** r3 已过一次零上下文审查（§11）。实现后每步 security + correctness 各一轮。

## 10. 要 XG 定

1. **界面分层**（§6.1 ②、§6.2）：探测到的凭证优先一键用；推荐内部 ADA、SSO，外部 AWS 登录、SSO；访问密钥、手选
   profile、实例角色收在"更多方式"；Bedrock API key、Anthropic key 放在"先只用对话"。推荐这样。
2. **AWS 登录先靠 AWS CLI**（§6.3b）：登录和续期都交给 CLI（daemon 里的 `awscrt` 和语音依赖冲突，不能自己续）；
   CLI 低于 2.32 时卡片上写明并给更新链接和别的路；会话最长 12 小时，每天大约登录一次。前提已按文档和源码核实，
   端到端跑通后放进推荐。推荐这样。
3. **Hive 不挡向导**：推荐不挡；Hive 状态是待办，可以"以后再说"，期望数量不降到 0（§7.1）。
4. **Hive 花费确认**：按钮上写清台数、月度估算和账号，点了就是同意。推荐这样。
5. **没有 Hive 的老用户**：只提示、一键创建，不自动建。推荐这样。
6. **"至少一台"怎么算**：已停止的也算。推荐这样。
7. r2 留下的：一个主区域、Hive 保留自己的区域；另一个账号里的 Hive 保留旧凭证只用于停止和终止；实施方式
   （R1，跨模块、改数据形状、有迁移）：直接改，还是走 pipeline。

## 11. 审查记录

**向导第二步的 mockup：** `Knowledge/Mockups/2026-10-31-aws-account-wizard-step2.html`（SwarmWS 里，七个状态：
找到账号、外部没找到、内部没找到、Midway 过期、ADA 角色不对、SSO 设备授权、先只用对话）。

**r1 → r2：** 一次零上下文 correctness 审查，5 HIGH、12 MEDIUM、3 LOW，全部吸收。
- **交付方式改了：** r1 的"注入短期凭证、过期前刷新"做不到（进程起来后环境改不了），改为 SwarmAI 自己的
  `credential_process` profile（§5.3）；ada 也不再写会过期的静态凭证。
- **补了漏掉的消费方：** Transcribe（awscrt）、Polly 裸 client、桥、脚本 job、`verify-auth`、eval 区域、独立 skill。
- **记录：** `source` 替代 `auth_method`；`bedrock_key` / `none` 没有账号 ID；Hive 模式自动建。
- **迁移：** 另一个账号的 Hive 仍能停止和终止；多行、零行、STS 失败；备份不带明文密钥；`_get_account` 全部替换。
- **钉死：** 按 access key 变化核对；不在调用方线程里发网络请求；解析加锁。
- **测试：** 完整性测试改成 AST 检查 import，带写明原因的例外清单；交付加真实冒烟。

**r2 → r3：** XG 给出用户旅程的方向（§1）。新增 §2 用户和时刻、§6 用户旅程、§7 产品怎么管；记录加 `context`、
`sso` 改为 App 内登录、加 `hive_fleet`；新装的认证方式收成两种，其余只为升级保留；分步加第 5 步。

**r3 → r3.1：** 一次零上下文审查，correctness 3 HIGH、9 MEDIUM、3 LOW，security 4 HIGH、4 MEDIUM、3 LOW，逐条对照
代码核实后全部吸收：
- **谁能做（§7.7、§5.1）：** 新路由登记进操作表、只由人做；`aws_account` / `hive_fleet` 不能经通用设置路由写；
  `ada.bin` 只认配置时的路径。
- **Hive 数量（§7.3）：** 按 EC2 是否还在计数（表里的 `error` 可能是活着在计费的实例）；对齐时查 EC2 写回表；
  创建按差额、在锁里；替换成对。
- **状态（§7.1）：** 账号状态和 Hive 状态分开；Hive 是待办，可以"以后再说"。
- **能力检查（§7.2）：** 查"能不能建"（DryRun、策略模拟），按所选区域；云存储在还没有 Hive 时查"能建"；新桶名
  用完整账号 ID。
- **权限模板（§7.4）：** 权限边界、限定可挂的策略、`PassedToService`、建时打标签、`swarmai:managed`；按 provisioner
  真实命名；加提权测试。
- **令牌（§5.1、§5.3、§6.3）：** 刷新令牌放系统密钥库、只由 daemon 读和刷新；子命令向 daemon 要凭证；客户端过期
  重新注册；只打开 AWS 登录域名。
- **旅程（§2、§6）：** Hive 今天也走向导，Hive 模式跳过 ②–⑤；恢复备份挪到账号前；ADA 失败分类、预填角色、③④ 可
  返回；SSO 老用户从默认链推出 profile；外部单账号用户和 §10 第 1 条挂钩；凭证横幅改读账号状态。
- **换账号清理（§7.5）：** 只删记录里的资源，不按前缀扫；中途失败不切换。

已知、不在本设计范围：今天的备份把 `hive_accounts` 整表导出（`core/backup_manager.py:106`），老配置里明文的静态
密钥会进备份；§5.5 的迁移把密钥挪出这张表以后不再有。

**r3.1 → r4：** XG 给出三点（§1）：Hive 最多 8 台；认证方式按外部、内部的真实场景考虑；不能让向导把人挡在外面。
- §6.2 按场景列出外部 8 种、内部 6 种拿凭证的方式，各自记成哪个 `source`、放在界面哪一层、能不能用 Hive。
- 向导 ② 改成"先探测，再问"：本机已有的凭证一键可用。
- 新来源 `login`（AWS 登录，§6.3b）；ADA 加 Conduit；"只用对话"的来源改名 `anthropic`。
- 每个失败页都有同一组出路，Bedrock 不可用也能先只用对话进产品。
- Hive 1 到 8 台，两条创建路由都挡。

**r4 → r4.1：** 一次零上下文审查（security + correctness），3 HIGH、7 MEDIUM、4 LOW，核实后全部吸收：
- **AWS 登录在 daemon 里续不了期**：`LoginProvider` 要 `awscrt` 的 `EC`，App 里的 0.26.1 没有（实测），语音的依赖又钉住
  这个版本。改为登录和续期都交给 AWS CLI（`export-credentials`），核实不过就不进推荐。
- **探测不是只读、会挂住**：自动探测只做不弹窗、不写文件的部分（静态密钥、ADA、有效的缓存令牌不刷新、实例角色）；
  `credential_process`、MFA、要刷新的 profile 由用户逐行点"检测"；每个探测在可杀的子进程里；不探测环境变量。
- **8 台上限能被并发绕过**：三条会变多的路由在一把锁里先数再写；重试不算自己；替换算一台；已超过 8 台的只能减。
- **对话的路和 AWS 账号分开**（`chat.via`）：只用 key 对话不会丢掉账号；`child_env()` 按 `chat.via` 给变量。
- 探测到的 SSO / 登录 profile 改记成 App 自己的 `sso` / `login`，避免和用户的 CLI 抢缓存；AWS 登录的缓存目录权限和备份；
  "需要重新登录"补上登录会话和 profile；云桌面的实例角色"看能力检查"；逐行检测算 `change`。

**r4.3 → r4.4（实施第 2 步时）：** §5.3 原来给所有子进程设 `AWS_CONFIG_FILE` + `AWS_PROFILE=swarmai`，会让 agent
的命令看不到用户自己的 profile。改为 CLI 的 `awsCredentialExport`（只管 CLI 自己的 AWS 调用），agent 的环境不动；
本机实测 CLI、`--bare` job、经思考网关都能用。

**第 2 步实现后的审查：** security + correctness 各一轮（security 1 HIGH、2 MED、3 LOW；correctness 5 MED、3 LOW）。
已改：job 的快速失败客户端也走钉死账号；`rederive` 只在 AWS 登录方式变化时、只换成那种来源、不换成没有账号 ID
的记录；非 owner 渠道会话不能调 `/api/system/auth-method`；job 不管对话走哪条路都给 `awsCredentialExport`；会话
启动前的检查有时间上限、对设置坏了直接拦下；readiness 只读记录状态、不发网络请求；对话的路和以前的判断完全一致；
没有记录时 job 的区域默认值、设置页的会话和以前一样；botocore 的刷新时间对齐 `resolve` 的缓存；Polly 在账号连上后
换用钉死账号。未改：上面 §8 第一条（CLI 的退回）。
