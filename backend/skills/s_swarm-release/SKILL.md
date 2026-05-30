---
name: swarm-release
description: "Full SwarmAI release cycle: preflight checks, version bump, binary build, desktop package, smoke test, and GitHub publish. Supersedes s_release (which only does version bump + tag) by adding\
  \ build/package/smoke stages.\n  TRIGGER: \"release\", \"cut release\", \"ship it\", \"发版\".\n  NOT FOR: s_swarm-build, s_release, s_swarm-hive use cases."
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
