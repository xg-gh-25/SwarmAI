---
title: 统一认知 Ingestion 准入层 — ingestion_gate（A′ 修正版,P8 落地）
run: run_0d60e04e
profile: goal
date: 2026-08-10
status: design-in-build (Gate-1 BLOCK 后重写)
depends_on: [2026-08-10-cognitive-ingestion-gates-survey.md, 2026-08-10-self-adversarial-trust-gate-design.md]
gate1_corrections: [dispatcher-not-proposal-shaped, real-cutover-step, unify-both-noise-fns, drop-phantom-150-callers, real-insert-points]
---

# 统一 Ingestion 准入层 ingestion_gate（A′ 真统一）

## 0. 目标 + Gate-1 修正记录
P8 落地:4 库 × 7 入口 funnel 过一个 `ingestion_gate`。**Gate-1 BLOCK 了初版,全部 code-verified 属实,已修:**
1. dispatcher 不吃 CultivationProposal(初版误以为复用 admission_band——它是 DDD-proposal 专用)→ 改吃 `(text, store, trigger, context: dict)`。
2. 初版无 cutover = 永久双路径 = patch 冒充 refactor → **加 C7 cutover:每条旧路迁完就删,DoD='旧路已删+grep零残留'**。
3. 初版漏了:真正被 MEMORY/EVOLUTION 用的 noise 函数是 `is_noise_entry`(不是 `is_noise`),两者噪音类别互补 → **统一成一个 `is_noise`**(高价值合并点)。
4. "150 caller" 是幻觉(实为 1 test + 2 内部调用)→ 删除该风险叙事。
5. MEMORY/EVOLUTION 插入点是 `_run_locked_write(path,section,text)`(不是 `_write_entries`)。
6. `evaluate_auto_approval`/`stamp_trust` 吃 proposal,**非 store-无关** → trust/confident tier 吃 context dict,DDD store 时才启用。

## 1. 架构 — 叶子模块 + store-无关 dispatcher

> **Gate-1 round 2 修正(全部 code-verified):** ⓐ 两个 noise 函数 **不是安全并集** —— `is_noise`
> 经 `is_quality_lesson` 带 **≥5-word 价值 floor**,`is_noise_entry` 无 floor 只看结构。合并会 silently
> 拒掉 summarization/distillation 今天保留的短决策片段。→ **noise 拆两层**(结构噪音 vs DDD 价值 floor)。
> ⓑ `route_lesson_type`(type-holdback,按 entry-TYPE)与 `is_protected_zone`(按 doc/section)**正交**,
> 删 type-holdback 丢掉 KEEP_TYPES 永久 auto-sink 守卫 → **保留为独立 tier,不删**。ⓒ orchestrator 有
> 两条写路径(`_ch_llm_refresh` + `_auto_apply_ddd_proposals`),各自单独 trigger。ⓓ gate 必须保留
> `apply_to_ddd` 的 status 词汇(writeback fault-warning 依赖它)。ⓔ C3 必须**删** admission_band 决策
> 体(sequence 移进 gate),不是包一层,否则仍 C042。

```
新建 backend/core/ingestion_gate.py  (叶子,只依赖 leaf 模块)
  ├─ noise 拆两层(Gate-1 ⓐ:强度不同不能合并):
  │    structural_noise(text) -> bool          # 全 store 适用,无长度 floor
  │      = table碎片/agent-monologue/emoji前缀 (原 is_noise_entry 语义)
  │    ddd_value_floor(text) -> bool            # 仅 DDD lesson trigger
  │      = ≥5-word/instance-log/narration (原 is_quality_lesson + is_noise 语义)
  │    → noise tier(全 store)= structural_noise;confident tier(仅 DDD)= ddd_value_floor
  ├─ self_adversarial_judge(text, section, neighbors) -> (verdict, reason)  # 新, run_8dea0dd5 设计
  ├─ TRIGGER_TIERS: dict[trigger_id, list[str]]   # 声明表 SSOT
  └─ ingestion_gate(text, store, trigger, context: dict) -> GateVerdict
         按 TRIGGER_TIERS[trigger] 跑 tier;任一判 discard/review 短路
         context 携带 store 专用料(DDD: {proposal_dict, project_dir};MEMORY: {section})
         fail-closed:任一 tier 异常 → review
```

**dispatcher 是 store-无关的**:吃 `text` + `context: dict`。DDD 的 `admission_band` 变成
**caller**——把 CultivationProposal 拆成 `text=proposal.content` + `context={proposal, project_dir}`
喂进 gate,不是 gate 吃 proposal。MEMORY/EVOLUTION 喂 `text` + `context={section}`。

**依赖方向(避循环,Gate-1 Q1 已验 is_noise 可安全下沉):** ingestion_gate 是叶子;
ddd_cultivation / memory_extractor / distillation_hook / summarization 各自 import 它。

## 2. Tier 目录(store-aware:每 tier 声明自己适用哪些 store)

| Tier | 零件 | 适用 store | 作用 |
|---|---|---|---|
| `noise` | structural_noise(叶子,无长度 floor) | 全部 | table/monologue/emoji → discard |
| `dedup` | filter_duplicate_entries(locked_write, 已 leaf) | 全部 | 重复 → discard |
| `confident` | ddd_value_floor(≥5-word+instance-log+narration) | **仅 DDD**(Gate-1ⓐ:MEMORY 短片段不该被拒) | DDD lesson value floor |
| `keep_type_holdback` | route_lesson_type / classify_entry_type(按 entry-TYPE) | MEMORY/EVOLUTION 自动 | KEEP_TYPES(principle/correction/decision/model)→ review(永久写不可撤,Gate-1ⓑ) |
| `trust` | stamp_trust_from_run + trust_source(吃 context.run_id) | **仅 DDD** | 继承 Gate-2 → passed(inherited_gate2) |
| `magnitude` | evaluate_auto_approval(吃 context.proposal) | **仅 DDD** | magnitude+circuit-breaker |
| `judge` | self_adversarial_judge | 自动 rating-5 路径 | 无继承 trust 时对抗审,pass 盖 self_adversarial |
| `human` | 返回 review | 手动路径 | 强制人审 |

> **protected_zone 不是 gate tier**(Gate-1 round3 HIGH):`is_protected_zone` 的 pre-drop 保留在
> `_cultivate_proposals:2259` 的 caller 里(它 sink 到 protected-zone-candidates.jsonl 供周报,是
> caller 专属副作用)。放进 gate 会与 caller double-decide + 分裂 sink。judge 的保护区门禁
> (run_8dea0dd5 R1:trust=n/a + 保护区 → review,judge 不跑)仍在 judge tier 内实现——那是
> "self_adversarial 不能开保护区门",与 caller 的 pre-drop 不同层,不冲突。

DDD-专用 tier(confident/trust/magnitude)只在 context 带了对应料时启用。
**`keep_type_holdback` 与 `protected_zone` 是正交两轴(entry-TYPE vs doc-location),都保留,不合并**(Gate-1ⓑ)。
`keep_type_holdback` 直接复用 `route_lesson_type(lesson)->(section|None,etype)`(已是 SSOT):section=None → review。

**层次修正(grep 后更清晰,Gate-1ⓓ):gate = DECISION 层,`apply_to_ddd` = EXECUTION 层,两者不合并。**
gate 只判 auto/review/discard;判 auto 后**仍由 `apply_to_ddd` 执行写入**——所以 apply 的 status 词汇
(applied/created_section/duplicate/rejected_low_value/not_safe/doc_missing/locked)**原样保留**,
writeback 先调 gate(决策)再调 apply_to_ddd(执行,仅 verdict=auto 时),fault-warning 契约天然不破,
无需 GateVerdict 携带 apply status。

## 3. TRIGGER_TIERS 声明表(SSOT)

| trigger_id | store | rating | tier 序列 |
|---|---|---|---|
| `ddd_reflect` / `ddd_session_signal` | DDD | 4 | noise→trust→(n/a)judge→confident→magnitude→dedup（protected_zone 留 caller@2259,不进 tier——避免 double-decide,Gate-1 HIGH-Q1）|
| `ddd_writeback` (原 bypass) | DDD | 4 | 同上 |
| `ddd_orch_llm_refresh` (原 bypass, `_ch_llm_refresh`) | DDD | 5 | noise→confident→magnitude→dedup |
| `ddd_orch_mechanical` (原 bypass, `_auto_apply_ddd_proposals`) | DDD | 5 | noise→confident→dedup（auto-commit@745 挪到 gate 后）|
| `ddd_conversation` | DDD | 2 | human |
| `memory_distill` | MEMORY | 5 | noise→keep_type_holdback→judge→dedup（**不含 confident**:MEMORY 短片段不该被 DDD 价值 floor 拒,Gate-1ⓐ;freq/git-verify 是 gate 前的 store-local 前置)|
| `memory_save_button` | MEMORY | 3 | noise→dedup |
| `memory_persist` / `evolution_persist` | MEM/EVO | 1-2 | dedup |
| `evolution_distill` | EVOLUTION | 5 | noise→keep_type_holdback→judge→dedup |
| `knowledge_*` | KNOWLEDGE | index | 不过 gate（index 刷新非知识准入）|

原则:rating-5 自动路径上 judge;rating-1/2 手动只 dedup(C042 不无差别铺)。

## 4. MEMORY/EVOLUTION 现有 store-local gate 的处置（Gate-1 round2 修正）
- **freq-gate(≥2 files)**:MEMORY 量闸,gate 无对应 tier → `memory_distill` 的 gate 前 **store-local 前置**(量非质,不重叠,不 subsume)。code-verified 它在 `distillation_hook.py:533-541`,先于 `_run_locked_write`,所以 entry 只过一层质量决策(gate),非双 gate。
- **git-verify([UNVERIFIED] 标注)**:MEMORY 来源闸,不 reject 只标注 → 保留为前置,不进 gate。
- **type-holdback(route_lesson_type)**:Gate-1ⓑ 证明它与 protected_zone **正交**(按 entry-TYPE vs 按 doc/section)→ **不删,提为 gate 的独立 `keep_type_holdback` tier**,MEMORY/EVOLUTION 自动路径声明里带它。删它会丢 KEEP_TYPES 永久 auto-sink 守卫。
- **`_is_noise_entry` 两处调用**:被 gate 的 `noise` tier(=structural_noise,同语义无长度 floor)subsume → 改调,删旧调用。**summarization 切到 structural_noise 而非带 floor 的 is_noise**(Gate-1ⓐ:否则 silently 拒短片段)。
诚实边界:gate 统一的是**通用质量闸**(structural_noise/judge/dedup + 显式声明的 DDD 专用 confident/trust/magnitude + 正交的 keep_type_holdback);freq-gate/git-verify 是 MEMORY 独有的**量/来源**闸,明确留 store-local 前置,不强塞(过度统一是另一种 break)。

## 4b. INGESTION 范围界定(Gate-1 round3 BLOCK — 明确 carve-out,不 silently 漏)
> **⚠️ C4 CODE-VERIFIED CORRECTION (run_0d60e04e C4):初版把 orchestrator 的 4 条 channel
> 分成"2 ingestion + 2 refresh"是错的——code-trace 证明 ALL 4 条都是 value-refresh,不是
> ingestion。** `_ch_llm_refresh` 经 `_apply_llm_proposal:1537-1546` 做 `current_text→proposed_text`
> **verbatim-match 替换**(要求 current_text 逐字出现在 doc 里,再 `.replace(...,1)`);
> `_auto_apply_ddd_proposals:662-665` 只在 `is_mechanical`(proposed = current + 追加行)时
> apply——**两者都是改写已有 doc 文本,不创建新 CultivationProposal 知识条目**。按本节自己
> 的**判据**(下一行:"改已有条目不是 ingestion"),它们与 `_ch_mechanical_refresh`/`_ch_memory_refresh`
> 同类,全部 **carve-out,不 gate**。把 replace-in-place 的路塞进 append 语义的 gate(noise/confident
> tier 判的是"新条目好不好")是 wrong-layer——会拿"这是不是好的新经验"的尺子量一个 drift 修正 diff。

orchestrator 的 **4 条 channel 全部是 value-refresh,不进 gate**(改已有条目,非新知识准入)。
真正的 DDD ingestion bypass **只有 writeback 一条**(C4a):
- ✅ ingestion(进 gate,C4a):`improvement_writeback_hook._append_lessons`——它 build **append**
  `CultivationProposal`(新经验条目进 IMPROVEMENT.md),`source_run_id="session_..."`(非 run_)→
  trust=n/a,却**直调 apply_to_ddd 绕过 admission_band**。这是唯一没上门卫的新知识入口。
- ❌ NOT ingestion(carve-out,DoD 需列为 intentional,grep 才诚实):`_ch_llm_refresh`(1241)、
  `_auto_apply_ddd_proposals`(594)、`_ch_mechanical_refresh`(1136)、`_ch_memory_refresh`(1189)
  ——全是 `current→proposed` 替换/数值漂移修正,改已有条目,非新知识准入。

**判据(可复用):gate 治的是"新知识进不进大脑";改已有条目的数值/引用漂移不是 ingestion。**

## 5. Cutover — 每条旧路迁完就删（Gate-1 BLOCK#1 核心修正）

| Cycle | 迁移 | 删除(cutover) | 验证 |
|---|---|---|---|
| C1 | 建 ingestion_gate.py:structural_noise(table/monologue/emoji,无 floor)+ ddd_value_floor(≥5-word + instance-log + narration + **_MACHINE_BROADCAST_RE 3 shapes**,Gate-1ⓕ:machine-broadcast 在 is_noise 非 is_quality_lesson,必须显式进 ddd_value_floor)+ GateVerdict(verdict+tiers_run+reason,**不**含 apply status)+ TRIGGER_TIERS + noise/dedup/confident tier | — | 每 tier 单测 + noise 无 floor vs confident 有 floor + machine-broadcast trust=passed DDD proposal→discard |
| C2 | self_adversarial_judge + judge(含保护区门禁 run_8dea0dd5 R1)/trust/magnitude/keep_type_holdback tier + fail-closed | — | judge 三路+异常→review+保护区→review+KEEP_TYPES→review |
| C3 | DDD:admission_band 拆 proposal→调 gate（**admission_band 保留为 gate-delegating shim,不整体删**——它有第 2 caller `backfill_proposals:1912`,shim 保它可用,Gate-1 r4 MED）| **删 admission_band 决策体(noise/trust/magnitude/confidence 移进 gate)**;同时处置 `_cultivate_proposals` pre-gate 逻辑(Gate-1 HIGH):protected_zone pre-drop@2259 **保留在 caller only**(它 sink 到 candidates.jsonl 供周报,不进 gate tier——避免 double-decide),conversation-escalate@2296 + retire/rewrite@2305 **显式声明为 out-of-gate**(是 change_type/source 路由非质量);删 `filter_lessons_for_ddd:493` 的 is_noise 预调(gate 现拥有,避免 preserve+insert 双闸,Gate-1 MED-Q5) | ddd 回归绿 + grep admission_band 内无 trust/magnitude/confidence + grep :493 无 is_noise 预调 |
| C4a | DDD ingestion bypass(唯一一条):writeback 先调 admission_band(决策)→ verdict=auto 才调 apply_to_ddd(执行) | 删 writeback **无条件**直调 apply_to_ddd(改为 gate 门控) | writeback fault-warning 仍只 fault status 触发(apply 契约原样)+ gate 门控断言 + trust=n/a 走 judge |
| ~~C4b~~ | **CANCELLED(C4 code-verified,见 §4b 修正):** orchestrator 4 条全是 value-refresh(replace-in-place / 数值漂移),**不是 ingestion,不 gate**。记为 DoD 的 intentional carve-out。 | — | grep:4 条 orchestrator writer 均为 current→proposed 替换,标注为 intentional-carve-out(非 ungated ingestion) |
| C5 | MEMORY:_run_locked_write 前插 gate(noise+keep_type_holdback+judge) | **删** `_is_noise_entry` **3 处**旧调(summarization:381 + distillation:756,761 — Gate-1 r4 MED 更正:不是 2 处)改调 structural_noise;删 distillation@569/context_health@1222 的 inline route_lesson_type | memory 回归 + distill 上 judge + 短片段不被拒 + grep route_lesson_type/is_noise_entry 无残留 |
| C6 | EVOLUTION:堵 _write_corrections 格式洞 + 插 gate | **删** 旧格式提取的无 gate 路径 | evolution 回归 + 格式洞 test + judge |
| C7 | noise 收口 | **删** extraction_patterns.is_noise_entry;summarization 改调 structural_noise(非带 floor 的) | grep is_noise_entry 零生产调用 + 全库 smoke + DoD 全绿 |

**DoD 关键:"旧路已删 + grep 零残留" + "admission_band 决策体已删(非包一层)"。** strangler 只在新路过测前活;过测即删。

## 6. Boundaries
### Always
- strangler:旧路只活到新路过集成测试,过测**即删**(C4/C5/C6/C7 各有删除项 + grep 零残留断言)
- fail-closed:任一 tier 异常/judge 失败 → review
- rating-5 自动路径必上 judge;保护区只 inherited_gate2 能 auto
- DDD-专用 tier 只在 context 带料时启用(不假装 store-无关)
### Ask First
- daemon 重启上线(R12)——停 PUSH-READY 等 XG
### Never
- 不动底层 locked_read_modify_write(gate 在决策层)
- rating-1/2 手动路径不上 judge(C042)
- 不 preserve+insert 造双 gate(重叠的 type-holdback/noise 必须删,量闸 freq/git-verify 明确标为 store-local 前置)
- 不强把 MEMORY 的量/来源闸(freq/git-verify)塞进通用 gate(过度统一)

## 7. Success Criteria (= DoD)
- ingestion_gate(text,store,trigger,context) 存在,store-无关签名,7 trigger 声明 tier
- 统一 is_noise 覆盖两函数全部噪音类别,is_noise_entry 生产调用 grep 零残留
- 7 入口全过 gate;**每条旧路已删**(DDD 2 bypass、_is_noise_entry、type-holdback 重复、格式洞路径)
- survey 3 洞闭合 + judge fail-closed + 保护区门禁
- 每库回归绿 + 独立 smoke
- 停 PUSH-READY,daemon 重启等 XG 批
