---
name: Release
description: >
  Bump version across all package files, update CHANGELOG, create git tag, and
  publish GitHub Release. Ensures no file is missed during version bumps.
  TRIGGER: "release", "bump version", "cut release", "new version", "发版",
  "版本升级", "打 tag", "create release".
  DO NOT USE: for build/verify (use ./prod.sh build), DMG packaging, or deploy.
  SIBLINGS: deliver = pipeline artifact packaging | qa = test verification |
  release = version bump + tag + GitHub Release.
tier: lazy
---
# Release

Read INSTRUCTIONS.md for the full release workflow.
