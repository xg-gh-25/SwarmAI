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
cd $SWARMAI_ROOT
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

## 8 Dimensions (35 Checks)

| Dim | Name | Checks | What It Covers |
|-----|------|--------|----------------|
| 1 | Self-Context | 4 | Files present, agent-file freshness, DDD in KNOWLEDGE, uncommitted |
| 2 | Self-Memory | 5 | Distillation recency, backlog, caps, OT hygiene, archive |
| 3 | Self-Knowledge | 4 | Index completeness, architecture currency, capability coverage, nav |
| 4 | Self-Evolution | 4 | Pipeline last run, skill_health.json, correction capture, competence |
| 5 | Cross-Loop | 3 | Memory→Knowledge, Memory→Evolution, DA→Memory flow |
| 6 | Brain Safety | 4 | Remote exists, push recency, push health, critical files committed |
| 7 | Infrastructure | 5 | Hook proof, DA generation, token budget, growth rate, stale locks |
| 8 | Governance | 6 | Budget counts, idle rules, correction recurrence, gate fires |

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

## Dimension 8: Governance Health (Three-Layer Governance)

| # | Check | Pass | Warn | Fail | Auto-Fix |
|---|-------|------|------|------|----------|
| 8a | AGENT.md rule count | ≤22 | 23-25 | >25 | No (COMPRESS needed) |
| 8b | SOUL.md principle count | ≤4 | 5 | >5 | No (human decision) |
| 8c | STEERING.md rule count | ≤12 | 13-15 | >15 | No (RETIRE needed) |
| 8d | Per-rule last-triggered date | All <30d | Any 30-60d | Any >60d | Surface candidates |
| 8e | Same-class correction recurrence | 0 after promote | 1 after promote | 2+ after promote | Suggest REFINE |
| 8f | Gate fire count (30d) | Any fires | — | 0 fires on all | Suggest GRADUATE |

**Scoring:**
- 100: All within budget, no idle rules, no recurring corrections post-promote
- 75: Budget pressure (at cap) OR 1 idle rule
- 50: Over budget OR 2+ idle rules OR correction recurrence
- 25: Structural violation (principle count > 5)

**How to check 8d (rule last-triggered):**
```bash
# Count corrections per bias class in last 30 days
grep -c "\[Bias" .context/EVOLUTION.md
# Check dates of most recent corrections per class
grep "### C0" .context/EVOLUTION.md | tail -5
```

**How to check 8e (recurrence after promote):**
```bash
# Find promoted corrections
grep "promoted" .context/EVOLUTION.md
# Check if same bias class has NEW entries after the promote date
```

**Auto-fix actions for Dimension 8:**
- Surface retirement candidates in report (rules >30d idle)
- Surface compression candidates (3+ rules same parent principle)
- CANNOT auto-fix: budget violations require COMPRESS/RETIRE (judgment call)

## Scripts & Entry Points

| Script | Purpose |
|--------|---------|
| `scripts/loops_health_check.py` | Main engine — all checks + fixes + reporting |

## Verification

- [ ] Script ran without errors
- [ ] Score reported (0-100)
- [ ] All 8 dimensions checked
- [ ] Auto-fixes applied where safe
- [ ] Pending items presented with options
