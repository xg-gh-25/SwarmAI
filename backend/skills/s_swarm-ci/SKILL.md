---
name: swarm-ci
disable-model-invocation: true
description: "Check SwarmAI GitHub Actions CI status: list recent runs, diagnose failures, and summarize health. Replaces ad-hoc gh run commands with structured output.\n  TRIGGER: \"CI status\", \"check\
  \ CI\", \"is CI green\", \"CI failures\".\n  NOT FOR: pytest use cases."
tier: lazy
platform: desktop
project_scope: SwarmAI
---

# Swarm CI

Check GitHub Actions CI status for the SwarmAI repo. Read INSTRUCTIONS.md before proceeding.

**Project Guard:** This skill is SwarmAI-only. If the active project is NOT SwarmAI,
ABORT with: "s_swarm-ci is for the SwarmAI GitHub repo only."
