"""file_change_classifier — Bash WRITE/DELETE-target parser for the Canvas surfacing layer.

Originally the unified Canvas file-change decision layer (run_e626e121). As of
run_4de279ca the git-based `needs_human_review` became the sole surfacing authority
and the relevance/bookkeeping copy was retired; run_a18d69f5 then retired the Bash
WRITE-target parser too (the turn-end git sweep discovered writes author-agnostically
from `git status`). run_cce6f4b9 REVERSED that: the per-turn git sweep was the root
cause of the Canvas tab-isolation/trigger/cost regression, so live surfacing went back
to an EVENT-DRIVEN per-tool emit — which needs to know which paths a Bash command
WROTE. So this module owns BOTH parsers again:
  - parse_bash_write_targets: `>`/`>>`, `tee`, `cp/mv DEST` → the write emit.
  - parse_bash_delete_targets: `rm`, `mv SRC` → the operation=deleted rail-drop emit.
Both conservative (under-match HARD): a MISSED write/delete just skips one surface;
a FALSE one pops/removes a phantom row — strictly worse.

Pure — no I/O, no imports beyond stdlib — unit-tested, callable on the hot path.
"""
from __future__ import annotations

import re
import shlex

__all__ = [
    "parse_bash_write_targets",
    "parse_bash_delete_targets",
]

# run_4de279ca (Gate-2 F7): classify_relevance / _is_bookkeeping / _BOOKKEEPING_DIRS
# / _is_surfaceable_knowledge REMOVED. They were the SECOND copy of the machine-vs-
# human boundary (a hardcoded 3-dir denylist), and the whole-chain root fix collapses
# ALL surfacing decisions onto the ONE git-based authority `needs_human_review`
# (git check-ignore + tree-relative dot-scan + the surfaceable-knowledge allowlist,
# which now lives ONLY in needs_human_review). Both writes (turn-end sweep) and
# deletes (_build_file_delete_events) call that single authority. This module now
# owns ONLY the Bash write/delete-target PARSER (still needed to know which paths a
# Bash command deleted, for the operation=deleted emit). Pure, stdlib-only, hot-path.


# ── Bash target parsing (conservative, under-match) — WRITE + DELETE targets ──
# `>`/`>>` redirection (write, regex); `tee [-a] f` + `cp/mv SRC... DEST` (write dest,
# also del src) + `rm` (delete) handled via token walk. (run_cce6f4b9: _REDIRECT_RE +
# parse_bash_write_targets RESTORED — the event-driven per-tool emit needs the WRITE
# targets, the symmetric twin of the delete parser.)
_REDIRECT_RE = re.compile(r"(?<![\d&])>>?\s*([^\s;|&<>]+)")
_DISCARD_TARGETS = {"/dev/null", "/dev/stdout", "/dev/stderr"}


# Shell metacharacters that CANNOT appear in an UNQUOTED redirect/copy target —
# bash would word-split or syntax-error on them. Their presence means the token is
# NOT a real filename (e.g. `L4(top-right)` from a mis-parsed prose `>`), so it is
# rejected at the source (Layer 3, run_6ebe2d09). Legal filename chars — letters
# (incl. CJK/unicode), digits, `.`, `-`, `_`, `/`, `~`, `@`, `+`, `:`, `,`, `=`,
# `%`, `!` — are all allowed; only true shell metacharacters are banned. (`!` is
# NOT here: it is a literal in non-interactive shells — the runtime here — and a
# legal filename char; Gate-2 F5 flagged it as an over-rejection.)
_ILLEGAL_TARGET_CHARS = frozenset(" \t()[]{}*?|&;<>`#$'\"\\")


def _clean_target(tok: str) -> str | None:
    """A redirect/copy target is a real file iff it is not a discard sink AND does
    not contain shell metacharacters that can't appear in an unquoted filename."""
    if not tok or tok in _DISCARD_TARGETS:
        return None
    if tok.startswith("&"):          # fd dup, e.g. &1 in 2>&1
        return None
    # Layer 3: a bare word carrying a shell metacharacter is not a real unquoted
    # filename — drop it (kills `L4(top-right)`, globs, subshells, etc.).
    if any(ch in _ILLEGAL_TARGET_CHARS for ch in tok):
        return None
    return tok


def parse_bash_write_targets(command: str) -> list[str]:
    """Return the files a Bash command writes — conservatively (run_cce6f4b9 restore).

    Catches: `>`/`>>` redirection (incl. no-space `x>y`), `tee [-a] f`, `cp/mv dest`.
    Returns [] (not a guess) for anything it cannot parse confidently — a missed
    deliverable is preferable to a false Canvas pop (directive). /dev/null and fd
    dups (`2>&1`) never count as writes.
    """
    if not command or not command.strip():
        return []

    targets: list[str] = []

    # 1) Redirection operators (regex on the raw string — robust to spacing).
    #    Blank heredoc BODIES first (so a `>` inside <<EOF..EOF is not a redirect —
    #    Layer 2, run_6ebe2d09), then blank single/double-quoted spans (so
    #    `echo 'a > b'` does not register a redirect either).
    unquoted = _blank_quoted(_blank_heredocs(command))
    for m in _REDIRECT_RE.finditer(unquoted):
        t = _clean_target(m.group(1))
        if t:
            targets.append(t)

    # 2) tee / cp / mv via a best-effort token walk (shlex; bail on parse error).
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = []  # unbalanced quotes etc → rely on the redirect regex only
    for i, tok in enumerate(tokens):
        if tok == "tee":
            # tee [-a] FILE... → every following non-flag token is a write target
            for nxt in tokens[i + 1:]:
                if nxt.startswith("-"):
                    continue
                t = _clean_target(nxt)
                if t and t not in targets:
                    targets.append(t)
                break  # first file target is enough for surfacing
        elif tok in ("cp", "mv"):
            # dest is the LAST non-flag argument
            args = [a for a in tokens[i + 1:] if not a.startswith("-")]
            if len(args) >= 2:
                t = _clean_target(args[-1])
                if t and t not in targets:
                    targets.append(t)

    return targets


def parse_bash_delete_targets(command: str) -> list[str]:
    """Return the files a Bash command DELETES — conservatively (G1, run_5a7be540).

    Safe-direction, under-match HARD: a MISSED delete only leaves a stale rail row
    (== current behavior, no regression); a FALSE delete REMOVES a file from the
    rail that still exists on disk — strictly worse. So we reject anything we
    cannot resolve to a concrete, existing-file delete:

      - `rm FILE...`      → each plain filename arg (the SRC set)
      - `mv A B`          → A (the SRC of a 2-arg rename; the DEST B is caught as a
                            WRITE by parse_bash_write_targets, so rail shows B added
                            + A removed = the correct rename UI)

    REJECTED (return nothing for that command token):
      - `git rm` / `npm rm` / any `rm` that is NOT the command token (subcommand)
      - `rm -r` / `rm -rf` (recursive DIR delete — can't enumerate the files)
      - globs (`rm *.tmp`) — the real files are unknown at parse time
      - `mv` with >2 non-flag args or a `-t` target-dir flag (multi-source/dir dest)
      - anything inside a quoted span / heredoc body (blanked first, like the write parser)
      - shell-illegal target tokens (subshells, metachars) via _clean_target

    Pure, no I/O. Never raises.
    """
    if not command or not command.strip():
        return []

    # Same defence as the write parser: blank heredoc bodies + quoted spans so a
    # `rm` inside them is never treated as a command.
    unquoted = _blank_quoted(_blank_heredocs(command))
    try:
        tokens = shlex.split(unquoted)
    except ValueError:
        return []  # unbalanced quotes → refuse to guess

    targets: list[str] = []
    # Split into command segments on shell separators, so we only treat a token as
    # `rm`/`mv` when it is the COMMAND (segment head), never a subcommand
    # (`git rm`) or a bare word (`cat rmfile`).
    _SEPARATORS = {"&&", "||", "|", ";", "&"}
    segments: list[list[str]] = [[]]
    for tok in tokens:
        if tok in _SEPARATORS:
            segments.append([])
        else:
            segments[-1].append(tok)

    for seg in segments:
        if not seg:
            continue
        cmd0 = seg[0]
        rest = seg[1:]
        if cmd0 == "rm":
            # Reject recursive (dir) deletes — we can't enumerate the files.
            flags = [a for a in rest if a.startswith("-")]
            if any(("r" in f) or ("R" in f) for f in flags):
                continue
            for a in rest:
                if a.startswith("-"):
                    continue
                if "*" in a or "?" in a or "[" in a:  # glob → unknown files, reject
                    continue
                t = _clean_target(a)
                if t and t not in targets:
                    targets.append(t)
        elif cmd0 == "mv":
            # Only a simple 2-arg `mv A B` rename: A is the SRC (deleted). Reject a
            # `-t` flag (target-dir reordering) or a dir dest or >2 args (ambiguous).
            if any(a.startswith("-") for a in rest):
                continue
            args = [a for a in rest if not a.startswith("-")]
            if len(args) != 2:
                continue
            src, dest = args
            if dest.endswith("/"):  # dir dest → not a plain rename we track
                continue
            if any(g in src for g in ("*", "?", "[")):
                continue
            t = _clean_target(src)
            if t and t not in targets:
                targets.append(t)

    return targets


# A heredoc opener: `<<` optionally `-`, optional quote, a delimiter WORD, optional
# closing quote. The leading `(?<!<)` rejects `<<<WORD` (a herestring, NOT a heredoc
# body — Gate-2 F4: without it the regex matched the trailing `<<WORD` inside `<<<`).
_HEREDOC_OPEN_RE = re.compile(r"(?<!<)<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def _blank_heredocs(s: str) -> str:
    """Blank the CONTENTS of heredoc bodies (length-preserving), so a `>` inside a
    heredoc body is not mistaken for a shell redirect (Layer 2, run_6ebe2d09).

    Only the BODY is blanked — the opener line (which may carry a REAL trailing
    redirect, e.g. `cat <<EOF > real.html`) is left intact, and the closing
    delimiter line is preserved. Conservative safe-direction (design directive):
    if no closing delimiter is found, blank to end-of-string — over-blanking can
    only MISS a later redirect (a tolerated under-match), never produce a false pop.

    Handles `<<WORD`, `<<-WORD` (leading-tab close), and quoted `<<'WORD'`/`<<"WORD"`.
    The closing line is the delimiter alone (whitespace-trimmed; `<<-` also allows
    leading tabs). Multiple heredocs in one command are handled left-to-right.
    """
    if "<<" not in s:
        return s  # fast path — no heredoc possible
    lines = s.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)  # opener line kept intact (may carry a real redirect)
        m = _HEREDOC_OPEN_RE.search(line)
        # `<<<` is a herestring, not a heredoc — the regex won't match `<<<` because
        # after `<<` it needs an optional `-`/quote then a WORD, and `<` is neither.
        if m:
            delim = m.group(2)
            dash = line[m.start():m.start() + 3].startswith("<<-")
            i += 1
            # Blank body lines until the closing delimiter line (or end-of-string).
            while i < len(lines):
                body = lines[i]
                # Bash close rule: the delimiter must be ALONE on the line. For `<<-`
                # leading TABS are stripped first; for plain `<<` the line must equal
                # the delimiter EXACTLY (an indented `EOF` stays in the body — Gate-2
                # F3: `.strip()` here wrongly closed on an indented delimiter).
                closes = (body.lstrip("\t").rstrip() == delim) if dash else (body == delim)
                if closes:
                    out.append(body)  # closing delimiter line kept
                    break
                out.append(" " * len(body))  # blank the body (length-preserving)
                i += 1
        i += 1
    return "\n".join(out)


def _blank_quoted(s: str) -> str:
    """Replace the CONTENTS of single/double-quoted spans with spaces so quoted
    `>` is not mistaken for a redirect. Length-preserving; unbalanced quotes are
    left as-is (the shlex path already bails safely on those).

    Handles backslash-escaped quotes inside a double-quoted span (`"a\\"b"` — the
    escaped `"` does NOT close the span) so a real `>` after the true closing quote
    is still detected. Single-quoted spans do not process escapes (POSIX shell
    semantics: no escaping inside single quotes)."""
    out = []
    quote = None
    escaped = False
    for ch in s:
        if quote:
            if escaped:
                # inside "..."; previous char was a backslash → this char is literal
                out.append(" ")
                escaped = False
            elif quote == '"' and ch == "\\":
                out.append(" ")   # blank the backslash too (content, not delimiter)
                escaped = True
            elif ch == quote:
                quote = None
                out.append(ch)
            else:
                out.append(" ")
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
        else:
            out.append(ch)
    return "".join(out)
