# Quality Convergence Data

Timestamped records of measurable system improvements. Each entry includes evidence (commit hash, file path, or command to reproduce). Updated on meaningful milestones — not on every commit.

**Why this file exists:** "Knowledge compounds" and "quality converges" are claims. Claims without data are marketing. This file is the data.

**How to verify any entry:** Every entry includes an `Evidence` field pointing to a git commit, file, or reproducible command. Run it yourself.

**Data source:** Extracted from private operational memory (MEMORY.md, EVOLUTION.md, DailyActivity logs) which contain sensitive work context and are not published. What you see here are the measurable outcomes — independently verifiable via git history.

---

## Correction Trajectory

Corrections are structural fixes that eliminate an entire class of bugs. Each one makes the next occurrence impossible — not just unlikely.

| Date | Total Corrections | New This Period | Pattern Eliminated | Evidence |
|------|------------------|-----------------|-------------------|----------|
| 2026-03-13 | 1 | 1 | Recurring bugs not escalated across sessions | C001, `backend/context/EVOLUTION.md` |
| 2026-03-19 | 5 | 4 | Self-reinforcing false memory; sycophantic compliance; topology inference | C002-C005 |
| 2026-04-12 | 9 | 4 | Implementation before thinking; stale mental model assertions | C006-C009 |
| 2026-04-25 | 12 | 3 | Pipeline passes but feature broken; tool-oriented thinking; MCP give-up | C010-C012 |
| 2026-05-08 | 20 | 8 | Context pressure hallucination; destructive verification; shallow research | C013-C020 |
| 2026-05-15 | 25 | 5 | Quality gate bypass; scope mismatch; process skip under comfort bias | C021-C025 |

**Trend:** Correction rate is ~4/week. The interesting metric isn't "fewer corrections" (we're still learning) — it's "no correction repeats a pattern already captured." Zero C001-class bugs since March 15. Zero C005-class bugs since April. The captured patterns are working.

> **Live count:** The table above is a historical trajectory. For the current total, see the live [`EVOLUTION.md`](../backend/context/EVOLUTION.md) (`grep -c "^### C" backend/context/EVOLUTION.md`) — the shipped seed and the live workspace diverge, so this doc does not freeze a single number.

---

## P0 (Critical Bug) Rate Per Release

| Version Range | Releases | Total P0s | P0/Release | Dominant Failure Class |
|---------------|----------|-----------|-----------|----------------------|
| v1.6–v1.8 | 3 | 3 | 1.0 | Catastrophic: OOM cascade, app won't start, streaming loss |
| v1.9 | 1 | 3 | 3.0 | Release scope (60 commits — outlier, caused scope gate rule) |
| v1.10–v1.12 | 3 | 1 | 0.3 | Edge case: pipe flush race under concurrent shutdown |
| v1.13 | 1 | 0 | 0.0 | (current — full pipeline + adversarial active) |

**Evidence:** Release tags in git (`git tag -l "v1.*"`), P0 incidents in `Projects/SwarmAI/IMPROVEMENT.md` "What Failed" section.

**Trend:** Failure class migration — catastrophic → edge-case. Not "fewer bugs found" (adversarial review finds MORE) but "bugs found earlier and at lower severity."

---

## Pipeline Quality Gate Effectiveness

| Metric | Pre-Pipeline (< v1.10) | With Pipeline (v1.10+) | With Adversarial (v1.12+) |
|--------|----------------------|----------------------|--------------------------|
| Bugs shipped to user | ~3/release | ~1/release | ~0.3/release |
| Bugs caught before merge | 0 (no gate) | ~5/pipeline run | ~8/pipeline run |
| False positive rate | N/A | ~20% (early) → ~5% (current) | ~10% (fresh context over-flags) |

**Evidence:** Pipeline run artifacts in `Projects/SwarmAI/.artifacts/runs/`. Each `run.json` records stages, findings, and fixes applied.

---

## DDD Knowledge Growth

Domain knowledge documents grow from normal work. No dedicated "documentation sprints."

| Date | Project | PRODUCT.md | TECH.md | IMPROVEMENT.md | PROJECT.md | Total Sections |
|------|---------|-----------|---------|---------------|-----------|---------------|
| 2026-03-24 | SwarmAI | 12 sections | 8 sections | 3 entries | 5 items | 28 |
| 2026-04-15 | SwarmAI | 18 sections | 14 sections | 12 entries | 12 items | 56 |
| 2026-05-01 | SwarmAI | 22 sections | 18 sections | 28 entries | 18 items | 86 |
| 2026-05-16 | SwarmAI | 26 sections | 20 sections | 42 entries | 22 items | 110 |

**Evidence:** `git log --oneline -- Projects/SwarmAI/*.md | wc -l` shows commit frequency. Section count via `grep -c "^##" Projects/SwarmAI/*.md`.

**Trend:** ~3 sections/week organic growth. Zero dedicated documentation sessions — all growth from pipeline REFLECT, corrections, and signal processing.

---

## Memory Compound Metrics

| Date | MEMORY.md Entries | Key Decisions | Lessons Learned | COEs | Open Threads |
|------|------------------|--------------|-----------------|------|-------------|
| 2026-03-14 | 5 | 2 | 1 | 0 | 2 |
| 2026-04-01 | 28 | 12 | 8 | 5 | 4 |
| 2026-05-01 | 52 | 25 | 20 | 8 | 3 |
| 2026-05-16 | 64 | 30 | 25 | 9 | 1 |

**Evidence:** `.context/MEMORY.md` (runtime instance). Seed data in `backend/context/MEMORY.md` (git-tracked template). Memory Index section at top of file shows exact counts.

**Trend:** Open Threads decreasing (issues getting resolved permanently). Key Decisions accumulating monotonically (institutional knowledge). Lessons compounding into STEERING rules (active → structural).

---

## Session Resume Context Enrichment

How much context does a cold-resumed session start with?

| Date | Resume Context Size | Layers | Evidence |
|------|-------------------|--------|----------|
| Pre 2026-04-02 | ~600 tokens | Checkpoint only (last request + files touched) | `git show 3d8c1f9` |
| 2026-04-02 | ~5K tokens | Structured checkpoint + recent 5 turns | Commits `3d8c1f9`→`59e55a7` |
| 2026-05-02 | ~50-100K tokens | 5 extraction layers (conclusions, directives, tool results, git state, crash checkpoint) | Commits `2352c65`→`7bd1a23` |

**Evidence:** `backend/core/session_unit.py` — `build_resume_context()` function. Budget tiers in `_compute_resume_budget()`.

**What this means:** A resumed session in May 2026 starts with 100x more relevant context than one in March. The agent doesn't re-read files or re-discover decisions — it picks up where it left off.

---

## Test Suite Growth

| Date | Test Count | Coverage Focus | Evidence |
|------|-----------|---------------|----------|
| 2026-03-15 | ~50 | Core session lifecycle | Early commits |
| 2026-04-01 | ~220 | Multi-session + memory | `pytest --co -q \| wc -l` |
| 2026-04-15 | ~580 | Evolution + proactive | Same |
| 2026-05-01 | ~700 | Pipeline + DDD | Same |
| 2026-05-16 | ~780+ | Cultivation + quality gates | Same |

> **Note:** This table records the early ramp only. Test count has grown well past this range since — see the current figure in the [Snapshot History](#snapshot-history) table below (run the command to reproduce live).

**Evidence:** `cd backend && .venv/bin/python -m pytest --co -q 2>/dev/null | tail -1`

---

## How to Read This Data

**If you're evaluating SwarmAI, ask these questions:**

1. **Is correction count growing without repeats?** → System is learning, not just accumulating errors.
2. **Is P0/release trending down?** → Quality convergence is real, not claimed.
3. **Is DDD growing without documentation sprints?** → Knowledge compounds from work, not effort.
4. **Is resume context increasing?** → Sessions compound, they don't reset.

**If any of these trends reverse**, that's a signal the compound thesis is failing. We'll record that too.

---

_Last updated: 2026-07-13 (current release v1.25.0). Updated on meaningful milestones, not on schedule._

---

## Snapshot History

| Date | Version | Corrections | DDD Sections | Tests | Source |
|------|---------|-------------|-------------|-------|--------|
| 2026-05-16 | v1.13.0 | 23 | 33 | 3906+ | auto (release) |
| 2026-05-17 | v1.14.0 | 23 | 33 | 3932+ | auto (release) |
| 2026-05-17 | v1.14.1 | 21 | 33 | 3988+ | auto (release) |
| 2026-05-19 | v1.15.0 | 24 | 33 | 4087+ | auto (release) |
| 2026-05-20 | v1.16.0 | 26 | 33 | 4161+ | auto (release) |
| 2026-05-20 | v1.16.1 | 25 | 33 | 4172+ | auto (release) |
| 2026-05-30 | v1.17.0 | 17 | 35 | 4347+ | auto (release) |
| 2026-05-30 | v1.17.1 | 17 | 35 | 4402+ | auto (release) |
| 2026-05-31 | v1.17.4 | 17 | 35 | 4434+ | auto (release) |
| 2026-06-01 | v1.17.5 | 17 | 35 | 4469+ | auto (release) |
| 2026-06-07 | v1.17.6 | 17 | 35 | 4513+ | auto (release) |
| 2026-06-07 | v1.17.7 | 17 | 35 | 4583+ | auto (release) |
| 2026-06-08 | v1.18.0 | 17 | 35 | 4583+ | auto (release) |
| 2026-06-09 | v1.18.1 | 17 | 35 | 4583+ | auto (release) |
| 2026-06-10 | v1.18.2 | 17 | 35 | 4591+ | auto (release) |
| 2026-06-18 | v1.19.0 | 17 | 37 | 0+ | auto (release) |
| 2026-06-18 | v1.20.0 | 17 | 37 | 4962+ | auto (release) |
| 2026-06-26 | v1.20.1 | 17 | 37 | 5811+ | auto (release) |
| 2026-07-04 | v1.23.0 | 17 | 37 | 6672+ | auto (release) |
| 2026-07-10 | v1.24.0 | 17 | 37 | 7017+ | auto (release) |
| 2026-07-13 | v1.24.1 | 17 | 37 | 7188+ | auto (release) |
| 2026-07-17 | v1.25.0 | see live EVOLUTION.md | — | 7188+ | current |

> Correction/section counts in the seed lag the live workspace — see live [`EVOLUTION.md`](../backend/context/EVOLUTION.md) for the current figure rather than the frozen seed value.
