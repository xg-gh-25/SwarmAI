"""shell_segments — the one shell-command splitter the Bash judges share.

Extracted unchanged from ``security_hooks._is_irreversible_external_op`` /
``_segment_is_irreversible`` (C041) so a second reader of "what does this Bash command
run" (``core/turn_products.remote_action``, design
``docs/design/2026-10-26-session-products.md`` §2.2) reads it the same way instead of
growing its own parser that drifts.

Pure, no I/O. Accepted limits are C041's (documented there): a wrapper that takes its
own argument (``timeout 5 gh …``) and subshell grouping (``(git push)``,
``$(git push)``) are not recognised.

Public: ``split_segments``, ``command_tokens``, ``git_subcommand_index``.
"""
from __future__ import annotations

import shlex
from typing import Optional

_SEPARATORS = frozenset({";", "&&", "||", "|", "&"})
_WRAPPERS = frozenset({"sudo", "env", "command", "nice", "nohup", "time"})


def split_segments(command: str) -> Optional[list[list[str]]]:
    """The command's segments (token lists), split on ``; && || | &`` and newlines.
    None when shlex cannot tokenize it (the caller decides what unparseable means)."""
    # shlex treats a newline as plain whitespace, which would fuse a second line
    # into the first line's segment: normalize newlines to ';' first.
    try:
        all_tokens = shlex.split(command.replace("\n", " ; ").replace("\r", " ; "))
    except ValueError:
        return None
    # shlex leaves a bare ';' glued to a word ('status;'); shlex already stripped
    # quotes, so a ';' surviving in a token is a real separator. Re-split it.
    resplit: list[str] = []
    for tok in all_tokens:
        if ";" in tok and tok != ";":
            parts = tok.split(";")
            for idx, p in enumerate(parts):
                if p:
                    resplit.append(p)
                if idx < len(parts) - 1:
                    resplit.append(";")
        else:
            resplit.append(tok)
    segments: list[list[str]] = []
    current: list[str] = []
    for tok in resplit:
        if tok in _SEPARATORS:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        segments.append(current)
    return segments


def command_tokens(tokens: list[str]) -> list[str]:
    """*tokens* from the real command word on: leading ``NAME=value`` assignments and
    benign wrapper words (``sudo env command nice nohup time``) dropped."""
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if "=" in t and t.split("=", 1)[0].replace("_", "").isalnum() and not t.startswith("-"):
            i += 1  # env assignment NAME=value
        elif t in _WRAPPERS:
            i += 1
        else:
            break
    return tokens[i:]


def git_subcommand_index(tokens: list[str]) -> int:
    """Index of the git subcommand in a ``git …`` token list, past the global options
    (``-C path``, ``-c k=v``, ``--git-dir=…``); ``>= len(tokens)`` when there is none."""
    k = 1
    while k < len(tokens):
        t = tokens[k]
        if t in ("-C", "-c", "--namespace", "--git-dir", "--work-tree", "--config-env",
                 "--exec-path", "--super-prefix"):
            k += 2  # option that consumes a value (given as the next token)
        elif t.startswith(("--git-dir=", "--work-tree=", "--namespace=", "--config-env=", "-C", "-c")):
            k += 1  # =form, self-contained
        elif t.startswith("-"):
            k += 1  # any other global flag
        else:
            break  # the subcommand
    return k
