---
title: DailyActivity, Timeline and ToDo — one job each
created: 2026-10-31
updated: 2026-10-31
project: SwarmAI
status: design r3 (premise review folded in; r3: a model never closes the person's work)
follows: 2026-10-31-extraction-at-task-end.md, 2026-10-31-daily-activity-useful.md
---
# DailyActivity, Timeline and ToDo — one job each

Agreed with XG 2026-10-31: **DailyActivity is for continuing work, Timeline is for
looking back, ToDo is the only place for work still to do.** One thing is recorded in
one place.

## 1. What is wrong today (measured 2026-10-31, live workspace)

- **DailyActivity is injected for 7 days into every session** (`memory_injection.
  _recent_days`, count 7; budget 12000 tokens). Its recall was measured useful 83%
  within 2 days, 42% at 3–7 days, 13% older (`recall_filter.py`, design 2026-10-26 §10).
- **It is also a task list.** The injected block is 25,650 characters: 59 session
  rows and 164 open follow-up lines (`select_daily_activity` `_row`). 51 of them are
  release steps (push / build / restart). One fact is repeated: the source repo's
  `main` is 1615 commits ahead of `origin/main`, last pushed 2026-10-27. A follow-up
  closes only when a later row lists it in `resolves`; otherwise it stays until it
  falls out of the 7-file window (2051 written ever, 475 resolved). Rows
  written under the new rules still add "git push" and "build and restart".
- **Open work has three writers and no owner:** DailyActivity `next_items` (164 open),
  S7 review `gap` units with `gap_kind: follow_up` written to a project's IMPROVEMENT
  (7 of 43 in SwarmAI), and ToDos (45 open: 37 in_discussion, 8 pending; 32
  `ai_detected`). None reads the others. ToDos are not in the system prompt (the
  session briefing that carried them was retired).
- **Timeline is injected into every session** (`select_timeline`, 1500 tokens: month
  summaries, then newest events). Day-to-day work does not use it; it serves
  retrospectives and "when and why did this change". Some events still carry status
  phrases ("landed, not pushed").
- **DailyActivity sentences and Timeline events both say what happened**, so one piece
  of work is written twice.

## 2. Decisions (r2, after the premise review)

**D1 — DailyActivity: continue work.** One row per session-day: what was done, where it
stopped (what it waits on), where the results are (commits, files). The row holds no
follow-up list.
- Injection: the rows of the **two most recent days that have rows** within the last 7.
  Older rows are reached by recall (its age rule exists). No Next lines.
- The md blocks of the injected window are re-rendered without Next lines by the
  migration (§3), so recall of days 3–7 does not bring back closed follow-ups.
- Removed once nothing reads them: `open_next_items_in`, `read_open_next_items`, the
  hook's open list, `earlier_row`'s follow-ups, `_legacy_records`' Next parsing,
  `da_resummarize`'s follow-up handling, the context-health "stale Next: build" check.

**D2 — ToDo: the only store of work still to do; one producer from sessions.**
- The producer is the DailyActivity summarizer (the post-session hook), not S7. It runs
  about two minutes after a session goes quiet, for every desktop session; S7 runs an
  hour later and its admission rules exclude exactly this content ("the state of the
  moment is not knowledge", "record nothing" without citable evidence). The worker Hive
  does not register this hook (`session_hooks.register_default_hooks`), so no ToDo is
  written where nobody sees it.
- The summarizer is shown **every** open ToDo (pending, overdue, in_discussion: id and
  whole title) and answers with items, as many as the session left open (a cap of
  three dropped real open work on 2026-10-31, so there is none): a new one (title + a description that
  stands alone: what, why, where it stopped, session and day), `same` as an open ToDo
  (nothing written), or `done` naming an open ToDo the session finished. `done` goes
  through the guard the lifecycle hook already uses (`db.todos.update_if`: only an
  active ToDo with no review state moves, to `review_state='completed'`; a person's
  Confirm sets `handled`), so a Confirm or Reject made meanwhile wins.
- A new ToDo is `source_type='ai_detected'`, `status='pending'`. `_find_duplicate`
  also counts `overdue`. Release steps are never ToDos (the prompt says so; D4).
- The other writers of open work are removed: S7's `follow_up` gap kind and the event
  unit's `follow_up` field (contracts_v2). The other gap kinds (debt, risk,
  deferred_finding, process_issue) stay in the project's IMPROVEMENT: they are
  knowledge about the project's weak points, not tasks. Other "Next" writers found by
  the review (`writer.py` proposed_next, `session_healing`, `brain_bootstrap`) are
  checked and either removed or shown to write no task.
- **Not injected into the system prompt.** The prompt-assembly path is filesystem-only
  and keeps dashboard data out (SwarmAI TECH, "System-prompt purity"; Radar ToDos were
  taken off it with the session briefing). ToDos are the person's panel; the agent
  reads them when the work needs it (a ToDo dragged into a chat, "what is open?").
  Continuity in the prompt comes from D1's "where it stopped".

**D3 — Timeline: look back.**
- Per-session injection: the month summaries (current and previous month) only.
  Events are read from `.context/TIMELINE.md` when looking back (retro, "when and why
  did this change"); a live context file is not in FTS (TECH, live/cold rule), so the
  file is the way. The current month has no summary until the month ends: until then
  nothing of it is injected, by design of the role.
- The S7 event rule: a decision (and why), a milestone, or an incident (and how it was
  resolved), as it stands at the end; never a status.
- SWARMAI.md's row for TIMELINE is updated (source edit + rebuild).

**D4 — Release state is observed, not stored.** No follow-up, ToDo, Timeline event or
prompt line records "not pushed / not built". The agent reads it when it needs it
(`git status`, `git rev-list origin/main..main`), as SOUL P1 asks for runtime state. A
line computed at session start was rejected: in a long session it goes stale like the
follow-ups it would replace.

**D5 — The same rule for every user (P7).** Nothing names SwarmAI's repo or this
machine.

## 3. Existing data (one-time migration, recorded)

- **Scope:** the follow-ups still open in the 7-day window (164), counted by next_id
  over the sidecars, not through `open_next_items_in`. The older unresolved ones
  (1412 of 1576; 2051 ever written, 475 resolved) fell out of the window long ago; they
  are recorded as "expired, not migrated" with their ids, not turned into ToDos.
- **Per item, one model call** with evidence it can cite: the later rows of all
  sessions (row id + text) and the commit subjects of the repos the item's session
  worked in. Answers: done, release step, same as an open ToDo (its id), still open.
  No answer, a failure or an uncited "done" → still open. Still open → an
  `ai_detected` pending ToDo with its source. next_id is the idempotency key: a second
  run changes nothing.
- **r3 — a model's verdict never closes the person's work.** r2 let a cited "done" and
  a "release" close an item outright. Checking that the cited row or commit *exists*
  does not check that it *shows the work finished*, and no deterministic check can; the
  live run proved it (2 of the last 9 closed wrongly: a planned next step judged a
  release step, a month-summary check "done" by citing the injection commit). That
  broke D2's own rule for the live producer (a model's done goes to review, a person
  closes it). Now the same rule holds at every door:
  - done → a ToDo in review as a suggestion (`POST /api/todos/{id}/suggest-done`,
    `todo_manager.to_review(suggested=True)`): never auto-confirmed; Confirm closes it,
    Reject puts it back in To Do;
  - release → stands only when the item's text names a release step (push, build,
    restart, deploy, ...: a parsed-text check, `RELEASE_WORDS`); otherwise open;
  - same → stays: it names an open ToDo, which keeps the work.
  `--review-closed` applies this to what the r2 runs already decided.
- **`follow_up` gaps:** every project's IMPROVEMENT is scanned; each is checked the same
  way; still open → ToDo, then withdrawn with status `moved` (the vault keeps it).
- **Event `follow_up` text** in TIMELINE: listed; still-open ones checked the same way.
- The window's md blocks are re-rendered without Next lines. Existing ToDos unchanged.
- Backups of DailyActivity, IMPROVEMENT, TIMELINE and the todos table first, verified;
  every decision written to a migration record (JSONL).

## 4. Order of work

1. ToDo producer in the summarizer (shown open ToDos; new / same / done), the guard
   for done, `_find_duplicate` with overdue.
2. Migration (§3).
3. DailyActivity: rows without follow-ups, injection of two days, then delete the
   follow-up readers.
4. CDF: remove the `follow_up` gap kind and the event `follow_up` field; the event rule
   (CDF code; another session works on CDF — coordinate before landing).
5. Timeline: injection of month summaries only; SWARMAI.md.

Step 1 lands before 3, so open work is never in neither place.

## 5. Verification plan

| Must hold | Test |
|---|---|
| A session's follow-up becomes one ToDo; `same` writes none; the same item over two parts and two sessions gives one ToDo | summarization / hook tests |
| `done` moves only an active ToDo with no review state, to completed, never handled; a Reject made meanwhile wins | hook test |
| A worker Hive writes no ToDo | session_hooks test |
| No release step becomes a ToDo (prompt) | summarization prompt test |
| The `follow_up` gap kind and event field are gone; other kinds unchanged | contracts / pipeline tests |
| DailyActivity injects the two most recent days with rows; no Next lines | memory injection test |
| Timeline injection is month summaries only | memory injection test |
| Nothing new reads the database on the prompt path | prompt purity test (existing) |
| Migration: each of the 164 + gaps + event follow-ups has one recorded decision; an uncited done stays open; a second run changes nothing | migration test on a fixture |

Each test goes red when its rule is reverted. Review: correctness on the producer, the
done guard and the migration; security on what may become a ToDo. After rebuild: the
next sessions' new ToDos (count, duplicates), the DailyActivity block size, and whether
any session still writes a release step anywhere.

## 6. Premise review (correctness, zero context, 2026-10-31)

4 HIGH, 9 MED, 1 LOW. Folded above: the open-ToDo prompt block broke the prompt-purity
rule (D2: not injected); S7 as producer would write almost nothing and runs an hour late
(D2: the summarizer); the event `follow_up` field was a fourth writer (D2); a worker Hive
would write ToDos nobody sees (D2: the hook is not registered there); "the list only
grows" was wrong — items also fall out of the 7-file window (§1, §3 scope); dedup and the
done race (D2); migration evidence and failures (§3); recall of old Next lines (D1);
Timeline recall and the current month (D3); the timing gap (D2); other Next writers (D2).
