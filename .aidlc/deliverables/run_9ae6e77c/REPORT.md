# Commit gate: a single pathspec commit is gated only on its own files

Pipeline run `run_9ae6e77c` · profile: bugfix · project: SwarmAI

## TL;DR

The adversarial and active-run commit gates demanded review coverage for every file in
the staged index, even when the command was `git commit -- <paths>`, which commits only
those paths. With several sessions sharing one index, a file another session had staged
made a correct commit get denied. The gate now narrows to the literal paths for one
provable command shape and keeps the old, wider set for everything else.

## Intent → requirement

- Requested: make the gate's coverage check follow the paths a commit actually takes,
  without opening a new way for unreviewed files to be committed.
- Shipped scope (chosen by the user after the plan review blocked three times): narrow
  **only** when the whole command is exactly one `git commit` with known options and
  plain file paths. Directory and glob pathspecs, `cd`, chained commands, wrappers,
  `git -C`, env prefixes and `--amend` are deliberately **not** narrowed; they keep the
  wide set, so they behave as before.

## What changed

`backend/core/security_hooks.py` — `_pending_commit_paths` calls a new fast path,
`_single_commit_only_paths`, which returns the literal paths only when all hold:

- an operator-aware tokenizer finds a single command `git commit …` with no shell
  operator (so `-m fix& a.py`, `;`, `&&`, `|`, redirects are rejected);
- no command substitution, heredoc or here-string, except the
  `-m "$(cat <<'EOF' … EOF)"` message convention;
- no shell-expansion character in any argument, option values included;
- every option is an exact git-commit option (git accepts abbreviations such as
  `--tra` for `--trailer`, so unknown names are rejected), and none of
  `-a/-i/-p/--interactive/--pathspec-from-file/--amend`;
- each path is a plain path, not a directory or symlink, whose `git ls-files
  --full-name` match is exactly itself and whose `git ls-tree -r HEAD` match adds
  nothing else.

In the general walk: `-u` and `-S` no longer swallow the next token (a path after
`-u` used to be dropped), `-o` is value-less, and `--pathspec-from-file` sweeps the
working tree like `-a`.

`backend/tests/test_pending_commit_paths_only_mode.py` — new tests, including a
differential test that runs each command with real git and asserts that every
committed file is in the computed set (and equals it for fast-path commands).

## Verification

- New test file plus the commit-gate suites (`test_adversarial_commit_gate`,
  `test_active_run_commit_gate`, `test_reviewed_paths_multirepo`, `test_security_hooks`,
  `test_run_commit_d7`) pass; the change was also tested on HEAD with only this
  commit's hunks applied.
- Mutation check: reverting the operator-aware tokenizer turns 5 tests red, including
  two real-git differential cases.
- Not yet live: the daemon runs the packaged binary; this takes effect after rebuild
  and restart.

## Review

- The plan review (Gate 1) blocked three times, each time on a new way bash could commit
  files a narrowed set missed (`cd` before the commit, `cd` inside a substitution, an
  earlier command rewriting the file, a later unrecognised commit). That led to the
  single-command scope above.
- Code review and Gate 2 (correctness, security, integration, operational, performance,
  red team) found and fixed: operators glued to option values, globs in option values,
  unquoted heredoc bodies, quoted redirect-shaped file names, deleted-directory
  prefixes, a directory-to-file type change, and `--amend`. A widening added during
  review for an older gap was itself bypassable and was removed.

## Known gaps (pre-existing, not introduced here)

- The general path cannot see a second commit nested inside `$( )` or an unquoted
  heredoc body.
- The newline normaliser treats a bare CR as a command separator; bash does not.
- `cd other-repo && git commit` is evaluated against the hook's working repository.
- `git add new.py && git commit -m x` computes the staged set before `git add` runs.
- `GIT_INDEX_FILE` / `GIT_DIR` set in the shell are not modelled.
- The option table follows git 2.48; unknown options from other versions fall back to
  the wide set.

## Lessons

- Narrowing a fail-closed parser of shell text is a fail-open treadmill; what converged
  was an allowlist of one provable shape whose committed set git itself confirms.
- `shlex.split` splits only on whitespace; deciding "one command or several" needs an
  operator-aware tokenizer.
- Gaps that predate a narrowing belong in their own change, not the same diff.
- The structurally right layer is git: a pre-commit hook can read the exact committed
  set from the temporary index git builds for a pathspec commit.
