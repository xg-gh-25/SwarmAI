"""The app session id grammar, and the short session key landings carry.

One rule, used wherever an app session id crosses a boundary (a pipeline run's
owner, the session-worktree stamp): ``[A-Za-z0-9_.:-]{1,128}``. It was duplicated in
``RunEngine._create`` and ``run_owner_gate``. It is not ``core.session_ids``, which
validates SDK transcript ids (a different identifier).

Public: ``valid_app_session_id``, ``session_key``, ``valid_turn_id``.
"""
from __future__ import annotations

import hashlib
import re

_APP_SID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_TURN_ID = re.compile(r"[0-9a-f]{32}")


def valid_app_session_id(value: object) -> bool:
    return isinstance(value, str) and _APP_SID.fullmatch(value) is not None


def session_key(session_id: str) -> str:
    """The 12-hex key a landing stamp names its session by (no session id in git)."""
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]


def valid_turn_id(value: object) -> bool:
    return isinstance(value, str) and _TURN_ID.fullmatch(value) is not None
