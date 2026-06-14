# DDD Cultivation Weekly Report — Instructions

## What This Does

Generates an MBR-style weekly report covering DDD cultivation activity across ALL projects.
The report tells a story: what knowledge grew, what needs your decision, what's getting stale.

## How to Run

```bash
cd $SWARMAI_ROOT/backend
.venv/bin/python -c "
from jobs.handlers.ddd_weekly_report import run_ddd_weekly_report
result = run_ddd_weekly_report(config={'window_days': 7})
print(f'Output: {result[\"output_path\"]}')
"
```

## Parameters

| Param | Default | Description |
|-------|---------|-------------|
| `window_days` | 7 | How many days back to scan for changes |

## Workflow

1. **Run the handler** — call `run_ddd_weekly_report(config={"window_days": 7})`
2. **Show the output path** — report saved to `Knowledge/Reports/YYYY-MM-DD-ddd-weekly.md`
3. **Open or summarize** — read the report and present key findings to user

## Example Invocations

- "DDD weekly" → run with default 7-day window
- "DDD 周报" → same
- "what changed in DDD this week" → run + summarize
- "DDD report for last 14 days" → `config={"window_days": 14}`

## Report Sections (MBR Format)

| Section | What It Shows |
|---------|--------------|
| Executive Summary | 1-paragraph narrative: quiet week vs active cultivation |
| Highlights & Lowlights | [HL]/[LL] format — top lessons applied, staleness warnings |
| Decisions Needed | Escalated proposals with context + approve/reject prompt |
| DDD Health Dashboard | Per-project × per-doc table with status indicators |
| Change Log | Detailed table of all auto-applied entries |
| Next Week | Forward-looking actions + stale doc warnings |

## Data Sources

- `Projects/*/.artifacts/ddd-changelog.jsonl` — applied entries (per project)
- `Projects/*/.artifacts/proposals/*.json` — pending escalations
- `Projects/*/PRODUCT.md|TECH.md|IMPROVEMENT.md|PROJECT.md` — health stats (line count, mtime)

## Multi-Project by Design

This report is NOT hardcoded to any specific project. It dynamically discovers
ALL projects that have DDD documents and reports across all of them. A user with
1 project or 10 projects gets the same treatment — per-project breakdown with
cross-project summary.

## Auto vs On-Demand

- **Auto:** System job runs every Monday at 04:00 UTC (12:00 ICT)
- **On-demand:** User says "DDD weekly" or "DDD 周报" → skill triggers immediately

Both produce the same output in the same location.
