---
title: Brain 只管知识；run 归执行它的地方 — Brain Eval 两个问题，Pipelines 改成 Swarm Out 的列表 + 大卡片
created: 2026-11-01
updated: 2026-11-01
project: SwarmAI
status: design r4（XG 2026-11-01：Brain 里不放 run；自主度放 Swarm Out / Pipelines；Pipelines overlay 一起改）
mockup: Knowledge/Designs/mockups/2026-11-01-brain-eval-and-runs.html（工作区内，r4）
follows: 2026-10-30-brain-runs-one-card.md, 2026-10-31-swarm-out-run-review.md, 2026-10-31-cdf-health.md
---
# Brain 只管知识；run 归执行它的地方

XG 2026-11-01：Brain 的 Eval 要看知识健康、信息有用、run 自主；run 记录参考 Swarm Out 的列表
+ 点开大卡片。r4 再定一条：**Brain 只是大脑，可以指挥任何地方的 run，它不该自己统计 run。**
于是：Brain 里只留知识的两个问题；run 的列表、大卡片和自主度放在执行它的地方——本机 run 在
Pipelines，Hive run 在 Swarm Out。

## 1. 现在哪里不对（2026-11-01 实看）

- **Brain Hub 把 run 又读了一遍。** Conductor · Runs tab 和 Eval 里的落地数、时长、token、
  插手次数，读的是 Pipelines 和 Swarm Out 已经在读的记录。两份读法一不一样就出事：同一个
  Brain 的本机 run 和 Hive run 两种卡片；Brain 里挂 Hive 的大卡片时 asks / order 传空
  （`LiveDispatches.tsx:320`），问题答不了、J / K 不动。
- **Brain 的 Eval 答的不是知识的问题。** 条目总数 1916 是存量不是健康（P6），"Does it help
  Runs?" 写 UNKNOWN，可数据其实有：CDF 评审按项目记了判定（`cdf_health.db` `verdicts.project`），
  pipeline 的需求 / 设计记了引用了哪些条目（`knowledge_refs`）。
- **Pipelines overlay 是一张回顾统计表。** 顶上 6 个数字格（Runs、Completion、Avg cycle、
  Tokens、Needs you、Running now），一条按周的吞吐柱，置顶的 Needs you 区，按项目折叠的分组
  （只展开第一个），每行 profile / 时间 / 时长 / token，点开是 420px 的右侧抽屉。
  - 没有自主度。
  - 按项目折叠：要找一个 run 先猜它在哪个项目里，再展开。
  - 一行塞了时长和 token，"它要我做什么"反而没有。
  - 抽屉和 Swarm Out 的大卡片是两套看法；它的命令（继续 / 回答 / 取消）散在抽屉里。
- **插手次数看不见真实的插手。** 本机 8 个 run 的 `human_decisions` 全是 0，连 run_8437ff7e
  也是 0，可它是人手工合进 main 的（`reason`："landed natively as 827a1e29b on XG's call"）。

## 2. Brain：只管知识

### 2.1 删掉

- Conductor · Runs tab；Eval 里的 run 统计和 Project Agent 一节；Overview 地图上的 Conductor
  节点和 run 点。
- Brain 的 Needs you 只放这个 Brain 自己的事（提案、上报、新读进来的仓库）。run 等你的，在全局
  Need You 和 Pipelines / Swarm Out 里。
- 留下：Project Agent tab（"用这个 Brain 去跑时怎么做"是大脑的一部分，不是 run 记录）。

### 2.2 Eval：两个问题

**排版（XG r3）**：最上面两个大数（每个问题的结论 + 和什么比），下面两节从上往下，占满宽度：
怎么算的、辅助数、要处理的东西。不分列。没测到的写"没测"，样本太少写"样本不足（n）"，不写 0。

**数据还年轻，照实说。** 评审 job（`cdf-health`，工作日 UTC 09:00）10-31 才上线，到 11-01 只跑
过一次：109 条判定全是那一次的，人改判 0 条；写入记录最早 10-29。窗口写"自 <第一条记录的日期>
起"直到满 30 天；没有前一期就不比；"写入 14 天以上"在 11-12 前写"还不到 14 天"。

**知识健康吗** —— Brain 里的东西对不对、干不干净
- 结论数：写进这个 Brain 的条目里，评审判 useful 的比例，对比目标 90%（`verdicts`，`project = P`，
  dest ∈ {DDD, GAP}；人改判过的用人的），带样本数。
- 抽样按 Brain 分：现在每个去处全局随机抽 20 条（`health.py:55,577` `SAMPLE_PER_DEST`），
  SwarmAI 凑巧 37 条，AIDLC 1 条。改成按（去处, 项目）抽，每个 Brain 每次至少 10 条（不够就全抽），
  在 CDF health 设计 §4 一起改（花费随 Brain 数增加）。
- 要处理的：判成 weak / withdraw 的条目（标题 + 理由一句 + 在 Canvas 定位到它）；重复的条目数，
  跨 PRODUCT / TECH / IMPROVEMENT / PROJECT 一起比（`reclaim_duplicate_entries` 只比一个文件之内，
  `ddd_entry_lifecycle.py:979-1011`）；没关的 gap 数和最老一条多久了。
- 条目总数挪到 Brain (DDD) tab 的标题行。

**知识被用上了吗** —— 信息有用度。评审只看条目本身；"用上了"才是结果。这一问要看 run，但问的
是知识，所以留在 Brain。
- 结论数：这个 Brain 的 pipeline run 里，需求或设计引用了**这个 Brain 里仍有效、评审判有用**的
  条目的比例。只算 DDD 的 id（`knowledge_refs` 也能指向 Library 段落和 pipeline lesson，
  `run_knowledge.py:5-16`；引擎只核对 id 存在，`engine.py:2637-2661`）。
- 漏用：run 的 REFLECT 教训和这个 Brain 里已有的条目说的是同一件事——知识在，这次没用上。列出来。
- 辅助：写入超过 14 天的条目里，后来被读过或被引用过的比例（`cognition_level.used` 按
  `ingestion_writes.target_path` 的 `Projects/<P>/` 过滤）。
- 不放"给了 `[DDD:P]` 提示后去读了"：`knowledge_usage_report.py:113-118` 读任何 DDD 都算，不分项目。

## 3. 自主度：一个口径，放在 run 待的地方

**插手 = run 需要你才能走下去或走对**（XG r3：review、approve、judge 不算）。后端一个函数，
Pipelines、Swarm Out、audit 的 Autonomy 一维都用它。
- **算**：回答 run 问你的问题（`AskUserQuestion`、pipeline 的 decision）；纠偏（修正重跑；run 跑着时
  你往它会话里发的消息，和 Hive 的 `said` 同口径，`swarm_out_results.py:496`——只问一句"进行到
  哪了"也算一次，分不出来就宁可多算，不靠模型判断）；救场（run 被丢下后由人手工落地；卡住后你
  手动恢复）。
- **不算**：批准或拒绝危险 / 不可逆的操作（权限请求、run-land、push）；验收交付物（通过、退回、
  丢弃）；接受一个 finding（`accept_finding`）；Judge 和评审专家的结论。
- 今天不一致、要一起改的：audit 的 Autonomy 一维算进了 run-land 和 `accept_finding`
  （`audit.py:419-428`）；Swarm Out 的自主度算进了权限的批准 / 拒绝（`runReview.ts:136-140`）；
  本机 run 没记会话里的消息和手工落地。`manual_rescues` 一直是 null（`audit.py:425-428`），手工
  落地是它第一个有来源的值。

**放在哪**：Pipelines 和 Swarm Out 的标题行各一句：`30 天 · 结束 N 个 · 没人插手 M 个 ·
平均插手 x 次`；每一行有自主度一个词（全自主 / 插手 N 次）；大卡片的自主度 tab 列逐次明细。

## 4. Pipelines overlay：Swarm Out 的列表 + 大卡片

### 4.1 列表

照 `RunList`（Swarm Out 设计 §2.1）：
- 标题行：`30 天 · 落地 4/8 · 没人插手 7/8 · 平均 47 分钟 · 花费 $X（有记录的才算）`。取代顶上
  6 个数字格和按周吞吐柱（吞吐不支撑任何决定）。
- Brain 筛选 chip（从行里的 Brain 生成，带数，不写死），取代按项目折叠分组。
- 分区：**等你**（等你回答 > 待你确认 > 停住了，按等了多久）永远在上面；**在跑**；**最近完成**
  （默认折叠，分页）。"Needs you" 就是"等你"分区的行数（今天的格子和置顶区已经是同一个定义，
  `needs_you`，只是字段名叫 `aborted_count`，`routers/pipelines.py:402`；顺手改名）。
- 一行：状态点 · 标题（一行截断）· Brain · profile · 自主度一个词 · 要你做的事（一个动词标签）·
  等了多久 · 常驻"打开会话 ↗"。时长、token、步骤不在行里（去大卡片）。

### 4.2 大卡片（取代 420px 抽屉）

做法三选一，推荐 B：

| | 做什么 | 工作量 | 风险 | 取舍 |
|---|---|---|---|---|
| A | `RunOverlay` 直接改成读一个"run 视图" | 很大：命令全走 `swarmOutService`、问题卡走 S3 桥、验收条读 `machineChecks(SwarmOutRun)`、内容在 `RemoteContentContext` 里、跟着 Swarm Out 面板开关 | 一次碰 Swarm Out 正在用的卡片的每一层 | 一个组件 |
| **B（推荐）** | 抽一个外壳 + `RunView` 接口（动作、等待、各 tab），共用的 tab 抽成组件；本机 run 是第一个 adapter，Swarm Out 的卡以后迁成第二个 | 中 | 小：Swarm Out 现在的卡不动 | 迁完前有两个外壳，但读同一个接口、共用 tab |
| C | 留着抽屉 | 小 | 无 | 两套看法，就是现在的问题 |

外壳照 `RunOverlay`：顶上验收标准 + 机器核对（✓ / △，不挡命令）；左边 tab：交付物（REPORT 和
落地的提交，文件用聊天的 `FileItemRow`，点了在 Canvas 打开）· 自主度（插手明细 + Autonomy
Judge 五维）· 过程（stage 和重跑）· 花费 · 教训（REFLECT lessons）；右边命令面板，命令由后端按
状态给，前端只渲染，J / K 走"等你"的下一单。
- 本机命令只用已有的路：回答（`POST /pipelines/{id}/decision`）、继续 / 取消（resume / cancel）、
  打开会话；"确认已落地"是要人授权的 landed-as（`engine.py:1902-1916`），加一个人专用路由。
  本机没有"丢弃"。
- 字段今天分在两个接口里（`PipelineRunDetail` 有 reportMd、commits、reflectLessons、decision、
  ownerSessionId、landedAs；`PipelineRunSummary` 有 audit、humanDecisions、花费；都没有 stage 重跑
  次数和逐次插手明细，`services/pipelines.ts:104-205`）：加 `GET /api/pipelines/{id}/view` 一次给齐。
- 本机的问题（`PipelineDecision`，`pipelines.ts:38-44`）和 Hive 的问题（`SwarmOutAsk` decision，
  `swarmOut.ts:233-249`）形状不同：adapter 把前者换成问题卡要的样子，测一次回答的来回。

## 5. Swarm Out：只加自主度

标题行加 §3 那一句，行里的自主度一词改用新口径（不再算权限的批准 / 拒绝）。列表、大卡片、
命令集、状态表都不动。

## 6. 后端要补

- 插手次数一个函数（§3）：本机 run 读 decision 事件、run 期间会话里的消息、手工落地；Hive 读
  answered + said；audit 的 Autonomy 一维和 `PipelineRunSummary.humanDecisions` 都改读它。
- `GET /api/brains/{name}/eval`：两节的结论数、对比数、下钻列表，只读，算一次缓存成快照（像
  north_star），面板只读快照。
- CDF health 按（去处, 项目）抽样；`cognition_level.used` 按项目过滤。
- `GET /api/pipelines/{id}/view`；本机 run 的 `actions` 按状态给（对应 `swarm_out_settle.derive`）；
  人专用的"确认已落地"路由。
- `GET /api/pipelines/analytics` 的标题行数字（落地、没人插手、平均插手、花费）和"等你"的排序。

## 7. 不做

- 不做综合分，不把条目数、改动数当健康。
- Brain 里不放任何 run 统计。
- 不改 Swarm Out 的命令集、状态表和大卡片（B 之后再迁）。
- 不把本机 run 并进 Swarm Out 的列表（所有 run 一个地方看是另一件事）。

## 8. 验证计划

- 后端：插手函数对造好的事件给出对的数（回答 + 会话消息 + 手工落地算；权限批准、run-land、
  accept_finding、验收不算）；run_8437ff7e 这种手工落地的是 1；audit 的 Autonomy 一维跟着变。
  `/brains/{name}/eval` 对造好的 workspace 给两节的数，样本 < 10 给"样本不足"，没记录给 null。
  `/pipelines/{id}/view` 一次给齐 §4.2 的字段。
- 前端：Brain 里没有 Runs tab、没有 run 统计、地图没有 Conductor；Eval 两个大数 + 两节竖排。
  Pipelines 是分区列表 + Brain chip，标题行带自主度；点一行打开大卡片（不再有抽屉），命令来自
  后端 `actions`，问题能答，J / K 能走。Swarm Out 标题行有自主度，行里的词用新口径。
- 每个测试回退修复会变红（mutation check）。
- 评审：插手函数动到 audit 和 Swarm Out 的计数，大卡片外壳是新的状态 / 命令面板：改完请
  correctness + state-machine 专家各看一轮。

## 9. 要 XG 定的

1. 大卡片用 B？
2. 这是跨模块的改动（audit 计数、Swarm Out、Pipelines、Brain Hub、CDF health 抽样）：直接改，还是
   走 pipeline？

## 10. 评审记录

r2 折入零上下文评审（4 HIGH / 4 MEDIUM / 3 LOW，关键几条已对源码和数据核实）：数据年轻、抽样不按
Brain；插手数看不见真实插手；"引用了"量的是习惯；大卡片 A 比写的难、数据分在两个接口、本机没有丢弃、
问题形状不同；mockup 的数是编的（实数：5 个有 audit 的 run，Isolation 5 PASS，Quality 4 WARN 1
BLOCK，Autonomy 5 PASS，Speed 4 N/A 1 BLOCK，Token Cost 5 N/A）；重复条目只比一个文件。r3：排版
竖排，插手不含 review / approve / judge。r4：Brain 不放 run，Pipelines 一起改。
