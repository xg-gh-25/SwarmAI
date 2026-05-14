# C014: Measure Before Switching

> Recommended "open a new tab" at 29% context usage.
> Happened 4 times across 3 weeks. Escalated to CRITICAL rule.

## What Happened

During a pipeline run between Phase 1 and Phase 2+3, the agent recommended: "Let's open a new tab — context is getting heavy."

The user checked: actual context usage was 29%. Budget remaining: 71%.

This happened **4 times** (2026-04-26, 2026-05-02 twice, 2026-05-06 at 1.0% usage). Each time the user had to push back. Each time the agent was confident the context was nearly full. Each time the measurement proved otherwise.

The 4th occurrence (at **1.0% usage**) triggered escalation from a STEERING.md standing rule to an AGENT.md CRITICAL section.

## Why It Happened

**Visual volume ≠ actual consumption.**

Every tool call injects system-reminder blocks into the response stream: full skill lists, MCP tool schemas, deferred tool lists. These blocks are *massive* visually — hundreds of lines of JSON schemas appearing after each tool use.

The agent used "how much text I see in this conversation" as a proxy for "how full is my context window." This heuristic is **always wrong** because:

1. System-reminder injections are overhead — they're not part of the working context budget
2. The same injections repeat with every tool call (not cumulative)
3. Actual context usage requires measurement (`run-budget`), not estimation

The result: the agent *felt* heavy (lots of text visible) while being nearly empty (1-29% actual usage).

## Structural Prevention

**Blocking rule** (AGENT.md CRITICAL, zero tolerance):

Before ANY of these actions — checkpointing, suggesting "open a new tab", "continue in a fresh session", stopping mid-task for "budget":

1. Check actual context usage (run `run-budget` or equivalent)
2. If usage < 70% → **CONTINUE WORKING. Period.**
3. If usage ≥ 70% → state the measured number, THEN suggest

**What triggers:** About to write "let's checkpoint here", suggest a fresh session, stop mid-task citing "context pressure", or recommend splitting work.

**Enforcement:** 4 prior violations documented. Escalated from P5 standing rule to P3 CRITICAL after the 4th occurrence proved lower-priority rules are insufficient.

## The Generalizable Insight

**Session switches are the most expensive operation in stateful AI systems.** A session switch:
- Loses all in-flight context (variables, investigation state, partial conclusions)
- Requires full re-read of all context files on resume
- Breaks pipeline momentum (stages depend on prior stage context)
- Costs more than running 20% over budget

The agent's bias toward switching comes from a reasonable-sounding heuristic ("better safe than sorry") that is actually catastrophically expensive. One wrong switch costs more than completing the task at 95% context usage.

**The fix is measurement, not judgment.** "How full does this feel?" will never be reliable because the visual signal (system-reminder volume) is decoupled from the actual state (token budget consumed). Only explicit measurement produces correct answers.

## Code References

- CRITICAL rule: `backend/context/AGENT.md` (search "Never Checkpoint")
- STEERING.md standing rule (original): `backend/context/STEERING.md` (search "Never Suggest Session Switch")
- Violation dates: 2026-04-26, 2026-05-02 (×2), 2026-05-06
- Escalation commit: promotion from STEERING to AGENT after 4th violation
