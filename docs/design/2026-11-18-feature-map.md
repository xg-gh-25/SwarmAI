---
title: Feature Map — a Brain knows what its product does, and can prove each feature still works
date: 2026-11-18
created: 2026-11-18
updated: 2026-11-18
project: SwarmAI
status: v2.1 — full product design (replaces the v1 dogfood draft), revised after one zero-context premise review (7 HIGH, 11 MEDIUM, 9 LOW, all checked against the code and folded in); for the owner's decision on §11
related: core/code_intel (graph, routes); core/brain_bootstrap (survey, New Brain extraction); skills s_browser-agent, s_qa, s_project-manager, s_autonomous-pipeline (TEST); core/run_protocol (validation reports, checks); core/attention_authority (Needs You); jobs (system jobs); hive (user_data, provisioner)
---

# Feature Map

## 0. Summary

A Brain that governs a code repo knows how the code is built (the code graph). With
Feature Map it also knows **what the product does for its users**, and can **prove each
feature still works** by replaying it, with no model in the loop, in a sandbox the
product builds. Every feature has one or more **walks**: a start state, the steps a user
takes, and what must (or must not) be visible at the end. Walks live with the code they
describe; the product checks them, replays them, picks the ones a change can reach, and
shows the results in the Brain, in the pipeline's result card and in Needs You, with the
evidence (screenshots, console, requests) for the person to judge.

It works the same for every user, repo and language: web front ends (any framework,
including the web layer of Tauri/Electron apps), HTTP services and command-line tools.
What it cannot cover (native windows, mobile apps, pixel layout, live third-party
services) is said in the product, not discovered.

## 1. Problem and goal

**Today** (from the code, 2026-11-18):

- Nothing in SwarmAI drives a product from the user's side. Pipeline TEST runs "the
  change's tests and the wider checks" (`backend/skills/s_autonomous-pipeline/stages/test.md:3`).
  `s_qa`'s visual step is "start the dev server, open affected routes with
  s_browser-agent, take screenshots" and judge by eye (`backend/skills/s_qa/INSTRUCTIONS.md:85-92`):
  no scripted steps, no assertions, nothing saved.
- `s_browser-agent` drives Chromium through Playwright (Node) for exploration, but has no
  replay, its element numbers change on every read (`INSTRUCTIONS.md:293-297`), and it
  keeps one global set of state files under `/tmp` and CDP port 9222
  (`browser-agent.mjs:98-109,418`). The browser binary is installed by hand
  (`INSTRUCTIONS.md:49-58`); nothing provisions it, on the desktop or on a Hive
  (`backend/hive/user_data.py:36,848`). Node itself is optional on the desktop: Settings
  shows "Not installed" as a normal state (`desktop/src/components/settings/SystemTab.tsx:58-64`).
- The code graph cannot see what a UI uses: no JSX element edges, no string literals such
  as test ids (`core/code_intel/parser.py:130-131`); only file-level imports
  (`graph_store.py:141-153`) and HTTP routes for FastAPI, Express and Next.js
  (`route_parser.py:173,200,228`).

So user-visible regressions are found by the person looking at the app and reproduced by
the agent by hand, again and again. An agent that cannot check a result from the user's
side cannot be left alone with the work (Autonomous Tenets T2/T3: the person judges at the
end, the loop is evaluated). The success criterion this serves: *one requirement ends as
verified work or one precise question* (PRODUCT.md, Success Criteria).

**Goal.** For any Brain with a code repo:

1. The Brain lists the product's main features from the day it is created.
2. Any feature can be replayed on request, with a result a program decided and evidence a
   person can look at.
3. A change (native, pipeline or Hive) is checked against the walks it can reach, and the
   person sees those results where they judge the work.
4. A feature that breaks between changes (someone else's commit, a dependency bump) is
   noticed without anyone looking, once the person turns that on.
5. Walks stay true as the product changes, without hiding real regressions.

## 2. The concepts

| Concept | What it is | Where it lives |
|---|---|---|
| **Target** | How to start one runnable part of the product: kind (`web`, `http`, `cli`), its root in the repo, how to prepare, which services to start, readiness, start states | `targets/<id>.yaml` in the feature folder |
| **Feature** | Something a user does and can see the result of: id, title, target, walks, notes | `<id>.yaml`, one per feature |
| **Walk** | Start state → steps → expectations, replayed with no model | inside its feature file |
| **Suggested feature** | A feature the Brain believes exists (from New Brain extraction): how a user reaches it and what they should see; no walk yet | the Brain: `.artifacts/feature-map/<repo>/suggested.json` (derived, regenerable); dismissals in `dismissed.json` next to it |
| **Replay** | One run of walks in a fresh sandbox, by the CLI | — |
| **Evidence** | Per walk: a screenshot per `expect` and at a failing step, console errors, failed and blocked requests, the result; a trace on failure | app data (§5.11); copied into the run's records when a pipeline run made it |
| **Result** | `pass`; `fail` (first failing step, expected vs seen); `flaky` (failed, then passed on the confirming run); `not_run` (with its reason) | the replay ledger (§5.11) |

The Feature Map belongs to a governed code-repo asset, like the code graph: one map per
repo bound to the Brain (`bindings.yaml`, `core/ddd_bindings.py:96-123`). A Brain with no
repo (a pure knowledge Brain) has no Feature Map and shows no Features tab: there is no
product to replay, and that is complete, not degraded.

## 3. Who uses it, and their journeys

| Who | Journey | What they get |
|---|---|---|
| **New user, new Brain** from a repo URL or a local checkout | New Brain runs as today (`s_project-manager` P0-P6). The P2 extraction also writes suggested features (§5.3). Nothing in the repo runs. | The Brain's Features tab lists the product's main features from day one, with one action: *Write walks for all*. |
| **Existing user**, Brains already bound | After upgrade, the Features tab appears on every Brain with a code repo, with *Suggest features* (the same extraction, dispatched to chat). | The same, one click later. Nothing to migrate. |
| **The person, reporting a bug** ("the Pipeline view does not open") | The agent finds the feature (`cognition find`, features leg) and replays its walk. | A red step with a screenshot (reproduced), or green: the report needs more detail, and the agent asks one precise question. |
| **The agent, after a native change** touching a target | `feature_map affected` → replays the reached walks → reports results, screenshots open in Canvas. | The observable result, before commit. |
| **A pipeline run** | TEST replays the reached walks; the engine reads their results from the ledger (§5.8). A red walk keeps the run in TEST like a red test. | The result card shows *Feature checks* with screenshots: what the person judges at the end. |
| **A Hive run** (Swarm Out) | The same CLI on the Hive, after the Hive meets §5.10. | The run brings the same evidence back. |
| **The person, checking product health** | Brain Hub → Features: what fails, since which commit, with the screenshot; what is flaky or quarantined. *Replay*, *Fix* (dispatch), *Retire*. | One page that answers: can I trust the product now, and what needs me? |
| **The person who wants regressions caught between changes** | Turns on *Replay nightly* (§5.9). | A walk that turns red shows up in Needs You with its screenshot and a *Fix* action. |
| **A teammate or another tool on the repo** | Walk files are plain YAML in `.swarmai/features/`; the CLI replays them. | Shared, versioned with the code. |
| **A repo whose owners do not want these files**, or a Brain with only a managed mirror | The walks live in the Brain (§5.2), recorded on the binding once. | Everything above except sharing through the repo and Hive replay (§5.10). |
| **A non-web repo** (HTTP service, CLI tool) | Targets of kind `http` / `cli`, same files, CLI and surfaces. | The same loop with request/response or command/output steps. |
| **A library with no user-facing surface** | Suggestions come only from its CLI or examples; a walk may point at an existing test command (`run:`). | The tab says what it found; no invented features. |

The person's attention is spent at the two ends only: turning the map on (one dispatch to
write all walks, one switch for nightly) and judging results. Writing, repairing and
replaying walks is the agent's work.

## 4. Lifecycle

```
suggested ──Write walk──▶ written ──verify (green replay)──▶ passing ⇄ failing
    │                       │ (red: not saved; evidence shown)   │  ▲       │
    └──Dismiss (kept)                                            ▼  │       │
                                                              flaky ──3 of last 10──▶ quarantined
any feature ──Retire (deletes its file)──▶ gone (ledger lines kept)
```

| State | Shown as | Actions | In pipeline TEST |
|---|---|---|---|
| Suggested | title, how to reach, expected result | Write walk, Dismiss | — |
| Passing | last pass, at which commit, commits since | Replay, evidence | replayed if reached; red → blocks |
| Failing | first failing step, expected vs seen, screenshot, since which commit | Fix, Replay, Retire | red at the run's base too → `pre_existing` (listed, does not block); otherwise blocks |
| Flaky | failed then passed on the confirming run | Replay, Fix walk | reported `flaky`, counted, does not block |
| Quarantined | flaky 3 of its last 10 replays | Fix walk (released after 5 greens in a row), Retire | **not replayed**; listed as quarantined |
| Not run | why: dependencies missing, needs a service the machine lacks, launch failed, not ready, port taken, browser missing, bad file | Replay, open log | `not_run` with that reason, never blocks |

There is no "never passed" state: a walk is saved only after a green verify (§5.4). A
walk added or changed by a run's own diff is judged on that run's tree like any other.

States are computed from the ledger when asked; nothing stores a derived "verified"
flag (PROJECT.md decision: *Feature Map reports facts*). "Passing" always says at which
commit and what changed since. A walk's history follows its **content hash**: editing a
walk starts its history over, so an edited walk never inherits an old pass or an old
quarantine.

## 5. Design

### 5.1 Files and format

Layout of a feature folder (`.swarmai/features/` in the repo, or the Brain's
`.artifacts/feature-map/<repo>/`, §5.2):

```
schema: 1                 # in index.yaml; an older SwarmAI refuses a newer major with a clear message
targets/web.yaml
targets/api.yaml
pipeline-overlay-k3f9.yaml
fixtures/…
```

**Target** (`targets/<id>.yaml`):

```yaml
id: web
kind: web                         # web | http | cli
root: desktop/                    # the part of the repo this target is built from (monorepos)
paths: [desktop/, shared/ui/]     # optional: what else a change must touch to reach it (§5.6)
needs: [api]                      # targets to start first
prepare:                          # run to completion, in order, in the sandbox copy of the checkout
  - [npm, ci]
  - [npm, run, build]
services:                         # kept running until the replay ends; each in its own process group
  - {id: ui, argv: [npx, vite, preview, --port, "{port.ui}"], ready: "http://127.0.0.1:{port.ui}/"}
open: "http://127.0.0.1:{port.ui}/"
env: {VITE_API_URL: "http://127.0.0.1:{port.api}"}   # anything beyond the sandbox allowlist, named
allow_hosts: []                   # web: hosts the page may reach besides 127.0.0.1 (default: none)
start_states:
  home: {setup: [[curl, -sf, -X, PUT, "http://127.0.0.1:{port.api}/api/system/onboarding-complete"]]}
  with-brain: {fixture: fixtures/with-brain, to: "{sandbox}/data"}
teardown: []                      # optional extra commands; process groups are always killed
locale: en-US
```

Placeholders: `{port.<service>}` (a free port per service, chosen by the tool),
`{sandbox}` (the run's sandbox directory), `{checkout}` (the sandbox copy of the repo).
`prepare` and `services` are argv lists, never shell strings.

**Feature** (`<id>.yaml`). The example uses elements that exist in SwarmAI today:
`data-testid="nav-pipeline"` (`desktop/src/components/layout/ThreeColumnLayout.tsx:377`),
`overlay-host-mode-badge` (`desktop/src/components/layout/OverlayHost.tsx:262`); the
`home` start state marks onboarding complete because a fresh data folder opens on the
onboarding wizard (`desktop/src/App.tsx:306`).

```yaml
id: pipeline-overlay-k3f9         # assigned once at creation (slug + 4 random chars), never derived from a label
title: Open the Pipeline view from the left nav
target: web
walks:
  - id: from-home
    start: home
    steps:
      - click: {testid: nav-pipeline}
      - expect: {visible: {testid: overlay-host-mode-badge}, text: Pipeline}
    notes: [The overlay animates in; wait for the badge, not a fixed time.]
    stubs:                        # optional: responses the browser gets instead of the network
      - {url: "/api/chat/stream*", body: fixtures/answer-hello.sse, content_type: text/event-stream}
```

Step vocabulary:

- **web**: `goto`, `click`, `type`, `press`, `drag` (from, to), `select`, `upload`, `wait`
  (for an element), `expect`: `visible`, `text` (matches the element's rendered text as a
  user sees it, `innerText`, so CSS case transforms count), `absent` (polled for N
  seconds — the only time-based check, and it is a window, not a sleep), `url`, `count`.
  An element is found by `role` + accessible `name`, by `testid`, or by visible `text`.
  The writer picks the handle that is most stable for that element: role + name where the
  label is stable product copy, testid where the label changes; the locale is fixed by
  the target, so names do not move with the language.
- **http**: `request` (method, path, body, headers), `expect` (`status`, `json` path
  equals or exists, `header`).
- **cli**: `run` (argv, stdin, cwd under `{checkout}`), `expect` (`exit`, `stdout`
  contains or matches, `file` exists or contains).
- Any kind: `run:` instead of `steps` points at an existing spec or test command. Such a
  walk has the evidence its command produces (output, exit code), not screenshots.

Files are read with a strict schema (unknown keys refused, the file and key named); a bad
file is `not_run: bad file`, never half-run.

### 5.2 Where it lives — chosen once, recorded on the binding

The first time a feature file is written for a repo, the location is decided and written
on that binding in `bindings.yaml` as `features: repo` or `features: brain` (one new
optional field on `Binding`, `core/ddd_bindings.py:105-113`). From then on every reader
uses the recorded value through one resolver; the rule below only picks the first value.

| The binding's checkout (`core/repo_placement.py:8-15`) | First value | Why |
|---|---|---|
| `local` or `repos_dir:<name>` (a working checkout, including internal ones people commit CRs from) | `repo`: `.swarmai/features/` | A walk describes the product at a commit; a UI change fixes its walk in the same commit, like a test. Teammates, Hive task clones and other tools get it. It sits next to `.swarmai/pipeline.toml` and `.swarmai/review.md`, the files repos already use to describe themselves to SwarmAI. |
| `mirror` (the managed clone under `~/.swarm-ai/bindings/`, replaced on re-bind) | `brain`: `.artifacts/feature-map/<repo>/` | Nothing is committed to a mirror. |
| The person says "keep walks out of this repo" | `brain` | Written on the binding the same way. |

`bind --relocate` (mirror → workspace) keeps the recorded value; moving the files into the
repo is a separate, explicit action (*Move walks into the repo*). Suggested features are
always in the Brain.

### 5.3 Suggested features: the map is not empty on day one

Quality comes from extraction, not review (EVOLUTION K007). New Brain's P2 already has an
agent read the DISTILL material whole and the survey (`s_project-manager/SKILL.md:133`,
`core/brain_bootstrap/survey.py`: docs, `code_shape`, `commands`, `api_contracts`) to
write the first knowledge. The same pass writes `suggested.json`, to a standard written
into the extraction instructions:

- for each feature: title, the user's path in words, the expected visible result, its
  target, and, only when the material names them, the files or routes involved;
- a feature is something a user does and can see the result of, from a start state,
  small enough that one walk shows it works; the product's main paths first (5-20 per
  target), never an inventory of every button;
- a draft target per runnable part, from the survey's commands and package scripts. It
  stays a draft until a walk verifies green against it.

Nothing runs at bind or in New Brain; the repo is only read. Existing Brains get the same
pass through *Suggest features*. A dismissed suggestion is kept in `dismissed.json` and
not suggested again.

### 5.4 Writing a walk

The agent writes walks from the source, where the test ids, labels and routes are (no
recording machinery): it reads the components, may explore the running app with
`s_browser-agent` as it does today, writes the feature file, and runs

`feature_map verify <feature>[/<walk>]` — a replay in a fresh sandbox. A walk is saved
(becomes part of the map) only when verify is green; a red verify returns the evidence,
including for a missing element the elements nearest to it (same role, similar name), and
the agent fixes the walk or reports that the product does not do what the suggestion
says.

*Write walks for all* (Features tab) dispatches one chat task that writes and verifies
walks for every suggested feature of the Brain; the person consents once. Stubs are
fixtures the agent writes (an SSE body, a JSON reply); they are never captured from a
live call, since the sandbox has no credentials to make one. In a repo home the new files
are part of the agent's change and are committed with it under the repo's convention.

### 5.5 Replay: runtime, sandbox, determinism

**Runtime.** `"$SWARM_BACKEND_BIN" py -m scripts.feature_map <command>` (the product's CLI
convention: JSON on stdout, exit codes; `backend/desktop_main.py:554`). The browser side is
a small Node runner shipped with the backend. On first use the CLI installs, into the app
data folder, a **pinned Node runtime, a pinned Playwright and its Chromium**, each checked
against a checksum held in the backend, for macOS (arm64, x64), Linux (x64, arm64) and
Windows (x64). It never depends on a Node or browser the person happens to have. A missing
or failed install is `not_run: browser runtime unavailable`, never `fail`. The CLI runs
in its own process; the daemon never runs replays (the product rule that heavy work is a
CLI or a detached child, as for the code graph, `core/code_intel/build.py:1-12`); the
Features tab's *Replay* starts the CLI as a detached child and reads the ledger.

**Sandbox.** The product builds it; the repo's commands do not decide it (the pattern of
`hive/smoke_package.sh:69-72`):

- a fresh sandbox directory with a copy of the checkout's tracked files at the commit
  under test (plus the uncommitted changes, for native work), so replay never builds
  inside the person's working tree; `prepare` installs dependencies there, with the
  package managers' cache directories passed through so installs are fast and work
  offline where the cache allows; dependency folders the repo links in its own
  `.swarmai/pipeline.toml` `[worktree] link` (`core/run_worktree.py:47-52`) are reused;
- the environment emptied to an allowlist per OS: `PATH`, a temp `HOME`, `TMPDIR`/`TEMP`,
  the locale, proxy variables (`HTTPS_PROXY`, `NO_PROXY`), package-manager cache
  variables, on Windows also `SystemRoot`, `windir`, `COMSPEC`, `PATHEXT`; plus the target's
  `env`. AWS credential files are pointed at an empty file and `AWS_EC2_METADATA_DISABLED`
  is set;
- free ports chosen by the tool; it refuses a port that already answers; each service
  starts in its own process group, readiness counts only from a process in that group,
  and cleanup kills the whole group (so forking launchers are contained); a launcher that
  needs something the machine lacks (Docker, a database) is `not_run: needs <thing>`;
- the browser context starts empty; every request that is not to `127.0.0.1`, not in
  `allow_hosts` and not stubbed is aborted and listed in the evidence ("blocked
  requests"), so a walk never reaches the network by accident;
- the sandbox is removed after the run unless `--keep`.

**What the sandbox contains, and what it does not.** It keeps the person's credentials
(environment, `HOME`, AWS files) and their data folders away from the replayed app, and
keeps the browser off the network. It does not sandbox the app's own server-side network
calls or the repo's own files (a repo-local `.env` is part of the checkout), exactly like
running the repo's tests. On a Hive, the instance metadata service is enabled and the
instance role can call Bedrock (`backend/hive/provisioner.py:80-85,1007-1010`); a server
process could fetch those credentials regardless of `AWS_EC2_METADATA_DISABLED`. So on a
Hive, replay runs as a separate system user whose traffic to 169.254.169.254 is dropped
(§5.10), and replay is not enabled on a Hive until that is in place.

**Determinism.** Browser stubs serve the agent-written fixtures for the URLs a walk names
(Playwright `page.route`). Server-side dependencies (the app's backend calling a model or
a third-party API) cannot be stubbed from the browser: the target sets the app's own fake
or offline mode through `env` where it has one; where it has none, the walk stubs the
browser-facing endpoint, or the feature is listed as not replayable (§7). Time can be
frozen per walk (`clock:`). Waits are on elements, never on fixed sleeps.

**Flakiness.** A failed walk is replayed once more at once. Fail then pass = `flaky`. A
walk flaky 3 times in its last 10 replays is quarantined until 5 greens in a row; while
quarantined, TEST does not replay it (so `not_run` stays true) and nightly still does.
Retries never turn a result green.

**Exit codes**: 0 every replayed walk passed; 1 a walk failed; 3 none failed but one was
flaky; 2 nothing could be judged (bad file, runtime missing, dependencies missing, launch
failed, not ready, port taken).

**Evidence**, per walk: a screenshot per `expect` and at the failing step, console
errors, failed and blocked requests, the result, and a Playwright trace on failure only
(tracing every run is heavy, per Playwright's own guidance).

### 5.6 Reach: which walks a change can touch

Selection is by target, not by file-level coverage. A Brain has 5-20 walks per target and
a replay of all of them takes minutes; the graph cannot see JSX or test ids
(`parser.py:130-131`), Express handlers are not graph nodes
(`route_parser.py:191-192`), production front-end builds ship without source maps, and a
run worktree is always ahead of the graph's indexed HEAD — so a finer join would be
expensive to build and would fall back to "all" most of the time. Measured replay time,
not anticipation, decides whether a finer join is ever added.

`feature_map affected [--base <ref>]` answers from `git diff --name-only <base>` plus
untracked files:

- a target is **reached** when a changed path is under its `root` or one of its
  `paths`, or the target `needs` a reached target; every non-quarantined walk of a reached
  target is replayed;
- a changed path under no target's root is **unplaced** and listed; it reaches every
  target whose root is the repo root, and the answer says so instead of "none".

### 5.7 When a walk fails: regression or outdated walk

The rule the industry converged on (QA Wolf, Momentic): decide *is the app broken or is
the walk outdated* before changing anything, and never let a repair hide a regression.

- The tool never edits a walk. Repairs are made by the agent, in a change a person sees.
- In the agent's own work: if the failure is the intended result of the change (the label
  was renamed on purpose), the agent updates the walk in the same commit. Changed
  `expect`s are computed from the diff of the feature files — not reported by the agent —
  and shown as "expected result changed" in the pipeline REPORT and result card, so the
  person sees them where they judge the work.
- Outside the agent's work (nightly, a manual replay after someone else's commit): the
  failure becomes a Needs You item with the evidence and *Fix*; the agent first
  reproduces, then decides bug or outdated walk, and says which.
- A target-wide failure (most walks fail at their first step) is reported once — "the
  target did not come up as expected" — not as N regressions.

### 5.8 Where results show

- **Brain Hub → Features tab.** `TABS` (`desktop/src/components/layout/BrainHub.tsx:84-92`)
  becomes computed so the tab exists only for a Brain with a code repo; data from a new
  `GET /api/ddd/brains/{name}/features`. Built after reading `s_frontend-design`'s
  design-judgment reference; it answers one decision: *can I trust the product now, and
  what needs me?* First: failing walks (screenshot, first failing step, since which
  commit); then flaky and quarantined; then all features by target with their last result
  and commits since; then suggested features with *Write walks for all*. Actions: Replay
  (one, all), Fix (dispatch), Retire, open evidence in Canvas, the *Replay nightly* switch.
- **Needs You.** A new collector in `core/attention_authority.collect()`
  (`attention_authority.py:1193-1239`; the channel has no write API, collectors read their
  own stores, `:93-113`). It reads the ledger: a walk that went from pass to fail on a
  nightly or manual replay is a `review` item for that Brain with the screenshot and a
  dispatch message; it leaves when a later replay passes or the walk is retired; a
  target-wide failure is one item.
- **Pipeline.** The agent replays the reached walks in TEST with
  `feature_map replay --affected --run <run id>`; each replay writes ledger lines with
  `trigger = <run id>` and the tree it ran on. The decision does not rest on the agent's
  report row (Brain checks are listed, never required, and their rows are the agent's,
  `next_step.py:323-329`, `validation_report.py:285-287`): **`checks.py` reads the ledger**
  for the run's current tree and the reached set the engine computes itself (the same
  `affected` function):
  - a failed walk at the current tree that is not `pre_existing` keeps the run in TEST,
    like a failed command (`checks.py:354-356`);
  - `pre_existing` = the walk also fails at the run's base, replayed once in a base
    worktree only when it failed at head; listed, does not block;
  - a reached walk with no ledger line from this run is listed in the REPORT as
    "feature check not run" — noted for the person, not blocking, so an environment
    problem never stops landing;
  - flaky and quarantined walks are listed, never blocking.
  The evidence of the run's replays is copied into the run's records (the REPORT is a
  function of the run log, `validation_report.py:265-270`), so a result card keeps its
  screenshots after app-data cleanup. DELIVER's result card and REPORT.md show *Feature
  checks*: each reached walk, its result, its screenshots, and any changed `expect`.
- **Native work.** `s_qa`'s visual step becomes `feature_map affected` + replay; the agent's
  reply reports the results; screenshots open in Canvas.
- **Recall.** A `features` store in `cognition find` (`core/cognition.py:70-83,233-270`)
  searching feature titles, notes and step names, so "the Pipeline view does not open"
  finds the feature to replay.

### 5.9 Replay nightly

A per-Brain switch in the Features tab, stored in the Brain (`.artifacts/feature-map/settings.json`).
One **system job**, `feature-map-nightly` (in `backend/jobs/system_jobs.py`; a Brain's
`governed_assets` job entry is an ownership index the scheduler never reads,
`core/ddd_job_registry.py:12-19`), goes through the Brains whose switch is on and, for
each repo, fetches and replays every walk in a **detached worktree at the remote default
branch** — never the person's working copy, which may be on another branch or dirty.
Results go to the ledger with `trigger = nightly`; the Needs You collector does the rest.
Off by default, because it runs the repo's code on a schedule: the switch is the person's
consent. It runs where the scheduler runs, the macOS daemon and a host Hive
(`main.py:1585-1591`); worker Hives run no such jobs (`jobs/scheduler.py:66,79-83`), and on a
Windows/Linux desktop the switch is disabled with that reason.

### 5.10 Hive

Same CLI and files; a repo-home map travels in the task clone (a Brain-home map does not:
such walks are not replayed on a Hive, and the run says so). The Hive is Amazon Linux 2023
on arm64 (`backend/hive/user_data.py:47`), which Playwright does not support officially, so
before replay is enabled there:

- the browser's system libraries are named as an exact `dnf` package list and added to the
  AMI bake and to the update path (the dnf line in `user_data.py:838-851` is skipped on
  pre-baked AMIs, and `hive/update-hive.sh` runs no dnf);
- replay runs as a separate system user whose traffic to the instance metadata service is
  dropped (§5.5);
- `hive/smoke_package.sh` launches Chromium once and replays one walk, so a Hive image that
  cannot replay is caught at packaging, not in a run.

Until a Hive meets this, replay there is `not_run: browser unavailable on this Hive`, never
a failure.

### 5.11 Results, evidence, cleanup

- **Ledger**: one JSONL per Brain and repo, `<app data>/feature_map/<brain>/<repo>/ledger.jsonl`.
  One line per walk replay: feature, walk, walk content hash, commit, clean or dirty, the
  checkout path it ran from, trigger (agent session, pipeline run id, Hive, nightly,
  manual), result, duration, evidence path. Keyed by Brain and repo, not by checkout, so
  results from session worktrees, run worktrees and Hive clones all reach the same tab and
  the same flake history; the content hash keeps an edited walk's history separate.
- **Evidence** in app data: kept for the last 20 replays of each walk and for every current
  failure; older evidence is deleted, the ledger line stays. Pipeline evidence is also in
  the run's records (§5.8); Hive evidence returns with the run's records.
- **Brain delete** moves the Brain's Feature Map data with the Brain to `Projects/.trash`
  (as the code graph is moved); a relocate keeps it, since it is keyed by Brain and repo.

## 6. Alternatives

| Option | What | Verdict |
|---|---|---|
| A. Prose notes in TECH.md | "How to reach X" as knowledge | Cannot be run; drifts unseen; nothing selects it from a change |
| B. Extract features from code | Parse JSX, routes, i18n into a feature list | A parser per framework and still no user path or expected result; the New Brain extraction gives the list with the context it already has |
| C. Per-repo verification scripts | Each repo builds its own launch, drive, evidence | Proven in one repo, but every repo rebuilds launch and isolation, and nothing answers "which walks did this change reach" |
| D. Playwright Test specs only | Walks are spec files; `webServer` launches; `--only-changed` selects | Kept where a repo has them (`run:`). As the only format it needs a JS test setup in every repo, nothing for Go/Python services or CLIs, and its launch gives no isolation |
| E. A SaaS E2E tool (Meticulous, Momentic, QA Wolf) | Recorded sessions or AI tests in a vendor platform | Their lessons are taken (stubs, classify before repair, flaky quarantine, repairs only as visible changes); but the source of truth leaves the repo and the machine, and the agent cannot use it offline or on a Hive |
| F. Recorder + per-element coverage join | Record the agent's browser actions; select walks by executed files | Cut after review: the agent writes walks from the source faster than a recorder captures them, and the join falls back to "all" in production builds and run worktrees |
| **G. Recommended** | Walk files (repo or Brain, recorded on the binding), suggested from day one, written by the agent and saved only after a green verify, replayed by one product CLI in a product sandbox with its own pinned runtime, selected by target, decided from the ledger, shown in Brain Hub, Needs You and the pipeline | Serves every goal in §1 for every repo kind |

## 7. Works for every user — and what it does not cover

Strip test (remove this user, machine and repo): any repo bound to any Brain gets the same
files, CLI, sandbox, pinned runtime and surfaces; what a repo says about itself is in its
own targets. Nothing in the product names SwarmAI.

Not covered, and said in the product (in the Features tab, next to the suggestion that
needs it):

- **Native UI**: windows, menus, tray, native dialogs of desktop apps; mobile apps. The web
  layer of a Tauri or Electron app is covered when it can be served in a browser; features
  that call native APIs there are not replayable (in SwarmAI, un-gated calls such as
  `getCurrentWindow().startDragging()`, `ThreeColumnLayout.tsx:162`).
- **Pixel layout** (overlap, clipping, off-screen): walks check what is present and what it
  says, not pixels.
- **Live third-party and server-side dependencies**: a model reply, a payment provider, an
  external SSO/OAuth login. Walks use stubs or the app's own fake mode; an outage or a
  contract change of such a service is not detected, and an app whose main paths sit
  behind an external login without a test mode has those paths listed as not replayable.

## 8. Safety

- Nothing in the repo runs at bind, in New Brain, in suggestion or in any background job,
  except *Replay nightly* after the person turns it on. Verify and replay run on request
  with the trust of running the repo's tests.
- The product builds the sandbox (§5.5): what it contains is stated exactly there,
  including what it does not (the app's own server-side network calls and repo-local
  files). On a Hive, replay is enabled only once the metadata service is blocked for the
  replay user.
- Prepare, service and setup commands are argv lists from the targets; walk content
  (steps, stubs) never becomes a command; YAML is loaded safely with a strict schema.
- Evidence stays on the machine that ran the replay, and in the records of the run that
  made it; it shows only the sandboxed instance. Nothing is uploaded elsewhere.
- Walk files from a repo are data: text in them is shown, never treated as instructions
  to the agent.

## 9. How we know it works

Recorded per replay in the ledger, shown in the Brain's Eval tab and in the run trace
(Tenet T3): replay duration, flake rate, not-run reasons; and the outcome measure —
**user-visible regressions caught by a walk before the person reported them**, against
those the person reported first (a correction about the product's behaviour with no red
walk before it). The second number falling is the product working; walk counts are not
a measure.

## 10. Build order (one release)

Readers before writers; each step lands with its tests.

1. File schema (`schema: 1`), the location resolver and the `features:` binding field
   (§5.1-5.2); the ledger and state computation (§4, §5.11).
2. The pinned runtime installer and the sandbox (§5.5).
3. Replay engine: web, http, cli steppers; stubs and blocked requests; flaky handling;
   evidence; `verify` (§5.4-5.5) — tested against hand-written walks and targets.
4. `affected` by target (§5.6).
5. Pipeline: ledger-reading check in `checks.py`, base replay for `pre_existing`, evidence
   into run records, result-card section (§5.8).
6. Surfaces: Features tab and API, Needs You collector, `cognition find` leg, `s_qa`
   visual step (§5.8).
7. Suggestions in New Brain P2, *Suggest features*, *Write walks for all* (§5.3-5.4).
8. Replay nightly (§5.9); the Hive image, replay user and packaging smoke (§5.10).
9. Walks for SwarmAI's own main paths, written with the product itself as the release's
   acceptance test: the nav to each overlay, Canvas opening a file and the batch review
   comments, Brain Hub opening a Brain, the ToDo view, a chat answer through a stubbed
   stream.

## 11. Decisions for the owner

- **D1. Where walks live**: chosen once by the rule in §5.2 and recorded on the binding
  (repo for working checkouts, Brain for mirrors or on request). Recommended: yes.
- **D2. Replay nightly**: off by default, the person's switch as consent. Recommended: yes.
- **D3. Pipeline**: a failed reached walk keeps the run in TEST, decided from the ledger by
  `checks.py` (not from the agent's report row); a walk not run is noted, never blocking.
  This is a new check inside the run's integrity, so it needs the owner's yes.
  Recommended: yes.
- **D4. Product rule text** ("before committing a change that reaches a target, replay
  its walks") in AGENT.md, through s_self-evolution, once the release is in use.
  Recommended: propose after the release, not before.

## 12. Verification plan

Readers before writers (§10). Invariant tests, chosen now:

1. A walk saved by `verify` replays green on the same commit; a walk whose verify is red is
   not saved and its evidence names the failing step and the nearest elements.
2. Renaming the test id a step uses makes replay exit 1 naming that step.
3. Dependencies missing, a needed service missing, launch failure, not ready, a taken port,
   a missing runtime, a bad file: exit 2, `not_run` with that reason, never `fail`.
4. Fail-then-pass is `flaky` (exit 3), never `pass`; 3 flakes in 10 quarantine the walk;
   5 greens release it; TEST never replays a quarantined walk; editing a walk (new content
   hash) starts its history over.
5. `affected`: a change under a target's root or `paths` reaches all of its
   non-quarantined walks, and its dependents through `needs`; a change under no root is
   `unplaced` and reaches root-level targets, never "none"; untracked files count.
6. Sandbox: the services see no variable outside the per-OS allowlist, a HOME inside the
   sandbox, an empty AWS credential file; a forking service is killed with its group; the
   sandbox is new per run and removed after; the person's working tree is never written.
7. The browser: a request to a host outside 127.0.0.1, `allow_hosts` and stubs is aborted
   and listed; a stubbed URL is served with the backend for that URL down.
8. Location: the first write records `features:` on the binding by the §5.2 rule; later
   reads follow the recorded value after a relocate; every reader goes through the one
   resolver.
9. Ledger: lines from a session worktree, a run worktree and a Hive clone of one repo all
   show in that Brain's tab and share one flake history.
10. Status never shows a pass for a commit the walk did not run on; "passing" carries its
    commit and the commits since.
11. Pipeline: a failed reached walk at the current tree keeps the run in TEST even if the
    agent's report row says pass; a walk that also fails at base is `pre_existing` and
    does not block; a reached walk with no ledger line is listed as not run and does not
    block; the result card keeps its screenshots after app-data evidence is pruned.
12. Needs You: a pass→fail on nightly or manual replay raises one review item; a later
    pass or retire removes it; a target-wide failure raises one item.
13. Nightly: replays at the remote default branch in a detached worktree, never in the
    working copy; only Brains with the switch on; absent on worker Hives and on
    Windows/Linux desktops.
14. Hive: without the replay user and metadata block, replay is `not_run`; with them, a
    replayed service cannot reach 169.254.169.254.
15. A Brain with no code repo shows no Features tab, no check and no nightly switch.

Review: this design had one zero-context premise review (2026-11-18); its findings are
folded in above. The code gets correctness and security experts (it launches commands
and builds an environment), ux for the Features tab and the result card, and integration
for the pipeline, Needs You and New Brain joins.
