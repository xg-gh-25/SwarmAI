---
name: swarm-daemon
description: >
  Manage the SwarmAI launchd daemon: status, stop, start, restart, deploy,
  logs, and health verification. Replaces manual launchctl and dev.sh daemon
  commands with structured per-step execution.
  TRIGGER: "daemon status", "restart daemon", "daemon logs", "stop daemon",
  "start daemon", "daemon health", "deploy daemon", "守护进程".
  DO NOT USE: for building the binary (use s_swarm-build first), for Hive
  instances (use s_swarm-hive), for non-SwarmAI services.
  SIBLINGS: s_swarm-build = build + verify + deploy + restart |
  s_swarm-hive = cloud instance management.
tier: lazy
platform: desktop
project_scope: SwarmAI
---

# Swarm Daemon

Manage the SwarmAI backend daemon (com.swarmai.backend). Read INSTRUCTIONS.md before proceeding.

**Project Guard:** This skill is SwarmAI-only. If the active project is NOT SwarmAI,
ABORT with: "s_swarm-daemon is SwarmAI-only. This project has its own service management."
