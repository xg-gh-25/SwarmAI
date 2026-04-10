# fact_estimated_revenue 字段字典与理解报告

> **数据库**: rl_quicksight_reporting (Athena, cn-north-1)  
> **分区字段**: ar_month_start_date (date)  
> **探索时间**: 2026-03-13  
> **数据范围**: 2025-11 ~ 2026-03 (5个月)  
> **最新刷新**: 2026-03-13 00:12:17  
> **目的**: 建立字段级理解，供 DE 团队 review 确认  
> **DE Review**: 2026-03-16 已完成，以下为更新后版本  

---

## 1. 时间维度 (7个字段)

`ar_` = **Amortized Revenue**（摊销收入），所有 revenue 都是这个口径。

| # | 字段 | 类型 | 说明 | 值域 |
|---|---|---|---|---|
| 1 | `ar_year` | int | 会计年份 | 2025, 2026 |
| 2 | `ar_week_start_date` | date | 周开始日期（**周日**） | 2025-11-02 ~ 2026-03-08 |
| 3 | `ar_date` | date | 具体日期（最细粒度的时间维度） | 每天一条 |
| 4 | `ar_month_start_date` | date | **分区字段**，月份起始日 | 2025-11-01 ~ 2026-03-01 |
| 5 | `data_refreshed_time` | timestamp | 数据刷新时间戳 | 每天约 00:10 UTC 刷新 |
| 6-8 | `week_sequence` / `month_sequence` / `year_sequence` | int | 相对时间序列编号 | week: -13~0, month: -1~1, year: 0 |

**✅ DE 确认：**
- `ar_` = Amortized Revenue
- 周起始日 = **周日（Sunday）**，每周 Sun~Sat = 7天
- `week_sequence`: 0 = 当前周，负数往前按自然周递减（-1=上周，-2=上上周...）
- `month_sequence`: 以 SRP 发布 actual rev 的月为 sequence 0（不按自然月）。当前月（估算中）= 1，最新 actual 月 = 0，更早 = -1

## 2. 时间标志位 (8个字段)

| # | 字段 | 类型 | 说明 |
|---|---|---|---|
| 9 | `is_ytd` | int (0/1) | 是否属于年初至今 |
| 10 | `is_previousyear_ytd` | int (0/1) | **去年同期的 YTD**（如今年3月15日 → 去年1月1日~3月15日） |
| 11 | `is_lastytd` | int (0/1) | **去年全年 + 今年的 YTD** |
| 12 | `is_currentmonth` | int (0/1) | 是否当月 |
| 13 | `is_currentquarter` | int (0/1) | 是否当季 |
| 14 | `is_lastmonth` | int (0/1) | 是否上月 |
| 15 | `is_past3month` | int (0/1) | 是否最近3个月内 |
| 16 | `is_past12month` | int (0/1) | 是否最近12个月内 |

**✅ DE 确认：**
- 这些标志是 **ETL 时静态写入**
- `is_previousyear_ytd` = 去年同期的 YTD
- `is_lastytd` = 去年全年 + 今年的 YTD

## 3. 销售组织层级 (7个字段: sh_l1 ~ sh_l7)

`sh` = **Sales Hierarchy**（销售组织层级）

| 层级 | 正式含义 | 基数 | 值域示例 |
|---|---|---|---|
| `sh_l1` | SH_L1 (Region) | 1 | GCR (Greater China Region) |
| `sh_l2` | **Division** | 5 | FSI-DNB, HK, INDUSTRY, SMB, STRATEGIC |
| `sh_l3` | **Group** | 13 | AUTO & MFG, DNBP, FSI-DNB, HK, ISV & SUP, MEAGS, RFHC, SMB, STRATEGIC... |
| `sh_l4` | **Unit** | 39 | AUTO, FSI, GAMES, GFD AM, HK-ENT, HK-FSI, MFG... |
| `sh_l5` | **Team** | 107 | 更细的团队划分 |
| `sh_l6` | 保留字段 | - | **当前空值，忽略** |
| `sh_l7` | 保留字段 | - | **当前空值，忽略** |

### sh_l2 全称

| sh_l2 | 全称 | 说明 |
|---|---|---|
| **FSI-DNB** | Financial Services Industry - Digital Native Business | 金融行业 + 数字原生企业 |
| **HK** | Hong Kong | 香港及台湾地区（也称 HKT） |
| **INDUSTRY** | Industry | 行业客户（下辖9个 L3 子分组） |
| **SMB** | Small & Medium Business | 中小企业 |
| **STRATEGIC** | Strategic | 战略客户（GCR 最重要的大型客户，LT 审批的固定列表） |

### sh_l3 全称（INDUSTRY 下 9 个子 BU）

| sh_l3 | 全称 | 说明 |
|---|---|---|
| **AUTO & MFG** | Automotive & Manufacturing | 汽车与制造业 |
| **DNBP** | DNB Pursue | 数字原生企业重点追踪客户（GCR VP 审批的固定列表） |
| **IND GFD** | Industry Greenfield | 行业绿地客户（Turnover≥$50M 但尚未深度覆盖的新客户） |
| **IND SS** | Industry Self-Sufficient | 行业自助客户（不需专人覆盖，自助使用） |
| **ISV & SUP** | Independent Software Vendor & Startup | 独立软件供应商 & 初创企业 |
| **MEAGS** | Media, Entertainment, Advertising, Games & Sports | 媒体、娱乐、广告、游戏与体育 |
| **NWCD** | Ningxia Western Cloud Data | 宁夏西云数据（AWS 中国运营伙伴），覆盖能源/交通/旅游/矿业/公共安全/建筑地产/环保/航空航天/农业等 |
| **PARTNER** | Partner | APN 合作伙伴（Resellers & System Integrators） |
| **RFHC** | Retail, FSI, Healthcare | 零售、金融服务、医疗健康 |

### sh_l2 → sh_l3 映射关系：

```
GCR (SH_L1)
├── FSI-DNB (Division) ──→ FSI-DNB (Group)
├── HK (Division) ────────→ HK (Group)
├── INDUSTRY (Division) ──→ AUTO & MFG, DNBP, IND GFD, IND SS, ISV & SUP, MEAGS, NWCD, PARTNER, RFHC
├── SMB (Division) ───────→ SMB (Group)
└── STRATEGIC (Division) ─→ STRATEGIC (Group)
```

**✅ DE 确认：** L6/L7 保留字段，当前未使用。
**✅ 缩写全称已补充（2026-03-17，来源：GCR Segmentation / China Regions v-team / AP Territory wiki）**

## 4. 账户体系 (14个字段)

### 4.1 账户标识

| # | 字段 | 基数 | 说明 |
|---|---|---|---|
| `account_id` | 321,520 | AWS 账号 ID |
| `account_company_name` | - | 账号关联公司名 |
| `sfdc_account_18id` | 28,696 | Salesforce 账户 18位 ID |
| `sfdc_account_name` | - | Salesforce 账户名 |
| `master_account_id` | 139,886 | 主账号 ID（Organizations payer） |
| `master_account_name` | - | 主账号名 |
| `parent_master_account_id` | 139,276 | 父级主账号（更高层聚合） |
| `parent_master_account_name` | - | 父级主账号名 |
| `master_account_owner` | 494 | 主账号 Owner（销售负责人 login） |

### 4.2 账户属性

| # | 字段 | 值域 | 说明 |
|---|---|---|---|
| `account_marketplace_group_name` | AWS, ACTS, AISPL, EUSC, Spoof for SRRP Non-biller | AWS 市场分组 |
| `account_registration_date` | date | 账号注册日期 |
| `account_first_billing_date` | date | 首次计费日期 |
| `account_first_usage_date` | date | 首次使用日期 |
| `bcp_email_domain` | 高基数 | 注册邮箱域名 |

### 4.3 账户分类

| # | 字段 | 值域 | 说明 |
|---|---|---|---|
| `master_account_type` | AWS, SFDC | ~~主账号来源类型~~ **已弃用，不用关注** |
| `parent_account_tier` | 1.Negative ~ 9.XXXL+ | 父账号收入层级（见下方详细定义） |
| `sfdc_account_tier` | 全为 NULL | Salesforce 账户层级（当前未填充） |
| `account_phase` | GREENFIELD, GREENFIELD EARLY STAGE, ENGAGED GROW, INVESTING 1, RAMPING, SCALING | 客户生命周期阶段（**来自 SFDC，Ops 手动填写**） |
| `account_segment` | ENT, ISV, SMB, SUP | 客户细分 |
| `account_subsegment` | ENT LARGE/MAJOR/MIDSIZE, ISV LARGE/MAJOR/MIDSIZE/SMALL, SMB MEDIUM/SMALL, STARTUP PRIORITY | 更细的细分 |

### ✅ parent_account_tier 定义（DE 确认）

基于 parent account 过去3个月平均 Net Usage (MRR/3) 分档：

| Tier | MRR/3 范围 |
|---|---|
| 1.Negative | < $0 |
| 2.Zero | = $0 |
| 3.Micro | < $100 |
| 4.S | < $1,000 |
| 5.M | < $10,000 |
| 6.L | < $100,000 |
| 7.XL | < $1,000,000 |
| 8.XXL | < $10,000,000 |
| 9.XXXL+ | ≥ $10,000,000 |

### 4.4 地理与行业

| # | 字段 | 基数 | 说明 |
|---|---|---|---|
| `sfdc_billing_city` | 1,299 | 计费城市 |
| `industry` | ~25 | 客户行业（Media & Entertainment, Software & Internet, Financial Services...） |
| `mdm_industry_tier1` | ~34 | MDM 行业一级分类 |
| `mdm_industry_tier2` | 高基数 | MDM 行业二级分类 |

**✅ DE 确认：**
- ACTS = China Region（西云+光环），AISPL = 印度，EUSC = 欧洲，**不用太关注**

## 5. Territory（销售区域）

| # | 字段 | 基数 | 说明 |
|---|---|---|---|
| `territory` | 523 | 销售区域代码 |
| `master_territory` | 530 | 主账号的销售区域 |

### ✅ Territory 编码规则（DE 确认 + Wiki）

**10 段式结构：**
```
Type - BU - Geo - Area - Phase - Sales Area - Sub Sales Area - Business Model - Geography - Free Text
```

- 前 4 段（Type/BU/Geo/Area）是 WW 强制字段
- 总长度限制 80 字符
- Phase 标签：100% 纯相位标 SCL/RMP/NVT/GFD，混合标 MXD
- Business Model 标签：T2K / CRD / T2K_CRD / INDIRECT
- Free Text 建议用数字（01/02/03）

### ✅ Territory 与 Sales Hierarchy 的关系

- Territory 挂在 **sh_l5 (Team)** 下面
- 一个 Team 可以有多个 Territory
- 一个 Territory 只有一个 owner

### ✅ Territory 分配逻辑

多维度综合分配，不是简单按注册地址：
- **Coverage Segmentation (DFSI)**：Deep / Focus / Scale / Indirect
- **Phase**：Greenfield / Investing / Ramping / Scaling
- **Industry**：按 GCR Industry Grouping 分配，强制行业纯度
- **Segment**：ENT / ISV / SMB / SUP 等
- **GTMC**：通过 GTM Builder 管理 parent-child 关系
- **T2K 标签**：是否为 T2K Priority Account
- **地理**：MMT territory 按 RRCC（Revenue Routing Country Code）路由
- **Indirect**：SMB 账户 Turnover<$10M 且 TTM<$10K 分配给 PTM territory
- 在 Annual Planning 期间通过 Planning Tool 完成分配

### ✅ Territory 变更

历史数据**按最新 Territory 分配回溯**，不保留历史分配。

## 6. 产品/服务维度 (4个字段)

| # | 字段 | 基数 | 说明 |
|---|---|---|---|
| `service_group` | 34 | AWS 服务分组（Compute, Storage, Database, AI, ML...） |
| `product_name` | 243 | AWS 产品名称 |
| `instance_type` | 49 | EC2 实例类型（仅 Compute 相关） |

**Top 5 产品（3月 Revenue）：**
1. Amazon EC2 — $77.6M
2. AWS Data Transfer — $9.3M
3. Amazon S3 — $7.3M
4. Aurora — $7.3M
5. AWS Support (Enterprise) — $5.7M

**✅ DE 确认：** `#N/A#` 产品名（$10.8M Revenue）= 找不到产品信息的 revenue

## 7. 收入分类维度 (4个字段)

| # | 字段 | 基数 | 说明 |
|---|---|---|---|
| `biz_charge_type_group` | 11 | 业务收费类型分组 ← **统一用这个** |
| `biz_charge_type_group_gcr` | 11 | GCR 本地分组（与上述无实质区别，仅 Usage 命名不同，下游 BA hard code 保留） |
| `charge_item_classification_name` | 17 | 收费项分类 |
| `charge_item_description_text_group` | 42 | 收费项描述分组 |

### biz_charge_type_group 构成（3月数据）：

| 类型 | Revenue | 说明 |
|---|---|---|
| **Net Usage** | +$332.6M | 净用量收入（核心指标） |
| Support One Time Fees | +$7.5M | 支持服务一次性费用 |
| Other One Time Fees | +$5.2M | 其他一次性费用 |
| SaaS Revenue Recognition | +$0.4M | SaaS 收入确认 |
| ProServe One Time Fees | +$0.2M | 专业服务 |
| Training One Time Fees | +$0.1M | 培训 |
| Refunds | -$0.5M | 退款 |
| Promotional Credits | -$2.3M | 促销抵扣 |
| Other Credits | -$3.5M | 其他抵扣 |
| Contractual Credits | -$6.5M | 合同抵扣 |
| **EDP Discounts** | **-$175.9M** | EDP 折扣（最大扣减项） |
| **= Total (CDER)** | **$157.2M** | 净收入 |

**关键公式：**
- **Usage** = `biz_charge_type_group = 'Net Usage'` 时的 `total_sales_revenue` 之和
- **Revenue (CDER)** = 所有 `biz_charge_type_group` 的 `total_sales_revenue` 之和
- **Revenue = Usage - EDP Discounts - Credits + One Time Fees**

**✅ DE 确认：**
- CDER = **Consolidated Daily Estimated Revenue**（增强版 DER，对齐 Amortized Sales Revenue actuals）
- `biz_charge_type_group` vs `biz_charge_type_group_gcr` 无实质区别，统一用前者

## 8. GenAI 相关字段 (6个字段)

| # | 字段 | 值域 | 说明 |
|---|---|---|---|
| `genai_flag` | CORE, GENAI | **主分类标记** ← 默认用这个 |
| `genai_comp_flag` | CORE, GENAI | 不同口径的 GenAI 分类（来自 WW） |
| `genai_full_stack_flag` | Y, N | 不同口径的 GenAI 分类（来自 WW） |
| `genai_product_group_gcr` | Bedrock, SageMaker AI, AI Services, Amazon EC2 - GenAI, Bedrock Knowledge Bases, Amazon Bedrock AgentCore, AWS Outposts, #N/A# | GCR GenAI 产品分组 |
| `is_sso_bedrock_gcr` | Bedrock, Non Bedrock | 是否 Bedrock 相关 |

**3月 GenAI vs CORE 对比：**
| 类型 | Revenue | 行数 | 占比 |
|---|---|---|---|
| CORE | $130.4M | 54.8M 行 | 83% |
| GENAI | $26.8M | 2.0M 行 | 17% |

**✅ DE 确认：** 几个 flag 都来自 WW，是不同口径的 GenAI 定义。默认只看 `genai_flag` 即可。

## 9. 收入口径标志 (4个字段)

| # | 字段 | 值域 | 说明 |
|---|---|---|---|
| `gross_revenue_flag` | Y, N | Y=毛收入项, N=折扣/抵扣项 |
| `bridge` | Fact Base Revenue, GAAP_ALT | 收入桥接类型 |
| `fbr_flag` | Y, N | 是否 Fact Base Revenue |
| `gsr_flag` | Y | 全为 Y（GSR） |

**关键关系：**
- `fbr_flag = Y` ↔ `bridge = 'Fact Base Revenue'` → $163.8M（主体收入）
- `fbr_flag = N` ↔ `bridge = 'GAAP_ALT'` → -$6.6M（GAAP 调整项）
- `gross_revenue_flag = Y` → $346.4M（毛收入）
- `gross_revenue_flag = N` → -$189.2M（折扣/抵扣）
- **Net = Gross + Non-Gross = $157.2M**

**✅ DE 确认：**
- `GAAP_ALT` = 向 GAAP Revenue 靠齐的 adjustment，GCR 主要是 China netdown 调整。**GCR reporting 通常不看这部分，看 Fact Base Revenue（fbr_flag=Y）**
- `gsr_flag` 目前全为 Y，都是 GSR

## 10. 地区标志 (2个字段)

| # | 字段 | 值域 | 说明 |
|---|---|---|---|
| `region` | CHN, HK | AWS 区域（中国大陆 vs 香港） |
| `china_regions_flag` | China Regions, Non China Regions | 是否中国大陆区域 |
| `csc_group` | C2C, C2G, G2C, HKT | **China Steering Committee 分组** |

### ✅ CSC Group 定义（DE 确认）

基于客户地理位置和服务消费区域分组：

| CSC Group | 定义 |
|---|---|
| **C2C** | China-to-China：中国客户用中国区 |
| **C2G** | China-to-Global：中国客户用全球区 |
| **G2C** | Global-to-China：海外客户用中国区（MNC，通过 mapping_global_mnc_account 表匹配） |
| **HKT** | Hong Kong and Taiwan：香港或台湾客户 |

分类逻辑（简化）：
```
if territory contains '-HK-' or '-TW-' → HKT
elif gtm_country = 'China' and marketplace = 'Global' → C2G
elif gtm_country = 'China' and is_mnc → G2C
else → C2C
```

## 11. 核心度量 (1个字段)

| # | 字段 | 类型 | 说明 |
|---|---|---|---|
| `total_sales_revenue` | decimal(38,8) | 销售收入金额（美元），正值=收入，负值=折扣/抵扣/退款 |

**这是整张表唯一的度量字段。** 所有指标都通过不同维度的筛选 + 聚合这个字段得到。

---

## 数据质量与一致性观察

1. **`sfdc_account_tier`** 全为 NULL — 字段存在但未填充
2. **`sh_l6`/`sh_l7`** — 保留字段，当前空值，忽略
3. **`type`** 在3月分区中全为 NULL — Actuals 和 G-ALT 只出现在历史数据中
4. **`#N/A#`** 出现在 `product_name` 和 `genai_product_group_gcr` 中 — 找不到产品信息的 revenue
5. **`gsr_flag`** 全为 Y — 目前无例外
6. **`master_account_type`** — 已弃用，不用关注
7. **`biz_charge_type_group_gcr`** — 与 `biz_charge_type_group` 无实质区别，统一用后者

---

## 仍待补充

（暂无）

---

## 核心查询模板

### 1. 周度 Revenue（CDER）
```sql
SELECT sh_l3, SUM(total_sales_revenue) as revenue
FROM fact_estimated_revenue
WHERE ar_week_start_date = DATE '2026-03-01'
  AND fbr_flag = 'Y'
GROUP BY sh_l3
```

### 2. 周度 Usage（Net Usage）
```sql
SELECT sh_l3, SUM(total_sales_revenue) as usage
FROM fact_estimated_revenue
WHERE ar_week_start_date = DATE '2026-03-01'
  AND biz_charge_type_group = 'Net Usage'
  AND fbr_flag = 'Y'
GROUP BY sh_l3
```

### 3. GenAI vs CORE 拆分
```sql
SELECT genai_flag, SUM(total_sales_revenue) as revenue
FROM fact_estimated_revenue
WHERE ar_week_start_date = DATE '2026-03-01'
  AND fbr_flag = 'Y'
GROUP BY genai_flag
```

### 4. Top 客户
```sql
SELECT parent_master_account_name, SUM(total_sales_revenue) as revenue
FROM fact_estimated_revenue
WHERE ar_week_start_date = DATE '2026-03-01'
  AND fbr_flag = 'Y'
GROUP BY parent_master_account_name
ORDER BY revenue DESC
LIMIT 20
```

---

*Report generated by DataRetriever 🐕 | 2026-03-13*  
*DE Review completed: 2026-03-16*  
*SH 缩写全称补充: 2026-03-17*  
*Status: ✅ All reviewed — no pending items*
