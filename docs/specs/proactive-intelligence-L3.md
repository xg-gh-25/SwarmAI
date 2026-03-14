---
title: "Proactive Intelligence Level 3 — Cross-Session Learning"
date: 2026-03-14
author: Swarm + XG
status: draft — awaiting review
version: "0.1"
tags: [proactive-intelligence, learning, cross-session, architecture]
parent: proactive-intelligence.md
---

# Proactive Intelligence Level 3 — Cross-Session Learning

## What This Is

Level 3 closes the feedback loop. L2 suggests what to work on; L3 observes what the user *actually* did, and adjusts future suggestions accordingly. This is the first level that introduces **persistent state** across sessions.

## The Problem L3 Solves

L2 scores items purely from static signals (priority, staleness, report count). It has no memory of what it suggested before or whether the user followed through. This leads to:

1. **Nagging** — suggesting "rebuild app" every session when the user keeps choosing feature work
2. **No skip awareness** — a 4x-reported P0 that the user consciously defers still dominates
3. **No work-type learning** — can't distinguish "user prefers feature work mornings, maintenance evenings"

## What Changes for the User

### Before (L2)

```
Session 1: Suggested: rebuild app, fix MCP, tab-switch bug
           User did: Proactive Intelligence L2

Session 2: Suggested: rebuild app, fix MCP, tab-switch bug  ← identical
           User did: Proactive Intelligence L2 review fixes

Session 3: Suggested: rebuild app, fix MCP, tab-switch bug  ← still identical
           User did: started L3 design
```

Agent keeps suggesting the same things. No adaptation.

### After (L3)

```
Session 1: Suggested: rebuild app, fix MCP, tab-switch bug
           User did: Proactive Intelligence L2
           Learning: user chose feature work over maintenance

Session 2: Suggested: Proactive Intelligence L3, fix MCP, rebuild app
           ↑ reranked: feature work promoted, maintenance demoted
           User did: PI L3 design

Session 3: Suggested: PI L3 implementation, fix MCP, tab-switch bug
           ↑ maintenance items slowly recover as staleness grows
```

Suggestions adapt. Maintenance items don't disappear — they just rank lower until staleness makes them urgent again.

## E2E Data Flow

```
Session N starts:
    |
    v
build_session_briefing()
    |
    ├── L0-L1: parse threads + signals
    ├── L2: score + rank items
    └── L3: apply learned adjustments    <-- NEW
         |
         ├── Read proactive_state.json
         ├── For each item: adjust score based on:
         │     skip_count (user ignored this N times)
         │     work_type_affinity (user prefers feature/maintenance/investigation)
         │     time_pattern (morning vs evening preference)
         └── Re-rank with adjusted scores
    |
    v
Briefing injected into system prompt
    |
    v
User works on their chosen task
    |
    v
Session ends → DailyActivity written (auto, existing pipeline)
    |
    v
Next session start:
    |
    v
_update_learning_state()             <-- NEW, runs before briefing
    |
    ├── Read last session's briefing (from proactive_state.json)
    ├── Read last session's DailyActivity deliverables
    ├── Compare: which suggestions were followed vs skipped?
    ├── Classify work done: feature / maintenance / investigation / design
    └── Update proactive_state.json with new observations
```

## Learning Model

### What We Track

```json
// ~/.swarm-ai/proactive_state.json
{
  "version": 1,
  "last_updated": "2026-03-14T19:30:00",
  "last_briefing": {
    "session_date": "2026-03-14",
    "suggested": [
      "Tab switching loses streaming content",
      "MCP servers not connecting in app"
    ]
  },
  "item_history": {
    "tab switching loses streaming content": {
      "suggested_count": 4,
      "followed_count": 1,
      "skipped_count": 3,
      "last_suggested": "2026-03-14",
      "last_worked": "2026-03-14"
    },
    "mcp servers not connecting in app": {
      "suggested_count": 2,
      "followed_count": 0,
      "skipped_count": 2,
      "last_suggested": "2026-03-14",
      "last_worked": null
    }
  },
  "work_type_distribution": {
    "feature": 5,
    "maintenance": 2,
    "investigation": 3,
    "design": 2
  },
  "observations": [
    {
      "date": "2026-03-14",
      "suggested_top": "rebuild app",
      "actual_work": "Proactive Intelligence L2",
      "work_type": "feature",
      "followed_suggestion": false
    }
  ]
}
```

### Work Type Classification

Classify each session's deliverables by keywords in `**Delivered:**` lines:

| Work Type | Keywords | Examples |
|-----------|----------|---------|
| feature | "built", "implemented", "created", "added", "new" | "Built Proactive Intelligence L2" |
| maintenance | "fixed", "rebuilt", "verified", "upgraded", "migrated" | "Fixed tab-switch bug" |
| investigation | "investigated", "diagnosed", "root cause", "analyzed" | "Diagnosed MCP connection failure" |
| design | "designed", "spec", "drafted", "wireframe", "mockup" | "Drafted L3 design doc" |

Classification is keyword-based (no LLM). Multiple types per session are fine — take the dominant one.

### Score Adjustments

L3 modifies L2 scores with learned adjustments:

```python
def _apply_learning(item: ScoredItem, state: LearningState) -> ScoredItem:
    adjusted = item.score

    # 1. Skip penalty: user ignored this item repeatedly
    history = state.get_item_history(item.title)
    if history and history.skip_count >= 2:
        # -10 per skip after the first, cap at -30
        adjusted -= min((history.skip_count - 1) * 10, 30)

    # 2. Work type affinity: boost items matching user's preferred type
    item_type = _classify_work_type(item.title, item.status)
    preferred_type = state.preferred_work_type()
    if item_type == preferred_type:
        adjusted += 15  # mild boost, not dominant

    # 3. Recovery: items skipped many times but getting stale
    #    should recover score (staleness eventually overrides skip penalty)
    #    This is already handled by L2's staleness scoring — no extra logic needed.

    item.score = max(adjusted, 0)
    return item
```

### Key Constraints

1. **Adjustments are mild** — L3 tweaks scores by ±10-30 points. L2's base scoring (P0=100) still dominates. A P0 bug can never be fully suppressed by learning.

2. **Staleness beats skip penalty** — an item skipped 5 times but open 10 days will have: skip_penalty(-30) + staleness(+30) = net zero. The skip effect naturally decays as urgency grows.

3. **No negative scores** — `max(adjusted, 0)` ensures items never disappear entirely.

4. **State file is optional** — if `proactive_state.json` doesn't exist or is corrupt, L3 degrades to L2 (no adjustments). Same try/except pattern as L0-L2.

## Architecture

### New Code

```
backend/core/proactive_intelligence.py   <-- extend existing
  + LearningState dataclass               (state file model)
  + _load_learning_state()                 (read JSON, graceful fallback)
  + _save_learning_state()                 (write JSON, atomic)
  + _update_learning_from_activity()       (compare suggestions vs deliverables)
  + _classify_work_type()                  (keyword-based classification)
  + _apply_learning()                      (adjust ScoredItem scores)
```

**Still one file.** No new module — L3 is ~80-100 lines added to `proactive_intelligence.py`. The state file is the only new artifact.

### State File Location

`~/.swarm-ai/proactive_state.json` — outside SwarmWS (not git-tracked). This is ephemeral learning data, not curated knowledge.

### Integration Points

```
build_session_briefing()
  |
  ├── _parse_open_threads()        # L0
  ├── _parse_continue_hints()      # L0
  ├── _detect_patterns()           # L1
  ├── _build_suggestions()         # L2 (scoring + ranking)
  │     |
  │     └── For each ScoredItem:
  │           _apply_learning(item, state)   # L3 adjustment  <-- NEW
  │
  ├── _update_learning_from_activity()       # L3 observation  <-- NEW
  │     (runs first, before scoring, to update state
  │      from previous session's outcomes)
  │
  ├── _format_suggestions()        # L2
  └── Save current suggestions to state      # L3 bookkeeping  <-- NEW
```

### Update Timing

Learning state is updated at the **start** of each session (before building the briefing), by comparing:
- What was suggested last session → from `proactive_state.json["last_briefing"]`
- What was actually done → from most recent DailyActivity `**Delivered:**` lines

This is safe because DailyActivity is written at the **end** of each session (by the existing auto-extraction hook), and the state update happens at the **start** of the next session.

## Edge Cases

| Case | Behavior |
|------|----------|
| No state file exists | Create with defaults. First session = no adjustments (pure L2). |
| State file corrupt | Delete and recreate. Log warning. |
| User works on something not suggested | That's fine — no penalty. We only track suggested items. |
| User works on multiple things | Each delivered item is checked against suggestions. |
| 1-day gap between sessions | Normal. Update happens at next session start. |
| 7-day gap between sessions | State is stale but still valid. Skip counts don't expire. |
| User manually edits MEMORY.md | Threads may not match state titles. Fuzzy match on first 30 chars. |
| Concurrent tabs | Both read same state file. Last writer wins. Acceptable — learning is statistical, not precise. |

## What L3 Does NOT Do

- **No LLM calls** — still pure rules + keyword matching
- **No complex ML** — simple counters and keyword classification, not a model
- **No autonomous actions** — still suggestions only
- **No negative user experience** — items never fully disappear, skip penalty is capped
- **No privacy concerns** — state file contains only item titles and counts, not user data

## Observations Window

The `observations` array in the state file has a rolling window of **30 entries** (cap). Older observations are dropped. This keeps the file small (<5KB) and prevents ancient patterns from dominating.

## Test Plan

| Test | What it verifies |
|------|-----------------|
| Skip penalty reduces score | item with skip_count=3 scores lower than skip_count=0 |
| Skip penalty capped at -30 | skip_count=10 doesn't go below -30 adjustment |
| Work type classification | "Built feature X" → feature, "Fixed bug Y" → maintenance |
| Work type affinity boost | +15 when item type matches preferred type |
| Staleness recovers skipped items | high staleness + high skip = roughly neutral |
| State file round-trip | save → load → state unchanged |
| Missing state file graceful | returns default state, no crash |
| Corrupt state file graceful | resets to default, logs warning |
| Update from activity | suggestion "Fix X" + delivered "Fixed X" → followed_count++ |
| Update from activity miss | suggestion "Fix X" + delivered "Built Y" → skip_count++ |
| Observations window capped | 25 entries → oldest 5 dropped |
| Integration with L2 scoring | Full pipeline applies adjustments correctly |
| State not written on read error | If DailyActivity missing, state unchanged |

## Delivery Plan

**Single delivery**, ~80-100 lines of new code in `proactive_intelligence.py`:

1. `LearningState` dataclass + JSON I/O (`_load_learning_state`, `_save_learning_state`)
2. `_update_learning_from_activity()` — observation extraction
3. `_classify_work_type()` — keyword classifier
4. `_apply_learning()` — score adjustment
5. Wire into `build_session_briefing()` and `_build_suggestions()`
6. Tests for all the above

**What needs to exist first:**
- L2 must be stable (it is — 51 tests pass)
- At least 2-3 DailyActivity files with `**Delivered:**` sections (we have 3 days)

**When it's useful:**
- Immediately useful with 3+ sessions of data
- Gets better with more sessions (more observations = more accurate type distribution)
- Skip detection takes 2 consecutive sessions to activate (need suggestion + outcome)

## Open Questions for Review

1. **State file location:** `~/.swarm-ai/proactive_state.json` (outside SwarmWS, not git-tracked). Alternative: inside SwarmWS but `.gitignore`'d. Preference?

2. **Skip threshold:** Currently 2 skips to activate penalty. Should it be 3 (more lenient)?

3. **Work type affinity boost (+15):** ✅ Decided: +15. A P1 feature item gets 55 vs P1 maintenance at 40. Mild nudge. A P0 maintenance bug (100+) still dominates.

4. **Should we surface the learning?** ✅ Decided: Yes, surface it. Shows as "Pattern: feature work preferred (6/6 sessions, 100%)" in briefing.

5. **Observations window (30):** ✅ Decided: 30 entries ≈ 1 month of daily use. Balances signal quality with storage.

---

*Design doc ready for review. No code written. Implementation starts after approval.*
