---
title: "Hive tasks: the toolchain a repository's checks need"
date: 2026-11-02
created: 2026-11-02
updated: 2026-11-02
project: SwarmAI
status: r3, simplified; waiting for XG
---

# Hive tasks: the toolchain a repository's checks need

r3 replaces r1/r2 (in git history). They grew one mechanism per review finding: a
five-source version resolver, an admission list, a read-only store, a runnable-check
task with its own result format. XG: too complicated. r3 keeps only what the problem
needs.

## 1. The problem

A task on a Hive runs its Project Agent's setup lines and checks (`scripts/task_checks`).
Those lines are CI `run:` blocks copied verbatim, and they assume the tools the CI job's
`uses:` steps installed (`setup-python`, `setup-node`, `setup-bun`, `rust-toolchain`).
A Hive has only `python3.12`, Node 20, npm, git, gcc and make (`backend/hive/user_data.py:26`).
So `pip install uv` fails (`pip: command not found`), `bun …` fails, `cargo check` fails.
Observed on the demo Hive in E2E round 3; read from the SwarmAI, gbrain and AIDLC
Brains on 2026-11-02.

## 2. The fix: tools are part of the definition, one helper installs them

1. **The Hive package ships mise** (one static binary, pinned version, sha256 checked in
   `hive/release.sh`) and a small script `hive/bin/task-tools`. New, updated and
   pre-baked Hives all get both through the package; no provisioning change.
2. **A mode may list its tools** in `project-agent.yaml`:
   ```yaml
   tools: [python@3.11, node@22, rust@stable]
   ```
   with a `source` like every other row. The drafting agent reads them from the CI job's
   setup steps and the repo's version files (`.nvmrc`, `rust-toolchain.toml`, …) — the
   same judgment it already makes for setup and checks. `validate` checks only the shape
   (`name@version`, plain characters). A person can fix a wrong one by hand.
3. **The Host puts one setup line first** when the mode has tools
   (`core/swarm_out.work_checks`): `task-tools python@3.11 node@22 rust@stable`.
   Because setup is then never empty, the agent is always told to run setup
   (`task_spec.py:390`), so a checks-only mode gets its tools too.
4. **`task-tools`** runs `mise install` for each tool, then writes the tools' paths and
   variables (`mise env`) to `$GITHUB_PATH` / `$GITHUB_ENV`, which `task_checks` already
   reads for every later line and check. For python it makes a venv in the task's
   `checks/` directory from that python, so `pip install …` stays inside the task (what
   `setup-python` gives a job). It runs mise with a clean environment (no inherited
   `MISE_*`, config files pointed at empty ones, working directory outside the repo), so
   no repository or leftover config can add hooks.

That is the whole change. An older Hive without `task-tools` fails that first line with
"command not found", which says exactly what to do: update the Hive.

## 3. What it does not do

Services (`postgres`), containers, secrets and system packages (Tauri's webkit libs) are
not provided. A check that needs them fails with its own log, and the dispatching agent
reads why. No extra mechanism for this: to see what a Brain can run on a Hive, dispatch
an ordinary task that runs setup and every check.

`e48a5689c` (a setup-python venv found by scanning workflows) is reverted; the helper's
venv replaces it.

## 4. Work and tests

One change, one commit:
- `hive/release.sh` (mise, hash) + `hive/bin/task-tools`;
- `project_agent.validate` and the drafter's proposal format accept `tools`;
- `work_checks` prepends the line; revert `e48a5689c`.

Tests: `validate` refuses a bad tool string; `work_checks` puts the line first and only
when there are tools; `task-tools` with a fake `mise` writes `$GITHUB_PATH` /
`$GITHUB_ENV`, makes the venv, ignores a `MISE_*` variable and a planted `mise.toml`.

Acceptance: add `tools` to SwarmAI and gbrain, update the demo Hive, dispatch one task
each that runs setup and all checks. Expected: python, node and bun checks run;
SwarmAI's `tauri-check` still fails on missing system libraries.
