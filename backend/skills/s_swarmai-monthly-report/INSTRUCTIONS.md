# SwarmAI Monthly Report — Instructions

## What This Does

Generates an MBR-style monthly report covering all 12 Core Engine subsystems.
The report tells a story: what happened, so what, what's next — not just numbers.

## How to Run

```bash
cd $SWARMAI_ROOT/backend
.venv/bin/python -c "
from jobs.handlers.swarmai_monthly_report import run_swarmai_monthly_report
result = run_swarmai_monthly_report(config={'month': 'YYYY-MM'})
print(f'Output: {result[\"output_path\"]}')
"
```

## Parameters

| Param | Default | Description |
|-------|---------|-------------|
| `month` | Previous full month | Format: "YYYY-MM" (e.g., "2026-05") |

## Workflow

1. **Determine month** — default is last full month. User can say "this month" or "April".
2. **Run the handler** — call `run_swarmai_monthly_report(config={"month": "YYYY-MM"})`
3. **Show the output path** — report saved to `Knowledge/Reports/YYYY-MM-swarmai-monthly.md`
4. **Open or summarize** — read the report and present key findings to user

## Example Invocations

- "生成月报" → run for previous month
- "SwarmAI monthly for May" → `config={"month": "2026-05"}`
- "how did we do this month" → run for current month (partial data)
- "monthly report" → run for previous month

## Report Sections

| Section | What It Shows |
|---------|--------------|
| Executive Summary | 1-paragraph narrative: what happened, key numbers |
| P0 Metrics | Table with status indicators (🟢/🟡/🔴) for all subsystems |
| Highlights & Lowlights | [HL]/[LL] format with narrative judgment |
| Subsystem Health | Per-subsystem breakdown with numbers |
| Risks & Next Month | Forward-looking flags + action items |

## Data Sources (all automatic, no manual input)

- Pipeline runs: `Projects/SwarmAI/.artifacts/runs/*/run.json`
- DDD changelog: `Projects/*/.artifacts/ddd-changelog.jsonl`
- Memory: `.context/MEMORY.md`
- Evolution: `.context/EVOLUTION.md`
- Context files: `.context/*.md` (token counts)
- Job results: `Knowledge/JobResults/.job-results.jsonl`
- Health findings: `.context/health_findings.json`
- Code Intelligence: `code_intel.db` (if available)
- Skills: `backend/skills/s_*/SKILL.md` (count + tiers)
- Pollinate: `Knowledge/Pollinate/` (directory scan)
- Git: `git log --since --until` on swarmai repo

## Auto vs On-Demand

- **Auto:** System job runs on 1st of every month at 05:00 UTC (13:00 ICT)
- **On-demand:** User says "月报" or "monthly report" → skill triggers immediately

Both produce the same output in the same location.
