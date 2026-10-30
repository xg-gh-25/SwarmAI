---
created: 2026-10-30
title: "A dispatched task works on this machine's commit, with every repository of its Brain"
status: implemented 2026-10-30, simplified the same day (r2: the daemon only keeps the bundle)
updated: 2026-10-30
---

# A dispatched task works on this machine's code, with every repository of its Brain

Status: implemented 2026-10-30 (native). Owner decision: a task goes out with its Brain and
Project Agent, and the repositories come from the Brain, not from the mode.

## 1. What was wrong (measured 2026-10-30)

| # | Problem | Evidence |
|---|---|---|
| P1 | The repositories a task gets came from `modes[].repos`. A mode without the field (SwarmAI's `research` and `release`) sent **no repository**: a research task about the code had no code. | `task_spec.build`: `for name in view["repos"]`; the SwarmAI spec for mode research printed `"repos": []`. |
| P2 | The Hive's agent cloned the binding's public URL at the delivery branch. A Brain whose commits land on local main (`remote_kind: self-hosted-main`) is never what GitHub has: here local main was 1421 commits ahead of GitHub and 15 behind. The Hive worked on GitHub's code, not the person's. | `git rev-list --count origin/main..main` = 1421, `main..origin/main` = 15. |
| P3 | The clone was an instruction to the agent ("clone it if the directory does not exist"), so which commit a run started from was whatever the agent pulled, and what the run changed was measured from a reflog guess. | `task_repos.task_text`, `_pulled_upstream`. |

## 2. The change

1. **Every repository of the Brain goes** (`bindings.yaml`), whatever the mode. A mode's
   `repos` still says where its setup and checks run (`check_sources`), and nothing else.
2. **The commit is this machine's.** For a binding with a local checkout, the spec records
   the checkout's branch and its HEAD commit, and how many files have uncommitted changes
   (they are not sent; the confirm card says so). A binding with no checkout here works as
   before: the Hive clones its URL at the delivery branch.
3. **The host ships the commit as a git bundle** (`core/repo_ship`): only what the remote
   does not have (`base..HEAD`, base = the merge-base with the checkout's tracking branch of
   that remote; 16 MB for the 1421 commits above), or the whole branch when there is no such
   branch. A bundle over 100 MB is refused, never cut.
4. **The Hive's daemon only keeps the bundle** (`PUT /api/hive/repos`,
   `core/hive_repo_inbox`): written as `Hive/incoming/<sha>.bundle` in the workspace the way
   a Brain's files are (`core/brain_sync`: directories opened without following a link, a
   fresh random name created exclusively, renamed into place), refused unless it starts
   with a git bundle header; regular bundle files older than two days are removed by name
   when the next one arrives. The daemon runs no git and reads nothing the agent wrote.
   The host takes the 200 only when it names that path and the byte count it sent. When
   the remote already has the commit, nothing is sent.
5. **The agent puts the repository on the commit itself**, from the commands the task text
   gives (`task_repos.task_text`, run from the workspace): clone the URL if the directory is
   missing, otherwise fetch origin (a whole-branch bundle can be cloned instead when the
   clone is refused); fetch the bundle; `checkout -B <branch> <sha>`. Its git runs as the
   agent, under its own gates, so anything planted in a `.git` reaches only the agent.
   `repo_facts` takes the declared commit as the base, and a declared commit that is not in
   the repository is reported as such (`ERR_NO_COMMIT`), never replaced by a guess from the
   remote. The commits the run makes come back as today (`hive_bundle` → `hive_return`,
   which merges against this machine's own merge-base).
6. **An older Hive** (no `/api/hive/repos`) is refused with "update the Hive".

## 3. Not changed

- What the Hive may do with the repository (commit on the declared branch, never push).
- How commits come back and land (`hive_return`).
- Concurrency: two tasks on one Hive with the same repository share its directory, as
  before. Known issue, not introduced here; a per-session directory would fix it.
- The commit is read again at dispatch: a commit made after the confirm card is the one
  that goes. The dispatch reply lists the `sha` of each repository sent.
- Which commit the run starts from depends on the agent following the commands; when it
  does not, the run's facts say so (`ERR_NO_COMMIT`, or a base other than the declared
  commit) instead of measuring from a guess.
- One binding whose checkout is not on a branch, or has no URL a Hive can clone, refuses
  every dispatch of that Brain (every repository goes, whatever the mode).

## 4. Why r1 was replaced

r1 had the Hive's daemon prepare the repository: a bare cache it owned, a `--no-local`
clone into a new directory, a move into `Hive/repos/` relative to directories opened
without following links, one deadline, a 409 asking for the whole branch. Two security
review rounds found five HIGHs, every one in that step: a daemon that runs git in, or
renames and deletes in, a tree the agent writes has to defend against what the agent
plants there. Before r1 the agent cloned on its own and none of that existed. r2 keeps the
bundle (the Hive still gets this machine's commit) and removes the step: about 270 lines
and the `.previous` slot gone. GitHub PRs were not taken as the default: 9 of the 12
bindings are internal (code.amazon) and SwarmAI's repository is public, so every dispatch
would push unreleased code and need the person's push approval; `delivery: branch-pr`
stays for the GitHub-hosted bindings.

## 5. Verification

- Spec: mode `research` of a Brain with one binding lists that repository with its branch
  and commit.
- Real git, temporary repositories, and the task text's own commands run as the agent
  would: a host with unpushed commits ships a thin bundle and the Hive repository ends on
  the declared branch at exactly the host commit, twice in the same directory; with no
  tracking branch the whole branch goes and the commands get there with the clone
  refused; with the commit already on the remote nothing is sent and they still get there.
- The Hive keeps exactly `Hive/incoming/<sha>.bundle`; a body that is not a bundle leaves
  no file; a linked `incoming/` is refused with nothing written where it points; old
  bundles are removed and a planted link is neither followed nor removed.
- `repo_facts` measures from the declared commit and reports one never fetched.
- The host refuses a 200 that does not name the kept bundle and its size.
- Each guard reverted turns its test red (`scripts.mutation_check`).
