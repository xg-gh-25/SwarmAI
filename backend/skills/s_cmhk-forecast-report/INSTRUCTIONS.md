# Forecast Report — Full Workflow

Generate forecast vs baseline analysis for CMHK leadership. 4-tab layout
with waterfall visualization, monthly trajectory, and opportunity/risk detail.

## Step 0: Credential Pre-Flight

Before running any script that uses DataProxy SDK, verify AWS credentials:
```bash
aws sts get-caller-identity --profile cmhk-platform 2>&1
```
- If **account = 210347900436** → proceed
- If **ExpiredToken** or wrong account → tell user: `ada credentials update --account 210347900436 --role Admin --profile cmhk-platform --provider isengard`
- Do NOT attempt to run generator scripts with expired credentials

---

## Execution Transparency Protocol (ETP)

**MANDATORY:** Output these 4 sections in chat when running this skill.

### 1. BRIEFING (before execution)
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 CMHK Forecast Report — {SCOPE} (Cycle: {cycle_id})
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Scope: {scope} | Cycle: {latest cycle or user-specified}
  Target: FY{year} ${target}M | Baseline: ${baseline}M | Gap: ${gap}M
  Data path: MCP direct_query → Athena (cluster=fast)
  Tables: forecast_reporting_tool, fact_pipeline_d, fcst_baseline_detail, fact_estimated_revenue
  Tabs: Overall | CORE | GenAI (+ Bedrock/Non-Bedrock split) | Breakdown
  GenAI split: Bedrock vs Non-Bedrock via genai_product_group_gcr
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 2. FETCH TRACE (after generator)
```
## ✦ FETCH [{N} queries]
  Baseline attainment — {rows} rows
  Gap decomposition (top accounts) — {rows} rows
  CORE vs GenAI forecast split — {rows} rows
  GenAI Bedrock vs Non-Bedrock split — {rows} rows
  Pipeline coverage for gap — {rows} rows
  Cycle comparison (if requested) — {rows} rows
  Total: {time}s | {failures} failures
```

### 3. ANALYSIS LOGIC (Playbooks: PB4 + PB5)
```
## ✦ ANALYSIS [PB4: Pipeline + PB5: Attainment]

  Forecast Health (PB5):
    FY Baseline Attn: {X%} → {🟢/🟡/🔴} (threshold: ≥100%/95-100%/<95%)
    Gap: ${X}M ({positive=surplus/negative=shortfall})

  Pipeline Coverage (PB4):
    Coverage Narrow: {X}x → {🟢/🟡/🔴} (In Forecast ARR / Gap, ≥3x/2-3x/<2x)
    Coverage Broad: {X}x → {🟢/🟡/🔴} (Total Open / Gap, ≥2x/1.5-2x/<1.5x)
    Combined signal: "{pattern}" ({condition})

  Gap Attribution:
    Top gap accounts: {account1} ${X}M, {account2} ${X}M
    Recovery feasibility: {high/medium/low} — {reason}
```

### 4. QUALITY GATE + DELIVER
```
## ✦ QUALITY [IQ1-IQ5] — {all pass / N failed}
## ✦ DELIVER — {output_path} ({time}s)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## 🚨 CRITICAL: Workflow — IN1 Single Continuous Flow

> Standard: TECH.md `IN1 — Insights Pattern`. All CMHK report skills follow this.

```
1. Run generator.py → HTML shell + .insights_data.json
2. Read .insights_data.json → YOU analyze the data (Playbook: PB4 + PB5)
3. Generate per-tab insights dict (your judgment, not another model)
4. Inject insights into HTML (render_insights_section)
5. Open final report — DONE
```

**All steps in ONE response. No pausing. No asking "want insights?".**
**NEVER present a report without insights.** You ARE the LLM — you read data, judge, write insights inline.
Insights explain WHY the gap exists, WHO owns the risk, and WHAT to accelerate.

**Analysis Playbooks for this skill:** PB4 (Pipeline) + PB5 (Attainment) — see TECH.md.

## Step 1: Determine Scope & Cycle

Parse the user's message for:
- **Cycle**: specific (`fcst_2026_04`) or "latest" (default — picks latest PUBLISH cycle)
- **Scope**: CMHK (default), or a BU hierarchy ID like `GCR/RFHC`

## Step 2: Run Generator

```bash
DATAPROXY_API_KEY="<key>" python scripts/generator.py \
  --cycle latest --scope CMHK [--output DIR]
```

The script handles:
1. Find latest published forecast cycle (or use specified cycle_id)
2. Fetch forecast view (monthly data: fbr, fcst, target, attn, yoy)
3. Fetch forecast baseline (fyTarget, ytdFbr, royRevenue, fcstGap, royRisk, royOpp)
4. Fetch top opportunities and risks from baseline tables
5. Render 4-tab HTML report
6. Save to `Projects/CMHK_SalesIntel/outputs/`

## Step 3: Present Results

```
open /path/to/output/2026-05-02-1500-forecast-cmhk-fcst_2026_04.html
```

Key findings to highlight:
- Target attainment % (ytd + forecast vs target)
- Forecast gap ($ and %)
- Top opportunities contributing to gap close
- Top risks threatening forecast

## Tabs

| Tab | Content |
|-----|---------|
| **Overall** | Waterfall (Target→Organic→Flow-in→Flow-out→Forecast), monthly trajectory table, KPI cards |
| **CORE** | CORE-specific monthly data (fbr, fcst, target, attn) |
| **GenAI** | GenAI-specific monthly data |
| **Breakdown** | Top opportunities by contribution, top risks by impact |

## Step 3: Generate Per-Tab Executive Insights (MANDATORY before presenting)

**Audience:** Ops / Rob — Forecast call 前看
**Focus:** 每个 tab 有独立设计的 insights，聚焦该 tab 数据的解读
**语言:** 中文

After forecast + baseline data fetched, produce **per-tab** insights:

**⚠️ SCHEMA: `contributors` must be array of OBJECTS, not strings!**
✅ "contributors": [{"name": "BU/Account Name", "delta": "+$1.2M", "pct": "+15%"}]
❌ "contributors": ["RFHC", "ISV"]  ← WILL CRASH renderer
Each contributor object: `name` (required), `delta`, `pct`, `trend`, `owner_name`, `owner_alias` (all optional).

```python
insights = {
    "overall": {
        "summary": "Gap 规模 + 覆盖率判断（≤60字）",
        "highlights": [...],   # Upside 在兑现 / pipeline 转化
        "lowlights": [...],    # Gap 扩大 / committed slip
        "actions": [...],      # 加速哪些 deal
        "outlook": "Gap 补齐可能性（≤40字）"
    },
    "core": {
        "summary": "CORE 业务 forecast 健康度",
        "highlights": [...],   # CORE organic growth signals
        "lowlights": [...],    # Churn / flow-out risks
        "actions": [...]       # Retention actions
    },
    "genai": {
        "summary": "GenAI forecast 信心度",
        "highlights": [...],   # GenAI upside converting
        "lowlights": [...],    # GenAI deal slip / dependency risk
        "actions": [...]       # Landing acceleration
    },
    "breakdown": {
        "summary": "Top opportunities / risks 判断",
        "highlights": [...],   # Top opportunities 进展
        "lowlights": [...],    # Top risks 恶化
        "actions": [...]       # Deal-level: "找 {owner} 加速 {opp_name}"
    }
}

render(data, insights=insights)
```

### Per-Tab Focus Rules

| Tab | Focus | Must-Have |
|-----|-------|-----------|
| **overall** | 全局 gap + 能否达标 | `outlook` 必须填 |
| **core** | CORE organic 健康度 | flow-out risk signals |
| **genai** | GenAI forecast 信心 | deal dependency + landing progress |
| **breakdown** | Deal-level actions | `contributors` = territory_owner from baseline |

### Analysis Framework (12 points)

#### Overall Tab
1. **Gap 轨迹** — 是在收窄还是扩大？对比上月 cycle 的 fcstGap
2. **Pipeline 覆盖率** — royOpp / fcstGap >= 3x? 如果不够 = 缺 pipeline 不缺 forecast
3. **风险集中度** — top 3 risks 占 royRisk 的多少？集中 = 可管理

#### CORE Tab
4. **Organic 韧性** — baseline organic growth 是否扭转去年趋势？
5. **Flow-out 归因** — 哪些客户在 churn？是迁移还是优化？找 owner
6. **ROY 信心** — CORE ROY forecast 比 YTD run rate 高还是低？

#### GenAI Tab
7. **Landing 进度** — 多少 GenAI pipeline 已 launch vs 还在 qualified？
8. **客户集中度** — top 3 GenAI 客户占比？如果 >50% = 单点风险
9. **产品线 (Bedrock Split)** — Bedrock vs Non-Bedrock 的 revenue 占比变化（用 `genai_bedrock_split` 数据）。Bedrock 包含 Bedrock/AgentCore/Knowledge Bases。趋势：Bedrock 占比是否在上升？

#### Breakdown Tab
10. **Deal velocity** — top opportunities 在 pipeline 停了多久？stage >= 120 days = stall
11. **Risk mitigation** — top risks 有 mitigation plan 吗？territory_owner 知道吗？
12. **New vs Existing** — top opps 来自新客 还是 existing expand？

### Quality Gate (BLOCKING — 不通过不交付)

- [ ] 每条 insight 有 **opportunity/risk name** + **territory_owner** (来自 baseline tables)
- [ ] 每条 root_cause 是 **判断** ("committed slip 因为 POC 延期") 不是 **推迟** ("需确认")
- [ ] 每条 action 有 **具体人名(alias)** + **做什么** + **为什么紧急**
- [ ] summary 是 **判断句** 不是 **数字复述**

### Common Rules (all tabs)
- **Contributors = opportunities/risks** — baseline tables 直接有 `territory_owner`
- **Gap 是核心数字** — fcstGap / royRisk / royOpp 来自 baseline API
- **Pattern 适配** — `sustained_decline` = gap 连续扩大; `trend_reversal` = gap 开始收窄
- **Revenue/Usage 涨幅不同是正常的** — EDP/promotion 导致，不标记异常
- Legacy flat format 向后兼容（所有 tab 显示相同内容）
- `insights=None` 时报告正常渲染，无 insights section

---

## Scripts & Entry Points

| Script | Purpose | Args |
|--------|---------|------|
| `scripts/generator.py` | Full orchestration | `--cycle {id\|latest}` `--scope {CMHK\|BU}` `[--output DIR]` |

## Data Sources

| Source | Required? | Failure |
|--------|-----------|---------|
| Forecast API (cycles + view) | ✅ Required | Abort |
| Forecast API (baseline) | ✅ Required | Abort |
| Baseline tables (gcr_sales) | ⚠️ Optional | Skip opportunity/risk detail |
| Revenue for YTD cross-check | ⚠️ Optional | Skip validation |

## Quality Rules

1. Forecast API requires `region: "CHINA_REGION"` in body
2. Baseline tables in `gcr_sales` database, cn-northwest-1
3. Hierarchy IDs use `GCR/` prefix (e.g., `GCR/RFHC`)
4. `fcst_type` is `CORE` or `GENAI` in baseline tables
5. CSS-only HTML — no external dependencies
