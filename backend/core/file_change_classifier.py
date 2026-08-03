"""file_change_classifier — the unified Canvas file-change decision layer.

Cycle 2 of run_e626e121 (Canvas-trigger unification). Two pure functions that let
the streaming orchestrator decide, for each file an agent touches, (1) whether it
is worth surfacing and (2) which files a Bash command actually wrote — WITHOUT any
frontend string-parsing (the old, drifting MergedToolBlock PATH_PREFIXES path).

Design directives (from the user, 2026-08-03):
  #3 "别什么都 trigger 成噪音" → relevance is a WHITELIST, not a blacklist:
     ONLY a WRITE to a real deliverable auto-surfaces. Reads/greps/lists are
     `incidental` (they list in the rail but never pop the Canvas). Bookkeeping
     paths (.artifacts / .git / .context / dotfiles / tmp) are filtered entirely.
  conservative Bash parsing → a MISSED deliverable is far better than a FALSE pop,
     so parse_bash_write_targets deliberately UNDER-matches: it catches the common
     redirection/copy shapes and returns [] on anything it cannot parse confidently
     (heredoc bodies, command substitution, nested subshells, quoted '>').

Pure — no I/O, no imports beyond stdlib — so it is unit-tested exhaustively and
callable on the streaming hot path.
"""
from __future__ import annotations

import re
import shlex

__all__ = ["classify_relevance", "parse_bash_write_targets", "Relevance"]

Relevance = str  # 'deliverable' | 'incidental' | 'bookkeeping'

# ── Bookkeeping: never a user-facing deliverable, whatever the operation ──
# Mirrors the frontend isBookkeepingPath rule (CanvasOutputRail.tsx) so the two
# agree during the migration window; this is now the authoritative copy.
_BOOKKEEPING_DIRS = {".artifacts", ".git", ".context"}

# Operations that MODIFY a file (candidate for auto-surface). Everything else
# (read / searched / listed / …) is at most `incidental`.
_WRITE_OPS = {"written", "created", "edited"}


def _is_bookkeeping(path: str) -> bool:
    segments = path.split("/")
    base = segments[-1] if segments else ""
    if any(seg in _BOOKKEEPING_DIRS for seg in segments):
        return True
    if base.startswith("."):                       # .DS_Store, .eslintrc, dotfiles
        return True
    if path.startswith("/tmp/") or path.startswith("/private/tmp/"):
        return True
    if base.endswith(".tmp") or base.endswith("~"):
        return True
    return False


def classify_relevance(path: str, operation: str) -> Relevance:
    """Classify a touched file for Canvas surfacing (WHITELIST).

    - bookkeeping  → filtered entirely (never rail, never pop). Wins over all.
    - deliverable  → a WRITE (written/created/edited) to a non-bookkeeping file →
                     eligible to auto-surface.
    - incidental   → anything else (a read / grep / list of a real file) → lists
                     in the rail, never auto-surfaces.
    """
    if not path:
        return "bookkeeping"
    if _is_bookkeeping(path):
        return "bookkeeping"
    if operation in _WRITE_OPS:
        return "deliverable"
    return "incidental"


# ── Bash write-target parsing (conservative, under-match) ──
# Redirection: `> f`, `>> f`, `>f` (fd-number prefix like `2>` is EXCLUDED so
# `2>&1` / `2> /dev/null` never yield a deliverable). We require the char before
# `>` to NOT be a digit and NOT be `&`, and the target to not be an fd dup (`&1`).
_REDIRECT_RE = re.compile(r"(?<![\d&])>>?\s*([^\s;|&<>]+)")
# `tee [-a] FILE...` and `cp/mv SRC... DEST` handled via token walk.
_DISCARD_TARGETS = {"/dev/null", "/dev/stdout", "/dev/stderr"}


def _clean_target(tok: str) -> str | None:
    """A redirect/copy target is a real file iff it is not a discard sink."""
    if not tok or tok in _DISCARD_TARGETS:
        return None
    if tok.startswith("&"):          # fd dup, e.g. &1 in 2>&1
        return None
    return tok


def parse_bash_write_targets(command: str) -> list[str]:
    """Return the files a Bash command writes — conservatively.

    Catches: `>`/`>>` redirection (incl. no-space `x>y`), `tee [-a] f`, `cp/mv dest`.
    Returns [] (not a guess) for anything it cannot parse confidently — a missed
    deliverable is preferable to a false Canvas pop (directive). /dev/null and fd
    dups (`2>&1`) never count as writes.
    """
    if not command or not command.strip():
        return []

    targets: list[str] = []

    # 1) Redirection operators (regex on the raw string — robust to spacing).
    #    Skip anything inside single/double quotes by blanking quoted spans first,
    #    so `echo 'a > b'` does not register a redirect.
    unquoted = _blank_quoted(command)
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
