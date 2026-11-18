---
title: Feature Map — a Brain's map of what the product does and how to check it
date: 2026-11-18
created: 2026-11-18
updated: 2026-11-18
project: SwarmAI
status: REVISED after one premise review (2026-11-18); for the owner's decision on Q1-Q3
related: core/code_intel (the code graph this map joins to); skills s_browser-agent (the driver it reuses), s_qa, s_expert-review; hive/smoke_package.sh and backend/scripts/newuser_smoke_e2e.sh (the isolated launch it copies); core/run_protocol/checks.py (how a TEST report is judged)
---

# Feature Map

## 1. Problem

A Brain that governs a code repo knows how the code is built (the code graph:
symbols, callers, imports). It does not know what the product does for a user: which
features exist, how a user reaches each one, what a working result looks like, and
how to drive the app there. So when a change touches user-visible behaviour, nothing
checks it from the user's side, and nothing remembers how to.

What the code shows today:

- No stage of the pipeline drives the app. TEST runs "the change's tests and the wider
  checks the repo expects" (`backend/skills/s_autonomous-pipeline/stages/test.md:3`);
  DELIVER is reflect and land.
- The only skill that looks at a running UI is `s_qa`, and its visual step is one line:
  start the dev server, use `s_browser-agent` on affected routes, take screenshots
  (`backend/skills/s_qa/INSTRUCTIONS.md:85-91`). SwarmAI's desktop app has one route
  (`desktop/src/App.tsx:315-326`), so "affected routes" selects nothing.
- `s_browser-agent` can drive any web page, but its element numbers reset on every
  read and it saves nothing between sessions (`INSTRUCTIONS.md:293-297`; its only
  state is under /tmp). Every session works out again how to reach a feature.
- The workspace copy of `s_chat-brain-check` keeps a hand-written "F4: Live Smoke
  Test" table of steps and expected results. It covers chat only, and nothing runs it.
- What does exist is an **isolated launch**: `hive/smoke_package.sh` and
  `backend/scripts/newuser_smoke_e2e.sh` start a backend under an empty environment
  and a temp HOME, with AWS credential files and the instance metadata service off
  (`smoke_package.sh:69-71`). They check HTTP contracts, not what a user sees. The
  replay sandbox in this design copies that pattern (§4.4).

The cost is visible in our own history: front-end regressions are found by the person
looking at the app, then reproduced by the agent by hand, again and again. An agent
that cannot reproduce a report or check a result on its own cannot be left alone with
the work.

## 2. What a Feature Map is

A Feature Map is a list of the product's features. Each feature carries one or more
**walks**: from a start state, the steps a user takes to reach the feature, and what
must (or must not) be visible at the end. A walk is **replayed** by a tool with no
model in the loop, so it works like a test written from the user's side.

It is the partner of the code graph:

| | Code graph | Feature Map |
|---|---|---|
| Answers | how the code is built | what the product does for a user |
| Unit | symbol, edge, import | feature, walk, step |
| Made from | parsing the repo | recording one real run, then saved |
| Used for | callers, impact, review facts | reproducing reports, checking changes, judging if a feature is complete |

The two are joined at file level: each walk lists the files that render what it
touches, and the graph's import table tells which walks a change can reach (§4.5).

## 3. Who uses it

- **The agent reproducing a report.** The person says "the Pipeline view does not
  open from the nav". The agent finds the feature, replays its walk, and gets a red
  step with a screenshot, or a green run that says the report needs more detail.
- **The agent checking its own change** (native work or a pipeline run). Before
  commit it asks which walks the change can reach, replays them, and looks at the
  screenshots: the observable result that a test repeating the code's own logic does
  not give.
- **A new user who binds a web-app repo.** Nothing new happens at bind. The first time
  the agent needs to check a feature, it records the walk it used; the map grows from
  real work. No setup screen, no questions.
- **An existing user** with repos already bound: the same. The map starts empty.
  Nothing to migrate.
- **Another tool or teammate on the same repo.** The walk files live in the repo
  (§4.2), so they can read and replay them. Replay results stay on the machine that
  ran them; a fresh clone sees walks with no result yet, which is the truth for it.

## 4. Design

### 4.1 The record

One file per feature. Example, using elements that exist in the SwarmAI app today
(`data-testid="nav-pipeline"`, `desktop/src/components/layout/ThreeColumnLayout.tsx:377`;
`overlay-host-mode-badge`, `desktop/src/components/layout/OverlayHost.tsx:262`, which
shows the mode `PIPELINE` from `desktop/src/components/layout/navTable.ts:69`):

```yaml
id: pipeline-overlay          # assigned once when the feature is created, never derived from a label
title: Open the Pipeline view from the left nav
target: app                   # names a target in target.yaml
walks:
  - id: from-home
    start: home               # a start state the target defines
    steps:
      - click: {testid: nav-pipeline}
      - expect:
          visible: {testid: overlay-host-mode-badge}
          text: PIPELINE
    notes:                    # how to drive it, learned the hard way
      - The overlay animates in; wait for the badge, not for a fixed time.
    files:                    # the files that render what the walk touches (filled at save, §4.5)
      - desktop/src/components/layout/ThreeColumnLayout.tsx
      - desktop/src/components/layout/OverlayHost.tsx
```

Steps: `click`, `type`, `press` (a key), `drag` (from, to), `wait` (for an element),
and `expect`. An `expect` checks `visible`, `text`, or `absent` with a settle time
(for "this must not appear" and "this must appear by itself within N seconds"). A step
finds its element by a test id, else role plus accessible name, else visible text.
The target fixes the locale, so names do not change with the language.

A repo that already has Playwright or Cypress specs does not write steps twice: a walk
may instead say `run: <command>` (for example one named spec), and the feature keeps
only its title, notes and files.

`target.yaml` says how to start the app: the launch command, the readiness URL, the
URL to open, the locale, and the start states. A **start state** is a list of setup
commands run against the fresh instance, or a fixture folder copied into the sandbox
before launch. For SwarmAI, `home` would mark onboarding complete (a fresh data folder
opens on the onboarding wizard, `App.tsx:306`; `newuser_smoke_e2e.sh` flips it with
`PUT /api/system/onboarding-complete`), and a `with-brain` state would copy in a
fixture Brain. Its launch would start the backend on `FEATURE_MAP_PORT` (with a temp
HOME the backend's data folder is inside the sandbox, `backend/config.py:47-66`) and
serve a front end built with `VITE_API_URL` pointing at it
(`desktop/src/services/tauri.ts:58-74`).

### 4.2 Where it lives

**Recommended: in the bound repo, under `.swarmai/features/`** (one file per feature
plus `target.yaml`), next to the `.swarmai/review.md` and `.swarmai/pipeline.toml`
files repos already use to tell SwarmAI about themselves.

Why the repo and not the Brain folder:

- A walk describes the product built from that repo, at that commit. When a change
  renames a button, the walk is fixed in the same commit, the way a test is.
- Everyone and every tool working on the repo gets it, Hive clones included.
- It does not compete with CDF. CDF is the one writer of the Brain's documents; walk
  files are records a tool replays, closer to tests than to knowledge.

Replay evidence and results (screenshots, console errors, the result file) go to the
app data folder, per repo. Nothing else is stored: what a status needs is computed
when asked (§4.6).

If a repo's owners do not want these files in their repo, the fallback is the Brain's
`.artifacts/feature-map/<repo>/`. This is open question Q1.

### 4.3 How a walk is made

1. **The agent drives the app** in the replay sandbox (§4.4) with `s_browser-agent` in
   a new recording mode. Each action is logged with a stable locator for the element
   acted on. The accessibility snapshot has no test ids, so recording reads the
   element's `data-testid` from the DOM, and falls back to role and name.
2. **The agent adds what must be visible at the end** (`expect`) and any driving notes.
3. **The tool replays the walk at once** in a fresh sandbox, and saves it with the
   result. A walk that has never replayed green is shown as **never passed**.

What counts as a feature is written into the recording instructions, so every agent
cuts features the same way: something a user does and can see the result of, reached
from a start state, small enough that one walk shows it works. Features are added when
they are needed (a report to reproduce, a change to check), not listed up front.

### 4.4 Replay and the sandbox

`feature_map replay <feature>[/<walk>]` launches the target in a sandbox, waits for
readiness, sets up the start state, runs the steps with Playwright (reusing
`s_browser-agent`'s Playwright setup, not a second driver), and writes the evidence: a
screenshot per `expect`, console errors, failed network requests, and a result (the
first failing step, with what was expected and what was seen). No model is called.

Exit codes: **0** every walk passed; **1** a walk was red (the app did not do what the
walk expects); **2** the walk could not run (launch failed, not ready in time, port
taken, a locator never appeared because the start state did not hold). Exit 2 is
reported as not run, with its reason, never as a failure.

The product builds the sandbox; the repo's launch command does not get to decide it:

- an empty environment except an allowlist (PATH, a temp HOME, `TMPDIR`, the locale,
  `FEATURE_MAP_SANDBOX` = a fresh directory, `FEATURE_MAP_PORT` = a free port), with
  AWS credential files and the instance metadata service off, the way
  `hive/smoke_package.sh:69-71` starts a backend. Anything else the launch needs is
  named in `target.yaml` and passed explicitly;
- before launch, the tool refuses to start if the chosen port is already listening;
  after launch, readiness counts only if the process listening on the port descends
  from the process replay started. A dev server the person already runs can never be
  driven by mistake;
- the sandbox is removed after the run unless kept for inspection.

So a walk that clicks "send" acts on a throwaway instance, and with no credentials it
cannot make a paid model call. Walks that need a model reply (a chat answer, Canvas
opening on an answer) are out of phase 1; a model stub is phase 2 if the dogfood shows
they matter.

Replay and record run only when the agent or the person asks, with the same trust as
running the repo's tests. Bind, the code-graph build and any scheduled job never run
them, so a just-bound clone executes nothing.

### 4.5 Which walks a change can reach

When a walk is saved, the tool fills its `files` list with no model: each test id or
literal name is searched in the repo, and the files where it is written are kept.
Text search works in any language. A locator that cannot be found (a test id built at
run time) is kept and noted; the agent adds the right file by hand if it knows it.

`feature_map affected [--base <ref>]` then answers from the changed files
(`git diff --name-only`) and the graph's import table (`code_imports`, src_file →
dst_file, `backend/core/code_intel/graph_store.py:139-148`), found through
`code_intel_dbs()` (`backend/core/code_intel/__init__.py`), the one lookup that also
covers a repo that is not a managed clone:

- a walk is **reached** when a changed file is one of its files, or is imported by one
  of its files directly or through one more file. This is the usual front-end case: the
  button lives in a child component that the walk's file imports;
- a changed file the graph cannot place (CSS, build config, a lockfile, a file the
  parser skips) and a changed file that only the backend uses are listed as
  **unplaced**: the change may reach every walk of that target, and the answer says so
  instead of saying "none";
- when there is no graph, or it is behind HEAD, the answer is "unknown, replay all",
  never "none".

The answer orders the walks to replay. It never vouches that a walk still passes; only
a replay does.

### 4.6 What a status says

There is no derived "verified" status. For each walk, `feature_map status` reports
facts: the last replay's result and the commit it ran on, whether the tree was clean
or dirty then, and how many commits and changed files lie between that commit and now,
with the reached ones marked. "Last passed at abc123, clean tree, 4 commits since, 1
reached file changed" lets the reader judge; a single word would claim more than the
data holds.

### 4.7 Who reads it

- **The agent**, in any work on the repo: `feature_map affected` before a commit that
  touches user-visible code; `feature_map find <words>` when reproducing a report.
- **The pipeline**: no new stage and no new check. A replay the agent runs in TEST is
  reported like any other command, so a red walk counts like a red test (the TEST
  report's failed commands are refused, `backend/core/run_protocol/checks.py:351-353`),
  and a walk that could not run (exit 2) is reported as not run with its reason, so an
  environment problem never blocks landing. `stages/test.md` says this in one line.
- **Later, only if the dogfood shows the need**: a `features` leg in `cognition find`,
  and a Brain Hub card that answers one question (which features can the agent check
  alone, which have not passed lately), built after reading `s_frontend-design`'s
  design-judgment reference.

### 4.8 What it does not hold

- **Why a feature tends to break** (a root cause, a class of bug) is knowledge. It
  stays in the Brain's documents through CDF. Tagging those entries with a feature id
  is open question Q2.
- **Expected values that drift** (counts, timings). An `expect` checks what a user
  sees, not a number that changes every week.

## 5. Alternatives

| Option | What | Why not (or why) |
|---|---|---|
| A. Notes in TECH.md | The agent writes "how to reach X" as prose in the Brain | Cannot be run, so it drifts unseen; nothing selects it from a change |
| B. Extract from code, like the graph | Parse JSX, routes, i18n and shortcuts into a feature list | A parser per front-end framework, and it still cannot know the user's path or a working result. Text search keeps only the useful part (§4.5) |
| C. A per-repo verification skill (the approach in the source talk) | Each repo writes its own launch/drive/evidence script and a markdown map | Proven, but every repo builds its own launch and isolation, and nothing answers "which walks did this change reach" |
| D. Playwright Test specs only | Walks are spec files; Playwright's `webServer` launches the app | Good where a repo already has them, so a walk may point to one (`run:`). As the only format it needs a JS test setup in every repo, and its launch does not give the isolation of §4.4 |
| **E. Recommended** | Repo-owned walk files (steps, or a pointer to an existing spec); one product CLI to record, replay in a product-built sandbox, and select walks from a change through the graph's imports | Runs with no model, travels with the repo, isolated by the product, and the only option that answers §4.5 |

## 6. Works for every user (strip test) and limits

Remove this user, this machine and this repo: a web app in any language, bound to any
Brain, gets the same CLI, file format, sandbox and Playwright driver. What the repo
says about itself (launch, start states, fixtures) is in its own `target.yaml`.
Nothing in the product names SwarmAI.

Limits in phase 1:

- Web targets only. Desktop-only behaviour of a Tauri or Electron app (native file
  dialogs, tray, windows) is not covered; 20 non-test front-end files in SwarmAI
  import `@tauri-apps/*` and will not work in a browser.
- No walks that need a model reply.
- No replay on a Hive: a Hive provisions `mise` tools only, no browser
  (`backend/core/project_agent.py:70`).
- Reach is file level plus two import hops. A shared store or layout parent several
  hops away shows up only as "unplaced" when it is not code, or not at all when it is
  imported further away; the agent replays more when the change is broad.

## 7. Safety

- Bind and every background job execute nothing from the repo. `record` and `replay`
  run only on request, with the trust of running the repo's tests.
- The product builds the sandbox (allowlisted environment, temp HOME, no AWS
  credentials, no instance metadata), refuses a port that is already taken, and drives
  only a process it started (§4.4).
- The launch command is run as an argument list from the repo's own `target.yaml`,
  never through a shell string built from walk content.
- Evidence stays on the machine, under the app data folder, and shows only the
  sandboxed instance.

## 8. Phases

1. **Phase 1 — the loop works on one repo.** The file format, the `feature_map` CLI
   (`record`, `replay`, `status`, `affected`, `find`), the sandbox, the recording mode
   in `s_browser-agent`, the `test.md` line. Dogfood on SwarmAI with walks that need no
   model: the Pipeline view from the nav, Brain Hub opening a fixture Brain, a file
   opened from the Workspace explorer showing in Canvas, a ToDo shown in the ToDo view
   from a fixture. Each is confirmed when it is recorded.
2. **Phase 2 — only what the dogfood asks for.** A model stub for chat walks, the
   `cognition find` leg, the Brain Hub card, tagging CDF entries with a feature id.
3. **Phase 3 — more targets.** Command-line targets (command, expected output), macOS
   native apps through `s_peekaboo`, replay on a Hive.

## 9. Open questions for the owner

- **Q1. Walk files in the repo or in the Brain?** Recommended: the repo
  (`.swarmai/features/`), with the Brain's `.artifacts/` as the fallback. In the repo
  they change in the same commit as the UI and every tool on the repo gets them; in
  the Brain they stay private to this machine.
- **Q2. How a CDF entry names its feature** (phase 2). Like the `skill` field on a
  cognition unit (`backend/core/ingestion/contracts_v2.py:193`), but a reader must be
  able to join on it after the inbox is cleaned; to check before phase 2.
- **Q3. Should the product rules say "replay the reached walks before a commit that
  touches user-visible code"?** Not before phase 1 shows walks are cheap and steady;
  then through the normal governance path.

## 10. Verification plan

Readers before the writer: `replay` and `affected` are built and tested first against
hand-written walks; `record` comes after, and must produce a file they accept.

Invariant tests, chosen now:

1. A recorded walk replays green on the same commit.
2. Renaming the test id a step uses makes replay exit 1, naming that step.
3. A launch that never becomes ready exits 2 and is reported as not run.
4. Changing a child component that a walk's file imports marks the walk reached;
   changing an unrelated file does not.
5. A changed CSS file is reported as unplaced, never as "no walks reached"; with no
   graph the answer is "unknown, replay all".
6. Replay refuses when the port is already listening, and never treats a process it
   did not start as ready.
7. The launched process sees no variable outside the allowlist, a HOME inside the
   sandbox, and no AWS credential file; the sandbox directory is new for each run and
   removed after it.
8. A walk that has never replayed green is reported as never passed, and `status`
   never prints a pass for a commit the walk did not run on.

Review: this design had one zero-context premise review (2026-11-18), whose findings
are folded in above. The phase 1 code gets correctness and security experts (it
launches commands and builds an environment).
