---
title: "Swarm Out: why Hive runs die, a diagnostics read, run states to merge"
date: 2026-11-02
created: 2026-11-02
updated: 2026-11-02
project: SwarmAI
---

# Swarm Out: why Hive runs die, how the dispatcher sees it, and which run states can go

Date: 2026-11-02. Status: draft r1, for review.
Scope: three parts asked together. (1) The cause of the coding runs that died on the
demo Hive today. (2) A read-only diagnostics read, so the agent that dispatched a run
can find out itself why the run's Hive session died. (3) An inventory of the run's
state fields (`state` / `state_reason`, `stage` / `why`, `settle.why_not`) and the
states that can be merged or deleted.

## 1. Why the runs died (observed, not inferred)

Evidence was read from the demo Hive over SSM on 2026-11-02 08:25 UTC, with the
read-only script described in §2 (kernel journal, the service's journal, `systemctl
show`, `free`).

**What happened.** Two Swarm Out runs shared the demo Hive: the retro-test fix
(session `38af272d`, dispatched 06:21:17Z) and a gbrain unit-test run dispatched from
another host session (session `2f3a82db`, 06:20:55Z).

- `06:26:57` the daemon's own sample (`lifecycle_manager.memory_sample`) shows the
  gbrain session's process tree at 4.3 GB, then 5.1 GB at `06:27:57` (the advisory
  warning at 2.5 GB fired, nothing acts on it).
- `06:28:20` kernel: `oom-kill:constraint=CONSTRAINT_MEMCG ...
  oom_memcg=/system.slice/swarmai-hive.service ... Killed process 3754732 (node)
  anon-rss:5314364kB`; the `claude` CLI of the other session was also selected.
- `06:28:20` systemd: `swarmai-hive.service: Failed with result 'oom-kill'` — the whole
  service stopped; both CLIs exited 143; `session_router.disconnect_all` killed the
  live units; the service came back at `06:28:34` (`NRestarts=2`).
- `06:23:43` the first death of the retro run has the same signature (a 5.0 GB `node`
  killed in the same memcg).

**Cause.** The released unit file (v2.4.0, still installed: `MemoryMax=6442450944` in
`systemctl show`) caps the WHOLE service — the daemon plus every task's builds and
tests — at 6 GB on a 15.6 GB machine, and leaves systemd's default `OOMPolicy=stop`.
One task's large command crossing 6 GB therefore stops the service and ends every run on
that Hive, including runs that were doing nothing heavy. "Pipeline coding runs cannot
finish on a Hive" was a wrong reading on my part: pipeline mode is not the factor; any
run sharing a Hive with a heavy command dies.

**Already fixed in code, not yet on any Hive.** Commits `204a262fb` and `397fcd7cf`
(another session, today) changed `hive/swarmai-hive.service` to `MemoryMax=infinity` and
`OOMPolicy=continue` (lines 37-47): the kernel kills the largest process only, the
service keeps running, the task sees its command fail. A Hive gets a new unit file only
through `update-hive`, which installs a published release (`routers/hive.py:633`
`update_instance` → `provisioner.update`), so both Hives keep the 6 GB unit until the
next release.

**What still holds after the fix (not addressed here).** Admission is decided once, at
spawn (`resource_monitor` spawn budget, 90% of memory); a session's growth after that is
not bounded. Two heavy commands on one Hive can still fill the machine; the kernel then
kills the largest process, which can be a run's own `claude` CLI. No such case has been
observed with the new unit; per P9 nothing is built for it until one is.

**Decision needed (only the person can make it, it changes the Hives).**
- A. Publish a release now (v2.4.1) and `update-hive` both Hives. Recommended: it is the
  existing path, and it also brings G2/G4 of the round-2 spec to the Hives.
- B. Hot-fix the two Hives over SSM now (a systemd drop-in with the two lines, then
  `daemon-reload`), and let the next release overwrite it. Faster, but it is a write on
  the Hives outside the release path.

## 2. Diagnostics the dispatching agent can read

**The gap.** When a run's Hive session dies, the host sees only its effects: the CLI's
exit code in the result, a probe that does not answer, `state_reason` turning
`not_held`. The cause is on the Hive (kernel log, service journal, memory), and today
only the person can read it (Settings → Hive; `hive.instance.get` is person-only in
`core/swarm_out_ops.py:131`). So the agent that answers for the run hands the question
to the person, which is the hand-back the zero-touch design removes.

**Channel: SSM, not the Hive's HTTP API.** The case that matters is the daemon being
down or restarting, when no HTTP route on the Hive answers. The host already reaches
each Hive over SSM Run Command for updates and health (`hive/provisioner.py:777`
`_wait_healthy_via_ssm`, `:1452` in `update`), with the SwarmAI account's `manage`
session (`hive/accounts.py:102` `session_for`). Today's evidence was read exactly this
way, in about 10 s.

**Shape.**
- `core/hive_diagnostics.py`: `read(dispatch_id) -> dict`. It resolves the Hive, the
  session id and the time window from the host's own run row (`sent_at` to the result's
  `ended_at` / records `last_at`, plus 5 minutes), and runs ONE fixed script. Nothing in
  the script comes from the caller: the only interpolated values are the window (two
  timestamps the host formats from datetimes it parsed) and the session id (checked
  against `hive_records.SESSION_RE`). No command text is accepted, so the read cannot
  become a remote shell.
- The script reads only: `systemctl show swarmai-hive` (ActiveEnterTimestamp,
  NRestarts, Result, MemoryMax, MemoryCurrent, OOMPolicy), `free -m`, `uptime`, kernel
  journal lines matching OOM in the window, and the service's journal in the window
  restricted to lines naming this run's session id plus the service-level lines
  (`memory_sample`, `memory_warning`, `oom-kill`, `Failed with result`,
  `disconnect_all`, start/stop). Output is bounded (line count and bytes per part) and
  returned as fields, not one text blob.
- `findings`: a deterministic list built from those fields, no model: `oom_kill` (a
  kernel OOM line in the window, with the victim's name, RSS and memcg), `service_restart`
  (ActiveEnterTimestamp inside the window, with the previous result), `session_error`
  (the session's own error lines), `none_seen`.
- Route `GET /api/swarm-out/runs/{dispatch_id}/diagnostics`, row in `swarm_out_ops`
  with callers `(person, owner_agent)`, weight `read`; CLI `swarm_out diagnose
  <dispatch_id>`. A Hive in another account, stopped, or without SSM answers with the
  reason (`not_reachable: <why>`), never a guess.
- The text it returns is Hive-written and untrusted (the same marker the CLI already
  prints around Hive data, `[end of Hive-written data]`).
- No new run state. It is a read the agent uses before it decides re-run, take over or
  discard; the s_swarm-out skill says to read it when a run ended in an error or was
  cut off.

**Security notes for review.** SSM Run Command executes as root on the Hive, so the
safety of this read rests on the script being fixed text with two validated values
(SECURITY-BASELINE A3: no shell built from untrusted input). Journal lines can contain
other sessions' text; the filter keeps this run's session lines and the service-level
lines only. The agent and the daemon share an OS user on the host, so this read gives
the agent nothing it could not already reach with the AWS keys (ops registry §2.4); it
removes a hand-back, it does not widen a boundary.

**Tests (written before the code).**
- The script for a given row equals a pinned template with only the window and session
  id substituted; a session id or timestamp outside its pattern raises before SSM.
- `findings` from recorded fixtures of today's two kills: `oom_kill` with the victim
  `node` 5.3 GB and `service_restart`; from a quiet fixture: `none_seen`.
- Journal filtering keeps this session's lines and the service lines, drops another
  session's lines.
- The ops-registry test sees the new route and that the CLI calls it.
- A Hive whose account is not the SwarmAI account answers `not_reachable` without
  calling SSM.

## 3. Run states: what to merge, delete, or leave

Source: a read-only inventory of every value of `state` / `state_reason`
(`core/swarm_out.py:1387-1421`, `run_state`), `stage` / `why` (`core/swarm_out_settle.py:473-500`,
`derive`), `settle.code` / `verdict` / `why_not` (`swarm_out_settle.py:421-516`) and
`actions` (`swarm_out_settle.py:348-389`), with every backend and desktop reader. The
claims used below were re-read at the cited lines.

**The rule.** A value earns its own place only if some reader behaves differently for
it. A value whose only effect is the label shown is text, and text never caused a bug
in the two E2E rounds; every bug found sat on a branch (re-send, dead session, already
landed, token, lessons pending). So this part merges and deletes only where a branch or a
field goes away, and leaves label-only values as they are.

| # | Today | Change | Why | Readers to convert (R27) |
|---|---|---|---|---|
| S1 | stages `discarded` and `expired` | one stage `closed`; the old name moves to `why` (`discarded` · `reverted` · `expired` · `hive_gone`) | identical everywhere but the label: no actions (`_actions` 389), one funnel gate (`swarm_out_record.py:288`), one done group (`runReview.ts:54`), one tone (`RunOverlay.tsx:336`) | `swarm_out_settle.STAGES` 332, `_OUTCOME_STAGE` 334, derive 478/480/492, `swarm_out_record` 288, desktop `SwarmOutStage`, `DONE_STAGES`, `STAGE_TONE`, labels, i18n |
| S2 | settled outcomes `gone` and `expired` | one outcome `expired` with the reason kept | both closed (`closed_state` 319), both shown as `expired` (`_OUTCOME_STAGE` 334), both "other" in pipeline rows (`pipelineRows.ts:109`) | settle 319/334/556/620, `pipelineRows.ts`; records already written keep reading (map `gone` → `expired` on read) |
| S3 | `running` / `waiting` booleans next to `state` | delete; readers read `state`, and the two acts that remove a run's Hive workspace decide `unknown` the same way: not while the Hive does not answer | pure functions of `state` (`swarm_out.py:1754-1755`), and the readers disagree on `unknown` (`running` is `None`): a discard is refused (`swarm_out_actor.py:265`), but `derive` lets it settle (`swarm_out_settle.py:522`), and settling tells the Hive to release the workspace (`released: False` at 566, released at 633/662), which ends the session's runs (G4). A turn the probe could not see may still be running there. Expiry after `RETENTION_DAYS` idle (612) stays allowed for `unknown`: the Hive sweeps the workspace itself by then, and blocking it would keep a run on a Hive that never answers again open for good | settle 402/481/489/522/612, actor 265, `useSwarmOutActivity.ts:51`, desktop fallbacks (`live.ts:74`, `tone.ts:50`, `runReview.ts:38-40`) |
| S4 | `settle.code = "rejected"` | delete the branch | no writer: `hive_return` writes only `prepared` and `landed` (`hive_return.py:411, 613`); only records from an older design hold it | `_code` 274-276, `code_done` 462; an old record reading `rejected` maps to `none` |
| S5 | `settle.verdict` on the row | keep it inside `derive`, drop it from the row | no reader in backend or desktop; only the TypeScript type declares it | `derive` return, `swarmOut.ts` type |
| S6 | `state_reason = hive_updating` shown as "the Hive didn't answer" | keep the value, give it its own label ("the Hive is updating") | the person should wait, not act; today the label tells them something is wrong | `live.ts` label map, i18n |
| S7 | i18n `why.paused` | delete | never produced: a paused result is provisional, so the run shows `running` | `en.json:1876` (and other locales) |

**Left as they are, on purpose.**
- `state_reason` pairs nothing branches on (`idle` / `not_held`; `decision` /
  `question` / `waiting_input`; `streaming` / an activity name): they are what the probe
  saw. They cost nothing, and `not_held` is what the diagnostics of §2 start from.
- The eight `why` values of a stopped run (`turn_limit`, `error`, `crashed`, ...): labels
  from the result's own `ended`.
- `pulled_out` / `closed` / `unqueued`: local variables of `derive`, not on the row.

**Known, out of scope.** A person's command on a run is not checked against the run's
`actions`; only `closed_state` guards it (`routers/swarm_out.py:150-151`,
`swarm_out.py:1835-1844`). An agent's is. Whether the person's commands should be held to
the same list is a separate decision; recorded here, not changed.

**Tests (red before, green after).**
- S1/S2: every path that ended a run (discard, revert, Hive gone, expiry) gives stage
  `closed` with the old name in `why`; an old record with outcome `gone` reads `expired`;
  the desktop groups and tones `closed` once.
- S3: a row with `state = unknown`, a final result and a pass verdict is not settled and
  is refused for discard; the same row idle past `RETENTION_DAYS` expires; a row with no
  `running` / `waiting` keys derives exactly what it derived with them.
- S4: an old record whose returns are all `rejected` derives `code = none`.
- S6: the label for `hive_updating` differs from `hive_silent`.

## 4. Order and verification plan

1. **§1 release (person's decision A or B).** Then on each Hive: `systemctl show
   swarmai-hive -p MemoryMax,OOMPolicy` reads `infinity` / `continue`.
2. **§2 diagnostics**, its tests first; then live: `swarm_out diagnose so_c2d9593ffa79`
   returns `oom_kill` (`node`, about 5.3 GB) and `service_restart` for 06:28 from the demo
   Hive.
3. **§3 S1–S7** in one change, R27 consumer grep for every field; backend and desktop
   tests of the converted readers.
4. **E2E round 3 after the release:** two runs on one Hive at the same time, one of them
   running a command over 6 GB (a full type check of a large repo). Must hold: the heavy
   command fails alone, the other run finishes, both Hives keep running, and `diagnose`
   on the heavy run reports the kill.

Review: one zero-context review of this document's premise and its claims about the
code (AGENT § Iterative Refinement); then correctness for §2 and §3 and security for §2
after the code.
