# DELIVER — what makes a Gate-2 review COUNT for the commit gate

Read when the card points here: before staging for review, when the adversarial commit
gate DENIES a commit after a review ran, or when you write a spawn prompt by hand. Two
mechanisms decide coverage, and neither is the file list you paste into the prompt.

## 1. Coverage = the working tree at SubagentStop, not the prompt's file list

`reviewed_paths` is written by `runtime_hooks.py` at SubagentStop from a mechanical
`git diff --name-only HEAD` over the working tree, and the gate requires
`pending_commit ⊆ union(reviewed_paths)` (`security_hooks.py`). Naming a file you never
opened does not widen coverage; omitting one does not narrow it.

UNTRACKED files are invisible to every `git diff` form. **`git add` your new files
BEFORE spawning the review, not after.** Reproduce with
`bash backend/scripts/check_review_scope.sh` — with `a.txt` modified and `b.txt` new:

| | untracked | after `git add b.txt` |
|---|---|---|
| `git diff --name-only HEAD` — the `reviewed_paths` source | `a.txt` | `a.txt b.txt` |
| `git diff --name-only --cached` — the pending-commit source | *(empty)* | `b.txt` |

A file left UNSTAGED while the review runs cannot enter the covered set; staging it for
the commit then puts it in `pending`, `pending ⊄ covered`, and the gate DENIES. Fail-opens:
`SWARM_ADVERSARIAL_GATE_FORCE=1` (operator-set, never yours); a `pending` git cannot
compute (including a repo with no commits, where `git diff HEAD` exits 128); a marker
whose `reviewed_paths` KEY is absent. An EMPTY `pending` also approves — not a bypass,
there is nothing to cover. A marker whose key is present but whose list is EMPTY bounds
coverage to the null set, so it makes a DENY more likely; a reviewer spawned in an
already-clean worktree produces that shape. Staging never leaves the local repo, so it
is free to do (STEERING #5).

**Staging is necessary, not sufficient.** `git diff HEAD` compares the WORKTREE to HEAD,
so a staged file whose worktree copy matches HEAD again drops out of `covered` while
staying in `pending`. All four reproduce in the script above:
- (a) staged-new, then deleted from the worktree
- (b) staged edit, then reverted to HEAD content
- (c) a staged MODE-only change (`chmod +x`, stage, then a formatter or checkout resets
  the bit). Requires `core.fileMode=true` (git's default; false on some Windows/CIFS
  checkouts, where this shape vanishes).
- (d) a staged symlink↔regular-file TYPE swap, reverted on disk. Survives
  `core.fileMode=false`, so it is the more robust of the two.

A staged DELETION and a RENAME are both fine. Rule of thumb: the reviewer must see the
change ON DISK, not merely in the index.

**`pending` is the WHOLE INDEX, not your run's files.** A parallel session's staged file
lands in `pending` too and demands coverage, so staging early widens the window in
which your file shares an index with another session's. Before spawning the review, run
`git diff --cached --name-only` and unstage anything that is not yours
(`git restore --staged -- <path>`, R29). `run-commit` itself does not sweep the index:
its `git add` AND its `git commit` are both pathspec-scoped to this run's
`files_touched` (`-- <files>`), so a foreign pre-staged file stays staged-but-uncommitted.
The concern here is the GATE's coverage math, not run-commit sweeping.

**A positional pathspec is added to `pending` even if the path does not exist.**
`git commit -m msg -- typo.txt` yields `pending = {…, typo.txt}`, which no review can
cover → DENY on a typo. If a commit is refused for a path you do not recognise, check
your own command line before re-running the review.

Coverage is also captured relative to ONE repo root (the reviewer's cwd). A change
spanning the source repo AND SwarmWS needs a reviewer per root, or the other repo's
paths are uncoverable however you stage.

Recorded case (generic): a new test file sat unstaged through the first review pass, so
it was both unread and uncovered. The pass that finally covered it ran a batch of
mutations and proved several of its tests vacuous — one data-loss guard's only writer
could be deleted with the suite still green. The gate did DENY, but only at commit
time, after a review round had been spent.

## 2. Whether a marker is written at all = the prompt's WORDING

Whether an `_adv_` marker is written is decided by
`_is_adversarial_intent(agent_type, "", prompt_head)`. With a generic `subagent_type`
(`general-purpose` for almost every real marker, `Explore` otherwise), no type matches
the adversarial-type regex, so classification falls back ENTIRELY to the prompt's words.
Measured against the live function:

| spawn prompt | marker written? |
|---|---|
| "You are an ADVERSARIAL reviewer…" | yes |
| "find bugs in the diff" | yes |
| "Please carefully review this changeset and tell me if it is correct." | **no** |
| "Check the diff for mistakes before I commit." | **no** |
| "find bugs in the **staged** diff" / "audit the **staged** diff" | **no** |
| "Prove this diff wrong." / "Try to falsify the claims" | **no** |

A thorough review phrased in ordinary language produces **zero coverage**, and the
commit is then DENIED for a review that happened. Two traps in the same classifier:
- **Keep the subject BARE.** The pattern wants `this diff` / `the diff`; one adjective in
  between breaks it. "prove wrong" / "falsify" are not in the vocabulary.
- **Front-load it.** The head is `content[:2000]` (`_read_subagent_prompt_head`), so
  adversarial vocabulary past ~2000 chars is never seen. Put it in the first sentence.

Safe vocabulary (verified against the live function): adversarial / refute / red-team /
poke holes / stress-test / try to break this / hunt for bugs / find bugs in the diff /
对抗 / 挑刺 / 找 bug. The shipped gate2 prompt already leads with it
(`tests/test_deliver_template_drift.py` pins that); this matters only for a prompt you
write yourself.
