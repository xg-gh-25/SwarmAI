---
title: "外界怎么解读 AI-Native？—— Transformation 是演进，不是一步到位（#106 的对外解读）"
created: 2026-09-07
updated: 2026-09-11
status: published
---
<!-- GitHub Discussion #108: https://github.com/xg-gh-25/SwarmAI/discussions/108 -->

[#106](https://github.com/xg-gh-25/SwarmAI/discussions/106) 发出来之后，有人问了一个好问题：**外界都在谈 AI-Native，你们和他们说的是同一件事吗？**

我把外界的解读扫了一圈（咨询、工程、VC、组织研究、中文语境，出处附在文末）。结论是：**大部分人在回答"AI-Native 长什么样"，很少有人回答"一个已经跑着业务的组织怎么走到那里"。**

而后者才是难题。所以这篇不做流派对比 —— 我把 #106 的主线摆出来，**外界的每一条证据都挂在这条链的某一环上**：要么给它背书，要么对它施压。施压的那几条我会明确说"这条我们没有答案"。

---

## 一、先定义 AI-Native，否则各说各话

这是必须放在最前面的一步。"AI-Native" 在外部语境里理解极度分散，不先定义，后面每句话都会被翻译成别的意思。

> **AI-Native 组织 = 人 + AI Agents 的新型组织。Agents 是新的组织成员。**

承认这一点，就有**三件事必须重新定义**：

| # | 重新定义什么 | 内容 |
|---|---|---|
| ① | **角色** | Agents 从"工具"变成**执行与交付的成员**；人从"执行者"变成**判断者与监督者** |
| ② | **责任 = 边界** | 责任落在谁身上，那个角色的边界就到哪里 —— 谁定义"对"、谁签字、**谁对 AI Agent 的结果负责**。举例：Full-Stack Engineer = 指挥 AI 产出代码 + 借助 AI 守住 Quality/Security 底线；PM = 借助 AI 快速定义做什么/不做什么 + Prototype 快速验证 + 高效协调对齐 |
| ③ | **Workflow** | **双向嵌套，不是单向流水线**：`Human in the Agents loop`（Agents 主导执行，人在关键节点判断/审批/接管）× `Agents in the Human loop`（人主导流程，Agents 参与协调/评估/验证） |

**外界给的是终态判定，不是组织定义。** 两个流传最广的测试都很好用：

- IBM：**"如果把 AI 拿掉，产品不是降级，而是彻底失去存在意义"**（反例：浏览器里加了个 AI 朗读功能）
- CRV：**"模型变强的时候，你是高兴还是难过？"** 模型进步自动让产品更值钱 = AI-native；模型进步威胁你已建的东西 = 你只是包了一层壳

这两个测试的优点是**不可自我美化**。但它们判定的是**产品架构**，不是**组织**；而且它们是给从零开始的公司用的 —— 一家 10 年历史的公司做这个测试，答案必然是"现在不 native"，然后呢？测试不回答"然后呢"。

IBM 自己还说了句很老实的话：这个词大量被当营销词滥用，且**随着 AI 普及，它的效用会逐渐消失**。

---

## 二、调性：速度不是目的，是结果

> **速度不是目的，是结果。**
> 整个转型要优化的是 **outputs / outcomes**，然后用 **working backwards** 的方式重新 design 人 + AI Agents 的 workflow —— 想清楚哪些地方是 `Human in the Agents loop`、哪些地方是 `Agents in the Human loop`。
> **不要把精力放在优化 input 指标**（AI 采纳率、生码率、PR 数）。真正要建的是一个**人指挥、Agents 交付**的组织，并让判断力沉淀进系统、可复利 —— 速度是这件事做对了以后自然长出来的。

**外界从反面证明了这一点。** 两条独立证据：

- HBR 指出，**多数"AI 裁员"其实是 AI-washing** —— 普通重组套上 AI 话术安抚投资人。高盛估算 AI 过去一年只压低美国月均就业增长约 **1.6 万人**、失业率 **+0.1pp**。
- 技术社区的判断更锋利：**"宣传自己 AI 驱动，就像宣传自己用云。说你用什么技术，不如说你提供什么价值。"**

把这两条放在一起，会得到一个很有用的检测器：**AI-washing 的技术特征，就是只能出示 input 指标**（采纳率、token 数、席位数）。所以"**你在量什么**"本身就是最好的 AI-washing 检测器。

反过来对我们也成立：我们哪天开始拿采纳率汇报，我们就是被批评的那一类。

---

## 三、什么**不变** —— 这一节缺了，整件事会被读成"门槛降低了"

这是最容易漏的一节。只讲"什么要重新定义"，听众会得出反向结论：AI 时代对人要求变低了。

> **定义一个 builder 的，是不变量，不是工具。**
> **Coding is becoming cheaper, engineering judgment more valuable.**

`LP + EE/OE → Outcome` 这个内核不变，而且在 AI 时代要求**更高**：

| 不变的内核 | 为什么要求更高 |
|---|---|
| **Outcomes define success** | 对 Customers / Business / Team 的长期结果 —— 产出量暴增后，唯一还能区分好坏的就是它 |
| **强化的 LP** | Learn & Be Curious · Ownership · Customer Obsession · Deliver Results —— Agents 能执行，但**问题是不是真问题、要不要做**只有人能担 |
| **强化的 EE / OE** | Technical Judgment · Hands-on Engineering · Production Quality —— 代码由 AI 产出后，**判断它对不对、扛不扛得住生产**成了核心能力，不是可选项 |

**门槛不是降低了，是转移了 —— 从写代码转移到工程判断力上。**

**外界唯一同调的一家是 Thoughtworks**，而且它是主动给这波降温的：

> **"机会不是全自主开发，而是 AI-enabled core renewal"** —— 用 AI 重建软件交付的核心（现代化遗留系统、架构完整性、质量、流水线稳定性），而不是自动化任务。
> AI 必须在**严格工程监管 + human-in-the-loop** 下运行，否则会复合技术债、引入安全漏洞、产出幻觉需求。

这就是我们 T4（EE/OE 是速度的前提而非代价）在外界的独立版本。差别是：它说"要有监管"，没说监管**怎么随阶段变化**。

---

## 四、主线：每一步都是上一步逼出来的

```
Agent 把 coding 做便宜了
  ↓ 瓶颈不在 coding 了，移到两头(定义 + 验证)        → T1 定义问题
  ↓ 两头是组织问题，先知道缺哪块、拿什么衡量           → T2 管理与准则
  ↓ 知道要改什么，才能重建交付方式                    → T3 AIDLC
  ↓ 变快之后，质量与安全成为唯一瓶颈和唯一护城河        → T4 EE / OE
  ↓ 快 + 稳都有了，让教训沉淀下来才有复利              → T5 飞轮
```

**主线任务一句话：把组织从"用 AI 提效（~2x）"变成"人 + Agents 的复利系统（Nx）"。**

而且这是**演进，不是一步到位**：Phase 1（人驱动·AI 辅助，`<1.5x`）→ Phase 2（人决策·AI 执行，Spec-Driven，`2x+`）→ Phase 3（人监督·AI 自主，DDD+SDD+TDD，`4x+`），且 **Phase 2 与 Phase 3 并行推进**；底下是四级递进 **Spec-Driven → DDD → Autonomous → Agentic OS**，**跳层 = 地基不稳**。

### 每个 Theme 解决什么，删掉会怎样

| Theme | 在主线上解决什么 | 删掉它会怎样 | 外界证据 |
|---|---|---|---|
| **T1 定义问题** | **诊断** —— 瓶颈在哪、我们在哪一格 | 拿着答案找问题：上工具、量采纳率，撞在 2x 上还不知道为什么 | ✅ 见下方"最强背书" |
| **T2 管理与准则** | **共同语言与度量** —— 改什么、什么顺序、拿什么衡量 | 各团队各自摸索，没统一度量就无法判断"到底有没有进步" | 🟡 BCG 说要重设计运营模型，但不给阶段顺序 |
| **T3 AIDLC** | **交付方式重建** —— Spec 立规矩 + 判断力沉淀进系统 | 组织认知到位但交付方式没变 —— 光有共识不产出 | ⚠️ **workslop 是跳过这一步的后果**（见下） |
| **T4 EE / OE** | **底线** —— 质量与安全跟上速度 | 越快越危险：交付频率涨了，安全逃逸同步暴涨，速度作废 | ✅ Thoughtworks（§3） |
| **T5 飞轮** | **复利** —— 每轮教训变成下轮能力 | 第 1 天 2x，第 300 天还是 2x —— 租了工具，没建成资产 | ✅ 5% / 95% 两组数（见下） |

**T1 最强的外界背书**，来自一位组织学者的实地观察：他两年内谈了约 **300 位大企业领导，没有一个拿得出 robust 的 AI transformation** —— 登录数、token 用量、use case 都有，局部 ROI 也有，但"为什么没看到更好的软件？"

他引的这句，是对"瓶颈转移"最精确的表述：

> **"加速网络里的节点（原岗位上的个体），通常不会改善网络本身的性能 —— 它只是把瓶颈挪走，往往挪到地下。网络才是约束。"**

**这就是 Phase 1 的天花板被实证了**：Phase 1 = 个人自由发挥、Copilot 补代码、只覆盖 30% lifecycle。300 位领导都卡住，是整个行业卡在 Phase 1 的证据。

两组数字给 T5 定了性：BCG 说**只有 5% 的企业拿到规模化价值**；MIT 那份报告说 **95% 的 GenAI pilot 没有可衡量财务回报**（这个 95% 后来被指出有夸大/误读，但方向没人反驳）。

**而 workslop 正好是跳过 T3 的后果**：HBR 记录了这个现象 —— AI 生成的"看似完整、实则无用"的产出，反向摧毁生产力。上游没有 Spec 纪律就让 AI 大量产出，产出的是需要别人返工的东西。

**一个关键观察：外界的反方，没有一条在反对"要演进"，它们反对的全是"跳过演进"。**
- 95% pilot 失败 = 停在 Phase 1，无机制、无方法论
- AI-washing = **假装已经到了 Phase 3**（只能出示 input 指标）
- workslop = 跳过 Spec 纪律直接放量

用 #106 的话说，这是一份"**跳层 = 地基不稳**"的实证清单。反方是我们的证据，不是对手。

---

## 五、哪个最关键：T3 里的"判断力沉淀进系统"（DDD）

必须给答案，不含糊：**T1 是一次性诊断、T2 是共识、T4 是底线 —— 它们都不产生复利。只有判断力进了系统，后面每一轮才比前一轮强，T5 的飞轮才有燃料。**

这也是外界唯一真正空着的一格。有四处很接近，但沉淀的都是**别的东西**：

| 谁 | 沉淀了什么 | 差在哪 |
|---|---|---|
| 咨询侧 | "institutional knowledge 应成为 living enterprise asset" | 一句话，没有机制 |
| 组织研究侧 | "我们缺一把尺子" | 他要量的是**技能养成**，不是判断力沉淀 |
| 中文技术侧 | Spec-Driven Development + 规格文档成为核心资产 + Context Engineering | 沉淀**规格**（做什么），不是**判断**（为什么这样判、上次错在哪） |
| 一家车企的实践 | 沉淀 3 万多个业务单元 → 转成智能体可调用的 Skill | 沉淀**能力 / 流程**，不是判断力 |

**为什么这不是文字游戏**：Spec 和 Skill 沉淀的是"该做什么、怎么做"，写一次然后照着执行。判断力沉淀的是"**上一次我们判错了什么，于是这一类错误从此不许再发生**" —— 它必须能被**下一次交付强制读到**，否则就是一份没人维护的 wiki。

这正是 #106 的核心主张：`AGENTS.md` / System Specs 解决的是**导航**，不是**判断**。而 DDD 的关键在于它**一个动作解两件事** —— 各角色共养同一份 DDD（PM 养 PRODUCT、Engineer 养 TECH、QA 养 IMPROVEMENT），**人养的 DDD 就是 AI 用的 DDD，同一份东西**。所以组织转型与 AI 赋能是同一个动作，不是两件事。

顺带说一个中文语境里已经出现的、和这条同向的实践 —— **角色牵引**而非等员工自发转型：产品经理 → AI 产品经理，前后端开发 → 智能体架构师。同一家车企还给了一条很硬的治理判断：AI 审批替代人工审批后，追责逻辑变了（追业务人还是追 AI 训练者？），他们的原则是"只要模型准确率超越人类平均水平就应该上线"，**管理中心从"单次审批"转向"审批模型的维护"**。这是我见过对"责任=边界"最实的落地形态。

---

## 六、只做这些够吗？—— 不够

这五步是**必要条件，不是充分条件**。管不到的至少还有：**业务判断本身对不对、模型/算力的成本与选型、数据治理与合规、以及人的意愿**（愿不愿意把执行交出去）。#106 只解一个问题：**当 AI 能执行了，组织和交付方式该怎么改。**

**① "AI 不减少工作，它加剧工作"**（HBR）。如果人从执行者变成判断者，而判断量随 AI 产出量线性膨胀，人的负担是**上升**的 —— "Nx" 就可能是把执行的苦换成审阅的苦。我们目前的对策 1、在**降低单次判断成本**这个方向（gate 自动化、Eval-First、pipeline 只在关键节点要人），2、把输入边界清晰 + 有安全网 + 风险可控(bug fixing / AutoOps / AutoSecurity)的尽量自动化或者Autonomous。

**② 判断力沉淀如果不带审计与追责，它进不了核心流程。** 一份来自企业软件侧的观察给出了三个鸿沟：**AI 没有你的业务知识 / AI 看到的信息是碎片化的（ERP、CRM、群聊、三方系统各存一块）/ 缺乏 AI 治理架构 —— 操作无法审计、无法追责，就永远只能做演示。** 这正是 DDD 要解的；"**谁对这条沉淀下来的判断负责**"需要明确。而判断力一旦从"某个资深工程师、Domain Expert的经验"变成"所有 agent 都会继承的规则"，写错的**杀伤半径变大了**, 所以必须得审计。

---

## 想听听大家怎么看

1. **"AI-Native"你怎么定义？** 我给的是"人 + AI Agents 的新型组织 —— Agents 是新成员，角色、责任（= 边界）、workflow 全都要重新定义"。如果你的定义落在"用 AI 提效"这一层，差别在哪？
2. **判断力真能沉淀吗？** DDD 让专家判断进系统、被 agent 继承 —— 但一份团队共养的知识层，会不会最后腐烂成没人维护的 wiki？我的答案是达尔文式衰减（引用即自然选择），但这只在**有足够引用量**时成立。小团队够吗？
3. **五大支柱真的不能跳吗？** 我的主张是 Top-down 先建地基（文化 · 度量 · 就绪度），跳到 4-5（Spec · Autonomous）就是空转。但现实里大多数团队是**先上工具见到效果**才有预算谈组织 —— 这个顺序矛盾怎么解？
4. **Agents 当成员之后，责任怎么定？** 谁对 AI Agent 的**结果**负责 —— 下指令的人？审的人？还是团队？我的主张是**用责任反推边界**，但落到具体组织里，签字权和问责权到底怎么划？这是三个重定义里最没标准答案的一个。
5. **两个 loop 怎么共存？** `Human in the Agents loop` 和 `Agents in the Human loop` —— 一个组织里两种都会存在，但**什么工作该走哪一个**、切换的判据是什么？更难的是：同一件事在不同阶段可能要换 loop，这个切换谁来判断？
6. **门槛是降低还是提高了？** 我的主张是**转移**而非降低 —— coding 变便宜，工程判断力（EE/OE）变成核心。但这意味着**新人更难入门**了：过去靠写代码练判断力，现在代码由 AI 产出，**新人从哪练？** 这条我没有好答案。（外部数据在加压：一份针对 AI-native 创业公司的研究发现，它们团队小 25%、工程师多 13%，但 **entry-level 少 15%、senior 多 20%**，估值持平。）
7. **单 Agent 还是 multi-agent？** 我选单 agent 角色切换，理由是编排开销吃掉收益、判断力被拆散。这条最容易有分歧。
8. **组织跟不上时，先推谁？** S×T 矩阵的处方是 `S>T → 推组织`、`T>S → 拉个人`。但最危险的格子是 `S3+T2`（最强的人被流程卡住，最先离开）—— 这种情况下你会先做什么？
9. **Autonomous 从哪开始？** 我的判据是输入边界清晰 + 有安全网 + 风险可控（bug fixing / AutoOps / AutoSecurity）。有人从别的地方切入成功过吗？


---

## 出处

**定义与终态判定**
- [What Is AI Native? — IBM Think, 2026-02-03](https://www.ibm.com/think/topics/ai-native)
- [What Is AI-Native? The Founder's Guide — CRV, 2026-03-31](https://www.crv.com/content/what-is-ai-native)

**组织重设计（终态形状 + 5% 规模化）**
- [Design Your Company for AI, Not AI for Your Company — BCG, 2026-04-23](https://www.bcg.com/publications/2026/design-your-company-for-ai-not-ai-for-your-company) · [Unlocking the AI-First Organization (PDF)](https://media-publications.bcg.com/AI-First-Organization.pdf)
- ⚠️ 未读原文（站点 403，据摘要）：[The seven operating truths of AI-native companies — McKinsey, 2026-06-11](https://www.mckinsey.com/capabilities/business-building/our-insights/the-seven-operating-truths-of-ai-native-companies) · [The agentic organization](https://www.mckinsey.com/capabilities/people-and-organizational-performance/our-insights/the-agentic-organization-contours-of-the-next-paradigm-for-the-ai-era)

**工程纪律（唯一强调渐进的一家）**
- [AI and software delivery — Thoughtworks Looking Glass 2026](https://www.thoughtworks.com/en-us/insights/looking-glass/looking-glass-2026/AI-and-software-delivery)

**卡在 Phase 1 的实证**
- [AI-Native Organizational Transformation — Matt Beane, 2026-07-15](https://www.wildworldofwork.org/p/ai-native-organizational-transformation)（他据此联合创办了 SkillBench，文中已声明利益相关）
- [MIT report: 95% of generative AI pilots are failing — Fortune, 2025-08-18](https://fortune.com/2025/08/18/mit-report-95-percent-generative-ai-pilots-at-companies-failing-cfo/)（报告直链已 302 跳转，故引报道）· 反驳：[这个 95% 被夸大了 — Marketing AI Institute](https://www.marketingaiinstitute.com/blog/mit-study-ai-pilots)

**AI-washing 与 workslop**
- [AI Transformation Requires Redesigning Work, Not Cutting Roles — HBR, 2026-08-28](https://hbr.org/2026/08/ai-transformation-requires-redesigning-work-not-cutting-roles)
- [AI-Generated "Workslop" Is Destroying Productivity — HBR, 2025-09](https://hbr.org/2025/09/ai-generated-workslop-is-destroying-productivity)
- [AI Doesn't Reduce Work—It Intensifies It — HBR, 2026-02-09](https://hbr.org/2026/02/ai-doesnt-reduce-work-it-intensifies-it)
- ['AI washing': firms are scrambling to rebrand — The Guardian, 2026-05-24](https://www.theguardian.com/technology/2026/may/24/ai-washing-pr-firms-scrambling-rebrand) · [Hacker News 讨论](https://news.ycombinator.com/item?id=48257980)

**中文语境（政策 + 实践 + 商业模式）**
- [AI 原生企业：智能经济崛起的风向标 — 智源社区, 2025-11-14](https://hub.baai.ac.cn/view/50337)（"AI as an Outcome"：Sierra、Decagon 按 AI 完成的工单量计费；政策背景：2025-08-26 国务院《关于深入实施"人工智能+"行动的意见》提出"培育智能原生企业"）
- [AI Native 企业的关键，是从外化到内生 — 澎湃·湃客, 2026-05-26](https://www.thepaper.cn/newsDetail_forward_33232244)（角色牵引 / 审批模型的维护 / 74% 停在试点的三个鸿沟）
- [什么是真正的 AI Native — 腾讯云开发者社区](https://cloud.tencent.com/developer/article/2624391)（Spec-Driven Development + Context Engineering）

**entry-level 数据**
- ⚠️ 未读 working paper 原文，读的是财经媒体转述：[AI-Native Startups Hire Fewer Junior Workers — Benzinga/Yahoo Finance, 2026-07-07](https://finance.yahoo.com/technology/ai/articles/ai-native-startups-hire-fewer-033109211.html)

**未能核实**
- ⚠️ Gartner *Hype Cycle for Agentic AI, 2026*（站点 403）：据第三方转述已进入"幻灭低谷"、流传数字"仅 17% 已部署" —— 引用前请自行核。

---

**相关**：[#106 AI-Native Transformation —— 打造你的复利组织（人+AI Agents）](https://github.com/xg-gh-25/SwarmAI/discussions/106)
