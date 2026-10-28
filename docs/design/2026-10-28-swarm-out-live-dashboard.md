---
title: Swarm Out 面板接真实数据，没有数据源的写 TBD
created: 2026-10-28
updated: 2026-10-28
project: SwarmAI
status: r3 — 已实现。前提审查和代码审查（ux、correctness）的意见都已改进
---

# Swarm Out 面板接真实数据，没有数据源的写 TBD

**修订记录：** r1 经一次前提审查（correctness），9 条意见按原因改成 r2：
- run 的状态改用 Hive 自己报的每个会话的 `activity` 和 `waiting_input`，不再只看 `streaming`（等人、排队时它是 false）。
- 机群单独一个路由，只有打开的面板轮询，tab 角标的轮询不多出探测。每台 Hive 的探测并发、短超时。
- 停掉的 Hive 上的 run 算已停下，不再永远"状态未知"。空位不会是负数，Hive 返回的数字先校验。
- 机群的数字写"占用"，指 Hive 上所有会话，和"本机派出的在跑"分开。被拒收的问题单独一组，和"等你"的数对得上。
- 面板的 Hive 数和设置引导改从机群取，不再另外轮询 `/hives`。`history.ts`（按大脑分组）只服务示例数据，删掉。

**代码审查后的改动（r3）：**
- 顶部只留三个数，顺序是等你回答 → 空位 → 我派出的在跑。"已停下"那个数删掉，因为它不改变任何决定，各组标题上已经有条数。
- "等你回答"还包括 Hive 说在等人、但问题没传到这里的 run（tab 角标用同一个规则）。被拒收的问题不计入，写成"+N 个要去 Hive 页面处理"。
- "已停下"改叫"不在跑了"（说不出是做完、失败还是被停）。Hive 关机写"Hive 已关机"，从本机删掉的 Hive 写"Hive 已删除"，都不再去问它。
- 一台 Hive 的问题读取（S3）也有 10 秒上限。Hive 报来的 activity 只收短的小写词。
- 机群读不到时明说，不再一直显示"正在读"。没有任何 run 时只显示一行。后端的英文错误转成中文并写明是哪台 Hive。
- 轮询的旧回答晚到时不会覆盖新回答。历史视图里 TBD 区块放到真实数据后面。

## 1. 问题

Swarm Out tab 左边的面板，只有"真实派发"一节读真数据（`LiveDispatches`，`desktop/src/components/swarm-out/LiveDispatches.tsx:143` 起，读 `/api/swarm-out/runs`）。它下面的机群、四个数字、run 卡片和整个"历史"视图都来自静态文件 `mockData.ts`（`SwarmOutDashboard.tsx:33-36` 导入，`:174-224` 的 `Overview` 和 `:352` 起的 `History` 全部读它），上面有一条"以下是示例数据"的提示（`:435`）。

结果是同一个面板上有两套数字：真实派发说有 2 个任务，下面的示例说有 5 个在跑、8 个槽。用户要的是：面板上的每个数都是真的，还没有数据源的地方保留结构、写 TBD，不放假数字。

## 2. 这个面板回答的三件事

按设计判断的第一条（先找用户在这里做的决定），面板只回答三件事，排版也按这个顺序：

1. **有没有在等我的？** 等你回答的问题和审批，带回答控件。这是唯一需要用户动手的东西，放最上面，唯一用强调色。
2. **还能不能再派任务？** 每台 Hive 在不在、忙不忙、还有几个空位。
3. **派出去的怎么样了？** 在跑的、已经停下的。

TBD 的部分只占结构，用暗色小字写"还缺什么数据源"，不做卡片墙，不抢这三件事的位置。

## 3. 每一块的数据源

| 区块 | 来源 | 状态 |
|---|---|---|
| 顶部 Hive 数（"2 台 Hive 在运行"） | `/api/swarm-out/hives`（现有） | 真 |
| 机群：每台的名字、状态、版本、区域 | 本机 `hive_instances` 表（`name, status, version, region, instance_type`） | 真 |
| 机群：占用（Hive 上所有会话）、并发上限、是否忙满、连不连得上 | 每台运行中 Hive 的 `/api/chat/sessions/admission-state`（`backend/routers/chat.py:777`，返回 `streaming_count`、`max_concurrent`、`saturated`），本机派发前已经在用 | 真 |
| 数字：空位 | 连得上的运行中 Hive 各自 `max(0, max_concurrent - streaming_count)` 之和（忙满的算 0），分母是 `max_concurrent` 之和。"占用"是 Hive 上所有会话，包括它自己的任务和网页会话 | 真 |
| 数字：等你回答 | `asks` 里没被拒收的条数，和 tab 角标同一个函数（`activityOf`）。它数的是问题，不是 run，也包括不是本机派出的会话的问题 | 真 |
| 数字：在跑 | 本机派出、`state == running` 的 run 数（同一个函数） | 真 |
| 数字：已停下 | 24 小时内派出、`state == stopped` 的 run 数 | 真 |
| 等你：问题和审批卡片 | `asks`（现有 `AskCard`，回答、拒收、截断的逻辑都不动）。被拒收的单独一组"要去 Hive 页面处理"，不算进数 | 真 |
| 在跑 / 已停下 / 状态未知：run 卡片 | 派发记录：标题、Hive、派出时间、状态和原因 | 真 |
| run 卡片：结果（成功、失败、落地）、Judge 标签、trace | 本机还没有从 Hive 拿回结果和审计 | TBD |
| 已安排 | 还没有定时派发 | TBD |
| 历史：每天派出的 run（近 14 天，按本机日期） | 派发记录的 `sent_at`（UTC，换成本机日期再分桶） | 真 |
| 历史：Run 记录（近 7 天） | 派发记录，按 Hive 分组 | 真 |
| 历史：按大脑 / 按 Project Agent 分组 | 派发记录里没有大脑和 Project Agent | TBD |
| 历史：一句话结论、五维汇总、按类型、工蜂占用 | 没有跨 run 的审计，也没有按时间采样的并发 | TBD |

**run 的状态（后端算，每行一个 `state` 和 `state_reason`）：** 来自 Hive 的 `/api/chat/sessions/streaming-state`，它对每个会话报 `state`、`activity`、`waiting_input`（`backend/core/session_state.py:76` 起，`activity` 的取值见同文件 `derive_activity`：`stopping`、`waiting_input`、turn 的阶段、`streaming`、`idle`）。

| 情况 | state | 原因 |
|---|---|---|
| 会话 `waiting_input` 为真，或有一条没被拒收的问题 | waiting | 在等人 |
| 会话在，`activity` 不是 `idle` | running | 在跑（包括排队、准备、停止中） |
| 会话在且 `idle`，或 Hive 已经不持有这个会话 | stopped | 不在跑了 |
| 这台 Hive 不在运行 | stopped | Hive 已停止 |
| Hive 没回答，或会话 id 从没拿到 | unknown | 说不清 |

`waiting` 和 `running` 两个布尔照旧给（tab 角标和命令行在读），由 `state` 推出来。"已停下"不说"完成"：本机不知道它是做完了、失败了还是被停了。状态未知的 run 和已停下的一样，只在 24 小时内显示在运行视图里，更早的只在历史里。

## 4. 接口

**新路由 `GET /api/swarm-out/fleet`**，只有打开的面板轮询：

```
{ hives: [{ name, status, version, region, instance_type,
            reachable: bool | null,      # 非运行中的 Hive 为 null
            streaming: int | null, max_concurrent: int | null, saturated: bool | null }],
  running: int }                         # 运行中的台数，和 /hives 同义
```

- 所有 Hive 都列出来，包括停着的。行按白名单的键构造，不从 `_instance` 的字典展开，所以永远没有地址和凭证（测试断言键集合）。
- 每台运行中的 Hive 问一次 `admission-state`，各台并发（`asyncio.gather`），每次探测 5 秒超时。一台超时或回答不合规（非 JSON、不是 0 到上限之间的整数、`saturated` 不是布尔），它的 `reachable` 为 false，三个数为 null，其余照常。
- `/hives` 保留给命令行和 skill。面板改从 `/fleet` 取 Hive 数和"要不要显示设置引导"，不再另外轮询 `/hives`。

**`/api/swarm-out/runs` 的改动：** 每行加 `state`、`state_reason`。`_streaming_sessions` 改为返回每个会话的状态（同一个 streaming-state 回答，不多一次请求）。各 Hive 的读取改成并发，探测超时同样 5 秒，tab 角标的轮询不会因为一台 Hive 挂了而变慢。前端类型 `SwarmOutRuns` 和 s_swarm-out skill 的 `runs` 说明同步。

## 5. 前端结构

- `mockData.ts` 改名为 `shapes.ts`：删掉所有示例数据，保留类型和五维的定义（`DIMENSIONS`、`DIMENSION_ROLE`）。`JudgeTag`、`TraceTags`、`judge.ts` 读它们，审计接进来时要用，这次不渲染，它们自己的测试保留。
- 删掉 `history.ts` 和它的测试：按大脑 / 按 Project Agent 分组只服务示例数据，现在没有这两个字段。
- `LiveDispatches` 拆开：`AskCard` 原样移到"等你"一节，run 列表按 `state` 分成"在跑""已停下""状态未知"三组卡片。
- 四个数和 tab 角标用同一个导出函数，不各算各的。
- 删掉"示例数据"提示和所有示例渲染。
- TBD 用一个小组件：标题 + 一行暗色"TBD：<缺的数据源>"，不画假的图表骨架。

## 6. 不做

- 不从 Hive 拉每个 run 的最终回复或审计（需要设计"什么时候拉、存在哪"，读写两边）。
- 不做定时派发。
- 不改回答、拒收、截断、内容哈希的任何逻辑。

## 7. 验证计划

| 写什么 | 谁读 | 测试 |
|---|---|---|
| `/fleet` | 面板的机群、空位、Hive 数、设置引导 | 后端：一台运行中可连、一台运行中连不上（超时）、一台回答不合规、一台停着，各自的字段。一台挂了不拖慢另外几台（总时长有上限）。`streaming > max_concurrent` 时空位为 0。行的键集合正好是白名单 |
| `/runs` 每行的 `state` | 面板的三组 run、四个数、tab 角标 | 后端：表里每种情况一条（等人但问题被拒收、等人但 S3 读失败、排队中、idle、会话已不在、Hive 已停止、Hive 没回答、会话 id 从没拿到） |
| 面板的四个数 | 用户 | 前端：给定 runs/asks/fleet，四个数等于第 3 节的规则。被拒收的问题不算等你。unknown 不算进任何数。面板和角标用同一个函数 |
| 历史的每日柱 | 用户 | 前端：UTC 跨日的派发按本机日期分桶 |
| TBD 区块 | 用户 | 前端：面板里不再出现"示例数据"提示和任何示例数字。每个 TBD 区块写出缺的数据源 |
| 拆出来的 `AskCard` | 用户 | `LiveDispatches.test.tsx` 现有的回答、拒收、截断、多选测试改为在面板里跑，全部保持通过。被拒收的问题在单独一组 |

`SwarmOutDashboard.test.tsx` 里断言示例数字的测试删掉，响应式布局（窄面板 2×2、卡片网格）的测试改用真实数据的形状保留。

**最后跑一次的不变量：** Swarm Out 后端测试、`components/swarm-out` 和 `ChatHeader` 的前端测试、`tsc`。改完重新 build 后，用两台真实 Hive 看一遍：demo 和 explorer 都出现在机群里，数字和 `/runs`、`/fleet` 的原始数据对得上。

**评审：** 代码改完请 ux 和 correctness 两位专家各审一次。
