# Corrections — How SwarmAI Learns From Failure

27 captured corrections. Each one produced a structural prevention that eliminates the entire class of bug, not just the instance.

## Why This Exists

Most AI agent projects show you what works. This shows what broke, why it broke, and the architectural fix that prevents recurrence. Every correction is a citable case study with code references.

**The pattern:** Each correction follows `incident → investigation → root cause → structural fix`. The fix never patches the symptom — it makes the entire class of failure structurally impossible.

## Tier 1 — Full Case Studies

Corrections that produced generalizable architectural insights.

| ID | Title | Structural Output |
|----|-------|-------------------|
| C007 | [Goal Over Tool](./C007-goal-over-tool.md) | 3-attempt alternative search before reporting failure to user |
| C009 | [Think Before Code](./C009-think-before-code.md) | Pre-Implementation Checkpoint (5 mandatory items) |
| C011 | [Pipeline Confidence Illusion](./C011-pipeline-confidence-illusion.md) | State Machine Audit + Post-Implementation E2E Checkpoint |
| C014 | [Measure Before Switching](./C014-measure-before-switching.md) | Context budget measurement as blocking gate |
| C020 | [Extract ≠ Extend](./C020-extract-not-extend.md) | Separate commits + calling context audit |
| C022 | [Purpose Over Symptom](./C022-purpose-over-symptom.md) | Allowlist from purpose > blocklist from symptoms |
| C023 | [Understand the State Machine](./C023-understand-the-state-machine.md) | Draw all states × transitions × timing before fixing |

## Tier 2 — All 27 Corrections

| ID | Date | One-liner |
|----|------|-----------|
| C001 | 2026-03-13 | Tab-switch streaming loss — diagnosis without durable fix across 4 sessions |
| C003 | 2026-03-15 | Conflated in-app MCP bug with Claude Code session MCPs in memory |
| C004 | 2026-03-19 | Sycophantic compliance — wrote rules silently, raised disagreement as afterthought |
| C005 | 2026-03-19 | False memory propagation — reported wrong implementation status for 5+ sessions |
| C006 | 2026-03-26 | Context % calculated from cumulative API usage, not per-call |
| **C007** | **2026-04-09** | **Tool failure → ask user to fix, instead of trying alternative paths** |
| C008 | 2026-04-12 | Architecture topology answered from stale mental model, not verified |
| **C009** | **2026-04-12** | **Coded before thinking — 5 iterations on a 55-line solution** |
| C010 | 2026-04-15 | Asserted eliminated concept ("MAX_CONCURRENT=2") as current architecture |
| **C011** | **2026-04-25** | **Full pipeline, 10/10 confidence, 57 tests green — feature 100% non-functional** |
| C012 | 2026-04-25 | WebFetch failed → asked user to paste content instead of trying curl |
| C013 | 2026-04-29 | Ran full test suite proactively, caused xdist deadlock |
| **C014** | **2026-05-02** | **Recommended session switch at 29% context usage (4 occurrences)** |
| C015 | 2026-05-03 | Optimized a bottleneck that didn't exist (context loading: 78ms, not slow) |
| C016 | 2026-05-05 | Restarted daemon 3x to "verify" — killed all active sessions |
| C017 | 2026-05-05 | Ran raw shell command instead of using the skill that wraps it safely |
| C018 | 2026-05-05 | Generated report insights as data→text format conversion, not LLM judgment |
| C019 | 2026-05-06 | Made factual assertions about own system from inference, not verification |
| **C020** | **2026-05-08** | **Combined function extraction with new caller in one commit — hid 2 bugs** |
| C021 | 2026-05-09 | Skipped mandatory adversarial review when validator schema was strict |
| **C022** | **2026-05-10** | **Fixed wrong layer (backend) for a UI problem — symptom-first thinking** |
| **C023** | **2026-05-13** | **Daemon upgrade hung 3 rounds — each round only fixed previous symptom** |
| C024 | 2026-05-14 | Research task declared done after reading descriptions, never read actual code |

## How Corrections Compound

```
Incident (one bug)
    → Correction (one structural fix)
        → Standing Rule (prevents class in all future sessions)
            → Code Quality Scan pattern (auto-detected in future code)
                → Pipeline REVIEW pattern (caught before commit)
```

Each layer makes the next incident less likely. After 27 corrections, the system has 27 independent prevention layers — each earned, not speculated.

## Reading Guide

- **For AI agent architects:** Start with C011 (pipeline confidence) and C023 (state machine understanding). These are the hardest problems in autonomous systems.
- **For engineering leaders:** Start with C009 (think before code) and C014 (measure before switching). These generalize beyond AI agents.
- **For researchers:** The full list shows correction velocity: ~1 per 2.2 days, with severity decreasing over time (catastrophic → edge case).
