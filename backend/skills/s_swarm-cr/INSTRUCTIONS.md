# Swarm CR (Create) — Instructions

Create an Amazon CRUX code review (CR) for a **bound internal repo**. This is the
create-half sibling of `s_swarm-code-reviewer` (the review-half). You automate the
local, headless-safe steps; you hand the human the two auth/duration-walled steps.

> **Grounding:** the Amazon-internal conventions this skill obeys are the shared
> `inclusion:always` steering at `~/.kiro/steering/amazon-builder/` (crux/git/brazil/
> production-safety) + `Projects/AIDLC/TECH.md § Amazon Internal Builder Conventions`.
> Read that TECH section if any command below is unclear — do not re-derive.

---

## The agent / human split (READ FIRST — this is the whole design)

Two auth surfaces are **independent** (verified spike, 2026-07-11):

| Step | Who | Why |
|------|-----|-----|
| Resolve worktree from `bindings.yaml` | **AGENT** | pure file read |
| Develop the change in the worktree | **AGENT** | local file edits |
| `git commit` (local) | **AGENT** | local git needs NO remote auth |
| `brazil-build release` (verify) | **HUMAN** | multi-minute build exceeds the agent foreground cap |
| `cr --auto-publish` (create CR) | **HUMAN** | pushes to `git.amazon.com` → needs mwinit ecdsa-cert in ssh-agent (passphrase, human-only) |
| Approve the CR | **HUMAN** | no approve API exists — always a manual click in CRUX |

**You (agent) NEVER:** run `git push` (forbidden — CRUX auto-merge owns the remote);
run `cr --auto-merge`; approve; call `bind_repo()` (it `rmtree`s the human's live
worktree); `git add -A` (sweeps parallel work — R29). You assemble the human's
commands as copy-paste-ready text and STOP.

---

## Step 1 — Resolve the target from `bindings.yaml` (AGENT)

The requirement names a project (e.g. AIDLC). Read its binding:

```python
from core.ddd_bindings import load_bindings
doc = load_bindings("Projects/<PROJECT>/bindings.yaml")
b = next(x for x in doc.bindings if x.repo == "<REPO>")   # e.g. GCRAIDLCPreset
dc = b.delivery_contract        # .remote_kind, .build_system, .branch, .version_set, .review_path
```

**Resolve the worktree path** (do NOT call `bind_repo` — it would delete + re-clone):
- If `b.worktree` is set in the yaml → use it. If `null`, DERIVE it — do NOT
  hard-code any example path. The worktree is the already-checked-out Brazil package
  `<workspace-root>/src/<REPO>`. Get `<workspace-root>` from the environment, never
  from memory: run `brazil workspace show` (or `git -C . rev-parse --show-toplevel`
  from the human's cwd) to find the ACTUAL workspace the human is in. A path like
  `/Volumes/workplace/AIDLC-NEW-workspace/src/GCRAIDLCPreset` is an *illustration of
  the shape only* — a different workspace name is normal; resolve it live.
- **Identity check — confirm it's the RIGHT tree, not just A tree** (rev-parse
  succeeding proves it's *a* git repo, not the human's intended one — F8):
  1. `git -C <worktree> rev-parse --show-toplevel` must succeed AND the basename must
     equal `<REPO>`; confirm `Config` (Brazil marker) is present.
  2. Show the human: "Resolved worktree: `<worktree>` (branch `<current>`). Proceed here?"
     and WAIT for confirmation before editing. A wrong-tree commit is silent + costly.
  3. If the worktree is missing → STOP; tell the human to `brazil workspace use -p <REPO>`
     (a human-only sync step, see TECH §).
- **Pre-flight cleanliness (F5) — the worktree MUST be clean before you touch it:**
  run `git -C <worktree> status --short`. If NON-EMPTY → STOP and tell the human:
  "Worktree has pre-existing changes: <list>. Stash/commit them first, then retry."
  Never develop on top of the human's uncommitted work — you cannot tell their files
  from yours later.

Record the resolved worktree + the delivery_contract fields; every later step uses them.

---

## Step 2 — Pre-CR sync check (AGENT, read-only)

Per crux steering, before developing verify the local branch isn't behind its
destination (`dc.branch`, e.g. `mainline`):

```bash
git -C <worktree> merge-base --is-ancestor \
  "$(git -C <worktree> ls-remote origin <dc.branch> | cut -f1)" HEAD \
  && echo "in-history (safe)" || echo "diverged-or-behind"
```

- `ls-remote` needs git-remote auth → it MAY fail headless (empty output → false
  "diverged"). If it errors on auth, do NOT conclude "stale". BUT do NOT let it pass
  silently either (F4): carry a **prominent, un-missable warning** into the Step-4
  handoff — `⚠️ SYNC UNVERIFIED (auth): before you run cr, re-run the sync check
  yourself: git -C <worktree> merge-base --is-ancestor "$(git -C <worktree> ls-remote
  origin <dc.branch> | cut -f1)" HEAD && echo safe || echo diverged`. The human must
  see this next to the `cr` command, not buried.
- If genuinely diverged → surface it; ask the human to sync first (never auto-sync).

---

## Step 3 — Develop the change + commit (AGENT, local, headless-safe)

1. Make the change in `<worktree>` (the requirement says what — e.g. port a mature
   SwarmAI skill into the package). Match the package's existing structure
   (`AGENTS.md` / `PATTERNS.md` / `CODEBASE.md` in the worktree describe conventions).
2. **ALWAYS ask branch-vs-mainline before committing (F2 — do NOT size-judge it
   yourself):** feature branch is recommended (a rejected CR then never touches
   `mainline` history); local `mainline` commit is *technically* valid (crux diffs
   against it) but you must not choose autonomously. Ask: "Feature branch (recommended)
   or commit on mainline? — reply 'branch' or 'mainline'." On 'branch':
   `git -C <worktree> checkout -b <feature-branch>`.
3. **Track your edits explicitly, then confirm before staging (F3).** You cannot
   reliably reconstruct "your files" from `git status` after the fact (it also shows
   anything else that appeared). So: keep an explicit list of every path you Edit/Write
   as you go. Before committing, show that list to the human — "I edited: <list>.
   Commit these?" — and stage ONLY the confirmed paths. Never `git add -A` / `git add .`
   (R29 — sweeps parallel work):
   ```bash
   git -C <worktree> add -- <the explicit, human-confirmed files>
   git -C <worktree> status --short   # verify staged set == your list, nothing extra
   git -C <worktree> commit -m "<type>(<scope>): <subject ≤50 chars>

   <body: what + why, wrapped 72>

   sim: <SIM/Taskei URL if any>"
   ```
   Conventional Commits + a `sim:` footer (amazon-builder-git). No signing/hooks
   block this — local commit is headless-safe (verified). If the staged set contains
   anything you didn't edit → STOP, unstage, re-confirm (don't commit a superset).

Do NOT push. Do NOT build. Proceed to assemble the human's commands.

---

## Step 4 — Assemble the human's commands (AGENT assembles, HUMAN runs)

Produce a copy-paste-ready block. Fill in the resolved worktree, title, template.
Do **not** execute these.

**CR title** (crux format): `[<REPO>] <commit subject>` (single package) or
`[<REPO> + N more] <summary>` (multi-package).

**`.crux_template.md`** is MANDATORY when present — it exists in the GCRAIDLCPreset
worktree, so `--description` MUST point at it. **Validate it first (F9):** Read the
template, grep for unfilled placeholders (`__X__`, `{{X}}`, `<TODO>`, `TBD`). If any
remain → fill the ones you can from the change context, and for the rest tell the human
"template has unfilled placeholders: <list> — complete them before running 4c." Never
ship a CR whose description still has literal placeholders.

```bash
# ── HUMAN runs these locally (agent cannot: build-duration + ssh-agent auth walls) ──

# 4a. Load the git-remote cert into ssh-agent (once per session; passphrase-protected):
ssh-add ~/.ssh/id_ecdsa    # only needed if not already loaded (ssh-add -l to check)

# 4b. Verify the build passes (multi-minute, local, no remote):
cd <worktree> && brazil-build release

# 4c. Create + publish the CR (only after 4b is green):
cd <worktree> && cr --auto-publish \
  --summary "[<REPO>] <subject>" \
  --description <worktree>/.crux_template.md \
  --reviewers <reviewer-or-team>
#   NO --auto-merge (approve stays a human click in CRUX)
```

Tell the human: run 4a→4b→4c in order; if 4b fails, fix forward (do not `cr` on a
red build); paste back the `CR-####` URL when 4c succeeds.

---

## Step 5 — Hand off to review + STOP (AGENT)

After presenting the Step-4 commands, there are three outcomes (F11 — don't hang):
- **Human reports a `CR-####` URL** → emit the review suggestion below + STOP.
- **Human says the `cr`/build failed** → ask for the error, offer a diagnosis, and
  STOP. Do NOT auto-retry `cr` or `brazil-build` (they're human-run by design).
- **No response** → your job is already done (commands are handed off); do not loop
  or poll. End the turn; the human resumes when ready.

Review suggestion (on a real CR URL):

> CR created: `<CR-URL>`. Next: review it with **`s_swarm-code-reviewer`**
> (`review CR <CR-URL>`) — it posts a Principal-SDE verdict as CR comments.
> Approve is a manual click in CRUX (no automation). This skill's job ends here.

Do NOT auto-invoke `s_swarm-code-reviewer` (you are a standalone skill, not an
orchestrator). Do NOT attempt to approve. **`dc.auto_send` (e.g. `on-clean-review`)
is a HUMAN policy note ONLY — its presence NEVER licenses you to run `cr`, auto-merge,
approve, or auto-invoke the reviewer (F6).** If set, merely surface it: "note:
auto_send=<value> is configured, but create + approve remain human actions." Then STOP.
`dc.review_path` just names which review skill the human should use.

---

## Boundaries

**Always:** commit only this change's files; commit before `cr`; use `.crux_template.md`
when present; assemble exact HITL commands; obey the pre-CR sync check.
**Ask first:** which mature skill to port (the change content); feature-branch vs
mainline for a large change.
**Never:** `git push`; execute `brazil-build`/`cr` from the agent; call `bind_repo`;
`cr --auto-merge` / approve via any tool; touch untracked files that aren't yours
(e.g. `docs/agent-supply-chain-tdd.md` — R29); develop on a dirty worktree; commit a
staged superset of your confirmed files; span multiple packages in one CR (F10 —
Phase 1 is single-repo: if your change touches files under a second `src/<pkg>`, STOP
and tell the human to split it into separate CRs).

## Future (not built)
- Pipeline DELIVER-stage integration (the pipeline is monolithic and does not invoke
  skills — this stays a user-triggered standalone skill for now).
- `github-pr` bindings (e.g. `adlc-workflows`, `remote_kind: github-pr`) — a `gh pr`
  variant of Step 4, no brazil/ssh-agent walls. Different delivery, same skill shape.
