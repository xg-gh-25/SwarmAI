---
name: swarm-release
description: >
  Full SwarmAI release cycle: preflight checks, version bump, binary build,
  desktop package, smoke test, and GitHub publish. Supersedes s_release (which
  only does version bump + tag) by adding build/package/smoke stages.
  TRIGGER: "release", "cut release", "ship it", "发版", "版本升级", "new version",
  "release swarm", "swarm release".
  DO NOT USE: for build-only (use s_swarm-build), for version bump without
  shipping (use s_release), for Hive updates (use s_swarm-hive).
tier: lazy
platform: desktop
project_scope: SwarmAI
---

# Swarm Release

Full release cycle for SwarmAI desktop app. Read INSTRUCTIONS.md before proceeding.

**Project Guard:** This skill is SwarmAI-only. If the active project is NOT SwarmAI,
ABORT with: "s_swarm-release is SwarmAI-only. Other projects have their own release process."

**Scope Gate:** STEERING rule mandates ≤20 commits per release without sign-off.
If >20 commits since last tag, WARN and require explicit approval before proceeding.
