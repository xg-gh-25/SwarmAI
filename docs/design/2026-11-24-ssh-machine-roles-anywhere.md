---
title: SSH 机器的 Hive 身份：IAM Roles Anywhere（方案 B）
date: 2026-11-24
created: 2026-11-24
updated: 2026-11-24
project: SwarmAI
status: 设计 r2（零上下文审查一轮后），待 XG 看过再落地
related: docs/design/2026-11-24-swarm-out-ssh-machine.md（§5.3 安装、§7 安全）
---

# SSH 机器的 Hive 身份：IAM Roles Anywhere（方案 B）

## 1. 要解决什么

XG：「用户不应该去操心 EC2, CloudDesktop, Hive 的各种配置, SwarmAI 应该把所有Hive 的配置的setting 搞定」。

SSH 机器今天只剩一项 SwarmAI 自己的配置要人管：机器上得有一个能访问 SwarmAI 账户的 AWS profile。
`_find_profile`（`hive/ssh_provisioner.py`）在容器里逐个查身份，用第一个属于账户的角色；Hive 的窄角色
`SwarmAI-Hive-<name>` 信任的就是它（`trust_policy`，`:564`）。Amazon 员工的 Cloud Desktop 上有 ada 找得到，外部用户的机器多半没有。

Hive 自己只做三类 AWS 调用：Bedrock、自己的 S3 bridge 前缀、读发布包（`hive/provisioner.py` 的 `hive_iam_policy`）。
用量同步和 Brain 同步由 Host 经隧道拉（`core/usage_hive_sync.py`、`core/brain_sync_host.py`），不用机器上的 AWS 身份。
容器的 `AWS_PROFILE=swarmai-hive-<name>`（`env_file`），Hive 的 boto3 和它起的 Claude CLI 都经这一个 profile。
所以只要换掉这个 profile 的来源，机器上就不需要任何 AWS 设置。

目标：机器上不需要任何 AWS 凭据，Hive 自己拿到窄角色的临时凭据，Host 关着也能一直用。
任务"以人的身份跑"不变（原设计 §7）：人自己的 profile 在机器上照旧可用，只是 Hive 不再依赖它。
非目标：任务的身份、EC2 Hive、Elastic Workers、legacy 账户模式都不动。

## 2. 方案

| | 做法 | 不选的理由 |
|---|---|---|
| A1（选） | IAM Roles Anywhere：Host 当一个小 CA，在账户里登记信任锚（外部证书包，不用 AWS Private CA）；机器自己生成密钥，Host 签证书；窄角色只信任这次安装的证书；机器用官方 `aws_signing_helper` 换临时凭据 | — |
| A2 | Host 替机器 assume 窄角色，经 SSH 定期送临时凭据 | Host 一关，最多 12 小时后机器停摆 |
| A3 | 保持现状（机器上找 profile） | 外部用户机器上没有，就得人配，正是要去掉的 |
| A4 | AWS Private CA 当 CA | $400/月/CA（短期模式 $50/月）；外部证书包免费 |
| A5 | 证书密钥放进只有 root 能读的旁路容器，用工具的 `serve` 模式发凭据 | 偷不走密钥，只能在机器上取 1 小时的凭据，更安全；但多一个容器、要改 SDK 取凭据的方式。先不做，列为以后的加固（§5） |

## 3. 官方事实（2026-11-24 核对，来源见 §10）

- 信任锚 `sourceType` 可以是 `CERTIFICATE_BUNDLE`（PEM），不需要 Private CA。CA 证书要 `CA:true`、keyUsage 含 `Certificate Sign`，签名 SHA256 以上。每个信任锚最多 2 张证书（可用来轮换），账户每区最多 50 个信任锚、250 个 profile（可提额）。
- 终端证书：X.509v3，`CA:false`，keyUsage 含 `Digital Signature`，SHA256 以上，主题不能为空；支持 RSA、ECDSA。主题各项变成会话标签 `aws:PrincipalTag/x509Subject/<RDN>`。
- 角色信任：Principal `rolesanywhere.amazonaws.com`，动作 `sts:AssumeRole`、`sts:TagSession`、`sts:SetSourceIdentity`；条件用 `aws:SourceArn`（信任锚的规范 ARN）和 `aws:PrincipalTag/x509Subject/CN`。没有条件时，同一 CA 签的任何证书都能 assume。
- Profile：`roleArns` 0–250 个，`durationSeconds` 900–43200，默认 3600。CreateProfile 需要 `iam:PassRole`（`iam:PassedToService = rolesanywhere.amazonaws.com`）。
- 凭据工具：`aws_signing_helper credential-process --certificate --private-key --trust-anchor-arn --profile-arn --role-arn`；官方地址按版本和架构固定，文档页给出每个二进制的 SHA256（Linux 版没有代码签名）。当前 1.8.6。
- us-east-1、us-west-2 可用。

## 4. 设计

### 4.1 Host 的 CA 和信任锚（每个 Host × 账户 × 区域一个）

- **CA**：Host 第一次需要时生成 EC P-256 密钥和自签 CA 证书（`CA:true`，pathlen 0，**有效期 2 年**，主题 `CN=SwarmAI Hive CA <host-id>`），放在 `~/.swarm-ai/state/hive-ca/<account>-<region>/`（目录 0700、密钥 0600），用后端已有的 `cryptography`。不进备份。
- **信任锚**：`swarmai-hives-<host-id>`，`CERTIFICATE_BUNDLE` = 这张 CA 证书，`enabled=True` 显式传。每个 Host 自己一个锚，两台 Mac 管同一账户互不影响。
- **CA 轮换**：CA 剩不到 6 个月时生成新 CA，把它作为信任锚的第二张证书加入（每锚 2 张），各机器下次续证书时由新 CA 签、角色信任不变（条件是锚的 ARN 和 CN，不是哪张 CA）；所有机器都换过后移除旧 CA 证书。
- **全部作废**：删掉信任锚，这个 Host 签过的证书一起失效。
- 只在 SwarmAI 账户模式下做（用 Host 已有的账户会话 `ssh_provisioner._session`）。

### 4.2 每台机器的身份

- **CN 带这次安装的 id**：`CN=swarmai-hive-<name>-<安装 id 前 8 位>`（安装 id = 机器行的 id）。同一个名字删了再加，是新的 id，旧证书 assume 不了新角色。
- **密钥在机器上生成，从不离开机器，每次签证书都换新密钥**：安装步骤 `identity key` 在容器里用 Hive 自带的 Python（`/opt/swarmai/backend/.venv/bin/python`，有 `cryptography`）在 `$HOME/.swarmai-hive/identity/` 生成 P-256 PKCS#8 新密钥（0600），打印 CSR。
- **Host 签证书**：只取 CSR 的公钥，主题和扩展全由 Host 定：上面的 CN，`CA:false`，keyUsage `digitalSignature`，`notBefore` = 现在减 5 分钟（容忍时钟差），**有效期 30 天**。证书经 stdin 写回 `identity/cert.pem`。
- **窄角色信任**：Principal `rolesanywhere.amazonaws.com`，三个动作，条件 `ArnEquals aws:SourceArn = 本 Host 的信任锚`、`StringEquals aws:PrincipalTag/x509Subject/CN = 上面的 CN`。
- **Roles Anywhere profile**：每台机器一个，`roleArns` 只有它的角色，`durationSeconds` 3600，`enabled=True`。移除时直接删，没有共享列表要并发维护。
- **记下来**：`carrier_config` 记 `identity: "rolesanywhere"`、信任锚 ARN、profile ARN、区域、CN、证书 `not_after`、机器上 AWS profile 的名字（`aws_profile`）。移除和续期都用记下的值，不按今天的默认区域现算。
- **机器上的 AWS profile**（`~/.aws/config` 里 SwarmAI 的标记块，`PROFILE_SH` / `UNPROFILE_SH` 管理）：
  `credential_process = /opt/swarmai/bin/aws_signing_helper credential-process --certificate <$HOME 绝对路径>/.swarmai-hive/identity/cert.pem --private-key <…>/key.pem --trust-anchor-arn … --profile-arn … --role-arn …`（`credential_process` 不展开 `~`）。不再有 `source_profile`。工具只在镜像里，人在机器本身上用这个 profile 会失败，它本来就只给 Hive 用。
- **凭据工具进镜像**：Host 按预检的 `arch` 从官方地址下载固定版本，按代码里写死的 SHA256 校验，校验不过就不装；放进构建上下文，装到 `/opt/swarmai/bin/`（root 所有）。Host 缓存下载结果。**镜像标签带上工具版本**（`<Hive 版本>-h<工具版本>`）：今天 `BUILD_SH` 见到同名镜像就复用（`:353`），不带的话改造前建的同版本镜像里没有工具。

### 4.3 安装流程（替换原设计 §5.3 第 3–4 步）

```
preflight → image（含 aws_signing_helper）→ Host: 确保 CA + 信任锚
  → Host: 建窄角色（Roles Anywhere 信任）→ Host: 建 Roles Anywhere profile
  → 机器: identity key（新密钥，打印 CSR）→ Host: 签证书 → 机器: 写 cert.pem
  → 机器: 写 profile 块 → 容器里查身份 = 这个窄角色（已有的 profile check）
  → env → start → /health
```
任一步失败照旧落 `error` 并说明；Roles Anywhere 或 IAM 调用被拒时说明缺哪个权限（§9）。

### 4.4 续期（自己的一轮，不依赖版本）

- `hive_idle.check_once` 每轮（`CHECK_EVERY_S` = 300 秒）对每台运行中的 SSH 机器看记下的 `not_after`：**剩不到 10 天才 SSH 过去**，生成新密钥、签新证书、写回，记新的 `not_after`。不看版本、不管机器忙不忙、开发版 Host 也做（`follow_host` 在没有机器落后或开发版时直接返回，`core/carriers/ssh.py:427-433`，所以不能挂在它下面）。
- 换证书不打断在跑的任务：`credential_process` 每次取凭据都读文件，已发出的凭据到期前照常可用。
- Host 关机超过约 20 天，证书可能过期，Hive 拿不到凭据：卡片显示"身份证书已过期，Host 上线后自动续"，Host 一上线下一轮就续。

### 4.5 迁移（已经用人的 profile 装好的机器）

迁移放在一次 update 里，失败时能回滚到旧容器、旧身份：
1. swap 之前：建 CA / 信任锚 / Roles Anywhere profile，签证书；把窄角色信任改成**两条都认**（旧的人的角色 + Roles Anywhere）；写一个**新名字**的 profile 块 `swarmai-hive-<name>-ra`，旧块不动。
2. swap：新容器的 env 里 `AWS_PROFILE` 指向新块。
3. `/health` 之外，在新容器里查一次身份，必须是这个窄角色（今天的 update 不查身份）。
4. 通过、`COMMIT_SH` 之后：信任只留 Roles Anywhere 一条，删旧 profile 块，`carrier_config` 记 `identity: "rolesanywhere"`、`aws_profile`，删 `source_profile`。
5. 任一步失败：`ROLLBACK_SH` 起旧容器，它的 env 仍指旧块，旧信任还在，照旧能用。

新安装的机器直接走 §4.3，不经过这些。

**删除**：`_find_profile`、`_found_profiles`、`MAX_PROFILE_TRIES`、添加接口和服务里的 `source_profile` 字段、相关测试，都在这次改动里删掉，不留兼容层。

### 4.6 移除和失效

- 移除机器：删 Roles Anywhere profile（按记下的 ARN 和区域）、删窄角色（已有）、删机器上的 `identity/` 目录和 profile 块（已有 `UNPROFILE_SH`）。信任锚和 CA 留给别的机器。
- 单台作废：删 profile + 删角色即可。证书不用单独吊销：没有角色可 assume，名字重用时 CN 也不一样（§4.2）。
- Host 换电脑、CA 丢了：新 Host 建自己的 CA 和信任锚；机器下次续期时由新 Host 签证书、角色信任改成新锚。旧锚由新 Host 在确认没有角色引用它后删除。

## 5. 安全

- **对已有人 profile 的机器（Cloud Desktop）更窄**：今天窄角色从人的身份（XG 这里是账户 Admin）assume，同一台机器上的任务本来就能读到人的 profile；之后 Hive 只靠一张只能换这一个窄角色的证书。
- **对没有人 profile 的机器（外部用户）是一份新的长期凭据**：机器密钥在 `~/.swarmai-hive/identity/`，容器里任务和 Hive 同一个 uid，能读到。拿到它的人在证书到期前（最多 30 天，且下次续期换新密钥后旧密钥作废）能在别处换到这台机器的窄角色：用 Bedrock、读写这台 Hive 自己的 bridge 前缀。这是接受的代价，换来"机器上不用配任何 AWS"；要再收紧就做 A5（旁路容器 + `serve`），只在机器上能取 1 小时的凭据。
- **CA 密钥在 Host 上**：未加密文件、不受 SSO 吊销影响，能离线签证书。能得到的只有本 Host 这些机器的窄角色（每个角色只认自己的 CN 和本锚），比 Host 已持有的账户会话窄。有效期 2 年、按 §4.1 轮换；全部作废就删信任锚。不用 macOS 钥匙串：Host 也跑在 Windows 和 Linux 上。
- 凭据工具按固定 SHA256 校验后才进镜像；校验值写在代码里、随版本升级一起改。

## 6. 对所有用户都一样吗（P7 strip test）

去掉 XG、这台 Mac、这台 Cloud Desktop：机器只要满足"能免密 SSH、能用 docker"（已有的预检），Host 连着 SwarmAI 账户，
就不需要任何 AWS 设置。不依赖 Amazon 内部工具、不认任何公司路径。legacy 账户模式不支持 SSH 机器（今天就如此）。
用受限权限模板（不是 Admin）的账户要按 §9 更新模板才能用——今天的 SSH 机器其实也一样（重试时 `_ensure_role` 调 `update_assume_role_policy`，`:589`，模板里没有）。

## 7. 代价

- Host 侧新代码：CA 和签证书（约百行，`cryptography`）、信任锚 / profile 的创建删除、凭据工具下载校验、续期一轮、迁移步骤、权限模板。
- 机器侧：镜像多一个二进制；安装多两步 SSH 调用；续期每 20 天一次 SSH。
- AWS 侧：每台机器一个 Roles Anywhere profile（免费，配额 250/区），每个 Host 一个信任锚。
- 删掉：机器上找 profile 的全部代码和字段。

## 8. 测试（最终集）

1. CA 生成一次、之后复用；目录和密钥 0700 / 0600；剩不到 6 个月时第二张 CA 进信任锚。
2. 签出的证书：CN 带安装 id、`CA:false`、`digitalSignature`、30 天、`notBefore` 提前 5 分钟、能被 CA 验证；CSR 里塞的主题和扩展被忽略。
3. 窄角色信任策略：只有 rolesanywhere 主体、三个动作、`SourceArn` 和 `CN` 两个条件。
4. 安装流程（录制机器 + 假 AWS）：步骤如 §4.3；机器密钥从未出现在 Host 收发的任何数据里；profile 块里是绝对路径的 `credential_process`，没有 `source_profile`；`carrier_config` 记下 §4.2 列的值。
5. 凭据工具 SHA256 不符 → 安装失败、镜像不建；改造前建的同版本镜像不被复用（标签不同）。
6. 续期跑在 `check_once` 里：剩 9 天的被重签（新密钥），剩 11 天的不 SSH；Host 是开发版、没有机器落后版本时也照样续。
7. 迁移：带 `source_profile` 的机器 update 后身份是 Roles Anywhere、旧块和旧信任被移除；新容器身份不对或 `/health` 不过时回滚，旧容器仍能用旧身份。
8. 移除后同名重加：旧证书的 CN 不被新角色接受。
9. 移除：Roles Anywhere profile（按记下的区域）、角色、`identity/` 都被删，信任锚保留。
10. 权限模板：含 §9 的新动作；`PassRole` 只给 rolesanywhere、只限 `SwarmAI-Hive-*`。
11. 真机两台：Cloud Desktop 删掉 `swarmai` profile 后添加；另一台没有任何 `~/.aws` 的普通 Linux 机器。两台上 Hive 都能调 Bedrock、读写自己的 bridge 前缀，读不了别的 Hive 的前缀。

## 9. 权限

Admin 账户都有。受限权限模板（`core/aws_permissions.template`，给外部用户）要加，并在 `run_hive_checks` 里一起检查：
- `rolesanywhere:{Create,Delete,Get,List,Update}TrustAnchor`、`{Create,Delete,Get,Update}Profile`、`TagResource`；
- `iam:PassRole`，只限 `SwarmAI-Hive-*`、`iam:PassedToService = rolesanywhere.amazonaws.com`；
- `iam:UpdateAssumeRolePolicy`，只限 `SwarmAI-Hive-*`。**这放宽了模板原有的"Hive 角色的信任策略不能改"**：
  持模板权限的人能把某个 Hive 角色的信任改成别的主体。可接受，因为 Hive 角色只能带 SwarmAI 边界创建（模板已有），
  改了信任能得到的也只是边界内的权限；而迁移（§4.5 第 1、4 步）和今天 SSH 机器的重试都需要它。
- 权限不够时安装报错，写明缺的那一项。

## 10. 来源

- 信任锚与证书要求、信任策略、吊销：https://docs.aws.amazon.com/rolesanywhere/latest/userguide/trust-model.html
- `sourceType`：https://docs.aws.amazon.com/rolesanywhere/latest/APIReference/API_Source.html
- Profile：https://docs.aws.amazon.com/rolesanywhere/latest/APIReference/API_CreateProfile.html
- 配额与区域：https://docs.aws.amazon.com/general/latest/gr/rolesanywhere.html
- 凭据工具（版本、下载、SHA256、`serve`）：https://docs.aws.amazon.com/rolesanywhere/latest/userguide/credential-helper.html
- 权限：https://docs.aws.amazon.com/service-authorization/latest/reference/list_awsidentityandaccessmanagementrolesanywhere.html
- 费用：https://aws.amazon.com/about-aws/whats-new/2022/07/aws-identity-access-management-iam-roles-anywhere-workloads-outside-aws/ · https://aws.amazon.com/private-ca/pricing/
