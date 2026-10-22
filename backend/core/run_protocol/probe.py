"""Safe deterministic probe protocol for Pipeline v1 (v13 §A2.5).

Public symbols:
- ``validate`` — validate one git grep/ls-files probe and normalize its shape.
- ``execution_spec`` — construct the isolated argv/env/resource contract.
- ``evaluate_result`` — map bounded execution facts to pass/fail/error.

This module never executes a command. Cycle 6/4 injects an argv-only subprocess runner.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

TIMEOUT_SECONDS = 30
STDOUT_LIMIT_BYTES = 256 * 1024
GREP_FLAGS = frozenset({"-n", "-l", "-L", "-c", "-I", "-i", "-w", "-v", "-E", "-F",
                        "--untracked", "--cached"})
LS_FLAGS = frozenset({"--cached", "--others", "--exclude-standard", "--error-unmatch"})
FORBIDDEN = frozenset({"-O", "--open-files-in-pager", "--no-index", "--ext-diff", "--textconv"})
TRUSTED_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


class ProbeError(ValueError):
    """A model-authored probe is outside the frozen safe subset."""


@dataclass(frozen=True)
class Probe:
    """One validated probe, safe to hand to ``execution_spec``."""

    subcommand: str
    options: tuple[str, ...]
    patterns: tuple[str, ...]
    paths: tuple[str, ...]
    expect_rc: int
    expect_stdout: str | None
    expect_lines: int | None


def _path(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProbeError("pathspec must be non-empty text")
    if "\\" in value or os.path.isabs(value) or value.startswith(":") \
            or (len(value) > 1 and value[1] == ":"):
        raise ProbeError("pathspec must be repo-relative without magic")
    if any(char in value for char in "?[]"):
        raise ProbeError("pathspec supports only * as a glob metacharacter")
    normalized = value.replace("\\", "/")
    if any(part == ".." for part in normalized.split("/")):
        raise ProbeError("pathspec must not traverse with ..")
    if "\x00" in value:
        raise ProbeError("pathspec contains NUL")
    return value


def _expect(value: Any) -> tuple[int, str | None, int | None]:
    if not isinstance(value, Mapping):
        raise ProbeError("expect must be an object")
    if set(value) - {"rc", "stdout", "lines"}:
        raise ProbeError("expect has unknown fields")
    rc = value.get("rc")
    if rc not in (0, 1) or isinstance(rc, bool):
        raise ProbeError("expect.rc must be 0 or 1")
    stdout, lines = value.get("stdout"), value.get("lines")
    if (stdout is None) == (lines is None):
        raise ProbeError("expect needs exactly one of stdout or lines")
    if stdout is not None and stdout not in ("empty", "nonempty"):
        raise ProbeError("expect.stdout must be empty or nonempty")
    if lines is not None and (not isinstance(lines, int) or isinstance(lines, bool) or lines < 0):
        raise ProbeError("expect.lines must be a non-negative integer")
    return rc, stdout, lines


def validate(argv: Sequence[str], expect: Mapping[str, Any]) -> Probe:
    """Validate model-authored argv; no shell, wrapper or git global option is allowed."""
    if not isinstance(argv, (list, tuple)) or any(not isinstance(x, str) for x in argv):
        raise ProbeError("argv must be a string array")
    if len(argv) < 4 or argv[0] != "git" or argv[1] not in ("grep", "ls-files"):
        raise ProbeError("only git grep and git ls-files are allowed")
    sub = argv[1]
    if any(token in FORBIDDEN for token in argv):
        raise ProbeError("probe uses an explicitly forbidden option")
    try:
        sep = argv.index("--", 2)
    except ValueError as exc:
        raise ProbeError("pathspecs must follow a -- separator") from exc
    before, after = list(argv[2:sep]), list(argv[sep + 1:])
    if not after:
        raise ProbeError("at least one pathspec is required after --")
    paths = tuple(_path(path) for path in after)
    options: list[str] = []
    patterns: list[str] = []
    if sub == "grep":
        i = 0
        while i < len(before):
            token = before[i]
            if token == "-e":
                if i + 1 >= len(before) or not before[i + 1] or "\x00" in before[i + 1]:
                    raise ProbeError("git grep -e requires a pattern that is non-empty and NUL-free")
                patterns.append(before[i + 1])
                options += ["-e", before[i + 1]]
                i += 2
                continue
            if token not in GREP_FLAGS:
                raise ProbeError(f"git grep option {token!r} is not allowed")
            options.append(token)
            i += 1
        if not patterns:
            raise ProbeError("git grep requires a pattern through -e")
    else:
        for token in before:
            if token not in LS_FLAGS:
                raise ProbeError(f"git ls-files option {token!r} is not allowed")
            options.append(token)
    rc, stdout, lines = _expect(expect)
    return Probe(sub, tuple(options), tuple(patterns), paths, rc, stdout, lines)


def execution_spec(probe: Probe, *, repo_root: str,
                   allowed_repo_roots: Sequence[str], temp_home: str,
                   temp_root: str) -> dict[str, Any]:
    """Return the exact isolated execution contract for a validated probe."""
    if not isinstance(probe, Probe):
        raise ProbeError("execution requires a validated Probe")
    if not isinstance(repo_root, str) or not os.path.isabs(repo_root):
        raise ProbeError("repo_root must be absolute")
    canonical_repo = os.path.realpath(repo_root)
    allowed = {os.path.realpath(root) for root in allowed_repo_roots
               if isinstance(root, str) and os.path.isabs(root)}
    if canonical_repo not in allowed:
        raise ProbeError("repo_root is not an allowed run repository")
    if not isinstance(temp_root, str) or not os.path.isabs(temp_root):
        raise ProbeError("temp_root must be absolute")
    canonical_temp = os.path.realpath(temp_root)
    if not os.path.isabs(temp_home) or not (
        os.path.realpath(temp_home) + os.sep
    ).startswith(canonical_temp.rstrip(os.sep) + os.sep):
        raise ProbeError("temp_home must be inside the trusted temp root")
    if not isinstance(TRUSTED_PATH, str) or not TRUSTED_PATH:  # frozen constant sanity
        raise ProbeError("trusted PATH is unavailable")
    argv = ["git", probe.subcommand, *probe.options, "--"]
    argv += [(":(glob)" + path) if "*" in path else (":(literal)" + path)
             for path in probe.paths]
    return {
        "argv": argv, "cwd": canonical_repo,
        "env": {
            "PATH": TRUSTED_PATH, "HOME": os.path.realpath(temp_home), "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C",
        },
        "stdin": "/dev/null", "shell": False,
        "timeout_seconds": TIMEOUT_SECONDS,
        "stdout_limit_bytes": STDOUT_LIMIT_BYTES,
    }


def evaluate_result(probe: Probe, *, rc: int | None, stdout: bytes,
                    timed_out: bool = False, overflow: bool = False,
                    tree_changed: bool = False) -> dict[str, Any]:
    """Evaluate bounded runner facts; integrity/resource failures are errors."""
    if not isinstance(probe, Probe):
        raise ProbeError("result requires a validated Probe")
    if not isinstance(stdout, bytes):
        raise ProbeError("stdout must be bytes")
    if timed_out:
        return {"result": "error", "reason": "probe timed out"}
    if overflow or len(stdout) > STDOUT_LIMIT_BYTES:
        return {"result": "error", "reason": "probe stdout exceeded 256 KB"}
    if tree_changed:
        return {"result": "error", "reason": "probe changed code_tree"}
    if not isinstance(rc, int) or isinstance(rc, bool):
        return {"result": "error", "reason": "probe returned no exit code"}
    if rc not in (0, 1):
        return {"result": "error", "reason": f"probe exited {rc}"}
    matched = rc == probe.expect_rc
    if probe.expect_stdout == "empty":
        matched = matched and not stdout
    elif probe.expect_stdout == "nonempty":
        matched = matched and bool(stdout)
    elif probe.expect_lines is not None:
        matched = matched and len(stdout.splitlines()) == probe.expect_lines
    return {"result": "pass" if matched else "fail",
            "reason": "expectation matched" if matched else "expectation did not match"}
