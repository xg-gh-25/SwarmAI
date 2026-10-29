---
created: 2026-10-30
title: "A dispatched task works on this machine's commit, with every repository of its Brain"
status: implemented 2026-10-30 (native, security ×2 + correctness reviewed)
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
4. **The Hive's daemon prepares the repository before the task starts**
   (`PUT /api/hive/repos`, `core/hive_repo_prepare`). **Git never runs in a directory the
   Hive's agent could have written**: the agent can write a repository's `.git` (config,
   `config.worktree`, alternates, a second `remote.origin.url`, `core.worktree`), and git
   runs configured commands and follows configured paths, so a list of risky keys always
   misses one (the first version had one; review found four ways around it).
   - History lives in a bare cache only the daemon writes, outside the workspace
     (`<data dir>/hive-repo-cache/<host>__<owner>__<name>.git`): cloned from the URL the
     first time (started empty when the bundle is the whole branch, no network), fetched
     from the URL when a thin bundle's prerequisites are missing; still missing → 409, and
     the host sends the whole branch once. The bundle's tip must be the declared commit.
   - A new directory next to the cache gets a clone of it with `--no-local` (a local
     clone hardlinks the object files, and the agent could then rewrite the cache's
     history through its own repository), the commit checked out on the branch and
     `origin` set to the URL; it is then moved into `Hive/repos/`.
   - Nothing in the workspace is reached by path after it is checked: the directories
     from the workspace down to `<owner>` are opened one by one without following a
     symlink (one in their place is refused) before anything is made, and both renames and
     the removal of an older `.previous` are relative to that open directory. A directory
     the agent swaps for a symlink while the history is fetched changes nothing outside.
     The cache is `<host>/<owner>/<name>.git` (never shared by two repositories) and
     must not be inside the workspace. What was
     there (the previous task's checkout, whose commits already went back with its turns)
     is kept as `<name>.previous`, replacing an older one.
   - Every call: fixed arguments, no shell, hooks off, no global or system config,
     submodules not followed, only https/ssh/file protocols, one deadline for the whole
     preparation (1000 s, shorter than the host's 1200 s wait). Uploads wait outside the
     workspace; git's own error text is logged on the Hive, never returned.
5. **The run starts from that commit and is measured from it**: the task text says the
   repository is ready (no clone, no pull); `repo_facts` takes the declared commit as the
   base. The commits the run makes come back as today (`hive_bundle` → `hive_return`, which
   merges against this machine's own merge-base).
6. **An older Hive** (no `/api/hive/repos`) is refused with "update the Hive".

## 3. Not changed

- What the Hive may do with the repository (commit on the declared branch, never push).
- How commits come back and land (`hive_return`).
- Concurrency: two tasks on one Hive with the same repository share its directory, as
  before (a second dispatch replaces the directory a running task works in). Known issue,
  not introduced here; a per-session directory would fix it.
- The commit is read again at dispatch: a commit made after the confirm card is the one
  that goes. The dispatch reply lists the `sha` of each repository sent.
- Same OS user: the Hive's agent and daemon run as one user, so this is not OS isolation.
  It guarantees that nothing the daemon runs reads, follows or deletes by path what the
  agent wrote. Left: the agent can move a directory of its own choosing into the
  `<name>.previous` slot, and the next preparation removes it. That is a removal the
  agent could make itself; it is listed so it is a decision, not a surprise.
- One binding whose checkout is not on a branch, or has no URL a Hive can clone, refuses
  every dispatch of that Brain (every repository goes, whatever the mode).

## 4. Verification

- Spec: mode `research` of a Brain with one binding lists that repository with its branch
  and commit.
- Real git, temporary repositories: a host with unpushed commits ships a thin bundle; the
  Hive side (a clone of the "remote" through `url.insteadOf`) ends on the declared branch
  at exactly the host commit; a remote without the base answers "need full", the full
  bundle then succeeds; a bundle whose tip is not the declared commit is refused.
- `repo_facts` with a declared commit measures from it.
- The agent's own `.git` is never run: a smudge filter in `config` and in `config.worktree`,
  `core.worktree` pointing elsewhere, a second origin URL with an `uploadpack` command:
  nothing runs, nothing outside is written. A symlink in place of the directory is
  replaced, never followed. A bundle header that never ends asks for the whole branch.
- The checkout shares no object file with the cache; a symlinked `Hive/repos` is refused
  with nothing made at its destination; a directory swapped for a symlink after the check
  leaves the outside untouched; a cache inside the workspace is refused.
- A research mode's section tells the Hive the repositories are there to read.
- Each fix reverted turns its test red (`scripts.mutation_check`).
