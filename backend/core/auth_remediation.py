"""Method-aware auth remediation text (single source of truth).

Both the spawn-time credential pre-flight (session_unit) and the health-driven
CredentialBanner surface "your credentials aren't working" to the user. The fix
instruction MUST match the user's actual auth method — the old code hardcoded
`mwinit -f`, an Amazon-internal Midway command, which is wrong (and useless) for:
  - external SSO users        → the fix is `aws sso login`
  - Hive IAM-role users       → the fix is an IAM policy change
  - Bedrock API-key users     → the fix is entering a new short-term bearer token

`use_bedrock` alone cannot tell ada from sso (both are Bedrock), so the active
method is persisted as `auth_method` in config and passed here.

This is the ONLY place remediation text is defined — consumers import it.
"""
from __future__ import annotations

from typing import Optional, TypedDict


class Remediation(TypedDict):
    message: str       # what's wrong (user-facing, one line)
    fix_text: str      # how to fix it, specific to the method
    settings_tab: str  # deep-link target so the UI can offer an in-app button
    # The one-click fix (design docs/design/2026-11-01-settings-account-merge.md §4.2):
    #   {"kind": "terminal", "command": "...", "title": "..."}: the app types the command
    #       into a new SwarmAI terminal (no Enter: the person runs it);
    #   {"kind": "sign_in"}: the account page runs the in-app sign-in again;
    #   {"kind": "settings", "tab": "..."}: open that settings tab.
    # Commands come only from the fixed strings here (or core.aws_account for a
    # profile name it validated); the frontend never builds one.
    action: dict


# Deep-link target for the model settings (Settings → LLM Models).
_SETTINGS_TAB = "ai-models"
# Settings → Swarm AWS Account (the one AWS account, core/aws_account.py).
_ACCOUNT_TAB = "aws-account"

_REMEDIATION: dict[str, Remediation] = {
    "ada": {
        "message": "Your AWS credentials aren't working.",
        # never a bare `ada credentials update`: with no --profile it overwrites the
        # person's default AWS profile
        "fix_text": (
            "Refresh your Amazon credentials: run `mwinit -f` in a terminal, then "
            "re-verify in Settings → Swarm AWS Account."
        ),
        "settings_tab": _ACCOUNT_TAB,
        "action": {"kind": "terminal", "command": "mwinit -f", "title": "Midway sign-in"},
    },
    # The SwarmAI account signs in with ADA (core/aws_account, source=ada): the daemon
    # runs ada itself, so a valid Midway is all it needs.
    "ada_account": {
        "message": "Your Midway sign-in has expired.",
        "fix_text": (
            "Run `mwinit -f` in a terminal, then send your message again "
            "(SwarmAI fetches fresh credentials itself)."
        ),
        "settings_tab": _ACCOUNT_TAB,
        "action": {"kind": "terminal", "command": "mwinit -f", "title": "Midway sign-in"},
    },
    # The SwarmAI account is a profile from ~/.aws (core/aws_account.py, source=profile).
    "profile": {
        "message": "Your AWS profile's sign-in has expired.",
        "fix_text": (
            "Sign in again for the AWS profile SwarmAI uses (for an SSO profile: "
            "`aws sso login --profile <name>`; the name is in Settings → SwarmAI "
            "Account), then send your message again."
        ),
        "settings_tab": _ACCOUNT_TAB,
        "action": {"kind": "settings", "tab": _ACCOUNT_TAB},
    },
    # The SwarmAI account signs in to AWS SSO inside the app (core/aws_signin).
    "sso_app": {
        "message": "Your AWS SSO sign-in has expired.",
        "fix_text": (
            "Sign in again in Settings → Swarm AWS Account (Sign in again), then send "
            "your message again."
        ),
        "settings_tab": _ACCOUNT_TAB,
        "action": {"kind": "sign_in"},
    },
    # The SwarmAI account uses AWS login through the AWS CLI (sessions last up to 12 hours).
    "aws_login": {
        "message": "Your AWS login session has ended.",
        "fix_text": (
            "Sign in again in Settings → Swarm AWS Account (AWS login sessions last at "
            "most 12 hours), then send your message again."
        ),
        "settings_tab": _ACCOUNT_TAB,
        "action": {"kind": "sign_in"},
    },
    "access_keys": {
        "message": "Your AWS access keys aren't working.",
        "fix_text": (
            "The access keys were deactivated or deleted. Enter new ones in Settings → "
            "Swarm AWS Account (Change account), then send your message again."
        ),
        "settings_tab": _ACCOUNT_TAB,
        "action": {"kind": "settings", "tab": _ACCOUNT_TAB},
    },
    # The SwarmAI account's record points at something gone or invalid (a deleted
    # profile, ada no longer installed, an invalid role).
    "account_broken": {
        "message": "SwarmAI can't sign in to its AWS account.",
        "fix_text": (
            "The sign-in it was set up with no longer works (see Settings → SwarmAI "
            "Account for the reason). Fix it there or in LLM Models, then send your "
            "message again."
        ),
        "settings_tab": _ACCOUNT_TAB,
        "action": {"kind": "settings", "tab": _ACCOUNT_TAB},
    },
    # The credentials resolved belong to another AWS account than the SwarmAI one.
    "account_mismatch": {
        "message": "Your AWS credentials belong to a different account than your SwarmAI account.",
        "fix_text": (
            "Fix the sign-in so it reaches the SwarmAI account, or check the account "
            "in Settings → Swarm AWS Account."
        ),
        "settings_tab": _ACCOUNT_TAB,
        "action": {"kind": "settings", "tab": _ACCOUNT_TAB},
    },
    "sso": {
        "message": "Your AWS credentials aren't working.",
        "fix_text": (
            "Refresh your AWS SSO session: run `aws sso login` in a terminal, "
            "then re-verify in Settings → Swarm AWS Account."
        ),
        "settings_tab": _ACCOUNT_TAB,
        "action": {"kind": "terminal", "command": "aws sso login", "title": "AWS SSO sign-in"},
    },
    "iam_role": {
        "message": "The instance IAM role can't access Bedrock.",
        "fix_text": (
            "Add `bedrock:InvokeModel` to this instance's IAM role policy (and "
            "enable Bedrock model access for the region), then re-verify."
        ),
        "settings_tab": _SETTINGS_TAB,
        "action": {"kind": "settings", "tab": _SETTINGS_TAB},
    },
    "bedrock_api_key": {
        "message": "Your Bedrock API key isn't working.",
        "fix_text": (
            "Your Bedrock bearer token has expired or is invalid. Generate a new "
            "short-term key (max 12h) and enter it in Settings → LLM Models, then "
            "send your message again."
        ),
        "settings_tab": _SETTINGS_TAB,
        "action": {"kind": "settings", "tab": _SETTINGS_TAB},
    },
}

# Method-agnostic fallback — used when auth_method is unset/unknown. Deliberately
# does NOT name any provider-specific command (never hardcode mwinit here).
_FALLBACK: Remediation = {
    "message": "Your credentials aren't working.",
    "fix_text": (
        "Connect an AWS account in Settings → Swarm AWS Account (or enter a Bedrock API "
        "key in Settings → LLM Models), then send your message again."
    ),
    "settings_tab": _ACCOUNT_TAB,
    "action": {"kind": "settings", "tab": _ACCOUNT_TAB},
}


def remediation_for(auth_method: Optional[str]) -> Remediation:
    """Return the remediation for an auth method.

    Accepts "ada" | "ada_account" | "sso" | "sso_app" | "aws_login" | "access_keys" | "profile" |
    "account_mismatch" | "iam_role" | "bedrock_api_key"; anything else (None,
    unrecognized) returns the safe method-agnostic fallback. Never raises,
    never emits Amazon-internal jargon for a non-ADA method.
    """
    if auth_method and auth_method in _REMEDIATION:
        return _REMEDIATION[auth_method]
    return _FALLBACK
