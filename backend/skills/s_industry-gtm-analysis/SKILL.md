---
name: industry-gtm-analysis
description: >
  Generate multi-level industry GTM analysis reports for any AWS product × any BU.
  Pulls customers from Sentral, revenue from DataProxy Athena, optional web research.
  Outputs: Rob/CEO summary HTML, GM detailed HTML (tabs+charts), working Excel.
  TRIGGER: "GTM analysis", "GTM report for [BU]", "[行业]客户分析", "industry analysis for [product]".
  DO NOT USE: for weekly revenue reports (use s_cmhk-weekly-report).
---

# Industry GTM Analysis

为任意 AWS 产品 × 任意 BU/行业 生成多层级 GTM 分析报告。

## 能力范围

| 层级 | 说明 | 模板 | 格式 |
|------|------|------|------|
| Rob/CEO | 一页摘要：关键数字 + Top 10 + 推荐行动 | `rob_summary.py` | HTML (中文) |
| GM | 完整分析：6 Tab 交互式报告 + 图表 | `gm_detailed.py` | HTML (中文) |
| 工作级 | 全量客户明细 + GTM Plays + 场景分析 | `excel_builder.py` | Excel (.xlsx) |

## 使用流程

### Step 1: Agent 从 Sentral 拉客户 (MCP 调用)

Agent 使用 `mcp__aws-sentral-mcp__search_accounts` 按 territory + T-shirt size 查客户:

```
search_accounts:
  condition: {"operator":"AND","conditions":[
    {"field":"territory_Lookup__r.name","operator":"CONTAINS","value":"<BU_TERRITORY>"},
    {"field":"awsci_customer.customerRevenue.tShirtSize","operator":"EXACT_MATCH","value":"<SIZE>"}
  ]}
  limit: 100
```

对 XXL, XL, L 分别查，合并去重。保存为 JSON:
```json
[{"id":"001xxx","name":"Company","website":"x.com","size":"XXL","owner":"Name","geo":"GCR"}]
```

保存到: `/tmp/gtm_accounts_{bu}.json`

### Step 2: 调用 analyzer.py 生成报告

```bash
python3 skills/s_industry-gtm-analysis/analyzer.py \
  --bu "AUTO & MFG" \
  --product agentcore \
  --tshirt L \
  --accounts /tmp/gtm_accounts_automfg.json \
  --template all \
  --output /tmp/gtm-output/
```

### Step 3: Agent 做联网调研 (可选)

如果 `--research full`，Agent 在报告生成后做联网搜索:
- 搜每个客户的 AI/Agent 场景
- 结果缓存到 `knowledge/cache/{customer}_{date}.json`
- 更新 Excel 的场景列

## 参数

| 参数 | 必须 | 说明 |
|------|------|------|
| `--bu` | ✅ | sh_l3 BU 名，如 "AUTO & MFG", "FSI-DNB", "ISV & SUP" |
| `--product` | ✅ | AWS 产品名，如 "agentcore", "bedrock", "sagemaker" |
| `--tshirt` | ❌ | 最小 T-shirt (L/XL/XXL)，默认 L |
| `--accounts` | ✅ | Step 1 保存的客户 JSON 路径 |
| `--template` | ❌ | rob / gm / excel / all，默认 all |
| `--output` | ❌ | 输出目录，默认 /tmp/gtm-output/ |

## 数据源

| 数据 | 来源 | 方法 |
|------|------|------|
| 客户名单 | Sentral MCP | Agent 在 Step 1 调用 |
| TTM/GenAI/Bedrock Revenue | DataProxy Athena | analyzer.py → data.py |
| AI 场景调研 | 联网搜索 | Agent 可选 Step 3 |
| 产品知识 | knowledge/ 目录 | 预置 + 可扩展 |

## 产出位置

```
{output}/
├── rob_summary.html        Rob/CEO 一页摘要
├── gm_detailed.html        GM 完整报告 (6 Tab)
├── gtm_analysis.xlsx       工作级 Excel
└── revenue_data.json       原始 revenue 数据 (可复查)
```

## 扩展

添加新产品: 在 `knowledge/` 下创建 `{product}.md`，定义组件/能力/场景映射。
analyzer.py 自动读取对应产品知识文件。
