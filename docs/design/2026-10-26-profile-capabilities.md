---
title: profile 的能力只定义一次，`next` 和命令用同一套判断
created: 2026-10-26
updated: 2026-10-26
project: SwarmAI
status: r1 — 设计，待 XG 确认
---

# profile 的能力只定义一次，`next` 和命令用同一套判断

起因：research run 在 DELIVER 卡住（`2026-10-26-research-deliver-fixes.md`，已修，`8ee85faba`）。XG 要求看看其他
profile 有没有同类问题，系统地解决。

## 1. 怎么查的

写了一个"只照 `next` 走"的测试（`tests/test_pipeline_v1_follow_next.py`，在分支 `kiro/profile-next` 上，没合进 main）：
测试不知道任何阶段规则，只读 `next` 给出的 action 和命令，交出对应的产物，一直走到结束。agent 实际也是这样用 pipeline 的。
跑了 6 个 profile，以及两条非正常路径。

| 场景 | 结果 |
|---|---|
| direct、trivial、full、goal、research | 走到正确结局（research 是 `8ee85faba` 修好之后） |
| **bugfix** | 卡在 EVALUATE：`stage-complete` 返回 `awaiting_answer`、`handouts: []`，`next` 又给出 `stage-complete`，一直循环 |
| **full，`understanding.readings` 有两种理解** | 同上，卡在 EVALUATE |
| **BUILD 连续失败 3 次（not-landed 路径）** | 卡在 DELIVER：`next` 一直给出 `report-tests`，正确的命令是 `stage-complete`（`finish_not_landed`） |

现有测试为什么全绿：rig 的 `complete_stage` 按阶段把正确的步骤写死了（bugfix 先调 `gate0()`；DELIVER 没 repo 就 `stage-complete`），
从来不看 `next`。所以 `next` 给错了路，没有任何测试会发现。

## 2. 根因

**"现在合法的下一条命令"有两个来源。**

- 命令自己的判断，在 engine 和 lifecycle 里：
  - DELIVER 怎么结束，看两件事：research 不落地，有 `pending_landing` 就走 not-landed 路径。这个判断分别写在
    `stage_complete`、`finish_not_landed`、`_normal_land`、`_prepare_land_request` 里，一共 4 处；
  - 要不要 Gate-0（bugfix 一定要；full 或 goal 有两种以上理解时要），在 `gate0_start` 和 `stage_complete` 里各写了一遍。
- `next_step` 又自己推了一遍，规则只有一部分：DELIVER 只看"reflect 准备好没有"和 LG-1；EVALUATE 只说 `stage-complete`。

另外，profile 的能力是用字面量散落在各处的：`profile == "research"` 和 `!= "research"` 一共 12 处，`{"full", "bugfix", "goal"}` 出现 3 次，
`direct` / `trivial` 的特殊处理有好几处。哪一处漏了，哪一处就出错，research 的 3 个问题和这次的 2 个都是这么来的。

## 3. 方案

### 3.1 profile 能力表，只有一张

在 `lifecycle.py` 里，紧挨着 `PROFILE_STAGES`，给每个 profile 写一行：

| 字段 | 含义 | 取值 |
|---|---|---|
| `lands_code` | 会不会落地代码 | research 为 False，其余为 True |
| `gate0` | Gate-0 什么时候派 | bugfix 为 `always`；full、goal 为 `readings`（两种以上理解时）；其余为 `never` |
| `requirements` | REQUIREMENTS 的形式 | direct 为 `lite`，其余为 `full` |
| `design` | DESIGN_SPEC 的形式 | research、direct 为 `None`；trivial 为 `lite`；其余为 `full` |

engine、lifecycle、next_step、checks 里所有按 profile 名字比较的地方，都改为读这张表。`checks.py` 的豁免表照旧保留
（它记的是每条检查为什么不适用的原因），只有"research 没有代码"这一条改成读 `lands_code`。

### 3.2 每个路由判断只写一个函数，命令和 `next` 都调它

- `lifecycle.deliver_route(state) -> "land" | "finish"`：不落地的 profile，或者已经有 `pending_landing` 时，是 `finish`。
  - `stage_complete` 在 DELIVER 时，`finish` 才接受；
  - `land` 和 `run-land --prepare` 在 `finish` 时直接拒绝，并且不写任何事件；
  - `next`：`finish` 就给出 `stage-complete`，`land` 时才去看 LG-1、决定要不要先 `report-tests`。
- `engine.gate0_status() -> {required, open_handout, answered}`：从日志里的事件读出来，不靠传参。
  - `next` 在 EVALUATE 时：需要 Gate-0、但还没有 handout，给出 `gate0-start --artifact -`；有 handout、还没回答，给出
    `wait`，并带上 handout；需要第二轮时再次给出 `gate0-start`；其余情况给出 `stage-complete`；
  - `stage_complete` 和 `gate0_start` 也用同一个函数，不再各算一遍"要不要派"。

### 3.3 验收：照 `next` 走必须走到结局，并加一个只许缩小的已知失败集合

- `test_pipeline_v1_follow_next.py` 覆盖每个 profile，以及这几条路径：Gate-0 必需（第一轮回答是否定的、第二轮通过）；
  BUILD 连续失败 3 次；DELIVER 时 main 往前走了（先 `report-tests`，再 `land`）；run 被暂停后恢复。
  `KNOWN` 先列出现在会失败的场景，只能删、不能加；改完之后要为空。
- 结构测试：`lifecycle.py` 以外的产品代码里，不能出现 profile 名字的字面量比较（找 `== "research"` 这类写法），
  已有的写法也列进同一个只许缩小的集合。
- rig 的 `complete_stage` 不改。旧测试照旧跑；新的跟随测试才是"`next` 对不对"的依据。

### 3.4 不做

- 不改哪些检查适用于哪个 profile。
- 不新增 profile。
- 不改 Gate-0 的判断规则本身，只是让它只写在一处。

## 4. 实施

1. 先合入跟随测试和结构测试，带上 `KNOWN`（现在会失败的 3 个场景，以及字面量比较的现状）。
2. 加能力表，把字面量逐处替换掉；每替换一处，就从结构测试的 `KNOWN` 里删一项。
3. 加 `deliver_route`，engine 和 next_step 都调它，not-landed 那个场景从 `KNOWN` 删除。
4. 加 `gate0_status`，同样两边都调它，bugfix 和"两种理解"两个场景从 `KNOWN` 删除。

每一步一个提交，测试先行，做变异检查（撤掉改动，对应场景必须变红）。改动只在 CLI 里，不碰聊天的发送路径。
需要重新打包才会生效。
