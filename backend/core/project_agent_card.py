"""Ask the person, in the chat, to confirm a Project Agent that widens the agent's authority.

Design: docs/design/2026-11-01-project-agent-owned-by-agent.md §4. The agent confirms
its Brain's definition itself when the authority part is no wider than the person's
floor (core/project_agent_review.agent_confirm). When it is wider, only the person
confirms; this card asks them where they already are, the way a Hive's wait is asked
(core/swarm_out_wait_card):

1. ``card(brain, session)`` lists what widened and builds the AskUserQuestion input,
   starting with ``[Project Agent · <brain> · <revision 8>]``, and keeps what it built
   (``state/project-agent/cards/<brain>.json``): the session, the revision, the shape's
   hash.
2. The agent calls AskUserQuestion with that input, unchanged.
3. ``forward(session, questions, answers)`` (ask_question_gate, a person's answer only)
   confirms as the person (``project_agent_review.confirm``, the form's own function,
   ``reviewed_by`` the login) when the card is the one built, in the session that built
   it, for the revision still current, and the pick is "Confirm". "Keep them for me"
   records that the person kept them; the agent undoes those changes. Anything else
   (free text, another session, an edited card, a changed definition) confirms nothing.

The agent never sees a confirm it could call itself: the person's pick is read by the
daemon (SOUL P5: a hash, a lookup, the person's recorded answer).

Public: ``MARKER_PREFIX``, ``card``, ``forward``, ``status``, ``CONFIRM``, ``KEEP``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

MARKER_PREFIX = "[Project Agent · "
_BRAIN_RE = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}"
_MARKER_RE = re.compile(r"^\[Project Agent · (" + _BRAIN_RE + r") · ([0-9a-f]{8})\]")
CONFIRM = "Confirm"
KEEP = "Keep them for me"
#: what one card lists before it says how many more
MAX_LINES = 12


class CardError(ValueError):
    """No card can be built; the message is safe to show."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _path(brain: str) -> Optional[Path]:
    if not re.fullmatch(_BRAIN_RE, brain or ""):
        return None
    from config import get_app_data_dir
    return Path(get_app_data_dir()) / "state" / "project-agent" / "cards" / f"{brain}.json"


def _load(brain: str) -> Optional[dict[str, Any]]:
    p = _path(brain)
    if p is None:
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _save(rec: dict[str, Any]) -> None:
    p = _path(rec["brain"])
    if p is None:
        raise CardError("not a Brain name")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.parent / f".{p.name}.{secrets.token_hex(6)}.tmp"
    tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _shape(questions: Any) -> str:
    """The hash of what a card asks (the wait card's rule: a field left out reads as its
    default)."""
    from core.swarm_out_wait_card import _shape as shape
    return shape(questions)


def _project_dir(brain: str) -> Path:
    from core.project_agent_session import PinError, brain_dir
    from core.project_registry import get_swarmws
    try:
        return brain_dir(get_swarmws(), brain)
    except PinError as e:
        raise CardError(str(e)) from None


def card(brain: str, session: Optional[str]) -> dict[str, Any]:
    """The AskUserQuestion input that asks the person to confirm what widened."""
    from core import project_agent_floor as fl
    from core.app_session_id import valid_app_session_id
    from core.project_agent_review import agent_confirm
    if not session or not valid_app_session_id(session):
        raise CardError("this command has no session id; run it as one plain command in your Bash tool")
    d = _project_dir(brain)
    result = agent_confirm(d, session=session, why="checked before asking the person")
    if result.get("status") != "widened":
        raise CardError({"already": "the definition is confirmed; there is nothing to ask",
                         "confirmed": "the agent confirmed it: nothing widened, there is nothing to ask",
                         "none": f"{brain} has no Project Agent definition"}.get(
            str(result.get("status")),
            "fix the definition first: " + "; ".join(str(p) for p in result.get("problems") or [])[:400]))
    rev = str(result["revision"])
    lines = [str(x) for x in result.get("widened") or []]
    shown = lines[:MAX_LINES] + ([f"and {len(lines) - MAX_LINES} more"] if len(lines) > MAX_LINES else [])
    set_by = fl.load(d).get("set_by")
    text = (f"[Project Agent · {brain} · {rev[:8]}] The {brain} Project Agent now gives the agent more than "
            f"{'you last confirmed' if set_by else 'the product default'}:\n"
            + "\n".join(f"- {x}" for x in shown)
            + "\nConfirm these, so the agent may do them without asking you?")
    questions = [{"question": text, "header": "Project Agent", "multiSelect": False, "options": [
        {"label": CONFIRM, "description": "confirm this revision as you; it becomes your new floor"},
        {"label": KEEP, "description": "the agent changes these back and confirms the rest itself"}]}]
    _save({"brain": brain, "revision": rev, "session": session, "shape": _shape(questions),
           "widened": lines, "created_at": _now(), "outcome": None})
    return {"questions": questions}


def _record(rec: Optional[dict[str, Any]], outcome: dict[str, Any]) -> dict[str, Any]:
    outcome = {**outcome, "at": _now()}
    if rec is not None and (rec.get("outcome") or {}).get("status") != "confirmed":
        rec["outcome"] = outcome
        try:
            _save(rec)
        except (OSError, CardError):
            logger.warning("project_agent_card: the outcome was not kept", exc_info=True)
    return outcome


async def forward(session: Optional[str], questions: Any, answers: Any) -> Optional[dict[str, Any]]:
    """Confirm as the person when the card and the pick say so (module docstring).
    None when the card is no Project Agent card. Never raises: the chat answer goes on
    either way. The caller has checked that the answer is the person's."""
    import asyncio
    try:
        return await asyncio.to_thread(_forward, session, questions, answers)
    except Exception:  # noqa: BLE001 — never break the chat's own answer path
        logger.warning("project_agent_card: forwarding failed", exc_info=True)
        return {"status": "not_confirmed", "reason": "forwarding failed", "at": _now()}


def _forward(session, questions, answers):
    if not isinstance(questions, list) or not questions:
        return None
    keys = {m.groups() for q in questions if isinstance(q, dict)
            for m in [_MARKER_RE.match(str(q.get("question", "")))] if m}
    if not keys:
        return None
    if len(keys) != 1 or len(questions) != 1:
        return _record(None, {"status": "not_confirmed", "reason": "one card, one question"})
    brain, rev8 = next(iter(keys))
    rec = _load(brain)
    if rec is None or not str(rec.get("revision") or "").startswith(rev8):
        return _record(None, {"status": "not_confirmed", "reason": "no card was built for this revision"})
    if (rec.get("outcome") or {}).get("status") == "confirmed":
        return _record(rec, {"status": "not_confirmed", "reason": "this card was already confirmed"})
    if not session or rec.get("session") != session:
        return _record(rec, {"status": "not_confirmed", "reason": "answered in another session"})
    if _shape(questions) != rec.get("shape"):
        return _record(rec, {"status": "not_confirmed", "reason": "the card asked is not the one built"})
    values = [v.strip() for v in (answers or {}).values() if isinstance(v, str)] if isinstance(answers, dict) else []
    pick = values[0] if len(values) == 1 else None
    if pick == KEEP:
        return _record(rec, {"status": "kept", "reason": "the person kept these for themselves"})
    if pick != CONFIRM:
        return _record(rec, {"status": "not_confirmed", "reason": "the answer is not one of the options"})
    import getpass

    from core.project_agent_review import ReviewError, confirm
    try:
        out = confirm(_project_dir(brain), seen_revision=str(rec["revision"]), reviewer=getpass.getuser())
    except (ReviewError, CardError) as e:
        return _record(rec, {"status": "not_confirmed", "reason": str(e)})
    logger.info("project_agent_card: the person confirmed %s at %s", brain, str(rec["revision"])[:8])
    return _record(rec, {"status": "confirmed", "revision": out.get("revision")})


def status(brain: str) -> Optional[dict[str, Any]]:
    """The last card's outcome for *brain*: confirmed / kept / not_confirmed (why),
    {"status": "asked"} while it waits, or None (no card)."""
    rec = _load(brain)
    if rec is None:
        return None
    return {**(rec.get("outcome") or {"status": "asked"}), "revision": rec.get("revision"),
            "widened": rec.get("widened")}
