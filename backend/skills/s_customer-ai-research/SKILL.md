---
name: customer-ai-research
description: >
  Research customer AI Agent scenarios via web search. Outputs structured JSON/Markdown
  with each customer's AI products, Agent capabilities, and competitive positioning.
  TRIGGER: "research AI scenarios", "客户AI调研", "research [company] AI capabilities".
  DO NOT USE: for revenue data (use s_industry-gtm-analysis) or weekly reports (use s_cmhk-weekly-report).
---

# Customer AI Research

Web research on customer AI Agent scenarios. Can be called by s_industry-gtm-analysis (Step 3) or standalone.

## Input

- **Customer list**: path to a JSON file of customer names (from Sentral export) OR comma-separated names
- **Industry focus**: e.g., "automotive AI agent", "smart home IoT agent"
- **Mode**: `full` (all customers) / `incremental` (only uncached or stale >30 days) / `refresh` (force re-search)

## Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `customers` | Yes | Path to JSON list of customer names OR comma-separated names |
| `industry` | No | Focus area (e.g., "automotive AI agent", "smart home IoT agent") |
| `mode` | No | `full` / `incremental` / `refresh` — default `incremental` |
| `output` | No | Path for results — default `/tmp/ai-research-output/` |

## Usage Flow

### Step 1: Agent reads customer list

Agent reads the customer list from a JSON file or parses comma-separated names:

```json
["理想汽车", "蔚来", "小鹏", "比亚迪", "TP-LINK"]
```

Or from a Sentral export JSON (extracts `name` or `short` field from each object).

### Step 2: For each customer, run web search queries

Agent uses Tavily search tool or WebFetch to search both Chinese and English queries.

Query templates are in `knowledge/search_templates.md`. For each customer:
- `"{company} AI Agent 场景 2025 2026"`
- `"{company} 智能体 产品 agentic"`
- `"{company} OpenClaw 龙虾"`
- `"{company} AI agent product 2025 2026"`
- `"{company} agentic AI cloud platform"`

For batch industry queries:
- `"中国{industry} AI Agent 场景 2025"`
- `"{industry} AI agent China companies"`

### Step 3: Extract structured fields

For each customer, extract:

| Field | Description |
|-------|-------------|
| `company` | Company name (Chinese + English) |
| `ai_products` | List of AI products/platforms |
| `agent_scenarios` | Agent use cases and capabilities |
| `openclaw_usage` | OpenClaw / agentic platform usage |
| `cloud_provider` | Primary cloud provider(s) |
| `maturity` | AI maturity level: High / Medium / Low / Unknown |

### Step 4: Cache results

Results are cached per-customer as JSON:
```
knowledge/cache/{customer_name}_{YYYY-MM-DD}.json
```

- In `incremental` mode, skip customers with cache newer than 30 days
- In `refresh` mode, re-search all customers regardless of cache
- In `full` mode, search all customers but use cache if fresh

### Step 5: Generate output

Outputs:
```
{output}/
├── research_results.json    Consolidated JSON (all customers)
└── research_summary.md      Markdown summary table
```

## Module: researcher.py

Python helper module that handles caching and structuring. The actual web search
is performed by the agent (via Tavily/WebFetch) — this module does NOT call any
external APIs directly.

Functions:
- `load_cache(customer, cache_dir)` — returns cached result dict or None if stale
- `save_cache(customer, data, cache_dir)` — saves result with timestamp
- `is_stale(cache_path, max_age_days=30)` — checks if cache needs refresh
- `parse_research_result(raw_text, customer_name)` — extracts structured fields from raw search text
- `build_search_queries(customer_name, industry)` — generates search query strings
- `compile_report(results, output_path)` — generates Markdown summary from all results

## Integration with s_industry-gtm-analysis

This skill can be called after GTM analysis (Step 3) to enrich customer records:
1. s_industry-gtm-analysis generates the account list and revenue data
2. s_customer-ai-research researches AI scenarios for each customer
3. Results are merged back into the Excel working sheet
