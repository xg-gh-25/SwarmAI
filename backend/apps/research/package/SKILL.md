---
name: research
description: >
  Investigate a question against high-trust primary sources and capture findings as
  a Markdown file. Delegates reading legwork to a background agent.
  TRIGGER: "research X", "look into Y", "find out about Z", "docs/API facts needed",
  "delegate this reading", or when the user wants a topic researched in depth.
  DO NOT USE: for simple factual lookups that don't need a written artifact, or when
  the user wants to do the reading themselves.
tier: lazy
---
# Research

> **Adapted from:** [mattpocock/skills](https://github.com/mattpocock/skills) @ `5beb35b040e3de97e54fa9e386d7e42d15c295c2`
>
> **License:** MIT — see `LICENSE.upstream`
>
> **Modification notice:** This wrapper adapts the upstream skill for SwarmAI conventions.
> Original files are preserved verbatim under `upstream/`. The instructions below integrate
> upstream discipline with this repository's documentation locations.

TRIGGER: "research X", "look into Y", "find out about Z", "docs/API facts needed",
"delegate this reading", or when the user wants a topic researched in depth.

DO NOT USE: for simple factual lookups that don't need a written artifact, or when
the user wants to do the reading themselves.

## Instructions

Read `INSTRUCTIONS.md` before proceeding. Reference files:

- `upstream/SKILL.md` — original upstream instructions (verbatim)
- `upstream/openai.yaml` — optional agent metadata

This skill requires network access for web research and subagent capability.
