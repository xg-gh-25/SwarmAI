---
name: swarm-ci
description: >
  Check SwarmAI GitHub Actions CI status: list recent runs, diagnose failures,
  and summarize health. Replaces ad-hoc gh run commands with structured output.
  TRIGGER: "CI status", "check CI", "is CI green", "CI failures", "why did CI fail",
  "GitHub Actions", "build status", "CI 状态".
  DO NOT USE: for running tests locally (use pytest directly), for Hive CI
  (Hive has no CI), for non-SwarmAI repos.
  SIBLINGS: s_swarm-build = local build | s_swarm-release = full release cycle.
tier: lazy
platform: desktop
project_scope: SwarmAI
---

# Swarm CI

Check GitHub Actions CI status for the SwarmAI repo. Read INSTRUCTIONS.md before proceeding.

**Project Guard:** This skill is SwarmAI-only. If the active project is NOT SwarmAI,
ABORT with: "s_swarm-ci is for the SwarmAI GitHub repo only."
