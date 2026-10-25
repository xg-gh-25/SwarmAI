"""turn_products — what a turn produced, as one record per product (design
docs/design/2026-10-26-session-products.md §2).

A product is something the agent made or did that outlives the turn: a file it wrote
that the emit gate classes as a product (content / knowledge / an external repo or
plain-FS file, including a session worktree), or a remote action from an explicit
table (a Slack post, an email, a task or ticket). Everything else is process.

The streaming orchestrator calls this at two points of one call:

- ``remote_candidate(tool_name, tool_input)`` at the ``tool_use``: the redacted label
  and target of a listed remote action (raw input is never kept), or None;
- ``blocks_for_result(tool_use_id, file_events, candidate, result_text, parent)`` at
  that call's SUCCESSFUL result: the ``turn_product`` blocks, inserted after it as one
  ``assistant`` event (the ``pipeline_stage`` pattern: same bytes streamed and stored).

Block keys are camelCase, as ``pipeline_stage`` blocks, so the frontend reads them
without a mapper. Pure, no I/O.

Public: ``PRODUCT_FILE_KINDS``, ``REMOTE_PRODUCTS``, ``BLOCK_TYPE``,
``remote_candidate``, ``remote_action``, ``blocks_for_result``, ``result_text``.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from core.shell_segments import command_tokens, git_subcommand_index, split_segments

BLOCK_TYPE = "turn_product"

# The emit gate's kinds that are products (the router's turn_deliverables reads
# these too). ``source`` (code in a bound repo / run worktree) is not: code changes
# live on the card's changes section and the landing row.
PRODUCT_FILE_KINDS = frozenset({"content", "knowledge", "external-diff", "external-nodiff"})

_TARGET_MAX = 120

# Exact (server, tool) → product spec. ``action``: the row's label; ``target``: input
# fields, the first non-empty string one names what was acted on; ``by_action``: for
# a dispatch tool, the exact ``tool_input.action`` values that are products. A tool
# or action not listed is not a product (unknown = left off). One row + a test to add.
REMOTE_PRODUCTS: dict[tuple[str, str], dict] = {
    ("slack-mcp", "post_message"): {"action": "Posted to Slack", "target": ("channel",)},
    ("slack-mcp", "post_block_message"): {"action": "Posted to Slack", "target": ("channel",)},
    ("slack-mcp", "bulk_post_message"): {"action": "Posted to Slack", "target": ("channels",)},
    ("slack-mcp", "schedule_message"): {"action": "Scheduled a Slack message", "target": ("channel",)},
    ("slack-mcp", "schedule_block_message"): {"action": "Scheduled a Slack message", "target": ("channel",)},
    ("slack-mcp", "create_canvas"): {"action": "Created a Slack canvas", "target": ("title",)},
    ("slack-mcp", "upload_file"): {"action": "Uploaded to Slack", "target": ("filename", "channel")},
    ("aws-outlook-mcp", "email_send"): {"action": "Sent an email", "target": ("subject", "to")},
    ("aws-outlook-mcp", "email_reply"): {"action": "Replied to an email", "target": ("subject",)},
    ("aws-outlook-mcp", "email_forward"): {"action": "Forwarded an email", "target": ("subject", "to")},
    ("builder-mcp", "TaskeiCreateTask"): {"action": "Created a task", "target": ("name", "title")},
    ("builder-mcp", "TaskeiUpdateTask"): {"action": "Updated a task", "target": ("taskId", "id")},
    ("builder-mcp", "SimAddComment"): {"action": "Commented on an issue", "target": ("issueId", "id")},
    ("builder-mcp", "TicketingWriteActions"): {"by_action": {
        "create-ticket": {"action": "Created a ticket", "target": ("title",)},
        "update-ticket": {"action": "Updated a ticket", "target": ("ticketId",)},
        "add-comment": {"action": "Commented on a ticket", "target": ("ticketId",)},
    }},
}

_PR_URL = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+/pull/\d+$")


def _redact(text: str) -> str:
    from core.tool_summarizer import redact_display
    return redact_display(text, _TARGET_MAX)


def _target(tool_input: Any, fields: tuple) -> str:
    if not isinstance(tool_input, dict):
        return ""
    for f in fields:
        v = tool_input.get(f)
        if isinstance(v, list):
            v = ", ".join(x for x in v if isinstance(x, str))
        if isinstance(v, str) and v.strip():
            return _redact(v.strip().splitlines()[0])
    return ""


def remote_candidate(tool_name: str, tool_input: Any) -> Optional[dict]:
    """``{action, target, tool}`` for a listed remote action, else None. Bash goes
    through ``remote_action``."""
    if tool_name == "Bash":
        cmd = tool_input.get("command") if isinstance(tool_input, dict) else None
        return remote_action(cmd) if isinstance(cmd, str) else None
    parts = tool_name.split("__")
    if len(parts) != 3 or parts[0] != "mcp":
        return None
    spec = REMOTE_PRODUCTS.get((parts[1], parts[2]))
    if spec is None:
        return None
    if "by_action" in spec:
        act = tool_input.get("action") if isinstance(tool_input, dict) else None
        spec = spec["by_action"].get(act) if isinstance(act, str) else None
        if spec is None:
            return None
    return {"tool": tool_name, "action": spec["action"], "target": _target(tool_input, spec["target"])}


def remote_action(command: str) -> Optional[dict]:
    """The remote product a Bash command makes: ``git push``, ``gh pr create|merge``,
    ``gh issue create``, ``gh release create``, split the way the C041 gate splits
    (core/shell_segments). The first such segment counts; None when there is none or
    the command cannot be parsed."""
    segments = split_segments(command)
    for seg in segments or []:
        t = command_tokens(seg)
        if not t:
            continue
        if t[0] == "git":
            k = git_subcommand_index(t)
            if k < len(t) and t[k] == "push":
                rest = [x for x in t[k + 1:] if not x.startswith("-")]
                return {"tool": "Bash", "action": "Pushed", "target": _redact(" ".join(rest))}
        if t[0] == "gh" and len(t) >= 3:
            label = {("pr", "create"): "Opened a pull request", ("pr", "merge"): "Merged a pull request",
                     ("issue", "create"): "Opened an issue",
                     ("release", "create"): "Created a release"}.get((t[1], t[2]))
            if label:
                return {"tool": "Bash", "action": label, "target": _redact(" ".join(t[3:4]))}
    return None


def result_text(content: Any) -> str:
    """The text of a tool result's content (a string or the SDK's block list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for b in content:
            t = b.get("text") if isinstance(b, dict) else getattr(b, "text", None)
            if isinstance(t, str):
                out.append(t)
        return "\n".join(out)
    return ""


def _url(candidate: dict, text: str) -> Optional[str]:
    # Only where the tool's own output format names it: gh pr create prints the new
    # PR's URL as its last line. Nothing is scraped from other output.
    if candidate.get("action") == "Opened a pull request":
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if lines and _PR_URL.match(lines[-1]):
            return lines[-1]
    return None


def blocks_for_result(tool_use_id: str, file_events: list[dict], candidate: Optional[dict],
                      result: Any = None, parent: Optional[str] = None) -> list[dict]:
    """The ``turn_product`` blocks one SUCCESSFUL call made: one per written product
    file (in event order, one per path), plus its remote action if it was listed."""
    out: list[dict] = []
    seen: set[str] = set()
    base = {"type": BLOCK_TYPE, "toolUseId": tool_use_id, **({"parentToolUseId": parent} if parent else {})}
    for ev in file_events:
        if ev.get("type") != "file_changed" or ev.get("operation") != "written":
            continue
        ap = ev.get("absolutePath")
        if ev.get("kind") not in PRODUCT_FILE_KINDS or not isinstance(ap, str) or not ap or ap in seen:
            continue
        seen.add(ap)
        out.append({**base, "product": "file", "path": str(ev.get("path") or ap), "absolutePath": ap,
                    "fileKind": ev["kind"]})
    if candidate:
        blk = {**base, "product": "remote", "tool": candidate["tool"], "action": candidate["action"],
               "target": candidate.get("target") or ""}
        url = _url(candidate, result_text(result))
        if url:
            blk["url"] = url
        out.append(blk)
    return out
