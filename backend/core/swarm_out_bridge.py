"""Swarm Out bridge, Hive side: a person answers this Hive's open prompts through S3.

A Hive runs dispatched tasks with nobody watching. When a session needs a person —
an AskUserQuestion, or a dangerous-command approval — the prompt sits on the
session's prompt board (``permission_manager``) and the hook waits, exactly as it
does on a desktop. This module does not add a second way to wait. It only:

- mirrors every open prompt on the board to
  ``s3://<bucket>/bridge/<hive>/ask/<session>/<key>.json``;
- writes ``bridge/<hive>/closed/<session>/<key>.json`` once a mirrored prompt is no
  longer open (answered on the Hive's own web page, expired, or the session ended),
  so the Host stops showing it;
- reads ``bridge/<hive>/answer/<session>/<key>.json`` (written only by the Host,
  which holds the account credentials; the Hive role can read but not write there)
  and delivers a valid answer the way the web page does: through the session
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
        import boto3
        self._s3 = boto3.client("s3", region_name=region)
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


def _ask_record(hive: str, session_id: str, key: str, item: dict[str, Any]) -> dict[str, Any]:
    base = {"hive": hive, "session_id": session_id, "request_id": key,
            "asked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if item.get("kind") == "ask_user_question":
        questions = []
        for q in (item.get("questions") or [])[:MAX_ANSWERS]:
            if not isinstance(q, dict):
                continue
            questions.append({
                "question": str(q.get("question", ""))[:MAX_ANSWER_CHARS],
                "header": str(q.get("header", ""))[:200],
                "multiSelect": bool(q.get("multiSelect")),
                "options": [{"label": str(o.get("label", ""))[:500],
                             "description": str(o.get("description", ""))[:1000]}
                            for o in (q.get("options") or [])[:10] if isinstance(o, dict)],
            })
        return {**base, "kind": "question", "questions": questions}
    tool_input = item.get("toolInput") if isinstance(item.get("toolInput"), dict) else {}
    # A Bash approval shows its command; any other gated tool shows its input.
    command = tool_input.get("command") if isinstance(tool_input.get("command"), str) \
        else json.dumps(tool_input, ensure_ascii=False, default=str)
    return {**base, "kind": "approval", "tool_name": str(item.get("toolName", ""))[:100],
            "command": command[:MAX_COMMAND_CHARS],
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

    def __init__(self, hive: str, store: Store, pm=None, router=None):
        from .permission_manager import permission_manager
        self.hive = hive
        self.store = store
        self.pm = pm or permission_manager
        self._router = router
        self.prefix = f"bridge/{hive}/"
        self._mirrored: dict[str, _Mirrored] = {}
        self._delivered: set[str] = set()
        self._tasks: set[asyncio.Task] = set()

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

    def _valid(self, key: str, mirrored: _Mirrored, raw: bytes) -> Optional[dict[str, Any]]:
        try:
            ans = json.loads(raw)
        except ValueError:
            logger.warning("swarm_out_bridge: unreadable answer for %s", key)
            return None
        if not isinstance(ans, dict) or ans.get("request_id") != key:
            return None
        if mirrored.kind == "question":
            answers = ans.get("answers")
            ok = (isinstance(answers, dict) and 0 < len(answers) <= MAX_ANSWERS
                  and all(isinstance(k, str) and isinstance(v, str)
                          and len(k) <= MAX_ANSWER_CHARS and len(v) <= MAX_ANSWER_CHARS
                          for k, v in answers.items()))
            return {"answers": answers} if ok else None
        decision = ans.get("decision")
        return {"decision": decision} if decision in DECISIONS else None

    def _deliver(self, key: str, mirrored: _Mirrored, answer: dict[str, Any]) -> None:
        if mirrored.kind == "question":
            gen = self.router.continue_with_answer(
                mirrored.session_id, json.dumps(answer["answers"], ensure_ascii=False),
                tool_use_id=key, answered_by="person")
        else:
            gen = self.router.continue_with_cmd_permission(
                mirrored.session_id, key, answer["decision"] == "approve")
        task = asyncio.create_task(self._drain(key, gen), name=f"swarm-out-answer-{key}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        self._delivered.add(key)
        logger.info("swarm_out_bridge: delivered %s for %s", mirrored.kind, key)

    async def _drain(self, key: str, gen) -> None:
        """Read the continued turn to its end. A continuation that fails (raises, or
        reports an error event such as SESSION_NOT_FOUND) is forgotten as delivered,
        so the next pass tries again while the prompt is still on the board."""
        failed = False
        try:
            async with contextlib.aclosing(gen) as events:
                async for ev in events:
                    if isinstance(ev, dict) and ev.get("type") == "error":
                        failed = True
                        logger.warning("swarm_out_bridge: continuation for %s reported %s",
                                       key, str(ev.get("code", "error"))[:60])
        except asyncio.CancelledError:
            raise
        except Exception as e:
            failed = True
            logger.warning("swarm_out_bridge: continuation for %s failed (%s)", key, type(e).__name__)
        if failed:
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
        open_now = self._open_now()
        # 1. mirror new prompts
        for key, (sid, item) in open_now.items():
            if key in self._mirrored:
                continue
            rec = _ask_record(self.hive, sid, key, item)
            body = json.dumps(rec, ensure_ascii=False).encode()
            await asyncio.to_thread(self.store.put, f"{self.prefix}ask/{sid}/{key}.json", body)
            self._mirrored[key] = _Mirrored(sid, rec["kind"])
        # 2. close the ones that left the board
        for key in [k for k in self._mirrored if k not in open_now]:
            m = self._mirrored.pop(key)
            self._delivered.discard(key)
            body = json.dumps({"request_id": key, "closed_at": time.time()}).encode()
            await asyncio.to_thread(self.store.put, f"{self.prefix}closed/{m.session_id}/{key}.json", body)
        # 3. deliver answers for the ones still open, once each
        waiting = {k: m for k, m in self._mirrored.items() if k not in self._delivered}
        if not waiting:
            return
        listed = set(await asyncio.to_thread(self.store.list_keys, f"{self.prefix}answer/"))
        for key, m in waiting.items():
            akey = f"{self.prefix}answer/{m.session_id}/{key}.json"
            if akey not in listed:
                continue
            raw = await asyncio.to_thread(self.store.get, akey)
            answer = self._valid(key, m, raw) if raw is not None else None
            if answer is not None:
                self._deliver(key, m, answer)


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
