"""Security hooks for the agent execution environment.

This module provides hook factory functions used by SessionUnit to enforce
security policies during agent execution.  Each hook is composed into the
Claude Agent SDK's hook system via HookMatcher configurations.

Public symbols
--------------
- ``create_pre_tool_logger``                 — factory: logs every tool invocation + durable SEL audit
- ``DEFAULT_DANGEROUS_PATTERNS``             — default glob patterns for dangerous commands
- ``load_dangerous_patterns``                — load patterns from ~/.swarm-ai/dangerous_commands.json
- ``create_dangerous_command_gate``          — single PreToolUse gate for Bash commands
- ``create_file_access_permission_handler``  — workspace file-path sandbox
- ``create_skill_access_checker``            — skill allow-list enforcement
- ``create_session_link_gate``               — deny removing/overriding SWARM_APP_SESSION_ID (run↔session link)
- ``create_audit_marker_write_gate``         — deny writes/deletes in state/pipeline_agent_audit (forged review markers)
- ``create_pipeline_test_gate``              — in an active run, tests/builds only via pipeline_test.py
- ``create_run_amend_gate``                  — in an active run, deny git commit --amend (no HITL prompt)
- ``create_direct_commit_gate``              — >20 source lines committed outside a run → pipeline_v1.py create --profile direct
- ``create_governance_file_gate``            — Three-Layer Governance file write interception
- ``GOVERNANCE_TIER1_PATTERNS``              — Tier 1 (Constitutional) file patterns
- ``GOVERNANCE_TIER2_PATTERNS``              — Tier 2 (Statutory) file patterns
"""

import asyncio
import fnmatch
import functools
import glob
import hashlib
import json
import logging
import os
import re
import shlex
import subprocess
import time
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

from .doc_paths import is_doc_only_path
from .gate_force import force_enabled

logger = logging.getLogger(__name__)


def create_pre_tool_logger(
    session_key: str = "unknown",
    session_context: dict[str, Any] | None = None,
):
    """Factory for the pre-tool logger hook, closing over the session identity.

    Why a factory (Gate-2 HIGH, run_7ada39d6): the SDK invokes every hook callback
    with a HARDCODED ``context={"signal": None}`` (claude_agent_sdk query.py:448 —
    ``HookContext`` carries only a reserved ``signal`` field). So a bare hook CANNOT
    read session identity from its ``context`` arg — it is always empty. Every other
    gate in this module (dangerous/external/…) gets identity via a factory CLOSURE
    (``session_key`` passed by ``build_hooks``), NOT from the SDK context. The SEL
    ``caller`` must do the same, or it is permanently ``"unknown"`` and the audit's
    whole purpose (归属 — which session ran which tool) is defeated.
    """
    # Resolve the durable identity ONCE at build time (closure). This deliberately
    # SNAPSHOTS, unlike create_dangerous_command_gate which re-reads session_context
    # per-call — safe because hooks are rebuilt per-send AND the app session_id is
    # immutable per unit (session_router.py's single writer always writes the same
    # stable app id). Resolution expression matches the gate's for consistency.
    resolved_caller = session_key or "unknown"
    if session_context:
        resolved_caller = str(
            session_context.get("sdk_session_id") or session_key or "unknown"
        )

    async def pre_tool_logger(
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        """Log tool usage before execution + append a persistent SEL audit event.

        The ``logger.info`` line is transient (daemon log rotates at 20MB — the C050
        追溯 gap). The SEL append is the DURABLE record: it survives log rotation and a
        data.db wipe because it lives OUTSIDE both. This hook always approves, so the
        SEL outcome here is always ``allow`` — it is the "which tool was invoked when"
        audit stream; downstream gate denies are a Phase-2 埋点. SEL is fail-soft, so
        this never blocks tool execution even if the audit write fails.
        """
        tool_name = input_data.get('tool_name', 'unknown')
        tool_input = input_data.get('tool_input', {})
        logger.info(f"[PRE-TOOL] Tool: {tool_name}, Input keys: {list(tool_input.keys())}")

        # Durable audit — fail-soft (append_event swallows its own errors; the extra
        # guard here protects against an import-time failure of the sel module itself).
        try:
            from .verdict import Disposition
            from . import sel
            sel.append_event(
                event_type="tool_invocation",
                caller=resolved_caller,  # closure-resolved identity, NOT the empty SDK context
                operation=str(tool_name),
                outcome=Disposition.ALLOW.name.lower(),
                resource=sel.summarize_resource(tool_input),  # keys+sizes only, never values
            )
        except Exception as exc:  # pragma: no cover - defensive; append_event is already fail-soft
            logger.warning("pre_tool_logger: SEL append skipped (non-fatal): %s", exc)

        return {"decision": "approve"}

    return pre_tool_logger


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


def _is_dangerous_rm(command: str, _depth: int = 0) -> bool:
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

    Judged per simple command (``_gate_shell_segments``): an rm chained with
    other commands (``rm -rf <tmp dir> && mkdir ...``) is judged on its own
    operands, not on the words of the commands after it.
    """
    segments = _gate_shell_segments(command)
    if segments is None:
        # Unparseable (unbalanced quotes) → cannot prove safe → fail closed,
        # but only if an rm command is visible in it.
        return bool(re.search(r"(?:^|[;&|(\n]\s*)(?:sudo\s+)?rm\s", command))
    if any(_rm_segment_is_dangerous(_skip_leading_wrappers(seg)) for seg in segments):
        return True
    # A script handed to another shell (`sh -c '...'`, `bash <<EOF`) is judged too.
    return _depth < 2 and any(
        _is_dangerous_rm(s, _depth + 1) for s in _inner_shell_scripts(command, segments))


def _rm_segment_is_dangerous(tokens: list[str]) -> bool:
    """``_is_dangerous_rm`` for ONE simple command (already segmented)."""
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


# the person approved THIS v1 authorization command for THIS tool call (one use)
_LAND_APPROVALS: "dict[tuple[str, str], float]" = {}
_LAND_APPROVAL_TTL = 120.0


def _approval_key(tool_use_id: "str | None", command: str) -> "tuple[str, str]":
    return (str(tool_use_id or ""), hashlib.sha256(command.encode()).hexdigest())


def record_land_approval(tool_use_id: "str | None", command: str) -> None:
    """dangerous_command_gate: the user approved THIS v1 authorization (this tool call)."""
    now = time.monotonic()
    for k, t in list(_LAND_APPROVALS.items()):
        if now - t > _LAND_APPROVAL_TTL:
            _LAND_APPROVALS.pop(k, None)
    _LAND_APPROVALS[_approval_key(tool_use_id, command)] = now


def consume_land_approval(tool_use_id: "str | None", command: str) -> bool:
    """run_owner_gate (phase approved): was this exact command approved by the user for
    this tool call? One use. Position after the approval gate is not proof: a hook that
    raises is skipped by the chain, so without this record a crash in the approval gate
    would hand the request its token unapproved."""
    t = _LAND_APPROVALS.pop(_approval_key(tool_use_id, command), None)
    return t is not None and time.monotonic() - t <= _LAND_APPROVAL_TTL


def _count_required_approval(session_id: "str | None") -> None:
    """Count one required approval on the session's v1 runs (D4); off the decision."""
    if not session_id:
        return
    try:
        from core import pipeline_human
        pipeline_human.record_required_approval(str(session_id))
    except Exception:  # noqa: BLE001 — telemetry never blocks the approved call
        logger.debug("required approval not counted", exc_info=True)


def _count_required_approval_soon(session_id: "str | None"):
    """Hand the count to the ``pipeline_signal`` pool and return at once: it reads
    the session's run files, which must never happen on the event loop."""
    from core import executors
    return executors.get_pool("pipeline_signal").submit(_count_required_approval, session_id)


def v1_land_card(command: str) -> "str | None":
    """What the approval card shows for a v1 run-land / accept-finding / discard-snapshot
    (fixing spec C1): read from the signed land_request event, never from the run's
    JSON — run, action, each repo's code and delivery tree, the REPORT's first line, the
    push reasons and whether tests are skipped. None when it cannot be read."""
    from core.run_owner_gate import parse_cli_calls
    calls, _single = parse_cli_calls(command)
    call = next((c for c in calls if c.get("tool") == "pipeline_v1"
                 and c.get("sub") in {"run-land", "accept-finding", "discard-snapshot"}), None)
    if call is None:
        return None
    argv = call.get("argv") or []
    request_id = next((argv[i + 1] for i, t in enumerate(argv[:-1]) if t == "--request"),
                      None) or next((t.split("=", 1)[1] for t in argv
                                     if t.startswith("--request=")), None)
    run_id = call.get("run_id")
    if not request_id or not run_id:
        return None
    try:
        from core import pipeline_human
        from core.run_protocol import landing
        engine = pipeline_human.load_engine(run_id)
        if engine is None:
            return None
        payload = landing.request_event(engine.store, request_id)
    except Exception as exc:   # the card falls back to the generic text
        logger.warning("v1 approval card for %s %s unreadable: %s", run_id, request_id, exc)
        return None
    request = payload["request"]
    lines = [f"Pipeline v1 {request['action']} — run {run_id}, request {request_id}"]
    repos = (request.get("bindings") or {}).get("repos")
    if isinstance(repos, dict):
        for name, row in sorted(repos.items()):
            lines.append(f"{name}: code {row['code_tree'][:12]} → delivery "
                         f"{row['delivery_tree'][:12]}")
        lines.append("skip tests: " + ("yes" if request.get("skip_tests") else "no"))
        lines.append(f"push reasons: {int(payload.get('push_reasons') or 0)}")
    else:
        lines.append("binds: " + ", ".join(f"{k}={str(v)[:16]}" for k, v in
                                           sorted((request.get("bindings") or {}).items())))
    sha = payload.get("report_sha")
    if isinstance(sha, str):
        try:
            text = engine.store.read_blob(sha).decode("utf-8")
            first = next((ln.strip("# ").strip() for ln in text.splitlines() if ln.strip()), "")
            lines.append(f"REPORT: {first[:120]}")
        except OSError:
            lines.append("REPORT: (blob missing)")
    if request.get("note"):
        lines.append(f"note: {str(request['note'])[:200]}")
    return "\n".join(lines)


def _is_v1_land_request(command: str) -> bool:
    """True for an executed Pipeline v1 action that consumes human authorization."""
    if "pipeline_v1" not in command or "--request" not in command or not any(
            name in command for name in ("run-land", "accept-finding", "discard-snapshot")):
        return False
    from core.run_owner_gate import parse_cli_calls
    calls, _single = parse_cli_calls(command)
    if not calls and _gate_shell_segments(command, []) is None:
        return True
    for call in calls:
        if call.get("tool") != "pipeline_v1" or call.get("sub") not in {
                "run-land", "accept-finding", "discard-snapshot"}:
            continue
        argv = call.get("argv") or []
        values = []
        for index, token in enumerate(argv):
            if token == "--request" and index + 1 < len(argv):
                values.append(argv[index + 1])
            elif token.startswith("--request="):
                values.append(token.split("=", 1)[1])
        if len(values) == 1 and values[0]:
            return True
    return False
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


# What a `find -exec/-execdir/-ok` may run without being a destroy: readers and
# reporters. Anything else (rm, mv, cp, sh -c ..., an unknown binary) counts as a
# possible destroy, so the gate stays fail-closed for what it cannot judge.
_FIND_EXEC_READERS = frozenset({
    "ls", "stat", "cat", "head", "tail", "wc", "file", "du", "grep", "egrep", "fgrep",
    "rg", "md5", "md5sum", "shasum", "sha1sum", "sha256sum", "basename", "dirname",
    "realpath", "readlink", "echo", "printf", "test", "[", "true", "jq", "less",
    "strings", "xxd", "od", "diff", "cmp", "sqlite3", "git",
})


def _find_destroys(tokens: list) -> bool:
    """True when a ``find`` deletes or may modify what it matches.

    ``-delete`` always counts. ``-exec`` / ``-execdir`` / ``-ok`` / ``-okdir`` count
    unless the program they run is a known reader (``-exec ls -la {} ;`` lists
    files; it used to be gated like ``-exec rm``). A reader with a write flag
    (``sqlite3 db 'DELETE ...'``, ``git rm``) is still judged by the reader set
    only for ``ls``-like tools: ``sqlite3`` and ``git`` count when followed by a
    mutating word.
    """
    if "-delete" in tokens:
        return True
    for i, tok in enumerate(tokens):
        if tok not in ("-exec", "-execdir", "-ok", "-okdir"):
            continue
        prog = tokens[i + 1].rsplit("/", 1)[-1] if i + 1 < len(tokens) else ""
        if prog not in _FIND_EXEC_READERS:
            return True
        rest = " ".join(tokens[i + 2:i + 8]).lower()
        if prog in ("sqlite3", "git") and any(
                w in rest for w in ("delete", "drop", "update", "insert", "vacuum",
                                    " rm", "clean", "reset", "checkout", "restore")):
            return True
    return False


_POSITIONAL_RE = re.compile(r'"?\$(?:\{(\d+|@|\*)\}|(\d+|@|\*))"?')


def _inner_shell_scripts(command: str, segments: "list[list[str]]") -> list[str]:
    """Scripts a segment hands to another shell, to be judged like the outer one:
    ``sh -c STRING [ARG0 ARG1 ...]`` (positional ``$1``.. ``$@`` substituted with
    the ARGs, so ``sh -c 'rm "$1"' _ <store>`` is judged as ``rm <store>``), and,
    for a shell that reads its script from stdin (no ``-c``, no script file), every
    heredoc body of the command. Bodies are dropped by ``_gate_shell_segments``
    (stdin is data to most programs, but code to ``bash <<EOF``)."""
    scripts: list[str] = []
    reads_stdin = False
    for seg in segments:
        seg = _skip_leading_wrappers(seg)
        if not seg or os.path.basename(seg[0]) not in _SHELL_INTERPRETERS:
            continue
        parts = _shell_c_parts(seg[1:])
        if parts is None:
            if not any(not a.startswith("-") for a in seg[1:]):
                reads_stdin = True  # `bash`, `bash -s`, `bash -euo pipefail`
            continue
        script, positional = parts
        args = positional[1:]  # positional[0] is $0

        def _sub(m: "re.Match[str]") -> str:
            key = m.group(1) or m.group(2)
            if key in ("@", "*"):
                return " ".join(shlex.quote(a) for a in args)
            idx = int(key)
            if idx == 0:
                return shlex.quote(positional[0]) if positional else ""
            return shlex.quote(args[idx - 1]) if idx <= len(args) else ""

        scripts.append(_POSITIONAL_RE.sub(_sub, script))
    if reads_stdin:
        scripts.extend(_heredoc_bodies(command))
    return scripts


def _heredoc_bodies(command: str) -> list[str]:
    """The bodies of the command's heredocs (``<<WORD`` .. a line ``WORD``)."""
    bodies: list[str] = []
    lines = command.split("\n")
    i = 0
    while i < len(lines):
        openers = list(_HEREDOC_OPENER_RE.finditer(lines[i]))
        i += 1
        for m in openers:
            word = m.group("q1") or m.group("q2") or m.group("bs") or m.group("bare")
            body: list[str] = []
            while i < len(lines):
                line = lines[i].lstrip("\t") if m.group("dash") else lines[i]
                i += 1
                if line == word:
                    break
                body.append(lines[i - 1])
            bodies.append("\n".join(body))
    return bodies


def _copy_overwrites(dest: str, sources: list[str]) -> bool:
    """Would ``cp``/``tee`` into *dest* overwrite an existing file? Fail-closed:
    an unexpanded ``$VAR`` or glob counts as an overwrite."""
    if "$" in dest or any(c in dest for c in "*?["):
        return True
    path = os.path.expanduser(dest)
    if not os.path.isabs(path):
        # Relative to the Bash call's cwd, which is not ours: cannot check.
        return True
    if os.path.isdir(path):
        if not sources:
            return True
        return any(
            "$" in s or any(c in s for c in "*?[")
            or os.path.lexists(os.path.join(path, os.path.basename(s.rstrip("/"))))
            for s in sources
        )
    return os.path.lexists(path)


def _targets_irreplaceable_store(command: str, _depth: int = 0) -> bool:
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

    Judged per simple command (``_gate_shell_segments``, which also opens
    subshells and ``$(...)``): before, the whole line was one command, so a
    store-destroy after ``cd x &&`` was missed (``tokens[0]`` was ``cd``) and a
    read followed by other commands was judged on their words.
    """
    segments = _gate_shell_segments(command)
    if segments is None:
        return False
    if any(_store_segment_destroys(_skip_leading_wrappers(seg)) for seg in segments):
        return True
    return _depth < 2 and any(
        _targets_irreplaceable_store(s, _depth + 1)
        for s in _inner_shell_scripts(command, segments))


def _store_segment_destroys(tokens: list[str]) -> bool:
    """``_targets_irreplaceable_store`` for ONE simple command."""
    from core.data_safety import classify_store, StoreClass
    # Delete/relocate/obliterate verbs + cp (cp /dev/null <store> zeroes a file).
    _DESTRUCTIVE_VERBS = ("rm", "mv", "truncate", "dd", "shred", "tee", "cp")
    if not tokens:
        return False
    verb = tokens[0]
    # `find <store> ... -delete` / `-exec rm` deletes a store TREE while the verb
    # is `find`. Gate it when a store operand co-occurs with a delete action.
    if verb == "find":
        if not _find_destroys(tokens):
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
    _DEST_ONLY = {"cp"}
    if verb == "tee":
        # Every operand of tee is an output it truncates (`-a` appends: no destroy).
        appends = any(t == "--append" or (t.startswith("-") and not t.startswith("--")
                                           and "a" in t[1:]) for t in tokens[1:])
        gated_operands = [] if appends else [d for d in operands if _copy_overwrites(d, [])]
    elif verb in _DEST_ONLY:
        if t_dest is not None:
            gated_operands = [t_dest]
        elif operands:
            gated_operands = [operands[-1]]  # last = destination
        else:
            gated_operands = []
        # A copy destroys only what it OVERWRITES: a new file in a store (a backup
        # into Knowledge/Library, a report into a project) is not a destroy.
        sources = operands if t_dest is not None else operands[:-1]
        gated_operands = [d for d in gated_operands if _copy_overwrites(d, sources)]
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

    Token-aware, segment-split on shell separators so a rewrite op chained after a
    benign command is still caught. A heredoc / here-string body is judged by what
    reads it (_executed_bodies): a shell runs it as commands, an interpreter as code,
    any other program (``cat > f``, ``git commit -F -``) only receives data, so the
    word "rebase" in a file being written is not a rewrite. Unparseable input → the
    rewrite signature searched in the text with its terminated heredoc bodies removed
    (fail closed). Non-git → False.
    """
    return _judge_with_bodies(command, _segment_is_history_rewrite, _REWRITE_SIG_RE, 0)


def _segment_is_commit_amend(seg: list[str]) -> bool:
    sub_rest = _segment_git_subcommand(seg)
    return bool(sub_rest and sub_rest[0] == "commit"
                and any(a.startswith("--amend") for a in sub_rest[1]))


def _is_commit_amend(command: str) -> bool:
    """Is any segment of *command* a ``git commit --amend``? Same tokenizing and
    heredoc rule as ``_is_history_rewrite``; unparseable input naming ``commit`` and
    ``--amend`` → True."""
    return _judge_with_bodies(command, _segment_is_commit_amend, _AMEND_SIG_RE, 0)


_REWRITE_SIG_RE = re.compile(r"\b(rebase|reset\s+--hard|--amend|filter-branch|filter-repo)\b")
_AMEND_SIG_RE = re.compile(r"\bcommit\b.*--amend\b", re.S)
_STDIN_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
# Script operands that are stdin itself.
_STDIN_PATHS = frozenset({"-", "/dev/stdin", "/dev/fd/0", "/proc/self/fd/0"})
# Shell long options that take a value.
_SHELL_VALUE_OPTS = frozenset({"--rcfile", "--init-file"})
_STDIN_INTERPRETER_RE = re.compile(r"^(?:python[\d.]*|perl|ruby|node|php)$")
_BODY_DEPTH = 3


def _reads_script_from_stdin(prog: list[str]) -> "str | None":
    """'shell' / 'python' / 'code' when the program of a segment (wrappers already
    unwrapped) runs a script it reads from stdin, else None. A shell does so with
    ``-s``, with no script operand, or with a stdin path as the script (_STDIN_PATHS);
    option values (``-o pipefail``, bundled ``-euo pipefail``, ``--rcfile f``) are not
    operands; ``-c`` is inline code. ``source`` / ``.`` read stdin only through a stdin
    path. An interpreter with no script / inline code / module, or a stdin path."""
    if not prog:
        return None
    name = os.path.basename(prog[0])
    if name in ("source", "."):
        ops = [a for a in prog[1:] if not a.startswith("-")]
        return "shell" if ops and ops[0] in _STDIN_PATHS else None
    if name in _STDIN_SHELLS:
        i = 1
        while i < len(prog):
            a = prog[i]
            if a == "--":
                i += 1
                break
            if a in _SHELL_VALUE_OPTS:
                i += 2
                continue
            if a.startswith("--"):
                i += 1
                continue
            if len(a) > 1 and a[0] in "-+":
                letters = a[1:]
                if "c" in letters:
                    return None              # inline code
                if "s" in letters:
                    return "shell"           # script from stdin, operands are its args
                i += 2 if letters[-1] in "oO" else 1
                continue
            break
        if i >= len(prog):
            return "shell"
        return "shell" if prog[i] in _STDIN_PATHS else None
    if name.startswith("python") and _STDIN_INTERPRETER_RE.match(name):
        kind, inline = "python", ("-c", "-m")
    elif _STDIN_INTERPRETER_RE.match(name):
        kind, inline = "code", ("-e", "-E", "-p", "-r")
    else:
        return None
    i = 1
    while i < len(prog):
        a = prog[i]
        if a in _STDIN_PATHS:
            return kind
        if a in inline:
            return None          # inline code / a module: no script read from stdin
        if kind == "python" and a in ("-W", "-X"):
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        return None              # a script operand: stdin is that script's data
    return kind


def _executed_bodies(command: str, segments: "list[list[str]]") -> "list[tuple[str, str]]":
    """(kind, body) for the heredoc / here-string bodies of *command* when some segment
    runs a script read from stdin ('shell' or 'code', _reads_script_from_stdin). Which
    body feeds which segment is not tracked, so every body is taken with every kind
    present: over-collecting only makes the caller stricter. No such segment → []
    (the bodies are data: ``cat > f <<EOF``, ``git commit -F - <<EOF``)."""
    kinds = {k for k in (_reads_script_from_stdin(_unwrap_program(seg)) for seg in segments) if k}
    if not kinds:
        return []
    return [(k, b) for b in _stdin_bodies(command) for k in sorted(kinds)]


def _judge_with_bodies(command: str, seg_pred, sig_re, depth: int) -> bool:
    """True when *seg_pred* holds for a segment of *command*, or for a segment of a
    heredoc body a shell runs, or *sig_re* matches a body an interpreter runs and that
    code can execute something (_code_can_execute; non-python interpreters count as
    able). Unparseable → *sig_re* on the text with terminated heredoc bodies removed."""
    segments = _gate_shell_segments(command)
    if segments is None:
        return bool(sig_re.search(_normalize_newlines_quote_aware(command)))
    if any(seg_pred(seg) for seg in segments):
        return True
    for kind, body in _executed_bodies(command, segments):
        if kind == "shell":
            if depth >= _BODY_DEPTH or _judge_with_bodies(body, seg_pred, sig_re, depth + 1):
                return True
        elif sig_re.search(body) and (kind != "python" or _code_can_execute(body)):
            return True
    return False


_GIT_RETARGET_RE = re.compile(r"\bGIT_(?:DIR|WORK_TREE|COMMON_DIR|INDEX_FILE)\b")


def _rewrite_is_branch_local(seg: list[str]) -> bool:
    """True when the rewrite in *seg* only moves the CURRENT branch: ``reset``,
    ``commit --amend``, or a ``rebase`` with at most one positional (the upstream) and
    no ``--update-refs``. ``filter-branch`` / ``filter-repo`` / ``update-ref`` /
    ``branch -f|-D|-M`` / ``checkout -B`` and a rebase naming a branch can move refs a
    worktree shares with its main checkout and the other runs: never local."""
    sub_rest = _segment_git_subcommand(seg)
    if sub_rest is None:
        return False
    sub, rest = sub_rest
    if sub in ("reset", "commit"):
        return True
    if sub != "rebase":
        return False
    positional = 0
    i = 0
    while i < len(rest):
        a = rest[i]
        if a.startswith("--update-refs"):
            return False
        if a in ("--onto", "-s", "--strategy", "-X", "--strategy-option", "-x", "--exec"):
            i += 2
            continue
        if not a.startswith("-"):
            positional += 1
        i += 1
    return positional <= 1


def _rewrite_dirs(command: str, cwd: str) -> "list[str] | None":
    """The directory each git history rewrite of *command* runs in ("" = unknown or not
    exemptible), following ``cd X &&`` chains and ``git -C``. A rewrite that is not
    branch-local (_rewrite_is_branch_local) gets "". None (cannot tell) when the
    command does not parse; when a heredoc body a shell / interpreter runs holds a
    rewrite; when it names GIT_DIR / GIT_WORK_TREE / GIT_COMMON_DIR / GIT_INDEX_FILE
    (an assignment, ``env`` or ``export`` retargets git); or when it has a backtick or
    an ``eval`` / ``source`` / ``.`` (a directory change not followed)."""
    seps: list[str] = []
    segments = _gate_shell_segments(command, seps)
    if segments is None:
        return None
    if "`" in command or _GIT_RETARGET_RE.search(_normalize_newlines_quote_aware(command)):
        return None
    if any((_unwrap_program(seg) or [""])[0] in ("eval", "source", ".") for seg in segments):
        return None
    if _executed_bodies(command, segments) and _judge_with_bodies(
            command, lambda seg: False, _REWRITE_SIG_RE, 0):
        return None
    out: list[str] = []
    cur = cwd or ""
    stack: list[str] = []
    pending: "str | None" = None
    for seg, sep in zip(segments, seps):
        k = 0
        while k < len(sep):
            op = sep[k:k + 2] if sep[k:k + 2] in ("&&", "||") else sep[k]
            k += len(op)
            if op == "(":
                stack.append(cur)
            elif op == ")":
                cur = stack.pop() if stack else ""
            elif pending is not None:
                # only `cd X && …` is known to run in X: after `;` a failed cd leaves
                # the old directory
                cur = pending if op == "&&" else ""
            pending = None
        if _segment_is_history_rewrite(seg):
            out.append(_git_dir_of(seg, cur) if _rewrite_is_branch_local(seg) else "")
        pending = _cd_segment_target(seg, cur)
    return out


def _git_dir_of(seg: list[str], cur: str) -> str:
    """The directory a git segment runs in: *cur*, moved by each ``-C DIR``."""
    tokens = _skip_leading_wrappers(seg)
    if not tokens or tokens[0] != "git":
        return cur
    d = cur
    i = 1
    while i < len(tokens) and tokens[i].startswith("-"):
        t = tokens[i]
        if t == "-C" and i + 1 < len(tokens):
            d = _resolve_dir(d, tokens[i + 1]) if d or os.path.isabs(
                os.path.expanduser(tokens[i + 1])) else ""
            i += 2
            continue
        if t.startswith("-C") and len(t) > 2:
            d = _resolve_dir(d, t[2:])
        elif t in ("-c", "--namespace", "--git-dir", "--work-tree"):
            if t in ("--git-dir", "--work-tree"):
                return ""          # another repo than the directory says
            i += 2
            continue
        elif t.startswith(("--git-dir=", "--work-tree=")):
            return ""
        i += 1
    return d


def _under_any(path: str, roots: "list[str]") -> bool:
    real = os.path.realpath(path)
    for r in roots:
        rr = os.path.realpath(r)
        if real == rr or real.startswith(rr.rstrip("/") + "/"):
            return True
    return False


def _temp_roots() -> "list[str]":
    import tempfile
    return [tempfile.gettempdir(), "/tmp", "/private/tmp", "/var/folders", "/private/var/folders"]


def _repo_store_of(d: str) -> "str | None":
    """Where the repo containing *d* keeps its objects: the ``.git`` dir, or for a
    linked worktree the ``gitdir:`` its ``.git`` file names. None: no repo found or
    the pointer cannot be read."""
    cur = os.path.realpath(d)
    while True:
        g = os.path.join(cur, ".git")
        if os.path.isdir(g):
            return g
        if os.path.isfile(g):
            try:
                line = open(g, encoding="utf-8").read().strip()
            except OSError:
                return None
            if not line.startswith("gitdir:"):
                return None
            p = line[len("gitdir:"):].strip()
            return os.path.realpath(p if os.path.isabs(p) else os.path.join(cur, p))
        parent = os.path.dirname(cur)
        if parent == cur:
            return ""
        cur = parent


def _own_run_worktree(d: str, sid: str) -> "bool | None":
    """None when *d* is not inside a run worktree; else whether the run's run.json
    names *sid* as its session (unreadable or no sid → False)."""
    try:
        from core.run_worktree import run_for_path
        hit = run_for_path(d)
    except Exception:  # noqa: BLE001 — no worktree root: not a run worktree
        logger.debug("_own_run_worktree: suppressed exception on a fail-safe path", exc_info=True)
        return None
    if hit is None:
        return None
    if not sid:
        return False
    try:
        import glob
        import json as _json
        pattern = os.path.join(_pipeline_runs_root(), "Projects", "*", ".artifacts", "runs",
                               hit[0], "run.json")
        for f in glob.glob(pattern)[:4]:
            data = _json.loads(open(f, encoding="utf-8").read())
            if data.get("session_id") == sid:
                return True
    except Exception:  # noqa: BLE001 — cannot tell whose run it is: not scratch
        logger.debug("_own_run_worktree: suppressed exception on a fail-safe path", exc_info=True)
        return False
    return False


def _scratch_dir(d: str, sid: str = "") -> bool:
    """A directory whose history no other session shares: inside THIS session's own
    run worktree (another run's worktree stays gated), or a temp directory whose repo
    store is in a temp directory too (a temp worktree of a shared repo is not scratch)."""
    if not d:
        return False
    own = _own_run_worktree(d, sid)
    if own is not None:
        return own               # another session's run worktree is never scratch
    roots = _temp_roots()
    if not _under_any(d, roots):
        return False
    store = _repo_store_of(d)
    return store == "" or (store is not None and _under_any(store, roots))


def _rewrites_only_scratch(command: str, cwd: str, sid: str = "") -> bool:
    """True when every history rewrite in *command* is branch-local and runs in a
    scratch directory (_scratch_dir): this session's own run worktree or a temp repo,
    where no sibling session's work can be lost. Unknown, not branch-local, or
    unparseable → False (the gate applies)."""
    dirs = _rewrite_dirs(command, cwd)
    return bool(dirs) and all(_scratch_dir(d, sid) for d in dirs)


_PY_INTERPRETER_RE = re.compile(r"(?:^|/)python[\d.]*$")


def _runner_loop_kind(command: str) -> "str | None":
    """Does *command* run ``pipeline_test.py`` more than once in ONE Bash call?
    ``"loop"`` when a ``for``/``while``/``until`` segment sits beside a runner call,
    ``"repeat"`` when two segments run it, else None. One call is capped at
    ``alarm 110`` to stay under the Bash tool's 120 s default; a loop of continuations
    in one call is the retired long-call design again (run_bd60daff 2026-10-19: a
    ``for i in 1 2 3 4 5`` loop with a 600 s Bash timeout). Quote-aware (a ``for`` in
    ``python3 -c "..."`` is not a loop); a script name that is only read (grep, sed)
    is not a run. A heredoc body is not shell text (a ``for`` in a file being written,
    or in python an edit script runs, is not a shell loop) unless a shell runs it.
    Unparseable → None (fail-open)."""
    counted = _runner_counts(command, 0)
    if counted is None:
        return None
    runs, looped = counted
    if runs and looped:
        return "loop"
    if runs >= 2:
        return "repeat"
    return None


def _runner_counts(command: str, depth: int) -> "tuple[int, bool] | None":
    """(runner calls, any shell loop) of *command*, the heredoc bodies a shell runs
    included; None when it does not parse."""
    segments = _gate_shell_segments(command)
    if not segments:
        return None
    runs = 0
    looped = False
    if depth < _BODY_DEPTH:
        for kind, body in _executed_bodies(command, segments):
            if kind == "shell":
                inner = _runner_counts(body, depth + 1)
                if inner:
                    runs += inner[0]
                    looped = looped or inner[1]
    for seg in segments:
        if seg and seg[0] in ("for", "while", "until"):
            looped = True
        seen_py = False
        for tok in seg:
            if _PY_INTERPRETER_RE.search(tok):
                seen_py = True
            elif seen_py and tok.endswith("pipeline_test.py"):
                runs += 1
                break
    return runs, looped


def _segment_git_subcommand(tokens: list[str]) -> "tuple[str, list[str]] | None":
    """``(subcommand, rest)`` when ONE segment is a git command (env assignments,
    wrappers and git global options skipped), else None."""
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
        return None
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
        return None
    return tokens[k], tokens[k + 1:]


def _segment_is_history_rewrite(tokens: list[str]) -> bool:
    """Judge ONE command segment (already split on shell separators)."""
    sub_rest = _segment_git_subcommand(tokens)
    if sub_rest is None:
        return False
    sub, rest = sub_rest
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

        # Surface the question on the session's prompt board (permission_manager),
        # which the session's stream reads. The item carries `kind` + `tool_use_id`
        # so the stream's SSE branch routes it as a question.
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
            _pm.publish_prompt(actual_session_id, {
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

        try:
            answer = await ask_question_mgr.wait_for_answer(tool_use_id)
        finally:
            _pm.close_prompt(tool_use_id)
        answered_by = ask_question_mgr.pop_answered_by(tool_use_id)

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

        # A PERSON's pick on a pipeline decision card is the run's human_answer
        # (docs/design/pipeline-v1-human-loop.md). Recorded here, in the daemon,
        # because the agent has no answer command; an unattended channel pick
        # (answered_by="auto") or free text records nothing, so the run keeps
        # waiting and the agent re-asks.
        if answered_by == "person" and isinstance(answer, dict) and any(
                isinstance(q, dict) and "[" in str(q.get("question", "")) for q in questions):
            from . import pipeline_human
            await asyncio.to_thread(pipeline_human.record_card_answer,
                                    actual_session_id, answer)

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
            or _is_v1_land_request(command)  # consumes one explicit manual-land approval
        )
        # DoD1: local history-rewrite (rebase / reset --hard / commit --amend /
        # filter-*) is dangerous ONLY when a SIBLING live session could lose work
        # on the shared tree. Solo session → a local rewrite affects only its own
        # history, not gated. Registry unavailable → fail-safe stringent (these
        # ops are irreversible; s_workspace-git already requires asking first).
        if not is_dangerous and _is_history_rewrite(command) and not _rewrites_only_scratch(
                command, str(input_data.get("cwd") or ""),
                str(session_context.get("sdk_session_id") or "")):   # run.json's namespace
            # A rewrite inside a run's own worktree or a temp repo touches no history
            # another session shares (each run has its own branch): not gated.
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

        # Check session approvals. A v1 authorization is asked every time: each one is
        # a person's decision about one signed request.
        if not _is_v1_land_request(command) \
                and permission_mgr.is_command_approved(session_key, command):
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
        # a v1 authorization shows what it binds, read from the signed event (C1)
        reason_text = (_is_v1_land_request(command) and v1_land_card(command)) \
            or "Matches dangerous command pattern"

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
            "reason": reason_text,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
        }
        permission_mgr.store_pending_request(permission_request)

        logger.warning(
            "[PERMISSION_REQUEST] Dangerous command requires approval: %s (request_id: %s)",
            command[:50], request_id,
        )

        decision = await permission_mgr.request_decision(actual_session_id, {
            "sessionId": actual_session_id,
            "requestId": request_id,
            "toolUseId": tool_use_id,
            "toolName": "Bash",
            "toolInput": tool_input_data,
            "reason": reason_text,
            "options": ["approve", "deny"],
        })
        logger.info("User decision for %s: %s", request_id, decision)
        permission_mgr.remove_pending_request(request_id)

        if decision == "approve":
            permission_mgr.approve_command(session_key, command)
            if _is_v1_land_request(command):
                record_land_approval(tool_use_id, command)
            else:   # a v1 authorization is a human decision, not a required approval
                _count_required_approval_soon(actual_session_id)
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

        logger.warning(
            "[PERMISSION_REQUEST] External tool requires approval: %s (request_id: %s)",
            tool_name, request_id,
        )

        decision = await permission_mgr.request_decision(actual_session_id, {
            "sessionId": actual_session_id,
            "requestId": request_id,
            "toolUseId": tool_use_id,
            "toolName": tool_name,
            "toolInput": tool_input_data,
            "reason": reason,
            "options": ["approve", "deny"],
        })
        permission_mgr.remove_pending_request(request_id)

        if decision == "approve":
            _count_required_approval_soon(actual_session_id)
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
# Programs that never run their arguments or their stdin. When a detach keyword
# appears in a command, EVERY segment must be one of these (or a read-only git
# subcommand, or the pipeline CLI) for the keyword to count as text: anything else
# (a wrapper, a shell, an interpreter, find/xargs/ssh/su ...) may run it.
_BG_TEXT_PROGRAMS = frozenset({
    "grep", "egrep", "fgrep", "rg", "ag", "echo", "printf",
    "head", "tail", "wc", "sort", "uniq", "cut", "tr", "cat", "less", "column", "nl",
    "ls", "cd", "pwd", "true", "mkdir",
})
_BG_GIT_TEXT_SUBCOMMANDS = frozenset({"log", "commit", "grep", "show", "diff", "status", "blame"})
_BG_PYTHONS = re.compile(r"^python(?:3(?:\.\d+)?)?$")
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
    control operator (``cmd &`` or ``cmd & next``). Plus a detach keyword anywhere,
    unless every segment is a text-only program (_bg_keyword_is_text).

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
    if _BG_KEYWORD_RE.search(command) and not _bg_keyword_is_text(command):
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


def _bg_segment_is_text(seg: list[str]) -> bool:
    """True when *seg* is a program that never runs its arguments or stdin."""
    name = os.path.basename(seg[0])
    if name in _BG_TEXT_PROGRAMS:
        return not (name == "rg" and any(t.startswith("--pre") for t in seg))
    if name == "git":
        return len(seg) > 1 and seg[1] in _BG_GIT_TEXT_SUBCOMMANDS
    # The pipeline CLI takes requirement / message texts that may name a keyword.
    return bool(_BG_PYTHONS.match(name)) and len(seg) > 1 and (
        os.path.basename(seg[1]) == "pipeline_v1.py")


# Programs that run a file, a string or their stdin as code: a list that runs one
# anywhere keeps the strict whole-command rule (`echo nohup x > f; bash f`).
_BG_CODE_RUNNERS = frozenset({
    "bash", "sh", "zsh", "dash", "ksh", "fish", "source", ".", "eval", "exec",
    "xargs", "env", "sudo", "su", "ssh", "node", "perl", "ruby", "osascript", "make",
})


def _bg_keyword_is_text(command: str) -> bool:
    """True when the detach keywords in *command* can only be text.

    The command must parse and have no backtick. It is split into lists at
    top-level ``;`` ``&&`` ``||`` (a pipe or a subshell keeps segments together):
    every list that contains a keyword must consist of text-only programs
    (_bg_segment_is_text) with no substitution inside a token. A list without a
    keyword cannot pass one on — unless some list runs code (a shell, an
    interpreter, a wrapper: _BG_CODE_RUNNERS / python), which could read a file the
    keyword was written to; then every segment of the whole command must be text,
    as before. So ``sed -n 1,9p f; grep setsid f`` is text (10-20 / 10-21 false
    denies), while a keyword reached through a wrapper (``env -i``, ``sudo -u``,
    ``nohup grep``), shell or interpreter code (``bash -c``, ``python -c``, a
    heredoc a shell reads), or a program that runs its arguments (``find -exec``,
    ``xargs``, ``ssh``) is still denied. A heredoc body written by ``cat > f`` is
    not a segment, so its text does not count."""
    if "`" in command:
        return False
    seps: "list[str]" = []
    segments = _gate_shell_segments(command, seps)
    if not segments:
        return False
    lists: "list[list[list[str]]]" = []
    for seg, sep in zip(segments, seps):
        if not lists or sep in (";", "&&", "||"):
            lists.append([seg])
        else:
            lists[-1].append(seg)

    def _has_kw(segs: "list[list[str]]") -> bool:
        return any(_BG_KEYWORD_RE.search(t) for sg in segs for t in sg)

    def _all_text(segs: "list[list[str]]") -> bool:
        return all(_bg_segment_is_text(sg)
                   and not any(m in t for t in sg for m in ("$(", "<(", ">("))
                   for sg in segs)

    runs_code = any(os.path.basename(sg[0]) in _BG_CODE_RUNNERS
                    or _BG_PYTHONS.match(os.path.basename(sg[0]))
                    for sg in segments)
    if runs_code:
        return _all_text(segments)
    return all(_all_text(lst) for lst in lists if _has_kw(lst))


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
# pytest command guard — a pytest run must end before the Bash timeout (R9)
# ---------------------------------------------------------------------------
# In run_241014d4 a slow pytest ran past the Bash timeout, the CLI moved it to the
# background, the call returned empty, and the agent re-ran it ~6×. This guard
# denies a pytest run that can outlive the call (see pytest_command_guard) and
# approves everything else (fail-safe). It is a pure deny — no HITL.

# pytest as an INVOCATION (not the literal string in a filename like pytest.log):
# at a command-word position — line start, or after a shell separator/pipe,
# `python -m` (any minor version), a runner wrapper, an env-assignment prefix,
# OR a wall-clock wrapper (gtimeout/timeout N). The wall-clock-wrapper arm is
# essential: the wrapped form is the shape we MANDATE, so the gate must still
# SEE it as pytest (its cap is then checked against the Bash timeout). This is
# what stops the `cat pytest.log | tail` false positive while catching
# `gtimeout 900 pytest`.
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
# the interpreter may be given by path (`backend/.venv/bin/python`, `/abs/.venv/bin/python3`):
# conftest requires the venv interpreter, so a bare-`python`-only cap denied the one
# command that can actually run the tests.
_RUNNER_PREFIX = r"(?:(?:[\w.~/-]*/)?python[\d.]*\s+-m\s+|(?:uv|poetry|pdm)\s+run\s+)?"
_WALLCLOCK_BOUND_PYTEST_RE = re.compile(
    # gtimeout/timeout N (N>=1) wrapping pytest (optionally via a runner prefix):
    r"\bg?timeout\s+(?P<timeout>[1-9]\d*)\s+" + _RUNNER_PREFIX + r"(?:py\.test|pytest)\b"
    # OR perl-alarm: alarm N (N>=1) ... exec @ARGV <runner?> <pytest>, one command:
    r"|\bperl\b[^|\n]*?\balarm\s+(?P<alarm>[1-9]\d*)\b[^|\n]*?\bexec\s+@ARGV['\"]?\s+"
    + _RUNNER_PREFIX + r"(?:py\.test|pytest)\b",
    re.IGNORECASE,
)
# The CLI's own Bash timeout (BASH_DEFAULT_TIMEOUT_MS, 120s here; a call may ask for
# more with its `timeout` input). A command still running when it fires is moved to
# the background and the call returns no output, so a pytest cap must end the run
# this many seconds before it (pytest's report and the shell need the margin).
_BASH_TIMEOUT_DEFAULT_MS = 120_000
_CAP_MARGIN_S = 10


_BASH_TIMEOUT_MAX_MS = 600_000  # the CLI clamps a call's timeout to this


def _bash_call_timeout_s(tool_input: dict) -> int:
    """The timeout of this Bash call, seconds: its `timeout` input, else
    BASH_DEFAULT_TIMEOUT_MS, else 120s; clamped to the CLI's 600s maximum. A value
    that is not a positive whole number (a bool, a float string, garbage) is not
    trusted and the default applies, so a malformed input can only shorten the limit."""
    for raw in (tool_input.get("timeout"), os.environ.get("BASH_DEFAULT_TIMEOUT_MS")):
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int):
            ms = raw
        elif isinstance(raw, str) and raw.strip().isdigit():
            ms = int(raw.strip())
        else:
            continue
        if ms > 0:
            return min(ms, _BASH_TIMEOUT_MAX_MS) // 1000
    return _BASH_TIMEOUT_DEFAULT_MS // 1000


def _pytest_run_time_s(command: str) -> "int | None":
    """How long the pytest runs in *command* can take: the SUM of their caps (runs
    in one call follow each other), or None when any run has no cap of its own.

    Every pytest invocation (`_PYTEST_INVOCATION_RE`) must have a cap bound to it
    (`_WALLCLOCK_BOUND_PYTEST_RE`): one capped run does not cover a second uncapped
    one (`gtimeout 1 pytest a; pytest b`). Both are counted on the raw text, so a
    quoted decoy (`echo 'gtimeout 1 pytest x'; pytest b`) adds one invocation AND
    one cap and the real run is still uncovered."""
    command = _drop_quoted_data(command)
    caps = [int(m.group("timeout") or m.group("alarm"))
            for m in _WALLCLOCK_BOUND_PYTEST_RE.finditer(command)]
    runs = sum(1 for _ in _PYTEST_INVOCATION_RE.finditer(command))
    if not caps or len(caps) < runs:
        return None
    return sum(caps)


# A quoted span that names pytest or tail is data (an echo, a -k expression, a commit
# message), never a run, a cap or a pager: it is dropped before counting. Every other
# quoted span stays, because the perl cap is one (`perl -e 'alarm 90; exec @ARGV'`).
# One rule for all three counts, so a decoy cannot add a cap the real run lacks
# (`echo 'gtimeout 1 pytest x'; pytest b`) and cannot add a run (`echo '; pytest x'`).
_QUOTED_SPAN_RE = re.compile(r"'[^']*'|\"[^\"]*\"")
_DATA_WORD_RE = re.compile(r"\b(?:py\.test|pytest|tail)\b", re.IGNORECASE)


def _drop_quoted_data(command: str) -> str:
    return _QUOTED_SPAN_RE.sub(lambda m: "" if _DATA_WORD_RE.search(m.group(0)) else m.group(0),
                               command)


# `tail -f` / `-F` / `--follow` never exits, so a capped run piped into it still
# outlives the call.
_FOLLOW_RE = re.compile(r"\btail\b[^|;&\n]*\s(?:-[a-zA-Z]*[fF][a-zA-Z]*|--follow\b)")


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
    """PreToolUse (Bash): DENY a pytest run whose wall-clock cap does not end it
    before this Bash call's timeout.

    Why: the CLI moves a command still running at its Bash timeout (120s default,
    or the call's `timeout` input) to the background and returns no output; the
    agent reads that as a hang and re-runs it (run_241014d4, C040). Transcripts of
    2026-10-14..21 show `perl -e 'alarm 120' … pytest` moved to the background
    exactly this way, so a cap only counts when it is shorter than the timeout
    (by _CAP_MARGIN_S). A per-test `--timeout` does not cap the run.

    One rule, one message. A pipe into tail/head used to be denied as a second
    rule; it only hid output because the run had no cap, and its suggested fix
    (`pytest … > file`) was itself denied by the cap rule, so agents were refused
    twice in a row (25 sessions in a week hit it 3+ times). A capped run piped into
    tail returns its tail.

    Fail-safe for the INVOCATION gate: any non-Bash, non-pytest command is
    approved untouched.

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

    # One rule: every run carries a wall-clock cap, and together they end before the
    # Bash call's own timeout. Checked on the RAW command (NOT quote-stripped): the
    # perl cap carries `alarm N` / `exec @ARGV` INSIDE its single-quoted -e arg.
    # A pipe into tail/head is fine once the runs end in time (it was denied before
    # because an uncapped run got moved to the background); `tail -f` is not, it
    # never ends. Accepted residual: `pytest … | tail` reports tail's exit status;
    # pytest's own summary line is still in the output.
    timeout = _bash_call_timeout_s(input_data.get("tool_input", {}) or {})
    limit = timeout - _CAP_MARGIN_S
    total = _pytest_run_time_s(command)
    follow = bool(_FOLLOW_RE.search(_drop_quoted_data(command)))
    if total is None or total > limit or follow:
        logger.warning("[BLOCKED] pytest runs %s > %ss (follow=%s): %s", total, limit,
                       follow, command[:80])
        if follow:
            why = "pipes into `tail -f`, which never exits"
        elif total is None:
            why = ("has a pytest run with no wall-clock cap of its own"
                   if _WALLCLOCK_BOUND_PYTEST_RE.search(command) else "has no wall-clock cap")
        else:
            why = (f"is capped at {total}s in total, which outlives this call's "
                   f"{timeout}s Bash timeout")
        if limit < 1:
            how = (f"This call's Bash timeout is {timeout}s, too short to leave the "
                   f"{_CAP_MARGIN_S}s margin a run needs: omit the call's `timeout` "
                   "(120s default) or raise it.")
        else:
            n = min(limit, 100)
            how = ("Run it as: "
                   f"`perl -e 'alarm {n}; exec @ARGV' python -m pytest <tests> "
                   f"--timeout=60 -q` (one cap per pytest run, at most {limit}s for all "
                   f"runs in the call together; redirecting to a file is fine). If it "
                   f"cannot finish in {limit}s, run fewer tests.")
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"This pytest command {why}. A command still running when the Bash "
                    "timeout fires is moved to the background and the call returns no "
                    "output. A per-test --timeout does not cap the run. " + how
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
#     SWARM_GATE_FORCE=adversarial_commit_gate (daemon env) is the operator bypass (logged).
#   • Scope is `git commit` only (reusing the segment-split + wrapper-strip discipline
#     of _is_irreversible_external_op). Non-commit git (add/status/push), non-git,
#     and the amuse-case `git commit --amend` all count as commits (still need review).

_AGENT_AUDIT_DIR = get_app_data_dir() / "state" / "pipeline_agent_audit"

# Wrappers that take NO positional argument of their own before the wrapped command.
_WRAPPERS_NOARG = {"sudo", "env", "command", "nice", "nohup", "time"}
# Wrappers that REQUIRE a positional argument (a duration) before the wrapped command:
# `timeout 600 git commit`. Adding only the wrapper NAME to the no-arg set would leave
# the duration (`600`) as tokens[0] != "git" → the commit goes UNRECOGNIZED → fail-OPEN
# (run_68245424 / Gate-1 catch). So these must also skip their following non-flag arg.
_WRAPPERS_DURATION = {"timeout", "gtimeout"}


# GNU `timeout` options that CONSUME a following value token (space form). The =form
# is self-contained. Missing one here is a FAIL-OPEN: its value would be mis-read as the
# duration positional, the real duration shifts to tokens[0] != "git", and the wrapped
# `git commit` goes UNRECOGNIZED → gate skipped (run_68245424 Gate-2 security finding:
# `-k`/`--kill-after` was missing → `timeout -k 5 600 git commit` bypassed the gate).
_TIMEOUT_VALUE_OPTS = {"-s", "--signal", "-k", "--kill-after"}

# Shell redirection OPERATORS whose own token is NEVER a pathspec. Two shapes, handled
# differently in the pathspec walk (run_68245424 Gate-2 correctness finding):
#   • BARE operator (`>`, `>>`, `<`, `&>`, `2>`, `1>>`, `>&`, `&>>`): its OPERAND is a
#     SEPARATE following token (`git commit -m x > out.log`) — BOTH the operator AND the
#     operand must be skipped, else the operand (`out.log`) is collected as a FALSE
#     pathspec → a legitimate reviewed commit is FALSE-DENIED.
#   • SELF-CONTAINED (`>out.log`, `2>&1`, `<<EOF`, `<<-EOF`): operator+target are one
#     token — skip just that one token.
# Matching only these unambiguous shapes keeps a real filename (which never starts with a
# redirection operator) from being skipped — fail-safe in BOTH directions.
_REDIR_BARE_OP_RE = re.compile(r"^\d*(?:>>|<<<|>&|&>>|&>|>|<)$")       # operand is NEXT token
_REDIR_SELF_CONTAINED_RE = re.compile(r"^\d*(?:>>|>|<)\S+$|^<<-?\S*$|^\d*>&\d+$|^&>\S+$")


def _skip_leading_wrappers(tokens: list[str]) -> list[str]:
    """Strip env-assignments and command wrappers from the FRONT of a segment, returning
    the tokens starting at the real command word. Handles both no-arg wrappers
    (`sudo`/`env`/…) and duration-arg wrappers (`timeout <dur>`/`gtimeout <dur>`), the
    latter also skipping any leading options (`--foreground`, `-s SIG`, `-k DUR`, their
    =forms) AND the mandatory DURATION positional, so `timeout -s KILL -k 5 600 git
    commit` resolves to `git commit`.

    FAIL-CLOSED duration handling: the duration positional is consumed ONLY if, after the
    options, the next token is NOT already the wrapped command. If a malformed/novel
    option layout leaves `git` as the next token, we do NOT swallow it as a duration —
    the wrapped command stays visible (recognizing the commit = the safe direction for a
    gate). Over-recognition (treating a non-commit as a commit) only adds a redundant
    coverage check; under-recognition would fail-OPEN."""
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if "=" in t and t.split("=", 1)[0].replace("_", "").isalnum() and not t.startswith("-"):
            i += 1
        elif t in _WRAPPERS_NOARG:
            i += 1
        elif t in _WRAPPERS_DURATION:
            # Skip the wrapper + its own options (value-taking opts consume their value),
            # then the DURATION positional — but never swallow the wrapped command itself.
            i += 1
            while i < len(tokens) and tokens[i].startswith("-"):
                if tokens[i] in _TIMEOUT_VALUE_OPTS and i + 1 < len(tokens):
                    i += 2  # option + its separate value token
                else:
                    i += 1  # =form / value-less option
            # Consume the duration positional ONLY if it is not already the command.
            # (A bare `timeout git commit` with no duration is malformed, but keeping
            # `git` visible is the fail-CLOSED choice — recognize the commit.)
            if i < len(tokens) and tokens[i] not in ("git",) and tokens[i] not in _WRAPPERS_NOARG and tokens[i] not in _WRAPPERS_DURATION:
                i += 1  # the duration positional (e.g. 600, 5s, 1m)
        else:
            break
    return tokens[i:]


# A heredoc opener `<<[-][ ]WORD` whose WORD is an IDENTIFIER, bare or fully quoted
# ('EOF' / "EOF" / \\EOF). Requiring an identifier keeps `$((1<<2))` / `$[1<<2]` (shift
# operators) from ever being read as a heredoc; real heredoc delimiters are words.
# The opener must END at a word boundary (whitespace / operator / end), so `<<EOF.txt`
# or `<<EO"F"` (shapes this model does not cover) are simply not recognised → kept.
_HEREDOC_OPENER_RE = re.compile(
    r"<<(?P<dash>-?)[ \t]*(?P<delim>'(?P<q1>[A-Za-z_]\w*)'|\"(?P<q2>[A-Za-z_]\w*)\""
    r"|\\(?P<bs>[A-Za-z_]\w*)|(?P<bare>[A-Za-z_]\w*))(?=[\s;&|<>()]|$)"
)


def _redirect_glued_to_word(command: str, i: int) -> bool:
    """True iff the unquoted `<`/`>` at *i* starts a redirect operator that is glued to
    the preceding WORD (`commit>out`, `commit<<EOF`). False when nothing precedes it,
    it follows whitespace / an operator char, it continues an operator (`>>`, `&>`), or
    the preceding word is an all-digit fd number at a word start (`2>&1`)."""
    if i == 0 or command[i - 1] in " \t\n\r;&|()<>":
        return False
    j = i - 1
    while j >= 0 and command[j].isdigit():
        j -= 1
    if j < i - 1 and (j < 0 or command[j] in " \t\n\r;&|("):
        return False  # `2>&1` / `0<<EOF`: an fd number, part of the operator
    return True


def _normalize_newlines_quote_aware(command: str) -> str:
    """Replace command-separating newlines with ` ; ` — only newlines in UNQUOTED command
    text — and drop heredoc BODIES, so the shlex split that runs next sees only shell
    code. A newline inside `'...'`/`"..."` (incl. inside a `"$(...)"`) is literal DATA (a
    multiline -m message) and stays a newline inside its token.

    Why quote-aware (run_68245424): the old blind `command.replace("\\n", " ; ")` turned
    a newline inside a quoted `-m "<<A\\n"` value into a separator, which split the real
    positional pathspec (`secret.py`) into a NON-commit segment → the pending-path set
    missed it → unreviewed file committed (fail-OPEN).

    Quote tracking mirrors POSIX shell quoting so it does NOT diverge from shlex (a
    divergence is a fail-OPEN: run_68245424 Gate-2 found that treating `\\"` inside a
    double-quoted span as a CLOSING quote inverts the quote state). So:
      • single-quote `'...'`: everything literal, only `'` ends it — backslash is NOT
        special (POSIX single-quote rule); `$'...'` (ANSI-C) honours backslash escapes;
      • double-quote `"..."`: a backslash escapes the next char, so `\\"` stays INSIDE;
        `$(` re-enters unquoted rules until its `)`, so `"$(cat <<'EOF' … EOF)"` works;
      • unquoted: a backslash escapes the next char too, so `\\'`/`\\"` do NOT open a
        span and a `\\<newline>` line-continuation is not treated as a separator.

    Heredoc bodies (the false-DENY fix): a heredoc body is stdin DATA, but it used to be
    scanned as code — an apostrophe or odd `"` in a message (`git commit -F - <<'EOF'` …
    `don't` … `EOF`) inverted the quote state, shlex raised, and the callers' whitespace
    fallback turned every body word into a "pathspec". Now an UNQUOTED `<<[-]WORD` opener
    with an IDENTIFIER delimiter (bare, `'EOF'`, `"EOF"`, `\\EOF`; see
    _HEREDOC_OPENER_RE) registers a heredoc, and at the next unquoted newline its body is
    dropped up to the exact terminator line (`<<-` strips leading tabs). Not recognised
    inside quotes (`-m "<<A"` is a literal), for `<<<`, or inside `$((…))`/`((…))` (shift).
    A spaced `<< EOF` is emitted glued (`<<EOF`) so the token walker skips it as ONE
    redirect. FAIL-CLOSED by construction: text is dropped ONLY when the terminator line
    is actually found — with no terminator the body is kept and scanned as code, because
    wrongly dropping real command text would hide a commit (fail-OPEN) while keeping data
    only over-collects. Comments are deliberately NOT modelled (dropping a `#…` tail that
    bash does not treat as a comment — `a\\ #`, `$(x)#` — hid real commits); instead any
    unquoted `#` switches heredoc recognition off for the rest of its line, so a `<<WORD`
    inside a comment can never swallow the code after it. An unquoted `<`/`>` glued to a
    word (`git commit<<EOF`, `git commit>out`) gets a space before it, so shlex splits it
    off the word the way bash does (see _redirect_glued_to_word).

    This is a pre-tokenizer only — shlex does the authoritative split next. An UNCLOSED
    quote leaves the tail unconverted, which only MERGES lines — bash rejects such a
    command anyway, so nothing that merges here can run."""
    out: list[str] = []
    # Context stack: "top" (unquoted command text), "dq" (inside "..."), "cmd" (inside
    # `$(...)` / `(...)` — unquoted rules again, even when nested in a "..."), "arith"
    # (the two paren frames of `$((...))` / `((...))` — same rules as "cmd").
    stack: list[str] = ["top"]
    pending: list[tuple[str, bool]] = []  # heredocs opened on the current line
    # An unquoted `#` MAY start a comment, and a `<<WORD` inside a comment is not a
    # heredoc — recognising it would drop the real code below it (fail-OPEN). Comments
    # are not modelled precisely (escapes / continuations make that error-prone), so any
    # unquoted `#` simply turns heredoc recognition OFF until the next real newline.
    hash_on_line = False
    i = 0
    n = len(command)

    def _sep() -> str:
        # A newline nested anywhere inside "..." is literal DATA inside one shlex token;
        # only an unquoted newline separates commands.
        return "\n" if "dq" in stack else " ; "

    while i < n:
        c = command[i]
        ctx = stack[-1]
        if ctx == "dq":
            # double-quote: backslash escapes the next char, so `\"` stays inside.
            if c == "\\" and i + 1 < n:
                out.append(command[i:i + 2]); i += 2; continue
            if command.startswith("$((", i):
                out.append("$(("); stack += ["arith", "arith"]; i += 3; continue
            if command.startswith("$(", i):
                out.append("$("); stack.append("cmd"); i += 2; continue
            out.append(c)
            if c == '"':
                stack.pop()
            i += 1
            continue
        # ctx is "top" or "cmd": unquoted shell rules.
        # A backslash escapes the next char (a `\'`/`\"` does NOT open a span, a
        # `\<newline>` continuation is not a separator).
        if c == "\\" and i + 1 < n:
            out.append(command[i:i + 2]); i += 2; continue
        if command.startswith("$'", i):
            # ANSI-C quoting `$'...'`: backslash escapes inside, so `$'\''` is ONE quote
            # char — modelled so it cannot invert the quote state.
            j = i + 2
            while j < n and command[j] != "'":
                j += 2 if command[j] == "\\" else 1
            j = min(j + 1, n)
            out.append(command[i:j]); i = j; continue
        if c == "'":
            # single-quote: literal; only `'` ends it (backslash NOT special in POSIX).
            # An UNCLOSED quote copies the rest verbatim (fail-closed: merges segments).
            j = command.find("'", i + 1)
            j = n if j == -1 else j + 1
            out.append(command[i:j]); i = j; continue
        if c == '"':
            out.append(c); stack.append("dq"); i += 1; continue
        if command.startswith("$((", i) or command.startswith("((", i):
            # Arithmetic: two ordinary paren frames (each closes on `)`, newlines are
            # handled as in any frame), tagged so `<<` inside is a SHIFT, never a heredoc.
            k = 3 if c == "$" else 2
            out.append(command[i:i + k]); stack += ["arith", "arith"]; i += k; continue
        if command.startswith("$(", i):
            out.append("$("); stack.append("cmd"); i += 2; continue
        if c == "(":
            out.append(c); stack.append("cmd"); i += 1; continue
        if c == ")" and ctx in ("cmd", "arith"):
            out.append(c); stack.pop(); i += 1; continue
        if c == "#":
            hash_on_line = True
        if c in "<>" and "arith" not in stack and _redirect_glued_to_word(command, i):
            # `git commit<<EOF` / `git commit>out`: bash splits the operator off the word,
            # shlex does not — a glued `commit<<EOF` token would hide the commit (fail-OPEN,
            # pre-existing). Emit a space so the word and the operator tokenize apart.
            out.append(" ")
        if (command.startswith("<<", i) and not command.startswith("<<<", i)
                and (i == 0 or command[i - 1] != "<") and "arith" not in stack
                and not hash_on_line):
            m = _HEREDOC_OPENER_RE.match(command, i)
            if m:
                word = m.group("q1") or m.group("q2") or m.group("bs") or m.group("bare")
                pending.append((word, bool(m.group("dash"))))
                # Glue `<< EOF` → `<<EOF` so the token walker skips it as ONE
                # self-contained redirect (its delimiter is never a pathspec).
                out.append("<<" + (m.group("dash") or "") + m.group("delim"))
                i = m.end()
                continue
            out.append("<<"); i += 2; continue
        if c in ("\n", "\r"):
            out.append(_sep())
            i += 1
            hash_on_line = False
            if c == "\n" and pending:
                # Drop each pending heredoc body, in order — but ONLY when its exact
                # terminator line (bash rule; `<<-` strips leading TABs) is found. No
                # terminator → the text is KEPT and scanned as code: wrongly dropping real
                # command text would be fail-OPEN, keeping data is only over-collection.
                for word, strip_tabs in pending:
                    k = i
                    found = None
                    while k < n:
                        j = command.find("\n", k)
                        line = command[k:] if j == -1 else command[k:j]
                        nxt = n if j == -1 else j + 1
                        cand = line.rstrip("\r")
                        if strip_tabs:
                            cand = cand.lstrip("\t")
                        if cand == word:
                            found = nxt
                            break
                        k = nxt
                    if found is None:
                        break  # keep this body (and any later ones) as code
                    i = found
                    out.append(_sep())
                pending = []
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _segment_is_git_commit(tokens: list[str]) -> bool:
    """True iff this ONE already-separator-split segment is a `git commit`.

    Mirrors _segment_is_irreversible's wrapper/env-assignment strip + git global-option
    skip so `env X=y git -C path commit`, `sudo git commit`, `git -c k=v commit`,
    `timeout 600 git commit` all match. Only the `commit` subcommand — NOT
    add/status/log/diff/push."""
    tokens = _skip_leading_wrappers(tokens)
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
    """True iff any shell segment of *command* is a `git commit`. Uses the QUOTE-AWARE
    newline→';' normalization (so a newline inside a quoted `-m` value is NOT a false
    separator), glued-';' resplit, and separator-split. Fail-safe: unparseable input →
    substring fallback (only a real `git commit` token pattern)."""
    normalized = _normalize_newlines_quote_aware(command)
    try:
        all_tokens = shlex.split(normalized)
    except ValueError:
        # Substring check on the NORMALIZED text with the separators it inserted
        # removed: only heredoc bodies with a found terminator are gone (stdin data),
        # so a body that merely MENTIONS `git commit` cannot make a non-commit
        # look like one, and no inserted separator can split a real `git … commit`.
        return bool(re.search(r"\bgit\b[^;&|]*\bcommit\b",
                              normalized.replace(" ; ", " ")))
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
# to 4 sequential git subprocesses, each bounded by _GATE_GIT_TIMEOUT: root rev-parse +
# (the single-commit fast path's ls-files + ls-tree, then diff --cached if it declines)
# OR (one -C retarget rev-parse + diff --cached + diff for -a). The fast path only runs
# without -C / -a, so the two never add up. The outer wait_for MUST exceed the
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


# ── Single-commit pathspec ("--only") fast path ─────────────────────────────────
# `git commit [--] <paths>` (no -a/-i/-p) commits ONLY those paths and leaves every
# other staged entry staged. The general path below still seeds pending with the WHOLE
# staged index, so a file a PARALLEL session staged was demanded as review coverage and
# the commit false-DENIED. The fast path narrows pending to the literal paths, but ONLY
# for the one command shape whose meaning we can prove; everything else keeps the
# general (wider) set. Each condition below closes a reproduced fail-open:
#   • the whole command is ONE simple command `git commit …` with NO shell operator
#     token at all, by an operator-aware tokenizer (plain shlex splits on whitespace
#     only, so `-m fix& a.py` reads as a value `fix&` while bash backgrounds a bare
#     `git commit` of the whole index). This also excludes `cd`, functions, `source`,
#     subshells, wrappers, a second unrecognised commit, and every redirect (a quoted
#     `'>x.py'` is a real file name once quotes are gone).
#   • no command substitution anywhere (`$(`, backtick, `<(`, `>(`) and no heredoc or
#     here-string (`<<`, `<<<`: an unquoted-delimiter heredoc body runs `$(…)`, and its
#     quotes are gone after tokenizing). One exception: the `-m "$(cat <<'EOF' … EOF)"`
#     message, whose delimiter quotes survive inside the double-quoted token.
#   • no expansion characters in ANY argument, option values included: `-m *.py a.py`
#     globs into extra pathspecs.
#   • every option an EXACT git-commit option (git 2.48 `git commit -h` plus the hidden
#     --allow-empty/--allow-empty-message). git accepts unambiguous long-option PREFIXES
#     (`--tra x` == `--trailer x`), so an unknown name could be an option whose value we
#     would misread as a path → not eligible. --amend is excluded: the rewritten commit
#     also carries the original HEAD's changes.
#   • every pathspec a plain path whose `git ls-files --full-name` match is EXACTLY
#     itself (not a directory prefix of a deleted tree, not a glob), whose HEAD-tree
#     match (`git ls-tree -r HEAD`) adds nothing else (a dir→file type change commits
#     the deletion of the old tree), and not a symlink or directory on disk. The
#     literal paths are then exactly the committed set.
# The general walk in _pending_commit_paths shares the option tables below.
_COMMIT_LONG_VALUE_OPTS = frozenset({
    "--file", "--author", "--date", "--message", "--reedit-message", "--reuse-message",
    "--fixup", "--squash", "--trailer", "--template", "--cleanup",
})
_COMMIT_LONG_FLAG_OPTS = frozenset({
    "--quiet", "--verbose", "--reset-author", "--signoff", "--edit", "--status",
    "--gpg-sign", "--only", "--no-verify", "--verify", "--dry-run", "--short",
    "--branch", "--ahead-behind", "--porcelain", "--long", "--null",
    "--no-post-rewrite", "--post-rewrite", "--untracked-files", "--allow-empty",
    "--allow-empty-message",
})
# Options that pull in paths/files beyond the literal pathspecs → never eligible.
_COMMIT_INDEX_SWEEP_OPTS = frozenset({
    "--all", "--include", "--interactive", "--patch", "--pathspec-from-file",
    "--pathspec-file-nul", "--amend",
})
_COMMIT_SHORT_VALUE = frozenset("FmcCt")   # value is the rest of the cluster or next token
_COMMIT_SHORT_FLAG = frozenset("qvsenoz")  # value-less
_COMMIT_SHORT_OPTARG = frozenset("Su")     # optional value, only ever STUCK (-Skey, -uno)
# Characters bash expands in an unquoted word (quotes are gone after tokenizing, so a
# quoted `*` is rejected too — that only costs the fast path, never safety).
_ARG_UNSAFE_CHARS = frozenset("*?[{~$`")
# ...plus what git or bash read specially inside a pathspec word.
_PATHSPEC_UNSAFE_CHARS = _ARG_UNSAFE_CHARS | frozenset("]}\\!:'\"#")
_SUBSTITUTION_MARKERS = ("$(", "`", "<(", ">(")
_SHELL_OPERATOR_CHARS = frozenset("();<>|&")
# `-m "$(cat <<'EOF' … EOF)"` — the commit-message convention. The command text has been
# through _normalize_newlines_quote_aware, which has ALREADY DROPPED a heredoc body
# once its terminator line is found, so a safe message heredoc reads exactly
# `$(cat <<'EOF'` + whitespace + `)`. Anything else left between them is text bash
# would run (an early terminator followed by commands, an unterminated body). QUOTED
# delimiter only: with an unquoted one bash expands `$(…)` inside the (dropped) body.
_SAFE_MSG_HEREDOC_RE = re.compile(r"\A(?:--message=)?\$\(cat <<-?(['\"])\w+\1\s*\)\Z")


def _is_safe_message_heredoc(token: str) -> bool:
    return bool(_SAFE_MSG_HEREDOC_RE.match(token))


def _operator_aware_tokens(normalized: str) -> list[str] | None:
    """Tokenize like a shell word splitter: unquoted operator characters become their own
    tokens (`fix&` → `fix`, `&`), quoted spans stay whole, `#` is never a comment (a
    dropped comment could hide a real pathspec). None if the text does not tokenize."""
    import shlex as _shlex
    lexer = _shlex.shlex(normalized, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        return None


def _commit_only_mode_pathspecs(args: list[str]) -> list[str] | None:
    """Pathspecs of a `git commit` argv (tokens after `commit`) iff every option is an
    exact known git-commit option and none sweeps the index; else None."""
    paths: list[str] = []
    after_dashdash = False
    i = 0
    while i < len(args):
        t = args[i]
        if after_dashdash:
            paths.append(t)
            i += 1
            continue
        if t == "--":
            after_dashdash = True
            i += 1
            continue
        if t.startswith("--"):
            name, eq, _val = t.partition("=")
            negated = "--" + name[5:] if name.startswith("--no-") else None
            if name in _COMMIT_INDEX_SWEEP_OPTS:
                return None
            if name in _COMMIT_LONG_VALUE_OPTS:
                if not eq:
                    if i + 1 >= len(args):
                        return None
                    i += 1  # the value token
            elif name in _COMMIT_LONG_FLAG_OPTS:
                pass
            elif negated and (negated in _COMMIT_LONG_FLAG_OPTS
                              or negated in _COMMIT_LONG_VALUE_OPTS):
                pass  # --no-<option>: value-less
            else:
                return None  # unknown or abbreviated long option
        elif t.startswith("-") and len(t) > 1:
            cluster = t[1:]
            j = 0
            while j < len(cluster):
                c = cluster[j]
                if c in _COMMIT_SHORT_VALUE:
                    if j + 1 == len(cluster):  # value is the next token
                        if i + 1 >= len(args):
                            return None
                        i += 1
                    break  # rest of the cluster (if any) is the value
                if c in _COMMIT_SHORT_OPTARG:
                    break  # optional value is the rest of the cluster
                if c not in _COMMIT_SHORT_FLAG:
                    return None  # -a/-i/-p (index sweep) or unknown
                j += 1
        else:
            paths.append(t)
        i += 1
    return paths


def _single_commit_only_paths(root: str, dir_path: str, normalized: str) -> set[str] | None:
    """The exact pending set for a provable single-command pathspec commit, or None when
    the command is not that shape (caller then uses the general, wider computation).
    `normalized` is the command after _normalize_newlines_quote_aware."""
    tokens = _operator_aware_tokens(normalized)
    if not tokens or len(tokens) < 3 or tokens[0] != "git" or tokens[1] != "commit":
        return None
    for tok in tokens:
        if _is_safe_message_heredoc(tok):
            continue
        if not tok or all(c in _SHELL_OPERATOR_CHARS for c in tok):
            return None  # a shell operator: a second command, a redirect, a group
        if "<<" in tok or any(m in tok for m in _SUBSTITUTION_MARKERS):
            return None
        if any(c in _ARG_UNSAFE_CHARS for c in tok):
            return None
    paths = _commit_only_mode_pathspecs(tokens[2:])
    if not paths:
        return None
    base = dir_path or root
    out: set[str] = set()
    rels: set[str] = set()
    for p in paths:
        if not p or p.startswith("-") or any(c in _PATHSPEC_UNSAFE_CHARS for c in p):
            return None
        ap = p if os.path.isabs(p) else os.path.join(base, p)
        if os.path.islink(ap) or os.path.isdir(ap):
            return None
        real = os.path.realpath(ap)
        rel = os.path.relpath(real, root)
        if rel.startswith(".."):
            return None
        out.add(real)
        rels.add(rel.replace(os.sep, "/"))
    try:
        r = subprocess.run(
            ["git", "-C", base, "ls-files", "-z", "--full-name", "--", *paths],
            capture_output=True, timeout=_GATE_GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    matched = {m.decode("utf-8", "surrogateescape") for m in r.stdout.split(b"\0") if m}
    if matched != rels:
        return None  # untracked, a directory prefix, or a case/normalization mismatch
    # A partial commit also matches the pathspec against HEAD: a path that was a
    # DIRECTORY in HEAD and is a file now commits the deletion of everything under it.
    # Unborn HEAD (ls-tree fails) → not provable here → general path.
    try:
        t = subprocess.run(
            ["git", "-C", base, "ls-tree", "-r", "-z", "--full-name", "--name-only",
             "HEAD", "--", *paths],
            capture_output=True, timeout=_GATE_GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if t.returncode != 0:
        return None
    in_head = {m.decode("utf-8", "surrogateescape") for m in t.stdout.split(b"\0") if m}
    if not in_head <= rels:
        return None
    return out


def _same_repo(option: str, value: str, root: str) -> bool:
    """Whether git option *option* (``-C`` / ``--work-tree`` / ``--git-dir``) at *value*
    still names the repository at *root*. A ``-C`` that resolves to no repo is git's
    own error (nothing commits). A ``--git-dir`` is that repo's git directory: the same
    repo when it is *root*'s own ``.git`` (a main checkout) or its common dir."""
    if option == "--git-dir":
        real = os.path.realpath(value)
        if real == os.path.realpath(os.path.join(root, ".git")):
            return True
        try:
            r = subprocess.run(["git", "-C", root, "rev-parse", "--git-common-dir"],
                               capture_output=True, text=True, timeout=_GATE_GIT_TIMEOUT)
            common = r.stdout.strip() if r.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return False
        return bool(common) and real == os.path.realpath(os.path.join(root, common))
    other = _gate_repo_root_for(value)
    return other == root or (other is None and option == "-C")


def _effective_commit_dir(dir_path: str, command: str) -> str:
    return _commit_dirs(dir_path, command)[1]


def _commit_dirs(dir_path: str, command: str) -> "tuple[str, str]":
    """(the shell's directory when the first ``git … commit`` of *command* starts, the
    directory that commit runs in): *dir_path*, moved by each ``cd <dir>`` before it (R1
    review r2), then by each ``git -C <dir>`` before ``commit``, in order (a relative
    one against the previous directory, as the shell and git do). *dir_path* twice when
    there is none or the command does not split (R1 review r1: a ``git -C <repo>
    commit`` from a non-repo cwd was unbound)."""
    import shlex as _shlex
    try:
        tokens = _shlex.split(_normalize_newlines_quote_aware(command))
    except ValueError:
        return dir_path, dir_path
    cwd = dir_path
    for i, tok in enumerate(tokens):
        if tok == "cd" and (i == 0 or tokens[i - 1] in (";", "&&", "||")) \
                and i + 1 < len(tokens) and tokens[i + 1] not in (";", "&&", "||", "|", "&"):
            cwd = os.path.join(cwd, os.path.expanduser(tokens[i + 1]))
            continue
        if os.path.basename(tok) != "git":
            continue
        here, j = cwd, i + 1
        while j < len(tokens) and tokens[j] != "commit":
            if tokens[j] == "-C" and j + 1 < len(tokens):
                here = os.path.join(here, os.path.expanduser(tokens[j + 1]))
                j += 2
                continue
            if tokens[j] in (";", "&&", "||", "|", "&"):
                break
            j += 1
        if j < len(tokens) and tokens[j] == "commit":
            return cwd, here
    return dir_path, dir_path


def _commit_identity(path: str) -> str:
    """The canonical identity of a file a commit names (R1 review r2): its PARENT is
    realpath'd (so two spellings of the same directory agree), but the final component
    is kept verbatim. A tracked symlink is committed AS the link, not as what it points
    at, so resolving the leaf would move it out of the repo and past the gate."""
    path = path if os.path.isabs(path) else os.path.abspath(path)
    parent, name = os.path.split(path)
    return os.path.join(os.path.realpath(parent), name)


def _pending_commit_paths(dir_path: str, command: str):
    """The set of ABSOLUTE paths a `git commit` is about to commit; or None if
    uncomputable (not a repo / git error / timeout → gate fails OPEN); or the
    _PENDING_CROSS_REPO sentinel if a global option retargets another repo (→ DENY).

    A command that is exactly one provable pathspec commit (`git commit [opts] -- f1 f2`,
    see _single_commit_only_paths) returns just those files: git commits only them, so
    files a parallel session staged are not demanded. Every other shape folds EVERY
    sweep form: staged (`--cached`), `-a/--all` / `--pathspec-from-file` working-tree,
    positional pathspec, `-o/--only` — a deliberately wide set. Git-diff paths are
    repo-root-relative; a positional pathspec is COMMITTER-CWD-relative (git
    semantics), NOT root-relative."""
    import shlex as _shlex
    origin, dir_path = _commit_dirs(dir_path, command)
    root = _gate_repo_root_for(dir_path)
    if root is None:
        return None

    # Isolate the `git commit` command's own argv BEFORE token-walking. A heredoc/`-F -`
    # message body or OTHER commands in a multi-line Bash script must NOT leak their words
    # as FALSE positional pathspecs (false-DENY, run_463715dc), AND a real positional
    # pathspec must NOT be dropped (fail-OPEN, run_68245424).
    #
    # ROOT FIX (run_68245424 — replaces the old raw-text `_HEREDOC_BODY_RE` pre-strip):
    # the pre-strip ran a MULTILINE/DOTALL regex on RAW command text with NO shell-quote
    # context, so a shell-QUOTED `<<A` inside `-m "<<A\n"` was mis-read as a real heredoc;
    # its phantom body swallowed the real `secret.py` pathspec → pending missed it →
    # fail-OPEN (verified: bash commits secret.py, the gate did not see it). The fix is to
    # stop guessing shell syntax on raw text: normalize newlines QUOTE-AWARELY (a newline
    # inside a quoted span is literal data, not a separator), then let shlex tokenize.
    #   • quoted `<<A\n` → stays inside the `-m` value token; `secret.py` stays a token in
    #     the SAME commit segment → collected as a pathspec → covered-check sees it.
    #   • a REAL (unquoted) heredoc body → DROPPED by the normalizer once its terminator
    #     line is found (it is stdin DATA),
    #     so its words never become pathspecs and a stray `'`/`"` in it (`don't`, an
    #     odd `"`) can no longer invert quote state and push shlex into the whitespace
    #     fallback that turned every body word into a pathspec (the `-F - <<'EOF'`
    #     false-DENY).
    # No regex on raw text: heredocs are recognised only in UNQUOTED context. An unclosed quote only MERGES segments (over-collect →
    # fail-CLOSED). `-a`/`--cached` sweeps still come from `git diff` below, independent.
    _normalized = _normalize_newlines_quote_aware(command)
    _shlex_ok = True
    try:
        _all = _shlex.split(_normalized)
    except ValueError:
        # ONE segment like HEAD (over-collect = fail-closed): drop only the separators
        # the normalizer inserted; terminated heredoc bodies stay dropped.
        _all = _normalized.replace(" ; ", " ").split()
        _shlex_ok = False
    _resplit: list[str] = []
    for _tok in _all:
        if ";" in _tok and _tok != ";":
            _parts = _tok.split(";")
            for _idx, _p in enumerate(_parts):
                if _p:
                    _resplit.append(_p)
                if _idx < len(_parts) - 1:
                    _resplit.append(";")
        else:
            _resplit.append(_tok)
    _all = _resplit
    _SEP = {";", "&&", "||", "|", "&"}
    _segments: list[list[str]] = []
    _cur: list[str] = []
    for _tok in _all:
        if _tok in _SEP:
            if _cur:
                _segments.append(_cur)
            _cur = []
        else:
            _cur.append(_tok)
    if _cur:
        _segments.append(_cur)
    # A provable single-segment pathspec commit is gated on exactly its literal paths,
    # so a file a parallel session staged is no longer demanded (see the fast-path block
    # above _single_commit_only_paths). Any other shape falls through to the wider set.
    if _shlex_ok:
        _only = _single_commit_only_paths(root, dir_path, _normalized)
        if _only is not None:
            return _only
    # (3) W3: select EVERY segment that is a `git commit` (via _segment_is_git_commit,
    #     which strips wrappers/env-assignments/git global options) — NOT a bare
    #     `"commit" in seg` (which would match a `-m "commit the fix"` message value).
    #     ALL commit segments, not just the first: a chained `git commit A && git commit B`
    #     is ONE PreToolUse command, and the gate computes pending ONCE for it — so a
    #     SECOND commit's positional pathspec must enter pending too, or an unreviewed
    #     path it commits (one NOT in `git diff --cached`, e.g. a modified-not-staged file
    #     committed by a positional pathspec) silently sneaks past `pending ⊆ covered`
    #     (Gate-2 HIGH fail-open, run_463715dc — the single-segment version I first shipped
    #     had exactly this hole). Fall back to the whole token stream if NO segment matches
    #     (defensive: over-collect rather than silently empty pending → never fail-open).
    commit_segments = [seg for seg in _segments if _segment_is_git_commit(seg)]
    if not commit_segments:
        commit_segments = [_all]

    # GLOBAL options BEFORE `commit` retarget git: `-C <dir>` / `--git-dir=` /
    # `--work-tree=` can point at ANOTHER repo → the pending set computed against
    # dir_path would be wrong (Gate-2 HIGH bypass: `git -C /other commit` unreviewed).
    # If a retarget resolves to a DIFFERENT root, we cannot bind it → DENY sentinel.
    # Checked for EVERY commit segment — a retarget in ANY chained commit poisons the bind.
    for _seg in commit_segments:
        try:
            _ci = _seg.index("commit")
        except ValueError:
            _ci = len(_seg)
        _g = 1  # skip tokens[0] == "git"
        while _g < _ci:
            gt = _seg[_g]
            # a retarget that names another repo, or one that cannot be resolved to a
            # work tree (a bare --git-dir), cannot be bound: refused (R1 review r1)
            if gt in ("-C", "--git-dir", "--work-tree") and _g + 1 < _ci:
                if not _same_repo(gt, os.path.join(origin, _seg[_g + 1]), root):
                    return _PENDING_CROSS_REPO
                _g += 2; continue
            if gt.startswith(("--git-dir=", "--work-tree=")):
                name, value = gt.split("=", 1)
                if not _same_repo(name, os.path.join(origin, value), root):
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

    # Decode EACH commit segment's flags/pathspec. Walk tokens AFTER the `commit`
    # subcommand, accumulating cwd_paths/dash_a across ALL commit segments (so a
    # chained second commit's positional pathspec is captured, not dropped — see the
    # per-segment rationale above).
    dash_a = False
    for tokens in commit_segments:
        try:
            ci = tokens.index("commit")
        except ValueError:
            ci = 0
        i = ci + 1
        while i < len(tokens):
            t = tokens[i]
            # Shell REDIRECTION tokens are not pathspecs. Two shapes (run_68245424 Gate-2
            # correctness finding — the earlier version skipped only the operator token
            # and left `> out.log`'s OPERAND `out.log` to be collected as a FALSE pathspec
            # → a reviewed commit false-DENIED):
            #   • BARE operator (`>`,`>>`,`<`,`2>`,`&>`,`>&`…): operand is the NEXT token →
            #     skip BOTH.
            #   • SELF-CONTAINED (`>out.log`,`2>&1`,`<<EOF`,`<<-EOF`): one token → skip it.
            # Only these unambiguous shapes match; a real filename never starts with a
            # redirection operator, so nothing real is skipped (fail-safe both ways).
            if _REDIR_BARE_OP_RE.match(t):
                i += 2  # operator + its operand (the redirect target), both non-pathspec
                continue
            if _REDIR_SELF_CONTAINED_RE.match(t):
                i += 1
                continue
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
                elif name in _COMMIT_LONG_VALUE_OPTS or name == "--pathspec-from-file":
                    if name == "--pathspec-from-file":
                        # Its paths live in a file we do not read; any tracked change could
                        # be listed there, so sweep the working tree like -a (widen).
                        dash_a = True
                    if not eq and i + 1 < len(tokens):
                        i += 1  # space form consumes next token (equals form does not)
                # --gpg-sign[=keyid] / --amend / --no-edit / --signoff → no path arg
            elif t.startswith("-") and len(t) > 1:
                # Decode a short-flag cluster. VALUE flags whose value is a SEPARATE token
                # when trailing: -m -C -c -F -t. NOT -S / -u (their value is OPTIONAL and
                # must be STUCK: -Skey, -uno) and NOT -s / -o (no value) — consuming a next
                # token for any of them would swallow a real pathspec (Gate-2 MED:
                # -S secret.py; `-u mine.py` commits mine.py). -o's path is positional.
                cluster = t[1:]
                j = 0
                consumed_next = False
                while j < len(cluster):
                    c = cluster[j]
                    if c == "a":
                        dash_a = True; j += 1; continue
                    if c in _COMMIT_SHORT_OPTARG:
                        break  # optional value, stuck to the flag: rest of cluster
                    if c in _COMMIT_SHORT_VALUE:
                        inline = cluster[j + 1:]  # rest of cluster is this flag's value
                        if not inline and i + 1 < len(tokens):
                            consumed_next = True  # value is the next token; skip it
                        break  # rest of cluster (if any) was this flag's value
                    # value-less short flag (-q -v -e -s -o -n -z…) → skip
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
        out.add(_commit_identity(rel if os.path.isabs(rel) else os.path.join(root, rel)))
    for p in cwd_paths:    # positional pathspec / -o → relative to committer CWD
        out.add(_commit_identity(p if os.path.isabs(p) else os.path.join(dir_path or root, p)))
    return out


def _session_adversarial_coverage(session_id: str):
    """Return (has_marker, covered_abs_paths, has_unbounded) for this session's
    adversarial markers. `has_unbounded` is True iff SOME marker lacks the
    reviewed_paths KEY (git-unavailable at review time = unbounded, back-compat).
    A marker with reviewed_paths == [] contributes the EMPTY set to `covered` and
    does NOT set has_unbounded — the []-vs-key-absent distinction is by KEY
    PRESENCE, never truthiness (Gate-1 round-2 #3).

    C2: `has_unbounded` is NO LONGER a blanket approve signal. The gate now binds
    the diff FIRST (covered ⊇ pending → approve); an unbounded marker only serves
    as a LOUD (WARN) fail-open fallback when covered does not cover pending AND git
    genuinely couldn't compute coverage at review time. So a single path-less marker
    can no longer unlock every commit in the session — a real, coverable-but-uncovered
    path is still DENIED. Fail-open on THIS helper's own failure is still signalled by
    returning (True, set(), True) on OSError (the caller then hits the unbounded
    fallback + WARN)."""
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

        if force_enabled("adversarial_commit_gate", input_data):
            return {"decision": "approve"}

        session_id = session_context.get("sdk_session_id", "") or ""
        cwd = input_data.get("cwd", "") or ""

        # Coverage decision (all git subprocesses run off-loop + are per-call
        # timeout-bounded so this gate can never hang the commit — bash_syntax_guard
        # discipline). Order of the fail-OPEN branches is load-bearing.
        try:
            has_marker, covered, has_unbounded = await asyncio.wait_for(
                executors.run_in("gate", _session_adversarial_coverage, session_id),
                timeout=_GATE_COVERAGE_TIMEOUT,
            )
        except Exception:  # incl. asyncio.TimeoutError; ANY gate-infra failure (even
            logger.warning("_gate: gate infrastructure error — failing open", exc_info=True)
            return {"decision": "approve"}  # a bug in the helper) fails OPEN by design

        if not has_marker:
            pass  # → DENY below (Plan B parity: no adversarial review at all)
        else:
            # C2: an unbounded (path-less) marker no longer SHORT-CIRCUITS to approve.
            # A missing reviewed_paths key means "git was unavailable at review time"
            # (legit back-compat degrade) — but it must NOT be a blanket pass that
            # lets a path NO review covered ride through. So: always try to bind the
            # diff first (covered ⊇ pending → approve). ONLY if binding is impossible
            # OR pending is not covered do we fall back to the unbounded fail-open —
            # and then loudly (WARN), never silently. This preserves the legit-degrade
            # path (a real review whose marker lost its paths still approves when its
            # covered set — from OTHER bounded markers — suffices, or when nothing can
            # be computed) while closing the "one path-less marker unlocks every commit"
            # hole (a real, uncovered path is now DENIED even with an unbounded marker
            # present, unless git genuinely can't compute the pending set).
            #
            # Bind to the diff: every pending path must be covered. Pending set
            # uncomputable (not a repo / git error / timeout) → fail OPEN.
            try:
                pending = await asyncio.wait_for(
                    executors.run_in("gate", _pending_commit_paths, cwd, command),
                    timeout=_GATE_COVERAGE_TIMEOUT,
                )
            except Exception:  # incl. asyncio.TimeoutError → fail open (never hang)
                logger.warning("_gate: gate infrastructure error — failing open", exc_info=True)
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
            elif has_unbounded:
                # Covered set (from bounded markers) does NOT cover pending, but an
                # unbounded marker exists → fall back to the back-compat fail-open,
                # but LOUDLY: git was unavailable at review time, so we cannot prove
                # coverage. This is observable (unlike the old silent short-circuit)
                # so a persistent unbounded-marker pattern surfaces instead of hiding.
                logger.warning(
                    "[adversarial-gate] UNBOUNDED fail-open — a path-less adversarial "
                    "marker (git unavailable at review time) does not cover pending "
                    "path(s) %s; approving on back-compat degrade (session=%s)",
                    sorted(p for p in pending if p not in covered)[:5], session_id,
                )
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
            logger.debug("_gate: suppressed exception on a fail-safe path", exc_info=True)
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
                    "only the operator can make one, with SWARM_GATE_FORCE=adversarial_commit_gate in the backend/daemon environment; a variable set in a Bash command has no effect.)"
                ),
            }
        }

    return _gate


# ---------------------------------------------------------------------------
# active-run commit gate — a live v1 run's files land only through its landing (Bash)
# ---------------------------------------------------------------------------
# A v1 run lands its change (code + its REPORT deliverable) with `pipeline_v1.py land`,
# which checks the signed tests and review first. An agent `git commit` of a path the
# run changed (RunEngine.owned_paths: its signed code facts) would put the change on
# main without those checks, so it is denied and pointed at the landing. The landing
# commits in its own subprocess, never through the agent's Bash tool, so it never
# reaches this gate. A relevant run that cannot be read, or a commit retargeted at
# another repo (`git -C`, `--git-dir`), is denied rather than let through (R1 review).

# Scan root for active pipeline runs. Overridable in tests. The daemon's live
# workspace is ~/.swarm-ai/SwarmWS; runs live at Projects/*/.artifacts/runs/*/run.json.
_ACTIVE_RUN_GLOB_ROOT = os.path.expanduser("~/.swarm-ai/SwarmWS")



class _UnreadableContract(Exception):
    """A run directory's protected contract exists but cannot be read."""

    def __init__(self, run_id: str) -> None:
        super().__init__(run_id)
        self.run_id = run_id


def _runs_touching(pending: set) -> "list[tuple[str, str]]":
    """(project, run_id) of every v1 run whose code a pending path lies in: its
    protected contract names a repo root, or the run has a worktree, that holds the
    path. Read from the run directory's own location and the protected contract only:
    nothing an agent can edit in run.json (R1 review r1). No git, no event fold."""
    from config import get_app_data_dir
    from core import run_worktree
    from core.run_protocol.engine import signed_terminal
    from core.run_protocol.runner import read_contract
    data_dir = get_app_data_dir()
    projects = os.path.join(_ACTIVE_RUN_GLOB_ROOT, "Projects")
    out: list = []
    try:
        project_names = os.listdir(projects)
    except OSError:
        return out
    for project in project_names:
        runs_dir = os.path.join(projects, project, ".artifacts", "runs")
        try:
            run_ids = os.listdir(runs_dir)
        except OSError:
            continue
        for run_id in run_ids:
            if not re.fullmatch(r"run_[A-Za-z0-9_-]+", run_id):
                continue                                 # not a run directory
            # A contract that EXISTS but cannot be read leaves the run's repos unknown:
            # raise, so the caller refuses instead of treating it as owning nothing.
            try:
                contract = read_contract(data_dir, run_id)
            except Exception as exc:  # noqa: BLE001
                # A run whose SIGNED log says it finished cannot own anything now: skip
                # it, so one broken old run does not block every commit (re-audit
                # 84b72ec1 MED). Read from the signed events, never run.json (c772dd92).
                if signed_terminal(data_dir, run_id):
                    logger.warning("run %s: contract unreadable, run finished — skipped",
                                   run_id)
                    continue
                raise _UnreadableContract(run_id) from exc
            if not isinstance(contract, dict):
                continue                                 # no contract: not a v1 run
            homes = []
            for name, root in (contract.get("repo_roots") or {}).items():
                if isinstance(root, str):
                    homes.append(os.path.realpath(root))
                try:
                    homes.append(os.path.realpath(str(run_worktree.wt_path(run_id, name))))
                except Exception:  # noqa: BLE001
                    pass
            # homes are realpath'd dirs; a pending identity's parent is realpath'd too
            if any(p == h or p.startswith(h + os.sep) for p in pending for h in homes):
                out.append((project, run_id))
    return out


def _active_run_ownership(touching: "list[tuple[str, str]]") -> "tuple[set, set]":
    """(real paths owned by those runs that are not terminal, their run ids). Each
    run's state and paths come from the run itself (``RunEngine``: the signed events'
    lifecycle, its contract, its code). A run that cannot be read raises: the caller
    then refuses the commit rather than treat an unreadable run as owning nothing."""
    from config import get_app_data_dir
    from core.run_protocol import lifecycle
    from core.run_protocol.engine import RunEngine
    owned: set = set()
    ids: set = set()
    data_dir = get_app_data_dir()
    for project, run_id in touching:
        engine = RunEngine.load(data_dir=data_dir, workspace=_ACTIVE_RUN_GLOB_ROOT,
                                project=project, run_id=run_id)
        if engine.projection()["status"] in lifecycle.TERMINAL:
            continue
        ids.add(run_id)
        owned |= engine.owned_paths()
    return owned, ids


# A run's review deliverable (B) lives at <repo>/.aidlc/deliverables/<run_id>/ in
# whichever repo the landing writes it, so it is matched by path shape, not by root.
_DELIVERABLE_PATH_RE = re.compile(r"[\\/]\.aidlc[\\/]deliverables[\\/]([A-Za-z0-9_-]{1,64})[\\/]")


def _active_run_deliverables(pending: set, run_ids: set) -> list:
    """Pending paths that are an active run's review deliverable (REPORT.md and
    anything else under its .aidlc/deliverables/<run_id>/). Committing one by hand
    is the REPORT-only commit that splits a delivery in two."""
    return sorted(p for p in pending
                  if (m := _DELIVERABLE_PATH_RE.search(p)) and m.group(1) in run_ids)


def active_run_commit_deny_text(offending: "list[str]", run_ids: "list[str]") -> str:
    """The active-run commit gate's deny (E): names the landing command that exists."""
    run = run_ids[0] if len(run_ids) == 1 else "<run_id>"
    return (
        "git commit → DENY: this commit touches file(s) owned by an ACTIVE pipeline run ("
        + ", ".join(list(offending)[:5]) + "; run " + ", ".join(list(run_ids)[:3]) + "). "
        "A run's code and its REPORT.md land together in ONE commit made by the run's "
        "DELIVER landing; a commit by hand splits them. FIX: do not `git commit` the run's "
        "code yourself: in DELIVER run `python backend/scripts/pipeline_v1.py land "
        f"--project <project> --run-id {run} --message '<message>'`. A run's own "
        "REPORT.md (.aidlc/deliverables/<run_id>/) is never committed by hand either. If "
        "this is genuinely NOT a pipeline-run file (false match), tell the user: only the "
        "operator can bypass this gate, with SWARM_GATE_FORCE=active_run_commit_gate in "
        "the backend/daemon environment.")


def create_active_run_commit_gate(session_context: dict[str, Any]):
    """Factory: PreToolUse (Bash) gate that DENYs an agent `git commit` whose pending
    paths are an ACTIVE v1 run's code (``_active_run_ownership``) or its reserved
    REPORT, routing it to the run's ``land``. Prevents the code/REPORT split.
    session_context is accepted for registration symmetry (unused — ownership is
    global across runs, not session-scoped)."""

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

        if force_enabled("active_run_commit_gate", input_data):
            return {"decision": "approve"}

        cwd = input_data.get("cwd", "") or ""

        # Compute the pending commit path-set off-loop + timeout-bounded (same discipline
        # as adversarial_commit_gate — the gate can never hang the commit). ANY failure
        # fails OPEN.
        try:
            pending = await asyncio.wait_for(
                executors.run_in("gate", _pending_commit_paths, cwd, command),
                timeout=_GATE_COVERAGE_TIMEOUT,
            )
        except Exception:  # incl. asyncio.TimeoutError
            logger.warning("_gate: gate infrastructure error — failing open", exc_info=True)
            return {"decision": "approve"}

        # None (uncomputable: not a repo / git error) / empty (no-op/amend-no-diff) →
        # nothing to bind → fail-open approve. A retarget to another repo that cannot be
        # bound is refused (R1 review r1): it may be a run's repo.
        if pending is _PENDING_CROSS_REPO:
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "git commit → DENY: this commit points git at another repository "
                    "(-C / --git-dir / --work-tree) than the one it runs in, so its files "
                    "cannot be checked against active pipeline runs. Run the commit from "
                    "inside that repository instead (`cd <repo> && git commit …`).")}}
        if pending is None or not pending:
            return {"decision": "approve"}
        if not isinstance(pending, set):
            return {"decision": "approve"}

        try:
            touching = await asyncio.wait_for(
                executors.run_in("gate", _runs_touching, pending),
                timeout=_GATE_COVERAGE_TIMEOUT,
            )
        except _UnreadableContract as exc:   # its repos are unknown: refuse, never skip
            logger.warning("active_run_commit_gate: contract of %s unreadable — refusing",
                           exc.run_id)
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"git commit → DENY: pipeline run {exc.run_id} has a protected contract "
                    "that cannot be read, so which repositories it owns is unknown. Check "
                    f"the run (`python backend/scripts/pipeline_v1.py status --project "
                    f"<project> --run-id {exc.run_id}`) and abandon it if it is broken.")}}
        except Exception:
            logger.warning("_gate: gate infrastructure error — failing open", exc_info=True)
            return {"decision": "approve"}
        if not touching:
            return {"decision": "approve"}
        try:
            owned, run_ids = await asyncio.wait_for(
                executors.run_in("gate", _active_run_ownership, touching),
                timeout=_GATE_COVERAGE_TIMEOUT * 2,
            )
        except Exception as exc:  # a run this commit touches could not be read in time
            names = ", ".join(rid for _p, rid in touching[:3])
            logger.warning("active_run_commit_gate: runs %s unreadable (%r) — refusing",
                           names, exc)
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"git commit → DENY: this commit touches the code of pipeline run(s) "
                    f"{names}, whose state could not be read just now ({type(exc).__name__}). "
                    "Retry the commit; if it keeps failing, check the run with "
                    "`python backend/scripts/pipeline_v1.py status --project <project> "
                    "--run-id <run_id>`.")}}
        reports = _active_run_deliverables(pending, run_ids)
        offending = sorted({*(p for p in pending if p in owned), *reports})
        if not offending:
            return {"decision": "approve"}

        logger.warning(
            "[BLOCKED] git commit of active-run path(s) %s — lands only through the "
            "run's DELIVER landing (command=%s)", offending[:5], command[:80],
        )
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": active_run_commit_deny_text(
                    offending, sorted(run_ids)),
            }
        }

    return _gate


# ---------------------------------------------------------------------------
# pipeline-test gate + direct-commit gate (PreToolUse, Bash)
# design: docs/design/pipeline-speed-systematic-fix.md §4-B3 and §4-G1
# ---------------------------------------------------------------------------
# Both gates key on "this session's active run" (contract C6): a run.json under
# <workspace>/Projects/*/.artifacts/runs/*/run.json with status active / reflecting and
# session_id == the hook's APP session id. The app session id arrives as
# session_context["sdk_session_id"] (historical name; see _other_live_sessions_present).
#
#   • pipeline_test_gate (B3): while the session HAS an active run, every test/build
#     command goes through backend/scripts/pipeline_test.py (one entry point, scoped
#     by mode, cached, hang-safe). Direct pytest / vitest / tsc / eslint / vite build /
#     npm|pnpm|yarn|bun build|test, make test*, `git worktree add`, inline / stdin python
#     that names a test runner AND can run code, and manual hang triage (ps / pgrep / kill / pkill aimed
#     at pytest, python -X faulthandler) are DENIED with a copyable runner command.
#     A session with no active run is never affected.
#   • direct_commit_gate (G1): a session with NO active run of its own that commits
#     more than 20 changed SOURCE lines (pure-doc paths, core/doc_paths.py, excluded) in the SwarmAI
#     source repo is DENIED and pointed at `pipeline_v1.py create --profile direct`, so a direct
#     fix also gets an exit contract. Commits in other repos are never gated.
#
# FAIL-OPEN on every infra error or timeout (adversarial_commit_gate discipline).
# Each session is judged ONLY by runs whose session_id equals its own id: a run with
# no session_id (pre-C1) is nobody's run, so it neither gates its creator nor exempts
# anyone else. Creating a run with SWARM_APP_SESSION_ID removed or overridden from a
# Bash command is denied by session_link_gate (section below); a v1 run cannot be
# created without one (its contract names the owner). Only runs active within
# _RUN_ACTIVE_WINDOW_SECONDS (3 h) count: an active run's projection is rewritten on
# every pipeline_v1.py call, so an idle "active" one is a run nobody drives and must
# not pin a gate.
#
# ACCEPTED RESIDUAL (same trade as pytest_command_guard): eval, project scripts and
# `sudo -u X` are not recognised; a cd to an unresolvable directory (`cd $X`, `cd -`,
# popd, or any cd joined by `||` / `|` / `&`) leaves the directory unknown, so a bare
# make after it is not a hit (explicit test*/check* targets still are); `yarn check`
# (a v1 builtin, denied as a check script) is imprecise. Recognised: `<shell> -c`
# (bash, sh, zsh, dash, ksh, mksh, csh, tcsh, fish) and the package-manager shell
# modes (`pnpm [-c|-rc|--shell-mode] exec|dlx [-c]`, `pnpx|pnx -c`, `npx -c`,
# `npm exec -c|--call`; see _js_pm_shell_c_command), both one level deep (a shell
# -c skips -o/+o/--long options),
# make targets test*/check*/lint*/typecheck* (and a bare make whose default goal is
# one, resolved in the directory a leading `cd X &&` / `cd X;` / `pushd X &&` of the
# same chain moved to), coverage run, python -c / stdin code naming pytest or vitest,
# node entry points of vitest/eslint/tsc, npm run test*/lint*/typecheck*/check*/
# build*, and the same for pnpm / yarn / bun (`<pm> test`, `<pm> run <script>`, the
# implicit `<pm> lint|vitest|build`, `pnpm exec|dlx`, `yarn exec|dlx`, `bunx`,
# `bun x`, `bun build`); `pnpm install`, `yarn`, `bun install`, `pnpm dev` pass.

# An active run counts only while it shows recent activity (run.json mtime or its
# updated_at, whichever is newer): a run nobody drives must not gate tests for days.
_RUN_ACTIVE_WINDOW_SECONDS = 3 * 3600
_RUN_SCAN_TIMEOUT = _GATE_GIT_TIMEOUT
# _pending_commit_paths (≤4 git calls) + rev-parse + 2 numstat calls, each ≤ _GATE_GIT_TIMEOUT.
_DIRECT_GATE_TIMEOUT = _GATE_GIT_TIMEOUT * 7 + 3
_DIRECT_COMMIT_MAX_SOURCE_LINES = 20

try:
    # backend/core/security_hooks.py → repo root = parents[2] (NOT cwd: daemon cwd is backend/).
    _SOURCE_REPO_ROOT: str | None = os.path.realpath(str(Path(__file__).resolve().parents[2]))
except Exception as _exc:  # pragma: no cover - import-time path edge
    logger.warning("direct_commit_gate: cannot resolve source repo root: %s", _exc)
    _SOURCE_REPO_ROOT = None
_SOURCE_ROOT_CACHE: "list" = [0.0, None]


def _source_repo_root() -> "str | None":
    """The SwarmAI source repo (a git toplevel). From this module's own path when it is
    one (dev, tests) — in the packaged daemon ``__file__`` lies inside the bundle, so
    that path is never the repo and every gate scoped to it silently failed open. Then
    from the newest v1 runs' protected contracts (their ``repo_roots``): the first root
    that holds this module's twin. Cached 60 s; None when neither resolves."""
    if _SOURCE_REPO_ROOT and os.path.isdir(os.path.join(_SOURCE_REPO_ROOT, ".git")):
        return _SOURCE_REPO_ROOT
    import time as _time
    now = _time.time()
    if now - _SOURCE_ROOT_CACHE[0] < 60:
        return _SOURCE_ROOT_CACHE[1]
    import glob as _glob
    files = []
    for f in _glob.glob(os.path.join(_pipeline_runs_root(), "Projects", "*", ".artifacts",
                                     "runs", "*", "run.json")):
        try:
            files.append((os.path.getmtime(f), f))
        except OSError:
            continue
    found = None
    try:
        from config import get_app_data_dir
        from core.run_protocol.runner import read_contract
        data_dir = get_app_data_dir()
    except Exception:  # noqa: BLE001 — no config: nothing to read
        data_dir = None
    for _t, f in sorted(files, reverse=True)[:50]:   # newest first, stop at the first fit
        try:
            contract = read_contract(data_dir, os.path.basename(os.path.dirname(f))) \
                if data_dir is not None else None
        except Exception:  # noqa: BLE001 — an unreadable contract names no root
            contract = None
        roots = (contract or {}).get("repo_roots") if isinstance(contract, dict) else None
        for root in (roots or {}).values() if isinstance(roots, dict) else ():
            if not isinstance(root, str) or not os.path.isabs(root):
                continue
            root = os.path.realpath(root)
            # a main checkout (.git a directory), never a linked worktree
            if os.path.isdir(os.path.join(root, ".git")) and os.path.isfile(
                    os.path.join(root, "backend", "core", "security_hooks.py")):
                found = root
                break
        if found:
            break
    _SOURCE_ROOT_CACHE[:] = [now, found]
    return found


def _pipeline_runs_root() -> str:
    """Workspace root whose Projects/*/.artifacts/runs/*/run.json hold pipeline runs.

    Mirrors artifact_cli._get_workspace, the WRITER of run.json: SWARM_WORKSPACE wins,
    else the default the active-run gate scans (_ACTIVE_RUN_GLOB_ROOT, read at call
    time so tests can repoint it)."""
    ws = os.environ.get("SWARM_WORKSPACE")
    if ws:
        return os.path.expanduser(ws)
    return _ACTIVE_RUN_GLOB_ROOT


def _run_activity_time(path: str, data: dict) -> float:
    """Last activity of a run: the newer of run.json's mtime and its ``updated_at``
    (ISO-8601; a missing/unparseable value is ignored)."""
    t = os.path.getmtime(path)
    raw = data.get("updated_at")
    if isinstance(raw, str) and raw:
        try:
            from datetime import datetime as _dt, timezone as _tz
            parsed = _dt.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=_tz.utc)
            t = max(t, parsed.timestamp())
        except (ValueError, OverflowError):
            pass
    return t


def _v1_cmd(args: str) -> str:
    from core.worktree_write_gate import v1_command
    return v1_command(args)


def _signed_activity_time(view: dict) -> float:
    """Last activity of a run: its newest SIGNED event's time (never run.json's mtime
    or ``updated_at``, which the agent can write). Unknown -> 0 (not active)."""
    raw = view.get("_last_event_at")
    if not isinstance(raw, str) or not raw:
        return 0.0
    try:
        from datetime import datetime as _dt, timezone as _tz
        parsed = _dt.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_tz.utc)
        return parsed.timestamp()
    except (ValueError, OverflowError):
        return 0.0


def _session_run_state(app_session_id: str) -> "dict[str, str] | None":
    """C6 scan → ``{"project", "id"}`` of THIS session's active run, else None.

    Active = ``status`` active / reflecting AND ``session_id == app_session_id`` AND last
    activity (_run_activity_time) within _RUN_ACTIVE_WINDOW_SECONDS; the most recently
    active match wins. A session is judged ONLY by its own runs: a run with another
    session_id, or with no session_id at all (unowned), is simply not this session's
    run and changes nothing for it — no global fail-open. A Bash command that would
    create a run with SWARM_APP_SESSION_ID removed or overridden is denied by
    session_link_gate (create_session_link_gate); a v1 run cannot be created
    without one. A corrupt or
    unreadable run.json is skipped. A glob failure propagates; each caller fails OPEN on it."""
    import time as _time
    from core.run_worktree import _iter_runs

    if not app_session_id:
        return None
    cutoff = _time.time() - _RUN_ACTIVE_WINDOW_SECONDS
    best: dict[str, str] | None = None
    best_t = -1.0
    # status, owner, id and project from the SIGNED view (events + contract + run dir),
    # never run.json's own fields (B-class re-check: a forged status/session_id lifted
    # direct_commit_gate and the test gate)
    for _path, data in _iter_runs(_pipeline_runs_root()):
        f = str(_path)
        try:
            t = _signed_activity_time(data)
            if t < cutoff:
                continue
            if data.get("status") not in ("active", "reflecting"):   # a v1 run at work
                continue
            if data.get("session_id") != app_session_id:
                continue
        except Exception:
            logger.debug("_session_run_state: skipped unreadable %s", f, exc_info=True)
            continue
        if t < cutoff or t <= best_t:
            continue
        run_dir = os.path.dirname(f)
        project_dir = os.path.dirname(os.path.dirname(os.path.dirname(run_dir)))
        best = {"project": os.path.basename(project_dir), "id": os.path.basename(run_dir)}
        best_t = t
    if best is None:
        # pipeline-run-worktree §4.2: an isolated run with a live worktree is this
        # session's run however long BUILD has been quiet (no 3-hour window).
        from core.run_worktree import own_isolated_run, wt_root
        if wt_root().is_dir():
            own = own_isolated_run(app_session_id, _pipeline_runs_root(), with_empty=False)
            if own is not None:
                best = {"project": own["project"], "id": own["id"]}
    return best


_GATE_PUNCT_CHARS = ";&|()<>"
_GATE_SEPARATOR_CHARS = frozenset(";|()")
_PY_INTERP_RE = re.compile(r"^python[\d.]*$")
_JS_TEST_FILE_RE = re.compile(r"\.(?:test|spec)\.[cm]?[jt]sx?$")
_SHELL_KEYWORDS = frozenset({"!", "{", "}", "then", "do", "else", "elif", "if",
                             "while", "until", "exec"})
# Shells whose `-c STRING` is classified like the outer command (csh / tcsh / fish
# take -c too; their builtins unsetenv / setenv / `set -e` are covered by
# session_link_gate).
_SHELL_INTERPRETERS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "mksh",
                                 "csh", "tcsh", "fish"})
_TRIAGE_PROGRAMS = frozenset({"ps", "pgrep", "pkill", "kill", "killall"})
# The ones that ACT on a test process. A read-only listing (ps / pgrep) changes nothing
# and is how a stall is seen from outside, so it is not a test run; killing a test
# process by hand is (the runner owns hang triage and reports stacks).
_TRIAGE_ACTING_PROGRAMS = frozenset({"pkill", "kill", "killall"})
_XARGS_VALUE_OPTS = frozenset({"-n", "-I", "-P", "-L", "-d", "-E", "-s", "-a"})
_PYTEST_VALUE_OPTS = frozenset({"-c", "-p", "-k", "-m", "-o", "-W", "--ignore",
                                "--deselect", "--rootdir", "--confcutdir", "--basetemp"})
_NPM_VALUE_OPTS = frozenset({"--prefix", "-C", "--workspace", "-w"})
# npm run <script>: script names (or `name:variant`) that run tests / lint / typecheck /
# build. Matched by prefix of the part before ':' (test, test:run, lint:contract, ...).
_NPM_GATED_SCRIPT_PREFIXES = ("build", "test", "lint", "typecheck", "type-check",
                              "check", "tsc", "eslint", "vitest")
# pnpm / yarn / bun: global options that take a separate value (skipped before the
# subcommand), and the subcommands that run a package binary like npx.
_JS_PM_VALUE_OPTS = {
    "pnpm": frozenset({"-C", "--dir", "--filter", "-F", "--filter-prod", "--reporter",
                       "--loglevel", "--workspace-concurrency", "--resume-from",
                       "--test-pattern", "--changed-files-ignore-pattern"}),
    "yarn": frozenset({"--cwd", "--modules-folder", "--cache-folder", "--global-folder",
                       "--link-folder", "--mutex", "--network-timeout", "--registry",
                       "--use-yarnrc", "--network-concurrency"}),
    "bun": frozenset({"--cwd", "--filter", "-F", "--config", "-c", "--preload", "-r",
                      "--env-file", "--tsconfig-override", "--elide-lines", "--shell"}),
}
_JS_PM_EXEC_SUBCOMMANDS = {
    "pnpm": frozenset({"exec", "dlx"}),
    "yarn": frozenset({"exec", "dlx"}),
    "bun": frozenset({"x"}),
}
_JS_EXEC_VALUE_OPTS = frozenset({"-p", "--package", "--filter", "-F", "-C", "--dir", "--cwd"})
# yarn workspaces foreach options that take a value.
_YARN_FOREACH_VALUE_OPTS = frozenset({"--include", "--exclude", "--from", "-j", "--jobs"})
# make targets that run tests / checks (backend/Makefile: test, test-all, test-file, ...).
_MAKE_GATED_TARGET_PREFIXES = ("test", "check", "lint", "typecheck")
_MAKE_VALUE_OPTS = frozenset({"-C", "-f", "-I", "-o", "-W", "-l", "--directory",
                              "--file", "--makefile", "--include-dir", "--old-file",
                              "--what-if", "--new-file", "--assume-new", "--assume-old"})
_MAKE_RULE_RE = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.\-/]*)\s*::?(?!=)")
_COVERAGE_VALUE_OPTS = frozenset({"--rcfile", "--source", "--omit", "--include",
                                  "--concurrency", "--context", "--data-file"})
_NODE_VALUE_OPTS = frozenset({"-r", "--require", "--import", "--loader",
                              "--experimental-loader", "--env-file", "-C", "--conditions"})
_NODE_TOOL_SCRIPTS = (("vitest", "vitest"), ("eslint", "eslint"), ("tsc", "tsc"))
_PYTEST_INFO_ONLY_OPTS = frozenset({"--version", "-V", "--help", "-h"})
# Programs whose arguments name what a manual hang-triage command is looking for
# (`ps aux | grep pytest`, `pkill -f pytest`); a pytest word elsewhere (echo text,
# a commit message) is not triage.
_TRIAGE_FILTER_PROGRAMS = frozenset({"grep", "egrep", "fgrep", "rg", "ag", "awk"})


def _gate_shell_segments(
    command: str, seps: "list[str] | None" = None,
) -> "list[list[str]] | None":
    """Split *command* into simple-command token lists, or None if unparseable.

    Heredoc bodies are dropped and unquoted newlines become separators
    (_normalize_newlines_quote_aware). Backticks become separators so a
    `` `cmd` `` substitution is scanned as its own segment. A POSIX shlex with
    punctuation_chars splits `;` `&&` `||` `|` `(` `)` off words the way bash does,
    so `$(pytest)` and `a;pytest` expose pytest as a command word. Redirect operators
    (`>`, `2>`, `>&`, `<<`) are dropped together with their target.

    When *seps* is a list it receives, per returned segment, the concatenated
    separator tokens that PRECEDE it ("" for the first; e.g. ");" or "&&(") — the
    cd/pushd chain tracking in _classify_pipeline_test_command reads them."""
    normalized = _normalize_newlines_quote_aware(command).replace("`", " ; ")
    try:
        lex = shlex.shlex(normalized, posix=True, punctuation_chars=_GATE_PUNCT_CHARS)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return None
    segments: list[list[str]] = []
    cur: list[str] = []
    pending_sep = ""
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok and set(tok) <= set(_GATE_PUNCT_CHARS):
            if set(tok) & _GATE_SEPARATOR_CHARS or tok in ("&", "&&"):
                if cur:
                    segments.append(cur)
                    if seps is not None:
                        seps.append(pending_sep)
                    pending_sep = ""
                cur = []
                pending_sep += tok
                i += 1
                continue
            i += 2  # redirect operator + its target
            continue
        cur.append(tok)
        i += 1
    if cur:
        segments.append(cur)
        if seps is not None:
            seps.append(pending_sep)
    return segments


def _skip_dash_opts(tokens: list[str], i: int, value_opts: "frozenset[str]") -> int:
    """Advance *i* past leading `-` options; an option in *value_opts* also skips its
    value token. `--` ends the options and is consumed."""
    while i < len(tokens) and tokens[i].startswith("-"):
        if tokens[i] == "--":
            return i + 1
        i += 2 if (tokens[i] in value_opts and i + 1 < len(tokens)) else 1
    return i


def _unwrap_program(tokens: list[str]) -> list[str]:
    """Strip wrappers so tokens[0] is the program that really runs.

    Handles env assignments and timeout/sudo-style wrappers (_skip_leading_wrappers),
    shell keywords (`do`, `then`, `{`, `!`, `exec`), `xargs [opts]`, the sanctioned
    perl-alarm cap (`perl -e '...@ARGV...' CMD`), `uv|poetry|pdm run`, and
    `npx` / `npm exec` / `bunx` / `pnpx` / `pnpm|yarn [opts] exec|dlx` /
    `bun [opts] x` (whose package token becomes the program), and a `function NAME`
    prefix. A package-manager shell mode (`pnpm [opts] exec|dlx -c STRING`, `pnpx -c`,
    `npx -c`, `npm exec -c|--call`; see _js_pm_shell_c_command) is returned unchanged
    from the package-manager token on; _js_pm_shell_c_command extracts its STRING."""
    for _ in range(10):  # bounded: each pass strips at least one token
        tokens = _skip_leading_wrappers(tokens)
        if not tokens:
            return tokens
        if tokens[0] in _SHELL_KEYWORDS:
            tokens = tokens[1:]
            continue
        if tokens[0] == "function":
            tokens = tokens[2:]  # `function NAME { body` — the body starts after NAME
            continue
        w = os.path.basename(tokens[0])
        if w == "xargs":
            tokens = tokens[_skip_dash_opts(tokens, 1, _XARGS_VALUE_OPTS):]
            continue
        if w == "perl":
            i, script = 1, None
            while i < len(tokens) and tokens[i].startswith("-"):
                t = tokens[i]
                if not t.startswith("--") and t.endswith(("e", "E")) and i + 1 < len(tokens):
                    script = tokens[i + 1]
                    i += 2
                    continue
                i += 1
            if script is not None and "@ARGV" in script and i < len(tokens):
                tokens = tokens[i:]
                continue
            return tokens
        if w in ("uv", "poetry", "pdm") and len(tokens) > 1 and tokens[1] == "run":
            tokens = tokens[_skip_dash_opts(tokens, 2, frozenset()):]
            continue
        if w in _JS_PM_SHELL_PROGRAMS and _js_pm_shell_c_command(tokens) is not None:
            return tokens  # shell mode: the caller classifies the string
        start = -1
        if w in ("npx", "bunx", "pnpx", "pnx"):
            start = 1
        elif w == "npm" and len(tokens) > 1 and tokens[1] in ("exec", "x"):
            start = 2
        elif w in _JS_PM_VALUE_OPTS:
            j = _skip_dash_opts(tokens, 1, _JS_PM_VALUE_OPTS[w])
            if j < len(tokens) and tokens[j] in _JS_PM_EXEC_SUBCOMMANDS[w]:
                start = j + 1
        if start >= 0:
            i = _skip_dash_opts(tokens, start, _JS_EXEC_VALUE_OPTS)
            if i >= len(tokens):
                return []
            pkg = tokens[i]
            if not pkg.startswith("@"):
                pkg = pkg.split("@", 1)[0]  # vitest@1.2 → vitest
            tokens = [pkg] + tokens[i + 1:]
            continue
        return tokens
    return tokens


def _pytest_file_args(args: list[str]) -> list[str]:
    """Explicit test FILE arguments of a pytest invocation (`::node` suffix dropped)."""
    out: list[str] = []
    skip = False
    for a in args:
        if skip:
            skip = False
            continue
        if a in _PYTEST_VALUE_OPTS:
            skip = True
            continue
        if a.startswith("-"):
            continue
        path = a.split("::", 1)[0]
        if path.endswith(".py"):
            out.append(path)
    return out


def _add_pytest_run(args: list[str], hits: set, files: list) -> None:
    """Record a pytest invocation with arguments *args*. `--version` / `--help` (and
    -V / -h) only print and exit, so they add the internal "_pytest_info" marker
    instead of a hit."""
    if any(a in _PYTEST_INFO_ONLY_OPTS for a in args):
        hits.add("_pytest_info")
        return
    hits.add("pytest")
    files.extend(_pytest_file_args(args))


def _mentions_test_runner(text: str) -> bool:
    """True iff *text* (inline / stdin code) names pytest, py.test or vitest."""
    return bool(_PYTEST_TOKEN_RE.search(text) or re.search(r"\bvitest\b", text))


# Modules inline / stdin code may import and still be unable to start a process or load
# other code: text, files, data. Anything else (subprocess, os, pty, runpy, importlib,
# pytest, a project module whose import runs code, ...) counts as able to run tests.
_INERT_IMPORTS = frozenset({"pathlib", "json", "re", "textwrap", "difflib", "string",
                            "collections", "itertools", "functools", "typing", "math",
                            "datetime", "time", "hashlib", "base64", "unicodedata",
                            "dataclasses", "enum", "copy", "pprint", "fnmatch", "glob",
                            "shlex", "tomllib", "csv", "io", "__future__"})
_IMPORT_RE = re.compile(r"\bimport\s+([\w.]+(?:\s*,\s*[\w.]+)*)|\bfrom\s+([\w.]+)\s+import\b")
_DYNAMIC_RE = re.compile(r"(?<![.\w])(?:exec|eval|compile|__import__|getattr|globals|"
                         r"breakpoint|open_code)\s*\(|__builtins__|__loader__|__spec__")


def _code_can_execute(text: str) -> bool:
    """True when python code in *text* could start a process or load other code: an
    import outside _INERT_IMPORTS, or a dynamic-execution primitive. Judges what the
    code can DO, not what it names, so an edit script that only writes text naming the
    test runner is not a test run. Fail-closed on anything it does not recognise."""
    if _DYNAMIC_RE.search(text):
        return True
    for many, one in _IMPORT_RE.findall(text):
        for name in (many.split(",") if many else [one]):
            if name.strip().split(".")[0] not in _INERT_IMPORTS:
                return True
    return False


def _classify_coverage(args: list[str], hits: set, files: list) -> None:
    """`coverage run [opts] (-m pytest | pytest-script) ...` → the pytest it wraps."""
    if not args or args[0] != "run":
        return
    i = 1
    while i < len(args) and args[i].startswith("-") and not args[i].startswith("-m"):
        if args[i] == "--":
            i += 1
            break
        i += 2 if (args[i] in _COVERAGE_VALUE_OPTS and i + 1 < len(args)) else 1
    rest = args[i:]
    if not rest:
        return
    if rest[0].startswith("-m"):
        _classify_python(rest, hits, files)
    elif os.path.basename(rest[0]) in ("pytest", "py.test"):
        _add_pytest_run(rest[1:], hits, files)


def _classify_python(args: list[str], hits: set, files: list) -> None:
    """Classify a python[3.x] invocation: `-m pytest`, `-m coverage run ...`, a pytest
    or coverage script, `-X faulthandler`, or inline code. `-c CODE` is a hit when
    CODE names pytest / vitest (`import pytest; pytest.main([...])`). Code read from
    stdin (`-` or no script at all) is not classified: in practice it is an edit script
    whose text happens to name pytest (docs/design/pipeline-gate-expert-simplification.md
    G1). Any other
    script, including backend/scripts/pipeline_test.py, is not a direct test run."""
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("-X"):
            opt = a[2:] if len(a) > 2 else (args[i + 1] if i + 1 < len(args) else "")
            if opt.startswith("faulthandler"):
                hits.add("python -X faulthandler")
            i += 1 if len(a) > 2 else 2
            continue
        if a.startswith("-m") and not a.startswith("--"):
            mod = a[2:] if len(a) > 2 else (args[i + 1] if i + 1 < len(args) else "")
            rest = args[i + (1 if len(a) > 2 else 2):]
            if mod in ("pytest", "py.test"):
                _add_pytest_run(rest, hits, files)
            elif mod == "coverage":
                _classify_coverage(rest, hits, files)
            return
        if a == "-W":
            i += 2
            continue
        if a == "-":
            return  # code from stdin: an edit script far more often than a test run
        if a.startswith("-") and not a.startswith("--") and a.endswith("c"):
            if i + 1 < len(args) and _mentions_test_runner(args[i + 1]) \
                    and _code_can_execute(args[i + 1]):
                hits.add("python -c pytest")
            return  # inline code
        if a in ("--version", "-V", "-VV", "--help", "-h"):
            return  # prints and exits: reads no code
        if a.startswith("-"):
            i += 1
            continue
        base = os.path.basename(a)
        if base in ("pytest", "py.test"):
            _add_pytest_run(args[i + 1:], hits, files)
        elif base == "coverage":
            _classify_coverage(args[i + 1:], hits, files)
        return
    # no script, module or -c: python reads code from stdin, not classified


def _classify_npm(args: list[str], hits: set) -> None:
    """`npm [opts] test|t|tst` and `npm [opts] run|run-script <script>` where the
    script name (part before ':') starts with build / test / lint / typecheck / check
    / tsc / eslint / vitest (_NPM_GATED_SCRIPT_PREFIXES). `npm install`, `npm run dev`
    and other scripts pass."""
    i = _skip_dash_opts(args, 0, _NPM_VALUE_OPTS)
    if i >= len(args):
        return
    sub = args[i]
    if sub in ("test", "t", "tst"):
        hits.add("npm test")
    elif sub in ("run", "run-script", "rum", "urn"):
        j = _skip_dash_opts(args, i + 1, _NPM_VALUE_OPTS)
        if j < len(args):
            script = args[j]
            if script.split(":", 1)[0].startswith(_NPM_GATED_SCRIPT_PREFIXES):
                hits.add("npm run " + script)


def _classify_js_pm(
    pm: str, args: list[str], cwd: str, depth: int, hits: set, files: list,
    chain_dir: "str | None" = None,
) -> None:
    """`pnpm` / `yarn` / `bun` (same rules as npm / npx). `<pm> test` (pnpm also
    `t` / `tst`) and `bun build` are hits; `<pm> run|run-script <script>` is a hit when
    the script matches _NPM_GATED_SCRIPT_PREFIXES. Any other subcommand is the
    implicit script-or-binary form (`pnpm lint`, `yarn vitest`, `pnpm vite build`):
    a gated name is a hit, otherwise `[name, *rest]` is classified as its own command
    (`pnpm install` / `yarn` / `pnpm dev` pass). exec / dlx / `bun x` are unwrapped
    earlier (_unwrap_program). `pnpm recursive|multi|m ...`, `yarn workspace NAME ...`
    and `yarn workspaces [foreach [opts]] ...` re-classify the rest."""
    i = _skip_dash_opts(args, 0, _JS_PM_VALUE_OPTS[pm])
    if i >= len(args):
        return  # bare `yarn` = install
    sub, rest = args[i], args[i + 1:]

    def _again(more: list[str]) -> None:
        _classify_test_segment([pm, *more], cwd, depth, hits, files, chain_dir)

    if pm == "pnpm" and sub in ("recursive", "multi", "m"):
        _again(rest)
    elif pm == "yarn" and sub == "workspace":
        _again(rest[1:])
    elif pm == "yarn" and sub == "workspaces":
        j = 1 if rest[:1] == ["foreach"] else 0
        _again(rest[_skip_dash_opts(rest, j, _YARN_FOREACH_VALUE_OPTS):])
    elif sub == "test" or (pm == "pnpm" and sub in ("t", "tst")):
        hits.add(pm + " test")
    elif pm == "bun" and sub == "build":
        hits.add("bun build")
    elif sub in ("run", "run-script"):
        j = _skip_dash_opts(rest, 0, _JS_PM_VALUE_OPTS[pm])
        if j < len(rest) and rest[j].split(":", 1)[0].startswith(_NPM_GATED_SCRIPT_PREFIXES):
            hits.add(pm + " run " + rest[j])
    elif sub.split(":", 1)[0].startswith(_NPM_GATED_SCRIPT_PREFIXES):
        hits.add(pm + " " + sub)
    elif sub not in _JS_PM_VALUE_OPTS:  # `pnpm yarn ...` nesting is not followed
        _classify_test_segment([sub, *rest], cwd, depth, hits, files, chain_dir)


def _makefile_default_goal(make_dir: str, makefile: "str | None") -> str:
    """First ordinary rule target of the Makefile make would read ("" if unknown).
    Targets starting with '.' (.PHONY, .DEFAULT_GOAL handling aside) are skipped."""
    names = [makefile] if makefile else ["GNUmakefile", "makefile", "Makefile"]
    for name in names:
        path = name if os.path.isabs(name) else os.path.join(make_dir, name)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for _n, line in zip(range(2000), fh):
                    m = _MAKE_RULE_RE.match(line)
                    if m and not m.group(1).startswith("."):
                        return m.group(1)
        except OSError:
            continue
        return ""
    return ""


def _resolve_dir(base: str, sub: str) -> str:
    """Directory *sub* (`~` expanded) resolved against *base*; "" = unknown (a
    relative *sub* with an unknown base, or one containing $ ` * ? [ { or an
    unexpanded ~user)."""
    if any(ch in sub for ch in "$`*?[{"):
        return ""
    p = os.path.expanduser(sub)
    if p.startswith("~"):
        return ""
    if os.path.isabs(p):
        return os.path.normpath(p)
    return os.path.normpath(os.path.join(base, p)) if base else ""


def _classify_make(args: list[str], cwd: str, hits: set) -> None:
    """`make [opts] [VAR=val] targets...`: a target starting with test / check / lint /
    typecheck is a hit. With no target, the Makefile's default goal decides (in
    backend/Makefile the first target is `test`). *cwd* is the directory make runs
    in ("" = unknown, e.g. after `cd $X`); with an unknown directory a bare make is
    not a hit, explicit targets still are."""
    make_dir, makefile = cwd, None
    targets: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in _MAKE_VALUE_OPTS and i + 1 < len(args):
            if a in ("-C", "--directory"):
                make_dir = _resolve_dir(make_dir, args[i + 1])
            elif a in ("-f", "--file", "--makefile"):
                makefile = args[i + 1]
            i += 2
            continue
        if a.startswith("-"):
            if a.startswith("--directory="):
                make_dir = _resolve_dir(make_dir, a.split("=", 1)[1])
            elif a.startswith("-C") and len(a) > 2:
                make_dir = _resolve_dir(make_dir, a[2:])
            elif a == "-j" and i + 1 < len(args) and args[i + 1].isdigit():
                i += 1
            i += 1
            continue
        if "=" not in a:
            targets.append(a)
        i += 1
    if makefile:
        makefile = os.path.expanduser(makefile)
    known = bool(make_dir) and os.path.isabs(make_dir)
    if not targets and (known or (makefile and os.path.isabs(makefile))):
        goal = _makefile_default_goal(make_dir if known else "", makefile)
        targets = [goal] if goal else []
    for t in targets:
        if t.startswith(_MAKE_GATED_TARGET_PREFIXES):
            hits.add("make " + t)


def _classify_node(args: list[str], hits: set, files: list) -> None:
    """`node [opts] <script>` where the script is a vitest / eslint / tsc entry point
    (node_modules/.bin/<tool>, node_modules/<pkg>/bin/<tool>...)."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] in ("-e", "--eval", "-p", "--print"):
            return  # inline code
        i += 2 if (args[i] in _NODE_VALUE_OPTS and i + 1 < len(args)) else 1
    if i >= len(args):
        return
    script = args[i].replace("\\", "/")
    base = os.path.basename(script).split(".", 1)[0]
    for tool, label in _NODE_TOOL_SCRIPTS:
        if base == tool or ("node_modules/" + tool + "/") in script \
                or (tool == "tsc" and "node_modules/typescript/" in script):
            hits.add(label)
            if tool == "vitest":
                files.extend(a for a in args[i + 1:] if _JS_TEST_FILE_RE.search(a))
            return


def _shell_c_command(args: list[str]) -> "str | None":
    """The command string of `<shell> [opts] -c STRING ...`, else None
    (see _shell_c_parts)."""
    parts = _shell_c_parts(args)
    return parts[0] if parts is not None else None


def _shell_c_parts(args: list[str]) -> "tuple[str, list[str]] | None":
    """``(STRING, positional)`` of `<shell> [opts] -c STRING [ARG0 ARG1 ...]`, else None.
    *positional* are the words after STRING (the shell's $0, $1, ...).

    Skips `-o NAME` / `+o NAME` / `-O NAME` / `+O NAME` (also inside a flag group
    such as `-eo pipefail`), `--rcfile FILE` / `--init-file FILE` and other `--long`
    options; the string is the first operand after a flag group containing `c`.
    fish's `--command STRING` / `--command=STRING` is the same form."""
    saw_c = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            i += 1
            break
        if a == "--command" and i + 1 < len(args):
            return args[i + 1], args[i + 2:]
        if a.startswith("--command="):
            return a.split("=", 1)[1], args[i + 1:]
        if a in ("--rcfile", "--init-file"):
            i += 2
            continue
        if a.startswith("--"):
            i += 1
            continue
        if len(a) > 1 and a[0] in "-+":
            flags = a[1:]
            if a[0] == "-" and "c" in flags:
                saw_c = True
            i += 1 + sum(1 for ch in flags if ch in "oO")  # each o/O takes a value
            continue
        break
    if saw_c and i < len(args):
        return args[i], args[i + 1:]
    return None


# Package-manager shell modes — the string runs like `sh -c STRING`:
#   • pnpm [opts] exec|dlx [opts] STRING... with `-c` / `--shell-mode`, which pnpm
#     accepts before or after the subcommand and inside a short flag group (`-rc`);
#     pnpx / pnx (= pnpm dlx) likewise. The operands are joined with spaces, as
#     pnpm does before handing them to the shell.
#   • npx [opts] -c|--call STRING and npm [opts] exec|x [opts] -c|--call STRING: the
#     string is the option's VALUE.
#   • yarn has no shell mode (`yarn dlx` takes only -p/--package and -q/--quiet), so
#     `yarn dlx -c STRING` fails in yarn itself; it is still classified like pnpm
#     (an inert alias that can only over-deny a command that cannot run).
# The value maps a program to the subcommands that take the shell mode (empty = the
# program itself is the dlx form).
_JS_PM_SHELL_MODE_SUBCOMMANDS = {
    "pnpm": frozenset({"exec", "dlx"}),
    "pnpx": frozenset(),
    "pnx": frozenset(),
    "yarn": frozenset({"dlx"}),
}
_JS_PM_SHELL_PROGRAMS = frozenset(_JS_PM_SHELL_MODE_SUBCOMMANDS) | {"npx", "npm"}
_PNPM_SHORT_VALUE_FLAGS = "CFp"  # in a short group, these take the rest as a value


def _npm_call_command(pm: str, tokens: list[str]) -> "str | None":
    """The `-c` / `--call` value of `npx [opts] -c STRING` or
    `npm [opts] exec|x [opts] -c STRING` (also `--call=STRING`), else None."""
    value_opts = _NPM_VALUE_OPTS | _JS_EXEC_VALUE_OPTS
    need_sub = pm == "npm"
    call = None
    i = 1
    while i < len(tokens):
        a = tokens[i]
        if a == "--":
            break
        if a.startswith("-") and len(a) > 1:
            if a in ("-c", "--call"):
                call = tokens[i + 1] if i + 1 < len(tokens) else call
                i += 2
            elif a.startswith("--call="):
                call = a.split("=", 1)[1]
                i += 1
            else:
                i += 2 if (a in value_opts and i + 1 < len(tokens)) else 1
            continue
        if not need_sub:
            break  # npx: the first operand is the package — options end here
        if a not in ("exec", "x"):
            return None
        need_sub = False
        i += 1
    return None if need_sub else call


def _js_pm_shell_c_command(tokens: list[str]) -> "str | None":
    """The shell string of a package-manager shell mode (forms in the comment above),
    else None. *tokens* start at the package-manager word. Options that take a value
    (`--filter X`, `-C DIR`, `-p PKG`, `--package PKG`) are skipped."""
    if not tokens:
        return None
    pm = os.path.basename(tokens[0])
    if pm in ("npx", "npm"):
        return _npm_call_command(pm, tokens)
    subs = _JS_PM_SHELL_MODE_SUBCOMMANDS.get(pm)
    if subs is None:
        return None
    value_opts = _JS_PM_VALUE_OPTS.get(pm, _JS_PM_VALUE_OPTS["pnpm"]) | _JS_EXEC_VALUE_OPTS
    need_sub = bool(subs)
    shell_mode = False
    i = 1
    while i < len(tokens):
        a = tokens[i]
        if a == "--":
            if need_sub:
                return None
            i += 1
            break
        if a.startswith("-") and len(a) > 1:
            if a in ("-c", "--shell-mode"):
                shell_mode = True
            elif pm != "yarn" and not a.startswith("--"):
                for ch in a[1:]:
                    if ch in _PNPM_SHORT_VALUE_FLAGS:
                        break
                    if ch == "c":
                        shell_mode = True
                        break
            i += 2 if (a in value_opts and i + 1 < len(tokens)) else 1
            continue
        if not need_sub:
            break
        if a not in subs:
            return None
        need_sub = False
        i += 1
    if need_sub or not shell_mode or i >= len(tokens):
        return None
    return tokens[i] if pm == "yarn" else " ".join(tokens[i:])


def _git_subcommand_args(tokens: list[str]) -> list[str]:
    """Tokens from the git SUBCOMMAND on, skipping git global options."""
    k = 1
    while k < len(tokens):
        t = tokens[k]
        if t in ("-C", "-c", "--namespace", "--git-dir", "--work-tree"):
            k += 2
        elif t.startswith("-"):
            k += 1
        else:
            break
    return tokens[k:]


def _classify_test_segment(
    tokens: list[str], cwd: str, depth: int, hits: set, files: list,
    chain_dir: "str | None" = None,
) -> str:
    """Add the test-gate families ONE simple command hits to *hits* (and explicit test
    files to *files*). Returns the unwrapped program basename ("" if none).

    *cwd* is the hook's cwd (copy-source checks); *chain_dir* is the directory this
    segment runs in after any leading cd/pushd of its chain ("" = unknown, None =
    *cwd*). make and a nested `bash -c` resolve against *chain_dir*."""
    if chain_dir is None:
        chain_dir = cwd
    t = _unwrap_program(tokens)
    if not t:
        return ""
    w = os.path.basename(t[0])
    args = t[1:]
    pm_shell_cmd = _js_pm_shell_c_command(t) if w in _JS_PM_SHELL_PROGRAMS else None
    if pm_shell_cmd is not None:
        if depth < 1:  # `pnpm dlx -c STRING`: classified like `bash -c STRING`
            sub_hits, sub_files = _classify_pipeline_test_command(
                pm_shell_cmd, cwd, depth + 1, start_dir=chain_dir)
            hits.update(sub_hits)
            files.extend(sub_files)
    elif w in ("pytest", "py.test"):
        _add_pytest_run(args, hits, files)
    elif _PY_INTERP_RE.match(w):
        _classify_python(args, hits, files)
    elif w == "coverage":
        _classify_coverage(args, hits, files)
    elif w in _SHELL_INTERPRETERS and depth < 1:
        sub_cmd = _shell_c_command(args)
        if sub_cmd is not None:
            sub_hits, sub_files = _classify_pipeline_test_command(
                sub_cmd, cwd, depth + 1, start_dir=chain_dir)
            hits.update(sub_hits)
            files.extend(sub_files)
    elif w in ("make", "gmake"):
        _classify_make(args, chain_dir, hits)
    elif w in ("node", "nodejs"):
        _classify_node(args, hits, files)
    elif w == "vitest":
        hits.add("vitest")
        files.extend(a for a in args if _JS_TEST_FILE_RE.search(a))
    elif w in ("tsc", "eslint"):
        hits.add(w)
    elif w == "vite":
        first = next((a for a in args if not a.startswith("-")), "")
        if first == "build":
            hits.add("vite build")
    elif w == "npm":
        _classify_npm(args, hits)
    elif w in _JS_PM_VALUE_OPTS:
        _classify_js_pm(w, args, cwd, depth, hits, files, chain_dir)
    elif w == "git":
        # `git archive` and cp / rsync copies are not hits: a copy runs no test, and any
        # test run on it is itself denied here. `git worktree add` registers a worktree.
        sub = _git_subcommand_args(t)
        if sub and sub[0] == "worktree":
            rest = [a for a in sub[1:] if not a.startswith("-")]
            if rest and rest[0] == "add":
                hits.add("git worktree add")
    return w


def _cd_segment_target(tokens: list[str], cur: str) -> "str | None":
    """None if *tokens* is not cd / pushd / popd; else the directory it switches to
    ("" = unknown: `cd -`, `popd`, `pushd +N`, `$VAR`, globs, a relative path from an
    unknown dir). `cd` alone goes to ~; `pushd -n` does not change the directory."""
    t = _unwrap_program(tokens)
    if not t or t[0] not in ("cd", "pushd", "popd"):
        return None
    if t[0] == "popd":
        return ""
    no_change = False
    i = 1
    while i < len(t) and t[i].startswith("-") and t[i] != "-":
        if t[i] == "--":
            i += 1
            break
        if t[0] == "pushd" and t[i] == "-n":
            no_change = True
        elif t[0] == "pushd" or not set(t[i][1:]) <= set("LPe@"):
            return ""  # pushd -N rotates the stack; unknown cd option
        i += 1
    if no_change:
        return cur
    if i >= len(t):
        return "" if t[0] == "pushd" else _resolve_dir(cur, "~")
    arg = t[i]
    if arg == "-" or arg.startswith("+"):
        return ""
    return _resolve_dir(cur, arg)


def _classify_pipeline_test_command(
    command: str, cwd: str = "", depth: int = 0, start_dir: "str | None" = None,
) -> "tuple[list[str], list[str]]":
    """Classify a Bash command for pipeline_test_gate → ``(hits, test_files)``.

    ``hits`` names every denied family the command contains (sorted, empty = allow).
    ``test_files`` lists explicit pytest / vitest test files, in order, deduplicated.

    pytest is detected two ways and either one is a hit: the existing
    pytest_command_guard shape (anchored _PYTEST_INVOCATION_RE plus the shlex
    command-word check, applied to the heredoc-stripped text) and the segment parse
    below. A pytest word that only appears inside quotes, a grep pattern or a commit
    message is never a command word, so it is not a hit. backend/scripts/
    pipeline_test.py is an ordinary python script here, so the runner never matches.
    Hang triage = a ps / pgrep / pkill / kill / killall segment in a command where a
    triage or filter program (grep / rg / awk / the triage program itself) names
    pytest (e.g. `ps aux | grep pytest`, `pkill -f pytest`); `echo "pytest finished"`
    after a kill is not triage. `pytest --version` / `--help` only print, so they are
    not a hit. Python reading code from stdin (`python - <<EOF`, `... | python`) is a
    hit when the command text names pytest or vitest (the heredoc body is in it).

    A cd / pushd / popd segment moves the directory later segments run in (make's
    Makefile lookup and a nested `bash -c`), starting from *start_dir* (None = *cwd*).
    The move applies only across `&&` / `;`; after `||`, `|` or `&` the directory is
    unknown (""), and `( ... )` restores the outer one. Unknown → a bare make is not a
    hit; explicit make targets and every other family are unaffected."""
    hits: set = set()
    files: list = []
    normalized = _normalize_newlines_quote_aware(command)
    regex_pytest = 0
    if _PYTEST_INVOCATION_RE.search(normalized) and _pytest_token_is_command_word(normalized):
        regex_pytest = len(_PYTEST_INVOCATION_RE.findall(normalized))
    seps: list[str] = []
    segments = _gate_shell_segments(command, seps)
    info_runs = 0
    if segments is not None:
        progs: list[str] = []
        cur = cwd if start_dir is None else start_dir
        stack: list[str] = []
        pending: "str | None" = None  # target of the previous segment if it was a cd
        for seg, sep in zip(segments, seps):
            k = 0
            while k < len(sep):
                op = sep[k:k + 2] if sep[k:k + 2] in ("&&", "||") else sep[k]
                k += len(op)
                if op == "(":
                    stack.append(cur)
                elif op == ")":
                    cur = stack.pop() if stack else ""
                elif pending is not None:
                    cur = pending if op in ("&&", ";") else ""
                pending = None
            progs.append(_classify_test_segment(seg, cwd, depth, hits, files, cur))
            pending = _cd_segment_target(seg, cur)
            if "_pytest_info" in hits:  # marker is per segment: count it, then drop it
                info_runs += 1
                hits.discard("_pytest_info")
        triage_mention = any(
            p in _TRIAGE_PROGRAMS or p in _TRIAGE_FILTER_PROGRAMS
            for p, seg in zip(progs, segments)
            if any(_PYTEST_TOKEN_RE.search(tok) for tok in seg[1:])
        )
        if triage_mention:
            for p in progs:
                if p in _TRIAGE_ACTING_PROGRAMS:
                    hits.add(p + " on pytest")
    # The anchored-regex detector is independent of the segment parse; it is only
    # discounted for `pytest --version|--help` it matched one-for-one.
    if regex_pytest and regex_pytest > info_runs:
        hits.add("pytest")
    seen: set = set()
    uniq = [f for f in files if not (f in seen or seen.add(f))]
    return sorted(hits), uniq


def _worktree_root_of(rp: str, src: "str | None") -> "str | None":
    """The root of the git worktree *rp* lies in, when that worktree belongs to the source
    repo *src* (its ``.git`` file points into ``<src>/.git/worktrees/``). Stat-only."""
    if not src:
        return None
    gitdir = os.path.join(os.path.realpath(src), ".git") + os.sep
    d = os.path.dirname(rp)
    while True:
        g = os.path.join(d, ".git")
        if os.path.isfile(g):
            try:
                with open(g, encoding="utf-8") as fh:
                    target = fh.read(4096).strip()
            except OSError:
                return None
            if target.startswith("gitdir:"):
                t = os.path.realpath(target[len("gitdir:"):].strip())
                return d if (t + os.sep).startswith(gitdir) else None
            return None
        if os.path.isdir(g):
            return None   # a separate repo (or the source repo itself)
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _repo_relative(path: str, cwd: str) -> str:
    """*path* relative to its repo root when it lives inside the source repo or a run's
    worktree of it (the runner is run from the repo root and resolves --files against
    the run's worktree); otherwise the path as resolved."""
    p = path if os.path.isabs(path) or not cwd else os.path.join(cwd, path)
    if not os.path.isabs(p):
        return path
    rp = os.path.realpath(p)
    src = _source_repo_root()
    roots = [src]
    wt = _worktree_root_of(rp, src)   # an agent working in a worktree of the source repo
    if wt:
        roots.append(wt)
    # This module's own checkout: when it runs from a worktree (dev, tests) its .git is a
    # file, so _source_repo_root() skips it; in the packaged daemon it is the bundle, which
    # no agent path lies in.
    if _SOURCE_REPO_ROOT and _SOURCE_REPO_ROOT not in roots:
        roots.append(_SOURCE_REPO_ROOT)
    for repo in roots:
        if repo and (rp == repo or rp.startswith(repo + os.sep)):
            return os.path.relpath(rp, repo)
    return rp


def _pipeline_runner_command(project: str, run_id: str, mode: str, files: list[str]) -> str:
    """The copyable pipeline_test.py command (contract: run from the repo root)."""
    parts = [
        "perl", "-e", "'alarm 110; exec @ARGV'", "backend/.venv/bin/python",
        "backend/scripts/pipeline_test.py", "--project", shlex.quote(project),
        "--run-id", shlex.quote(run_id), "--mode", mode,
    ]
    if files:
        parts += ["--files", *(shlex.quote(f) for f in files)]
    return " ".join(parts)


def create_pipeline_test_gate(session_context: dict[str, Any]):
    """Factory: PreToolUse (Bash) gate (design §4-B3, narrowed by
    docs/design/pipeline-gate-expert-simplification.md G1).

    - Always (run-independent): DENY backend/scripts/pipeline_test.py in a
      for/while/until loop or twice in one Bash call (_runner_loop_kind).
    - While THIS session has an active run (C6: own session_id, status running,
      activity within _RUN_ACTIVE_WINDOW_SECONDS): a direct test/lint/typecheck/build
      command (_classify_pipeline_test_command) is APPROVED with an additionalContext
      note naming the runner command. Landing and push_ready read only the runner's
      evidence, so a direct run cannot qualify a run; denying it only cost turns.

    Fail-open on any error. session_context is the mutable dict SessionUnit updates
    (read at call time)."""

    async def _gate(
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        if input_data.get("tool_name") != "Bash":
            return {"decision": "approve"}
        command = (input_data.get("tool_input", {}) or {}).get("command", "") or ""
        if not command:
            return {"decision": "approve"}
        cwd = input_data.get("cwd", "") or ""
        try:
            loop_kind = _runner_loop_kind(command)
        except Exception:
            logger.debug("pipeline_test_gate: loop check failed — failing open", exc_info=True)
            loop_kind = None
        if loop_kind:
            # Run-independent and IO-free: one runner call per Bash call, always.
            logger.warning("[BLOCKED] pipeline_test_gate (runner %s): %s", loop_kind, command[:120])
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "DENY (runner " + loop_kind + "): run backend/scripts/pipeline_test.py "
                        "ONCE per Bash call, never in a for/while/until loop or chained "
                        "twice. Each call is capped (perl alarm 110, under the Bash tool's "
                        "120 s default); a loop of calls in one Bash call is a multi-minute "
                        "call you cannot see into or stop. When the output says continue: "
                        "true, make a new Bash call with the SAME command: it runs only the "
                        "unfinished tests. Read each call's failures before the next one. "
                        "Do not raise the Bash timeout to fit a loop."
                    ),
                }
            }
        try:
            hits, files = _classify_pipeline_test_command(command, cwd)
        except Exception:
            logger.debug("pipeline_test_gate: classify failed — failing open", exc_info=True)
            return {"decision": "approve"}
        if not hits:
            return {"decision": "approve"}  # cheap path: no IO for ordinary commands

        session_id = session_context.get("sdk_session_id", "") or ""
        if not session_id:
            return {"decision": "approve"}
        try:
            own_run = await asyncio.wait_for(
                executors.run_in("gate", _session_run_state, session_id),
                timeout=_RUN_SCAN_TIMEOUT,
            )
        except Exception:  # incl. asyncio.TimeoutError
            logger.warning("pipeline_test_gate: run scan failed — failing open", exc_info=True)
            return {"decision": "approve"}
        if own_run is None:
            return {"decision": "approve"}  # not in a pipeline run → no effect

        suggest = [_repo_relative(f, cwd) for f in files[:5]]
        mode = "tdd" if suggest else "verify"
        runner = _pipeline_runner_command(own_run["project"], own_run["id"], mode, suggest)
        logger.info("pipeline_test_gate: direct %s in run %s (allowed, not evidence)",
                    ", ".join(hits), own_run["id"])
        return {
            "decision": "approve",
            "additionalContext": (
                "Note (" + ", ".join(hits) + "): pipeline run " + own_run["id"]
                + " is active in this session. This direct run is allowed, but landing "
                "and push_ready read only the pipeline test runner's results, so it does "
                "not count as evidence. To record evidence, run from the repo root:\n"
                + runner + "\n"
                "Modes: tdd = up to 5 explicit test files, verify = end of BUILD, "
                "regression = TEST stage, recheck = after a fix."
            ),
        }

    return _gate


def create_run_amend_gate(session_context: dict[str, Any]):
    """Factory: PreToolUse (Bash) gate. While THIS session has an active pipeline run
    (same ``_session_run_state`` test as pipeline_test_gate), DENY ``git commit
    --amend`` outright instead of letting dangerous_command_gate park the run on a
    human approval prompt (run_c5eb0858 waited 2 h on one). A run's commit message is
    built by its landing (``pipeline_v1.py land --message``); the deny says so. No run → no
    effect (an interactive amend still goes through dangerous_command_gate).
    Fail-open on any error (the command then reaches dangerous_command_gate)."""

    async def _gate(
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        if input_data.get("tool_name") != "Bash":
            return {"decision": "approve"}
        command = (input_data.get("tool_input", {}) or {}).get("command", "") or ""
        try:
            if not command or not _is_commit_amend(command):
                return {"decision": "approve"}  # cheap path: no IO for ordinary commands
        except Exception:
            logger.debug("run_amend_gate: classify failed — failing open", exc_info=True)
            return {"decision": "approve"}
        session_id = session_context.get("sdk_session_id", "") or ""
        if not session_id:
            return {"decision": "approve"}
        try:
            own_run = await asyncio.wait_for(
                executors.run_in("gate", _session_run_state, session_id),
                timeout=_RUN_SCAN_TIMEOUT,
            )
        except Exception:  # incl. asyncio.TimeoutError
            logger.warning("run_amend_gate: run scan failed — failing open", exc_info=True)
            return {"decision": "approve"}
        if own_run is None:
            return {"decision": "approve"}
        logger.warning("[BLOCKED] run_amend_gate in run %s: %s", own_run["id"], command[:120])
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "DENY (git commit --amend): pipeline run " + own_run["id"]
                    + " is active in this session, and a run never rewrites a commit by "
                    "hand. 要改提交信息就在 DELIVER 落地时改 message: the run's landing "
                    "(`pipeline_v1.py land --message ...`, or the two-step human "
                    "`run-land`) builds the commit and adds the trailer itself. If the commit has "
                    "already landed, leave it as is and note the wording in the run's "
                    "report; the user decides after the run whether to reword it. Do not "
                    "ask the user to approve an amend."
                ),
            }
        }

    return _gate


# ---------------------------------------------------------------------------
# session-link gate (PreToolUse, Bash) — keeps the run↔session link of §4-B3/§4-G1
# ---------------------------------------------------------------------------
# pipeline_test_gate and direct_commit_gate judge a session only by runs whose
# run.json session_id equals its app session id. `pipeline_v1.py create` reads
# that id from SWARM_APP_SESSION_ID (prompt_builder injects it into the CLI
# subprocess env) into the run's protected contract, and pipeline_test.py compares
# it with the run's owner before it completes a stage. A Bash command that removes or overrides the variable creates an
# unowned (or foreign) run, and then neither gate applies. session_link_gate DENIES
# such commands ALWAYS, not only during a run:
#   • removal: `unset VAR` / `unset VAR[0]`, `env -u VAR` / `-uVAR` / `--unset[=]VAR`
#     (also inside `env -S STRING`), `export -n VAR`, `declare|typeset|local VAR`
#     without -p/-f (a function-local shadow is empty for child processes), csh
#     `unsetenv VAR`, fish `set [-e] VAR`;
#   • a NAME built at run time as the operand of unset / unsetenv / export -n /
#     env -u / --unset (`unset $X`, `unset "$1"`, `unset ${!SWARM_APP_*}`, a glob
#     matching the name, `env -u "$(…)"`, a backtick, `xargs -I{} env -u {}`, an
#     `env -u` with no name at all), and a nameref (`declare|local|typeset -n R=VAR`,
#     ksh `nameref`) whose target is the variable or a run-time name;
#   • override: any assignment (`VAR=x cmd`, `VAR=x`, `VAR+=x`, `env VAR=x`,
#     `sudo VAR=x`, `export|readonly|declare VAR=x`, csh `setenv VAR`) plus
#     `read|mapfile|readarray VAR`, `printf -v VAR`, `for|select VAR in`,
#     `getopts OPTS VAR`;
#   • a wholesale env reset (`env -i` / `env -` / `--ignore-environment`, `exec -c`,
#     `sudo` without -E / --preserve-env, `su -|-l -c`) whose wrapped command is a
#     link reader (_is_link_reader_program: the PROGRAM is python* / uv /
#     pipeline_v1* / pipeline_test* or computed at run time, or a word is a path to
#     pipeline_v1.py / pipeline_test.py). The link is consumed by Python CLIs only,
#     so `sudo dnf install python3.11`, `env -i make run` or `env -i node a.js` pass;
#     a reset in front of a shell is judged by the commands of that shell string;
#   • a shell reading its script from stdin: the script — the echo / printf words
#     piped into it, or the command's heredoc / here-string bodies — is classified
#     like a `-c` string (`bash <<'EOF'` + `echo $VAR` passes); from any other pipe
#     producer (`cat f | sh`) the script is unknown, and naming the variable in the
#     command denies.
# Every segment is checked, and the strings of `<shell> -c` (bash, sh, zsh, dash,
# ksh, mksh, csh, tcsh, fish; with its positional arguments substituted when one
# names the variable: `bash -c 'unset "$1"' _ VAR`), `su [user] -c`, `eval` and the
# package-manager shell modes (`pnpm [-c] exec|dlx [-c]` incl. `-rc` / `--shell-mode`,
# `pnpx|pnx -c`, `npx -c`, `npm exec -c|--call`) are checked recursively up to
# _SESSION_LINK_MAX_DEPTH levels. `function NAME { … }` bodies are commands too.
# `env`, `sudo`, `su` and the shell forms are found at any word position, so an
# unknown wrapper in front (`caffeinate env -u VAR ...`) does not hide them. Reading
# the variable (`echo $VAR`, `env | grep SWARM`, `printenv VAR`, `declare -p VAR`,
# `grep VAR= file`) is allowed. The scan is linear and bounded
# (_SESSION_LINK_MAX_WORDS, _SESSION_LINK_MAX_CHARS, _SESSION_LINK_MAX_DEPTH); past a
# bound it fails CLOSED only when the unscanned text names the variable or holds a
# tamper form.
# RESIDUAL, no longer an unlink: interpreter code (`python -c "os.environ.pop(…)"`,
# `subprocess.run(…, env={})`), a script file, `source FILE`, an escaped name a
# program decodes (`echo -e '\x53…' | sh`) and a command shlex cannot split
# (bash_syntax_guard denies those) all pass this gate. A v1 run created that way has
# no owner and is refused (`create` requires a session id), and pipeline_test.py
# called without the owner's id completes no stage. Fail-open on
# internal errors. No operator bypass (gate_force.UNFORCEABLE).
_SESSION_LINK_VAR = "SWARM_APP_SESSION_ID"
_SESSION_LINK_MAX_DEPTH = 3
# Linear-time bounds (the classifier runs synchronously inside the hook, and the
# chain's 5 s wait_for cannot cancel sync code): at most _SESSION_LINK_MAX_WORDS
# words of one simple command are scanned for wrappers and builtins, and a command
# longer than _SESSION_LINK_MAX_CHARS is not parsed at all. Past either limit the
# gate fails CLOSED only when the unscanned text names the variable or holds a
# tamper form (_SESSION_LINK_TAMPER_WORDS / _SESSION_LINK_TAMPER_HINT_RE); anything
# else is approved.
_SESSION_LINK_MAX_WORDS = 64
_SESSION_LINK_MAX_CHARS = 32 * 1024
_SHELL_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[[^\]]*\])?\+?=")
# Text test for a link reader, used where no command structure is available (a shell
# reading a heredoc / pipe, a string past the depth limit).
_SESSION_LINK_READER_RE = re.compile(r"pipeline_v1|pipeline_test|\bpython[\d.]*\b|\buv\b")
_SESSION_LINK_READER_SCRIPTS = ("pipeline_v1.py", "pipeline_test.py")
# Cheap pre-filter: a command naming neither the variable nor one of these words is
# approved without parsing. Indirect forms (`unset $X`, `env -u {}`, `declare -n`,
# `bash -c 'unset "$1"' _ VAR`) all carry one of the words.
_SESSION_LINK_HINT_RE = re.compile(
    r"\b(?:env|sudo|exec|su|unset|unsetenv|setenv|export|declare|typeset|local|"
    r"readonly|nameref|eval)\b")
# Tamper forms looked for in text that is too long / too deep to parse.
_SESSION_LINK_TAMPER_HINT_RE = re.compile(
    r"\b(?:unset|unsetenv|setenv|nameref|sudo|su)\b|\benv\s+-|\bexec\s+-"
    r"|\b(?:declare|typeset|local|export)\s+[-+]")
# Builtins whose operands are variable NAMES (an indirect operand matters for them).
_SESSION_LINK_NAME_BUILTINS = frozenset({"unset", "unsetenv", "setenv", "export",
                                         "declare", "typeset", "local", "nameref"})
# Words that, past the scanned window, could still carry a tamper form.
_SESSION_LINK_TAMPER_WORDS = (
    frozenset({"env", "sudo", "su", "exec", "eval", "readonly", "set"})
    | _SESSION_LINK_NAME_BUILTINS | _SHELL_INTERPRETERS | _JS_PM_SHELL_PROGRAMS)
_NAME_SUBSCRIPT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\[")
_POSITIONAL_REF_RE = re.compile(r"\$\{(\d+|[@*])\}|\$(\d|[@*])")
_SU_VALUE_OPTS = frozenset({"-s", "--shell", "-g", "--group", "-G", "--supp-group",
                            "-w", "--whitelist-environment"})
_ENV_VALUE_SHORT_OPTS = "uCSPLU"  # GNU + BSD env short options that take a value
_ENV_VALUE_LONG_OPTS = {"unset": "u", "chdir": "C", "split-string": "S"}
_SUDO_VALUE_SHORT_OPTS = "CDghpRrTtUu"
_SUDO_VALUE_LONG_OPTS = frozenset({
    "close-from", "chdir", "group", "host", "prompt", "chroot", "role", "type",
    "command-timeout", "other-user", "user"})
_DECLARE_BUILTINS = frozenset({"declare", "typeset", "local"})


def _names_session_link(word: str) -> bool:
    """True iff *word* names the link variable (bare, `$VAR`, `${VAR}`, or with a
    subscript `VAR[0]`, which for a scalar is the variable itself)."""
    v = _SESSION_LINK_VAR
    if word in (v, "$" + v, "${" + v + "}"):
        return True
    m = _NAME_SUBSCRIPT_RE.match(word)
    return bool(m) and m.group(1) == v


def _maybe_names_session_link(word: str) -> bool:
    """True iff *word*, used as a variable-NAME operand, is the link variable or a
    name the shell builds at run time: a glob matching it (`SWARM_APP_SESSION_I?`),
    or a word holding an expansion (`$X`, `"$1"`, `${!SWARM_APP_*}`, `$(...)`, a
    backtick), a brace expansion or an xargs placeholder (`{}`). A literal name with
    a subscript (`arr[$i]`) names `arr`, so it passes."""
    if _names_session_link(word):
        return True
    if len(word) <= 256 and any(c in word for c in "*?[") \
            and fnmatch.fnmatchcase(_SESSION_LINK_VAR, word):
        return True
    if _NAME_SUBSCRIPT_RE.match(word):
        return False
    return any(c in word for c in "$`{}")


def _assigns_session_link(word: str) -> bool:
    """True iff *word* is a shell assignment to the link variable (`VAR=`, `VAR+=`)."""
    m = _SHELL_ASSIGN_RE.match(word)
    return bool(m) and m.group(1) == _SESSION_LINK_VAR


def _env_session_link(args: list[str]) -> "tuple[str | None, bool, list[str]]":
    """Parse the arguments of `env` → ``(tamper, resets, wrapped)``.

    *tamper* labels a removal / override of the link (None if none); *resets* is True
    for `-i` / `-` / `--ignore-environment`; *wrapped* is the command env runs.
    Short flag groups (`-iu VAR`, `-uVAR`), `--unset[=]VAR` and `-S STRING`
    (split into more arguments) are handled; NAME=VALUE operands follow the options."""
    a_list = list(args)
    resets = False
    i = 0
    while i < len(a_list):
        a = a_list[i]
        if a == "--":
            i += 1
            break
        if a == "-":
            resets = True
            i += 1
            continue
        if not a.startswith("-") or len(a) < 2:
            break
        opt, val = "", None
        if a.startswith("--"):
            name, eq, v = a[2:].partition("=")
            if name == "ignore-environment":
                resets = True
            elif name in _ENV_VALUE_LONG_OPTS:
                opt, val = _ENV_VALUE_LONG_OPTS[name], (v if eq else None)
        else:
            for k, ch in enumerate(a[1:], start=1):
                if ch == "i":
                    resets = True
                elif ch in _ENV_VALUE_SHORT_OPTS:
                    opt, val = ch, (a[k + 1:] or None)
                    break
        if opt:
            if val is None:
                if i + 1 >= len(a_list):
                    if opt == "u":  # `xargs env -u`, `` env -u `cmd` ``: name comes later
                        return "env -u without a literal name", resets, []
                    break
                i += 1
                val = a_list[i]
            if opt == "u" and _maybe_names_session_link(val):
                if _names_session_link(val):
                    return "env -u " + _SESSION_LINK_VAR, resets, []
                return "env -u of a name built at run time", resets, []
            if opt == "S":
                try:
                    a_list[i + 1:i + 1] = shlex.split(val)
                except ValueError:
                    logger.debug("session_link_gate: unsplittable env -S %r", val[:80])
        i += 1
    while i < len(a_list) and _SHELL_ASSIGN_RE.match(a_list[i]):
        if _assigns_session_link(a_list[i]):
            return "env " + _SESSION_LINK_VAR + "=…", resets, []
        i += 1
    return None, resets, a_list[i:]


def _sudo_session_link(args: list[str]) -> "tuple[str | None, bool, list[str]]":
    """Parse the arguments of `sudo` → ``(tamper, resets, wrapped)`` like
    _env_session_link. sudo resets the environment unless -E / --preserve-env (bare,
    or a list naming the link variable) is given; `sudo VAR=x cmd` overrides it."""
    preserve = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            i += 1
            break
        if not a.startswith("-") or len(a) < 2:
            break
        if a.startswith("--"):
            name, eq, v = a[2:].partition("=")
            if name == "preserve-env":
                preserve = preserve or not eq or _SESSION_LINK_VAR in v.split(",")
            elif name in _SUDO_VALUE_LONG_OPTS and not eq:
                i += 1
        else:
            for k, ch in enumerate(a[1:], start=1):
                if ch == "E":
                    preserve = True
                elif ch in _SUDO_VALUE_SHORT_OPTS:
                    if not a[k + 1:]:
                        i += 1
                    break
        i += 1
    while i < len(args) and _SHELL_ASSIGN_RE.match(args[i]):
        if _assigns_session_link(args[i]):
            return "sudo " + _SESSION_LINK_VAR + "=…", not preserve, []
        i += 1
    return None, not preserve, args[i:]


def _su_command(args: list[str]) -> "tuple[str | None, bool]":
    """`su [opts] [user] [args]` → ``(the -c / --command string or None, login)``.
    *login* is True for `-` / `-l` / `--login` (a login shell resets the environment)."""
    cmd: "str | None" = None
    login = False
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-", "-l", "--login"):
            login = True
        elif a in ("-c", "--command"):
            cmd = args[i + 1] if i + 1 < len(args) else cmd
            i += 2
            continue
        elif a.startswith("--command="):
            cmd = a.split("=", 1)[1]
        elif a in _SU_VALUE_OPTS:
            i += 2
            continue
        elif len(a) > 1 and a[0] == "-" and not a.startswith("--"):
            head, c, rest = a[1:].partition("c")
            login = login or "l" in head
            if c:
                if rest:
                    cmd = rest
                elif i + 1 < len(args):
                    cmd = args[i + 1]
                    i += 2
                    continue
        i += 1
    return cmd, login


def _nameref_session_link(word: str, operands: list[str]) -> "str | None":
    """Label if a nameref declaration (`declare|local|typeset -n R=T`, ksh
    `nameref R=T`) targets the link variable or a name built at run time."""
    for op in operands:
        _name, eq, target = op.partition("=")
        if eq and _maybe_names_session_link(target):
            return word + " -n …=" + (_SESSION_LINK_VAR if _names_session_link(target)
                                      else "<a name built at run time>")
    return None


def _builtin_session_link(word: str, args: list[str]) -> "str | None":
    """Label if the shell builtin *word* (in command position) with *args* removes
    or overrides the link variable, else None. See the section comment for the
    forms; `declare|typeset -p|-f|-F VAR` only print, so they pass. For the name
    operands of unset / unsetenv / setenv / export -n and nameref targets, a name
    built at run time (_maybe_names_session_link) is denied too."""
    named = any(_names_session_link(a) for a in args)
    operands = [a for a in args if not (len(a) > 1 and a[0] in "-+")]
    indirect = any(_maybe_names_session_link(a) for a in operands)
    if word in ("unset", "unsetenv"):
        if named:
            return word + " " + _SESSION_LINK_VAR
        return word + " of a name built at run time" if indirect else None
    if word == "setenv":  # csh: setenv NAME [VALUE]
        if operands and _maybe_names_session_link(operands[0]):
            return "setenv " + _SESSION_LINK_VAR
        return None
    if word == "set" and named:  # fish: set [-e|-gx|-U] NAME [VALUE]
        return "set " + _SESSION_LINK_VAR
    if word == "nameref":  # ksh
        return _nameref_session_link(word, operands)
    if word in ("export", "readonly") or word in _DECLARE_BUILTINS:
        if any(_assigns_session_link(a) for a in args):
            return word + " " + _SESSION_LINK_VAR + "=…"
        flags = "".join(a[1:] for a in args if len(a) > 1 and a[0] in "-+" and a != "--")
        if word == "export" and "n" in flags and (named or indirect):
            return "export -n " + (_SESSION_LINK_VAR if named
                                   else "of a name built at run time")
        if word in _DECLARE_BUILTINS and "n" in flags:
            hit = _nameref_session_link(word, operands)
            if hit is not None:
                return hit
        if named and word in _DECLARE_BUILTINS and not set(flags) & set("pfF"):
            return word + " " + _SESSION_LINK_VAR
        return None
    if word in ("read", "mapfile", "readarray") and named:
        return word + " " + _SESSION_LINK_VAR
    if word == "printf":
        for k, a in enumerate(args):
            if (a == "-v" and k + 1 < len(args) and _names_session_link(args[k + 1])) \
                    or a == "-v" + _SESSION_LINK_VAR:
                return "printf -v " + _SESSION_LINK_VAR
    if word in ("for", "select") and args and _names_session_link(args[0]):
        return word + " " + _SESSION_LINK_VAR
    if word == "getopts" and len(args) >= 2 and _names_session_link(args[1]):
        return "getopts … " + _SESSION_LINK_VAR
    return None


def _shell_reads_stdin(args: list[str]) -> bool:
    """True iff `bash|sh|zsh|dash ARGS` reads its script from stdin: no `-c`, and
    `-s` or no script operand (options parsed as in _shell_c_command)."""
    stdin_flag = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            i += 1
            break
        if a in ("--rcfile", "--init-file"):
            i += 2
            continue
        if a.startswith("--"):
            i += 1
            continue
        if len(a) > 1 and a[0] in "-+":
            flags = a[1:]
            if a[0] == "-" and "c" in flags:
                return False
            stdin_flag = stdin_flag or (a[0] == "-" and "s" in flags)
            i += 1 + sum(1 for ch in flags if ch in "oO")
            continue
        break
    return stdin_flag or i >= len(args)


@functools.lru_cache(maxsize=16)
def _reader_in_text(text: str) -> bool:
    """_SESSION_LINK_READER_RE over *text*, memoised: every stdin-shell segment of
    one command asks about the same text, and a per-segment scan would be quadratic."""
    return bool(_SESSION_LINK_READER_RE.search(text))


def _is_link_reader_program(tokens: list[str]) -> bool:
    """True iff the simple command *tokens* runs a link reader: its PROGRAM word
    (before and after unwrapping, _skip_leading_wrappers / _unwrap_program) is
    python* / uv / pipeline_v1* / pipeline_test* or is computed at run time (`$P`,
    `"$(which python3)"`), or one of its words is a path ending in pipeline_v1.py /
    pipeline_test.py (`py scripts/pipeline_v1.py`). A word merely containing
    "python" (`dnf install python3.11`, `chown … ~/.swarm-ai/python`) is not one."""
    for t in (_skip_leading_wrappers(tokens), _unwrap_program(tokens)):
        if not t:
            continue
        prog = t[0]
        base = os.path.basename(prog)
        if "$" in prog or "`" in prog or _PY_INTERP_RE.match(base) \
                or base in ("uv", "uvx") or base.startswith(("pipeline_v1", "pipeline_test")):
            return True
    return any(os.path.basename(a).endswith(_SESSION_LINK_READER_SCRIPTS) for a in tokens)


def _reset_wraps_reader(wrapped: list[str], text: str, depth: int) -> bool:
    """True iff the command an environment reset runs (*wrapped*) is a link reader
    (_is_link_reader_program). A wrapped `<shell> -c STRING` or package-manager shell
    mode is judged by the commands of its STRING (_text_has_reader); a wrapped shell
    reading stdin by the whole command *text*, which still holds the heredoc body."""
    if not wrapped:
        return False
    t = _unwrap_program(wrapped)
    if t:
        w = os.path.basename(t[0])
        sub = None
        if w in _SHELL_INTERPRETERS:
            sub = _shell_c_command(t[1:])
            if sub is None and _shell_reads_stdin(t[1:]):
                return _reader_in_text(text)
        elif w in _JS_PM_SHELL_PROGRAMS:
            sub = _js_pm_shell_c_command(t)
        if sub is not None:
            return _text_has_reader(sub, depth + 1)
    return _is_link_reader_program(wrapped)


def _text_has_reader(command: str, depth: int) -> bool:
    """True iff some simple command of *command* is a link reader (see
    _reset_wraps_reader). Too deep, too long or unsplittable → the text test
    _SESSION_LINK_READER_RE (fail closed)."""
    if depth > _SESSION_LINK_MAX_DEPTH or len(command) > _SESSION_LINK_MAX_CHARS:
        return bool(_SESSION_LINK_READER_RE.search(command))
    segments = _gate_shell_segments(command)
    if segments is None:
        return bool(_SESSION_LINK_READER_RE.search(command))
    return any(_reset_wraps_reader(seg[:_SESSION_LINK_MAX_WORDS], command, depth)
               for seg in segments)


_HERESTRING_RE = re.compile(
    r"(?<!<)<<<[ \t]*(?:'([^']*)'|\"((?:[^\"\\]|\\.)*)\"|([^\s;&|()<>]+))")


def _stdin_bodies(text: str) -> list[str]:
    """Here-string words (`<<< WORD`) and heredoc bodies (`<<[-]WORD` … WORD) of
    *text*. Line-based and not quote-aware, so it may over-collect, which only makes
    the stdin-shell check stricter. Linear in len(text)."""
    bodies = [next((g for g in m.groups() if g is not None), "")
              for m in _HERESTRING_RE.finditer(text)]
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        for m in _HEREDOC_OPENER_RE.finditer(line):
            if m.start() > 0 and line[m.start() - 1] == "<":
                continue  # the tail of a `<<<` here-string
            delim = m.group("q1") or m.group("q2") or m.group("bs") or m.group("bare")
            body: list[str] = []
            while i < len(lines):
                ln = lines[i].lstrip("\t") if m.group("dash") else lines[i]
                i += 1
                if ln == delim:
                    break
                body.append(ln)
            bodies.append("\n".join(body))
    return bodies


class _LinkScan:
    """Facts shared by the segments of ONE command string under classification:
    the original *text*, its quote-free *flat* form, the nesting *depth*, and the
    segments with their preceding separators (_gate_shell_segments)."""

    __slots__ = ("text", "flat", "depth", "segments", "seps", "_bodies_hit")

    def __init__(self, text: str, flat: str, depth: int,
                 segments: "list[list[str]]", seps: "list[str]") -> None:
        self.text = text
        self.flat = flat
        self.depth = depth
        self.segments = segments
        self.seps = seps
        self._bodies_hit: "list[str | None] | None" = None  # memo: [] = no bodies

    def _named(self, shell: str) -> "str | None":
        if _SESSION_LINK_VAR in self.flat:
            return shell + " reading a script that names " + _SESSION_LINK_VAR
        return None

    def stdin_hit(self, idx: int, shell: str) -> "str | None":
        """Label if the *shell* of segment *idx*, reading its script from stdin, runs
        a script that removes or overrides the link. The script is classified like a
        `-c` string: the echo / printf words piped into it (`echo "…" | bash`), else
        the command's heredoc / here-string bodies. Another pipe producer
        (`cat f | sh`), a printf format with `%`, or no body at all leaves the script
        unknown: then naming the variable anywhere in the command denies."""
        sep = self.seps[idx] if idx < len(self.seps) else ""
        if idx > 0 and sep.endswith("|") and not sep.endswith("||"):
            prod = _unwrap_program(self.segments[idx - 1][:_SESSION_LINK_MAX_WORDS])
            name = os.path.basename(prod[0]) if prod else ""
            args = prod[1:]
            if name == "echo":
                while args and args[0] in ("-n", "-e", "-E", "-ne", "-en"):
                    args = args[1:]
            if name == "echo" or (name == "printf" and args and "%" not in args[0]):
                body = " ".join(args).replace("\\n", "\n")
                return _command_session_link(body, self.depth + 1)
            return self._named(shell)
        if self._bodies_hit is None:
            bodies = _stdin_bodies(self.text)
            hit = None
            for body in bodies:
                hit = _command_session_link(body, self.depth + 1)
                if hit is not None:
                    break
            self._bodies_hit = [hit] if bodies else []
        if self._bodies_hit:
            return self._bodies_hit[0]
        return self._named(shell)


def _positional_session_link(sub: str, positional: list[str], depth: int) -> "str | None":
    """`<shell> -c STRING ARG0 ARG1 …` with the link variable among the ARGs: STRING is
    classified with its positional references ($0-$9, ${N}, $@, $*) replaced by those
    ARGs, so `bash -c 'unset "$1"' _ VAR` reads as `unset "VAR"`. An expansion that
    would exceed _SESSION_LINK_MAX_CHARS is denied without being built."""
    if not any(_SESSION_LINK_VAR in p for p in positional):
        return None
    rest = " ".join(positional[1:])
    longest = max(len(rest), max(len(p) for p in positional))
    if len(sub) + len(_POSITIONAL_REF_RE.findall(sub)) * longest > _SESSION_LINK_MAX_CHARS:
        return "a shell -c string whose arguments name " + _SESSION_LINK_VAR \
            + " (too long to expand)"

    def _rep(m: "re.Match[str]") -> str:
        key = m.group(1) or m.group(2)
        if key in ("@", "*"):
            return rest
        n = int(key)
        return positional[n] if n < len(positional) else ""

    return _command_session_link(_POSITIONAL_REF_RE.sub(_rep, sub), depth + 1)


def _beyond_window_tampers(cmd_word: str, beyond: list[str]) -> bool:
    """True iff the words of a simple command past the scanned window could still
    remove or override the link: one names the variable, is a tamper word
    (_SESSION_LINK_TAMPER_WORDS), or — for a name-taking builtin — is a name built
    at run time."""
    if any(_SESSION_LINK_VAR in t or os.path.basename(t) in _SESSION_LINK_TAMPER_WORDS
           for t in beyond):
        return True
    return cmd_word in _SESSION_LINK_NAME_BUILTINS \
        and any(_maybe_names_session_link(t) for t in beyond)


def _segment_session_link(tokens: list[str], scan: _LinkScan, idx: int) -> "str | None":
    """Label if ONE simple command (*tokens*, segment *idx* of *scan*) removes or
    overrides the link, else None.

    Command position (after keywords, `function NAME`, `command` / `builtin`,
    `exec [-c]` and leading assignments): assignments to the variable, the builtins
    of _builtin_session_link, `eval STRING` and `exec -c` in front of a link reader.
    Any word position: `env`, `sudo`, `su [-] -c STRING`, a shell `-c` string (with
    its positional arguments, _positional_session_link), package-manager shell modes,
    and a shell reading stdin (_LinkScan.stdin_hit). Nested strings recurse at
    depth + 1. Only the first _SESSION_LINK_MAX_WORDS words after the command position
    are scanned; the rest is checked by _beyond_window_tampers (fail closed)."""
    depth = scan.depth
    i = 0
    reset = ""
    while i < len(tokens):
        tok = tokens[i]
        if tok == "function":
            i += 2  # `function NAME { body` — like `NAME() {`
            continue
        if tok == "exec":
            i += 1
            while i < len(tokens) and tokens[i].startswith("-") and len(tokens[i]) > 1:
                if tokens[i] == "--":
                    i += 1
                    break
                flags = tokens[i][1:]
                reset = "exec -c" if "c" in flags else reset
                i += 2 if flags.endswith("a") else 1
            continue
        if tok in _SHELL_KEYWORDS or tok in ("command", "builtin"):
            i += 1
            continue
        if _SHELL_ASSIGN_RE.match(tok):
            if _assigns_session_link(tok):
                return _SESSION_LINK_VAR + "=… assignment"
            i += 1
            continue
        break
    if i >= len(tokens):
        return None
    end = min(len(tokens), i + _SESSION_LINK_MAX_WORDS)
    win = tokens[i:end]
    hit = _builtin_session_link(win[0], win[1:])
    if hit is None and win[0] == "eval":
        hit = _command_session_link(" ".join(tokens[i + 1:]), depth + 1)
    if hit is None and reset and _reset_wraps_reader(win, scan.text, depth):
        hit = reset
    if hit is not None:
        return hit
    prog_at = len(win) - len(_unwrap_program(win))
    for k, word in enumerate(win):
        w = os.path.basename(word)
        if w in ("env", "sudo"):
            parse = _env_session_link if w == "env" else _sudo_session_link
            tamper, resets, wrapped = parse(win[k + 1:])
            if tamper is not None:
                return tamper
            if resets and _reset_wraps_reader(wrapped, scan.text, depth):
                return "env -i" if w == "env" else "sudo (resets the environment)"
            continue
        if w == "su":
            cmd, login = _su_command(win[k + 1:])
            if cmd is not None:
                hit = _command_session_link(cmd, depth + 1)
                if hit is None and login and _text_has_reader(cmd, depth + 1):
                    hit = "su - (a login shell resets the environment)"
                if hit is not None:
                    return hit
            continue
        sub = None
        if w in _SHELL_INTERPRETERS:
            parts = _shell_c_parts(win[k + 1:])
            if parts is None:
                if k in (0, prog_at) and _shell_reads_stdin(win[k + 1:]):
                    hit = scan.stdin_hit(idx, w)
                    if hit is not None:
                        return hit
                continue
            sub = parts[0]
            hit = _positional_session_link(sub, parts[1], depth)
            if hit is not None:
                return hit
        elif w in _JS_PM_SHELL_PROGRAMS:
            sub = _js_pm_shell_c_command(win[k:])
        if sub is not None:
            hit = _command_session_link(sub, depth + 1)
            if hit is not None:
                return hit
    if end < len(tokens) and _beyond_window_tampers(win[0], tokens[end:]):
        return ("a simple command of over %d words whose unscanned part names %s or "
                "holds a tamper form" % (_SESSION_LINK_MAX_WORDS, _SESSION_LINK_VAR))
    return None


def _command_session_link(command: str, depth: int = 0) -> "str | None":
    """Classify a Bash command for session_link_gate → a label of the first form
    that removes or overrides SWARM_APP_SESSION_ID, or None (allow).

    Cheap path: a command that names neither the variable nor a word of
    _SESSION_LINK_HINT_RE (after dropping quotes and backslashes, so
    `"SWARM_APP_"SESSION_ID` still counts) is allowed without parsing. A string
    nested deeper than _SESSION_LINK_MAX_DEPTH, or whose shell code (heredoc bodies
    excluded) is longer than _SESSION_LINK_MAX_CHARS, is not parsed: it is denied
    when it names the variable
    or matches _SESSION_LINK_TAMPER_HINT_RE, else allowed. A backtick becomes `$` +
    separator, so `` env -u `cmd` `` / `` unset `cmd` `` leave an expansion operand
    (a name built at run time) while the substituted command is still scanned as its
    own segment. An unsplittable command is allowed (bash_syntax_guard denies it)."""
    flat = re.sub(r"[\"'\\]", "", command)
    named = _SESSION_LINK_VAR in flat
    if not named and not _SESSION_LINK_HINT_RE.search(flat):
        return None
    too_deep = depth > _SESSION_LINK_MAX_DEPTH
    # The length limit applies to the shell CODE: heredoc bodies (dropped by the
    # quote-aware, linear _normalize_newlines_quote_aware) do not count, so writing a
    # long document through `cat > f <<'EOF'` is still parsed normally.
    if too_deep or (len(command) > _SESSION_LINK_MAX_CHARS and len(
            _normalize_newlines_quote_aware(command)) > _SESSION_LINK_MAX_CHARS):
        if named or _SESSION_LINK_TAMPER_HINT_RE.search(flat):
            what = ("a shell string nested deeper than %d levels" % _SESSION_LINK_MAX_DEPTH
                    if too_deep else "a command over %d characters" % _SESSION_LINK_MAX_CHARS)
            return what + " (not parsed) that names " + _SESSION_LINK_VAR \
                + " or holds a tamper form"
        return None
    seps: list[str] = []
    segments = _gate_shell_segments(
        command.replace("`", "$`") if "`" in command else command, seps)
    if segments is None:
        return None
    scan = _LinkScan(command, flat, depth, segments, seps)
    for idx, seg in enumerate(segments):
        hit = _segment_session_link(seg, scan, idx)
        if hit is not None:
            return hit
    return None


def _session_link_advice(hit: str) -> str:
    """The how-to-proceed sentence of the deny text for the form *hit*."""
    if hit.startswith("sudo"):
        return ("To run a Python tool under sudo, keep the variable: `sudo -E …` or "
                "`sudo --preserve-env=" + _SESSION_LINK_VAR + " …` (or run it without "
                "sudo). ")
    if hit.startswith("su "):
        return "Use `su` without `-` / `-l` so the environment is kept. "
    if hit.startswith(("env -i", "exec -c")):
        return ("For a clean environment, drop other variables with `env -u NAME` "
                "instead of `env -i` / `exec -c`. ")
    if "built at run time" in hit or "without a literal name" in hit:
        return ("Name variables literally: an expansion, glob, `{}` or nameref could "
                "resolve to " + _SESSION_LINK_VAR + ". ")
    if "not parsed" in hit or "over %d words" % _SESSION_LINK_MAX_WORDS in hit \
            or "too long" in hit:
        return ("Split the command into shorter ones, or write long content with the "
                "file tool instead of the shell. ")
    return ""


def create_session_link_gate(session_context: dict[str, Any]):
    """Factory: PreToolUse (Bash) gate that DENIES, always (with or without an active
    run), any command removing or overriding SWARM_APP_SESSION_ID
    (_command_session_link; forms in the section comment above). Without the link a
    run is not this session's run, so pipeline_test_gate and direct_commit_gate stop
    applying. Reads pass. No operator bypass (gate_force.UNFORCEABLE). Fail-open on
    any internal error.
    session_context is the mutable dict SessionUnit updates (read at call time)."""

    async def _gate(
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        if input_data.get("tool_name") != "Bash":
            return {"decision": "approve"}
        command = (input_data.get("tool_input", {}) or {}).get("command", "") or ""
        if not command:
            return {"decision": "approve"}
        try:
            hit = _command_session_link(command)
        except Exception:
            logger.debug("session_link_gate: classify failed — failing open", exc_info=True)
            return {"decision": "approve"}
        if hit is None:
            return {"decision": "approve"}
        session_id = session_context.get("sdk_session_id", "") or ""
        logger.warning("[BLOCKED] session_link_gate (%s) in session %s: %s",
                       hit, session_id, command[:120])
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "DENY (" + hit + "): this command removes or overrides "
                    + _SESSION_LINK_VAR + ", or may (the checker could not rule it "
                    "out). The backend sets that variable for every command; it links "
                    "a pipeline run to this session (`pipeline_v1.py create` records "
                    "it as the run's session_id). A run without this session's id is "
                    "not this session's run, so the pipeline test gate and the "
                    "direct-commit gate stop applying to it. Leave the variable as the "
                    "backend set it; reading it is fine. " + _session_link_advice(hit)
                    + "This gate has no bypass: if it is wrong, tell the user."
                ),
            }
        }

    return _gate


# ---------------------------------------------------------------------------
# audit_marker_write_gate (PreToolUse: Bash and Write|Edit|MultiEdit|NotebookEdit)
# ---------------------------------------------------------------------------
# The backend's SubagentStop hook (runtime_hooks.create_agent_tool_audit_hook) is the
# only legitimate writer of _AGENT_AUDIT_DIR. Its markers are the evidence behind the
# D7 expert check, the Red Team marker check, the Gate-0/1 spawn binding,
# adversarial_commit_gate and pipeline_validator Check 9b. A marker the agent writes
# by hand forges all of them, and deleting the directory sends the readers down their
# "unreadable" fail-open path. This gate DENIES:
#   • Write/Edit/MultiEdit/NotebookEdit whose path (~ expanded, realpath) is the
#     directory or anything under it;
#   • Bash that writes into it: a redirect whose target names it; a verb in
#     _AUDIT_WRITE_VERBS with an argument that names it (copy verbs: the
#     destination); `sed -i`, `find -delete/-exec`, `tar -x` on it; interpreter code
#     (python -c, a heredoc fed to node, bash -c, eval, …) that names it at all;
#     a removal of the directory or of an ancestor (`rm -rf ~/.swarm-ai/state`);
#     a `cd`/`pushd` into it (the Bash tool keeps its cwd between calls, so a later
#     relative `> m.marker` would be invisible); via `xargs` or a `read` loop, a
#     write verb in a command that names it anywhere; any of these inside $(…),
#     backticks or <(…). Relative words resolve against the cwd the hook input
#     reports, then against each `cd` in the command.
# "Names it" (for a PATH word): a whole path component equals the dir name, the word
# expands a variable assigned from such a word (`D=…/pipeline_agent_audit; echo x >
# $D/m`) or a `for` list over it, or an absolute/cwd-resolved path matches it
# component-wise, globs included (`~/.swarm-ai/state/*`). So ~, $HOME and absolute
# spellings all match, but `pipeline_agent_audit_reader.py` and a same-named dir under
# a DIFFERENT root (`/tmp/x/pipeline_agent_audit`) do NOT. Inline interpreter code is
# matched by substring, not component, and is denied even when it only reads, because
# telling read from write inside a program is a regex arms race.
# Reads by absolute path pass (cat, ls, jq, grep, cp OUT of it, git commit -m text).
# No operator bypass (gate_force.UNFORCEABLE).
# Fail-open on any internal error. A command the tokenizer cannot parse fails CLOSED
# when its text names the dir (a valid command can carry an apostrophe shlex rejects;
# a genuinely malformed one is separately denied by bash_syntax_guard). RESIDUAL, read
# as command text: a path assembled at run time ("pipeline_agent" + "_audit"), a
# standalone script FILE that writes there, brace expansion (`pipeline_agent_{audit,x}`),
# a sed `w`-command, or a relative path whose cwd the command never shows. It closes
# the natural forgery forms; it is not a sandbox.
_AUDIT_WRITE_VERBS = frozenset({
    "rm", "rmdir", "unlink", "mv", "ln", "link", "touch", "tee", "truncate", "shred",
    "srm", "chmod", "chown", "chgrp", "chflags", "mkdir", "mkfifo", "patch", "dd",
    "sort", "mktemp", "sponge",
})
_AUDIT_COPY_VERBS = frozenset({"cp", "rsync", "install", "scp", "gcp", "ditto"})
_AUDIT_EXTRACT_VERBS = frozenset({"tar", "bsdtar", "gtar", "unzip", "unar"})
# Verbs whose argument may remove the directory by naming an ANCESTOR of it.
_AUDIT_REMOVE_VERBS = frozenset({"rm", "rmdir", "unlink", "mv", "shred", "srm"})
_AUDIT_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish"})
_AUDIT_INTERPRETERS = frozenset({
    "python", "node", "nodejs", "ruby", "perl", "php", "deno", "bun", "osascript",
    "awk", "gawk", "mawk", "nawk", "lua", "Rscript", "swift", "tclsh", "jshell",
})
# Options that carry an interpreter's inline program as their next word.
_AUDIT_CODE_FLAGS_DEFAULT = frozenset({"-c", "-e", "--eval", "--command"})
_AUDIT_CODE_FLAGS: dict[str, frozenset[str]] = {
    "python": frozenset({"-c"}),
    "perl": frozenset({"-e", "-E"}),
    "node": frozenset({"-e", "-p", "--eval", "--print"}),
    "nodejs": frozenset({"-e", "-p", "--eval", "--print"}),
    "bun": frozenset({"-e", "-p", "--eval", "--print"}),
    "php": frozenset({"-r"}),
}
# Interpreter -> options that take a VALUE word (so the value is not misread as the
# script file). Attached forms (perl -Mstrict, python -Xutf8) are handled too.
_AUDIT_INTERP_VALUE_FLAGS: dict[str, frozenset[str]] = {
    "python": frozenset({"-X", "-W"}),
    "perl": frozenset({"-I", "-M", "-m", "-C", "-F"}),
    "ruby": frozenset({"-I", "-r", "-C", "-E", "-F"}),
    "node": frozenset({"-r", "--require", "--import", "--loader", "--experimental-loader"}),
    "nodejs": frozenset({"-r", "--require", "--import", "--loader", "--experimental-loader"}),
    "bun": frozenset({"-r", "--require", "--preload"}),
}
# Wrapper -> its options whose next word is their value, not the wrapped command.
_AUDIT_WRAPPERS: dict[str, frozenset[str]] = {
    "sudo": frozenset({"-u", "-g", "-C", "-h", "-p", "-U", "-r", "-t", "-D"}),
    "doas": frozenset({"-u", "-C"}),
    "env": frozenset({"-u", "--unset", "-C", "--chdir", "-P"}),
    "command": frozenset(), "builtin": frozenset(), "exec": frozenset({"-a"}),
    "nohup": frozenset(), "time": frozenset({"-f", "-o"}), "chronic": frozenset(),
    "nice": frozenset({"-n"}), "ionice": frozenset({"-c", "-n", "-p"}),
    "caffeinate": frozenset({"-t", "-w"}), "arch": frozenset(),
    "timeout": frozenset({"-s", "--signal", "-k", "--kill-after"}),
    "gtimeout": frozenset({"-s", "--signal", "-k", "--kill-after"}),
    "stdbuf": frozenset({"-i", "-o", "-e"}),
    "xargs": frozenset({"-I", "-n", "-P", "-L", "-d", "-s", "-E", "-a", "-J", "-R"}),
    # package/venv runners: `uv run python -c …`, `poetry run …`, `npx node -e …`
    "uv": frozenset(), "poetry": frozenset(), "pipenv": frozenset(),
    "pdm": frozenset(), "rye": frozenset(), "hatch": frozenset(),
    "npx": frozenset({"-p", "--package", "-c", "--call"}), "bunx": frozenset(),
}
# Wrappers whose first positional word is a value, not the command: a duration
# (timeout), or a `run`/`exec` subcommand (the venv runners).
_AUDIT_WRAPPER_POSITIONAL = frozenset({
    "timeout", "gtimeout", "uv", "poetry", "pipenv", "pdm", "rye", "hatch",
})
_AUDIT_KEYWORDS = frozenset({
    "do", "done", "then", "else", "elif", "fi", "if", "while", "until", "!", "{", "}",
    "esac", "in", "local", "declare", "typeset", "readonly", "export",
})
_AUDIT_FIND_ACTIONS = frozenset({
    "-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf",
    "-fls",
})
_AUDIT_REDIRECT_OPS = frozenset({">", ">>", ">|", "&>", "&>>", "<>", ">&", "&>|"})
_AUDIT_DISCARD = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr", "-"})
_AUDIT_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\+?=")
_AUDIT_GREP_VERBS = frozenset({"grep", "egrep", "fgrep", "zgrep", "rg", "ag", "ack"})
# Above this many characters a command is judged by name only (bounded work). The
# event loop must never block on classification; a real command is far shorter.
_AUDIT_MAX_COMMAND_CHARS = 100_000
_AUDIT_OP_RE = re.compile(
    r"&>>?\|?|<<<|<<|<>|>>|>\||>&|<&|>|<|\|&|\|\||&&|;;|[;|&\n]"
)
_AUDIT_MAX_DEPTH = 3
# Bash call sites ignore case on macOS/Windows file systems, so names are compared
# lower-cased everywhere (a differently-cased twin directory on Linux over-matches).


def _audit_dir_parts() -> list[tuple[str, ...]]:
    """Lower-cased path components of the audit directory, as configured and resolved."""
    out: list[tuple[str, ...]] = []
    for raw in (str(_AGENT_AUDIT_DIR), os.path.realpath(str(_AGENT_AUDIT_DIR))):
        parts = tuple(c.lower() for c in Path(os.path.normpath(raw)).parts)
        if parts not in out:
            out.append(parts)
    return out


def _audit_expand_home(word: str) -> str:
    """*word* with a leading ~, $HOME or ${HOME} replaced by the home directory."""
    home = os.path.expanduser("~")
    for pre in ("${HOME}", "$HOME"):
        if word == pre or word.startswith(pre + "/"):
            return home + word[len(pre):]
    return os.path.expanduser(word) if word.startswith("~") else word


_AUDIT_VARREF_RE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)")


def _audit_refs_tainted(word: str, tainted: set[str]) -> bool:
    """True when *word* expands one of the *tainted* shell variables. One pass over
    the word (no per-name regex compile — that was quadratic in names × words and
    could stall the event loop, correctness-review finding 3)."""
    if not tainted or "$" not in word:
        return False
    return any(m.group(1) in tainted for m in _AUDIT_VARREF_RE.finditer(word))


def _audit_path_hit(word: str, tainted: set[str], cwd: "str | None" = None) -> "str | None":
    """'in' when *word* names the audit directory or something under it, 'ancestor'
    when it is a path (or glob) of a directory containing it, else None. A relative
    word is resolved against *cwd* when that is known. Path words match by whole
    component, not substring: `pipeline_agent_audit_reader.py` and
    `/tmp/x/pipeline_agent_audit` (a different dir) do NOT hit (review finding 9)."""
    if not word:
        return None
    if _audit_refs_tainted(word, tainted):
        return "in"
    name = _AGENT_AUDIT_DIR.name.lower()
    path = _audit_expand_home(word)
    if os.path.isabs(path):
        # Absolute: precise component-prefix match only (so a same-named dir under a
        # different root does NOT hit).
        return _audit_prefix_hit(path)
    if cwd and not path.startswith("$"):
        hit = _audit_prefix_hit(os.path.join(cwd, path))
        if hit:
            return hit
    # Fallback for a relative or embedded word (dd `of=~/…`, a quoted `rm -rf ~/…`
    # string, an unknown cwd): a whole component is literally the dir name.
    comps = [c.lower() for c in re.split(r"[/\\]", path) if c not in ("", ".", "..")]
    return "in" if name in comps else None


def _audit_prefix_hit(path: str) -> "str | None":
    """'in'/'ancestor'/None by matching the audit dir against *path* component-wise
    (globs honoured). *path* is treated as absolute (normalized)."""
    pat = tuple(c.lower() for c in Path(os.path.normpath(path)).parts)
    for d in _audit_dir_parts():
        n = min(len(pat), len(d))
        if all(fnmatch.fnmatchcase(d[i], pat[i]) for i in range(n)):
            return "in" if len(pat) >= len(d) else "ancestor"
    return None


def _audit_text_names_dir(text: str, tainted: set[str]) -> bool:
    """True when *text* mentions the audit directory at all: its name as a substring,
    a tainted variable, or a path-like word resolving into it. Used for the
    fail-CLOSED unparseable backstop, where any mention should block."""
    if not text:
        return False
    if _AGENT_AUDIT_DIR.name.lower() in text.lower() or _audit_refs_tainted(text, tainted):
        return True
    return any(
        _audit_path_hit(w, tainted) == "in"
        for w in re.findall(r"[~/$][^\s'\"`;,()\[\]{}]+", text)
    )


def _audit_code_names_dir(text: str, tainted: set[str]) -> bool:
    """True when interpreter code *text* writes using a PATH that resolves into the
    audit directory (or a tainted variable). Unlike _audit_text_names_dir this does
    NOT match the bare name as a substring: `s/pipeline_agent_audit/x/` in a perl/sed
    refactor, or a string that merely mentions the name, is not a write (review
    finding 6). A path assembled from pieces is the documented residual."""
    if not text:
        return False
    if _audit_refs_tainted(text, tainted):
        return True
    return any(
        _audit_path_hit(w, tainted) == "in"
        for w in re.findall(r"[~/$][^\s'\"`;,()\[\]{}]+", text)
    )


def _audit_strip_comments(command: str) -> str:
    """Remove ``#`` comments quote-aware, length-preserving per line. Without this an
    apostrophe in a comment (`# don't …`) looks like an unbalanced quote to the
    tokenizer and the whole command would fail open (correctness-review HIGH). A
    ``#`` only starts a comment at the start of a word (preceded by whitespace or at
    line start) and outside quotes."""
    out = []
    for line in command.split("\n"):
        quote = None
        esc = False
        res = []
        for ch in line:
            if esc:
                res.append(ch)
                esc = False
            elif quote:
                res.append(ch)
                if quote == '"' and ch == "\\":
                    esc = True
                elif ch == quote:
                    quote = None
            elif ch in ("'", '"'):
                quote = ch
                res.append(ch)
            elif ch == "\\":
                res.append(ch)
                esc = True
            elif ch == "#" and (not res or res[-1] in " \t"):
                break  # comment runs to end of line
            else:
                res.append(ch)
        out.append("".join(res))
    return "\n".join(out)


def _audit_segments(command: str) -> "list[list[str]] | None":
    """Simple commands of *command* as word lists (redirect operators kept as their
    own words). Splits on ; & | && || and newlines; parentheses are dropped, so a
    `$(…)` stays inside the command that uses it. None on unbalanced quotes."""
    lex = shlex.shlex(command.replace("\\\n", " "), posix=True,
                      punctuation_chars="();<>|&\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        tokens = list(lex)
    except ValueError:
        return None
    segments: list[list[str]] = []
    seg: list[str] = []
    for tok in tokens:
        if tok and all(ch in "();<>|&\n" for ch in tok):
            for op in _AUDIT_OP_RE.findall(tok.replace("(", "").replace(")", "")):
                if "<" in op or ">" in op:
                    seg.append(op)
                elif seg:
                    segments.append(seg)
                    seg = []
            continue
        seg.append(tok)
    if seg:
        segments.append(seg)
    return segments


def _audit_cmd_names_path(segments: list[list[str]], tainted: set[str],
                          cwd: "str | None") -> bool:
    """True when some word in *segments* is a real PATH into the audit dir (or an
    ancestor). A grep/rg PATTERN operand is skipped, so `grep -rl pipeline_agent_audit
    backend | xargs sed -i …` (a refactor) is not read as touching the dir — only an
    actual path like `ls …/pipeline_agent_audit` is (red-team finding 6)."""
    for seg in segments:
        words = [w for w in seg if w not in _AUDIT_REDIRECT_OPS
                 and w not in ("<", "<&", "<<", "<<<")]
        verb_idx = next((i for i, w in enumerate(words)
                         if not _AUDIT_ASSIGN_RE.match(w) and not w.startswith("-")), None)
        skip = -1
        if verb_idx is not None and os.path.basename(words[verb_idx]) in _AUDIT_GREP_VERBS:
            skip = next((j for j in range(verb_idx + 1, len(words))
                         if not words[j].startswith("-")), -1)
        for j, w in enumerate(words):
            if j == skip:
                continue
            if _audit_path_hit(w, tainted, cwd) in ("in", "ancestor"):
                return True
    return False


def _audit_strip_wrappers(rest: list[str]) -> "tuple[int, bool]":
    """(index of the real verb in *rest*, whether xargs feeds it from stdin)."""
    k, from_stdin = 0, False
    while k < len(rest):
        name = os.path.basename(rest[k])
        if name not in _AUDIT_WRAPPERS:
            break
        from_stdin = from_stdin or name == "xargs"
        argflags = _AUDIT_WRAPPERS[name]
        positional_done = name not in _AUDIT_WRAPPER_POSITIONAL
        k += 1
        while k < len(rest):
            w = rest[k]
            if w == "--":
                k += 1
                break
            if w.startswith("-") and len(w) > 1:
                k += 2 if w in argflags else 1
            elif name in ("env", "sudo") and _AUDIT_ASSIGN_RE.match(w):
                k += 1
            elif not positional_done:
                positional_done = True
                k += 1
            else:
                break
    return k, from_stdin


def _authority_key_path(path: str, cwd: "str | None" = None, *, recursive: bool = False) -> bool:
    """Whether a path expression can resolve to a Pipeline v1 authority key."""
    if not path:
        return False
    raw = path.split("=", 1)[-1] if "=" in path else path
    expanded = _audit_expand_home(raw)
    if cwd and not os.path.isabs(expanded):
        expanded = os.path.join(cwd, expanded)
    normalized = os.path.normpath(expanded)
    candidate = (os.path.realpath(normalized) if not glob.has_magic(normalized)
                 else normalized)
    parts = tuple(part.lower() for part in Path(candidate).parts)
    for root in _audit_dir_parts():
        n = min(len(parts), len(root))
        if not all(fnmatch.fnmatchcase(root[i], parts[i]) for i in range(n)):
            continue
        if len(parts) < len(root):
            if recursive:
                return True
            continue
        rest = parts[len(root):]
        # Keys have one canonical shape below the protected root. A non-recursive
        # shell glob at the root (audit/*) cannot descend through runs/<run>/.
        key_pattern = (len(rest) == 3
                       and fnmatch.fnmatchcase("runs", rest[0])
                       and bool(rest[1]) and rest[1] not in {".", ".."}
                       and fnmatch.fnmatchcase("authority.key", rest[2]))
        if key_pattern:
            return True
        if recursive and len(rest) <= 2:
            # audit root, runs/, or one run directory can recursively contain a key.
            return True
        base = rest[-1] if rest else ""
        if ("$" in base or "`" in base) and ("authority" in base or ".key" in base):
            return True
    return False


def _authority_script_read(seg: list[str], cwd: "str | None") -> bool:
    """Inspect a directly invoked interpreter script for a hard-coded key read."""
    words = [word for word in seg if word not in _AUDIT_REDIRECT_OPS]
    state = {"tainted": set(), "names_dir": False, "bodies": "", "depth": 0}
    _audit_set_cwd(state, cwd)
    rest = _audit_leading(words, state)
    k, _ = _audit_strip_wrappers(rest)
    if k >= len(rest):
        return False
    verb = re.sub(r"^(python|ruby|perl|node|php|lua)[\d.]*$", r"\1",
                  os.path.basename(rest[k]))
    if verb not in _AUDIT_INTERPRETERS:
        return False
    _codes, from_stdin, _inplace, operands = _audit_inline_code(verb, rest[k + 1:])
    if from_stdin or not operands:
        return False
    script = _audit_expand_home(operands[0])
    if not os.path.isabs(script):
        script = os.path.join(cwd, script) if cwd else script
    try:
        if not os.path.isfile(script) or os.path.getsize(script) > _AUDIT_MAX_COMMAND_CHARS:
            return False
        source = Path(script).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    keyish = ("authority.key" in source or
              ("authority" in source and ("*.key" in source or ".key" in source)))
    return keyish and (_AGENT_AUDIT_DIR.name.lower() in source.lower() or
                       bool(cwd and _audit_path_hit(cwd, set()) == "in"))


def _authority_bash_read(segments: list[list[str]], cwd: "str | None") -> bool:
    values: dict[str, str] = {}
    current_cwd = cwd

    def expand(word: str) -> str:
        pattern = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
        return pattern.sub(
            lambda m: values.get(m.group(1) or m.group(2), m.group(0)), word)

    for seg in segments:
        expanded_seg = [expand(word) for word in seg]
        for word in expanded_seg:
            match = _AUDIT_ASSIGN_RE.match(word)
            if match:
                values[match.group(1)] = expand(word[match.end():])
        state = {"tainted": set(), "names_dir": False, "bodies": "", "depth": 0}
        _audit_set_cwd(state, current_cwd)
        rest = _audit_leading(expanded_seg, state)
        k, _ = _audit_strip_wrappers(rest)
        if k >= len(rest):
            continue
        verb = os.path.basename(rest[k])
        args = rest[k + 1:]
        normalized_verb = re.sub(r"^(python|ruby|perl|node|php|lua)[\d.]*$", r"\1", verb)
        recursive = (
            verb in {"rg", "ripgrep", "ag", "ack", "find"}
            or (verb in _AUDIT_GREP_VERBS and any(
                arg in {"-r", "-R", "--recursive"}
                or (arg.startswith("-") and "r" in arg[1:].lower())
                for arg in args))
            or (verb in {"cp", "rsync", "zip"} and any(
                arg in {"-r", "-R", "-a", "--recursive", "--archive"}
                or (arg.startswith("-") and any(flag in arg[1:] for flag in "rRa"))
                for arg in args))
            or (verb in {"tar", "bsdtar", "gtar"} and not any(
                arg in {"-x", "--extract", "--get"}
                or (arg.startswith("-") and "x" in arg[1:]) for arg in args))
        )
        if verb == "find":
            name_filters = [args[i + 1] for i, arg in enumerate(args[:-1])
                            if arg in {"-name", "-iname", "-path", "-ipath"}]
            if name_filters and all(
                    not fnmatch.fnmatchcase("authority.key", pattern.lower())
                    for pattern in name_filters):
                recursive = False
        if any(_authority_key_path(arg, current_cwd, recursive=recursive) for arg in args):
            return True
        if _authority_script_read(expanded_seg, current_cwd):
            return True
        if verb in {"cd", "pushd"}:
            target = next((arg for arg in args if not arg.startswith("-")), "")
            if target:
                target = _audit_expand_home(target)
                current_cwd = (os.path.normpath(target) if os.path.isabs(target)
                               else os.path.normpath(os.path.join(current_cwd, target))
                               if current_cwd else None)
        elif verb == "popd":
            current_cwd = None
    return False


def _audit_bash_hit(command: str, depth: int = 0, tainted: "set[str] | None" = None,
                    cwd: "str | None" = None) -> "str | None":
    """Why *command* may write into the audit directory, or None (block comment above).

    *tainted* holds shell variables known to name it; *cwd* is the working directory
    the command starts in, when known (the hook input's cwd, or an enclosing `cd`).
    Nested shell code (bash -c, eval, a heredoc fed to a shell) is walked recursively
    up to _AUDIT_MAX_DEPTH, then judged by name."""
    from core.file_change_classifier import _blank_heredocs

    if not command:
        return None
    names_dir = _AGENT_AUDIT_DIR.name.lower() in command.lower()
    if "authority.key" in command and (names_dir or (
            cwd and _audit_path_hit(cwd, set()) == "in")):
        return "read or access to Pipeline v1 event authority key"
    if depth > _AUDIT_MAX_DEPTH or len(command) > _AUDIT_MAX_COMMAND_CHARS:
        return "nested shell code that names the audit directory" if names_dir else None
    blanked = _audit_strip_comments(_blank_heredocs(command))
    bodies = "\n".join(o for o, b in zip(command.split("\n"), blanked.split("\n")) if o != b)
    segments = _audit_segments(blanked)
    if segments is None:
        # Unbalanced quotes the stripper could not resolve (ANSI-C `$'…'`, nested).
        # bash_syntax_guard denies a GENUINELY malformed command, but a valid one can
        # land here, so fail CLOSED when the text names the dir or carries a path into
        # it — never silently approve a forgery (correctness-review HIGH).
        probe = set(tainted or ())
        if names_dir or _audit_text_names_dir(command, probe) or any(
            _audit_path_hit(w, probe, cwd) in ("in", "ancestor")
            for w in re.findall(r"[~/$][^\s'\"`;,()\[\]{}]+", command)
        ):
            return "unparseable command that names the audit directory"
        return None
    if _authority_bash_read(segments, cwd):
        return "read or access to Pipeline v1 event authority key"
    state: dict[str, Any] = {
        "tainted": set(tainted or ()), "names_dir": names_dir, "bodies": bodies,
        "depth": depth,
    }
    _audit_set_cwd(state, cwd)
    state["path_names_dir"] = _audit_cmd_names_path(segments, state["tainted"], state["cwd"])
    for seg in segments:
        hit = _audit_segment_hit(seg, state)
        if hit:
            return hit
    # $(…), `…`, <(…) and >(…) run as commands of their own; walked last so every
    # variable the outer command taints is known.
    for inner in _audit_substitutions(blanked):
        hit = _audit_bash_hit(inner, depth + 1, state["tainted"], state["cwd"] or cwd)
        if hit:
            return "command substitution: " + hit
    return None


def _audit_set_cwd(state: dict, cwd: "str | None") -> None:
    """Record the walk's working directory (None = unknown) and whether it is in the
    audit directory."""
    state["cwd"] = os.path.normpath(cwd) if cwd else None
    state["cwd_in"] = bool(cwd) and _audit_path_hit(cwd, set()) == "in"


def _audit_substitutions(text: str) -> list[str]:
    """Bodies of $(…), <(…), >(…) and `…` in *text* (outermost only; quotes are not
    tracked, so a quoted one is also returned, which can only over-match)."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == "`":
            end = text.find("`", i + 1)
            if end < 0:
                break
            out.append(text[i + 1:end])
            i = end + 1
            continue
        if text[i] in "$<>" and text.startswith("(", i + 1) and not text.startswith("((", i + 1):
            depth, j = 1, i + 2
            while j < n and depth:
                depth += {"(": 1, ")": -1}.get(text[j], 0)
                j += 1
            out.append(text[i + 2:j - 1] if depth == 0 else text[i + 2:])
            i = j
            continue
        i += 1
    return out


def _audit_split_redirects(seg: list[str], state: dict) -> "tuple[str | None, list[str], list[str], bool]":
    """(hit, words, herestrings, has_heredoc) of one simple command. *hit* is set when
    an output redirect targets the audit directory (or a relative path after a cd
    into it); the other redirect operators and their targets are removed from words."""
    words: list[str] = []
    herestrings: list[str] = []
    has_heredoc = False
    i = 0
    while i < len(seg):
        tok = seg[i]
        target = seg[i + 1] if i + 1 < len(seg) else ""
        if tok in _AUDIT_REDIRECT_OPS:
            i += 2
            if not target or target in _AUDIT_DISCARD or (tok == ">&" and target.isdigit()):
                continue
            if _audit_path_hit(target, state["tainted"], state["cwd"]) == "in":
                return "redirect into the audit directory", words, herestrings, has_heredoc
            continue
        if tok in ("<", "<&"):
            i += 2
        elif tok == "<<":
            has_heredoc = True
            i += 2
        elif tok == "<<<":
            herestrings.append(target)
            i += 2
        else:
            words.append(tok)
            i += 1
    return None, words, herestrings, has_heredoc


def _audit_leading(words: list[str], state: dict) -> list[str]:
    """*words* past leading keywords and assignments; taints assigned names (and a
    `for` variable) when the command words name the audit directory."""
    tainted = state["tainted"]
    j, assigned, declaring = 0, [], False
    while j < len(words):
        w = words[j]
        m = _AUDIT_ASSIGN_RE.match(w)
        if w in _AUDIT_KEYWORDS:
            declaring = declaring or w in ("export", "local", "declare", "typeset", "readonly")
        elif m:
            assigned.append(m.group(1))
        elif not (declaring and w.startswith("-")):
            break
        j += 1
    if assigned:
        # Resolve the RHS of each `VAR=…`, not the whole `VAR=…` token, so
        # `D=~/…/pipeline_agent_audit` taints D (its ~ must expand).
        probe = []
        for w in words:
            am = _AUDIT_ASSIGN_RE.match(w)
            probe.append(w[am.end():] if am else w)
        if any(_audit_path_hit(w, tainted, state["cwd"]) == "in" for w in probe):
            tainted.update(assigned)
    rest = words[j:]
    if rest and rest[0] == "for" and len(rest) >= 2:
        if any(_audit_path_hit(w, tainted, state["cwd"]) == "in" for w in rest[2:]):
            tainted.add(rest[1])
        return []
    return rest


def _audit_inline_code(verb: str, args: list[str]) -> "tuple[list[str], bool, bool, list[str]]":
    """(inline programs, reads its program from stdin, in-place flag, file operands) of
    a shell or interpreter call. A script FILE argument ends option parsing: it and the
    words after it are operands (data files); the file's contents are not visible here.
    The `-e`/`-c` program text is NOT an operand, so a `s/name/x/` script is never
    mistaken for a path (review finding 6)."""
    codes: list[str] = []
    inplace = False
    shell, awk = verb in _AUDIT_SHELLS, verb.endswith("awk")
    code_flags = _AUDIT_CODE_FLAGS.get(verb, _AUDIT_CODE_FLAGS_DEFAULT)
    value_flags = _AUDIT_INTERP_VALUE_FLAGS.get(verb, frozenset())
    idx, n = 0, len(args)
    while idx < n:
        a = args[idx]
        nxt = args[idx + 1] if idx + 1 < n else ""
        short = a.startswith("-") and not a.startswith("--") and len(a) > 1
        if a == "-":
            return codes, not codes, inplace, list(args[idx + 1:])
        if shell and a in ("-o", "+o", "-O", "+O"):
            idx += 2
            continue
        if a in code_flags:                       # explicit code flag: next word is code
            codes.append(nxt)
            idx += 2
            continue
        if a in value_flags:                      # separate value flag: python -X utf8
            idx += 2
            continue
        if short and any(a.startswith(vf) and len(a) > len(vf) for vf in value_flags):
            idx += 1                              # attached value flag: perl -Mstrict
            continue
        if short:
            cluster = a[1:]
            if (shell and "c" in cluster) or (verb == "python" and "c" in cluster) \
                    or (verb in ("perl", "ruby") and "e" in cluster):
                if verb in ("perl", "ruby") and "i" in cluster:
                    inplace = True
                codes.append(nxt)
                idx += 2
                continue
            if verb in ("perl", "ruby") and "i" in cluster:
                inplace = True
            idx += 1
            continue
        if awk and a in ("-f", "-v", "-F"):
            if a == "-f":
                return codes, False, inplace, list(args[idx + 2:])
            idx += 2
            continue
        if verb == "python" and a == "-m":
            return codes, False, inplace, list(args[idx + 2:])
        if a.startswith("-") or a.startswith("+"):
            idx += 1
            continue
        if awk and not codes:
            codes.append(a)
            idx += 1
            continue
        return codes, False, inplace, list(args[idx:])  # a script file + its operands
    return codes, not codes, inplace, []


def _audit_code_hit(verb: str, args: list[str], any_in: bool, herestrings: list[str],
                    has_heredoc: bool, state: dict) -> "str | None":
    """Hit for a shell / eval / interpreter call (see the block comment)."""
    tainted, cwd_in = state["tainted"], state["cwd_in"]
    if verb == "eval":
        codes, from_stdin, inplace, operands = [" ".join(args)], False, False, []
    else:
        codes, from_stdin, inplace, operands = _audit_inline_code(verb, args)
    op_in = any(_audit_path_hit(o, tainted, state["cwd"]) == "in" for o in operands)
    if has_heredoc:
        codes.append(state["bodies"])
    codes.extend(herestrings)
    if from_stdin and not has_heredoc and not herestrings and state["names_dir"]:
        # `echo '…dir…' | bash` / `… | python` — the piped text IS the program, so a
        # mention of the dir name in the command is treated as naming it (substring).
        return f"{verb} reading code from a pipe in a command that names the audit directory"
    if inplace and (op_in or (cwd_in and not operands)):
        return f"{verb} -i on the audit directory"
    if verb in _AUDIT_SHELLS or verb == "eval":
        for code in codes:
            hit = _audit_bash_hit(code, state["depth"] + 1, tainted, state["cwd"])
            if hit:
                return f"{verb} code: {hit}"
        return None
    code = "\n".join(c for c in codes if c)
    if code.strip() and (_audit_code_names_dir(code, tainted) or cwd_in or op_in):
        return f"{verb} code that writes into the audit directory"
    return None


def _audit_segment_hit(seg: list[str], state: dict) -> "str | None":
    """Hit for one simple command; updates *state* (tainted variables, cwd)."""
    hit, words, herestrings, has_heredoc = _audit_split_redirects(seg, state)
    if hit:
        return hit
    tainted, cwd_in = state["tainted"], state["cwd_in"]
    rest = _audit_leading(words, state)
    k, from_stdin = _audit_strip_wrappers(rest)
    if k >= len(rest):
        return None
    verb = re.sub(r"^(python|ruby|perl|node|php|lua)[\d.]*$", r"\1", os.path.basename(rest[k]))
    args = rest[k + 1:]
    if verb == "read":
        if state["names_dir"]:
            skip = {"-p", "-d", "-t", "-n", "-N", "-u"}
            tainted.update(a for i, a in enumerate(args)
                           if not a.startswith("-") and (i == 0 or args[i - 1] not in skip))
        return None
    if verb in ("cd", "pushd", "popd"):
        # A cd INTO the directory is denied: the Bash tool keeps its cwd between
        # calls, so a later call's relative `> m.marker` would be invisible here.
        target = next((a for a in args if not a.startswith("-")), "")
        if verb != "popd" and target and _audit_path_hit(target, tainted, state["cwd"]) == "in":
            return f"{verb} into the audit directory"
        if verb == "popd" or target.startswith("$") or (verb == "cd" and args[:1] == ["-"]):
            _audit_set_cwd(state, None)
        elif not target:
            _audit_set_cwd(state, os.path.expanduser("~"))
        else:
            path = _audit_expand_home(target)
            if not path.startswith("/"):
                path = os.path.join(state["cwd"], path) if state["cwd"] else ""
            _audit_set_cwd(state, path or None)
        return None
    hits = [_audit_path_hit(a, tainted, state["cwd"]) for a in args]
    any_in = "in" in hits
    via_stdin = from_stdin and state["path_names_dir"]
    if verb in _AUDIT_SHELLS or verb in _AUDIT_INTERPRETERS or verb == "eval":
        if via_stdin:
            return f"xargs {verb} fed a path in the audit directory"
        return _audit_code_hit(verb, args, any_in, herestrings, has_heredoc, state)
    return _audit_tool_hit(verb, args, hits, cwd_in or via_stdin, tainted, state["cwd"])


def _audit_tool_hit(verb: str, args: list[str], hits: list, implicit: bool,
                    tainted: set[str], cwd: "str | None") -> "str | None":
    """Hit for a file-tool verb. *implicit*: its paths may be in the audit directory
    without naming it (after a cd into it, or fed by xargs in a command naming it)."""
    any_in = "in" in hits
    touched = any_in or implicit
    short_flag = lambda a, ch: a.startswith("-") and not a.startswith("--") and ch in a[1:]  # noqa: E731
    positional = [a for a in args if not a.startswith("-")]

    if verb == "sed":
        # Only `sed -i` that EDITS a file operand in the dir is a write. Matching or
        # printing the dir NAME in a script is a read; the dir name in the s/// is a
        # refactor of this repo's own source (review finding 6). The `w`-command
        # writing into the dir is a documented residual.
        inplace = any(a == "--in-place" or a.startswith("--in-place=") or short_flag(a, "i")
                      for a in args)
        val_opts = {"-e", "--expression", "-f", "--file", "-l", "--line-length", "-I"}
        has_script_opt = any(a in ("-e", "--expression", "-f", "--file") for a in args)
        operands, skip, script_consumed, prev = [], False, has_script_opt, ""
        for a in args:
            if skip:
                skip = False
            elif a == "" and short_flag(prev, "i"):
                pass  # macOS `sed -i ''` backup-suffix argument, not the script
            elif a in val_opts:
                skip = True
            elif a.startswith("-"):
                pass
            elif not script_consumed:
                script_consumed = True
            else:
                operands.append(a)
            prev = a
        op_hits = [_audit_path_hit(o, tainted, cwd) for o in operands]
        if inplace and ("in" in op_hits or (implicit and not operands)):
            return "sed -i on the audit directory"
        return None
    if verb == "find":
        writers = (_AUDIT_WRITE_VERBS | _AUDIT_COPY_VERBS | _AUDIT_SHELLS
                   | _AUDIT_EXTRACT_VERBS | {"sed", "dd", "tee", "truncate", "patch"})
        acting = False
        for i, a in enumerate(args):
            if a in ("-delete", "-fprint", "-fprint0", "-fprintf", "-fls"):
                acting = True
            elif a in ("-exec", "-execdir", "-ok", "-okdir") and i + 1 < len(args):
                # A read action (-exec cat/jq/python -m json.tool) is not a write.
                acting = acting or os.path.basename(args[i + 1]) in writers
        if acting and (touched or "ancestor" in hits):
            return "find with a write action on the audit directory"
        return None
    if verb in _AUDIT_EXTRACT_VERBS:
        if verb in ("tar", "bsdtar", "gtar"):
            first = args[0] if args else ""
            extracting = any(a in ("-x", "--extract", "--get") or short_flag(a, "x") for a in args) \
                or (bool(first) and not first.startswith("-") and "x" in first)
        else:
            extracting = True  # unzip / unar always extract
        dest = None
        for i, a in enumerate(args):
            if a in ("-C", "--directory", "-d") and i + 1 < len(args):
                dest = args[i + 1]
            elif a.startswith("--directory="):
                dest = a.split("=", 1)[1]
        # Only the extract DESTINATION matters; reading an archive FROM the dir is a read.
        dh = _audit_path_hit(dest, tainted, cwd) if dest is not None else ("in" if implicit else None)
        return f"{verb} extract into the audit directory" if extracting and dh in ("in", "ancestor") else None
    if verb in _AUDIT_WRITE_VERBS:
        if touched:
            return f"{verb} on the audit directory"
        recursive_mode = verb in ("chmod", "chown", "chgrp", "chflags") and any(
            short_flag(a, "R") or a == "--recursive" for a in args)
        if "ancestor" in hits and (verb in _AUDIT_REMOVE_VERBS or recursive_mode):
            return f"{verb} on a directory that contains the audit directory"
        return None
    if verb in _AUDIT_COPY_VERBS:
        dests = positional[-1:] + [args[i + 1] for i, a in enumerate(args[:-1])
                                   if a in ("-t", "--target-directory")]
        dests += [a.split("=", 1)[1] for a in args if a.startswith("--target-directory=")]
        dest_hits = [_audit_path_hit(d, tainted, cwd) for d in dests]
        if implicit or "in" in dest_hits:
            return f"{verb} into the audit directory"
        # A recursive copy / rsync / ditto whose destination CONTAINS the dir can merge
        # a forged marker into it (the two-step Write-then-copy forgery, both reviews).
        recursive = verb in ("rsync", "ditto") or any(
            short_flag(a, "r") or short_flag(a, "R") or short_flag(a, "a")
            or a in ("--recursive", "--archive") for a in args)
        if "ancestor" in dest_hits and recursive:
            return f"{verb} into a directory that contains the audit directory"
        if any_in and "--remove-source-files" in args:
            return f"{verb} --remove-source-files out of the audit directory"
    return None


_AUDIT_FILE_TOOLS = {
    "Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
}

_AUDIT_READ_TOOLS = {"Read": "file_path", "Grep": "path"}


def _authority_key_file(path: str, cwd: "str | None" = None, *, recursive: bool = False) -> bool:
    if not path:
        return False
    if _authority_key_path(path, cwd, recursive=recursive):
        return True
    expanded = _audit_expand_home(path)
    if cwd and not os.path.isabs(expanded):
        expanded = os.path.join(cwd, expanded)
    # A file tool may pass a directory that does not yet exist in a synthetic test.
    # For recursive Grep, any audit/run directory scope can contain authority.key.
    return recursive and _audit_prefix_hit(os.path.realpath(expanded)) in {"in", "ancestor"} \
        and not os.path.splitext(os.path.basename(expanded))[1]


def _audit_file_tool_hit(path: str, cwd: "str | None" = None) -> bool:
    """True when a file tool's *path* (~ expanded, relative to *cwd*, symlinks
    resolved) is the audit directory or inside it."""
    if not path:
        return False
    path = os.path.expanduser(path)
    if cwd and not os.path.isabs(path):
        path = os.path.join(cwd, path)
    # Compare by path components (os.sep-agnostic) so a Windows backslash path and the
    # root's trailing-slash handling can't disagree (review finding 6).
    resolved = tuple(c.lower() for c in Path(os.path.realpath(path)).parts)
    for d in _audit_dir_parts():
        if resolved[:len(d)] == d:
            return True
    return False


def create_audit_marker_write_gate(session_context: dict[str, Any]):
    """Factory: PreToolUse gate (Bash and Write|Edit|MultiEdit|NotebookEdit) that
    DENIES, always, a write, move or delete in _AGENT_AUDIT_DIR (forms in the section
    comment above). Reads pass. No operator bypass (gate_force.UNFORCEABLE). Fail-open
    on any internal error.
    session_context is the mutable dict SessionUnit updates (read at call time)."""

    async def audit_marker_write_gate(
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        tool = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {}) or {}
        cwd = input_data.get("cwd")
        cwd = cwd if isinstance(cwd, str) and cwd.startswith("/") else None
        try:
            if tool == "Bash":
                subject = str(tool_input.get("command") or "")
                hit = _audit_bash_hit(subject, cwd=cwd)
            elif tool in _AUDIT_FILE_TOOLS:
                subject = str(tool_input.get(_AUDIT_FILE_TOOLS[tool]) or "")
                hit = (f"{tool} into the audit directory"
                       if _audit_file_tool_hit(subject, cwd) else None)
            elif tool in _AUDIT_READ_TOOLS:
                subject = str(tool_input.get(_AUDIT_READ_TOOLS[tool]) or "")
                hit = ("read or access to Pipeline v1 event authority key"
                       if _authority_key_file(subject, cwd, recursive=tool == "Grep") else None)
            else:
                return {"decision": "approve"}
        except Exception:  # noqa: BLE001
            logger.warning("audit_marker_write_gate: classify failed — failing open",
                           exc_info=True)
            return {"decision": "approve"}
        if hit is None:
            return {"decision": "approve"}
        session_id = session_context.get("sdk_session_id", "") or ""
        logger.warning("[BLOCKED] audit_marker_write_gate (%s) in session %s: %s",
                       hit, session_id, subject[:120])
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "DENY (" + hit + "): this writes, moves or deletes files in "
                    + str(_AGENT_AUDIT_DIR) + ", or may (the checker could not rule "
                    "it out). Only the backend's SubagentStop hook writes there. Its "
                    "markers are the proof that review sub-agents really ran: the D7 "
                    "experts, the Red Team, the Gate-0/1 skeptics and the adversarial "
                    "commit gate all read them, so a hand-written or deleted marker "
                    "would defeat those checks. To produce a marker, spawn the reviewer "
                    "with the Agent tool. Reading markers is fine: cat, ls, grep or jq "
                    "by an absolute path. This gate has no bypass: if it is wrong, "
                    "tell the user."
                ),
            }
        }

    return audit_marker_write_gate


def _project_for_repo(root: str) -> str:
    """Name of the workspace project for repo *root*: the Projects/<P> directory whose
    name equals the repo directory name case-insensitively (swarmai → SwarmAI), else
    the placeholder "<project>"."""
    want = os.path.basename(root.rstrip(os.sep)).lower()
    try:
        for name in sorted(os.listdir(os.path.join(_pipeline_runs_root(), "Projects"))):
            if name.lower() == want:
                return name
    except OSError:
        pass
    return "<project>"


def _numstat_lines(root: str, *extra: str) -> "dict[str, int] | None":
    """repo-root-relative path → added+removed lines from `git diff --numstat`
    (renames off, NUL-separated so odd paths parse). Binary files count 0.
    None on git error / timeout."""
    try:
        r = subprocess.run(
            ["git", "-C", root, "diff", "--numstat", "--no-renames", "--no-ext-diff",
             "-z", *extra],
            capture_output=True, text=True, errors="replace", timeout=_GATE_GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    out: dict[str, int] = {}
    for rec in r.stdout.split("\0"):
        parts = rec.strip("\n").split("\t", 2)
        if len(parts) != 3 or not parts[2]:
            continue
        added, removed, rel = parts
        n = (int(added) if added.isdigit() else 0) + (int(removed) if removed.isdigit() else 0)
        out[rel] = out.get(rel, 0) + n
    return out


def _pending_source_line_count(cwd: str, command: str,
                               project_repos: bool = False) -> "int | None":
    """Changed SOURCE lines (added+removed; pure-doc paths excluded; with
    *project_repos* — worktree mode — any other path counts at least 1, so a binary or
    mode-only change is never free) the `git commit` in
    *command* is about to record; None if uncomputable (→ caller fails open).

    The path set is _pending_commit_paths (staged + -a + pathspec + -o). Lines come
    from `git diff --cached --numstat` for the pending paths it covers. Pending
    paths the index does not cover (pathspec / -a forms on unstaged files) take
    their working-tree count from `git diff --numstat`. A pending DIRECTORY
    (`git commit -- backend/`) covers every numstat path under it.

    Scope: only a commit whose repo root (resolved from *cwd*) is the SwarmAI source
    repo (_source_repo_root()) is counted — or, with *project_repos* (worktree mode on),
    any workspace project's code repo (worktree_write_gate._project_repo); any other
    repo returns 0, so the gate never fires on a user's unrelated project."""
    root = _gate_repo_root_for(cwd)
    if root is None:
        return None
    src = _source_repo_root()
    if not src or root != src:
        from core.worktree_write_gate import _project_repo
        if not (project_repos and _project_repo(os.path.realpath(root))):
            return 0
    pending = _pending_commit_paths(cwd, command)
    if pending is None or pending is _PENDING_CROSS_REPO:
        return None
    if not pending:
        return 0
    pending_dirs = tuple(p + os.sep for p in pending if os.path.isdir(p))

    def _in_pending(ap: str) -> bool:
        return ap in pending or (bool(pending_dirs) and ap.startswith(pending_dirs))

    cached = _numstat_lines(root, "--cached")
    if cached is None:
        return None
    total = 0
    seen: set[str] = set()
    for rel, n in cached.items():
        ap = os.path.realpath(os.path.join(root, rel))
        if _in_pending(ap):
            seen.add(ap)
            if not is_doc_only_path(rel):
                # worktree mode (limit 0): a binary / mode-only change is still a
                # source path; the mode-off 20-line rule counts lines only
                total += max(n, 1) if project_repos else n
    if pending_dirs or any(p not in seen for p in pending):
        worktree = _numstat_lines(root)
        if worktree is None:
            return None
        for rel, n in worktree.items():
            ap = os.path.realpath(os.path.join(root, rel))
            if ap in seen or not _in_pending(ap):
                continue
            if not is_doc_only_path(rel):
                # worktree mode (limit 0): a binary / mode-only change is still a
                # source path; the mode-off 20-line rule counts lines only
                total += max(n, 1) if project_repos else n
    return total


def _direct_gate_mode(cwd: str, session_id: str) -> "tuple[bool | None, str | None]":
    """(mode, repo root) for direct_commit_gate. mode None: exempt (worktree mode on and
    the session has a live shared-mode run of its own — exactly worktree_write_gate rule
    4's exemption; a paused or stale isolated run exempts nothing). True: worktree mode on and the repo is one
    a run can isolate (run_worktree.repo_for_write) → limit 0. False: the 20-line rule
    (mode off, or a repo no run could isolate — a Brazil package, submodules — whose
    commits a run could not take over anyway)."""
    from core.run_worktree import mode_enabled, repo_for_write
    root = _gate_repo_root_for(cwd)
    if not mode_enabled():
        return False, root
    from core.worktree_write_gate import _session_runs_light
    if any(r.get("worktree_mode") == "shared" for r in _session_runs_light(session_id)):
        return None, root
    return (root is not None and repo_for_write(root, _pipeline_runs_root()) is not None), root


def create_direct_commit_gate(session_context: dict[str, Any]):
    """Factory: PreToolUse (Bash) gate on `git commit` (design §4-G1). When this
    session has NO active run of its own (C6), a commit in the SwarmAI source repo
    recording more than 20 changed SOURCE lines (pure-doc paths excluded:
    ``core.doc_paths.is_doc_only_path``, the same definition rule 4 uses) is
    DENIED with a copyable `pipeline_v1.py create --profile direct` command for the repo's
    project. With worktree mode on the limit is 0 and every project code repo counts:
    a code change lands from a run's worktree, so a source commit in a main checkout
    by a session with no run is exactly the write the worktree gate could not see
    (an interpreter or formatter writing files). Runs of other sessions and unowned runs (no session_id) do not exempt
    a session. Commits in other repos and docs pass, and so (worktree mode off, or a
    repo no run can isolate) do changes of at most 20 lines. Fail-open on
    any error/timeout. Bypass (operator only, backend/daemon env):
    SWARM_GATE_FORCE=direct_commit_gate (or commit_gate), logged."""

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

        if force_enabled("direct_commit_gate", input_data):
            return {"decision": "approve"}

        session_id = session_context.get("sdk_session_id", "") or ""
        if not session_id:
            return {"decision": "approve"}  # cannot scope → fail open
        cwd = input_data.get("cwd", "") or ""
        try:
            own_run = await asyncio.wait_for(
                executors.run_in("gate", _session_run_state, session_id),
                timeout=_RUN_SCAN_TIMEOUT,
            )
        except Exception:  # incl. asyncio.TimeoutError
            logger.warning("direct_commit_gate: run scan failed — failing open", exc_info=True)
            return {"decision": "approve"}
        if own_run is not None:
            return {"decision": "approve"}  # in its own run: other gates own the commit

        try:
            mode_on, _root = await asyncio.wait_for(
                executors.run_in("gate", _direct_gate_mode, cwd, session_id),
                timeout=_DIRECT_GATE_TIMEOUT,
            )
        except Exception:  # incl. asyncio.TimeoutError
            logger.warning("direct_commit_gate: mode check failed — failing open", exc_info=True)
            return {"decision": "approve"}
        if mode_on is None:
            return {"decision": "approve"}  # a live run of its own (any mode): exempt
        limit = 0 if mode_on else _DIRECT_COMMIT_MAX_SOURCE_LINES
        try:
            lines = await asyncio.wait_for(
                executors.run_in("gate", _pending_source_line_count, cwd, command,
                                 mode_on),
                timeout=_DIRECT_GATE_TIMEOUT,
            )
        except Exception:  # incl. asyncio.TimeoutError
            logger.warning("direct_commit_gate: line count failed — failing open", exc_info=True)
            return {"decision": "approve"}
        if lines is None or lines <= limit:
            return {"decision": "approve"}

        project = shlex.quote(_project_for_repo(_root or _source_repo_root() or ""))
        logger.warning("[BLOCKED] direct_commit_gate: %d source lines outside a run "
                       "(session=%s): %s", lines, session_id, command[:120])
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "git commit → DENY: this commit changes " + str(lines) + " source lines "
                    "(limit " + str(limit) + (", worktree mode on: a code change lands from "
                    "a run's worktree" if mode_on else "") + ") and this session "
                    "has no pipeline run. A direct fix needs an exit contract written before "
                    "verification starts (design §4-G1), so verification has an end point and "
                    "the change goes through the same runner, gates and landing. FIX: "
                    "create a direct run and let its DELIVER landing commit:\n"
                    + _v1_cmd("create --project " + project
                              + " --profile direct --requirement '<the user request>'") + "\n"
                    "Pure-doc files (*.md, and anything under backend/context/) are "
                    "exempt" + (" here" if mode_on else ", and so are changes of at most "
                                + str(limit) + " source lines") + ". Only the operator can make a deliberate "
                    "exception, by setting SWARM_GATE_FORCE=direct_commit_gate in the backend/daemon "
                    "environment (a variable set in a Bash command has no effect); ask "
                    "the user if you believe one is warranted."
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
# zsh expands `=word` (EQUALS: the path of command `word`) even under -n, so
# `echo ======` failed the parse check with "===== not found" and was denied as a
# hang. It is not one: at run time it fails fast. 140 of the 144 denials found in
# session transcripts (2026-10-17..20) were this, mostly `echo ===` separators.
# noequals turns that expansion off for the check only; real parse errors
# (unmatched quote, unclosed block) still exit non-zero.
_SYNTAX_CHECK_ARGS: tuple[str, ...] = (
    ("-n", "-o", "noequals") if os.path.basename(_SYNTAX_CHECK_SHELL) == "zsh" else ("-n",)
)
# A pathological command (e.g. ~1M-deep `$()` nesting) can make the parse check
# spin a core; cap input size so it never reaches the shell. Real commands are
# tiny; 256KB is orders of magnitude above any legit command. (Gate-2 LOW.)
_SYNTAX_CHECK_MAX_CMD_BYTES = 256 * 1024
# The parse check is sub-millisecond; this cap is pure defense-in-depth so the
# guard itself can never hang. On timeout we KILL the subprocess and fail-OPEN.
# 3.0s (was 1.0): the cap also covers the fork/exec of this large process, which
# under host memory pressure took over 1s (2026-10-22: two fail-opens at 1.0s,
# parse itself instant). Still far below the Bash call's own budget.
_BASH_SYNTAX_CHECK_TIMEOUT_S = 3.0


# Fast path (F3, 2026-10-21; quote-aware 2026-10-22): a command can only be
# parse-incomplete through an open quote / backtick / substitution / brace /
# bracket / heredoc / compound keyword, or a trailing operator or backslash.
# _syntax_cannot_be_incomplete approves a command none of these can leave open
# without starting a shell.
_SYNTAX_KEYWORDS = frozenset({"if", "then", "elif", "else", "fi", "for", "while", "until",
                              "do", "done", "case", "esac", "select", "function", "repeat",
                              "coproc", "time", "!"})
_SYNTAX_TRAILING = ("|", "&&", "||", "\\")


def _syntax_cannot_be_incomplete(command: str) -> bool:
    """True only when *command* is certainly complete: the caller then approves it
    without starting a shell. False means "not sure", never "incomplete": the shell's
    own ``-n`` parse decides those.

    Heredocs, compound keywords outside quotes, a trailing operator or backslash,
    backticks and ANSI-C ``$'...'`` quoting are not judged here (False). Otherwise one pass tracks
    single and double quotes (backslash escapes outside single quotes) and the
    ( { [ nesting outside quotes; a ``$(`` or `` ` `` inside double quotes is not
    judged either. Complete = no open quote and every bracket closed. A ``#`` is an
    ordinary character here, so a quote inside a comment only makes it unsure."""
    if "<<" in command or "`" in command or "$'" in command:
        return False
    if command.rstrip().endswith(_SYNTAX_TRAILING):
        return False
    closing = {"(": ")", "{": "}", "[": "]"}
    stack: list[str] = []
    quote = ""  # "", "'" or '"'
    bare: list[str] = []  # the command outside quotes, for the keyword check
    i, n = 0, len(command)
    while i < n:
        c = command[i]
        bare.append(c if not quote and c not in "'\"" else " ")
        if quote == "'":
            if c == "'":
                quote = ""
        elif c == "\\":
            i += 1  # the next character is escaped (also inside double quotes)
        elif quote == '"':
            if c == '"':
                quote = ""
            elif c == "$" and i + 1 < n and command[i + 1] == "(":
                return False
        elif c in "'\"":
            quote = c
        elif c in closing:
            stack.append(closing[c])
        elif c in ")}]":
            if not stack or stack.pop() != c:
                return False
        i += 1
    if quote or stack:
        return False
    return not any(w in _SYNTAX_KEYWORDS for w in re.split(r"[\s;&|()]+", "".join(bare)))


# Verdicts of commands already parsed: (exit code, stderr) by sha1 of the command.
_SYNTAX_VERDICTS: "dict[bytes, tuple[int, bytes]]" = {}
_SYNTAX_VERDICTS_MAX = 512


def _run_syntax_check(cmd_bytes: bytes) -> "tuple[int | None, bytes]":
    """``<shell> -n`` on *cmd_bytes* (blocking: run in a pool) → (exit code, stderr);
    (None, b"") when it exceeds _BASH_SYNTAX_CHECK_TIMEOUT_S (subprocess.run kills
    the child)."""
    try:
        r = subprocess.run([_SYNTAX_CHECK_SHELL, *_SYNTAX_CHECK_ARGS], input=cmd_bytes,
                           capture_output=True, timeout=_BASH_SYNTAX_CHECK_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None, b""
    return r.returncode, r.stderr


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

    if _syntax_cannot_be_incomplete(command):
        return {"decision": "approve"}
    key = hashlib.sha1(cmd_bytes, usedforsecurity=False).digest()  # cache key
    cached = _SYNTAX_VERDICTS.get(key)
    if cached is not None:
        rc, stderr = cached
    else:
        try:
            # fork/exec of this ~300MB, ~50-thread process took up to 3.2s on the
            # event loop thread (2026-10-21 stack): run it in the gate pool
            from core import executors
            rc, stderr = await executors.run_in("gate", _run_syntax_check, cmd_bytes)
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
        if rc is None:
            logger.warning(
                "[bash_syntax_guard] %s -n exceeded %.1fs — failing open",
                _SYNTAX_CHECK_SHELL, _BASH_SYNTAX_CHECK_TIMEOUT_S,
            )
            return {"decision": "approve"}
        if len(_SYNTAX_VERDICTS) >= _SYNTAX_VERDICTS_MAX:
            _SYNTAX_VERDICTS.clear()
        _SYNTAX_VERDICTS[key] = (rc, stderr)

    # exit 0 → parseable (incl. valid multiline/heredoc/$()/quoted) → APPROVE.
    if rc == 0:
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
    # Pipeline v1 keeps executable stage guidance in the nine top-level cards;
    # detailed rules and checks live in core/run_protocol/.
    "*/s_autonomous-pipeline/stages/*.md",
    # Whole-file prompts emitted by scripts/spawn_prompt.py remain governed.
    "*/s_autonomous-pipeline/data/gate-prompts/*.md",
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


def _bash_governance_target(command: str) -> tuple[str, int]:
    """(path, tier) of the highest-tier governance file a Bash command may write.

    Uses the same parsers as the live file-change emit, plus the heredoc
    candidates (an interpreter script's path literals). A candidate the script
    only reads can over-trigger; the gate is advisory, so that costs a reminder.
    Returns ("", 0) when nothing matches.
    """
    from core.file_change_classifier import (
        parse_bash_heredoc_write_candidates,
        parse_bash_write_targets,
    )

    if not command:
        return "", 0
    best_path, best_tier = "", 0
    for path in parse_bash_write_targets(command) + parse_bash_heredoc_write_candidates(command):
        tier = _match_governance_tier(path)
        if tier and (best_tier == 0 or tier < best_tier):
            best_path, best_tier = path, tier
            if tier == 1:
                break
    return best_path, best_tier


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
        if tool_name not in ("Edit", "Write", "Bash"):
            return {"decision": "approve"}

        tool_input = input_data.get("tool_input", {}) or {}
        if tool_name == "Bash":
            # Shell writes (redirects, tee, cp/mv, sed -i, interpreter heredoc
            # patches) reach the same files Edit/Write do; without this a
            # `python3 - <<'PY'` patch to SOUL.md skipped the gate entirely.
            # Self-guarded: this hook runs solo under its matcher (no chain
            # try/except), and a parse error must never block a command.
            try:
                file_path, tier = _bash_governance_target(
                    str(tool_input.get("command") or "")
                )
            except Exception:  # noqa: BLE001
                logger.warning("governance_file_gate: gate infrastructure error — failing open", exc_info=True)
                return {"decision": "approve"}
        else:
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
            'MultiEdit': 'file_path',
            'NotebookEdit': 'notebook_path',
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
