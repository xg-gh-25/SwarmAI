---
title: Brain 的 Eval 和 Runs — 三个问题一个结论；run 用 Swarm Out 的列表和大卡片
created: 2026-11-01
updated: 2026-11-01
project: SwarmAI
status: design r3（XG 2026-11-01：上 3 个大数、下 3 节竖排；插手不含 review / approve / judge）
mockup: Knowledge/Designs/mockups/2026-11-01-brain-eval-and-runs.html（工作区内）
follows: 2026-10-30-brain-runs-one-card.md, 2026-10-31-swarm-out-run-review.md, 2026-10-31-cdf-health.md
---
# Brain 的 Eval 和 Runs

XG 2026-11-01：Eval 要回答的是 **这个 Brain 的知识健康吗、信息有用吗、run 自主吗**；
run 记录参考 Swarm Out 的 run 列表 + 点开大卡片。

## 1. 现在哪里不对（SwarmAI Brain，2026-11-01 实看）

- **Eval 答的不是这三个问题。** 现在是四个 run 统计格（Landed 4/8、Need you now、
  Average cycle 47 min、Tokens UNKNOWN），一个 Project Agent 格（Human decisions per Run
  0.1），两个 Brain 格（Knowledge entries 1916、Does it help Runs? UNKNOWN）。条目数是存量
  不是健康（P6：数量代替了判断）；"有没有用"写着 UNKNOWN，但数据其实有：CDF 的评审按项目
  记了判定（`cdf_health.db` `verdicts.project`，近 30 天 SwarmAI 的 DDD 19 条里 18 条
  useful、GAP 18 条里 15 条），pipeline 的需求 / 设计文档记了引用了哪些 Brain 条目
  （`knowledge_refs`），会话里给了 `[DDD:<P>]` 提示后有没有去读也能数
  （`scripts/knowledge_usage_report`）。
- **Runs 是一墙大卡片。** 每个 run 一张高卡（标题四行 + 6 个数字格），一屏三张，没有
  "等你 / 在跑 / 完成"的分区，找不到哪个要我。本机 run 点 "Open Run" 跳 Pipelines 的抽屉，
  Hive run 打开 Swarm Out 的大卡片：同一个 Brain 的 run 有两种看法。

## 2. Eval：三个问题，每个一个结论

**排版（r3，XG）**：最上面一行三个大数（每个问题的结论数 + 和什么比），下面三节从上往下，
每节占满宽度：结论的说明、辅助数、要处理的东西。不分列（三列挤在一起没法看）。

每一节回答一个问题：一个最显眼的数，旁边写和什么比（目标 / 上一期），下面是要处理的东西
（点了打开）。没测到的写"没测"，样本太少写"样本不足（n）"，不写 0。

**数据还年轻，界面要照实说。** 评审 job（`cdf-health`，工作日 UTC 09:00）10-31 才上线，到
11-01 只跑过一次：109 条判定全是那一次的，人改判 0 条；写入记录和 run 记录最早 10-29。所以：
窗口写"自 <第一条记录的日期> 起"，直到满 30 天；"对比前 30 天"在没有前一期时不显示；
"写入 14 天以上"这一项 11-12 前是"还不到 14 天"。

### 2.1 知识健康吗 —— Brain 里的东西对不对、干不干净

- **结论数**：近 30 天写进这个 Brain 的条目里，评审判 useful 的比例，对比目标 90%
  （`verdicts`，`project = P`，dest ∈ {DDD, GAP}；人改判过的用人的）。显示样本数；少于 10
  条写"样本不足"。
- **抽样要按 Brain 分**：现在每个去处全局随机抽 20 条（`health.py:55,577`
  `SAMPLE_PER_DEST`），SwarmAI 凑巧抽到 37 条，AIDLC 只有 1 条，别的 Brain 会一直"样本不足"。
  改成按（去处, 项目）抽，每个 Brain 每次至少 10 条（不够就全抽）。这是 CDF health 的改动，
  花费随 Brain 数增加，要在 health 设计 §4 里一起改。
- **要处理的**：判成 weak / withdraw 的条目（标题 + 理由一句 + 打开），重复的条目数（跨
  PRODUCT / TECH / IMPROVEMENT / PROJECT 一起比：`reclaim_duplicate_entries` 只比一个文件之内，
  `ddd_entry_lifecycle.py:979-1011`，要按项目把几个主文件合起来算），还没关的 gap 数和最老一条
  多久了。
- **不放**：条目总数。它挪到 Brain (DDD) tab 的标题行，是"有多少"，不是"好不好"。

### 2.2 知识被用上了吗 —— 信息有用度

评审只看条目本身写得好不好；"用上了"才是结果（CDF health §4 原话）。
- **结论数**：近 30 天这个 Brain 的 pipeline run 里，需求或设计引用了**这个 Brain 里仍有效、
  评审判有用**的条目的比例。只算 DDD 的 id：`knowledge_refs` 也可以指向 Library 段落或 pipeline
  lesson（`run_knowledge.py:5-16`），引擎只核对 id 存在（`engine.py:2637-2661`），所以"引用了
  什么"要先查 id 落在哪。只看"有没有引用"量的是引用习惯，不是有用。
- **漏用的信号**（更能说明问题）：run 的 REFLECT 教训和这个 Brain 里已有的条目说的是同一件事
  ——知识在，但这次没用上。列出这些 run。
- **辅助**：写入超过 14 天的条目里，后来被读过或被引用过的比例。`cognition_level.used` 今天只有
  全局数，按项目分只要按 `ingestion_writes.target_path` 里的 `Projects/<P>/` 过滤。
- **不放**："给了 `[DDD:P]` 提示后去读了"：`knowledge_usage_report` 里读任何 DDD、调任何
  `knowledge` 都算跟了（`knowledge_usage_report.py:113-118`），不分项目。

### 2.3 run 自主吗

- **结论数**：近 30 天结束的 run 里，人一次都没插手的比例，对比前 30 天（没有前一期就不比）。
- **插手怎么数（r3，XG：review、approve、judge 不算）**：插手 = run 需要你才能走下去或走对。
  一个函数，卡片、Eval、`humanPerRun`、Swarm Out 的自主度一词、audit 的 Autonomy 一维都用它。
  - **算**：回答 run 问你的问题（`AskUserQuestion`、pipeline 的 decision）；纠偏（修正重跑；
    run 跑着时你往它会话里发的消息，和 Hive 的 `said` 同口径，`swarm_out_results.py:496`）；
    救场（run 被丢下后由人手工落地；卡住后你手动恢复）。
  - **不算**：产品有意留给人的把关：批准或拒绝一个危险 / 不可逆的操作（权限请求、run-land、push）；
    验收交付物（通过、退回、丢弃）；接受一个 finding（`accept_finding`）；Judge 和评审专家的结论
    （机器给的）。
  - 今天和这个口径不一致的地方，一起改：audit 的 Autonomy 一维把 run-land 和 `accept_finding`
    算进了插手（`audit.py:419-428`）；Swarm Out 的自主度把权限的批准 / 拒绝算进了插手
    （`runReview.ts:136-140`）；本机 run 只记了 decision，没记会话里的消息和手工落地（今天 8 个 run
    全是 0，run_8437ff7e 是你手工合进 main 的，`reason` 写着 "on XG's call"）。
  - `manual_rescues` 一直是 null（`audit.py:425-428`）：手工落地就是第一个有来源的救场。
- **辅助**：每个 run 平均插手几次；Autonomy Judge 五维各自 PASS / WARN / BLOCK 几次（点一格
  列出是哪些 run）；"要过你"的 run 列表，一行一句为什么，点开是它的大卡片。
- **不放**：落地数、平均时长、token —— 这是 run 的统计，放到 Runs tab 的标题行。

不做综合分（和 Autonomy Judge 一样：三个问题各自成立，合成一个数会互相掩盖）。

## 3. Runs：Swarm Out 的列表 + 大卡片

### 3.1 列表

照 `RunList`（Swarm Out 设计 §2.1），本机和 Hive 合在一个列表里：
- 标题行：`30 天 · 落地 4/8 · 平均 47 分钟 · 花费 $X（有记录的才算）`。
- 分区：**等你**（等你回答 > 待你确认 > 停住了，按等了多久）永远在上面；**在跑**；
  **最近完成**（默认折叠）。
- 一行：状态点 · 标题（一行截断）· 在哪跑（Chat tab / Hive 名）· Agent mode · 自主度一个词
  （全自主 / 插手 N 次 / Judge BLOCK）· 要你做的事（一个动词标签）· 等了多久 · 常驻
  "打开会话 ↗" · 在跑时 "■ 停止"。花费、token、步骤不在行里。
- 不靠搜索找：分区 + 排序 + 大卡片里 J / K 走上一单 / 下一单。

### 3.2 大卡片

点一行打开同一张大卡片，不管本机还是 Hive：
- **Hive run**：就是 Swarm Out 的 `RunOverlay`。今天 Brain 里挂它时传的是
  `asks={[]} order={[]}`（`LiveDispatches.tsx:320`）：问题卡说"去别处回答"，J / K 不动。
  Brain 的 Runs tab 要自己读 asks（`useSwarmOutActivity`），并把两种 run 合成一个顺序传进去。
- **本机 run**：同一个 `RunOverlay`。左边 tab：交付物（REPORT 和落地的提交，文件用聊天的
  `FileItemRow`，点了在 Canvas 打开）· 自主度（Autonomy Judge 五维 + 人工决定明细）· 过程
  （stage 和重跑）· 花费 · 教训（REFLECT lessons）。右边命令按状态由后端给：等你回答 → 问题卡
  （pipeline 的 decision）；在跑 → 打开会话；被丢下但改动已在 main 上 → 确认已落地（landed-as）；
  完成 → 打开会话（只读）。字段分散在两个接口里：`PipelineRunDetail` 有 reportMd、commits、
  reflectLessons、decision、ownerSessionId、landedAs，没有 audit、humanDecisions、花费；
  `PipelineRunSummary` 反过来（`services/pipelines.ts:104-205`）；两者都没有 stage 重跑次数和
  逐次插手的明细。加一个 `GET /api/pipelines/{id}/view` 一次给齐。
- **本机 run 的命令只用已有的路**：回答（`POST /pipelines/{id}/decision`）、继续 / 取消
  （resume / cancel）、打开会话；"确认已落地"是要人授权的 landed-as（`engine.py:1902-1916`），
  要加一个人专用路由。本机没有"丢弃"，mockup r1 画的丢弃去掉。
- 本机的问题（`PipelineDecision`，`pipelines.ts:38-44`）和 Hive 的问题（`SwarmOutAsk`
  decision，`swarmOut.ts:233-249`）形状不同，adapter 把前者换成问题卡要的样子，并测一次回答的
  来回。

做法有三种：

| | 做什么 | 工作量 | 风险 | 取舍 |
|---|---|---|---|---|
| A | `RunOverlay` 直接改成读一个"run 视图" | 很大：命令全走 `swarmOutService`、问题卡走 S3 桥、验收条读 `machineChecks(SwarmOutRun)`、内容在 `RemoteContentContext` 里、跟着 Swarm Out 面板开关（评审逐条列了位置） | 一次改动碰 Swarm Out 正在用的卡片的每一层 | 一个组件 |
| **B（推荐，r2）** | 先抽出一个外壳 + `RunView` 接口（动作、等待、各 tab），共用的 tab 抽成组件；本机 run 是第一个 adapter，Swarm Out 的卡以后迁成第二个 | 中 | 小：Swarm Out 现在的卡不动 | 迁完之前有两个外壳，但读同一个接口、共用 tab，不会各走各的 |
| C | 本机 run 还是打开 Pipelines 抽屉 | 小 | 无 | 两种看法，就是现在的问题 |

## 4. 后端要补

- `GET /api/brains/{name}/eval`：三节的结论数、对比数、下钻列表，只读（`cdf_health.db`、
  各 run 的 `run.json` 和 artifacts、Swarm Out runs、knowledge_usage 的按项目汇总）。
  算一次缓存到快照（像 north_star），面板只读快照。
- 按项目的"写入后被用上"比例：`cognition_level.used` 加 `project` 维。
- 本机 run 的 `actions` 按状态给出（对应 Swarm Out 的 `swarm_out_settle.derive`），前端只渲染。
- `GET /api/pipelines/{id}/view`：一个本机 run 的大卡片要的全部字段（§3.2）。
- 人专用的"确认已落地"路由，包住 landed-as 的授权动作。
- 插手次数一个函数（§2.3），`PipelineRunSummary.humanDecisions` 和 Eval 都读它。
- CDF health 按（去处, 项目）抽样（§2.1）。

## 5. 不做

- 不做综合分，不把条目数、改动数当健康。
- 不在 Eval 里放 run 统计（落地数、时长、token），它们在 Runs 标题行。
- 不改 Swarm Out 的命令集和状态表。

## 6. 验证计划

- 后端：`/eval` 对一个造好的 workspace（几条判定、几个 run、一个 knowledge_refs）给出
  三节的数；样本 < 10 时给"样本不足"；没有记录时给 null，不给 0。
- 前端：Eval 三节各一个结论数和对比；没有条目总数；"要处理"的条目点了打开文件。Runs 是分区
  列表；本机行点开是新外壳（B），Hive 行是 `RunOverlay` 且带着 asks 和顺序（J / K 能走、问题能
  答）；本机 run 的命令来自后端 `actions`；run_8437ff7e 这种人工落地的 run 插手数是 1 不是 0。
- 每个测试回退修复会变红（mutation check）。
- 评审：方案 A 动到 Swarm Out 的大卡片，改完请 correctness + state-machine 专家各看一轮。

## 7. 要 XG 定的

1. 大卡片用 B（先抽外壳 + RunView 接口，本机 run 先接，Swarm Out 以后迁）？
2. ~~插手口径~~ 已定（r3）：见 §2.3。run 跑着时发进会话的消息都算纠偏，只问一句"进行到哪了"
   也会被算一次；分不出来就宁可多算，不靠模型判断。

## 8. 零上下文评审（2026-11-01）怎么折进来的

评审列了 4 HIGH / 4 MEDIUM / 3 LOW，关键几条已对源码和数据核实（判定只有 10-31 那一次、
run_8437ff7e 记 0 次插手但是人手工落地、Brain 里挂 RunOverlay 时 asks/order 为空）。按原因合成
六组，一次改完：
1. 数据年轻、抽样不按 Brain（H1、L10）→ §2 开头和 §2.1 抽样。
2. 插手数看不见真实的插手（H2）→ §2.3 一个口径。
3. "引用了"量的是习惯（H3、L11）→ §2.2 只算 DDD 且有效有用的条目，加漏用信号，去掉提示跟读。
4. 大卡片：Brain 里缺 asks/order（H4）；A 比写的难得多、数据不在一处、本机没有丢弃、问题形状不同
   （M5–M7）→ §3.2 改选 B，补 `/view`、确认已落地路由、adapter。
5. mockup 的 Judge 表和百分比是编的（M8）→ r2 用真实数或明确标"示例"。实数（5 个有 audit 的
   run）：Isolation 5 PASS，Quality 4 WARN 1 BLOCK，Autonomy 5 PASS，Speed 4 N/A 1 BLOCK，
   Token Cost 5 N/A。评审说"没有 BLOCK"不对。
6. 重复条目只比一个文件（L9）→ §2.1 跨主文件比。
