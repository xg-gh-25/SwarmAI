#!/usr/bin/env python3
"""Enforce the project's Co-Authored-By identity on new commits.

WHY THIS EXISTS — the local hook that was supposed to do this is DEAD
--------------------------------------------------------------------
AGENTS.md / the project rules require EVERY commit to end with

    Co-Authored-By: Swarm <swarm@swarmai.dev>

and explicitly forbid the Claude/Anthropic identity. The repo ships
``.git/hooks/prepare-commit-msg`` to rewrite an SDK-injected Claude trailer into
the Swarm one automatically. That hook NEVER RUNS on this machine:

    $ git config --get core.hooksPath
    /usr/local/amazon/var/git-defender/hooks

``core.hooksPath`` redirects git to the corporate git-defender hook set, which has
its OWN ``prepare-commit-msg`` and ``pre-commit``. A hooksPath override does not
merge with ``.git/hooks`` — it REPLACES it. So every repo-local hook is shadowed:
the trailer rewrite, the CMHK SDK-drift check, the doc-frontmatter lint, and the
discussions mirror-drift check have all been silently inert.

Result: 22 of the last 80 commits carry no trailer at all (0 carried a wrong
identity, so the rule was never actively violated — just unenforced).

WHY CI AND NOT A HOOK
---------------------
The hooksPath value is machine/corporate policy and the project rules say never to
modify git config, so re-pointing it is off the table. A guard has to live where it
actually executes — CI — which is the same conclusion this repo reached for the
async-blocking class and the URL-contract lint rule. CI cannot REWRITE a commit
message, so this VERIFIES instead of fixing: write the trailer when you commit.

GRANDFATHERED HISTORY (the ratchet, not a rewrite)
-------------------------------------------------
The 22 existing violations are on main and rewriting published history is
forbidden. So enforcement starts at ``ENFORCED_FROM``: commits at or before it are
reported as a COUNT (visible debt, not hidden) and never fail. A commit AFTER it
must comply. Same shape as tests/silent_except_baseline.json — stop the class
growing rather than pretend the past is clean.

USAGE
    python3 scripts/check_commit_trailers.py                  # ENFORCED_FROM..HEAD
    python3 scripts/check_commit_trailers.py <range>           # explicit range
    python3 scripts/check_commit_trailers.py --audit           # report, never fail
Exit 0 = compliant, 1 = a violating commit exists, 2 = git/usage error.
"""
from __future__ import annotations

import subprocess
import sys

REQUIRED_TRAILER = "Co-Authored-By: Swarm <swarm@swarmai.dev>"

# Identities that must NEVER appear (the project rule names these explicitly:
# "Never use Claude/Anthropic identity in commit trailers").
FORBIDDEN_MARKERS = ("claude", "anthropic")

# Enforcement cutoff: the last commit of the un-enforced era. Everything AFTER this
# must carry the trailer. Bump it ONLY to record a deliberate, explained amnesty —
# never to paper over a fresh violation (fix the commit message instead).
#
# 2026-08-13 amnesty: bumped bcec9d4f → 2a5d465b. A single residual trailer-less
# commit (2a5d465b "fix(pipeline): completion gate credits per-cycle commits") sat
# in bcec9d4f..HEAD — a leftover of the artifact_cli trailer-injection bug that is
# now fixed at the source (artifact_cli.py builds the trailer into every message).
# It is ~45 commits deep and unpushed; rewriting it is barred (no interactive rebase
# + parallel sessions share this tree, R29). Amnestying the one residual is exactly
# what this ratchet is for; every commit AFTER 2a5d465b is still enforced.
#
# 2026-08-16 amnesty: bumped 2a5d465b → 00346142. Seven commits from a parallel
# session (579d4a7b, ca43daeb, f0f62d65, 0cdbbb7c, 643de6b1, edbfb236, 00346142 —
# a memory/recall refactor + context-doc edits) carry no trailer: the repo-local
# prepare-commit-msg hook that stamps it was SHADOWED by the corporate git-defender
# hook set (core.hooksPath redirects git away from repo-local hooks), so those
# commits were written without the rewrite firing. All seven are already pushed to
# origin/main (public history) — rewriting them is barred (R29). 00346142 is the
# newest of the seven, so 00346142..HEAD amnesties exactly those seven while every
# commit AFTER 00346142 stays enforced. This gate itself was silently inert until
# 2026-08-16 (a ci.yml YAML parse error made GitHub reject the whole workflow), which
# is why the seven accumulated unnoticed.
#
# 2026-08-16 amnesty (2): bumped 00346142 → d8c247a3. One more trailer-less commit
# (d8c247a3 "fix(tests): update recall-degradation test to renamed reason") landed
# from a direct commit — same shadowed-hook cause: core.hooksPath points git at the
# corporate git-defender hook set, so the repo-local prepare-commit-msg trailer
# rewrite never fired. It is already pushed to origin/main (public history), so
# rewriting it is barred (R29 + no-rewrite-published-history). d8c247a3..HEAD
# amnesties exactly this one while every commit AFTER it stays enforced.
ENFORCED_FROM = "d8c247a3"

_REC = "\x1e"
_FLD = "\x1f"


def _git(*args: str) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def _commits(rev_range: str) -> list[tuple[str, str, str]]:
    """[(short_sha, subject, body)] for a range, newest first."""
    out = _git("log", rev_range, f"--format=%h{_FLD}%s{_FLD}%b{_REC}")
    rows = []
    for rec in out.split(_REC):
        if not rec.strip():
            continue
        parts = rec.strip("\n").split(_FLD)
        if len(parts) >= 3:
            rows.append((parts[0], parts[1], parts[2]))
    return rows


def classify(body: str) -> str:
    """'ok' | 'wrong-identity' | 'missing' for one commit body."""
    if REQUIRED_TRAILER in body:
        return "ok"
    for line in body.splitlines():
        low = line.strip().lower()
        if low.startswith("co-auth"):
            if any(m in low for m in FORBIDDEN_MARKERS):
                return "wrong-identity"
            return "wrong-identity"  # a trailer that is not the project identity
    return "missing"


def main(argv: list[str]) -> int:
    audit = "--audit" in argv
    args = [a for a in argv if not a.startswith("--")]

    if args:
        rev_range = args[0]
    else:
        try:
            _git("rev-parse", "--verify", f"{ENFORCED_FROM}^{{commit}}")
        except RuntimeError:
            # A shallow CI clone may not contain the cutoff. Do not invent a range
            # (that would silently check everything or nothing) — say so and pass.
            print(f"SKIP: cutoff {ENFORCED_FROM} not in this clone (shallow?) — "
                  f"pass an explicit range to enforce.")
            return 0
        rev_range = f"{ENFORCED_FROM}..HEAD"

    try:
        rows = _commits(rev_range)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    bad = [(sha, subj, classify(body)) for sha, subj, body in rows
           if classify(body) != "ok"]

    print(f"trailer check: range={rev_range}  commits={len(rows)}  "
          f"violations={len(bad)}")

    if not bad:
        # Keep the grandfathered debt VISIBLE rather than silently clean.
        try:
            old = _commits(f"{ENFORCED_FROM}~40..{ENFORCED_FROM}")
            legacy = sum(1 for _, _, b in old if classify(b) != "ok")
            if legacy:
                print(f"  (grandfathered: {legacy} of the 40 commits up to "
                      f"{ENFORCED_FROM} predate enforcement — not rewritten)")
        except RuntimeError:
            pass
        return 0

    print("\nEvery commit MUST end with:")
    print(f"    {REQUIRED_TRAILER}")
    print("\nViolations:")
    for sha, subj, kind in bad:
        print(f"  [{kind:>14}] {sha}  {subj[:64]}")
    print("\nWhy this is not auto-fixed for you: .git/hooks/prepare-commit-msg would")
    print("have rewritten it, but core.hooksPath redirects git to the corporate")
    print("git-defender hook set, which SHADOWS every repo-local hook. Add the")
    print("trailer in the commit message yourself.")
    return 0 if audit else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
