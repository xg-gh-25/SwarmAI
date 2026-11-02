---
title: "Hive tasks: the toolchain a repository's checks need"
date: 2026-11-02
created: 2026-11-02
updated: 2026-11-02
project: SwarmAI
status: r2, review findings folded in; waiting for XG
---

# Hive tasks: the toolchain a repository's checks need

Scope: a task dispatched to a Hive runs its Project Agent's setup lines and checks there
(`scripts/task_checks`). On the real Brains most of them cannot run, because the Hive
does not have the tools the repository's CI gives its jobs. This design replaces the
one-tool fix `e48a5689c` (a venv for `actions/setup-python` only) with one mechanism for
every tool. It also adds a check-only task that says which of a Brain's checks can run
on a given Hive, and why the others cannot.

r2 changes (one zero-context review of r1, correctness and security; each finding
re-read in the code):
- The runnable check is now an ordinary Swarm Out task, not a new Hive route, so it is
  counted, released and guarded like any run (3.4).
- Tools install from both `setup` and `run`, so a mode with no setup lines gets them
  too (3.3).
- Rows are matched to CI jobs by the drafter's own `_declared` rule, and the per-mode
  merge is defined (3.2).
- mise runs with an allowlisted environment and reads no config of any kind (3.1).
- The shared store is read-only, and the per-task homes are listed (3.1, 3.3).
- Admission lists the tools a Hive takes (3.3).
- Repository files are read only through `safe_read_text` (3.2).
- The result carries checked fields only, never log tails (3.4).

## 1. What is wrong (observed 2026-11-02)

**The Hive.** A Hive is an arm64 EC2 machine (`hive/fleet.py` `DEFAULT_TYPE = "m7g.xlarge"`,
50 GB root volume, `hive/provisioner.py:664`). First boot installs
`python3.12 python3.12-pip python3.12-devel nodejs20 npm git gcc make`
(`backend/hive/user_data.py:26`, the same list in `hive/setup.sh:44`) and nothing else.
So there is no `python` or `pip` command (only `python3.12`, `pip3.12`), no uv, bun,
Java, Rust, Go or Docker. An update (`provisioner.update`, the script at
`provisioner.py:1330`) rsyncs the package and runs `ensure-node.sh`; it installs no
system package.

**What the Brains ask for.** Read from the three Brains that have a Project Agent and
their bound repositories (read-only survey, 2026-11-02):

| Brain | Setup and checks (project-agent.yaml) | What their CI gives its jobs that a Hive does not have |
|---|---|---|
| SwarmAI | setup: `pip install uv … uv sync` (from `ci.yml`), `cp .env.example .env`, `npm ci`; 13 checks starting with `python`, `npx`, `npm`, `cargo` | `actions/setup-python` 3.11, `actions/setup-node` 22, `dtolnay/rust-toolchain`; Linux system libraries for Tauri (`release.yml:99`, `apt-get install libwebkit2gtk-4.1-dev …`); step `env:` whose values are all `${{ github.event… }}` |
| gbrain | setup: `bun scripts/ci-bun-install.ts --frozen-lockfile` (from `e2e.yml`); checks `bun run typecheck / test / verify` (from `package.json`) | `oven-sh/setup-bun` 1.4.4 in every job; the setup step appears in 13 jobs of `e2e.yml`, 9 of them with `services: postgres` |
| AIDLC | 1 check (a shell `find … awk` lint) | nothing the check needs; other jobs use `setup-python 3.x`, AWS credentials and `services` |

When G2 ran SwarmAI's checks on a real Hive (E2E round 3), the setup's first line
failed (`pip: command not found`) and every check after it errored.

## 2. Why (the root, not the symptom)

1. **Only one layer of the CI job is copied.** A Project Agent's setup and checks are
   CI `run:` lines copied verbatim (`core/project_agent_draft.py` module doc; the parser
   `core/brain_bootstrap/commands.py::_github_actions` reads `run:` only and never
   `uses:`, `env:`, `services:` or `container:`). The `run:` lines assume the
   environment the job's `uses:` steps built. On a Hive that environment is not there.
2. **Nothing runs the definition before a real task does.** The first time a Brain's
   setup and checks are executed on a Hive is inside a dispatched task. Before G2 the
   check statuses were inferred from the agent's commands, so the gap stayed hidden;
   with G2 it shows as every check erroring.

Adding tools one at a time (what `e48a5689c` started) fixes (1) for one tool and not
(2). The next repository needs the next tool.

## 3. Design

Four parts. 3.1 to 3.3 make the common tools available to every task; 3.4 says, before
a real task depends on it, what still cannot run on a given Hive.

### 3.1 One tool manager on every Hive

The Hive package ships [mise](https://mise.jdx.dev): one static binary with core
backends for python, node, bun, java, rust, go, ruby and deno, plus uv and pnpm through
its aqua registry (checked on this Mac with `mise registry`). It goes into the package,
not into the boot script:

- `hive/release.sh` downloads one pinned mise release for `linux-arm64`, checks its
  sha256 against a value kept in the repository, and writes it into the staging tree at
  `hive/bin/mise`, after the rsync of the shipped `hive/` files (`release.sh:138-144`).
  So it never needs to be a tracked file. A wrong hash fails the build.
- A new Hive (user data), an updated Hive (the update's `rsync --delete`,
  `provisioner.py:1330`) and a pre-baked AMI all get the same mise, with no new step.

**How `task_checks` calls mise.** Every call goes through one helper, which never
passes the caller's environment on. It builds the environment from an allowlist, the
same way `hive_workspace._ENV_KEEP` does for git (`hive_workspace.py:106-126`):

- `PATH`, `HOME`, `LANG` and the proxy variables are kept.
- `MISE_DATA_DIR` is the store (below).
- `MISE_CONFIG_DIR`, `MISE_GLOBAL_CONFIG_FILE` and `MISE_SYSTEM_CONFIG_FILE` point to an
  empty, package-owned directory and file.
- `MISE_OVERRIDE_CONFIG_FILENAMES` and `MISE_OVERRIDE_TOOL_VERSIONS_FILENAMES` are set to
  names that never exist.
- `MISE_DISABLE_BACKENDS=asdf,vfox`, so no plugin script runs.
- `MISE_PARANOID=1`.
- Every other `MISE_*` variable is dropped.
- The working directory is the task's `checks/` directory.

The tool table (3.2.1) names fully qualified backends (`core:python`,
`aqua:astral-sh/uv`). So mise reads no project file, no global file and no repository
value except the `tool@version` arguments themselves. Those come from the order, after
the pattern check of 3.3.

**The store.** Tools are installed once into `MISE_DATA_DIR` (under the `swarm` user's
home) and shared by later tasks. Installs run under one `flock`. The lock is released
when the process ends, so an agent's call cap that ends `task_checks` (its signal
handlers end the child's group, `task_checks.py:272-276`) also frees the lock.

An install counts only when `task_checks` has written a marker after it. Once a tool is
installed, `task_checks` makes its install directory read-only (`chmod -R a-w`) and
then writes the marker. A directory with no marker (an install cut off) is removed and
installed again. Read-only stops a task from writing into a shared interpreter by
accident, or by running a careless script (a `sitecustomize.py`, corepack shims next to
`node`). It does not stop the same OS user from making the directory writable again.
That is the boundary the Hive already states (`hive_workspace.py` module doc: no file
on a Hive is out of the agent's reach), and this design does not claim more.

### 3.2 Which tools a task needs: resolved on the Host, sent in the order

A new module `core/task_tools.py` (Host side, no model) computes the tools of one mode:
a list of `{tool, version, source}`. `version` is what mise accepts (`3.11`, `22`,
`1.4.4`, `stable`, `latest`). `source` says where the value came from, so a person or an
agent can see it and correct it.

**Per row.** For each setup line and each check, each tool takes the first of these
sources that names a version:

1. **The mode's own `tools:`** in `project-agent.yaml` (optional, written by a person
   or an agent; `validate` checks its shape). This is how a wrong guess is corrected
   without a code change.
2. **The CI job the row came from.** The row's `source` workflow is parsed again with
   `parse_commands`, and the row is matched to a `run:` step by exactly the rule that
   admitted it, `project_agent_draft._declared`: same `cwd`, the block starts with the
   row's lines, and the prefix is complete. That function is shared with the drafter,
   not copied. Of the jobs that match (SwarmAI's 5-line setup prefix also starts the
   `frontend` and `security` blocks of `ci.yml`), the first in file order that has a
   setup action for the tool gives its version, read through the action table (3.2.1).
3. **The repository's version files**, nearest to the row's `cwd` first:
   - `.tool-versions` and the `[tools]` table of `mise.toml` / `.mise.toml`;
   - `.nvmrc`, `.node-version`, `.python-version`, `rust-toolchain.toml` /
     `rust-toolchain`, `.bun-version`, `.java-version`, `.ruby-version`;
   - the `go` / `toolchain` line of `go.mod`;
   - the `packageManager` field of `package.json` (pnpm, yarn, bun) and the lower bound
     of its `engines` (`>=20.19.0` gives `20.19.0`).
4. **The mode's setup rows.** A check from `validations.json` or `package.json` has no
   job. It runs in the environment the mode's setup built, so it takes the tools found
   for the setup rows.
5. **The command itself.** The row's first word may belong to a tool that no source
   above named: `python` / `pip` / `pytest` → python, `cargo` → rust, `bun` → bun,
   `go` → go, `java` / `mvn` / `gradle` → java, `uv` → uv. That tool is added with
   `latest` (`stable` for rust). The Hive's own commands are `python3.12`, `node`,
   `npm`, `npx` and `git`, which stay in use with no tool. `python` and `pip` are not
   Hive commands (§1).

**Per mode (the merge).** The order carries one version per tool. Rows are read in a
fixed order, setup lines first and then checks, and the first row's version for a tool
wins. A later row that resolved another version is listed as a conflict, with both
sources, in the result of 3.4.

**Values that are skipped.** A value is skipped, and the next source is read, when it
is any of these:

- a GitHub expression (`${{ matrix.bun }}`);
- a value that does not match the version pattern of 3.3, applied on the Host as well;
- a version file that cannot be read safely.

**Reading repository files.** Every file is read through
`brain_bootstrap/repo_read.safe_read_text` (confined to the repository, regular file
only, size cap, link checks). This includes the files named by `python-version-file:`,
`node-version-file:` and the like. A path that leaves the repository, or a symlinked
`.nvmrc`, is skipped like an unreadable file.

#### 3.2.1 The table is data

`core/task_tools.py` holds three plain tables:

- setup actions → tool, its mise backend, and the inputs that carry its version;
- version files → tool;
- first words → tool.

Adding a tool means adding rows, not code. The first table covers:
- `actions/setup-python`, `actions/setup-node`, `oven-sh/setup-bun`;
- `actions/setup-java` (with `distribution`), `actions/setup-go`;
- `dtolnay/rust-toolchain` (its `@ref` or `toolchain:` input),
  `actions-rust-lang/setup-rust-toolchain`;
- `ruby/setup-ruby`, `pnpm/action-setup`, `astral-sh/setup-uv`, `denoland/setup-deno`.

An action not in the table is not guessed. It is listed in the result of 3.4 as "a CI
step the Hive does not repeat".

### 3.3 The order and the Hive side

- **Admission names the tools.** A Hive reports `task_tools`: the list of tool names its
  own table takes (`routers/chat.py`, next to `task_checks` at `:977`). The Host sends
  only tools on that list. A tool missing from it is left out of the order and shown as
  "this Hive's version has no X" in the result of 3.4. An older Hive (no list) gets the
  order it gets today. Its `_parse_checks` refuses any unknown `checks` key
  (`hive_workspace.py:442`), and this keeps that from ever triggering.
- **`_parse_checks`** accepts `tools`: at most 20 `{tool, version}` rows. `tool` must be
  on the Hive's own list. `version` must fullmatch `[A-Za-z0-9][A-Za-z0-9._+-]{0,40}`.
  The pattern lives in one module that the Host and the Hive import. Because the Host
  sends only rows that pass (3.2), a row the Hive refuses means a bug, not repository
  content. It refuses the order, as today.
- **Installs do not depend on the agent running setup.** `task_checks setup` and
  `task_checks run` both start by making sure the order's tools are installed and their
  environment is written to `env.json`. This step is idempotent and cheap once done.
  Today a mode with checks and no setup lines (SwarmAI's `tauri-check` mode) never runs
  setup (`task_spec.py:390`: the setup instruction is written only when there are
  setup lines). The task text says the first call may take several minutes, while
  tools install.
- **The tool environment comes from mise, never from shims.** `mise env --json
  <tool>@<version> …` (through the helper of 3.1) gives the `PATH` entries and the
  variables a tool needs: `JAVA_HOME`, the rustup variables, and the like. Those go into
  `env.json`, ahead of what setup lines add through `$GITHUB_PATH` / `$GITHUB_ENV`.
  mise's shims directory is never put on `PATH`, and `mise activate` is never used.
  Shims look up config from the current directory, which is the repository.
- **Per task, written down.** These live under the task's `checks/` directory and are
  removed with the task:
  - the python venv (from the resolved python, else the newest `python3.N` on the Hive,
    as `e48a5689c` does today);
  - `NPM_CONFIG_PREFIX` (its `bin` on `PATH`);
  - `CARGO_HOME`, `GOPATH`, `BUN_INSTALL` and `COREPACK_HOME`.

  Rust toolchains (`RUSTUP_HOME`) are shared and read-only, like the rest of the store.
  The pip and uv download caches are shared: their entries are named by content hash,
  and losing them only costs time.
- **The record.** `setup.jsonl` gets one line per tool: tool, asked version, installed
  version, exit. A tool that does not install stops the command with its own exit and
  log, like a failing setup line.

### 3.4 Before dispatch: can this Brain's checks run on this Hive?

`swarm_out runnable <brain> [--mode M] [--hive H]` dispatches **a normal Swarm Out
task** whose text is fixed: run setup, run every check through `task_checks`, change
nothing, and return an answer (`returns: answer`, repositories read-only). Nothing new
runs on the Hive. Every part of an ordinary run applies to it:

- it is an open run, so `pick_hive` counts it (`swarm_out.py:699`), and so does the
  update guard ("no open runs");
- `release` waits for its session;
- the agent caps each call;
- the Host reads the check record (`GET /work/{id}/checks`) as for any task.

The ops row is `task.dispatch`'s, with the same callers and weight. An earlier draft
added a Hive route that ran the checks with no agent. Its review showed that every
part of a run's lifecycle would have to be built again for it (counting, release,
update guard, a hang guard per step, an owner across a restart), so it is dropped.

**What the result says.** It is built by code from the record and the repository, never
from the agent's reply:

- each tool: installed version, or the install's exit;
- each setup line and each check: passed, failed (exit N), or "command not found: X"
  (exit 127, with X taken from the log and accepted only if it matches
  `^[A-Za-z0-9._+-]{1,64}$`);
- for a row from a CI job, what that job had that the Hive does not repeat:
  - `services:` names and `container:`;
  - the names of `env:` values that are expressions (`${{ secrets.* }}`,
    `${{ github.* }}`);
  - setup actions not in the table;
  - packages of `apt-get install` lines;
  - every name checked against the same pattern;
- tool conflicts (3.2) and tools the Hive's version lacks (3.3).

Whether a check passed is decided by its exit code only; the rest are reasons shown
next to a failure. Log tails stay on the Hive with the task's logs, so a secret a check
prints is never copied to the Host.

**Where the answer goes.** The run is in the Swarm Out history like any other. Its row
also keeps a small `runnable` field: the per-item statuses above, the mode, the
definition's revision, the resolved tools, the repository commits and the Hive's
version. `swarm_out dispatch` finds the latest finished runnable run for the Brain,
mode and Hive. It adds one line to its reply, as data in a fenced block, built only from
those checked fields. The line says one of three things:

- which checks did not run there and why;
- that there is none yet;
- that it is stale: the revision, the tools, a repository commit or the Hive's version
  differ from the dispatch's.

It never refuses a dispatch. The dispatching agent decides what to do with a check that
cannot run there, and tells the person only when it cannot decide. It may dispatch
anyway and judge that check by hand, pick another Hive, or fix the definition.

## 4. What changes in `e48a5689c`

- `_wanted_pythons` is deleted. The Hive no longer scans workflow files: the Host
  resolves the tools (3.2).
- `_python_on_path` stays, as the fallback when the order has no python tool: an older
  Host, or a mode where nothing names python.
- `_task_python` takes the resolved python first.
- Its three tests are rewritten for that input.
- The docstring paragraph about setup-python becomes the tools paragraph.

## 5. Not in scope (said in the result of 3.4 instead)

- **Running `uses:` actions or whole CI jobs** (`act`). It needs Docker and x86 runner
  images on an arm64 machine (alternative C below).
- **`services:` and `container:`** (a postgres the tests reach, a job in a container).
  The Hive runs no containers.
- **Secrets and GitHub context values.** They exist only inside Actions. They are
  reported by name and never copied.
- **System packages** (`apt-get install …`). The Hive is Amazon Linux, so the package
  names differ, and installing them would change the shared machine.
- **Copying literal `env:` values.** I proposed this in chat, but none of the rows the
  three Brains use reads one: SwarmAI's step env values are all expressions, and
  gbrain's setup step has none. It is not built until 3.4 shows a check failing for one.
- **Matrix jobs**: the matrix is not expanded. Its expression values are skipped (3.2).
- **A lock file of resolved tool checksums.** `MISE_PARANOID` and the recorded installed
  version are the bar for now.

## 6. Alternatives

| | What | Why not |
|---|---|---|
| A. One emulation per action | `setup-python`, then `setup-node`, then `setup-bun` …, each written into `task_checks` | Every new repository needs new code; `e48a5689c` started down this path |
| B. One tool manager, tools resolved from what the repo declares, a runnable task (this design) | §3 | The table can miss an action; the runnable task names the miss before a real task depends on it |
| C. Run the CI job itself with `act` | Docker on the Hive, GitHub's runner images | The images are x86 and the Hive is arm64, so they run emulated and slowly; several GB per image; `services:` and secrets still do not work |

## 7. Order of work

Each step lands on its own with its tests. The Hives need an update after steps 1 and 3.

1. **mise in the package.** `hive/release.sh` (pinned version, sha256, written into
   staging); `verify_package.sh` checks that the binary is there and that its hash
   matches. No behaviour change yet.
2. **`core/task_tools.py`**: the three tables, the shared version pattern,
   `resolve(mode, repo_roots)`. `_declared` is shared with the drafter, and `validate`
   accepts a mode's `tools:`. Tests on fixture repositories built in the test:
   - each source and its precedence;
   - an expression or bad value skipped;
   - the matching job that has the action deciding;
   - the merge and its conflicts;
   - a command-only tool, with `python` / `pip` counted as missing;
   - a `../` or absolute `*-version-file` path, and a symlinked `.nvmrc`, skipped.
3. **The order and `task_checks`**: the admission list `task_tools`, `_parse_checks`,
   `work_checks`, the mise helper and its environment, the store (lock, marker,
   read-only), `mise env` into `env.json` from both `setup` and `run`, the per-task
   homes, the venv fallback. The scan code of `e48a5689c` is deleted. Tests:
   - an older Hive gets no `tools`, and a tool not on the Hive's list is left out;
   - a bad row refuses the order;
   - `run` with no `setup` first still installs, using a fake `mise` on `PATH` in the
     test;
   - a failed install stops the command;
   - a directory with no marker is installed again;
   - the shims directory is never on `PATH`;
   - a `mise.toml` in a parent directory, a global config with a postinstall hook, and a
     `MISE_*` variable in the caller's environment all have no effect;
   - the per-task homes are under `checks/`.
4. **The runnable task**: the fixed task text, the `runnable` field of the run row, the
   result built from the record, the dispatch line and its stale rules. Tests:
   - statuses come from exit codes only;
   - a "command not found" name that fails the pattern is dropped;
   - every stale input is detected;
   - the dispatch line is never a refusal.

## 8. Verification plan

**The final test set**, run once at the end:

- `test_task_tools.py` (new);
- `test_task_checks.py`;
- the `_parse_checks` part of `test_hive_workspace.py`;
- `test_project_agent.py` (the `validate` of `tools:`) and the drafter tests of
  `_declared`;
- the swarm_out dispatch tests that cover `work_checks` and the dispatch reply;
- the package check of step 1.

**Invariants that must hold at the end:**

- An order to a Hive without `task_tools` is the same as today's.
- `task_checks` runs only the order's commands and `mise install` / `mise env` of the
  order's tools. mise reads no project, global or environment config.
- A task's checks get their tools whether or not the agent ran `setup`.
- Whether a check passed comes from its exit code only. The CI facts are reasons, never
  a status.
- A runnable task is an ordinary run: counted, released and guarded like one. Its
  result never blocks a dispatch.

**Acceptance on a real Hive** (closes the toolchain ToDo). Run `swarm_out runnable
SwarmAI` and `swarm_out runnable gbrain` on the demo Hive after it is updated, then
compare what comes back with this:

- SwarmAI: python 3.11 and node 22 install, and the backend and frontend checks run.
  `tauri-check` gets rust but is expected to fail on the missing system libraries; the
  result names the `apt-get install` packages of `release.yml`.
- gbrain: bun 1.4.4 installs and `typecheck` runs. `test` may need postgres; if it
  does, the result names the `services` of the jobs that run it.

**Review**:
- r1 got one zero-context review (correctness and security). r2 folds it in.
- After the code: a correctness and security review of step 3 (it changes what a Hive
  executes and the shared store), and a correctness review of step 4.
- Steps 1 and 2: their tests only.

## 9. Decided in r2 (were open in r1)

1. A runnable check counts against the Hive's run limit. It is an ordinary run (3.4),
   so this needs nothing new. Under r1's own route nothing would have counted it: the
   Hive's admission reads memory pressure and streaming sessions only, and `pick_hive`
   counts open dispatch rows.
2. The resolved tools are not written back into `project-agent.yaml`. They are shown in
   the runnable result. A person or an agent writes a mode's `tools:` only to correct a
   guess.
