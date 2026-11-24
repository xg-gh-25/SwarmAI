---
title: Swarm Out 第三种执行面：SSH 机器（首个实例：内部 Cloud Desktop）
date: 2026-11-24
created: 2026-11-24
updated: 2026-11-24
project: SwarmAI
status: 定稿（r3）：D1–D8 全部已定（XG 2026-11-24，D8 改为默认放开）；零上下文审查一轮，§14 记了改了什么；spike 已做完（§2）。下一步按 §12 落地
related: docs/design/2026-11-20-swarm-out-multi-carrier.md（派发层、执行面契约，本设计在其上加第三种）；docs/design/2026-10-30-dispatch-center.md（准入、§2.2 私有网络）；Knowledge/Designs/2026-10-08-swarm-hive-mind-TECH-DESIGN.md（A2 载体 = 带身份的执行基座）
---

# Swarm Out 第三种执行面：SSH 机器

## 0. 一页结论

Swarm Out 现在有两种执行面：EC2 Hive 和 AgentCore 弹性 Hive。两种都拿不到内部权限，所以需要内部 repo、内部工具或内网的任务，
在准入时就被拒绝（`core/task_spec.py:378-381`）。本设计加第三种执行面 **`ssh`**：一台人自己的、能 SSH 上去的机器，Host 通过
SSH 隧道把它当一台常驻 Hive 来用。第一个实例是 XG 的内部 Cloud Desktop（`dev-dsk-<alias>-1a`），它本来就有 Midway、toolbox、
brazil 和内网。

这台机器上跑的是**同一个 Hive 包**，装进一个 AL2023 镜像（由 AgentCore 已有的 Dockerfile 派生），机器自己的系统可以是老的。
Host 调的还是同一套 Hive HTTP API，区别是：地址是 Host 本机的一个隧道端口；机器不由 Host 起停；它带着人的身份，能访问私有网络。

和 EC2、AgentCore 最大的不同在安全上：**在这台机器上跑的任务就是以这个人的身份在跑**（SSH 证书、Midway、AWS 凭据）。
任务照常按负载放到这台机器上（D8，"能跑就能用"）；每台机器有一个开关，人可以把它收窄到只接需要内网的任务。

### 要定的决定

| # | 决定 | 建议 | 在哪 | 状态 |
|---|---|---|---|---|
| D1 | 做成通用的"SSH 机器"，还是专门的"Cloud Desktop" | 通用（P7：Cloud Desktop 只是一个实例） | §3 | **已定**（XG 2026-11-24） |
| D2 | 机器上怎么跑 Hive | Hive 包装进镜像（只读），容器挂进人的 `$HOME` 和配置里列的目录 | §5.1 | **已定**（XG 2026-11-24） |
| D3 | Host 怎么连到它 | 每台机器一条 `ssh -N -L` 隧道，本机端口每台固定 | §4.3 | **已定**（XG 2026-11-24） |
| D4 | 机器上 Hive 自己用的 AWS 凭据 | 每台机器一个窄角色 `SwarmAI-Hive-<name>`（和 EC2 Hive 同一份策略），由人的 profile 去 assume | §5.3、§7 | **已定**（XG 2026-11-24） |
| D5 | SSH 证书（Cloud Desktop 上是 Midway）过期 | 派发时已过期：不接新任务；跑到一半过期：不加门槛，run 里的命令失败、照常提问 | §4.2 | **已定**（XG 2026-11-24） |
| D6 | 需要私有网络的任务 | 准入不再一律拒绝；交给调度，只放到能访问私有网络的位置；放不下时仍按"私有网络"拒收、计数 | §4.4 | **已定**（XG 2026-11-24） |
| D7 | 自动更新 | 空闲且版本落后 Host 时自动换新镜像 | §5.4 | **已定**（XG 2026-11-24） |
| D8 | 不需要私有网络的任务能不能放到这台机器上 | 能，默认放开，和在跑的 EC2 一起按负载排；每台机器有一个开关，可以收窄到只接需要私有网络的或点名到它的 | §4.4、§7 | **已定**（XG 2026-11-24：默认放开） |

## 1. 为什么做，做到哪

**问题。** XG 的内部开发（GCRAIDLCPreset、Rocky 等内部 repo）没法派给 Hive：EC2 和 AgentCore 都没有内部身份，
准入看到 repo 是 `kind: internal` 或从私有主机克隆，就直接拒绝（`core/task_spec.py:149-187` 算出原因，`:378-381` 拒绝）。
可是 Cloud Desktop 本来就是一台有内部身份的常驻机器，长任务放上去跑，人合上 Mac 也不影响。

**目标。**

- G1：一台 SSH 机器能被加进 Swarm Out，之后像常驻 Hive 一样被调度、问答、取结果、自动更新。
- G2：需要私有网络的任务可以派到能访问私有网络的位置；没有时，拒收说清楚原因，照样记为"需要私有网络"。
- G3：不碰 EC2 和 AgentCore 的现有行为：EC2 的起停、空闲自动停、升级、重试、账户迁移都不会作用到 SSH 机器上。
- G4：对任何用户、任何能 SSH 上去且有 docker 的机器都成立，不认 Amazon，不认 Cloud Desktop（P7）。
- G5：这台机器上的 Hive 写不了别的 Hive 的 bridge 前缀，也改不了账户桶里的发布包。

**不做。**

- 不由 Host 开机器、关机器，也不替人刷新 SSH 证书（Midway 只能人刷新）。
- 不做这台机器的诊断（读容器日志）；先用面板上的状态和错误，真需要再加（§11 Q1）。
- 不改 run 回来的方式：代码照样以 bundle 回到本机落地（`core/hive_return.py`），内部 repo 的 CR 照样在本机发。

## 2. spike 实测（2026-11-23 ~ 24，XG 的 Cloud Desktop）

| 项 | 结果 |
|---|---|
| 机器 | Amazon Linux 2，x86_64，4 核，15 GB，空闲盘 42 GB，glibc 2.26，docker 25 |
| 直接装在机器上 | 不行：`onnxruntime`（`markitdown → magika` 带进来）和 `pillow 13` 最低要 glibc 2.28 |
| AL2023 容器 | 行：glibc 2.34，`uv pip install -e .[all]` 全部装上（`onnxruntime 1.32`） |
| Hive 后端 2.5.5 | 容器里 7 秒起来，`/health` healthy、`db_healthy:true` |
| Host → 机器 | `ssh -L` 隧道 4 秒建好；每次请求往返约 650 ms（北京 → us-west-2） |
| 凭据 | 机器上 ada 的 profile 解析到 SwarmAI 账户（Admin）；能读 S3 bridge 桶；容器里调 Bedrock haiku 761 ms 返回 |
| 内部工具 | 容器里 `git ls-remote ssh://git.amazon.com/...` 通；`brazil ws create` + `ws use -p GCRAIDLCPreset` 拉到包，`brazil-build` 成功（99 passed） |
| 容器的前提 | 镜像里要有和人同 uid 的用户（否则 ssh 报 `No user exists for uid`）；`libxcrypt-compat`（brazil 的 ruby 要 `libcrypt.so.1`）；挂进 `/apollo`（只读，brazil 的解释器在那）和 `/workplace` |
| 证书 | 机器上的 SSH 证书只有 12 小时（`ssh-keygen -L` 的 `Valid`），git/brazil 都靠它；ada 的凭据在证书过期后仍能用 |
| 机器的 OpenSSH | 7.4，没有 `StrictHostKeyChecking=accept-new`；git.amazon.com 的 host key 要从别处核对后加进 `known_hosts` |
| 意外 | 机器上的 toolbox 曾被一个沙盒会话改坏：`globalInfo.json` 里 27 条路径和 6 个命令链接指向已经不存在的 `~/.sbox-session/<id>/broker-home/...`；已修 |

## 3. 方案对比（D1）

| | A：新执行面 `ssh`（推荐） | B：EC2 执行面的一个变体（换地址） | C：不跑 Hive，Host 直接 SSH 驱动机器上的 CLI |
|---|---|---|---|
| 做法 | `core/carriers/ssh.py`，自己的 offers、位置、client、职责；对 run 的操作和 EC2 走同一套 Hive API | `hive_instances` 加一个"地址类型"，EC2 执行面按它换 CloudFront 和隧道 | Host 在机器上跑 `claude -p` 之类的命令，自己收结果 |
| 能做到 | G1–G5 | G1、G2；G3 做不到：EC2 的空闲自动停、升级（SSM）、开机、重试（会去建一整套 EC2）都会作用到这台机器上 | 只有 G2 的一部分：没有 run record、问答桥、zero-touch 门、Brain 同步 |
| 代价 | 一个新模块，加上 §6 列的那些"只该管 EC2"的地方加执行面检查 | 改动点更多，散在 EC2 的每条生命周期路径里 | 重造一套执行体系，和"同一个 Hive 包"冲突 |
| 判断 | 执行面的职责（`duties`）本来就为这种情况设：机器不由 Host 起停，就不列 `idle_stop` | 正是多执行面设计 §3 方案 C 说的"每多一种执行机，每处再改一遍" | 不选 |

**为什么叫"SSH 机器"。** 产品里的能力要对每个用户成立（SOUL P7）。Cloud Desktop 在这里起作用的性质是"一台人自己的、能 SSH 上去的
机器，带着人的身份和网络"，家里的服务器、公司的开发机也是这样。Amazon 特有的部分（`/apollo` 挂载、证书刷新命令 `mwinit -o`）
都是每台机器的配置，由人（或替人设置的 agent）填。

## 4. 执行面 `ssh`

### 4.1 字段和职责

| 字段 | 值 | 读它的地方 |
|---|---|---|
| `kind` | `ssh` | `registry` |
| `reaches_private` | 按机器配置（默认是），写进每个 offer | 调度的私有网络过滤（`placement.py:47`） |
| `max_run_s` | 无 | run 状态 |
| `brain_lock_scope` | `machine` | 调度的 Brain 版本锁 |
| `duties` | `fleet`、`brain_sync`、`usage_sync`、`follow_host`（§5.4） | 各后台任务（`registry.with_duty`） |

没有 `idle_stop`（机器不由 Host 起停），没有 `diagnostics`（§1 不做）。

### 4.2 位置和 offers

每台机器是 `hive_instances` 里的一行，`carrier = 'ssh'`，和 AgentCore 那一行一样共用这张表和它的 id 空间（`base.py` 的
`Offer.location_id` 就是 `hive_instances.id`）。这一行用到的列：`name`、`status`（`provisioning` / `running` / `updating` /
`error`，和 EC2 同一套词，面板的状态显示照用）、`version`、`account_ref`（Host 的 SwarmAI 账户）、`region`、`s3_bucket`
（账户的 bridge 桶，和 EC2 Hive 同一个）、`auth_user` / `auth_password`（同 EC2：Host 存口令，机器上只有 bcrypt 哈希）、
`instance_type`（写机器的描述，如 `x86_64 · 4 vCPU · 15 GB`，不留默认的 `m7g.xlarge`，`sqlite.py:3075`）、
`iam_role_name`（D4 的窄角色）。新加一列 `carrier_config`（JSON，迁移 v22，只加列）：

```json
{"ssh_host": "dev-dsk-<alias>-1a-0123abcd.us-west-2.amazon.com",
 "remote_port": 18321, "local_port": 28431, "source_profile": "swarmai",
 "reaches_private": true, "general_tasks": true,
 "mounts": ["/apollo:ro", "/workplace"], "cert_renew_hint": "mwinit -o"}
```

`offers(needs)`：每台 `status = running`、账户可管理（同 EC2 的 `_manageable`）的 SSH 机器给一个 offer，层是 `TIER_WARM`。
不能接时写 `blocked`：

| `blocked` | 什么时候 |
|---|---|
| `unreachable` | 隧道建不起来（Host 这边 SSH 认证失败、机器关了、网络断了），或 `admission-state` 没回答；面板显示 ssh 最后一行错误 |
| `cert_expired` | 机器报告的 SSH 证书最晚到期时间已过（§5.2）；面板显示到期时间和 `cert_renew_hint` |
| `out_of_date` | 版本没有 `REQUIRED_CAPS`（同 EC2，`swarm_out.py:1386-1420`） |
| `saturated` | 机器忙（同 EC2） |
| `not_general` | 任务不需要私有网络、也没点名到它，而人把这台机器的 `general_tasks` 关了（D8，默认开） |

**D5 的含义。** 只在派发那一刻看证书，剩余时间多少都照常派。run 跑到一半证书过期，run 里的 git/brazil 命令会失败，agent 按现有方式
提问（经 bridge 问派它的人或 agent），人刷新证书后回答，run 接着做。证书过期这件事本身不进 Needs You：面板上这台机器显示
"证书已过期"，只有真有任务卡在它上面时，才以那个任务的问题出现。

### 4.3 连接：SSH 隧道（D3）

`core/carriers/ssh_tunnel.py`，每台机器一条：

```
ssh -N -o BatchMode=yes -o ExitOnForwardFailure=yes -o ConnectTimeout=10 \
    -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
    -o ControlMaster=no -o ControlPath=none \
    -L 127.0.0.1:<local_port>:127.0.0.1:<remote_port> -- <ssh_host>
```

- **本机端口每台固定。** 加机器时分配一次、记进 `carrier_config.local_port`，以后每次重建都用它。这样长时间持有机器记录的地方
  （relay，`swarm_out_relay.py:58-75`；远程 tab 的流，`routers/swarm_out_remote.py:151`）在隧道重建后照样连得上，也不会出现
  "A 机器的端口被 B 机器的隧道拿走，A 的 Basic 口令发给了 B"。端口被别的程序占了，就报 `unreachable` 并说明，不换端口。
- **`ControlMaster=no`、`ControlPath=none`。** 人的 `~/.ssh/config` 里如果开了连接复用，`ssh -N -L` 会变成复用客户端，
  端口转发留在主连接里，pid 跟踪、结束进程和 `ExitOnForwardFailure` 都失效。用户名、跳板、证书仍来自人的 `~/.ssh/config`。
- **`--` 在主机名前**，`ssh_host` 按主机名字符集校验（以 `-` 开头一律拒绝），参数列表直接交给 `subprocess`，不经 shell。
- **永不关 host key 检查。** 第一次连不上，面板上让人在终端里先连一次。
- **用的时候才建，不在 offers 里建。** `offers()` 只探测已经活着的隧道；没有隧道时返回 `unreachable`，同时在后台建一条（一台机器
  一个任务、一把锁），下一次派发就能用。隧道一旦建好就一直留到 Host 退出（ServerAlive 发现断开后由下一次使用重建）。
  另外，`_offers` 给每个执行面单独计时，一个执行面超时只丢它自己的 offer（记进 `unread`），不再让整个 `_within(..., 20 s)`
  失败、连 EC2 和 AgentCore 的 offer 一起丢（今天是一个预算包住全部，`swarm_out.py:1146-1147`）。
- **派发打开位置时**（`open_run`，不在 offers 里）可以等隧道建好，最多 15 秒（真正的挂起保护：一条永远连不上的 ssh），
  超时就是 `LocationUnavailable`，调度换下一个。
- **不留孤儿。** 子进程的 pid 写在 Swarm Out 状态目录（`swarm_out._state_dir()`）里；Host 启动时，文件里的 pid 若确实是这台机器的
  隧道（命令行对得上）就结束它。Host 退出时结束所有隧道。
- daemon 的 PATH 来自登录 shell（`swarmai_backend.sh:87-99`），`ssh` 读人的 `~/.ssh/config` 和密钥文件，不需要 ssh-agent。
- 请求照样带 Basic 认证，由 Hive 的应用层认证检查（`middleware/hive_auth.py`，Hive 模式下对每个 `/api/*` 生效）。这里没有 Caddy：
  EC2 上 Caddy 是外层、`hive_auth` 是内层；这里只有内层，外层由"Host 只绑 127.0.0.1、机器上的 Hive 只绑 127.0.0.1"代替。
- Windows 的 OpenSSH 也支持 `-L`，这套做法不依赖 ControlMaster。

### 4.4 调度的变化（D6、D8）

1. **准入不再一律拒绝私有网络的任务。** 删掉 `task_spec.admit` 里那条（`:378-381`）；`spec["private"]` 照样算，进
   `Needs.private`（`swarm_out.py:1084`）。调度本来就过滤（`placement.py:47`）。**放不下时仍是"私有网络"的拒收：**
   `needs.private` 非空而调度失败，抛 `SwarmOutRefused(private=True)` 而不是今天 `_placement_refused` 返回的普通
   `SwarmOutError`（`swarm_out.py:1174-1196`），并记进拒收计数的 `private`。这样下游照旧：路由的 422 `private`
   （`routers/swarm_out.py:110-112`）、"在这里做"的卡片（`swarm_out_card.py:242`、`scripts/swarm_out.py:130`）、历史页的计数
   （`HistoryView.tsx:287-289`）。`/spec` 的提示（`routers/swarm_out.py:306`）改成"没有能访问私有网络的位置时才提示"。
   `s_swarm-out` 技能里说 `--needs-private` 会被拒的三处（`SKILL.md:65,94,155`）同步改。
2. **D5（启动停着的 EC2）只看同一种执行面。** 今天 `place` 在"有在跑的 offer 被挡住、原因不是满了"时不启动停着的 EC2
   （`placement.py:81-85`）。SSH 机器的 offer 也是 `TIER_WARM`，它的 `cert_expired` / `not_general` 不该挡住启动一台停着的
   EC2 Hive。改成只数和停着的那台同一个执行面的在跑 offer。
3. **D8：不需要私有网络的任务也放到这台机器上**（XG："能在上面跑的就可以用"），和在跑的 EC2 一起按负载排（`load_key`）。
   人把这台机器的 `general_tasks` 关掉后，offer 在 `needs.private` 为空、也没点名时 `blocked = not_general`，这台机器就只接
   需要私有网络的任务（§7 第 1 条说什么时候值得关）。

### 4.5 对 run 的操作

和 EC2 一样：会话状态、取结果（`hive_records.pull`）、问答、修正发进同一个 session、release，都走同一套 Hive API。
Host 里到达 run 的路径有两条，SSH 执行面两条都要接：

- **经记录**：`registry.carrier(h.carrier).connect(h)` 拿机器记录，再 `registry.client_for(inst)` → `swarm_out._client(inst)`
  （`hive_return.py:238-261`、`swarm_out_results.py:719-749`、`swarm_out_remote.py:88-151`）。SSH 位置的记录自己组装，不经
  `_validate_instance`（它要求 CloudFront 域名，`swarm_out.py:440-451`）：带上 `name`、`region`、`s3_bucket`、`auth_user` /
  `auth_password`、`_account`（`_s3` 和 `_bridge_waits` 要，`:488`、`:2021`），以及 `_base_url`。`swarm_out._base(inst)`
  （`:484`）有 `_base_url` 就用它，没有就照旧拼 CloudFront。`_base_url` 只由位置代码设，不从数据库读。
- **经句柄**：`registry.client_for(RunHandle)` 调 `carrier.client(handle)`（`registry.py:75-76`），用在 `swarm_out.py:2290、
  2545、2774、2798` 和 `swarm_out_live.py:169`。SSH 执行面实现自己的 `client(handle)`，每次创建时从位置 id 现算地址。

**round 要写对执行面。** 今天派发记录在"可能已发出"时只在 `run_ref` 有值时写 `rounds`（`_record_maybe_sent`，
`swarm_out.py:795-806`），否则读的一侧用 `handle_from_legacy_row` 现算一个 EC2 句柄（`base.py:216-219`）；`_first_round`
（`:777-786`）也写死 EC2 和 `cloudfront_domain`。改成：非 EC2 执行面打开的 round 一律写进 `rounds`；`_first_round` 用打开时的句柄。
旧记录（没有 `rounds`）照旧当 EC2。`_order_state`（`:2209`）改用这一行自己的句柄（`registry.handle_of(row)`）。

## 5. 机器上

### 5.1 镜像和容器（D2）

**镜像由 `hive/agentcore/Dockerfile` 派生**（它已经是 AL2023 + Hive 包按 EC2 首次启动的方式装好，`:10-40`）：去掉 supervisor，
加 `openssh-clients`、`libxcrypt-compat`（挂进来的老二进制常要 `libcrypt.so.1`），加一个和人同 uid/gid 的用户（构建参数），
基础镜像按 digest 固定，多架构（x86_64 和 arm64）。**Hive 包在镜像里**（`/opt/swarmai`，属 root，运行用户不可写）：
run 和 Hive 同一个 uid，包要是放在可写的 `$HOME` 下，run 就能改 Hive 自己的门禁代码，而且改动会活过重启
（EC2 上 Hive 以单独的用户 `swarm` 运行，`swarmai-hive.service:12`，就是为了这个）。容器里没有 docker socket，run 改不了镜像。

运行：

```
docker run -d --name swarmai-hive --restart unless-stopped --network host \
  -u <uid>:<gid> -v $HOME:$HOME <配置里的 mounts> --env-file ~/.swarmai-hive/hive.env \
  swarmai-hive-ssh:<Hive 版本> bash /opt/swarmai/hive/swarmai-hive.sh
```

- `--restart unless-stopped`：机器重启后容器自己起来，不依赖 Host。
- 挂进整个 `$HOME`：任务要用人的 SSH 证书、工具、内部凭据，这是这种执行面存在的意义（§7）。
- Hive 的数据放在 `SWARM_DATA_DIR=~/.swarmai-hive/data`（`config.py:47-62` 的覆盖），不碰人可能有的 `~/.swarm-ai`。
- `--network host`：Hive 只绑 127.0.0.1，隧道才能到；代价见 §7 最后一条。

### 5.2 Hive 包里要改的（只在 `SWARMAI_CARRIER=ssh` 时生效）

1. **bridge 的身份从环境里来。** 今天 EC2 Hive 从 IMDS 的角色名（`SwarmAI-Hive-<name>`）得出自己的名字（`swarm_out_bridge.py:
   140-166`）；Cloud Desktop 的 IMDS 是公司账户的角色，对不上，bridge 就关了。加一条和 AgentCore 同样形状的分支
   （`run_bridge_loop`，`:496`）：名字、桶、region 来自 `SWARMAI_HIVE_NAME` / `SWARMAI_HIVE_BUCKET` / `AWS_REGION`，不读 IMDS。
2. **`admission-state` 报告 SSH 证书最晚到期时间。** `routers/chat.py:898` 的返回里加 `ssh_cert_valid_until`：`~/.ssh/*-cert.pub`
   里每张证书 `ssh-keygen -L` 的 `Valid ... to` 取最晚的一张（Unix 秒）；没有证书时是 `null`，不按证书挡。
   EC2 和 AgentCore 忽略这个字段（`parse_admission` 只取它要的字段，`ec2.py`）。
3. 其余不改：`hive_auth` 走 `Authorization`，和 EC2 一样；凭据由默认链从 `AWS_PROFILE`（D4 的窄角色 profile）取。

### 5.3 加一台机器（安装）

Swarm Out › 设置里加一台（§8），Host 在后台做，`status` 走 `provisioning → running`，失败是 `error` 加 `error_message`：

1. **预检**（ssh 一次，只读）：能 BatchMode 登录；`docker info` 能用；`$HOME` 所在盘空闲 ≥ 5 GB；`remote_port` 空着；
   顺带记下 `toolbox --version` 之类常用工具能不能跑（只写进安装记录，不挡）。
2. **窄角色（D4）。** Host 用自己的账户凭据建 `SwarmAI-Hive-<name>`：策略和 EC2 Hive 角色同一份（只读 `v*` 包，只写自己
   `bridge/<name>/` 下的 `ask/`、`closed/`，只读自己的 `answer/`，`hive/provisioner.py:90-130`），打 `HiveName` 标签，
   信任人在机器上的 `source_profile` 所解析到的那个身份（由第 4 步读到的 ARN 决定）。
3. **镜像**：Host 送构建上下文（Dockerfile + 从账户桶取到并按 `checksums.txt` 校验过的 Hive 包），机器上 `docker build`。
4. **凭据**：写 `~/.aws/config` 里的一个新 profile `swarmai-hive-<name>`（`role_arn` = 窄角色，`source_profile` = 人填的那个），
   只新增、不改别的 profile；在容器里用它调 `sts get-caller-identity`，**ARN 必须是这个窄角色**，不是就失败并说明。
5. **环境文件** `~/.swarmai-hive/hive.env`（`600`）：`SWARMAI_MODE=hive`、`SWARMAI_CARRIER=ssh`、`SWARMAI_HIVE_NAME`、
   `SWARMAI_HIVE_BUCKET`、`AWS_REGION`、`AWS_PROFILE=swarmai-hive-<name>`、`SWARM_DATA_DIR`、`SWARMAI_PORT`、`HIVE_USER`、
   `HIVE_PASS_HASH`。经 ssh 的 stdin 送，不进命令行参数，不进日志；Host 只送 bcrypt 哈希。
6. **启动并等 `/health`**（经隧道，最多 60 秒）。

每一步可以重跑（已完成的跳过）。失败后的"重试"走 SSH 自己的接口（§6），不走 EC2 的 `/instances/{id}/retry`。

**发布包要先确认是真的。** 账户桶里已有 `v<版本>/` 包和 `checksums.txt` 时，EC2 的 `_sync_release_to_s3` 直接信它
（`hive/provisioner.py:713-716`）。在本设计之前，能写这个桶的只有人的 Host；本设计之后多了一台以人身份跑任务的机器。
所以第 3 步（以及 EC2 的部署和升级）改成：桶里的 `checksums.txt` 必须和 GitHub 发布页上同版本的 `.sha256` 一致才用，
不一致就拒绝并说明。窄角色本身写不了 `v*`，这一条防的是人的身份（§7）。

### 5.4 更新和移除（D7）

- **跟随 Host 的版本。** 机器空闲（同一个 `busy_reasons`：没结的 run、在流、审查任务、正在派发，`hive_idle.py:97`）且版本
  落后 Host 时：构建新版本镜像 → 停旧容器 → 起新容器 → 等 `/health`；新容器起不来就起回旧的，`error_message` 写原因。
  这一步在 `hive_idle.check_once` 里**另加**一段，只对有 `follow_host` 职责的非 EC2 执行面调用它自己的方法；EC2 的那段原样不动
  （EC2 的跟随和空闲停照旧在 `idle_stop` 下，`hive_idle.py:291-340`）。更新期间 `status = updating`，每分钟写一次心跳，
  这样 `release_stale_updates`（`provisioner.py:1686-1702`）不会把它当成卡住的 EC2 升级退回 `running`；那个函数本身也加
  `carrier = 'ec2'` 过滤（§6）。
- **移除。** 停掉并删除容器和镜像，删掉窄角色和机器上新增的那个 profile，删掉 `hive_instances` 那一行。`~/.swarmai-hive/data`
  留在机器上（不替人删数据，P4），面板上告诉人它在哪。

## 6. Host 里要改的地方

先改读的一侧，再让接口写 `carrier = 'ssh'` 的行。

**只该管 EC2 的地方，加执行面检查**（今天它们按 `status` 或 id 选行，SSH 机器的行也会被选中）：

| 位置 | 不改会怎样 |
|---|---|
| `core/carriers/ec2.py:196` `_hive_rows`、`:439` `running` | EC2 执行面给 SSH 机器出 offer、跑 EC2 的定时任务 |
| `core/hive_idle.py:148` `_running_hives` | 用 EC2 provisioner 去停或升级 SSH 机器 |
| `routers/hive.py` 的 `/instances/{id}` 全部改动接口：stop `:595`、start `:618`、restart `:640`、update `:703`、reset-password `:791`、retry `:827`，以及已用 `_is_elastic` 的 list / delete / replace（`:505`、`:748`、`:780`） | 例如 retry 会对 SSH 机器的行跑 `cleanup` 再 `provisioner.deploy`，建一整套 EC2（`:930-955`）。改成一个"只接 EC2 行"的检查，统一用在每个接口上 |
| `hive/provisioner.py:1686-1702` `release_stale_updates` | SSH 机器的更新被当成卡住的 EC2 升级退回 `running` |
| `hive/accounts.py:235-245`（改指向账户前的检查数了所有行）、`:355-372` `keep_old_hives` | SSH 机器挡住账户切换，或被悄悄挪到"旧账户"下变得不可管理 |
| `hive/accounts.py:237、300、337、359`、`routers/hive.py:291、351、374` 的其余查询 | 逐条看，属于"只管 EC2"的加过滤 |

已经安全的不改：`hive/accounts.py:435` 的 `COUNTED_SQL`、`core/machine_cost.py:165` 和 `hive/fleet.py:374`（都按
`ec2_instance_id` 过滤）。实现第一步把 `core/`、`hive/`、`routers/` 里全部读写 `hive_instances` 的语句（约 75 处）逐条分成
"只管 EC2"和"所有位置"两类（R27），上表是已经确认的部分，不是全部。

**对每台机器都要做、今天写死了 EC2 的地方，改成按这一行的执行面找位置**（新加 `registry.location_of(ref)`：读这一行的
`carrier`，返回那个执行面的位置；调用方再看那个执行面有没有对应职责）：

| 位置 | 做什么 |
|---|---|
| `core/swarm_out.py:2009` `_read_hive` | 列一台机器的待答问题和会话状态 |
| `core/swarm_out.py:3141` `_wait_bridge` | 回答问题时找 bridge |
| `core/swarm_out.py:2954` `_admission`、`:2972` `fleet` | 面板的机器探测 |
| `core/swarm_out.py:691` `dispatch` 的旧路径 | 点名派发 |
| `core/brain_sync_host.py:255` `hive_of` | Brain 同步 |
| `core/usage_hive_sync.py:122` `_pull` | 用量同步 |
| `core/hive_idle.py:161` `_sessions` | 判断空闲 |

**round 和句柄**（§4.5）：`_record_maybe_sent`、`_first_round`、`_order_state`。

**新加的**：`core/carriers/ssh.py`、`ssh_tunnel.py`；`registry._MAKERS` 加 `ssh`（`registry.py:25`）；`swarm_out._base` 认
`_base_url`；`_offers` 按执行面分别计时；`hive/ssh_provisioner.py`（§5.3、§5.4）；`routers/hive.py` 的 `/ssh` 一组接口（加、更新、
重试、删除、开关 `general_tasks`）；迁移 v22。

## 7. 安全

**这台机器上的任务以人的身份跑。** 容器挂着人的 `$HOME`：SSH 密钥和证书、Midway cookie、人自己的 AWS profile。这是这种执行面
存在的理由（内部任务要的就是人的身份），所以没法、也不该靠"把身份藏起来"来防：同一个 uid 的 run 能读到人的 profile，能直接用
人的身份做人能做的事。能做、而且本设计做了的是：

1. **哪些任务到这里来，由人定（D8）。** 默认什么任务都可以放过来（XG 2026-11-24 定："能跑就能用"）。这意味着 Swarm Run 的
   子任务、处理公开 repo 里不受信任内容的任务，也可能以人的身份在这台机器上跑。人如果在意这一点（比如机器上有不想让任务碰到的
   东西），关掉这台机器的 `general_tasks`，它就只接需要私有网络的任务和点名到它的任务。
2. **Hive 自己只用窄角色（D4）。** Hive 进程（bridge、记录、用量）用 `SwarmAI-Hive-<name>`，和 EC2 Hive 同一份策略：写不了别的
   Hive 的 `bridge/` 前缀，写不了 `answer/`，写不了 `v*` 发布包。Hive 自己的代码出错，波及不到别的 Hive。
3. **发布包按 GitHub 的校验值确认**（§5.3 最后一段），机器写坏了账户桶里的包，EC2 Hive 也不会装。
4. **Hive 的门禁代码在只读镜像里**（§5.1），run 改不了。
5. **派出去的任务 zero-touch**：危险命令当场拒绝，push、发布这类保留动作回到人手里（`routers/chat.py:960-971` 的 `fixed_reserved`、
   `zero_touch`）。
6. 面板上加机器时写明这一点：这台机器上的任务以你的身份运行。

其余：隧道只绑 Host 的 `127.0.0.1`，请求照样要 Basic 认证，口令不出 Host；不关 host key 检查；ssh 命令是固定参数列表。
`--network host` 让 run 能访问机器自己的元数据服务（Cloud Desktop 上是公司账户的角色）：人在这台机器上本来就能用它，
run 以人的身份跑，不额外防；改用桥接网络要让 Hive 绑 0.0.0.0，得不偿失。

## 8. 面板

Swarm Out › 设置今天有三块：账户、Elastic Workers、常驻 Hive（`SwarmOutSettings.tsx:114`、`:276`、`:323`）。在 Elastic Workers
和常驻 Hive 之间加一块 **"SSH 机器"**，样子和常驻 Hive 那块一样（同一个 `Head`、同一种可展开的行，`:323-439`），按钮接到 `/ssh`
接口：

- 行：名字、`instance_type` 里的机器描述和版本、状态词（运行中 / 证书已过期 / 连不上 / 正在安装 / 正在更新 / 出错）。
- 展开：SSH 主机、证书到期时间和刷新提示、登录（`AuthDisplay`）、Brains（`HiveBrains`）、"也接不需要内网的任务"开关（默认开）；按钮：重试（出错时）、删除。
- 加一台：输入 SSH 主机，名字默认取主机名第一段；必填 AWS profile（会以它为源 assume 窄角色）；高级里填挂载目录、证书刷新提示、
  能否访问私有网络。说明里写清楚 §7 第 6 条。

另外几处今天按"agentcore 还是 ec2"二选一，要认第三种：`FleetPills.tsx:31,52`、`RunBoard.tsx:86`、`SwarmOutWelcome.tsx:49`
（哪些状态算能接任务）、`CostSection.tsx:46-73` 和 `costStats.ts:28`（写死两种执行面并用 `k.sides.ec2!`；SSH 机器没有机器费，只算模型费）。

## 9. 失败模式

| 情况 | 结果 |
|---|---|
| Host 这边 SSH 证书过期 | 隧道建不起来，offer `unreachable`，面板显示 ssh 的错误；在跑的 run 不受影响，Host 暂时读不到它，回来后接上 |
| 机器上的证书过期 | 新任务不派过去（`cert_expired`）；在跑的 run 按 D5 |
| 机器关机或断网 | `unreachable`；派发换别的位置（需要私有网络的就拒收，原因写这台机器连不上）；不拖慢 EC2 和 AgentCore 的 offer（§4.3） |
| 机器重启 | 容器自己起来；隧道下次用时重建，端口不变 |
| Host 重启 | 旧隧道按 pid 文件结束，用时重建 |
| 本机端口被别的程序占了 | `unreachable` 并说明是哪个端口 |
| 机器上的工具坏了（§2 的意外） | 不自动修；run 里的命令失败，按问题出现；安装记录里有预检看到的工具状态 |
| 安装或更新中途失败 | `error` 加原因；更新失败时起回旧容器；重试跳过已完成的步骤 |

## 10. 和已有决定的关系

| 已有决定 | 关系 |
|---|---|
| 多执行面 D1（派发层在 Host 里） | 不变；隧道也在 Host 里 |
| 多执行面"AgentCore 拿不到内部权限，需要的任务准入拒绝（对 EC2 也一样）" | 改：准入不再拒，交给调度（D6） |
| hive-mind D1（内部 repo 要带身份的执行基座） | 本设计就是它的 A2 载体 |
| 派单中心 §2.2（私有网络的判断） | 判断不变，只是拒绝从准入挪到调度 |

## 11. 开放问题

| # | 问题 | 怎么定 |
|---|---|---|
| Q1 | 要不要读这台机器的容器日志（诊断职责） | 先不做；真遇到看不到原因的失败再加 |
| Q2 | 一台机器上的 Hive 和人自己的工作（kirocrew 等）抢资源 | 由 `admission-state` 的内存和在流数挡；不够再加每台机器的并发上限 |
| Q3 | 窄角色的信任对象：人的 `source_profile` 解析到的是 Isengard 的 Admin 角色会话，信任它等于信任这个角色 | 实现时按第 4 步读到的 ARN 设；够用，若要更窄再看 |

## 12. 分步落地和等待关系

一个需求，一次 pipeline run（`full`），下面是它的任务和谁等谁：

| 步 | 内容 | 等 |
|---|---|---|
| S1 | 迁移 v22；`hive_instances` 的全部语句逐条分类，只管 EC2 的加检查（§6 第一张表）；`registry.location_of` 和第二张表的七处；round 和句柄的三处。EC2 和 AgentCore 行为不变 | 不等 |
| S2 | Hive 包里的两处（§5.2）；派生镜像；发布包按 GitHub 校验（§5.3 最后一段，EC2 也用）；`hive/ssh_provisioner.py` 的安装、更新、移除，含窄角色 | 不等（和 S1 同时） |
| S3 | `ssh_tunnel.py`、`ssh.py`（offers、位置、client、记录）、`_base_url`、`_offers` 按执行面计时、`follow_host` | S1 |
| S4 | 准入和调度的三处变化（§4.4），含 `SwarmOutRefused(private)`、技能文本 | S1 |
| S5 | `/ssh` 接口和面板（§8） | S2、S3 |
| S6 | 在 XG 的 Cloud Desktop 上端到端验收（§13 第 12 条） | S2–S5 |

S1、S2 同时做；S1 之后 S3、S4 同时做。

## 13. 验证计划

**谁读新写的东西：** `carrier = 'ssh'` 的行被约 75 处语句读到，S1 先分好类、改好，S5 才让接口写这种行；`carrier_config` 只有
SSH 执行面和它的 provisioner 读；`ssh_cert_valid_until` 只有 SSH 执行面读；派发记录里 SSH 的 `rounds` 由所有对 run 的调用读，
S1 先让读的一侧认它。

**最后必须成立的不变量（最终测试集）：**

1. 一行 `carrier = 'ssh'`、`status = 'running'`：EC2 的 offers 里没有它；`hive_idle` 不会用 EC2 provisioner 认领、停止或升级它；
   `/instances/{id}` 的每个改动接口都拒绝它（逐个接口测）；`release_stale_updates` 不碰它；账户切换不数它、不挪它；它不计入 Hive 数量。
2. §6 第二张表的七处，对 SSH 机器拿到的是 SSH 位置，对 EC2 Hive 行为不变（现有测试全绿，断言不改）。
3. 地址：`_base(inst)` 只在位置代码设了 `_base_url` 时用它；SSH 的 `client(handle)` 每次从位置现算；数据库里任何列都改变不了
   请求的地址；隧道重建后端口不变。
4. round：一次"可能已发出"的 SSH 派发，记录里有 SSH 的 `rounds`，之后的状态、结果、live、release 都走 SSH 执行面；旧的、没有
   `rounds` 的记录照旧当 EC2。
5. `place`：需要私有网络的任务只落在 `reaches_private` 的位置；没有时抛 `SwarmOutRefused(private=True)`，拒收计数的 `private`
   加一，原因里有每个位置；不需要私有网络的任务在 `general_tasks` 开（默认）时能落在 SSH 机器上、关时不落；SSH 机器 `cert_expired` 或
   `not_general` 时，停着的 EC2 照样能被选中。
6. `admit` 不再因为私有网络拒绝；`/spec` 只在没有能访问私有网络的位置时提示。
7. SSH offer 的 `blocked`：隧道不在 → `unreachable` 且后台建一条；证书过期 → `cert_expired`；没有证书 → 不挡；版本不够 →
   `out_of_date`；忙 → `saturated`；D8 → `not_general`。
8. 一台连不上的 SSH 机器不让 EC2 和 AgentCore 的 offer 丢失：`_offers` 里 SSH 超时，其余 offer 照样在，派发照样成功。
9. 隧道：并发的十次使用只建一条；命令里有 `ControlMaster=no`、`ControlPath=none`、`--`，没有 `StrictHostKeyChecking=no`；
   以 `-` 开头的 `ssh_host` 被拒；Host 重启后旧 pid 被结束；`open_run` 15 秒连不上就是 `LocationUnavailable`。
10. Hive 包：`SWARMAI_CARRIER=ssh` 时 bridge 身份来自环境，不访问 IMDS；`admission-state` 报证书到期时间；EC2 和 AgentCore
    模式下两处都不变；镜像里 `/opt/swarmai` 对运行用户不可写。
11. 安装：口令不出现在任何命令行参数和日志里，机器上只有哈希；窄角色的 ARN 不对就失败；`~/.aws/config` 里只新增一个 profile；
    桶里的 `checksums.txt` 和 GitHub 不一致时拒绝（EC2 部署也是）；任一步失败行变 `error` 带原因，重试跳过已完成的步骤；
    更新失败起回旧容器。
12. 端到端（S6，在 XG 的 Cloud Desktop 上，手动）：从面板加机器 → 派一个 GCRAIDLCPreset 的小任务（私有网络）→ 任务里用 brazil
    构建 → 经 bridge 回答一个问题 → 结果以 bundle 回到本机 → 关掉 `general_tasks`，派一个不需要内网的任务，确认它不去这台机器，再打开 → 把 Host 版本调高一次，
    空闲时机器自动换新镜像。

**评审：** S1 做 correctness + integration（逐条分类）；S2 做 security（窄角色、凭据传递、发布包校验）；S3 做 correctness +
concurrency + security（隧道、孤儿进程、地址来源）；S4 做 correctness；S5 做 ux。

## 14. r2 改了什么（零上下文审查一轮）

| 原因 | 审查发现 | 改法 |
|---|---|---|
| 人的身份太宽 | Admin profile 能写任何 Hive 的 `answer/`、能改发布包（`provisioner.py:713-716` 见到包就信）；不需要内网的任务也会按负载落到人的身份上；包在可写的 `$HOME` 下 | D4 窄角色、发布包按 GitHub 校验、包进只读镜像（§5、§7）；D8 加每台机器的开关（r2 建议默认关，XG 定为默认开） |
| EC2 专用路径会作用到 SSH 行 | `/instances/{id}` 的 stop/start/restart/update/reset-password/retry 没有执行面检查；`release_stale_updates`；账户切换的两处；状态词用错（`failed` 应为 `error`） | §6 第一张表补全，统一检查；状态词和 EC2 一致；重试走 `/ssh` |
| 到达 run 的路径不全 | `client_for(RunHandle)` 走 `carrier.client`；"可能已发出"时不写 round，读时当 EC2；`_first_round`、`_order_state` 写死 EC2 | §4.5 两条路径都接，round 一律写 |
| 隧道的生命周期 | 在 offers 里建隧道会让 `_place` 的 20 秒总预算超时、丢掉所有 offer；端口换了以后长期持有的记录连错；ControlMaster 让 pid 跟踪失效 | offers 不建隧道、`_offers` 按执行面计时、端口固定、`ControlMaster=no` |
| 私有网络的信号丢了 | 调度拒收是普通 `SwarmOutError`，路由的 422、"在这里做"卡片、历史计数都不再触发 | 抛 `SwarmOutRefused(private=True)` 并计数；技能文本同步 |
| 引用和细节 | daemon 只继承 PATH；语句约 75 处不是 49；`costStats.ts` 也写死两种；`instance_type` 默认值 | 已改 |
| 过度设计 | 新写 Dockerfile 而不复用 AgentCore 的；`follow_host` 拆 EC2 的职责 | 从 AgentCore 的派生；`follow_host` 只另加一段，EC2 那段不动 |
