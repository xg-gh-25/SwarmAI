"""Benchmark cases seeded from a repository's history (S1).

Design: docs/design/2026-11-18-expert-review-benchmark.md §3. No model is called here.

A **fix** F is a commit whose subject line matches a pattern and that changes both source
files and test files (``core.review_order.role``). For each one:

1. **SZZ** (``szz``): the lines F deleted or changed in its source files are blamed in
   F's parent; the commit B that wrote most of them is the change a reviewer should have
   caught it in. Kept only when B owns at least *owner_share* of the blamed lines and B's
   diff has at most *max_diff_lines* changed lines. A fix that only adds lines has
   nothing to blame (``add_only``). The defect lines are the line numbers blame reports
   in B's own version of the file, so commits between B and F cannot move them.
2. **Red/green** (``red_green``, when a test command is given): F's tree, exported as
   stored (``_export``); F's test files must pass there and fail once F's source files are put
   back to their F^ content. The test command is the repository's own (``{tests}`` in it
   stands for F's test files); it runs the repository's code, in a temporary directory,
   with a wall-clock cap that only guards against a hang.

``extract`` runs both over the history and returns the cases and a count per outcome, so
the yield of every filter is visible.

Public: ``Found``, ``szz``, ``red_green``, ``candidates``, ``extract``.
"""
from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

from core.review_order import role

DEFAULT_GREP = r"\bfix|修复|\bbug"
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@", re.M)
# The user's own git configuration (colour, textconv drivers) must not change what is read.
_GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_PAGER": "cat"}
_SHA = r"[0-9a-f]{40}(?:[0-9a-f]{24})?"     # SHA-1 or SHA-256


def _git(repo: Path | str, *args: str, binary: bool = False, ok: Sequence[int] = (0,),
         env: Optional[dict] = None, stdin: Optional[bytes] = None):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=120,
                       input=stdin, env={**os.environ, **_GIT_ENV, **(env or {})})
    if r.returncode not in ok:
        raise RuntimeError(f"git {' '.join(args[:3])} failed: {r.stderr.decode(errors='replace')[:300]}")
    return r.stdout if binary else r.stdout.decode("utf-8", "replace")


def _changed(repo: Path | str, commit: str) -> list[tuple[str, str]]:
    """(status, path) of every file *commit* changes against its first parent."""
    out = _git(repo, "diff-tree", "--no-commit-id", "-r", "--no-renames", "--name-status",
               "-z", f"{commit}^", commit)
    parts = [p for p in out.split("\0") if p]
    return list(zip(parts[0::2], parts[1::2]))


def _diff_lines(repo: Path | str, commit: str) -> int:
    total = 0
    for line in _git(repo, "diff", "--numstat", "--no-renames", "--no-textconv",
                     f"{commit}^", commit).splitlines():
        a, d, _p = (line.split("\t", 2) + ["", ""])[:3]
        total += (int(a) if a.isdigit() else 0) + (int(d) if d.isdigit() else 0)
    return total


def _old_lines(repo: Path | str, fix: str, path: str) -> list[int]:
    """Line numbers, in F's parent, of the lines F deleted or changed in *path*."""
    diff = _git(repo, "diff", "-U0", "--no-renames", "--no-ext-diff", "--no-textconv", "--no-color",
                f"{fix}^", fix, "--", path)
    lines: list[int] = []
    for m in _HUNK.finditer(diff):
        start, count = int(m.group(1)), int(m.group(2) or 1)
        lines += range(start, start + count)
    return lines


def _ranges(nums: Iterable[int]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for n in sorted(set(nums)):
        if out and n == out[-1][1] + 1:
            out[-1] = (out[-1][0], n)
        else:
            out.append((n, n))
    return out


def _blame(repo: Path | str, rev: str, path: str, lines: list[int]) -> list[tuple[str, str, int]]:
    """(commit, its file name, its line number) for each of *lines* of *path* at *rev*."""
    args = ["blame", "--porcelain"]
    for a, b in _ranges(lines):
        args += ["-L", f"{a},{b}"]
    out = _git(repo, *args, rev, "--", path)
    rows, current, names = [], None, {}
    for line in out.splitlines():
        head = line.split(" ")
        if len(head) >= 3 and re.fullmatch(_SHA, head[0]):
            current = (head[0], int(head[1]))
        elif line.startswith("filename ") and current:
            names[current[0]] = line[len("filename "):]
        elif line.startswith("\t") and current:
            rows.append((current[0], names.get(current[0], path), current[1]))
    return rows


@dataclass
class Found:
    """What SZZ found for one fix: B and its labels, or why there is no case."""
    reason: Optional[str] = None
    commit: Optional[str] = None
    share: float = 0.0
    labels: dict[str, list[tuple[int, int]]] = field(default_factory=dict)


def szz(repo: Path | str, fix: str, *, owner_share: float = 0.8,
        max_diff_lines: int = 800) -> Found:
    # only files F modified: a file F deleted would blame every line it had
    sources = [p for s, p in _changed(repo, fix) if s in ("M", "T") and role(p) == "source"]
    blamed: list[tuple[str, str, int]] = []
    for path in sources:
        lines = _old_lines(repo, fix, path)
        if lines:
            blamed += _blame(repo, f"{fix}^", path, lines)
    if not blamed:
        return Found(reason="add_only")
    counts: dict[str, int] = {}
    for commit, _p, _n in blamed:
        counts[commit] = counts.get(commit, 0) + 1
    owner, n = max(counts.items(), key=lambda kv: kv[1])
    share = n / len(blamed)
    if share < owner_share:
        return Found(reason="no_single_owner", share=share)
    parents = _git(repo, "rev-list", "--parents", "-n1", owner).split()[1:]
    if not parents:
        return Found(reason="root_commit", commit=owner, share=share)
    if len(parents) > 1:   # a merge: its "change" is the whole merged branch
        return Found(reason="merge_b", commit=owner, share=share)
    if _diff_lines(repo, owner) > max_diff_lines:
        return Found(reason="diff_too_large", commit=owner, share=share)
    by_path: dict[str, list[int]] = {}
    for commit, path, line in blamed:
        if commit == owner:
            by_path.setdefault(path, []).append(line)
    return Found(commit=owner, share=share,
                 labels={p: _ranges(ls) for p, ls in sorted(by_path.items())})


def _export(repo: Path | str, rev: str, dest: Path) -> None:
    """*rev*'s tree as stored, into *dest*. Not ``git archive``: it follows
    ``.gitattributes`` (``export-ignore`` leaves files out, ``export-subst`` rewrites them).
    No attribute applies either (they are read from the empty tree, ``GIT_ATTR_SOURCE``)
    and line endings are not converted, so neither the tree's own ``.gitattributes`` nor
    the repository's configured filters (a smudge command, git-lfs) run on the export."""
    dest.mkdir(parents=True, exist_ok=True)
    empty = _git(repo, "hash-object", "-t", "tree", "--stdin", stdin=b"").strip()
    with tempfile.TemporaryDirectory(prefix="expert-bench-index-") as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index"), "GIT_ATTR_SOURCE": empty}
        _git(repo, "read-tree", rev, env=env)
        _git(repo, "-c", "core.autocrlf=false", "-c", "core.eol=lf", "checkout-index", "-a", "-f",
             f"--prefix={dest}{os.sep}", env=env)


def _run(cmd: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    if not cwd.is_dir():
        return -2, f"no directory {cwd}"
    p = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout)
        return p.returncode, out.decode("utf-8", "replace")[-2000:]
    except subprocess.TimeoutExpired:   # a hang guard: a test run that never ends
        os.killpg(p.pid, signal.SIGKILL)  # its whole group: workers and servers it started
        p.communicate()
        return -9, "timed out"


def red_green(repo: Path | str, fix: str, test_cmd: Sequence[str], *, cwd: str = "",
              timeout: int = 300, red_pattern: Optional[str] = None) -> tuple[bool, str, str]:
    """(kept, reason, the red run's last output) for *fix*: its tests pass on its own
    tree and fail with its source files back at their F^ content. With *red_pattern*
    (the runner's own mark of a failed test, ``^FAILED `` for pytest) a red run must show
    it: tests that cannot even load on the old source (an import of what F added) prove
    no defect (``red_without_failed_test``)."""
    changed = _changed(repo, fix)
    tests = [p for s, p in changed if s != "D" and role(p) == "test"]
    sources = [(s, p) for s, p in changed if role(p) == "source"]
    if not tests:
        return False, "no_tests", ""
    rel = Path(cwd)
    shown = [os.path.relpath(t, rel) if cwd else t for t in tests]
    if "{tests}" in test_cmd:
        at = list(test_cmd).index("{tests}")
        cmd = [*test_cmd[:at], *shown, *test_cmd[at + 1:]]
    elif any("{tests}" in a for a in test_cmd):   # inside an argument, e.g. sh -c "... {tests}"
        cmd = [a.replace("{tests}", shlex.join(shown)) for a in test_cmd]
    else:
        cmd = [*test_cmd, *shown]
    with tempfile.TemporaryDirectory(prefix="expert-bench-") as tmp:
        tree = Path(tmp)
        _export(repo, fix, tree)
        rc, _out = _run(cmd, tree / rel, timeout)
        if rc != 0:
            return False, "not_green_at_fix", ""
        for status, path in sources:
            target = tree / path
            if status == "A":
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)   # F may have removed its folder
                target.write_bytes(_git(repo, "show", f"{fix}^:{path}", binary=True))
        rc, out = _run(cmd, tree / rel, timeout)
        if rc == 0:
            return False, "not_red_on_old_source", ""
        if red_pattern and not re.search(red_pattern, out, re.M):
            return False, "red_without_failed_test", ""
        return True, "", out


def candidates(repo: Path | str, *, grep: str = DEFAULT_GREP, max_files: int = 6,
               start: int = 0, limit: Optional[int] = None) -> list[str]:
    """Fix commits, newest first: a subject line matching *grep*, one parent, at most
    *max_files* changed files, among them a source file and a test file. *start* /
    *limit* take a slice of them."""
    pattern = re.compile(grep, re.I)
    out: list[str] = []
    log = _git(repo, "log", "--no-merges", "--format=%H%x1f%s%x1e")
    for rec in log.split("\x1e"):
        sha, _, subject = rec.strip().partition("\x1f")
        if not sha or not pattern.search(subject):
            continue
        try:
            files = _changed(repo, sha)
        except RuntimeError:   # the root commit has no parent
            continue
        roles = {role(p) for _s, p in files}
        if len(files) <= max_files and {"source", "test"} <= roles:
            out.append(sha)
            if limit is not None and len(out) >= start + limit:
                break
    return out[start:]


def _subject(repo: Path | str, commit: str) -> str:
    return _git(repo, "log", "-1", "--format=%s", commit).strip()


def extract(repo: Path | str, *, test_cmd: Optional[Sequence[str]], grep: str = DEFAULT_GREP,
            test_cwd: str = "", max_files: int = 6, owner_share: float = 0.8,
            max_diff_lines: int = 800, reviewed_pattern: Optional[str] = None,
            start: int = 0, limit: Optional[int] = None, timeout: int = 300,
            red_pattern: Optional[str] = None, skip: Iterable[str] = (),
            kept_b: Iterable[str] = (),
            on_outcome: Optional[Callable[[str, str, Optional[dict], str], None]] = None,
            ) -> tuple[list[dict], dict[str, int]]:
    """The S1 cases of *repo* and the count of every outcome (``candidates``, each SZZ
    reason, each red/green reason, ``duplicate_b``, ``error``, ``kept``). *on_outcome(fix,
    outcome, case, detail)* is called as soon as each candidate is decided, so a caller can keep progress
    that a stopped run would lose; *skip* names fixes already decided and *kept_b* the Bs
    already kept, so a resumed run keeps one case per B. A candidate that raises is
    recorded as ``error`` and the run goes on."""
    counts: dict[str, int] = {"candidates": 0, "kept": 0}
    cases: list[dict] = []
    seen_b: set[str] = set(kept_b)
    skipped = set(skip)
    reviewed = re.compile(reviewed_pattern, re.M) if reviewed_pattern else None

    def outcome(fix: str, what: str, case: Optional[dict] = None, detail: str = "") -> None:
        counts[what] = counts.get(what, 0) + 1
        if on_outcome:
            on_outcome(fix, what, case, detail)

    for fix in candidates(repo, grep=grep, max_files=max_files, start=start, limit=limit):
        if fix in skipped:
            continue
        counts["candidates"] += 1
        try:
            case = _case(repo, fix, test_cmd=test_cmd, test_cwd=test_cwd, owner_share=owner_share,
                         max_diff_lines=max_diff_lines, reviewed=reviewed, seen_b=seen_b,
                         timeout=timeout, red_pattern=red_pattern)
        except Exception as e:  # noqa: BLE001 -- one bad commit is recorded, never fatal
            outcome(fix, "error", detail=f"{type(e).__name__}: {str(e)[:300]}")
            continue
        if isinstance(case, str):
            outcome(fix, case)
            continue
        seen_b.add(case["head"])
        cases.append(case)
        outcome(fix, "kept", case)
    return cases, counts


def _case(repo: Path | str, fix: str, *, test_cmd, test_cwd: str, owner_share: float,
          max_diff_lines: int, reviewed, seen_b: set[str], timeout: int,
          red_pattern: Optional[str]) -> dict | str:
    """The case *fix* gives, or the name of the outcome that drops it."""
    found = szz(repo, fix, owner_share=owner_share, max_diff_lines=max_diff_lines)
    if found.reason:
        return found.reason
    if found.commit in seen_b:   # one case per B: a second fix of it is not a second case
        return "duplicate_b"
    red = ""
    if test_cmd:
        kept, reason, red = red_green(repo, fix, test_cmd, cwd=test_cwd, timeout=timeout,
                                      red_pattern=red_pattern)
        if not kept:
            return reason
    b = found.commit
    message = _git(repo, "log", "-1", "--format=%B", b)
    note = _subject(repo, fix)
    return {
        "id": f"s1-{b[:12]}", "source": "seeded", "repo": str(Path(repo).resolve()),
        "base": _git(repo, "rev-parse", f"{b}^").strip(), "head": b,
        "goal": _subject(repo, b), "labels": "partial", "confirmed": bool(test_cmd),
        "red_output": red, "diff_lines": _diff_lines(repo, b), "owner_share": found.share,
        "reviewed_before": bool(reviewed.search(message)) if reviewed else None,
        "defects": [{"path": p, "from_line": a, "to_line": z, "category": None,
                     "fix": fix, "note": note}
                    for p, spans in found.labels.items() for a, z in spans],
    }
