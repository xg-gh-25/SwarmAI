---
title: "Proactive Intelligence Level 2 — Actionable Suggestions"
date: 2026-03-14
author: Swarm + XG
status: draft — awaiting review
version: "0.1"
tags: [proactive-intelligence, rule-engine, session-briefing, architecture]
parent: proactive-intelligence.md
---

# Proactive Intelligence Level 2 — Actionable Suggestions

## What This Is

Level 2 upgrades the briefing from **"here's what's happening"** to **"here's what you should do."** A deterministic rule engine scores and ranks actions, then injects a focused suggestion list into the session briefing.

## What Changes for the User

### Before (L0+L1 — current)

```
## Session Briefing
**Blockers:**
  - BLOCKING: Tab switching loses streaming content (4x)
**Signals:**
  - "Tab switching loses streaming content" reported 4x — needs durable fix
  - MCP servers not connecting (3x)
**Continue from last session:**
  - User runs ./dev.sh build, verifies briefing in agent's first response.
  - Level 2 design doc for review.
**Also pending (P1):**
  - MCP servers not connecting in app (3x)
  - Sandbox network blocked (2x)
```

User sees **status** but has to decide **what to do**. Agent may or may not align with what matters most.

### After (L2)

```
## Session Briefing
**Suggested focus for this session:**
  1. Fix MCP server connection — 3x reported, root cause unknown (est. investigation)
  2. Tab-switch P0 — needs Session State Machine design review before coding
  3. Sandbox network — SDK proxy issue, different approach needed

**Why this order:** MCP is blocking 2 other verifications. Tab-switch needs design
time (don't start without 2h). Sandbox is isolated, can be done anytime.

**Also in the background:**
  - Proactive Intelligence L2 design under review
  - Swarm Radar v2 mockup approved, not started
```

User sees **prioritized actions with reasoning**. Agent anchors on the top suggestion unless user redirects.

## E2E User Flow

```
Session start
    |
    v
build_session_briefing()            <-- existing L0+L1
    |
    v
_build_suggestions(threads,         <-- NEW L2
                   hints,
                   signals)
    |
    v
Score each item:
  priority_score   (P0=100, P1=40, P2=10)
  + staleness_score (days_open * 5, cap 30)
  + frequency_score (report_count * 8, cap 40)
  + blocking_score  (blocks others? +30)
  + momentum_score  (continued from last session? +15)
  - skip_penalty    (user skipped 2+ times? -20)
    |
    v
Rank by total score, take top 3
    |
    v
_generate_reasoning(ranked_items)   <-- explain the order
    |
    v
Inject into briefing:
  "**Suggested focus:**" replaces raw thread listing
  "**Also in background:**" for P2 items
    |
    v
Agent reads briefing, anchors on top suggestion
    |
    v
User either:
  a) Goes with suggestion  -> agent proceeds
  b) Picks different item  -> agent follows user (no resistance)
  c) Brings new task       -> agent pivots completely
```

## Scoring Model

### Input: Scoreable Items

Every item from these sources becomes a candidate:

| Source | Item type | Example |
|--------|-----------|---------|
| Open Threads P0 | Bug/blocker | "Tab switching loses streaming content" |
| Open Threads P1 | Important issue | "MCP servers not connecting" |
| Open Threads P2 | Nice-to-have | "Swarm Radar v2 redesign" |
| Continue hints | Unfinished work | "Level 2 design doc for review" |
| COE Registry | Investigation | "streaming content loss on tab switch" |

### Scoring Rules

```python
def _score_item(item: ScoredItem) -> int:
    score = 0

    # Priority weight (P0 dominates)
    score += {"P0": 100, "P1": 40, "P2": 10}.get(item.priority, 10)

    # Staleness (older = more urgent, cap at 30)
    if item.days_open:
        score += min(item.days_open * 5, 30)

    # Frequency (more reports = more important, cap at 40)
    score += min((item.report_count - 1) * 8, 40)

    # Blocking bonus (unblocks other items)
    if item.blocks_others:
        score += 30

    # Momentum bonus (was worked on last session)
    if item.from_continue_hint:
        score += 15

    # Skip penalty (user ignored this 2+ sessions in a row)
    if item.skip_count >= 2:
        score -= 20

    return max(score, 0)
```

### Score Examples

| Item | Priority | Days | Reports | Blocks | Continue | Skip | Total |
|------|----------|------|---------|--------|----------|------|-------|
| Tab-switch bug | P0 | 2 | 4 | no | no | 0 | 100+10+24+0+0-0 = **134** |
| MCP not connecting | P1 | 2 | 3 | yes | no | 0 | 40+10+16+30+0-0 = **96** |
| Sandbox network | P1 | 1 | 2 | no | no | 0 | 40+5+8+0+0-0 = **53** |
| PI L2 design | P1 | 0 | 1 | no | yes | 0 | 40+0+0+0+15-0 = **55** |
| Radar v2 | P2 | 6 | 1 | no | no | 0 | 10+30+0+0+0-0 = **40** |

Result: Tab-switch > MCP > PI L2 > Sandbox > Radar

### Blocking Detection

An item "blocks others" if:
- Its status text contains "blocks", "blocking", or "pending" referencing other items
- Multiple P1 items have status "Needs rebuild & verify" AND there's a rebuild-related thread
- It's a P0 and 2+ other items reference the same subsystem

Implementation: keyword matching on status text + simple cross-reference check. No graph traversal needed for V1.

### Skip Detection

Track which suggestions the user ignored:
- After each session, compare suggested items vs. DailyActivity deliverables
- If a suggested item appears in 2+ consecutive briefings but never in deliverables, increment `skip_count`
- Store skip counts in a lightweight JSON file: `~/.swarm-ai/proactive_state.json`

**V1 simplification:** Skip detection requires cross-session state. For initial delivery, **omit skip_penalty** — add it as a fast-follow once L2 is stable. This keeps L2 pure-functional (no state file) like L0/L1.

## Reasoning Generator

The "why this order" line is **not LLM-generated** — it's template-based:

```python
def _generate_reasoning(ranked: list[ScoredItem]) -> str:
    reasons = []
    for item in ranked[:3]:
        parts = []
        if item.blocks_others:
            parts.append(f"blocking {item.blocked_count} other items")
        if item.report_count >= 3:
            parts.append(f"reported {item.report_count}x")
        if item.days_open and item.days_open >= 3:
            parts.append(f"open {item.days_open} days")
        if item.from_continue_hint:
            parts.append("momentum from last session")
        if parts:
            reasons.append(f"{item.title}: {', '.join(parts)}")
    return ". ".join(reasons) + "." if reasons else ""
```

## Architecture

### New Code

```
backend/core/proactive_intelligence.py   <-- extend existing file
  + ScoredItem dataclass
  + _build_suggestions(threads, hints, signals) -> list[ScoredItem]
  + _score_item(item) -> int
  + _detect_blocking(threads) -> dict[str, list[str]]
  + _generate_reasoning(ranked) -> str
  + _format_suggestions(ranked) -> str
```

**No new files.** L2 extends `proactive_intelligence.py` — same module, same injection point. The `build_session_briefing()` function calls `_build_suggestions()` internally and swaps the raw thread listing for ranked suggestions.

### Data Flow

```
MEMORY.md ────────────┐
  (Open Threads)       |
                       |──> _parse_open_threads()  ──> threads
                       |
DailyActivity/ ───────|──> _parse_continue_hints() ──> hints
                       |
Pattern detection ────|──> _detect_patterns()       ──> signals
                       |
                       v
              _build_suggestions(threads, hints, signals)
                       |
                       v
              _score_item() for each candidate
                       |
                       v
              Sort by score, take top 3
                       |
                       v
              _format_suggestions() + _generate_reasoning()
                       |
                       v
              Inject into briefing (replaces raw P0/P1 listing)
```

### Briefing Structure Change

**Before (L0+L1):**
```
## Session Briefing
**Blockers:**         <-- raw P0 list
**Signals:**          <-- pattern signals
**Continue from:**    <-- Next: lines
**Also pending:**     <-- raw P1 list
```

**After (L2):**
```
## Session Briefing
**Suggested focus for this session:**   <-- ranked, scored, top 3
  1. ...
  2. ...
  3. ...
**Why this order:** ...                 <-- template reasoning
**Also in the background:**             <-- everything else, compact
```

The raw thread listing moves to "background" — still visible but not the primary signal. Blockers/signals are folded into the scoring, not shown separately.

**Fallback:** If `_build_suggestions()` fails or returns empty, fall back to L0+L1 format. No degradation.

## Multi-Tab Behavior

Same as L0+L1: read-only, no shared state. Both tabs get identical suggestions. Acceptable — user naturally picks one tab for focused work.

## Token Budget

| Section | L0+L1 | L2 |
|---------|-------|----|
| Blockers | ~30 tokens | folded into suggestions |
| Signals | ~60 tokens | folded into scoring |
| Continue-from | ~50 tokens | folded into scoring |
| P1 list | ~40 tokens | moved to background |
| **Suggestions** | n/a | ~80 tokens |
| **Reasoning** | n/a | ~40 tokens |
| **Background** | n/a | ~30 tokens |
| **Total** | ~185 tokens | ~150-200 tokens |

Net change: roughly neutral. Might save tokens by replacing verbose signal list with compact ranked suggestions.

## Edge Cases

| Case | Behavior |
|------|----------|
| No open threads, no hints | Return None (same as L0) — no briefing |
| Only P2 items | Show suggestions but label "Low priority — no blockers" |
| All items same score | Tiebreak: P0 > P1 > P2, then alphabetical |
| 10+ open threads | Only top 3 in suggestions, rest in background (1-line each) |
| User always ignores suggestions | V1: no penalty. V2: skip_penalty reduces score |
| New session immediately after previous | Continue-from items get momentum bonus |
| MEMORY.md malformed | try/except → fall back to L0+L1 format |

## What This Does NOT Do

- No LLM calls — purely deterministic rules
- No state file (V1) — stateless like L0/L1
- No effort estimation — we don't know how long things take (L3 concern)
- No autonomous action — suggestions only, user decides
- No mid-session updates — briefing is static at session start

## Test Plan

| Test | What it verifies |
|------|-----------------|
| Score P0 > P1 > P2 | Priority weight dominance |
| Score with blocking bonus | Blocking detection works |
| Score with staleness | Days-open calculation |
| Score with momentum | Continue-from bonus applied |
| Top-3 selection | Correct ranking and truncation |
| Reasoning generation | Template produces readable text |
| Blocking detection | Cross-reference keywords work |
| Fallback on empty | Returns None gracefully |
| Fallback on error | L0+L1 format used |
| Integration with build_session_briefing | Full pipeline produces L2 format |
| Token budget | Output stays under 250 tokens |
| Tiebreaking | Deterministic order on equal scores |

## Iteration Plan

1. **This delivery:** Core scoring + ranking + formatting. No skip detection, no state file.
2. **Fast-follow:** Skip detection via `proactive_state.json` — requires 2-3 sessions of data first.
3. **Later (L3):** Replace skip detection with proper learning loop.

## Open Questions for Review

1. **Should suggestions replace or augment the current briefing?** Proposed: replace raw thread listing, keep signals as inputs to scoring. The raw data is still in MEMORY.md if the agent needs it.

2. **Is the blocking bonus (30 points) too strong?** It can elevate a P1 above a P0 in some cases. Example: a P1 that blocks 3 other items (40+30=70) would still rank below a P0 (100), but a blocking P1 with reports (40+30+16=86) gets close. Is that the right behavior?

3. **Should the reasoning line be optional?** It adds ~40 tokens. We could skip it and let the agent infer the ordering. But explicit reasoning helps the agent explain its focus to the user.

4. **Top-3 or top-N?** 3 is compact. But some sessions have 1 clear focus — should we show just 1 when the top item dominates (score gap > 30)?

---

*This design doc is ready for review. No code has been written. Implementation starts after XG approves or revises.*
