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
- `Projects/*/.artifacts/protected-zone-candidates.jsonl` — lessons auto-dropped from
  a protected zone (TECH>Architecture / SELF / PRODUCT>Vision,Non-Goals,Strategic) that
  a human should hand-distill (NEW — run_97519f7c). NOT auto-writable (human-only zone),
  so cultivation diverts them here instead of silently dropping.
- `Projects/*/PRODUCT.md|TECH.md|IMPROVEMENT.md|PROJECT.md` — health stats (line count, mtime)
- `.context/.auto_refresh_log.jsonl` — Layer 1/2 auto-refresh activity (NEW)

## Human-Distill Candidates Section (NEW — run_97519f7c)

The weekly report MUST surface the protected-zone candidates so the sink sediments UP
into a human decision instead of becoming a write-only landfill (Principle 1). Read
`Projects/<project>/.artifacts/protected-zone-candidates.jsonl` (one JSON object per
line: `target_doc`, `target_section`, `content`, `source_run_id`, `confidence`), group
by `target_doc § target_section`, dedup by content, and render:

```markdown
## 🖐 Lessons For You To Hand-Distill (protected zones)

Auto-detected but land in human-only zones — cultivation can't write them. Review and
hand-write the ones worth keeping into the named doc/section.

| Target | Lesson (excerpt) | Runs |
|--------|------------------|------|
| TECH.md § Architecture | <content excerpt> | run_xxx (+N) |
```

If the file is absent or empty → omit this section (nothing to distill).

## Auto-Refresh Audit Section (NEW — 2026-06-17)

The weekly report MUST include an **Auto-Refresh Audit** section that shows what
the DDD & Memory Auto-Refresh Engine did this week. Read the log file:

```python
from core.auto_refresh import read_refresh_log
from pathlib import Path

log_path = Path(workspace) / ".context" / ".auto_refresh_log.jsonl"
entries = read_refresh_log(log_path, since_days=7)
```

**Format the section:**

```markdown
## Auto-Refresh Audit

### Layer 1 — Mechanical (auto-applied, FYI only)
| Target | Change | Evidence |
|--------|--------|----------|
| .context/MEMORY.md | "8-stage" → "9-stage" | stages/*.md count = 9 |

### Layer 2 — LLM-Proposed (auto-applied, review window)
(none this week)

### Layer 3 — Pending Escalation (needs decision)
(use existing "Decisions Needed" section — proposals with source="auto_refresh")
```

If no auto-refresh activity this week, show: `No auto-refresh activity this week.`
Keep it concise — 3-8 items max. This is an audit trail, not a wall of noise.

## Multi-Project by Design

This report is NOT hardcoded to any specific project. It dynamically discovers
ALL projects that have DDD documents and reports across all of them. A user with
1 project or 10 projects gets the same treatment — per-project breakdown with
cross-project summary.

## Auto vs On-Demand

- **Auto:** System job runs every Monday at 04:00 UTC (12:00 ICT)
- **On-demand:** User says "DDD weekly" or "DDD 周报" → skill triggers immediately

Both produce the same output in the same location.
