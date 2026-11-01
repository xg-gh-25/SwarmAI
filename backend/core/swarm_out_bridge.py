"""Swarm Out bridge, Hive side: a person answers this Hive's open prompts through S3.

A Hive runs dispatched tasks with nobody watching. When a session needs a person —
an AskUserQuestion, or a dangerous-command approval — the prompt sits on the
session's prompt board (``permission_manager``) and the hook waits, exactly as it
does on a desktop. This module does not add a second way to wait. It only:

- mirrors every open prompt on the board to
  ``s3://<bucket>/bridge/<hive>/ask/<session>/<key>.json``;
- writes ``bridge/<hive>/closed/<session>/<key>.json`` once a mirrored prompt is no
  longer open, so the Host stops showing it: with ``why: delivered`` when the session
  took the answer this bridge handed over (the Host's only sign its answer was used;
  never written over), plainly when it left otherwise (answered on the Hive's own web
  page, expired, the session ended); and while it is still open, with ``why:
  answer_refused`` and a ``reason`` code (``no_option``, ``changed``, ``too_long``)
  when an answer does not match the prompt still waiting (design
  2026-10-31-swarm-out-single-loop-fixes §2.1);
- hands each pass to ``core.swarm_out_decisions``, which lists the pipeline runs
  waiting on a person's decision in ``bridge/<hive>/decisions.json`` and answers them;
  a run's own question card for such a decision is not mirrored here (one decision is
  never shown as two);
- reads ``bridge/<hive>/answer/<session>/<key>.json`` (written only by the Host,
  which holds the account credentials; the Hive role can read but not write there)
  and delivers a valid answer the way the web page does. An approval must name what
  the person was shown (``shown_sha256``, ``approval_digest``) and must not be for a
  command too long to show whole; a deny is always delivered; a question's answer must
  answer the questions still waiting. Delivery goes through the session
  router's ``continue_with_answer`` / ``continue_with_cmd_permission``, which unblock
  the waiting hook AND read the rest of the turn (the unit stopped reading when it
  went WAITING_INPUT); on start, asks left from before a restart are marked closed.

Design: docs/design/2026-10-27-swarm-out-v0-dispatch.md §3.3. Communication is not
real time: one pass every ``POLL_SECONDS``.

Identity is read from the instance itself and cannot be chosen by anything running
on it: the Hive name is the suffix of the instance-profile role
``SwarmAI-Hive-<name>`` (IMDSv2), the bucket is the deploy-time ``.hive-bucket``
file. The IAM role scopes every key to ``bridge/${aws:PrincipalTag/HiveName}/``.

Public symbols:
- ``run_bridge_loop`` — the background task started in hive mode (``main.py``).
- ``Bridge`` — ``reconcile`` at start, then one pass (``tick``) at a time; tests drive it
  with a fake store and a fake router.
- ``resolve_identity`` — (hive_name, bucket, region) from IMDS, or None
  (``identity_from`` checks the parts).
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Protocol

logger = logging.getLogger(__name__)

POLL_SECONDS = 20
ROLE_PREFIX = "SwarmAI-Hive-"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")
KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
MAX_ANSWERS = 20
MAX_ANSWER_CHARS = 4000
MAX_COMMAND_CHARS = 2000
DECISIONS = frozenset({"approve", "deny"})
_IMDS = "http://169.254.169.254"
_BUCKET_FILE = Path(os.environ.get("SWARMAI_HIVE_BUCKET_FILE", "/opt/swarmai/.hive-bucket"))


class Store(Protocol):
    """The three S3 calls the bridge needs (boto3 in production, a dict in tests)."""
    def put(self, key: str, body: bytes) -> None: ...
    def list_keys(self, prefix: str) -> list[str]: ...
    def get(self, key: str) -> Optional[bytes]: ...


class S3Store:
    def __init__(self, bucket: str, region: str):
        from core import aws_account  # on a Hive: its instance role (the default chain)
        self._s3 = aws_account.boto_session(region).client("s3", region_name=region)
        self._bucket = bucket

    def put(self, key: str, body: bytes) -> None:
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=body,
                            ContentType="application/json", ServerSideEncryption="AES256")

    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token = None
        while True:
            kw: dict[str, Any] = {"Bucket": self._bucket, "Prefix": prefix, "MaxKeys": 1000}
            if token:
                kw["ContinuationToken"] = token
            resp = self._s3.list_objects_v2(**kw)
            keys += [o["Key"] for o in resp.get("Contents", [])]
            if not resp.get("IsTruncated"):
                return keys
            token = resp.get("NextContinuationToken")

    def get(self, key: str) -> Optional[bytes]:
        try:
            return self._s3.get_object(Bucket=self._bucket, Key=key)["Body"].read(64 * 1024)
        except self._s3.exceptions.NoSuchKey:
            return None


def _imds(path: str, token: str) -> str:
    req = urllib.request.Request(_IMDS + path, headers={"X-aws-ec2-metadata-token": token})
    with urllib.request.urlopen(req, timeout=3) as r:   # fixed link-local host, not user input
        return r.read().decode()


def resolve_identity() -> Optional[tuple[str, str, str]]:
    """(hive_name, bucket, region) of this instance, or None when not on a Hive EC2."""
    try:
        req = urllib.request.Request(_IMDS + "/latest/api/token", method="PUT",
                                     headers={"X-aws-ec2-metadata-token-ttl-seconds": "300"})
        with urllib.request.urlopen(req, timeout=3) as r:
            token = r.read().decode()
        role = _imds("/latest/meta-data/iam/security-credentials/", token).strip().splitlines()[0]
        region = _imds("/latest/meta-data/placement/region", token).strip()
        bucket = _BUCKET_FILE.read_text().strip()
    except Exception as e:                                # not an EC2 Hive, or IMDS down
        logger.info("swarm_out_bridge: no Hive identity (%s); bridge off", type(e).__name__)
        return None
    ident = identity_from(role, bucket, region)
    if ident is None:
        logger.warning("swarm_out_bridge: unexpected role or bucket name; bridge off")
    return ident


def identity_from(role: str, bucket: str, region: str) -> Optional[tuple[str, str, str]]:
    """(hive_name, bucket, region) when the role is ``SwarmAI-Hive-<name>`` and every
    part has the expected shape (they all go into S3 keys and the S3 endpoint)."""
    name = role[len(ROLE_PREFIX):] if role.startswith(ROLE_PREFIX) else ""
    if not NAME_RE.match(name) or not re.match(r"^[a-z0-9.-]{3,63}$", bucket) \
            or not re.match(r"^[a-z]{2}(-[a-z]+)+-\d$", region):
        return None
    return name, bucket, region


def approval_digest(tool_name: str, command: str, reason: str = "") -> str:
    """What a person approved: the tool and the command text exactly as shown. The Host
    computes it over what it put on the card, the bridge over the request still waiting
    on the board; an approval is delivered only when the two agree (so a rewritten ask
    cannot carry an approval to a different command, or under a different reason).
    ``surrogatepass``: a lone surrogate in Hive-written text must hash, not raise."""
    blob = json.dumps([tool_name, command, reason], ensure_ascii=False).encode("utf-8", "surrogatepass")
    return hashlib.sha256(blob).hexdigest()


def approval_view(tool_name: str, tool_input: dict[str, Any]) -> str:
    """The text a person approves for a gated tool call. Bash: its command, and a line
    naming dangerouslyDisableSandbox when set (it changes what the command can reach).
    Any other tool: its whole input as sorted JSON, so no field the card did not show
    can ride along with the approval."""
    if tool_name == "Bash" and isinstance(tool_input.get("command"), str):
        view = tool_input["command"]
        if tool_input.get("dangerouslyDisableSandbox"):
            view += "\n# dangerouslyDisableSandbox: true"
        return view
    return json.dumps(tool_input, ensure_ascii=False, sort_keys=True, default=str)


def _ask_record(hive: str, session_id: str, key: str, item: dict[str, Any]) -> dict[str, Any]:
    from . import approval_class
    base = {"hive": hive, "session_id": session_id, "request_id": key,
            "asked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            # what the wait is about, as the gate that made it wait said (design
            # 2026-10-31-hive-approvals §2.2): the Host shows it, and only a class in
            # HOST_CLASSES takes the dispatching agent's answer (``_valid``)
            "approval_class": approval_class.clean(item.get("approval_class")) or approval_class.UNKNOWN}
    if item.get("kind") == "ask_user_question":
        questions = []
        all_qs = item.get("questions") if isinstance(item.get("questions"), list) else []
        omitted_q, omitted_o = max(0, len(all_qs) - MAX_ANSWERS), 0
        for q in all_qs[:MAX_ANSWERS]:
            # a question with no text cannot be answered: dropped and counted, as the
            # Host does, so both sides agree on which questions an answer must cover
            if not isinstance(q, dict) or not str(q.get("question", "")).strip():
                omitted_q += 1
                continue
            opts = q.get("options") if isinstance(q.get("options"), list) else []
            kept = [{"label": str(o.get("label", ""))[:500],
                     "description": str(o.get("description", ""))[:1000]}
                    for o in opts[:10] if isinstance(o, dict)]
            omitted_o += len(opts) - len(kept)
            questions.append({
                "question": str(q.get("question", ""))[:MAX_ANSWER_CHARS],
                "header": str(q.get("header", ""))[:200],
                "multiSelect": bool(q.get("multiSelect")),
                "options": kept,
            })
        rec = {**base, "kind": "question", "questions": questions}
        if omitted_q or omitted_o:   # the Host adds its own and shows the total
            rec["omitted"] = {"questions": omitted_q, "options": omitted_o}
        return rec
    tool_input = item.get("toolInput") if isinstance(item.get("toolInput"), dict) else {}
    tool_name = str(item.get("toolName", ""))[:100]
    command = approval_view(tool_name, tool_input)
    return {**base, "kind": "approval", "tool_name": tool_name,
            "command": command[:MAX_COMMAND_CHARS],
            # the card shows the first MAX_COMMAND_CHARS; a longer command cannot be
            # approved from the panel (the person never saw the rest)
            "truncated": len(command) > MAX_COMMAND_CHARS,
            "reason": str(item.get("reason", ""))[:1000], "options": sorted(DECISIONS)}


@dataclass
class _Mirrored:
    session_id: str    # the session that waits on it (the router's id for the unit)
    kind: str          # question | approval


def _drain_router():
    from . import session_registry
    return session_registry.session_router


class Bridge:
    """One Hive's bridge state. ``tick`` is one pass; it is called on the event loop
    (the board and the session units are loop-side) and does its S3 calls in a thread.

    An answer is delivered the way the web page delivers it: through the session
    router's ``continue_with_answer`` / ``continue_with_cmd_permission``. A unit
    waiting on a person has already stopped reading its stream (it is WAITING_INPUT),
    and only that continuation reads the rest of the turn; the generator is drained in
    a task kept here until it ends, as ``routers/chat.py`` keeps a detached turn.
    """

    def __init__(self, hive: str, store: Store, pm=None, router=None,
                 decisions=None, record_decision=None):
        from .permission_manager import permission_manager
        from .swarm_out_decisions import DecisionMirror
        self.hive = hive
        self.store = store
        self.pm = pm or permission_manager
        self._router = router
        self.prefix = f"bridge/{hive}/"
        self._mirrored: dict[str, _Mirrored] = {}
        self._delivered: set[str] = set()
        self._refused: set[str] = set()   # an answer that does not match what is waiting
        self._tasks: set[asyncio.Task] = set()
        # the pipeline decisions waiting on a person (tests pass their own readers)
        self.decisions = DecisionMirror(hive, store, lambda: self.router, KEY_RE,
                                        decisions=decisions, record_decision=record_decision)
        self._hidden: set[str] = set()

    @property
    def router(self):
        return self._router or _drain_router()

    def _waiting_session(self, key: str) -> Optional[str]:
        """The id under which the router holds the unit that waits on *key*. The board
        may file a first-turn prompt under a temporary id, so the unit is the authority."""
        for unit in self.router.list_units():
            if getattr(unit, "_pending_tool_use_id", None) == key and unit.session_id:
                return unit.session_id
        return None

    def _open_now(self) -> dict[str, tuple[str, dict[str, Any]]]:
        out: dict[str, tuple[str, dict[str, Any]]] = {}
        for board_sid in self.pm.prompt_session_ids():
            for item in self.pm.open_prompts(board_sid):
                key = str(item.get("requestId") or item.get("tool_use_id") or "")
                sid = self._waiting_session(key) if KEY_RE.match(key) else None
                if sid and KEY_RE.match(sid):
                    out[key] = (sid, item)
        return out

    def _valid(self, key: str, mirrored: _Mirrored, raw: bytes,
               waiting: Optional[dict[str, Any]] = None) -> tuple[Optional[dict[str, Any]], str]:
        """(the answer to deliver, None) or (None, the reason code it is refused with:
        ``no_option`` it is unreadable or answers something else, ``changed`` it was given
        for a card that does not show what waits now, ``too_long`` an approve of a command
        too long to have been shown whole). ``waiting`` is the ask record of the request
        still on the board: an approve must name it (``shown_sha256``); a deny needs no
        check; a question's answer must cover exactly the questions waiting."""
        try:
            ans = json.loads(raw)
        except ValueError:
            logger.warning("swarm_out_bridge: unreadable answer for %s", key)
            return None, "no_option"
        if not isinstance(ans, dict) or ans.get("request_id") != key:
            return None, "no_option"
        by = ans.get("answered_by", "person")
        if by not in ("person", "host"):
            return None, "no_option"
        if by == "host":
            # the dispatching agent answers only what stays inside the work it manages
            # for this run; anything else is the person's (design §2.4, H3). Checked
            # here, on the Hive, whatever the Host checked
            from . import approval_class
            if (waiting or {}).get("approval_class") not in approval_class.HOST_CLASSES:
                logger.warning("swarm_out_bridge: a host answer for %s refused: the wait is "
                               "%s, the person's to answer", key, (waiting or {}).get("approval_class"))
                return None, "not_host_class"
        if mirrored.kind == "question":
            answers = ans.get("answers")
            ok = (isinstance(answers, dict) and 0 < len(answers) <= MAX_ANSWERS
                  and all(isinstance(k, str) and isinstance(v, str)
                          and len(k) <= MAX_ANSWER_CHARS and len(v) <= MAX_ANSWER_CHARS
                          for k, v in answers.items()))
            # it answers the questions still waiting, not a rewritten mirror of them
            if not ok:
                return None, "no_option"
            asked = {q.get("question") for q in (waiting or {}).get("questions") or []}
            if waiting is not None and set(answers) != asked:
                logger.warning("swarm_out_bridge: answer for %s does not match the waiting "
                               "questions; not delivered", key)
                return None, "changed"
            if by == "host":
                # the host picks options, never free text (design §4 layer 2)
                labels = {q.get("question"): {o.get("label") for o in q.get("options") or []}
                          for q in (waiting or {}).get("questions") or []}
                if any(v not in labels.get(k, set()) for k, v in answers.items()):
                    return None, "no_option"
            return {"answers": answers, "by": by}, ""
        decision = ans.get("decision")
        if decision not in DECISIONS:
            return None, "no_option"
        if decision == "deny":   # always safe: no content check (an older Host sends none)
            return {"decision": decision, "by": by}, ""
        if waiting is None or ans.get("shown_sha256") != approval_digest(
                waiting.get("tool_name", ""), waiting.get("command", ""), waiting.get("reason", "")):
            logger.warning("swarm_out_bridge: approval for %s does not match the waiting "
                           "request; not delivered", key)
            return None, "changed"
        if decision == "approve" and waiting.get("truncated"):
            logger.warning("swarm_out_bridge: approval for %s refused: the command was too "
                           "long to be shown whole", key)
            return None, "too_long"
        return {"decision": decision, "by": by}, ""

    def _deliver(self, key: str, mirrored: _Mirrored, answer: dict[str, Any]) -> None:
        by = "host" if answer.get("by") == "host" else "person"
        if mirrored.kind == "question":
            gen = self.router.continue_with_answer(
                mirrored.session_id, json.dumps(answer["answers"], ensure_ascii=False),
                tool_use_id=key, answered_by=by)
        else:
            # who decided travels with the decision to the gate that waits
            # (permission_manager.set_permission_decision / pop_decided_by)
            gen = self.router.continue_with_cmd_permission(
                mirrored.session_id, key, answer["decision"] == "approve", by=by)
        task = asyncio.create_task(self._drain(key, gen), name=f"swarm-out-answer-{key}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        self._delivered.add(key)
        logger.info("swarm_out_bridge: delivered %s for %s", mirrored.kind, key)

    async def _drain(self, key: str, gen) -> None:
        """Read the continued turn to its end. The hand-over failed when the first event
        is SESSION_NOT_FOUND (no unit waits), or when the continuation raised before its
        first event and the prompt is still on the board (taking the answer takes the
        prompt off the board, before the unit yields anything): then the answer is
        forgotten as delivered, and the next pass tries again while the prompt is on the
        board (one that left with its session closes plainly). Any other error comes
        after the session got the answer, so it stays delivered: the answer was used."""
        seen = False
        handover_failed = False
        try:
            async with contextlib.aclosing(gen) as events:
                async for ev in events:
                    if isinstance(ev, dict) and ev.get("type") == "error":
                        code = str(ev.get("code", "error"))[:60]
                        logger.warning("swarm_out_bridge: continuation for %s reported %s", key, code)
                        if not seen and code == "SESSION_NOT_FOUND":
                            handover_failed = True
                    seen = True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("swarm_out_bridge: continuation for %s failed (%s)", key, type(e).__name__)
            if not seen and key in self._open_now():
                handover_failed = True
        if handover_failed:
            self._delivered.discard(key)

    async def reconcile(self) -> None:
        """At start: every ask this Hive wrote before a restart has no waiter now;
        mark the ones not open on the board as closed so the Host stops showing them."""
        open_now = self._open_now()
        keys = await asyncio.to_thread(self.store.list_keys, f"{self.prefix}ask/")
        closed = set(await asyncio.to_thread(self.store.list_keys, f"{self.prefix}closed/"))
        for k in keys:
            rel = k[len(f"{self.prefix}ask/"):]
            sid, _, name = rel.partition("/")
            key = name[:-5] if name.endswith(".json") else ""
            if not key or key in open_now or f"{self.prefix}closed/{rel}" in closed:
                continue
            if KEY_RE.match(sid) and KEY_RE.match(key):
                body = json.dumps({"request_id": key, "closed_at": time.time(), "why": "restart"}).encode()
                await asyncio.to_thread(self.store.put, f"{self.prefix}closed/{rel}", body)

    async def tick(self) -> None:
        board = self._open_now()
        try:
            self._hidden = await self.decisions.run_pass(board)
        except Exception as e:   # S3 down: the prompts still get their pass
            logger.warning("swarm_out_bridge: the decision pass failed (%s)", type(e).__name__)
        # a waiting decision's own card is shown once, as the decision
        open_now = {k: v for k, v in board.items() if k not in self._hidden}
        # 1. mirror new prompts
        for key, (sid, item) in open_now.items():
            if key in self._mirrored:
                continue
            try:
                rec = _ask_record(self.hive, sid, key, item)
                # ASCII escapes: a lone surrogate in model text writes, and reads back
                # as the same string on the Host (the digest still matches)
                body = json.dumps(rec, ensure_ascii=True).encode()
                await asyncio.to_thread(self.store.put, f"{self.prefix}ask/{sid}/{key}.json", body)
            except Exception as e:   # one prompt must not stop the pass for the others
                logger.warning("swarm_out_bridge: mirroring %s failed (%s)", key, type(e).__name__)
                continue
            self._mirrored[key] = _Mirrored(sid, rec["kind"])
        # 2. close the ones that left the board. One the session took from this bridge
        # (handed over and not failed since) says so: ``why: delivered`` is the Host's
        # only sign its answer was used, and nothing closes that key again. A refused one
        # is closed again plainly: it is settled now (answered on the Hive's page,
        # expired, or the session ended), no longer "not delivered"
        for key in [k for k in self._mirrored if k not in open_now]:
            m = self._mirrored.pop(key)
            delivered = key in self._delivered
            self._delivered.discard(key)
            self._refused.discard(key)
            close = {"request_id": key, "closed_at": time.time()}
            if delivered:
                close["why"] = "delivered"
            await asyncio.to_thread(self.store.put, f"{self.prefix}closed/{m.session_id}/{key}.json",
                                    json.dumps(close).encode())
        # 3. deliver answers for the ones still open, once each
        waiting = {k: m for k, m in self._mirrored.items()
                   if k not in self._delivered and k not in self._refused}
        if not waiting:
            return
        listed = set(await asyncio.to_thread(self.store.list_keys, f"{self.prefix}answer/"))
        for key, m in waiting.items():
            akey = f"{self.prefix}answer/{m.session_id}/{key}.json"
            if akey not in listed:
                continue
            raw = await asyncio.to_thread(self.store.get, akey)
            if raw is None:
                continue
            sid, item = open_now[key]
            answer, reason = self._valid(key, m, raw, _ask_record(self.hive, sid, key, item))
            if answer is not None:
                self._deliver(key, m, answer)
            else:
                # not delivered, and the Host is told why (a reason code: the Host words
                # it); the request stays on the board for the Hive's own page (or expires)
                self._refused.add(key)
                body = json.dumps({"request_id": key, "closed_at": time.time(),
                                   "why": "answer_refused", "reason": reason}).encode()
                await asyncio.to_thread(self.store.put, f"{self.prefix}closed/{m.session_id}/{key}.json", body)


async def run_bridge_loop() -> None:
    """Background task for hive mode. Off when the instance has no Hive identity."""
    ident = await asyncio.to_thread(resolve_identity)
    if ident is None:
        return
    hive, bucket, region = ident
    bridge = Bridge(hive, await asyncio.to_thread(S3Store, bucket, region))
    logger.info("swarm_out_bridge: on for hive %s", hive)
    try:
        await bridge.reconcile()
    except Exception as e:
        logger.warning("swarm_out_bridge: reconcile failed (%s)", type(e).__name__)
    last_error = 0.0
    while True:
        await asyncio.sleep(POLL_SECONDS)
        try:
            await bridge.tick()
        except asyncio.CancelledError:
            raise
        except Exception as e:                    # S3 down, IAM not updated yet: retry next pass
            if time.time() - last_error > 600:
                logger.warning("swarm_out_bridge: pass failed (%s)", type(e).__name__)
                last_error = time.time()
