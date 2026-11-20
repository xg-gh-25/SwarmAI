"""The Inbox send path: a reply goes out only on the person's click, to the target they saw,
at most once.

Design: ``docs/design/2026-11-20-inbox.md`` §8. The route (``routers/inbox.py``) runs the
caller check (``core/inbox/caller.py``) first; this module does the rest, in order:

1. read the item; fetch the thread fresh (``source.thread``), fingerprint its target and
   compare it with the ``target_sha`` the UI showed -> ``target_changed``. Recipients only
   ever come from the source: the request carries none.
2. the item must be ``open`` (``not_open``) and the source able to send (``cannot_send``).
3. ``channels.egress_redactor.redact_text`` must leave the text as it is -> else
   ``redacted`` with the spans it would change (offsets and replacement; the original
   characters are not echoed back). Nothing is silently rewritten.
4. reserve an ``inbox_sends`` row (``reserved``). The partial unique index allows one
   reserved / unknown row per item: a double click, or a second click with edited text,
   gets that row back and sends nothing.
5. ``source.reply`` -> ``sent`` and the item ``replied``; ``SendFailed`` -> ``failed`` (may
   be sent again); any other exception or a timeout -> ``unknown``.

``unknown`` is never resent automatically. Each pull of the source asks ``find_sent``
(``reconcile_unknown``); a hit records ``sent``. The person may confirm it did not go out
(``not_sent`` -> ``failed``), which allows a send again. At startup every leftover
``reserved`` row becomes ``unknown`` (``recover_reserved``): the process may have stopped
after the source sent.

The reply runs in its own task, shielded from the request: a client that disconnects
mid-send does not cancel it, so the row always reaches its outcome.

Public: ``SendRefused``, ``target_for``, ``send_item``, ``not_sent``, ``resend``,
``recover_reserved``, ``reconcile_unknown``, ``REPLY_TIMEOUT_S``.
"""
from __future__ import annotations

import asyncio
import difflib
import json
import logging
from dataclasses import asdict
from typing import Any, Optional

from core.inbox import store
from core.inbox.models import (
    ItemState,
    ReplyTarget,
    SendOrigin,
    SendStatus,
    SourceHealth,
    ThreadRef,
    target_sha,
)
from core.inbox.sources.base import SendFailed, Source

logger = logging.getLogger(__name__)

#: bounds a reply that hangs; the outcome is then ``unknown``, never retried
REPLY_TIMEOUT_S = 120.0

_IN_FLIGHT: set[asyncio.Task] = set()
_SPAN_JOIN = 8


class SendRefused(Exception):
    """The send was not attempted. ``status`` is the HTTP status, ``code`` the reason
    (not_found, target_changed, not_open, cannot_send, redacted, not_unknown, not_failed,
    source_unavailable), ``extra`` what the UI needs to show it."""

    def __init__(self, status: int, code: str, message: str = "", **extra: Any) -> None:
        super().__init__(code)
        self.status, self.code, self.message, self.extra = status, code, message or code, extra

    def detail(self) -> dict:
        return {"code": self.code, "message": self.message, **self.extra}


def _target_json(target: ReplyTarget) -> str:
    return json.dumps(asdict(target), ensure_ascii=False)


def _target_out(target: ReplyTarget, ref: ThreadRef, can_send: bool) -> dict:
    return {"label": target.label, "recipients": list(target.recipients), "public": target.public,
            "flags": list(target.flags), "target_sha": target_sha(target, ref), "can_send": can_send}


def redaction_spans(original: str, redacted: str) -> list[dict]:
    """Where redaction would change the text: ``{start, end, replacement}`` over the
    original. The original characters are not included (they are the secret)."""
    changed: list[list[int]] = []
    matcher = difflib.SequenceMatcher(a=original, b=redacted, autojunk=False)
    for op, a0, a1, b0, b1 in matcher.get_opcodes():
        if op == "equal":
            continue
        # a character the replacement happens to share ("A" of a key and of "[REDACTED]")
        # splits one change into several: changes this close are one span
        if changed and a0 - changed[-1][1] < _SPAN_JOIN and b0 - changed[-1][3] < _SPAN_JOIN:
            changed[-1][1], changed[-1][3] = a1, b1
        else:
            changed.append([a0, a1, b0, b1])
    return [{"start": a0, "end": a1, "replacement": redacted[b0:b1]} for a0, a1, b0, b1 in changed]


async def _load(item_id: str) -> tuple[dict, dict]:
    from database import db
    item = await db.inbox_get_item(item_id)
    if item is None:
        raise SendRefused(404, "not_found", "no such item")
    source_row = await db.inbox_get_source(item["source_id"])
    if source_row is None:
        raise SendRefused(409, "cannot_send", "the item's source is not configured")
    return item, source_row


async def _open(source_row: dict) -> Source:
    try:
        return await store.open_source(source_row)
    except Exception as exc:  # noqa: BLE001 — no connector, or it cannot start
        raise SendRefused(503, "source_unavailable", type(exc).__name__)


async def _fresh_target(source: Source, ref: ThreadRef) -> Optional[ReplyTarget]:
    try:
        thread = await source.thread(ref)
    except Exception as exc:  # noqa: BLE001 — auth or network: nothing can be checked
        raise SendRefused(503, "source_unavailable", type(exc).__name__)
    return thread.target


async def _source_can_send(source: Source, source_row: dict) -> bool:
    if source_row.get("health") == SourceHealth.AUTH_FAILED.value:
        return False
    try:
        return bool(await source.can_send())
    except Exception as exc:  # noqa: BLE001 — cannot tell: treated as cannot send
        logger.info("inbox: can_send failed (%s)", type(exc).__name__)
        return False


async def target_for(item_id: str) -> dict:
    """``TargetOut`` fields for an item: its reply target resolved fresh from the source."""
    item, source_row = await _load(item_id)
    source = await _open(source_row)
    try:
        ref = store.ref_from_json(item["thread_ref"])
        target = await _fresh_target(source, ref)
        if target is None:
            raise SendRefused(409, "cannot_send", "this thread cannot be replied to")
        return _target_out(target, ref, await _source_can_send(source, source_row))
    finally:
        await _close(source)


async def _close(source: Source) -> None:
    try:
        await source.close()
    except Exception:  # noqa: BLE001 — a source that fails to close changes no outcome
        logger.debug("inbox: source close failed", exc_info=True)


async def _checks(item: dict, source_row: dict, source: Source, text: str,
                  shown_sha: str) -> tuple[ThreadRef, ReplyTarget]:
    """Design §8 steps 1-3. Raises ``SendRefused``."""
    ref = store.ref_from_json(item["thread_ref"])
    target = await _fresh_target(source, ref)
    if target is None:
        raise SendRefused(409, "cannot_send", "this thread cannot be replied to")
    if target_sha(target, ref) != shown_sha:
        raise SendRefused(409, "target_changed", "the recipients changed since they were shown",
                          target=_target_out(target, ref, await _source_can_send(source, source_row)))
    if item["state"] != ItemState.OPEN.value:
        raise SendRefused(409, "not_open", f"the item is {item['state']}")
    if not await _source_can_send(source, source_row):
        raise SendRefused(409, "cannot_send", "the source cannot send now")
    from channels.egress_redactor import redact_text
    redacted = redact_text(text)
    if redacted != text:
        raise SendRefused(409, "redacted", "the text contains what looks like a credential",
                          spans=redaction_spans(text, redacted))
    return ref, target


async def send_item(item_id: str, text: str, shown_sha: str,
                    draft_id: Optional[str] = None) -> dict:
    """Send one reply on the person's click. Returns ``SendOut`` fields."""
    from database import db
    item, source_row = await _load(item_id)
    source = await _open(source_row)
    handed_over = False
    try:
        ref, target = await _checks(item, source_row, source, text, shown_sha)
        stamp = store.ts(store.now())
        row = {"id": store.new_id("snd"), "item_id": item_id, "source_id": item["source_id"],
               "draft_id": draft_id, "origin": SendOrigin.CLICK.value,
               "target": _target_json(target), "target_sha": shown_sha, "text": text,
               "status": SendStatus.RESERVED.value, "external_msg_id": None, "url": "",
               "error": "", "created_at": stamp, "sent_at": None, "updated_at": stamp}
        if not await db.inbox_insert_send(row):
            existing = await db.inbox_open_send(item_id)
            if existing is None:     # settled between the two statements: read it again
                raise SendRefused(409, "not_open", "the item was just sent")
            return await store.send_out(existing, item=item, source=source_row)
        handed_over = True
        return await _deliver(source, row, item, source_row, ref)
    finally:
        if not handed_over:
            await _close(source)


async def _deliver(source: Source, row: dict, item: dict, source_row: dict,
                   ref: ThreadRef) -> dict:
    """Run the reply in its own task (kept in ``_IN_FLIGHT``) and wait for it shielded."""
    task = asyncio.get_running_loop().create_task(_reply_and_record(source, row, item, ref))
    _IN_FLIGHT.add(task)
    task.add_done_callback(_IN_FLIGHT.discard)
    final = await asyncio.shield(task)
    return await store.send_out(final, item=item, source=source_row)


async def _reply_and_record(source: Source, row: dict, item: dict, ref: ThreadRef) -> dict:
    status, fields = SendStatus.UNKNOWN.value, {}
    try:
        receipt = await asyncio.wait_for(source.reply(ref, row["text"]), REPLY_TIMEOUT_S)
        status = SendStatus.SENT.value
        fields = {"external_msg_id": receipt.external_msg_id or None, "url": receipt.url or "",
                  "sent_at": store.ts(receipt.sent_at or store.now())}
    except SendFailed as exc:
        status, fields = SendStatus.FAILED.value, {"error": _err(exc)}
    except asyncio.TimeoutError:
        fields = {"error": f"no answer from the source within {int(REPLY_TIMEOUT_S)} s"}
    except BaseException as exc:  # noqa: BLE001 — the outcome is unknown, whatever it was
        fields = {"error": _err(exc)}
        if not isinstance(exc, Exception):
            await _record(row, item, status, fields)
            raise
    finally:
        await _close(source)
    return await _record(row, item, status, fields)


async def _record(row: dict, item: dict, status: str, fields: dict) -> dict:
    from database import db
    stamp = store.ts(store.now())
    ok = await db.inbox_update_send(row["id"], stamp, expect_status=SendStatus.RESERVED.value,
                                    status=status, **fields)
    if not ok and fields.get("external_msg_id"):
        # the source's message id is already recorded (a job recorded it): keep the outcome
        fields = {k: v for k, v in fields.items() if k != "external_msg_id"}
        await db.inbox_update_send(row["id"], stamp, expect_status=SendStatus.RESERVED.value,
                                   status=status, **fields)
    if status == SendStatus.SENT.value:
        await _mark_replied(item)
        await _edit_feedback(row, item)
    logger.info("inbox.send id=%s item=%s status=%s", row["id"], row["item_id"], status)
    return await db.inbox_get_send(row["id"]) or {**row, "status": status, **fields}


async def _mark_replied(item: dict) -> None:
    from database import db
    stamp = store.ts(store.now())
    for state in (ItemState.OPEN.value, ItemState.SNOOZED.value):
        if await db.inbox_update_item(item["id"], stamp, expect_state=state,
                                      state=ItemState.REPLIED.value, snooze_until=None):
            return


async def _edit_feedback(row: dict, item: dict) -> None:
    """The person changed the draft before sending: one ``edit_sent`` feedback row."""
    from database import db
    if not row.get("draft_id"):
        return
    draft = await db.inbox_get_draft(row["draft_id"])
    if draft and draft["text"] != row["text"]:
        await db.inbox_insert_feedback({
            "id": store.new_id("fb"), "item_id": item["id"], "source_id": item["source_id"],
            "action": "edit_sent", "from_lane": item["lane"], "to_lane": item["lane"],
            "created_at": store.ts(store.now())})


def _err(exc: BaseException) -> str:
    from channels.egress_redactor import redact_text
    return redact_text(f"{type(exc).__name__}: {exc}")[:500]


async def not_sent(send_id: str) -> dict:
    """The person confirms an ``unknown`` send did not go out: ``failed``, so the item can
    be sent again."""
    from database import db
    row = await db.inbox_get_send(send_id)
    if row is None:
        raise SendRefused(404, "not_found", "no such send")
    if not await db.inbox_update_send(send_id, store.ts(store.now()),
                                      expect_status=SendStatus.UNKNOWN.value,
                                      status=SendStatus.FAILED.value,
                                      error="you confirmed it was not sent"):
        raise SendRefused(409, "not_unknown", f"the send is {row['status']}")
    return await store.send_out(await db.inbox_get_send(send_id))


async def resend(send_id: str) -> dict:
    """Send a ``failed`` send again, through the same checks, to the target it was shown
    with: a changed target is refused like a first send."""
    from database import db
    row = await db.inbox_get_send(send_id)
    if row is None:
        raise SendRefused(404, "not_found", "no such send")
    if row["status"] != SendStatus.FAILED.value:
        raise SendRefused(409, "not_failed", f"the send is {row['status']}")
    item, source_row = await _load(row["item_id"])
    source = await _open(source_row)
    handed_over = False
    try:
        ref, _target = await _checks(item, source_row, source, row["text"], row["target_sha"])
        if not await db.inbox_update_send(send_id, store.ts(store.now()),
                                          expect_status=SendStatus.FAILED.value,
                                          status=SendStatus.RESERVED.value, error=""):
            existing = await db.inbox_open_send(row["item_id"]) or await db.inbox_get_send(send_id)
            return await store.send_out(existing, item=item, source=source_row)
        handed_over = True
        return await _deliver(source, {**row, "status": SendStatus.RESERVED.value}, item,
                              source_row, ref)
    finally:
        if not handed_over:
            await _close(source)


async def recover_reserved() -> int:
    """At startup: a ``reserved`` send was interrupted, it may have gone out -> unknown."""
    from database import db
    n = await db.inbox_reserved_to_unknown(store.ts(store.now()))
    if n:
        logger.warning("inbox: %d interrupted send(s) marked unknown", n)
    return n


async def reconcile_unknown(source_row: dict, source: Source) -> int:
    """On each pull: ask the source whether each ``unknown`` send of it went out. A hit
    records ``sent`` (and the item ``replied``); a miss leaves it ``unknown``. Returns the
    number settled. ``SourceAuthError`` propagates (the pull marks the source)."""
    from database import db
    from core.inbox.sources.base import SourceAuthError
    settled = 0
    for row in await db.inbox_sends((SendStatus.UNKNOWN.value,), source_id=source_row["id"]):
        item = await db.inbox_get_item(row["item_id"])
        if item is None:
            continue
        try:
            receipt = await source.find_sent(store.ref_from_json(item["thread_ref"]),
                                             store.parse_ts(row["created_at"]), row["text"])
        except SourceAuthError:
            raise
        except Exception as exc:  # noqa: BLE001 — the next pull asks again
            logger.info("inbox: find_sent failed for send %s: %s", row["id"], type(exc).__name__)
            continue
        if receipt is None:
            continue
        fields = {"external_msg_id": receipt.external_msg_id or None, "url": receipt.url or "",
                  "sent_at": store.ts(receipt.sent_at or store.now())}
        stamp = store.ts(store.now())
        ok = await db.inbox_update_send(row["id"], stamp, expect_status=SendStatus.UNKNOWN.value,
                                        status=SendStatus.SENT.value, **fields)
        if not ok and fields["external_msg_id"]:
            fields.pop("external_msg_id")
            ok = await db.inbox_update_send(row["id"], stamp,
                                            expect_status=SendStatus.UNKNOWN.value,
                                            status=SendStatus.SENT.value, **fields)
        if ok:
            settled += 1
            await _mark_replied(item)
    return settled
