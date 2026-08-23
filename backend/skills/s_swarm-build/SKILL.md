---
name: swarm-build
disable-model-invocation: true
description: "Build SwarmAI backend binary (PyInstaller), verify capabilities, deploy to daemon, and restart. Replaces ./prod.sh build with per-stage visibility.\n  TRIGGER: \"build\", \"build backend\"\
  , \"swarm build\", \"deploy binary\".\n  NOT FOR: s_swarm-release use cases."
tier: lazy
platform: desktop
project_scope: SwarmAI
---

# Swarm Build

Build the SwarmAI backend binary and deploy to daemon. Read INSTRUCTIONS.md before proceeding.

**Project Guard:** This skill is SwarmAI-only. If the active project is NOT SwarmAI,
ABORT with: "s_swarm-build is SwarmAI-only. This project has its own deploy workflow."
