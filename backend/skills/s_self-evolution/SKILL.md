---
name: self-evolution
description: >
  Governance lifecycle manager for the Three-Layer Governance OS.
  Six operations: CLASSIFY, CAPTURE, PROMOTE, REFINE, RETIRE, COMPRESS.
  Manages principles (SOUL), rules (AGENT/STEERING), and gates (hooks/code).
  Detects capability gaps, captures corrections with bias tags, auto-promotes
  recurring patterns (3x threshold), and prevents governance bloat.
  TRIGGER: always-active — auto-detects corrections, capability gaps, stuck states.
  Also invoked explicitly for governance changes ("add rule", "steeringify").
  DO NOT USE: for one-off file edits (just edit directly).
tier: always
platform: all
---
# Self-Evolution — Governance Lifecycle Manager

Manages the Three-Layer Governance OS: Principles → Rules → Gates.
Read INSTRUCTIONS.md for the full protocol.

## Six Operations

| Operation | Trigger | What It Does |
|-----------|---------|-------------|
| **CLASSIFY** | Any governance change proposed | Classify layer, parent principle, check conflict/budget |
| **CAPTURE** | User correction detected | Write C-entry with bias tag (A/B/C/D) |
| **PROMOTE** | Pattern seen 3x OR user approves | Elevate from EVOLUTION → AGENT/STEERING (absorbs steeringify) |
| **REFINE** | Principle not precise enough | Sharpen existing principle/rule wording |
| **RETIRE** | Rule covered by gate, or 30d idle | Remove from active rules, record in EVOLUTION |
| **COMPRESS** | 3+ rules share same root | Merge into single rule with broader coverage |

## Trigger Detection (unchanged from v1)

### 🔴 Reactive — Capability gap
### 🟡 Proactive — Better way exists (deferred)
### 🟢 Correction Capture — User pushback → C-entry with bias tag
### 🔵 Stuck — Going in circles

**Priority:** Stuck > Reactive > Correction > Proactive
**Limits:** Max 3 triggers/session. 60s cooldown.

## Bias Classification (on every correction)

| Bias | Pattern | Indicator |
|------|---------|-----------|
| **A** | Premature completion | "Good enough", skip review, defer known issue |
| **B** | Inference over evidence | Assert without reading, stale memory |
| **C** | Productivity over quality | Rush output, shallow analysis |
| **D** | Surrender on obstacle | Give up, ask user to compensate |

## Governance Budget (enforced on PROMOTE/REFINE)

- SOUL.md principles: ≤5
- AGENT.md rules: ≤25
- STEERING.md standing rules: ≤15

At cap → COMPRESS or RETIRE existing before adding.

## Intake Classification (on every governance change)

Before writing to SOUL/AGENT/STEERING:
1. **Layer:** principle / rule / gate?
2. **Parent:** P1-P4?
3. **Conflict:** duplicates/contradicts existing?
4. **Budget:** current count vs cap
5. **Source:** user / correction / pipeline / self-detect

Output brief → user confirms (user source) or auto-apply (3x agent evidence).

## Config

| Key | Default | Effect |
|-----|---------|--------|
| `enabled` | `true` | Kill switch |
| `max_triggers_per_session` | `3` | Per-session cap |
| `promotion_threshold` | `3` | Same-class corrections needed for auto-promote |
| `deprecation_days` | `30` | Days idle before retirement candidate |

## Writing to EVOLUTION.md

Use Read + Edit tools. Never `python3 locked_write.py` (PyInstaller crash).
JSONL changelog append after every mutation (mandatory).

## Verification

- [ ] **Correction captured with bias tag** — C-entry has [Bias A/B/C/D]
- [ ] **Classification brief output** — before any governance file write
- [ ] **Budget respected** — count verified before PROMOTE
- [ ] **JSONL changelog updated** — after every EVOLUTION.md mutation
