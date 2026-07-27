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

from enum import Enum
from typing import Any, Optional


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
      1. user-local override (Phase 2; always None today)
      2. built-in by-name tables (WRITE_LOCAL / EXEC / READ)
      3. MCP verb taxonomy (external verb → EXTERNAL, read/local verb → READ/WRITE_LOCAL)
      4. fail-OPEN default → READ

    `metadata` is accepted for forward-compat (openworker keys off a
    `requires_approval` flag) but SwarmAI MCP tools carry no such metadata today, so
    classification is name-based. If a metadata object ever exposes a truthy
    `requires_approval`, it is honored as EXTERNAL.
    """
    if overrides is not None:
        ov = overrides(tool_name) if callable(overrides) else None
        if ov is not None:
            return ov

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

    # forward-compat: honor an explicit metadata approval flag if present
    if metadata is not None and bool(getattr(metadata, "requires_approval", False)):
        return RiskClass.EXTERNAL

    # 4. fail-OPEN default
    return RiskClass.READ


def is_external(tool_name: str, metadata: Any = None) -> bool:
    """Convenience: True iff the tool has an off-machine side effect (approval-gated)."""
    return classify(tool_name, metadata) is RiskClass.EXTERNAL
