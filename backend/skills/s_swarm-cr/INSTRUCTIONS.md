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
- `b.worktree` is usually `null` in the yaml. When null, the worktree is the
  already-checked-out Brazil package under the human's workspace:
  `/Volumes/workplace/<WORKSPACE>/src/<REPO>`
  (for AIDLC/GCRAIDLCPreset → `/Volumes/workplace/AIDLC-NEW-workspace/src/GCRAIDLCPreset`).
- **Verify it exists and is that package** before proceeding:
  `git -C <worktree> rev-parse --show-toplevel` (must succeed) and confirm `Config`
  is present. If the worktree is missing → STOP and tell the human to
  `brazil workspace use -p <REPO>` (see TECH § — a human-only sync step).

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
  "diverged"). If it errors on auth, do NOT conclude "stale" — note that the sync
  check is a **human step** (`ssh-add ~/.ssh/id_ecdsa` then re-run) and continue;
  the human will re-verify sync before running `cr`.
- If genuinely diverged → surface it; ask the human to sync first (never auto-sync).

---

## Step 3 — Develop the change + commit (AGENT, local, headless-safe)

1. Make the change in `<worktree>` (the requirement says what — e.g. port a mature
   SwarmAI skill into the package). Match the package's existing structure
   (`AGENTS.md` / `PATTERNS.md` / `CODEBASE.md` in the worktree describe conventions).
2. **Optional feature branch** (recommended for a non-trivial change, keeps the CR
   clean): `git -C <worktree> checkout -b <feature-branch>`. Committing on `mainline`
   locally is *also* valid (crux treats `mainline` as the default destination; the CR
   diffs against it) — branch is a cleanliness choice, not a requirement. Ask the
   human which they prefer if the change is large.
3. Commit — **only this change's files**, never `git add -A` (R29):
   ```bash
   git -C <worktree> add -- <exact files you edited>
   git -C <worktree> commit -m "<type>(<scope>): <subject ≤50 chars>

   <body: what + why, wrapped 72>

   sim: <SIM/Taskei URL if any>"
   ```
   Conventional Commits + a `sim:` footer (amazon-builder-git). No signing/hooks
   block this — local commit is headless-safe (verified).

Do NOT push. Do NOT build. Proceed to assemble the human's commands.

---

## Step 4 — Assemble the human's commands (AGENT assembles, HUMAN runs)

Produce a copy-paste-ready block. Fill in the resolved worktree, title, template.
Do **not** execute these.

**CR title** (crux format): `[<REPO>] <commit subject>` (single package) or
`[<REPO> + N more] <summary>` (multi-package).

**`.crux_template.md`** is MANDATORY when present — it exists in the GCRAIDLCPreset
worktree, so `--description` MUST point at it (`--description <worktree>/.crux_template.md`,
filled in).

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

Once the human reports the `CR-####` URL, emit the review suggestion and stop:

> CR created: `<CR-URL>`. Next: review it with **`s_swarm-code-reviewer`**
> (`review CR <CR-URL>`) — it posts a Principal-SDE verdict as CR comments.
> Approve is a manual click in CRUX (no automation). This skill's job ends here.

Do NOT auto-invoke `s_swarm-code-reviewer` (you are a standalone skill, not an
orchestrator). Do NOT attempt to approve. `dc.review_path` names the review skill
(`s_swarm-code-reviewer`) and `dc.auto_send` is a *human* policy hint, not an
agent auto-action — surface it, never act on it.

---

## Boundaries

**Always:** commit only this change's files; commit before `cr`; use `.crux_template.md`
when present; assemble exact HITL commands; obey the pre-CR sync check.
**Ask first:** which mature skill to port (the change content); feature-branch vs
mainline for a large change.
**Never:** `git push`; execute `brazil-build`/`cr` from the agent; call `bind_repo`;
`cr --auto-merge` / approve via any tool; touch untracked files that aren't yours
(e.g. `docs/agent-supply-chain-tdd.md` — R29).

## Future (not built)
- Pipeline DELIVER-stage integration (the pipeline is monolithic and does not invoke
  skills — this stays a user-triggered standalone skill for now).
- `github-pr` bindings (e.g. `adlc-workflows`, `remote_kind: github-pr`) — a `gh pr`
  variant of Step 4, no brazil/ssh-agent walls. Different delivery, same skill shape.
