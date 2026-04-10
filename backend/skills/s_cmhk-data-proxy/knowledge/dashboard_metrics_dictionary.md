# Dashboard 指标词典

> **用途**：系统梳理 MBR Dashboard 上可见的每个指标的定义、计算逻辑和业务含义
> **范围**：三份 MBR Dashboard（Revenue / Pipeline / Forecast）
> **与 field_dictionary.md 的关系**：field_dictionary 定义底表字段（原材料），本文件定义 Dashboard 指标（成品）
> **维护方式**：持续学习，每次发现新指标或纠正理解时更新

---

## 一、Revenue Dashboard 指标 [CMHK_REV_01]

### 1.1 达成类指标

| 指标 | 计算公式 | 数据口径 | 刷新频率 | 说明 |
|---|---|---|---|---|
| **YTD Attn%** | YTD SRP Rev / YTD Target × 100% | SRP | 月 | 年初至今达成率，最核心的"健康体温" |
| **CM Attn%** | CM SRP Rev / CM Target × 100% | SRP | 月 | 当月单月达成率，比 YTD 更敏感 |
| **Est CM Attn%** | CM CDER Rev（截至今日）÷ 日数 × 月总天数 / CM Target | CDER | 日 | 当月实时预估达成率，波动较大 |
| **Qx Attn%** | Qx SRP Rev / Qx Target × 100% | SRP | 月 | 单季度达成率 |

### 1.2 增长类指标

| 指标 | 计算公式 | 说明 |
|---|---|---|
| **YTD YoY Rev Growth%** | (CY YTD Rev - LY YTD Rev) / LY YTD Rev | 年初至今同比增长率 |
| **CM DMoM%** | (CM ADRR - LM ADRR) / LM ADRR | 月环比 ADRR 变化，最敏感的趋势信号 |
| **YTD Incremental%** | (YTD ADRR - LY Q4 ADRR) / LY Q4 ADRR | 相对去年年底的增量，衡量今年起步动能 |
| **DQoQ Rev%** | (CQ Rev - LQ Rev) / LQ Rev | 季环比变化，看增长节奏 |
| **YoY Usage Growth%** | (CY YTD Usage - LY YTD Usage) / LY YTD Usage | 用量同比增长率 |
| **Usage DMoM%** | (CM Usage ADRR - LM Usage ADRR) / LM Usage ADRR | 用量月环比 |

### 1.3 结构类指标

| 指标 | 计算公式 | 说明 |
|---|---|---|
| **Rev vs Usage 剪刀差** | YoY Rev Growth% - YoY Usage Growth% | 正 = 增长质量好（高价值服务占比↑）；负 = 折扣加大或低价服务增长快 |
| **CSC Rev Mix%** | 各板块(C2C/C2G/G2C/HK) Rev / Total Rev | 客户板块收入占比 |
| **CSC Usage Mix%** | 各板块 Usage / Total Usage | 客户板块用量占比 |
| **YoY Mix Δ** | CY Mix% - LY Mix% | 结构变化方向和幅度 |

### 1.4 账号动态指标

| 指标 | 计算公式 | 说明 |
|---|---|---|
| **Accelerator 贡献** | Σ(MoM ADRR 增量 > 0 的账号的增量) | 月环比正向贡献金额 |
| **Decelerator 拖累** | Σ(MoM ADRR 增量 < 0 的账号的减量) | 月环比负向拖累金额 |
| **净效应** | Accelerator 贡献 - |Decelerator 拖累| | 正 = 账号层面整体在增长 |
| **New Account Incremental$** | 新客户（LY Q4 ADRR=0）的 YTD ADRR 贡献 | 新客获取效果 |
| **Existing Account Δ ADRR** | 存量客户 CY ADRR - LY Q4 ADRR | 存量客户增长/流失情况 |

### 1.5 基础度量

| 指标 | 定义 | 说明 |
|---|---|---|
| **ADRR** | Average Daily Run Rate = 期间 Rev / 天数 | 日均收入率，消除月天数差异 |
| **Target** | 年度/季度/月度收入目标（WW 下达） | Quota，不可由 BU 自行更改 |
| **SRP Rev** | 月度正式发布的标准收入 | 滞后但准确 |
| **CDER Rev** | 日估收入（全 charge type 之和） | 及时但波动 |
| **Usage** | Net Usage 收入（biz_charge_type_group = 'Net Usage'） | 通常 > Revenue（因为 Rev 包含 EDP Discount 等扣减） |

---

## 二、Pipeline Dashboard 指标 [CMHK_PIP_01]

### 2.1 覆盖率指标

| 指标 | 计算公式 | 说明 |
|---|---|---|
| **Pipeline Coverage** | Total Weighted Pipeline / Remaining Gap | ≥3x 健康，2-3x 需关注，<2x 危险 |
| **Remaining Gap** | Target - Already Achieved (SRP) | 还需要多少才达标 |
| **Weighted Pipeline** | Σ(Opp Amount × Win Probability) | 按赢率加权的管道总额 |
| **In FCST 占比** | In FCST Pipeline / Total Pipeline | 越高 = Forecast 越可靠 |

### 2.2 健康度指标

| 指标 | 计算公式 | 说明 |
|---|---|---|
| **Pipeline Aging** | 各年龄段（<90d / 90-180d / >180d）的金额分布 | >180d 占比高 = 管道虚胖（"僵尸 Pipeline"） |
| **Stalled Opps** | 停留在同一 Stage > 90天的 Opportunity 数 & 金额 | 来自 Opp Management Standards 定义 |
| **Pipeline Defects** | 违反 Opp Management Standards 的商机数 | 必填字段缺失、Stage 不合规等 |

### 2.3 效率指标

| 指标 | 计算公式 | 说明 |
|---|---|---|
| **Velocity** | (# Opps × Avg Deal Size × Win Rate) / Avg Sales Cycle | 管道转化速度 |
| **Win Rate** | Won Opps / (Won + Lost) | 赢单率 |
| **Avg Sales Cycle** | 从 Opp Created → Closed Won 的平均天数 | 越短越好 |
| **Conversion Rate** | Pipeline → Won 的转化率 | 按金额或数量 |
| **New Pipe Generation** | 当月新创建的 Opp 总金额 | 管道补充速度 |

### 2.4 分布指标

| 指标 | 说明 |
|---|---|
| **Stage Distribution** | 各 Stage 的 Opp 数量和金额（应呈漏斗型） |
| **BU Pipeline Split** | 各 BU 的 Pipeline 金额和 Coverage |
| **Deal Size Distribution** | 大/中/小 Deal 的数量和金额分布（判断集中度风险） |

### 2.5 输入指标

| 指标 | 说明 |
|---|---|
| **Customer Engagements** | SA 客户互动次数 |
| **POC/Workshop 数量** | PoC 和 Workshop 完成数 |
| **Technical Win** | 技术验证成功数 |
| **Leads Generated** | 市场活动产生的线索数 |
| **MQL → SQL Conversion** | 市场线索 → 销售线索的转化率 |

---

## 三、Forecast Dashboard 指标 [CMHK_FCST_01]

### 3.1 Guidance 核心指标

| 指标 | 计算公式 | 说明 |
|---|---|---|
| **FY Target** | WW 下达的全年目标 | 锚定值 |
| **Current Forecast** | Baseline + Net Flow-in - Flow-out | 当前预测值 |
| **Gap** | Target - Forecast | 正 = 有缺口，负 = 超额 |
| **FY Forecast Attn%** | Forecast / Target | 预测达成率 |

### 3.2 Baseline 指标

| 指标 | 计算公式 | 说明 |
|---|---|---|
| **Baseline** | YTD SRP + Existing Run Rate × 剩余天数 | 存量业务"平跑"推测 |
| **Organic 增量** | 存量客户自然用量增长（无需新 Deal） | 通常是技术驱动（如用量自然增长） |
| **Organic 降量** | 存量客户自然用量衰退 | 合同到期、迁移、降本等 |

### 3.3 增量指标

| 指标 | 计算公式 | 说明 |
|---|---|---|
| **Flow-in** | In FCST + In FCST at Risk 的 Opp 金额（按落地月加权） | 新 Deal 带来的增量收入 |
| **Flow-out (Risk)** | Risk MRR / 30 × 季度内天数 | 有迁移/流失风险的收入 |
| **Net 增量** | Flow-in - Flow-out | 新 Deal 净贡献 |
| **Upside** | SFDC 标记为 Upside / Strong Upside 的 Deal | 潜在增量，未纳入正式 Forecast |

### 3.4 Fine-tune 指标

| 指标 | 说明 |
|---|---|
| **Call Up** | 上调 Forecast（好消息多于预期） |
| **Call Down** | 下调 Forecast（坏消息多于预期） |
| **CDER vs Forecast 偏差** | 实际跑数 vs 预测的偏差，触发 Fine-tune |

### 3.5 结构拆分

| 指标 | 说明 |
|---|---|
| **CORE vs GENAI** | 传统业务 vs AI 业务分开看 Forecast |
| **By Quarter** | Q1（已发生）vs Q2-Q4（预测）拆分 |
| **By BU** | 各 BU 的 Target / Forecast / Gap |
| **Launch Date Distribution** | 各 Deal 预计落地月份，集中在季末 = 高风险 |

---

## 四、跨 Dashboard 复合指标

| 指标 | 涉及 Dashboard | 计算 / 判断逻辑 |
|---|---|---|
| **全绿** | Rev + Pipe + Fcst | Rev Attn ≥ 100% + Coverage ≥ 3x + Forecast ≥ Target |
| **短期 OK 长期危险** | Rev + Pipe | Rev Attn ≥ 100% + Coverage < 2x |
| **执行问题** | Rev + Pipe | Rev Attn < 100% + Coverage ≥ 3x（管道够但转化不行） |
| **紧急干预** | Rev + Pipe + Fcst | Rev Attn < 100% + Coverage < 2x + Forecast Gap 大 |
| **增长质量** | Rev | Rev Growth > Usage Growth（正向剪刀差）+ 新客存量双增 + 不依赖单一大客户 |

---

## 五、指标阈值速查

| 指标 | 🟢 健康 | 🟡 关注 | 🔴 危险 |
|---|---|---|---|
| YTD Attn% | ≥ 100% | 95-100% | < 95% |
| Pipeline Coverage | ≥ 3x | 2-3x | < 2x |
| Pipeline Aging (>180d 占比) | < 20% | 20-40% | > 40% |
| Win Rate | ≥ 30% | 15-30% | < 15% |
| DMoM% | 正值 | 0 附近 | 持续负值 |
| Forecast Attn% | ≥ 100% | 95-100% | < 95% |
| New Pipe Generation MoM | 增长 | 持平 | 持续下降 |

> ⚠️ 阈值为经验参考值，具体标准可能因 BU、季节、战略优先级不同而调整。
> 通过后续实战中 qiuyac 或 leadership 的反馈持续校准。

---

## 六、待补充 🚧

以下指标已在 Dashboard 上见过但定义尚不完全确定，待通过实际 Dashboard 截图或 qiuyac 教学补充：

- [ ] GenAI Revenue 的具体筛选条件（genai_flag 的分类逻辑）
- [ ] EDP Discount 在 Dashboard 上的呈现方式
- [ ] SRP 发布的具体时间点（每月几号？）
- [ ] Dashboard Filter 的默认值和可选项
- [ ] Forecast 的 Win Probability 权重表（各 Stage 对应的 %）
- [ ] SA Activity 指标的具体数据源（是 SFDC Activity 还是另一个系统）

---

*文档版本：v1.0 | 创建日期：2026-03-17 | 维护者：DataRetriever 🐕*
*更新策略：每次从 Dashboard 截图、qiuyac 教学或实际数据查询中学到新指标时更新*
