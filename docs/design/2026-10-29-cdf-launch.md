---
title: CDF 上线修复 —— 一次切换前要修完的问题
created: 2026-10-29
updated: 2026-10-29
project: SwarmAI
status: 待上线
supersedes-part-of: 2026-10-26-swarm-cognitive-distillation-flow.md §14 阶段 B（切换方式按用户 2026-10-29 决定：一次切换、无开关、无影子）
---

# CDF 上线修复

用户 2026-10-29：一次切换，不留后路，上线后有问题靠历史补救；一个完整方案一起做完；不过度设计；CDF 是 agent 的认知核心，P0。

本文只列切换前必须修完的问题和修法。来源：五路专家评审（状态机、写入正确性、性能、安全、架构与认知）加一次真实数据回放（数据副本上，S2 两个会话）。每条都对源码核实过。

## 1. 不修就不能上线的

| # | 问题 | 修法 |
|---|---|---|
| A1 | **S2/S6 提取 100% 失败**：三类单元共用 24 字段的扁平 schema（含一个无字段的 `relation_hint` object），模型把 `units` 写成字符串，且字符串里中文引号未转义。实测 3/3 失败，对照实验 8 种写法定位 | schema 改成每个 kind 一个数组，元素只带该 kind 的字段；提取不再要 `relation_hint`（实测 4/4 成功） |
| B1 | 停放的单元（`unknown_domain` / `missing_section`）被 `_READY` 当作就绪，worker 空转并饿死所有车道 | `_READY` 只认 `next_attempt_at IS NOT NULL AND <= now` 的 deferred |
| B2 | v1 分支对停放的单元每轮重判（调 Bedrock）| v1 分支同样跳过停放的单元。核实：v1 在生产里已没有活着的生产者（context_health 的 cultivation 早已返回 False，两个 hook 未注册），v1 引擎代码仍被十几个引擎测试当作夹具，删除留到上线后 |
| B3 | 候选卡在 `in_progress` 无出路 | 启动和每日维护时按单元状态重算候选状态；`writing` 且无写入计划的单元回到 `queued` |
| B4 | 重试的写入被标成"已写入"（op_id 固定 + `INSERT OR IGNORE` 读回已放弃的旧计划） | 同一单元已放弃的计划在插入前删除；`_hand_to_writer` 不再自己标 written，只认 writer 的结果 |
| B5 | 一个坏的写入计划让 worker 每次启动都崩；关系写入失败在文件已写之后抛错 | `recover` 每个计划单独 try，出错即放弃该计划；无效关系跳过并记事件，不抛错；关系主语锚点从写入后的内容解析 |
| B6 | v2 单元处理没有错误边界；三个后台线程遇到任何异常就死 | 单元级 try：校验失败拒绝 `malformed_payload`，其余按指纹延后/毒化；三个线程循环内捕获、记日志、退避 30 秒后继续 |
| B7 | writer 的 `_defer` 不退避、不计次 | 退避并计次，5 次后毒化 |
| B8 | 基础设施故障（判官不可用）约 3 分钟就把单元永久毒化 | 判官不可用按小时级退避，24 次后才毒化 |
| B9 | owner 锁只试一次，重启重叠时新进程永远不当 owner | 拿不到锁时后台每 30 秒重试 |
| S1 | Slack 群里非 owner 的话被拼进 owner 的消息，作为"用户原话"进入 STEERING | 历史前言作为 external 部分单独记录，不算用户原话 |
| S2 | 渠道消息一律记为 user 信任 | 非 owner 记为 external |
| S3 | S7 复盘里一个模型的"蕴含"判断就能清掉外部内容的污染，且可以去 STEERING | 清污只对 EVOLUTION / MEMORY 生效，其余目的地仍按污染处理 |
| S4 | v2 不执行目的地规则 | 移入 v2 两条确定性规则：STEERING/USER/TOOLS 要求 `user_explicit`（C2）；认知单元含易变数字的拒绝（D4，R30#4） |
| S5 | `/api/ingestion/redact` 把内部错误原文返回给客户端 | 返回通用文本，细节记日志。核实：它只靠回环地址和固定 header，任何同用户进程都能调用——但这是整个本地 API 共有的（前端调用本来就没有逐请求密钥，wake 凭证文件 agent 同样读得到），不是 CDF 独有，记为已有问题 |

## 2. 认知形态：文件可以长，注入有上限，按价值排序，被取代的不再出现

| # | 问题 | 修法 |
|---|---|---|
| C1 | 每日写入上限 `reserve_quota` 没有生产调用者 | 交给 writer 前预留；MEMORY / EVOLUTION / STEERING / USER / TOOLS 按全局计（不按项目）；超限延后到次日 |
| C2 | 注入预算对 meta 层豁免，EVOLUTION 的 meta 层已超预算 | meta 层也受预算约束：仍然最先取，在预算内按分数取，再取其余层。**这改变了原设计阶段 C 的验收项「元认知层完整保留」**：不改的话 CDF 写进 EVOLUTION 的每条纠错都永久、无上限地进每轮 prompt |
| C3 | 预算内按 `1/(1+年龄)` 排序，年龄决定去留 | 改为慢半衰期（90 天减半，meta 层不衰减）乘使用次数的对数，使用价值主导 |
| C4 | 被取代（supersede）的条目只在超预算时被过滤 | 注入时总是过滤被取代的条目 |
| C5 | refine 的合并写进了渲染器不读的字段，新条目只有新内容；旧条目被取代后（C4 不再注入）它的内容就丢了 | 新条目的 what 带上旧条目内容；旧条目字节保留（审计、recall），由 C4 在注入时过滤 |
| C6 | STEERING/USER/TOOLS 的元数据注释格式读不出；多行字段会造出新标题 | `project_hint` 等放进 cand 标记；每轮注入的存储一律单行渲染，含注释标记的单元在卡 0 拒绝 |
| C7 | 标题被 `strip(" -*#.")` 改坏（`Use C#` → `Use C`）；DDD 标题含 `|` / `**` 会抛错并无限重试 | 只去空白；格式字符替换 |

## 3. 成本和热路径

| # | 问题 | 修法 |
|---|---|---|
| P1 | S2 窗口数无上限（一个会话 273 个窗口 ≈ 100 美元） | 主提取（读 agent 和工具内容，本身就是污染来源）每次最多 8 个窗口，均匀取样保留首尾，未取的数量记进 attempt；用户原话那一遍不设上限 |
| P2 | 一次复盘产出的单元数无上限 | 最多 15 个，多的丢弃并记数 |
| P3 | 每次发消息为每个部分新开 ingestion.db 连接 | 核实：worker 的写事务都很短（一次单元认领或一次插入），等待风险低，不重构；只修 `with sqlite3.connect()` 不关闭连接导致的每条消息一个连接泄漏 |
| P4 | 每次 Read/Grep 命中都重新解析整个文件 | 按（路径, mtime）缓存解析结果 |

## 4. 接口与收尾

- 文件浏览器：新建、移到废纸篓对受管文件带上 `expected_revision`；重命名/移动受管文件时把后端的 409 原因显示给用户（受管文件的重命名走 CDF 的树级操作，前端首版不提供）。
- Capabilities 页去掉已删除的条目；`verify_build` 加 `core.ingestion.worker`。（`knowledge_backflow_hook`、`improvement_writeback_hook` 是未注册的死代码，上线后与 v1 引擎一起删；`code_change_feed` 仍被 code_intel 使用，不是死代码。）
- `brain_sync` 的写入登记进 materializer 清单（从 Hive 安装整个 brain 目录，记为同步来源）。
- 治理文字与新机制一致：SWARMAI.md 的 Darwinian 推论、SELF.md 的 Memory management、AGENT.md R22–R24 改成描述"写入上限 + 注入选择 + 被取代过滤"。AGENT/SWARMAI/SELF 属系统文件，和重启一起交用户批准。

## 5. 不做

- 不加开关、影子期、新存储。
- 不整理已有内容（§13 不变）。
- TIMELINE、epoch 栅栏、S4/S5/S6 等是否删减，上线后按数据另议，不混进本次。
- recall 路径不读 supersede 关系（只有注入路径过滤）；CDF 写的条目 replace/refine 时已从文件移除，影响只剩基线条目被取代的情况。

## 6. 验证计划

- **每个修复**：先写失败的测试，再修；`scripts.mutation_check` 撤掉修复测试必须变红。
- **提交前**：改动涉及的已有测试（ingestion、memory_injection、writer、worker、s2、session_inputs、gateway、workspace_api、security）。
- **最终一次**：`tests/test_cdf*.py` 全部，加上面这些，分块各一次。
- **数据副本上的真实回放**：S2 抽 3 个会话 + S6，排空 worker；我逐条看写入的内容，按 Principle 1 打分并报告。
- **审查**：实现完成后 correctness、state-machine、security 各一轮，最多两轮。
- **切换前**：`cdf_cutover_snapshot.py` 对全部受管文件和 ingestion.db 做快照；用户批准后 build、重启；重启后看 `CDF ingestion owner worker started` 和第一个会话的 S2 任务结果。

## 7. 结果

**实现**：§1–§4 全部落地（e2ef73aea、07f45b39b、4f77a17f5、45ad22808、8a813a220、af2058cdd），之后是两轮审查修复（9670a9cca、2aa15b6ca）和回放修复（3853e6687）。B2 的 v1 引擎、§4 的两个死 hook 留到上线后删。

**测试**：每个修复撤掉后测试都变红（mutation 共 52 处，全部抓到）。`tests/test_cdf*.py` 48 个文件分三块跑，加上 workspace_api、brain_sync、cdf167 的测试，全绿。唯一的失败是 `test_cdf_engine_core::test_busy_returns_within_bound`：在 main 上同样失败（这台机器负载高，1.8 秒超过 1.7 秒上限），和本次无关。

**审查**：
- 第一轮 correctness / state-machine / security。HIGH 1 个：被放弃的写入计划让单元卡在 writing，或者热循环。MEDIUM 7 个：重复写入、恢复和 run lane 竞争、S2 线程会死、抽样主提取丢失、额度满时每天重判、refine 丢 taint、重命名的信任和竞争。全部修了。
- 第二轮 state-machine，无 HIGH。它发现第一轮的防重复检查不完整，根因是「放弃一个条目已经写进文件的计划」。改法是 recover 对 target_applied 之后的计划只完成、不放弃，并删掉了第一轮加的磁盘检查。抽样主提取的重试猜测也撤掉了：它是测量样本，猜错会把整段会话重新提取一遍。
- 没修：旧版 writer 留下的 next_attempt_at 为空的 deferred 行（线上 ingestion.db 还没有任何单元）；进程内重启时 owner 获取线程不释放（生产不会进程内重启）。

**真实回放**（线上数据副本，最终代码）：S2 跑了 3 个会话。大会话共 17 个窗口，主提取取 8 个。判官处理 168 个单元：写入 52，强化 8，拒绝 108。拒绝原因：66 个 not_atomic，23 个 speculative，7 个 already_exists，其余是 untyped / conflict / volatile。写入的去向：DDD 25，公共 DDD 10，TIMELINE 16，EVOLUTION 1；MEMORY 0（没有一条达到「每次会话都要注入」的门槛）。按 Principle 1 逐条看了写入内容：
- 约 30 条是能直接改变判断的具体教训。例如凭证脱敏正则漏了 `aws_secret_access_key`；pipeline CLI 加 `2>&1` 会破坏 JSON；SSM 以 root 运行触发 git 的 dubious ownership；baselines.json 没有打进二进制；worktree 闸门把 heredoc 里的路径字符串当成写入目标；main 推进会耗尽落地次数。
- 约 5 条是带版本或日期的快照，价值低但不是错的。
- 发现并修了三类问题：MEMORY/EVOLUTION 条目被渲染成「what: …; applicability: …」字段名；refine 一个 TIMELINE 事件时把上一个事件整块抄进来；同一条教训用中英文各写了一次（判重读的是词）。修完后重跑会话 1：英文输出，旧条目判为 reinforced / already_exists，没有重复。
- 一个窗口的回复 8 次里有 1 次解不开。现在会再调一次；单个窗口失败只丢那个窗口，所有窗口都失败时任务才失败重试。
- 成本：这次回放加探针约 200 万输入 token、22 万输出 token。

**治理文字**（系统文件，等用户批准后改 `backend/context/` 再 rebuild）：AGENT R22 和 SELF「Memory management」里写的「meta-cognitive layer always whole」现在不对了，应改成「meta-cognitive layer first, inside the budget」，并补一句：每个每轮注入的文件每天有写入上限（4 KB；DDD 每个项目 20 KB），超了的单元等到下一个 UTC 日。SWARMAI 的 Darwinian 推论讲的是 DDD 的衰减和归档，和这次改动不冲突，不用改。

**切换**：落地 main → `cdf_cutover_snapshot.py snapshot` 做快照 → 用户批准后 build、重启 → 看日志里的 `CDF ingestion owner worker started` 和第一个会话 S2 任务的结果。
