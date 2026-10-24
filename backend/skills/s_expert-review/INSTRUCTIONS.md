# Expert review — native mode

You wrote and tested a change natively (no pipeline run). Before you commit it, decide
whether it needs review experts, and which. This is your judgment (AGENT.md R2, design
`2026-10-24-swarmai-usable-by-anyone.md` P6); nothing gates the commit. In a pipeline run
do NOT use this skill: the pipeline runs its own Gate-0 and Gate-2 review.

## 1. Decide who reviews

Read the diff, then pick from CATALOG.md:

| The change | Call |
|---|---|
| Ordinary logic of some size (more than a few lines of real code) | `correctness` |
| Auth, permissions, input validation, credentials, network, file paths, shell commands | `security`; add `red-team` when the risk is high or the diff is large |
| Concurrency, locks, async, retries, shared state | `concurrency` |
| A state machine or lifecycle | `state-machine` |
| A public interface, schema or data format | `api-contract` |
| Several modules, or callers changed | `integration` |
| A hot path or large data | `performance` |
| Deploy, config, logging, background jobs, recovery | `operational` |
| UI interaction | `ux-test` |
| A tiny change or docs only | usually nobody |

A repository's own guidance can ask for more (its AGENTS.md or `.swarmai/review.md`,
injected when you start working there): follow it in that repository. If the user said
not to review, do not.

## 2. Run them

One sub-agent per expert, in parallel, with the Agent tool:

- `subagent_type: "pipeline-expert"` (read-only tools, bounded effort); the prompt's
  first line is `EXPERT-ID: <id>`.
- Name the expert's prompt file by its absolute path: this skill's base directory joined
  with the relative path in CATALOG.md.
- Give the expert what it needs and nothing it does not: the repository root, the changed
  files (or the exact `git diff` command), what the change is for, and anything in
  `.swarmai/review.md`. Ask for findings with severity (CRITICAL / HIGH / MEDIUM / LOW),
  file:line, the failure it causes, and a fix.

Example prompt:

```
EXPERT-ID: correctness
Prompt file: <skill base dir>/../../experts/code/correctness.md
Repository: /path/to/repo   Diff: git -C /path/to/repo diff HEAD -- src/a.py src/b.py
Goal of the change: <one sentence>
Report findings as: SEVERITY | file:line | what breaks | fix. Native review, no pipeline run.
```

## 3. Handle findings

- A finding is a lead, not a verdict: check it against the code before acting.
- Fix what is real, re-run the affected tests, and decide whether the fix itself needs a
  second look (re-run the same expert on the new diff only when the fix was non-trivial).
- Two rounds at most. A HIGH still open after the second round means the design keeps
  producing new failure modes: stop, report it to the user, and ask whether the mechanism
  should exist at all, rather than patching a third round.
- Once one round ran and tests are green, the user saying "commit" ends the rounds.

## 4. Report

In your reply (and, briefly, the commit message): who reviewed, what they found, what
you fixed, and what risk remains open. If you decided no review was needed, say why in
one line.
