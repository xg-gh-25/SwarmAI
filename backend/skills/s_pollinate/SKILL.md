---
name: Pollinate
description: >
  Swarm's content production engine. Transforms domain knowledge into
  multi-format, multi-platform content via an 8-stage pipeline
  (EVALUATE->REFLECT). Video-first: B站, YouTube, 小红书, 抖音, 视频号.
  Knowledge in, content out, spread everywhere.
  TRIGGER: "pollinate", "make content about", "create video about",
  "make a video", "content pipeline", "produce content".
  DO NOT USE: for one-off text generation (just write), for code changes
  (use autonomous-pipeline), for research without content output (use
  deep-research).
  SIBLINGS: autonomous-pipeline = code delivery | deep-research = research
  only | summarize = quick condensation | pollinate = full content production.
tier: lazy
---

# Pollinate -- Swarm Content Production Engine

> Knowledge in, content out, spread everywhere.

Read INSTRUCTIONS.md before proceeding.

## Quick Start

Tell your agent: **"Pollinate: [topic]"** or **"Make a video about [topic]"**

## Verification

Before marking a Pollinate run complete, show evidence for each:

- [ ] **REPORT.md generated** -- saved to `content/{name}/REPORT.md`
- [ ] **All 8 stages executed** -- EVALUATE through REFLECT
- [ ] **Confidence score calculated** -- breakdown shown
- [ ] **Decision log complete** -- every decision classified (mechanical/taste/judgment)
- [ ] **Studio preview reviewed** -- user approved before 4K render
- [ ] **Platform specs validated** -- `check_specs.py` passed for all target platforms
