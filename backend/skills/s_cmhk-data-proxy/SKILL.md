---
name: CMHK Data Proxy
description: >
  Query CMHK revenue, usage, forecast, and account data via the DataProxy API.
  Covers Athena (fact_estimated_revenue), GCR Sales Data API (forecast cycles,
  baselines, inputs), hierarchy permissions, and report publishing.
  TRIGGER: "revenue", "usage", "CMHK data", "weekly numbers", "forecast",
  "BU revenue", "GenAI revenue", "top accounts", "RFHC numbers", "周报数据",
  "收入", "用量".
  DO NOT USE: for generating full HTML reports (that stays on DataRetriever cron),
  for non-GCR data, or for Sentral/SFDC opportunity data (use aws-sentral-mcp).
version: "1.0.0"
---

# CMHK Data Proxy

Query GCR/CMHK revenue, usage, forecast, and account data directly from Athena
and the GCR Sales Data API. Data source: `fact_estimated_revenue` table
(Athena, cn-north-1, `rl_quicksight_reporting` database).

## Quick Start

```python
# All queries go through this pattern:
import requests, json

PROXY_URL = "https://thuedsrtpc.execute-api.cn-northwest-1.amazonaws.com.cn/prod/"
PROXY_KEY = "zV76dHel_s61cwljvPkFnNtCh7nhFS0XhImzteuVRfw"

def data_proxy(payload):
    resp = requests.post(
        PROXY_URL,
        headers={"Content-Type": "application/json", "x-api-key": PROXY_KEY},
        json=payload,
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()
```

## Workflow

### Step 1: Determine what the user wants

| User asks about | Query type | Go to |
|---|---|---|
| Revenue / usage by BU, week, account | Athena query | Step 2 |
| Forecast / baseline / target / gap | Forecast API | Step 3 |
| Account list / permissions / hierarchy | Hierarchy API | Step 4 |
| Report metadata / download link | Report query | Step 5 |
| Health check / connectivity | Health | `{"action": "health"}` |

### Step 2: Athena Queries (Revenue & Usage)

**Action:** `athena`

```python
result = data_proxy({
    "action": "athena",
    "query": "<SQL>",
    "database": "rl_quicksight_reporting",
    "region": "cn-north-1",
    "max_wait": 60,
})
# Returns: {"columns": [...], "rowCount": N, "rows": [{...}, ...]}
```

**Optional:** Pass `"alias": "<login>"` to execute under that user's Lake Formation
row-level filter (e.g. `"alias": "zhangaz"` only sees ISV & SUP data).

#### Core Query Templates

**Latest available week:**
```sql
SELECT MAX(ar_week_start_date) as latest_week
FROM fact_estimated_revenue
WHERE fbr_flag = 'Y' AND sh_l1 = 'GCR'
```

**Revenue by BU (one week):**
```sql
SELECT sh_l3,
       ROUND(SUM(total_sales_revenue)/1e6, 2) as rev_millions
FROM fact_estimated_revenue
WHERE fbr_flag = 'Y' AND sh_l1 = 'GCR'
  AND ar_week_start_date = DATE '{week_start}'
GROUP BY sh_l3
ORDER BY rev_millions DESC
```

**Usage by BU (one week):**
```sql
SELECT sh_l3,
       ROUND(SUM(total_sales_revenue)/1e6, 2) as usage_millions
FROM fact_estimated_revenue
WHERE fbr_flag = 'Y' AND sh_l1 = 'GCR'
  AND biz_charge_type_group = 'Net Usage'
  AND ar_week_start_date = DATE '{week_start}'
GROUP BY sh_l3
ORDER BY usage_millions DESC
```

**GenAI vs CORE split:**
```sql
SELECT genai_flag,
       ROUND(SUM(total_sales_revenue)/1e6, 2) as rev_millions
FROM fact_estimated_revenue
WHERE fbr_flag = 'Y' AND sh_l1 = 'GCR'
  AND ar_week_start_date = DATE '{week_start}'
GROUP BY genai_flag
```

**Top accounts by revenue (for a specific BU):**
```sql
SELECT sfdc_account_name,
       ROUND(SUM(total_sales_revenue)/1e6, 2) as rev_millions
FROM fact_estimated_revenue
WHERE fbr_flag = 'Y' AND sh_l1 = 'GCR'
  AND sh_l3 = '{bu_name}'
  AND ar_week_start_date = DATE '{week_start}'
  AND LOWER(TRIM(sfdc_account_name)) <> 'unknown'
GROUP BY sfdc_account_name
ORDER BY rev_millions DESC
LIMIT 10
```

**WoW comparison (current week vs previous week):**
```sql
SELECT sh_l3,
       ROUND(SUM(CASE WHEN ar_week_start_date = DATE '{cw_start}' THEN total_sales_revenue ELSE 0 END)/1e6, 2) as cw_rev,
       ROUND(SUM(CASE WHEN ar_week_start_date = DATE '{pw_start}' THEN total_sales_revenue ELSE 0 END)/1e6, 2) as pw_rev
FROM fact_estimated_revenue
WHERE fbr_flag = 'Y' AND sh_l1 = 'GCR'
  AND ar_week_start_date IN (DATE '{cw_start}', DATE '{pw_start}')
GROUP BY sh_l3
ORDER BY cw_rev DESC
```

**Multi-week trend (6 weeks):**
```sql
SELECT ar_week_start_date,
       ROUND(SUM(total_sales_revenue)/1e6, 2) as rev_millions
FROM fact_estimated_revenue
WHERE fbr_flag = 'Y' AND sh_l1 = 'GCR'
  AND ar_week_start_date >= DATE '{six_weeks_ago}'
GROUP BY ar_week_start_date
ORDER BY ar_week_start_date
```

### Step 3: Forecast API

**List forecast cycles:**
```python
result = data_proxy({
    "action": "api",
    "method": "GET",
    "path": "/api/v1/forecast/cycles",
    "query": {"year": "2026"}
})
# Returns: {"body": {"forecastCycles": [{"cycleId": "fcst_2026_03", "status": "PUBLISH", ...}]}}
```

**Forecast view (comprehensive monthly/quarterly/yearly, Core+GenAI):**
```python
result = data_proxy({
    "action": "api",
    "method": "POST",
    "path": "/api/v1/forecast/fcst_2026_03/view",
    "query": {"region": "OVERALL"},
    "body": {"hierarchy": {"hierarchyId": "GCR/RFHC"}}
})
```

**Forecast baseline (target, YTD FBR, ROY estimates, gap):**
```python
result = data_proxy({
    "action": "api",
    "method": "POST",
    "path": "/api/v1/forecast/fcst_2026_03/baseline/query",
    "query": {"region": "OVERALL"},
    "body": {"parentHierarchy": {"hierarchyId": "GCR"}}  # get all L2 BUs
})
```

**Forecast input (monthly forecast by Core/GenAI, with overrides):**
```python
result = data_proxy({
    "action": "api",
    "method": "POST",
    "path": "/api/v1/forecast/fcst_2026_03/input/query",
    "body": {"hierarchy": {"hierarchyId": "GCR/RFHC"}}
})
```

### Step 4: Hierarchy & Permissions

**Get user's data access permissions:**
```python
result = data_proxy({
    "action": "lf_get_filter",
    "alias": "zhangaz"
})
# Returns: {"alias": "zhangaz", "full_access": false,
#           "filter_expression": "sh_l3 = 'ISV & SUP'", ...}
```

**Provision/revoke Lake Formation access for a user:**
```python
# Provision (creates IAM role + LF filters)
result = data_proxy({"action": "lf_provision", "alias": "newuser"})
# Revoke
result = data_proxy({"action": "lf_revoke", "alias": "olduser"})
# Status check
result = data_proxy({"action": "lf_status", "alias": "zhangaz"})
```

### Step 5: Report Metadata

**Query published reports (with presigned S3 download URLs):**
```python
result = data_proxy({
    "action": "os_query",
    "cycle_year": "2026",
    "cycle_week": "14",
    "hierarchy_name": "RFHC",
    "presign": True,
    "presign_expires": 3600,
})
# Returns: {"reports": [{"presigned_url": "https://...", "report_metadata": {...}}]}
```

**Upload report to S3 (presigned PUT):**
```python
# Step 1: get upload URL
url_result = data_proxy({
    "action": "os_get_upload_url",
    "s3_key": "reports/weekly-revenue-report/2026-W15/rfhc.html",
    "content_type": "text/html",
})
# Step 2: PUT file content to the presigned URL
import requests
with open("rfhc.html", "rb") as f:
    requests.put(url_result["presigned_url"], data=f.read(),
                 headers={"Content-Type": "text/html"})
```

## Data Model Reference

### Key Fields (fact_estimated_revenue)

| Field | Type | Description |
|---|---|---|
| `ar_week_start_date` | date | Week start (Sunday). Main time dimension for weekly queries |
| `ar_date` | date | Daily granularity |
| `ar_month_start_date` | date | **Partition field**. Always include in WHERE for large scans |
| `total_sales_revenue` | decimal | **The only metric.** All KPIs derive from filtering + summing this |
| `fbr_flag` | Y/N | Always filter `= 'Y'` (Fact Base Revenue, excludes GAAP adjustments) |
| `sh_l1` | string | Always `'GCR'` for our queries |
| `sh_l2` | string | Division: FSI-DNB, HK, INDUSTRY, SMB, STRATEGIC |
| `sh_l3` | string | Group (BU): 13 values — the main reporting level |
| `sh_l4` | string | Unit: 39 values |
| `genai_flag` | CORE/GENAI | GenAI classification |
| `biz_charge_type_group` | string | Charge type. `'Net Usage'` = Usage metric |
| `sfdc_account_name` | string | Account name (filter `<> 'unknown'` for top-N) |
| `data_refreshed_time` | timestamp | Daily ~00:10 UTC refresh |

### Key Formulas

- **Usage** = `SUM(total_sales_revenue) WHERE biz_charge_type_group = 'Net Usage'`
- **Revenue (CDER)** = `SUM(total_sales_revenue)` (all charge types, fbr_flag='Y')
- **Revenue = Usage - EDP Discounts - Credits + One Time Fees**
- **WoW%** = `(CW - PW) / PW * 100`

### Sales Hierarchy (sh_l3 = 13 BUs)

| sh_l3 | Full Name | Division (sh_l2) |
|---|---|---|
| AUTO & MFG | Automotive & Manufacturing | INDUSTRY |
| DNBP | DNB Pursue | INDUSTRY |
| FSI-DNB | Financial Services - Digital Native Biz | FSI-DNB |
| HK | Hong Kong | HK |
| IND GFD | Industry Greenfield | INDUSTRY |
| IND SS | Industry Self-Sufficient | INDUSTRY |
| ISV & SUP | ISV & Startup | INDUSTRY |
| MEAGS | Media, Entertainment, Ad, Games & Sports | INDUSTRY |
| NWCD | Ningxia Western Cloud Data | INDUSTRY |
| PARTNER | Partner | INDUSTRY |
| RFHC | Retail, FSI, Healthcare | INDUSTRY |
| SMB | Small & Medium Business | SMB |
| STRATEGIC | Strategic | STRATEGIC |

### Forecast Hierarchy IDs

Use `hierarchyId` in forecast API calls:
- GCR overall: `"GCR"`
- Division: `"GCR/RFHC"`, `"GCR/FSI-DNB"`, etc.
- Unit: `"GCR/RFHC/CROSS"`, `"GCR/RFHC/FSI"`, etc.
- Use `parentHierarchy` to get children: `{"hierarchyId": "GCR"}` returns all L2s

## Quality Rules

1. **Always include `fbr_flag = 'Y'`** in Athena WHERE clauses — omitting it mixes
   GAAP adjustments and inflates/deflates numbers.
2. **Always include `sh_l1 = 'GCR'`** — the table contains non-GCR data.
3. **Week starts on Sunday** (`ar_week_start_date`). A "week of Apr 5" = Apr 5 (Sun) to Apr 11 (Sat).
4. **Filter `LOWER(TRIM(sfdc_account_name)) <> 'unknown'`** in top-N account queries.
5. **Include `ar_month_start_date` in WHERE** for large full-table scans to leverage partitioning.
6. **Timeout:** Set `max_wait: 60` for simple queries, `120` for complex joins/aggregations.
7. **Numbers are in USD.** Divide by 1e6 for millions display.
8. **Do NOT modify or publish data** without explicit user confirmation — read-only by default.
   Write actions (lf_provision, os_write, ddb_write, report upload) require user approval.
9. **API key is internal.** Never expose in user-facing output or logs.
10. **Present data clearly:** Always show $ units (M/K), week dates, and BU full names.
    Format tables with alignment. Include WoW% when comparing periods.
