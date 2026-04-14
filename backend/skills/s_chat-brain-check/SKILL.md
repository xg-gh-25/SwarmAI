---
name: chat-brain-check
description: Tiered chat experience validator -- quick checks (5min) after every change, full audit (30min) before releases. Covers state machine invariants, SSE pipeline, streaming indicators, queue drain, and regression detection.
trigger:
  - chat brain check
  - chat health
  - chat regression test
  - test chat experience
  - verify chat pipeline
  - chat audit
  - is chat working
do_not_use:
  - general app health (use health-check)
  - UI-only review (use web-design-review)
  - backend API review unrelated to chat
siblings:
  - health-check = post-build smoke test
  - code-review = general code review
tier: lazy
---
# chat-brain-check

> This skill loads full instructions on activation. Read INSTRUCTIONS.md before proceeding.
