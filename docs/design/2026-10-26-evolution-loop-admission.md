---
title: 进化回路的准入 — 宁缺毋滥：没有具体可执行内容的提案一律不产生
created: 2026-10-26
updated: 2026-10-26
project: SwarmAI
status: final r3 — XG 2026-10-26 定稿（§4 选 A：删除自动提案链路）；r1–r3 见 §8
---

# 进化回路的准入（宁缺毋滥）

## 0. 一句话

进化回路只在能给出**具体、可执行、已经和现有规则核对过**的内容时才产生东西；做不到就什么都不产生。
空泛的提案、重复的规则、没人读的数据，都比没有更糟：它们占注意力、稀释规则，还让"回路在工作"
看起来是真的。顺着这条原则查下去，回路里有一整条链路只写不读，本设计把它删掉。

## 1. 起因

2026-10-26 按 Anthropic 单循环迁移指南（"删除为默认；行为不对时，修法很少是再加一句"）审了
SOUL / AGENT / STEERING / SELF / TOOLS：3 处矛盾、6 处重复、5 处为旧模型或旧架构写的指令，已删改
（`ba9868915`）。晋升入口 `s_self-evolution` 的条数上限、自动晋升、过期 P 范围已修（`b9d5cae5d`）。
本设计处理剩下的问题："不该进来的东西从哪里来"，以及"有没有东西在清理"。

## 2. 现状（每条都是本轮读到的代码或数据）

### 2.1 自动链路：分类 → 计数 → 阶梯 → 提案，几乎全程只写不读

- **分类**：`hooks/evolution_maintenance_hook.py:326-337` 每次 session 收尾跑
  `classify_new_corrections`，每条纠错一次 Bedrock 调用，单次收尾最多 50 条
  （`core/evolution/governance_router.py:171-260`）。结果写进
  `~/.swarm-ai/state/governance_pending.json`（148 行，全仓库没有读者）和 tracker 计数。
- **计数**：`~/.swarm-ai/state/correction_tracker.json` 只有大桶：CLASS_A 21、CLASS_B 112、
  CLASS_C 18、SESSION_LOW_QUALITY 43、OPERATIONAL 2961。唯一读 count 的 `growth_report()`
  （`core/eval_service.py:1091`）没有生产调用方。
- **阶梯**：`core/evolution/escalation_ladder.py:143-155` 计数 ≥3 且无 active_rule → 提
  "propose an L1 rule"；`:123-138` 有 active_rule 且规则后复发 ≥2 → 提 "escalate to a CODE GATE"。
  调用点只有 `evolution_maintenance_hook.py:339-353` → `escalate_class`（`governance_router.py:421`）。
- **"规则"是占位名**：dashboard accept 调 `register_gate(cls, f"GATE_{cls}", ...)`（`eval_service.py:1714`）
  或 `register_rule`（`:1739`），gate 还经 `core/evolution/gate_scaffold.py` 往 DDD `3-gates/` 生成
  `GATE_<cls>.py` 空壳。tracker 里的 active_rule 因此是 `RULE_CLASS_A/B/C`、`RULE_SESSION_LOW_QUALITY`，
  active_gate 有 `GATE_CLASS_A`。
- **产出**：`.evolution_proposals.json` 唯一的治理行是 "CLASS_C: rule RULE_CLASS_C failed (17x
  recurrence post-rule) — escalate to a CODE GATE."，不可执行（SOUL P7）。四个月里这条链路有一次
  带来了真实工作：2026-08-10 的 `CLASS_B:rule` 卡片引出了 STEERING #16（`EVOLUTION_CHANGELOG.jsonl`）；
  #16 的文字是人在对话里写的，卡片只给了"CLASS_B 32 次"。
- **剩下的读者**：tracker 的 active_rule / active_gate / post_rule_count 在生产代码里只被
  `closed_loop.audit_recurrence`（`core/evolution_optimizer.py:1617-1640`，写一条
  "gate did not hold" 日志）和 `check_auto_resolve`（`evolution_maintenance_hook.py:299`）读。
- **另一条大桶路径**：`evolution_maintenance_hook.py:985-1069` 的 `_check_promotion_threshold` 写
  `.governance_promotion_candidates.json`（"Bias X (Nx)"），没有读者。

### 2.2 治理挖掘器

- `core/evolution/governance_miner.py:225-262` 的 `CLASS X:` 段路径在现有数据上产出为空：唯一的
  CLASS 段没有 `**Pattern**:` 行，被 `:249-256` 的空 pattern 守卫跳过。
- GC 路径（`:264-288`）：EVOLUTION.md § Governance Candidates 达到 3 次证据才提案。现有每一行都是
  1/3，自 6 月起没有一行到过 3/3：计数只在 agent 记得去改表时才动。
- 裁决不留痕：dashboard 的 accept / reject 只删提案行，miner 只跳过标了 PROMOTED 的 GC 行
  （`:170`），所以被拒或被接受的 GC 提案下一轮会被重新挖出来。
- 模板与解析不一致：miner 从第 4 列读计数，`s_self-evolution/INSTRUCTIONS.md:247-252` 的表模板
  第 4 列是 Bias；按文档格式写的行会被静默跳过。

### 2.3 skill 优化器的提案没有读者

- 高置信度时把 `User said should: '…'` 行（heuristic 路径）、`AntiPatternGenerator` 段和 LLM 改写
  写进 `.evolution_proposals.json`（`core/evolution_optimizer.py:1466-1547`、`:1710-1731`）。
- 全仓库读这个文件的代码全部只取治理行：growth report（`eval_service.py:1137`）、
  `_read_governance_proposals`（`:1640-1660`）、`routers/eval.py:324`（overlay 的 Approve 角标）、
  `:738-745`（dashboard 列表）、`attention_authority._collect_governance`（`:273-372`，Need-You 卡）。
  skill 行唯一的读取是写入方自己的去重（`:1728`）；`atomic_deploy` 没有生产调用方。没有审批，也没有部署。
- 现有 5 条 skill 提案的内容例：往 summarize 加 `- schema for existing credits check`，往 wireframe 加
  `❌ Don't 使用亚马逊邮箱。`。没有任何 skill 文件含 "auto-generated from corrections"。
- `AntiPatternGenerator` 在 MINE 阶段还算一个 `anti_patterns_count`（`:1375-1380`），这个值不写进
  `skill_health.json`。

### 2.4 清理与评估

- `s_context-hygiene` §7 的周度清扫：两个 job 在 `user-jobs.yaml:406`、`:488`，均 `enabled: false`，
  最后一次运行 2026-08-14 失败（"Input is too long"）。
- `scripts/eval_runner.py:2840` 的 `--trigger model_change|steering_edit` 只是标签，没有代码用它触发。
- **OS Eval 能测治理文字**：`load_golden_set`（`eval_runner.py:307-330`）合并
  `golden_set.private.yaml`：172 条，113 条 LLM 判定、19 条 behavior；60 条的 `affected_by` 指向
  SOUL / AGENT / STEERING，`_resolve_reference`（`:938-941`）把规则原文放进 judge 上下文。LLM 判定不确定，
  跑两次是有意义的。

## 3. 设计

每个入口只有一个问题："这次产出的东西，一个人拿到能不能直接执行，而且产出者已经和现有规则核对过？"
不能 → 不产出，也不留"低优先级"中间态。

### 3.1 删掉只写不读的自动链路（D1）

删除，前提是实现时 grep 全部读者（R27，含测试）确认 §2.1 的结论：
- 收尾 hook 的分类调用、`governance_pending.json` 的写入、`judgment_classifier` 只为这条链路存在的部分。
- `CorrectionClassTracker` 及其状态文件、`escalation_ladder.py`、`escalate_class`、
  `closed_loop.audit_recurrence`、`check_auto_resolve`、`_check_promotion_threshold` 及
  `.governance_promotion_candidates.json`。
- `eval_service.decide_governance` 的 register 分支、`gate_scaffold.py`、`growth_report()`。
- `jobs/handlers/session_quality.py:129-132` 的计数记录（只删记录，handler 本身另判）。

实现时任何一项若发现生产读者，保留并在提交里写明读者是谁。

**这会去掉"纠错到阈值自动出提案"**（XG 2026-10-26 定：§4）。删掉之后，系统不再"自己"提出治理改动；
只有 GC 行已经写好具体文字、核对过、计到 3 次时，才会作为提案摆出来，而 GC 计数靠对话里推动。

### 3.2 GC 路径：只有写明"核对过什么"的行才能成为提案（D2）

- 删除 miner 的 `CLASS X:` 段路径和 `_has_existing_rule`。理由：现有数据上它已经不产出（§2.2），
  而且大类 pattern 本来就是 SOUL 原则的复述。
- GC 表加一列 **Checked**：`checked against: <规则编号> — new because <一句话>`。agent 加计数时必须
  填写（它的 prompt 里本来就有全部四个文件，语义核对在这里做最准）。miner 只把**计数 ≥3 且 Checked
  非空**的行变成提案，判断依据是字段是否存在，确定性。
- 不做自动重叠打分：BM25 分数随查询长度和语料变化，不能用固定阈值；SOUL 几乎没有中文，而 GC 行多是
  中文；同义不同词的重复（例如 GC14 和 R30 (1)(2)）打分抓不到。用分数会给出"核对过"的假象，比不核对更糟。
- 裁决要留痕：accept 把 GC 行的计数列改成 `✅ PROMOTED`（miner 已跳过），reject 改成 `✖ REJECTED`
  （miner 同样跳过）。dashboard 的按钮语义随之变成"接受 → 接下来用 s_self-evolution 写进文件"和
  "拒绝 → 不再出现"。
- 修 `s_self-evolution` 的 GC 表模板，让列顺序和 miner、和 EVOLUTION.md 现有表一致
  （ID | Proposed Rule | Evidence | Count | Since | Checked）。

### 3.3 删掉 skill 优化器的提案输出（D3）

删除 `AntiPatternGenerator`、`:1375-1380` 的计数块、`:1466-1547` 的提案写入和
`_write_evolution_proposal`。MINE 阶段的其余部分和 `skill_health.json` 不动。

### 3.4 一次性清理

- 先备份到 `.context/.sweep-backups/<date>/`：`.evolution_proposals.json`、`correction_tracker.json`、
  `governance_pending.json`、`.governance_promotion_candidates.json`。
- `.evolution_proposals.json` 删 skill 行和阶梯行（source_class 有值、gc_id 为空），保留 GC 行；其余三个
  状态文件随 D1 删除。
- 若 `Projects/*/3-gates/` 下有 `GATE_CLASS_*.py` 空壳则删除（SwarmAI 下目前只有 `context`）。
- `user-jobs.yaml` 里两个已停用的 context-sweep job，以及 `s_context-hygiene` §7 的 sweep 模式：
  一并删除。它们跑不起来，也没人读产出。`user-jobs.yaml` 是用户配置，删之前单独确认。

### 3.5 模型换了，提示审一遍治理文件（P1）

- 比较 `config.default_model` 和 `.context/.governance_audit.json` 的 `audited_model`。不比较
  per-session 模型：tab 和 channel 可以各自覆盖模型（`resolve_model`，`prompt_builder.py:606-611`），
  逐 session 比会来回跳。
- 首次（文件不存在）：写 `baseline_model`，不提示，不写 `audited_model`（没审过就不能说审过）。
  `default_model` 和 `audited_model`（无则 `baseline_model`）不同 → briefing 里加一行：
  "默认模型换成了 <新>，治理文件上次按 <旧> 审过：建议做一次 prompt 审计（s_context-hygiene）。"
- 审计做完、改动经用户批准后，由 `s_context-hygiene` 写 `audited_model`。
- 读写都放在 briefing 组装之外：`build_session_briefing(workspace_dir)`（`prompt_builder.py:1446-1456`）
  只读文件、不接模型参数，所以由已知 `default_model` 的调用方算出提示行再传入；读取路径不写状态，
  baseline 由 daemon 启动时写一次。
- 不加定时任务，不加每月扫描。

### 3.6 审计工具与评估

- `s_context-hygiene` 增加 **audit** 模式（替代删掉的 §7）：读 SOUL / AGENT / STEERING / SELF / TOOLS，
  按"矛盾 / 重复 / 为旧模型或旧架构写的"三类出候选，每条带出处；删改提给用户批准。这是今天手工做的
  那一遍，写成步骤，不加打分脚本。
- 治理改动的评估：系统文件改动 rebuild 并重启之后，由部署触发跑 `affected_by` 指向被改文件的私有
  golden case，**跑两次**，和改动前的 EvalHistory 对比，列出从过变成不过的 case（不只看总分）。
  这需要 `eval_runner` 支持按 `affected_by` 选 case 和重复次数，并输出翻转列表。eval 仍只在
  CI / 部署 / 定时触发，不在编码过程中手动跑（AGENT § Eval）。
- 今天的 `ba9868915` / `b9d5cae5d` 是第一个用这套方法评估的改动。

### 3.7 不做的事

- 不改 MEMORY / EVOLUTION / DDD / KNOWLEDGE 的摄取（各有 ingestion_gate 和 cultivation judge）。
  P8 接缝：§3.2 的"产出者写明核对过什么"同样适用于这几扇门，本次不接。
- 不加自动重叠打分（理由见 §3.2）。

## 4. 决定：删除自动提案链路（XG 2026-10-26）

这推翻了 2026-06-25 的指示（"认知纠错自动计数，到阈值自动发出结构性修法提案，不用问"，MEMORY Open
Threads 的 Evolution Pipeline 条）。

- **选定 A：删除。** 依据 §2.1：这条链路四个月产出的是占位名、一个不可执行的 gate 提案和一次
  "CLASS_B 32 次"的提示，具体文字始终是人在对话里写的；代价是每条纠错一次 Bedrock 调用和四个没人读的
  状态文件。
- **没选 B（保留计数、只删提案）**：计数没有读者，保留等于继续花钱写一个没人看的数字。
- **没选 C（分类结果自动推进 GC 计数）**：分类只给出大类和父原则，映射不到具体 GC 行；要做就得再加一层
  模型判断"这条纠错属于哪个 GC"，又回到不确定的自动产出。

同步更新（随 §3.1 同一提交）：SELF § Kernel Flows 的 Self-evolution 段、EVOLUTION.md 设计哲学和 GC 表头
"_Auto-promote at 3x evidence._"、L0 行 "Yes (confidence-gated)"、`s_self-evolution/INSTRUCTIONS.md:23`
和 `:255`、`governance_router.py` docstring（`:27-29`、`:299`）、`routers/eval.py:749`、
`EvalDashboard.tsx:876-890` 的文案、MEMORY 那条 Open Thread（按 R30#4 删除，不改写）。

## 5. dashboard 与 Need-You

- GC 提案按构造就是可执行的：不再给它算 confidence；`CMBrainOverlay.tsx:115` 的
  `GOV_ACTIONABLE_CONF` 只对带 confidence 的行生效，GC 行不置灰（现在 3/3 的 GC 是 0.6，会被置灰）。
- GC 行带 `title`（proposed_rule 首句），Need-You 卡用它做标题，不再按 `source_class` 生成
  "a new rule for a recurring mistake"（`attention_authority.py:303-313`）。
- 按钮：接受 / 拒绝，语义见 §3.2。

## 6. 验证计划（现在定，不在做的过程中加）

**谁读写入的东西**

| 写入 | 读者 | 本设计后 |
|---|---|---|
| `.evolution_proposals.json` | `_read_governance_proposals`、`routers/eval.py:324`、dashboard、`attention_authority` | 只剩 GC 行，多 `title` 字段；读者先改（§5）再改写入方 |
| EVOLUTION.md GC 表的 Checked 列 / 状态列 | miner | miner 先支持新列和 REJECTED，再改模板 |
| `.governance_audit.json`（新） | briefing 调用方 | 读者先实现，再接 baseline / audited 写入 |
| 删除的四个状态文件 | 无（§2.1） | 实现时 grep 复核 |

**最终测试集（结束时跑一次）**

1. miner：计数 ≥3 且 Checked 为空 → 不出提案；计数 ≥3 且 Checked 非空 → 出提案并带 title；
   PROMOTED / REJECTED 行 → 不出；`CLASS X:` 段 → 不出；按新模板写的行能被解析。
2. accept / reject 写回 GC 行状态，下一轮 miner 不再挖出同一 gc_id。
3. session 收尾 hook 不调分类、不写任何治理提案或状态文件。
4. 优化器高置信度路径不写 `.evolution_proposals.json`。
5. 模型提示：首次 → 写 baseline、不提示；相同 → 不提示；不同 → 一行；审计写入后 → 不提示；
   per-tab 模型覆盖不影响结果。
6. 一次性清理：四个备份存在且可读之后才删；skill 行和阶梯行删除，GC 行保留。
7. overlay：GC 行不置灰；Need-You 卡标题来自 title。
8. eval_runner：按 `affected_by` 选 case、重复 N 次、输出翻转列表（用 fixture，不对部署系统跑）。

每个修复写的时候做变异检查（`scripts.mutation_check`）。会断的现有测试（审查已列出）：
`test_escalation_ladder.py`、`test_governance_router.py:322-397`、`test_correction_tracker.py`、
`test_class_key.py:68-70`、`test_gate_scaffold.py`、`test_governance_api.py:79-124`、
`test_evolution_optimizer.py:19,896+`、`test_todo_noise_fixes.py:132-148`、
`test_governance_miner.py:26-108,177-226`、`scripts/evolution_escalation_probe.py`：随被删代码一起删或改。
提交前跑被改函数及其直接调用方已有的测试，不跑目录级回归。

**审查**

- 设计：已做一次零上下文前提审查（§8）。按规则，不再做第二轮设计审查；定稿后由测试验证。
- 代码：D1 / D3 以删除为主，一个 correctness 专家，重点核"删掉的东西确实没有读者"；§3.5 和 §5
  加一个 state-machine 视角。

## 7. 落地方式

跨 hook、evolution、eval_service、eval_runner、briefing、前端 overlay 六处，并改变 dashboard 的行为，按 R1
属于高风险：开始实现时由 XG 选直接改或走 pipeline。顺序（每步可单独提交、单独验证）：

1. §3.3 skill 优化器提案输出（纯删除）
2. §3.1 + §3.4 自动链路删除与一次性清理（含 §4 的同步更新）
3. §3.2 + §5 GC 准入列、裁决留痕、dashboard / Need-You
4. §3.5 模型变更提示
5. §3.6 audit 模式与 eval 翻转对比

## 8. 修订记录

- **实现备注（2026-10-26，代码审查后）**：correctness 与 state-machine 两位专家指出，proposal 文件作为 GC 表的
  第二份副本会产生竞态（已裁决的提案被挖掘器重新追加）、过期（GC 行被改后提案仍在）和旧行残留。按原因一次修：
  删掉这份副本，GC 表成为唯一存储，读取方每次从 EVOLUTION.md 推导，裁决在原行写入；`governance_router` 和
  优化器的 Phase 3c 随之删除。接受的裁决写 `✔ ACCEPTED`（规则还没写进文件，不叫 PROMOTED）。回写只改 GC 段内
  未裁决的行、保留换行与文件权限、支持 `\|`；`--affected-by` 不再自动带出 behavior case；接口的阻塞锁放进线程；
  审计状态文件损坏时自动重写 baseline。§3.2、§5、§6 中关于 `.evolution_proposals.json` 的描述以此为准。
- **r3（定稿）**：XG 选 §4 的 A；§3.1、§4、§7 改为已定结论，不再列待定项。
- **r2（零上下文审查后）**，按原因合并：
  - **我对评估能力的判断错了**：只看了公开 golden set，漏了 runner 合并的私有集（172 条，60 条覆盖治理
    文件）。§2.4、§3.6 重写；"跑两次"从删除改为保留并落到部署后的翻转对比。
  - **D1 的范围小了**：阶梯上游的分类、计数、两个候选文件都只写不读，一起删；growth report 没有调用方，
    不能作为保留计数的理由。
  - **D2 的机制换了**：固定阈值的重叠打分不可靠，换成"产出者写明核对过什么"的字段检查；补上裁决留痕
    （REJECTED），否则 accept / reject 都会被重新挖出；补上模板列顺序。
  - **自动性的代价写明**：GC 路径同样靠人推进，删除 D1 等于去掉自动提案，§4 改成 A/B/C 让 XG 定。
  - **§3.5 改为比较 default_model**，首次写 baseline 而不是 audited，读取路径不写状态。
  - 补 dashboard / Need-You 的副作用（§5）、`AntiPatternGenerator` 的第二个用处、已停用的 sweep job、
    会断的测试和要同步的文档。
- **r1**：初稿。
