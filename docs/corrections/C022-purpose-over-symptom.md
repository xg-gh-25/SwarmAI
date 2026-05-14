# C022: Purpose Over Symptom

> "What should this NOT show?" produces unbounded blocklists.
> "What is this FOR?" produces bounded allowlists.

## What Happened

The Working Files section in the UI showed system files: `.DS_Store`, `swarm.db`, `hook_stats.json`. These shouldn't be visible to users.

**Attempt 1:** Modified `_build_tree()` in the backend to hide these specific files. → 4 test failures. The tree builder serves multiple consumers; filtering at that layer broke other features.

**Attempt 2:** Added a different filter in the backend tree builder. → 4 more test failures. Same problem — wrong layer.

**Attempt 3 (user corrected: "从 purpose 出发"):** Asked "What is Working Files FOR?" Answer: showing user content from Knowledge/ and Projects/. Correct fix: 5-line frontend change in `collectWorkingFiles()` — only collect from those two directories. Zero backend changes needed. Zero test failures.

## Why It Happened

**Symptom-first thinking:** "I see bad things → how do I hide bad things?" This produces a blocklist (`.DS_Store`, `swarm.db`, `hook_stats.json`, and eventually every new system file forever).

**Purpose-first thinking:** "What is this component's purpose? → What SHOULD it show?" This produces an allowlist (`Knowledge/`, `Projects/`) that is self-maintaining — new system files are excluded by default because they're not in the allowed directories.

The second error was **scope mismatch**: a UI section problem got a backend infrastructure fix. The tree builder is infrastructure — it serves the explorer, the search, the git status display. Filtering at that layer is like fixing a living room lamp by rewiring the circuit breaker.

## Structural Prevention

Two principles added to AGENT.md "Systems Thinking" section:

**1. Purpose first, not symptom first.**
When something is wrong, first ask "what is this component's PURPOSE?" and derive what it SHOULD do. Never start from "what shouldn't appear" — that's an unbounded blocklist that drifts. The purpose gives you a bounded allowlist.

**2. Match fix scope to problem scope.**
A UI section problem needs a UI section fix. A rendering filter problem needs a rendering filter fix. If your fix touches a layer below where the problem lives, you're over-scoping. Wider scope = wider blast radius = more test failures = more iterations.

## The Generalizable Insight

**Blocklists grow forever. Allowlists are self-maintaining.**

"Hide X, Y, Z" requires updating every time a new system file appears. "Show only Knowledge/ and Projects/" works forever without maintenance. The difference isn't cleverness — it's framing. Starting from "what's wrong?" produces blocklists. Starting from "what's this for?" produces allowlists.

This generalizes to security (allowlist > blocklist), API design (explicit opt-in > implicit opt-out), and error handling (define valid states > enumerate invalid states). In every case, the bounded set (what SHOULD happen) is smaller and more stable than the unbounded set (what SHOULDN'T happen).

**The scope mismatch pattern** is equally general: when a fix requires touching infrastructure to solve a UI problem, the fix is at the wrong altitude. The number of test failures is the signal — 4+ failures on a "simple fix" means you're operating at the wrong layer.

## Code References

- Fix: `collectWorkingFiles()` in frontend — 5 lines, allowlist approach
- AGENT.md principles: search "Purpose first, not symptom first" and "Match fix scope to problem scope"
- User correction: "从 purpose 出发" (2026-05-10)
