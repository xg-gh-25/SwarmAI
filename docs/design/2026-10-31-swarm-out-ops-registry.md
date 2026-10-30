---
created: 2026-10-31
updated: 2026-10-31
title: "Swarm Out 能力登记表：一张操作表、从表生成的门、写清楚它挡不住什么"
status: r3 — 第一阶段范围和 ⑤ 已定（XG 2026-10-31）；第二阶段排期、devtools 待定（§9）
related: docs/design/2026-10-30-dispatch-center.md · docs/design/2026-10-31-swarm-out-run-review.md · docs/design/2026-10-31-swarm-run.md（§5.6 Conductor、§5.8 代答、P0-c）
---

# Swarm Out 能力登记表

**XG 2026-10-31：** Swarm Out 是平台级能力，我们自己的 agent 会在各处调用它；要有像 API gateway
那样的统一管理：派单中心、命令中心和其他相关 API 由一处管。

## 1. 一句话

把 Swarm Out 和 Hive 的每个操作登记在一张表里（谁能调、动作分量），agent 一侧的门从这张表生成，
测试保证没有一条路由漏登记。同时写清楚：在 agent 能读到"冒充人"所需的秘密之前，"只有人能做"是
减速带，不是边界；真正的边界要靠第二阶段的"人签名"（§6）。

**r1 → r2 改了什么：** r1 以为一张表加一个检查函数就能把人专用操作管住。零上下文审查找到了四条
不经过 Host 门的路（§2.4），其中两条也绕过 r1 设想的 token。所以 r2 把第一阶段收窄到"登记 + 生成的门
+ 完整性测试 + 两个便宜的补洞"，把执行检查、审计和 CLI 生成推迟到有真实读者或真实身份的时候。

## 2. 现状（2026-10-31 对着 main 读过）

### 2.1 调用方

| 调用方 | 入口 | 代码 |
|---|---|---|
| 人（桌面 App） | HTTP `/api/swarm-out/*`、`/api/swarm-out/remote/*`、`/api/hive/*` | `desktop/src/services/swarmOut.ts`、`services/remoteSession.ts` 等 |
| owner 的 agent | Bash 跑 `scripts.swarm_out`（`backend/scripts/swarm_out.py:102-128`），经本机 HTTP 打同一组路由 | route / spec / dispatch / runs / hives |
| 非 owner 的 channel agent | 不允许：`security_hooks.owner_command_denial`（:6333）对命令里出现 `swarm[_-]?out` 一律拒绝，CLI 也按 `RESTRICTED_ENV` 自拒（:127） | |
| 后端内部改状态的调用方 | 将来 Swarm Run 的 Conductor 调 `dispatch_spec`（swarm-run §5.6）、代答提问（§5.8 B） | 今天没有；其余内部 import（`attention_authority`、`routers/usage.py:55`、`routers/chat.py:412,977`、`channels/gateway.py:1950`）都是读 |

### 2.2 操作

- **派单**（`routers/swarm_out.py`）：`route`:159、`spec`:173、`dispatch`:98、`hives`:191、`fleet`:197、
  `runs`:267、`runs/{id}/result`:273、`runs/{id}/refresh`:280。
- **命令**（同文件）：`verdict`:221、`manual-landed`:230、`return/prepare`:138、`return/land`:149、
  `rerun`:246、`stop`:253、`discard`:260、`asks/{hive}/{session}/{request}/answer`:287
  （交给 `core.swarm_out.answer`，它按 `decision` / `answers` 分成审批和提问，写 S3 桥 `bridge/<hive>/answer/…`，
  `core/swarm_out.py:1290-1300`）。
- **Hive 会话代理**（`routers/swarm_out_remote.py`）：`relay` 开 / 关（:91、:104，开着一条长连接），
  其余按 `core/swarm_out_remote.py:82` 的 `ROUTES`（19 条）经一个通配路由转发（:120）。
- **Hive 管理**（`routers/hive.py`，r1 漏了）：建 / 删 EC2 实例（花钱、删除不可逆）、start / stop / update /
  reset-password、`GET /instances/{id}/credentials`（:635，只查是不是桌面模式，返回 Hive 的登录密码）、
  Brain 同步（代码送出本机）、账户增删改。
- **出站**：HTTP 统一经 `swarm_out._instance`（:170）和 `_client`（:220）；**另有 S3**：回答提问走账户的
  AWS 凭证写桶（`_s3()`，:235）。

### 2.3 管控点

| 管什么 | 在哪 | 性质 |
|---|---|---|
| Hive 上不能用 | `_require_desktop`（`routers/swarm_out.py:23`、`routers/hive.py`）、代理 `_guard`（`routers/swarm_out_remote.py:43`） | 可靠 |
| 写请求来自 App | 只有代理检查 JSON + `X-Swarm-User-Action`；主 router 不检查 | 挡跨站网页；curl 能加这个头；同源页面也能（§2.4 ④） |
| 只有人能做 | `run_owner_gate` 在 agent 的 Bash 命令文本上跑正则（`core/run_owner_gate.py:70-75`、:378-392） | 减速带（:381 自述）；漏过 answer 路由；不含代理的 `DELETE …/pending/…`；不含 `/api/hive/*` |
| 非 owner channel 不能用 | `owner_command_denial`（:6333）+ CLI 自拒 | 减速带 + 自拒 |
| 谁做了什么 | 派单日志、settle 记录、`hive_return` 落地记录 | 只有结果 |

### 2.4 "只有人能做"今天能被哪些路绕过

daemon 在桌面模式下没有调用者认证（`HiveAuthMiddleware` 只在 `SWARMAI_MODE=hive` 时生效），agent 的
Bash 默认不在沙箱里（`prompt_builder.build_sandbox_config`：`sandbox_enabled_default` 缺省为 False），
和 daemon 是同一个 OS 用户。以"批准 Hive 上的一条危险命令"为例，今天至少有四条路：

| # | 路 | 门看得到吗 |
|---|---|---|
| ① | Host `POST /api/swarm-out/asks/…/answer` | 看不到（漏了，本设计第 1 步补上） |
| ② | Host 代理 `…/remote/{sid}/chat/cmd-permission-continue` | 看得到（今晚已加） |
| ③ | 读 `GET /api/hive/instances/{id}/credentials`（或直接读 `data.db`），用 Hive 的密码直接调 Hive 的 `cmd-permission-continue` | 看不到 |
| ④ | 读账户 AWS 凭证（`data.db` 或 `~/.aws`，`hive/provisioner.py:191-194`），直接往 S3 桥写回答；需要的 `shown_sha256` 在可读的 `ask/` 对象里 | 看不到 |

另有一条和 Hive 无关的：⑤ 文件预览的"在浏览器打开"（`HtmlRenderer.tsx:10`）打开
`/api/workspace/file/raw`（`routers/workspace_api.py:1562`，`FileResponse`，无 CSP），agent 或 Hive 写的 HTML
在 daemon 的同源里运行脚本，可以带上 `X-Swarm-User-Action` 调任何路由。

**结论：** 门和 Host 上的 token 只能管住 ①②⑤；③④靠的是 agent 能读到冒充人所需的秘密。只要这一点
不变，"只有人能做"就只能是减速带。这是平台级的问题：`routers/ddd_brain.py:1700-1707`、:1807-1814，
`routers/ingestion_redact.py:38-46` 用的是同一个头。

## 3. 目标和非目标

**目标（第一阶段）**
1. 一张表列出 Swarm Out 和 Hive 管理的全部操作，以及谁能调；新增路由不登记，测试变红。
2. agent 一侧的门从表生成，人专用的路由不可能"忘了加"。
3. 补上 ①，并让 `credentials` 进门（③ 的 HTTP 那一半）；⑤ 按 §9 的选择处理。

**非目标**
- 第一阶段不宣称"人专用"是边界：§2.3 的"减速带"标注保留，直到第二阶段完成。
- 不新起 gateway 进程（§4 C）。
- 不做限流、配额、花费上限（STEERING #2）。
- 不改现有路由的路径和响应形状（前端不用动）。

## 4. 方案比较

| 方案 | 做什么 | 代价 | 评价 |
|---|---|---|---|
| A 维持 | 每加路由补正则 | 最小 | 已经漏过 ①；Swarm Run 会新增操作 |
| **B 登记表（推荐，分两阶段）** | 第一阶段 §5；第二阶段 §6 | 第一阶段小 | 第一阶段解决"漏登记"，诚实标注边界；第二阶段解决身份 |
| C 独立 gateway 进程 | 前置代理统一鉴权、审计 | 大 | ③④ 根本不经过它；单机多一个进程 |

## 5. 第一阶段

### 5.1 操作表 `core/swarm_out_ops.py`（数据，不是框架）

```python
@dataclass(frozen=True)
class Op:
    name: str            # "run.discard"
    method: str          # "POST"
    path: str            # "/api/swarm-out/runs/{dispatch_id}/discard"
    callers: frozenset   # {"person", "owner_agent", "internal:<name>"}；restricted_agent 永远没有
    weight: str          # read | change | irreversible | off_machine
```

| op | 调用方 | 分量 |
|---|---|---|
| `hive.list` `hive.fleet` `run.list` `run.result` `run.refresh` | person, owner_agent | read |
| `task.route` `task.spec` | person, owner_agent, internal:conductor | read |
| `task.dispatch` | person, owner_agent, internal:conductor | off_machine |
| `run.accept` `run.manual_landed` `run.return_land` `run.discard` | person | irreversible |
| `run.return_prepare` `run.rerun` `run.stop` | person | change |
| `ask.answer`（审批和提问共用一条路由；门分不出请求体，整条按人专用；代答提问将来走内部调用 `internal:wp_answer`，swarm-run §5.8） | person | off_machine |
| `remote.relay`（开 / 关长连接） | person | change |
| `remote.read`（`ROUTES` 里的 GET） | person | read |
| `remote.write`（`ROUTES` 里其余的，含 `DELETE …/pending/…`） | person | change |
| `hive.credentials` | person | off_machine（拿到它就能冒充人，§2.4 ③） |
| `hive.create` `hive.delete` `hive.reset_password` `hive.account.*` | person | irreversible / off_machine |
| `hive.start` `hive.stop` `hive.update` `hive.brain_sync` 及其余 `/api/hive/*` 读 | 按路由逐条定 | 逐条定 |

（`/api/hive/*` 的完整清单在实现第 2 步从 `app.routes` 生成后逐条定调用方，设计里不预先拍板。）

### 5.2 门从表生成

`run_owner_gate` 里 Swarm Out 和 Hive 的人专用正则改为从表生成：callers 不含 owner_agent 的 op 的路径
（通配代理按 `ROUTES` 展开），规范化沿用现有的 URL 解码、`./..` 折叠和去引号。
`owner_command_denial` 的"出现 swarm-out 字样就拒绝"**保持不变**（非 owner 的 channel agent 什么都不能调，
这比按操作生成更稳）。生成的门在自身出错时的行为和今天相同（随 `run_owner_gate` 的既有约定），在本文里
写明，不改。

### 5.3 完整性测试（这是"漏不了"的来源）

- 枚举 FastAPI `app.routes` 中 `/api/swarm-out` 和 `/api/hive` 前缀的路由，加上 `core.swarm_out_remote.ROUTES`
  展开的代理路由，每一条都必须对应表里的 op（一条路由可以对应多个 op 的情况在表里显式写出）。
- 对每个 callers 不含 owner_agent 的 op，构造 curl（含编码、`..`、拆引号、代理前缀），门必须拒绝；对含
  owner_agent 的读操作，门不拒绝。
- CLI 的子命令集合 ⊆ callers 含 owner_agent 的 op 集合（测试断言，不从表生成 CLI）。

注意："登记了"不等于"挡住了"。这组测试保证表和门一致、没有漏登记，不保证 agent 进不来（§2.4）。

### 5.4 两个便宜的补洞（独立于登记表，先做）

- **①** answer 路由进门（第 1 步，今天手写也行，第 3 步被生成的门替换）。
- **⑤ 定为 b：HTML 在浏览器里走一个只放文件的 origin**（XG 2026-10-31；a 和 c 不做）。见 §5.5。

### 5.5 ⑤ 的做法：只读内容 origin

**现在（读过）：** 应用内预览用 `<iframe sandbox="allow-scripts allow-popups" src={rawUrl}>`
（`HtmlRenderer.tsx:246-256`），页面是不透明 origin，碰不到 daemon，这一路已经安全。露出的只有"在浏览器打开"
（`HtmlRenderer.tsx:155-157` 的 `openExternal(rawUrl)`）：`rawFileUrl` 指向 daemon 的 `/api/workspace/file/raw?path=…`
（`file-viewer/utils/rawFileUrl.ts`），浏览器里这个页面和整个 API 同源。这个入口最初是为了替代渲染不稳定的
内嵌方式（DDD `95facf86`），不是为了让报告调 API。另外，因为路径放在查询串里，报告里的相对路径（`./data.json`、
`./img/a.png`）在今天的浏览器打开里本来就解析错。

**做法：**
- daemon 多监听一个只读端口（桌面模式；dev 和 Hive 不开）：同一进程里第二个 `uvicorn.Server`，挂一个只有一条
  路由的小 ASGI 应用：`GET /f/<编码后的绝对路径>`，复用 `_resolve_file_path` 和 50 MB 上限
  （`routers/workspace_api.py:1562-1586`），只读，不挂任何 API、不发 CORS 头。端口默认 daemon 端口 + 1，
  被占用就换一个空闲端口。
- 路径放在 URL 路径里而不是查询串里，所以报告里的相对链接和 fetch 解析到同目录的文件，能用了。
- 前端问一次地址：`GET /api/workspace/content-origin` → `{base}`；`HtmlRenderer` 的"在浏览器打开"改用
  `contentFileUrl(path)`。应用内 iframe 不变（已经是不透明 origin），图片、PDF、音视频不变。
- 兜底：daemon 的 `/api/workspace/file/raw` 返回 `text/html` 时加 `Content-Security-Policy: sandbox allow-scripts
  allow-popups`（不给 allow-same-origin）。应用内 iframe 本来就有这个 sandbox，不受影响；以后任何地方再在顶层打开
  这个 URL，也拿不到 API 的同源。
- 内容 origin 上的页面仍可以向 daemon 发跨源的"简单请求"（和今天用 `file://` 打开 HTML 一样，
  `FileEditorCore.tsx:1549`）。命令路由要带 `so_` 加 12 位十六进制的单号，猜不到；其余路由的跨站请求问题
  是平台级的，不在本文。

**要验证（实现第一步就做）：** WKWebView / 系统浏览器打开 `http://localhost:<内容端口>/f/…` 能正常渲染；
daemon 和内容端口一起起停（launchd 重启、`backend.json`）；Internal 实例（18421）不撞端口。

### 5.6 暂不做（r1 有，r2 去掉）

- **HTTP 上的 `authorize` 和 `@op` 装饰器：** 第一阶段分不出人和 agent，它什么都不拦，只增加机制。等第二阶段
  有了身份再加。
- **从表生成 CLI：** CLI 只有五个子命令，各有自己的参数；用 §5.3 的断言得到同样的保证。
- **审计日志 `ops.jsonl`：** 设想的读者（大卡片过程 tab、重复纠正率）可以从现有 settle 记录推出，Needs You
  的"门拒绝"提醒的写入者其实是 hook 不是 `authorize`。没有真实读者之前不写；需要时随第一个读者一起做。

## 6. 第二阶段：从"agent 打不出这条命令"到"人签了名"（先做验证，再单独设计）

§2.4 的 ③④ 说明，只给 Host 加一个 token（r1 §5.6）不够：agent 拿着 Hive 密码或 AWS 凭证可以完全不经过
Host。能同时关掉 ①–④ 的形态是：**人专用的动作由人签名，执行方验签。**

- 签名密钥只在 App 手里：放在系统钥匙串里，访问控制只给签过名的 SwarmAI.app（macOS Keychain ACL）；
  不落盘、不进环境变量、不进 `data.db`。
- App 对人专用动作签名（批准、验收、丢弃……），Host 和 Hive 都验签：Host 验 ①②；Hive 验 ③（直接调
  Hive 的审批也要签名）和 ④（S3 桥上的回答带签名，Hive 拒绝无签名的回答）。
- 这样 agent 即使读得到 Hive 密码和 AWS 凭证，也伪造不了人的签名。

**会让它失败的事（审查列出，需验证）：** 发布版编进了 devtools（`desktop/src-tauri/Cargo.toml:16`），能打开
检查器或 webview 里有 XSS，就能让 App 代签；daemon 是 launchd 服务，不是 App 的子进程，App 和 daemon 重启
都要重新交接公钥；对端身份要用 audit token 而不是 pid；Linux / Windows 的等价物（P7）。

**最便宜的验证（约半天，macOS）：**
1. 在 agent 的 Bash 里尝试读这把钥匙串项（`security find-generic-password …`），确认被拒或弹窗。
2. 在打包后的 App 里确认检查器打不开（或决定关掉 devtools feature）。
3. Hive 侧验签的改动范围：审批、提问回答、S3 桥三处。

任何一步不过，就停在第一阶段，并把"减速带"写进产品安全说明。另一个平台级选项是**默认打开 agent 的 OS
沙箱并禁止读取 `data.db` 和 `~/.aws`**（今天缺省关闭），它和签名互补，影响面更大，单独评估。

## 7. 和 Swarm Run 的关系

Swarm Run 的新操作直接进表：`internal:conductor` 能调 `task.route / task.spec / task.dispatch`；按 work id 查
状态是新的 read op；代答提问是内部调用 `internal:wp_answer`，审批永远只有 person。swarm-run §9 写的"把审批
路由加进门"由 §5.2 得到；它的 P0-c（更强的人类证明）就是本文第二阶段。

## 8. 分步和验证计划

**分步**
1. 补洞：answer 路由进门（①）；⑤ 内容 origin 加 raw 路由的 HTML 兜底 CSP（§5.5）。
2. `core/swarm_out_ops.py` 登记 §2.2 全部操作（`/api/hive/*` 逐条定调用方）；§5.3 完整性测试。
3. 门从表生成；删掉手写的 Swarm Out 正则；`hive.credentials` 等进门。
4. 第二阶段验证（§6），通过后单独设计。

**谁读新写的东西：** 操作表 → `run_owner_gate`（生成正则）、完整性测试、CLI 断言。没有新写入的运行时数据。

**最终测试集（不变量）**
1. `/api/swarm-out`、`/api/hive` 的每条路由和每条代理路由都在表里。
2. 每个人专用 op 的 curl 变体被门拒绝；owner_agent 的读操作不被拒。
3. 非 owner 的 channel 会话里任何含 swarm-out 字样的命令被拒（现有行为，加测试）。
4. CLI 子命令 ⊆ owner_agent 的 op。
5. ⑤：内容端口只响应 `GET /f/…`（任何 `/api/*` 返回 404）；"在浏览器打开"的 URL 指向内容 origin；
   报告里的相对路径解析到同目录文件；daemon 的 raw 路由对 `text/html` 带 sandbox CSP，其他类型不带。
6. 现有 Swarm Out、hive、run_owner_gate 测试不变绿。

**审查：** 本文已过一次零上下文审查（r1）；第二轮只在前提再变时做。实现后 security（门的生成和规范化）+
correctness，每类最多两轮。

## 9. 决定

**已定（XG 2026-10-31）**
1. 第一阶段范围：Swarm Out + `/api/hive/*`。ddd_brain / ingestion_redact / pipelines decision 的人专用操作不在
   本阶段，等第二阶段的身份方案一起考虑。
2. ⑤：方案 b，只读内容 origin（§5.5）。

**待定**
3. 第二阶段（人签名）要不要现在排期做验证：平台级，影响所有"只有人能做"的操作，也是 Swarm Run P0-c 的前提。
4. 发布版的 devtools 是否关掉（和第二阶段相关，也独立于它）。
