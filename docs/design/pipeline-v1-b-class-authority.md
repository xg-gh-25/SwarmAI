---
title: Pipeline v1 B-class — the agent and the daemon share one OS user
created: 2026-10-24
updated: 2026-10-24
status: approved by XG 2026-10-24 (B-a..B-e; daemon offline = pipeline commands fail)
---

# Pipeline v1 B-class：agent 和 daemon 是同一个系统用户

## 问题

三方复审报了 3 个 HIGH。它们的根因相同：agent 起的进程和 daemon 用的是同一个系统用户，
gate 只能从命令文本上判断，agent 在 Python 里临时拼出字符串，gate 就看不出来。

| # | agent 能做到的事 | 现在靠什么挡 |
|---|---|---|
| B1 | 读 `authority.key`，自己给事件签名，包括 hook 和 cli 的身份 | `audit_marker_write_gate` 按命令文本拦截 |
| B2 | 伪造 `SWARM_APP_SESSION_ID`，冒充别的 session，包括接手别人的 run | `session_link_gate` 按命令文本拦截 |
| B3 | 编码 URL 后 POST `/api/pipelines/<id>/decision`，替人回答决定 | `run_owner_gate` 按命令文本拦截 |

## 核心改动：密钥只在 daemon 里

`authority.key` 只有 daemon 进程能读。CLI（`pipeline_v1.py`）和 runner（`pipeline_test.py`）
不再自己加载 engine 去签名，改为通过已有的仅属主可访问的 Unix socket（hook_token oracle，
`<app data>/run/`）请求 daemon 执行。daemon 校验后由它自己签名写入。

1. **执行搬到 daemon（B1）。** CLI 只做三件事：解析参数，带上一次性 token 发请求，把 daemon 的结果打印出来。
   - 现在 `RunEngine` 的所有变更都在 daemon 的一个专用线程池里执行（类似 nav 池，不占 chat 的池）。
     操作锁、store 锁等全部规则不变，只是换了个进程执行。
   - `pipeline_test` 跑 pytest 仍在 agent 的进程里，测试结果的签名改由 daemon 完成：
     runner 把结果的摘要和证据 blob 交给 daemon，daemon 写入 `test_call` 事件。
   - 新 run 创建时，密钥写到 daemon 专用的目录，权限 0600。
     **局限：** 同一个用户仍然能读 0600 的文件，所以这一步本身并不防同用户，只是让签名只发生在 daemon 里。
     真正的保护来自下面第 3 条。
2. **token 绑定每一次请求（B1、B2）。** hook 已经会给每个 pipeline 命令发一次性 token
   （`run_owner_gate` 通过 `updatedInput` 注入，记录只存在 daemon 内存）。现在要求每个 mutating 请求都带 token，
   daemon 用 token 里记下的 session、run 和子命令执行，**不信请求里自带的 session id**。
   这样伪造 `SWARM_APP_SESSION_ID` 就没有用了，B2 关闭。
3. **密钥换存储位置（B1 的真正修复）。** 只有 daemon 持有密钥：
   - macOS：存进 Keychain。daemon 是 launchd agent，Keychain 条目的 ACL 只允许 daemon 二进制访问。
     agent 起的 python 读不到，读取时会弹系统授权框。
   - Linux / Hive：内核 keyring 的 session keyring，或 systemd credentials。
   - 拿不到这种存储时（例如 dev 模式）退回 0600 文件，并在 E7 表里标出"同用户可读"。
4. **decision 接口（B3）。** 改为只接受 dashboard 会话：
   - daemon 每次启动生成一个随机 secret，只通过 Tauri 的 IPC 交给 webview，不写盘，不放进环境变量。
   - decision、run-land 这些人工接口都要求带上这个 secret。
   - agent 的 curl 拿不到它。**局限：** 同一用户的进程可以读 daemon 进程的内存，或者附加调试器，
     这只能靠操作系统级别的隔离（单独的用户，或 sandbox）来防，见下文。

## 这一轮做不到的

- 只要 agent 是同一个用户、并且能跑任意代码，它理论上就能附加调试 daemon，或者读进程内存。
  根治只有两条路：让 agent 跑在**单独的系统用户**下，或者放进 **macOS sandbox profile**，禁止 ptrace、
  禁止访问 daemon 的 socket 以外的东西。这属于部署层面的改动（安装器、launchd、文件权限），需要你决定要不要做。
- dev 模式下 Keychain 不可用时，B1 只能做到"签名只在 daemon 里"，做不到"agent 读不到密钥"。

## 实施顺序（每步一个 commit，测试先行）

| 步 | 内容 | 关闭 |
|---|---|---|
| B-a | daemon 侧执行入口：socket 请求 → token 校验 → 在专用池里跑 `RunEngine` | — |
| B-b | CLI 改成薄客户端；runner 的结果交给 daemon 签名 | B2，B1 的签名路径 |
| B-c | 新 run 的密钥进 Keychain / keyring；没有时退回文件并在 E7 标出 | B1 |
| B-d | ~~人工接口要求启动 secret，Tauri IPC 交付~~ **不做（XG 2026-10-24）：** 同用户 agent 也能连 secret 所在的 socket，只抬门槛；和 B-c 残余一起记为 TODO，归入"agent 进程隔离"设计，见 `pipeline-v1-e7-review.md` 遗留项 7 | B3（未关） |
| B-e | 审查两轮、三方复审、E7 更新 | — |

## 代价

- **热路径：** 每个 pipeline 命令多一次 socket 往返，估计不到 5 ms，要实测。chat 的发送和流式路径都不受影响。
- **daemon 必须在线：** daemon 不在时，pipeline 命令直接失败，不能退回本地签名，否则这道防线等于没有。
  dev 模式下由 `./dev.sh` 起的后端充当 daemon。
- **测试：** rig 要能起一个进程内的 daemon 执行入口，`pipeline_v1_rig` 需要改。

## 需要你定

1. 是否按 B-a 到 B-e 做。
2. 是否接受"daemon 不在时 pipeline 命令直接失败"。
3. 单独系统用户或 sandbox 这一步，这一轮不做，单独立项。
