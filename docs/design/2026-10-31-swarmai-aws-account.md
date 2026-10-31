---
created: 2026-10-31
updated: 2026-10-31
title: "SwarmAI 账号：一个 AWS 账号，产品底层统一管理"
status: r2 — 方案，已过一次零上下文审查（§9），待 XG 定（§8）
related: docs/design/2026-10-31-human-proof-verification.md · docs/design/2026-10-31-swarm-out-ops-registry.md
---

# SwarmAI 账号：一个 AWS 账号，产品底层统一管理

## 1. 一句话

**XG 2026-10-31：** SwarmAI 只有一个 AWS 账号，Bedrock、Hive、云存储和整个产品生态都用它；每个用户安装时配好，
由产品底层管理。

今天不是这样：有两套互不相干的身份，还有十来处各自解析凭证。本设计把它们收成**一条账号记录 + 一个解析入口 +
一条交付凭证的通道**：daemon 里的代码从入口拿会话，daemon 起的每个子进程（CLI、job、脚本、skill）都通过一个
SwarmAI 自己的 AWS profile 拿凭证，这个 profile 的凭证每次都由入口现解析、现核对账号。

## 2. 现状（2026-10-31 读过，审查逐条复核）

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

## 3. 目标和非目标

**目标**
1. **一条账号记录**（§5.1），替代 `auth_method` 作为唯一来源。
2. **一个解析入口**（§5.2）：daemon 里要 AWS 会话只从这里拿。
3. **一条交付通道**（§5.3）：daemon 起的每个子进程都通过同一个 SwarmAI profile 拿凭证，自动刷新，长会话不断。
4. **账号钉死**：每次解析出新凭证都核对账号，对不上就明确报错，绝不悄悄打到别的账号。
5. **按能力看就绪**：Bedrock、Hive、语音各自就绪与否、缺什么权限；按运行模式（桌面 / Hive）分别检查。
6. **安装时配，设置里管**。
7. **对每个用户都成立（P7）**：ada、SSO（IAM Identity Center）、命名 profile、静态密钥、实例角色、只有 Bedrock
   API key、只有 Anthropic key；macOS、Linux、Windows。

**非目标**
- 不支持多个账号（XG 已定）。
- 不加花费上限（STEERING #2）。
- 不改 Hive 上的身份：仍是实例角色，在同一个账号里。
- 不动用户自己的 `~/.aws/config` 和 `~/.aws/credentials`。

## 4. 方案比较

| 方案 | 做什么 | 代价 | 评价 |
|---|---|---|---|
| A 打补丁 | 两套各留着；Bedrock 加 profile 设置，Hive 加账号核对 | 小 | 十来处解析器照样各走各的 |
| **B 一条记录、一个入口、一条通道（推荐）** | §5 | 中，分四步，每步可独立上线 | 根除重复；新功能只能走入口 |
| C 每个能力一个账号 | 账号表扩成多行 | 大 | 和 XG 的决定相反 |

## 5. 方案 B

### 5.1 账号记录

`config.json` 里一个对象（非密钥部分）；静态密钥放 `secrets.json`（0600，已有的密钥库），不再进 SQLite。

```
aws_account: {
  source: "ada" | "sso" | "profile" | "keys" | "instance_role" | "bedrock_key" | "none",
  account_id: "533267412361" | null,   # STS 核实过的；bedrock_key / none 为 null，不钉
  ada: { account, role, provider, bin },   # source=ada：ada 的绝对路径在配置时记下
  sso: { profile },                        # source=sso：用户自己 ~/.aws/config 里的 sso profile
  profile: "name",                         # source=profile：用户已有的任意 profile
  region: "us-east-1",                     # 主区域
  caller_arn, verified_at, verified: true|false
}
```

- `source` 替代 `auth_method`。过渡期由 `source` 推出 `auth_method` 给旧读者（`auth_remediation`、凭证横幅、
  会话启动前检查），第二步把这些读者改读 `source`，然后删掉 `auth_method`。
- `bedrock_key`：只有 Bedrock API key，调不了 STS，没有账号 ID、不钉；Hive 和语音标为"需要 AWS 账号"。
- `none`：只用 Anthropic key（`use_bedrock=false`），同上。
- `instance_role`：Hive 上（`SWARMAI_MODE=hive`）由 daemon 启动时用 STS 和 IMDS 区域**自动建**，Hive 没有安装向导；
  桌面主机跑在 EC2 / 云桌面上时也可以选它。

### 5.2 解析入口 `core/aws_account.py`

- `record()`：读记录；Hive 模式下没有就自动建。
- `credentials() -> 短期凭证`：按 `source` 解析：ada 调记录里的 `ada credentials print --account --role`；sso 用
  botocore 读用户的 sso profile；profile 直接用；keys 读 `secrets.json`；instance_role 读 IMDS。**每次拿到一个新的
  access key ID 就做一次 STS 核对**，账号和记录不符就抛 `AccountMismatch`（带两个账号 ID）。结果缓存到过期前几分钟；
  同一时间只有一次解析（锁）。
- `session(region=None) -> boto3.Session`：用 `credentials()` 的可刷新凭证建会话，每个线程一个；**不在调用方线程里
  发网络请求**，核对在工作线程里做并缓存。默认主区域；Hive 实例的操作显式传实例区域。
- `child_env() -> dict`：给子进程的环境（见 §5.3）。
- `identity()`：缓存的 STS 结果，凭证检查和就绪采样读它。
- `capabilities(mode)`：桌面检查 Bedrock、EC2 / SSM / IAM / S3 / CloudFront（Hive 用）、Polly / Transcribe；
  Hive 模式只检查 Bedrock 和它自己那几个 S3 前缀（实例角色本来就只有这些权限）。

### 5.3 交付通道：SwarmAI 自己的 AWS profile

子进程（CLI、job、脚本、skill）都是长时间运行的，环境变量在启动后改不了，所以不往环境里塞短期凭证。改为：

- daemon 维护一个 SwarmAI 自己的 AWS 配置文件 `~/.swarm-ai/aws/config`，只有一段：
  `[profile swarmai] credential_process = <python-backend> aws-credentials` 和 `region = <主区域>`。
- `python-backend aws-credentials` 是一个子命令：调 §5.2 的 `credentials()`（同样核对账号），按 AWS 的
  `credential_process` 格式输出短期凭证和过期时间。CLI 和 boto3 都会在过期前自己再调一次，长会话不断。
- `child_env()` 返回：`AWS_CONFIG_FILE=~/.swarm-ai/aws/config`、`AWS_PROFILE=swarmai`、`AWS_REGION` /
  `AWS_DEFAULT_REGION=主区域`，并**去掉**进程环境里其余的 `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` /
  `AWS_SESSION_TOKEN` / `AWS_DEFAULT_PROFILE` / 凭证 token 变量，免得 CLI 按优先级先用了它们。
- `bedrock_key` 来源：`child_env()` 只给 `AWS_BEARER_TOKEN_BEDROCK` 和区域（今天的做法），不给 profile。
- `instance_role`（Hive）：`child_env()` 只给区域，清掉 profile 和密钥，子进程走实例角色（今天的做法）。
- 思考网关用 `session()`，和 `child_env()` 同源，签名用的 key 和 CLI 一致。
- 用户自己的 `~/.aws/config` 一个字都不改。

### 5.4 消费方改走入口（按风险从低到高）

| 消费方 | 改成 | 删掉 |
|---|---|---|
| 设置页模型列表、`aws_credentials_configured` | `session()` / `identity()` | 各自的默认链 |
| 凭证检查、就绪采样、`verify-auth` | `identity()`；检查的就是实际要用的身份 | `verify-auth` 的 `os.environ` 换值 |
| `jobs/bedrock` 的所有调用方（summarization、scorer、CDF 抽取、eval 判官） | `session()` | SSO 缓存兜底 |
| `jobs/executor.py`（agent job 和脚本 job） | `child_env()` | 冻结密钥、删 `AWS_PROFILE`、us-west-2、脚本 job 去密钥 |
| CLI 子进程（`claude_environment.py`） | `child_env()` | 依赖 daemon 环境碰巧对 |
| 思考网关 | `session()` | 默认链会话 |
| Polly | `session().client("polly")` | 裸 client、`TRANSCRIBE_REGION` |
| Transcribe（awscrt） | 给 `TranscribeStreamingClient` 一个从 `credentials()` 取值并会刷新的凭证解析器 | 它自己的凭证链 |
| eval | 区域取记录 | 先读环境变量 |
| 独立脚本、skill（如 pollinate 的 polly） | 不 import 入口，靠 executor 给的 `child_env()` | — |
| Hive：`provisioner` 的 `_get_account` / `_get_session`（全部约 10 处）、`swarm_out._s3`、`routers/hive.py` verify | `session(region=实例区域)` | `hive_accounts` 的凭证字段、verify 里重复的会话代码 |
| Hive 侧：桥、Hive 上的 CLI | 记录自动建（`instance_role`），`session()` / `child_env()` | 默认链 |
| Hive 侧 shell 里的 `aws s3 cp`（`user_data.py`、SSM 更新脚本） | 不改，实例角色 | —（列入完整性测试的例外，写明原因） |

### 5.5 Hive 账号表的迁移

迁移前备份 `hive_accounts`（0600，且备份里去掉明文密钥：密钥先挪进 `secrets.json` 再备份），STEERING #20。

| 情况 | 处理 |
|---|---|
| 一行，STS 核实是 SwarmAI 账号 | 合并：实例的 `account_ref` 指向 SwarmAI 账号；密钥（如有）挪进 `secrets.json` |
| 一行，是另一个账号 | 不合并。这个账号的凭证保留，**只用于这些实例的停止、终止、清理**，直到用户把实例迁走或销毁；设置页显示"这台 Hive 在另一个账号" |
| 多行 | 与 SwarmAI 账号相同的合并；其余按上一行 |
| 零行但有实例 | 实例按 SwarmAI 账号处理，第一次操作前核对实例确实在这个账号里，不在就提示 |
| 迁移时 STS 失败（断网、凭证过期） | 不迁移，记录标"未核实"，设置页提示，下次启动再试 |

表保留一个版本（只读），下个版本再删。

### 5.6 现有记录怎么生成

第一次启动新版本时，从 `auth_method` 和实际在用的来源推出 `source`（ada：从 `~/.aws/config` 的默认段或
`ada profile print` 读账号和角色；sso：用户选过的 profile；apikey / bedrock_api_key：对应来源），用 STS 核实。
推不出来，或者 launchd 环境里找不到 ada：记录标"未核实"，**不猜**，设置页请用户确认。

### 5.7 安装和设置

- **安装向导**第二步改为"连接 SwarmAI 账号"。沿用现有自动检测（`/system/auth-hint`）；来源：
  - ada（Amazon 内部）：填账号和角色，记下 ada 的绝对路径；
  - SSO（AWS IAM Identity Center，外部用户主路径）：选 `~/.aws/config` 里的 sso profile，或按向导建一个
    `sso_session` 段；没有 AWS CLI 时说明怎么装；`aws sso login` 由用户在终端里跑，向导给出命令并等待；
  - 已有 profile、静态密钥、实例角色、只有 Bedrock key、只有 Anthropic key。
  核实后显示账号 ID 和各能力的就绪情况。仍可"稍后配置"。
- **登录过期：** SSO / ada 过期时，现有凭证横幅按 `source` 给出重新登录的命令。
- **设置页**"SwarmAI 账号"一页（替代 AI & Models 里的认证块和 Hive 页的账号块）：账号 ID、来源、主区域、核实时间、
  各能力就绪；重新核实；更换账号时，有 Hive 在旧账号里就先说清后果。
- **平台：** `~/.aws`、ada / aws 可执行文件的位置按平台解析，配置时记下绝对路径，不靠 launchd 或 `zsh -lic` 的 PATH。

## 6. 会让它失败的事

- **CLI 不认 `AWS_CONFIG_FILE` + `credential_process`：** 第 1 步先验证 CLI（Claude Code 用的是 JS 版 AWS SDK）和
  awscrt 都按这个 profile 取凭证、过期自己刷新。不认就退到 daemon 提供的容器凭证端点
  （`AWS_CONTAINER_CREDENTIALS_FULL_URI` + 授权 token），同样可刷新。
- **钉的是账号不是角色：** ada 换角色账号不变，权限缺口由 `capabilities()` 报。
- **断网：** 核对结果缓存；断网时用上次结果并标"未核实"，不阻塞。钉死是防打错账号，不是安全边界。
- **ada 在 launchd 下找不到：** 配置时记绝对路径。

## 7. 分步和验证计划

**分步（旧路径在新路径通过测试前保留）**
1. 先验证 §6 第一条。然后 `core/aws_account.py`、记录、§5.6 生成、`aws-credentials` 子命令、
   `~/.swarm-ai/aws/config`、只读 API、设置页"SwarmAI 账号"（只看）。
2. 本机消费方改走入口（§5.4 前十行），`auth_method` 的读者改读 `source`，删 `jobs/bedrock` 兜底和 executor 冻结密钥。
3. Hive：消费方改走入口，Hive 模式自动建记录，§5.5 迁移，Hive 设置页去掉账号块。
4. 安装向导和设置页的更换流程，SSO 路径。

**最终测试集（不变量）**
1. **完整性：** 对 `backend/` 做 AST 检查，import `boto3` / `botocore` / `aiobotocore` / `amazon_transcribe` 的模块只能是
   `core/aws_account.py` 和一份写明原因的例外清单（测试、独立 skill 脚本、Hive shell 模板、第三方 app）；区域字面量和
   `AWS_REGION` / `TRANSCRIBE_REGION` 的读取同样只在入口里。
2. **钉死：** 解析出的凭证账号和记录不符时抛错，不退回默认链；access key ID 变了就重新核对。
3. **交付：** `child_env()` 每种来源给什么、清掉什么；再加一个真实冒烟：CLI 实际签名用的 key（经思考网关看）属于记录的账号。
4. **迁移：** §5.5 每种情况；另一个账号的 Hive 仍能停止和终止；备份里没有明文密钥。
5. **没有 AWS 账号的来源：** `bedrock_key` / `none` 下 Hive 和语音显示"需要 AWS 账号"，其余照常。
6. **Hive 模式：** 自动建记录；桥、Bedrock、CLI 照常；就绪检查不报 Hive 本来就没有的权限。

**审查：** 本文已过一次零上下文审查（§9）。实现后每步 security + correctness 各一轮。

## 8. 要 XG 定

1. 方案 B 和四步分法。
2. 只有 Bedrock API key 或只有 Anthropic key 的用户：允许（Hive、语音不可用）。推荐允许。
3. 区域：一个主区域，Hive 实例保留自己的区域。推荐这样。
4. 另一个账号里的 Hive：保留旧凭证只用于停止和终止，直到迁走或销毁。推荐这样。
5. 实施方式（R1，跨模块、改数据形状、有迁移）：直接改，还是走 pipeline。

## 9. 审查记录

r1 经过一次零上下文 correctness 审查：5 HIGH、12 MEDIUM、3 LOW，r2 全部吸收。
- **交付方式改了：** r1 的"注入短期凭证、过期前刷新"做不到（进程起来后环境改不了），改为 SwarmAI 自己的
  `credential_process` profile（§5.3）；ada 也不再写会过期的静态凭证。
- **补了漏掉的消费方：** Transcribe（awscrt）、Polly 裸 client、桥、脚本 job、`verify-auth`、eval 区域、独立 skill。
- **记录：** `source` 替代 `auth_method`；`bedrock_key` / `none` 没有账号 ID；Hive 模式自动建。
- **迁移：** 另一个账号的 Hive 仍能停止和终止；多行、零行、STS 失败；备份不带明文密钥；`_get_account` 全部替换。
- **钉死：** 按 access key 变化核对；不在调用方线程里发网络请求；解析加锁。
- **测试：** 完整性测试改成 AST 检查 import，带写明原因的例外清单；交付加真实冒烟。
- **非 Amazon 用户和其他平台：** SSO 路径、登录过期提示、可执行文件绝对路径。
- **§2 的措辞**：executor 的区域、`jobs/bedrock` 的取法、eval 的区域。
