---
title: Feature Map — a Brain knows what its product does, and can prove each feature still works
date: 2026-11-18
created: 2026-11-18
updated: 2026-11-23
project: SwarmAI
status: v3 — layered (own the knowledge and the loop, rent the execution) and scoped (the core loop now, the rest on measured triggers); supersedes v2.1. Owner direction 2026-11-19: "按这个分层改设计文档；先做核心闭环，walk 用 Playwright spec 格式".
related: core/code_intel; core/brain_bootstrap; skills s_browser-agent, s_qa, s_autonomous-pipeline (TEST); core/run_protocol (validation reports, checks); core/attention_authority; jobs; hive
---

# Feature Map

## 0. Summary

A Brain that governs a code repo knows how the code is built (the code graph). With
Feature Map it also knows **what the product does for its users**, and can **prove each
feature still works**: a replay with no model in the loop, in a sandbox the product
builds, with screenshots the person can judge.

The design is in two layers, and the split is the point:

- **What SwarmAI owns** (durable, the differentiator): the feature knowledge — which
  features exist, how a user reaches each, what working looks like, which part of the repo
  each depends on — and the loop around it: a change selects the features it can reach,
  their results decide a pipeline run from facts the program recorded, and the evidence
  reaches the person where they judge the work.
- **What SwarmAI rents** (standard, maintained by others): the execution. A web walk is a
  standard **Playwright Test spec**; browser driving, waiting, assertions, retries, flaky
  detection, screenshots and traces are Playwright's. SwarmAI ships a pinned Playwright
  runtime so a repo needs no JS test setup of its own, in any language.

Scope: the **core loop** is built now (§3-§9). Everything else (§10) is built when a
measured trigger says it is worth it.

## 1. Why, and why this form

**Today** (from the code, 2026-11-18): nothing in SwarmAI drives a product from the
user's side. Pipeline TEST runs "the change's tests and the wider checks"
(`backend/skills/s_autonomous-pipeline/stages/test.md:3`); `s_qa`'s visual step opens routes
with `s_browser-agent` and judges screenshots by eye (`backend/skills/s_qa/INSTRUCTIONS.md:85-92`),
with nothing saved or replayable. `s_browser-agent` is an exploration tool: element numbers
change on every read (`INSTRUCTIONS.md:293-297`), one global state under `/tmp`
(`browser-agent.mjs:98-109`), and its browser is installed by hand (`INSTRUCTIONS.md:49-58`).

**What it would catch, measured.** In the last 30 days `desktop/src` had 470 commits, 125
of them fixes. Of 16 user-visible fixes sampled: about 7 were a page crashing, wrong text
or language, a missing element or wrong state (the Project Agent page crashing on its
profiles table, one panel taking the whole app down, Needs You showing 需要你 in English,
a reload marking a file unsaved) — catchable by a walk; about 6 were position and
appearance (a comment box leaving the window, dialogs under the macOS title bar,
light-theme contrast) — catchable for the "leaves the window" part by an in-viewport check
(§4.3), not for colour; about 3 were races or backend/Hive state — not a walk's job. Of
the 42 sessions the person corrected in W44-W47, only 2-3 were about a UI regression; the
rest were judgment. So the value is concentrated: the agent checks its own user-visible
changes with evidence, a large class of front-end regressions is stopped before the person
sees it, and the person judges a pipeline run from screenshots instead of opening the app.
It is not a lever on judgment mistakes.

**Why it is a long-term direction.** As code gets cheap to write, the limit on autonomy is
verification: with people only at the two ends of the work, the agent must bring evidence
from the user's side. A deterministic replay is also the only kind of verification that may
decide a gate (a model's verdict cannot, SOUL P5); model-driven checks will grow next to it
for new or changed features, not replace it.

**Why rent the execution.** Playwright already provides the runner, the waiting model,
assertions including `toBeInViewport({ ratio })`, retries with a `flaky` outcome, traces,
and (since 1.56) planner / generator / healer agents. A home-grown step format would
compete with all of that and need maintaining forever. What no runner provides is the
knowledge of what the product's features are and the loop from a change to the person's
judgment — that is SwarmAI's part.

## 2. The concepts

| Concept | What it is | Layer | Where it lives |
|---|---|---|---|
| **Feature** | Something a user does and can see the result of: id, title, how a user reaches it, the expected result, its target, its specs | owned | `features.yaml` in the feature folder |
| **Target** | One runnable part of the product: kind, root in the repo, how to prepare it, which services to start, start states | owned | `targets/<id>.yaml` |
| **Walk** | One Playwright test (a `test()` in a spec file) belonging to a feature | rented format | `specs/<feature-id>.spec.ts` |
| **Command walk** | For an HTTP service or a CLI: a command whose exit code decides (a `curl -f …`, a script, an existing test) | owned, minimal | in `features.yaml` (`run:`) |
| **Replay** | One product CLI run: sandbox → prepare → services → start state → Playwright (or the commands) → evidence → ledger | owned loop, rented runner | — |
| **Ledger** | One line per walk result: walk, content hash, commit, tree, trigger, result, evidence | owned | app data (§7) |

The feature folder is `.swarmai/features/` in the repo, or the Brain's
`.artifacts/feature-map/<repo>/` (§4.4). A Brain with no repo has no Feature Map.

## 3. The core loop, end to end

1. **Write.** The agent writes a feature entry and its spec from the source (test ids,
   labels and routes are there), may explore the running app with `s_browser-agent` as
   today, and runs `feature_map verify <feature>`. A walk is saved only when verify is green.
2. **Change.** The agent (native or in a pipeline run) changes code.
3. **Select.** `feature_map affected` lists the features whose target the change reaches
   (§5).
4. **Replay.** `feature_map replay --affected` replays them in a fresh sandbox (§6); each
   result is a ledger line written by the CLI.
5. **Decide.** In a pipeline run, `checks.py` reads those ledger lines, not the agent's
   report row: a failed reached walk keeps the run in TEST; a walk that could not run is
   noted and never blocks (§8).
6. **Show.** The agent's reply (native) and the run's result card and REPORT (pipeline)
   carry the results and the screenshots; changed expectations are listed (§8). The person
   judges there.

## 4. Files

### 4.1 `features.yaml` — the feature knowledge

```yaml
schema: 1                            # an older SwarmAI refuses a newer major, saying why
features:
  - id: pipeline-overlay-k3f9        # assigned once at creation (slug + 4 random chars), never derived from a label
    title: Open the Pipeline view from the left nav
    reach: Click Pipeline in the left nav       # how a user gets there, in words
    expect: The Pipeline overlay opens with its mode badge
    target: web
    spec: specs/pipeline-overlay-k3f9.spec.ts
    notes: [The overlay animates in; wait for the badge, not a fixed time.]
  - id: api-health-7q2m
    title: The API answers health checks
    target: api
    run: [curl, -sf, "http://127.0.0.1:{port.api}/health"]   # command walk: exit 0 = pass
```

`reach` and `expect` are the knowledge a person or a later agent reads to understand what
the spec checks; they are also what the `cognition find` leg will search (§10). The file is
read with a strict schema (unknown keys refused, the key named). Schema 2 adds an optional
`owns` per feature: the repo-relative prefixes only that feature depends on, used to
narrow replay (§5, and `2026-11-23-feature-map-replay-conditions.md` §3.3); it is not part
of the walk's content hash.

### 4.2 Specs — standard Playwright

The example uses elements that exist in SwarmAI today: `data-testid="nav-pipeline"`
(`desktop/src/components/layout/ThreeColumnLayout.tsx:377`) and `overlay-host-mode-badge`
(`desktop/src/components/layout/OverlayHost.tsx:262`); the `home` start state marks
onboarding complete because a fresh data folder opens on the onboarding wizard
(`desktop/src/App.tsx:306`).

```ts
import { test, expect } from '@playwright/test';

test('from home', { tag: '@start:home' }, async ({ page }) => {
  await page.goto('/');
  await page.getByTestId('nav-pipeline').click();
  const badge = page.getByTestId('overlay-host-mode-badge');
  await expect(badge).toBeVisible();
  await expect(badge).toHaveText(/pipeline/i);
  await expect(badge).toBeInViewport({ ratio: 1 });
});
```

Rules for specs (what the product config and the verify step enforce or rely on):

- the start state is a tag `@start:<name>` (one per test, default `default`);
- `page.goto` uses relative URLs; the product config sets `baseURL`;
- no network beyond the sandbox (§6.3); stubs are `page.route` with fixtures under
  `fixtures/`, written by the agent, never captured from a live call;
- no fixed sleeps (`waitForTimeout` is refused at verify).

### 4.3 The in-viewport check

The class of bug the person most often finds by eye is something leaving the window or
being covered. Playwright's `toBeInViewport({ ratio: 1 })` is deterministic: the element is
wholly inside the viewport. The product config runs every web spec in **two viewports**,
`regular` (1440×900) and `compact` (1024×640), as two Playwright projects, so a dialog,
popover or menu that only fits a large window fails in `compact`. The writing standard
(§4.5) asks every walk that opens a dialog, popover or menu to assert `toBeInViewport({ ratio: 1 })`
on it. This does not see colour, contrast or overlap with a native title bar.

### 4.4 Where the folder lives

The first time a feature is written for a repo, the location is decided and recorded on
that binding in `bindings.yaml` as `features: repo` or `features: brain` (one new optional
field on `Binding`, `core/ddd_bindings.py:105-113`); every reader then uses the recorded
value through one resolver.

| The binding's checkout (`core/repo_placement.py:8-15`) | First value |
|---|---|
| `local` or `repos_dir:<name>` (a working checkout, including internal ones people commit CRs from) | `repo` — `.swarmai/features/`, next to `.swarmai/pipeline.toml` and `.swarmai/review.md`; specs change in the same commit as the UI, like tests |
| `mirror` (the managed clone, replaced on re-bind) | `brain` — `.artifacts/feature-map/<repo>/` |
| The person says "keep it out of this repo" | `brain` |

### 4.5 Targets

```yaml
# targets/web.yaml
id: web
kind: web                         # web (Playwright specs) | command (command walks only)
root: desktop/                    # changes under here reach this target (§5)
paths: [shared/ui/]               # optional, more paths that reach it
ignore: ["desktop/**/__tests__/**"]   # optional (schema 2), globs that are no change to it
needs: [api]                      # targets started first
prepare:                          # run to completion, in order, in the sandbox copy
  - [npm, ci]
  - [npm, run, build]
services:                         # kept running for the replay; each in its own process group
  - {id: ui, argv: [npx, vite, preview, --port, "{port.ui}", --strictPort], ready: "http://127.0.0.1:{port.ui}/"}
base_url: "http://127.0.0.1:{port.ui}/"
env: {VITE_API_URL: "http://127.0.0.1:{port.api}"}
start_states:
  default: {}
  home: {setup: [[curl, -sf, -X, PUT, "http://127.0.0.1:{port.api}/api/system/onboarding-complete"]]}
locale: en-US
```

Placeholders: `{port.<service>}` (a free port per service), `{sandbox}`, `{checkout}`. All
commands are argv lists, never shell strings. The writing standard for features and specs
(what a feature is: something a user does and can see the result of, small enough that one
walk shows it; the product's main paths first; stable handles — role and name where the
label is product copy, test id where it is not; the in-viewport rule) ships with the
`feature_map` skill so every agent writes them the same way.

## 5. Reach: which features a change can touch

By target, not by file-level coverage: a target has a handful to a few dozen walks and
replaying them all takes minutes, while the code graph cannot see JSX or test ids
(`core/code_intel/parser.py:130-131`), production builds have no source maps, and a run
worktree is always ahead of the graph. Measured replay time, not anticipation, decides
whether a finer join is ever added.

`feature_map affected [--base <ref>]`, from `git diff --name-only <base>` plus untracked
files:

- a target is **reached** when a changed path is under its `root` or `paths`, or it `needs`
  a reached target; all its walks are selected (a quarantined one too: it is replayed and
  recorded, so it can earn its release, and never blocks);
- a changed path under no target is **unplaced** and listed; it reaches every target whose
  root is the repo root, and the answer says so instead of "none";
- a change to the feature folder itself reaches the features whose files changed.

Schema 2 adds two optional declarations that narrow this, each falling back to the rule
above when it does not apply (`2026-11-23-feature-map-replay-conditions.md` §3): a path
matching a target's `ignore` does not reach it and is listed as ignored; within a reached
target, a path some feature `owns` selects only its owners (plus the features that own
nothing), a path no feature owns selects them all, a target reached through `needs` selects
them all, and a prefix missing from the checkout is listed as stale and treated as not
declared.

## 6. Replay

### 6.1 Runtime

`"$SWARM_BACKEND_BIN" py -m scripts.feature_map <verify|replay|affected|status> …` (the
product CLI convention: JSON on stdout, exit codes; `backend/desktop_main.py:554`). On first
use it installs into the app data folder a **pinned Node runtime, a pinned
`@playwright/test` and its Chromium**, each checked against a checksum held in the backend,
for macOS (arm64, x64), Linux (x64, arm64) and Windows (x64). It never relies on a Node or
browser the person happens to have (Node is optional on the desktop:
`desktop/src/components/settings/SystemTab.tsx:58-64`). A missing or failed install is
`not_run: runtime unavailable`.

The specs import `@playwright/test`. So that the product's pinned copy is the one they
resolve (and a repo's own Playwright, if any, never mixes in), the sandbox's copy of the
feature folder gets a `node_modules/@playwright/test` link to the pinned package; the
nearest `node_modules` wins in Node's resolution. The product's Playwright config (generated
per replay, never the repo's) sets `testDir`, `baseURL`, the two viewport projects,
`retries: 1`, `screenshot: 'on'`, `trace: 'retain-on-failure'`, the JSON reporter, the
locale, and the browser proxy of §6.3. **First build step verifies this resolution on
all three OSes before anything else is built on it.**

The CLI runs in its own process; the daemon never runs replays (heavy work is a CLI or a
detached child, as for the code graph, `core/code_intel/build.py:1-12`).

### 6.2 Sandbox

The product builds it; the repo's commands do not decide it (the pattern of
`hive/smoke_package.sh:69-72`):

- a fresh directory with a copy of the checkout's tracked files at the commit under test,
  plus the uncommitted changes for native work; replay never builds in the person's working
  tree. `prepare` installs there with package-manager caches passed through; dependency
  folders the repo links in `.swarmai/pipeline.toml` `[worktree] link`
  (`core/run_worktree.py:47-52`) are reused;
- the environment emptied to a per-OS allowlist (`PATH`, a temp `HOME`, temp dirs, locale,
  proxy variables, package-manager cache variables; on Windows also `SystemRoot`, `windir`,
  `COMSPEC`, `PATHEXT`) plus the target's `env`; AWS credential files pointed at an empty
  file, `AWS_EC2_METADATA_DISABLED` set;
- free ports chosen by the tool, a port that already answers refused; each service in its
  own process group (a job object on Windows), readiness counted only from that group,
  cleanup kills the whole group; a launcher needing something absent (Docker, a database) is
  `not_run: needs <thing>`;
- removed after the run unless `--keep`.

### 6.3 What the sandbox contains, and what it does not

It keeps the person's credentials (environment, `HOME`, AWS files) and data folders away from
the replayed app, and keeps the **browser** off the network: Chromium is launched with a
proxy that refuses every connection (loopback is never proxied), so a page cannot reach any
other host, by name or by IP address; a stub is the way to give a page an outside response.
(DNS-only blocking was considered and rejected: a literal IP address bypasses it.) It does **not** contain the app's own
server-side network calls or the repo's own files (a repo-local `.env` is part of the
checkout) — exactly like running the repo's tests. This is said in the CLI's help and in the
skill.

**A machine with an instance metadata service** (a Hive, any cloud machine): the metadata
service hands the instance role's credentials (on a Hive: Bedrock) to any local process, and
an emptied environment does not stop that. So on a machine that needs it every process the
sandbox starts (prepare, setup, services, command walks, Playwright and its browser) runs as
the unprivileged **replay user** `swarm-replay`, through `sudo -n -u swarm-replay`, and only
after a probe run as that user fails to connect to 169.254.169.254 and [fd00:ec2::254] and
reaches the pinned runtime; otherwise every selected walk is `not_run: runtime unavailable`
and nothing of the repo starts (`core/feature_map/replay_user.py`). Which machines need it
is decided from fixed facts first, never only from a probe that a slow connect would turn
into "no": a Hive (`SWARMAI_MODE=hive`) and a Linux machine where the replay user exists
always do; elsewhere a TCP connect to the metadata service decides (a desktop: it does not
answer), and that "no" is never remembered. One replay at a time uses the replay user on a
machine (a lock its CLI process holds); every process of that user is stopped before the
replay starts (replay is refused if one survives) and again when its sandbox closes, so
nothing a replay started outlives it, also a process that cleared its environment. The host sets the user up as root:
`hive/replay-host.sh`, run by a Hive's first boot and every update, creates it (its own
home holds its package caches; traverse-only on the Hive user's home, so the tools that
user runs run for replay too), lets only the Hive user run commands as it (a visudo-checked
sudoers drop-in, no pty, no PAM session), and loads at every boot an nftables table that
rejects that uid's traffic to the two metadata addresses. The replay user can read what the
Hive user's home leaves readable to other users (the traverse it needs for the task's tools
and the pinned runtime) and write none of it: before this boundary the replayed app ran as
the Hive user itself. A separate uid is what holds: a
network namespace without network breaks prepare and the app's own calls, and a process
that stays the Hive user can ptrace the backend or rewrite the code it runs; a group
(`newgrp`) or a delegated cgroup can be left by the process itself. One start of the
sandbox is stopped by a spawn tag in its processes' environment (also one that left its
process group), all of them when the sandbox closes, and the sandbox's files are removed as
that user first. The sandbox folder is shared with that user, so once a process of it has
run, the Hive user's own process (which can reach the role) writes nothing there by a name
that process could have set up as a link: a spawn's environment file and the generated
Playwright config are new files (a random name, exclusive create, no link followed), each
log is opened once, exclusively, under the `logs` folder handle taken before the folder was
given away, a start state's folder and its fixture are made by the replay user, and `HOME`,
`TMPDIR` and the empty AWS file are made before anything runs; the proxy variables are not
passed to the replayed app (a proxy is a way around the firewall rule). Root runs a
root-owned copy of `hive/replay-host.sh` (`/usr/local/libexec/swarmai/`), taken on an update
from the checked package itself, never the copy in the install tree the Hive user owns. A
machine that does not need it (a desktop) runs the sandbox as the person, as before.

On Linux the pinned headless shell also needs system libraries and fonts (on Amazon Linux
2023 the package set in `hive/replay-host.sh`, found with `ldd` and a launch that renders
text); `runtime.ensure` names any missing library (`not_run: runtime unavailable`, never
a failed walk), and `hive/smoke_package.sh` starts the browser from the package it
installed.

### 6.4 Results

Per walk, from Playwright's JSON report: `pass`; `fail` (the failing step and Playwright's
error: expected vs received); `flaky` (Playwright's own outcome: failed, then passed on the
retry); `not_run` with a reason (bad file, runtime unavailable, dependencies missing, needs a
service, launch failed, not ready, port taken). A walk flaky 3 times in its last 10 replays
is **quarantined** until 5 greens in a row: it is still replayed and recorded (that is how
it earns the release), but its result never blocks and never sets the exit code; it is
told apart by its exact key, never by a title pattern. Retries never turn a result green: a
`flaky` result is reported as flaky.

Exit codes: 0 all replayed walks passed; 1 one failed; 3 none failed, one was flaky; 2
nothing could be judged.

Evidence per walk: Playwright's screenshots (one per test end, plus at failure), its trace on
failure, console errors, and the JSON result, kept under the ledger's evidence folder.

## 7. The ledger

`<app data>/feature_map/<brain>/<repo>/ledger.jsonl`, one line per walk result: feature,
walk (spec file + test title + viewport project), the walk's **content hash** (spec file +
its feature entry), commit, clean or dirty, the checkout it ran from, trigger (session,
pipeline run id, manual), result, duration, evidence path.

- Keyed by Brain and repo, not by checkout, so results from session worktrees and run
  worktrees reach one history.
- The content hash keeps an edited walk's history separate: an edited walk never inherits an
  old pass or an old quarantine.
- States are computed from it when asked; nothing stores a derived "verified" flag
  (PROJECT.md decision: *Feature Map reports facts*). `feature_map status` says, per walk,
  its last result, at which commit, and the commits since.
- Evidence is kept for the last 20 replays of each walk and for every current failure;
  older evidence is deleted, the line stays. Pipeline evidence is also copied into the run's
  records (§8). Brain delete moves the data with the Brain to `Projects/.trash`.

## 8. Where it plugs in

**Pipeline TEST.** The agent replays the reached features with
`feature_map replay --affected --run <run id>`. The decision does not rest on the agent's
report row (Brain checks are listed, never required, and their rows are the agent's,
`core/run_protocol/next_step.py:323-329`, `validation_report.py:285-287`): `checks.py`
reads the ledger for the run's current tree and the reached set it computes itself with the
same `affected` function:

- a walk that failed at the current tree keeps the run in TEST like a failed command
  (`checks.py:354-356`), unless it is `pre_existing`: it also fails at the run's base
  (replayed once in a base worktree, only for walks that failed at head) — listed, not
  blocking;
- a reached feature with no ledger line from this run at the current tree (the replay was
  not run since the last edit) keeps the run in TEST: running it is the agent's step. An
  environment problem is not this case: it records `not_run` lines with their reason,
  which are listed, never blocking;
- a feature folder the run made invalid (or facts that could not be computed) keeps the
  run in TEST as not decided;
- flaky and quarantined walks are listed, never blocking.

What this guards against is a missed or mistaken replay: the ledger is a local file, and an
agent with a shell could write it like any local record.

`next` at TEST names the `feature_map replay --affected` command when the run's repo has a
Feature Map. The run's evidence is copied into the run's records (the REPORT is a function
of the run log, `validation_report.py:265-270`), so a result card keeps its screenshots.

**Result card and REPORT.** A *Feature checks* section: each reached walk, its result, its
screenshots (both viewports), and the **changed expectations** — computed from the diff of
the feature folder (an `expect` call, a `reach`/`expect` field added, removed or changed),
never reported by the agent — so the person sees where the run changed what "working"
means.

**Native work.** After a `git commit` outside a pipeline run, an inject-only notice names
the features the session's commits reached since the last replay in that repo and the
command to replay them (`2026-11-23-feature-map-replay-conditions.md` §4); it never blocks.
`s_qa`'s visual step becomes `feature_map affected` + replay; the agent's
reply reports the results and the screenshots open in Canvas. A failing walk that is the
intended result of the change is updated in the same commit, and the commit message lists
the changed expectations.

**When a walk fails.** Decide first whether the app is broken or the walk is outdated, then
act; the tool never edits a walk, and a repair is a change a person can see. A target-wide
failure (most walks fail at their first step) is reported once as "the target did not come
up", not as N regressions.

## 9. Safety

- Nothing in the repo runs at bind or in any background job. Verify and replay run on
  request, with the trust of running the repo's tests.
- The product builds the sandbox (§6.2) and states what it contains and what it does not
  (§6.3).
- Prepare, service and setup commands are argv lists from the targets; a spec is the repo's
  own test code, run by Playwright in the sandbox exactly as a repo's own tests are; feature
  entries are data, never commands or instructions to the agent.
- Evidence stays on the machine, and in the records of the run that made it.

## 10. Built later, on a measured trigger

Each is designed in v2.1 (commit `d5e307895`) and is built when its trigger holds. The
trigger is read from the ledger and the correction records, not from a feeling.

| Part | What it adds | Trigger |
|---|---|---|
| **Features tab** in Brain Hub (`BrainHub.tsx:84-92` `TABS` made computed) | One page: what fails, since when, with screenshots; Replay, Fix, Retire | A repo has 15+ features, or the person asks to see the map |
| **Hive replay** — built (§6.3): AL2023 arm64 browser libraries and fonts by first boot and every update (`hive/replay-host.sh`); the replay user with the metadata service rejected; a Chromium launch in `hive/smoke_package.sh` | Swarm Out runs bring the same evidence back | Built: Swarm Out runs on repos with a Feature Map reported `not_run: runtime unavailable` |
| **Suggested features** in New Brain P2 and *Suggest features* | The map is not empty on day one for a new repo | The core is in use on 2+ repos and writing the first features by hand proves slow |
| **`cognition find` features leg** (`core/cognition.py:70-83`) | "the Pipeline view does not open" finds the feature to replay | A bug report was reproduced by hand although its feature existed |
| **Model-driven checks** (Playwright planner/healer, a model judging screenshots of new features) | Evidence for features not yet walked; repair proposals | Never as a gate; as evidence only, once the core's replay is steady |

Not built, by the person's decision (2026-11-23): a nightly replay and a Needs You item
for a red walk. Replay runs when code changes, and a red walk is the changing agent's
to fix in its own work; the person is not asked.

Outcome measure for the whole feature (Tenet T3), from the first day of the core: the
user-visible regressions a walk caught before the person reported them, against those the
person reported first. It decides whether the later parts are worth building.

## 11. Alternatives

| Option | Verdict |
|---|---|
| Our own step format (v2.1) | Cut: competes with Playwright for no gain and needs maintaining forever |
| Playwright with the repo's own setup only | Kept as an input (a repo's existing specs can be referenced from `features.yaml`), but as the only way it needs a JS test setup in every repo and gives no isolation |
| A SaaS E2E tool | The source of truth leaves the repo and the machine; the agent cannot use it offline |
| Per-element coverage reach | Cut: falls back to "all" in production builds and run worktrees |
| **Recommended** | Feature knowledge and the loop owned by SwarmAI; Playwright specs and a pinned Playwright for execution; reach by target; decisions from the ledger |

## 12. What it does not cover

Native UI (windows, menus, tray, native dialogs; mobile apps — the web layer of a Tauri or
Electron app is covered when it can be served in a browser); colour, contrast and overlap
with a native title bar; races that depend on timing; live third-party and server-side
dependencies (stubbed, so an outage is not detected); apps whose main paths sit behind an
external login with no test mode.

## 13. Decisions

- **D1. Where the folder lives**: §4.4, chosen once and recorded on the binding.
- **D2. Pipeline**: a failed reached walk keeps the run in TEST, decided from the ledger; a
  walk `not_run` for an environment reason is noted, never blocking (§8). Taken on the
  owner's go-ahead for the core loop (2026-11-19); a single switch reverts it to "listed
  only". Review of the build added: a reached feature never replayed on the current tree,
  and a feature folder the run made invalid, block too (otherwise the gate passes by not
  running the command); the switch covers them.
- **D3. Product rule text** ("before committing a change that reaches a target, replay its
  features") in AGENT.md through s_self-evolution, proposed once the core is in use.

## 14. Build order (the core) and verification

Readers before writers; each step lands with its tests.

1. **Spike**: the pinned runtime on macOS, Linux and Windows, and spec resolution of the
   pinned `@playwright/test` from a sandbox copy (§6.1). Everything else waits on it.
2. File schemas (`features.yaml`, targets), the location resolver and the binding field; the
   ledger and status.
3. Sandbox, prepare/services, start states, browser network limit; replay and verify;
   command walks; flaky and quarantine.
4. `affected` by target.
5. Pipeline: `next` names the command; `checks.py` reads the ledger; `pre_existing` by a
   base replay; evidence into run records; the result card and REPORT section with changed
   expectations.
6. `s_qa` visual step and the `feature_map` skill (writing standard).
7. Walks for SwarmAI's own main paths as the acceptance test: the nav to each overlay,
   Canvas opening a file and the batch review comments (the comment box in viewport in
   `compact`), Brain Hub opening a Brain, the ToDo view, a chat answer through a stubbed
   stream.

Invariant tests, chosen now:

1. A feature saved by `verify` replays green on the same commit; a red verify is not saved.
2. Renaming a test id a spec uses fails replay, naming the step, in both viewports.
3. A popover that fits 1440×900 but not 1024×640 fails `toBeInViewport({ ratio: 1 })` in
   `compact` only.
4. Runtime missing, dependencies missing, a needed service missing, launch failure, not
   ready, a taken port, a bad file: exit 2, `not_run` with that reason, never `fail`.
5. Fail-then-pass is `flaky` (exit 3), never `pass`; 3 in 10 quarantine; 5 greens release;
   a quarantined walk is replayed but never blocks; editing a walk starts its history over.
6. Specs resolve the pinned `@playwright/test` even when the repo has its own Playwright.
7. `affected`: a change under a target's root reaches all its walks and its dependents; a
   change under no root is `unplaced`, never "none"; untracked files count.
8. Sandbox: no variable outside the allowlist, a HOME inside the sandbox, an empty AWS
   credential file; a forking service dies with its group; the working tree is never
   written; a page request to a non-loopback host fails.
9. Pipeline: a failed reached walk keeps TEST red even when the agent's row says pass; a
   walk failing at base too is `pre_existing`; a reached walk with no ledger line is listed,
   not blocking; the result card keeps its screenshots after evidence pruning; changed
   expectations are listed from the diff.
10. Lines from a session worktree and a run worktree of one repo share one history.
11. A Brain with no code repo gets no check.

Review: the code gets correctness and security experts (it launches commands and builds an
environment), integration for the pipeline join, ux for the result card section.
