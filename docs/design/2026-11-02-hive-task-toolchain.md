---
title: "Hive tasks: the toolchain a repository's checks need"
date: 2026-11-02
created: 2026-11-02
updated: 2026-11-02
project: SwarmAI
status: r1, waiting for one zero-context review and XG
---

# Hive tasks: the toolchain a repository's checks need

Scope: a task dispatched to a Hive runs its Project Agent's setup lines and checks there
(`scripts/task_checks`). On the real Brains most of them cannot run, because the Hive
does not have the tools the repository's CI gives its jobs. This design replaces the
one-tool fix `e48a5689c` (a venv for `actions/setup-python` only) with one mechanism for
every tool, and adds a check, run before any task, that says which of a Brain's checks
can run on a given Hive and why the others cannot.

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

Four parts. Parts 3.1 to 3.3 make the common tools available; 3.4 makes what still
cannot run visible before dispatch.

### 3.1 One tool manager on every Hive

The Hive package ships [mise](https://mise.jdx.dev) (one static binary; core backends
for python, node, bun, java, rust, go, ruby, deno; uv and pnpm through its registry;
checked on this Mac with `mise registry`). It goes into the package, not into the boot
script:

- `hive/release.sh` downloads one pinned mise release for `linux-arm64`, checks its
  sha256 against a value in the repository, and puts it at `hive/bin/mise`. A wrong hash
  fails the build.
- So a new Hive (user data), an updated Hive (the update's rsync) and a pre-baked AMI
  all get the same mise with no new step in any of them.

Tools are installed on first use, by `task_checks` (3.3), into the `swarm` user's mise
data directory, and shared by later tasks: a second task asking for `python 3.11`
installs nothing. Installs are serialized with one lock file, so two tasks starting
together do not install the same tool at once.

mise never reads a repository's own config: `task_checks` runs it with its working
directory in the task's `checks/` directory (a sibling of the repository clones, not a
parent) and with mise's config search stopped at that directory. A repository's
`mise.toml` can define environment hooks and tasks; those would run commands the order
did not name, which `task_checks` must never do (its module doc: "Only the commands of
the order run"). Version files are read by our own parser instead (3.2).

### 3.2 Which tools a task needs: resolved on the Host, sent in the order

A new module `core/task_tools.py` (Host side, no model) computes the tools of one mode:
a list of `{tool, version, source}`. `version` is what mise accepts (`3.11`, `22`,
`1.4.4`, `stable`, `latest`). `source` says where the value came from, so a person or an
agent can see and correct it.

For each tool, the first source that names a version wins:

1. **The mode's own `tools:`** in `project-agent.yaml` (optional; written by a person or
   an agent; `validate` checks its shape). This is how a wrong guess is corrected
   without changing code.
2. **The CI job a row came from.** For a setup or check row whose `source` is a workflow
   file, the jobs whose `run:` steps hold that command (the parser already names each
   item `<job>: <step>`). Their setup actions give the tool and version, through a
   table of actions (data, §3.2.1). When the row's command appears in several jobs (the
   gbrain setup line is in 13), the first job in file order decides; the others are
   not read for versions.
3. **The repository's version files**, nearest to the row's `cwd` first:
   `.tool-versions`, the `[tools]` table of `mise.toml` / `.mise.toml`, `.nvmrc`,
   `.node-version`, `.python-version`, `rust-toolchain.toml` / `rust-toolchain`,
   `.bun-version`, `.java-version`, `.ruby-version`, the `go` / `toolchain` line of
   `go.mod`, `package.json` `packageManager` (pnpm, yarn, bun) and the lower bound of
   `engines` (`>=20.19.0` gives `20.19.0`).
4. **The setup jobs of the same mode.** A check from `validations.json` or
   `package.json` has no job; it runs in the environment the mode's setup built, so it
   gets the tools found for the mode's setup rows.
5. **The command itself.** When a row's first word belongs to a tool no source above
   named (`cargo` → rust, `bun` → bun, `go` → go, `java` / `mvn` / `gradle` → java),
   that tool is added with `latest` (`stable` for rust), unless the Hive already has
   the command (python3.12, node, npm, git: their system versions stay in use).

A value that is a GitHub expression (`${{ matrix.bun }}`) is not a version; that
source is skipped for that tool and the next one is read. `python-version-file:`,
`node-version-file:` and the like are read from the named file.

#### 3.2.1 The table is data

`core/task_tools.py` holds three plain tables: setup actions → tool and the inputs that
carry its version; version files → tool; first words → tool. Adding a tool means adding
rows, not code. The first table covers `actions/setup-python`, `actions/setup-node`,
`oven-sh/setup-bun`, `actions/setup-java` (with `distribution`), `actions/setup-go`,
`dtolnay/rust-toolchain` (its `@ref` or `toolchain:` input),
`actions-rust-lang/setup-rust-toolchain`, `ruby/setup-ruby`, `pnpm/action-setup`,
`astral-sh/setup-uv`, `denoland/setup-deno`. An action not in the table is not guessed:
it is listed in the result of 3.4 as "a CI step the Hive does not repeat".

### 3.3 The order and the Hive side

- **The order.** `work_checks` (`core/swarm_out.py:842`) adds `tools` to the order's
  `checks`. An older Hive refuses an order with an unknown `checks` key
  (`core/hive_workspace._parse_checks`: `set(checks) - {"setup", "validations"}`), so a
  new admission flag, `task_tools`, is added next to `task_checks`
  (`routers/chat.py:977`), and the Host sends `tools` only to a Hive that says it. An
  older Hive gets the order it gets today.
- **`_parse_checks`** accepts `tools` as a list of at most 20 `{tool, version}` rows;
  `tool` must be a name in the table, `version` must match a plain version pattern
  (`[A-Za-z0-9][A-Za-z0-9._+-]{0,40}`). Anything else refuses the order, as today.
- **`task_checks setup`** first installs each tool (`mise install <tool>@<version>`),
  then puts each tool's `bin` directory on `PATH` for every setup line and check, the
  same way it puts `$GITHUB_PATH` entries today. It writes one line per tool to
  `setup.jsonl` (`tool`, `asked`, the installed version, `exit`). A tool that does not
  install stops setup with its own exit and log, like a failing setup line.
- **Python and Node stay per task.** `actions/setup-python` gives a job a `python` and a
  writable `pip`; a job's VM is its own. On a shared Hive the mise python is shared, so
  `task_checks` makes a venv for the task from it (`checks/python`, removed with the
  task) and puts that first on `PATH`. This is the venv `e48a5689c` made, now made from
  the resolved python instead of a scan of the workflows. For Node, `NPM_CONFIG_PREFIX`
  points into the task's `checks/` directory, so a `-g` install stays in the task.

### 3.4 Before dispatch: can this Brain's checks run on this Hive?

A new command, `swarm_out runnable <brain> [--mode M] [--hive H]` (an ops row
`brain.runnable`, callers person and owner agent), runs the mode's setup and every
check on a Hive with no agent session, and reports, per item:

- each tool: installed version, or the install's error;
- each setup line: ok, or exit and the last lines of its log;
- each check: passed, failed (exit N), or "command not found: X" (exit 127 and the name
  the shell printed);
- for a row from a CI job: what that job had and the Hive does not repeat —
  `services:` names, `container:`, the names of `env:` values that are expressions
  (`${{ secrets.* }}`, `${{ github.* }}`), setup actions not in the table, and
  `apt-get install` lines in the job (system packages for another Linux).

These facts are read from the workflow by code and shown as reasons next to a failure;
whether a check passed is decided by its exit code only.

**How it runs.** The Host prepares a normal work order on the Hive (`POST /work`, a new
`so_…` id, the mode's repositories at the Host's checked-out commits, the checks and
tools), starts it with a new Hive-only route `POST /work/{id}/checks/run` (runs
`task_checks setup` and then `task_checks run` in a background process; refused when a
session took the order or a run of it is already going), reads the record with the
existing `GET /work/{id}/checks` (extended to return the setup lines too), and releases
the order when every item has ended. A check that never ends is the one hang this path
must bound, because no agent caps it here: each check gets a hang guard of 60 minutes,
after which its process group is ended and it is recorded as cut off.

**Where the answer goes.** The result is kept in the Brain at
`.artifacts/project-agent/runnable-<hive>.json`, with the definition's revision and the
Hive's version. `swarm_out dispatch` reads it and adds one line to its reply: which
checks did not run on that Hive last time and why, or that there is no result yet, or
that it is stale (the definition or the Hive changed since). It never refuses a
dispatch. The dispatching agent decides what to do with a check that cannot run there
(dispatch anyway and judge that check by hand, pick another Hive, or fix the
definition), and tells the person only when it cannot decide.

## 4. What changes in `e48a5689c`

`_wanted_pythons` and `_python_on_path` are deleted: the Hive no longer scans workflow
files; the Host resolves tools (3.2). `_task_python` stays, made from the resolved
python (3.3). Its three tests are rewritten for the new input; the docstring paragraph
about setup-python is replaced by the tools paragraph.

## 5. Not in scope (said in the result of 3.4 instead)

- **Running `uses:` actions or whole CI jobs** (`act`). It needs Docker and x86 runner
  images on an arm64 machine; rejected as alternative C below.
- **`services:` and `container:`** (a postgres the tests reach, a job in a container).
  The Hive runs no containers. Reported per check.
- **Secrets and GitHub context values** (`${{ secrets.* }}`, `${{ github.* }}`). They
  exist only inside Actions. Reported by name, never copied.
- **System packages** (`apt-get install …`). The Hive is Amazon Linux; package names
  differ and installing them changes the shared machine. Reported. A Brain that needs
  them can install them in its own setup only if they are installable without root,
  which is the repository's choice.
- **Copying literal `env:` values.** I proposed this in chat; the survey shows none of
  the rows the three Brains use reads a literal value (SwarmAI's step env are all
  expressions, gbrain's setup step has none). Not built until 3.4 shows a check that
  fails for one.
- **Matrix jobs**: the first job in file order decides (3.2), the matrix is not
  expanded.

## 6. Alternatives

| | What | Why not |
|---|---|---|
| A. One emulation per action | `setup-python`, then `setup-node`, then `setup-bun` … each written in `task_checks` | Every new repository needs new code; that is how `e48a5689c` started |
| B. One tool manager, tools resolved from what the repo declares, a runnable check (this design) | §3 | The table can miss an action; the runnable check names it before a task depends on it |
| C. Run the CI job itself with `act` | Docker on the Hive, GitHub's runner images | The images are x86 (the Hive is arm64: emulation is slow); several GB per image; `services:` and secrets still do not work |

## 7. Order of work

Each step lands on its own with its tests; the Hives need an update after steps 1 and 3.

1. **mise in the package**: `hive/release.sh` (pinned version, sha256), a package test
   that the binary is there and its hash matches. No behaviour change yet.
2. **`core/task_tools.py`** with the three tables and `resolve(mode, repo_roots)`;
   `project_agent.validate` accepts a mode's `tools:`. Tests on fixture repositories
   built in the test (each source and its precedence, an expression skipped, the first
   job deciding, a command-only tool, the Hive's own command kept).
3. **The order and `task_checks`**: admission flag `task_tools`, `_parse_checks`,
   `work_checks`, the install step with its lock and record line, the per-task venv
   from the resolved python, `NPM_CONFIG_PREFIX`; delete the scan code of `e48a5689c`.
   Tests: an old Hive gets no `tools`; an unknown tool or a bad version refuses the
   order; setup installs (a fake `mise` on `PATH` in the test) and stops on a failed
   install; the PATH order; the config search never reaches a parent `mise.toml` (a
   planted one in a parent directory must not be read).
4. **The runnable check**: the Hive route, the setup lines in `GET checks`, the hang
   guard, the Host command and ops row, the stored result, the dispatch line. Tests: the
   route refuses a taken order and a second start; a check over the guard is ended and
   recorded; the CI facts per row; the stale rules; dispatch never refused by it.

## 8. Verification plan

**The final test set** (run once at the end): `test_task_tools.py` (new),
`test_task_checks.py`, `test_hive_workspace.py` (`_parse_checks` part),
`test_project_agent.py` (`validate` of `tools:`), the swarm_out dispatch tests that
cover `work_checks`, the package test of step 1.

**Invariants that must hold at the end**

- An order to a Hive without `task_tools` is byte-identical to today's.
- `task_checks` runs only the order's commands and `mise install` of the order's tools;
  it reads no repository config.
- Whether a check passed comes from its exit code only; the CI facts are reasons, never
  a status.
- The runnable check and the dispatch line never block a dispatch.

**Acceptance on a real Hive** (closes the toolchain ToDo): `swarm_out runnable SwarmAI`
and `swarm_out runnable gbrain` on the demo Hive, after it is updated. Expected, to be
compared with what comes back: SwarmAI's python 3.11 and node 22 install and its backend
and frontend checks run; `tauri-check` gets rust but is expected to fail on the missing
system libraries, and the report says `apt-get install` lines of `release.yml`. gbrain's
bun 1.4.4 installs and `typecheck` runs; `test` may need postgres, and if it does the
report names the `services` of the jobs that run it.

**Review**: one zero-context review of this design before code. After the code, a
correctness and security review of steps 3 and 4 (they change what a Hive executes);
none for steps 1 and 2 beyond their tests.

## 9. Open questions

1. Does a runnable check count against the Hive's run limit, like a task? It runs real
   checks next to real runs (the OOM of 2026-11-02 came from two runs sharing memory).
   I lean yes, but have not read how admission counts work orders yet; to be read
   before step 4.
2. Should a mode's resolved `tools` be written back into `project-agent.yaml` when the
   Project Agent is drafted, so a person sees them in the file, or only shown by
   `runnable`? This design shows them only (one source of truth: the repository).
