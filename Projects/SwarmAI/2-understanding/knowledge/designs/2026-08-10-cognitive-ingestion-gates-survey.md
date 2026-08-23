---
title: 认知系统 Ingestion 准入闸普查 — 4 store × N trigger 的一致性 gap
run: run_8bf1977f
profile: research
date: 2026-08-10
status: research-complete
method: 3 并行 Explore agent code-trace(DDD / MEMORY / EVOLUTION+KNOWLEDGE)+ 真实 store 采样
---

# 认知系统 Ingestion 准入闸普查

## 0. 一句话结论

"三条线"是低估——认知系统实际是 **4 个 store,每个有 2-7 个 ingestion trigger,准入闸严重
不一致**,而且**有多条绕过主 gate 的自动直写路径**。self_adversarial judge(刚设计的)只覆盖
其中最窄的一条(DDD cultivation band),而**噪音率最高、最无人在场的路径(MEMORY 自动
distillation、EVOLUTION 自动 _write_corrections)完全没有对抗审查、连 admission_band 都没有。**

这就是"每次改认知系统必须同时考虑所有线"这条 principle 的实证:护栏装在了次要门上,主门敞着。

## 1. 全景表 — 每个 store 的每个 ingestion trigger

| Store | Trigger | 入口(file:line) | 自动/人工 | 现有准入闸 | 对抗审查? | 无人在场(1-5) |
|---|---|---|---|---|---|:---:|
| **DDD** | T1 pipeline REFLECT cultivate | `context_health_hook.py:906`→`cultivate_from_reflect` | 自动(maintenance loop) | is_noise+admission_band(trust) | ✅(新 judge 将覆盖) | 4 |
| **DDD** | T2 session correction/decision | `context_health_hook.py:1339` | 自动 | admission_band | ✅ | 4 |
| **DDD** | ⚠️T3 improvement_writeback | `improvement_writeback_hook.py:318`→`apply_to_ddd` **直调** | 自动(session-close) | **仅 value-floor+dedup,绕过 admission_band/trust** | ❌ | 4 |
| **DDD** | ⚠️T4 orchestrator refresh channels | `ddd_orchestrator.py` `_ch_auto_apply`/`_ch_llm_refresh`(auto-commit!) | 自动(TIMER_30MIN) | conf 阈值,**绕过 admission_band** | ❌ | 5 |
| **DDD** | T5 conversation | `conversation_digest.py:88` | 手动 opt-in(默认 dormant) | 强制 escalate | — | 2 |
| **DDD** | T6 code_change_feed | `code_change_feed.py:345` | 自动 | **不写 DDD 了**(只 telemetry) | n/a | — |
| **MEMORY** | ⚠️T2/T3 自动 session-end distillation | `distillation_hook.py:352` | **全自动每次 session close**(THRESHOLD=0) | 频次≥2+git-verify+type-holdback+injection-scan+dedup | ❌ **无 judge/band** | **5** |
| **MEMORY** | T1 手动 Save-to-Memory | `memory_extractor.py:366` | 人点按钮,**内容 100% LLM 选** | confident-only prompt(软)+dedup | ❌ | 3 |
| **MEMORY** | T5 s_persist | `s_persist/SKILL.md` | agent-initiated | prose Step-0 gate | ❌ | 2 |
| **EVOLUTION** | ⚠️自动 _write_corrections/_write_competence | `distillation_hook.py:598,607` | **全自动每次 session close** | **无:仅 regex+长度**(currently input-starved) | ❌ | **5** |
| **EVOLUTION** | evolution_optimizer 日志 | `evolution_optimizer.py:935` | 自动(weekly job) | conf 0.15 deploy | ❌ | 4 |
| **EVOLUTION** | s_persist correction | `s_persist/SKILL.md:94` | agent-initiated | prose Step-0 | ❌ | 1 |
| **KNOWLEDGE** | s_persist reference | 实际 redirect 到 `Knowledge/Library/` | agent-initiated | prose Step-0 | ❌ | 1 |
| **KNOWLEDGE** | index 刷新 | `context_health_hook.py:696` | 自动 | 只改机器 index 段,非知识准入 | n/a | 5 |

## 2. 关键发现(超出原问题的)

### 发现 A — 有 4 条自动无人在场(rating 5)的 ingestion,只有 0 条有对抗审查
最高风险的四条:MEMORY 自动 distillation、EVOLUTION 自动 _write_corrections、DDD orchestrator
refresh(还 auto-commit)、DDD 的 T3 writeback。**这四条恰恰是最该有 judge 的(全自动、无人、刮
prose),却一条都没有。** self_adversarial judge 设计覆盖的是 admission_band——而这四条里有三条
**绕过 admission_band**。

### 发现 B — admission_band 不是 DDD 的唯一入口(上一轮我漏了)
我上一轮说"DDD 走 admission_band"是**不完整**的。code-verified:`improvement_writeback_hook`
和 orchestrator 的 refresh channels **直调 `apply_to_ddd`,绕过 admission_band + trust**
(`apply_to_ddd:804` 明确不 re-check trust)。所以"trust 是唯一权威"这个不变量**只对
_cultivate_proposals 那一条 band 路径成立**,对这些直写路径不成立。历史 1990 条 applied/reflect
很可能就是走这些 bypass 路径进的。

### 发现 C — MEMORY 噪音率 ~35-45%,是四个 store 里最高的
采样证据:MEMORY.md 自动 distillation 的短 bullet 大量是**截断的碎片**(`md (cultivation:...)`
起手是砍掉的文件名;`Confirmed the regression` 三词无内容;mid-quote 起手的孤儿句)。频次门+
supersede 减了量但拦不住"语法碎片但 pattern-match 命中"的行。对比:手动 `source:manual` 的
`[decision]`/`[guideline]` 段几乎全高价值。**噪音密度最高的路径 = 无人在场 rating 最高的路径**,
双重该上 judge。

### 发现 D — EVOLUTION 自动写路径"armed but starved"
`_write_corrections` 每次 session close 自动 append `### C0xx` 到 EVOLUTION,**无任何 gate**。
现在没爆发是因为它只从 DailyActivity 里 `- ` bullet 提取,而现网 DA 是 `**Corrections:** <count>`
计数头格式,提不出东西。**但这是格式偶然,不是设计护栏**——一旦某个 DA 带了 bullet 化 corrections
(或 JSONL sidecar 的 corrections 数组被填),就绕过一切直接自动写进 EVOLUTION 认知底座。这是
agent-owned store(R21)里最危险的隐性自动路径。

### 发现 E — EVOLUTION 文件写入 ≠ Intake Gate 覆盖
人类 Intake Gate 只 gate SOUL/AGENT/STEERING 的**提升**,**不 gate EVOLUTION.md 文件写入**
(EVOLUTION 是 L3 "self-record, no deploy",文件刻意在治理门之外)。所以 EVOLUTION.md 的准入
只靠 distillation regex + agent 判断,没有治理门兜底。

## 3. 分级判断 — 哪些该上 self_adversarial judge

判据:**自动化程度 × 噪音密度 × 现有闸强度 × 无人在场**。judge 该装在"自动、无人、噪音多、
现无对抗审查"的路径上;不该无差别铺开(避免 C042)。

| 路径 | 该上 judge? | 判据 |
|---|:---:|---|
| **MEMORY 自动 distillation** | ✅ **最该** | rating 5 + 噪音 ~40% + 无 judge + 进系统 prompt 核心。四条里优先级第一 |
| **EVOLUTION 自动 _write_corrections** | ✅ **该(+先堵格式洞)** | rating 5 + 无任何 gate + agent-owned 认知底座。先修"armed but starved"隐患,再上 judge |
| **DDD orchestrator refresh(auto-commit)** | ✅ 该 | rating 5 + 绕过 admission_band + 还自动 commit |
| **DDD T3 improvement_writeback** | ✅ 该 | 绕过 admission_band/trust,直调 apply_to_ddd |
| **DDD cultivation band** | ✅ 已设计 | run_8dea0dd5 已覆盖 |
| MEMORY 手动 Save-to-Memory | 🟡 可选 | rating 3,内容 LLM 选但人在场触发;judge 有价值但非最急 |
| MEMORY s_persist / EVOLUTION s_persist | ❌ 不该 | rating 1-2,agent 主动沉淀、有人在场、信号密度高。judge 是负担不是护栏 |
| KNOWLEDGE index 刷新 | ❌ 不该 | 只改机器 index 段,非知识准入 |

## 4. 真正的 root(比"给每条加 judge"更深)

零散给每条路径加 judge 会重演碎片化。真正的 root 是:**认知系统缺一个统一的 ingestion 准入层。**
现在是 4 个 store × N trigger 各写各的闸(有的 admission_band、有的 confident-only prompt、有的
regex、有的 prose Step-0、有的什么都没有、还有的绕过自己的主闸)。

两个层次的修法(供后续决策,不在本 research 范围内实现):
- **窄修**:按 §3 分级,给 4 条高风险自动路径接上 self_adversarial judge(judge helper 本就
  store-无关,输入是 text+neighbors)。先堵 EVOLUTION 格式洞(发现 D)。
- **根修**:抽一个统一 `ingestion_gate(text, store, trigger, context)` 层,所有 store 的所有
  trigger 都过它;judge 是其中一档,noise/dedup/trust/confident 是其它档;每条 trigger 声明
  自己需要哪几档。这是"每次改认知系统同时考虑所有线"这条 principle 的**结构化落地**——有了
  统一层,改一条不会漏其它条。

## 5. 兜底完整性(AC5)
从三条已知线出发 + grep 所有 store 的 locked_write/apply/append caller,确认认知 store 是
4 个(DDD 4文档 / MEMORY / EVOLUTION / KNOWLEDGE),无第 5 条被漏的独立 ingestion 路径。
(DailyActivity / Signals / JobResults 是 raw log,非"经准入的认知 store",不在此列。)

## 附:这条 principle 的证据
本 research 本身就是 principle "每次做认知系统的 change,DDD/MEMORY/EVOLUTION/KNOWLEDGE 这几条线
必须同时考虑,否则越来越乱" 的实证:我上一轮只改了 DDD 一条(self_adversarial judge),就漏看了
(a) DDD 自己还有 3 条 bypass 路径,(b) MEMORY/EVOLUTION 的自动路径噪音更高却无闸。单改一条 =
准入标准漂移 = 越来越乱。principle 成立。
