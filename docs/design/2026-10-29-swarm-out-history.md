---
title: "Swarm Out 历史视图：从“派过什么”到“派出去的活靠不靠谱”"
created: 2026-10-29
updated: 2026-10-29
status: 已实现（2026-10-29；mockup Knowledge/Designs/mockups/2026-10-29-swarm-out-run-card-history.html）
---

# Swarm Out 历史视图：从"派过什么"到"派出去的活靠不靠谱"

日期：2026-10-29 · 范围：`desktop/src/components/swarm-out/`（只改前端）· 状态：设计，待确认

## 1. 现在的问题

历史视图是 run 结果还没从 Hive 取回来时写的（`2026-10-28-swarm-out-live-dashboard.md` 第 57–60 行）。之后 run 卡片接上了结果、Brain / Project Agent、花费、人工介入、Autonomy Judge、教训，历史视图却没改。逐块看：

| 现在的块 | 问题 |
|---|---|
| 每天派出的 run（14 天柱） | 只有量和结局颜色。run 少的时候大多是空柱，不改变任何决定 |
| Run 记录（7 天，按 Hive 分组） | Hive 是机器，不是决策维度。每行只有标题和结局词，卡片上的 Brain、类型、时长、花费、人工、Judge 全都没有。点不开，看不到细节 |
| "按 Brain / Project Agent 分组：TBD" | **过时**：`/runs` 每行都带 `project_agent.brain`，运行视图的"按 Brain（7 天）"已经在用 |
| 一句话结论 · 五维汇总 · 按类型：TBD | **过时**：`/runs` 每个 pipeline run 都带五维判定（`result.pipeline.audit`），类型可以从 `result.kinds` + `pipeline.profile` 推出来 |
| 工蜂占用：TBD | 真没有数据源（并发不按时间采样），保留 |
| "结束情况未知：这个 Hive 的版本不记录……" | 一句长话每行重复一遍 |

数据面：`/runs` 一次返回整个派发记录（`core/swarm_out.list_runs` 读整份 log，不截断），每行的 `result` 是 `swarm_out_results.public()` 的形状。所以历史视图需要的东西**都能在前端算出来，不用改后端**。

## 2. 历史视图回答什么

派到 Hive 的意义是**无人值守把活做完**。历史视图回答四个问题，顺序就是页面顺序：

1. **这段时间派出去的活，有多少是自己做完的？** → 一句结论，一个数字突出
2. **哪里好、哪里不好？** → 按 Brain / 类型 / Hive 拆开对比
3. **代价多少？** → 花费、时长、要人的次数
4. **找到某一个 run，看它的细节** → 记录列表，展开就是 run 卡片

## 3. 布局

```
┌ Swarm Out   [运行] [历史]                                   2 个 Hive 在运行 ┐
│ [近 7 天] [近 30 天] [全部]                                                  │
│                                                                              │
│ 8 个 run：5 个做完（4 个无人值守）· 1 个停下 · 2 个结束情况未知 · 花费 ≥ $7.86     │  ← 一句结论
│ ┌──────────────────────────────────────────────────────────────────────────┐ │
│ │ 无人值守做完 4/6      要人  3 次（2 个问题没人答）   中位时长 55 秒    花费 ≥ $7.86 │ │
│ │ (67% · 已知结局 6 个)  Judge  2 个 pipeline ✓2                              │ │
│ │ ▁▁▁▁▃█▂   ← 每天的 run 按结局叠色，高 48px                                    │ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│ 拆开看   [按 Brain] [按类型] [按 Hive]                                          │
│  Brain            runs  结局                     无人值守  中位时长  花费    Judge │
│  General           1    ██████████               0/1      5 分钟   $3.70   ✓1  │  ← 点一行 = 过滤下面的记录
│  没走 Project Agent 7    ███████░░░▒▒             4/5      26 秒    $4.15   ✓1  │
│                                                                              │
│ 五维（2 个 pipeline run）  Run Isolation ✓2 · Quality N/A 2 · Autonomy ✓2 ·    │
│                           Speed N/A 2 · Token Cost N/A 2                     │
│                                                                              │
│ 记录  [全部 8] [失败 0] [停下 1] [要人 3] [未知 2]      Brain: General ✕       │
│  ● E2E run record check     General · Runner   pipeline·research  做完        │
│                             5 分钟 · $3.70 · 要人 1（没答）· Judge ✓  10/29 17:29 ↗ │
│  ● E2E: run result of a…    没走 PA            pipeline·research  做完   …     │
│  ○ E2E: rewritten approval  没走 PA            —                  未知ⓘ  …     │
│  显示更早的 20 条                                                              │
│                                                                              │
│ 工蜂占用  TBD：每个 Hive 的并发没有按时间采样                                     │
└──────────────────────────────────────────────────────────────────────────────┘
```

示意图里的数字是 2026-10-29 当天 `/runs` 的真实数据（8 个 run）。**突出的只有一个**：结论句里的"无人值守做完"。其余数字降级，一律灰底 12px，不做大数字块。

## 4. 每个数的定义（全部来自 `/runs`）

`R` = 时间窗口内的 run，按 `sent_at` 的本机日期算（和现在的日柱一致）。窗口：近 7 天（默认）、近 30 天、全部。

| 数 | 定义 | 字段 |
|---|---|---|
| 结局 | 沿用 `outcomeBucket`：做完 / 失败 / 停下 / 未知；`state === 'running'` 单独算"在跑"，不进任何比率 | `result.ended`、`provisional`、`state` |
| 已知结局 | 结局不是"未知"、也不在跑的 run 数，所有比率都用它做分母，并且写出来（"已知结局 6 个"）。未知的 run 不算失败，也不算成功 | 同上 |
| **无人值守做完** | 做完，并且 `approved + denied + answered_questions + unanswered == 0` | `result.work.human` |
| 要人 | 人工介入次数之和（和卡片的"人工"同一个口径）；括号里是没人答的问题数，琥珀色 | `result.work.human` |
| 中位时长 | 已结束 run 的 `active_s` 取中位数，不取平均：一个跑了很久的 run 不该把整体拉偏 | `result.active_s` |
| 花费 | `cost_usd` 求和；只要有一个 run 是 `cost_partial` 或者没有花费，就写"≥"，悬停说明有几个 run 没算进去 | `result.cost_usd`、`cost_partial` |
| Judge | 有五维判定的 pipeline run 用 `judgeState` 判成 PASS / WARN / BLOCK 计数（`✓2 !1 ✕0`）；没有 pipeline run 就不显示 | `result.pipeline.audit` |
| 五维 | 每一维在这些 pipeline run 里各有几个 PASS / WARN / BLOCK / N/A，最差的排前面 | 同上 |
| 类型 | 一个 run 只归一类，按优先级：`pipeline · <profile>` > 改代码（`code`）> 写文件（`files`）> 只回答（`answer`）> 没有产出（`kinds` 为空）> 未知（没有 result） | `result.kinds`、`pipeline.profile` |
| 教训（只在按 Brain 拆时出现） | 带回 / 写进 Brain：`lessons.returned` / `lessons.written` 求和 | `result.lessons` |

一个都不猜：没有 result 的 run 进"未知"，悬停写原因（沿用 `endedLabel` / `resultErrorLabel` 的话），不是零。

## 5. 记录列表

- 一行就是 run 卡片的摘要，用的是卡片的同一组函数（`agentFacts`、`runType`、`endedLabel`、`runStats`、`runJudge`）。宽面板一行；窄面板（< 820px，`COMPACT_BELOW`）两行。
- 点击一行**在原地展开成完整的 `RunCard`**（详情、回复、代码、教训全部复用），再点一次收起。`↗` 打开 Hive tab，和卡片上的按钮一样。
- 过滤：结局 chip（全部 / 失败 / 停下 / 要人 / 未知，带计数），再加上拆开表里点中的那一行（Brain / 类型 / Hive），显示成可以 ✕ 掉的 chip。两种过滤叠加。
- 新的在前，先显示 20 条，"显示更早的"每次多 20 条。
- 未知那一行只写"未知"，原因放在 ⓘ 的悬停里。

## 6. 删掉、搬走、保留

- **删掉**："按 Brain / Project Agent 分组：TBD"这一行，以及一句话结论、五维、按类型三个 TBD 块（都换成了真数据）；按 Hive 分组的记录（Hive 变成拆开表的一个维度）。
- **搬走**：运行视图底部的"按 Brain（7 天）"挪到历史视图（拆开表的默认维度）。运行视图只管"现在"，过去的统计都在历史视图。
- **保留**：日柱（缩到 48px，跟着窗口走：7 天 7 根、30 天 30 根，"全部"也只画最近 30 天）；工蜂占用 TBD 一行。

## 7. 实现

- `live.ts` 新增纯函数：`inWindow(runs, now, days|null)`、`runType(r)`、`historySummary(runs)`、`breakdown(runs, 'brain'|'type'|'hive')`、`dimensionCounts(runs)`。`runsByBrain` 合进 `breakdown`；`runsByHive` 和 `runsByBrain` 原来的调用方一并迁走（R27：先 grep 全部调用方，包括测试）。
- `SwarmOutDashboard.tsx` 里的 `History` 重写；记录行是新组件 `HistoryRow`，展开时渲染现有的 `RunCard`（需要从 `LiveDispatches.tsx` export 出来）。
- 新增文案只加 `swarmOut.history.*`，en / zh 都加（`swarmOutI18n.test.tsx` 会查两边的 key 是否一致）。

## 8. 验证计划（定在这里，做完逐行报告）

实现时和设计不同的地方：卡片"消耗"格的拆分是悬停提示（四种 token），不按类别拆金额。CLI 只报总花费，按单价拆是猜的。宽面板一行一条记录，窄面板（`COMPACT_BELOW`）两行。

**最后要守住的不变量（测试）**
1. 做完 + 失败 + 停下 + 未知 + 在跑 = 窗口内的 run 数；每个比率的分母都等于"已知结局"，而且在页面上写出来。
2. 无人值守做完 ≤ 做完；只要有一次人工介入，就不算无人值守。
3. 只要有一个 run 没有花费或者花费不完整，花费就带"≥"。
4. 一个 run 只归一个类型；同时有 pipeline 和 files 的 run 归 pipeline。
5. 点拆开表的一行，记录列表只剩这一组；✕ 掉以后恢复。结局 chip 和它叠加。
6. 展开一行，出现的是 RunCard 的详情（回复、Judge），不是另一套。
7. 历史视图里不再出现那三个过时的 TBD；运行视图里不再有"按 Brain"。
8. 换窗口：日柱的根数和统计都跟着变。

**每处修改的检查**：`live.ts` 的新函数写单测，每个修复做一次 mutation 检查（把修复撤掉，测试要变红）；组件测试覆盖 5、6、7。提交前跑 `src/components/swarm-out` 的测试和 `tsc -b`。

**Review**：纯前端展示，不碰数据和安全面，不请专家；统计口径由第 1–4 条测试守住。
