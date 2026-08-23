# Track G: Data Report (XLSX)

> Data-driven decision support with branded charts and insight-per-table.
> All data represented. Sheet count = data dimension count.

## When This Track Runs

This track executes during BUILD stage when `"data_report"` is in `confirmed_tracks`
(from discovery.json). Read this file at BUILD time — you do NOT need the full INSTRUCTIONS.md.

## Inputs Required

- `content/{name}/content_package.md` — Data Layer (metrics, comparisons, time series)
- `content/{name}/discovery.json` — audience, outcome context
- Active design direction YAML — for chart/header brand colors

## Production Flow

```
Step 1: Extract data structure from content_package Data Layer
Step 2: Determine sheet structure (from content type)
Step 3: Create workbook with branded styling via openpyxl
Step 4: Apply brand colors to charts via brand_chart.py
Step 5: Validate formulas via recalc.py
Step 6: Quality verification (RP-G)
```

---

## Step 1: Data Structure Extraction

### Pre-condition Check (BLOCKING)

Read `content_package.md` Data Layer. If it is empty or has < 3 metrics:

```
⚠️ Data Layer insufficient for data_report track.
Options:
  a) Extract data from Evidence Layer / research.md (look for numbers, benchmarks, comparisons)
  b) Ask user to provide a data source (CSV, API, manual input)
  c) CHECKPOINT: "data_report confirmed but no data available — please provide data or remove track"
```

If (a) yields ≥ 3 extractable metrics → proceed.
If not → CHECKPOINT. Do NOT produce an empty XLSX.

### Extraction (when Data Layer is populated)

From `content_package.md` Data Layer, extract:
- **Metrics:** name, value, source, period → KPI summary cells
- **Comparisons:** dimensions × entities → comparison matrices
- **Time Series:** metric × time → trend charts
- **Formulas:** relationships between values → dynamic cells (NOT hardcoded)

---

## Step 2: Sheet Structure

Based on content type, determine sheets:

| Use Case | Sheet Structure |
|----------|----------------|
| Competitive analysis | Overview + Per-competitor + Scoring Matrix + Recommendation |
| Performance report | Summary KPIs + Trend Charts + Detail Tables + Actions |
| Financial proposal | Executive Summary + Projections + Assumptions + Sensitivity |
| Technical comparison | Feature Matrix + Performance Benchmarks + TCO Calculator |
| Quarterly MBR | Executive Summary + Revenue Trends + BU Breakdown + Actions |

**Sheet count = data dimension count.** One logical dimension = one sheet.
If the evidence bank has 6 independent metric groups, use 6 sheets + 1 summary.
NEVER compress into fewer sheets to "keep it simple."

---

## Step 3: Create Workbook (openpyxl)

```python
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.utils import get_column_letter
import yaml

# Load direction tokens
with open(direction_yaml_path) as f:
    direction = yaml.safe_load(f)

tokens = direction['tokens']
accent_hex = tokens['accent'].lstrip('#')
bg_hex = tokens['bg-deep'].lstrip('#')

# Brand styling constants
HEADER_FILL = PatternFill(start_color=bg_hex, end_color=bg_hex, fill_type='solid')
HEADER_FONT = Font(name='Inter', size=11, bold=True, color='FFFFFF')
ACCENT_FONT = Font(name='Inter', size=12, bold=True, color=accent_hex)
BODY_FONT = Font(name='Inter', size=10, color='2D3436')
MUTED_FONT = Font(name='Inter', size=9, color='666666', italic=True)
INSIGHT_FILL = PatternFill(start_color='F5F5F5', end_color='F5F5F5', fill_type='solid')
THIN_BORDER = Border(
    bottom=Side(style='thin', color='E0E0E0')
)
```

### Sheet 1: Executive Summary (ALWAYS first)

```python
ws = wb.active
ws.title = "Executive Summary"

# Title
ws['A1'] = "Executive Summary"
ws['A1'].font = Font(name='Inter', size=16, bold=True, color=accent_hex)

# KPI Cards (row 3-4)
kpis = [...]  # From content_package metrics
for i, kpi in enumerate(kpis):
    col = get_column_letter(i * 3 + 1)
    ws[f'{col}3'] = kpi['value']
    ws[f'{col}3'].font = Font(name='Inter', size=20, bold=True, color=accent_hex)
    ws[f'{col}4'] = kpi['label']
    ws[f'{col}4'].font = MUTED_FONT

# Summary chart (overview)
# ... add chart object (see Step 4 for brand styling)

# "So What" insight (MANDATORY per sheet)
last_row = ws.max_row + 2
ws[f'A{last_row}'] = "→ Key Insight:"
ws[f'A{last_row}'].font = ACCENT_FONT
ws[f'B{last_row}'] = "{one-sentence implication for decision-maker}"
ws[f'B{last_row}'].font = Font(name='Inter', size=10, italic=True)
```

### Body Sheets (data-driven)

For each data dimension, create a sheet:

```python
ws = wb.create_sheet(title="{Descriptive Name}")  # NEVER "Sheet2"

# Header row
for col, header in enumerate(headers, 1):
    cell = ws.cell(row=1, column=col, value=header)
    cell.font = HEADER_FONT
    cell.fill = HEADER_FILL
    cell.alignment = Alignment(horizontal='center')

# Data rows
for row_idx, data_row in enumerate(data, 2):
    for col_idx, value in enumerate(data_row, 1):
        cell = ws.cell(row=row_idx, column=col_idx, value=value)
        cell.font = BODY_FONT
        cell.border = THIN_BORDER

# "So What" row (MANDATORY — insight row at bottom)
insight_row = ws.max_row + 2
ws.cell(row=insight_row, column=1, value="→ So What:")
ws.cell(row=insight_row, column=1).font = ACCENT_FONT
ws.cell(row=insight_row, column=2, value="{insight from this data}")
ws.cell(row=insight_row, column=2).font = MUTED_FONT
for col in range(1, ws.max_column + 1):
    ws.cell(row=insight_row, column=col).fill = INSIGHT_FILL
```

### Formulas Over Hardcoded Values

```python
# ✅ CORRECT: Dynamic formulas
ws['D2'] = '=B2/C2'           # Ratio
ws['E2'] = '=D2-D3'           # Delta
ws['F10'] = '=SUM(F2:F9)'    # Total
ws['G2'] = '=IF(D2>0.1,"↑","↓")'  # Status indicator

# ❌ WRONG: Hardcoded intermediate values
ws['D2'] = 0.45               # Should be =B2/C2
```

---

## Step 4: Brand Chart Styling

Use `brand_chart.py` to apply direction tokens to charts:

```bash
python3 "$SKILL_DIR/scripts/brand_chart.py" \
  "content/{name}/tracks/data-report/{topic}.xlsx" \
  --direction "$DIRECTION_YAML" \
  --json
```

Or inline in Python:

```python
from openpyxl.chart import BarChart, Reference
from openpyxl.chart.series import DataPoint

chart = BarChart()
chart.title = "{Chart Title}"
chart.style = 10  # Minimal gridlines

# Apply brand colors to series
data = Reference(ws, min_col=2, min_row=1, max_col=4, max_row=10)
chart.add_data(data, titles_from_data=True)

# Series colors from direction tokens
chart.series[0].graphicalProperties.solidFill = accent_hex
chart.series[1].graphicalProperties.solidFill = "4A4A4A"  # neutral

# Clean chart styling (no clutter)
chart.legend.position = 'b'
chart.y_axis.delete = False
chart.x_axis.tickLblPos = 'low'

ws.add_chart(chart, "A12")
```

---

## Step 5: Formula Validation

```bash
XLSX_SCRIPT="${SWARM_WORKSPACE:-$PWD}/.claude/skills/s_xlsx/recalc.py"
python3 "$XLSX_SCRIPT" "content/{name}/tracks/data-report/{topic}.xlsx" --json
```

If errors found (e.g., `#REF!`, `#VALUE!`), fix formulas and re-run.

---

## Step 6: Quality Verification (RP-G)

| # | Pattern | How to Verify | Gate |
|---|---------|--------------|------|
| RP-G1 | Executive summary sheet | Sheet 1 exists, titled "Executive Summary", has KPI cells + chart | BLOCKING |
| RP-G2 | "So what" present | Every sheet with data has an insight row (grep for "→" or "So What" or "Key Insight") | BLOCKING |
| RP-G3 | Brand colors | Chart series use direction accent color (not openpyxl defaults) | BLOCKING |
| RP-G4 | Formula integrity | `recalc.py` returns 0 errors | BLOCKING |
| RP-G5 | Sheet naming | All sheets have descriptive names (never "Sheet1", "Sheet2") | BLOCKING |
| RP-G6 | Data completeness | All metrics from content_package Data Layer appear in the workbook | BLOCKING |

---

## Output Files

| File | Always? | Content |
|------|---------|---------|
| `tracks/data-report/{topic}.xlsx` | ✅ | Branded workbook with charts |
| `tracks/data-report/summary.md` | ✅ | Text version of executive summary (for cross-format RP-X) |

---

## Anti-Rationalization

| Shortcut | Required Response |
|----------|-------------------|
| "Only 3 metrics, one sheet is fine" | One sheet is fine IF the data has one dimension. Structure follows data, not convenience. |
| "Chart styling is cosmetic" | Brand consistency in charts signals professionalism. Apply direction tokens. |
| "Hardcoded values are faster" | Formulas let the reader change assumptions. Hardcoded values are dead data. |
| "So-what row is redundant with the chart" | Charts show WHAT. So-what row says WHY IT MATTERS. Both required. |
| "recalc.py takes too long, skip validation" | Formula errors in delivered files destroy credibility. Always validate. |
