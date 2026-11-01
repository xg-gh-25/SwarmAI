---
title: "CDF health: is what the cognition system keeps worth keeping"
created: 2026-10-31
updated: 2026-10-31
project: SwarmAI
status: implemented (r3)
---

# CDF health: is what the cognition system keeps worth keeping

Status: r2, 2026-10-31 (r1 + one zero-context premise review: checks rebuilt on the existing
CDF definitions, cost read as the usage view reads it, marker-only locating, text-only judge
classes). Mockup: `Knowledge/Mockups/2026-10-31-cdf-health.html` (r2, approved).

## 1. What the user gets

A tab **Health** in the Needs You overlay, placed before **How it works**. It answers three
questions for any user, about the cognition system as a whole (no instance-specific or
short-term rows):

1. **Is what was kept useful?** The share of sampled new entries a separate judge scored
   useful, against a target, per destination.
2. **Is CDF itself healthy?** Deterministic checks of the pipeline: every session reviewed,
   reviews not failing, every-turn files fed only from the user's words, a measurement or a
   repeat, gaps being closed, store integrity. No data is shown as *no data*, never as a pass.
3. **What does it cost?** CDF's model spend, its share of the whole system's spend, and the
   cost per useful entry.

Below them, one section each with the details; last, the judge's reading (what it saw, why,
what to change). The judge only scores and reads. It never deletes, edits or vetoes
anything: removing an entry stays with the person or with the agent's own cleanup.

## 2. Pieces

| Piece | Where | Runs |
|---|---|---|
| `core/ingestion/health.py` | checks, sampling, value judge, reading, cost, snapshot | the job, and the API for a re-check |
| `cdf_health.db` (app data dir) | judge verdicts, user verdicts, run snapshots | written by `health.py` only |
| system job `cdf-health` | `jobs/system_jobs.py`, type `cdf_health`, weekdays 09:00 UTC | the daemon's job scheduler thread |
| `GET /api/ingestion/cognition-health?window=7d\|30d` | routers/cognition_health.py | navigation executor, read-only |
| `POST /api/ingestion/cognition-health/recheck` | takes the lease, then starts one pass in a background thread; 409 if one is running; loopback + `X-Swarm-User-Action: recheck` | daemon |
| `PUT /api/ingestion/cognition-health/verdicts/{unit_id}` | the person's verdict on a sampled entry; loopback + `X-Swarm-User-Action: verdict`, like the redaction route | daemon |
| `CdfHealth.tsx` | the tab | desktop |

**Why a separate database.** The judge observes the CDF store; it must not change its schema
(a v12 migration would make a rollback to an older binary fail on `unsupported ingestion
schema`). It opens `ingestion.db` and `data.db` read-only (`mode=ro`) and writes only its own
file. Losing `cdf_health.db` loses history and the person's verdicts, nothing else; it is
still never deleted automatically.

**One pass at a time.** A lease in `cdf_health.db` (taken by the job, or by the re-check
before its thread starts, so the tab sees the pass at once), renewed before every model call
and released at the end.

**Why it does not touch chat.** The model calls are direct Bedrock Converse calls
(`jobs.bedrock.structured`), not CLI sessions: no chat slot, no subprocess, no memory
admission. The job runs in the daemon's job scheduler thread (jobs run one after another, so a
slow pass delays the next job, never a chat); a re-check from the UI runs in a thread of its
own. Neither runs on the event loop; the GET only reads. The only write outside
`cdf_health.db` is the usage-ledger row every `structured()` call records.

## 3. Destinations

A sampled entry's destination is where the entry is now (entries move after they are written:
a migration, a person's move, a project rename), not what its source asked for:

| Group | Destination | From | Target |
|---|---|---|---|
| every turn | MEMORY | cognition written to `.context/MEMORY.md` | 95% |
| every turn | EVOLUTION | `.context/EVOLUTION.md` | 95% |
| every turn | USER_FILES | `.context/{STEERING,USER,TOOLS}.md` | 95% |
| every turn | TIMELINE | `unit_kind = event` | 95% |
| on demand | DDD | cognition under `Projects/<P>/` | 90% |
| on demand | GENERAL | cognition anywhere else (General Brain, Library documents) | 90% |
| gap · reference | GAP | `unit_kind = gap` | 90% |
| | REFERENCE | `unit_kind = reference` | 90% |

The every-turn files carry a higher target because a bad entry there misleads every turn.
The headline value is the sample-weighted share over all destinations, shown against 90%.

## 4. Value judge

**Sample.** Each run takes the units written in the last 7 days that have no verdict yet and
are still in force: some main cognition file, user file or the timeline holds the unit's marker
`cand:<unit_id>` (every rendering writes it and a move keeps it; titles are rewritten by the
renderers, so a title is no test), or, for a reference document, its own file names the unit.
Withdrawn, closed or overridden units are not scored. For a project's Brain (DDD, GAP), up
to `SAMPLE_PER_BRAIN = 10` per project and destination, so every Brain gets a rate of its own
(design 2026-11-01-brain-eval-and-runs §2.2; the cost grows with the number of Brains); for
every other destination up to `SAMPLE_PER_DEST = 20`; at random. A unit is scored once; one left unscored by a failed call stays a
candidate for the next run while it is in the 7-day window.

**Text shown.** The unit's content fields (its payload without the fields that only relate or
cite), secret-redacted (`MemoryGuard`), whole, as one JSON string per entry (the `user_check`
data discipline: `<`/`>` escaped, nothing inside can close its tag). An entry is never cut;
batches are packed by size (at most 20 entries or 60 000 characters, an entry larger than that
alone).

**Call.** One `structured()` call per destination batch of at most 20, Sonnet (the
`BedrockJudge` default), `feature="cdf.health"`, read timeout 60 s, 2 attempts. The prompt
states the destination's purpose (§3 table, one sentence each) and the verdicts:

- `useful` — would change or inform a later judgment for its destination's reader.
- `weak` — holds, but is vague, half a lesson, or much longer than what it says.
- `withdraw`, with one class: `wrong_kind` (an open problem or an event written as a lesson,
  a lesson written as an event), `fixed_in_place` (a defect found and fixed in the same work,
  with no rule beyond that code), `lookup` (only restates what one look at the code or data
  answers), `status` (a status snapshot, progress or construction log), `copy` (a copy or
  summary of a document the entry itself names as saved), `unclear` (cannot be understood
  alone).

r3 (calibration, XG 2026-10-31): the judge never checks the code or asks whether the writer
researched enough: that is the writing side's job, and CDF does not re-litigate it. Each
destination states its own bar: an open gap is useful when it names a concrete problem and
what would close it (its describing how things stand is what a gap is); an event when it says
what happened and its outcome (no cause or lesson needed); a General Brain document about one
subject that is no project is judged as that subject's brain, not as a cross-project lesson.
A calibration against the agent's own verdicts on 13 live samples found the judge too strict on
exactly those three (gaps and events judged as lessons, subject documents judged as general
lessons); with the per-destination bar the disagreements flipped.

The judge sees only the entry and decides only from what it says: it cannot tell an entry
that went stale or a gap already done elsewhere, and the tab says so. That is why the value
indicator also shows the outcome CDF already measures (`status.north_star`: of the entries
written long enough ago, the share read or cited after their write). When unsure, `useful`. Each verdict carries a reason in English and Chinese (≤ 200
characters each). A batch whose call fails is left unscored and tried at the next run; the
run records `judge_failed` per destination. A failed judge is never a zero.

**The person's verdict.** On a sampled entry the person can set useful / weak / withdraw.
It is stored beside the judge's verdict; the value rate uses the person's verdict where one
exists. **Agreement** = the share of person-verdicted entries where the judge agreed; shown
under the reading as how far to trust the judge (no data until there are 10).

## 5. Checks

Each check returns `pass | fail | no_data`, the measured value and the requirement, and is
built on the definition the rest of CDF already uses, never a second copy of it.

| # | Check | Measured (7 days unless said) | Requirement |
|---|---|---|---|
| C1 | every session reviewed | sessions with a user message since the first review job, older than `s2_jobs.MAX_WAIT + 1h`, with no review job | 0 |
| C2 | reviews succeed | failed review jobs; S7 attempts `failed` of those finished (a `running` one is neither) | 0 / ≤ 10% |
| C3 | review output valid | the validator's refusals over units, as `queries.review_health` counts them | ≤ 5% |
| C4 | nothing stuck | `status.stalls` (what Need You raises) and units waiting over 24 h, parked deferrals (`PARKED_DEFER_REASONS`) excepted | 0 |
| C5 | every-turn files keep the rule | written units with a basis row: STEERING/USER/TOOLS not `user`; MEMORY/EVOLUTION neither `user` nor `measured` nor admitted as a repeat (`version_vector.admitted_by`) — the stores and rule of `pipeline_v2._admitted` | 0 |
| C6 | no event written twice | repeated non-empty `event_key` in TIMELINE | 0 |
| C7 | relations intact | active relation endpoints in a main cognition file that point at no entry | 0 |
| C8 | no unresolved alarms | unresolved events of the kinds CDF emits for a failed write or recovery, in the window (some are never marked resolved by the product) | 0 |
| C9 | store intact | `PRAGMA quick_check` | ok |
| C10 | knowledge is used | `status.north_star`: mature entries used | > 0 |

Not checks: gaps opened vs closed (a gap may rightly stay open; it is shown under the GAP
destination's samples instead). C2 misses a review whose process was killed before it recorded
an attempt (S7 records the attempt at the end); C3 counts the validator's refusals only, not the
ones made at queue time.

## 6. Cost

This machine's spend rows of the usage ledger (`kind='spend'`, `machine='host'`), read as the
usage view reads them (`core.usage_report`: attribution rows never summed, one CLI result's
rows one call, local days). A Hive's rows belong to that Hive's own cognition. Per CDF
feature (`cdf.s7` the after-session review, `cdf.user_check`, `cdf.health` this judge, the rest
as it is named): spend, calls, spend per priced call. Per day: system and CDF. **Cost per useful
entry** = CDF spend / (units written in the window × value rate); absent when the rate is.
Rows with tokens but no price are counted as calls (`unpriced_calls`), never as zero spend.
Recording only: no cap, no budget.

## 7. The reading

After the checks and scoring, one `structured()` call writes the reading, bilingual
(`en`, `zh`): `seen` (≤ 3 short paragraphs), `why` (≤ 2), `suggest` (≤ 3, each a change to how
entries are written or routed — never "delete entry X"). Input: the three headline numbers,
the per-destination rates, the check results, the cost lines, and the outcome (entries used), the window's
`withdraw` and `weak` samples (title, destination, class, reason; the newest 60). The whole
input is one escaped JSON string inside its tag. It is told to use only the numbers given. The input's hash (without the spend, which every pass changes by its own calls) is stored with
it: a run whose input hash equals the latest reading's does not call the model. Each reason and
reading paragraph is a label, shortened when the model writes far more than asked. A failed call keeps the previous reading and records the
failure.

## 8. Snapshot and API shape

Each run stores one snapshot: `{at, checks: [...], reading, reading_input_hash,
judge_failed, sampled}`. `GET /api/ingestion/health?window=` returns:

```
{ window, generated_at, running,
  used: { used_rate, mature_written, mature_used, window_days },
  value: { rate, target: 0.9, scored, trend: [{week, rate, scored}],
           destinations: [{key, group, target, scored, useful, weak, withdraw, rate,
                           written, trend: [...], samples: [{unit_id, title, project, verdict,
                           klass, reason_en, reason_zh, user_verdict, written_at}]}] },
  checks: [{id, status, value, requirement}],   # from the latest snapshot
  checks_trend: [{at, passed, measured, total}],
  cost: { cdf_usd, system_usd, share, per_useful_usd, unpriced_calls,
          features: [{feature, usd, calls, avg_usd}], days: [{day, cdf_usd, system_usd}] },
  reading: { en, zh, at, input_hash } | null,
  agreement: { judged_by_person, agreed, rate } }
```

A destination with fewer than 10 scored entries in the window has `rate: null`. Samples:
at most 10 per destination, `withdraw` then `weak` then `useful`, newest first. Every text
field from an entry or the model is secret-redacted before it is stored.

## 9. Verification plan

**Reader before writer.** The tab (`CdfHealth.tsx`) and the GET model are written against §8
before the job writes snapshots; a fixture snapshot drives the frontend test.

**Final test set** (the invariants that must hold at the end):

1. Each check returns `no_data` on an empty store and `fail` on a seeded violation
   (C1–C10), and `pass` on a clean seed.
2. A withdrawn unit is not sampled; a unit is scored once; the destination comes from the
   write journal, not the source's request.
3. A failed judge call leaves the batch unscored and sets `judge_failed`, never writes a
   verdict.
4. The person's verdict overrides the judge's in the rate; agreement counts only
   person-verdicted entries.
5. The reading is not regenerated when its input hash is unchanged; a failed reading call
   keeps the previous one.
6. Cost: unpriced rows are counted, not summed as zero; per-useful is absent without a rate.
7. The judge prompt carries entry text only as escaped JSON strings (an entry containing
   `</entries>` cannot close the tag).
8. `health.py` opens `ingestion.db` and `data.db` read-only (a write through those
   connections raises).
9. The GET route validates against the response model; the tab renders the fixture with
   tabs in the order Needs you · Cognition flow · Health · How it works.

Each new test is mutation-checked once (revert the line it guards → red).

**Review.** Correctness expert on `health.py` + the routes (state, data shapes, the
read-only promise); security expert on the judge prompt and the verdict route (untrusted
entry text reaching a model, a write route). The frontend gets no expert; it is checked by
its test and by rendering against the mockup.

## 10. Not in this change

- An attention item when a check fails or a destination falls below target: later, after
  the judge has a few weeks of agreement data.
- The weekly `memory-health` job's model phase judges stale MEMORY/EVOLUTION entries (report
  only): it overlaps this judge. Folding it in or retiring it is a separate decision.
- Feeding person-verdicted entries into OS Eval as a judge-calibration set.
