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
import platform
import re
import shlex
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

from config import get_app_data_dir
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


# Eval invocation — `eval_runner.py run`, `ci_eval_gate.py` (executed), or
# `eval_service` CLI run. Eval is a SYSTEM-LEVEL decoupled subsystem (DEC05/PIT179)
# that runs against the DEPLOYED system via CI (post-push) / deploy / scheduled —
# NEVER by the agent inside a coding pipeline (running it on un-deployed changes
# tests the OLD binary, proves nothing, wastes tokens, and hung the judge's Bedrock
# call, 2026-06-28). Matched against the unquoted form so `git commit -m "fix eval"`
# is not a false hit.
#
# The threat is EXECUTION, never a reader naming the file. `ci_eval_gate.py` has no
# `run` subcommand (it IS the gate), so — unlike the three verb-bearing arms — its
# arm anchors on an EXECUTION position instead of a verb: the filename must sit at a
# command-word position (line start / after a `;`/`&`/`|` separator / immediately
# after a `python[3]`/`bash` interpreter, optionally with flags / `./`-prefixed).
# This is the run_3bde4b8b fix for the verb-less-arm false-positive: a bare
# `grep/cat/wc/git log … ci_eval_gate.py` (filename as a trailing ARG of a reader)
# is NOT at a command-word position → APPROVED, while every real execution shape
# (`python … ci_eval_gate.py`, `./ci_eval_gate.py`, `bash ci_eval_gate.py`,
# `cd x && python … ci_eval_gate.py`) still matches → DENIED (Gate-1 skeptic
# verified the deny-set holds + no fail-open).
# ACCEPTED RESIDUAL (documented, not fixed — pytest_command_guard precedent): an
# indirect launch with a bare non-python/bash wrapper and no separator
# (`time ci_eval_gate.py`, `xargs … ci_eval_gate.py`, `sudo ci_eval_gate.py`,
# `env ci_eval_gate.py`, `sh ci_eval_gate.py`, `python -m foo ci_eval_gate.py`) is
# NOT recognized → approved. This residual is SAFE ONLY WHILE `ci_eval_gate.py` stays
# mode 0644 (non-executable): `sudo/time/sh/env <file>` would ENOEXEC / mis-parse and
# NOT actually run the Python (Gate-2 security). ⚠️ If ci_eval_gate.py is ever made
# `chmod +x`, these upgrade to real fail-opens — broaden the anchor then. The
# script-runners that DO execute a non-+x .py (`uv/poetry/pdm run`, `python`, `bash`)
# ARE covered above. The alternative (matching the bare filename anywhere) is the
# false-positive we just fixed.
_CI_EVAL_GATE_EXEC = (
    r"(?:^|[;&|]"                              # line start / separator, OR an exec launcher:
    r"|\bpython[\d.]*\s+(?:-\S+\s+)*"          #   python[3] [-flags]
    r"|\b(?:uv|poetry|pdm)\s+run\s+(?:python[\d.]*\s+(?:-\S+\s+)*)?"  # uv/poetry/pdm run [python] — uv IS used in this repo's CI (Gate-2)
    r"|\bbash\s+)"                             #   bash
    r"\s*(?:[\w./]*/)?ci_eval_gate\.py\b"      # opt. path + filename
)
_EVAL_INVOCATION_RE = re.compile(
    r"(?:\beval_runner\.py\s+run\b"          # eval_runner.py run ...
    r"|" + _CI_EVAL_GATE_EXEC +               # ci_eval_gate.py at an exec position
    r"|\beval_runner\s+run\b"                 # bare `eval_runner run` (entrypoint)
    r"|\beval_service\b[^|\n]*\brun\b)",      # eval_service ... run
    re.IGNORECASE,
)


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


async def eval_command_guard(
    input_data: dict[str, Any],
    tool_use_id: str | None,
    context: Any,
) -> dict[str, Any]:
    """PreToolUse (Bash): DENY running eval (`eval_runner.py run`, `ci_eval_gate.py`,
    `eval_service ... run`) from inside the agent's Bash path.

    Eval is a SYSTEM-LEVEL decoupled subsystem (DEC05/PIT179): it scores the
    DEPLOYED system across the golden set. Running it on UN-deployed changes (the
    daemon still runs the old binary mid-pipeline) tests the OLD code — it proves
    nothing about the change in flight, burns tokens, and on 2026-06-28 hung the
    LLM-judge's Bedrock call (a network HANG that froze the session spinner).

    Eval's correct triggers are CI (post-push), deploy, and scheduled jobs — all
    OUTSIDE the agent's interactive Bash. So denying eval in the agent Bash path
    has no legitimate-use cost: the agent never needs to run eval by hand. Prose
    (R6/R9/STEERING #5) said this and was violated anyway (CLASS A/B, 12 prior
    skip-process occurrences) — this gate is the structural backstop (P7: defense
    outside the agent), the twin of pytest_command_guard / background_command_guard.

    Fail-safe: any non-Bash, non-eval command is approved untouched. Matched on the
    unquoted form so `git commit -m "fix eval_runner.py"` is not a false hit.
    """
    if input_data.get("tool_name") != "Bash":
        return {"decision": "approve"}
    command = (input_data.get("tool_input", {}) or {}).get("command", "") or ""
    if not command:
        return {"decision": "approve"}
    if not _EVAL_INVOCATION_RE.search(_strip_quoted(command)):
        return {"decision": "approve"}

    logger.warning("[BLOCKED] eval invocation in agent Bash path: %s", command[:80])
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "运行 eval(eval_runner / ci_eval_gate / eval_service run)→ DENY。 "
                "Eval 是系统层解耦子系统(DEC05/PIT179),针对**已部署**系统跑,由 "
                "CI(push 后)/ deploy / scheduled 触发 —— 绝不在 coding pipeline 内、"
                "也不由 agent 手动跑。改动还没上线时跑 eval 测的是旧 binary,证明不了本轮改动、"
                "纯浪费,并曾把 judge 的 Bedrock 调用挂死(2026-06-28)。 "
                "`ci_eval_gate` 报 stale 是预期的 —— 它在改动上线后由 eval 作为系统关注点跑过时自然清掉,"
                "不是靠你现在对旧 binary 重跑 eval。 (R6 / R9 / STEERING #5)"
            ),
        }
    }


# ---------------------------------------------------------------------------
# release-publish guard — code-enforced CI-green gate (PreToolUse, Bash)
# ---------------------------------------------------------------------------
# run_9fec1fb1 (2026-07-04): hardens s_swarm-release Stage 7b from runbook-enforced
# to CODE-enforced. Root cause (v1.24.0): a `gh release create` published a GitHub
# Release (tag + DMG — irreversible star/download side effects) on a HEAD that CI
# had NOT validated; CI then went red on 3 stale artifacts. A prose runbook gate
# does not structurally stop the agent from reaching `gh release create` (CLASS A:
# skip-verification, 12 prior occurrences). This gate removes the choice.
#
# Design A (marker-based, NO network in the hook): the gate ALLOWS `gh release
# create` ONLY when a CI-green marker exists AND its head_sha == the current git
# HEAD. The marker is written EXCLUSIVELY by `artifact_cli.py release-gate --poll`,
# which is the only thing that actually queries CI. Keeping the `gh run list` call
# in the CLI (not here) is deliberate: a network call inside a <5s PreToolUse hook
# would reintroduce the exact foreground-timeout hang trap this whole effort fixed.
# The hook only reads a local file + compares HEAD — it CANNOT hang.
#
# Fail-closed: marker absent / unreadable / head_sha mismatch (stale = previous
# release's HEAD) → DENY. Escape hatch for a legit manual re-publish:
# SWARM_RELEASE_GATE_FORCE=1 (env) — logged, deliberate, never the default.

# The publish actions we gate — TWO verbs, because release became CI-driven
# (run_900bb839, 2026-07-15):
#   (a) `gh release create`  — the legacy one-shot create+publish.
#   (b) `gh release edit … --draft=false`  — the CURRENT publish path. CI
#       (release.yml) creates the Release as a DRAFT on tag push; the human/agent
#       flips it to published with `gh release edit --draft=false`. This flip is the
#       real "goes public" moment and MUST clear the same CI-green marker. The gate
#       used to match only (a), so the (b) flip published on an unvalidated HEAD
#       completely unchecked (observed live during the v1.25.0 release).
# NOT gated: `gh release view/list/download` (not publish), `gh release edit --notes`
# WITHOUT a draft-flip (metadata-only on an already-public release), `--draft=true`
# (re-drafting = the REVERSE of publish), and `gh release delete` (owned by the C041
# irreversible-op gate). `_strip_quoted` removes quoted spans first so a --draft=false
# inside a --notes string / commit message is not a false match.
_GH_RELEASE_CREATE_RE = re.compile(r"\bgh\s+release\s+create\b", re.IGNORECASE)
_GH_RELEASE_EDIT_RE = re.compile(r"\bgh\s+release\s+edit\b", re.IGNORECASE)
# draft-flip token. gh's `--draft` is a BOOLEAN pflag (verified against gh 2.88.1):
#   • a value ONLY attaches via `=` — `--draft false` (space) is a HARD gh error
#     ("accepts 1 arg(s), received 2"), it never publishes, so we do NOT match it.
#   • the value is parsed by Go strconv.ParseBool → the FALSE set is EXACTLY
#     {false, f, 0} (case-insensitive; `no`/`n`/`yes` are NOT ParseBool tokens — gh
#     rejects them as "invalid syntax" and publishes nothing). Matching only the
#     literal "false" was a bypass (`--draft=0`/`--draft=f` published unchecked —
#     Gate-2 run_900bb839, verified live against gh).
# So: `--draft=` + a ParseBool-false token. `=true/t/1` (re-draft) does NOT match.
_DRAFT_FALSE_RE = re.compile(r"--draft=(?:false|f|0)\b", re.IGNORECASE)


def _is_release_publish(command: str) -> bool:
    """True IFF the command PUBLISHES a GitHub Release (goes public):
      - `gh release create …`, OR
      - `gh release edit …` carrying a `--draft=<false>` flip (false-token = {false,f,0}).
    Quoted spans are stripped first (a --draft=false inside --notes/commit text is
    not a publish). `--draft=true` (re-draft) and metadata-only edits are NOT publish.
    """
    stripped = _strip_quoted(command)
    if _GH_RELEASE_CREATE_RE.search(stripped):
        return True
    if _GH_RELEASE_EDIT_RE.search(stripped) and _DRAFT_FALSE_RE.search(stripped):
        return True
    return False


# Flags on `gh release create/edit` that DECOUPLE the published commit / tag name
# from the positional tag → the hook cannot locally verify what gh will actually
# publish, so their presence forces fail-CLOSED (Gate-2 CRITICAL, run_81ad1cfe):
#   --target <branch|full-sha>  publishes the tag at THAT commit (not the positional tag's)
#   --tag <name>                renames the published tag
# Neither is used by the legit s_swarm-release runbook flip (7c is bare
# `gh release edit v${VERSION} --draft=false --latest`), so denying them costs the
# flow nothing and closes a real fail-open (publish an unverified commit under a
# CI-green-looking tag name).
_RELEASE_DECOUPLING_FLAG_RE = re.compile(r"(?:^|\s)--(?:target|tag)(?:=|\s)", re.IGNORECASE)

# gh release create/edit flags that take a SPACE-separated VALUE — the value token
# must NOT be mistaken for the positional tag (Gate-2 HIGH). `=`-attached forms
# (--title=x) are already `-`-prefixed so the scanner skips them; only the space
# form needs the value swallowed. ONLY value-taking flags belong here — a BOOLEAN
# flag (--prerelease/--latest/--draft) listed here would wrongly swallow the real
# tag as its "value" → false-DENY (Gate-2 re-review LOW). Verified value-flags per
# `gh release create/edit -h` (gh 2.88.1):
_RELEASE_VALUE_FLAGS = {
    "-t", "--title", "-n", "--notes", "-F", "--notes-file", "--target", "--tag",
    "-R", "--repo", "--discussion-category",
}


def _extract_release_tag(command: str) -> str | None:
    """Extract the tag arg from a `gh release create/edit <tag> …` publish command.
    Returns the first positional token after the `create`/`edit` verb, correctly
    SKIPPING both `--flag=value` (single token, `-`-prefixed) AND `--flag value`
    (two tokens — the value must not be read as the positional tag). gh's release
    tag is positional, e.g. `gh release edit v1.26.0 --draft=false`.
    Quoted spans are stripped first so a tag-lookalike inside --notes is ignored.
    Returns None if no positional tag is found (→ caller fails CLOSED)."""
    stripped = _strip_quoted(command)
    m = re.search(r"\bgh\s+release\s+(?:create|edit)\b(.*)$", stripped,
                  re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    toks = m.group(1).split()
    i = 0
    while i < len(toks):
        tok = toks[i]
        if tok.startswith("-"):
            # A space-valued flag consumes the NEXT token as its value; `=`-attached
            # flags carry their own value and consume nothing extra.
            if tok in _RELEASE_VALUE_FLAGS and "=" not in tok:
                i += 2
            else:
                i += 1
            continue
        return tok  # first true positional = the tag
    return None


def _release_command_is_decoupled(command: str) -> bool:
    """True if the publish command carries a --target/--tag flag that decouples the
    published commit/tag from the positional tag the hook can verify locally."""
    return bool(_RELEASE_DECOUPLING_FLAG_RE.search(_strip_quoted(command)))


def _release_marker_authorizes_head(published_tag: str | None = None) -> tuple[bool, str]:
    """(authorized, reason). True IFF the CI-green marker attests the commit BEING
    PUBLISHED.

    Two modes, chosen by whether the marker records a `tag` (written by
    `release-gate --poll --ref <tag>`):

    - **Tag-anchored** (marker has `tag`): the release verified a specific commit
      (the tag target), which for a re-pointed tag is NOT the branch tip. Resolve
      the PUBLISHED tag (from the `gh release` command) to its commit LOCALLY
      (`git -C <repo_root> rev-parse <tag>^{commit}`) and require it == marker.head_sha.
      This checks "CI green on the exact commit this publish ships", independent of
      where `main` HEAD now points. LOCAL git only — NO network (the hook must never
      hang; the marker is authoritative, the hook does one local deref + compare).
    - **HEAD-anchored** (legacy, no `tag`): branch-tip release — require
      marker.head_sha == current HEAD (unchanged behavior).

    All-local: reads Projects/SwarmAI/.artifacts/.release-ci-green.json + `git -C
    <repo_root> rev-parse`. Any failure → (False, reason) — fail-CLOSED.
    """
    import subprocess
    try:
        from config import get_app_data_dir
        ws = os.environ.get("SWARM_WORKSPACE", str(get_app_data_dir() / "SwarmWS"))
        marker = Path(ws).expanduser() / "Projects" / "SwarmAI" / ".artifacts" / ".release-ci-green.json"
    except Exception as e:  # noqa: BLE001 — config import failure → fail-closed
        return False, f"cannot resolve marker path: {type(e).__name__}"
    if not marker.exists():
        return False, "no CI-green marker (run `artifact_cli.py release-gate --poll` until PASS)"
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return False, f"marker unreadable: {type(e).__name__}"
    marker_head = data.get("head_sha") or ""
    repo_root = data.get("repo_root") or ""
    marker_tag = data.get("tag") or ""
    if not marker_head or not repo_root:
        return False, "marker missing head_sha/repo_root (stale format — re-poll release-gate)"

    # CRITICAL: resolve in the SOURCE repo the marker names (`git -C <repo_root>`),
    # NOT this process's cwd. The hook runs IN-PROCESS in the daemon whose cwd is "/"
    # (not a git repo) — a bare `git rev-parse` here returns 128 and would DENY every
    # release (adversarial run_9fec1fb1 caught this). The marker records which repo
    # its head_sha came from; re-resolve against that.
    def _rev_parse(spec: str) -> str | None:
        try:
            r = subprocess.run(["git", "-C", repo_root, "rev-parse", spec],
                               capture_output=True, text=True, timeout=10)
            return r.stdout.strip() if r.returncode == 0 else None
        except (subprocess.TimeoutExpired, OSError):
            return None

    if marker_tag:
        # Tag-anchored: verify the PUBLISHED tag derefs to the CI-verified commit.
        if not published_tag:
            return (False,
                    f"marker is tag-anchored (tag {marker_tag}) but the publish command "
                    "names no tag to verify — fail-closed (re-poll release-gate, or the "
                    "publish must name the tag)")
        published_commit = _rev_parse(f"{published_tag}^{{commit}}")
        if not published_commit:
            return (False,
                    f"cannot resolve published tag '{published_tag}' in {repo_root} "
                    "(local tag missing?) — fail-closed")
        if published_commit != marker_head:
            return (False,
                    f"published tag {published_tag} → {published_commit[:8]} != CI-verified "
                    f"commit {marker_head[:8]} (marker tag {marker_tag}) — CI not green on the "
                    "commit being published")
        return (True,
                f"CI green on {marker_head[:8]} — published tag {published_tag} matches "
                f"marker tag {marker_tag} (run {data.get('run_id')})")

    # HEAD-anchored (legacy branch-tip release).
    head = _rev_parse("HEAD")
    if not head:
        return False, f"git HEAD unresolved in marker repo_root {repo_root}"
    if marker_head != head:
        return False, f"marker HEAD {marker_head[:8]} != current HEAD {head[:8]} (stale — CI not green on THIS HEAD)"
    return True, f"CI green on HEAD {head[:8]} (run {data.get('run_id')})"


async def release_publish_guard(
    input_data: dict[str, Any],
    tool_use_id: str | None,
    context: Any,
) -> dict[str, Any]:
    """PreToolUse (Bash): DENY a GitHub Release PUBLISH unless CI is green on the
    current HEAD (marker written by `artifact_cli.py release-gate --poll`).

    Publish = `gh release create` OR `gh release edit --draft=<false-token>` (the
    draft→published flip — the current CI-driven publish path; the false-token set is
    gh's boolean-flag ParseBool false values {false,f,0,no,n}, see `_is_release_publish`).

    The code-enforced half of s_swarm-release Stage 7b (run_9fec1fb1; extended to the
    edit-flip verb run_900bb839). Prose said "wait for CI green before publish" and was
    structurally unenforced; this gate makes publishing-before-green impossible without
    an explicit logged override. Fail-safe: non-Bash / non-publish commands approve
    untouched (view/list/download, metadata-only `edit --notes`, `--draft=true`
    re-draft, and `gh release delete` — the last owned by the C041 gate).

    Accepted residuals (adversarial run_9fec1fb1, both by-design not defects):
    - Indirect invocation (`bash -c "gh release create …"`) is NOT caught: `_strip_quoted`
      removes the quoted span before the regex (same documented residual as
      pytest_command_guard). The release skill types a BARE publish verb (7a/7c),
      never wrapped — this is a contrived-attack surface, not a flow-hit. LOW.
    - The marker is a plaintext file the agent could `echo >` to forge a green.
      Threat model here is "stop CLASS-A skip-verification," not a malicious agent;
      `SWARM_RELEASE_GATE_FORCE=1` is already a sanctioned bypass, so the gate is an
      honor-system guardrail by design (forgery just makes the bypass explicit). MEDIUM.
    """
    if input_data.get("tool_name") != "Bash":
        return {"decision": "approve"}
    command = (input_data.get("tool_input", {}) or {}).get("command", "") or ""
    if not command or not _is_release_publish(command):
        return {"decision": "approve"}

    if os.environ.get("SWARM_RELEASE_GATE_FORCE") == "1":
        logger.warning("[release-gate] FORCE override — publishing without CI-green marker check: %s",
                       command[:80])
        return {"decision": "approve"}

    # Gate-2 CRITICAL (run_81ad1cfe): --target/--tag decouple the published commit/tag
    # from the positional tag the hook verifies locally → the hook CANNOT attest what
    # gh will actually publish → fail-CLOSED. The legit runbook flip never uses them.
    if _release_command_is_decoupled(command):
        logger.warning("[BLOCKED] GitHub Release publish carries --target/--tag (decouples "
                       "published commit from verifiable tag): %s", command[:80])
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "GitHub Release publish with `--target`/`--tag` → DENY: these flags publish "
                    "the release at a commit / under a tag name the CI-green gate cannot verify "
                    "locally (it would let an UNVERIFIED commit ship under a green-looking tag). "
                    "The s_swarm-release flip is a BARE `gh release edit v${VERSION} --draft=false "
                    "--latest` — drop --target/--tag. (Deliberate manual case: SWARM_RELEASE_GATE_FORCE=1.)"
                ),
            }
        }

    published_tag = _extract_release_tag(command)
    ok, reason = _release_marker_authorizes_head(published_tag=published_tag)
    if ok:
        logger.info("[release-gate] publish allowed — %s", reason)
        return {"decision": "approve"}

    logger.warning("[BLOCKED] GitHub Release publish without CI-green marker: %s", reason)
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"GitHub Release publish (`gh release create` / `edit --draft=false`) → DENY: {reason}. "
                "发布 GitHub Release(tag+DMG,有 star/下载不可逆副作用)前,CI 必须在**当前 HEAD** 上绿。 "
                "先跑 `python backend/scripts/artifact_cli.py release-gate --poll`(agent 驱动轮询,一次一 call)"
                "直到 state=PASS 写下 marker,再发布。CI 红 → 修 → push → 重新 poll,绝不在红的 HEAD 上发。 "
                "(s_swarm-release 7b, R6; 合法手动补发用 SWARM_RELEASE_GATE_FORCE=1)"
            ),
        }
    }


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
