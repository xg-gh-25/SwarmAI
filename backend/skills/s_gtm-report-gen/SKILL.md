---
name: gtm-report-gen
description: >
  Generate GTM reports from pre-computed data. Takes a JSON data file and renders
  Rob/CEO summary, GM detailed HTML, or working-level Excel.
  TRIGGER: "generate GTM report", "生成GTM报告", "render report from data".
  DO NOT USE: for data collection (use s_industry-gtm-analysis).
---

# GTM Report Generator

Pure template rendering -- takes pre-computed data JSON and generates HTML/Excel reports.
No data fetching. Used when data is already available (e.g., from a previous analysis
or manual compilation).

## Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `data` | Yes | Path to JSON file with pre-computed analysis data |
| `template` | No | `rob` / `gm` / `excel` / `all` -- default `all` |
| `product` | No | AWS product name (for branding/knowledge lookup), default `agentcore` |
| `output` | No | Output directory, default `/tmp/gtm-report/` |

## Usage

```bash
python3 skills/s_gtm-report-gen/generator.py \
  --data /tmp/analysis_data.json \
  --template all \
  --product agentcore \
  --output /tmp/gtm-report/
```

## Flow

1. **Read JSON** -- Load the pre-computed data file
2. **Load product knowledge** -- Read from knowledge dir (product components, differentiators)
3. **Render template(s)** -- Dispatch to Rob summary, GM detailed, and/or Excel builder
4. **Save files** -- Write outputs to the output directory

## Data JSON Format

The input JSON must match the format produced by `s_industry-gtm-analysis/analyzer.py`:

```json
{
  "bu_name": "AUTO & MFG",
  "product": "agentcore",
  "total_accounts": 202,
  "total_ttm": 61684232,
  "total_genai": 3900216,
  "total_bedrock": 1451904,
  "auto_count": 46,
  "mfg_count": 156,
  "top_accounts": [{"name": "...", "ttm": 1000, "genai": 100, "bedrock": 50}],
  "bedrock_accounts": [{"name": "...", "bedrock": 500}],
  "categories": {"消费电子": {"count": 40, "ttm": 18000000}},
  "plays": [],
  "scenarios": [],
  "competitive": []
}
```

For Excel template, provide an `accounts` key with the full account list.

## Output

```
{output}/
├── rob_summary.html     Rob/CEO one-page summary
├── gm_detailed.html     GM detailed multi-tab report
└── gtm_analysis.xlsx    Working-level Excel (if accounts data provided)
```

## Integration

This skill reuses templates from `s_industry-gtm-analysis`:
- `templates/rob_summary.py` -- Rob/CEO HTML summary
- `templates/gm_detailed.py` -- GM detailed HTML with tabs
- `templates/excel_builder.py` -- Multi-sheet Excel workbook

The data collection pipeline is:
1. `s_industry-gtm-analysis` -- pulls data from Sentral + Athena
2. `s_customer-ai-research` -- enriches with web research (optional)
3. `s_gtm-report-gen` -- renders reports from the collected data
