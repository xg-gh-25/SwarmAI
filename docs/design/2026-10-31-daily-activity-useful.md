---
title: "DailyActivity and Timeline: content worth reading in the prompt"
created: 2026-10-31
updated: 2026-10-31
project: SwarmAI
status: implemented
---

# DailyActivity and Timeline: content worth reading in the prompt

Status: implemented 2026-10-31. Supersedes the "day 3–7 summaries" part of the CDF v2
memory-injection design and the mid-session checkpoint block in DailyActivity.

## 1. What is wrong (measured 2026-10-31)

The "Daily Activity (last 7 days)" block injected into every desktop session, and the
Timeline section of the base prompt, are meant to tell the next session what was done
recently and what is still open. Measured on the live workspace they mostly do not:

| # | Defect | Evidence | Cause |
|---|---|---|---|
| 1 | The model's summary is thrown away | 9 of 14 rows on 2026-10-30, 4 of 7 on 10-31 have no deliverables; their sentence is the user's own words joined by "；" | `_ENRICHMENT_MAX_TOKENS=2048`: a full 12-field answer for a long session is ~3,100 chars, mostly CJK, so it is cut, `StructuredOutputError` fires and the rule-based fallback (user messages) stands. A probe without the cap returned a good 3,096-char answer in 26 s for a 590-message session. |
| 2 | Half the requested fields have no reader | `rejected_approaches`, `corrections`, `process_reflection`, `signal_driven_actions`, `lessons`, `lesson_types`, `continue_from` are written to the sidecar and read by nothing (grep of core/hooks/jobs/routers/scripts) | Left over from the retired distillation. They cost output tokens and make the cap hit sooner. |
| 3 | One session is summarized again on every later day | 5 of 7 rows on 10-31 are sessions already on 10-30, each summarizing the whole transcript again | The hook writes to `date.today()` but summarizes the complete transcript. |
| 4 | Rows say what the user asked, not what was done | titles like "Step 2/3: Installing frontend dependencies", "[File Review: …" | The title is the longest user message; the fallback sentence is user topics; the rule-based Next is "Resolve: <user question>". |
| 5 | Day 3–7 are never shown | the 10-31 prompt shows 19 rows and "6 not shown"; every day 3–7 unit was among the omitted | Rows of today/yesterday are admitted first; a day summary is the concatenation of all its sentences plus every deliverable, so it is the largest unit and the first to fall out. Its open follow-ups are ids without text. |
| 6 | The Timeline budget counts hidden text | 1,484 tokens counted vs 1,349 visible; 8 events shown, 23 omitted | `select_timeline` estimates the unit with its `<!-- … -->` metadata, which the loader strips afterwards. |
| 7 | Product strings are Chinese | "（另有 N 个 session 未注入…）", "## 日汇总", "主线/交付/未完成 Next" | Hard-coded; every user gets them. |
| 8 | Continue hints are always empty | `_parse_continue_hints` expects `**Next:** text` on one line; the writer emits `**Next:**` then bullets | Format drift; it also ignores `resolves`. |
| 9 | Session counts are wrong | `engine_metrics._collect_session_stats` counts every `## ` line (checkpoint blocks, day summaries) | Counts headings, not sessions. |
| 10 | Archived days are deleted at once | 134 archived day files deleted from `Knowledge/Archives/` (all of 2026-03-05 … 2026-08-01) | `prune_archives` deletes archives older than 90 days, while `context_health_hook` archives a day at 90 days and documents a 365-day retention; the two numbers contradict, so a file is archived and deleted in the same week. |
| 11 | Noise blocks in the day file | 14 "📸 Mid-session checkpoint" blocks on 10-30 (file names only) | Written every 10 tool calls "in case the post-session hook does not fire"; the idle hook and the startup scan already summarize every session from its persisted transcript. |
| 12 | Dead code | `DAILY_ACTIVITY` cultivation event (no channel subscribes), `set_daily_frontmatter` (no caller), stale docstrings naming `DistillationTriggerHook` and a truncation that does not exist | — |
| 13 | Self-heal enrichment fires a model call it cannot wait for | `session_unit._derive_heal_enrichment` calls `summarize()` on 60 messages under a 3 s timeout, using only rule-based fields | The model call is abandoned in a thread each time. |

## 2. What the reader needs

The reader is the next session's agent. From the block it needs, per recent session:
*what the work was* (a title), *what was done and where it ended* (one or two sentences),
and *what is still open* (follow-ups with an id it can resolve). Older days need the
same in less space.

## 3. Decisions

**D1 — The answer is never cut.** `_ENRICHMENT_MAX_TOKENS` 2048 → 16000; read timeout
90 → 150 s (the hook budget is 180 s). A reply that still stops at the limit keeps the
existing behaviour (reported as truncated, fallback stands).

**D2 — Ask only for what has a reader.** Schema: `title`, `summary_sentence`,
`deliverables`, `key_outputs`, `validation_status`, `next_items`, `resolves`,
`coe_signal`, `coe_topic`. The other seven fields are removed from the schema, the
prompt, `StructuredSummary`, the sidecar and the sanitizer list.

**D3 — The prompt asks for the outcome.** `title`: a few words naming the work.
`summary_sentence`: what was done and where it ended, one or two sentences, no fixed
length, in the session's language. `deliverables`: results that exist at the end
(commit, file, decision with its reason, an answer). `next_items`: follow-ups agreed or
clearly left open and not done in this transcript. Never the user's words restated.

**D4 — One row per session per local day, about that day.** The hook summarizes, for
each local day of the last 7 on which the session has messages (`created_at` is naive
local time), the messages of that day. Looking at every such day, not only today,
keeps the tail of a day whose last idle fired after midnight. When the session has a
row on an earlier day, its title and sentence are given to the model as context
("already recorded on <day>"). The git window and the row time are the day's first and
last message.

**D5 — Each idle summarizes only what is new.** The sidecar records `message_count`
(messages covered) and `enriched`. A day whose enriched row covers all its messages is
skipped (no model call). A day whose enriched row covers fewer is summarized from the
new messages only, with the row as "the record so far" (the same mechanism as a long
day's parts, below), so a long working day does not cost a full re-summary after every
turn. A row without a model answer is rebuilt from all the day's messages.
A long day is sent in parts of about 100K tokens, split between messages (no message is
cut); each part carries the record so far. The hook declares its own hang guard
(900 s), since a first summary of a very long day takes several calls.

**D6 — The fallback is factual.** Every session with at least one exchange is sent to
the model (threshold 8 → 2). Without a model answer the row keeps the derived title, an
empty sentence or the session's commit subjects, no deliverables and no Next.
`enriched=false` makes the next idle try again. The rule-based "Resolve: <question>"
Next and the topics-as-sentence fallback are removed.

**D7 — Injection: one compact row per session for all seven days.**

```
- 2026-10-31 00:16 Title: what was done and where it ended
  - Next [d1594305000b]: follow-up text
```

The date is on every row (not a day heading) so whole-row admission can never leave a
row under the wrong day's heading.

The budget is 12000 tokens (measured on the repaired history: ~100-250 tokens a row,
6-15 sessions a day, so seven days fit). Days newest first, rows newest first, whole
rows; a Next resolved by any later row is
not shown. Day summaries are no longer injected and no longer written (`## 日汇总`
blocks were a concatenation of the same rows). When rows are left out:
`(N more sessions not shown; they are in Knowledge/DailyActivity/)`.

**D8 — Timeline keeps its window; its budget counts what is shown.** Month summaries
first, then newest events, whole units, counted on the text the prompt shows (metadata
comments removed), so the same budget holds more events. The recent overlap with
DailyActivity stays on purpose: Timeline events are the curated key events, and an
owner channel DM gets the Timeline but no DailyActivity block. Omitted suffix:
`(N more events not shown; read .context/TIMELINE.md)`.

**D9 — Consumers read the sidecar.** Continue hints come from `read_open_next_items`
(resolves-aware, newest first). Session counts come from sidecar rows. The Delivered
section lists deliverables only.

**D10 — Retention keeps one number.** `prune_archives` is deleted; the documented
policy (archive at 90 days, delete archives at 365) stays in `context_health_hook`.

**D11 — Removed:** the md checkpoint block (the checkpoint JSON stays: the resume
context reads it), the crash-recovery md block, the `DAILY_ACTIVITY` event,
`set_daily_frontmatter`, `minimal_summary`, `append_daily_summary` and the day-summary
readers. The self-heal enrichment uses the rule-based fields only (`enrich=False`).

## 4. History repair (`scripts/da_resummarize.py`)

- Dry run by default; `--apply` writes. Before writing it copies every file it will
  touch to `~/.swarm-ai/backups/daily-activity-<ts>/` and checks the copy (count and
  sha256) before the first write.
- Scope: every local day with transcripts (`data.db` keeps 90 days). For each
  (session, day) with at least 2 messages it rebuilds the row through the same function
  the hook uses, days in order and, within a day, sessions in the order they started
  (in batches), so `resolves` and the open list match what was known then. Rows whose
  transcript is gone, rows whose rebuild fails and rows whose rebuild is noise are kept
  as they are.
- The day file is rebuilt from the new rows plus the kept old blocks; checkpoint and
  day-summary blocks are dropped; the frontmatter keys are kept (`distilled` drives
  `coe_threads`).
- The 134 archived day files deleted by `prune_archives` are restored to
  `Knowledge/Archives/` from the workspace git history (their last version).

## 5. Verification plan

| What must hold | Test |
|---|---|
| A 6,000-token answer reaches the summary | test_enrichment_truncation (budget, complete answer) |
| Schema = the nine read fields; prompt asks for title and outcome | test_summarization |
| A session spanning midnight gets a row per day, each from that day's messages, the later one told of the earlier | test_daily_activity_day_window (new) |
| No model call when the row is enriched and unchanged; a fallback row is retried | same |
| Fallback has no user text as sentence, no Next | test_summarization |
| Injection: rows for 7 days, compact, resolved Next hidden, English suffix, no day-summary units | test_cdf_v2_memory_daily |
| Timeline: budget counted on visible text, English suffix | test_cdf_v2_memory_injection |
| Continue hints read the sidecar | test_proactive_intelligence |
| Session stats count sidecar rows | test_engine_metrics |
| No archive is deleted by workspace init | test_swarm_workspace_manager |
| Repair: backup verified before write; transcript-less rows kept; idempotent | test_da_resummarize (new) |

Each new test is mutation-checked (revert the fix → red). Review: correctness expert on
the hook/injection change; security expert on the repair script (it rewrites a user
store). Generation-side changes go live after the user rebuilds the daemon.
