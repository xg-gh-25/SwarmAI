"""The Inbox runner: pulls each source on its own interval, triages, stores.

Design: ``docs/design/2026-11-20-inbox.md`` §9. One asyncio task in the backend lifespan
(``main.py``), started only when ``should_run(backend_mode, owns_backend_json)``: in
``daemon`` (macOS) and ``subprocess`` (Windows / Linux desktop) mode, by the backend that
owns ``backend.json``. Never on a Hive: it has no access to the person's channels and
should not hold their messages. Not the job scheduler: that wakes hourly, in UTC, and only
in daemon / hive mode.

The loop sleeps until the next source is due (``interval_s`` since its last attempt;
default 900 s for a conversation source, 21,600 s for the feed source), pulls only inside
the local pull window (default 07:00-22:00, app setting ``inbox_pull_window`` like
``"07:00-22:00"``), one source at a time, and after a sleep pulls once from the cursor
(no catch-up of missed intervals). The person's refresh and retry pull now
(``pull_now``), under the same lock.

One pull of a source:

1. ``whoami`` and ``can_send``, then ``pull(cursor)``.
2. ``find_sent`` for each ``unknown`` send of the source (``send.reconcile_unknown``).
3. Each message: the person's own (``Identity.is_me``) is never stored; it closes the open
   REPLY / PROPOSED items of its thread received at or before it (``replied``). Any other
   gets ``triage.rule_lane`` and is stored (one row per ``(source_id, external_id)``); new
   items and items an earlier batch did not cover go to ``triage.triage`` in batches, each
   candidate id being the item's row id. The answer's lane is applied, never below the
   rule's floor and never to PROPOSED (only ``inbox submit`` makes those).
4. Health: ``SourceAuthError`` -> ``auth_failed`` with its ``fix`` (Needs You shows it),
   any other error -> ``error``; neither is retried before the next interval. A source
   that pulls but cannot send is ``read_only``, with its ``status_detail()`` kept in the
   ``fix`` column (the channel row shows why).

Removing a source (``remove_source``) takes the pull lock, so no pull of it is half-way
when its rows go and none writes items for it afterwards.

The first pull after 07:00 local each day also writes the trend summary
(``trends.summarize`` over the last 24 h of TREND items).

Triage, drafting and the trend summary are async contract functions (``core/inbox/
{triage,trends}.py``); their model calls run off the event loop inside them. The runner's
own blocking work (file reads) runs on the Inbox pool (``store.run_blocking``).

Public: ``should_run``, ``start``, ``stop``, ``wake``, ``pull_now``, ``run_cycle``,
``pull_source``, ``remove_source``, ``pull_window``, ``next_delay``, ``kick_summary``.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, time as dtime, timedelta
from typing import Optional

from core.inbox import send, store, triage, trends
from core.inbox.models import Candidate, Lane, Message, SourceCategory, SourceHealth, TriageResult
from core.inbox.sources.base import SourceAuthError

logger = logging.getLogger(__name__)

RUN_MODES = ("daemon", "subprocess")
DEFAULT_WINDOW = (dtime(7, 0), dtime(22, 0))
TRIAGE_BATCH = 40
MAX_SLEEP_S = 900.0   # also how soon a config.yaml feed edit made elsewhere is seen
MIN_SLEEP_S = 5.0
TREND_SUMMARY_HOUR = 7
SUMMARY_TIMEOUT_S = 300.0
REMOVE_WAIT_S = 15.0   # how long a removal waits for a running pull before answering "busy"

_LANE_RANK = {Lane.REPLY: 0, Lane.AWARE: 1, Lane.TREND: 2, Lane.SKIP: 3}

_task: Optional[asyncio.Task] = None
_loop_state: dict = {}


def should_run(backend_mode: str, owns_backend_json: bool) -> bool:
    """The runner runs in daemon and subprocess mode, by the backend that owns
    backend.json: never on a Hive, never in dev, never twice on one data dir."""
    return backend_mode in RUN_MODES and bool(owns_backend_json)


def _primitives() -> tuple[asyncio.Lock, asyncio.Event]:
    """The pull lock and the wake event of the running loop (a test's loop is new each
    time; one daemon loop in production)."""
    loop = asyncio.get_running_loop()
    if _loop_state.get("loop") is not loop:
        _loop_state.update(loop=loop, lock=asyncio.Lock(), wake=asyncio.Event(), summary=None)
    return _loop_state["lock"], _loop_state["wake"]


def wake() -> None:
    """Re-plan now (a source or the feed configuration changed)."""
    try:
        _primitives()[1].set()
    except RuntimeError:   # no running loop: nothing is sleeping
        pass


# ── schedule ─────────────────────────────────────────────────────────────────

def _parse_window(value: object) -> tuple[dtime, dtime]:
    try:
        start, end = str(value).split("-")
        a, b = dtime.fromisoformat(start.strip()), dtime.fromisoformat(end.strip())
        return (a, b) if a < b else DEFAULT_WINDOW
    except (ValueError, AttributeError):
        return DEFAULT_WINDOW


def pull_window() -> tuple[dtime, dtime]:
    try:
        from core.app_config_manager import AppConfigManager
        value = AppConfigManager.instance().get("inbox_pull_window")
    except Exception as exc:  # noqa: BLE001 — no config: the default window
        logger.debug("inbox: pull window setting unreadable (%s)", type(exc).__name__)
        value = None
    return _parse_window(value) if value else DEFAULT_WINDOW


def _in_window(local: datetime, window: tuple[dtime, dtime]) -> bool:
    return window[0] <= local.time() < window[1]


def _due_at(row: dict) -> datetime:
    last = store.parse_ts(row.get("last_attempt_at"))
    if last is None:
        return datetime.min.replace(tzinfo=store.now().tzinfo)
    return last + timedelta(seconds=int(row["interval_s"]))


def due_sources(rows: list[dict], at: datetime) -> list[dict]:
    return [r for r in rows if r["enabled"] and _due_at(r) <= at]


def next_delay(rows: list[dict], at: datetime, window: tuple[dtime, dtime]) -> float:
    """Seconds to sleep: until the next due source, or until the window opens."""
    local = at.astimezone()
    if not _in_window(local, window):
        start = local.replace(hour=window[0].hour, minute=window[0].minute, second=0,
                              microsecond=0)
        if start <= local:
            start += timedelta(days=1)
        return max(MIN_SLEEP_S, min((start - local).total_seconds(), 6 * 3600.0))
    enabled = [r for r in rows if r["enabled"]]
    if not enabled:
        return MAX_SLEEP_S
    soonest = min(_due_at(r) for r in enabled)
    return max(MIN_SLEEP_S, min((soonest - at).total_seconds(), MAX_SLEEP_S))


# ── loop ─────────────────────────────────────────────────────────────────────

async def run_forever() -> None:
    try:
        await send.recover_reserved()
    except Exception:  # noqa: BLE001 — pulls still run; the next start recovers again
        logger.exception("inbox runner: recovering interrupted sends failed")
    while True:
        lock, event = _primitives()
        try:
            await run_cycle()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — one bad cycle never ends the runner
            logger.exception("inbox runner: cycle failed")
        from database import db
        try:
            rows = await db.inbox_list_sources()
        except Exception as exc:  # noqa: BLE001
            logger.warning("inbox runner: sources unreadable (%s)", type(exc).__name__)
            rows = []
        delay = next_delay(rows, store.now(), pull_window())
        try:
            await asyncio.wait_for(event.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass
        event.clear()


def start() -> asyncio.Task:
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_running_loop().create_task(run_forever(), name="inbox-runner")
        _task.add_done_callback(_log_end)
        logger.info("Inbox runner started")
    return _task


def _log_end(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.error("inbox runner ended unexpectedly", exc_info=task.exception())


async def stop() -> None:
    """Stop the runner and a trend summary still running (no model call on the person's
    messages outlives the runner)."""
    global _task
    task, _task = _task, None
    summary = _loop_state.pop("summary", None)
    for t in (task, summary):
        if t is not None and not t.done():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
    if task is not None:
        logger.info("Inbox runner stopped")


async def run_cycle(at: Optional[datetime] = None) -> int:
    """Pull every due source (inside the window), one at a time. Returns how many."""
    from database import db
    at = at or store.now()
    if not _in_window(at.astimezone(), pull_window()):
        return 0
    await store.sync_feed_source()
    pulled = 0
    for row in due_sources(await db.inbox_list_sources(), at):
        await _pull_locked(row["id"], at)
        pulled += 1
    if pulled:
        kick_summary()
    return pulled


async def pull_now(source_ids: Optional[list[str]] = None) -> int:
    """The person's refresh / retry: pull now, ignoring interval and window."""
    from database import db
    await store.sync_feed_source()
    rows = [r for r in await db.inbox_list_sources() if r["enabled"]]
    if source_ids is not None:
        rows = [r for r in rows if r["id"] in source_ids]
    for row in rows:
        await _pull_locked(row["id"])
    if rows:
        kick_summary()
    return len(rows)


def kick_summary() -> Optional[asyncio.Task]:
    """Start the daily trend summary in the background, unless one is already running.
    It never holds the pull lock: a slow model call delays no pull, and two kicks (a
    refresh during a cycle) write the day's summary once."""
    _primitives()
    running = _loop_state.get("summary")
    if running is not None and not running.done():
        return None
    task = asyncio.get_running_loop().create_task(_summarize_once(), name="inbox-trend-summary")
    _loop_state["summary"] = task
    return task


async def _summarize_once() -> None:
    try:
        # bounds a model call that never returns; a slow one finishes well inside it
        await asyncio.wait_for(maybe_summarize_trends(), SUMMARY_TIMEOUT_S)
    except asyncio.TimeoutError:
        logger.warning("inbox: trend summary gave no answer within %d s", int(SUMMARY_TIMEOUT_S))
    except Exception:  # noqa: BLE001 — tried again after the next pull
        logger.exception("inbox: trend summary failed")


class PullRunning(Exception):
    """A pull holds the lock longer than a removal waits."""


async def remove_source(source_id: str) -> Optional[dict]:
    """Remove a source and everything it pulled, between pulls (``store.delete_source_rows``:
    the counts removed, ``None`` when there is no such source, ``SendInFlight`` while one of
    its sends is reserved). Waits at most ``REMOVE_WAIT_S`` for a running pull, then raises
    ``PullRunning``: the person tries again, nothing is half removed."""
    lock, _ = _primitives()
    try:
        await asyncio.wait_for(lock.acquire(), REMOVE_WAIT_S)
    except asyncio.TimeoutError:
        raise PullRunning(source_id) from None
    try:
        return await store.delete_source_rows(source_id)
    finally:
        lock.release()


async def _pull_locked(source_id: str, at: Optional[datetime] = None) -> None:
    from database import db
    lock, _ = _primitives()
    async with lock:
        row = await db.inbox_get_source(source_id)   # read again under the lock
        if row is not None:
            await pull_source(row, at)


def _err(exc: BaseException) -> str:
    from channels.egress_redactor import redact_text
    return redact_text(f"{type(exc).__name__}: {exc}")[:500]


async def pull_source(row: dict, at: Optional[datetime] = None) -> str:
    """One pull of one source; records its health. Returns the health. ``at`` is the
    attempt time the schedule counts the interval from (default: now)."""
    from database import db
    sid = row["id"]
    await db.inbox_update_source(sid, store.ts(store.now()),
                                 last_attempt_at=store.ts(at or store.now()))
    category = SourceCategory(row["category"])
    source = None
    try:
        source = await store.open_source(row)
        me = await source.whoami()
        can_send = category is SourceCategory.CONVERSATION and bool(await source.can_send())
        result = await source.pull(row.get("cursor"))
        await send.reconcile_unknown(row, source)
        await ingest(row, me, category, list(result.messages))
        health = (SourceHealth.OK if category is SourceCategory.FEED or can_send
                  else SourceHealth.READ_ONLY).value
        detail = store.status_detail_of(source) if health == SourceHealth.READ_ONLY.value else ""
        stamp = store.ts(store.now())
        await db.inbox_update_source(
            sid, stamp, cursor=result.cursor if result.cursor is not None else row.get("cursor"),
            me=store.identity_to_json(me), can_send=1 if can_send else 0, health=health,
            fix=detail, last_error="", last_pull_at=stamp)
        return health
    except SourceAuthError as exc:
        logger.warning("inbox: source %s needs sign-in again", sid)
        await db.inbox_update_source(sid, store.ts(store.now()),
                                     health=SourceHealth.AUTH_FAILED.value,
                                     fix=str(exc.fix or "")[:300], last_error=_err(exc))
        return SourceHealth.AUTH_FAILED.value
    except Exception as exc:  # noqa: BLE001 — retried at the next interval
        logger.warning("inbox: pull of source %s failed: %s", sid, type(exc).__name__)
        await db.inbox_update_source(sid, store.ts(store.now()), health=SourceHealth.ERROR.value,
                                     fix="", last_error=_err(exc))
        return SourceHealth.ERROR.value
    finally:
        if source is not None:
            try:
                await source.close()
            except Exception:  # noqa: BLE001
                logger.debug("inbox: source close failed", exc_info=True)


# ── ingest ───────────────────────────────────────────────────────────────────

def clamp_lane(rule: Lane, floor: Optional[Lane], answer: Lane) -> Lane:
    """The lane an answer may set: never PROPOSED (only submit makes those), never below
    the rule floor (a direct message may go to AWARE, never to SKIP)."""
    if answer not in _LANE_RANK:
        return rule
    if floor is not None and floor in _LANE_RANK and _LANE_RANK[answer] > _LANE_RANK[floor]:
        return floor
    return answer


async def ingest(row: dict, me, category: SourceCategory, messages: list[Message]) -> dict:
    """Store and triage one pull's messages. Returns counts for the log and tests."""
    from database import db
    sid = row["id"]
    own: list[Message] = []
    kept: list[tuple[Message, Lane, Optional[Lane]]] = []
    for msg in sorted(messages, key=lambda m: m.received_at):
        if me.is_me(msg.author_id):
            own.append(msg)
            continue
        verdict = triage.rule_lane(msg, me, category)
        if verdict is None:          # the rules say it is the person's own
            own.append(msg)
            continue
        kept.append((msg, verdict.lane, verdict.floor))
    stored = await db.inbox_insert_items(
        [store.message_to_item_row(sid, m, lane, floor) for m, lane, floor in kept])
    candidates: list[Candidate] = []
    seen: set[str] = set()
    by_thread: dict[str, list[Message]] = {}
    for msg, lane, floor in kept:
        item_id, inserted = stored[msg.external_id]
        context = tuple(by_thread.get(msg.thread.key(), [])[-3:])
        by_thread.setdefault(msg.thread.key(), []).append(msg)
        if inserted and item_id not in seen:
            seen.add(item_id)
            candidates.append(Candidate(id=item_id, source_kind=msg.thread.source_kind,
                                        message=msg, rule_lane=lane, floor=floor,
                                        thread_context=context))
    for left in await db.inbox_untriaged_items(sid):
        if left["id"] in seen:
            continue
        seen.add(left["id"])
        candidates.append(Candidate(
            id=left["id"], source_kind=left["source_kind"], message=store.item_message(left),
            rule_lane=Lane(left["rule_lane"]),
            floor=Lane(left["floor"]) if left["floor"] else None))
    # the person's own messages close their threads: REPLY / PROPOSED items at once (by
    # rule a direct ask the person has now answered); an item of another lane no triage
    # answer covers yet is marked answered, so an answer raising it to REPLY (now or at a
    # later pull) closes it instead of making an already answered item owed again
    closed = 0
    stamp = store.ts(store.now())
    for msg in own:
        closed += await db.inbox_close_thread(sid, msg.thread.key(), store.ts(msg.received_at), stamp)
    triaged = await _triage(sid, candidates) if candidates else 0
    return {"stored": sum(1 for _i, ins in stored.values() if ins), "closed": closed,
            "triaged": triaged, "own": len(own)}


async def _context(source_id: str) -> triage.TriageContext:
    from database import db
    profile = await store.run_blocking(_user_profile)
    examples = []
    for fb in await db.inbox_recent_feedback(source_id):
        if fb["action"] == "relane" and fb["from_lane"] and fb["to_lane"]:
            item = await db.inbox_get_item(fb["item_id"])
            title = (item or {}).get("title") or (item or {}).get("author") or ""
            examples.append(f"{fb['from_lane']} -> {fb['to_lane']}: {title[:120]}")
    return triage.TriageContext(user_profile=profile, examples=tuple(examples))


_LANGUAGES = {"zh": "Chinese (简体中文)", "en": "English"}


def _app_language() -> str:
    try:
        from core.app_config_manager import AppConfigManager
        return _LANGUAGES.get(str(AppConfigManager.instance().get("ui_language") or ""), "")
    except Exception as exc:  # noqa: BLE001 — no setting: the profile's language
        logger.debug("inbox: ui_language unreadable (%s)", type(exc).__name__)
        return ""


def _user_profile() -> str:
    """The person's profile (USER.md) as triage and trend context, after the language
    they use the app in when the app has said it (``ui_language``): the Inbox's own
    sentences (the reason line, trend titles) are written in that language. Measured: a
    profile written in English made every reason English for a person using the app in
    Chinese. Blocking: a file read."""
    language = _app_language()
    head = f"App language: {language}\n\n" if language else ""
    try:
        from core.project_registry import get_swarmws
        path = get_swarmws() / ".context" / "USER.md"
        return head + (path.read_text(encoding="utf-8")[:3000] if path.is_file() else "")
    except Exception as exc:  # noqa: BLE001 — no profile: triage without it
        logger.debug("inbox: USER.md unreadable (%s)", type(exc).__name__)
        return head


async def _triage(source_id: str, candidates: list[Candidate]) -> int:
    from database import db
    context = await _context(source_id)
    done = 0
    for i in range(0, len(candidates), TRIAGE_BATCH):
        batch = candidates[i:i + TRIAGE_BATCH]
        try:
            results = await triage.triage(batch, context)
        except Exception as exc:  # noqa: BLE001 — TriageRejected or a model error: next pull
            logger.info("inbox: triage batch of %d not applied (%s)", len(batch),
                        type(exc).__name__)
            continue
        if not _covers_exactly(batch, results):
            logger.info("inbox: triage answer does not cover the batch exactly; not applied")
            continue
        by_id = {c.id: c for c in batch}
        stamp = store.ts(store.now())
        for res in results:
            cand = by_id[res.id]
            lane = clamp_lane(cand.rule_lane, cand.floor, res.lane)
            await db.inbox_update_item(
                res.id, stamp, lane=lane.value, priority=max(0, min(3, int(res.priority))),
                reason=str(res.reason or "")[:300], triaged_at=stamp)
            await db.inbox_close_if_answered(res.id, stamp)
            done += 1
    return done


def _covers_exactly(batch: list[Candidate], results: list[TriageResult]) -> bool:
    ids = [r.id for r in results]
    return len(ids) == len(set(ids)) and set(ids) == {c.id for c in batch}


# ── trend summary ────────────────────────────────────────────────────────────

async def maybe_summarize_trends() -> bool:
    """The first pull after 07:00 local each day writes the day's trend summary."""
    from database import db
    local = datetime.now().astimezone()
    if local.hour < TREND_SUMMARY_HOUR:
        return False
    day = local.date().isoformat()
    latest = await db.inbox_latest_trend_summary()
    if latest and latest["day"] == day:
        return False
    rows = await db.inbox_items_since(Lane.TREND.value,
                                      store.ts(store.now() - timedelta(hours=24)))
    if not rows:
        return False
    try:
        points = await trends.summarize([store.item_message(r) for r in rows],
                                        await store.run_blocking(_user_profile))
    except NotImplementedError:
        return False
    except Exception as exc:  # noqa: BLE001 — tried again at the next pull
        logger.info("inbox: trend summary failed (%s)", type(exc).__name__)
        return False
    # external ids are unique per source only: a point links to an item only when exactly
    # one trend item has its id
    by_ext: dict[str, list[dict]] = {}
    for r in rows:
        by_ext.setdefault(r["external_id"], []).append(r)
    out = []
    for p in points:
        matches = by_ext.get(p.external_id or "") or []
        item = matches[0] if len(matches) == 1 else None
        out.append({"title": p.title, "why": p.why, "source_label": p.source_label,
                    "item_id": item["id"] if item else None, "url": item["url"] if item else ""})
    await db.inbox_put_trend_summary({"id": store.new_id("ts"), "day": day,
                                      "generated_at": store.ts(store.now()),
                                      "points": json.dumps(out, ensure_ascii=False)})
    return True
