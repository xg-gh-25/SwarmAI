---
created: 2026-10-31
updated: 2026-10-31
title: "SwarmAI 账号：一个 AWS 账号，产品底层统一管理"
status: r3.1 — 按用户旅程重写安装、升级、设置和管理（§6、§7）；r3 已过一次零上下文审查（§11）；待 XG 定（§10）
related: docs/design/2026-10-31-human-proof-verification.md · docs/design/2026-10-31-swarm-out-ops-registry.md
---

# SwarmAI 账号：一个 AWS 账号，产品底层统一管理

## 1. 一句话

**XG 2026-10-31：** SwarmAI 只有一个 AWS 账号，Bedrock、Hive、云存储和整个产品生态都用它；每个用户安装时配好，
由产品底层管理。

**XG 2026-10-31（r3 的输入）：** 从用户的角度想安装向导和设置页：新装和升级、内部和外部用户。从 AWS 账号 ID 开始；
内部账号用 ADA 或 AWS SSO 认证，外部账号用 AWS SSO；安装只给这两种方式。Hive 默认开一台，数量可调，但至少一台。
然后把这些当产品来管。

今天的问题是两套互不相干的身份、十来处各自解析凭证（§2）。底层的解法是**一条账号记录 + 一个解析入口 + 一条交付
凭证的通道**（§5，r2 已定形）。r3 在上面加两层：用户在什么时刻、看到什么、做什么（§6）；产品怎么持续管理这个
账号和它的 Hive（§7）。

## 2. 用户和时刻

| | 内部用户（Amazon 员工，Isengard 账号） | 外部用户（自己的 AWS 账号） |
|---|---|---|
| **第一次安装** | 账号 ID → ADA（默认）或 AWS SSO → 登录 → 核实 → Hive | 账号 ID → AWS SSO → 登录 → 核实 → Hive |
| **升级到这个版本** | 老配置自动生成账号记录；一致就不打扰；不一致才问（§6.4） | 同左 |
| **之后（设置页）** | 一页"SwarmAI 账号"：状态、能力、Hive 数量（§6.5） | 同左 |

还有一种运行方式是 **Hive 本身**。今天 Hive 也走同一个向导（`OnboardingPage.tsx` 的 `isHive` 分支）；r3 里 Hive
模式（`SWARMAI_MODE=hive`）跳过 ②–⑤：daemon 启动时用实例角色自动建记录（§5.1），不做 Hive 数量对齐（§7.3）。

内部和外部的差别只有四处，其余流程完全一样：

| | 内部 | 外部 |
|---|---|---|
| 怎么判断 | `~/.midway` 或 `~/.ada` 存在 → 内部（今天的 `/system/auth-hint` 已经这样判断）；没有就默认外部，向导上一键切换 | 同左 |
| 认证方式 | ADA（默认）、AWS SSO | AWS SSO |
| 登录前提 | Midway 有效（`mwinit`） | 账号接入了 IAM Identity Center，用户有一个能进这个账号的权限集 |
| 权限 | Isengard Admin 角色通常都够 | 由用户的权限集决定；产品提供一份权限模板（§7.4） |

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
5. **新装只有两种认证方式**：ADA 和 AWS SSO；SSO 在 App 里完成登录，不需要用户装 AWS CLI。
6. **Hive 是账号的一部分**：默认一台，数量可调，至少一台；数量是用户定的期望值，产品负责对齐（§7.3）。
7. **升级不打扰**：老用户的配置自动接上，只有不一致时才问。
8. **按能力看就绪**：Bedrock、Hive、云存储、语音各自就绪与否、缺什么权限、怎么补。
9. **对每个用户都成立（P7）**：内部和外部；macOS、Linux、Windows。

**非目标**
- 不支持多个账号（XG 已定）。
- 不加花费上限（STEERING #2）。花费只显示估算，不拦。
- 不改 Hive 上的身份：仍是实例角色，在同一个账号里。
- 不动用户自己的 `~/.aws/config` 和 `~/.aws/credentials`。
- 不在 App 里替用户开通 IAM Identity Center 或改权限集：那是账号管理员的操作，产品只说明缺什么、给模板。

## 5. 底层机制

### 5.1 账号记录

`config.json` 里一个对象（非密钥部分）。SSO 的刷新令牌和客户端密钥放**系统密钥库**（macOS Keychain、Windows
Credential Manager、Linux Secret Service；`backup_manager` 已经这样存它的 token），只有 daemon 读；系统没有密钥库
时退回 `secrets.json`（0600）并在设置页说明。静态密钥（老配置）放 `secrets.json`，不再进 SQLite。

```
aws_account: {
  context: "internal" | "external",
  source: "ada" | "sso" | "instance_role"            # 新装只会是前两种；Hive 上是 instance_role
        | "profile" | "keys" | "bedrock_key" | "none",  # 只为升级上来的老配置保留（§6.4）
  account_id: "533267412361" | null,   # 用户输入、STS 核实；bedrock_key / none 为 null，不钉
  ada: { role, provider: "isengard", bin },          # ada 的绝对路径在配置时记下
  sso: { start_url, sso_region, role },              # App 自己登录（§6.3）；令牌在系统密钥库
  profile: "name",                                   # 老配置：用户已有的 profile
  region: "us-east-1",                               # 主区域：Bedrock、云存储、语音、新 Hive 的默认区域
  caller_arn, verified_at,
  state: 见 §7.1
}
hive_fleet: {
  desired_count: 1,                    # ≥ 1
  instance_type: "m7g.xlarge",         # 新 Hive 的默认规格
  region: null                         # null = 用主区域
}
```

- `source` 替代 `auth_method`。过渡期由 `source` 推出 `auth_method` 给旧读者（`auth_remediation`、凭证横幅、
  会话启动前检查），第二步把这些读者改读 `source`，然后删掉 `auth_method`。
- `bedrock_key` / `none`：老配置里只有 Bedrock API key 或只有 Anthropic key 的用户，调不了 STS，没有账号 ID、
  不钉；Hive、云存储和语音标为"需要 AWS 账号"。
- `instance_role`：Hive 上（`SWARMAI_MODE=hive`）由 daemon 启动时用 STS 和 IMDS 区域**自动建**。
- **`aws_account` 和 `hive_fleet` 不能经通用的 `PUT /api/settings` 写**（`routers/settings.py:35` 的
  `WRITABLE_KEYS` 把它们排除），只能经 §7.7 的专用路由写。`ada.bin` 只接受配置时找到的那个路径（属于用户、不在
  工作区里），否则 daemon 和每个 `credential_process` 都会去执行一个被改过的程序。

### 5.2 解析入口 `core/aws_account.py`

- `record()`：读记录；Hive 模式下没有就自动建。
- `credentials() -> 短期凭证`：按 `source` 解析：ada 调记录里的 `ada credentials print --account --role`；sso 用
  App 自己的 Identity Center 令牌调 `GetRoleCredentials`（§6.3）；profile 直接用；keys 读 `secrets.json`；instance_role
  读 IMDS。令牌的刷新只在 daemon 里做（§5.3），不会有两个进程同时刷新。**每次拿到一个新的
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
- `python-backend aws-credentials` 是一个子命令：**向 daemon 要**凭证（本机回环接口，带 daemon 发的授权 token），
  daemon 调 §5.2 的 `credentials()`（同样核对账号），子命令按 AWS 的 `credential_process` 格式输出短期凭证和过期
  时间。CLI 和 boto3 都会在过期前自己再调一次，长会话不断。子命令自己不读令牌、不刷新：刷新令牌只在 daemon
  里，多个子进程不会互相把对方的刷新令牌作废，也不会同时写密钥库。daemon 不在时子命令返回明确错误。
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
`ada profile print` 读账号和角色；sso：用户选过的 profile，记为 `profile` 来源，§6.4；apikey / bedrock_api_key：对应来源），用 STS 核实。
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

**② 账号 ID 和认证方式**
- 一个输入框：12 位 AWS 账号 ID。内部用户检测到 ADA 已配置时，预填 ADA 当前的账号（今天 `adaDetails` 已经探测到）。
- 内部 / 外部按 §2 判断，显示为一行小字"Amazon 员工？切换"，不是一道问题。
- 认证方式：内部给 ADA（默认）和 AWS SSO 两张卡；外部只有 AWS SSO，不显示选择。
- ADA 多一个"角色"输入：ADA 已配置时预填它当前的角色（`_probe_ada_details` 已经返回 `role_name`，
  `routers/system.py:768-786`），否则默认 `Admin`。
- SSO 多两个输入：SSO 起始地址（`https://xxx.awsapps.com/start`）和 SSO 区域；用户的 `~/.aws/config` 里已有
  `sso_session` 或 sso profile 时，从那里预填（只读，不改它）。

**③ 登录**
- **ADA：** App 直接调 `ada credentials print --account <ID> --role <角色> --provider isengard`（只读，不写任何
  凭证文件；和 §5.3 的 `credential_process` 是同一个调用）。Midway 过期时显示"在终端运行 `mwinit -f`，完成后
  自动继续"，App 每几秒重试一次，成功就进下一步。其他失败各给一句话：角色不对或这个账号你没有该角色（"改角色"）；账号
  不在 Isengard（"这个账号不能用 ADA，改用 SSO"）；找不到 ada（"安装 ada"）。
- **SSO：** App 自己走 IAM Identity Center 的设备授权（§6.3）：点"登录"→ 浏览器打开 AWS 登录页（页上的验证码在
  App 里同时显示）→ 用户在浏览器里登录并允许 → App 收到令牌，列出这个用户在**输入的那个账号**里能用的角色；只有
  一个就直接选中，多个就让用户选（优先显示名字带 Admin 的）。这个账号不在用户能进的账号里 → 明确报错
  "这次 SSO 登录看不到账号 X"，并列出能看到的账号。

③ 和 ④ 都能返回上一步，也都有"稍后配置"。

**④ 核实**
- STS `GetCallerIdentity` 的账号必须等于输入的账号 ID，否则停在这一步，说明两边各是哪个账号。
- 通过后记录写入，状态"已连接"，然后跑能力检查（§7.2），一行一个能力：Bedrock、Hive、云存储、语音。
- **Bedrock 不可用就不能往下走**（没有模型，SwarmAI 什么都做不了），提示缺什么（模型访问没开、区域里没有该模型、
  权限缺哪个动作）。其余能力缺权限只标出来，不挡。

**⑤ Hive**
- 默认"创建 1 台"，数量可加（最少 1），规格默认 m7g.xlarge，区域默认主区域；按钮上写清后果："创建 1 台 Hive，
  约 $119/月，计入账号 533267412361"。
- 点了就提交创建，**不等它建完**（一台 Hive 要建 EC2、IAM 角色、S3、CloudFront，CloudFront 部署最长等 20 分钟，
  `hive/provisioner.py:1006`），向导直接往下走；建造进度在设置页和 Swarm Out 里看。
- 能力检查按**这一步选的区域**跑（区域一改就重跑）。没过（例如外部用户的权限集没有 EC2）时显示缺什么，按钮
  变成"补好权限后在设置里创建"，向导可以继续。这时 Hive 是一条待办（§7.1 的 Hive 状态），不是账号状态。
- 为什么不挡：本机对话只需要 Bedrock。为了一台云上的 Hive 把用户挡在整个产品外面，代价不对等。"至少一台"由
  §7.3 的期望数量来保证，而不是由向导卡住。

**"稍后配置"** 在 ②–④ 都有，作为出错时的出口；走了它，对话不可用，设置页显示"未连接"。

### 6.2 认证方式只给两种之后，别的方式去哪

| 方式 | 新装 | 老用户 |
|---|---|---|
| ADA | 有（内部） | 照常 |
| AWS SSO | 有（内部、外部） | 用 profile 的老配置照常；设置页建议改成 App 内登录 |
| 已有 profile、静态密钥 | 收起来的"高级"入口（§10 第 1 条） | 照常，标"旧方式" |
| 只有 Bedrock API key、只有 Anthropic key | 没有（今天外部用户的向导里有 Anthropic key，r3 去掉） | 照常，标"旧方式"；Hive、云存储、语音不可用 |
| 实例角色 | 没有（只在 Hive 上自动用） | 照常 |

去掉 Anthropic key 以后，外部用户最常见的情况（一个人一个 AWS 账号，没有 Organizations）只剩 SSO 一条路，而单个
账号里开的 Identity Center 不能给账号分配权限集（§8）。所以 §10 第 1 条要和这一节一起定。

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

### 6.4 升级：老用户第一次启动新版本

升级不进向导。新版本第一次启动时，后台按 §5.6 从老配置生成记录并用 STS 核实；然后按结果分三种：

| 情况 | 用户看到什么 |
|---|---|
| 核实通过，Hive（如有）都在同一个账号 | 什么都不弹。设置页"SwarmAI 账号"直接显示结果；一次性的一行提示"SwarmAI 账号已接上：533267412361" |
| 核实通过，但：没有 Hive / 有 Hive 在另一个账号 / 用的是"旧方式" | 设置页顶部一条待办，点开是一张确认卡，只问不一致的那一项（例："你还没有 Hive。创建 1 台，约 $119/月？"） |
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
Hive      2 台（期望 2，最少 1）    约 $238/月                [− ] 2 [+]
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
| 需要重新登录 | ADA：Midway 过期；SSO：令牌和刷新令牌都过期 | 用户登录后自动回到"已连接" |
| 账号不符 | 解析出的凭证不是记录里的账号（例如 ada 账号被别处改了） | 用户在设置页确认：改回，或更换账号 |
| 未核实 | 升级时推不出来，或 STS 暂时调不通 | 下次成功解析时自动核实 |

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

- `hive_fleet.desired_count` 是用户定的（向导、设置页），最少 1。
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
- 花费：每台按规格显示月度估算，数量旁边显示合计；不设上限。

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
| 发起 SSO 登录、ADA 登录 | 人 | change |
| 更换账号、改认证方式或角色 | 人 | change |
| 改 Hive 期望数量（调大会创建） | 人 | off_machine |
| 替换 Hive、终止旧账号里的 Hive | 人 | irreversible |
| 创建默认 VPC | 人 | off_machine |

agent 照常按"代我做事"的授权工作，读得到这些状态，需要时告诉用户该做什么；花钱和不可撤销的这几步由人点。今天
这张表在命令行一侧是减速带不是边界（agent 和 daemon 是同一个系统用户，表的说明里写着）；把它变成边界是
`2026-10-31-human-proof-verification.md` 的事，本设计不重复做。不加花费上限（STEERING #2）。

## 8. 会让它失败的事

- **CLI 不认 `AWS_CONFIG_FILE` + `credential_process`：** 第 1 步先验证 CLI（Claude Code 用的是 JS 版 AWS SDK）和
  awscrt 都按这个 profile 取凭证、过期自己刷新。不认就退到 daemon 提供的容器凭证端点
  （`AWS_CONTAINER_CREDENTIALS_FULL_URI` + 授权 token），同样可刷新。
- **SSO 设备授权：** 要验证 Identity Center 给 App 注册的客户端发刷新令牌（AWS CLI v2 的 `sso_session` 就是这样
  做的）。不给的话，令牌到期就要重新登录，功能不变，只是登录更频繁。
- **外部单账号用户没有 Identity Center 组织实例：** 只在单个账号里开的 Identity Center（账号实例）不能给 AWS
  账号分配权限集（[AWS 文档](https://docs.aws.amazon.com/singlesignon/latest/userguide/account-instances-identity-center.html)），
  这类用户要先开 AWS Organizations 才能用 SSO。见 §10 第 1 条。
- **钉的是账号不是角色：** ada 换角色账号不变，权限缺口由能力检查报。
- **断网：** 核对结果缓存；断网时用上次结果并标"未核实"，不阻塞。钉死是防打错账号，不是安全边界。
- **ada 在 launchd 下找不到：** 配置时记绝对路径。
- **账号没有默认 VPC：** Hive 创建今天依赖默认 VPC；能力检查先报，不让用户在创建到一半时才失败。

## 9. 分步和验证计划

**分步（旧路径在新路径通过测试前保留）**
1. 先验证 §8 前两条。然后 `core/aws_account.py`、记录、§5.6 生成、`aws-credentials` 子命令、
   `~/.swarm-ai/aws/config`、`GET /api/aws-account`（状态 + 能力）、设置页"SwarmAI 账号"（只看）。
2. 本机消费方改走入口（§5.4 前十行），`auth_method` 的读者改读 `source`，删 `jobs/bedrock` 兜底和 executor 冻结密钥。
3. Hive：消费方改走入口，Hive 模式自动建记录，§5.5 迁移，Hive 设置页去掉账号块。
4. 向导 ②–④ 和设置页的登录、更换账号：ADA 路径和 App 内 SSO 登录（§6.3）。
5. Hive 数量：`hive_fleet`、向导 ⑤、设置页数量、对齐（§7.3）、权限模板（§7.4）和 provisioner 建时打标签、
   新桶名、升级确认卡（§6.4）。

**最终测试集（不变量）**
1. **完整性：** 对 `backend/` 做 AST 检查，import `boto3` / `botocore` / `aiobotocore` / `amazon_transcribe` 的模块只能是
   `core/aws_account.py` 和一份写明原因的例外清单；区域字面量和 `AWS_REGION` / `TRANSCRIBE_REGION` 的读取同样只在入口里。
2. **钉死：** 解析出的凭证账号和记录不符时抛错、状态"账号不符"，不退回默认链；access key ID 变了就重新核对。
3. **交付：** `child_env()` 每种来源给什么、清掉什么；真实冒烟：CLI 实际签名用的 key 属于记录的账号。
4. **迁移和升级：** §5.5 每种情况；§6.4 三种情况各显示什么；没有 Hive 的老用户不会被自动建。
5. **向导：** 输入的账号 ID 和 STS 不符时停在 ④；Bedrock 不可用时不能往下；Hive 能力不够时向导仍能完成。
6. **SSO：** 设备授权、看不到输入账号时的报错、令牌续期、续不了进"需要重新登录"、重新登录后恢复；客户端过期后
   重新注册；令牌不在 `config.json` / `secrets.json` / 备份里（有系统密钥库时）。
7. **Hive 数量：** 不能删到 0；调大直接创建；别的原因少了只提示不创建；最后一台只能替换；表里 `error` 但 EC2 还在
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

1. **外部用户没有 Identity Center 组织实例**（个人或小公司的单个 AWS 账号）：
   (a) 只给 SSO，向导说明怎么开 Organizations 和 Identity Center；
   (b) 在 ② 加一个收起来的"高级：用已有的 AWS profile"，覆盖这类用户和已经有自己凭证习惯的工程师。
   推荐 (b)：主路径仍是两种，(b) 不显示在卡片里，只是一个链接。不选 (b) 的话，单账号的外部用户装好以后什么都
   用不了（§6.2）。
2. **Hive 是否挡住向导完成**：推荐不挡（§6.1 ⑤ 的理由），"至少一台"由期望数量和 Hive 状态保证；Hive 状态是
   待办，可以"以后再说"，但期望数量不降到 0（§7.1）。
3. **Hive 的花费确认**：创建按钮上写清台数、月度估算和账号，点了就是同意；不再单独弹确认。推荐这样。
4. **没有 Hive 的老用户**：只提示、一键创建，不自动建。推荐这样。
5. **"至少一台"怎么算**：已停止的 Hive 也算（它还在，随时能开）。推荐这样。
6. r2 留下的：方案 B 和分步；一个主区域、Hive 保留自己的区域；另一个账号里的 Hive 保留旧凭证只用于停止和终止；
   实施方式（R1，跨模块、改数据形状、有迁移）：直接改，还是走 pipeline。

## 11. 审查记录

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
