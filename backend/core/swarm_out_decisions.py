"""Swarm Out, Hive side: the pipeline decisions waiting on a person, as one snapshot.

A pipeline v1 run that cannot decide alone records a DC-1 decision and waits
(``awaiting_human``). The run's own state is the truth about what waits, so this module
does not give a decision a lifecycle of its own (no ask / closed markers, no
reconcile). Each pass of the bridge it:

- reads every waiting decision from the runs' event logs
  (``pipeline_human.pending_decisions``). That read is all-or-nothing: if any run
  cannot be read, the pass changes nothing;
- writes the whole list, overwriting the last one, to ``bridge/<hive>/decisions.json``
  (only when it changed). The Host shows exactly that list, so a decision is shown while
  its run waits — through a turn that ended, a card that expired, a restart — and is gone
  once the run moves on;
- hides the run's own question card from the bridge's mirroring (``hidden``), so one
  decision is never shown as two;
- answers from ``bridge/<hive>/answer/<session>/<key>.json``. An answer must name one of
  the options and what the card showed (``shown_sha256`` = ``decision_digest``, computed
  by the Host over the snapshot entry it shows and here over the entry rebuilt from the
  run), so an answer cannot reach an option other than the one the person read. A
  decision with a field longer than the card shows is ``truncated`` and is not answered
  from the Host.
  - Card open: the card gets the option's exact label as a person's answer, and the ask
    gate records it on the run (``security_hooks`` → ``record_card_answer``). If the run
    still waits two passes later, the answer is recorded here.
  - No card: the answer is recorded here (``answer_from_dashboard(source="swarm_out")``),
    then the session gets the dashboard's continue message.
  A refused answer is listed in the snapshot as ``refused``.

Design: docs/design/2026-10-31-swarm-out-hive-decisions.md.

Public symbols:
- ``DecisionMirror`` — one Hive's decision state; ``Bridge.tick`` calls ``run_pass``.
- ``decision_key`` / ``decision_digest`` / ``DECISION_CAPS`` — shared with the Host
  (``core.swarm_out``), which must compute the same digest over the same caps.
- ``SNAPSHOT`` — the snapshot's name under ``bridge/<hive>/``.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

SNAPSHOT = "decisions.json"
KEY_PREFIX = "dc-"
MAX_DECISIONS = 50          # a snapshot lists at most this many (the Host reads it whole)
MAX_OPTION_ID_CHARS = 200
MAX_RECORD_TRIES = 3        # recording a pick that failed for a passing reason (lock, I/O)
MAX_CONTINUE_TRIES = 3      # sending the continue message when the session did not take it
# what a decision entry shows of each field (the Host keeps the same caps); a longer
# field marks the entry ``truncated``: a person never picks an option they could not
# read whole
DECISION_CAPS = {"decision_request_id": 200, "question": 4000, "why": 2000,
                 "choice": 500, "consequence": 1000}


def decision_key(run_id: str, session_id: str, owner_generation: int,
                 decision: dict[str, Any]) -> str:
    """The key of one waiting decision: the same for the same run, owner and decision
    (a restart lists it under the same key, so an answer written before still applies),
    different after a takeover, including one back to an earlier owner (each
    ``owner_transfer`` is a new generation)."""
    shown = {k: decision.get(k) for k in ("request_id", "kind", "question", "options")}
    digest = hashlib.sha256(json.dumps([run_id, session_id, owner_generation, shown],
                                       sort_keys=True, ensure_ascii=True,
                                       default=str).encode()).hexdigest()
    return KEY_PREFIX + digest[:32]


def decision_digest(rec: dict[str, Any]) -> str:
    """What a person picked from: the run, the decision and every option exactly as the
    card shows them. ``surrogatepass``: model text with a lone surrogate must hash."""
    shown = [rec.get("run_id", ""), rec.get("decision_request_id", ""), rec.get("question", ""),
             rec.get("why", ""),
             [[o.get("id"), o.get("choice"), o.get("consequence"), bool(o.get("reversible")),
               o.get("risk")] for o in rec.get("options") or [] if isinstance(o, dict)]]
    blob = json.dumps(shown, ensure_ascii=False).encode("utf-8", "surrogatepass")
    return hashlib.sha256(blob).hexdigest()


def _utc_stamp(at: Any) -> str:
    """An event time as ``%Y-%m-%dT%H:%M:%SZ`` (the bridge's ``asked_at``)."""
    from datetime import datetime, timezone
    try:
        t = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _entry(key: str, d: dict[str, Any]) -> dict[str, Any]:
    """One waiting decision as the Host's card shows it."""
    pending = d["decision"]
    caps = DECISION_CAPS
    full = {"decision_request_id": str(pending.get("request_id", "")),
            "question": str(pending.get("question") or ""),
            "why": str(pending.get("why_evidence_insufficient") or "")}
    opts = [o for o in pending.get("options") or [] if isinstance(o, dict)]
    cut = any(len(v) > caps[k] for k, v in full.items()) or any(
        len(str(o.get(k) or "")) > caps[k] for o in opts for k in ("choice", "consequence"))
    return {"kind": "decision", "session_id": str(d["session_id"]), "request_id": key,
            "asked_at": _utc_stamp(d.get("asked_at")),
            "run_id": str(d.get("run_id", ""))[:100], "project": str(d.get("project", ""))[:100],
            **{k: v[:caps[k]] for k, v in full.items()},
            "decision_kind": str(pending.get("kind") or "")[:60],
            "options": [{"id": str(o.get("id")), "choice": str(o.get("choice") or "")[:caps["choice"]],
                         "consequence": str(o.get("consequence") or "")[:caps["consequence"]],
                         "reversible": o.get("reversible") is not False,
                         "risk": "high" if o.get("risk") == "high" else "low"} for o in opts],
            "truncated": cut}


def _answerable(d: dict[str, Any], key_re) -> bool:
    """A decision the Host can list and answer: an owner session id that is a bridge key,
    and 2-3 options, each with an id short enough to be sent back whole."""
    opts = [o for o in d["decision"].get("options") or [] if isinstance(o, dict)]
    return bool(key_re.match(str(d.get("session_id") or ""))) and 2 <= len(opts) <= 3 and all(
        isinstance(o.get("id"), str) and 0 < len(o["id"]) <= MAX_OPTION_ID_CHARS for o in opts)


def _continue_message(d: dict[str, Any], option_id: str) -> str:
    """The message that hands an answered run back to its session: the dashboard's
    continue prompt and the option id, in fixed words. It does not speak for the person
    and carries none of the run-written text (the decision's question or option)."""
    from .pipeline_human import continue_prompt
    return (f"{continue_prompt(str(d.get('run_id')), str(d.get('project')))} "
            f"Its pending decision was answered from Swarm Out with option id "
            f"{json.dumps(option_id, ensure_ascii=True)}; the status shows the answer.")


class DecisionMirror:
    """One Hive's waiting decisions (module docstring). ``run_pass`` is called once per
    bridge pass on the event loop; its S3 and run reads go to a thread."""

    def __init__(self, hive: str, store, router: Callable[[], Any], key_re,
                 decisions: Optional[Callable[[], list]] = None,
                 record_decision: Optional[Callable[[str, str, str], Any]] = None):
        from . import pipeline_human
        self.hive = hive
        self.store = store
        self._router = router
        self._key_re = key_re
        self._decisions = decisions or pipeline_human.pending_decisions
        self._record = record_decision or (
            lambda run_id, request_id, option_id: pipeline_human.answer_from_dashboard(
                run_id, request_id, option_id, source="swarm_out"))
        self.prefix = f"bridge/{hive}/"
        self._items: dict[str, dict[str, Any]] = {}   # key -> the run's pending decision
        self._cards: dict[str, tuple[str, str]] = {}  # key -> (board key, card question)
        self._written: Optional[bytes] = None          # the snapshot last written
        self._delivered: set[str] = set()
        self._refused: set[str] = set()
        self._on_card: dict[str, int] = {}             # answered on the card: passes since
        self._no_card: set[str] = set()                # a card answer not taken: record here
        self._record_failures: dict[str, int] = {}
        self._continues: dict[str, tuple[str, str, int]] = {}   # key -> (session, message, tries)
        self._sending: set[str] = set()
        self._tasks: set[asyncio.Task] = set()

    async def run_pass(self, board: dict[str, tuple[str, dict[str, Any]]]) -> set[str]:
        """One pass. *board* is the bridge's open prompts by key, ``(session, item)``.
        Returns the board keys that are a waiting decision's own card (not mirrored as
        questions). When the runs cannot be read nothing changes and the cards known
        from the last readable pass stay hidden."""
        try:
            found = await asyncio.to_thread(self._decisions)
        except Exception as e:
            logger.warning("swarm_out_decisions: the waiting decisions could not be read (%s)",
                           type(e).__name__)
            found = None
        self._retry_continues()
        if found is None:
            return {bkey for bkey, _ in self._cards.values()}
        self._read(found, board)
        self._check_cards()
        for gone in [k for k in self._delivered | self._refused | self._no_card
                     | set(self._record_failures) if k not in self._items]:
            self._delivered.discard(gone)
            self._refused.discard(gone)
            self._no_card.discard(gone)
            self._record_failures.pop(gone, None)
        await self._deliver_answers()
        await self._write_snapshot()
        return {bkey for bkey, _ in self._cards.values()}

    def _read(self, found: list, board: dict[str, tuple[str, dict[str, Any]]]) -> None:
        from .run_protocol import decision as decision_protocol
        items: dict[str, dict[str, Any]] = {}
        cards: dict[str, tuple[str, str]] = {}
        for d in found:
            if not isinstance(d, dict) or not isinstance(d.get("decision"), dict):
                continue
            if not _answerable(d, self._key_re):
                logger.warning("swarm_out_decisions: the decision of %s cannot be listed for "
                               "the Host (its session or options)", d.get("run_id"))
                continue
            sid = str(d["session_id"])
            key = decision_key(str(d.get("run_id")), sid, int(d.get("owner_generation") or 0),
                               d["decision"])
            items[key] = d
            marker = decision_protocol._marker(str(d["decision"].get("request_id")))
            for bkey, (bsid, b) in board.items():
                if bsid != sid or b.get("kind") != "ask_user_question":
                    continue
                asked = next((str(q.get("question")) for q in b.get("questions") or []
                              if isinstance(q, dict)
                              and str(q.get("question", "")).rstrip().endswith(marker)), None)
                if asked is not None:
                    cards[key] = (bkey, asked)
        self._items, self._cards = items, cards

    def _check_cards(self) -> None:
        """A decision answered on its card that the run still waits on two passes later:
        the ask gate did not record it. The answer is given again, recorded here."""
        for key in list(self._on_card):
            if key not in self._items:
                del self._on_card[key]
                continue
            self._on_card[key] += 1
            if self._on_card[key] >= 2:
                logger.warning("swarm_out_decisions: %s was answered on its card but the run "
                               "still waits; recording it directly", key)
                del self._on_card[key]
                self._no_card.add(key)              # the card stays hidden meanwhile
                self._delivered.discard(key)

    async def _write_snapshot(self) -> None:
        entries = []
        for key, d in list(self._items.items())[:MAX_DECISIONS]:
            entry = _entry(key, d)
            entry["refused"] = key in self._refused
            entries.append(entry)
        if len(self._items) > MAX_DECISIONS:
            logger.warning("swarm_out_decisions: %d decisions wait; the Host is shown %d",
                           len(self._items), MAX_DECISIONS)
        # ASCII escapes: a lone surrogate in model text writes, and reads back the same
        body = json.dumps({"hive": self.hive, "decisions": entries}, ensure_ascii=True,
                          sort_keys=True).encode()
        if body != self._written:
            await asyncio.to_thread(self.store.put, self.prefix + SNAPSHOT, body)
            self._written = body

    def _valid(self, key: str, raw: bytes, entry: dict[str, Any]) -> Optional[str]:
        """The option to deliver, or None (the reason is logged)."""
        try:
            ans = json.loads(raw)
        except ValueError:
            ans = None
        option = ans.get("option_id") if isinstance(ans, dict) and ans.get("request_id") == key else None
        if not isinstance(option, str) or option not in {o["id"] for o in entry["options"]}:
            why = "names no option of the waiting decision"
        elif ans.get("shown_sha256") != decision_digest(entry):
            why = "was given for a card that does not show this decision"
        elif entry["truncated"]:
            why = "is for a decision too long to be shown whole"
        else:
            return option
        logger.warning("swarm_out_decisions: answer for %s %s; not delivered", key, why)
        return None

    async def _deliver_answers(self) -> None:
        waiting = [k for k in self._items if k not in self._delivered and k not in self._refused]
        if not waiting:
            return
        listed = set(await asyncio.to_thread(self.store.list_keys, f"{self.prefix}answer/"))
        for key in waiting:
            d = self._items[key]
            akey = f"{self.prefix}answer/{d['session_id']}/{key}.json"
            if akey not in listed:
                continue
            raw = await asyncio.to_thread(self.store.get, akey)
            if raw is None:
                continue
            option = self._valid(key, raw, _entry(key, d))
            if option is None:
                self._refused.add(key)
                continue
            self._delivered.add(key)
            self._spawn(self._answer(key, d, option), f"swarm-out-decision-{key}")

    def _spawn(self, coro, name: str) -> None:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _answer(self, key: str, d: dict[str, Any], option_id: str) -> None:
        from .run_protocol import decision as decision_protocol
        from .run_protocol.engine import EngineError
        sid = str(d["session_id"])
        option = next(o for o in d["decision"]["options"] if o.get("id") == option_id)
        card = None if key in self._no_card else self._cards.get(key)
        if card:
            card_key, asked = card
            ok = await self._drain(self._router().continue_with_answer(
                sid, json.dumps({asked: decision_protocol._label(option)}, ensure_ascii=False),
                tool_use_id=card_key, answered_by="person"))
            if ok:
                self._on_card[key] = 0
            else:
                self._delivered.discard(key)        # tried again next pass
            return
        try:
            await asyncio.to_thread(self._record, str(d.get("run_id")),
                                    str(d["decision"].get("request_id")), option_id)
        except EngineError as e:
            # the run refuses it: it moved on, or another answer came first
            logger.warning("swarm_out_decisions: the run refused %s (%s)", key, e)
            self._refused.add(key)
            return
        except Exception as e:
            tries = self._record_failures.get(key, 0) + 1
            self._record_failures[key] = tries
            logger.warning("swarm_out_decisions: %s not recorded (%s), try %d of %d",
                           key, type(e).__name__, tries, MAX_RECORD_TRIES)
            if tries >= MAX_RECORD_TRIES:
                self._refused.add(key)
            else:
                self._delivered.discard(key)
            return
        self._record_failures.pop(key, None)
        self._continues[key] = (sid, _continue_message(d, option_id), 0)
        await self._send_continue(key)

    async def _drain(self, gen) -> bool:
        """Read a continued turn to its end; False when it raised or reported an error."""
        ok = True
        try:
            async with contextlib.aclosing(gen) as events:
                async for ev in events:
                    if isinstance(ev, dict) and ev.get("type") == "error":
                        ok = False
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("swarm_out_decisions: a card answer failed (%s)", type(e).__name__)
            ok = False
        return ok

    def _retry_continues(self) -> None:
        for key, (_sid, _msg, tries) in list(self._continues.items()):
            if tries and key not in self._sending:
                self._spawn(self._send_continue(key), f"swarm-out-continue-{key}")

    async def _send_continue(self, key: str) -> None:
        """Send the continue message of a recorded decision. The router owns a message
        once it says ``accepted`` (it is then kept and delivered, whatever comes next),
        so that is the only sign looked for; until it comes the message is sent again on
        later passes under the same client id (a repeat is recognised as the same send),
        ``MAX_CONTINUE_TRIES`` in all."""
        if key in self._sending or key not in self._continues:
            return
        sid, message, tries = self._continues[key]
        self._sending.add(key)
        taken = False
        try:
            gen = self._router().run_conversation(agent_id="default", user_message=message,
                                                  session_id=sid, client_id=f"swarm-out-{key}")
            async with contextlib.aclosing(gen) as events:
                async for ev in events:
                    if isinstance(ev, dict) and ev.get("type") == "accepted":
                        taken = True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("swarm_out_decisions: continuing %s raised (%s)", key, type(e).__name__)
        finally:
            self._sending.discard(key)
        if taken:
            self._continues.pop(key, None)
            return
        tries += 1
        if tries >= MAX_CONTINUE_TRIES:
            self._continues.pop(key, None)
            # the answer is on the run; the session never took the message. The run no
            # longer shows as waiting on the Host; the person can send it a message
            logger.error("swarm_out_decisions: %s recorded, but session %s did not take the "
                         "continue message after %d tries", key, sid, tries)
        else:
            self._continues[key] = (sid, message, tries)
