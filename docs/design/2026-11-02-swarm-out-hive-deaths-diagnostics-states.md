---
title: "Swarm Out: why Hive runs die, a diagnostics read, run states to merge"
date: 2026-11-02
created: 2026-11-02
updated: 2026-11-02
project: SwarmAI
status: r2, review findings folded in; waiting for XG
---

# Swarm Out: why Hive runs die, how the dispatcher sees it, and which run states can go

Scope: three parts asked together. (1) The cause of the coding runs that died on the
demo Hive on 2026-11-02. (2) A read-only diagnostics read, so the agent that dispatched
a run can find out itself why the run's Hive session died. (3) An inventory of the run's
state fields (`state` / `state_reason`, `stage` / `why`, `settle`) and the values that
can be merged or deleted.

r2 changes (one zero-context review of r1, all findings re-read in the code): §2 gets
an output budget and reads systemd's own lines for restarts (H2, M1), a stricter session
id and a named account purpose (M2, M3); §3 adds the Needs You reader and settled rows
(H1, M7), drops S2 and S4 (stored records keep those values), and S3 now goes first,
before the v2.4.1 Hives run G4 (M4, M5); §4 notes the follow fingerprints change once
(M6).

## 1. Why the runs died (observed, not inferred)

Evidence was read from the demo Hive over SSM at 2026-11-02 08:25 UTC (kernel journal,
the service's journal, `systemctl show`, `free`), with the read §2 makes a command.

**What happened.** Two Swarm Out runs shared the demo Hive: the retro-test fix
(session `38af272d`, dispatched 06:21:17Z) and a gbrain unit-test run dispatched from
another host session (session `2f3a82db`, 06:20:55Z).

- `06:26:57` the daemon's own sample (`lifecycle_manager.memory_sample`) shows the
  gbrain session's process tree at 4.3 GB, and 5.1 GB at `06:27:57` (the 2.5 GB advisory
  warning fired; nothing acts on it).
- `06:28:20` kernel: `oom-kill:constraint=CONSTRAINT_MEMCG ...
  oom_memcg=/system.slice/swarmai-hive.service ... Killed process 3754732 (node)
  anon-rss:5314364kB`; the `claude` CLI of the other session was also selected.
- `06:28:20` systemd: `swarmai-hive.service: Failed with result 'oom-kill'`: the whole
  service stopped, both CLIs exited 143, `session_router.disconnect_all` killed the
  live units; the service came back at `06:28:34` (`NRestarts=2`).
- `06:23:43` the retro run's first death has the same kernel signature (a 5.0 GB `node`
  killed in the same memcg). Which session owned that process is not in the capture.

**Cause.** The released unit file (v2.4.0, installed: `MemoryMax=6442450944` in
`systemctl show`) caps the WHOLE service, the daemon plus every task's builds and tests,
at 6 GB on a 15.6 GB machine, with systemd's default `OOMPolicy=stop`. One task's large
command crossing 6 GB stops the service and ends every run on that Hive. "Pipeline
coding runs cannot finish on a Hive" was a wrong reading on my part: at 06:28 the process
that grew was the gbrain run's; the retro run died with it.

**Fixed in code, shipping now.** `204a262fb` and `397fcd7cf` set `MemoryMax=infinity`
and `OOMPolicy=continue` (`hive/swarmai-hive.service:37-47`): the kernel kills the
largest process only, the service keeps running, the task sees its command fail.
`provisioner.update` installs a changed unit (`hive/provisioner.py:1399-1408`); new,
retried and replaced Hives get it from `user_data.py:158`. Tag `v2.4.1` (`f4af63f59`)
contains both commits and G4 of the round-2 spec; its Release workflow was running at
the time of writing. After it publishes, `update-hive` both Hives, then check
`systemctl show swarmai-hive -p MemoryMax,OOMPolicy` reads `infinity` / `continue`.

**What still holds after the fix (not addressed here).** Admission is decided once, at
spawn (`core/resource_monitor.py:178`, 90% of memory); growth after that is not bounded,
and there is no swap. Two heavy commands can still fill the machine; it can stall before
the kernel kills, and the largest process can be a run's own `claude` CLI. No case has
been observed with the new unit; per P9 nothing is built for it until one is. Round 3
(§4) expects a Hive that may stop answering for a while.

## 2. Diagnostics the dispatching agent can read

**The gap.** When a run's Hive session dies, the host sees only effects: the CLI's exit
code in the result, a probe that does not answer, `state_reason` turning `not_held`. The
cause is on the Hive (kernel log, service journal, memory). No one can read it from the
app today: `hive.instance.get` (`core/swarm_out_ops.py:131`) returns the instance's
fields and credentials, no logs. So the agent that answers for the run hands the question
to the person, who cannot answer it either without a shell on the Hive.

**Channel: SSM, not the Hive's HTTP API.** The case that matters is the daemon being
down or restarting, when no HTTP route on the Hive answers. The host already runs SSM
commands on each Hive for health and updates (`hive/provisioner.py:777`
`_wait_healthy_via_ssm`, `:1452` in `update`). The read uses the `manage` session
(`hive/accounts.py:102` `session_for`), which only the SwarmAI account grants; a Hive in
another account answers `not_reachable: other_account` without calling SSM. (`read` is
an other-account purpose, `accounts.py:49`, so it must not be used here.)

**Shape.**
- `core/hive_diagnostics.py`: `read(dispatch_id) -> dict`. It resolves the Hive, the
  session id and the window from the host's own run row: from `sent_at` to the later of
  the result's `ended_at` and the records' `last_at`, plus 5 minutes. It runs ONE fixed
  script. The only interpolated values are the two timestamps (formatted by the host from
  datetimes it parsed) and the session id, which must match
  `[A-Za-z0-9][A-Za-z0-9_-]{7,79}` (stricter than `hive_records.SESSION_RE`, which allows
  a leading `-`) and is passed after `--` to `grep -F`. No command text is accepted.
- The script prints its sections in this order, each with a line cap and the whole
  under 20 KB (SSM returns at most 24,000 characters of standard output; both r1
  captures were cut there):
  1. `systemctl show swarmai-hive -p MemoryMax,MemoryCurrent,OOMPolicy,NRestarts` and
     `free -m`, `uptime`: the state now.
  2. systemd's own lines for the unit in the window (`journalctl -u swarmai-hive
     _PID=1`): `Failed with result`, `killed by the OOM killer`, `Started`, `Stopped`.
  3. kernel lines in the window matching the OOM killer.
  4. the daemon's service-level lines (`memory_sample`, `memory_warning`,
     `disconnect_all`, startup): the newest 40.
  5. the daemon's lines naming this session id, without uvicorn access lines (they name
     the session on every records poll, about every 30 s): the newest 120.
  A section over its cap keeps its newest lines and says how many it left out. Lines are
  whole; none is cut.
- `findings`: a deterministic list from those sections, no model: `oom_kill` (from 3:
  victim name, RSS, memcg, time), `service_stopped` (from 2: the result and the time, one
  per stop, so a later restart does not hide an earlier one), `session_error` (the
  session's own error lines, from 5), `none_seen`.
- Route `GET /api/swarm-out/runs/{dispatch_id}/diagnostics`; a row in `swarm_out_ops`
  with callers `(person, owner_agent)`, weight `read`; CLI `swarm_out diagnose
  <dispatch_id>` (and its entry in the CLI-ops test). One SSM call per run per minute: a
  repeat within 60 s returns the kept answer.
- The returned text is Hive-written and untrusted; the CLI prints it inside the existing
  `[end of Hive-written data]` marker.
- No new run state. It is a read the agent uses before it decides re-run, take over or
  discard; the s_swarm-out skill says to read it when a run ended in an error, was cut
  off, or its Hive does not answer.

**Security notes.** SSM Run Command runs as root on the Hive, so the safety of the read
rests on the script being fixed text with two validated timestamps and one validated id
(SECURITY-BASELINE A3). Journal lines can carry other sessions' text: section 5 keeps
only this session's lines, sections 2 to 4 carry service-level lines (sizes, results,
ids), not message content. The agent and the daemon share an OS user on the host, so
this read gives the agent nothing it could not already reach with the account's keys
(ops registry design §2.4); it removes a hand-back, it does not widen a boundary.

**Tests (written before the code).**
- The script for a given row equals a pinned template with only the window and the id
  substituted; an id with a leading `-`, a space or a quote, and a timestamp that is not
  a parsed datetime, raise before SSM is called.
- From the r1 captures kept as fixtures: `oom_kill` (`node`, about 5.3 GB, 06:28:20)
  and two `service_stopped` (06:23:43 and 06:28:20); from a quiet fixture `none_seen`.
- Section 5 keeps this session's lines and drops another session's lines and every
  uvicorn access line; the whole output of a fixture with 2,000 journal lines stays under
  20 KB and every section reports what it left out.
- A Hive whose account is not the SwarmAI account answers `not_reachable` without an
  SSM call; a second call within 60 s makes no SSM call.
- The ops-registry test sees the new route, and the CLI test sees `diagnose`.

## 3. Run states: what to merge, delete, or leave

Source: a read-only inventory of every value of `state` / `state_reason`
(`core/swarm_out.py:1387-1421`), `stage` / `why` (`core/swarm_out_settle.py:473-500`),
`settle` (`swarm_out_settle.py:421-516`) and `actions` (`swarm_out_settle.py:348-389`),
with every backend and desktop reader; the claims below were re-read at the cited lines.

**The rule.** A value earns its own place only if some reader behaves differently for it.
A value whose only effect is a label is text; text never caused a bug in the two E2E
rounds, every bug sat on a branch. So this part changes only where a branch or a field
goes away, and leaves stored record values as they are (old records must keep reading).

| # | Today | Change | Why | Readers to convert (R27) |
|---|---|---|---|---|
| S3 | `running` / `waiting` booleans next to `state`; `unknown` decided two ways | delete the booleans, readers read `state`. While `state` is `unknown`, a run is neither settled nor discarded by an agent; expiry after `RETENTION_DAYS` idle still applies | pure functions of `state` (`swarm_out.py:1754-1755`). `derive` settles an `unknown` run (`swarm_out_settle.py:522`, `running` is `None`), the agent's discard refuses it (`swarm_out_actor.py:265`); the comment at `swarm_out.py:1389` shows settling was never meant. Settling tells the Hive to release (`released: False`, 566 → 633/662). The Hive refuses a release while a turn is open (`routers/hive_brains.py:208-224`), but not while background work or a pipeline run is open, and the v2.4.1 release ends the session's pipeline runs (G4). Expiry stays: the Hive sweeps the workspace itself by then, and blocking it would keep a run open for good | settle 402/481/489/522/612; actor 265; `useSwarmOutActivity.ts:36/43/51`; desktop fallbacks `live.ts:74`, `tone.ts:50`, `runReview.ts:38-40`; the test fixtures that set `running` / `waiting` |
| S1 | stages `discarded` and `expired` | one stage `closed`; the old name goes to `why`. For a settled row `why` comes from `settled.outcome` (`abandoned` → `discarded`, `reverted`, `gone` → `hive_gone`, `expired`), which `_show_settled` blanks today (`swarm_out_settle.py:574-578`) | identical everywhere but the label: no actions (`_actions` 389), one funnel gate (`swarm_out_record.py:288`), one Needs You rule (`attention_authority.py:804-805`), one done group (`runReview.ts:54`), one tone (`RunOverlay.tsx:336`) | `STAGES` 332, `_OUTCOME_STAGE` 334, derive 478/480/492, `_show_settled`, `swarm_out_record` 288, `attention_authority` 804; desktop `SwarmOutStage`, `DONE_STAGES`, `STAGE_TONE`, labels, i18n; `test_swarm_out_e2e_fixes.py:130` builds its row with the literal `"discarded"` and must build it through `derive` so it goes red with the change |
| S5 | `settle.verdict` on the row | keep it inside `derive`, drop it from the row | no reader in backend or desktop; only the TypeScript type declares it | `derive`'s return, `swarmOut.ts` type |
| S6 | `state_reason = hive_updating` labelled "the Hive didn't answer" | keep the value, give it its own label ("the Hive is updating") | the person should wait, not act | `live.ts` label map, i18n |
| S7 | i18n `why.paused` | delete | never produced for a current result (a paused result is provisional, so the run shows `running`); a kept result from before 2026-10-28 falls back to the raw word | `en.json:1876` and the other locales |

**Dropped from r1, with the reason.**
- S2 (merge the settled outcomes `gone` and `expired`): they are stored in records, and S1
  now reads the reason from them; merging would lose it.
- S4 (delete `code = "rejected"`): nothing writes it any more, but stored records can
  hold it, and a record with landed and rejected repositories must still read `landed`
  (`swarm_out_settle.py:274-277`). Three lines of reading old data stay.

**Left as they are, on purpose.** The `state_reason` values nothing branches on (`idle`
/ `not_held`; `decision` / `question` / `waiting_input`; `streaming` / an activity name)
are what the probe saw; `not_held` is where §2 starts. The eight `why` values of a
stopped run are labels from the result's own `ended`. `pulled_out` / `closed` /
`unqueued` are local variables of `derive`.

**A silent Hive with S3.** Its runs stay open while it does not answer: the agent can
neither settle nor discard them, and `update-hive` refuses a Hive with open runs
(`scripts/swarm_out.py:351-355`). That is the intended stop: restarting a Hive that may
still be working is the person's call. The agent reads `diagnose`, and when the Hive is
wedged it tells the person in its own session what it found and that restarting or
updating the Hive is theirs (Settings → Hive). (`ask-person` is not the channel: it is
for a landed result the agent cannot judge.) The person's
discard and the person's update in Settings stay available (a person's command is
guarded by `closed_state` only, `routers/swarm_out.py:150-151`; whether it should also
follow `actions` is a separate decision, not changed here).

**Tests (red before, green after).**
- S3: a row with `state = unknown`, a final result and a pass verdict is not settled and
  is refused for the agent's discard; the same row idle past `RETENTION_DAYS` expires;
  every reader gives the same answer for a row without `running` / `waiting` as it gave
  with them.
- S1: discard, revert, Hive gone and expiry each give stage `closed` with the old name in
  `why`, for a live row and for a settled row; a closed run's open waits stay out of
  Needs You (the `attention_authority` test, now built through `derive`); the desktop
  groups and tones `closed` once.
- S6: the label for `hive_updating` differs from `hive_silent`.

## 4. Order and verification plan

1. **S3 first, now.** v2.4.1 brings G4 to the Hives, so a settled `unknown` run's release
   can end a pipeline run; S3 lands on the host before the Hives are updated.
2. **v2.4.1 on both Hives** after its release publishes (being done in another session);
   `systemctl show` reads `MemoryMax=infinity` / `OOMPolicy=continue`.
3. **§2 diagnostics**, tests first; then live: `swarm_out diagnose so_c2d9593ffa79`
   returns `oom_kill` (`node`, about 5.3 GB) and `service_stopped` at 06:28:20.
4. **S1, S5, S6, S7** in one change, with the R27 consumer grep for each field. Upgrading
   changes the follow fingerprint of every closed run (`swarm_out_follow.fingerprint`
   hashes `stage` and `why`), so each one is reported as changed once in its session's
   next digest; accepted, not shimmed.
5. **E2E round 3:** two runs on one Hive at once, one of them running a command over 6
   GB (a full type check of a large repo). Must hold: the heavy command fails alone, the
   other run finishes, the Hive keeps running (or comes back), and `diagnose` on the heavy
   run reports the kill.

Review after the code: correctness for S3 and §2, security for §2.
