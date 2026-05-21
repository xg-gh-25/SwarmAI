# AI Agent for Data：从幻觉到精准

> 📎 [View on GitHub](https://github.com/xg-gh-25/SwarmAI/discussions/36) | Category: Ideas | Published: 2026-05-21

---

> 可信、安全、场景化的数据智能 — 在决策发生的地方交付

**目标受众：** CXO、VP、GM、技术负责人  
**场景：** 企业正在部署 AI Agent 来访问关键业务数据（收入、Pipeline、预测、客户洞察），需要在准确性、安全性和规模化之间找到平衡。

---

## 愿景

每个企业都想要同一件事：AI 用数据回答业务问题 — 即时、准确、安全。

技术条件已经成熟。大语言模型能理解自然语言。SQL 引擎能在秒级返回结果。云数据平台能扩展到 PB 级。

**那为什么 AI Agent 总是给出错误的数字？**

---

## 现实：5 个结构性缺陷

这不是 AI 模型的问题，而是 **知识管理的问题** — 只不过被包装成了 AI 问题。

| # | 缺陷 | 现象 | 代价 |
|---|------|------|------|
| **1** | **真相碎片化** | 多个 AI 工具对同一个问题给出不同答案。一个说收入 1500 万，另一个说 1420 万，仪表盘说 1510 万。 | 领导层失去信任，退回手动看报表。AI 投资打水漂。 |
| **2** | **变更不可见** | 数据平台加了表、改了策略、重命名了字段，AI 工具不知道。不报错 — 静默返回错误数字。 | 每次平台变更导致 1-2 周非计划停机。更糟：没人发现的错误答案。 |
| **3** | **全有或全无的权限** | 用一个超级账号看全部数据（合规噩梦），或者因为 per-user 太难做而永远不上自助服务。 | 要么安全风险，要么可用性死胡同。没有中间地带。 |
| **4** | **准确性靠运气** | 固定报表完美无缺，临时提问像抛硬币。用户分不清哪个结果可信，只能手动校验每一个。 | 手动校验每个 AI 回答 = 违背了用 AI 的初衷。 |
| **5** | **集成税** | 每个新 AI 工具都要从零学习每个数据源。每个集成 2 周。知识永远不能跨工具复用。 | N 个 Agent × M 个数据源 = N×M 维护负担。无法规模化。 |

### 根因

> 这 5 个缺陷有同一个根因：**缺少一份机器可读、版本化、可强制执行的数据语义描述 — 说清楚数据是什么意思、谁能看。**
>
> AI 模型没问题。缺的是数据平台和 AI 消费者之间的 **契约（Contract）**。

---

## 架构：四层分离

解法不是更好的 NL2SQL 引擎，而是关注点的 **结构性分离**：

![AI Agent for Data — 四层参考架构](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/diagrams-ai-agent-for-data/2026-05-21-ai-agent-for-data-architecture-cn.svg)

<details>
<summary>架构图文字说明（点击展开）</summary>

**第四层：消费层（AI Agent + Skills）**
- Agent Portal / 自助服务 / 运维 Agent / 开发 CLI / 自定义 Agent
- Skills（可复用的能力模块）：周报生成、客户360、Pipeline分析、CRM更新、预测Gap...
- 每个 Skill = 一段业务流程编排，决定"调哪些 MCP 工具、按什么顺序、怎么组合结果"
- Skill 不嵌入数据知识 — 从第三层的语义目录获取

**第三层：语义契约层（The Missing Middle）+ 🔄 进化循环**
- 📋 语义目录：表清单、列语义、领域规则、关联关系
- 📐 认证查询模式（越用越多，从使用中沉淀）：命名模式、可组合片段、校验规则
- 🔒 访问策略（声明式）：按表、按身份、按应用
- 📊 数据质量信号：新鲜度、已知约束、废弃通知
- 进化循环：使用产生信号 → 发现新 pattern → 审核沉淀 → 发布新版本 → 下次直接命中
- 契约 = 版本化产物（语义化版本号），越用越丰富

**第二层：执行层（Data MCP — 无状态, 策略强制执行）**
- 工具：execute_certified_query / execute_sql(自动RLS) / get_schema / validate_sql / explain_access
- 中间件流水线：认证 → 身份解析 → RLS 改写 → 执行 → 审计

**第一层：数据基础设施（现有的, 无需改动）**
- Athena (OLAP) / Redshift (DW) / S3 (数据湖) / CRM (客户关系) / NoSQL (运营数据)

</details>

### 核心概念：Agent、Skill、MCP 三者的关系

理解这三者如何协作，是理解整个架构的关键：

![Agent / Skill / MCP 三者关系](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/diagrams-ai-agent-for-data/2026-05-21-ai-agent-for-data-agent-skill-mcp-cn.svg)

| 组件 | 比喻 | 职责 | 例子 |
|------|------|------|------|
| **Agent** | 大脑 🧠 | 理解用户说了什么，决定该做什么。自然语言理解、意图识别、结果综合与呈现 | 用户说"上周 GenAI 收入涨了多少" → Agent 理解为 revenue_query + genai_filter + wow_comparison |
| **Skill** | 手 🤲 | 知道完成一个业务任务需要哪些步骤。编排多个 MCP 调用的顺序和逻辑。可复用、可版本化、可跨 Agent 共享 | skill "weekly_revenue_report" → Step 1: 调 MCP 查收入 → Step 2: 调 MCP 查 movers → Step 3: 合并计算 WoW% |
| **MCP** | 腿 🦿 | 执行单个原子操作，不懂业务上下文。强制安全策略（RLS、权限、审计）。无状态 | tool "execute_certified_query" → 接收 pattern_name + params + identity → 返回已 RLS 过滤的结果 |

**用一个具体例子串起来：**

```
用户："帮我看看上周 GenAI 收入情况，重点客户有哪些变化"

Agent（大脑）识别意图：
  → 需要 revenue summary + top movers
  → 选择 skill: "weekly_genai_analysis"

Skill（手）编排流程：
  → Step 1: 调 MCP execute_certified_query("genai_revenue_wow", {scope:"全局"})
  → Step 2: 调 MCP execute_certified_query("revenue_movers", {genai:"Y", top:5})
  → Step 3: 调 MCP execute_certified_query("account_6w_trend", {accounts: step2结果})
  → Step 4: 合并三步数据，计算 WoW%、排名、趋势

MCP（腿）每次只做一件事：
  → 收到 pattern_name + params
  → 从语义目录解析出 SQL
  → 注入 RLS 权限过滤
  → 执行 Athena 查询
  → 返回原始数据

Agent（大脑）最后呈现：
  → "上周 GenAI 收入 320 万，环比 +8.1%。
     Top 增长客户：[客户_A] +52万（AI 工作负载激活），[客户_B] +23万。
     关注：[客户_C] 连续 3 周下降，建议 SA 跟进。"
```

**为什么要分三层而不是让 Agent 直接调数据？**

| 全部揉在 Agent 里（现状） | 三者分离（目标） |
|--------------------------|-----------------|
| Agent 又理解意图又写 SQL 又管安全 | 各司其职，各层独立演进 |
| 换个 Agent（GPT→Claude）要重写 SQL 逻辑 | 换 Agent 只需要换"大脑"，Skill 和 MCP 不动 |
| 每个 Agent 自己维护表知识 | Skill 从语义目录统一获取 |
| SQL 准确性取决于 LLM 智商 | SQL 来自认证模式，准确性由平台保证 |
| 安全规则散落在 Agent 代码里 | 安全在 MCP 层强制执行，Agent 无法绕过 |

### 为什么第三层是关键

大多数组织在让 AI Agent 连接数据时，跳过了第三层。直接从 Agent（第四层）连到 SQL 执行（第二层）。后果是：

| 没有第三层（现状） | 有第三层（目标） |
|-------------------|-----------------|
| 每个 Agent 内嵌各自的数据知识 | 所有 Agent 消费同一份版本化目录 |
| 平台变更 → Agent 静默出错 | 平台发布新版本 → Agent 自动适配 |
| N 个 Agent × M 个数据源 = N×M 集成 | N 个 Agent + M 个数据源 = N+M 集成 |
| 安全规则分散（且必然 drift） | 安全定义一次，处处强制执行 |
| "Agent A 能用但 Agent B 不行" | 同一个真相，保证一致性 |
| 数据知识永远是 Day 1 水平 | 越用越丰富 — 高频查询自动沉淀为认证模式 |

---

## 准确性模型：三个层次

不是所有数据问题都该用同一种方式回答：

| 层级 | 准确率 | 机制 | 适用场景 | NL2SQL 参与？ |
|------|:------:|------|---------|:------------:|
| **第一层：认证模式** | 100% | 预写好的 SQL 模板，Agent 选择模式 + 填参数 | 领导层报表、定时推送、合规数据 | ❌ 不需要 |
| **第二层：受约束生成** | ~95% | Agent 生成 SQL，但受 Catalog 约束（表/列白名单 + 必填 filter + JOIN 规则） | 临时分析、自助探索 | ✅ NL2SQL 在这里 |
| **第三层：无约束 NL2SQL** | 60-70% | Agent 自由生成 SQL，无任何校验 | ❌ 生产环境禁用 | ⚠️ 唯一路径 = 危险 |

**核心洞察：** 大多数 AI 数据产品只做第三层（无约束 NL2SQL）。这个架构让第一层成为默认、第二层成为补充、第三层在生产中禁用。

**结果：** 报表永远准确。临时提问几乎总是准确。没人需要手动校验数字。

### NL2SQL 的位置：不是敌人，是受控的工具

> **常见挑战：** "主流云厂商都提供 NL2SQL 服务（Amazon Q in QuickSight、Bedrock Agent + Athena），你说 NL2SQL 不准确，那这些产品怎么定位？"

**回答：我们不反对 NL2SQL，我们反对的是把 NL2SQL 当唯一路径。**

NL2SQL 的核心问题不是"不能用"，而是**用错了场景**：

| 场景 | NL2SQL 是否合适 | 为什么 |
|------|:--------------:|--------|
| 领导层周报（每周一推送） | ❌ | 同一个报表每周数字不一致 = 不可接受。必须确定性。 |
| VP 追问 "drill into segment A" | ⚠️ | 如果有认证模式覆盖就用认证模式；如果没有，NL2SQL + 约束 是合理 fallback |
| BD 自助探索 "哪些客户用量涨了" | ✅ | 探索性问题，可以接受偶尔不精确，用户会追问修正 |
| Analyst 自由分析 | ✅ | 专业用户能判断结果是否合理 |

**正确的 mental model：**

![NL2SQL 的位置 — 三个准确性层次](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/diagrams-ai-agent-for-data/2026-05-21-ai-agent-for-data-nl2sql-position.svg)

> **核心区别：** 裸 NL2SQL（无 catalog）= 60-70% 准确 → NL2SQL + Catalog 约束 = ~95% 准确。Semantic Catalog 是让 NL2SQL 从"不可靠"变成"基本可靠"的关键。

**对现有产品的定位：**

- **Amazon Q in QuickSight** → 适合 BI 探索场景（第二层），不适合定时报表（第一层）
- **Bedrock Agent + Athena** → 是执行引擎，搭配 Semantic Catalog 约束后可作为可靠的第二层
- **本架构** → 不替代这些产品，而是**在其之上增加 Semantic Contract 层**，让同样的引擎输出更可靠的结果

**一句话：NL2SQL 是好的锤子。Semantic Catalog 告诉它只能敲哪些钉子。**

### Skill 之外的灵活性：Agent 如何处理"没见过的问题"

> **常见挑战：** "如果 Skill 只覆盖了固定的业务流程，用户问了一个新问题怎么办？Agent 变成了一个死板的菜单机器人？"

**回答：Skill 是快速通道，不是围墙。Catalog 保证了即使没有 Skill，Agent 也能安全地自由查询。**

**决策流程（Agent 视角）：**

```
用户提问："上个月 ISV segment 里 GenAI 用量环比下降的客户有哪些？"

Agent 思考：
  1. 有没有现成的 Skill？
     → 检查 skill 列表... "customer_genai_decline_analysis"... 没有匹配的 ❌
  
  2. 有没有认证模式可以组合？
     → 检查 catalog patterns...
     → "revenue_by_account" ✓ 但没有 "usage_decline_filter" 的现成 pattern ❌
  
  3. 走第二层：受约束的 SQL 生成（NL2SQL + Catalog）
     → 从 Semantic Catalog 获取：
       • 可用表：fact_estimated_revenue ✓
       • 相关列：sfdc_account_name, genai_flag, sh_l3, 
                 biz_charge_type_group, ar_date, total_sales_revenue ✓
       • 必填 filter：fbr_flag = 'Y' AND sh_l1 = 'GCR' ← 自动注入
       • 分区要求：ar_month_start_date ← 自动注入
       • "Net Usage" 语义：biz_charge_type_group = 'Net Usage' ← 从 catalog 获取
     
     → Agent 生成 SQL（受 catalog 约束）：
       SELECT sfdc_account_name, 
              SUM(CASE WHEN ar_date BETWEEN '上月' THEN ... END) as curr,
              SUM(CASE WHEN ar_date BETWEEN '上上月' THEN ... END) as prev
       FROM fact_estimated_revenue
       WHERE fbr_flag = 'Y' AND sh_l1 = 'GCR'  ← 自动
         AND sh_l3 = 'ISV'
         AND genai_flag = 'Y'
         AND biz_charge_type_group = 'Net Usage'
       GROUP BY sfdc_account_name
       HAVING curr < prev
       ORDER BY (prev - curr) DESC

  4. 发送到 MCP 执行：
     → MCP validate_sql() 校验：
       ✓ 所有表/列在 catalog 中
       ✓ 必填 filter 存在
       ✓ 无越权表访问
     → MCP execute_sql()：
       ✓ RLS 自动注入 territory 过滤
       ✓ Athena 执行
       ✓ 审计记录
     → 返回结果

  5. Agent 呈现答案
```

**三层 fallback 机制：**

![三层 Fallback 决策路径](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/diagrams-ai-agent-for-data/2026-05-21-ai-agent-for-data-fallback.svg)

> 所有路径都安全 — 有答案就给，没把握就拒绝，永远不猜。

**关键设计：**

| 问题 | 答案 |
|------|------|
| Skill 没覆盖的问题怎么办？ | Agent 自动 fallback 到第二层（NL2SQL + Catalog 约束） |
| 第二层的 NL2SQL 可靠吗？ | 比裸 NL2SQL 高 25-30 个百分点 — 因为 Catalog 限制了可选范围，消除了大部分幻觉来源 |
| 如果生成了错误 SQL 怎么办？ | MCP validate_sql() 会拒绝（引用了未知表/列），不会执行 |
| Agent 会"编造"表名吗？ | 不会 — system prompt 注入了完整 catalog，Agent 只能从已知列表选择 |
| 用户问了完全无关的数据怎么办？ | deny-by-default：任何不在 catalog 里的表，MCP 直接拒绝执行 |
| 新问题反复被问怎么办？ | 信号 → 应该为这个 pattern 创建一个认证模式 → 下次走第一层 |

---

## 进化循环：第三层是活的，越用越强

Semantic Contract 不是写完就放那里的静态文档。它有一个内在的自我进化循环 — **使用本身驱动目录的丰富和精确度的提升**。

![第三层进化循环](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/diagrams-ai-agent-for-data/2026-05-21-ai-agent-for-data-evolution-loop-cn.svg)

### 进化循环的四个阶段

**阶段 1：观测（自动）**

每次 Agent 走第二层（NL2SQL + Catalog）执行一个查询，系统自动记录：
- 用户原始问题
- Agent 生成的 SQL
- 涉及的表和列
- 执行是否成功
- 用户是否对结果满意（是否追问修正）

**阶段 2：模式识别（半自动）**

当同一类 SQL pattern 被多次生成（不同用户、不同时间、相似意图）：
- 自动聚类相似 SQL → 提取可参数化模板
- 标记为"候选认证模式"
- 推送给领域专家审核

```
例：3 周内 5 个不同 GM 都问了 "我的 BU top decelerators"
    → 系统识别出共同模式："revenue_movers_by_bu(bu_name, period, direction)"
    → 推荐：创建认证模式 + 注册到 Skill
```

**阶段 3：沉淀（人工审核 + 一键发布）**

领域专家审核候选模式：
- SQL 逻辑对不对？
- 参数化是否合理？
- 是否需要额外的 filter 规则？
- 审核通过 → 发布为认证模式 → Catalog 版本递增

**阶段 4：加速（自动）**

下次相同意图的问题 → Agent 直接匹配到认证模式 → 走第一层 → 更快、更准。

### 这跟静态语义层的本质区别

| 方面 | 静态语义层（dbt metrics, Cube） | 进化式语义契约（本架构） |
|------|-------------------------------|------------------------|
| 目录内容来源 | 人工定义，发布后不变 | 使用驱动 + 人工审核确认 |
| 认证模式数量 | 初始定义多少就是多少 | 随使用自然增长 |
| 应对新需求 | 必须人工添加，等下个发布周期 | 自动发现 → 推荐 → 快速审核 → 沉淀 |
| 闲置检测 | 无 | 长期未使用的模式自动标记为候选废弃 |
| 准确率趋势 | 恒定 | 持续提升（第二层使用率下降 = 更多走第一层） |

### 衡量进化健康度的指标

| 指标 | 含义 | 健康方向 |
|------|------|---------|
| **第一层命中率** | 多少 % 的查询走了认证模式 | ↑ 越高越好（说明目录覆盖全） |
| **第二层成功率** | NL2SQL 生成的 SQL 被 MCP 接受执行的比例 | ↑ 越高越好（说明 catalog 约束有效） |
| **新模式沉淀周期** | 从首次第二层查询到成为认证模式的天数 | ↓ 越短越好 |
| **模式候选队列** | 待审核的候选认证模式数量 | 不应持续增长（及时审核） |
| **目录 drift 检测** | 引用了 catalog 中不存在的表/列的查询数 | 应为 0（全部被 deny） |

### 对客户的意义

> "你不需要第一天就把所有 SQL 模板都写好。从 10 个认证模式开始 — 覆盖你的周报和最常见的 5 个问题。剩下的让用户自由探索（第二层）。系统会告诉你接下来该为什么创建认证模式 — 基于真实使用数据，不是猜测。"

```
Day 1:   10 个认证模式，覆盖 70% 查询
Month 1: 25 个认证模式，覆盖 85% 查询（15 个从使用中沉淀）
Month 3: 40 个认证模式，覆盖 93% 查询（系统推荐了 20 个，审核通过 15 个）

剩余 7% 永远走第二层 — 因为它们是低频的、长尾的、不值得固化的
但即使走第二层，也有 Catalog 约束 — ~95% 准确
```

**一句话：语义目录不是你维护的文档，是你培养的系统。用得越多，它就越聪明。**

---

## 安全模型：同一个问题，不同的答案

```
提问："本周 GenAI 收入多少？"

VP:       → 3210 万 (+8.1% 环比)，占总收入 20.8%  [全局视角]
GM:       → 890 万 (+12% 环比)，分 segment 明细     [事业部视角]
BD IC:    → 15 万 (+23%)，2 个客户激活新工作负载    [Territory 视角]
```

**同一个 Agent。同一个查询模式。同一条代码路径。** 区别完全在安全层：

- VP 的身份 → 全量权限 → 无过滤
- GM 的身份 → 事业部范围 → 自动注入 BU filter
- BD IC 的身份 → 6 个 territory 客户 → 自动注入 territory filter

**Agent 永远不决定谁能看什么。** 平台决定 — 通过执行层（第二层）强制执行的行级安全（RLS），由语义契约层（第三层）声明的策略驱动。

### 身份模型

| 身份类型 | 使用场景 | 访问范围 | 示例 |
|---------|---------|---------|------|
| **服务账号** | 定时报表、系统操作 | 全量（有审计） | 每周领导层报表推送 |
| **委托身份** | Agent 代替某人执行 | 被委托人的权限 | GM 向 Agent 提问 |
| **终端用户** | 自助服务、直接交互 | 仅自己的 territory/范围 | BD 浏览自己的 pipeline |

---

## 用户故事：数据在决策发生的地方找到你

> 洞察在决策点到达 — 在你已经在用的工具里，以你的角色需要的深度，带着你的数据应有的安全性。

### 故事 1：VP — "智能找人，不是人找智能"

区域销售 VP 周一早上打开手机，8:00 Slack 上已经有一条消息等着：

> 📊 **本周收入简报 — W21**
>
> 收入 1540 万 (+3.2% 环比) | GenAI 320 万（占比 20.8%，+8.1%）
>
> ⚠️ 细分市场 A：-5.2% — 由 [客户_X] 工作负载迁移驱动
> ✅ 细分市场 B：+12% — [客户_Y] 激活新 AI 工作负载
>
> _[查看完整报告]  [钻取细分 A]  [转发给 GM]_

不用登录。不用打开仪表盘。不用搜索。**数据找到了领导，不是领导去找数据。**

如果他回复 "下周预期怎样？" → Agent 调用预测认证模式 → 1 分钟内在同一个 Slack thread 里回答。

**价值：** 零摩擦、决策级别的智能，送到领导已经在用的地方 — Email 或 Slack。

---

### 故事 2：GM — "洞察 → 行动 → 分配，一个动作完成"

GM 准备周三下午的 Pipeline Review。在 Agent Portal 输入：

> "上周我的 BU pipeline 动了多少？coverage 够吗？"

Agent 返回：

> Pipeline 新增 420 万（23 个新商机），coverage 1.8x vs 目标 2.0x。
> Gap 集中在子细分 Z（coverage 仅 0.9x）。
>
> 3 个客户 pipeline 停滞超过 30 天无活动：
> | 客户 | 金额 | Owner | 停滞天数 |
> |------|------|-------|---------|
> | [客户_A] | 120 万 | BD_1 | 35 |
> | [客户_B] | 80 万 | BD_2 | 42 |
> | [客户_C] | 60 万 | SA_1 | 31 |

GM 说：**"帮我给这些 owner 发 action items"**

Agent 自动生成并发送：

> ✅ **已发送 3 条行动项：**
>
> → @BD_1（Slack DM）："请复盘 [客户_A] pipeline（120 万，停滞 35 天），周五前更新下一步。"
> → @BD_2（Email）："请尽快安排 [客户_B] 客户会议，pipeline coverage 有风险。"
> → @SA_1（Slack DM）："请为 [客户_C] 提供技术评估 — 架构评审卡住了。"
>
> _[查看全部]  [创建为 CRM 任务]  [周五提醒我跟进]_

**价值：** 从"发现问题"到"有人在解决" — 几分钟。不需要开会。不需要手动追踪。

---

### 故事 3：BD Manager — "客户楼下，10 分钟准备完毕"

BD Manager 提前 15 分钟到了客户 [客户_F] 的办公楼下。

掏出手机，Slack 输入：

> "帮我看下 [客户_F] 的客户 360，生成一个 Call Plan"

约 1 分钟后收到：

> 🏢 **[客户_F] — 客户 360**
>
> **收入：** 年 280 万 (+15% YoY) | AI 工作负载：42 万（占 15%，在增长）
> **Pipeline：** 3 个在途商机，共 150 万，最大单：80 万（POC 阶段）
> **风险信号：** 检测到竞品出现在工作负载中（风险评分 7/10）
> **最近活动：** 2 周前 SA 上门 — 架构方案待回复
> **关键联系人：** CTO（见过 3 次）、VP 工程（见过 1 次）、采购（从未接触）
>
> 📋 **建议 Call Plan：**
> 1. **开场：** 提及上次 SA 拜访，询问架构方案反馈
> 2. **探测：** 竞品存在 — 他们是否在评估替代方案？
> 3. **推进：** 提议 80 万商机的 POC 时间表
> 4. **请求：** 引荐采购部门（企业合同所需）
>
> _[保存为拜访记录]  [分享给 SA]  [更新商机备注]_

BD 点 "保存为拜访记录" → 自动写入 CRM（拜访记录 + Call Plan 内容）。

**价值：** 以前需要 15-30 分钟翻仪表盘 + 整理笔记的准备工作 → 手机上 10 分钟搞定，包括阅读和思考。一线团队留在一线。

---

### 故事 4：BD IC — "对话式 CRM，告别填表"

BD IC 刚结束客户会议，走出会议室，在 Slack 输入：

> "更新 [客户_G] 的商机：进入 POC 阶段，预计 Q3 关单，金额调到 120 万。客户确认预算批了，下周开始和 SA 团队做技术 POC。"

Agent 返回：

> ✅ **CRM 商机已更新：**
> • 阶段：Qualify → POC ✓
> • 预计关单：Q3 ✓
> • 金额：80 万 → 120 万 ✓
> • 备注已添加 ✓
>
> **同时已创建：**
> • 活动：客户会议（今天）— 自动记录
> • 下一步：「POC 启动」— 5 个工作日后到期
> • 通知：已告知 SA 团队 POC 即将启动
>
> _[在 CRM 查看]  [通知经理]  [撤销]_

**以前：** 回到办公室 → 登录 CRM → 找到商机 → 填 8 个字段 → 保存 → 写活动记录 → 设 next step = 10-15 分钟行政工作。
**现在：** 走路时一条 Slack 消息，不到 1 分钟完成更新。

**价值：** CRM 使用率从 40% 提升到 95% — 因为更新比不更新更容易。数据质量也提高了，因为在"现场"就记录，不是回去凭记忆填。

---

## 端到端流程：从提问到可信答案

一个完整示例，展示每一层如何参与：

```
用户（Slack，手机）：
"帮我看下 [客户_F] 的客户 360，生成一个 Call Plan"

─────────────────────────────────────────────────────────────────────

步骤 1：意图识别 → 选择 Skill
  │ 角色：Agent（大脑）
  │
  │ Agent 已注入语义目录 v2.4：
  │ • 识别意图："account_360" + "call_plan"
  │ • 选择 Skill："customer_360_with_call_plan"
  │ • Agent 不写 SQL，不知道表名 — 把任务交给 Skill
  ▼

─────────────────────────────────────────────────────────────────────

步骤 2：Skill 编排业务流程
  │ 角色：Skill（手）
  │
  │ Skill "customer_360_with_call_plan" 知道完成这个任务需要 4 步：
  │
  │ Step 1: 查客户收入 → 调 MCP tool "execute_certified_query"
  │ Step 2: 查客户 Pipeline → 调 MCP tool "execute_certified_query"
  │ Step 3: 查风险信号 → 调 MCP tool "execute_certified_query"
  │ Step 4: 查历史活动 → 调 MCP tool "execute_certified_query"
  │
  │ Skill 决定：4 步可以并行执行（无依赖关系）
  ▼

─────────────────────────────────────────────────────────────────────

步骤 3：MCP 执行每个原子查询
  │ 角色：MCP（腿）× 4 并行
  │
  │ 调用 1: execute_certified_query(
  │   pattern = "account_revenue_summary",
  │   params  = {account: "客户_F", period: "YTD"},
  │   identity = {sub: "bd_manager_1", type: "end_user"}
  │ )
  │ → 语义目录：解析 pattern → 参数化 SQL
  │ → RLS 中间件：自动注入 territory 过滤（WHERE territory IN (...)）
  │ → Athena 执行：返回结构化数据
  │ → 审计：记录 who/what/when
  │
  │ 调用 2: "account_pipeline_snapshot" — 同样流程
  │ 调用 3: "account_risk_signals" — 同样流程
  │ 调用 4: "account_activities" — 同样流程
  │
  │ 总延迟：~30-60 秒（并行查询 + LLM 处理）
  ▼

─────────────────────────────────────────────────────────────────────

步骤 4：Skill 组装 + Agent 呈现
  │ 角色：Skill（手）→ Agent（大脑）
  │
  │ Skill：
  │ • 合并 4 步结果为结构化 JSON
  │ • 计算 YoY%、risk score 排序等
  │ • 将数据 + "请生成 call plan" 指令交给 Agent
  │
  │ Agent：
  │ • LLM 综合呈现：格式化为手机可读的客户 360
  │ • 生成 Call Plan（基于数据信号 + 联系人历史推理）
  │ • 不编造数字 — 所有数据来自 MCP 返回值
  ▼

─────────────────────────────────────────────────────────────────────

步骤 5：交付 + 行动（闭环）
  │ 角色：Agent → MCP
  │
  │ → Slack 消息：客户 360 + Call Plan（手机适配格式）
  │ → 操作按钮：[保存拜访记录] [更新 CRM] [分享给 SA]
  │ → 可信标识："4 个认证模式 | 目录 v2.4 | RLS: territory 过滤"
  │
  │ 用户点击 [保存拜访记录]：
  │ → Agent 调用另一个 Skill："save_crm_activity"
  │ → Skill 调 MCP tool "crm_create_activity"
  │ → 同一身份、同一审计轨迹
  │ → CRM 记录自动创建
  ▼

─────────────────────────────────────────────────────────────────────

完整审计轨迹（每一步都有记录）：
{
  request_id: "uuid-xxx",
  caller: "bd_manager_1",
  identity_type: "end_user",
  agent: "mobile_agent_v3",
  skill_invoked: "customer_360_with_call_plan",
  mcp_calls: [
    {tool: "execute_certified_query", pattern: "account_revenue_summary", latency: 12s},
    {tool: "execute_certified_query", pattern: "account_pipeline_snapshot", latency: 8s},
    {tool: "execute_certified_query", pattern: "account_risk_signals", latency: 6s},
    {tool: "execute_certified_query", pattern: "account_activities", latency: 3s}
  ],
  tables_accessed: ["fact_revenue", "fact_pipeline", "risk_integrated", "crm_activities"],
  rls_applied: true,
  rls_scope: "territory = ['T-001', 'T-002', 'T-003']",
  catalog_version: "v2.4.0",
  total_latency_ms: 45000,
  timestamp: "2026-05-21T09:50:00Z"
}
```

### LLM 做什么 vs 平台做什么

| 职责 | 谁负责 | 保证 |
|------|--------|------|
| 理解问题 | LLM（Agent） | 尽力而为（自然语言理解） |
| 选择正确的查询 | 语义目录（认证模式） | 确定性（第一层） |
| 写正确的 SQL | 目录（预写好的模板） | 100%（无 LLM SQL 生成） |
| 强制数据访问权限 | MCP（RLS 中间件） | 密码学级别（身份绑定） |
| 返回准确数字 | Athena/Redshift（计算引擎） | 基础设施级别 |
| 清晰呈现结果 | LLM（Agent） | 尽力而为（格式化） |
| 审计轨迹 | MCP（第二层） | 每个请求必录 |

**核心洞察：** LLM 处理"软"的部分（理解意图、呈现结果）。平台处理"硬"的部分（正确 SQL、安全、准确性）。这就是为什么准确性不依赖于用哪个模型。

---

## 层间交互：同步机制

### 第四层 ↔ 第三层：发现 + 消费

```
Agent 启动时：
  1. 拉取语义目录（版本化，本地缓存）
     → 可用的表、列、含义、模式、规则
  2. 注入到 system prompt
     → "你可以访问这些表：[...]"
     → "以下场景必须使用认证模式：[...]"
     → "这些 filter 是强制性的：[...]"

Agent 运行时：
  选项 A: execute_certified_query(模式名, 参数)
    → Agent 发送意图，不发 SQL
    → MCP 从目录解析模式为 SQL
    → 确定性，永远准确

  选项 B: execute_sql(生成的SQL, 目录版本号)
    → Agent 发送 SQL + 参考了哪个目录版本
    → MCP 对照同版本目录验证
    → 引用未知表/列 → 拒绝

目录更新：
  → 新版本发布（v2.3 → v2.4）
  → Agent 下次会话启动时拉取
  → 新表/模式立即可用
  → 任何 Agent 代码零修改
```

### 第三层 ↔ 第二层：契约执行

```
目录发布：
  1. 领域专家添加新表 / 策略
  2. CI 验证：不破坏已有模式
  3. 版本号递增（semver），产物发布
  4. MCP 服务热加载新策略
     → 新 RLS 规则立即生效
     → 新表的跳过/拒绝规则生效
  5. Agent 在下次拉取时发现新版本

运行时执行（MCP 服务端）：
  SQL 到达 → MCP 检查：
  ✓ 所有引用的表在目录中存在？      （未知 = 拒绝）
  ✓ 必填 filter 存在？              （缺失 = 拒绝）
  ✓ RLS 策略已对每个表应用？        （自动注入 WHERE）
  ✓ 身份有权访问此表？              （无权 = 拒绝）
  → 全部通过 → 执行
  → 任何检查失败 → 向 Agent 返回结构化错误（不是静默失败）
```

### 供应链心智模型

```
         ┌──────────────────────────┐
         │  语义目录 Git Repo       │
         │  (Code Review + CI + semver) │
         └────────────┬─────────────┘
                      │ publish v2.4.0
         ┌────────────┼────────────┐
         v            v            v
   MCP Service    Agent A     Agent B
   (derive:       (read:      (read:
    policies,      tables,     tables,
    overrides,     patterns,   patterns,
    rules)         rules)      rules)
```

> 所有人消费同一个版本 → 保证一致性。版本锁定 → 无意外破坏。发布 = 一个地方 → 所有消费者自动适配。

### 为什么用 Git Repo 管理语义契约层

> **常见问题：** "为什么不用数据库、API 服务、或者 SaaS 产品来管理目录？为什么是 Git？"

**回答：因为语义目录本质上是 "关于数据的代码" — 它需要代码级的治理能力。**

| 需求 | Git 天然支持 | 数据库/SaaS 需要额外建设 |
|------|:-----------:|:----------------------:|
| 版本历史（谁改了什么、什么时候） | ✓ `git log` | 需要自建审计表 |
| Code Review（变更需要审批） | ✓ Pull Request | 需要自建审批流 |
| CI 验证（新版本不破坏旧 pattern） | ✓ GitHub Actions / Pipeline | 需要自建验证逻辑 |
| 多人协作（领域专家 + 数据工程师 + AI 工程师） | ✓ Branch + Merge | 需要自建权限 + 冲突解决 |
| 回滚（新版本有问题，秒级回退） | ✓ `git revert` | 需要自建回滚机制 |
| 声明式（YAML/JSON，人和机器都能读） | ✓ 文件即内容 | 需要 import/export |
| 离线可用（Agent 本地缓存一份） | ✓ `git clone` | 需要缓存 + 同步机制 |

**实际的 repo 结构：**

```
semantic-catalog/
├── catalog.yaml              ← 表清单 + 列语义 + 关联关系
├── rules/
│   ├── mandatory_filters.yaml   ← 必填 filter 规则
│   ├── partition_rules.yaml     ← 分区约束
│   └── enums.yaml               ← 枚举值定义
├── patterns/
│   ├── revenue/
│   │   ├── weekly_by_segment.sql.j2   ← Jinja2 参数化 SQL
│   │   ├── top_movers.sql.j2
│   │   └── genai_summary.sql.j2
│   ├── pipeline/
│   │   ├── coverage_analysis.sql.j2
│   │   └── stalled_accounts.sql.j2
│   └── forecast/
│       └── gap_by_hierarchy.sql.j2
├── policies/
│   ├── athena/
│   │   ├── policies.yaml        ← RLS 策略定义
│   │   └── table_overrides.yaml ← 维度表跳过/拒绝声明
│   └── redshift/
│       └── policies.yaml
├── quality/
│   └── freshness_probes.yaml    ← 自动探测配置
├── CHANGELOG.md                 ← 版本变更记录
└── VERSION                      ← 当前版本号 (e.g., 2.4.0)
```

### 各层怎么 Access 这个 Repo

**Git Repo = Source of Truth：**
- `main` branch = 生产版本
- `feature` branch = 变更中（PR 审核）
- `tag v2.4.0` = 已发布版本

#### 第四层（Agent / Skill）怎么拿到

| 方式 | 适用场景 | 机制 |
|------|---------|------|
| **构建时打包** | 生产 Agent | Agent 构建 pipeline 依赖 catalog repo tag → 打包进镜像/二进制 → 零网络依赖 |
| **运行时拉取** | 快速迭代 | Agent 启动时 `GET /catalog/v2.4.0/manifest.json` 或 `git clone --depth 1` → 本地缓存 TTL 1h |
| **注入 system prompt** | 最简方案（我们目前用这种） | 启动时拼接 catalog 关键信息到 LLM system prompt → Agent 直接"看到"可用表/列/规则 |

#### 第二层（MCP 服务）怎么拿到

**方式：部署 pipeline 自动同步**
- MCP 服务的 CI/CD pipeline 依赖 catalog repo
- 每次 catalog 发布新 tag → 触发 MCP 部署 pipeline
- Pipeline 从 catalog repo 生成：`policies.yaml`（RLS）+ `table_overrides.yaml` + `validation_rules.json`
- MCP 服务重启加载新配置（或热加载）

#### 版本不一致时的行为（fail-closed）

| 情况 | 结果 |
|------|------|
| Agent v2.3 + MCP v2.4：Agent 引用了已删除的列 | MCP 拒绝 ✓ |
| Agent v2.3 + MCP v2.4：Agent 不知道新增的表 | 不会查询 ✓ |
| Agent v2.4 + MCP v2.3：Agent 发了新 pattern | MCP 不认识 → 拒绝 ✓ |

> **所有不一致都表现为"拒绝"而不是"静默错误" — fail-closed by design。**

### 为什么不用数据库或 API 服务？

不是不能用 — 是 Git 在这个场景下综合成本最低：

| 方案 | 优势 | 劣势 |
|------|------|------|
| **Git Repo（推荐）** | 零基础设施成本、原生版本控制、Code Review 天然融入工作流、离线可用 | 非实时（分钟级同步）、大文件不适合 |
| API 服务 | 实时查询、细粒度权限 | 需要开发维护、是单点故障、Agent 依赖网络 |
| 数据库 | 复杂查询、动态过滤 | 需要 schema 设计、迁移管理、额外基础设施 |
| SaaS 产品（DataHub 等） | 现成 UI、搜索能力 | 为人设计不为机器设计、vendor lock-in、额外成本 |

**适用场景：**
- < 100 张表、< 50 个认证模式 → Git Repo 完全够用（我们的场景）
- 100-1000 张表 → Git + 自动生成索引（API facade over git）
- \> 1000 张表 → 考虑专用 catalog 服务（但仍然用 Git 作为 source of truth，服务只是 read cache）

**一句话：Git 是最便宜的、最成熟的、最被信任的 "configuration as code" 基础设施。语义目录本质上就是 configuration — 用 Git 管理它就像用 Git 管理 Terraform 一样自然。**

---

## 第三层管理：谁来维护目录？

### 三个角色

| 角色 | 定义什么 | 频率 | 示例 |
|------|---------|------|------|
| **领域专家**（业务） | 业务含义、规则、敏感度分级 | 新数据上线时（每月） | "这个表是日粒度收入表。`status_flag='final'` 是必填 filter。" |
| **数据工程师**（平台） | Schema、SLA、新鲜度、RLS 策略、基础设施映射 | 平台变更时（双周） | "表迁到新集群了。RLS 策略用 territory 级过滤。" |
| **AI 工程师**（Agent） | 查询模式、Prompt、验证逻辑、UX 提示 | 新场景出现时（每周） | "新模式：`weekly_forecast_gap` — 参数：scope, period, segment" |

### 自动化 vs 人工

| 目录组件 | 可自动派生 | 需要专家 |
|---------|:---------:|:--------:|
| 表 Schema（列、类型） | 90%（从 DDL/Glue 自动获取） | 10%（业务含义） |
| 数据新鲜度 | 100%（探测 MAX(date)） | 0% |
| RLS 策略 | 70%（表列表从目录获取） | 30%（过滤逻辑） |
| 领域规则 | 10%（分区检测） | 90%（业务逻辑） |
| 查询模式 | 30%（参数类型化） | 70%（意图→SQL 映射） |
| 关联关系/JOIN | 50%（FK 检测） | 50%（业务关联） |

**80% 的目录内容可以从现有系统自动派生。** 领域专家只需要添加机器无法推断的 20% — 主要是业务含义和特定领域规则。

### 这是不是一个新的全职岗位？

**不是。** 目录只是把已有的知识正式化 — 这些知识现在散落在 wiki、Slack 消息、代码注释、和老员工的脑子里。用结构化格式写下来是增量工作：

- **初始搭建：** 一个有 10-20 张表的领域，2-3 天
- **日常维护：** 每周 1-2 小时（由平台变更触发）
- **谁来做：** 同一批人 — 今天维护 SQL 模板、写 wiki 文档、做新人 onboarding 的人 — 只是从写散文变成写结构化格式

---

## 竞品差异化

| 方案 | 做什么 | 哪里不够 |
|------|--------|---------|
| **NL2SQL 引擎**（大多数 AI+数据演示） | LLM 从问题生成 SQL | ~60-70% 准确率。无安全模型。跨 Agent 无一致性保证。 |
| **语义层**（dbt metrics, Cube） | 预定义指标 + 受治理的 SQL | BI 工具的好方案。不是为多 Agent 消费设计的。执行层无 RLS。 |
| **数据目录**（DataHub, OpenMetadata） | 人类浏览的元数据发现 | 为人设计的，不是为机器设计的。无认证模式。无强制执行。 |
| **本架构** | 目录 + 认证模式 + 执行层 RLS + 多 Agent 消费 | 全栈：准确性（第一/二层）+ 安全性（AST 级 RLS）+ 规模化（N+M 不是 N×M） |

**一句话差异化：** 唯一同时解决准确性（认证模式）、安全性（执行层 RLS）和规模化（版本化契约支持多 Agent）的架构。

---

## 合规与数据跨境（CBDT）

> **在中国市场，任何 AI + 数据方案如果不解答"数据住在哪、模型跑在哪、谁能看什么"三个问题，就不会被采纳。**

![合规数据流向图](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/diagrams-ai-agent-for-data/2026-05-21-ai-agent-for-data-cbdt-dataflow.svg)

### 为什么这个架构天然适配合规要求

四层分离的架构本身就是合规友好的 — 因为关注点分离意味着**数据不需要离开它该待的地方**：

| 合规关切 | 本架构的回答 |
|---------|------------|
| **数据驻留** | 第一层（数据基础设施）100% 在客户指定区域内。Athena/Redshift/S3 部署在客户自己的账号和区域里，数据不动。 |
| **模型调用** | 第四层（Agent）调用的 LLM 可以部署在同区域（如区域内的 Bedrock endpoint），不需要数据出境。 |
| **中间层（MCP + Catalog）** | 第二层和第三层只传递**元数据和查询结果**，不传递原始数据。Catalog 是 schema/规则描述，不含业务数据。 |
| **结果回传** | Agent 只拿到查询结果（聚合后的数字），不拿原始 row-level 数据。RLS 进一步限制范围。 |

### CBDT 场景分析

```
场景 1：数据和模型在同区域（最简单）
  数据：cn-north-1 Athena
  模型：cn-north-1 Bedrock (Claude/Titan)
  Agent：cn-north-1 ECS
  → 零跨境。端到端在境内完成。

场景 2：模型在海外，数据在境内（需要评估）
  数据：cn-north-1 Athena（不出境）
  MCP：cn-north-1（不出境 — SQL 在境内执行）
  Agent：调用海外 LLM API
  → 出境的是什么？
    ✓ 用户的自然语言问题（非敏感）
    ✓ 查询结果的聚合数字（经 RLS 过滤）
    ✗ 原始业务数据 — 不出境（SQL 在境内执行并聚合）
  → 需要评估：聚合后的查询结果是否属于"重要数据"范畴

场景 3：多区域数据联邦（最复杂）
  → 每个区域有自己的 MCP 执行层 + RLS 策略
  → 语义目录可以跨区域共享（它只是 schema 描述，无业务数据）
  → Agent 可以并行调多个区域的 MCP，各自在本地执行
  → 结果在 Agent 侧合并 — 出境的只是聚合数字
```

### 架构内置的合规控制点

| 控制点 | 位置 | 机制 |
|--------|------|------|
| **数据最小化** | 第二层 RLS | 用户只能拿到自己权限范围内的数据，不是全量 |
| **查询审计** | 第二层审计中间件 | 每个查询记录 who/what/when/which tables，满足 PIPL 可追溯要求 |
| **访问分级** | 第三层策略声明 | 表级敏感度标签（公开/内部/机密），高敏感表可标记为"禁止经 Agent 查询" |
| **模型隔离** | 第四层部署选择 | Agent/LLM 可部署在同区域，或仅将非敏感的汇总结果发送到模型 |
| **目录脱敏** | 第三层 Catalog 设计 | Catalog 只包含 schema 描述（列名、类型、规则），不含实际业务数据 |

### 回答客户常见合规问题

| 客户问 | 回答 |
|--------|------|
| "我的数据会不会被 AI 模型训练？" | 数据不到达模型 — 模型只看到用户的问题和聚合后的结果。SQL 在你自己的 Athena 里执行。 |
| "数据出境了吗？" | 取决于模型部署位置。如果用区域内 Bedrock endpoint，端到端零出境。如果用海外模型，出境的只是聚合查询结果（经 RLS 过滤），原始数据不出境。 |
| "谁能看到我的数据？" | RLS 按用户身份强制过滤 + 审计日志记录每次访问。与仪表盘同等或更严格的权限模型。 |
| "怎么满足 PIPL/数据安全法？" | 数据最小化（RLS）+ 可追溯（审计）+ 访问分级（策略声明）+ 数据不出域（本地执行）。 |
| "如果监管要求数据分类分级？" | 第三层 Catalog 的表/列敏感度标签就是分类分级的机器可读实现 — 标注一次，处处执行。 |

### 一句话给 CXO

> "这个架构里，**数据永远不离开你的基础设施**。AI 看到的是问题和聚合结果，不是原始数据。安全策略在你的域内强制执行，不依赖外部系统的善意。"

---

## CXO 一页纸

![从 N×M 到 Hub & Spoke](https://raw.githubusercontent.com/xg-gh-25/SwarmAI/main/docs/diagrams-ai-agent-for-data/2026-05-21-ai-agent-for-data-cxo-before-after.svg)

> 你不会让 5 个部门各自接银行做转账 — 你让他们走统一的审批流程。为什么要让 5 个 AI Agent 各自解读你的数据？

---

## 实战案例：XX Agent for XX Sales Intelligence（2 个月生产运行）

**背景：** 某大型企业区域销售组织，100+ 销售团队成员，13 个事业部（BU），通过内部 AI Agent 平台提供数据智能服务。

**做了什么：**
- 基于统一 MCP 数据服务（Athena + RLS），为 Agent Portal 构建了销售数据查询能力
- 14 张核心 Athena 表（收入、Pipeline、预测、风险、层级权限），全部配有认证查询模式
- 行级安全（RLS）按用户 territory 自动过滤 — 在 MCP 执行层强制，Agent 无法绕过
- 覆盖场景：周报自动生成与推送、收入/Pipeline 临时查询、客户 360、预测 Gap 分析

**关键成果（2 个月）：**
- 领导层周报 **零数据准确性事故** — 全部走认证查询模式，无 NL2SQL
- RLS 覆盖 14 张表，deny-by-default — 未声明的表自动拒绝访问
- 新增数据查询场景（如 BMS 风险表）从提出到上线 **< 1 天**（加一条 policy + 注册认证模式）
- 5 个组织层级（VP → GM → Director → Manager → IC）同一套 Agent，按身份自动展示不同范围数据

**仍在解决的问题（诚实说）：**
- 语义目录还没有独立 repo，分散在 MCP server config 和 Agent skill 代码中
- 跨团队 table registry 同步还是手动 diff，没有自动化 contract
- per-user 自助查询（Quick For Biz）尚在规划，当前只有服务账号路径

**核心指标：**

| 指标 | 之前（手动 + 仪表盘） | 之后（Agent + MCP） |
|------|---------------------|-------------------|
| 周报生成时间 | 人工整理 2-3 小时 | 自动生成，定时推送 |
| 数据准确性（周报） | 取决于谁做（人为错误） | 100%（认证模式，确定性） |
| 临时数据问题响应 | 找数据团队排队，半天-1天 | Agent 自助，1-3 分钟 |
| 新表接入时间 | 对接开发 1-2 周 | < 1 天（policy + pattern） |
| 数据安全 | 共享 dashboard 无行级隔离 | RLS 按 territory 自动过滤 |

---

## 核心转变

| 从 | 到 |
|---|---|
| AI 生成 SQL（准确性取决于模型） | 平台提供认证 SQL（准确性取决于架构） |
| 每个 Agent 维护数据知识 | 所有 Agent 消费一份版本化契约 |
| 安全在 Agent 端（可绕过） | 安全在平台端（强制执行） |
| 人校验 AI 的数字 | 人信任 AI 的数字（审计轨迹可查） |
| 数据在仪表盘里等人来看（拉取） | 数据在决策点主动出现（推送 + 对话） |
| CRM 是苦差事（填表） | CRM 是对话（自然语言） |

---

## 已知限制与开放问题

> 诚实比完美重要。以下是我们清楚知道还没完全解决的问题：

| 限制 | 现状 | 缓解方向 |
|------|------|---------|
| **语义错误 SQL** | Level 2 NL2SQL 可能生成"语法正确但语义错误"的 SQL（如 `status='completed'` 实际应为 `status='final'`） | Catalog 中的 `enums.yaml` 定义每列的合法值。`validate_sql()` 对 WHERE 条件做枚举校验。但不能 100% 杜绝 — 这就是为什么 Level 1 是默认路径 |
| **跨异构源 JOIN** | Athena 表 + CRM 数据需要在 Agent 侧内存合并，不能直接 SQL JOIN | Skill 编排层处理：先查 Athena，再查 CRM，Python 侧 merge。牺牲一些延迟换来数据源隔离 |
| **实时数据新鲜度** | Catalog 同步是分钟级（Git TTL）。新表上线到可查询有 delay | 短期：缩短 TTL 到 5 分钟。长期：Git 作 source of truth + API facade（read-through cache），实现秒级发现 |
| **多团队目录冲突** | 50+ 数据团队共享一个 Catalog repo，pattern 命名/规则可能冲突 | 分布式维护权模型：每个 BU 维护自己的 `patterns/{bu}/` 子目录，中央 team 做 CODEOWNERS + merge conflict resolve |
| **Level 2 覆盖率上限** | ~95% 不是 100%。剩下 5% 是 catalog 约束无法捕获的复杂语义错误 | 接受这个上限。对领导层等高敏感场景始终走 Level 1。Level 2 适用于"能接受偶尔追问修正"的探索性场景 |
| **冷启动成本** | 新领域第一周只有少量认证模式，Level 2 使用率高（准确率相对低） | 从已有 SQL 模板/报表代码批量提取初始 patterns（我们做过，2-3 天可完成 10-20 个表的领域） |

---

## 最小可行部署（MVP）

> "我不需要一开始就建完整的四层。最小化验证这个模型需要什么？"

**30 天 MVP — 验证核心假设：**

```
Week 1: 选 1 个高频报表（如周报），提取其 SQL 为 3-5 个认证模式
         → 写入 catalog.yaml（表/列/规则）
         → 注册为 patterns/revenue/weekly.sql.j2

Week 2: 搭建最小 MCP 服务（1 个 tool: execute_certified_query）
         → 加 RLS 中间件（即使只有 1 条 policy）
         → 加审计日志

Week 3: 接入 1 个 Agent（哪怕是 Q-CLI + system prompt 注入 catalog）
         → 验证：Agent 能否通过 pattern 名 + 参数拿到正确数据？
         → 验证：RLS 是否正确限制了不同身份的数据范围？

Week 4: 度量 & 决策
         → 这 3-5 个 pattern 覆盖了多少 % 的实际查询？
         → 有没有出现准确性事故？
         → 决定：是否值得扩展到更多 pattern？
```

**MVP 的关键度量（4 周后回答）：**

| 问题 | 期望答案 |
|------|---------|
| 认证模式覆盖了多少 % 的实际查询？ | > 50%（说明选对了高频场景） |
| Level 1 路径有没有准确性事故？ | 0（如果 > 0，说明 pattern 本身有 bug） |
| RLS 是否正确隔离了不同身份？ | 是（admin 看全量，普通用户看子集） |
| 从 "用户提问" 到 "拿到结果" 延迟？ | < 30 秒（大部分时间在 Athena 执行） |
| 维护成本？| 这 4 周花了多少人时在 catalog 维护上？ |

**如果 MVP 成功 → 扩展路径：**
- Month 2: 10→25 个 patterns，覆盖 3 个常见场景
- Month 3: 加入 Level 2（NL2SQL + Catalog 约束），覆盖长尾查询
- Month 4: 进化循环开始工作 — 高频 Level 2 查询自动推荐为候选 pattern

---

## 行动号召

> "这 5 个缺陷里，哪一个今天最困扰你的组织？
>
> 我们从那里开始。"

| 起点 | 第一步 | 见效时间 |
|------|--------|---------|
| 准确性问题 | 为关键报表构建 5-10 个认证模式 | 2-4 周 |
| 安全问题 | 为 Top 5 表在执行层实施 RLS | 3-4 周 |
| 集成问题 | 从现有知识提取语义目录 | 2-3 周 |
| 采用率问题 | 为一个团队部署对话式 CRM | 4-6 周 |
| 以上全部 | 从一个事业部开始，验证模型，扩展 | 6-8 周 |

---

_架构经过 2 个月生产验证，覆盖 14 张数据表、13 个事业部、100+ 用户，领导层周报零数据准确性事故。_
