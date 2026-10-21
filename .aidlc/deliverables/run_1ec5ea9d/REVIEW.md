# Review — gates and experts

**Run** `run_1ec5ea9d` · **profile** ? · **project** SwarmAI

> 你为什么stop 你继续啊

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

## Gates

| Gate | Verdict | Notes |
|---|---|---|
| Gate 0 — understanding skeptic | SUPPORTED | framing: is the claim observed, is the root the root |
| Gate 1 — plan skeptic | WARN | GATE 1 VERDICT: WARN |
| REVIEW — spec + conformance | approved · litmus PASS | 11 finding(s) |
| Gate 2 — adversarial experts | full · 19 finding(s), 6 open | Red Team: yes |
| Meta-review | RISKS | 3 risk(s) |
| Push-ready | no | findings: 1 unresolved HIGH / MEDIUM(conf>=7); exit criteria unmet: ['X1']; unverified: evaluate: completed with 1 open gate error(s): Migration-class Gate (AC11): the requirement is migration-shaped… |

## Experts

| Expert | Findings | Blocking | Fixed | Refuted | Open |
|---|---|---|---|---|---|
| code-quality | 0 | 0 | 0 | 0 | 0 |
| correctness | 5 | 2 | 3 | 0 | 2 |
| fixspec-skeptic | 0 | 0 | 0 | 0 | 0 |
| gate0-skeptic | 0 | 0 | 0 | 0 | 0 |
| gate1-skeptic | 0 | 0 | 0 | 0 | 0 |
| integration | 3 | 2 | 3 | 0 | 0 |
| meta-review | 3 | 1 | 0 | 0 | 3 |
| operational | 3 | 2 | 3 | 0 | 0 |
| performance | 1 | 1 | 1 | 0 | 0 |
| red-team | 3 | 1 | 3 | 0 | 0 |
| root-cause | 0 | 0 | 0 | 0 | 0 |
| security | 1 | 0 | 0 | 0 | 1 |
| spec-compliance | 0 | 0 | 0 | 0 | 0 |

_Dispatch:_ Agent tool: correctness, security, performance, operational, integration, red-team in one batch; fixspec-skeptic PASS after root-cause SOUND; round-2 re-check by the five specialists with fixed findings: all RESOLVED

## Gate 2 findings

| Expert | Severity | Finding | Status |
|---|---|---|---|
| correctness | HIGH | Long sessions still get a new summary block on every post-session run, which breaks AC1. `list_by_session_paginated(session_id, limit=500)` with only a limit returns the 500 most recent messages (database/sqlite.py:756-764, `ORDER BY create | fixed · inv I1 · class fix: identity = session_id in the one writer |
| integration | HIGH | The block_key for long sessions is not a stable identity. list_by_session_paginated(limit=500) returns the 500 MOST RECENT messages (database/sqlite.py:756-764, ORDER BY created_at DESC LIMIT ?, then re-sorted ascending). So messages[0] is | fixed · inv I1 · class fix: identity = session_id |
| operational | HIGH | Once a session reaches 500 messages, the block_key for long sessions is `<sid>@<id of the first message in the window>`. list_by_session_paginated(limit=500) returns the 500 newest messages, so the window moves forward each time messages ar | fixed · inv I1 · class fix: identity = session_id |
| performance | HIGH | Long sessions still get a new summary block on every post-session run, so the day file keeps growing. list_by_session_paginated(limit=500) returns the 500 most recent messages (database/sqlite.py:756-786, ORDER BY created_at DESC LIMIT then | fixed · inv I1 · class fix: identity = session_id |
| correctness | MED | Every writer now uses `md_lock`, which creates sidecar files `Knowledge/DailyActivity/YYYY-MM-DD.md.lock` and `YYYY-MM-DD.jsonl.lock`. These are 0-byte, non-dot files. `library_health` scans `Knowledge/` with `rglob('*')` and skips only nam | fixed · inv I1 · class fix: _day_lock in the one writer |
| integration | MED | The upsert-in-place blocks meet a reader that still assumes the file is append-only. _truncate_daily_content (prompt_builder.py:197-231, its docstring says 'keeping the *tail* (newest entries) since DailyActivity files are append-only') kee | fixed · inv I1 · class fix: upsert_session_block cuts and appends |
| meta-review | MED | lifecycle_manager.py ~1791 (unchanged) skips a startup-backlog session when `sid in da_content` (today's file). Before this change, checkpoint and crash blocks carried only session_id[:8], so a session that had a checkpoint but no summary w | OPEN |
| operational | MED | The upsert rewrites the block where it already is (content[:span[0]] + block + content[span[1]:]). The day-file tail injected into every session (prompt_builder._truncate_daily_content:197) keeps only the last part of the file, on the assum | fixed · inv I1 · class fix: upsert_session_block cuts and appends |
| red-team | MED | Replacing a block in place breaks the per-session tail injection, which assumes the newest content sits at the end of the file. prompt_builder._truncate_daily_content (~197) keeps only the last ~2000 tokens 'since DailyActivity files are ap | fixed |
| red-team | MED | For sessions with 500 or more messages, the block key changes on every run, so duplicate summary blocks come back. list_by_session_paginated(limit=500) returns the 500 MOST RECENT messages (sqlite.py ~756: ORDER BY created_at DESC LIMIT ?, | fixed · inv I1 · class fix: identity = session_id |
| security | MED | The new stripper is the only thing keeping system text out of the correction detector and the high-signal capture, but it ends a block at the FIRST closing marker it finds. `_strip_leading` cuts the resume block at the first line-start `\\n- | OPEN |
| correctness | LOW | `record["block_key"] = block_key` adds a new field to the JSONL record. The plan's 'never: change the JSONL record fields' rules this out. Downstream readers ignore it today, but it breaks the stated contract. It also only exists to support | fixed |
| correctness | LOW | RP48 stale docstring: the module docstring still says upsert_session_block 'replaces the span between that session's own markers ... or appends a new marked block; text outside the markers is never changed'. The fix now removes the old bloc | OPEN |
| correctness | LOW | `_strip_leading` runs on every segment split by `\\n\\n---\\n\\n`. So a typed segment that starts with a UI-state header is dropped up to its first blank line. I verified that `u + '\\n\\n---\\n\\n## Current UI State is broken, fix it'` returns onl | OPEN |
| integration | LOW | write_daily_activity adds a new 'block_key' field to the JSONL sidecar record, but the run's PLAN says never to 'change the JSONL record fields'. read_jsonl_sidecar and distillation read records as dicts and accept the extra key. Still, the | fixed |
| meta-review | LOW | Writes now replace the file by rename. Any writer still using a plain append would lose its write to the replaced file. My grep of core/hooks/jobs/routers/scripts found none, but writers using other variable names (knowledge_backflow_hook, | OPEN |
| meta-review | LOW | Captures and checkpoints rewrite the whole file under one directory-wide lock. Unverified whether every caller runs this off the event loop. | OPEN |
| operational | LOW | Each day now leaves two lock files in Knowledge/DailyActivity/: `YYYY-MM-DD.md.lock` and `YYYY-MM-DD.jsonl.lock` (written by md_lock from upsert_session_block, append_daily_text and _write_jsonl_sidecar). Retention (context_health_hook._enf | fixed |
| red-team | LOW | write_daily_activity adds a new 'block_key' field to the JSONL sidecar record, and _write_jsonl_sidecar keys on it. The PLAN says 'never: change the JSONL record fields'. Also, a record without block_key is matched by session_id, so it woul | fixed |

### Fixed by class

- **correctness · HIGH** — Long sessions still get a new summary block on every post-session run, which breaks AC1. `list_by_session_paginated(session_id, limit=500)` with only a limit returns the 500 most recent messages (database/sqlite.py:756-764, `ORDER BY create
  - invariant: I1
  - root cause: window-start key moved every run
  - class fix: identity = session_id in the one writer
  - break test: backend/tests/test_daily_activity_upsert.py::test_long_session_rerun_keeps_one_summary_block
- **integration · HIGH** — The block_key for long sessions is not a stable identity. list_by_session_paginated(limit=500) returns the 500 MOST RECENT messages (database/sqlite.py:756-764, ORDER BY created_at DESC LIMIT ?, then re-sorted ascending). So messages[0] is
  - invariant: I1
  - root cause: window-start key moved every run
  - class fix: identity = session_id
  - break test: backend/tests/test_daily_activity_upsert.py::test_long_session_rerun_keeps_one_summary_block
- **operational · HIGH** — Once a session reaches 500 messages, the block_key for long sessions is `<sid>@<id of the first message in the window>`. list_by_session_paginated(limit=500) returns the 500 newest messages, so the window moves forward each time messages ar
  - invariant: I1
  - root cause: window-start key moved every run
  - class fix: identity = session_id
  - break test: backend/tests/test_daily_activity_upsert.py::test_long_session_rerun_keeps_one_summary_block
- **performance · HIGH** — Long sessions still get a new summary block on every post-session run, so the day file keeps growing. list_by_session_paginated(limit=500) returns the 500 most recent messages (database/sqlite.py:756-786, ORDER BY created_at DESC LIMIT then
  - invariant: I1
  - root cause: window-start key moved every run
  - class fix: identity = session_id
  - break test: backend/tests/test_daily_activity_upsert.py::test_long_session_rerun_keeps_one_summary_block
- **correctness · MED** — Every writer now uses `md_lock`, which creates sidecar files `Knowledge/DailyActivity/YYYY-MM-DD.md.lock` and `YYYY-MM-DD.jsonl.lock`. These are 0-byte, non-dot files. `library_health` scans `Knowledge/` with `rglob('*')` and skips only nam
  - invariant: I1
  - root cause: visible per-file lock sidecars
  - class fix: _day_lock in the one writer
  - break test: backend/tests/test_daily_activity_upsert.py::test_day_file_lock_is_hidden
- **integration · MED** — The upsert-in-place blocks meet a reader that still assumes the file is append-only. _truncate_daily_content (prompt_builder.py:197-231, its docstring says 'keeping the *tail* (newest entries) since DailyActivity files are append-only') kee
  - invariant: I1
  - root cause: in-place replace broke newest-last
  - class fix: upsert_session_block cuts and appends
  - break test: backend/tests/test_daily_activity_upsert.py::test_rewritten_block_moves_to_the_end
- **operational · MED** — The upsert rewrites the block where it already is (content[:span[0]] + block + content[span[1]:]). The day-file tail injected into every session (prompt_builder._truncate_daily_content:197) keeps only the last part of the file, on the assum
  - invariant: I1
  - root cause: in-place replace broke newest-last
  - class fix: upsert_session_block cuts and appends
  - break test: backend/tests/test_daily_activity_upsert.py::test_rewritten_block_moves_to_the_end
- **red-team · MED** — For sessions with 500 or more messages, the block key changes on every run, so duplicate summary blocks come back. list_by_session_paginated(limit=500) returns the 500 MOST RECENT messages (sqlite.py ~756: ORDER BY created_at DESC LIMIT ?,
  - invariant: I1
  - root cause: window-start key moved every run
  - class fix: identity = session_id
  - break test: backend/tests/test_daily_activity_upsert.py::test_long_session_rerun_keeps_one_summary_block

## REVIEW findings

| Expert | Severity | Finding | Status |
|---|---|---|---|
| spec-compliance | HIGH | core/injected_blocks.py _strip_leading: the '## Attached Terminal Output' block (outermost prefix when a terminal is attached) was not stripped, so everything after it reached the capture hooks. Fixed: TERMINAL_HEADER shape with fence-aware close; prompt_builder imports the header. | fixed · inv I3 · class fix: every prefixer's header/terminator is a constant in core/injected_blocks.py that the builder imports |
| spec-compliance | HIGH | core/injected_blocks.py: the keyword-miss recall block was not stripped. Fixed: RECALL_MISS constant used by session_router and the stripper. | fixed · inv I3 · class fix: session_router builds the miss block from RECALL_MISS |
| code-quality | HIGH | core/daily_activity_writer.py upsert_session_block: text quoting the markers broke the block (partial replace / duplicate append). Fixed: quoted markers are neutralized and markers count only as whole lines. | fixed · inv I1 · class fix: _neutralize_markers + _line_starts in the one writer |
| code-quality | MEDIUM | hooks/daily_activity_hook.py: a session over the 500-message window would replace its earlier summary with a summary of only its latest messages. Fixed: a full window keys the block (and JSONL record) by <session_id>@<first message id>. | fixed · inv I2 · class fix: write_daily_activity(block_key=...) used whenever the window is full |
| code-quality | MEDIUM | core/daily_activity_writer.py _atomic_write: mkstemp made rewritten day files 0600. Fixed: the original mode is kept (0644 for new files). | fixed |
| code-quality | LOW | Session id 'unknown' was a shared upsert key. Fixed: id-less writes append instead of replace (md and JSONL). | fixed |
| code-quality | LOW | core/summarization.py: a redacted cut summary can be under the cap. Fixed: treat summaries within 30 chars of the cap as cut. | fixed |
| code-quality | LOW | core/runtime_hooks.py _classify_message_language docstring named only the UI-state prefix. Fixed. | fixed |
| code-quality | LOW | _atomic_read_modify_write is a thin delegate; kept because the lock-timeout tests and callers name it. | OPEN |
| spec-compliance | LOW | A session keeps one checkpoint block and one summary block per day (AC1 is per kind). Left as specified; the requirement's FR-1 says per kind. | OPEN |
| code-quality | LOW | 'cp a dir' with no trailing slash is still listed as a file (parser cannot know a destination is a directory). Known limitation of parse_bash_write_targets. | OPEN |

## Open items

- Known issue (security MED conf 6, no worse than before): a replayed resume/recall body that quotes its own closing marker ends the strip early; needs a builder-enforced boundary in session_router
- Known issue (LOW, pre-existing): a typed delimiter segment that starts with a UI-state header is stripped
- Pre-existing: hooks/distillation_hook.py references undefined 'timezone' (also at HEAD)
- Pre-existing red on base: test_cli_skill_overrides, test_model_registry stdlib-only
- The meta-review lifecycle_manager fix landed after the round-2 re-check (covered by the final regression run and a unit test)
- [runner] Not tested in this run (regression): 482 test file(s) reach a changed module only through other modules and were not run: backend/tests/test_active_run_commit_gate.py, backend/tests/test_adaptive_timeout.py, backend/tests/test_admission_backfill.py, backend/tests/test_admission_band.py, backend/tests/test_admission_calibration.py, backend/tests/test_admission_is_noise.py, backend/tests/test_admission_rootfix.py, backend/tests/test_admission_routing.py, backend/tests/test_admission_trust_field.py, backend/tests/test_admission_trust_stamp.py, backend/tests/test_advance_continuation.py,…
- [degraded] deliver completed with 3 open gate error(s): 1 unresolved blocking finding(s) — HIGH (any confidence) or MEDIUM with confidence >= 7 (missing confidence = fail-closed). Fix each and set 'resolved': true, o; Gate-2 findings: adversarial_review.findings[] must carry, one record each, every finding the experts reported (HIGH at conf >= 5, MED at conf >= 7) under its `; Depth: adversarial_review has 1 unresolved blocking finding(s) — HIGH (any confidence) or MEDIUM with confidence >= 7 (missing confidence = fail-closed). Fix be
