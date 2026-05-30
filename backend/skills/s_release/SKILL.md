---
name: release
description: "Bump version across all package files, update CHANGELOG, create git tag, and publish GitHub Release. Ensures no file is missed during version bumps. SUPERSEDED BY: s_swarm-release (which includes\
  \ build + package + smoke test). Use this skill ONLY for version-bump-without-shipping scenarios.\n  TRIGGER: \"bump version only\", \"version bump\", \"just tag\".\n  NOT FOR: s_swarm-release, s_swarm-build\
  \ use cases."
tier: lazy
---
# Release (Version Bump Only)

> **Note:** For full releases, use `s_swarm-release` instead. This skill only
> handles version bump + changelog + tag — no build, no package, no smoke test.

Read INSTRUCTIONS.md for the version bump workflow.
