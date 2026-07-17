"""Method-aware auth remediation text (single source of truth).

Both the spawn-time credential pre-flight (session_unit) and the health-driven
CredentialBanner surface "your credentials aren't working" to the user. The fix
instruction MUST match the user's actual auth method — the old code hardcoded
`mwinit -f`, an Amazon-internal Midway command, which is wrong (and useless) for:
  - external SSO users        → the fix is `aws sso login`
  - Anthropic-direct users    → the fix is entering an API key in Settings
  - Hive IAM-role users       → the fix is an IAM policy change

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


# Deep-link target for the in-app auth panel (Settings → AI & Models).
_SETTINGS_TAB = "ai-models"

_REMEDIATION: dict[str, Remediation] = {
    "ada": {
        "message": "Your AWS credentials aren't working.",
        "fix_text": (
            "Refresh your Amazon credentials: run `mwinit -f`, then "
            "`ada credentials update` in a terminal — or re-verify in "
            "Settings → AI & Models."
        ),
        "settings_tab": _SETTINGS_TAB,
    },
    "sso": {
        "message": "Your AWS credentials aren't working.",
        "fix_text": (
            "Refresh your AWS SSO session: run `aws sso login` in a terminal, "
            "then re-verify in Settings → AI & Models."
        ),
        "settings_tab": _SETTINGS_TAB,
    },
    "apikey": {
        "message": "No working Anthropic API key.",
        "fix_text": (
            "Enter or update your Anthropic API key in Settings → AI & Models, "
            "then send your message again."
        ),
        "settings_tab": _SETTINGS_TAB,
    },
    "iam_role": {
        "message": "The instance IAM role can't access Bedrock.",
        "fix_text": (
            "Add `bedrock:InvokeModel` to this instance's IAM role policy (and "
            "enable Bedrock model access for the region), then re-verify."
        ),
        "settings_tab": _SETTINGS_TAB,
    },
}

# Method-agnostic fallback — used when auth_method is unset/unknown. Deliberately
# does NOT name any provider-specific command (never hardcode mwinit here).
_FALLBACK: Remediation = {
    "message": "Your credentials aren't working.",
    "fix_text": (
        "Open Settings → AI & Models to check your authentication, then send "
        "your message again."
    ),
    "settings_tab": _SETTINGS_TAB,
}


def remediation_for(auth_method: Optional[str]) -> Remediation:
    """Return the remediation for an auth method.

    Accepts "ada" | "sso" | "apikey" | "iam_role"; anything else (None,
    unrecognized) returns the safe method-agnostic fallback. Never raises,
    never emits Amazon-internal jargon for a non-ADA method.
    """
    if auth_method and auth_method in _REMEDIATION:
        return _REMEDIATION[auth_method]
    return _FALLBACK
