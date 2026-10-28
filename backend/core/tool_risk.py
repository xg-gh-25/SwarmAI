"""Declarative tool risk taxonomy — the intrinsic side-effect class of a tool call.

Ported from andrewyng/openworker's `risk.py` (MIT) and adapted to SwarmAI's tool
namespace: built-in Claude tools (Read/Write/Edit/Bash/…) + `mcp__Server__tool` MCP
names. Replaces the ad-hoc "is this dangerous" logic scattered across the Bash-only
gates with ONE declared property a single `classify()` reads.

Why this exists (the security hole it closes): SwarmAI's `dangerous_command_gate` is
`matcher="Bash"`-scoped and early-returns approve on non-Bash tools, and the SDK
`can_use_tool` file handler returns allow for every non-file tool (and is None under
`global_user_mode`). So a NON-Bash tool with OFF-MACHINE side effects — an MCP tool
that sends email / posts to Slack / mutates a CRM — passes with ZERO approval gating.
`classify()` makes EXTERNAL a first-class property so `external_approval_gate` can route
those through the existing PermissionManager approval flow (generalizing the C041
gh/git protection beyond Bash).

ONE-DIRECTION RULE (invariant): classify() decides the CLASS; the gates decide the
ACTION. This module contains NO gate carve-outs, re-implements NO gate logic, and
imports NOTHING from security_hooks. A gate calls classify(); classify() never calls
a gate. (Enforced by test_tool_risk.TestOneDirectionInvariant.)

FAIL-OPEN default (Gate-1 B3): an UNKNOWN tool defaults to READ, not EXTERNAL. A
missed gate is recoverable; a false-positive prompt-storm on every unknown/read tool
trains the user to blind-approve (PIT51/56) — strictly worse. EXTERNAL is asserted
only for verbs that genuinely leave the machine.
"""

from __future__ import annotations

import json
import logging
import os
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class RiskClass(str, Enum):
    READ = "read"  # no side effects — always allowed
    WRITE_LOCAL = "write_local"  # mutates the workspace — path-scoped + mode-gated
    EXEC = "exec"  # runs commands — the hardened dangerous_command_gate owns this
    EXTERNAL = "external"  # off-machine side effect — the approval hook


# --- Built-in Claude tools whose risk is fixed by name ------------------------
_WRITE_LOCAL_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
_EXEC_TOOLS = frozenset({"Bash", "BashOutput", "KillShell"})
# Built-ins that are pure reads / in-process orchestration — NEVER external.
# Task/Agent (sub-agent spawn), Skill (skill invocation), Todo* are in-process:
# they must not trigger an off-machine approval prompt.
_READ_BUILTINS = frozenset(
    {
        "Read", "Glob", "Grep", "LS",
        "WebFetch", "WebSearch",  # retrieve, do not mutate off-machine state
        "Task", "Agent",  # sub-agent spawn — in-process orchestration
        "Skill",  # skill invocation — in-process
        "TodoWrite", "TodoRead",  # local todo list
        "ToolSearch", "ExitPlanMode",
    }
)

# --- MCP verb taxonomy (name = mcp__Server__verb_object) ----------------------
# READ verbs: a tool whose verb is one of these is a pure read — the PIT56 guard
# (never prompt on a search_/get_/list_ call).
_MCP_READ_VERBS = frozenset(
    {"search", "read", "get", "list", "fetch", "lookup", "find", "query", "check", "describe", "download"}
)

# OFF-MACHINE verbs: a tool whose verb is one of these leaves the machine (sends /
# publishes / mutates a remote system) → EXTERNAL → route to approval.
_MCP_EXTERNAL_VERBS = frozenset(
    {"send", "post", "reply", "forward", "upload", "publish", "share", "invite", "remove", "delete", "update", "archive"}
)

# LOCAL-write verbs: a tool whose verb is one of these mutates LOCAL/workspace state
# (a draft, a local analysis artifact) — NOT off-machine. Classified WRITE_LOCAL, so
# it does NOT trigger the external approval prompt. (Gate-1 B3: email_draft,
# create_account_summary, sift_insights_create must not prompt.)
_MCP_LOCAL_WRITE_VERBS = frozenset({"draft", "create", "add", "set", "save"})

# A user-local override resolver: tool name -> RiskClass (or None to defer). Reserved
# for a future Phase-2 relax path (mirrors openworker's RiskOverrides); always None
# today — kept in the signature so callers/tests are stable.
RiskOverrides = Any


def _mcp_tokens(tool_name: str) -> Optional[list[str]]:
    """For an `mcp__Server__tool_name` name, return the lowercased underscore-delimited
    tokens of the TOOL part. Returns None for non-MCP names.

    We tokenize the whole tool part rather than taking the first token, because the
    action verb is NOT reliably first: `email_send` / `bulk_post_message` /
    `sift_insights_create` all carry the meaningful verb in a non-leading position.
    classify() then does priority membership over these tokens.

    Example: 'mcp__aws-sentral-mcp__get_opportunity_details' -> ['get','opportunity','details']
             'mcp__slack-mcp__bulk_post_message'             -> ['bulk','post','message']
    """
    if not tool_name.startswith("mcp__"):
        return None
    parts = tool_name.split("__")
    if len(parts) < 3 or not parts[2]:
        return None
    return [t for t in parts[2].lower().split("_") if t]


def classify(
    tool_name: str,
    metadata: Any = None,
    overrides: Optional[Any] = None,
) -> RiskClass:
    """Effective risk class of a tool call.

    Resolution order:
      1. user-local override (``user_overrides``: the user's own decision, may lower
         or raise; never for Bash, which dangerous_command_gate owns)
      2. built-in by-name tables (WRITE_LOCAL / EXEC / READ)
      3. MCP verb taxonomy (external verb → EXTERNAL, read/local verb → READ/WRITE_LOCAL)
      4. fail-OPEN default → READ
    then the tool's own *metadata* may RAISE the result to EXTERNAL, never lower it
    (``_metadata_says_external``): a third-party MCP server calling itself read-only
    does not count (design 2026-10-24-swarmai-usable-by-anyone §3.4). SwarmAI hooks see
    no MCP annotations today, so the gate passes none; the rule is here for when they do.
    """
    if overrides is not None and tool_name not in _EXEC_TOOLS:
        ov = overrides(tool_name) if callable(overrides) else None
        if ov is not None:
            return ov
    base = _classify_by_name(tool_name)
    if base is not RiskClass.EXTERNAL and _metadata_says_external(metadata):
        return RiskClass.EXTERNAL
    return base


def _meta_get(metadata: Any, key: str) -> Any:
    if isinstance(metadata, dict):
        return metadata.get(key)
    return getattr(metadata, key, None)


def _metadata_says_external(metadata: Any) -> bool:
    """True when the tool's own metadata declares an off-machine or destructive effect:
    ``requires_approval``, MCP ``destructiveHint``, or ``openWorldHint`` without
    ``readOnlyHint``. Only ever used to RAISE a class."""
    if metadata is None:
        return False
    if _meta_get(metadata, "requires_approval") is True:
        return True
    if _meta_get(metadata, "destructiveHint") is True:
        return True
    return _meta_get(metadata, "openWorldHint") is True \
        and _meta_get(metadata, "readOnlyHint") is not True


def _overrides_path() -> Path:
    from config import get_app_data_dir
    return get_app_data_dir() / "tool_risk_overrides.json"


_OVERRIDE_CACHE: dict = {}


def user_overrides(tool_name: str) -> Optional[RiskClass]:
    """The user's local override for *tool_name* from ``<app data>/tool_risk_overrides.json``
    (``{"mcp__server__tool": "read" | "write_local" | "exec" | "external"}``), or None.
    Re-read when the file's mtime changes; a missing or malformed file, or an unknown
    value, means no override."""
    path = _overrides_path()
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return None
    hit = _OVERRIDE_CACHE.get(str(path))
    if hit is None or hit[0] != mtime:
        table: dict = {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for name, value in raw.items():
                    try:
                        table[str(name)] = RiskClass(str(value).lower())
                    except ValueError:
                        logger.warning("tool_risk override %s=%r ignored: unknown class",
                                       name, value)
        except (OSError, ValueError):
            logger.warning("tool_risk overrides file %s unreadable — ignored", path)
        _OVERRIDE_CACHE[str(path)] = hit = (mtime, table)
    return hit[1].get(tool_name)


def _classify_by_name(tool_name: str) -> RiskClass:
    """Steps 2-4 of ``classify``: the class from the tool's name alone."""

    if not tool_name:
        return RiskClass.READ

    # 2. built-in by-name
    if tool_name in _WRITE_LOCAL_TOOLS:
        return RiskClass.WRITE_LOCAL
    if tool_name in _EXEC_TOOLS:
        return RiskClass.EXEC
    if tool_name in _READ_BUILTINS:
        return RiskClass.READ

    # 3. MCP verb taxonomy — priority membership over the tool-part tokens.
    # PRIORITY ORDER MATTERS (Gate-1 B3 + PIT56):
    #   READ first        → a tool whose name contains ANY read verb never prompts
    #                       (e.g. 'email_search', 'get_...'); the safest false-negative.
    #   LOCAL-write next  → 'draft'/'create'/'add' mutate LOCAL state, not off-machine
    #                       (email_draft, create_account_summary, sift_insights_create).
    #   EXTERNAL last     → only a genuine off-machine verb with no read/local token wins
    #                       (email_send, bulk_post_message, update_opportunity).
    #   unknown           → fail-OPEN (READ).
    tokens = _mcp_tokens(tool_name)
    if tokens is not None:
        tokenset = set(tokens)
        if tokenset & _MCP_READ_VERBS:
            return RiskClass.READ
        if tokenset & _MCP_LOCAL_WRITE_VERBS:
            return RiskClass.WRITE_LOCAL
        if tokenset & _MCP_EXTERNAL_VERBS:
            return RiskClass.EXTERNAL
        # unknown MCP verb → fail-OPEN (READ). A missed gate is recoverable; a
        # false-positive prompt-storm is not (Gate-1 B3).
        return RiskClass.READ

    # 4. fail-OPEN default
    return RiskClass.READ


def is_unknown_mcp(tool_name: str) -> bool:
    """True for an MCP tool whose name carries no verb of the taxonomy: ``classify``
    lets it through as READ (fail-open), and a session that reserves external
    messages for a person treats it as EXTERNAL instead (design
    2026-10-28-project-agent-flow §8.1)."""
    tokens = _mcp_tokens(tool_name)
    if tokens is None:
        return False
    t = set(tokens)
    return not (t & _MCP_READ_VERBS or t & _MCP_LOCAL_WRITE_VERBS or t & _MCP_EXTERNAL_VERBS)


def is_external(tool_name: str, metadata: Any = None) -> bool:
    """Convenience: True iff the tool has an off-machine side effect (approval-gated)."""
    return classify(tool_name, metadata) is RiskClass.EXTERNAL
