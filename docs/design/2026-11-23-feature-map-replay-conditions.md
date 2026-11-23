---
title: Feature Map — when a change is replayed
date: 2026-11-23
created: 2026-11-23
updated: 2026-11-23
project: SwarmAI
---

# Feature Map: when a change is replayed

Status: draft, reviewed once (2026-11-23). Extends `2026-11-18-feature-map.md` §5 (Reach)
and §8 (Where it plugs in). Product capability: every rule here works the same for every
user, repo, language and platform, and is decided only from what a repo declares in its
feature folder and from git.

## 1. Problem

Replay is the costly step of Feature Map: it builds the app in a sandbox, starts it, and
runs every selected walk in two viewports. Which changes replay which walks decides whether
the loop is affordable after every change.

Today (`backend/core/feature_map/reach.py:39-100`) the only unit of reach is the target:

- a changed path under a target's `root` or `paths` selects **every** feature of that
  target;
- a path under no target reaches every target rooted at the repo root;
- nothing a repo writes can say "this part of the tree does not change what a user sees",
  or "this feature only depends on this part".

A target is how the app is started (prepare, services, start states, §4.5), so a repo
cannot split its features across several targets without starting the same app several
times. In practice a repo has one target per app, and every code change replays the whole
app.

One sample shows the shape (SwarmAI's own map: one target over `desktop/` and `backend/`,
16 features, 31 walks): 248 of the last 300 commits reach the target and so replay all 31
walks; the walks alone took 168 s in one replay, before the build. This is one repo's map;
it shows the shape of the problem, not its size elsewhere.

The 2026-11-18 design chose target-level reach on purpose (§5: "Measured replay time, not
anticipation, decides whether a finer join is ever added"). Since then the person asked
that quality be guarded after every code change (「你把精力放在 改代码后守质量上」), so changes
outside a pipeline run are replayed too (§4), which multiplies the number of replays; and
that replay be conditional (「重放是有条件的 不能什么changes 都做」).

Not in scope, by the person's decision (2026-11-23): nightly replay and any Needs You item
for a red walk. A red walk is the changing agent's to fix in its own work.

## 2. Who meets it

| Who | Moment | What they get |
|---|---|---|
| The agent writing a map for any repo | Writes `features.yaml` and targets | Two optional fields, `ignore` on a target and `owns` on a feature, and the writing standard for them in the `feature_map` skill |
| The agent after a commit outside a pipeline run | `git commit` in a repo with a feature folder in the repo | One line in its context: which features the commit reaches and why (or none), and the command to replay them |
| A pipeline run (local or Hive) | TEST | The same reach, so fewer walks; FM-1 unchanged |
| The person | Judging the result | The same Feature checks section; nothing new to answer |

## 3. The rules

Each rule narrows only when the repo declared something; with nothing declared, the answer
is what it is today.

### 3.1 When: two moments only

1. **Pipeline TEST**, as today (design §8).
2. **A commit outside a pipeline run** (§4).

Nothing replays on a timer, on bind, or in a background job.

### 3.2 What counts as a change: a target's `ignore`

```yaml
# targets/app.yaml
root: desktop/
paths: [backend/]
ignore: ["desktop/src/**/__tests__/**", "backend/tests/**"]   # optional, glob, repo-relative
```

A changed path matching a target's `ignore` does not reach that target. Every answer
(`affected`, the notice, the run's Feature checks) lists the ignored paths by name, so a
wrong pattern shows on the next change that matches it. The product writes no pattern
itself: what is a test or a doc differs by language and by repo, so the repo declares it.

`ignore` is the one declaration that can hide a real change (a pattern that also matches
shipped files). The writing standard therefore allows only files that are not part of what
the target builds or serves: test files and test folders, and docs the app does not read.
§7 asks the person to accept this.

### 3.3 Which features: a feature's `owns`

```yaml
- id: chat-send-stream-h4kw
  target: app
  owns: [desktop/src/pages/chat/, backend/routers/chat.py]   # optional, repo-relative prefixes
```

Within a reached target, each changed path that reached it selects:

- every feature whose `owns` contains it;
- every feature with no `owns`;
- **every feature of the target, when no feature owns it.**

So a change narrows only when each of its paths is owned by some feature. A shared file
(an app shell, an API client) that no feature owns replays the whole target: it can break
any of them. A target reached only through `needs` (no changed path under it) selects all
its features. A change to the feature folder selects as today.

**Rot falls back to safe.** A declared prefix that matches no file in the checkout is listed
in the answer as `stale`, and is treated as not declared. A moved directory therefore
replays more, never less.

**`owns` is not part of a walk's identity.** The content hash (`files.py:326-340`) hashes the
feature entry; `owns` is left out of it, because it changes when a walk is selected, not
what the walk does. Tuning `owns` keeps every walk's history and quarantine.

**Schema.** A folder that uses `ignore` or `owns` declares `schema: 2`. A SwarmAI that reads
only schema 1 then refuses it with the existing "newer than this SwarmAI reads" message
(`files.py:176-180`) instead of "unknown key". Schema 1 folders read as today.

**The risk this accepts.** A feature that depends on a file another feature owns is not
replayed when only that file changes. Example: chat owns `desktop/src/pages/chat/`, and
another view renders a component from there. The writing standard says to own only what is
the feature's alone (its page, its route), never shared code; this is a rule for the
author, not a guarantee. §7 asks the person to accept this.

## 4. The moment outside a pipeline run

Today no code runs a replay outside an explicit CLI call: the pipeline engine names the
command at TEST (`core/run_protocol/next_step.py:363`) and reads the ledger
(`core/feature_map/pipeline.py:118-124`); outside a run, replay happens only if the agent
remembers the skills (`s_feature-map/INSTRUCTIONS.md:107-109`, `s_qa/INSTRUCTIONS.md:90-92`).

**Mechanism.** A PostToolUse hook on Bash, inject-only like the repository guidance hook
(`core/hook_builder.py:702-707`):

- **Trigger:** the parsed command is a `git commit` (also `git -C <dir> commit`), and that
  repo's HEAD moved during the call. A pull, checkout, reset or rebase is not a commit and
  gives no notice. A commit an agent's script makes without a `git commit` in its command
  is not seen; that is accepted (such a script lands work already committed elsewhere).
- **Reach:** the paths changed between HEAD before and after the call (`HEAD@{1}..HEAD`,
  so an amend reports what the amend changed), through the same `affected` with §3's rules.
  Never a merge-base with a remote: that would report other people's commits, and with no
  remote it would report nothing.
- **Within the task:** the hook keeps, in the session's memory, the features reached by
  this session's commits in that repo since its last `feature_map replay` or `verify`
  command there, and the notice names them all, so one replay at the end covers the task.
- **Line:** `Feature Map: this commit reaches 3 features (chat-send-stream-h4kw:
  desktop/src/pages/chat/ChatPage.tsx owned by it; …); 2 paths ignored (backend/tests/…).
  Replay before you report this work done: <command>` — or `reaches no feature (…)`.
- **Bounds:** two git calls and one YAML read; bounded to 3 s. On the bound or an error it
  says so in one line (`Feature Map: reach not computed (<reason>); run affected`), never
  silent, never blocks.
- **Not covered:** a feature folder that lives in the Brain (§4.4 of the 2026-11-18 design)
  changes without a commit, so it gets no notice; editing a walk is covered by `verify`, as
  today.

It never blocks a command or a commit (AGENT P7) and never starts a replay itself: the agent
replays once per task (AGENT § Tests). Rejected: replaying in a detached process on every
commit (several replays at once on a loaded machine, results nobody reads); refusing to
land a change with a red walk (a gate on native committing); reusing an earlier pass at the
same tree (it would need FM-1 to accept lines from outside the run, and the tree plus the
walk's hash do not cover the target file, its fixtures or the pinned runtime, so a stale
pass could be reused).

## 5. What changes, by file

| Part | Files | Waits for |
|---|---|---|
| P1. `ignore`, `owns`, schema 2, the selection rule, `stale` and `ignored` in the answer; `owns` out of the content hash | `core/feature_map/files.py`, `core/feature_map/reach.py` (`affected` gets the checkout root to test prefixes), its two call sites `scripts/feature_map.py:333` and `core/feature_map/pipeline.py:115`, tests | nothing |
| P2. The commit notice hook | new `core/feature_map/notice.py`, `core/hook_builder.py`, tests | P1 |
| P3. The writing standard for `ignore` and `owns` | `s_feature-map` skill | P1 |
| P4. SwarmAI's own map declares them (repo content, its own commit) | `.swarmai/features/` | P1 |

P2, P3 and P4 run at the same time once P1 lands. FM-1 and the engine are unchanged: they
read through `affected`.

The 2026-11-18 design is updated in place: §4.1 gets `owns`, §4.5 gets `ignore`, §5 states
the selection rule and points here, §8 "Native work" points to §4 here.

## 6. Verification

**Invariants (the final test set, written with P1-P2):**

1. A changed path owned by no feature selects every feature of its target.
2. A change narrows only when every one of its paths is owned.
3. A path matching a target's `ignore` does not reach it, and is listed as `ignored`.
4. A declared prefix that matches nothing is listed as `stale` and treated as not declared.
5. Changing `owns` does not change a walk's content hash; changing the walk does.
6. A schema-1 reader refuses a folder using `ignore` or `owns` with the newer-schema
   message.
7. The notice fires on `git commit` (and `git -C`), not on pull, checkout, reset or rebase;
   reports what the commit changed, accumulated until the next replay in that repo; never
   denies; says so in one line when it cannot compute.
8. The same map gives the same answer on a fixture repo with a non-web layout (a `cli`
   target over a Go-style tree): nothing depends on a language.
9. The existing Feature Map and FM-1 tests stay green unchanged.

Each fix gets a mutation check (remove it, its test goes red).

**Measured on more than one repo before P4 is accepted:** on SwarmAI's last 300 commits and
on the fixture repo's history, how many commits reach no feature and the mean walks per
commit, before and after the declarations. Whether the notice fires for a subagent's
commits is checked, not assumed.

**Reviews:** correctness on P1 (the selection may only narrow on a declaration); api-contract
on the schema change; integration on P2 (the hook chain and its bound).

**How it is built:** P1-P3 change one product module, its CLI, its skill and a hook every
session runs, so they go through one pipeline run (`full`), parts as its tasks. P4 is a
native change in the SwarmAI repo after it.

## 7. Decisions for the person

Both narrowing declarations trade certainty for fewer replays, and both are written by the
agent that writes the map:

- `owns`: a feature that depends on a file another feature owns is not replayed when only
  that file changes.
- `ignore`: a pattern that also matches a shipped file hides that change, though every
  answer lists it by name.

The alternative is computing each feature's dependencies from the code graph; the
2026-11-18 design rejected that (the graph does not see JSX or test ids, and lags a run's
worktree) and it is not proposed again. Recommended: accept both, with the fail-safe rules
above and the writing standard.
