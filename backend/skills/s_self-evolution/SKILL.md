---
name: self-evolution
description: "Manage governance (SOUL principles, AGENT/STEERING rules, gates): classify, capture, promote, refine, retire, compress. TRIGGER: \"add rule\", \"promote this correction\", \"steeringify\", \"加条规则\". NOT FOR: one-off file edits."
tier: always
platform: all
---
# Self-Evolution — Governance Lifecycle Manager

Manages the Three-Layer Governance OS: Principles → Rules → Gates.
Read INSTRUCTIONS.md for the full protocol.

## Six Operations

| Operation | Trigger | What It Does |
|-----------|---------|-------------|
| **CLASSIFY** | Any governance change proposed | Classify layer, parent principle, check overlap and size delta |
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

## Admission, not a count budget (enforced on PROMOTE/REFINE)

There is no count cap (AGENT.md § Intake Gate): the real risk is attention dilution, which no
count measures. Every new rule passes three questions — a NO blocks it: (1) a genuinely new
axis? overlap → fold into the existing rule; (2) load-bearing — can I name the failure it
prevents? (3) belongs here — principle → SOUL, one-off → EVOLUTION, better as a mechanism →
SOUL P7. Before drafting, ask whether a clearer skill, a better tool result, a mechanism or an
edit to an existing rule fixes it: a new sentence in the prompt is the last resort. An addition
that would dilute the set must REPLACE an existing rule or not land.

## Intake Classification (on every governance change)

Before writing to SOUL/AGENT/STEERING:
1. **Layer:** principle / rule / gate / knowledge?
2. **Parent:** which SOUL principle?
3. **Overlap:** `context_recall_cli.py --file <F> --query "<proposed text>"` over SOUL / AGENT /
   STEERING / SELF; list the top matches and say fold-into or new-axis.
4. **Delta:** what the change removes, and the file's size before → after.
5. **Source:** user / correction / pipeline / self-detect

Output brief → the user approves. Governance is never auto-written; 3× evidence only makes a
proposal eligible.

## Config

| Key | Default | Effect |
|-----|---------|--------|
| `enabled` | `true` | Kill switch |
| `max_triggers_per_session` | `3` | Per-session cap |
| `promotion_threshold` | `3` | Same-class corrections needed before a proposal is surfaced |
| `deprecation_days` | `30` | Days idle before retirement candidate |

## Writing to EVOLUTION.md

Use Read + Edit tools. Never `python3 locked_write.py` (PyInstaller crash).
JSONL changelog append after every mutation (mandatory).

## Verification

- [ ] **Correction captured with bias tag** — C-entry has [Bias A/B/C/D]
- [ ] **Classification brief output** — before any governance file write
- [ ] **Admission passed** — overlap listed, fold-or-new-axis stated, size delta shown
- [ ] **JSONL changelog updated** — after every EVOLUTION.md mutation
