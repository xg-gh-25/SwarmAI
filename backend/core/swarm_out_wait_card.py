"""A Hive's wait, seen and answered from the chat that dispatched the run.

Design docs/design/2026-10-31-swarm-out-agent-owns-its-runs.md §2.5, and who answers
which wait: docs/design/2026-10-31-hive-approvals-person-and-host.md (implementation
2026-11-01-hive-approvals-impl). The agent that dispatched the run answers for it: a wait
whose class is in ``approval_class.HOST_CLASSES`` (a question or decision about the task,
a command that only touches the Hive's own workspace, the run's own authorization) it
answers itself, from the card it built (``answer_as_host``: one of the card's options and
a reason, recorded as the host's, never the person's). It asks the person for the rest,
and whenever it is not sure (``escalate``, or the card in the chat). Asking the person:
the Host builds a question card and the daemon forwards the person's pick, the way a
pipeline decision card is recorded (core/security_hooks ask_question_gate):

1. ``card(dispatch_id, request_id)`` builds the AskUserQuestion input from the wait the
   Hive lists (a question, a pipeline decision, or a command approval), each question
   starting with ``[Hive · <dispatch> · <request>]``, and keeps what it built
   (``state/swarm-out/cards/<dispatch>/<request>.json``): the shape's hash, which card
   question is which Hive question, which option label is which option id / approve /
   deny, and the Hive's own ``shown_sha256``.
2. The agent calls AskUserQuestion with that input, unchanged.
3. The person answers in the App (``answered_by`` person; the chat answer route is
   refused from a command, core/run_owner_gate).
4. ``forward(session, questions, answers)`` (called by ask_question_gate) forwards when
   the card is one this module built for a run that *session* answers for, its shape is
   unchanged, and every answer maps (a decision or an approval takes one of its options,
   never free text). It answers through ``swarm_out.answer`` as the panel does, and keeps
   the outcome on the card (``forward``), which the run's follow fingerprint carries: a
   card not forwarded is news to the agent following the run.

No model decides anything here (SOUL P5): a hash, a lookup, the person's recorded pick,
or the dispatching agent's pick among the card's options for a class it answers for.

Public: ``MARKER_PREFIX``, ``card``, ``forward``, ``answer_as_host``, ``escalate``,
``escalation``, ``status``.
"""
from __future__ import annotations

import asyncio
import contextlib
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

MARKER_PREFIX = "[Hive · "
_MARKER_RE = re.compile(r"^\[Hive · (so_[0-9a-f]{12}) · ([A-Za-z0-9_.:-]{1,128})\]")
#: AskUserQuestion's own limits
MAX_QUESTIONS = 4
MIN_OPTIONS, MAX_OPTIONS = 2, 4
MAX_HEADER = 12
APPROVE, DENY = "Approve", "Deny"


class CardError(ValueError):
    """No card can be built for this wait, or the agent may not answer it; the message
    is safe to show. ``status``: the HTTP status a route returns for it (400 bad input or
    a Hive that refused, 403 not this session's or not its class, 409 already settled)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _who(cls: Any, escalated: bool) -> str:
    """Who answers a wait: ``you`` (the agent that dispatched the run) for a class it
    answers for that it has not escalated, else ``person``."""
    from core import approval_class
    return "you" if cls in approval_class.HOST_CLASSES and not escalated else "person"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _path(dispatch_id: str, request_id: str) -> Optional[Path]:
    from core import swarm_out, swarm_out_results
    if not swarm_out_results.DISPATCH_ID_RE.fullmatch(dispatch_id or "") \
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", request_id or ""):
        return None
    return swarm_out._state_dir() / "cards" / dispatch_id / f"{request_id}.json"



def _owner(rec: dict) -> Optional[str]:
    """Who answers this card's waits now: the dispatching session, or for a Swarm Run's
    child its lead run's current owner (core/swarm_run.owner_of)."""
    from core import swarm_run
    return swarm_run.owner_of({"parent_run": rec.get("parent_run"),
                               "dispatched_from": rec.get("dispatched_from")})

def _load(dispatch_id: str, request_id: str) -> Optional[dict[str, Any]]:
    p = _path(dispatch_id, request_id)
    if p is None:
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _save(rec: dict[str, Any]) -> None:
    p = _path(rec["dispatch_id"], rec["request_id"])
    if p is None:
        raise CardError("not a dispatch or a request id")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.parent / f".{p.name}.{secrets.token_hex(6)}.tmp"
    tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _shape(questions: Any) -> str:
    """The hash of what a card asks: each question's text, header, multiSelect and its
    options' labels and descriptions (a field the model leaves out reads as its
    default, so dropping ``multiSelect: false`` is not a change)."""
    out = []
    for q in questions if isinstance(questions, list) else []:
        if not isinstance(q, dict):
            out.append(None)
            continue
        out.append([str(q.get("question", "")), str(q.get("header", "")), bool(q.get("multiSelect")),
                    [[str(o.get("label", "")), str(o.get("description", ""))]
                     for o in (q.get("options") or []) if isinstance(o, dict)]])
    return hashlib.sha256(json.dumps(out, ensure_ascii=False).encode()).hexdigest()


def _marker(dispatch_id: str, request_id: str, title: str, hive: str) -> str:
    title = " ".join(str(title or "").split())[:80]
    return f"[Hive · {dispatch_id} · {request_id}] 「{title}」 on {hive} asks:"


def _risk(o: dict[str, Any]) -> str:
    tags = []
    if o.get("risk") == "high":
        tags.append("high risk")
    if o.get("reversible") is False:
        tags.append("cannot be undone")
    return ("[" + ", ".join(tags) + "] ") if tags else ""


async def card(dispatch_id: str, request_id: str) -> dict[str, Any]:
    """The AskUserQuestion input for one of the run's waits (module docstring)."""
    from core import swarm_out
    row = await swarm_out.run_row(dispatch_id)
    if not isinstance(row, dict) or not row.get("session_id"):
        raise CardError("no such run, or it has no session on its Hive")
    data = await swarm_out.list_runs(swarm_out.SNAPSHOT_FRESH_S)
    wait = next((w for w in data.get("asks") or [] if isinstance(w, dict)
                 and w.get("session_id") == row["session_id"] and w.get("request_id") == request_id), None)
    if wait is None or wait.get("phase", "waiting") != "waiting":
        raise CardError("that wait is not open on the run's Hive")
    marker = _marker(dispatch_id, request_id, row.get("title") or "", row.get("hive") or "the Hive")
    qmap: dict[str, str] = {}
    omap: dict[str, str] = {}
    labels: dict[str, list[str]] = {}
    kind = wait.get("kind")
    if kind == "question":
        hive_qs = [q for q in wait.get("questions") or [] if isinstance(q, dict)]
        if wait.get("omitted") or not hive_qs or len(hive_qs) > MAX_QUESTIONS or any(
                not (MIN_OPTIONS <= len(q.get("options") or []) <= MAX_OPTIONS) for q in hive_qs):
            raise CardError("this question does not fit a chat card (too many questions or options); "
                            "the person answers it in the Swarm Out panel")
        questions = []
        for q in hive_qs:
            text = f"{marker}\n{q['question']}"
            qmap[text] = q["question"]
            labels[text] = [o["label"] for o in q["options"]]
            questions.append({"question": text, "header": (str(q.get("header") or "Hive"))[:MAX_HEADER],
                              "multiSelect": bool(q.get("multiSelect")),
                              "options": [{"label": o["label"], "description": o.get("description", "")}
                                          for o in q["options"]]})
    elif kind == "decision":
        opts = wait.get("options") or []
        if wait.get("truncated") or not (MIN_OPTIONS <= len(opts) <= MAX_OPTIONS):
            raise CardError("this decision is too long to show whole; the person answers it on the "
                            "Hive's own page")
        text = f"{marker}\n{wait.get('question', '')}"
        if wait.get("why"):
            text += f"\nWhy it cannot be decided there: {wait['why']}"
        options = []
        for o in opts:
            label = f"{o['id']} — {o['choice']}"
            omap[label] = o["id"]
            options.append({"label": label, "description": _risk(o) + str(o.get("consequence", ""))})
        qmap[text] = ""
        questions = [{"question": text, "header": "Decision", "multiSelect": False, "options": options}]
    elif kind == "approval":
        if wait.get("truncated"):
            raise CardError("this command is too long to show whole; approve it on the Hive's own page, "
                            "or deny it in the Swarm Out panel")
        text = (f"{marker}\nThe run wants to run this command ({wait.get('tool_name', 'a tool')}):\n"
                f"{wait.get('command', '')}")
        if wait.get("reason"):
            text += f"\nWhy it needs approval: {wait['reason']}"
        omap = {APPROVE: "approve", DENY: "deny"}
        qmap[text] = ""
        questions = [{"question": text, "header": "Approval", "multiSelect": False,
                      "options": [{"label": APPROVE, "description": "the Hive runs the command"},
                                  {"label": DENY, "description": "the Hive does not run it"}]}]
    else:
        raise CardError("unknown kind of wait")
    cls = wait.get("approval_class")
    rec = {"dispatch_id": dispatch_id, "request_id": request_id, "kind": kind,
           "hive_id": row.get("hive_id"), "session_id": row["session_id"],
           "dispatched_from": row.get("dispatched_from"), "parent_run": row.get("parent_run"),
           "shape": _shape(questions),
           "questions": qmap, "options": omap, "hive_shown_sha256": wait.get("shown_sha256"),
           "approval_class": cls, "created_at": _now(), "forward": None,
           "labels": labels}
    old = _load(dispatch_id, request_id)
    if ((old or {}).get("forward") or {}).get("status") == "forwarded":
        raise CardError("an answer to this wait was already forwarded to the Hive")
    _save(rec)
    # who answers it: the dispatching agent for a class it answers for, else the person
    return {"questions": questions, "approval_class": cls,
            "who": _who(cls, bool(escalation(dispatch_id, request_id)))}


def _not(rec: Optional[dict[str, Any]], why: str) -> dict[str, Any]:
    out = {"status": "not_forwarded", "reason": why, "at": _now()}
    on_disk = _load(rec["dispatch_id"], rec["request_id"]) if rec is not None else None
    if rec is not None and ((on_disk or {}).get("forward") or {}).get("status") != "forwarded":
        # never over a forward that happened: the person's answer did reach the Hive
        rec["forward"] = out
        try:
            _save(rec)
        except (OSError, CardError):
            logger.warning("swarm_out_wait_card: outcome not kept", exc_info=True)
    logger.info("swarm_out_wait_card: not forwarded: %s", why)
    return out


async def forward(session: Optional[str], questions: Any, answers: Any) -> Optional[dict[str, Any]]:
    """Forward the person's answer on a Hive card to the Hive, or say why not. None when
    the card is no Hive card. Never raises: the chat answer goes on either way. The
    caller has checked that the answer is the person's."""
    try:
        return await _forward_once(session, questions, answers)
    except Exception:  # noqa: BLE001 — never break the chat's own answer path
        logger.warning("swarm_out_wait_card: forwarding failed", exc_info=True)
        return {"status": "not_forwarded", "reason": "forwarding failed", "at": _now()}


#: one lock per card while anyone holds or waits on it: [lock, holders and waiters]
_LOCKS: dict[tuple, list] = {}


@contextlib.asynccontextmanager
async def _card_lock(key: tuple):
    """One answer at a time per card (the person's forward and the host's answer
    alike): two answers at once cannot both pass the "already forwarded" check. The
    entry goes when the last holder or waiter leaves, so the map does not grow."""
    import asyncio
    entry = _LOCKS.setdefault(key, [asyncio.Lock(), 0])
    entry[1] += 1
    try:
        async with entry[0]:
            yield
    finally:
        entry[1] -= 1
        if entry[1] == 0 and _LOCKS.get(key) is entry:
            _LOCKS.pop(key, None)


async def _forward_once(session, questions, answers):
    keys = {m.groups() for q in (questions if isinstance(questions, list) else []) if isinstance(q, dict)
            for m in [_MARKER_RE.match(str(q.get("question", "")))] if m}
    key = next(iter(keys)) if len(keys) == 1 else ("", "")
    async with _card_lock(key):
        return await _forward(session, questions, answers)


async def _forward(session, questions, answers):
    if not isinstance(questions, list) or not questions:
        return None
    keys = {m.groups() for q in questions if isinstance(q, dict)
            for m in [_MARKER_RE.match(str(q.get("question", "")))] if m}
    if not keys:
        return None
    if len(keys) != 1:
        return _not(None, "one card asked about more than one wait")
    dispatch_id, request_id = next(iter(keys))
    rec = _load(dispatch_id, request_id)
    if rec is None:
        return _not(None, "no card was built for this wait (`swarm_out card`)")
    if (rec.get("forward") or {}).get("status") == "forwarded":
        return _not(None, "this wait was already forwarded")
    if not session or _owner(rec) != session:
        return _not(rec, "the card was answered in a session that does not answer for this run")
    if _shape(questions) != rec.get("shape"):
        return _not(rec, "the card asked is not the one built for this wait")
    if not isinstance(answers, dict):
        return _not(rec, "no answer")
    from core import swarm_out
    kwargs = _mapped(rec, answers, options_only=False)
    if isinstance(kwargs, str):
        return _not(rec, kwargs)
    try:
        await swarm_out.answer(rec["hive_id"], rec["session_id"], rec["request_id"], via="chat", **kwargs)
    except swarm_out.SwarmOutError as e:
        return _not(rec, str(e))
    rec["forward"] = {"status": "forwarded", "at": _now()}
    _save(rec)
    logger.info("swarm_out_wait_card: forwarded the person's answer on %s %s", dispatch_id, request_id)
    return rec["forward"]


def _mapped(rec: dict[str, Any], answers: Any, *, options_only: bool) -> "dict[str, Any] | str":
    """The ``swarm_out.answer`` arguments for *answers* given on the card *rec*, or why
    they do not map. *options_only*: every question's answer is one of its options'
    labels (the host never answers in free text, design §4 layer 2)."""
    if not isinstance(answers, dict):
        return "no answer"
    kwargs: dict[str, Any] = {}
    if rec["kind"] == "question":
        mapped = {}
        labels = rec.get("labels") or {}
        for text, hive_q in (rec.get("questions") or {}).items():
            v = answers.get(text)
            if not isinstance(v, str) or not v.strip():
                return "a question has no answer"
            if options_only and v.strip() not in (labels.get(text) or []):
                return "answer each question with one of its options"
            mapped[hive_q] = v.strip()
        kwargs["answers"] = mapped
        return kwargs
    values = [v for v in answers.values() if isinstance(v, str)]
    picked = (rec.get("options") or {}).get(values[0].strip()) if len(values) == 1 else None
    if picked is None:
        return "the answer is not one of the options (free text is not forwarded)"
    if rec["kind"] == "decision":
        kwargs["option_id"] = picked
    else:
        kwargs["decision"] = picked
    kwargs["shown_sha256"] = rec.get("hive_shown_sha256")
    return kwargs


async def answer_as_host(session: Optional[str], dispatch_id: str, request_id: str,
                         answers: Any, why: str, pick: Optional[str] = None) -> dict[str, Any]:
    """The agent that answers for the run answers one of its waits itself (module docstring):
    from the card this session built for it (what it saw: the Hive's ``shown_sha256`` and
    the option maps), only for a class it answers for, with one of the card's options and
    a reason. Written as the host's answer; kept on the card and in the run's commands,
    which the panel and the run's summary show. Raises CardError with a safe message."""
    from core import approval_class, swarm_out, swarm_out_actor
    why = " ".join(str(why or "").split())[:swarm_out_actor.MAX_WHY_CHARS]
    if not why:
        raise CardError("say why (--why): the person sees the reason for each wait you answer")
    rec = _load(dispatch_id, request_id)
    if rec is None:
        raise CardError("build the card first (`swarm_out card`): you answer what it shows")
    if (rec.get("forward") or {}).get("status") == "forwarded":
        raise CardError("this wait was already answered", 409)
    if not session or _owner(rec) != session:
        raise CardError("only the session that answers for this run may answer its waits", 403)
    cls = rec.get("approval_class")
    if cls not in approval_class.HOST_CLASSES:
        raise CardError(f"this wait is the person's to answer ({cls or 'its Hive does not say what it is about'}): "
                        "ask them with the card, or `swarm_out escalate`", 403)
    if escalation(dispatch_id, request_id):
        raise CardError("you escalated this wait: the person answers it", 409)
    if pick is not None:
        texts = list((rec.get("questions") or {}).keys())
        if len(texts) != 1:
            raise CardError("this card asks more than one question: give --answers-file")
        answers = {texts[0]: pick}
    kwargs = _mapped(rec, answers, options_only=True)
    if isinstance(kwargs, str):
        raise CardError(kwargs)
    async with _card_lock((dispatch_id, request_id)):
        rec = _load(dispatch_id, request_id) or rec
        if (rec.get("forward") or {}).get("status") == "forwarded":
            raise CardError("this wait was already answered", 409)
        try:
            await swarm_out.answer(rec["hive_id"], rec["session_id"], rec["request_id"],
                                   answered_by="host", **kwargs)
        except swarm_out.SwarmOutError as e:
            raise CardError(str(e)) from None
        # what the person reads afterwards: the card's own words (its first line is the
        # marker naming the run), and the label picked
        asked = "\n\n".join(t.split("\n", 1)[1] if "\n" in t else t
                             for t in (rec.get("questions") or {}).keys())
        pick = " · ".join(str(v).strip() for v in answers.values()) if isinstance(answers, dict) else ""
        rec["forward"] = {"status": "forwarded", "by": "host", "why": why, "at": _now()}
        _save(rec)
    swarm_out_actor.record_wait_answer(dispatch_id, session, request_id, cls, pick, why, asked=asked)
    logger.info("swarm_out_wait_card: the host answered %s %s (%s)", dispatch_id, request_id, cls)
    return rec["forward"]


def _esc_path(dispatch_id: str, request_id: str) -> Optional[Path]:
    p = _path(dispatch_id, request_id)
    return None if p is None else p.parent.parent.parent / "escalations" / dispatch_id / p.name


async def escalate(session: Optional[str], dispatch_id: str, request_id: str, why: str) -> dict[str, Any]:
    """The agent that answers for the run hands one of its waits to the person: it shows in
    Needs You as that session's, with the reason. Only that session may; once escalated,
    the agent no longer answers it (the person does)."""
    from core import swarm_out, swarm_out_actor
    why = " ".join(str(why or "").split())[:swarm_out_actor.MAX_WHY_CHARS]
    if not why:
        raise CardError("say why (--why): the person reads it in Needs You")
    p = _esc_path(dispatch_id, request_id)
    if p is None:
        raise CardError("not a dispatch or a request id")
    try:
        row = await swarm_out.run_row(dispatch_id)
    except swarm_out.SwarmOutError as e:
        raise CardError(str(e)) from None
    if not isinstance(row, dict):
        raise CardError("no such run")
    from core import swarm_run
    if not session or swarm_run.owner_of(row) != session:
        raise CardError("only the session that answers for this run escalates its waits", 403)
    try:
        data = await swarm_out.list_runs(swarm_out.SNAPSHOT_FRESH_S)
    except swarm_out.SwarmOutError as e:
        raise CardError(str(e)) from None
    if not any(isinstance(a, dict) and a.get("hive_id") == row.get("hive_id")
               and a.get("session_id") == row.get("session_id")
               and a.get("request_id") == request_id for a in data.get("asks") or []):
        raise CardError("this run has no open wait with that request id (`swarm_out watch`)")
    out = {"dispatch_id": dispatch_id, "request_id": request_id, "session": session,
           "why": why, "at": _now()}
    await asyncio.to_thread(_write_escalation, p, out)
    from core import swarm_out_record
    await asyncio.to_thread(swarm_out_record.append_quietly, dispatch_id, "agent", "escalate", why,
                            session, "escalate", {"request_id": request_id})
    logger.info("swarm_out_wait_card: %s %s escalated to the person", dispatch_id, request_id)
    return out


def _write_escalation(p: Path, out: dict[str, Any]) -> None:
    """Write the escalation record atomically (temp file + rename); run off the loop."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.parent / f".{p.name}.{secrets.token_hex(6)}.tmp"
    tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def escalation(dispatch_id: str, request_id: str) -> Optional[dict[str, Any]]:
    """The escalation of this wait (session, why, at), or None."""
    p = _esc_path(dispatch_id, request_id)
    if p is None:
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def status(dispatch_id: str, request_id: str) -> Optional[dict[str, Any]]:
    """The forward outcome of a card built for this wait: forwarded / not_forwarded
    (with why), {"status": "asked"} while it waits for the person, or None (no card)."""
    rec = _load(dispatch_id, request_id)
    if rec is None:
        return None
    return rec.get("forward") or {"status": "asked"}
