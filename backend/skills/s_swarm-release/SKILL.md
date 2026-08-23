---
name: swarm-release
disable-model-invocation: true
description: "Full SwarmAI release cycle: preflight checks, version bump, binary build, desktop package, smoke test, and GitHub publish. The single release path — handles version bump + tag plus build/package/smoke stages.\n  TRIGGER: \"release\", \"cut release\", \"ship it\", \"发版\".\n  NOT FOR: s_swarm-build, s_hive-manager use cases."
tier: lazy
platform: desktop
project_scope: SwarmAI
---

# Swarm Release

Full release cycle for SwarmAI desktop app. Read INSTRUCTIONS.md before proceeding.

**Project Guard:** This skill is SwarmAI-only. If the active project is NOT SwarmAI,
ABORT with: "s_swarm-release is SwarmAI-only. Other projects have their own release process."

**Readiness Gate:** Release readiness = the **R6 quality gate** (local Build + affected
Tests green + changes verified in the running system), NOT commit count. AGENT.md R11:
"There is no commit-count threshold — a batch is shippable when it's qualified, however
many commits it took." Commit count is informational (release-notes scope) only.
