"""pipeline_command — the one place that recognises a pipeline command in a Bash
command line (design docs/design/2026-10-24-swarmai-usable-by-anyone.md §3.2, r14).

Two forms are recognised until S6-5 removes the second one:

- program form (the one the agent is told to run, packaged and dev alike):
  ``"$SWARM_BACKEND_BIN" pipeline-v1 <sub> ...`` — the program word is the
  ``$SWARM_BACKEND_BIN`` reference or a ``python-backend[.exe]`` path, the next word is
  ``pipeline-v1``.
- legacy script form: ``<python> [-flags] .../pipeline_v1.py <args>``.

Every other module asks here instead of matching ``pipeline_v1`` / ``pipeline-v1`` text
(structural test ``tests/test_pipeline_command.py``).

Public:
- ``mentions(command)`` — cheap text pre-filter: could the command hold a pipeline call.
- ``recognize(prog)`` — one unwrapped simple command's tokens -> its call dict or None.
- ``calls(command)`` — (calls, single) for a whole command line.
- ``program_word(base)`` — a program basename that runs a pipeline CLI / the backend.
- ``segment_mentions(tokens)`` — a simple command that names a pipeline CLI at all.
- ``last_call_offset(command)`` / ``is_create(command)`` / ``is_land(command)``.
- ``command_text(args)`` — the canonical command the agent is told to run.
- ``SUBCOMMAND``, ``ENV_VAR``.
"""
from __future__ import annotations

import os
import re
import shlex

SUBCOMMAND = "pipeline-v1"
ENV_VAR = "SWARM_BACKEND_BIN"
_ENV_REFS = frozenset({f"${ENV_VAR}", f"${{{ENV_VAR}}}"})
_PROGRAM_NAMES = frozenset({"python-backend", "python-backend.exe"})
LEGACY_SCRIPTS = {"pipeline_v1.py": "pipeline_v1"}
_TEXT_MARKERS = ("pipeline_v1", SUBCOMMAND)
_KEY_OPTS = ("--run-id", "--project", "--stage", "--status", "--session-id")
_SUBST = ("$(", "`", "<(", ">(")
# a call word, in plain text: `pipeline-v1` or `pipeline_v1.py`
_CALL_WORD_RE = re.compile(r"(?<![\w.-])(?:pipeline-v1|[^\s'\";&|()]*pipeline_v1\.py)(?![\w.-])")


def mentions(command: object) -> bool:
    return isinstance(command, str) and any(m in command for m in _TEXT_MARKERS)


def _is_program_word(word: str) -> bool:
    return word in _ENV_REFS or os.path.basename(word) in _PROGRAM_NAMES


def program_word(base: str) -> bool:
    """A program word (basename) that runs a pipeline CLI or the backend program."""
    return base.startswith("pipeline_v1") or base in _PROGRAM_NAMES


def segment_mentions(tokens: "list[str]") -> bool:
    return any(os.path.basename(t) in LEGACY_SCRIPTS or t == SUBCOMMAND for t in tokens)


def _opt_all(tokens: "list[str]", name: str) -> "list[str]":
    out = []
    for i, t in enumerate(tokens):
        if t == name and i + 1 < len(tokens):
            out.append(tokens[i + 1])
        elif t.startswith(name + "="):
            out.append(t.split("=", 1)[1])
    return out


def _opt(tokens: "list[str]", name: str) -> "str | None":
    values = _opt_all(tokens, name)
    return values[0] if values else None


def _ambiguous(rest: "list[str]") -> bool:
    """A key option given twice, or abbreviated (argparse would take the last one / a
    prefix, while the gates read the first exact one)."""
    seen: dict[str, int] = {}
    for t in rest:
        name = t.split("=", 1)[0]
        if name in _KEY_OPTS:
            seen[name] = seen.get(name, 0) + 1
        elif name.startswith("--") and len(name) > 2 and any(
                k.startswith(name) and k != name for k in _KEY_OPTS):
            return True
    return any(n > 1 for n in seen.values())


def _call(tool: str, rest: "list[str]", **extra) -> dict:
    sub = next((t for t in rest if not t.startswith("-")), None)
    return {"tool": tool, "sub": sub, "run_id": _opt(rest, "--run-id"),
            "project": _opt(rest, "--project"), "stage": _opt(rest, "--stage"),
            "status": _opt(rest, "--status"), "session_id": _opt(rest, "--session-id"),
            "ambiguous": _ambiguous(rest), "argv": list(rest),
            "run_ids": _opt_all(rest, "--run-id"), "projects": _opt_all(rest, "--project"),
            "session_ids": _opt_all(rest, "--session-id"), **extra}


_FOREIGN = {"tool": "foreign", "sub": None, "run_id": None, "project": None, "stage": None,
            "status": None, "ambiguous": False}


def recognize(prog: "list[str]") -> "dict | None":
    """The call an unwrapped simple command makes: a pipeline call dict, a ``foreign``
    dict when it names a pipeline CLI without running it (a script path that is only an
    argument is not a call), or None when it names none."""
    if not prog or not segment_mentions(prog):
        return None
    if _is_program_word(prog[0]) and len(prog) > 1 and prog[1] == SUBCOMMAND:
        return _call("pipeline_v1", prog[2:], form="program", program=prog[0], script=None)
    from core.security_hooks import _PY_INTERP_RE
    j = 1
    while j < len(prog) and prog[j].startswith("-") and prog[j] not in ("-c", "-m"):
        j += 1
    if (_PY_INTERP_RE.match(os.path.basename(prog[0])) and j < len(prog)
            and os.path.basename(prog[j]) in LEGACY_SCRIPTS):
        return _call(LEGACY_SCRIPTS[os.path.basename(prog[j])], prog[j + 1:],
                     form="script", script=prog[j], program=None)
    return dict(_FOREIGN)


def calls(command: str) -> "tuple[list[dict], bool]":
    """(calls, single). One dict per simple command that names a pipeline CLI. *single*
    is True iff the whole command is exactly one simple command with no substitution."""
    from core.security_hooks import _gate_shell_segments, _skip_leading_wrappers, _unwrap_program
    seps: list[str] = []
    segments = _gate_shell_segments(command, seps)
    if segments is None:
        return [], False
    single = len(segments) == 1 and seps == [""] and not any(m in command for m in _SUBST)
    out = []
    for seg in segments:
        call = recognize(_unwrap_program(seg))
        if call is None:
            continue
        lead = seg[:len(seg) - len(_skip_leading_wrappers(seg))]
        call["assigns"] = [t.split("=", 1)[0] for t in lead if "=" in t]
        out.append(call)
    return out, single


def _words(command: str) -> "list[tuple[int, str]]":
    return [(m.start(), m.group(0)) for m in _CALL_WORD_RE.finditer(command)]


def last_call_offset(command: str) -> int:
    """Offset of the last pipeline CLI word in *command*, -1 when there is none."""
    words = _words(command) if isinstance(command, str) else []
    return words[-1][0] if words else -1


def _sub_after(command: str, start: int) -> "str | None":
    """The first non-option word after the CLI word at *start*, up to the end of its
    simple command."""
    tail = re.split(r"[;&|\n]", command[start:], maxsplit=1)[0]
    try:
        toks = shlex.split(tail)
    except ValueError:
        toks = tail.split()
    return next((t for t in toks[1:] if not t.startswith("-")), None)


def is_create(command: object) -> bool:
    """True when the LAST pipeline CLI call in *command* is ``create``."""
    if not mentions(command):
        return False
    words = _words(command)
    return bool(words) and _sub_after(command, words[-1][0]) == "create"


def is_land(command: object) -> bool:
    """True when any pipeline CLI call in *command* is a landing (``land`` / ``run-land``)."""
    if not mentions(command):
        return False
    return any(_sub_after(command, start) in ("land", "run-land")
               for start, _word in _words(command))


def command_text(args: str) -> str:
    """The pipeline command the agent is told to run, the same packaged and in dev (P7)."""
    return f'"${ENV_VAR}" {SUBCOMMAND} {args}'.rstrip()


__all__ = ["ENV_VAR", "LEGACY_SCRIPTS", "SUBCOMMAND", "calls", "command_text", "is_create",
           "is_land", "last_call_offset", "mentions", "program_word", "recognize",
           "segment_mentions"]
