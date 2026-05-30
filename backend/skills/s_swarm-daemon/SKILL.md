---
name: swarm-daemon
description: "Manage the SwarmAI launchd daemon: status, stop, start, restart, deploy, logs, and health verification. Replaces manual launchctl and dev.sh daemon commands with structured per-step execution.\n\
  \  TRIGGER: \"daemon status\", \"restart daemon\", \"daemon logs\", \"stop daemon\".\n  NOT FOR: s_swarm-build, s_swarm-hive use cases."
tier: lazy
platform: desktop
project_scope: SwarmAI
---

# Swarm Daemon

Manage the SwarmAI backend daemon (com.swarmai.backend). Read INSTRUCTIONS.md before proceeding.

**Project Guard:** This skill is SwarmAI-only. If the active project is NOT SwarmAI,
ABORT with: "s_swarm-daemon is SwarmAI-only. This project has its own service management."
