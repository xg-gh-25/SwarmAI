---
title: Extraction at task end
created: 2026-10-31
updated: 2026-10-31
project: SwarmAI
status: implemented
follows: 2026-10-31-daily-activity-useful.md
---
# Extraction at task end: what a session leaves behind, recorded once and kept current

Status: design 2026-10-31. Follows docs/design/2026-10-31-daily-activity-useful.md (the
DailyActivity rows themselves) and amends docs/design/2026-10-29-cdf-session-review.md
(when the after-session review runs) and 2026-10-30-cdf-agent-decides.md D1/D4 (what a
review sees and may declare).

## 1. The problem, measured (2026-10-31, live workspace)

Two extractors read every session:

| Extractor | Writes | Fires | Measured |
|---|---|---|---|
| DailyActivity hook | the session's row of the day (working memory) | every idle (120 s after a turn) | 5-10 an hour over all sessions; incremental since today |
| CDF after-session review (S7) | MEMORY, EVOLUTION, DDD docs, TIMELINE events, IMPROVEMENT gaps | after 10 quiet minutes | 4 passes per session-day (median; mean 5.7, p90 16); one session gave 39 units over 7 passes |

What the stores hold as a result:

- **TIMELINE**: of 40 injectable events, 3 pairs are the same milestone recorded twice
  (Swarm Out run review x2, Brain change feed x2, Tenets v0.4 x2); interim states stay
  as if current ("Root cause found for broken DailyActivity summaries", with no later
  event for the fix); 9 carry a status phrase ("not pushed", "needs build").
- **IMPROVEMENT gaps**: a pass writes "fix not done yet" as a gap; the work is finished an
  hour later in the same session and nothing closes the gap.
- **DailyActivity follow-ups**: 394 written over 7 days, 103 resolved, 311 open; each
  session lists 6-12; 45 say push, 36 restart, 55 "confirm ...". Counting only each
  session's latest row would leave 195.

## 2. Root causes (code read, file:line in the investigation)

1. **The review runs mid-task.** "10 quiet minutes = the task ended" is an assumption the
   review design marked for tuning by measured yield (session-review.md:244); it was never
   tuned. The prompt's first line says "This conversation is over for now" on every pass.
2. **A pass cannot see what earlier passes of the same session wrote.** It reads only the
   conversation after its watermark (s7_review.py:637). Its relation candidates exclude
   TIMELINE (relations_v2.pool: every-turn files + project docs), so an earlier event of
   the session is never shown; `_earlier_units` filters by that pool.
3. **An event or a gap cannot be declared the same as, or a replacement of, an earlier
   one.** EventUnit has no `same`/`overrides`, GapUnit no `overrides`
   (contracts_v2.py:184-221); `_check_overrides` takes cognition units only
   (pipeline_v2.py:370); an automated withdrawal may not reach TIMELINE
   (governed_edit.withdraw via gaps.main_cognition_files). `event_key` differs between
   passes (each cites its own messages), so the second record of a milestone is written.
4. **The prompt sends "not done yet" into gaps** (s7_review.py:252-254) with no rule for
   the case where the same session finishes it later.
5. **DailyActivity follow-ups have no owner rule.** Every row of 7 days contributes its
   follow-ups until something resolves them by id; a session's newer row does not
   supersede its own older rows' list; nothing stops the same action being written by
   several sessions; the model is not asked to choose.

## 3. Decisions

**E1 — Durable extraction waits for the task to end; working memory does not.** The
review's not-before after an idle goes from 8 to 58 minutes (60 quiet minutes; measured
1.2 passes per session-day, p90 3). Later activity still pushes it back, but never past
`MAX_WAIT` (3 h) after the first request of the wait, so a session that never rests an
hour is still reviewed while it is used; a rerun after `again` starts a new wait. The
Save to Memory button keeps reviewing at once. (A deleted session is not reviewed, as
before; closing a tab does not shorten the wait.) The prompt's first line says what is
true: the conversation has been quiet for an hour and may continue; an earlier review of
it may exist. DailyActivity keeps its idle cadence: it is the working memory a parallel
session needs within minutes, and each idle now sends only the new messages.

**E2 — A pass sees what this session already recorded.** The relation step is shown,
for every unit, the ten live entries earlier passes of this session wrote that are
closest to it (BM25, like every other pool), from the shared pool and from TIMELINE
(`_earlier_units`), marked "(this session)". Showing all of them under every unit made
the prompt of a long session pass 1M tokens, the relations turn failed and its units
stood as new; that failure counts as an errored review in health C2. An event's BM25
candidates come from TIMELINE, every other unit's from the shared pool as before: TIMELINE
does not join the shared pool, so a lesson is never matched to an event and DDD
candidates are not crowded out.

**E3 — The context that recorded a milestone or a gap may replace it.** EventUnit gets
`same` (resolved against TIMELINE: the event is not written again) and `overrides`,
checked by `overrides.check_session_own`: S7 only; the unit cites no external content;
the id and first line name one live CDF-written TIMELINE event or IMPROVEMENT gap; and
that entry's candidate has this session's `source_ref`. No time is compared: the later
pass is later by construction. `apply` retires a gap as `closed` (history a recurrence
may repeat) and an event as `overridden`; TIMELINE is added to what an automated
`overridden` withdrawal reaches, for CDF-written entries only. GapUnit needs no
`overrides`: what finishes a gap is the event that records the finish. Cross-session
closing of gaps stays deferred, as gap-lifecycle decided.

**E4 — The prompt records the end state.** Turn one: something the conversation left
open at its end is a gap; an event is what changed how the work stands, worded as it
stands now, without a status of the moment (not pushed, needs a build). Turn two: when a
unit completes, corrects or restates an event or gap this session recorded earlier,
declare `same` or `overrides` against it.

**E5 — DailyActivity follow-ups have one owner and are chosen.**
- A follow-up is open only in its session's **latest** row in the window and when no
  row resolved it; one predicate (`open_next_items_in`) serves the hook, the injection
  and the briefing.
- The model is shown, for the earlier day of the same session, its open follow-ups too,
  and is asked for at most three: the work the next session most needs, one per
  workstream, not one already on the open list, release steps (push, build, restart) only
  when the user left one pending.
- The injection shows a follow-up text once.
- A session's own follow-ups are not in the list it may resolve: its new row carries
  forward those still open (the earlier day's are shown with the earlier row) and drops
  the rest. A follow-up the model drops after seeing it is its judgment that it is done
  or moot; nothing else retires one.
- A row the model did not write (no answer) never replaces the session's earlier list:
  its empty list would erase what is still open. A row without a session id is a row of
  its own.
- The earlier row's follow-ups are shown to the model only while still open, and only if
  they pass the injection scanner again: the text goes back into a prompt.

**E6 — DailyActivity rows are shorter.** The prompt asks for one sentence of about 30
words or 60 Chinese characters, with an example; deliverables keep the detail.

**E7 — A session's commits are found by the subject its own command wrote.** Measured:
of 128 rows whose deliverables cite a commit, 110 list none. The time attribution (a
commit inside one of the session's git calls, 3 s slack) misses a commit whose hooks run
past the recorded end of the call (one real case: 16 s). The subject of a commit is in
the session's own `git commit` command (a heredoc, `-F -`, or `-m` / `-am` / `--message`,
first line of the message): a commit whose subject equals it is the session's, at any
time within the day's window. When the stored command was cut inside the subject, the
cut start is matched as a prefix instead. Commits made by a pipeline run or a sub-agent
are not in the session's transcript and stay unattributed (known limit).

**E8 — The work is reviewed and citable, not only the person's words.** Measured
2026-10-31: 12 reviews in a day and a half covered a stretch with no user message (5 to
314 messages of agent work each, all in sessions a person used) and returned "no user
message", moving the watermark past work that was then never reviewed. And of the 74
review units refused in 7 days, every class had one cause: the review is asked to
record what the agent learned from its own work, but could cite only the person's
messages and measured fields. With nothing to cite it wrote sentences, commit notes or
message ids (refused as format), then the agent's and tools' anchors once the record
showed them (refused as not in the index), then empty evidence once those were hidden
(refused as too short), and the prompt told it to record nothing when the index was
empty. Now only a session no person ever wrote in is passed over. Every record item
carries its anchors and is citable; a window with no user message shows the person's
last message before it as context, not citable, so an old message cannot become the
user's words for a claim it does not state. Trust is unchanged and decided downstream
from what is cited (`pipeline_v2.Facts`): the agent's words and tool output are
registered as external, so such a unit stays in its project's DDD and never reaches a
file read every turn. The window is registered once, and that one registration serves
the record, the evidence check and the read set.

## 4. Existing data

- TIMELINE: the 3 duplicate pairs and the superseded interim event are retired with
  `withdraw(..., status="overridden")` by a one-time script, each with its reason; the
  vault keeps them.
- DailyActivity: follow-ups need no rewrite (E5 is a reading rule). Commit lists are
  recomputed without a model call for every session-day with a transcript
  (`da_resummarize --commits`), re-rendering the block and row from the sidecar.

Premise review (2026-10-31, correctness, zero context): 3 HIGH (close/delete do not
review now; overrides could not address TIMELINE; no code knew which session wrote an
entry), 6 MED, 3 LOW. All folded into E1-E3 and E7 above.

Code review, round 1 (correctness, zero context), fixed by cause in one pass:
- ownership: an S1 row shares the session id as `source_ref`; ownership now also needs
  the row to be S7;
- the closed status was per decision; it is now per target (`override_closes`), so a
  cognition entry retired with a gap is never recorded closed;
- an event's `same` could match a gap; events are compared with TIMELINE only;
- a wait whose first request is older than MAX_WAIT (a retry) pulled the review into
  the past; a limit already past caps nothing;
- the commit-command parser missed `-am`, `-F -` and the `$(cat <<EOF` form, and matched
  a cut subject's prefix against uncut ones; rewritten, exact unless cut;
- follow-ups: an unanswered row erased the open list; the earlier row carried resolved
  items; fixed as in E5.
Accepted, noted only: `cited_external` vs `read_external` on the own-session check;
legacy md-only follow-ups outside the 7-day window.

## 5. Verification plan

| Must hold | Test |
|---|---|
| The idle review waits 60 quiet minutes, capped at 3 h; a rerun starts a new wait | test_cdf_extraction_at_task_end |
| A pass is shown its session's earlier TIMELINE event, marked; an event is compared with events | test_cdf_extraction_at_task_end |
| An event declared `same` as an existing one is not written | test_cdf_extraction_at_task_end |
| An event overriding its own session's earlier event or gap retires it (gap closed); another session's or a person's is refused | test_cdf_extraction_at_task_end |
| An automated overridden withdrawal reaches TIMELINE, CDF-written only | test_cdf_extraction_at_task_end |
| Open follow-ups: latest row per session, resolved hidden, text once | test_cdf_v2_memory_injection, test_daily_activity_writer |
| The prompt asks for <=3 follow-ups and a short sentence | test_summarization |
| A commit is the session's by the subject its command wrote (real command forms; exact unless cut) | test_daily_activity_v1_attribution, test_daily_activity_day_window |
| An unanswered row keeps the open list; only open follow-ups are carried | test_cdf_v2_memory_injection, test_daily_activity_day_window |
| Ownership needs an S7 row; closed status per target; event `same` never a gap; a stale cap does not pull into the past | test_cdf_extraction_at_task_end |

Each new test goes red when its fix is reverted (mutation check). Review: correctness on
the CDF change (contracts, pipeline, overrides, withdraw reach); security on the widened
automated withdrawal. The live check after the user's rebuild: the next day's review
passes per session, duplicate events, open follow-ups.
