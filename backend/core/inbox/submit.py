"""``inbox submit``: how a job or skill on this machine hands something to the Inbox.

Design: ``docs/design/2026-11-20-inbox.md`` §4 (本机提交) and §8 (自动发的记录). There is
no HTTP route for this: it is an in-process function, and a CLI subcommand of the backend
program for jobs and skills (``"$SWARM_BACKEND_BIN" inbox submit``). A Hive cannot reach
it.

- ``submit``: a proposed reply. It becomes a PROPOSED item (代拟), never REPLY, marked with
  its producer, and only after the source confirms the thread: ``source.thread(ref)``
  returns it, it can be replied to, and the person is in it (wrote in it, or was
  addressed in it) or its target is public (a public issue or discussion). Sending it is
  still the person's click.
- ``submit_sent``: a record of something the job already sent itself (``--sent``). It
  becomes an item in state ``replied`` and one ``inbox_sends`` row, origin
  ``auto:<producer>``, status ``sent``; the same source message id recorded twice is one
  row. It never calls ``reply`` and never opens a source.

The CLI writes ``data.db`` directly (the daemon need not run) but never migrates it: a
database older than schema v20 is refused with the reason.

Public: ``SubmitRefused``, ``SubmitRequest``, ``SentRecord``, ``submit``, ``submit_sent``,
``main``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from core.inbox import store
from core.inbox.models import ItemState, Lane, Message, SendStatus, SourceCategory, ThreadRef
from core.ui_language import text

logger = logging.getLogger(__name__)

# The note under a submitted draft, in the app's language (core/ui_language)
_NOTE_EN = {"drafted_by": "Drafted by {producer}"}
_NOTE_ZH = {"drafted_by": "由 {producer} 起草"}

_PRODUCER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SCHEMA_VERSION = 20


class SubmitRefused(Exception):
    """Nothing was stored; the message says why."""


@dataclass(frozen=True)
class SubmitRequest:
    source_id: str
    thread_ref: ThreadRef
    producer: str
    draft: str = ""          # the proposed reply text
    title: str = ""
    text: str = ""           # what the reply answers (shown as the excerpt)
    ask: str = ""            # one sentence: what the person is asked to decide
    author: str = ""
    url: str = ""
    external_id: str = ""    # the producer's id of this proposal (kept in the producer's own
    #                          namespace); default: one proposal per thread


@dataclass(frozen=True)
class SentRecord:
    source_id: str
    thread_ref: ThreadRef
    producer: str
    external_msg_id: str     # the id the source gave the sent message (a comment id)
    text: str
    url: str = ""
    to_label: str = ""
    title: str = ""
    sent_at: Optional[datetime] = None
    external_id: str = ""


def _item_key(producer: str, key: str) -> str:
    """The stored external id of a submitted item: always in the producer's own namespace,
    so a submission can never name (and attach a draft or a record to) a pulled item or
    another producer's item."""
    return f"submit:{producer}:{key}"


async def _check_existing(item_id: str, producer: str, ref: ThreadRef, *, need_open: bool) -> None:
    """A submission that found its item already stored: it must be this producer's item of
    the same thread; a proposal for one that is already handled is refused (the person
    would never see it) rather than reported as stored."""
    from database import db
    item = await db.inbox_get_item(item_id)
    if item is None or item["origin"] != f"submit:{producer}" or item["thread_key"] != ref.key():
        raise SubmitRefused("external_id names another item: use an id of this thread")
    if need_open and item["state"] not in (ItemState.OPEN.value, ItemState.SNOOZED.value):
        raise SubmitRefused(f"the proposal for this thread is already {item['state']}: pass a "
                            "new external_id for a new proposal")


def _check_producer(producer: str) -> None:
    if not _PRODUCER.match(producer or ""):
        raise SubmitRefused("producer must be 1-64 letters, digits, '.', '_' or '-'")


async def _source_row(source_id: str, ref: ThreadRef) -> dict:
    """The source row a submission names; with no ``source_id``, the one conversation
    source of the thread's kind (a job that posts as the person on GitHub need not know
    which id the person gave their GitHub source). None or several is refused."""
    from database import db
    if not source_id:
        rows = [r for r in await db.inbox_list_sources()
                if r["kind"] == ref.source_kind and r["category"] == SourceCategory.CONVERSATION.value]
        if len(rows) != 1:
            raise SubmitRefused(f"{len(rows)} Inbox sources of kind {ref.source_kind!r}: pass source_id")
        return rows[0]
    row = await db.inbox_get_source(source_id)
    if row is None:
        raise SubmitRefused(f"no Inbox source {source_id!r}")
    if row["category"] != SourceCategory.CONVERSATION.value:
        raise SubmitRefused(f"source {source_id!r} cannot be replied to")
    if ref.source_kind != row["kind"]:
        raise SubmitRefused(f"thread_ref is a {ref.source_kind!r} thread, source {source_id!r} "
                            f"is {row['kind']!r}")
    return row


async def submit(req: SubmitRequest) -> dict:
    """Store a proposed reply as a PROPOSED item. Returns ``{item_id, created}``."""
    from database import db
    _check_producer(req.producer)
    row = await _source_row(req.source_id, req.thread_ref)
    source_id = row["id"]
    try:
        source = await store.open_source(row)
    except Exception as exc:  # noqa: BLE001
        raise SubmitRefused(f"source {source_id!r} cannot be opened ({type(exc).__name__})")
    try:
        try:
            me = await source.whoami()
            thread = await source.thread(req.thread_ref)
        except Exception as exc:  # noqa: BLE001 — not found, no access, signed out
            raise SubmitRefused(f"the thread could not be read ({type(exc).__name__})")
        if thread.target is None:
            raise SubmitRefused("the thread cannot be replied to")
        involved = any(me.is_me(m.author_id) or m.is_direct or m.mentions_me
                       for m in thread.messages)
        if not (involved or thread.target.public):
            raise SubmitRefused("the person is not in this thread and it is not public")
    finally:
        try:
            await source.close()
        except Exception:  # noqa: BLE001
            logger.debug("inbox submit: source close failed", exc_info=True)
    first = thread.messages[0] if thread.messages else None
    msg = Message(
        # the full ref (message_id included), not ThreadRef.key: two proposals for two
        # messages of one mail conversation are two items
        external_id=_item_key(req.producer, req.external_id or json.dumps(
            {"kind": req.thread_ref.source_kind, "data": req.thread_ref.data}, sort_keys=True, ensure_ascii=False)),
        thread=req.thread_ref, author_id=req.producer,
        author_name=req.author or (first.author_name if first else req.producer),
        received_at=store.now(), text=req.text or (first.text if first else ""),
        url=req.url or (first.url if first else ""), title=req.title or (first.title if first else ""))
    item_row = store.message_to_item_row(source_id, msg, Lane.PROPOSED, None,
                                         origin=f"submit:{req.producer}", reason=req.ask[:300],
                                         producer=req.producer, triaged=True)
    stored = await db.inbox_insert_items([item_row])
    item_id, created = stored[msg.external_id]
    if not created:
        await _check_existing(item_id, req.producer, req.thread_ref, need_open=True)
    if req.draft.strip():
        latest = await db.inbox_latest_draft(item_id)
        if latest is None or latest["text"] != req.draft:
            await db.inbox_insert_draft({
                "id": store.new_id("dr"), "item_id": item_id, "text": req.draft, "lang": "",
                "note": text(_NOTE_EN, _NOTE_ZH, "drafted_by", producer=req.producer), "model": f"submit:{req.producer}",
                "created_at": store.ts(store.now())})
    return {"item_id": item_id, "created": created}


async def submit_sent(rec: SentRecord) -> dict:
    """Record a reply a job sent itself. Never sends. Returns ``{item_id, send_id,
    created}``; the same ``external_msg_id`` again returns the first record."""
    from database import db
    _check_producer(rec.producer)
    if not rec.external_msg_id.strip():
        raise SubmitRefused("--sent needs the sent message's external_msg_id")
    source_id = (await _source_row(rec.source_id, rec.thread_ref))["id"]
    existing = await db.inbox_send_by_msg(source_id, rec.external_msg_id)
    if existing is not None:
        return {"item_id": existing["item_id"], "send_id": existing["id"], "created": False}
    sent_at = rec.sent_at or store.now()
    msg = Message(
        external_id=_item_key(rec.producer, rec.external_id or f"sent:{rec.external_msg_id}"),
        thread=rec.thread_ref, author_id=rec.producer, author_name=rec.producer,
        received_at=sent_at, text=rec.text, url=rec.url, title=rec.title)
    item_row = store.message_to_item_row(source_id, msg, Lane.PROPOSED, None,
                                         origin=f"submit:{rec.producer}",
                                         state=ItemState.REPLIED, producer=rec.producer,
                                         triaged=True)
    item_id, item_created = (await db.inbox_insert_items([item_row]))[msg.external_id]
    if not item_created:
        await _check_existing(item_id, rec.producer, rec.thread_ref, need_open=False)
    stamp = store.ts(store.now())
    send_row = {
        "id": store.new_id("snd"), "item_id": item_id, "source_id": source_id,
        "draft_id": None, "origin": f"auto:{rec.producer}",
        "target": json.dumps({"label": rec.to_label or rec.title or rec.url, "recipients": [],
                              "public": False, "flags": []}, ensure_ascii=False),
        "target_sha": "", "text": rec.text, "status": SendStatus.SENT.value,
        "external_msg_id": rec.external_msg_id, "url": rec.url, "error": "",
        "created_at": stamp, "sent_at": store.ts(sent_at), "updated_at": stamp}
    if not await db.inbox_insert_send(send_row):
        existing = await db.inbox_send_by_msg(source_id, rec.external_msg_id)
        if existing is None:
            raise SubmitRefused("the record could not be stored")
        return {"item_id": existing["item_id"], "send_id": existing["id"], "created": False}
    return {"item_id": item_id, "send_id": send_row["id"], "created": True}


# ── CLI ──────────────────────────────────────────────────────────────────────

USAGE = """usage: python-backend inbox submit [--sent] --json <file | ->

The JSON object (UTF-8):
  source_id     the Inbox source the thread belongs to (GET /api/inbox/sources); empty or
                absent: the one conversation source of the thread's kind
  thread_ref    {"source_kind": ..., "data": {...}}: the source's own address of the thread
  producer      who submits: letters, digits, '.', '_' or '-' (shown in the Inbox)
plain submit (a proposed reply, sent only on the person's click):
  draft, title, text, ask, author, url, external_id   (all optional)
--sent (a record of a reply already sent by the job; nothing is sent):
  external_msg_id (required), text (required), url, to_label, title, sent_at, external_id
Prints one JSON line; exit 0 stored, 1 refused (reason on stderr), 2 usage."""


def _ref(value: Any) -> ThreadRef:
    if not isinstance(value, dict) or not isinstance(value.get("source_kind"), str) \
            or not isinstance(value.get("data", {}), dict):
        raise SubmitRefused('thread_ref must be {"source_kind": "...", "data": {...}}')
    return ThreadRef(source_kind=value["source_kind"], data=dict(value.get("data") or {}))


def _str(data: dict, key: str) -> str:
    value = data.get(key, "")
    if not isinstance(value, str):
        raise SubmitRefused(f"{key} must be text")
    return value


def _schema_ready(db_path: str) -> bool:
    import sqlite3
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error:
        return False
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION
    finally:
        conn.close()


async def _run(sent: bool, data: dict) -> dict:
    from database.sqlite import close_all_pools
    try:
        common = (_str(data, "source_id"), _ref(data.get("thread_ref")), _str(data, "producer"))
        if sent:
            sent_at = store.parse_ts(_str(data, "sent_at")) if data.get("sent_at") else None
            return await submit_sent(SentRecord(
                *common, external_msg_id=_str(data, "external_msg_id"), text=_str(data, "text"),
                url=_str(data, "url"), to_label=_str(data, "to_label"), title=_str(data, "title"),
                sent_at=sent_at, external_id=_str(data, "external_id")))
        return await submit(SubmitRequest(
            *common, draft=_str(data, "draft"), title=_str(data, "title"),
            text=_str(data, "text"), ask=_str(data, "ask"), author=_str(data, "author"),
            url=_str(data, "url"), external_id=_str(data, "external_id")))
    finally:
        await close_all_pools()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python-backend inbox", add_help=True)
    commands = parser.add_subparsers(dest="command")
    sub = commands.add_parser("submit")
    sub.add_argument("--sent", action="store_true")
    sub.add_argument("--json", required=True, dest="json_path")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 2
    if args.command != "submit":
        print(USAGE, file=sys.stderr)
        return 2
    try:
        raw = sys.stdin.read() if args.json_path == "-" else open(args.json_path, encoding="utf-8").read()
        data = json.loads(raw)
    except (OSError, ValueError) as exc:
        print(f"inbox submit: cannot read the JSON ({type(exc).__name__})", file=sys.stderr)
        return 2
    if not isinstance(data, dict):
        print("inbox submit: the JSON must be an object", file=sys.stderr)
        return 2
    from database import db
    if not _schema_ready(str(db.db_path)):
        print("inbox submit: the Inbox tables do not exist yet (data.db is older than schema "
              f"v{SCHEMA_VERSION}); start SwarmAI once and try again", file=sys.stderr)
        return 1
    try:
        result = asyncio.run(_run(args.sent, data))
    except SubmitRefused as exc:
        print(f"inbox submit: refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0
