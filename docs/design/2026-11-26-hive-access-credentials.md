---
title: Hive access credentials — one key per person, the agent cannot change the lock
date: 2026-11-26
created: 2026-11-26
updated: 2026-11-26
project: SwarmAI
---

# Hive 访问凭证：一人一把钥匙，agent 改不了门锁

状态：设计稿 r2（已过一轮无上下文安全评审），待 XG 确认 · 2026-11-26

## 1. 问题（已对照代码和线上核实）

EC2 Hive 的公网入口依次经过 CloudFront、Caddy `basic_auth`、后端 `HiveAuthMiddleware`。

**问题一：整台 Hive 只有一组凭证。**

- 用户名固定为 `admin`，密码由 `generate_password()` 生成：从 256 个词里取 4 个，约 32 bit（`hive/user_data.py:277`，部署时 `hive/provisioner.py:1664`）。
- 这一组凭证给了三类调用方：
  - 桌面端 Host（`core/swarm_out.py:497`）；
  - 主人自己的浏览器；
  - 被分享的人：Share 按钮会复制 URL 加这组密码（`desktop/src/components/settings/hiveComponents.tsx:440`）。
- 有两个 API 会返回明文密码：
  - `/credentials`（`routers/hive.py:932-948`）；
  - `GET /instances/{id}`，它的 `HiveInstanceDetailResponse` 带 `auth_password`（`routers/hive.py:348-358, 674-682`）。
- 不能单独撤销某一个人。没有 SSO，也没有 MFA。

**问题二：Hive 上的 agent 能改自己的门锁。** 这是评审发现的，比密码更严重。

- 后端和 agent 以 `swarm` 用户运行（`hive/swarmai-hive.service:12`）。
- 后端那一层凭证文件 `/etc/swarmai/hive-auth.env` 被 `chown swarm:swarm`（`hive/user_data.py:205`），agent 可以直接改写它。
- Caddy 以 root 运行（`user_data.py:213` 的 unit 里没有 `User=`），而且 admin API 开在默认的 `localhost:2019` 上（全局配置里没有 `admin off`，`user_data.py:86-98`）。
- 所以 agent（或者被网页内容注入指令的 agent）可以 POST 一份新配置给 Caddy：去掉认证，或者让以 root 运行的 Caddy 把任意文件当静态文件读出来。
- 后果：任何一个能登录的人都能给自己留后门，撤销对他就失效了。

**已经没问题、本设计不动的部分：**

- 实例角色的凭证离开这台机器就不能用：`DenyOutsideHiveElasticIp` 和 `DenyOutsideHiveVpc`（`hive/provisioner.py:186`）。线上 `SwarmAI-Hive-xg-swarm-remote-demo` 的角色已经有这两条，2026-11-26 用 `aws iam get-role-policy` 读过。
- 安全组只放行 CloudFront，另有 origin secret。
- S3 权限只限本 Hive 自己的前缀。
- SSH 机器只能通过 SSH 隧道访问，密码是 `token_urlsafe(24)`。它没有公网入口，不在本设计范围内。

## 2. 方案

**原则：机器的钥匙人看不到；人的钥匙一人一把，可以单独撤销；agent 碰不到门锁。**

### 2.1 一人一把钥匙

| 调用方 | 用户名 | 密码 | 谁能看到 |
|---|---|---|---|
| Host（Swarm Out） | `host` | `token_urlsafe(24)` | UI 和 API 都拿不到，只存在本机 `data.db` 里 |
| 每个人的浏览器 | 这个人的名字，限 `[a-z0-9-]{1,32}`，同一台 Hive 内唯一，`host` 和 `admin` 保留不可用 | `token_urlsafe(24)` | 创建时只显示一次；Host 只存 hash |

- 192 bit 的随机 token 猜不中，也没法离线破解，所以不加锁定和限速，hash 留在 user-data 里也没关系。
- bcrypt cost 保持 14 不变：Caddy 对不存在的用户名也会用一个 cost 14 的假 hash 做比较，降低 cost 不会减少未认证请求的开销。

### 2.2 一份名单文件，两层都读它

- Hive 上新增 `/etc/swarmai/hive-users`，`root:swarm 0640`，每行一条 `用户 hash`。agent 能读（读到的只是高熵 token 的 hash），但不能写。
- Caddy 的 `basic_auth @protected` 块改成 `import /etc/swarmai/hive-users`。
  - 如果真机验证发现 Caddy 不支持在块内 `import`，退回方案：由同一个脚本把同一份名单写进 Caddyfile 的块里。
- 后端 middleware 的规则：
  - 这个文件存在时，只认它。按用户名查表，一次请求只做一次 bcrypt。文件格式错误或为空时，全部拒绝，不回退到旧凭证。
  - 文件 mtime 变化时重新读取，不需要重启后端，所以不会中断正在跑的任务。
  - 文件不存在时，才使用旧的 `HIVE_USER` / `HIVE_PASS_HASH`。SSH 机器和 AgentCore 继续走这条路，行为不变。
- EC2 上的 `hive-auth.env` 改成 `root:root 0600`（systemd 以 root 读取 EnvironmentFile），里面的旧凭证对改成 `host`。这样回退到老版本包时，只有 Host 还能用，个人访问全部失败，失败方向是关闭的。

### 2.3 agent 碰不到门锁

- Caddy 全局配置加 `admin off`。现有所有改 Caddy 的路径都用 `systemctl restart caddy`，没有一处用 reload，所以不受影响。
- 结合 2.2 的文件权限，agent 既改不了名单，也改不了 Caddy 配置。

### 2.4 一个写入口：`apply_access(hive)`

- Host 新增 `hive_access(id, hive_id, name, pass_hash, created_at)` 表，外键 `ON DELETE CASCADE`。
- `hive_instances.auth_user/auth_password` 改为存 Host 自己的钥匙，所以 `swarm_out._auth` 不用改。
- apply 的步骤：
  1. 用 `host` 加上所有未撤销的个人钥匙拼出完整名单。
  2. 通过 SSM 写入 `/etc/swarmai/hive-users`，`restart caddy`。
  3. 写入失败时回滚。脚本不输出 hash 或 token：SSM 的输出会进入错误信息（`routers/hive.py:931`）。
- 并发：apply 和 update 用同一种 status claim（`UPDATE … WHERE status='running'`，与 `provisioner.py:1593` 是同一个模式）。claim 不到就拒绝。这样一次撤销不会被一次更早开始的 apply、update 或 `update_caddyfile` 的回滚覆盖。
- 轮换 Host 钥匙分两步：先把新旧两把都写进名单，确认新钥匙能用，再写库并删掉旧钥匙。这样 SSM 超时也不会把 Host 锁在门外。
- 删掉 `reset_password`，它的用途由 apply 和轮换取代。
- 重试部署（`routers/hive.py:1059`）时，用 user-data 写出当前名单。
- `fleet.replace` 会建一台新 Hive，个人钥匙不带过去，主人需要重新添加。UI 在替换时会说明这一点。

### 2.5 UI 和 API

- Hive 卡片去掉密码的显示和复制按钮。
- Share 改为「添加访问」：填名字，弹出一次性的 URL、用户名和密码。
- 卡片下面列出现有访问（名字、创建日期、撤销按钮），菜单里加「轮换 Host 钥匙」。
- 删掉 `/credentials`，删掉 detail 响应里的 `auth_password`。新的添加、撤销、轮换三个路由在 Swarm Out ops registry 里登记为只有人能调用。
- 说明一个事实：本机的 agent 仍然能读 `data.db`，所以「人看不到 Host 钥匙」只对 UI 和 API 成立。

### 2.6 备份

- `db-export` 导出 `hive_instances` 时把 `auth_password` 抹掉（`core/backup_manager.py:103`）。
- `hive_access` 只导出 hash。
- 恢复后 Host 没有钥匙，卡片提示「轮换 Host 钥匙」；轮换只需要 AWS 凭证和 SSM，不需要旧钥匙。

### 2.7 审计

Caddy 访问日志本来就记录 `user_id`。一人一把钥匙之后，每个请求都能知道是谁发的。

## 3. 老 Hive 怎么迁移

- 在下次「更新 Hive」时自动完成，只处理 EC2 行（`_is_ec2`），SSH 和 Elastic 行不受影响。
- 顺序：
  1. 装新包。middleware 能读名单文件，Caddyfile 里加上 `admin off`，`hive-auth.env` 收紧权限。
  2. 生成 Host 钥匙。
  3. apply 一份名单：`host` 加上一条名为「旧共享密码」的访问。这一条用户名仍是 `admin`，hash 不变，cost 14。
- 原来分享出去的人不会被踢掉，主人点一次撤销即可收回。
- 迁移完成之前，旧的明文密码保留在库里；apply 成功之后才删除。
- 没升级的 Hive 拒绝 apply，并提示「先更新这台 Hive」。

## 4. 不做，以及原因

- **SSO / MFA（在 CloudFront 前面接 Cognito 或 OIDC）**：每个账号要建 user pool、登录页和 edge 函数，成本最高。做完本设计后，猜密码不可能了，泄露只影响一个人且可以单独撤销，agent 也没法给自己留门。如果以后要 MFA，在 CloudFront 前面加一层就行，本设计这一层不用改。
- **撤销能把人彻底踢干净**：做不到，所以不承诺。被撤销的人在有权限期间可以通过 agent 以 `swarm` 用户身份埋下东西，比如 crontab 或主动外连。撤销只保证他不能再登录。如果访问日志显示某把泄露的钥匙曾被别人用过，处理方式是替换整台 Hive（`fleet.replace`），不是只撤销钥匙。
- **收窄 Bedrock 的 `Resource: *`**：凭证离开机器就失效已经在线上生效。登录进来的 agent 本来就要用 Bedrock，收窄到具体模型并不能减少这部分花费，反而每换一次模型都要改策略。
- **花费上限**：按 STEERING #2 不加。
- **Caddy 改为非 root 运行、源站这一跳改 TLS、未认证请求的 bcrypt 开销**：前两项是已记录的遗留问题；`admin off` 之后 Caddy 以 root 运行不再能被 agent 利用。第三项至今没有出现过。三项都不在本设计里做。

## 5. 验证计划

**最后必须成立的不变式：**

- 名单里任一条凭证，在 Caddy 和后端两层都能通过。
- 撤销的凭证在两层都返回 401。
- 名单文件为空或格式错误时，全部拒绝，不回退。
- SSH 机器和 AgentCore 的旧路径不变。
- 任何 `/api/hive` 的响应里都没有 Host 钥匙。
- `swarm` 用户写不了名单文件和 `hive-auth.env`，也连不上 Caddy 的 admin API。

**单元测试：**

- middleware：
  - 名单通过；撤销后拒绝；文件存在时旧凭证对不再生效；格式错误时拒绝；mtime 变化后生效；一次请求只做一次 bcrypt。
  - 对「文件存在时只认文件」和「格式错误时拒绝」两处做 mutation check。
- Host：
  - 添加、撤销、轮换都写出完整名单；用户名校验。
  - claim 不到时拒绝。
  - 迁移保留「旧共享密码」，且明文只在 apply 成功之后才删除。
  - 导出里没有 `auth_password`。
- 路由：遍历所有 `/api/hive` 路由的响应，断言里面没有 Host 钥匙。
- user-data 和 update 脚本：断言全局配置有 `admin off`，文件属主和权限正确，有 caddy 时跑 `caddy validate`。

**真机验证（xg-swarm-remote-demo，更新之后）：**

- Swarm Out 派一单能跑通。
- 新加的访问能在浏览器登录。
- 撤销后返回 401：经 CloudFront 测一次，再通过 SSM 在本机直接打后端测一次。
- 「旧共享密码」撤销前能用，撤销后返回 401。
- 以 `swarm` 用户执行 `curl localhost:2019/config/` 连接被拒，`echo >> /etc/swarmai/hive-users` 被拒。
- Caddy 访问日志里出现 `user_id`。
- 确认 Caddy 是否支持在块内 `import`，结果决定 2.2 用哪个写法。

**评审：** 代码完成后跑 `s_expert-review`，请 security 和 correctness 两位专家，最多两轮。

## 6. 执行计划

这次改动跨 Hive 包、Host、UI 三块，还涉及安全，按 AGENT R1 走一次 pipeline（profile: full），一个 run。

| 任务 | 依赖 |
|---|---|
| A. Hive 包：middleware 读名单文件；user-data 和 update 加 `admin off`、调整文件权限、写名单文件 | 先定名单格式（本设计 2.2），之后不依赖其他任务 |
| B. Host：`hive_access` 表、`apply_access`、轮换、迁移、删除 `/credentials`、`reset_password` 和 detail 里的密码、导出抹密码、ops registry | 名单格式 |
| C. UI：添加访问、访问列表、撤销、轮换，删除密码显示 | B 的 API 形态 |
| 真机验证 | A、B、C 合入并 build 之后，更新 demo Hive |

A 和 B 可以同时做，C 在 B 之后。

**要一起改的旧消费方：**

- `tests/test_hive_provisioner.py` 里的 `reset_password` 用例；
- 桌面端 mock `getCredentials` 的三个测试、`hive.ts:70` 的 `authPassword`；
- `en` / `zh` 两份 i18n 里的 share 文案；
- `reset_password` 和 `swarmai-hive.service` 里过时的 docstring；
- `hive/Caddyfile`、`hive/.env.example`；
- `_TEMPLATE_VARS`（`user_data.py:1019`）。
