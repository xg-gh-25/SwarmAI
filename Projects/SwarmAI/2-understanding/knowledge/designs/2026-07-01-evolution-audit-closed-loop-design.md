---
title: "EVOLUTION AUDIT 闭环补强 — 从『缺席信号』到『正向测量』"
date: 2026-07-01
project: SwarmAI
run: run_7e40bfa3
profile: docs
status: design (no production code)
tags: [evolution, audit, golden-case, effectiveness, escalation-ladder]
external_anchor: AutoSDE generate-rules (quality-ratio 反馈回流)
---

# EVOLUTION AUDIT 闭环补强

## 0. 一句话

EVOLUTION 的 MINE→ASSESS→ACT 已经比外部对标(AutoSDE generate-rules)更成熟;唯一真差距在
**AUDIT** —— 我们判断「一条治理规则有没有用」只有一个**二值缺席信号**(`correction_tracker`
的 30 天无复发 → auto-resolve),而 AutoSDE 有**正向测量**(quality-ratio)。本设计把 AUDIT
升级为正向测量,用**已有的** golden-case + eval 子系统做「投票人」,不引入任何新机制。

**关键前提在 EVALUATE 被现场证伪**(见 §2),它把 scope 从「建 gate rung」改写成「修一行
stale 注释 + 接一根 mint 线」。

---

## 1. 背景 — 我们 vs AutoSDE(逐维度,结论:只差 AUDIT)

| 维度 | AutoSDE generate-rules | 我们 EVOLUTION | 谁强 |
|---|---|---|---|
| 语料源 | CR 人类评论 | `session_miner`(transcript user-correction)+ `governance_miner`(EVOLUTION.md CLASS) | 平 |
| 降噪 | 丢 bot 评论 | `_is_sdk_noise` + `_is_operational_noise`(traceback 判别) | **我们** |
| 聚类 | Claude 找 ≥5 复现 | threshold 3 + CLASS_A/B/C axis 归类 | **我们** |
| 生成 | AUTOSDE.yaml + 递归去重 | governance proposal + `_has_existing_rule` 去重 | 平 |
| 分层 | package vs team-wide | `escalation_ladder` none→rule→gate 的 **KIND 阶梯** | **我们** |
| 晋升门 | `times_generated≥2 AND confidence≥6` | threshold 3 + 0.6 confidence floor + 人类 veto | 平 |
| **AUDIT/反馈** | **F1👍/F2👌/F3👎 → quality=(F1+F2)/total** 正向测量 | **仅 30d-silence→auto-resolve** 缺席信号;gate rung 从未被触发 | **AutoSDE** ← 本设计要补的 |

**为什么不能照抄 AutoSDE 的机制**(照抄 = C042「造错机制」):它 review 的是**别人的 CR**,
人类投票免费来;灰度/A-B 对**单用户单 binary** 无「% of users」可分。我们没有外部投票人 ——
所以偷的是它的**原则(正向测量 effectiveness)**,不是它的**机制(人类三档投票 + 灰度)**。

---

## 2. 前提证伪 — EVALUATE 现场观测(AC1)

需求 step-1 假设:「gate rung 是 Phase-3 dead code,需要建;先查有没有 rule 被 accepted 过」。
**现场 LIVE 观测把它证伪**:

**证据 A — 没有任何 rule/gate 被 accepted 过**
`~/.swarm-ai/state/correction_tracker.json`(实时读取):
```
CLASS_A:     count=7    active_gate=None  (active_rule key 不存在 → .get()=None)  resolved=False
CLASS_B:     count=13   active_gate=None  (active_rule key 不存在 → .get()=None)  resolved=False
CLASS_C:     count=1    active_gate=None  active_rule=null  resolved=False
OPERATIONAL: count=230  active_gate=None  active_rule=null  resolved=False
```
CLASS_A(7)、CLASS_B(13) 都远超 threshold=3,却无 `active_rule`(A/B 连该 key 都没有 —— 早于
该字段;C/OPERATIONAL 显式 null;`.get('active_rule')` 一律 None,逻辑不受影响)。前提「有
rule 被 accepted 过」→ **FALSIFIED**。(adversarial 校正:A/B 的 active_rule 是 key-absent
而非显式 null,别的 count/active_gate/resolved 值均核实无误。)

**证据 B — gate rung 不是 dead code,是实现好且可达的**
`escalation_ladder.py:123-138` 已完整实现 gate-rung(`active_rule AND post_rule_count ≥
_RED_THRESHOLD(2) → kind="gate"`),且 body 注释自己写着 "GATE RUNG (Phase 3, **now
reachable**)"。**它的文件头注释 L12-16 却仍写着「dead code / its caller does not exist
yet」—— 这是 STALE 注释,与自己的 body 矛盾**(R7 legibility-decay / 「code is truth,
comments are hypotheses」)。

**证据 C — accept path 存在,但它读的是「另一个队列」(adversarial 校正的关键发现)**

⚠️ **两个队列不是同一个**(这是本设计最初的事实错误,Gate-2 抓到):

| 队列 | 文件 | 内容 | 谁读它 |
|---|---|---|---|
| **confirm 队列** | `~/.swarm-ai/state/governance_pending.json` | **26 条 pending_confirm 的认知纠正**(schema:`{axis,class_name,parent_principle,confidence,status}`,**无 `target`/`proposal_kind`**) | `governance_router` park;等 `escalate_class` 消费 |
| **accept 队列** | `~/.swarm-ai/SwarmWS/.context/.evolution_proposals.json`(`_proposals_path` :1284;`_default_proposals_path` governance_router:327) | `target=='governance'` 的 rule/gate 提案(:1287 filter) | `get_pending_governance` :1313 / `decide_governance` :1318 / `EvalDashboard.tsx:722` |

**LIVE 观测校正(二次核实 —— 别信推断,读磁盘)**:`.evolution_proposals.json` **确实存在**
(6267 bytes),含 **7 条提案,其中 4 条 `target==governance` 的 rule 提案已就绪待 accept**:
CLASS_A(7x)、CLASS_B(13x)、CLASS_C、OPERATIONAL。**所以 accept 队列不空 —— XG 现在就能
在 EvalDashboard accept 这 4 条之一。** 但**0 条被 accept 过**(`active_rule` 全 None,§2-A)。

> 两处校正叠加:(a) 我最初「accept 那 26 条」**错**(26 条在 confirm 队列,不是 accept 队列);
> (b) adversarial 说「accept 队列空/不存在」**也错**(它有 4 条 governance rule 待接)。真相 =
> **两个队列都非空,accept 队列有 4 条就绪,0 条被接受**。`escalate_class` 已经在把提案落到
> accept 队列(governance_miner:244/263 `target="governance"` + governance_router:330
> `_append_proposal`)—— 所以 Run 0a 接线**大概率不需要**,直接 Run 0b(XG accept)即可。

- accept 分支代码完整:`eval_service.decide_governance()` (:1318) → 按 `proposal_kind` 调
  `register_gate`(:1349)/`register_rule`(:1352);API `routers/eval.py:476/487`;前端
  `EvalDashboard.tsx:722`。**代码在,accept 队列有货,只差人点 accept。**

**结论(改写 scope)**:真正的瓶颈**不是缺代码、也不是缺提案**,而是
1. **多处 STALE 注释**(3 处 —— 见 §4.2)误导「gate rung 是死的」;
2. **从没有人 accept 过任何 rule 提案**:4 条 governance rule 提案在 accept 队列待命,但 0 条被
   点 accept → `active_rule` 永不置位 → gate rung 端到端永不被触发;
3. **RULE 根本没有任何 effectiveness 信号**(§3 校正:30d-silence auto-resolve 是 **gate-only**
   且从未触发过)。

所以 step-1「建 gate rung」退化为「**修注释**」;真正要做的工程是 step-2(给 RULE 造正向 AUDIT
信号)+ 一个前置的运营动作(XG 在 dashboard accept 至少 1 条已就绪的 rule 提案,见 §7 Run0)。

---

## 3. 现状 AUDIT 的病:缺席信号的三重混淆

`correction_tracker.check_auto_resolve()` (:388) 逻辑:gate 部署满 `_RESOLVE_DAYS=30`
天无复发 → `resolved=True`。**adversarial 校正:这个信号是 GATE-ONLY 的**——:398 跳过任何
`active_gate` 为空的 class,:400 跳过 `post_gate_count>0` 的。而 live state 里**从来没有任何
class 有过 gate**(全 `active_gate=None`)→ **`check_auto_resolve` 从未触发过,且对 RULE
结构上不可能触发**。

所以准确的病情是:**RULE 层根本没有任何 effectiveness 信号**(连缺席信号都没有 —— 缺席信号只
覆盖 gate)。就算 gate 层的 30d-silence 也混淆三件事:
- (a) gate **真的挡住了**模式;
- (b) 那个情境这 30 天**压根没出现**;
- (c) 我**不再 mine** 那个 class 了。

silence 无法区分「有效」与「没发生」。AutoSDE 的 quality-ratio 是**正向**的 —— 它测「规则
命中时,人类认可的比例」,不吃这个歧义。我们要的正向信号 = **「该 class 若复发,是否被一个
会变 RED 的 golden case 抓到」的 pass-rate over time**,而且它要**同时覆盖 rule 和 gate**
(填上 rule 现在完全没有信号的空洞)。

---

## 4. 设计(方案 B — SIMPLICITY,推荐)

### 4.1 核心 wire:promote → auto-mint golden case → pass-rate = effectiveness

在**已有的 accept 点**上挂一根线,把「一条 rule/gate 被接受」变成「一个会考它的 golden
case 诞生」,该 case 的历史 pass-rate 就是这条规则的 effectiveness。

```
人类在 EvalDashboard accept 一条 proposal
   │  (routers/eval.py:487 → eval_service.decide_governance:1318)
   ▼
decide_governance 的 accept 分支 (eval_service.py:1341-1353):
   ├─ 调 register_rule/register_gate (correction_tracker:339/367)  ← active_rule 置位
   └──[新增 mint 线,挂在 decide_governance 本体内,紧跟 register_* 之后]──▶
        self.add_case({                                            ← 同类内 :300,无循环依赖
           "id": "EVOLUTION_EFFECT_<CLASS_X>",                     ← 必填,确定性生成
           "category": "governance-effectiveness",                ← 必填
           "dimension": "evolution",                              ← 必填
           "affected_by": ["EVOLUTION.CLASS_X"],                   ← 必填,已验证可 resolve
           "evaluators": [<见 §4.3:检测该 class 复发的 evaluator>], ← 必填,最难的一环
           "negative_command": <gate-eligible 时必填,证明有牙>
        })
        └─ mint 前 MUST 过 C044 knockout(见 §4.3),不能 RED → abort
   ▼
此后每次 scheduled eval → EvalHistory/*.json 记录该 case pass/fail   ← :58,153 已有
   │
   ▼
effectiveness(rule_X) = 该 minted case 在最近 N 次 run 的 pass-rate
   └─ 供 growth_report / 新的 rule-scoped auto-resolve 用「正向达标」代替「无信号」
```

**⚠️ 层级铁律(adversarial 抓到的接线错误)**:mint 线**必须挂在 `eval_service.decide_governance`
的 accept 分支内(:1341-1353,紧跟 `register_*` 之后)**,**绝不能挂进
`correction_tracker.register_rule/gate`**。原因:依赖方向是 `eval_service → correction_tracker`
(单向);`correction_tracker` 刻意零依赖(:8 "no DB/LLM/network")。若把 `add_case` 塞进
`register_rule`,`correction_tracker` 就得 import `eval_service` → **循环依赖 + 破坏纯度契约**。
`decide_governance` 本身已 import tracker 且与 `add_case` 同类,是唯一正确接入点。

**接入点(已验证符号;⚠️ add_case 契约见下,不是「有 add_case 就行」):**

| 作用 | 现有符号 | 位置 |
|---|---|---|
| mint 触发点 | `decide_governance` accept 分支,`register_*` 之后 | `eval_service.py:1341-1353` |
| 程序化写 case | `EvalService.add_case()` —— **要求 5 必填字段** `{id,category,dimension,evaluators,affected_by}`(:298),缺一即 `raise ValueError`(:314-316) | `eval_service.py:300`(持久化 `_write_partition:1132`) |
| class↔case 链 | `affected_by:["EVOLUTION.CLASS_A"]`;**resolver 在 :201-207**(要求 `.context/EVOLUTION.md` 有 `### CLASS A:` 头,已验证 A/B/C 均 resolve) | `golden_case_validator.py:201-207`(:173-174 只是示例注释) |
| effectiveness 读点 | `EvalHistory/*.json` 逐 run 结果 | `eval_service.py:58,153` |

**add_case 契约(adversarial 校正,最初 payload 会直接 raise)**:mint 必须合成全部 5 个必填
字段 —— `id`(确定性,如 `EVOLUTION_EFFECT_CLASS_A`)、`category`、`dimension`、`affected_by`
(已验证可 resolve)、以及**最难的 `evaluators`**(一个真能检测「该 class 失败特征复发」的
evaluator spec)。最初 payload 用的 `assertion`/`eval_method` **不是 schema 字段**,会被拒。
`evaluators` 怎么造是整个 effectiveness 想法的核心难点 —— 在 §4.3 展开。

### 4.2 修 stale 注释(不止一处 —— adversarial 校正:共 3 处)

最初只指认了文件头 `L12-16`,Gate-2 找到另外两处也已 STALE(body 会返回 `kind="gate"`,
而这些注释仍说不会):

| 位置 | 现状(STALE) | 应改为 |
|---|---|---|
| 文件头 `L12-22` | 「gate rung 需 Phase-3 dashboard 接线,现在建=dead code」 | gate rung body 已实现;在纯函数层可达(active_rule+post_rule≥RED);**端到端**尚不可达是因为 confirm→proposal 链没接通(§2-C),不是缺代码 |
| `EscalationDecision` docstring `L64-72` | 「kind 只有 none→rule;(Phase 3 will add gate and alert)」 | body 已能返回 `kind='gate'`,删掉「Phase 3 will add」 |
| `decide_escalation` 返回 docstring `L84-86` | 「kind 恒为 Phase-2 可达值(none\|rule);never raises」 | **字面已假** —— 函数现在可返回 `kind='gate'` |

**关键措辞校正**:不要说「gate rung 已可达」这种会误导的话。准确表述 =「gate rung 在**纯函数
层**可达;**端到端当前不可达**,因为没有任何 class 被置 `active_rule`(见 §2-C 队列问题)」。
**三处都只改注释/docstring,不动 14-caller 的函数体**(R25 / STEERING:绝不为零行为收益给高
扇入模块引入风险)。

### 4.3 evaluators 怎么造 + mint 必须过 C044 knockout(防 golden-set 污染)

**难点(adversarial 点名的 crux)**:`evaluators` 必须真能检测「该 class 的失败特征复发」,
而不是泛泛判分。一个 governance-effectiveness case 的 evaluator 候选形态:
- **trajectory 型**:给 agent 一个会诱发该 class 失败的 prompt,判轨迹里**有没有出现被该
  rule 禁止的行为**(如 CLASS_A = 是否跳过了某 gate)。这是「有牙」的正解,但造 prompt 需
  人工 seed(接 `s_golden-case` 的 ADD 流程,不是全自动)。
- **file_contains 型**:退化情形 —— 判某个 gate 的强制文本/hook 是否仍在位。更弱,只测「防线
  还在」不测「防线有效」。

**诚实边界**:`evaluators` 无法 100% 自动合成 —— 高质量的是 trajectory 型,需要人工 seed 一个
诱发-prompt。所以「auto-mint」实际是 **auto-mint 一个 case 骨架 + 标记 `needs_teeth`,由
`s_golden-case` ADD 流程补 evaluator**;骨架若补不出有牙 evaluator → 不 mint。这比最初「全自动
mint」的说法弱,但诚实(否则就是造无牙废案,即 C044 本体)。

**C044 knockout 前置**:C044 eval policy —— 一个 case 只有过 5 轴才配存在(refs resolve / 反映
当前真相 / **有牙:知识缺失即 RED** / 真执行 / 值得存在)。mint 前置一道 knockout 探针:构造
「该 class 知识被敲掉」的输入,confirm case 会 RED;不能 RED → **abort mint**(宁可不 mint,
也不造无牙 case)。接 PRI01「alive ≠ correct 必须是 eval CONTRACT」。

### 4.4 前置动作:接通 confirm→proposal→accept 链(adversarial 重写)

**LIVE 观测后定稿**:accept 队列 `.evolution_proposals.json` **已有 4 条就绪的 governance rule
提案**(CLASS_A/B/C/OPERATIONAL),`escalate_class` 已在正常把提案落到 accept 队列
(governance_miner:244/263 + governance_router:330 `_append_proposal`)。所以**接线大概率不需要**
—— 前置退化为纯运营:

- **Run 0(运营,非代码)**:XG 在 EvalDashboard accept 那 4 条之一(如 CLASS_A)→ `active_rule`
  置位。这解锁两件事:gate-rung 端到端可达(§4.2)+ mint 线有真实触发点(§4.1)。
- 唯一残留的诊断问题:为什么 confirm 队列 26 条 与 accept 队列 4 条数量不匹配?(confirm→escalate
  的转化率/过滤)—— 这不阻塞 Run 0,是 Run 2 之后可选的观察项,不在本设计 scope。

mint 线(Run 2)依赖 Run 0 产出至少 1 条 accepted rule(`active_rule` 置位)才能端到端验证。

---

## 5. Boundaries

### Always(自动强制)
- 修 escalation_ladder 只动**注释**,函数体 byte 不变(R25 + 14-caller MEDIUM 风险)。
- 每次 auto-mint 前跑 C044 knockout,不能 RED 就 abort(PRI01/C044)。
- 所有接入用**已有** seam(add_case / affected_by / EvalHistory),不新建子系统。

### Ask First(暂停确认)
- 是否把 auto-resolve 的判据从「30d silence」**扩展**到「minted-case pass-rate 达标」并
  **新增 rule-scoped 分支**(现在 check_auto_resolve 是 gate-only,rule 根本不 resolve)——
  这改动 `correction_tracker.check_auto_resolve` 语义,属 QUALITY 方案,需 XG 拍。
- mint 出的 case **默认且只能 private**(adversarial 校正):governance-effectiveness case
  天然 instance-specific(引用 EVOLUTION classes / .context 治理),PROMOTE 时会被隐私门
  (`golden_case_validator.py:150-157 _INSTANCE`)拒。所以「进正式 public golden set」实际
  被隐私门堵死 —— 不是一个真选项,保持 private。

### Never(硬禁止)
- ❌ 灰度 rollout(10%→30%→50%)—— 单用户单 binary 无「% users」作用域。
- ❌ A/B lab-id —— 同上。
- ❌ 人类三档投票(F1/F2/F3)—— 无外部投票人;golden case 是我们的「投票人」。
- ❌ 为「让 case 变绿」去修表面(C044 本体);mint 的 case 必须有牙或不 mint。
- ❌ 重写 escalation_ladder / correction_tracker 的工作代码换零行为收益(R25)。

> §Never 前三条显式写死,防止未来 run 因看到 AutoSDE 而重新引入(C042 防线)。

---

## 6. Success Criteria

- SC1: 设计文档存在且含 §2 现场证伪证据(带 file:line 与 live state),reviewer 一眼能核。
- SC2: 文档明确「修 stale 注释 ≠ 重写代码」,给出 escalation_ladder.py:12-16 精确行号。
- SC3: §4.1 的 mint wire 四个接入点全部指向**已存在**的符号(可 grep 命中),非杜撰。
- SC4: §5 Never 显式排除灰度/A-B/人类投票 + 理由。
- SC5: §7 用 pipeline RUN 分解,标出 Run 0(运营前置)是 Run 2 的硬依赖。

## 7. 工作分解(按 pipeline RUN,非步骤/工时)

| Run | Profile | 内容 | 依赖 | 独立可验证 |
|---|---|---|---|---|
| **Run 0(运营,非 pipeline)** | — | accept 队列已有 4 条就绪 governance rule 提案(LIVE 核实);XG 在 EvalDashboard accept 其一 → `active_rule` 置位。接线大概率不需要(escalate_class 已在写 accept 队列) | 无 | `correction_tracker.json` 出现 `active_rule != None` |
| **Run 1** | trivial | 修 `escalation_ladder.py:12-16` STALE 注释(只改注释)+ 加一个断言「gate-rung 在 active_rule+post_rule≥RED 时返回 kind=gate」的测试 | 无 | 注释与 body 一致;mutation:改回死注释描述→测试仍绿(注释无行为)故用**代码断言 gate-rung 可达** |
| **Run 2** | full | promote→auto-mint golden case 的 wire(挂在 register_rule/register_gate accept 分支)+ C044 knockout 前置 + effectiveness 读点(pass-rate from EvalHistory) | Run 0(需真有 accepted rule 才能端到端验证 mint) | 一条 accepted rule → EvalHistory 出现对应 minted case 的 pass/fail;knockout abort 路径有测试 |
| **Run 3(可选)** | bugfix | 把 `check_auto_resolve` 的 30d-silence 判据加一路「minted-case pass-rate 达标才 resolve」(Ask-First,需 §5 确认) | Run 2 | auto-resolve 不再纯靠沉默 |

**Run 1 是纯注释**,严格说无行为改动 —— 但按 R1「无 code change without pipeline」,它仍走
trivial pipeline + adversarial(且 GC:注释改动的「测试」应断言 body 行为可达,而非断言注释
文本,避免 test-theater)。

---

## 8. Test Strategy(针对本设计文档的下游实现 run,非本 docs run)

| # | AC/SC | 如何测 | Mock 边界 | 输入构造 |
|---|---|---|---|---|
| 1 | Run1 gate-rung 可达 | 单测 `decide_escalation` 传 `{count:5,active_rule:"RULE_X",post_rule_count:2}` → 断言 `kind=="gate"` | 无(纯函数) | 内联 class_state dict |
| 2 | Run2 mint 触发 | 集成:调 `decide_governance(pid,"accept")` → 断言 `add_case` 被调且 `affected_by` 含 `EVOLUTION.CLASS_X` | mock `add_case` 或用临时 golden set | fixture proposal(kind=rule) |
| 3 | Run2 knockout abort | 单测:喂一个「敲掉知识仍绿」的 case 候选 → 断言 mint abort、golden set 未增 | mock eval judge 返回 pass | 无牙 case 候选 |
| 4 | Run2 effectiveness 读 | 单测:构造 2 条 EvalHistory run(1 pass 1 fail)→ 断言 `effectiveness==0.5` | 用临时 EvalHistory 目录 | 2 个 fake run json |

## 9. Edge cases / 风险(接 EVALUATE pre_mortem)

- **accept-path 仍饿死**(pre_mortem #1):mint 线接好但 Run 0 没做 → active_rule 永不置位 →
  环空转。缓解:Run 0 标为硬前置,Run 2 的端到端验收依赖它。
- **mint 灌垃圾 case**(pre_mortem #2):无脑 mint 违反 C044。缓解:§4.3 knockout 前置,
  不能 RED 就 abort。
- **改注释变成改代码**(pre_mortem #3):缓解:§5 Always「函数体 byte 不变」+ Run1 trivial
  + adversarial。

---

## 10. File Discovery

| File | Category | Key Finding |
|---|---|---|
| `backend/core/evolution/escalation_ladder.py` | MODIFY(注释) | L12-16 STALE 头注释;L123-138 gate-rung 已实现可达。仅改注释。14 callers,MEDIUM 风险 → 不动 body。 |
| `backend/core/eval_service.py` | MODIFY | `decide_governance` accept 分支 :1341-1353 挂 mint(**不是** register_rule 内,避免循环依赖);`add_case` :300(5 必填字段 :298);EvalHistory 读点 :58/153;accept 队列 `_proposals_path` :1284。 |
| `backend/core/evolution/correction_tracker.py` | VERIFY / (Run3 MODIFY) | register_rule :339 / register_gate :367 置 active_rule;check_auto_resolve :388 是 gate-only+从未触发(Run3 加 rule-scoped 分支)。刻意零依赖(:8)—— mint 不可挂这里。 |
| `backend/scripts/golden_case_validator.py` | VERIFY | affected_by `EVOLUTION.CLASS_A` 的 **resolver 在 :201-207**(:173-174 只是示例注释);隐私门 :150-157 会拒 promote。 |
| `backend/routers/eval.py` | VERIFY | :476 get_pending_governance;:487 decide —— accept 入口,不改。 |
| `desktop/src/pages/EvalDashboard.tsx` | VERIFY | :722 decision POST —— accept UI 已存在,不改。 |
| `~/.swarm-ai/state/correction_tracker.json` | (live state) | 证伪证据来源:全 class active_rule/gate=None。 |
| `~/.swarm-ai/state/governance_pending.json` | (live state) | 26 parked / 0 accepted —— accept-path 饿死证据。 |

---

## 11. 决策记录

- **D1(taste)**:选方案 B 而非 QUALITY —— 不重写 `check_auto_resolve` 的 resolve 语义
  (那属 Run3 且 Ask-First),避免为零/低行为收益改动工作代码(R25)。
- **D2(mechanical)**:gate rung「不需要建」—— 现场证据 escalation_ladder.py:123-138 已实现。
- **D3(judgment,已用 AGENT 默认处理)**:Run 0(运营 accept)是硬前置,不是代码 —— 标注而非
  代替 XG 决定接受哪些提案(治理 write 仍归人类)。
