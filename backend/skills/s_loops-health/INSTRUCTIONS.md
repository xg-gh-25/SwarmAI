# Self-Loops Health — Maintenance Engine

Self-maintenance engine for SwarmAI's 4 self-management loops. Not a reporter —
a garbage collector that fixes what it can and only escalates genuine judgment calls.

**Core principle:** Detect → Fix → Report what was fixed → Escalate only what needs human judgment.

## When to Run

- User asks "are loops healthy?", "check memory", "brain health"
- Proactively after shipping a major feature (>5 files changed)
- When session briefing flags degraded health

## How to Run

Execute the health check script:

```bash
cd /Users/gawan/Desktop/SwarmAI-Workspace/swarmai
source backend/.venv/bin/activate
python backend/skills/s_loops-health/scripts/loops_health_check.py --auto-fix --json
```

## Output Interpretation

The script outputs JSON with:
- `overall_score`: 0-100 (min of all dimension scores)
- `scores`: per-dimension breakdown
- `findings`: what was found (pass/warn/fail per check)
- `fixes_applied`: what was auto-fixed
- `pending`: what needs human decision

## 7 Dimensions (29 Checks)

| Dim | Name | Checks | What It Covers |
|-----|------|--------|----------------|
| 1 | Self-Context | 4 | Files present, agent-file freshness, DDD in KNOWLEDGE, uncommitted |
| 2 | Self-Memory | 5 | Distillation recency, backlog, caps, OT hygiene, archive |
| 3 | Self-Knowledge | 4 | Index completeness, architecture currency, capability coverage, nav |
| 4 | Self-Evolution | 4 | Pipeline last run, skill_health.json, correction capture, competence |
| 5 | Cross-Loop | 3 | Memory→Knowledge, Memory→Evolution, DA→Memory flow |
| 6 | Brain Safety | 4 | Remote exists, push recency, push health, critical files committed |
| 7 | Infrastructure | 5 | Hook proof, DA generation, token budget, growth rate, stale locks |

## After Running

1. **Present the Found/Fixed/Pending report** to the user
2. **For Pending items** — present options with your recommendation (use CONFLICT/MISSING template)
3. **If score <70** — suggest a focused investigation session
4. **If score ≥90** — brief confirmation, no details needed

## Report Format

```
## Summary: Found X | Fixed Y | Pending Z

| Dimension | Score | Found | Fixed | Pending |
|-----------|-------|-------|-------|---------|
| ...       | ...   | ...   | ...   | ...     |

## Fixed (autonomous)
| # | Category | What | Action Taken |
...

## Pending (needs your decision)
| # | Finding | Options | My Lean |
...
```

## Auto-Fix Actions (Tier 1 — always safe)

- Cap enforcement (evict lowest-usage entries over cap)
- DDD injection into KNOWLEDGE.md
- Auto-commit uncommitted .context/ files (with integrity check)
- Auto-push to remote (rate-limited: >4h or >20 commits)
- Archive DailyActivity >90d
- Remove broken skill symlinks
- Clear stale lock files (>1h)
- Rebuild KNOWLEDGE index
- Remove dead file references from MEMORY

## Scripts & Entry Points

| Script | Purpose |
|--------|---------|
| `scripts/loops_health_check.py` | Main engine — all checks + fixes + reporting |

## Verification

- [ ] Script ran without errors
- [ ] Score reported (0-100)
- [ ] All 7 dimensions checked
- [ ] Auto-fixes applied where safe
- [ ] Pending items presented with options
