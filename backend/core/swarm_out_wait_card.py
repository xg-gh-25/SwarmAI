"""A Hive's wait, seen and answered from the chat that dispatched the run.

Design docs/design/2026-10-31-swarm-out-agent-owns-its-runs.md §2.5, and who answers
which wait: docs/design/2026-10-31-hive-approvals-person-and-host.md (implementation
2026-11-01-hive-approvals-impl). The agent that dispatched the run answers for it: a wait
whose class is in ``approval_class.HOST_CLASSES`` (a question or decision about the task,
a command that only touches the Hive's own workspace, the run's own authorization) it
answers itself, from the card it built (``answer_as_host``: one of the card's options and
a reason, recorded as the host's, never the person's). It is not bound by a chat card's
limits: ``view`` shows it the whole wait (every question and option), and
``answer_as_host`` takes one option per question from what ``view`` (or a card) showed.
Only what the Host cannot answer at all (``host_answerable``: questions the Hive left out,
a question with no option, a decision too long to show) and the classes it does not
answer go to the person. It asks the person for the rest, and whenever it is not sure
(``escalate``, or the card in the chat). Asking the person:
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

Public: ``MARKER_PREFIX``, ``card``, ``view``, ``forward``, ``answer_as_host``, ``escalate``,
``escalation``, ``status``, ``host_answerable``, ``agent_answers``.
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


def _who(cls: Any, escalated: bool, answerable: bool = True) -> str:
    """Who answers a wait: ``you`` (the agent that answers for the run) for a class it
    answers for that it has not escalated and that the Host can answer
    (``host_answerable``), else ``person``."""
    from core import approval_class
    return "you" if cls in approval_class.HOST_CLASSES and not escalated and answerable else "person"


def host_answerable(wait: Any) -> Optional[str]:
    """Why the agent that answers for the run cannot answer this wait from this Host,
    else None. Not a chat card's limits (those bound only ``card``): the agent answers
    any number of questions with ``swarm_out answer``. What it cannot do is answer what
    the Host does not hold whole or would need free text for: a question set the Hive
    left questions or options out of, a question with no option to pick (the host never
    answers in free text, design §4 layer 2), a decision too long to show whole. An
    approval can always be answered: a command cut short can still be denied. Only what the
    wait itself shows refuses (a wait the Hive lists always has its kind and fields)."""
    if not isinstance(wait, dict):
        return "the wait cannot be read"
    kind = wait.get("kind")
    if kind == "question":
        if wait.get("omitted"):
            return "its Hive left some of its questions or options out, so it cannot be answered whole here"
        if any(isinstance(q, dict) and not (q.get("options") or []) for q in wait.get("questions") or []):
            return "a question has no option to pick, and a host answer is never free text"
        return None
    if kind == "decision" and wait.get("truncated"):
        return "the decision is too long to show whole here; it is answered on the Hive's own page"
    # an approval can always be denied; anything else is refused, if at all, by the
    # answer itself (the agent then escalates it)
    return None


def agent_answers(dispatch_id: str, wait: Any) -> bool:
    """The agent that answers for the run answers this wait itself: a class it answers
    for, not escalated, and answerable from the Host (``host_answerable``). The one rule
    Needs You (core/attention_authority), the wake (core/swarm_out_record.actionable)
    and the follow summary read."""
    from core import approval_class
    if not isinstance(wait, dict) or wait.get("approval_class") not in approval_class.HOST_CLASSES:
        return False
    if escalation(str(dispatch_id or ""), str(wait.get("request_id") or "")):
        return False
    return host_answerable(wait) is None


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
                               "dispatched_from": rec.get("dispatched_from"),
                               "dispatch_id": rec.get("dispatch_id")})

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
    return f'[Hive · {dispatch_id} · {request_id}] "{title}" on {hive} asks:'


def _risk(o: dict[str, Any]) -> str:
    tags = []
    if o.get("risk") == "high":
        tags.append("high risk")
    if o.get("reversible") is False:
        tags.append("cannot be undone")
    return ("[" + ", ".join(tags) + "] ") if tags else ""


async def _open_wait(dispatch_id: str, request_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """(the run's row, its open wait *request_id* as the Hive lists it); CardError else."""
    from core import swarm_out
    row = await swarm_out.run_row(dispatch_id)
    if not isinstance(row, dict) or not row.get("session_id"):
        raise CardError("no such run, or it has no session on its Hive")
    data = await swarm_out.list_runs(swarm_out.SNAPSHOT_FRESH_S)
    wait = next((w for w in data.get("asks") or [] if isinstance(w, dict)
                 and w.get("session_id") == row["session_id"] and w.get("request_id") == request_id), None)
    if wait is None or wait.get("phase", "waiting") != "waiting":
        raise CardError("that wait is not open on the run's Hive")
    return row, wait


#: what the agent is told when a wait does not fit a chat card but is its own to answer
_NOT_A_CARD = ("read it whole with `swarm_out wait` and, when it is yours (`who: you`), answer it "
               "with `swarm_out answer`; otherwise the person answers it in the Swarm Out panel")


async def card(dispatch_id: str, request_id: str) -> dict[str, Any]:
    """The AskUserQuestion input for one of the run's waits (module docstring)."""
    row, wait = await _open_wait(dispatch_id, request_id)
    marker = _marker(dispatch_id, request_id, row.get("title") or "", row.get("hive") or "the Hive")
    qmap: dict[str, str] = {}
    omap: dict[str, str] = {}
    labels: dict[str, list[str]] = {}
    kind = wait.get("kind")
    if kind == "question":
        hive_qs = [q for q in wait.get("questions") or [] if isinstance(q, dict)]
        if wait.get("omitted") or not hive_qs or len(hive_qs) > MAX_QUESTIONS or any(
                not (MIN_OPTIONS <= len(q.get("options") or []) <= MAX_OPTIONS) for q in hive_qs):
            raise CardError("this question does not fit a chat card (too many questions or options): "
                            + _NOT_A_CARD)
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
            raise CardError("this decision does not fit a chat card (too long, or not 2-4 options): "
                            + _NOT_A_CARD)
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
            raise CardError("this command is too long to show whole on a chat card: " + _NOT_A_CARD
                            + " (cut short, it can only be denied here; when it should run, `swarm_out escalate` it)")
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
    if isinstance((old or {}).get("view"), dict):
        rec["view"] = old["view"]                  # what ``view`` showed the agent stays
    _save(rec)
    # who answers it: the dispatching agent for a class it answers for, else the person
    return {"questions": questions, "approval_class": cls,
            "who": _who(cls, bool(escalation(dispatch_id, request_id)), host_answerable(wait) is None)}


def _view_of(wait: dict[str, Any]) -> dict[str, Any]:
    """What the agent answers a wait from (``answer_as_host``), in the shape ``_mapped``
    reads: each Hive question -> itself and its option labels, a decision's labels and
    ids -> its option id, Approve / Deny, and the Hive's ``shown_sha256``."""
    kind = wait.get("kind")
    out: dict[str, Any] = {"kind": kind, "questions": {}, "labels": {}, "options": {}, "order": [],
                           "hive_shown_sha256": wait.get("shown_sha256")}
    if kind == "question":
        for q in wait.get("questions") or []:
            if isinstance(q, dict) and isinstance(q.get("question"), str):
                out["questions"][q["question"]] = q["question"]
                out["labels"][q["question"]] = [str(o.get("label", "")) for o in q.get("options") or []
                                                if isinstance(o, dict)]
                out["order"].append(q["question"])
    elif kind == "decision":
        out["questions"] = {str(wait.get("question", "")): ""}
        for o in wait.get("options") or []:
            if isinstance(o, dict) and isinstance(o.get("id"), str):
                out["options"][f"{o['id']} — {o.get('choice', '')}"] = o["id"]
                out["options"][o["id"]] = o["id"]
    elif kind == "approval":
        out["questions"] = {str(wait.get("command", "")): ""}
        out["options"] = {APPROVE: "approve", DENY: "deny", "approve": "approve", "deny": "deny"}
    return out


async def view(dispatch_id: str, request_id: str) -> dict[str, Any]:
    """One of the run's waits whole, as the Hive lists it (every question and option, a
    decision's options, an approval's command): what the agent reads before it answers
    (``answer_as_host`` answers from what this showed). Kept on the wait's card record
    (``view``) without touching a chat card built for it. ``who`` says who answers it
    and ``why_not`` why the Host cannot (``host_answerable``)."""
    row, wait = await _open_wait(dispatch_id, request_id)
    rec = _load(dispatch_id, request_id) or {
        "dispatch_id": dispatch_id, "request_id": request_id, "kind": wait.get("kind"),
        "hive_id": row.get("hive_id"), "session_id": row["session_id"],
        "dispatched_from": row.get("dispatched_from"), "parent_run": row.get("parent_run"),
        "shape": None, "questions": {}, "options": {}, "labels": {}, "hive_shown_sha256": None,
        "approval_class": wait.get("approval_class"), "created_at": _now(), "forward": None}
    if (rec.get("forward") or {}).get("status") == "forwarded":
        raise CardError("an answer to this wait was already forwarded to the Hive", 409)
    rec["view"] = {**_view_of(wait), "approval_class": wait.get("approval_class"),
                   "hive_id": row.get("hive_id"), "session_id": row["session_id"], "at": _now(),
                   # why the Host cannot answer it (``host_answerable``): answer_as_host refuses then
                   "cannot": host_answerable(wait),
                   # an approval shown cut short: it can only be denied
                   "truncated": bool(wait.get("truncated"))}
    _save(rec)
    cls = wait.get("approval_class")
    why_not = host_answerable(wait)
    shown = {k: wait.get(k) for k in ("kind", "approval_class", "questions", "omitted", "question", "why",
                                      "options", "truncated", "tool_name", "command", "reason")
             if wait.get(k) is not None}
    return {"dispatch_id": dispatch_id, "request_id": request_id, "wait": shown,
            "who": _who(cls, bool(escalation(dispatch_id, request_id)), why_not is None),
            "why_not": why_not,
            "answer_with": ("`swarm_out answer <dispatch> <request> --pick <label>` (one question, a "
                            "decision's option id, approve / deny) or `--answers-file <json>`: a list of "
                            "one option label per question in order, or question -> label")}


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
        raise CardError("read the wait first (`swarm_out wait`, or `swarm_out card`): you answer what it shows")
    if (rec.get("forward") or {}).get("status") == "forwarded":
        raise CardError("this wait was already answered", 409)
    if not session or _owner(rec) != session:
        raise CardError("only the session that answers for this run may answer its waits", 403)
    # what the agent read: the whole wait (``view``) when it read it so, else the card
    shown = rec["view"] if isinstance(rec.get("view"), dict) else rec
    cls = rec.get("approval_class")
    if cls not in approval_class.HOST_CLASSES:
        raise CardError(f"this wait is the person's to answer ({cls or 'its Hive does not say what it is about'}): "
                        "ask them with the card, or `swarm_out escalate`", 403)
    if escalation(dispatch_id, request_id):
        raise CardError("you escalated this wait: the person answers it", 409)
    if shown.get("cannot"):
        # what Needs You hands the person (``agent_answers``) is never answered here too
        raise CardError(f"this wait cannot be answered from this host ({shown['cannot']}): "
                        "the person answers it", 403)
    if pick is not None:
        texts = list((shown.get("questions") or {}).keys())
        if len(texts) != 1:
            raise CardError("this wait asks more than one question: give --answers-file")
        answers = {texts[0]: pick}
    answers = _keyed(shown, answers)
    if isinstance(answers, str):
        raise CardError(answers)
    kwargs = _mapped(shown, answers, options_only=True)
    if isinstance(kwargs, str):
        raise CardError(kwargs)
    if shown.get("kind") == "approval" and shown.get("truncated") and kwargs.get("decision") != "deny":
        raise CardError("this command was shown cut short: you may only deny it; when it should run, hand "
                        "the wait to the person with `swarm_out escalate` (they approve it on the Hive's own page)",
                        403)
    async with _card_lock((dispatch_id, request_id)):
        rec = _load(dispatch_id, request_id) or rec
        if (rec.get("forward") or {}).get("status") == "forwarded":
            raise CardError("this wait was already answered", 409)
        try:
            await swarm_out.answer(rec["hive_id"], rec["session_id"], rec["request_id"],
                                   answered_by="host", **kwargs)
        except swarm_out.SwarmOutError as e:
            raise CardError(str(e)) from None
        # what the person reads afterwards: the words the agent answered (a card's first
        # line is the marker naming the run), and the label picked
        asked = "\n\n".join(_unmarked(t) for t in (shown.get("questions") or {}).keys())
        pick = " · ".join(str(v).strip() for v in answers.values()) if isinstance(answers, dict) else ""
        rec["forward"] = {"status": "forwarded", "by": "host", "why": why, "at": _now()}
        _save(rec)
    swarm_out_actor.record_wait_answer(dispatch_id, session, request_id, cls, pick, why, asked=asked)
    logger.info("swarm_out_wait_card: the host answered %s %s (%s)", dispatch_id, request_id, cls)
    return rec["forward"]


def _unmarked(text: str) -> str:
    """A card question's text without its first line, the marker naming the run."""
    return text.split("\n", 1)[1] if _MARKER_RE.match(text) and "\n" in text else text


def _keyed(shown: dict[str, Any], answers: Any) -> "dict[str, Any] | str":
    """*answers* keyed by the questions of *shown* (``_mapped``'s keys): a list gives one
    answer per question in the wait's order; a card question's text (marker line first)
    counts as the Hive question under it."""
    texts = list((shown.get("questions") or {}).keys())
    if isinstance(answers, list):
        order = shown.get("order") or texts
        if len(answers) != len(order):
            return f"give one answer per question ({len(order)}), in order"
        return dict(zip(order, answers))
    if not isinstance(answers, dict):
        return "no answer"
    if all(k in texts for k in answers):
        return answers
    out = {}
    for k, v in answers.items():
        k2 = k if k in texts else _unmarked(str(k))
        if k2 not in texts:
            return "an answer names a question this wait does not ask"
        out[k2] = v
    return out


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
