---
name: swarm-cr
description: "Create an Amazon CRUX code review (CR) for a bound internal repo — resolve the checked-out worktree from bindings.yaml, develop + git-commit the change locally (agent-automated), then hand the human ready-to-run `brazil-build release` + `cr` commands (human-in-the-loop, because those cross the build-duration + ssh-agent auth walls). The create-half sibling of s_swarm-code-reviewer.\n  TRIGGER: \"create CR\", \"create a CR\", \"raise a CR\", \"发 CR\", \"发个 CR\", \"给这个 repo 建 CR\", \"cut a CR for <bound repo>\".\n  NOT FOR: reviewing an existing CR (use s_swarm-code-reviewer), local git/GitHub PR review (use code-review), or pushing to a public GitHub repo."
version: 1.0.0
tier: lazy
---
# Swarm CR (Create)

> This skill loads full instructions on activation. Read INSTRUCTIONS.md before proceeding.

Creates an Amazon CRUX code review for a **bound internal repo** (a project with a
`bindings.yaml` delivery_contract, e.g. AIDLC → GCRAIDLCPreset). It resolves the
already-checked-out Brazil worktree from `bindings.yaml`, develops the change and
`git commit`s it **locally** (agent-automated — local git needs no remote auth),
then **assembles but does NOT execute** the `brazil-build release` (verify) and
`cr --auto-publish` (create CR) commands, handing them to the human to run locally.

**Why the split (human-in-the-loop):** `brazil-build release` runs multi-minute
(exceeds the agent's foreground cap) and `cr` pushes to `git.amazon.com` which needs
the mwinit ecdsa-cert loaded into ssh-agent (a passphrase-protected, human-only
step). Local develop + `git commit` cross neither wall, so the agent does those; the
two walled steps are handed off. The agent never blocks on brazil or git-remote auth.

TRIGGER (subset of the frontmatter description above — that is the SDK-extracted source of truth): "create CR", "raise a CR", "发 CR", "cut a CR for <bound repo>"
DO NOT USE: to review an existing CR (use `s_swarm-code-reviewer`), for local git working-tree / GitHub PR review (use `code-review`), or to push to a public GitHub repo (that is not the CRUX flow).

Boundary: the agent NEVER runs `git push` (forbidden — CRUX auto-merge owns the
remote), NEVER runs `cr --auto-merge` or approves (approve has no API — always a
human click in CRUX), and NEVER calls `bind_repo` (it would `rmtree` the human's
live worktree). Scope: Phase 1 = single bound repo, triggered manually. Pipeline
DELIVER-stage integration is a future phase — this skill is standalone + user-triggered.
