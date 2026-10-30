---
title: "AI-Native Transformation —— 打造你的复利组织 (人+AI Agents)"
created: 2026-09-07
updated: 2026-09-11
status: published
---
<!-- GitHub Discussion #106: https://github.com/xg-gh-25/SwarmAI/discussions/106 -->

这份 deck 讲一件事:**AI-Native transformation 不是换工具,是换范式**。

但在讲怎么做之前,得先把"AI-Native 到底指什么"说清楚 —— 这个词现在的理解五花八门,网上的解释也大多不对版。所以先摆三个问题:**目标在哪 → 现在困在哪 → 怎么走过去**。

### 先说目标:AI-Native 组织 = 人 + AI Agents 的新型组织，AI Agents 是新的组织成员。(个人观点)

**Agents 是新的组织成员。** 一旦承认这一点,就有三件事必须重新定义 —— 这也是整份 deck 的骨架:

| 要重新定义的 | 具体是什么 |
|---|---|
| **① 角色** | Agents 从"工具"变成**执行与交付的成员**;人从"执行者"变成**判断者与监督者** |
| **② 责任 = 边界** | 责任落在谁身上,那个角色的边界就到哪里 —— 谁定义"对"、谁签字、**谁对 AI Agent 的结果负责**。举两个例子:<br>**Full-Stack Engineer** —— 责任是**指挥 AI 产出代码 + 借助 AI 守住 Quality 与 Security 底线**<br>**PM** —— 责任是**借助 AI 快速定义做什么、不做什么,Prototype 快速验证,高效协调对齐** |
| **③ Workflow** | 双向嵌套,不是单向流水线:**Human in the Agents loop**(Agents 主导执行,人在关键节点判断、审批、接管)× **Agents in the Human loop**(人主导的流程里,Agents 参与协调、评估、验证) |

#### 但更重要的是:什么**不变**

角色、责任、workflow 都要重新定义 —— 但**定义一个 builder 的,是不变量,不是工具**。

> **Coding is becoming cheaper, engineering judgment more valuable.**

工具会变、模型会变、方法会变;**LP + EE / OE → Outcome 这个内核不变**,而且在 AI 时代要求**更高**,不是更低:

| 不变的内核 | AI 时代为什么要求更高 |
|---|---|
| **Outcomes define success** | 对 Customers / Business / Team 的长期结果 —— 产出量暴增之后,唯一还能区分好坏的就是它 |
| **强化的 LP** | Learn & Be Curious · Ownership · Customer Obsession · Deliver Results —— Agents 能执行,但**问题是不是真问题、要不要做**只有人能担 |
| **强化的 EE / OE** | Technical Judgment · Hands-on Engineering · Production Quality —— 代码由 AI 产出以后,**判断它对不对、扛不扛得住生产**成了核心能力,而不是可选项 |

**这就是为什么 EE / OE 在这份 deck 里占了整整一幕(T4)**:AI 让"造得快"变便宜,于是"造得对、运营得住"成了唯一的护城河。**门槛不是降低了,是转移了 —— 从写代码转移到工程判断力上。**

> **速度不是目的,是结果。**
>
> 整个转型要优化的是 **outputs / outcomes**,然后用 **working backwards** 的方式重新 design 人 + AI Agents 的 workflow —— 想清楚哪些地方是 `Human in the Agents loop`、哪些地方是 `Agents in the Human loop`。
>
> **不要把精力放在优化 input 指标**(AI 采纳率、生码率、PR 数)。真正要建的是一个**人指挥、Agents 交付**的组织,并让判断力沉淀进系统、可复利 —— 速度是这件事做对了以后自然长出来的。

| | 不是 AI-Native | 是 AI-Native |
|---|---|---|
| **AI 的身份** | 一个更快的工具(Copilot 补代码) | **组织的新成员** |
| **人的角色** | 人做全部执行,AI 打辅助 | 人做**判断与监督**,Agents 做执行与验证 |
| **责任与边界** | 按职能分工,逐环交接 | **按责任重划** —— Engineer 端到端(含 Quality/Security 底线)、PM 端到端(定义 + 验证 + 对齐) |
| **判断力在哪** | 锁在个人脑子里,人走即失 | **沉淀进系统**,每个 agent 都能继承 |
| **量什么** | 用了多少 AI(input) | 交付了什么(output / outcome) |
| **质量与安全底线** | 速度的代价 —— 先交付,出事再补 | 速度的**前提** —— 跟速度同步上线,守不住就毫无意义 |
| **300 天后** | 还是 ~2x(租来的工具) | **10x**(建起来的资产) |

一句话:**Human Directs, AI Delivers.**

### 再说困在哪:2x 是结构性天花板,不是努力不够

卡住不是因为工具不行,是**瓶颈转移了**而组织没跟着变 —— Agent 把中间那段 coding 打掉了(约 30% 链路),瓶颈移向**两头**:上游"定义对错 + 跨角色协调",下游"验证质量 + 守住安全"。这两头不是简单引入一个工具能解的。

所以只追速度会走进死路:上游没有 Spec 纪律,AI 越快越漂;下游没有质量与安全底线,越快越危险。**第 1 天 2x 和第 300 天还是 2x,差别就在这段时间里有没有东西沉淀下来。**

### 最后才是怎么走:下面 31 页

从"为什么还是 <2x"开始,一路推到"怎么让飞轮转起来"。

## 主线:为什么必须是这五步,而且一步都不能少

先给一条**推导链** —— 每一步都是上一步逼出来的,不是并列的话题:

```
Agent 把 coding 做便宜了
        ↓  于是瓶颈不在 coding 了,移到两头(定义 + 验证)          → T1 定义问题
        ↓  瓶颈在两头 = 组织问题,得先知道自己缺哪一块、拿什么衡量   → T2 管理与准则
        ↓  知道了要改什么,才能重建交付方式:Spec 立规矩、判断力进系统 → T3 AIDLC
        ↓  交付变快之后,质量与安全成为唯一的瓶颈和唯一的护城河      → T4 EE / OE
        ↓  快 + 稳都做到了,让每一轮的教训沉淀下来,才有复利         → T5 飞轮
```

**主线任务只有一句:把组织从"用 AI 提效(~2x)"变成"人 + Agents 的复利系统(Nx)"。** 五个 Theme 各解决这条主线上的一个必要环节:

| Theme | 它在主线上解决什么 | 删掉它会怎样 |
|---|---|---|
| **T1 定义问题** | **诊断** —— 瓶颈到底在哪、我们在哪一格 | 拿着答案找问题:上工具、量采纳率,撞在 2x 上还不知道为什么 |
| **T2 管理与准则** | **共同语言与度量** —— 改什么、按什么顺序、拿什么衡量 | 各团队各自摸索,没有统一度量就无法判断"到底有没有进步" |
| **T3 AIDLC** | **交付方式重建** —— Spec 立规矩 + 判断力沉淀进系统 | 组织认知到位但交付方式没变,快不起来 —— 光有共识不产出 |
| **T4 EE / OE** | **底线** —— 质量与安全跟上速度 | 越快越危险:交付频率涨了,安全逃逸同步暴涨,速度作废 |
| **T5 飞轮** | **复利** —— 让每一轮的教训变成下一轮的能力 | 第 1 天 2x,第 300 天还是 2x —— 你租了工具,没建成资产 |

**哪个最关键?** 如果只能选一个:**T3 里的"判断力沉淀进系统"(DDD)**。因为 T1 是一次性诊断、T2 是共识、T4 是底线 —— 它们都**不产生复利**;只有判断力进了系统,后面每一轮才会比前一轮更强,T5 的飞轮才有燃料。

**贯穿五步的那条不变线:** 五个 Theme 讲的都是"要改什么",但整条主线是被同一个**不变量**锚住的 —— `LP + EE/OE → Outcome`。T1 的诊断、T2 的度量、T3 的判断力、T4 的底线、T5 的复利,衡量它们的标准始终是**长期 outcome**,而执行这些判断的能力始终是**工程判断力**。**这也是 T4 为什么不是"最后的补丁"而是承重墙:它就是那个不变量在 AI 时代的具体形态。**

**只做这些就够了吗?** 不够 —— 这五步是**必要条件,不是充分条件**。它管的是"组织与交付方式";它管不到的至少还有:业务判断本身对不对、模型/算力的成本与选型、数据治理与合规、以及人的意愿(愿不愿意把执行交出去)。这份 deck 只解一个问题:**当 AI 能执行了,组织和交付方式该怎么改。**

---

## 五个 Theme 分别讲什么

| Theme | 讲什么 | 页 |
|---|---|---|
| **T1 · 定义问题** | 瓶颈没消失,它从 coding 移向两头(定义 + 验证);S×T 矩阵诊断个人能力与组织就绪度的**错配** | 3–5 |
| **T2 · Management & Guideline** | 五大支柱是**依赖链不是菜单**;P0 Metrics 量 output/outcome 不量 input;Builder Role Guideline;三阶段演进 | 6–10 |
| **T3 · AIDLC Evolution** | 全链条 Spec-Driven → 它的天花板 → **DDD as Brain**(让判断力进系统) → Autonomous Pipeline(9 Stages · 3 Gates) → Agentic OS | 11–19 |
| **T4 · Builder EE / OE** | 守得住质量与安全的底线:SecDLC · Eval-First · 7×24 AI 值守 —— **守不住底线,速度毫无意义** | 20–27 |
| **T5 · Then …** | 双飞轮:技术复利 × 组织复利 —— 第 300 天不是 2x,是 Nx | 28–31 |

## 几个有张力的点

1. **Spec-Driven 规范了纪律,但没改变组织与沉淀判断力。** Spec 让 AI 忠于合约,可判断力还锁在个人脑子里 —— 不 scale、不沉淀、人走即失。`AGENTS.md` / System Specs 解决的是**导航**,不是**判断**。
2. **判断力必须成为系统的一部分(DDD)。** 各角色共养同一份 DDD:PM 养 PRODUCT、Engineer 养 TECH、QA 养 IMPROVEMENT、TPM/UXD 养 PROJECT —— 组织转型与 AI 赋能是**同一个动作**。
3. **知识治理的核心竞争力不是"记住",是"遗忘"。** 7 类 × 3 认知层的轻量 ontology + 达尔文式衰减:引用即自然选择,分类决定半衰期。不上 VectorDB / GraphDB,几份 markdown 就够。
4. **单 Agent 角色切换 > multi-agent 编排。** 更多 agent = 判断力更差 + 上下文在 agent 间丢失;拆判断就是拆出裂缝,编排开销吃掉收益。
5. **input 指标只能当指南针,不能当靶子。** LoC / PR 数 / AI 采纳率都是 input;要负责的是 output / outcome。`Measure ≠ Target`。
6. **Autonomous 从哪开始有明确判据** —— 输入边界清晰、有安全网、风险可控的地方(bug fixing / AutoOps / AutoSecurity),而不是开放式的 coding 通道。

deck 里提到的机制大部分在这个 repo 里能看到实现,相关 Discussion 也在:[AI-Ready Repo 标准](https://github.com/xg-gh-25/SwarmAI/discussions/51) · [DDD Knowledge Governance](https://github.com/xg-gh-25/SwarmAI/discussions/59) · [Coding as Black Box](https://github.com/xg-gh-25/SwarmAI/discussions/4) · [为什么选 Single-Agent](https://github.com/xg-gh-25/SwarmAI/discussions/43) · [Ontology 是决策层](https://github.com/xg-gh-25/SwarmAI/discussions/100)

## 几个开放探讨的点

1. **"AI-Native"你怎么定义?** 我给的是"人 + AI Agents 的新型组织 —— Agents 是新成员,角色、责任(= 边界)、workflow 全都要重新定义"。如果你的定义落在"用 AI 提效"这一层,差别在哪?
2. **判断力真能沉淀吗?** DDD 让专家判断进系统、被 agent 继承 —— 但一份团队共养的知识层,会不会最后腐烂成没人维护的 wiki?我的答案是达尔文式衰减(引用即自然选择),但这只在**有足够引用量**时成立。小团队够吗?
3. **五大支柱真的不能跳吗?** 我的主张是 Top-down 先建地基(文化 · 度量 · 就绪度),跳到 4-5(Spec · Autonomous)就是空转。但现实里大多数团队是**先上工具见到效果**才有预算谈组织 —— 这个顺序矛盾怎么解?
4. **Agents 当成员之后,责任怎么定?** 谁对 AI Agent 的**结果**负责 —— 下指令的人?审的人?还是团队?我的主张是**用责任反推边界**(责任落在谁身上,那个角色的边界就到哪里),但落到具体组织里,签字权和问责权到底怎么划?这是三个重定义里最没标准答案的一个。
5. **两个 loop 怎么共存?** `Human in the Agents loop`(Agents 主导执行,人在关键节点判断/审批/接管)和 `Agents in the Human loop`(人主导流程,Agents 参与协调/评估/验证)—— 一个组织里两种都会存在,但**什么工作该走哪一个**、切换的判据是什么?更难的是:同一件事在不同阶段可能要换 loop,这个切换谁来判断?
6. **门槛是降低还是提高了?** 我的主张是**转移**而非降低 —— coding 变便宜,工程判断力(EE/OE)变成核心。但这意味着**新人更难入门**了:过去靠写代码练判断力,现在代码由 AI 产出,新人从哪练?这条我没有好答案。
7. **单 Agent 还是 multi-agent?** 我选单 agent 角色切换,理由是编排开销吃掉收益、判断力被拆散。这条最容易有分歧。
8. **组织跟不上时,先推谁?** S×T 矩阵的处方是 `S>T → 推组织`、`T>S → 拉个人`。但最危险的格子是 `S3+T2`(最强的人被流程卡住,最先离开)—— 这种情况下你会先做什么?
9. **Autonomous 从哪开始?** 我的判据是输入边界清晰 + 有安全网 + 风险可控(bug fixing / AutoOps / AutoSecurity)。有人从别的地方切入成功过吗?


---

## 全部 31 页

### 1. 封面 · AI-Native Transformation

![Slide 1 — 封面 · AI-Native Transformation](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/01-Cover.png)

***

### 2. Agenda —— 一条主线,四个 Theme

![Slide 2 — Agenda —— 一条主线,四个 Theme](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/02-Agenda.png)

***

### 3. T1 · 定义问题

![Slide 3 — T1 · 定义问题](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/03-T1-Divider.png)

***

### 4. 瓶颈没有消失 —— 它转移了

![Slide 4 — 瓶颈没有消失 —— 它转移了](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/04-Bottleneck-Shift.png)

***

### 5. S×T 诊断矩阵 —— 转型失败是错配

![Slide 5 — S×T 诊断矩阵 —— 转型失败是错配](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/05-SxT-Matrix.png)

***

### 6. T2 · Management & Guideline

![Slide 6 — T2 · Management & Guideline](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/06-T2-Divider.png)

***

### 7. 五大支柱 —— 是依赖链,不是菜单

![Slide 7 — 五大支柱 —— 是依赖链,不是菜单](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/07-Five-Pillars.png)

***

### 8. P0 Metrics —— Output / Outcome

![Slide 8 — P0 Metrics —— Output / Outcome](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/08-P0-Metrics.png)

***

### 9. Builder Role Guideline 2026

![Slide 9 — Builder Role Guideline 2026](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/09-Builder-Role-Guideline.png)

***

### 10. 三阶段演进 —— 每阶段改变的是人的角色

![Slide 10 — 三阶段演进 —— 每阶段改变的是人的角色](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/10-Three-Phases.png)

***

### 11. T3 · AIDLC Evolution

![Slide 11 — T3 · AIDLC Evolution](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/11-T3-Divider.png)

***

### 12. 全链条 Spec-Driven —— Spec 是唯一锚点

![Slide 12 — 全链条 Spec-Driven —— Spec 是唯一锚点](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/12-Phase2-Spec-Driven.png)

***

### 13. Spec-Driven 的天花板

![Slide 13 — Spec-Driven 的天花板](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/13-The-Ceiling.png)

***

### 14. DDD as Brain —— 让判断力进系统

![Slide 14 — DDD as Brain —— 让判断力进系统](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/14-DDD-as-Brain.png)

***

### 15. Data Agent Sample —— DDD 让 AI 读懂数据

![Slide 15 — Data Agent Sample —— DDD 让 AI 读懂数据](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/15-DDD-for-Data.png)

***

### 16. DDD Cultivation —— Ontology + 达尔文

![Slide 16 — DDD Cultivation —— Ontology + 达尔文](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/16-DDD-Cultivation.png)

***

### 17. Coding Autonomous Pipeline —— 9 Stages + 3 Gates

![Slide 17 — Coding Autonomous Pipeline —— 9 Stages + 3 Gates](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/17-Autonomous-Pipeline.png)

***

### 18. 四大机制 · 三道 Gate 缺一不可

![Slide 18 — 四大机制 · 三道 Gate 缺一不可](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/18-Pipeline-Mechanisms.png)

***

### 19. Agentic OS —— 一层知识,多个交付引擎

![Slide 19 — Agentic OS —— 一层知识,多个交付引擎](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/19-Agentic-OS.png)

***

### 20. T4 · Builder EE / OE

![Slide 20 — T4 · Builder EE / OE](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/20-T4-Divider.png)

***

### 21. EE/OE 贯穿全链路

![Slide 21 — EE/OE 贯穿全链路](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/21-EEOE-Chain.png)

***

### 22. P0 指标却在破防

![Slide 22 — P0 指标却在破防](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/22-EEOE-Background.png)

***

### 23. Amazon 靠 EE/OE 确保造得对和运营好

![Slide 23 — Amazon 靠 EE/OE 确保造得对和运营好](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/23-EEOE-Framework.png)

***

### 24. EE/OE —— 编译进 AI-DLC 的每一环

![Slide 24 — EE/OE —— 编译进 AI-DLC 的每一环](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/24-EEOE-TheHow.png)

***

### 25. SecDLC —— 把安全编译进开发生命周期

![Slide 25 — SecDLC —— 把安全编译进开发生命周期](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/25-EEOE-SecDLC.png)

***

### 26. Eval-First —— Golden Case 贯穿全生命周期

![Slide 26 — Eval-First —— Golden Case 贯穿全生命周期](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/26-EEOE-Eval.png)

***

### 27. 7×24 AIxEngineers —— 把部分 Operations 交给 AI

![Slide 27 — 7×24 AIxEngineers —— 把部分 Operations 交给 AI](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/27-EEOE-Operations.png)

***

### 28. T5 · Then …

![Slide 28 — T5 · Then …](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/28-T5-Divider.png)

***

### 29. 双飞轮 —— 技术复利 × 组织复利

![Slide 29 — 双飞轮 —— 技术复利 × 组织复利](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/29-Dual-Flywheel.png)

***

### 30. 一个 Builder,服务两条线

![Slide 30 — 一个 Builder,服务两条线](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/30-Where-We-Are.png)

***

### 31. 从今天开始 —— Build 你的复利循环

![Slide 31 — 从今天开始 —— Build 你的复利循环](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/decks/ai-native-v10/31-Call-to-Action.png)

***

