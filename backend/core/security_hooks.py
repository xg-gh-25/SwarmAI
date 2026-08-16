"""Security hooks for the agent execution environment.

This module provides hook factory functions used by SessionUnit to enforce
security policies during agent execution.  Each hook is composed into the
Claude Agent SDK's hook system via HookMatcher configurations.

Public symbols
--------------
- ``pre_tool_logger``                        — logs every tool invocation
- ``DEFAULT_DANGEROUS_PATTERNS``             — default glob patterns for dangerous commands
- ``load_dangerous_patterns``                — load patterns from ~/.swarm-ai/dangerous_commands.json
- ``create_dangerous_command_gate``          — single PreToolUse gate for Bash commands
- ``create_file_access_permission_handler``  — workspace file-path sandbox
- ``create_skill_access_checker``            — skill allow-list enforcement
- ``create_governance_file_gate``            — Three-Layer Governance file write interception
- ``GOVERNANCE_TIER1_PATTERNS``              — Tier 1 (Constitutional) file patterns
- ``GOVERNANCE_TIER2_PATTERNS``              — Tier 2 (Statutory) file patterns
"""

import asyncio
import fnmatch
import json
import logging
import os
import re
import shlex
import subprocess
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

from config import get_app_data_dir
from . import executors
from .ask_question_manager import (
    TIMEOUT_SENTINEL as ASK_TIMEOUT_SENTINEL,
    ASK_ANSWER_TIMEOUT_SECONDS,
)

if TYPE_CHECKING:
    from .permission_manager import PermissionManager
    from .ask_question_manager import AskQuestionManager

logger = logging.getLogger(__name__)


async def pre_tool_logger(
    input_data: dict[str, Any],
    tool_use_id: str | None,
    context: Any
) -> dict[str, Any]:
    """Log tool usage before execution."""
    tool_name = input_data.get('tool_name', 'unknown')
    tool_input = input_data.get('tool_input', {})
    logger.info(f"[PRE-TOOL] Tool: {tool_name}, Input keys: {list(tool_input.keys())}")
    return {"decision": "approve"}


# ---------------------------------------------------------------------------
# Dangerous command gate — single permission layer
# ---------------------------------------------------------------------------

DEFAULT_DANGEROUS_PATTERNS: list[str] = [
    # NOTE: the blanket "rm -rf *" glob was removed (fix #3). A glob cannot
    # express "block / but allow /tmp", so recursive-rm danger is judged by the
    # fail-closed `_is_dangerous_rm` predicate below — which allows harmless
    # temp cleanups (/tmp, /var/folders) but blocks every other recursive rm.
    "sudo *",
    "chmod 777 *",
    "chmod -R 777 *",
    "chown -R * /",
    "kill -9 *",
    "mkfs.*",
    "dd if=*",
    "curl *|bash*",
    "curl *|sh*",
    "wget *|bash*",
    "wget *|sh*",
    "> /dev/sda*",
    "> /dev/hda*",
    "> /dev/nvme*",
    "> /dev/vda*",
    "> /etc/*",
    ":()*{*:*|*:*&*}*;*:*",
]

# Prefixes under which a recursive rm is considered harmless (OS temp dirs).
# Everything else is fail-closed dangerous.  macOS per-user temp lives under
# /var/folders (and the /private symlink); Linux/general temp is /tmp.
_SAFE_RM_PREFIXES: tuple[str, ...] = (
    "/tmp/",
    "/var/folders/",
    "/private/var/folders/",
    "/private/tmp/",
)
_SAFE_RM_EXACT: frozenset[str] = frozenset({"/tmp", "/private/tmp"})


def _is_dangerous_rm(command: str) -> bool:
    """Fail-closed predicate: is this a *dangerous* recursive ``rm``?

    Replaces the blanket ``rm -rf *`` glob (fix #3), which forced an approval
    prompt on every recursive rm — including harmless temp cleanups like
    ``rm -rf /tmp/build``.

    Rules:
    - Only ``rm`` invocations with BOTH recursive and force flags (``-rf`` /
      ``-r -f`` / ``-fr`` etc.) are candidates. Plain ``rm file`` is not the
      catastrophic pattern and is left to normal flow.
    - Dangerous UNLESS *every* path operand resolves under a known-safe temp
      prefix. Any operand that is a root/home/unknown/relative/glob target →
      dangerous (fail closed). Zero path operands → dangerous (e.g. ``rm -rf *``
      where the shell would expand cwd).
    - Non-rm commands always return False (other patterns judge those).
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        # Unparseable (unbalanced quotes) → cannot prove safe → fail closed,
        # but only if it actually looks like an rm command.
        return command.strip().startswith("rm ")
    if not tokens or tokens[0] != "rm":
        return False

    flags: set[str] = set()
    operands: list[str] = []
    for tok in tokens[1:]:
        if tok.startswith("-") and tok != "-":
            # collect single-letter flags (handles -rf, -fr, -r, -f, --recursive)
            if tok.startswith("--"):
                flags.add(tok)
            else:
                flags.update(tok[1:])
        else:
            operands.append(tok)

    recursive = ("r" in flags) or ("R" in flags) or ("--recursive" in flags)
    force = ("f" in flags) or ("--force" in flags)
    if not (recursive and force):
        # Not the `rm -rf` catastrophic shape — not this predicate's concern.
        return False

    if not operands:
        # `rm -rf` with no explicit target (or only globs the shell expands in
        # cwd) — cannot prove safe → dangerous.
        return True

    for op in operands:
        # Fail closed on any path-traversal or env/glob token — a ".." segment
        # can escape a safe prefix (rm -rf /tmp/../etc), and $VARS/globs are
        # unexpanded here so their real target is unknown.
        if ".." in op.split("/") or "$" in op or "~" in op:
            return True
        norm = os.path.normpath(op)
        if norm in _SAFE_RM_EXACT:
            continue
        if norm.startswith(_SAFE_RM_PREFIXES):
            continue
        # root, home, relative paths, globs, unknown absolute → dangerous.
        return True
    return False


# Raw-text destructive signatures, used ONLY as the fail-closed fallback when
# shlex cannot tokenize a command (e.g. an apostrophe in a comment). Matching
# the bare word "git"/"gh" here was too broad — it gated read-only `git log`
# whenever a script comment contained an apostrophe. These regexes look for an
# actually-destructive verb/flag in the raw string instead.
_IRREVERSIBLE_FALLBACK_RE = re.compile(
    # forced / deleting / history-rewriting push
    r"git\s+push\b[^\n;|&]*?(?:--force|--force-with-lease|--delete|--mirror|--prune|\s-\w*[fd])"
    # gh <noun> delete / delete-asset
    r"|gh\s+\S+\s+delete(?:-asset)?\b"
    # gh repo edit … --visibility (clears stars, irreversible — C041)
    r"|gh\s+repo\s+edit\b[^\n;|&]*?--visibility"
    # gh api destructive REST method or visibility field
    r"|gh\s+api\b[^\n;|&]*?(?:-X\s*(?:DELETE|PATCH|PUT)|--method[ =]\s*(?:DELETE|PATCH|PUT)|visibility=)",
    re.IGNORECASE,
)


def _is_irreversible_external_op(command: str) -> bool:
    """Fail-closed predicate: is this an IRREVERSIBLE destructive EXTERNAL op?

    C041 (2026-06-27): an inference-driven ``gh repo edit … --visibility private``
    on the public product repo wiped 209 GitHub stars — irreversible, no undo. The
    ``dangerous_command_gate`` had zero coverage for external ops (it guarded only
    local ``rm -rf`` + a glob list). This predicate flags the irreversible-external
    class so the SAME approval/auto-deny flow blocks them pending an explicit
    sign-off — never on inference alone.

    Token-aware (``shlex``), NOT glob: a glob cannot distinguish ``git push origin
    :branch`` (delete) from ``src:dst`` (normal), ``-f`` from a ``feature-f*`` branch
    name, or ``--force-with-lease`` from a safe push (skeptic-proven, run_73a54e70).

    Flags True for:
    - ``gh repo edit … --visibility[=…]`` (visibility toggle clears stars)
    - ``gh repo delete …`` / ``gh release delete …``
    - ``git push`` with force (``--force``, ``--force-with-lease``, ``-f`` incl.
      bundled short flags like ``-uf``) — all rewrite remote history
    - ``git push`` remote-branch delete (``--delete``/``-d`` flag, OR a colon-refspec
      ``:branch`` with an empty left side)

    Non-gh / non-git-push commands always return False (other patterns judge
    those). Unparseable (shlex ValueError) on a command mentioning gh/git → True
    (fail closed). Everything else under gh/git push that is not in the
    irreversible set → False (safe daily ops are not blocked).

    Gate-2 hardened (run_73a54e70) against: git global flags shifting the
    subcommand (``git -C p push -f``), ``+refspec`` force push, ``--mirror``/
    ``--prune`` ref-deleters, the ``gh api`` REST equivalent of the visibility
    toggle, env-var prefixes (``GH_TOKEN=x gh repo delete``), other destructive
    gh verbs (``secret delete``, ``delete-asset``), and ops chained after a
    benign command (``git status && git push -f``) including newline-separated.

    ACCEPTED RESIDUALS (Gate-2 2nd pass, LOW — documented not hidden, same posture
    as ``pytest_command_guard``'s indirect-invocation leak): a wrapper that takes
    its own argument (``nice -n 10 gh repo delete``, ``timeout 5 gh …``) and
    subshell/command-substitution grouping (``(git push -f)``, ``$(git push -f)``)
    are NOT recognized — they need a real shell parser. The fail-closed
    ``mentions_target`` arm only catches UNPARSEABLE input; these parse cleanly.
    """
    # NOTE: unparseable input (shlex ValueError) is handled in the `except`
    # below via _IRREVERSIBLE_FALLBACK_RE. We deliberately do NOT fail closed on
    # the mere presence of "git"/"gh" — that gated benign reads like `git log`
    # whenever a script comment contained an apostrophe (`Find ws_path's repo`).
    # shlex does NOT treat ';' as a standalone token unless it is space-padded
    # (it leaves `status;` glued). Pad bare ';' so segment-splitting sees it —
    # but NOT a ';' inside quotes (a -m message). Quote-aware pad: only outside
    # quoted spans. Cheap approach: shlex first, then also split each token on
    # a trailing/leading ';'. Simpler + correct: pad ';' that is not quoted.
    # Newlines are shell separators too (multi-line Bash: heredocs, `&&\n`
    # chains). shlex treats `\n` as plain whitespace, which would fuse a
    # second-line destructive op into the first line's segment (Gate-2 N1,
    # a fix-induced bypass — PIT56). Normalize raw newlines to `;` separators
    # BEFORE tokenizing. A newline inside a quoted message is rare and at worst
    # splits the (benign) message into two non-destructive segments.
    try:
        all_tokens = shlex.split(command.replace("\n", " ; ").replace("\r", " ; "))
    except ValueError:
        # Unparseable input cannot be tokenized for precise judgment. Fail
        # closed ONLY when a destructive signature is literally visible in the
        # raw text — not on the bare presence of "git"/"gh" (PIT: an apostrophe
        # in a comment, `Find ws_path's git repo`, made shlex choke and flagged
        # a read-only `git log`).
        return bool(_IRREVERSIBLE_FALLBACK_RE.search(command))
    if not all_tokens:
        return False
    # Re-split any token that carries a glued ';' (e.g. 'status;') into the word
    # and a standalone ';' separator. shlex already stripped quotes, so a ';'
    # surviving in a token is a real shell separator, not message text.
    _resplit: list[str] = []
    for tok in all_tokens:
        if ";" in tok and tok != ";":
            parts = tok.split(";")
            for idx, p in enumerate(parts):
                if p:
                    _resplit.append(p)
                if idx < len(parts) - 1:
                    _resplit.append(";")
        else:
            _resplit.append(tok)
    all_tokens = _resplit

    # Split into segments on shell separators so a destructive op chained after
    # a benign command (`git status && git push -f`, `x; gh repo delete y`) is
    # evaluated on its own. shlex keeps `;`/`&&`/`|` as standalone tokens.
    _SEPARATORS = {";", "&&", "||", "|", "&"}
    segments: list[list[str]] = []
    current: list[str] = []
    for tok in all_tokens:
        if tok in _SEPARATORS:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        segments.append(current)

    return any(_segment_is_irreversible(seg) for seg in segments)


def _segment_is_irreversible(tokens: list[str]) -> bool:
    """Judge ONE command segment (already split on shell separators)."""
    # Strip leading env-assignments (NAME=val) and benign wrapper words so the
    # real command word is found: `GH_TOKEN=x gh repo delete`, `sudo gh …`,
    # `env A=b git push -f`, `command git push -f`.
    _WRAPPERS = {"sudo", "env", "command", "nice", "nohup", "time"}
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if "=" in t and t.split("=", 1)[0].replace("_", "").isalnum() and not t.startswith("-"):
            i += 1  # env assignment NAME=value
        elif t in _WRAPPERS:
            i += 1
        else:
            break
    tokens = tokens[i:]
    if not tokens:
        return False

    # ── gh family ──
    if tokens[0] == "gh":
        sub = tokens[1:]
        if not sub:
            return False
        noun = sub[0]
        rest = sub[1:]
        # gh api — the REST surface. DELETE/PATCH/PUT methods, or any field
        # setting visibility, are the irreversible class (C041's REST twin).
        if noun == "api":
            for j, t in enumerate(rest):
                if t in ("-X", "--method"):
                    if j + 1 < len(rest) and rest[j + 1].upper() in ("DELETE", "PATCH", "PUT"):
                        return True
                if t.startswith("--method="):
                    if t.split("=", 1)[1].upper() in ("DELETE", "PATCH", "PUT"):
                        return True
                # bundled short form `-XDELETE` / `-X=DELETE` (Gate-2 N2)
                if t.startswith("-X") and len(t) > 2:
                    if t[2:].lstrip("=").upper() in ("DELETE", "PATCH", "PUT"):
                        return True
                # -f/-F/--field/--raw-field visibility=… (the literal C041 op)
                if t in ("-f", "-F", "--field", "--raw-field"):
                    if j + 1 < len(rest) and rest[j + 1].startswith("visibility="):
                        return True
                if t.startswith(("--field=", "--raw-field=")) and "visibility=" in t:
                    return True
            return False
        # Any destructive verb at the verb position, regardless of noun:
        # `gh repo delete`, `gh secret delete`, `gh release delete-asset`, …
        if rest and rest[0] in ("delete", "delete-asset"):
            return True
        # gh repo edit … --visibility (spaced or =form). Only --visibility is
        # irreversible; --add-topic/--description/etc. are safe edits.
        if noun == "repo" and rest and rest[0] == "edit":
            for t in rest[1:]:
                if t == "--visibility" or t.startswith("--visibility="):
                    return True
        return False

    # ── git family ── (handle global options that precede the subcommand:
    # `git -C path push -f`, `git --git-dir=… push -f`, `git -c k=v push -f`)
    if tokens[0] == "git":
        k = 1
        while k < len(tokens):
            t = tokens[k]
            if t in ("-C", "-c", "--namespace"):
                k += 2  # option that consumes a value
            elif t.startswith(("--git-dir", "--work-tree", "--namespace=", "-C", "-c")):
                k += 1  # =form, self-contained
            elif t.startswith("-"):
                k += 1  # any other global flag
            else:
                break  # the subcommand
        if k >= len(tokens) or tokens[k] != "push":
            return False
        args = tokens[k + 1:]
        # A --delete / -d push deletes whatever refs follow it. If EVERY such ref
        # is an explicit tag (refs/tags/<name>), the delete is REVERSIBLE (re-push
        # the tag) and NOT gated — but a mixed set (a tag AND a branch) stays gated
        # by the non-tag ref. (run_1141ea02, Gate-1 R1/R2.)
        #
        # TWO-PASS so the decision is ORDER-INDEPENDENT: git's getopt interleaves
        # options and operands, so `git push origin mybranch --delete` is a valid
        # BRANCH delete (flag AFTER the ref). A single-pass "gate only once the
        # delete flag was already seen" fails OPEN on that ordering (Gate-2 HIGH,
        # run_1141ea02). Pass 1: scan ALL tokens for force/mirror/prune (→ gate
        # immediately) and whether a delete flag is present anywhere. Pass 2: judge
        # the ref operands knowing delete-intent regardless of position.
        delete_flag_seen = False
        for tok in args:
            if tok in ("--force", "--force-with-lease", "--mirror", "--prune"):
                return True
            if tok.startswith("--force-with-lease="):
                return True
            if tok == "--delete":
                delete_flag_seen = True
            elif tok.startswith("-") and not tok.startswith("--") and tok != "-":
                # short flags, possibly bundled (-uf, -fd). 'f'=force, 'd'=delete.
                short = tok[1:]
                if "f" in short:
                    return True
                if "d" in short:
                    delete_flag_seen = True

        # Pass 2: ref operands. Positional layout is `git push [opts] <remote>
        # <refspec>...` — the FIRST bare operand is the remote (skip it), the rest
        # are refspecs. A ':<ref>' colon-delete or '+<ref>' forced update is a
        # refspec regardless of position and never consumes the remote slot.
        remote_consumed = False
        saw_ref_operand = False
        for tok in args:
            if tok.startswith("-"):
                continue  # flag, already handled in Pass 1
            if tok.startswith("+") and len(tok) > 1:
                return True  # forced ref update — history rewrite, always gated
            if tok.startswith(":") and len(tok) > 1:
                # colon-refspec delete. TAG delete is reversible → skip (a later
                # branch refspec in the same command must still gate). Else gate.
                saw_ref_operand = True
                if _is_tag_ref(tok[1:]):
                    continue
                return True
            # a plain bare operand (no leading -, :, +)
            if not remote_consumed:
                remote_consumed = True  # the <remote> positional — not a ref
                continue
            if ":" in tok:
                continue  # a src:dst update-refspec (non-empty left side) — safe
            if delete_flag_seen:
                # a bare ref operand under --delete/-d. Tag → reversible, skip;
                # anything else (branch, bare name, path-traversal) → gate.
                saw_ref_operand = True
                if _is_tag_ref(tok):
                    continue
                return True

        # A --delete/-d with NO explicit ref operand (e.g. `git push origin
        # --delete`) is NOT provably an all-tags delete → fail closed (the operand
        # could resolve to a branch via push config). Gate it. (Gate-2 MED.)
        if delete_flag_seen and not saw_ref_operand:
            return True
        return False

    return False


def _targets_irreplaceable_store(command: str) -> bool:
    """Fail-closed predicate: does this command DESTROY/TRUNCATE an irreplaceable store?

    run_a456640f (order 4, STEERING #20): the Bash face of the destruction guard.
    ``_is_dangerous_rm`` only fires on the catastrophic ``rm -rf`` *root* shape;
    it misses a plain ``rm .context/MEMORY.md`` or ``mv Projects/CMHK /tmp`` — each
    destroys/relocates an irreplaceable cognition/knowledge store with no undo. This
    flags a destructive verb whose target classifies as IRREPLACEABLE, so the SAME
    dangerous_command_gate approval flow blocks it.

    Destructive verbs covered (security-review run_a456640f, verb-scope expansion):
    ``rm``/``mv`` (delete/relocate); the in-place OBLITERATORS ``truncate``, ``dd``,
    ``shred``, ``tee``, ``cp`` (``cp /dev/null <store>`` zeroes a file); and
    ``find <store> … -delete``/``-exec`` (tree delete while the verb is ``find``).
    Reuses ``data_safety.classify_store`` as the SINGLE classification authority
    (P8): a REPLACEABLE artifact (L1 cache, FTS index) is NOT gated.

    ACCEPTED RESIDUALS (documented, not hidden — same posture as the sibling
    predicates): a ``$VAR``-prefixed operand whose var holds the store segment,
    a symlink INTO a store, and a store-destroy hidden in a subshell / pipeline /
    ``sh -c`` (``tokens[0]`` is ``sh``) EVADE this predicate — they need a real
    shell parser. This is a defense-in-depth layer atop ``_is_dangerous_rm`` +
    ``dangerous_command_gate``; the PRIMARY incident vectors (boot DB corruption,
    HTTP project-delete) are covered structurally by ``isolate_store`` / trash-move,
    not by this text gate. Redirection truncation (``> store``) is also not seen
    (no verb token). Unparseable → False (fallback regex path covers that class).
    """
    from core.data_safety import classify_store, StoreClass
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    # Delete/relocate/obliterate verbs + cp (cp /dev/null <store> zeroes a file).
    _DESTRUCTIVE_VERBS = ("rm", "mv", "truncate", "dd", "shred", "tee", "cp")
    if not tokens:
        return False
    verb = tokens[0]
    # `find <store> ... -delete` / `-exec rm` deletes a store TREE while the verb
    # is `find`. Gate it when a store operand co-occurs with a delete action.
    if verb == "find":
        has_delete = "-delete" in tokens or "-exec" in tokens or "-execdir" in tokens
        if not has_delete:
            return False
        # fall through: judge find's path operands below (verb handled as store-scan)
    elif verb not in _DESTRUCTIVE_VERBS:
        return False
    # Split operands (values) from flags. `-t DEST` / `--target-directory=DEST`
    # names the destination explicitly and moves it OFF the last position — parse it
    # so a `cp -t <store> src` (dest = store, NOT last operand) is not missed.
    operands: list[str] = []
    t_dest: str | None = None
    raw = tokens[1:]
    i = 0
    while i < len(raw):
        tok = raw[i]
        if tok in ("-t", "--target-directory"):
            if i + 1 < len(raw):
                t_dest = raw[i + 1]
                i += 2
                continue
            i += 1
            continue
        if tok.startswith("--target-directory="):
            t_dest = tok.split("=", 1)[1]
            i += 1
            continue
        # BUNDLED short-flag cluster containing `t` (GNU coreutils: `-rt DEST` ==
        # `-r -t DEST`, `-rtDEST` == target inline). Without this, `cp -rt <store> src`
        # slips the gate (the cluster is dropped as a valueless flag → t_dest never set
        # → store treated as a source = fail-open, the exact hole the -t parse closes).
        if (
            tok.startswith("-")
            and not tok.startswith("--")
            and tok != "-"
            and "t" in tok[1:]
        ):
            after_t = tok[tok.index("t", 1) + 1:]
            if after_t:
                t_dest = after_t  # inline target: -rtDEST
                i += 1
                continue
            if i + 1 < len(raw):
                t_dest = raw[i + 1]  # -rt DEST → next token is the target dir
                i += 2
                continue
            i += 1
            continue
        if tok.startswith("-") and tok != "-":
            i += 1  # a flag with no value we track (e.g. -r, -f, -s0)
            continue
        operands.append(tok)
        i += 1

    # PER-VERB operand ROLE (AC3, run_d47d3e5e): only the operand(s) a verb DESTROYS
    # are gated. A store as a READ SOURCE (a backup) must NOT be gated — the 8-12
    # incident's core harm was the absence of a backup; gating the backup is the
    # wrong fix.
    #   • cp / tee : destroy only the DESTINATION. `-t DEST` if present, else the LAST
    #     operand (cp/tee copy sources → dest). Source operands are reads → not gated.
    #   • dd       : destroys only `of=<path>`. `if=<store>` is a read → not gated.
    #   • rm/mv/truncate/shred/find : every path operand is a target (mv RELOCATES the
    #     store away = destruction of the original location too), so gate them all.
    _DEST_ONLY = {"cp", "tee"}
    if verb in _DEST_ONLY:
        if t_dest is not None:
            gated_operands = [t_dest]
        elif operands:
            gated_operands = [operands[-1]]  # last = destination
        else:
            gated_operands = []
    elif verb == "dd":
        # dd names its destructive target ONLY via of=; if= is a read source.
        gated_operands = []
        for tok in tokens[1:]:
            if tok.startswith("of="):
                gated_operands.append(tok.split("=", 1)[1])
    else:
        # rm / mv / truncate / shred / find → every path operand is destroyed/relocated.
        # (mv also honors -t DEST as a target — include it.)
        gated_operands = list(operands)
        if t_dest is not None:
            gated_operands.append(t_dest)

    for op in gated_operands:
        # `dd of=<path>` / `if=<path>` — the path rides in a key=value operand.
        # Strip a leading `key=` so the store-path is seen (dd is the one verb
        # here that names its target this way).
        if "=" in op and op.split("=", 1)[0].isalpha():
            op = op.split("=", 1)[1]
        # Only a path that names a known irreplaceable store counts. classify_store
        # is fail-closed (unknown → IRREPLACEABLE), but we must NOT gate every rm of
        # an unknown scratch path — so restrict to operands that actually reference
        # a governed store marker (.context / Projects/ / Knowledge/Library).
        low = op.lower().replace("\\", "/")
        # Path-boundary matches (not loose substrings) so `mydata.db`, `my-projects/`,
        # `metadata.dbx` don't false-gate. Each marker must sit at a path segment
        # boundary (string start or after a '/').
        segs = low.split("/")
        kn_lib_adjacent = any(
            segs[i] == "knowledge" and i + 1 < len(segs) and segs[i + 1] == "library"
            for i in range(len(segs))
        )
        names_a_store = (
            ".context" in segs
            or "projects" in segs
            or kn_lib_adjacent
            or low == "data.db" or low.endswith("/data.db")
        )
        if not names_a_store:
            continue
        if classify_store(op) in (StoreClass.IRREPLACEABLE, StoreClass.RECOVERABLE):
            return True
    return False


def _is_tag_ref(ref: str) -> bool:
    """True iff *ref* is an explicit, well-formed tag ref (``refs/tags/<name>``).

    Fail-closed (mirrors ``_is_dangerous_rm``'s posture): requires the literal
    ``refs/tags/`` prefix AND a non-empty tag name AND no ``..`` path-traversal
    segment (``refs/tags/../heads/main`` must NOT read as a tag — it resolves
    toward a branch). A bare name (``oldbranch``) or a ``refs/heads/`` branch ref
    is NOT a tag → returns False → stays gated. run_1141ea02, Gate-1 R1.
    """
    if not ref.startswith("refs/tags/"):
        return False
    name = ref[len("refs/tags/"):]
    if not name:
        return False
    if ".." in name.split("/"):
        return False
    # Reject a ':' or whitespace in the name (Gate-2 security LOW-1): a
    # 'refs/tags/v1:refs/heads/main' or a space-bearing spec is not a plain tag
    # ref — git itself rejects it, but the predicate must be self-sufficient and
    # not classify it as a reversible tag. Fail closed.
    if ":" in name or any(c.isspace() for c in ref):
        return False
    return True


def _is_history_rewrite(command: str) -> bool:
    """Fail-closed predicate: is this a LOCAL git history-rewrite op?

    DoD1 (2026-08-11): a sibling session's ``git rebase`` once dropped a whole
    commit (gate-enforcing code) from HEAD while another session was live on the
    SAME shared working tree (single ``main``, shared index). Unlike
    ``_is_irreversible_external_op`` (which guards REMOTE ops: force-push, gh
    delete/visibility), this guards the LOCAL ops that rewrite committed history
    reachable from HEAD, so they can be blocked WHEN another live session may
    lose work. Gate wiring decides whether to block; this only classifies.

    Flags True for:
    - ``git rebase`` (any form, incl. ``-i`` / ``--onto``)
    - ``git reset --hard`` / ``--merge`` / ``--keep`` (working-tree/HEAD-moving
      resets that can discard commits). A BARE ``git reset`` / ``reset -q -- <p>``
      is unstage-only (the auto_commit hook itself uses it) → NOT flagged.
    - ``git commit --amend`` (rewrites the tip commit)
    - ``git filter-branch`` / ``git filter-repo`` (history rewriters)

    Token-aware (``shlex``), segment-split on shell separators so a rewrite op
    chained after a benign command is still caught. Unparseable input mentioning
    ``git`` → True (fail closed). Non-git → False.
    """
    try:
        all_tokens = shlex.split(command.replace("\n", " ; ").replace("\r", " ; "))
    except ValueError:
        # Unparseable: only fail closed if a rewrite signature is literally present.
        return bool(re.search(r"\b(rebase|reset\s+--hard|--amend|filter-branch|filter-repo)\b", command))
    if not all_tokens:
        return False
    # Re-split glued ';' (shlex leaves 'status;' fused).
    _resplit: list[str] = []
    for tok in all_tokens:
        if ";" in tok and tok != ";":
            for idx, p in enumerate(tok.split(";")):
                if p:
                    _resplit.append(p)
                if idx < len(tok.split(";")) - 1:
                    _resplit.append(";")
        else:
            _resplit.append(tok)
    all_tokens = _resplit

    _SEPARATORS = {";", "&&", "||", "|", "&"}
    segments: list[list[str]] = []
    current: list[str] = []
    for tok in all_tokens:
        if tok in _SEPARATORS:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        segments.append(current)

    return any(_segment_is_history_rewrite(seg) for seg in segments)


def _segment_is_history_rewrite(tokens: list[str]) -> bool:
    """Judge ONE command segment (already split on shell separators)."""
    _WRAPPERS = {"sudo", "env", "command", "nice", "nohup", "time"}
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if "=" in t and t.split("=", 1)[0].replace("_", "").isalnum() and not t.startswith("-"):
            i += 1  # env assignment NAME=value
        elif t in _WRAPPERS:
            i += 1
        else:
            break
    tokens = tokens[i:]
    if not tokens or tokens[0] != "git":
        return False
    # Skip git global options that precede the subcommand (`git -C p rebase …`).
    k = 1
    while k < len(tokens):
        t = tokens[k]
        if t in ("-C", "-c", "--namespace"):
            k += 2
        elif t.startswith(("--git-dir", "--work-tree", "--namespace=", "-C", "-c")):
            k += 1
        elif t.startswith("-"):
            k += 1
        else:
            break
    if k >= len(tokens):
        return False
    sub = tokens[k]
    rest = tokens[k + 1:]
    if sub in ("rebase", "filter-branch", "filter-repo", "update-ref"):
        # update-ref rewrites a ref pointer directly (can orphan commits).
        return True
    if sub == "commit":
        return any(a == "--amend" or a.startswith("--amend") for a in rest)
    if sub == "reset":
        # --hard / --merge / --keep move HEAD/working-tree destructively.
        # A bare `git reset` or `git reset -- <path>` is unstage-only → safe.
        return any(a in ("--hard", "--merge", "--keep") for a in rest)
    if sub == "branch":
        # `git branch -f/-D/-M <name> [<start>]` force-moves/deletes a branch
        # pointer → can orphan commits on the shared tree (the incident's class).
        # `-m`/`-c` (rename/copy) and a bare `git branch` (list) are safe.
        return any(a in ("-f", "--force", "-D", "-M") or a.startswith("--force") for a in rest)
    if sub == "checkout":
        # `git checkout -B <name>` force-resets an existing branch to a start point
        # → orphans commits. Plain `-b` (create-new, fails if exists) is safe.
        return any(a == "-B" for a in rest)
    return False


def _other_live_sessions_present(current_app_session_id: str) -> "bool | None":
    """True iff another LIVE session (besides *current_app_session_id*) exists.

    Reads the session registry via ``list_units()`` (same source as
    ``auto_commit_hook._other_live_sessions_touched``). Compares each unit's own
    ``session_id`` (the app-level key ``_units`` is indexed by, and the value
    stored in ``session_context['sdk_session_id']``) — NOT the SDK/agent
    ``session_key``, which lives in a different namespace (Gate-2 BUG A).

    Only ``is_alive`` units count: ``_units`` retains DEAD/COLD units up to ~1h
    (lifecycle_manager._purge_stale_cold), so an unfiltered scan would gate every
    rewrite for an hour after a sibling died (Gate-2 BUG B).

    Returns:
    - ``True``  → at least one OTHER live session exists
    - ``False`` → this is the only live session
    - ``None``  → registry unavailable / error (caller decides fail-safe)
    """
    try:
        from core import session_registry
        router = session_registry.session_router
        if router is None:
            return None
        list_units = getattr(router, "list_units", None)
        if list_units is None:
            return None
        for unit in list_units():
            if not getattr(unit, "is_alive", False):
                continue
            if getattr(unit, "session_id", None) != current_app_session_id:
                return True
        return False
    except Exception as e:  # pragma: no cover - registry edge
        logger.debug("history-rewrite gate: could not read session registry: %s", e)
        return None


def load_dangerous_patterns() -> list[str]:
    """Load glob patterns from ``~/.swarm-ai/dangerous_commands.json``.

    Creates the file with ``DEFAULT_DANGEROUS_PATTERNS`` if missing.
    Falls back to defaults on invalid JSON or missing ``"patterns"`` key.
    Public API — also called by ``main.py`` for ``permissions.json`` generation.
    """
    patterns_path = get_app_data_dir() / "dangerous_commands.json"
    try:
        raw = patterns_path.read_text(encoding="utf-8").strip()
        if not raw:
            raise ValueError("empty file")
        data = json.loads(raw)
        if not isinstance(data, dict) or "patterns" not in data:
            raise ValueError("missing 'patterns' key")
        patterns = list(data["patterns"])
        # Migration (fix #3): existing installs persisted the obsolete blanket
        # rm globs. They are now handled by _is_dangerous_rm (which allows /tmp).
        # Strip them on load so the fix reaches users who already have the file —
        # otherwise the on-disk file silently overrides the code change
        # (default-propagation trap, PIT38).
        _obsolete = {"rm -rf *", "rm -rf /*", "rm -rf ~*"}
        if any(p in _obsolete for p in patterns):
            patterns = [p for p in patterns if p not in _obsolete]
            try:
                patterns_path.write_text(
                    json.dumps({"patterns": patterns}, indent=2) + "\n",
                    encoding="utf-8",
                )
                logger.info("Migrated dangerous_commands.json — removed obsolete rm globs")
            except OSError:
                pass  # in-memory strip still takes effect this run
        logger.info("Loaded %d dangerous patterns from %s", len(patterns), patterns_path)
        return patterns
    except FileNotFoundError:
        logger.info("dangerous_commands.json not found — seeding defaults")
        patterns_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"patterns": list(DEFAULT_DANGEROUS_PATTERNS)}
        patterns_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return list(DEFAULT_DANGEROUS_PATTERNS)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Invalid dangerous_commands.json (%s) — using defaults", exc)
        return list(DEFAULT_DANGEROUS_PATTERNS)
    except OSError as exc:
        logger.warning("Cannot read dangerous_commands.json (%s) — using defaults", exc)
        return list(DEFAULT_DANGEROUS_PATTERNS)


def create_ask_question_gate(
    session_key: str,
    session_context: dict[str, Any],
    ask_question_mgr: "AskQuestionManager",
) -> Callable[..., Any]:
    """Factory: returns an async PreToolUse hook for AskUserQuestion.

    The Claude CLI's AskUserQuestion tool self-resolves with an
    ``is_error:true, "Answer questions?"`` tool_result in headless/SDK mode
    (no interactive UI to satisfy its ``behavior:"ask"`` permission). This hook
    intercepts the tool call at PreToolUse — BEFORE the CLI self-resolves it —
    BLOCKS on ``ask_question_mgr.wait_for_answer(tool_use_id)`` until the user
    answers, then returns ``permissionDecision:"allow"`` with the user's answers
    injected into ``updatedInput.answers``. The CLI's ``call()`` then returns the
    real answers rather than the synthetic error.

    Correlation: the waiter is keyed on the ``tool_use_id`` arg (the SDK
    AskUserQuestion block.id), which is also what the ``ask_user_question`` SSE
    event surfaces as ``toolUseId`` and what ``continue_with_answer`` passes back.
    """

    async def ask_question_gate(
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        # Scoped to AskUserQuestion only — every other tool passes through (AC3).
        if input_data.get("tool_name") != "AskUserQuestion":
            return {"decision": "allow"}

        tool_input = input_data.get("tool_input", {}) or {}
        questions = tool_input.get("questions", [])

        # Defensive: without a tool_use_id we cannot correlate the answer back.
        # Allow with empty answers so the tool resolves rather than hangs.
        if not tool_use_id:
            logger.warning(
                "ask_question_gate: missing tool_use_id, cannot block for answer "
                "(session=%s)", session_key,
            )
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "updatedInput": {**tool_input, "answers": {}},
                    "permissionDecisionReason": "No tool_use_id — answers unavailable",
                }
            }

        # Surface the question to the frontend via the per-session queue that the
        # streaming orchestrator races. The item carries `kind` + `tool_use_id`
        # so the orchestrator's kind-aware drop-guard + SSE branch can route it.
        # Lazy import avoids a hard module-load cycle (permission_manager is the
        # surfacing channel, shared with the dangerous_command_gate path).
        from .permission_manager import permission_manager as _pm
        actual_session_id = session_context.get("sdk_session_id") or session_key
        # F3: register the waiter BEFORE surfacing. Surfacing (enqueue) then
        # awaiting wait_for_answer left a window where a fast non-human
        # auto-answer (channel gateway) arrived before the waiter existed and was
        # dropped. register_waiter is synchronous + idempotent — after this,
        # set_answer always has a live target.
        ask_question_mgr.register_waiter(tool_use_id)
        # The surface→await span must reap the waiter if it throws/cancels before
        # wait_for_answer is entered — otherwise the registered event leaks and
        # has_live_waiter() lies True with no coroutine blocked (ghost question /
        # answer-into-void). wait_for_answer's own finally covers the post-entry
        # case; this try only covers the enqueue/cancel window before it.
        try:
            await _pm.enqueue_permission_request(actual_session_id, {
                "kind": "ask_user_question",
                "sessionId": actual_session_id,
                "tool_use_id": tool_use_id,
                "questions": questions,
            })
        except BaseException:
            ask_question_mgr.discard_waiter(tool_use_id)
            raise

        logger.info(
            "ask_question_gate: blocking for answer session=%s tool_use_id=%s "
            "questions=%d", actual_session_id, tool_use_id, len(questions),
        )

        answer = await ask_question_mgr.wait_for_answer(tool_use_id)

        # Timeout (default 4h): the user never answered. DENY the tool — do NOT
        # inject a fabricated empty answer and proceed. Injecting {} + allowing
        # was the original bug: after the user stepped away, the agent would
        # "proceed with no selection". A deny tells the agent the question
        # expired so it can re-ask, never guess. The deny path carries NO
        # updatedInput.answers — there is no answer to inject.
        if answer == ASK_TIMEOUT_SENTINEL:
            logger.warning(
                "ask_question_gate: question expired (no answer in %ds) "
                "session=%s tool_use_id=%s",
                ASK_ANSWER_TIMEOUT_SECONDS, actual_session_id, tool_use_id,
            )
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "提问超时（Question expired）: no answer was received within "
                        "the wait window. The question was NOT answered — re-ask "
                        "the user rather than proceeding with a guessed default."
                    ),
                }
            }

        # answer is the user's answers dict.
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "updatedInput": {**tool_input, "answers": answer},
            }
        }

    return ask_question_gate


def create_dangerous_command_gate(
    session_context: dict[str, Any],
    session_key: str,
    permission_mgr: "PermissionManager",
    enable_human_approval: bool = True,
) -> Callable[..., Any]:
    """Factory: returns an async PreToolUse hook for Bash commands.

    Loads patterns once at gate creation time (not per-invocation).
    Uses *permission_mgr* for HITL flow and session approval tracking.

    When *enable_human_approval* is ``False`` (per-agent config), dangerous
    commands are auto-denied without prompting.
    """
    patterns = load_dangerous_patterns()

    async def dangerous_command_gate(
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        if input_data.get("tool_name") != "Bash":
            return {"decision": "approve"}

        command = input_data.get("tool_input", {}).get("command", "")
        if not command:
            return {"decision": "approve"}

        # Check if command matches any dangerous pattern (glob) OR is a
        # dangerous recursive rm (predicate — fix #3, allows /tmp & /var/folders)
        # OR is an irreversible destructive external op (C041 — gh repo
        # edit/delete, git push --force/delete; predicate, not glob).
        is_dangerous = (
            any(fnmatch.fnmatch(command, p) for p in patterns)
            or _is_dangerous_rm(command)
            or _is_irreversible_external_op(command)
            or _targets_irreplaceable_store(command)  # rm/mv of DDD/Library/.context (run_a456640f)
        )
        # DoD1: local history-rewrite (rebase / reset --hard / commit --amend /
        # filter-*) is dangerous ONLY when a SIBLING live session could lose work
        # on the shared tree. Solo session → a local rewrite affects only its own
        # history, not gated. Registry unavailable → fail-safe stringent (these
        # ops are irreversible; s_workspace-git already requires asking first).
        if not is_dangerous and _is_history_rewrite(command):
            # Use the app-level session_id (the _units key), NOT session_key —
            # session_key is resume_session_id/agent_id, a different namespace, so
            # a solo session would never self-match → falsely gated (Gate-2 BUG A).
            app_sid = session_context.get("sdk_session_id") or session_key
            others = _other_live_sessions_present(app_sid)
            if others is not False:  # True (sibling exists) OR None (unknown)
                is_dangerous = True
                logger.warning(
                    "[HISTORY-REWRITE] gated (%s): %s",
                    "sibling live" if others else "registry unavailable — fail-safe",
                    command[:80],
                )
        if not is_dangerous:
            return {"decision": "approve"}

        # Auto-deny when human approval is disabled
        if not enable_human_approval:
            logger.warning("[BLOCKED] Dangerous command (no human approval): %s", command[:80])
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Dangerous command blocked (human approval disabled)",
                }
            }

        # Check session approvals
        if permission_mgr.is_command_approved(session_key, command):
            logger.info("[APPROVED] Session-approved command: %s", command[:50])
            return {"decision": "approve"}

        # --- HITL prompt flow ---
        # Read session ID dynamically from the (mutable) session_context dict.
        # The hook closure captures session_context at creation time, but the
        # dict's contents may be updated by SessionRouter on each send() when
        # the subprocess is reused (IDLE → STREAMING).  Using the live value
        # ensures the permission request routes to the correct per-session
        # queue that _read_formatted_response is watching.
        actual_session_id = session_context.get("sdk_session_id") or session_key
        request_id = f"perm_{uuid4().hex[:12]}"
        tool_input_data = input_data.get("tool_input", {})

        permission_request = {
            "id": request_id,
            "session_id": actual_session_id,
            "tool_name": "Bash",
            "tool_input": json.dumps(tool_input_data),
            # Carry tool_call_id so a Bash approval is also PERSISTED durably (the
            # request survives a daemon restart in the on-disk store) and is
            # diagnosable by tool-call. NOTE: the Bash gate's re-prompt idempotency
            # is provided by is_command_approved() (command-hash, session-scoped), NOT
            # by is_resolved((session,tool_call_id)) — that key guards the external
            # gate. This field is for durability + audit, not a second idempotency path.
            "tool_call_id": tool_use_id,
            "reason": "Matches dangerous command pattern",
            "status": "pending",
            "created_at": datetime.now().isoformat(),
        }
        permission_mgr.store_pending_request(permission_request)

        await permission_mgr.enqueue_permission_request(actual_session_id, {
            "sessionId": actual_session_id,
            "requestId": request_id,
            "toolName": "Bash",
            "toolInput": tool_input_data,
            "reason": "Matches dangerous command pattern",
            "options": ["approve", "deny"],
        })

        logger.warning(
            "[PERMISSION_REQUEST] Dangerous command requires approval: %s (request_id: %s)",
            command[:50], request_id,
        )

        decision = await permission_mgr.wait_for_permission_decision(request_id)
        logger.info("User decision for %s: %s", request_id, decision)
        permission_mgr.remove_pending_request(request_id)

        if decision == "approve":
            permission_mgr.approve_command(session_key, command)
            return {"decision": "approve"}

        # Distinguish an explicit user denial from a timeout (fix #2/#3). Both
        # deny the command to the SDK, but the reason — surfaced to the user as
        # the tool_result — must make a timeout visibly different from a denial,
        # so a never-surfaced prompt reads as "审批超时" instead of a silent hang.
        if decision == "timeout":
            reason = (
                "审批超时（Approval timed out after 4 hours）: "
                "the command was auto-denied because no decision was received. "
                "Re-run if you still need it."
            )
        else:
            reason = "User denied: Matches dangerous command pattern"

        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    return dangerous_command_gate


# ---------------------------------------------------------------------------
# External-approval gate — off-machine side effects (PreToolUse, ALL tools)
# ---------------------------------------------------------------------------
# Closes the hole that dangerous_command_gate (Bash-only) and the can_use_tool
# file handler (file-tools only, and None under global_user_mode) both leave open:
# a NON-Bash tool with an OFF-MACHINE side effect — an MCP tool that sends email /
# posts to Slack / mutates a CRM / changes repo visibility — otherwise passes with
# ZERO approval gating. This generalizes the C041 gh/git protection from "these Bash
# commands" to "any EXTERNAL-classed tool call" (see core/tool_risk.classify).
#
# It is a PreToolUse hook (fires regardless of global_user_mode, unlike can_use_tool)
# and is registered as the SOLO occupant of the no-matcher PreToolUse group so its
# no_timeout (4h HITL block) does NOT poison the fast governance gate's hang-guard
# (hook_builder groups by (event, matcher); see the registration + Gate-1 B1 note).
def create_external_approval_gate(
    session_context: dict[str, Any],
    session_key: str,
    permission_mgr: "PermissionManager",
    enable_human_approval: bool = True,
) -> Callable[..., Any]:
    """Factory: async PreToolUse hook that routes EXTERNAL-classed (off-machine) tool
    calls through the PermissionManager approval flow.

    Reuses the exact enqueue → wait → decision flow as dangerous_command_gate, but
    keyed on the tool's risk CLASS (tool_risk.classify) rather than a Bash pattern.
    Bash is left entirely to dangerous_command_gate (no double-gate).
    """
    from core.tool_risk import RiskClass, classify

    async def external_approval_gate(
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        tool_name = input_data.get("tool_name", "")

        # Bash is owned by the hardened dangerous_command_gate — skip to avoid a
        # double prompt (a gh/git external Bash op is gated there, not here).
        if tool_name == "Bash":
            return {"decision": "approve"}

        # Only EXTERNAL (off-machine) tools are gated. READ / WRITE_LOCAL pass freely.
        if classify(tool_name) is not RiskClass.EXTERNAL:
            return {"decision": "approve"}

        # Gate-1 B4: without a tool_use_id we cannot correlate a durable approval
        # (idempotency key is (session_id, tool_call_id)). Approve + skip tracking
        # rather than block — mirrors ask_question_gate's missing-id fallback.
        if not tool_use_id:
            logger.warning(
                "external_approval_gate: missing tool_use_id for %s — approving without tracking",
                tool_name,
            )
            return {"decision": "approve"}

        # Auto-deny when human approval is disabled (per-agent config).
        if not enable_human_approval:
            logger.warning(
                "[BLOCKED] External tool (no human approval): %s", tool_name
            )
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"External tool {tool_name} blocked (human approval disabled)"
                    ),
                }
            }

        # Restart-idempotency: if this (session, tool_call) was already resolved
        # (e.g. a decision replayed across a daemon restart), do not re-prompt.
        actual_session_id = session_context.get("sdk_session_id") or session_key
        if permission_mgr.is_resolved(actual_session_id, tool_use_id):
            logger.info(
                "external_approval_gate: %s already resolved for (%s,%s) — approving",
                tool_name, actual_session_id, tool_use_id,
            )
            return {"decision": "approve"}

        request_id = f"perm_{uuid4().hex[:12]}"
        tool_input_data = input_data.get("tool_input", {})
        reason = f"External tool call ({tool_name}) has off-machine side effects"

        permission_mgr.store_pending_request({
            "id": request_id,
            "session_id": actual_session_id,
            "tool_name": tool_name,
            "tool_input": json.dumps(tool_input_data),
            "tool_call_id": tool_use_id,
            "reason": reason,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
        })

        await permission_mgr.enqueue_permission_request(actual_session_id, {
            "sessionId": actual_session_id,
            "requestId": request_id,
            "toolName": tool_name,
            "toolInput": tool_input_data,
            "reason": reason,
            "options": ["approve", "deny"],
        })

        logger.warning(
            "[PERMISSION_REQUEST] External tool requires approval: %s (request_id: %s)",
            tool_name, request_id,
        )

        decision = await permission_mgr.wait_for_permission_decision(request_id)
        permission_mgr.remove_pending_request(request_id)

        if decision == "approve":
            return {"decision": "approve"}

        if decision == "timeout":
            reason = (
                "审批超时（Approval timed out after 4 hours）: the external tool call "
                "was auto-denied because no decision was received. Re-run if still needed."
            )
        else:
            reason = f"User denied external tool call: {tool_name}"

        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    return external_approval_gate


# ---------------------------------------------------------------------------
# Background-command guard — runaway/hang prevention (PreToolUse, Bash)
# ---------------------------------------------------------------------------
# The CLI's per-command timeout (BASH_DEFAULT_TIMEOUT_MS=120s) bounds FOREGROUND
# commands only. Background commands (run_in_background, or shell &/nohup/disown/
# setsid) ignore it and can run indefinitely with no visibility
# (anthropics/claude-code#61568) — exactly how a bare `find` scanning
# node_modules hung ~10min. Prose rules don't hold (the agent backgrounded it
# despite AGENT.md); a PreToolUse hook is the only deterministic enforcement.
#
# Policy: DEFAULT-DENY backgrounding. Only a narrow allowlist of genuinely
# long-lived services (dev servers, --watch, tail -f) may background. Everything
# else (find/grep/pytest/build/install — all result-bearing) runs foreground,
# where the 120s timeout applies and the agent sees the result. Truly long
# detached work belongs on the daemon job system, not a background shell.

_BG_KEYWORD_RE = re.compile(r"\b(?:nohup|disown|setsid)\b")
# A heredoc body is DATA piped to a program's stdin — any `&` inside it is never a
# shell control operator (it's Python bitwise-and, an unquoted URL query, etc.).
# Matches `<<[-]?['"]?WORD` then the body up to the terminator, which MUST be its
# own whole line (`^[ \t]*WORD[ \t]*$`, MULTILINE) — bash ends a heredoc only on a
# line equal to the delimiter, so a PREFIX match (`WORD ...` mid-line, or a bare
# `WORD` line that is the real empty-body terminator) would let the lazy `.*?` span
# stretch PAST the true terminator and swallow a real backgrounding `&` that lives
# after it (Gate-2 correctness finding: `cat <<E\nE\nsleep 999 &\nE`). The
# terminator may be indented (the `<<-` tab-strip form). DOTALL lets `.*?` cross
# lines; MULTILINE anchors the terminator to a line. The opening `<<WORD` and any
# real `&` (intro line, or after the terminator) live OUTSIDE the matched span, so
# stripping the body can never hide a genuine control `&`.
_HEREDOC_BODY_RE = re.compile(
    r"<<-?\s*(['\"]?)(\w+)\1\n.*?^[ \t]*\2[ \t]*$",
    re.DOTALL | re.MULTILINE,
)
# Cap on `<<` occurrences before running the (lazy, backtracking) heredoc strip.
# _HEREDOC_BODY_RE's `.*?` scans to end-of-string for every UNTERMINATED `<<WORD`,
# so N such openers cost O(N^2) — a pathological command (thousands of `<<A`) could
# exceed the <5s PreToolUse budget and hang the hook (Gate-2 security finding, ReDoS).
# Over the cap we SKIP the strip (fail-SAFE: a body-`&` is then re-counted as
# backgrounding → an extra DENY on a 64+-heredoc command, NEVER a fail-open). Real
# commands have 0–2 heredocs; 64 is far above any legitimate use.
_HEREDOC_MAX_OPENERS = 64
# Narrow allowlist: deliberately long-lived services that SHOULD background.
_BG_SERVICE_ALLOWLIST_RE = re.compile(
    r"\b(?:npm|yarn|pnpm)\s+(?:run\s+)?(?:dev|start|serve)\b"
    r"|\bvite\b|\bnodemon\b|\bnext\s+dev\b"
    r"|\buvicorn\b|--reload\b|\bflask\s+run\b"
    r"|\bhttp\.server\b|\bhttp-server\b"
    r"|--watch\b"
    r"|\btail\s+-[fF]\b"
    r"|\./dev\.sh\b",
    re.IGNORECASE,
)


def _is_backgrounded(command: str, tool_input: dict[str, Any]) -> bool:
    """True if the Bash call would run in the background (tool flag OR shell).

    Shell detection strips every NON-backgrounding use of ``&`` — heredoc bodies
    (DATA, not shell), logical-AND (``&&``), redirects (``&>``/``&>>``), fd-dups
    (``2>&1``), and quoted literals — so any ``&`` left over is a backgrounding
    control operator (``cmd &`` or ``cmd & next``). Plus nohup/disown/setsid.

    Heredoc bodies are stripped FIRST, BEFORE the quote strips: a quoted heredoc
    delimiter (``<<'EOF'`` / ``<<"EOF"``, the common expansion-suppressing form)
    would otherwise have its delimiter quotes eaten by the quote strip, so the
    heredoc regex could no longer find the opening token and the body would survive
    (Gate-1 finding, run_3bde4b8b). A real backgrounding ``&`` always lives OUTSIDE
    the body (intro line or after the terminator), so this can never hide one.

    ACCEPTED RESIDUAL (documented, not fixed — pytest_command_guard precedent):
    ``&`` inside other DATA contexts that are NOT heredocs — command substitution
    ``$(...)``, backticks, process substitution ``<(...)`` — is still counted. These
    are far rarer than heredocs and each added strip is a new fail-open surface on a
    security gate (a real ``&`` inside ``<(cmd &)`` is genuinely ambiguous), so the
    trade is deliberately declined.
    """
    if tool_input.get("run_in_background") is True:
        return True
    if _BG_KEYWORD_RE.search(command):
        return True
    # Strip heredoc bodies (DATA) FIRST — but skip on a pathological opener count
    # (ReDoS guard, fail-safe: unstripped body-& only over-DENYs, never fail-opens).
    if command.count("<<") <= _HEREDOC_MAX_OPENERS:
        s = _HEREDOC_BODY_RE.sub(" ", command)
    else:
        s = command
    s = re.sub(r"'[^']*'", "", s)         # strip single-quoted literals
    s = re.sub(r'"[^"]*"', "", s)         # strip double-quoted literals
    s = s.replace("&&", "")               # logical-AND, not background
    s = re.sub(r"&>>?", "", s)            # &> / &>> redirect
    s = re.sub(r"\d*>&\d*", "", s)        # 2>&1 / >&2 fd dup
    return "&" in s                        # any remaining & = backgrounding


async def background_command_guard(
    input_data: dict[str, Any],
    tool_use_id: str | None,
    context: Any,
) -> dict[str, Any]:
    """PreToolUse (Bash): default-deny backgrounding except long-lived services.

    Closes the background-runaway hole (#61568) the foreground 120s timeout
    cannot: a backgrounded command escapes the timeout and can hang forever.
    """
    if input_data.get("tool_name") != "Bash":
        return {"decision": "approve"}
    tool_input = input_data.get("tool_input", {}) or {}
    command = tool_input.get("command", "") or ""
    if not command:
        return {"decision": "approve"}
    if not _is_backgrounded(command, tool_input):
        return {"decision": "approve"}
    # Backgrounded — allow only genuine long-lived services.
    if _BG_SERVICE_ALLOWLIST_RE.search(command):
        return {"decision": "approve"}
    logger.warning("[BLOCKED] Background execution denied: %s", command[:80])
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "Background execution is disabled for this command. Re-run it in "
                "the FOREGROUND (remove run_in_background and any trailing "
                "'&'/nohup/setsid) — the 120s timeout then bounds it and you see "
                "the result. Background is reserved for long-lived services "
                "(dev servers, --watch, tail -f). For genuinely long detached "
                "work use the daemon job system, not a background shell."
            ),
        }
    }


# ---------------------------------------------------------------------------
# pytest command guard — block two recurring pytest anti-patterns (R9)
# ---------------------------------------------------------------------------
# R9 documents these in prose but prose did not stop them: in run_241014d4 the
# agent piped a slow pytest into `tail`, the harness auto-backgrounded it, the
# foreground returned empty, and the agent misattributed + re-ran it ~6×. This
# guard is the structural backstop (defense outside the agent, like
# background_command_guard): deny the narrow anti-pattern, approve everything
# else (fail-safe). It is a pure deny — no HITL, no permission_mgr.

# pytest as an INVOCATION (not the literal string in a filename like pytest.log):
# at a command-word position — line start, or after a shell separator/pipe,
# `python -m` (any minor version), a runner wrapper, an env-assignment prefix,
# OR a wall-clock wrapper (gtimeout/timeout N). The wall-clock-wrapper arm is
# essential: the wrapped form is the shape we MANDATE, so the gate must still
# SEE it as pytest (else the pipe-to-pager check is bypassed for the exact
# commands we require — a fail-open hole). This is what stops the
# `cat pytest.log | tail` false positive while catching `gtimeout 90 pytest`.
# `python[\d.]*` matches python / python3 / python3.12 (Gate-2 C5).
_PYTEST_INVOCATION_RE = re.compile(
    r"(?:^|[;&|]|\bpython[\d.]*\s+-m"      # line start / separator / `python -m`
    r"|\b(?:poetry|uv|pdm)\s+run\s+"       # or a poetry/uv/pdm run wrapper
    r"|\bg?timeout\s+\d+\s+"               # or a gtimeout/timeout N wrapper
    r"|\bexec\s+(?:@ARGV['\"]?\s+)?"       # or perl-alarm `exec @ARGV['] <cmd>`
    r"|(?:^|[;&|]\s*)(?:[A-Z_][A-Z0-9_]*=\S+\s+)+)"  # or env-assignment prefix
    r"\s*(?:python[\d.]*\s+-m\s+)?(?:py\.test|pytest)\b",  # opt. `python -m` after wrapper
    re.IGNORECASE,
)
# Output piped into a pager that the harness swallows (tail/head), possibly at
# the end of a pipe chain (`pytest | grep x | tail`).
_PIPE_TO_PAGER_RE = re.compile(r"\|\s*(?:tail|head)\b", re.IGNORECASE)
# A wall-clock wrapper that BINDS the pytest token — the wrapper must IMMEDIATELY
# precede pytest (optionally via `python -m`), not merely appear somewhere in the
# string. Gate-2 (run BLOCK) proved an unbound "wrapper anywhere" check fails open:
#   `gtimeout 90 echo && pytest`  → wrapper wraps echo, pytest runs raw (C1)
#   `timeout 0 pytest`            → 0 means infinite, not a cap            (C2)
#   `perl -e 'print "alarm"'; pytest` → decoy alarm mention, pytest raw    (C3)
# Fixes: (a) wrapper adjacency to pytest; (b) duration `[1-9]\d*` rejects 0;
# (c) perl arm requires `alarm N` AND `exec @ARGV <pytest>` in ONE command (no
# `|`/newline crossing — `;` allowed since it's perl-script syntax). A per-test
# `--timeout=60` NEVER counts (it bounds each test, not the command → C040).
# On THIS machine gtimeout/timeout are NOT installed — only `/usr/bin/perl` is
# guaranteed — so the perl-alarm form is the real, always-available cap.
# Optional runner/interpreter prefix that may sit between the wall-clock wrapper
# and the pytest token: `python[3.x] -m `, `uv run `, `poetry run `, `pdm run `.
_RUNNER_PREFIX = r"(?:python[\d.]*\s+-m\s+|(?:uv|poetry|pdm)\s+run\s+)?"
_WALLCLOCK_BOUND_PYTEST_RE = re.compile(
    # gtimeout/timeout N (N>=1) wrapping pytest (optionally via a runner prefix):
    r"\bg?timeout\s+[1-9]\d*\s+" + _RUNNER_PREFIX + r"(?:py\.test|pytest)\b"
    # OR perl-alarm: alarm N (N>=1) ... exec @ARGV <runner?> <pytest>, one command:
    r"|\bperl\b[^|\n]*?\balarm\s+[1-9]\d*\b[^|\n]*?\bexec\s+@ARGV['\"]?\s+"
    + _RUNNER_PREFIX + r"(?:py\.test|pytest)\b",
    re.IGNORECASE,
)


def _strip_quoted(command: str) -> str:
    """Remove single/double-quoted spans so a quoted '| tail' inside a -k/-m
    expression isn't mistaken for a real pipe (Gate-2 false-positive fix).

    Only matches CLOSED quote spans (`'[^']*'`); an unterminated/odd quote does
    not match and the text passes through untouched (fail-closed on malformed
    input — the raw token is still seen by the caller's regex)."""
    s = re.sub(r"'[^']*'", "", command)
    return re.sub(r'"[^"]*"', "", s)


# A bare pytest TOKEN at a word boundary (just "is the word pytest present").
_PYTEST_TOKEN_RE = re.compile(r"\b(?:py\.test|pytest)\b", re.IGNORECASE)


def _pytest_token_is_command_word(command: str) -> bool:
    """True iff a pytest TOKEN appears as a real command WORD — i.e. NOT only
    inside a quoted string. Used with _PYTEST_INVOCATION_RE to require BOTH an
    anchor match AND that the pytest token is real command text, killing the
    in-quote false-positive (a token mentioned in a grep -E pattern / -k expr /
    commit message — run_5511508d) without fail-opening on real invocations.

    Uses `shlex.split(posix=False)` — a REAL shell lexer that respects quoting —
    NOT a regex quote-strip. A regex strip (`'[^']*'`) is fooled by an apostrophe
    in an unquoted word pairing with a later quoted arg: `echo it's; pytest -k 'x'`
    strips to `echo itx'`, deleting the REAL pytest token → a genuine uncapped run
    fail-opens to APPROVE (Gate-2 HIGH, run_5511508d). shlex tokenizes by true
    shell rules, so that trap cannot occur.

    `posix=False` keeps quote chars in tokens, so a fully-quoted word like
    `'pytest'` stays distinguishable (starts+ends with the same quote char) and is
    skipped — only a bare/word token containing pytest counts. An unbalanced quote
    raises ValueError → fail-CLOSED (treat as a real invocation; a malformed
    command must not slip past the cap)."""
    try:
        tokens = shlex.split(command, posix=False)
    except (ValueError, AttributeError, TypeError):
        # ValueError = unbalanced quoting; AttributeError/TypeError = a non-string
        # command slipped past the caller's coercion. Any tokenizer failure →
        # cannot prove the token is in-quote → fail CLOSED (treat as a real run).
        return True
    for tok in tokens:
        if len(tok) >= 2 and tok[0] in "'\"" and tok[-1] == tok[0]:
            continue  # fully-quoted word — a mention, not a command token
        if _PYTEST_TOKEN_RE.search(tok):
            return True
    return False


async def pytest_command_guard(
    input_data: dict[str, Any],
    tool_use_id: str | None,
    context: Any,
) -> dict[str, Any]:
    """PreToolUse (Bash): DENY pytest piped to tail/head AND DENY pytest without
    a wall-clock wrapper.

    Two pytest anti-patterns, BOTH now denied (upgraded XG-approved direct,
    run after run_6af22b0d — C040 12th CLASS-B recurrence):
      1. piped into tail/head → DENY. A slow run is auto-backgrounded, the
         foreground returns EMPTY, and that reads as a hang (run_241014d4).
      2. no WALL-CLOCK wrapper (`gtimeout N` / `timeout N`) → DENY. Was WARN-only;
         the WARN was insufficient — a no-wrapper `pytest > file 2>&1` (no `|tail`)
         still gets auto-backgrounded into the same hang. A per-test `--timeout`
         does NOT count: it bounds each test, not the whole command. Fail-closed
         (R9 / PIT10 allowlist): a pytest invocation MUST carry a wall-clock cap.

    Fail-safe for the INVOCATION gate: any non-Bash, non-pytest command is
    approved untouched. The pipe check runs on a quote-stripped copy so a quoted
    '| tail' in a -k expression is not mistaken for a real pipe.

    ACCEPTED RESIDUAL (Gate-2 C4/C6, NOT silently capped): pytest reached
    INDIRECTLY — `bash -c "pytest ..."`, `make test`, `xargs pytest`, backticks,
    a Makefile/script target — is NOT recognized, so it is approved uncapped.
    This is a deliberate trade vs the false-positive of denying
    `git commit -m "fix pytest"`: detecting pytest inside an arbitrary quoted
    string would block legitimate non-runs. The gate's threat model is the
    DIRECT shapes the agent actually types in this workspace (`python -m pytest`,
    `gtimeout/perl ... pytest`, env-prefixed, separator-led) — those are bound
    and capped. Indirect invocation is a known leak, documented not hidden; if it
    recurs as a real hang, the fix is to add the specific wrapper, not to widen
    the recognizer into the quoted-string false-positive zone (PIT10 weighed both
    directions: fail-closed on the cap, fail-OPEN on indirection by choice).
    """
    if input_data.get("tool_name") != "Bash":
        return {"decision": "approve"}
    command = (input_data.get("tool_input", {}) or {}).get("command", "") or ""
    if not command:
        return {"decision": "approve"}
    # INVOCATION GATE: a command is a real pytest invocation only if BOTH hold:
    #   (1) the anchored _PYTEST_INVOCATION_RE matches the RAW command — proves a
    #       separator / `python -m` / wrapper / env-prefix anchor sits before pytest
    #       (the anchor may legitimately live inside quotes, e.g. the sanctioned
    #       perl `-e 'alarm N; exec @ARGV'` form), AND
    #   (2) a pytest TOKEN appears as a real command WORD (shlex-tokenized, NOT
    #       only inside a quoted string) — see _pytest_token_is_command_word.
    # Requiring BOTH kills the false-positive (a pytest token that exists ONLY
    # inside a grep -E pattern / -k expr / commit message — run_5511508d blocked
    # such greps/commits twice) WITHOUT fail-opening on real invocations. The
    # token check uses a real shell lexer (shlex), not a regex quote-strip, because
    # a strip is fooled by an apostrophe-in-a-word pairing with a later quoted arg
    # (`echo it's; pytest -k 'x'`) — that deleted the real token and fail-opened a
    # genuine uncapped run (Gate-2 HIGH, run_5511508d).
    if not (
        _PYTEST_INVOCATION_RE.search(command)
        and _pytest_token_is_command_word(command)
    ):
        return {"decision": "approve"}

    # DENY: piped to a pager (check the unquoted form only).
    if _PIPE_TO_PAGER_RE.search(_strip_quoted(command)):
        logger.warning("[BLOCKED] pytest piped to pager: %s", command[:80])
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "pytest piped into tail/head is denied (R9): a slow run gets "
                    "auto-backgrounded and the foreground returns EMPTY output, "
                    "which reads as a hang. Redirect to a file and Read it: "
                    "`pytest ... --timeout=60 > /tmp/t.txt 2>&1` then Read /tmp/t.txt."
                ),
            }
        }

    # DENY: no WALL-CLOCK wrapper. A pytest without `gtimeout N`/`timeout N` can
    # run for minutes, get auto-backgrounded, return an empty foreground, and
    # read as a hang (C040, 12th CLASS-B). A per-test `--timeout` does NOT bound
    # the whole command, so it does not satisfy this. Fail-closed (R9 / PIT10).
    # Checked on the RAW command (NOT quote-stripped): the sanctioned perl-alarm
    # cap — `perl -e 'alarm 90; exec @ARGV' ... pytest` — carries its `alarm N` /
    # `exec @ARGV` cap tokens INSIDE the single-quoted -e arg; stripping quotes here
    # would drop them and wrongly DENY the very form we mandate (run_5511508d).
    if not _WALLCLOCK_BOUND_PYTEST_RE.search(command):
        logger.warning("[BLOCKED] pytest without wall-clock wrapper: %s", command[:80])
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "pytest 调用 → 必须被 gtimeout/timeout <N> 包裹,否则 DENY。 "
                    "per-test --timeout 不算数(它不挡 wall-clock)。 (R9) A no-wrapper "
                    "run gets auto-backgrounded → empty foreground → reads as a hang "
                    "(C040). gtimeout/timeout aren't installed on this box — use the "
                    "perl-alarm fallback (sanctioned shape): `perl -e 'alarm 90; exec "
                    "@ARGV' python -m pytest <smallest scope> --timeout=60 "
                    "-p no:cacheprovider > /tmp/t.txt 2>&1; echo exit=$?` then Read "
                    "/tmp/t.txt. If it can't return in 90s, the answer is SMALLER "
                    "scope, not a longer wait."
                ),
            }
        }

    return {"decision": "approve"}


# ---------------------------------------------------------------------------
# adversarial-commit gate — R1 structural backstop (PreToolUse, Bash)
# ---------------------------------------------------------------------------
# R1 (AGENT.md): "NO COMMIT WITHOUT ADVERSARIAL REVIEW FIRST" — every code change,
# pipeline OR direct, MUST spawn an adversarial sub-agent BEFORE commit (sequence
# code→test→adversarial→fix→commit). This was prose-only and got skipped: CLASS A
# skip-attempt #12 (2026-08-10, this workspace) — the agent did code→test then asked
# "commit?" with no adversarial pass; the pass, once forced, found a real HIGH
# governance bug. EVOLUTION.md's own verdict on prose gates: "Mechanical gates catch
# specific KNOWN bypass routes." This is that backstop for the ONE known, stable,
# binary-detectable route: a `git commit` with zero SubagentStop evidence this session.
#
# EXPLICIT SCOPE (this is a guardrail, NOT the P1 fix — do not mistake it for one):
#   • It proves an ADVERSARIAL sub-agent COMPLETED this session (a session_<sid>_adv_
#     marker exists, written at SubagentStop by runtime_hooks when the completing
#     sub-agent's agent_type/spawn-prompt showed adversarial-review intent). It does
#     NOT prove that review covered THIS diff — an adversarial review of an EARLIER
#     diff in the same session still satisfies it (session-scoped, not diff-scoped;
#     diff-relevance is the deliberately-out-of-scope "Plan A"). Threat model = the
#     手滑 "test passed → commit" reflex that #12 was, NOT a malicious bypass.
#   • run_df2668b4 TIGHTENED this: it used to accept ANY sub-agent marker, so an
#     Explore/investigation agent (spawned to locate code) satisfied it — the exact
#     hole that let this session commit two un-reviewed diffs. Now only an _adv_
#     marker counts.
#   • Marker producer already exists: runtime_hooks.create_agent_tool_audit_hook
#     writes STATE_DIR/pipeline_agent_audit/session_<sid>_<ts>.marker on EVERY
#     SubagentStop. This gate only READS it. No new producer, no coupling added.
#   • FAIL-OPEN on its own errors (missing session id, unreadable dir, any exception)
#     — a guard that false-blocks a legit commit is worse than the skip it prevents.
#     SWARM_ADVERSARIAL_GATE_FORCE=1 is the sanctioned explicit bypass (logged).
#   • Scope is `git commit` only (reusing the segment-split + wrapper-strip discipline
#     of _is_irreversible_external_op). Non-commit git (add/status/push), non-git,
#     and the amuse-case `git commit --amend` all count as commits (still need review).

_AGENT_AUDIT_DIR = get_app_data_dir() / "state" / "pipeline_agent_audit"


def _segment_is_git_commit(tokens: list[str]) -> bool:
    """True iff this ONE already-separator-split segment is a `git commit`.

    Mirrors _segment_is_irreversible's wrapper/env-assignment strip + git global-option
    skip so `env X=y git -C path commit`, `sudo git commit`, `git -c k=v commit` all
    match. Only the `commit` subcommand — NOT add/status/log/diff/push."""
    _WRAPPERS = {"sudo", "env", "command", "nice", "nohup", "time"}
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if "=" in t and t.split("=", 1)[0].replace("_", "").isalnum() and not t.startswith("-"):
            i += 1
        elif t in _WRAPPERS:
            i += 1
        else:
            break
    tokens = tokens[i:]
    if not tokens or tokens[0] != "git":
        return False
    k = 1
    while k < len(tokens):
        t = tokens[k]
        if t in ("-C", "-c", "--namespace"):
            k += 2  # option that consumes a value
        elif t.startswith(("--git-dir", "--work-tree", "--namespace=", "-C", "-c")):
            k += 1  # =form, self-contained
        elif t.startswith("-"):
            k += 1  # any other global flag
        else:
            break  # the subcommand
    return k < len(tokens) and tokens[k] == "commit"


def _command_has_git_commit(command: str) -> bool:
    """True iff any shell segment of *command* is a `git commit`. Reuses the same
    newline→';', glued-';' resplit, and separator-split normalization as
    _is_irreversible_external_op so chained/multiline commits are seen. Fail-safe:
    unparseable input → substring fallback (only a real `git commit` token pattern)."""
    try:
        all_tokens = shlex.split(command.replace("\n", " ; ").replace("\r", " ; "))
    except ValueError:
        return bool(re.search(r"\bgit\b[^;&|]*\bcommit\b", command))
    if not all_tokens:
        return False
    _resplit: list[str] = []
    for tok in all_tokens:
        if ";" in tok and tok != ";":
            parts = tok.split(";")
            for idx, p in enumerate(parts):
                if p:
                    _resplit.append(p)
                if idx < len(parts) - 1:
                    _resplit.append(";")
        else:
            _resplit.append(tok)
    all_tokens = _resplit
    _SEPARATORS = {";", "&&", "||", "|", "&"}
    segments: list[list[str]] = []
    current: list[str] = []
    for tok in all_tokens:
        if tok in _SEPARATORS:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        segments.append(current)
    return any(_segment_is_git_commit(seg) for seg in segments)


# NOTE (run_df2668b4 → run_f4b9ae6f): the old _session_has_subagent_evidence
# (accepted ANY session_<sid>_ marker) was REMOVED — it let an Explore agent satisfy
# the gate; do NOT reintroduce it. run_df2668b4 tightened the gate to require an
# ADVERSARIAL marker (existence). run_f4b9ae6f (Plan A) tightened further: the LIVE
# GATE now calls _session_adversarial_coverage (below) to bind the review to the
# committed PATH-SET. `_session_has_adversarial_evidence` is the earlier
# existence-only predicate — retained ONLY because its unit tests pin the `_adv_`
# infix + fail-open contract; it is NOT the gate's decision path anymore.


def _session_has_adversarial_evidence(session_id: str) -> bool:
    """Existence-only predicate: True iff an ADVERSARIAL-review marker exists for
    *session_id*. NOTE: NOT the live gate check anymore — the gate uses
    _session_adversarial_coverage (path-set binding, run_f4b9ae6f). Retained for
    its unit tests, which pin the `_adv_` infix match (a base marker
    session_<sid>_<numeric ts> can never contain `_adv_`) + the fail-OPEN contract
    (missing session_id / FS error → True)."""
    if not session_id:
        return True  # cannot scope → fail open (never block on missing identity)
    try:
        if not _AGENT_AUDIT_DIR.is_dir():
            return False
        # Exact shape: session_<sid>_adv_<digits>.marker. A `startswith` needle
        # would let a DIFFERENT session whose id begins with "<sid>_adv_run…" match
        # this sid's evidence (prefix ambiguity, REVIEW LOW-3). Bind the boundary.
        pat = re.compile(rf"^session_{re.escape(session_id)}_adv_\d+\.marker$")
        return any(pat.match(p.name) for p in _AGENT_AUDIT_DIR.iterdir())
    except OSError:
        return True  # fail open on FS error


_GATE_GIT_TIMEOUT = 3  # per git subprocess — a gate that shells git must never hang the commit
# Outer wall-clock cap on the whole coverage helper. _pending_commit_paths can run up
# to 4 sequential git subprocesses (2× rev-parse for a -C retarget check + diff --cached
# + diff for -a), each bounded by _GATE_GIT_TIMEOUT. The outer wait_for MUST exceed the
# worst-case inner sum, or it fires first and orphans the still-running git children
# (Gate-2 MED timeout-incoherence). 4×3 + 3 buffer = 15s ceiling; git on a warm repo is
# ~0.05s/call so this only bites a genuinely wedged/contended repo → fail-open.
_GATE_COVERAGE_TIMEOUT = _GATE_GIT_TIMEOUT * 4 + 3


def _gate_repo_root_for(dir_path: str) -> str | None:
    """Canonical repo-root resolver — MUST match runtime_hooks._repo_root_for so
    the gate's absolute paths compare byte-for-byte with the marker's. realpath of
    `git -C <dir> rev-parse --show-toplevel`, or None if not a repo / git error."""
    if not dir_path:
        return None
    try:
        r = subprocess.run(
            ["git", "-C", dir_path, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=_GATE_GIT_TIMEOUT,
        )
        if r.returncode != 0:
            return None
        top = r.stdout.strip()
        return os.path.realpath(top) if top else None
    except (OSError, subprocess.SubprocessError):
        return None


# Sentinel: the commit retargets git at another repo via a global option
# (`git -C <other> commit`); we cannot bind coverage there → the gate DENYs
# (never fail-open) — Gate-2 HIGH (git -C bypass).
_PENDING_CROSS_REPO = object()


def _pending_commit_paths(dir_path: str, command: str):
    """The set of ABSOLUTE paths a `git commit` is about to commit; or None if
    uncomputable (not a repo / git error / timeout → gate fails OPEN); or the
    _PENDING_CROSS_REPO sentinel if a global option retargets another repo (→ DENY).

    Folds EVERY sweep form: staged (`--cached`), `-a/--all` working-tree, positional
    pathspec, `-o/--only`. Git-diff paths are repo-root-relative; a positional
    pathspec / `-o` path is COMMITTER-CWD-relative (git semantics), NOT root-relative."""
    import shlex as _shlex
    root = _gate_repo_root_for(dir_path)
    if root is None:
        return None
    try:
        tokens = _shlex.split(command)
    except ValueError:
        tokens = command.split()

    # GLOBAL options BEFORE `commit` retarget git: `-C <dir>` / `--git-dir=` /
    # `--work-tree=` can point at ANOTHER repo → the pending set computed against
    # dir_path would be wrong (Gate-2 HIGH bypass: `git -C /other commit` unreviewed).
    # If a retarget resolves to a DIFFERENT root, we cannot bind it → DENY sentinel.
    try:
        _ci = tokens.index("commit")
    except ValueError:
        _ci = len(tokens)
    _g = 1  # skip tokens[0] == "git"
    while _g < _ci:
        gt = tokens[_g]
        if gt in ("-C", "--git-dir", "--work-tree") and _g + 1 < _ci:
            other = _gate_repo_root_for(tokens[_g + 1])
            if other is not None and other != root:
                return _PENDING_CROSS_REPO
            _g += 2; continue
        if gt.startswith(("--git-dir=", "--work-tree=")):
            other = _gate_repo_root_for(gt.split("=", 1)[1])
            if other is not None and other != root:
                return _PENDING_CROSS_REPO
        if gt == "-c" and _g + 1 < _ci:
            _g += 2; continue  # -c key=val consumes a value
        _g += 1

    def _diff(*extra: str) -> list[str] | None:
        try:
            r = subprocess.run(
                ["git", "-C", root, "diff", "--name-only", *extra],
                capture_output=True, text=True, timeout=_GATE_GIT_TIMEOUT,
            )
            if r.returncode != 0:
                return None
            return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
        except (OSError, subprocess.SubprocessError):
            return None

    root_rels: set[str] = set()  # repo-root-relative (git diff output)
    cwd_paths: list[str] = []     # committer-cwd-relative (positional pathspec / -o)
    staged = _diff("--cached")
    if staged is None:
        return None  # git couldn't answer → uncomputable → caller fails open
    root_rels.update(staged)

    # Decode the commit's flags/pathspec. Walk tokens AFTER the `commit` subcommand.
    dash_a = False
    try:
        ci = tokens.index("commit")
    except ValueError:
        ci = 0
    i = ci + 1
    while i < len(tokens):
        t = tokens[i]
        if t == "--":
            cwd_paths.extend(tokens[i + 1:]); break
        if t.startswith("--"):
            name, eq, inline_val = t.partition("=")
            if name == "--all":
                dash_a = True
            elif name == "--only":
                if eq:
                    cwd_paths.append(inline_val)               # --only=path
                elif i + 1 < len(tokens):
                    cwd_paths.append(tokens[i + 1]); i += 1    # --only path
            elif name in ("--message", "--author", "--date", "--reuse-message",
                          "--reedit-message", "--fixup", "--squash", "--template",
                          "--cleanup", "--file", "--pathspec-from-file"):
                if not eq and i + 1 < len(tokens):
                    i += 1  # space form consumes next token (equals form does not)
            # --gpg-sign[=keyid] / --amend / --no-edit / --signoff → no path arg
        elif t.startswith("-") and len(t) > 1:
            # Decode a short-flag cluster. VALUE flags whose value is a SEPARATE token
            # when trailing: -m -C -c -F -o -t -u. NOT -S (its keyid is OPTIONAL and
            # must be STUCK, -Skey) and NOT -s (--signoff, no value) — consuming a next
            # token for either would swallow a real pathspec (Gate-2 MED: -S secret.py).
            cluster = t[1:]
            j = 0
            consumed_next = False
            while j < len(cluster):
                c = cluster[j]
                if c == "a":
                    dash_a = True; j += 1; continue
                if c in ("m", "C", "c", "F", "o", "t", "u"):
                    inline = cluster[j + 1:]  # rest of cluster is this flag's value
                    if c == "o":  # -o <path> is a pathspec restriction we must track
                        if inline:
                            cwd_paths.append(inline)
                        elif i + 1 < len(tokens):
                            cwd_paths.append(tokens[i + 1]); consumed_next = True
                    else:
                        if not inline and i + 1 < len(tokens):
                            consumed_next = True  # value is the next token; skip it
                    break  # rest of cluster (if any) was this flag's value
                # value-less / optional-stuck-value short flag (-q -v -e -s -S…) → skip
                j += 1
            if consumed_next:
                i += 1
        else:
            cwd_paths.append(t)  # positional pathspec (cwd-relative in git)
        i += 1

    if dash_a:
        wt = _diff()  # tracked working-tree modifications -a will sweep
        if wt is None:
            return None
        root_rels.update(wt)

    out: set[str] = set()
    for rel in root_rels:  # git-diff output → relative to repo ROOT
        ap = rel if os.path.isabs(rel) else os.path.join(root, rel)
        out.add(os.path.realpath(ap))
    for p in cwd_paths:    # positional pathspec / -o → relative to committer CWD
        ap = p if os.path.isabs(p) else os.path.join(dir_path or root, p)
        out.add(os.path.realpath(ap))
    return out


def _session_adversarial_coverage(session_id: str):
    """Return (has_marker, covered_abs_paths, has_unbounded) for this session's
    adversarial markers. `has_unbounded` is True iff SOME marker lacks the
    reviewed_paths KEY (git-unavailable at review time = unbounded, back-compat).
    A marker with reviewed_paths == [] contributes the EMPTY set to `covered` and
    does NOT set has_unbounded — the []-vs-key-absent distinction is by KEY
    PRESENCE, never truthiness (Gate-1 round-2 #3). Fail-open signalled by the
    caller; this returns (True, set(), True) on OSError so the gate approves."""
    covered: set[str] = set()
    has_marker = False
    has_unbounded = False
    if not session_id:
        return (True, covered, True)  # cannot scope → treat as unbounded (fail-open)
    try:
        if not _AGENT_AUDIT_DIR.is_dir():
            return (False, covered, False)
        pat = re.compile(rf"^session_{re.escape(session_id)}_adv_\d+\.marker$")
        for p in _AGENT_AUDIT_DIR.iterdir():
            if not pat.match(p.name):
                continue
            has_marker = True
            try:
                data = json.loads(p.read_text())
            except (OSError, json.JSONDecodeError):
                has_unbounded = True  # unreadable marker → don't false-block
                continue
            if "reviewed_paths" not in data:      # KEY ABSENT → unbounded
                has_unbounded = True
            else:
                covered.update(data.get("reviewed_paths") or [])  # [] contributes nothing
        return (has_marker, covered, has_unbounded)
    except OSError:
        return (True, covered, True)  # FS error → fail open


def create_adversarial_commit_gate(session_context: dict[str, Any]):
    """Factory: PreToolUse (Bash) gate that DENYs `git commit` unless an
    ADVERSARIAL-review sub-agent COMPLETED this session AND its review covered the
    paths being committed (Plan A diff-binding). Coverage = the pending-commit
    path-set (staged + -a + pathspec + -o) must be a subset of the union of the
    session's adversarial markers' reviewed_paths — UNLESS some marker is path-less
    (git-unavailable at review time → unbounded, back-compat) or the pending set is
    uncomputable (not a repo / git error → fail-open). A base SubagentStop marker
    (any sub-agent ran) never suffices. session_context is the mutable dict
    SessionUnit updates with the live sdk_session_id (read at call time)."""

    async def _gate(
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        if input_data.get("tool_name") != "Bash":
            return {"decision": "approve"}
        command = (input_data.get("tool_input", {}) or {}).get("command", "") or ""
        if not command or not _command_has_git_commit(command):
            return {"decision": "approve"}

        if os.environ.get("SWARM_ADVERSARIAL_GATE_FORCE") == "1":
            logger.warning("[adversarial-gate] FORCE override — commit without subagent "
                           "evidence: %s", command[:80])
            return {"decision": "approve"}

        session_id = session_context.get("sdk_session_id", "") or ""
        cwd = input_data.get("cwd", "") or ""

        # Coverage decision (all git subprocesses run off-loop + are per-call
        # timeout-bounded so this gate can never hang the commit — bash_syntax_guard
        # discipline). Order of the fail-OPEN branches is load-bearing.
        try:
            has_marker, covered, has_unbounded = await asyncio.wait_for(
                executors.run_in("subprocess", _session_adversarial_coverage, session_id),
                timeout=_GATE_COVERAGE_TIMEOUT,
            )
        except Exception:  # incl. asyncio.TimeoutError; ANY gate-infra failure (even
            return {"decision": "approve"}  # a bug in the helper) fails OPEN by design

        if not has_marker:
            pass  # → DENY below (Plan B parity: no adversarial review at all)
        elif has_unbounded:
            return {"decision": "approve"}  # a path-less marker = unbounded (back-compat)
        else:
            # Bind to the diff: every pending path must be covered. Pending set
            # uncomputable (not a repo / git error / timeout) → fail OPEN.
            try:
                pending = await asyncio.wait_for(
                    executors.run_in("subprocess", _pending_commit_paths, cwd, command),
                    timeout=_GATE_COVERAGE_TIMEOUT,
                )
            except Exception:  # incl. asyncio.TimeoutError → fail open (never hang)
                return {"decision": "approve"}  # fail open on gate-infra failure
            if pending is _PENDING_CROSS_REPO:
                # A `git -C <other>` global option retargets a repo we can't bind →
                # DENY (never fail-open a cross-repo commit) — Gate-2 HIGH bypass.
                pass  # → DENY below
            elif pending is None:
                return {"decision": "approve"}  # uncomputable → fail open
            elif not pending:
                # Nothing to bind (empty pending, e.g. a no-op/amend with no diff) →
                # existence-only (Plan B parity), NOT a vacuous all([]) coverage-pass.
                return {"decision": "approve"}
            elif pending <= covered:  # subset → fully reviewed
                return {"decision": "approve"}
            # else: some pending path was never adversarially reviewed → DENY below

        uncovered = ""
        try:
            if has_marker and not has_unbounded and isinstance(pending, set):
                extra = sorted(p for p in pending if p not in covered)
                if extra:
                    uncovered = " Uncovered path(s): " + ", ".join(extra[:5])
            elif pending is _PENDING_CROSS_REPO:
                uncovered = " (commit retargets another repo via -C/--git-dir — cannot bind coverage)"
        except Exception:
            uncovered = ""

        logger.warning("[BLOCKED] git commit not covered by an adversarial review this "
                       "session (session=%s): %s", session_id, command[:80])
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "git commit → DENY: R1 requires an adversarial review of THIS diff before "
                    "commit. Either NO adversarial sub-agent completed this session, or one did "
                    "but it did not review the file(s) being committed." + uncovered + " "
                    "'This change is too simple for adversarial' IS the signal it's needed "
                    "(CLASS A skip-attempt #12, 2026-08-10). FIX: spawn an adversarial sub-agent "
                    "(Agent/Task tool) to REFUTE this diff — have it read the changed files and "
                    "hunt bugs/regressions/governance violations — fix what it finds, THEN commit. "
                    "(Deliberate exception, e.g. a docs-only or revert commit: set "
                    "SWARM_ADVERSARIAL_GATE_FORCE=1 for that one command.)"
                ),
            }
        }

    return _gate


# ---------------------------------------------------------------------------
# bash-syntax guard — hang prevention via parse-check (PreToolUse, Bash)
# ---------------------------------------------------------------------------
# A syntactically INCOMPLETE bash command (unterminated quote/backtick, unclosed
# if/brace/paren) makes bash enter PS2 continuation mode WAITING ON STDIN. In a
# headless session no stdin arrives, so the command BLOCKS FOREVER — run-real, an
# unterminated `echo "=== jobs/bedro` ran 12 minutes (it escaped the 120s
# foreground timeout via harness auto-backgrounding) inside a pipeline step.
#
# None of the existing layers catches this: the 120s foreground timeout is
# wall-clock disaster-recovery (O030 — it can't tell HANG from SLOW, and is
# escaped by auto-backgrounding); background_command_guard only matches EXPLICIT
# backgrounding; dangerous_command_gate pattern-MATCHES, it does not PARSE.
#
# Fix (P7: defense outside the agent — prose "close your quotes" cannot hold):
# run `bash -n` (parse-only, NO execution, ~ms) before the command runs. The set
# of commands `bash -n` rejects (exit!=0) is EXACTLY the PS2-continuation set ==
# the hang set (verified live: unterminated "/'/`/if/{ → exit 2; valid
# multiline/heredoc/$()/quoted/long → exit 0). So denying exit!=0 prevents the
# hang at ZERO false-kill cost for valid commands.
#
# FAIL-OPEN is the cardinal rule: the guard must NEVER block a command because of
# its OWN failure (bash missing, the check timing out, any unexpected error). A
# guard that false-kills legitimate work is worse than the hang it prevents — so
# every non-"clean syntax error" path APPROVES. The bash -n subprocess is itself
# wrapped in a short wait_for cap + killed-on-timeout so the guard cannot become a
# new hang source (the exact irony this guard exists to prevent).
#
# NOTE on the one KNOWN gap (documented, not hidden): an UNTERMINATED HEREDOC
# (`cat <<EOF` with no closing EOF) passes `bash -n` (exit 0) — but it also does
# NOT hang in headless mode (bash reads the heredoc body to end-of-string, it does
# not wait on a TTY), so approving it is correct. The hang set and the bash -n
# exit!=0 set coincide; heredoc-unterminated is in neither.

# Shell used for the parse check. CRITICAL: it MUST be the SAME shell the Bash
# tool actually executes commands in, or we false-kill valid syntax. On macOS the
# Claude Code Bash tool runs **zsh** ($SHELL=/bin/zsh, ZSH_VERSION set) — and a
# bare `/bin/bash -n` REJECTS valid zsh syntax (e.g. a one-line brace function
# `foo() { echo hi }`, zsh `for x { }` loops), which would DENY legitimate
# commands (Gate-2 adversarial HIGH, run_07fd1d8f — the builder hardcoded
# /bin/bash and never verified the exec shell). zsh -n catches the SAME real-hang
# set (unterminated quote/backtick/if/brace → exit!=0, verified) AND approves
# zsh-valid syntax, so checking with the exec shell is both safer (no false-kill)
# and complete (still catches the hang set). Resolve $SHELL first, then fall back
# to zsh then bash. Resolved once at import (cheap, stable).
def _resolve_syntax_check_shell() -> str:
    for cand in (os.environ.get("SHELL"), "/bin/zsh", "/bin/bash", "bash"):
        if cand and (cand in ("bash",) or os.path.exists(cand)):
            return cand
    return "bash"


_SYNTAX_CHECK_SHELL = _resolve_syntax_check_shell()
# A pathological command (e.g. ~1M-deep `$()` nesting) can make the parse check
# spin a core; cap input size so it never reaches the shell. Real commands are
# tiny; 256KB is orders of magnitude above any legit command. (Gate-2 LOW.)
_SYNTAX_CHECK_MAX_CMD_BYTES = 256 * 1024
# The parse check is sub-millisecond; this cap is pure defense-in-depth so the
# guard itself can never hang. On timeout we KILL the subprocess and fail-OPEN.
_BASH_SYNTAX_CHECK_TIMEOUT_S = 1.0


async def bash_syntax_guard(
    input_data: dict[str, Any],
    tool_use_id: str | None,
    context: Any,
) -> dict[str, Any]:
    """PreToolUse (Bash): DENY a command the EXEC-SHELL's `-n` parse rejects as
    syntactically incomplete (the headless-hang set); APPROVE everything else,
    fail-OPEN.

    Prevents the unterminated-quote / unclosed-block hang (a PARSE-incomplete
    command blocks forever waiting on stdin that never comes — run-real 12-minute
    hang). The check uses the SAME shell the Bash tool runs (`_SYNTAX_CHECK_SHELL`,
    zsh on macOS) so it never false-kills valid exec-shell syntax. The DENY set ==
    `<shell> -n` exit!=0 == the parse-continuation set; valid commands (incl.
    multiline/heredoc/$()/quoted/long, and zsh brace functions) exit 0, approved.

    SCOPE (honest): this catches PARSE-incomplete hangs only. A syntactically
    VALID command that blocks on stdin at RUNTIME (`cat`, `read`, `grep` w/o a
    file) parses fine (exit 0) and is approved — that is a different hang class,
    not the incident this guard targets, and not claimed to be covered.

    Fail-OPEN on EVERYTHING that is not a clean, reproduced syntax error:
    non-Bash, empty command, oversized command, valid syntax, shell missing,
    check timeout, or any unexpected exception. The guard must never block a
    legitimate command because of its OWN infra failure — worse than the hang.
    """
    if input_data.get("tool_name") != "Bash":
        return {"decision": "approve"}
    command = (input_data.get("tool_input", {}) or {}).get("command", "") or ""
    if not command:
        return {"decision": "approve"}
    # Oversized command → fail-open without invoking the shell (a pathological
    # deeply-nested command could otherwise pin a core for the whole cap). Real
    # commands are KB at most; 256KB is far above any legit command. (Gate-2 LOW.)
    cmd_bytes = command.encode("utf-8", errors="replace")
    if len(cmd_bytes) > _SYNTAX_CHECK_MAX_CMD_BYTES:
        logger.warning(
            "[bash_syntax_guard] command %d bytes > %d cap — failing open",
            len(cmd_bytes), _SYNTAX_CHECK_MAX_CMD_BYTES,
        )
        return {"decision": "approve"}

    try:
        proc = await asyncio.create_subprocess_exec(
            _SYNTAX_CHECK_SHELL,
            "-n",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(
                proc.communicate(input=cmd_bytes),
                timeout=_BASH_SYNTAX_CHECK_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            # Cancellable cap fired — kill the subprocess (no zombie) and fail-open.
            try:
                proc.kill()
                await proc.wait()
            except Exception:  # noqa: BLE001 — best-effort reap; never block on it
                pass
            logger.warning(
                "[bash_syntax_guard] %s -n exceeded %.1fs — failing open",
                _SYNTAX_CHECK_SHELL, _BASH_SYNTAX_CHECK_TIMEOUT_S,
            )
            return {"decision": "approve"}
    except Exception as e:  # noqa: BLE001
        # shell missing / spawn failure / anything unexpected → FAIL-OPEN. Never
        # block a real command on the guard's own infra failure. (GC19: log the
        # exception type, don't swallow silently.)
        logger.warning(
            "[bash_syntax_guard] parse check failed (%s: %s) — failing open",
            type(e).__name__,
            e,
        )
        return {"decision": "approve"}

    # exit 0 → parseable (incl. valid multiline/heredoc/$()/quoted) → APPROVE.
    if proc.returncode == 0:
        return {"decision": "approve"}

    # exit!=0 → syntax error == the headless-hang set → DENY, echo the shell's own
    # diagnostic so the agent rewrites the command instead of hanging on it.
    diagnostic = (stderr or b"").decode("utf-8", errors="replace").strip()
    # Keep the reason bounded — the first lines carry the unexpected-EOF message.
    if len(diagnostic) > 400:
        diagnostic = diagnostic[:400] + " …"
    logger.warning(
        "[BLOCKED] shell syntax error (would hang on stdin): %s", command[:80]
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "This Bash command has a SYNTAX ERROR — the shell's `-n` parse "
                "rejected it. In headless mode an unterminated quote/backtick or "
                "unclosed block makes the shell wait on stdin that never arrives, "
                "so the command would HANG indefinitely (not error fast). Rewrite "
                "it: close every quote/backtick, balance if/then/fi and braces, "
                "put ONE command per line, and move multi-line logic into a script "
                "file you then execute. The parse check said:\n"
                f"{diagnostic or '(no stderr captured)'}"
            ),
        }
    }


# ---------------------------------------------------------------------------
# Image-read dedup guard — PreToolUse(Read), per-session token-bloat backstop
# ---------------------------------------------------------------------------

# Image extensions the Read tool pulls into model context as a (large) vision
# payload. A single hi-DPI slide can cost tens of thousands of tokens, and the
# same unchanged image re-Read N times re-injects that payload N times
# (observed: s8.png/s9.png each read 5×, prompt 155K→235-596K). Dedup is by
# IDENTITY (path + mtime), never content — no whack-a-mole string matching.
_IMAGE_READ_EXTS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".heic", ".svg"}
)


def create_image_read_dedup_guard(
    session_context: dict[str, Any] | None = None,
) -> Callable[..., Any]:
    """Factory: returns a PreToolUse(Read) hook that dedupes redundant image reads.

    The returned closure owns a private ``{abspath: st_mtime_ns}`` cache. Because
    ``build_hooks`` runs once per session (``prompt_builder.build_system_prompt``),
    a fresh closure per session means the cache is per-session BY CONSTRUCTION —
    it can never false-deny a different session's first read (no module-global
    mutable state, no manual session keying). Mirrors ``create_dangerous_command_gate``.

    Behavior (fail-SAFE — anything uncertain is approved, never denied):
      • non-Read tool / no file_path / non-image extension → approve
      • path cannot be stat'd (missing/permission) → approve (can't dedup what we
        can't identify; never crash, never false-deny)
      • Read carries an ``offset`` or ``limit`` param → approve. This is the
        ESCAPE VALVE: a deliberate partial/forced re-read (e.g. the agent can no
        longer see an image evicted by soft-compaction) is never blocked.
      • image seen before at the SAME mtime → DENY with an informative stub. The
        ``permissionDecisionReason`` IS the substitute output — the SDK forwards
        it to the model (types.py "that reason is forwarded"), and the first
        read's payload is already above in the conversation.
      • image not seen, or seen at a DIFFERENT mtime (regenerated) → record + approve.

    Why deny (not "return a stub") — a PreToolUse hook cannot synthesize a fake
    tool result; it can only allow/deny/ask. Deny + reason achieves the goal: the
    model gets the reason instead of the ~tens-of-K payload, having already seen
    the image once. Token-bloat is structurally bounded, not politely requested.
    """
    _seen: dict[str, int] = {}

    async def image_read_dedup_guard(
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        if input_data.get("tool_name") != "Read":
            return {"decision": "approve"}
        tool_input = input_data.get("tool_input", {}) or {}
        file_path = tool_input.get("file_path") or tool_input.get("path", "")
        if not file_path:
            return {"decision": "approve"}

        # Escape valve: a deliberate partial/forced re-read is never deduped.
        if tool_input.get("offset") is not None or tool_input.get("limit") is not None:
            return {"decision": "approve"}

        # Image extension only — everything else passes untouched.
        try:
            ext = Path(file_path).suffix.lower()
        except (TypeError, ValueError):
            return {"decision": "approve"}
        if ext not in _IMAGE_READ_EXTS:
            return {"decision": "approve"}

        # Identity = (resolved abspath, mtime_ns). Unstat-able → fail-safe approve.
        # ValueError guards an embedded-null-byte path (Path() + .suffix succeed,
        # but .resolve()/os.stat raise ValueError, not OSError) — without it the
        # hook would crash instead of failing safe (Gate-2 operational HIGH).
        try:
            abspath = str(Path(file_path).resolve())
            mtime_ns = os.stat(abspath).st_mtime_ns
        except (OSError, ValueError):
            return {"decision": "approve"}

        if _seen.get(abspath) == mtime_ns:
            logger.info("[DEDUP] redundant image re-read denied: %s", abspath[:80])
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "This image was already read earlier in THIS conversation and "
                        "has not changed since — it is still visible above. Re-reading "
                        "it re-injects the full image payload (often tens of thousands "
                        "of tokens) for zero new information. Refer to the copy above. "
                        "If you genuinely can no longer see it (e.g. it scrolled out of "
                        "context), re-read with an explicit offset/limit param to force it."
                    ),
                }
            }

        _seen[abspath] = mtime_ns
        return {"decision": "approve"}

    return image_read_dedup_guard


# ---------------------------------------------------------------------------
# Governance file gate — Three-Layer Governance enforcement
# ---------------------------------------------------------------------------

# Tier 1: Constitutional (hard gate — require classification before write)
GOVERNANCE_TIER1_PATTERNS: list[str] = [
    "backend/context/SOUL.md",
    "backend/context/AGENT.md",
    "*/.context/SOUL.md",
    "*/.context/AGENT.md",
]

# Tier 2: Statutory (soft gate — advise, don't block)
GOVERNANCE_TIER2_PATTERNS: list[str] = [
    "*/.context/STEERING.md",
    "backend/context/STEERING.md",
    "*/s_autonomous-pipeline/stages/*.md",
]


def _match_governance_tier(file_path: str) -> int:
    """Return governance tier (1, 2, or 0 for non-governance) for a file path.

    Checks both exact matches and backup/temp file variants (.bak, ~, .tmp)
    to prevent bypass via intermediate files.
    """
    if not file_path:
        return 0
    # Normalize path for matching — strip common backup suffixes
    norm = file_path.replace("\\", "/")
    # Strip backup/temp suffixes to catch .bak, ~, .tmp, .swp variants
    base_norm = re.sub(r"(\.bak|\.tmp|\.swp|~)$", "", norm)

    for pattern in GOVERNANCE_TIER1_PATTERNS:
        suffix = pattern.lstrip("*/")
        if fnmatch.fnmatch(norm, pattern) or norm.endswith(suffix):
            return 1
        # Also check base (stripped) path
        if base_norm != norm and (fnmatch.fnmatch(base_norm, pattern) or base_norm.endswith(suffix)):
            return 1
    for pattern in GOVERNANCE_TIER2_PATTERNS:
        suffix = pattern.lstrip("*/")
        if fnmatch.fnmatch(norm, pattern) or norm.endswith(suffix):
            return 2
        if base_norm != norm and (fnmatch.fnmatch(base_norm, pattern) or base_norm.endswith(suffix)):
            return 2
    return 0


def create_governance_file_gate() -> Callable[..., Any]:
    """Factory: returns an async PreToolUse hook that intercepts governance file edits.

    Tier 1 (SOUL/AGENT): Outputs classification reminder — ADVISE mode.
    Tier 2 (STEERING/pipeline docs): Outputs soft reminder.

    Note: Initially advisory (soft gate). Phase 4 will upgrade Tier 1 to BLOCK.
    """

    async def governance_file_gate(
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        tool_name = input_data.get("tool_name", "")
        if tool_name not in ("Edit", "Write"):
            return {"decision": "approve"}

        tool_input = input_data.get("tool_input", {})
        file_path = tool_input.get("file_path", "")
        tier = _match_governance_tier(file_path)

        if tier == 0:
            return {"decision": "approve"}

        tier_label = "CONSTITUTIONAL (Tier 1)" if tier == 1 else "STATUTORY (Tier 2)"
        reminder = (
            f"⚠️ GOVERNANCE GATE [{tier_label}]: Modifying governance file.\n"
            f"Before proceeding, ensure:\n"
            f"  1. Classify: Principle / Rule / Gate?\n"
            f"  2. Parent: P1-P7?\n"
            f"  3. Conflict/Duplicate check done?\n"
            f"  4. Budget: SOUL ≤12 principles, AGENT ≤25 rules, STEERING ≤15"
        )

        logger.info(
            "[GOVERNANCE] Tier %d gate fired for %s (tool=%s)",
            tier, file_path, tool_name,
        )

        # Advisory mode: approve but include reminder in additionalContext
        return {
            "decision": "approve",
            "additionalContext": reminder,
        }

    return governance_file_gate


# ---------------------------------------------------------------------------
# inclusive_term_guard — WARN on non-inclusive terminology in written content
# ---------------------------------------------------------------------------
# Amazon's InclusiveTechScanner (CRUX) caught 8 "whitelist" findings on
# CR-291472994 that NONE of our own review layers (pipeline Gate-2,
# s_code-review, s_internal-crux-review) would have caught. This closes that gap
# on the WRITE path — an in-session nudge BEFORE the term reaches a commit,
# complementing (never replacing) the post-commit scanner.
#
# Design (Gate-1 reviewed, run_74567b08):
#   • WARN-ONLY. Wording is a style/inclusivity concern, NOT security — the guard
#     ALWAYS returns decision="approve" and NEVER emits a deny/block. STEERING #2:
#     a disaster-recovery/blocking control must not truncate real work over a word.
#   • Self-contained try/except + str() coercion: this hook is registered under a
#     unique matcher (Write|Edit|MultiEdit) → it runs SOLO, outside _build_chain's
#     try/except (hook_builder.py bare-register path). A regex/`.get()` throw would
#     otherwise crash the write path. The self-guard makes a WARN-hook structurally
#     unable to block a write, even on malformed input.
#   • Term set is single-sourced from .kiro/specs/legacy-code-cleanup (AC #6):
#     master, slave, whitelist, blacklist, whiteday, blackday.
#
# Per-term flagging policy (case-insensitive, tuned to minimize PIT51 noise):
#   • whitelist / blacklist — flag any occurrence INCL. embedded/suffixed forms
#     (_WHITELIST, getWhitelist, whitelisted), EXCEPT:
#       (a) hypothesis API `whitelist_categories` / `whitelist_characters`
#           (negative-lookahead — load-bearing, NOT redundant: the `\w*` stem would
#            otherwise match right through the underscore suffix), and
#       (b) PAIRED technical-contrast: if BOTH whitelist AND blacklist appear in
#           this write's text, suppress both (describing a whitelist-vs-blacklist
#           design decision, e.g. prompt_builder.py's blacklist-model doc).
#   • master — flag ONLY in the host-role/paired sense (adjacent to slave), NEVER
#     bare (git master / master copy / DB primary are legitimate English).
#   • slave — flag standalone (rare in innocent senses).
#   • whiteday / blackday — flag bare (rare, unambiguous).
#   • "inclusive" — never in the set, never flagged (math intervals).
#
# ACCEPTED LIMITATION (documented, not hidden): the paired-exemption is computed
# over THIS write's extracted text only. A one-line Edit touching a `whitelist`
# line in a file that defines `blacklist` elsewhere will not see the pair and
# will flag it. For a WARN nudge (no deny, no correctness impact) this
# false-positive is acceptable; reading the whole file was judged too heavy for a
# pre-write hook (Gate-1 FLAW3, taste decision).

_INCLUSIVE_SCAN_MAX_CHARS = 1_000_000  # above this, skip the scan (fail-safe)

# whitelist/blacklist: no left-\b (catch _WHITELIST, getWhitelist); \w* stem
# (catch whitelisted); negative-lookahead exempts the hypothesis API params.
_RE_WHITELIST = re.compile(r"whitelist(?!_(?:categories|characters))\w*", re.IGNORECASE)
_RE_BLACKLIST = re.compile(r"blacklist\w*", re.IGNORECASE)
# master flagged ONLY adjacent to slave (either order); standalone slave flagged.
# Left word-boundary on slave avoids the enslave/enslaved false-positive (Gate-2 M1).
_RE_MASTER_SLAVE = re.compile(r"master[\s/_-]{0,3}slave|slave[\s/_-]{0,3}master", re.IGNORECASE)
_RE_SLAVE = re.compile(r"\bslave\w*", re.IGNORECASE)
_RE_WHITEDAY = re.compile(r"white[\s_-]?day\w*", re.IGNORECASE)
_RE_BLACKDAY = re.compile(r"black[\s_-]?day\w*", re.IGNORECASE)

# term → (regex, suggested inclusive alternative)
_INCLUSIVE_ALTERNATIVES = {
    "whitelist": "allowlist / allowed list",
    "blacklist": "denylist / blocked list",
    "master/slave": "primary/replica, leader/follower",
    "slave": "replica / follower / worker",
    "whiteday": "an inclusive term",
    "blackday": "an inclusive term",
}


def _scan_inclusive_terms(text: str) -> list[str]:
    """Return the list of non-inclusive terms found in ``text`` (dedup'd, ordered).

    Applies the per-term policy + exemptions documented above. Pure; never raises
    on str input.
    """
    findings: list[str] = []
    has_white = bool(_RE_WHITELIST.search(text))
    has_black = bool(_RE_BLACKLIST.search(text))
    # Paired technical-contrast: both present → suppress both (design-decision prose).
    if has_white and not has_black:
        findings.append("whitelist")
    if has_black and not has_white:
        findings.append("blacklist")
    if _RE_MASTER_SLAVE.search(text):
        findings.append("master/slave")
    elif _RE_SLAVE.search(text):
        # standalone slave (no adjacent master) — still non-inclusive
        findings.append("slave")
    if _RE_WHITEDAY.search(text):
        findings.append("whiteday")
    if _RE_BLACKDAY.search(text):
        findings.append("blackday")
    return findings


def _extract_written_text(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Extract the text a Write/Edit/MultiEdit is about to add. Never raises."""
    if tool_name == "Write":
        return str(tool_input.get("content") or "")
    if tool_name == "Edit":
        return str(tool_input.get("new_string") or "")
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits")
        if not isinstance(edits, list):
            return ""
        parts: list[str] = []
        for e in edits:
            if isinstance(e, dict) and e.get("new_string") is not None:
                parts.append(str(e["new_string"]))
        return "\n".join(parts)
    return ""


async def inclusive_term_guard(
    input_data: dict[str, Any],
    tool_use_id: str | None,
    context: Any,
) -> dict[str, Any]:
    """PreToolUse (Write|Edit|MultiEdit): WARN on non-inclusive terminology.

    ALWAYS approves — this is an advisory nudge, never a block (STEERING #2:
    wording is not security). When non-inclusive terms are found in the content
    about to be written, an ``additionalContext`` note lists them + the inclusive
    alternative so the agent can self-correct BEFORE the term reaches a commit.

    Fail-safe by construction: any non-target tool, empty/oversized content, or a
    malformed ``tool_input`` returns a bare ``{"decision": "approve"}`` — a
    self-contained try/except guarantees a scan error can never crash the write
    path (this hook runs solo, outside _build_chain's try/except).
    """
    try:
        tool_name = input_data.get("tool_name", "")
        if tool_name not in ("Write", "Edit", "MultiEdit"):
            return {"decision": "approve"}
        tool_input = input_data.get("tool_input") or {}
        if not isinstance(tool_input, dict):
            return {"decision": "approve"}
        text = _extract_written_text(tool_name, tool_input)
        if not text or len(text) > _INCLUSIVE_SCAN_MAX_CHARS:
            return {"decision": "approve"}

        findings = _scan_inclusive_terms(text)
        if not findings:
            return {"decision": "approve"}

        lines = "\n".join(
            f"  • “{t}” → prefer {_INCLUSIVE_ALTERNATIVES.get(t, 'an inclusive term')}"
            for t in findings
        )
        reminder = (
            "🔤 INCLUSIVE-TERM NUDGE (advisory — not a block): this write contains "
            "non-inclusive terminology that Amazon's InclusiveTechScanner flags on "
            "CRUX code reviews:\n"
            f"{lines}\n"
            "Consider the inclusive alternative before committing. If the term is "
            "load-bearing (an external API name, a quoted spec), leave it — this is "
            "a nudge, not a rule."
        )
        return {"decision": "approve", "additionalContext": reminder}
    except Exception:  # noqa: BLE001 — a WARN nudge must NEVER crash the write path
        logger.exception("inclusive_term_guard raised — failing open (approve)")
        return {"decision": "approve"}


def create_file_access_permission_handler(
    allowed_directories: list[str],
    readonly_files: list[str] | None = None,
) -> Callable[..., Any]:
    """Create a file access permission handler with allowed paths bound.

    Args:
        allowed_directories: Directory paths allowed for file access. The grant
            is RECURSIVE (any file at any depth under the dir) and READ+WRITE.
        readonly_files: EXACT file paths granted READ-ONLY access (Read/Glob/Grep
            allowed; Write/Edit denied). Unlike ``allowed_directories`` this is an
            EXACT-match, non-recursive grant — used for the L3 shared lane
            (run_c220f153): a non-owner channel user may read a shareable
            project's DDD docs (PRODUCT/TECH/IMPROVEMENT/PROJECT.md) but NOT the
            rest of the project dir (``.artifacts/`` pipeline internals) and
            cannot corrupt them. Defaults to ``None`` → empty → zero behavior
            change for every existing caller.

    Returns:
        Async permission handler function for can_use_tool
    """
    # Resolve symlinks and normalize paths for consistent, secure comparison
    normalized_dirs = [os.path.realpath(d).rstrip('/') for d in allowed_directories]
    # Exact-path read-only allowlist (realpath-normalized, symlink-resolved on
    # BOTH sides so a symlink to a shared doc can't smuggle a different file in).
    normalized_ro_files = frozenset(
        os.path.realpath(f) for f in (readonly_files or [])
    )
    _READONLY_TOOLS = frozenset({"Read", "Glob", "Grep"})

    async def file_access_permission_handler(
        tool_name: str,
        input_data: dict[str, Any],
        context: dict[str, Any]
    ) -> dict[str, Any]:
        """Check if file access is allowed based on path restrictions."""

        # File tools that need path checking
        file_tools = {
            'Read': 'file_path',
            'Write': 'file_path',
            'Edit': 'file_path',
            'Glob': 'path',
            'Grep': 'path',
        }

        # Check file tools
        if tool_name in file_tools:
            # Get the path parameter name for this tool
            path_param = file_tools[tool_name]
            file_path = input_data.get(path_param, '')

            # If no path specified, allow (tool will handle the error)
            if not file_path:
                return {"behavior": "allow"}

            # Resolve symlinks and normalize to prevent symlink-based path traversal
            normalized_path = os.path.realpath(file_path)

            # Check if the path is within any allowed directory (recursive, r/w)
            is_allowed = any(
                normalized_path.startswith(allowed_dir + '/') or normalized_path == allowed_dir
                for allowed_dir in normalized_dirs
            )

            if is_allowed:
                logger.debug(f"[FILE ACCESS ALLOWED] Tool: {tool_name}, Path: {file_path}")
                return {"behavior": "allow"}

            # Read-only exact-file grant (L3 shared lane): a path in
            # readonly_files is readable but NOT writable, and matched EXACTLY
            # (no recursion — a sibling like .artifacts/ under the same project
            # is NOT granted). Write/Edit to a read-only file is denied.
            if normalized_path in normalized_ro_files:
                if tool_name in _READONLY_TOOLS:
                    logger.debug(
                        f"[FILE ACCESS ALLOWED — read-only] Tool: {tool_name}, Path: {file_path}"
                    )
                    return {"behavior": "allow"}
                logger.warning(
                    f"[FILE ACCESS DENIED — read-only] Tool: {tool_name} cannot "
                    f"modify shared file {file_path}"
                )
                return {
                    "behavior": "deny",
                    "message": f"File is read-only (shared): {file_path} cannot be modified",
                    "interrupt": False,
                }

            logger.warning(f"[FILE ACCESS DENIED] Tool: {tool_name}, Path: {file_path}, Allowed: {normalized_dirs}")
            return {
                "behavior": "deny",
                "message": f"File access denied: {file_path} is outside allowed directories",
                "interrupt": False  # Don't interrupt, let agent try alternative approach
            }

        # Check Bash tool for file access commands
        if tool_name == 'Bash':
            command = input_data.get('command', '')

            if not command:
                return {"behavior": "allow"}

            # Extract potential file paths from bash commands
            # Match common file access patterns
            suspicious_patterns = [
                r'\s+(/[^\s]+)',  # Absolute paths like /etc/passwd
                r'(?:cat|head|tail|less|more|nano|vi|vim|emacs)\s+([^\s|>&]+)',  # Read commands
                r'(?:echo|printf|tee)\s+.*?>\s*([^\s|>&]+)',  # Write redirects
                r'(?:cp|mv|rm|mkdir|rmdir|touch)\s+.*?([^\s|>&]+)',  # File manipulation
            ]

            potential_paths = []
            for pattern in suspicious_patterns:
                matches = re.findall(pattern, command)
                potential_paths.extend(matches)

            # Check each potential path
            for file_path in potential_paths:
                # Skip if relative path (will be relative to cwd which is safe)
                if not file_path.startswith('/'):
                    continue

                # Normalize and check
                normalized_path = os.path.realpath(file_path)
                is_allowed = any(
                    normalized_path.startswith(allowed_dir + '/') or normalized_path == allowed_dir
                    for allowed_dir in normalized_dirs
                )

                if not is_allowed:
                    logger.warning(f"[BASH FILE ACCESS DENIED] Command: {command[:100]}, Path: {file_path}, Allowed: {normalized_dirs}")
                    return {
                        "behavior": "deny",
                        "message": f"Bash file access denied: Command attempts to access {file_path} which is outside allowed directories ({', '.join(normalized_dirs)})",
                        "interrupt": False
                    }

            logger.debug(f"[BASH ALLOWED] Command: {command[:100]}")
            return {"behavior": "allow"}

        # Allow all other tools
        return {"behavior": "allow"}

    return file_access_permission_handler


# ---------------------------------------------------------------------------
# Skill access control
# ---------------------------------------------------------------------------


def create_skill_access_checker(
    allowed_skill_names: list[str],
    builtin_skill_names: list[str] | None = None,
) -> Callable[..., Any]:
    """Create a skill access checker hook with the allowed skill names bound.

    Built-in skills are always allowed regardless of the ``allowed_skill_names``
    list.  Pass ``builtin_skill_names`` so the hook can grant unconditional
    access to them.

    Args:
        allowed_skill_names: List of skill folder names that are allowed.
        builtin_skill_names: Optional list of built-in skill folder names
            that are always permitted.  When ``None``, no implicit allow
            is applied (backward-compatible behaviour).

    Returns:
        Async hook function that checks skill access.
    """
    _builtin_set: set[str] = set(builtin_skill_names) if builtin_skill_names else set()
    _allowed_set: set[str] = set(allowed_skill_names) if allowed_skill_names else set()

    async def skill_access_checker(
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any
    ) -> dict[str, Any]:
        """Check if the requested skill is allowed for this agent."""
        if input_data.get('tool_name') == 'Skill':
            tool_input = input_data.get('tool_input', {})
            requested_skill = tool_input.get('skill', '')

            # Built-in skills are always allowed
            if requested_skill in _builtin_set:
                logger.debug(f"[ALLOWED] Built-in skill access granted: {requested_skill}")
                return {"decision": "approve"}

            # Empty allowed list means no non-built-in skills are allowed
            if not _allowed_set:
                logger.warning(f"[BLOCKED] Skill access denied (no skills allowed): {requested_skill}")
                return {
                    'hookSpecificOutput': {
                        'hookEventName': 'PreToolUse',
                        'permissionDecision': 'deny',
                        'permissionDecisionReason': 'No skills are authorized for this agent'
                    }
                }

            # Check if requested skill is in allowed set (O(1) lookup)
            if requested_skill not in _allowed_set:
                logger.warning(f"[BLOCKED] Skill access denied: {requested_skill} not in {allowed_skill_names}")
                return {
                    'hookSpecificOutput': {
                        'hookEventName': 'PreToolUse',
                        'permissionDecision': 'deny',
                        'permissionDecisionReason': f'Skill "{requested_skill}" is not authorized for this agent. Allowed skills: {", ".join(allowed_skill_names)}'
                    }
                }

            logger.debug(f"[ALLOWED] Skill access granted: {requested_skill}")
        return {"decision": "approve"}

    return skill_access_checker
