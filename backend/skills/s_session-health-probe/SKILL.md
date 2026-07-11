---
name: session-health-probe
disable-model-invocation: true
description: "Runtime daemon + live-session liveness probe (zero-LLM, deterministic): daemon up, sessions progressing (not wedged — RP41 double-signal), RSS under budget, no unrecovered failure events, deployed commit == expected. Snapshot now; runs as a 15-min job; red → Slack."
trigger:
  - session health
  - runtime health
  - are sessions healthy
  - is the daemon healthy
  - session liveness
  - check running sessions
do_not_use:
  - static self-cognition/memory/governance (use loops-health)
  - post-build assumption verification (use health-check)
  - frontend chat-experience contract (use chat-brain-check)
  - desktop CPU/RAM worst-offenders report (use system-health)
tier: lazy
platform: all
---
# session-health-probe

> This skill loads full instructions on activation. Read INSTRUCTIONS.md before proceeding.

## Boundary (why this is the 5th health skill, not a merge)

This skill owns ONE axis no other health skill covers: **dynamic RUNTIME liveness
of the daemon and its live sessions, right now.** It is deterministic and zero-LLM.

| Skill | Axis | When |
|-------|------|------|
| **session-health-probe** (this) | **runtime daemon + live-session liveness** | continuous / 15-min job / on demand |
| s_loops-health | static self-cognition (memory, knowledge, evolution, governance) | periodic |
| s_health-check | post-build assertion of critical assumptions | after a build |
| s_chat-brain-check | frontend chat-experience contract | after a chat change |
| s_system-health | desktop CPU/RAM worst-offenders report | on demand |

If the question is "is the running system alive and are its sessions
progressing?" → this skill. Anything static, post-build, or frontend → not this.
