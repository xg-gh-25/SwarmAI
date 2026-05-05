---
name: swarm-build
description: >
  Build SwarmAI backend binary (PyInstaller), verify capabilities, deploy to
  daemon, and restart. Replaces ./prod.sh build with per-stage visibility.
  TRIGGER: "build", "build backend", "swarm build", "deploy binary",
  "build and deploy", "编译", "打包".
  DO NOT USE: for desktop/Tauri builds (manual), for release (use s_swarm-release),
  for dev mode start (use s_swarm-dev), for non-SwarmAI projects.
  SIBLINGS: s_swarm-daemon = daemon ops only | s_swarm-release = full release cycle |
  s_swarm-ci = CI status check.
tier: lazy
platform: desktop
project_scope: SwarmAI
---

# Swarm Build

Build the SwarmAI backend binary and deploy to daemon. Read INSTRUCTIONS.md before proceeding.

**Project Guard:** This skill is SwarmAI-only. If the active project is NOT SwarmAI,
ABORT with: "s_swarm-build is SwarmAI-only. This project has its own deploy workflow."
