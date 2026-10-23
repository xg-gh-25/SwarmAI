"""Hot-path cost of chat-turn-liveness P1 (docs/design/chat-turn-liveness.md S1).

Measures, on the event loop, with the real hub:
- refresh_activity when the activity changes (derive + set_activity + publish of a
  session_activity envelope), with 0 / 1 / 4 subscribers;
- refresh_activity when it does not change (the common case: every phase-changing
  publish calls it);
- TurnOpener.open (try_claim_turn + refresh);
- session_state_entry (the full snapshot: one pooled DB read of the queue) with
  0 / 10 / 100 queued rows.
Run (from backend/): .venv/bin/python scripts/bench_turn_liveness.py Prints p50 / p95 / max in µs.
"""
import asyncio
import os
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.getcwd())


def stats(xs):
    xs = sorted(xs)
    return (f"p50={statistics.median(xs):7.1f}µs  p95={xs[int(len(xs) * 0.95)]:7.1f}µs  "
            f"max={xs[-1]:7.1f}µs  n={len(xs)}")


async def main():
    from core import session_events, session_state
    from core.session_router import TurnOpener
    from core.session_unit import SessionState

    class U:
        state = SessionState.IDLE
        _stopping = False
        session_id = "bench"
        pid = None

    for nsub in (0, 1, 4):
        hub = session_events.SessionEventHub(buffer_size=1000, queue_size=100_000)
        session_events._hub = hub
        subs = [session_events._Subscriber(100_000) for _ in range(nsub)]
        hub._subs.update(subs)
        u = U()
        changed, same = [], []
        for i in range(2000):
            u.state = SessionState.STREAMING if i % 2 else SessionState.IDLE
            t = time.perf_counter_ns()
            session_state.refresh_activity("bench", u)
            changed.append((time.perf_counter_ns() - t) / 1000)
            t = time.perf_counter_ns()
            session_state.refresh_activity("bench", u)
            same.append((time.perf_counter_ns() - t) / 1000)
        print(f"refresh_activity changed  subs={nsub}: {stats(changed)}")
        print(f"refresh_activity same     subs={nsub}: {stats(same)}")

    hub = session_events.SessionEventHub()
    session_events._hub = hub
    opens = []
    for i in range(2000):
        sid = f"o{i}"
        t = time.perf_counter_ns()
        TurnOpener(source="chat", turn_client_id=None).open(sid, U())
        opens.append((time.perf_counter_ns() - t) / 1000)
    print(f"TurnOpener.open                 : {stats(opens)}")

    import core.session_pending as sp
    from database.sqlite import SQLiteDatabase
    d = tempfile.mkdtemp(dir=os.environ.get("KIROCREW_SCRATCH"))
    db = SQLiteDatabase(db_path=os.path.join(d, "b.db"))
    await db.initialize()
    sp._db_path_override = os.path.join(d, "b.db")
    for n in (0, 10, 100):
        sid = f"q{n}"
        for k in range(n):
            await sp.persist_pending(sid, user_message=f"queued {k} " * 20, content=None,
                                     agent_id="a", client_id=f"c{n}-{k}")
        u = U()
        u.session_id = sid
        xs = []
        for _ in range(200):
            t = time.perf_counter_ns()
            await session_state.session_state_entry(u)
            xs.append((time.perf_counter_ns() - t) / 1000)
        print(f"session_state_entry pending={n:3d}: {stats(xs)}")
    from database.sqlite import close_all_pools
    await close_all_pools()


asyncio.run(main())
