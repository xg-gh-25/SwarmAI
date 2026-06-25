# session-health-probe — Runtime Daemon + Session Liveness

A **zero-LLM, deterministic** snapshot answering: *is the running daemon healthy,
and are its live sessions progressing (not wedged) right now?*

## When to run
- On demand ("is the daemon healthy?", "are sessions stuck?")
- Automatically every 15 min via the `session-health-probe` system job
- After a deploy, to confirm the new binary is live and sessions recovered

## What it checks (all deterministic)
1. **daemon_health** — `/health` 200 + `db_healthy` + `channel_gateway` started.
2. **deployed_commit** (optional) — deployed binary `.version` == expected commit
   (catches "half-branch" / stale-binary regressions).
3. **total_rss** — process-tree RSS < 3500 MB (PROACTIVE reclaim threshold).
4. **no_wedged_sessions** — for each STREAMING session, a **double-signal**
   wedged check (RP41-safe): a session is wedged ONLY when BOTH
   (a) `tree_cpu_seconds` delta over a ~2s interval < epsilon (nothing in the
   process tree is burning CPU) **AND** (b) it emitted no new log events in the
   window. A healthy long turn waiting on Bedrock IO burns CPU OR emits events —
   either clears it. Unreadable signal → **fail-safe, no alarm** (a muted probe
   is worse than a quiet one).
5. **no_unrecovered_events** — failure markers (`force_unstick`,
   `streaming_timeout`, `SIGKILL`, `stuck`, `dumb-spawn-kill`) NOT followed by a
   recovery marker (`Retry`, `--resume`, `recovered`, `heal`, `COLD`). A
   self-healed timeout is NOT a fault.

## How to run
```bash
python -c "from jobs.handlers.session_health_probe import run_session_health_probe as r; import json; print(json.dumps(r(dry_run=True), indent=2))"
```
`dry_run=True` never sends a Slack notification. Drop it (or run the job) to let a
RED result notify Slack via s_notify.

## Output
A JobResult dict: `probe_status` (healthy|degraded|error), per-check `checks`,
`failed` names, `summary`. The 15-min job maps healthy→success, degraded→failed
(so a red shows in the job dashboard) and notifies Slack on red.

## Relationship to OS Eval
This is the PASSIVE axis (observe the live system). The ACTIVE axis — proving the
recovery paths still WORK by injecting a fault — lives in OS Eval as the
`runtime_health` evaluator + `GS_RTH001` golden_set case
(`backend/scripts/fault_inject_recovery.py`). Run that via `eval_runner.py` to see
runtime-recovery health as a pass/fail eval row.
