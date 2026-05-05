---
name: release
description: >
  Bump version across all package files, update CHANGELOG, create git tag, and
  publish GitHub Release. Ensures no file is missed during version bumps.
  SUPERSEDED BY: s_swarm-release (which includes build + package + smoke test).
  Use this skill ONLY for version-bump-without-shipping scenarios.
  TRIGGER: "bump version only", "version bump", "just tag".
  DO NOT USE: for full releases (use s_swarm-release), build/verify (use
  s_swarm-build), DMG packaging, or deploy.
  SIBLINGS: s_swarm-release = full release cycle | deliver = pipeline artifact
  packaging | qa = test verification.
tier: lazy
---
# Release (Version Bump Only)

> **Note:** For full releases, use `s_swarm-release` instead. This skill only
> handles version bump + changelog + tag — no build, no package, no smoke test.

Read INSTRUCTIONS.md for the version bump workflow.
