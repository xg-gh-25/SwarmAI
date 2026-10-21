# Requirements

**Run** `run_1ec5ea9d` · **profile** ? · **project** SwarmAI

> 你为什么stop 你继续啊

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

**Verdict** GO · **Scope** standard · **Scores** strategic 8, priority 8, feasibility 8

## Requirements

| ID | Requirement | Source | Status |
|---|---|---|---|
| FR-1 | One up-to-date block per session per kind per day: checkpoint and summary writes upsert by session id instead of appending | user-stated | confirmed |
| FR-2 | Checkpoint git activity excludes the workspace's own auto-snapshot commits; file names come only from real paths | ddd-derived | confirmed |
| FR-3 | Prompt-reading hooks match only user-typed text, never system-injected context blocks | ddd-derived | confirmed |

**FR-1 acceptance**
- Given a session that already has a checkpoint block today · when the next checkpoint or the post-session summary fires · then the session's block is replaced and the block count for that session and kind stays 1

**FR-2 acceptance**
- Given a Bash summary 'Running: cmd 2>/dev/null > /tmp/x/out.txt' · when files are extracted · then only /tmp/x/out.txt is kept and rendered by a meaningful name

**FR-3 acceptance**
- Given a prompt that is a RESUMED block plus a [RECALLED] block plus the user line '继续' · when high-signal capture and the correction detector run · then they see only '继续' and capture nothing

### Acceptance criteria

- AC1: Within one day file, a session has at most one block per kind (mid-session checkpoint, session summary); a later write for the same session and kind replaces the earlier block instead of appending a new one, in both the .md and the .jsonl sidecar.
- AC2: A crash-recovery write for a session that already has a block in today's file updates that block instead of adding a 'Recovered from crash checkpoint' block.
- AC3: The mid-session checkpoint no longer lists SwarmWS auto-snapshot commits (the workspace's own 'chore: chore (N)...' commits) as git activity.
- AC4: File names written to DailyActivity are real paths: tokens such as 2>/dev/null, /tmp/, sed expressions and shortened 'Running:' summaries never produce entries like null, n, .s, *.py or empty names.
- AC5: The high-signal capture and the user-correction detector see only the text the user typed: system-injected blocks ([RESUMED CONVERSATION HISTORY ... END RESUMED HISTORY], [RECALLED] knowledge, '## Current UI State', task notifications) are removed before matching.
- AC6: Existing DailyActivity files are never rewritten except for upserting the current session's own block; no history is deleted.

## Understanding (Gate 0)

- **Present state:** DailyActivity accumulates many blocks per session because every writer appends: the mid-session checkpoint appends a new block every 10 tool calls whenever the file list or the workspace git log grew, the post-session hook re-summarizes the whole session and appends on each run, and crash recovery appends a third copy; separately, the prompt-reading hooks scan system-injected context as if the user typed it.
- **Evidence:** Measured 2026-10-20.md: 312 blocks, 249 checkpoint blocks, 5 crash-recovered blocks, 8 duplicate bodies, 84 junk file tokens, 10 RESUMED-preamble captures; all 293 checkpoint blocks carry SwarmWS auto-commit lines (14 such commits 14:00-15:00). Code-trace: runtime_hooks.py create_session_checkpoint (~585-690) appends '## HH:MM | sid | Mid-session checkpoint' every _DEFAULT_CHECKPOINT_INTERVAL=10 calls when files or git_commits grew, git from _get_recent_git_commits(ws) on SwarmWS; daily_activity_writer._atomic_read_modify_write (~258-284) appends the formatted entry to the existing body; dail…
- **Alternative framing considered:** Filter the noise when DailyActivity is read (distillation, injection) instead of when written. It loses: the file is read by several consumers (prompt injection tail, distillation md + jsonl, knowledge_fts indexing, recall), each would need the same filter, and the files keep growing to 400KB/day.
- **Gate-0 skeptic:** SUPPORTED

## Root cause

W1. A session appears as many blocks per day because each writer appends a new block on every trigger  
   _evidence:_ 2026-10-20.md: 249 checkpoint blocks; runtime_hooks.py ~660-690 append
W2. The writers (checkpoint hook, post-session hook, crash recovery) each append to the day file independently and none knows whether the session already has a block  
   _evidence:_ daily_activity_writer.py ~279-284 body + new entry; runtime_hooks.py checkpoint append; daily_activity_hook.py ~82
W3. DailyActivity was designed as an append-only log with no per-session identity rule, so 'one current record per session' was never an invariant of the store  
   _evidence:_ daily_activity_writer.py frontmatter only counts sessions_count; no session-keyed lookup exists

- **Root layer:** module-design
- **Missed invariant:** A session has exactly one current block per kind in a day file; writers upsert by session id.
- **Searched:** grep -rn 'Mid-session checkpoint' backend; grep -rn 'Recovered from crash' backend; grep -rn '_META_PREFIXES' backend
- **Prior instances:** 1:
  - checkpoint append gained a has_new_files/has_new_commits guard to reduce redundant writes, which still fires on every workspace auto-commit
- **Siblings (same shape elsewhere):** 2:
  - C1 summarization.py ~462-488 — file-name extraction keeps any '/'-bearing token
  - C2 runtime_hooks.py ~2196 high-signal capture and ~325 correction detector — match on the full prompt including system-injected blocks; run_delivery_summary.py _META_PREFIXES already names these blocks but is not shared

## Pre-mortem

- Upsert by session id breaks the distiller's 'recurs in >=2 DailyActivity files' gate or its jsonl reader; check consumers of the jsonl sidecar.
- Replacing a block loses content the earlier block had (e.g. a checkpoint captured files the final summary omits); the summary must supersede only its own kind.
- Stripping injected blocks removes real user text when a user pastes text that starts with the same header; strip only the exact system-generated block shapes.
