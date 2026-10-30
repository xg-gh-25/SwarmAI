---
title: 补齐 Autonomy Tenets 的三个缺口：golden tasks 与基线、纠正台账与重复纠正率、知识健康与撤掉知识对照
created: 2026-10-30
updated: 2026-10-30
project: SwarmAI
status: 待确认（r2：已过一轮零上下文前提审查，修订见 §6）
follows: Knowledge/Designs/2026-10-30-cntech-ivt-autonomous-tenets-v0.2.md（团队验收标准）, 2026-10-30-brain-driven.md（v5；第 3 部分建在它上面）, 2026-10-30-brain-change-feed.md
---

# 补齐 Autonomy Tenets 的三个缺口

## 0. 背景与范围

团队验收标准（Tenets v0.2）拿来对照 SwarmAI 自己，有三处过不了：

| 缺口 | 对应 checkpoint | 一句话现状 |
|---|---|---|
| ① 没有 autonomous flow 本身的 golden tasks 和基线 | E1 🔴 · E2 · E3 🔴 · E8 | 离线考题只能考"读文件、回答"，考不到交付流程 |
| ② 重复纠正率算不出来 | H5 · E7 🔴 · H4 🔴 | 纠正由模型抽取，"是不是同一类"也由模型判断，人从不确认 |
| ③ 知识健康测不出来，也没有撤掉知识的对照 | K4 · E6 | `ddd_health` 的输入已经没有写入方；没有任何撤掉知识的手段 |

本文只做设计，不改代码。三部分可以分开落地，顺序见 §5。

---

## 1. Run 记分卡与 golden tasks（E1 / E2 / E3 / E8）

### 1.1 现有代码

- **离线考题考不到交付流程。**
  - 只有 product case 会放进 fixture 工作区（`backend/scripts/eval_runner.py:1612-1614`）。
  - agent 由 `backend/scripts/scenario_runner.py:325-444` 用 `claude --print` 拉起，工具只有 `{"Read","Grep","Glob"}`（`eval_runner.py:1588-1590`），超时 240s。
  - prompt 构建时 `include_ephemeral=False`，没有 recall（`core/prompt_builder.py:1766-1788`）。
- **每个真实 run 已经记下了大部分指标。**
  - **Pipeline audit**（`core/run_protocol/audit.py:322-545`）：
    - `autonomy.human_decisions`：人的回答加 `human_override`，后者计在 `lifecycle.py:537`。
    - `required_approvals`：危险命令审批和 `reserved_for_human` 提示（`security_hooks.py:2016-2019, 2168`），和 `human_decisions` 不重叠。
    - `manual_rescues` 恒为 None。
    - `quality`、`speed`（按 profile 取最近 ≥5 个 landed run 的 p95，`baselines.py`）、`token_cost`、`outcome.landing`。
    - 决策事件已经带 `decision_kind`（`requirement_ambiguity` | `design_tradeoff`，`run_protocol/decision.py:17`）。
  - **Hive 结果**（`core/swarm_out_results.py:199-427`）：
    - `ended`，表示 turn 结束了，不代表交付被收下。
    - 四类 token、`cost_usd`。
    - `work.human{approved,denied,unanswered,answered_questions}`，其中 `answered_questions` 混着 pipeline 决策卡和临时提问。
    - `pipeline{landing,landed_sha,audit}`（:360-372）。
    - `checks[{status: ran|errored|unknown|not_run}]`：靠匹配 agent 自己写的 Bash 命令行得出（`_runs` :159-168）。`pytest -q || true` 或改完代码再跑一遍，都会算 `ran`。
- **派发不能指定起点。** repo 永远取 host checkout 的 HEAD（`core/task_spec.py:155-180`），`repo_ship.pack` 在 HEAD 变动时会报错（`repo_ship.py:94-112`）。checkout 只是给 agent 的一条指示（`task_repos.py:157-167`）。每个 Hive 上一个 URL 只有一个 repo 目录。
- **`3-gates/` 不能放答案。** 它的每个文件都计入 Project Agent revision（`core/project_agent.py:243-257, 466`）：一改，状态就变成 `changed`，`admit` 会拒绝派发（`task_spec.py:197-203`）。而且整个目录会同步到 Hive（`brain_sync.py:97-98`），被测 agent 读得到。

### 1.2 设计

两条线：**每个真实 run 都打分**（online，不另花钱）；**golden tasks 定期重放**（offline，用来做回归）。

**① Run 记分卡（online）**

每个结束的 run 生成一行，字段全部从已有记录确定性读出，缺数据就是 None：

| 字段 | Host pipeline run | Hive run |
|---|---|---|
| `completed_autonomously` | `landing=landed` 且 `unplanned=0` 且 `rescues=0` | 带 pipeline 的：Hive `pipeline.landing=landed` 且 `unplanned=0`；不带 pipeline 的：§1.2② 的 host 复核通过 |
| `planned` | `required_approvals` | `work.human.approved` |
| `unplanned` | `human_decisions` 减去 `human_override` | `answered_questions` |
| `rescues` | `human_override`（从 `human_decisions` 里拆出来，单独记为人工救场） | 被打回的交付（`hive_return` 的 rejected） |
| `billed_tokens` | `pipeline_metrics.tokens`（已统一为全口径，`28903d316`） | `tokens` 四类之和 |
| `rework` | `stage_attempts` 中大于 1 的部分 | None（Hive 摘要里没有） |
| `escaped` | 交付后在纠正台账里被关联到这个 run（§2） | 同左 |

**聚合键**：Hive run 用 Brain × Project Agent mode（派发日志里的 `project_agent.mode`）；host run 用 Brain × profile。pipeline contract 里的 `mode` 是 worktree mode，不是 Project Agent 的 mode。

聚合后得到 E8 要的四条趋势：自主完成率、计划外介入、逃逸缺陷、单位 token 成本。

**② Golden tasks（offline 回归）**

- **放在哪**：现有 golden set 的私有集（`<workspace>/Eval/golden_set.private.yaml`，由 `core/golden_set_store.py` 读取）。新增 `level: delivery`，不另建存储。
  - 这个目录不会同步到 Hive，答案留在 host 上。
  - case 由人写，`s_golden-case` 负责校验，遵守"不自动生成 case"的规则。
- **一条 golden task**：
  ```yaml
  - id: GT-swarmai-01
    level: delivery
    brain: SwarmAI
    mode: bugfix                      # Project Agent 的 mode
    kind: product_judgment | code_fix # 两类都要有：DDD 管的是产品真相（v5 原则 1）
    task: "…真实任务原文…"
    acceptance: "…"
    repos: {swarmai: <base_sha>}      # 固定起点
    source: run_xxx | incident: …     # 来自哪个真实任务（E2）
    lesson_in_brain: true | false     # 这个任务的教训是否已经沉淀进 Brain（§3.3 分组用）
    guards: "它守的是什么、坏了说明什么"
    expect:                           # 只用确定性断言
      - validation: unit-tests        # Project Agent validations 的 id
      - pipeline_landing: landed      # 只看 Hive 上 pipeline 的结论，不在 host 落地
      - unplanned_max: 0
  ```
- **派发**：`dispatch_spec` 增加 `base_sha`。`repo_ship` 打包这个 commit 而不是 HEAD；Hive 在 session 开始前核对 repo 的 HEAD 等于 `base_sha`，不等就拒绝开始，由系统强制，不靠指示。同一 Hive 上同一 repo 的重放一个接一个跑。
- **判定（host 复核，确定性）**：Hive run 结束后，`hive_return` 已经会把 commit 取回到本地 ref（`_verify_and_fetch` :282-317）。复核分三步：
  1. 在临时 worktree 里检出这个 ref；
  2. 由 host 重跑 `expect` 里的 validations，按 exit code 判断；
  3. 再读 Hive 结果里的 `pipeline.landing` 和介入计数。

  **重放结果永远不落地**：`hive_return.land` 会 fast-forward 真实分支，重放流程里不调用它。judge 只用于 `product_judgment` 类的回答，且标明是判断，不进通过计数。
- **什么时候跑**（遵守 eval 规则：不在 coding pipeline 里跑）：发版后、每周一次、用户在面板上手动触发。job 先派发，等结果到达后再复核，不同步等待。
- **基线**：第一次完整重放的结果，加上真实 run 的历史记分卡。我们没有"纯人工模式"的数字，如实写进 E1 的证据。
- **回归**：每条跑 2 次，两次都过才算过；通过条数少于基线就进 `eval_followups`。
- **规模**：canon 是 20–50 条；先每个活跃 Brain 5–10 条，产品判断类和代码修复类都要有。

### 1.3 不做

- 不新建考试框架或 case 存储：沿用 golden set 和 Swarm Out。
- 不改现有离线考题：它考"读 DDD、做判断"，和 golden tasks 分工不同。
- 不靠 Hive 的 `checks` 判断过没过：它只说明 agent 敲过那条命令。

---

## 2. 纠正台账与重复纠正率（H4 / H5 / E7）

> **已撤销（2026-10-30，XG）**：本节"由人当场确认类别"的设计，以及它的实现（正则触发、BM25 候选、对话里的选择器、确认接口、CDF 待归类列表），已经全部删掉。错在两处：一是把"这次纠正是哪条已有教训的重犯"这种语义判断，交给了关键词和人工点选，而这正是带着完整上下文的 agent 最该做、也最擅长做的事；二是每次纠正都让人点一下，违反了 T3"人的注意力最贵"。重复纠正率改为由 agent 在复盘时做语义归类来计算，人只看结论，判断错了才去改，可信度靠定期抽样校准；具体做法并入 CDF 的整体梳理。下文保留，作为记录。

### 2.1 现有代码

- **纠正全部由模型认定。**
  - `correction` 只是模型给 cognition unit 打的一个 `type`（`core/ingestion/contracts_v2.py:39-40, 111-140`）。
  - 来源有四个：S2（`sources/s2_session.py:367-435`）、S7（`sources/s7_review.py:217, 258-259`）、S3 REFLECT（`run_protocol/reflect.py:28-29`）、S1。
  - 唯一的确定性信号是 `core/runtime_hooks.py:304-354` 的正则。它是 SDK 的 UserPromptSubmit hook，只能看到 prompt 文本和 sdk session id，拿不到 message id，也不向前端发事件；结果只写到 `state/corrections.jsonl`。
  - 其中 `_CORRECTION_PATTERNS_META` 一档按代码注释是"调整方向，不是纠错"（:326-331）。
- **"同一类"由模型判断。** `queries.recurrence()`（`core/ingestion/queries.py:361-424`）依赖 `judge.verify` 给出 `relation="same"`（`prompts.py:258-259`，`pipeline_v2.py:642-646`）。它唯一的读取方是 CDF 面板的"纠错复发"表（`routers/ingestion.py:106` → `desktop/src/services/ingestion.ts:84-129` → `CognitiveDistillationFlow.tsx:217-223`）。
- **另一处按类计数**：EVOLUTION 治理候选（GC）表的 `Count N/M`（`core/evolution/governance_miner.py`）同样由模型归类。
- **人做决定的地方，分不出"打回"和"选了一个选项"。**
  - Pipeline 决策卡的 `human_answer` 只有 `{request_id, option_id}`（`engine.py:1789-1814`）；
  - 一般的 AskUserQuestion 只留在 transcript 里；
  - Swarm Out 的 reject 没有原因（`hive_return.py:495-499`）。
- **纠正沉淀到哪里**：
  - 关于 agent 自己的纠正，写进 EVOLUTION.md 的 "Corrections Captured"（C 编号只是展示顺序，真正的标识是 `cand:<unit_id>`，`writer.py:429-432`）；
  - 关于外部世界的纠正写进 MEMORY；
  - 关于项目的纠正写进对应 DDD。
  - unit 的 evidence anchor 形如 `event:input:<uuid>`；它和消息的对应关系要经 `session_inputs.ref = messages.id` 才能连起来（`session_inputs.py:221-235`）。引用哪几个 event 是模型选的（`extract_llm.py:247-250`）。

### 2.2 设计：纠正台账

**一张表，一个写入方。** `ingestion.db` 新增 `correction_ledger`，只有 `ingestion/correction_ledger.py` 能写：

| 列 | 含义 |
|---|---|
| `id`、`at`、`session_id`、`message_id` | 纠正发生的时刻和那条用户消息 |
| `surface` | `chat` / `pipeline_override` / `swarm_out_reject` / `brain_withdraw` / `escalation` |
| `brain`、`run_id` | 所属 Brain、被纠正的是哪个 run 的产出（有则填） |
| `class_key` | 人选定的类别：已有条目的 anchor、golden task id、以往台账 id，或 `new` |
| `confirmed_by` | `human` / `unconfirmed` |

**触发点（只在这些时刻出现选择器）：**
- **对话发送路径**（`routers/chat.py` 的发送处理里，这时 message id 已知）：用户消息命中 `runtime_hooks` 中**纠错一档**的正则（`_CORRECTION_PATTERNS_EN`），不含 META 档。
- **显式动作**：pipeline 的 `human_override`（人工救场）、Swarm Out 交付被 reject、在 Brain 页面撤回条目（brain-change-feed 设计里用户唯一的动作），以及 escalation 被驳回。
- **没有 UI 的会话**（channel、Hive）：写成 `unconfirmed` 行，下次打开 Brain 页面时补确认。

**候选（确定性检索）：** 用 `memory_index._bm25_scores` 拿这条纠正的原文和被纠正的那段回复，在下面几处里检索，取前 3 条：
- 当前 Brain 的纠正和 pitfall 类条目；
- EVOLUTION "Corrections Captured"；
- MEMORY 的纠正；
- delivery 级 golden tasks；
- 最近 30 天的台账行。

**呈现：** 在那条消息下面放一个选择器："这是在纠正吗？归到：[候选 A] [候选 B] [候选 C] [新的一类] [不是纠正]"。点一下就写入台账。

**是否沉淀（判"情况 ②"用）：** 不另存字段，读取时现算。台账行对应的 `message_id`，经 `session_inputs` 映射到 `event:input:<uuid>`；只要有一个已写入的 unit 引用了这个 event，就算已沉淀。模型漏引时会被算成"未沉淀"，这是偏保守的方向。

**类别键跟随覆盖：** v5 下条目会被覆盖或撤回。计算时，`class_key` 顺着 `brain_changes` 和 `redactions` 走到当前条目。目标已被撤回的，按"以往出现过"计。

**重复纠正率（确定性）：**
```
repeat = class_key 指向已有沉淀条目 / golden task（情况 ①）
       或 指向以往台账行（情况 ②）
rate   = repeat 行数 / confirmed 行数     （滚动 4 周，可按 brain 分）
```
同时输出三个防作弊数字：
- `unconfirmed / 全部`；
- 漏记：S2/S7 抽到纠正，但那条消息没有台账行；
- 逃逸缺陷：护栏。

**已知偏差：** 候选正好匹配时，人更容易去点选择器，所以 `repeat / confirmed` 会偏高。好在这个偏差让门槛变严，不会让人钻空子。覆盖率和这个比例一起展示。

**读取方（先于写入方设计）：**
- `queries.correction_rate(window, brain=None)` 是唯一的计算函数。
- 它的读取方：
  - `GET /api/ingestion/health`：替换"纠错复发"表；
  - `eval_followups`：超过门槛时提示；
  - Brain 详情页：每个 Brain 一行；
  - 后续可以让 GC 表的计数改读台账。本文不做。

### 2.2a 落地记录（2026-10-30，步骤 1）

和上文设计相比，落地时改了下面几处，以代码为准：

- **入口**：
  - 已接入：对话、Swarm Out 交付被打回、escalation 选了 Swarm 推荐之外的选项。
  - `human_override` 不是纠正，是人工救场，归记分卡统计（§1.2）。
  - Brain 撤回等 v5 的撤回逻辑合并后再接。
- **一次归类**：已经归过类的行不能再改，接口返回 409。这样人的选择不会被覆盖，被别的纠正引用的类别也不会变。
- **只有人能归类**：除了 loopback 和 `X-Swarm-User-Action` 标记，`core.run_owner_gate` 还会拒绝 agent 通过 Bash 调用确认接口，做法和撤回接口一样。
- **对话里的那一行**：只挂在 10 分钟内的消息下面，只占一行。更早还在等归类的纠正，放在 CDF 面板的列表里。
- **"是否已沉淀"和漏记**：§2.2 写的"按 `session_inputs` 映射到 event"和漏记数还没做。现在的比例只区分两种重复：指向已有条目或 golden task，或者指向以往的纠正。

### 2.3 删除与不做

- **删除**：`queries.recurrence()`，以及它在 router、`ingestion.ts`、`CognitiveDistillationFlow.tsx` 里的读取方和测试，四处一起删。"同一类"不该有两种说法。
- **保留**：O3_CORRECTIONS（多少会话里出现了纠正），它回答的是另一个问题。
- **不做**：不让模型替人确认类别；不对每条消息都弹选择器。

---

## 3. 知识健康与撤掉知识对照（K4 / E6）

### 3.1 现有代码

- **`ddd_health` 已经失去输入，展示的是冻结数据。**
  - D5 是常数（`core/ddd_health.py:326-327`）。
  - D1 和 D3 读 `.artifacts/ddd-changelog.jsonl`，它的写入方要么已删（982c32190），要么是死代码：`_log_maturity_promotion` 只被 `_update_maturity`（`hooks/context_health_hook.py:673, 730`）调用，而 `_update_maturity` 没有调用方。
  - 唯一持久化结果的 `ddd_refresh` 调用在 dcd95e518 移除，`section_health.json` 停在 10-26。
  - 读取方有三处：
    - Brain Hub 的 trust 和 diagnostics（`routers/ddd_brain.py:726-756`）；
    - 设置页的 Engine Metrics（`engine_metrics._collect_ddd_health` :382-403 → `EngineMetricsTab.tsx`，每次 GET 现算五维）；
    - Brain 卡片的 `recentActivity`（`ddd_brain.py:631-660`），读的是同一份已死的 changelog。
- **v5（`2026-10-30-brain-driven.md`，待确认）已经定了知识健康要用的东西：**
  - 只有三种改动：新增、覆盖、撤回（§0.3）。
  - 不做事后的周期性复判（§0.5）。
  - `brain_changes(project, since)` 统计新增、覆盖、撤回和未决冲突（§2.4，另一个 session 的分支已有实现，未合并）。
  - 删除 `ref:`、`decay:`、`maturity:` 字段（§2.5）。
  - 月度错误倒查只量"完整"和"最新"（§2.8）。
- **没有撤掉知识的手段。**
  - 代码里没有任何开关。
  - `has_ddd` 只管 DDD 这一路，Library 和 lessons 两路照常运行（`run_protocol/run_knowledge.py:18, 239-245`）。
  - 任务说明里固定有一行，让 agent 跑 `knowledge find --project`（`task_spec.py:227-251`）。
  - General Brain 会自动同步到 Hive（`brain_sync_host.py:213-238`）。

### 3.2 设计：知识健康 = v5 的统计，加读取覆盖

| 指标（Tenets ③b） | 计算 | 数据源 |
|---|---|---|
| 未决冲突数（目标 0） | judge 判为 `contradicts`、但没有声明覆盖的条目对 | `brain_changes` |
| 新增 / 覆盖 / 撤回 每周数 | 计数 | `brain_changes` |
| 入库后被推翻率 | 写入后被覆盖或撤回 / 写入 | `brain_changes`。`ingestion_writes` 30 天后会被清理，所以每周持久化一份快照 |
| 读取覆盖（替代"死条目"） | 30 天内被注入、读取或引用过的条目占比，**只度量，不触发任何动作** | `unit_usage`。局限：只统计带 `cand:` 的条目，Hive 上的读取看不到 |
| 覆盖缺口 | 未关闭的 gap 数 | `2026-10-30-cdf-gap-lifecycle.md` |
| 知识效果（③a） | 月度错误倒查的"完整""最新" | v5 §2.8 |

**删除**以下内容，替代它们的是上表在 Brain 详情页的一行：
- `ddd_health.py` 的五维打分和 `section_health.json`；
- 它的三处读取方：Brain Hub 的 trust/diagnostics、Engine Metrics 里的 DDD health、`recentActivity`。

删除的理由：输入已经没有写入方，留着就是一直在给人看 10-26 的旧结论。

**同步修订 Tenets ③b：**
- "过期条目占比"和 v5 原则 5 冲突，改为"未决冲突数"和"入库后被推翻率"；
- "死条目"改为"读取覆盖"，并注明只度量。

### 3.3 设计：撤掉知识对照（E6）

撤掉知识做成派发的一个变体，开关放在系统里，agent 碰不到：

- **独立的包**：withheld 变体只装 `project-agent.yaml`、`bindings.yaml`、`3-gates/`、各 mode 用到的 `4-capabilities/` skills，不装 `2-understanding/`。
  - revision 只覆盖定义文件、用到的 skills 和 gates（`project_agent.py:455-467`），所以 withheld 包的 revision 和正常包一致，Hive 不会拒绝。
  - 安装到 Hive 上的**独立槽位**（`<brain>~withheld`），不覆盖 Hive 上持久的正常副本，也就不会和正常派发互相争用。
- **关掉其余知识通道**：任务说明带 `knowledge: withheld` 时，`run_knowledge` 的 DDD、Library、lessons 三路都返回空；`task_spec.segment` 不再写 `knowledge find` 那一行；不同步 General Brain。
- **明确不撤**：repo 自己的 AGENTS.md / CLAUDE.md 是代码的一部分，保留。报告里写明这一点。
- **两组对照**：同一组 golden tasks，有知识一组、撤掉知识一组。
  - 按 `lesson_in_brain` 分开报告：
    - 教训已沉淀的任务，测的是"沉淀能不能防止复发"；
    - 其余任务，测的是"知识对新问题有没有用"。
    - 两者不混在一个数里。
  - 只报计数：通过条数、计划外介入、单位 token 成本。不报显著性。
- **频率**：每月一次，成本是重放的两倍。

### 3.4 依赖

- §3.2 要等 v5 的 `brain_changes` 合并以后再做。
- §3.3 依赖 §1 的 `base_sha` 和 host 复核。

---

## 4. 不变量（最终测试集，每条都做 mutation check）

| # | 不变量 | 测试做法 |
|---|---|---|
| I1 | 记分卡只从已有记录读取，缺数据就是 None；`human_override` 计入救场，不计入计划外 | 构造一个带 override 的 run，断言 `unplanned` 不含它，`rescues=1` |
| I2 | golden task 的判定只来自 host 复核的 exit code 和 Hive 的 pipeline 结论；judge 不进通过计数 | judge 判 FAIL、断言全过时，计为通过；Hive `checks=ran` 但 host 复核 exit 1 时，计为失败 |
| I3 | 重放永远不落地 | 重放流程中 `hive_return.land` 的调用次数为 0 |
| I4 | Hive 在 HEAD ≠ `base_sha` 时拒绝开始 | 构造不一致的 repo，断言 session 没有启动 |
| I5 | 台账只有一个写入方；`class_key` 只能由人写入 | 模型抽到纠正时，台账行为 `unconfirmed`，不进比例 |
| I6 | 两种重复情况都能计到，并且能顺着覆盖链走 | 指向已覆盖条目的 key，按当前条目计为 repeat；指向以往未沉淀行，也计为 repeat |
| I7 | 漏记可见 | S7 抽到纠正、台账没有对应行时，漏记数 +1 |
| I8 | withheld 时，Hive 上读不到这个 Brain 的知识：DDD、Library、lessons 三路都为空，任务说明里没有 `knowledge find` 那一行，正常槽位不受影响 | 派发 withheld 后断言三路为空、正常槽位的 digest 不变 |
| I9 | Brain Hub 和 Engine Metrics 不再展示冻结的 health 数据 | 接口里没有 `trust`、`diagnostics` 和 DDD health 五维 |

---

## 5. 步骤与验证计划

| 步 | 内容 | 依赖 | 评审 |
|---|---|---|---|
| 1 | 纠正台账：表结构、发送路径触发、选择器、显式动作入口、`correction_rate` 和读取方；删除 `recurrence()` | 无 | correctness + ux（选择器打扰度） |
| 2 | Run 记分卡：拆分 `human_override`、两类 run 的字段映射、聚合与趋势 | 无 | correctness |
| 3 | Golden tasks：`level: delivery`、`base_sha` 派发与 Hive 校验、host 复核、不落地 | 步骤 2 | correctness + security（host 在临时 worktree 里跑 Hive 带回的代码）+ operational（重放成本、串行） |
| 4 | 知识健康：在 `brain_changes` 之上计算；删除 `ddd_health` 及三处读取方 | v5 合并 | correctness + integration（和 v5 分支的接缝） |
| 5 | 撤掉知识对照：withheld 包、独立槽位、关掉其余知识通道、分组报告 | 步骤 3 | correctness + integration |
| 6 | 修订 Tenets ③b 的两个指标 | 步骤 4 | 无（文档） |

每一步：先写对应不变量的失败测试，再实现，然后做 mutation check。§4 是收尾时一次跑完的最终测试集。

**需要 XG 决定：**
1. 是否同意删除 `ddd_health` 及其三处读取方（§3.2）。
2. 纠正选择器只在正则命中和显式动作时出现（§2.2），打扰度是否可以接受。
3. golden tasks 起步每个 Brain 5–10 条（§1.2）。
4. 步骤 3 会让 host 在临时 worktree 里执行 Hive 带回的代码（跑 validations），这是一个新的信任边界。这样做是否可以；或者只读 Hive 的 pipeline 结论，不在 host 上跑。

---

## 6. r1 → r2 修订（零上下文前提审查的结果）

| 问题 | r1 | r2 |
|---|---|---|
| golden tasks 放在 `3-gates/` 会让 revision 失效，而且答案会泄露给被测 agent | `3-gates/golden-tasks.yaml` | 放进 golden set 私有集 `level: delivery`，不同步到 Hive |
| 派发不能指定起点；`checks` 无法证明测试通过 | 给 checks 补 exit code | 新增 `base_sha` 并由 Hive 校验；host 在临时 worktree 里重跑 validations；重放永不落地 |
| 只撤 `2-understanding` 会破坏 revision，而且知识还会从其他通道漏进来 | 只装 `3-gates` 等 | 独立包和独立槽位；关掉 Library、lessons 和 `knowledge find` 那一行；按 `lesson_in_brain` 分组 |
| 计划内和计划外介入 | 要补 `kind` | 已经能区分：`required_approvals` 与 `human_decisions` 不重叠；`human_override` 拆为救场 |
| Hive 的"完成"不等于交付被收下 | `ended=completed` | 用 Hive `pipeline.landing`，或 host 复核 |
| 正则 hook 拿不到 message id，也没有 UI | 复用 hook | 在发送路径里触发，只用纠错一档；没有 UI 的会话写为 unconfirmed |
| 沉淀关联 | 按 message_id 回填 | 经 `session_inputs` 映射到 `event:input`，读取时现算；类别键顺着覆盖链走 |
| 候选池遗漏 | DDD 条目 | 加上 EVOLUTION Corrections 和 MEMORY 纠正 |
| 删除范围不完整 | Brain Hub trust | 再加 Engine Metrics 和 `recentActivity` |
| 打回入口依赖已被删的 git review | `ddd_review_reject` | 改为 Brain 页面撤回 |
