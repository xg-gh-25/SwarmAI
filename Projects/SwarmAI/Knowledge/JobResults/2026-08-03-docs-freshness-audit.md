# Docs Freshness Audit — 2026-08-03

## Summary
- Feature commits (7d): **63**
- New core/ files (7d): **13**
- COE/P0 fixes (7d): **4**
- Docs updated (7d): **1 file** (CODEBASE_METRICS.md, auto-generated)
- Gaps found: **5 major categories**

## Gaps

| # | Type | Change | Missing Doc | Priority |
|---|------|--------|-------------|----------|
| 1 | New Subsystems | 13 new `backend/core/*.py` files | 10 missing from TECH.md Key Subsystems | high |
| 2 | Feature Docs | 63 feat commits (Library, Canvas v3, Pipeline Analytics, Proprioception, Brain Hub, Jobs & Runs) | No corresponding updates in docs/ for external readers | medium |
| 3 | Post-Mortems | 4 P0/COE fixes (pipeline dashboard E2E, briefing crash-shell, CLI run-status parity, stale-detector) | No post-mortem docs created | high |
| 4 | Design Doc Drift | backend/core/ last changed 2026-08-03 | Architecture/Harness/Memory docs last updated 2026-07-17 (17 days behind) | medium |
| 5 | Sales Hub DDD | Wiki pages updated 2025-08-11/13 | DDD last synced 2026-06-01 (metadata inconsistency detected) | low |

## Detailed Findings

### 1. New Core Subsystems (10 undocumented)
**Found in TECH.md:** `heartbeat.py`, `session_harvest.py`, `session_scorer.py`

**Missing from TECH.md:**
- `file_change_classifier.py` — added in last 7d
- `ui_actions.py` — added in last 7d, supports feat(proprioception) ACT bridge
- `library_inbox.py` — added in last 7d
- `library_mounts.py` — added in last 7d, supports feat(library) Cycle 6+7
- `brain_graph.py` — added in last 7d
- `brain_size_series.py` — added in last 7d
- `context_brain.py` — added in last 7d
- `todo_stats.py` — added in last 7d
- `executors.py` — added in last 7d
- `readiness_sampler.py` — added in last 7d

**Commits:** Multiple feat commits reference these (6d84b67f pipeline, 41513f41 chat screenshot, f74f1650 proprioception, 993de94f library, etc.)

### 2. Major Features Undocumented (63 feat commits)
Recent major features with zero docs/ updates for external readers:

**Library System (Cycles 1-7):**
- `63a39fb3` feat(library): Cycle 6+7 — Inbox landing zone + mount freshness
- `cfca842c` feat(library): Cycle 5 — docs-dir mount briefing cards
- `a7e1ffe6` feat(library): Cycle 4 — code-dir mount indexing
- `4ac521a2` feat(library): Cycle 3 — ownership plan
- `e65ceb4e` feat(library): Cycle 2 — mount registry with CRUD
- `a2037450` feat(library): Cycle 1 — Library nav card

**Canvas v3:**
- `68a66fff` feat(canvas): v3 polish — HTML inline render, outputs count
- `fba6f363` feat(canvas): v2 polish — tab-scoped auto-surface
- `b6c1491a` feat(canvas): persistent Canvas entry

**Pipeline Analytics:**
- `482a0ee7` feat(pipeline): Pipeline Retro-Analytics NavCard

**Proprioception (UI Actions):**
- `a0eded9c` feat(proprioception): frontend ACT bridge
- `f74f1650` feat(proprioception): backend ACT channel
- `e6863b68` feat(proprioception): frontend ui_context collection
- `299d8b6f` feat(proprioception): backend ui_context schema

**Brain Hub & Context Management:**
- `76a1eda7` feat(nav): New Brain launcher overlay
- `9809fce7` feat(cm-overlay): overview rail
- `702a7470` feat(cm-overlay): Memory tab
- `bfd651e7` feat(cm-overlay): Context tab per-file Health tags
- `0e443c29` feat(brain): C&M Global Brain overlay Run 1

**Jobs & Runs:**
- `c3d747a5` feat(jobs): New Job form dispatches full-context prompt
- `4d38d98b` feat(jobs): Jobs & Runs left-NavCard + fullscreen overlay
- `0a0a50ea` feat(jobs): add GET /api/jobs/{job_id}/runs

None of these have corresponding documentation updates in docs/ explaining the features to external readers.

### 3. Post-Mortems Missing (4 P0 fixes)
**Critical fixes with no post-mortem documentation:**

- `fd754125` fix(pipeline): dashboard E2E flow P0s — cap run details, cache metrics, collapse groups
- `bb4a3bee` fix(briefing): root-cause the crash-shell false-alarm loop — empty-shell guard
- `57221696` fix(pipelines): CLI run-status parity — present terminal-but-crashed runs as completed
- `6cd06c96` fix(pipelines): stale-detector must honor is_terminal_run

**Expected:** `docs/post-mortems/05-*.md` or similar for each incident

### 4. Design Doc Drift (17 days)
**Core design docs last updated:** 2026-07-17
**backend/core/ last changed:** 2026-08-03
**Gap:** 17 days

**Affected docs:**
- `SwarmAI-Architecture-Design.md`
- `Self-Evolution-Harness-Design.md`
- `Memory-Management-Design.md`

These are the primary external-facing design documents. With 13 new core subsystems and 63 feature commits, these docs are likely outdated.

### 5. Sales Hub Wiki Freshness
CMHK DDD Sales Hub pages show modification dates:
- 2025-08-11
- 2025-08-13

DDD last synced: 2026-06-01

**Note:** Date metadata shows 2025 dates (past year), suggesting either:
- Wiki metadata corruption/rollback
- Historical content being re-published
- DDD may actually be current

**Low priority** - metadata inconsistency detected, manual verification needed.

## Stale Docs (>30d behind code)

| Doc | Last Updated | Related Code Last Changed | Gap (days) |
|-----|-------------|--------------------------|------------|
| SwarmAI-Architecture-Design.md | 2026-07-17 | backend/core/ 2026-08-03 | 17 |
| Self-Evolution-Harness-Design.md | 2026-07-17 | backend/core/ 2026-08-03 | 17 |
| Memory-Management-Design.md | 2026-07-17 | backend/core/ 2026-08-03 | 17 |
| Autonomous-Pipeline-Design.md | 2026-07-20 | backend/pipelines/ active | ~14 |
| DDD-Cultivation-Engine-HLD.md | 2026-07-22 | backend/ddd/ active | ~12 |

## No Action Needed
✓ TECH.md updated 2026-08-03 (current)
✓ IMPROVEMENT.md updated 2026-08-03 (current)
✓ CODEBASE_METRICS.md updated 2026-08-01 (auto-generated, current)
✓ Post-mortem infrastructure exists (docs/post-mortems/ with 4 existing PMs)
✓ docs/ structure is sound (22 docs, well-organized)

## Recommendations
1. **High Priority:** Update TECH.md Key Subsystems section to document 10 new core/ files
2. **High Priority:** Create post-mortems for 4 P0 fixes (capture context while fresh)
3. **Medium Priority:** Update Architecture/Harness/Memory design docs to reflect 17 days of backend/core/ evolution
4. **Medium Priority:** Add feature documentation for Library, Canvas v3, Proprioception, Brain Hub, Jobs & Runs
5. **Low Priority:** Manually verify Sales Hub wiki dates (metadata inconsistency detected)
