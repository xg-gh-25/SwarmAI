---
title: Pipeline 只管两头 — 合并 native-execution 草稿、现行 spec 和 10-26 发现的问题
created: 2026-10-26
updated: 2026-10-26
project: SwarmAI
status: r1 — 设计，待 XG 确认 §7 的决定
---

# Pipeline 只管两头

## 0. 主旨（XG）

**写代码全部交给 native。** Pipeline 只管两头：

- **前端**：把意图变成能核对的需求和方案（REQUIREMENTS、DESIGN_SPEC），需要时问人。
- **后端**：审查、汇总验证报告、落地、写 REPORT、沉淀经验（REVIEW、TEST、DELIVER、REFLECT）。

中间那一段（怎么写、怎么测、要不要并行）由 agent 用它平时的方式自己做，pipeline 只看出口：一份绑定代码树的验证报告。
pipeline 不跑测试、不规定写法、不选"构建模式"。这是 usable-by-anyone spec 的 r7 定稿（"pipeline 只做流程控制和质量收敛"），
也是 native-execution 草稿的 §0。

由此推出一条判断：**profile 只决定前端写多深、最后产出什么**，不决定代码怎么写。不同 profile 的中段完全一样。

## 1. 这份文档合并了什么

| 来源 | 状态 | 这里怎么处理 |
|---|---|---|
| `Projects/SwarmAI/2-understanding/knowledge/designs/2026-10-22-pipeline-native-execution.md`（NE 草稿） | DRAFT，10-23 写，在 S6 之前；方向由 XG 逐条确认过，未实施 | 逐条对照现在的代码（§2），还成立的并进来，S6 后已不成立的作废 |
| `docs/design/2026-10-24-swarmai-usable-by-anyone.md`（现行 spec） | S1–S6 已实施 | 不改它已实施的部分；它写"六个 profile 全部保留"，和 NE 的 D6 冲突，按 §7 的决定改一处 |
| `docs/design/2026-10-26-profile-capabilities.md` | 10-26 写，未实施 | 整份并进本文 §4、§5，原文标为被本文取代 |
| `docs/design/2026-10-26-research-deliver-fixes.md` | 已实施，`8ee85faba` | 它是 §5 那类问题的第一个实例，本文把同一修法推广到所有 profile |

## 2. NE 草稿逐条对照现在的代码

| NE 项 | 内容 | 现在 | 结论 |
|---|---|---|---|
| NE-1 | BUILD 卡片去掉写法要求 | S6 已改：`build.md` 写的是"照你没有 pipeline 时的方式测试"，出口是 BX-3（有改动，每个仓库在当前代码树上有 BUILD 报告） | 已完成。`goal_cycle.md` 随 goal 一起删（§6） |
| NE-2 | `status.next` | 已有（`next_step.py`，commit 4 handoff） | 有，但有三条路径会指错（§3）。用 §5 修，不另起 |
| NE-3 | TS-4：exit 测试在 base 上要按声明失败或通过 | 依赖 runner 在 base 上重跑测试，r7 以后 pipeline 不跑测试，runner 已删；现在的 TS-4 是另一件事（每条测试引用在代码里找得到，`18ca64436`） | **作废**。它要保证的"测试真的在测这次改动"，现在由 BUILD 卡片（先看到失败再修）、报告和 correctness 审查共同负责，不另设机制 |
| NE-4 | spec 要人能读（渲染补全、schema 加叙述字段） | 没做：`render_design` 里 `consumers` 还是逐行 JSON | 保留，排在后面（§8 第 6 步），等 §7 决定 spec profile 之后一起做 |
| NE-5 | 先把按名字判断的 profile 差异收成 lifecycle 的属性，再加 spec profile | 没做：产品代码里有 41 行按 profile 名字比较（§3.2） | 前半就是 §4，现在做；spec profile 待 §7 决定 |
| NE-6 | 自动续跑：run 是 active 但 owner 没在跑时，宿主自动 nudge | 没做：dashboard 显示 idle，要人点 Continue（`pipeline-v1-human-loop.md`） | 保留，排在后面（§8 第 7 步）。它要先定一条边界（nudge 和"dashboard 从不背着用户驱动 agent"的关系），由 XG 确认 |
| NE-7 | 删除 goal profile 和 GOAL_CYCLE | 没做 | **现在就删**（§6），理由见 §6 |

## 3. 10-26 发现的问题

### 3.1 照 `next` 走会卡住的 run

只读 `next` 给出的命令往下走（agent 实际就是这样用的），测试 `tests/test_pipeline_v1_follow_next.py`（分支 `kiro/profile-next`，在 main 上是红的）：

| 场景 | 结果 |
|---|---|
| research | 修过（`8ee85faba`）：之前 `next` 叫它跑 `report-tests`，`land` 留下一个没关闭的落地尝试，经验写不进去 |
| bugfix；full 且需求有两种理解 | 卡在 EVALUATE：`stage-complete` 返回 `awaiting_answer`、`handouts: []`，`next` 从不提 `gate0-start` |
| BUILD 连续失败 3 次（不落地的路径） | 卡在 DELIVER：`next` 一直给 `report-tests`，应该是 `stage-complete` |
| direct、trivial、full、goal | 能走到结局 |

现有测试没发现，是因为测试 rig 按阶段把正确步骤写死了，从不看 `next`。

### 3.2 根因：同一个事实写在好几处

- **"现在该跑哪条命令"有两份。** 命令自己判断（DELIVER 怎么结束写了 4 处；要不要 Gate-0 写了 2 处），`next_step` 又各推一遍，只推对了一部分。
- **profile 的能力靠名字比较散落各处。** 产品代码里 41 行：`engine.py` 22、`checks.py` 6、`pipeline_profiles.py` 6、`schemas.py` 3、`next_step.py` 2、`cli_commands.py` 2。
- **profile 规则还有第二个文件。** `core/pipeline_profiles.py` 有一套 `PROFILE_GATES`、`is_relaxed_profile`、`normalize_profile`、`get_profile_stages`、`REMOVED_PROFILES`；产品代码只用到 `PIPELINE_PROFILES`（`artifact_registry.py`，它只是转手读 lifecycle），其余只有测试在用。
- **两份 spec 对 goal 说法不同。** 现行 spec 说保留六个 profile，NE 草稿 D6 说删 goal。

这和聊天那次（多条写路径各有一套合并规则）是同一类问题：一个事实定义在多于一处。

## 4. profile：一张表，只有 lifecycle 一个来源

### 4.1 删 goal 之后的 profile

| profile | 前端产出 | 阶段 | 产出 |
|---|---|---|---|
| full | REQUIREMENTS + DESIGN_SPEC（完整） | evaluate, think, plan, build, review, test, deliver, reflect | 代码 |
| bugfix | 同 full，REQUIREMENTS 带 current / expected / unchanged | 同 full | 代码 |
| trivial | REQUIREMENTS（完整）+ DESIGN_SPEC（简版） | evaluate, plan, build, review, test, deliver, reflect | 代码 |
| direct | 无文档，需求是 `--requirement` 原话 | build, review, test, deliver, reflect | 代码 |
| research | REQUIREMENTS | evaluate, think, deliver, reflect | 报告，不落地 |

大任务（goal 原本的用途）拆成一串按顺序执行的普通 run。是否加 `spec` profile 见 §7 决定 2。

### 4.2 能力表

放在 `lifecycle.py`，紧挨着 `PROFILE_STAGES`，每个 profile 一行：

| 字段 | 含义 |
|---|---|
| `lands_code` | 落不落地代码（research 为 False） |
| `gate0` | `always`（bugfix）/ `readings`（full：两种以上理解时）/ `never` |
| `requirements` | `full` / `lite`（direct）/ `None` |
| `design` | `full` / `lite`（trivial）/ `None`（research、direct） |

engine、lifecycle、next_step、checks、schemas、cli_commands 里按名字比较的地方全部改为读这张表。`checks.py` 的豁免表保留，因为它记录每条检查为什么不适用。

`pipeline_profiles.py` 只留 `PIPELINE_PROFILES`；只有测试在用的那几个函数连同测试一起删掉。`PROFILE_GATES` 里还写着 goal 要派 gate0，和能力表重复，也删掉。

## 5. "下一条命令"只由一处决定

- 每个路由判断只写一个函数，放在 lifecycle 或 engine 里；命令接受不接受、`next` 指向哪里，都调用它：
  - `deliver_route(state) -> "land" | "finish"`：能力表说不落地，或已有 `pending_landing`，就是 `finish`。
  - `gate0_status() -> {required, open_handout, answered, second_round}`：从日志读，不靠参数传入。
- `next_step` 自己不再判断规则，只把这些函数的结果组合成 `next`。
- 命令拒绝时返回的错误里写明 `next`，这样 agent 走错一步也能被带回正路。

## 6. 删 goal

- **为什么现在删，不等自动续跑（改 NE 的 D6）。** D6 让等自动续跑，是因为当时 goal 提供跨 cycle 的循环、`GOAL_TDD_MAX` 和 full-scope 轮数豁免。runner 删掉之后，后两样已经没有了；v1 里的 `goal_cycle` 就是换了名字的 BUILD：检查同样是 BX-3，卡片写着"和 BUILD 一样测试"，一次 `stage-complete` 就结束。跨 cycle 的循环在 v1 里本来就不存在。所以现在删，不会少任何能力。
- **删哪些**：`PROFILE_STAGES` 的 goal 行、`goal_cycle` 阶段（卡片、`validation_report.STAGES`、`events.py` 的阶段集合、`checks.py` 的 BX-3 阶段范围和第 220、312 行、`engine.WORKTREE_STAGES`、`pipeline_stage_events` 的序号）、`{"full", "bugfix", "goal"}` 这几处，以及测试里的 goal 用例。以 grep 结果为准。
- **旧数据**：按"不动历史数据"的规则处理。删之前查一遍还没结束的 goal run（`run_index`，只读）。没有的话直接删；有的话先停下来问 XG。已结束的 goal run 只读不改；`status` 读到一个不认识的 profile 时，返回"这个 run 的 profile 已删除"，不报错退出。
- `create --profile goal` 给出明确的拒绝，提示"大任务拆成一串 run"。

## 7. 要 XG 定的事

1. **goal 现在就删**，不等自动续跑（§6）。同时把现行 spec 的"六个 profile 全部保留"改成五个，并写明原因。
2. **spec profile（NE 的 D7）做不做。** 它产出一份不落地、人能读的 spec，可以拿去线下评审，再用 `--from-spec` 起跑代码 run。
   - 不做：要先出 spec 时，用 full 跑到 PLAN 后 abandon，或者用 research。
   - 做：能力表加一行（`lands_code: False`、`design: full`），再加上 NE-4 的渲染和 `--from-spec`，工作量最大的是 NE-4。
   - 我建议这次先不做，等真实 run 里确实出现"只要 spec"的需求再说。
3. **自动续跑（NE-6）放进这轮，还是另开一轮。** 它要先定 nudge 和"dashboard 从不背着用户驱动 agent"的边界。我建议另开一轮，先把 §4–§6 做完。
4. **NE-3（TS-4 base）作废**，不再提。

## 8. 实施顺序

每一步一个提交，在自己的 worktree 里做，测试先行，做变异检查，合进本地 main，不 push。

| 步 | 内容 | 验收 |
|---|---|---|
| 1 | 合入跟随测试（每个 profile + Gate-0 必需 + BUILD 失败 3 次 + DELIVER 时 main 移动）和结构测试（产品代码里按 profile 名字比较的行数），各带一个只许缩小的 `KNOWN` | 两个测试在 main 上通过，`KNOWN` 记的是现状 |
| 2 | 删 goal（§6） | 跟随测试少一个 profile；结构测试的计数减少；grep 不到 `goal_cycle` |
| 3 | 能力表加上替换名字比较；`pipeline_profiles.py` 收缩 | 结构测试的 `KNOWN` 为空（豁免表除外） |
| 4 | `deliver_route`，命令和 `next` 都用它 | 跟随测试里 not-landed 那一项通过 |
| 5 | `gate0_status`，命令和 `next` 都用它 | bugfix 和"两种理解"两项通过；跟随测试的 `KNOWN` 为空 |
| 6 | 同步文档：现行 spec 改为五个 profile；NE 草稿和 `2026-10-26-profile-capabilities.md` 标为被本文取代；AGENTS.md、skill 卡片 | 文档里没有残留的 goal 和 goal_cycle |
| 7（另开一轮，看 §7 决定 3） | 自动续跑 | NE-6 的 V1–V3 |
| 8（看 §7 决定 2） | spec profile + NE-4 渲染 | — |

第 2 步放在第 3 步前面，是因为删掉 goal 之后，能力表少一行，要替换的名字比较也会少几处。

## 9. 五维

| 维度 | 影响 |
|---|---|
| Run Isolation | 不变 |
| Quality | 不变：没有删掉任何检查；goal 的检查和 BUILD 完全相同 |
| Autonomy | 变好：照 `next` 走的 run 不会再卡在 EVALUATE 或 DELIVER |
| Speed | 不变；`next` 多读一次日志，日志本来就在读 |
| Token | 卡住的 run 不再反复调用同一个命令 |

改动只在 CLI 和 pipeline 模块里，不碰聊天的发送路径。需要重新打包才会生效。
