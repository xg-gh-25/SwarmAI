"""Slack-native owner approval — Block Kit Allow/Deny for new teammates.

The interaction layer of the Slack-native allowlist flow (run_6038cd2c). When an
unapproved sender first speaks in a *tracked* channel, the owner gets a DM with
Allow / Deny buttons; this module builds that card and resolves the button click.

Design (Gate-1-revised):
  * **State-based replay guard, NOT a crypto nonce.** slack_bolt's SocketModeHandler
    already authenticates that a block_actions payload genuinely came from Slack —
    so a signed nonce would be redundant (Gate-1 simplicity finding). Replay/double-
    click safety comes from STATE: the button value carries the pending-approval id;
    ``resolve_pending`` no-ops if that pending is already resolved or expired (TTL).
  * **Owner-only.** Only ``allowed_senders[0]`` (the owner) may act on a card; a
    click by anyone else is denied + audited, never mutates the allowlist.
  * **Fail-closed.** Unknown action_id, missing pending, expired pending, or a
    non-owner clicker → deny path. The allowlist is only ever *appended* to, via the
    gateway's single ``add_trusted_sender`` writer (which enforces the owner
    invariant + cache invalidation).

This module is PURE logic + a Block Kit builder — the gateway is injected, so it is
unit-testable without a live Slack connection.
"""

from __future__ import annotations

import time
from typing import Any, Optional

# Block Kit action ids — the adapter registers bolt_app.action() on these EXACT
# strings in BOTH socket-start sites (R27). An unknown action_id fails closed.
ACTION_ALLOW = "swarm_allow"
ACTION_DENY = "swarm_deny"
_KNOWN_ACTIONS = frozenset({ACTION_ALLOW, ACTION_DENY})

# A pending approval is only actionable for this long. After it, the button is a
# no-op (the owner must let the sender speak again to re-trigger). Bounds the
# pending set and prevents a stale card from granting access days later.
PENDING_TTL_SECONDS = 24 * 3600


def build_approval_blocks(
    *,
    sender_id: str,
    sender_display_name: str,
    pending_id: str,
    channel_label: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Build the owner-DM Block Kit card: context + Allow/Deny buttons.

    The button ``value`` carries ``"{pending_id}:{sender_id}"`` — the pending_id
    is the replay/dedup key (resolve_pending no-ops on an already-resolved id),
    the sender_id is what gets appended on Allow.
    """
    who = f"*{sender_display_name}* (`{sender_id}`)"
    where = f" in <#{channel_label}>" if channel_label else ""
    value = f"{pending_id}:{sender_id}"
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"👋 {who} wants to talk to me{where}.\nAdd them as a *trusted* teammate?",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ Allow"},
                    "style": "primary",
                    "action_id": ACTION_ALLOW,
                    "value": value,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "🚫 Deny"},
                    "style": "danger",
                    "action_id": ACTION_DENY,
                    "value": value,
                },
            ],
        },
    ]


def parse_action_value(value: str) -> tuple[str, str]:
    """Split a button value ``"{pending_id}:{sender_id}"`` → (pending_id, sender_id).

    sender_id may itself be empty; pending_id is everything before the FIRST colon
    (pending ids are colon-free uuids). Returns ("","") on a malformed value.
    """
    if not value or ":" not in value:
        return ("", "")
    pending_id, _, sender_id = value.partition(":")
    return (pending_id, sender_id)


def is_owner_click(channel_config: dict, clicker_id: str) -> bool:
    """Only the owner (allowed_senders[0]) may act on an approval card.

    Fail-closed on a degenerate owner: an empty-string / falsy ``allowed[0]`` is
    NOT a valid owner, so a blank clicker_id ("") can never satisfy ``"" == ""``
    and escalate (Gate-2 RANK-1). Both the owner slot AND the clicker must be
    non-empty and equal.
    """
    from channels.gateway import _parse_json_list  # local import avoids cycle
    allowed = _parse_json_list(channel_config.get("allowed_senders"))
    if not allowed or not allowed[0] or not clicker_id:
        return False
    return clicker_id == allowed[0]


def pending_is_actionable(pending: Optional[dict], *, now: Optional[float] = None) -> bool:
    """A pending entry is actionable iff it exists, is unresolved, and unexpired.

    This is the STATE-based replay guard: once ``status`` is set (approved/denied),
    a re-click is a no-op. Expiry bounds the window (PENDING_TTL_SECONDS).
    """
    if not pending:
        return False
    if pending.get("status") not in (None, "", "pending"):
        return False  # already resolved → replay, no-op
    created = pending.get("created_at")
    if created is not None:
        clock = now if now is not None else time.time()
        if clock - float(created) > PENDING_TTL_SECONDS:
            return False  # expired
    return True
