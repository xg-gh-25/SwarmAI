---
title: 准入子系统 v2 — Self-Adversarial Trust Gate（方案 A）
run: run_8dea0dd5
profile: docs
date: 2026-08-10
status: design-approved-pending-build
supersedes_gap_in: 2026-08-09-knowledge-admission-subsystem-design.md (v1 只 inherit trust)
---

# 准入子系统 v2 — Self-Adversarial Trust Gate

## 0. 一句话

给 `admission_band` 补**第二条 trust 来源**：proposal 没有可继承的 Gate-2 记录时（trust=n/a），
复用现成 Bedrock Sonnet 通道跑一个 **zero-context 对抗 judge**（目标 refute），过了盖
`trust=passed(self_adversarial)` → auto，判疑 → review，判噪音 → discard。目的：让准入
**自己能审查一条知识**，把 rely-on-human 降到最低。

## 1. 病根（v1 的结构缺陷 —— 为什么"太多 pending review"）

v1 准入子系统的 trust **只能 inherit，不会 manufacture**：

```
proposal ──trust 唯一来源──> 源 run 的 Gate-2 记录 (derive_gate2_outcome)
    ├─ 来自带 Gate-2 的 pipeline run  → trust=passed → auto ✅
    └─ 其它一切(session decision / conversation / 存量 / 无 gate 的 reflect)
                                      → trust=n/a → 永远 human 队列 ❌
```

`admission_band`（`ddd_cultivation.py:1785`）：`if proposal.passed_adversarial_gate != "passed": return ("review", ...)`。
凡不来自带 Gate-2 的 run 的 proposal 一律落 review。这不是 bug，是**缺了一条腿**：系统没有
"对一条孤立知识自己做审查"的能力。用户要的正是补这条腿。

> **实测证据**：存量清算后剩 13 条 review proposal，`admission_band` 对全部 13 条返回
> `review`，reason 清一色 `trust:n/a`——不是 doc 类型（v1 已删 `is_safe_append` 白名单），
> 是"审查记录不存在"。它们都是真判断（Gate-1 adjudication / 根因决策），只是从未经过 Gate-2 通道。

## 2. 认知边界（这条最重要，写进代码注释）

judge 用的是**和主 agent 同一个模型**。所以它的价值 **NOT** 在"更聪明"——见 EVOLUTION.md
META-CORRECTION（2026-08-04）："同模型从 skeptic 席位审查不是万能"。它的价值在**立场**：

- 主 agent 写知识时带 authorship-trust（"我写的应该对"）→ CONFIRM 模式。
- judge zero-context + refute 目标 → 问"这条知识 ACTUALLY 站得住吗"。
- 同模型，相反的目标函数。

因此 judge 的两条铁律（AC2）：
1. **zero-context**：不喂 builder 的推理/上下文，只喂 { 待审 text，目标 section，现有邻居条目 }。
   邻居仅用于矛盾检测，不是"帮它理解我为什么写这条"。
2. **refute-goal prompt**：默认怀疑，四问——准确吗 / 可证伪吗 / 是噪音吗 / 与现有知识矛盾吗。

judge 是**降低 human 依赖的杠杆，不是消灭审查**。它把"人审每一条"变成"人只审 judge 判不准的"。

## 3. 架构 —— 决策树

> **⚠️ ADVERSARIAL-CORRECTED（run_8dea0dd5 Gate-2，code-verified）：self_adversarial 的权威
> 严格低于 inherited_gate2 —— 它只能开【非保护区】的门，保护区一律强制 review。** 见 §6
> 的洞与修正：原设计误以为 evergreen 保护独立于 trust；code-trace 证明 self_adversarial 盖
> passed 会**关掉** `_cultivate_proposals:2259` 的保护区 pre-drop，且全新 append 无
> contradiction_flag → destructive-supersede 不 fire → 无人复核写进 evergreen。因此保护区
> 的门只有 inherited_gate2 能开。

```
admission_band(proposal, project_dir):
  1. is_noise(text)?  ──yes──> DISCARD            (机器广播/fragment，不浪费 Bedrock 调用)
  2. trust == "passed" AND trust_source == "inherited_gate2"?
        ──yes──> [原 v1 路径: change_type/quality/confidence] ──> auto|review  (可进保护区)
  3. trust == "n/a"  ──> ┌─ change_type != append? ──yes──> REVIEW (retire/rewrite 永不 self-judge auto)
                         ├─ is_protected_zone(doc, section)? ──yes──> REVIEW
                         │      (🔒 保护区 self_adversarial 不可 auto —— 只有 inherited_gate2 能开;
                         │       judge 都不跑,直接落人审。见 §6)
                         ├─ self_adversarial_judge(text, section, neighbors):   # try/except 包裹, AC5
                         │     verdict == "noise"   ──> DISCARD
                         │     verdict == "suspect" ──> REVIEW
                         │     verdict == "pass"    ──> 盖 trust=passed, trust_source=self_adversarial
                         │                              ──> [走 v1 quality/confidence 检查] ──> auto|review
                         │     judge 异常/超时/解析失败/空 ──> REVIEW  (FAIL-CLOSED, AC5)
                         └─
  4. trust == "failed" ──> REVIEW  (显式判过不过，不 self-judge 翻案)
```

**两条关键约束（都被 §6 的 adversarial 修正强化）：**
1. **保护区门禁**：`is_protected_zone(doc, section)` 为真时，trust=n/a 一律 review，**judge 都不跑**。
   保护区（SELF.md 全文 / PRODUCT.md §Vision·Non-Goals·Strategic Priorities·**Design Philosophy** /
   TECH.md §Architecture）只有 `inherited_gate2` 能自动进——self_adversarial 无此权威。
2. **trust 必要非充分**：非保护区里 self_adversarial 的 pass 仍**不绕过** quality/confidence floor。

## 4. Trust 枚举 —— 新增 source 维度

现状：`passed_adversarial_gate ∈ {passed, failed, n/a}`（一个扁平字段）。

v2 的 `passed` 有两个**来源**，需可区分（审计 + 撤销时要知道是谁盖的）：

| trust 值 | source | 语义 | 能 auto? |
|---|---|---|---|
| `passed` | `inherited_gate2` | 源 run 真的跑过 pipeline Gate-2 对抗 | ✅（原路） |
| `passed` | `self_adversarial` | 无源 gate，judge zero-context refute 通过 | ✅（同权，但记来源） |
| `n/a` | — | 无 gate 记录且 judge 未跑/判 suspect | ❌ review |
| `failed` | 任一 | 显式判过不过 | ❌ review |

**实现选择**：不改 `passed_adversarial_gate` 的三值（避免 150 caller 契约破坏），**新增一个正交
字段** `trust_source: str = "none"`（值：`inherited_gate2` / `self_adversarial` / `none`）。
`passed_adversarial_gate="passed"` + `trust_source="self_adversarial"` 就是 self-judge 盖章。
向后兼容：老 proposal 无此字段 → `from_dict` 默认 `"none"`。

## 5. Judge 实现（AC1 —— 不实例化 LlmRefreshProposer）

新增独立 helper（放 `ddd_cultivation.py` 或新 `knowledge_judge.py`）：

```python
def self_adversarial_judge(text: str, target_section: str,
                           neighbors: list[str]) -> tuple[str, str]:
    """zero-context 对抗 judge。返回 (verdict, reason)，verdict ∈ {pass, suspect, noise}。
    FAIL-CLOSED: 任何异常/超时/解析失败 → ("suspect", "<why>") → 上游落 review。"""
```

- **Bedrock 调用**：直接 `from jobs.bedrock import get_client`（Bedrock 凭证 SSOT）+ 复用
  `_get_sonnet_model_id()` 的 model 选择 + 同款 timeout config（connect=5s/read=25s,
  max_attempts=1）。**不 `LlmRefreshProposer(...)`**——它带 throttle state/swarmai_root/
  citation-verify，全是 doc-refresh 专用，且 auto_refresh.py 是 HIGH risk（146 callers）。
  借 Bedrock 调用层，不碰那个类（THINK judgment decision）。
- **temperature=0**（判定类，要稳定）。
- **输出契约**：LLM 首行 `VERDICT: pass|suspect|noise`，次行 `REASON: <一句>`。正则解析
  `^VERDICT:\s*(pass|suspect|noise)`；匹配不到 → fail-closed `("suspect", "unparseable")`。
- **双层 fail-closed（Gate-2 INFO#2）**：helper 内部 broad try/except → 返回 `("suspect",...)`；
  **且** `admission_band` 的 judge **调用点**也用 try/except 包裹 → 异常 → review。不让 fail-closed
  只依赖 helper 内部纪律——若未来某次编辑让异常逃出 helper,调用点仍兜住,不会 crash `_cultivate_proposals`。
- **neighbor 排除自举（Gate-2 MED#5）**：喂给 judge 的 neighbors **排除**（或标记）此前由
  `trust_source=self_adversarial` 写入的条目——否则 judge 会拿自己过去的 pass 当"佐证"形成自我
  强化的准入回路。neighbor 仅用于矛盾检测,且只信 inherited_gate2/人工来源的邻居。

### Judge prompt（refute 立场，写死在 helper）

```
You are a skeptic reviewing ONE candidate knowledge entry for a project's brain.
You have ZERO context on why it was written. Your job is to REFUTE it, not trust it.

CANDIDATE (to be written under §{target_section}):
{text}

EXISTING NEIGHBOR ENTRIES (for contradiction check only):
{neighbors}

Answer, defaulting to skepticism:
1. ACCURATE? Is the claim factually plausible + internally consistent, or does it assert something dubious?
2. FALSIFIABLE / LOAD-BEARING? Is this a real reusable judgment, or vague/tautological/instance-noise?
3. NOISE? Is it a machine broadcast, a log fragment, or a narration with no lesson?
4. CONTRADICTS? Does it directly contradict a neighbor without justification?

Output EXACTLY two lines:
VERDICT: pass|suspect|noise
REASON: <one sentence>

Rules: "pass" ONLY if it survives all four. Any real doubt → "suspect". Machine/fragment/empty → "noise".
When uncertain between pass and suspect, choose suspect (a human will look).
```

## 6. 保护不被绕过（AC6 —— 经 Gate-2 adversarial 纠错后的真实机制）

> **原设计错了，这里是 code-verified 的正确版。** 原 AC6 声称"self_adversarial 盖 passed 只改
> band 决策、apply 层保护独立于 trust、judge 动不到"——**FALSE**。Gate-2 adversarial + 我的
> code-trace 证明三道我以为在的保护，对"全新 append 进保护区"这条路**全部不 fire**：

**洞（三处，全部 code-verified）：**
1. `_cultivate_proposals:2259` 的保护区 pre-drop 条件是 `passed_adversarial_gate != "passed" AND is_protected_zone(...)`
   —— self_adversarial **盖了 passed 就关掉这道 drop**（注释原意只想让 inherited_gate2 fall-through，
   但代码只判 `!= "passed"`，分不清来源）。
2. `apply_to_ddd` 的 destructive-supersede 只在 `proposal.contradiction_flag` 有值时 fire
   （`apply_to_ddd:877` `cf = proposal.contradiction_flag; if cf and ...`）。一条**全新** `[principle]`
   append 进空的/无冲突的保护区 section → `detect_contradiction` 返回 None → `contradiction_flag=None`
   → destructive-supersede 整块跳过 → 直接 append，无人审、无 keep-class 检查。
3. `route_lesson_type` 的 KEEP_TYPES holdback（section=None）**在 ddd_cultivation.py 零调用**
   （grep 确认）——它守的是 MEMORY.md distillation 写入路径，**不守 cultivation→DDD append 路径**。
   原 AC6 引错了保护:它保护的是另一个 writer。
4. 补充洞:`_PROTECTED_ZONES['PRODUCT.md']` 只含 {Vision, Non-Goals, Strategic Priorities}——
   本 doc `[principle]` 的落点 §"Design Philosophy — When Beliefs Become Enforcement"（persist_routing
   `project_principle` route）**不在保护列表**,连今天都没保护;且 `admission_band` 不读 route 的
   `safe_auto=False`。

**修正（写进实现 AC）：**
- **R1 保护区门禁前置**：`admission_band` 在跑 judge 之前先 `is_protected_zone(doc, section)`——
  保护区 + trust=n/a → 直接 review，**judge 不跑**。self_adversarial 无权开保护区的门。
- **R2 `:2259` pre-drop 认来源**：把条件从 `passed_adversarial_gate != "passed"` 改为
  `not (passed_adversarial_gate == "passed" AND trust_source == "inherited_gate2")`——**只有
  inherited_gate2 能让保护区 pre-drop 放行**;self_adversarial 盖的 passed 仍被 pre-drop 拦到
  protected-zone-candidates 人工蒸馏池。
- **R3 补 `_PROTECTED_ZONES`**：给 `PRODUCT.md` 加 `"Design Philosophy — When Beliefs Become Enforcement"`
  （高阶知识区,该受保护）。
- **R4 enforcement 读 trust_source**：每个当前 gate 在 `passed_adversarial_gate == "passed"` 上授予
  保护区权威的点,都要额外要求 `trust_source == "inherited_gate2"`——否则 trust_source 只是装饰
  （Gate-2 MED#4)。

**净效果**：self_adversarial 只能自动进**非保护区**（IMPROVEMENT/TECH 非 Architecture 段等）。
保护区（SELF/PRODUCT 高阶段/TECH Architecture）永远只有 inherited_gate2 能自动进,或人工。
judge 假阳性最多污染非保护区,evergreen 认知底座碰不到。

## 7. 成本护栏（AC9）

judge **只在**满足全部条件时触发一次 Bedrock 调用：
1. `trust == "n/a"`（trust=passed 走原路不调；trust=failed 直接 review 不调）
2. `is_noise == False`（噪音在 step-1 先 discard，不进 judge）
3. `change_type == "append"`（retire/rewrite 直接 review 不调）

即：judge 只审"无 gate 记录 + 非噪音 + append"的候选。不是每条 proposal 都调。
中心化成本治理（STEERING #2）：不在 helper 里埋 per-call 美元上限——judge 触发条件本身就是护栏。

## 8. 存量迁移（AC8）—— 只 count/re-stamp，绝不在 migration 里 auto-write

> **Gate-2 纠错（INFO#6）：** v1 backfill 的契约是**只 count `would_auto`、不 apply**
> （`ddd_cultivation.py:1921` 注释明写"do NOT auto-write during migration；下一个 cultivation
> cycle 再 apply"）。原 §8 措辞"pass→auto-apply"与此矛盾,会诱导实现在 backfill 里 mass-write
> 13 条进 DDD 无 checkpoint。**正确行为 = 保持 v1 的 count-only。**

backfill 复审对 `trust=n/a` 存量跑 `self_adversarial_judge`,但**只**：
- pass → re-stamp proposal JSON 为 `passed`+`trust_source=self_adversarial`,并 `would_auto += 1`
  （**不** apply;下一个正常 cultivation cycle 才真写,那时仍过 §3 决策树含保护区门禁）；
- suspect → 留 review；noise → discard（GC）。

第一次 backfill 先 `dry_run=True` 跑,输出 would_auto/would_review/would_discard 分布给 XG 看,
**再决定**是否放行下一个 cultivation cycle 真写。不需人工逐条过,但保留一个"看分布"的确认点
（Gate-2 MED#5:judge 假阳性率未经实测,先看存量复审分布再全面 auto）。

## File Discovery

| File | Category | Key Finding |
|------|----------|-------------|
| `backend/core/ddd_cultivation.py` | MODIFY | `admission_band:1785` 插 judge 分支；`CultivationProposal` 加 `trust_source` 字段(to_dict/from_dict)；backfill 升级 re-stamp |
| `backend/core/auto_refresh.py` | VERIFY | 只借 `_call_llm` 的形状(get_client + timeout config + _get_sonnet_model_id)，**不 import LlmRefreshProposer**；HIGH risk 146 callers 不碰 |
| `backend/jobs/bedrock.py` | VERIFY | `get_client()` = Bedrock 凭证 SSOT，judge 直调 |
| `backend/core/ddd_auto_approval.py` | VERIFY | `evaluate_auto_approval` 在 self_adversarial 盖 passed 后仍跑(quality/confidence floor 不被绕) |
| `backend/tests/test_admission_*.py` | TEST | 加 test_admission_self_adversarial_judge.py：judge verdict 路由 + fail-closed 全分支 + 保护不绕过 |
| (new) `backend/core/knowledge_judge.py` | MODIFY(可选) | 若 helper 独立成模块，judge + prompt 放这里,ddd_cultivation import |

## Change Spec (ordered) — 供后续 goal run

1. `CultivationProposal` → 加 `trust_source: str = "none"` 字段
   - Depends on: nothing · AC: AC3
   - Current: 有 `passed_adversarial_gate` 三值,无 source 维度
   - Target: 新增正交字段,to_dict/from_dict 带 `.get("trust_source","none")`;stamp_trust_from_run
     盖 inherited 时同时写 `trust_source="inherited_gate2"`
   - Verify: `test_admission_trust_field` 扩断言 round-trip + inherited 盖章带 source

2. `_PROTECTED_ZONES` 补 §Design Philosophy（R3）
   - Depends on: nothing · AC: AC6
   - Current: `PRODUCT.md: {Vision, Non-Goals, Strategic Priorities}`
   - Target: 加 `"Design Philosophy — When Beliefs Become Enforcement"`
   - Verify: `is_protected_zone("PRODUCT.md","Design Philosophy — ...")` == True

3. `self_adversarial_judge(text, section, neighbors)` → 新 helper
   - Depends on: nothing · AC: AC1, AC2, AC5
   - Current: 不存在
   - Target: 直调 get_client, zero-context refute prompt, 解析 VERDICT, 内部 fail-closed;
     neighbors 排除 self_adversarial 历史条目
   - Verify: `test_...judge` mock get_client → pass/suspect/noise 三路 + 异常→suspect

4. `admission_band` → 插保护区门禁 + trust=n/a judge 分支（R1+R4）
   - Depends on: #1, #2, #3 · AC: AC4, AC6, AC7, AC9
   - Current: `trust != passed → review`(1785)
   - Target: (a) trust=passed 授权保护区时额外要求 `trust_source==inherited_gate2`;
     (b) trust=n/a + `is_protected_zone` → review(judge 不跑);
     (c) trust=n/a + 非保护区 + non-noise + append → judge(调用点 try/except) → pass 盖 self_adversarial→quality 路
   - Verify: `test_admission_routing` 加 [保护区+n/a→review judge未调]、[非保护区 n/a+pass→auto]、[suspect→review]、[noise→discard]

5. `_cultivate_proposals:2259` pre-drop 认来源（R2）
   - Depends on: #1 · AC: AC6
   - Current: `if passed_adversarial_gate != "passed" and is_protected_zone(...)`
   - Target: `if not (passed_adversarial_gate=="passed" and trust_source=="inherited_gate2") and is_protected_zone(...)`
   - Verify: `test_...` self_adversarial 盖 passed 的保护区 proposal 仍被 pre-drop 拦到 candidates 池

6. `backfill_proposals` → re-stamp 升级（count-only, R§8）
   - Depends on: #3, #4 · AC: AC8
   - Current: re-stamp 只读 gate2 记录 → n/a
   - Target: n/a 存量跑 judge → pass re-stamp+would_auto++（**不 apply**）, suspect 留 review, noise GC
   - Verify: 存量 13 条 backfill dry-run → would_auto/would_review/would_discard 分布,零 DDD 写入

## Boundaries

### Always
- judge zero-context（只传 text+section+neighbors，绝不传 builder 推理）
- fail-closed：judge 任何失败路径 → review，绝不 auto
- is_noise 在 judge 之前先跑
- self_adversarial 盖 passed 后 quality/confidence floor 仍独立守

### Ask First
- 若 judge 假阳性率在存量复审中偏高（>~20% 明显误 pass）→ 暂停 auto、回报 XG 调 prompt

### Never
- 不实例化 LlmRefreshProposer（借 Bedrock 调用层即可）
- 不因 self_adversarial 盖章绕过 destructive-supersede escalate / KEEP_TYPES holdback
- 不在 helper 里埋 per-call cost/budget 截断（STEERING #2，成本由触发条件护栏 + 中心 governor）
- retire/rewrite 永不 self-judge auto

## Success Criteria
- `admission_band` 对 trust=n/a 的 append 非噪音候选跑 judge,pass→auto/suspect→review/noise→discard,可单测三路
- judge fail-closed:mock get_client 抛异常/超时/返回空/返回不可解析文本 → 全部落 review,有测
- evergreen 保护不被绕:一条 KEEP_TYPES polarity-flip 候选即使 judge pass 仍 escalate,有测
- 存量 13 条走新通道复审,backfill 输出分布,零人工逐条
- trust_source 字段 round-trip 正确,老 proposal 默认 "none"

## Test Strategy
| # | AC | How to Test | Mock Boundary | Input Construction |
|---|----|-----------|--------------|--------------------|
| 1 | AC1/AC2 judge helper | 单测直调 judge,mock get_client 返回构造的 VERDICT 文本 | mock `jobs.bedrock.get_client` 的 invoke_model | 三个 fixture text:真lesson/含糊/机器广播 |
| 2 | AC5 fail-closed | mock get_client 抛 Timeout/返回空/返回"garbage" | 同上 | 每种异常一个 case,断言 verdict=suspect |
| 3 | AC4 band 路由 | 单测 admission_band,mock self_adversarial_judge 返回 pass/suspect/noise | mock judge helper | trust=n/a + append + 非噪音 proposal |
| 4 | AC6 保护不绕 | admission_band + apply,一条 evergreen polarity-flip,judge=pass | mock judge=pass | KEEP_TYPES section 的矛盾候选,断言 escalate 而非 auto-write |
| 5 | AC7 顺序 | 机器广播文本 + trust=n/a,断言 judge 未被调 | spy on judge helper | is_noise=True 的 fixture,断言 judge call_count==0 |
| 6 | AC8 存量 | backfill dry-run over 存量,mock judge | mock judge | 真实 13 条存量 |

## 与 v1 的接口点
- v1 `admission_band` 三 band(auto/review/discard)不变,只在 trust!=passed 的 return 前插 judge 分支。
- v1 `is_noise` SSOT 复用为 judge 前置 + judge prompt 第 3 问的对齐参考。
- v1 backfill 的 re-stamp 从"读 gate2 记录"升级为"无记录则 judge"。
- v1 的 evergreen/KEEP_TYPES 保护(destructive-supersede/holdback)原样保留,doc 明确不被 self_adversarial 绕过。
