"""shell_segments — the one shell-command splitter the Bash judges share.

Extracted unchanged from ``security_hooks._is_irreversible_external_op`` /
``_segment_is_irreversible`` (C041) so a second reader of "what does this Bash command
run" (``core/turn_products.remote_action``, design
``docs/design/2026-10-26-session-products.md`` §2.2) reads it the same way instead of
growing its own parser that drifts.

Pure, no I/O. Accepted limits are C041's (documented there): a wrapper that takes its
own argument (``timeout 5 gh …``) and subshell grouping (``(git push)``,
``$(git push)``) are not recognised.

Public: ``split_segments``, ``join_continuations``, ``command_tokens``,
``git_subcommand_index``.
"""
from __future__ import annotations

import shlex
from typing import Optional

_SEPARATORS = frozenset({";", "&&", "||", "|", "&"})
#: characters after which a ``#`` starts a word, and so a comment
_WORD_BREAK = frozenset(" \t\n;&|()<>")
_WRAPPERS = frozenset({"sudo", "env", "command", "nice", "nohup", "time"})


def join_continuations(command: str) -> str:
    """*command* with each unquoted backslash-newline removed, as bash joins a
    continued line (``git push \\`` + newline + ``--force`` is ``git push --force``).

    Kept as it is (so the newline still separates): a backslash-newline inside single
    quotes or ``$'…'`` (literal there), inside double quotes (data either way), and
    after an unquoted ``#`` that starts a word on its line: that is a comment, which
    ends at the newline, and bash runs the next line as a new command. A ``#`` inside
    a word (``a#b``, ``$#``, ``${#a}``) starts no comment, so the line is joined.
    Quote state is tracked without heredocs: a quote char in a heredoc body can leave
    a later continuation unjoined, as it was before (shortcut: the heredoc-aware
    reader in security_hooks._normalize_newlines_quote_aware covers that case for the
    rm / discard / store / commit rules; reuse it here if a ``split_segments`` rule
    misses one)."""
    if "\\\n" not in command:
        return command
    out: list[str] = []
    quote = ""
    hashed = False
    i, n = 0, len(command)
    while i < n:
        c = command[i]
        if quote:
            if c == "\\" and quote != "'" and i + 1 < n:
                out.append(command[i:i + 2])
                i += 2
                continue
            if c == quote[-1]:
                quote = ""
            out.append(c)
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            if command[i + 1] == "\n":
                if not hashed:
                    i += 2
                    continue
                hashed = False      # the comment ends at this newline
            out.append(command[i:i + 2])
            i += 2
            continue
        if command.startswith("$'", i):
            quote = "$'"
            out.append("$'")
            i += 2
            continue
        if c in "'\"":
            quote = c
        elif c == "#" and (i == 0 or command[i - 1] in _WORD_BREAK):
            hashed = True
        elif c == "\n":
            hashed = False
        out.append(c)
        i += 1
    return "".join(out)


def split_segments(command: str) -> Optional[list[list[str]]]:
    """The command's segments (token lists), split on ``; && || | &`` and newlines
    (a backslash-newline continues the line, ``join_continuations``).
    None when shlex cannot tokenize it (the caller decides what unparseable means)."""
    # shlex treats a newline as plain whitespace, which would fuse a second line
    # into the first line's segment: normalize newlines to ';' first.
    command = join_continuations(command)
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
