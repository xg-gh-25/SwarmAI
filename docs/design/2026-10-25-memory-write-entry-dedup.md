---
title: Memory files are changed whole entries at a time, never lines
status: implemented v4 (2026-10-25; v1/v2 needs-redesign → v3 deleted write-time dedup; v3 review kept the direction and asked for four invariants, folded in here; §8 lists where the build differs)
date: 2026-10-25
created: 2026-10-25
updated: 2026-10-25
owner: Gawan
---

# Memory files are changed whole entries at a time, never lines

## 1. Why

EVOLUTION.md corrections C058 and C060-C062 were written as bare `### Cnnn` headers;
some MEMORY entries were written without their metadata line. Reproduced on synthetic
files (live files only read):

| # | Symptom | Mechanism |
|---|---|---|
| S1 | EVOLUTION correction written as a bare header | write dedup drops `- **Correction**:` / `- **Status**:` because those bold labels already exist in the file |
| S2 | MEMORY entry written without metadata | two metadata lines (same date + source) are byte-identical; the 120-char prefix rule drops the second |
| S3 | COE status update never lands; an orphan metadata line can be prepended | same `**topic**` bold → updated line dropped, its metadata kept |
| S4 | A different lesson with an existing headline is dropped | bold-title rule is a similarity guess |
| S5 | EVOLUTION dedup sweep strips one field line out of a `### Cnnn` block | `parse_entries` reads `- **Correction**:` and `- **Status**:` as two entries |
| S6 | Save-to-Memory entries are never swept | `memory_extractor` writes `- {date}: text`, which `_ENTRY_RE` does not parse |
| S7 | A swept entry leaves orphan `Detail:` / `valid_from` lines | distillation writes entry, metadata, then `Detail:`; `parse_entries` / `_strip_entries` end the entry at the metadata line |
| S8 | Scheduled stale-memory removal leaves orphan lines | `memory_health._remove_memory_entry` deletes the matching LINES only |

None of these logs a dropped line.

## 2. Root cause

MEMORY and EVOLUTION hold **multi-line entries**, but several mutators change them
**a line at a time** or with a parser that sees only part of an entry:

| Mutator | Unit today | Symptom |
|---|---|---|
| write dedup `filter_duplicate_entries` | line, guessed duplicates | S1-S4 |
| sweeps via `parse_entries` / `_strip_*` on EVOLUTION | bullet inside a `###` block | S5 |
| sweeps via `parse_entries` / `_strip_*` on MEMORY | lead..metadata, not the tail after it | S7 |
| `memory_health._remove_memory_entry` | line | S8 |
| `memory_extractor`, `runtime_hooks` writers | hand-built strings | S6 |

v1/v2 tried to make the write-time dedup entry-aware and hit a new grammar problem per
round. That layer duplicates the sweep's job and guesses. v3 deleted it; its review
kept that direction and required that the sweep then really covers what dedup did.

**Invariant of this design: every mutation of MEMORY.md / EVOLUTION.md adds, replaces
or removes whole entry spans; duplicates are judged in exactly one place, by one
signature.**

## 3. Design

### 3.1 One entry span

`parse_entries` / `_strip_entries` / `_strip_entries_by_line` (`core/ddd_entry_lifecycle`):
an entry is the lead bullet, its continuation lines, its `<!-- ref -->` metadata
line, **and the indented lines directly after the metadata** (today's `Detail:` /
`valid_from` tail), up to a blank line, a column-0 `- `, `##` or `###`. Writers keep
today's shape (metadata second; `format_memory_entry`, section caps, size-valve and
the migration utility already expect it). Existing entries with a tail become whole
spans too.

`EntryMetadata` gains `end_line`, so every strip removes exactly `line_number..end_line`.

### 3.2 One duplicate signature, one place

`entry_signature(e) = content_signature(lead line)`: the lead line only, so the
`Detail:` path, `valid_from`, metadata, and the trailing `(date[, run])` attribution
never enter it. The same lesson distilled on two days, or by two runs, has the same
signature.

`reclaim_duplicate_entries` groups by `(section, entry_signature)` and keeps one
survivor (highest ref, then newest) for **all types**, protected ones included. Two
copies of the same decision in the same section carry nothing a single copy lacks;
section scoping preserves deliberate cross-section repeats. COE Registry stays
excluded (it is keyed, §3.5). Archive-before-strip unchanged.

This sweep is the only duplicate judge. It already runs on every session close,
after distillation in the same hook chain (`main.py` hook order; context_health MEMORY
lifecycle).

### 3.3 Delete write-time dedup

- `scripts/locked_write.py`: delete `entry_dedup_keys`, `filter_duplicate_entries` and
  the dedup branch. `dedup` stays an accepted, ignored keyword (like `reindex_memory`).
- Template copy `templates/ddd-skills/s_ddd-persist/scripts/locked_write.py`: same;
  fix its `SKILL.md` dedup promise.
- Internal callers drop `dedup=True`: `distillation_hook._run_locked_write`,
  `memory_extractor._write_entries`, `runtime_hooks`.

Between a write and the next session close an exact duplicate can exist; the sweep
removes it.

### 3.4 EVOLUTION sweeps skip every `###` span

`parse_entries(content, *, skip_h3_spans=False)`: when True, nothing between a `###`
header and the next `###` / `##` becomes an entry (numeric IDs and narrative blocks
alike: `### C060`, `### CLASS …`, `### ROOT-CAUSE …`, `### DATA-POINT …`). Top-level
bullets (e.g. `evolution_optimizer`'s Competence line) stay eligible. Only the
EVOLUTION branch of `context_health_hook` passes it, threaded through
`assess_decay` / `inject_entry_metadata` / `reclaim_noise_entries` /
`reclaim_duplicate_entries`. Default False: DDD docs, MEMORY, KNOWLEDGE, UI counts and
retire are unchanged. `###` blocks keep being managed whole by fold and the EVOLUTION
size-valve.

### 3.5 Keyed records go through one guarded locked transform

New in `scripts/locked_write.py`:

```
locked_section_transform(path, section, fn: Callable[[str], str]) -> bool
```

Under the file lock: read, take the section body, call `fn(body)` once, run
`MemoryGuard.sanitize` and (for MEMORY.md) `validate_memory_content` on the RESULT,
write back. Returns whether bytes changed; raises `LockedWriteError`. `fn` must be
pure (no I/O, no other writer).

Used by:
- **COE Registry upsert** (`_write_coe_registry`). Key = `coe_key(topic)`: the title
  as `format_memory_entry` stores it (`strip().strip("*").rstrip(".").strip()`),
  casefolded; used for grouping and matching. All canonical entries with that key plus
  the new events merge into one: session dates = sorted union of valid ISO dates from
  each body's `Sessions:` list and the events (a body whose list does not parse is
  left untouched and the new events go into a new entry); status Resolved if any side
  is; metadata = the highest-`ref` old entry's line with `last:` set to the newest
  date; placed at the section top. Non-canonical legacy lines are left as they are.
  Replaying the same events changes nothing.
- **C/K-ID allocation** (`_write_corrections`, `_write_competence`): the next ID is
  computed from the body inside `fn`, so allocation and insertion are one locked step.

### 3.6 Writers emit canonical entries

One helper `canonical_entry_from_text(text, entry_type, date, *, run_id=None,
source)` in `ddd_entry_lifecycle`: strips a leading `- `, `YYYY-MM-DD:` and `[type]`,
splits title/body on the first ` — ` (no dash: body = text, title = first 60 chars),
calls `format_memory_entry`. Type is given by the caller, never re-guessed.
- `memory_extractor._write_entries`: type by bucket (`key_decisions`→decision,
  `lessons_learned`→guideline, `open_threads`/`recent_context`→process).
- `runtime_hooks` correction: `pitfall`, `run_id=sid[:8]`.

### 3.7 Scheduled removal removes spans

`memory_health._remove_memory_entry`: locate the entry by its lead line, remove its
`parse_entries` span (`line_number..end_line`) instead of every line containing the
needle.

### 3.8 Not changed

- Historical data; existing `- date:` lines are not rewritten.
- `parse_entries` default behaviour; DDD docs and `apply_to_ddd` dedup.
- Section caps, size-valves, fold.
- `memory_health` stale-decision detection only recognizes `[KD07]`-style IDs; it did
  not match the extractor's old `- date:` shape either, so this is no regression.
  Noted, not changed.
- `_update_open_threads` still counts each COE event it is given.

## 4. Trade-off

Reworded duplicates (same headline, different wording) are written; nothing guesses
them away. Exact repeats (same lead line apart from date/run) are removed at the next
session close, in every type. Caps still bound each section (Decisions 60, Guidelines
230, Pitfalls 200, Models 30, Processes 30, COE 15). In exchange, no correct entry is
dropped or written in pieces.

## 5. Where it runs and cost

Distillation, COE, EVOLUTION writes: post-session hook (serial worker). Correction
write: `UserPromptSubmit` hook. Save-to-Memory: user request. Writes do less work than
today (no dedup scan). The sweep's parse cost is unchanged (same parser, a few more
lines per span).

## 6. Acceptance tests (written first; red on current main unless marked)

1. S1: EVOLUTION containing `**Correction**`/`**Status**`; write 3 corrections → 3
   complete blocks.
2. S2: two entries, same date + source, separate writes → both keep metadata.
3. S4: same headline, different body → both written.
4. S5: two `### Cnnn` blocks with identical bodies, plus `### CLASS …` and
   `### DATA-POINT …` blocks → EVOLUTION sweep leaves all `###` spans byte-identical;
   a duplicated top-level bullet outside them is still swept.
5. S6: Save-to-Memory writes canonical shape with the bucket's type; saving twice then
   sweeping leaves one.
6. S7: entry with `Detail:` + `valid_from` after metadata, duplicated, swept → one
   complete entry, no orphan lines; neighbours untouched; 3+ tail lines work.
7. Cross-day / cross-run: the same lesson written as `(2026-10-24)` and
   `(2026-10-25, run_x)` with different `Detail:` paths → swept to one.
8. Protected types: duplicate `decision`/`principle`/`correction`/`model` in one
   section → one survivor; the same decision in two sections → both kept.
9. S3 / COE: Investigating T then resolution T → one entry, Resolved, both dates,
   `last:` newest, `ref` kept; `T.` matches `T`; two existing canonical T entries merge;
   replay changes nothing; unparsable `Sessions:` body untouched.
10. `locked_section_transform` runs MemoryGuard + validation on the result: an
    injection pattern in a COE topic is rejected as today.
11. Concurrent C/K writers (two processes, distinct bodies) → unique IDs, complete
    blocks.
12. S8: `_remove_memory_entry` on a canonical entry with tail → whole span gone.
13. (green, pin) `parse_entries` default output unchanged on a DDD doc with `### E001`
    headers; `dedup=True` still accepted.

Each fix is mutation-checked: revert that fix, its named test goes red.

## 7. Delete / update list

- `scripts/locked_write.py` + template copy: `entry_dedup_keys`,
  `filter_duplicate_entries`, the dedup branch; module/API docstrings.
- Template `SKILL.md` dedup promise.
- Docstrings describing "120-char prefix + bold-title": `routers/memory.py`,
  `core/memory_extractor.py`, `distillation_hook._run_locked_write`,
  `core/ingestion_gate.py:798`.
- Tests asserting write-time dedup: `tests/test_locked_write.py` dedup cases,
  `tests/test_runtime_hooks.py` AC4, `tests/test_memory_hardening.py` ~385-418 →
  replaced by §6.
- `reclaim_duplicate_entries` `_dedup_protected`: drop the type protection (keep COE).

## 8. As built (differences from the text above)

- §3.5's `locked_section_transform` was not added as a second facade. Instead
  `locked_read_modify_write` accepts `text` as a callable `build(content) -> str`,
  called once with the content read under the lock; its result passes the same
  MemoryGuard + MEMORY validation as a string, and an empty result is a no-op (except
  in replace mode). One write door, not two. COE uses it with `mode="replace"` on the
  COE section; C/K blocks with `mode="prepend"`.
- §3.6's helper is `core.ddd_entry_lifecycle.canonical_entry_from_text`; the
  Save-to-Memory type comes from `MEMORY_SECTIONS[section]["type"]`.
- §3.1 also makes the continuation scan in `parse_entries` and both strip helpers stop
  at any markdown heading (`#`..`######`). Before, a `### …` line after an entry was
  read as its continuation and stripped with it.
- The EVOLUTION branch passes `skip_h3_spans=True` to its decay `parse_entries` as well
  as to both reclaim calls. The decay one is not observable by a test today (every
  EVOLUTION section is evergreen, so decay never transitions); it keeps the three
  steps reading the same entries.
- The archive still stores `raw_text` (lead + continuation). A swept duplicate's
  `Detail:` / `valid_from` tail (provenance only) is not copied to the archive.
- Code review (fresh-context, 2026-10-25) found that `parse_entries` and
  `inject_entry_metadata` still had their own boundary loops. All four readers
  (`parse_entries`, `inject_entry_metadata`, `_strip_entries`, `_strip_entries_by_line`)
  now call one scanner, `_scan_entry`, which returns the entry text end, the metadata
  line and the span end. A blank run belongs to the entry only when the metadata line
  follows it. Headings follow CommonMark (`^ {0,3}#{1,6}( |\t|$)`).
- §3.2's signature is the entry TEXT before its metadata (lead + continuation), not
  the lead line alone: two entries with the same lead but different continuation text
  are different entries, and deleting one on a lead match would be a guess. The tail
  and metadata never enter it.
- The duplicate-sweep exclusion is `section == "COE Registry"` (a title containing
  "COE" elsewhere is an ordinary entry).
- `memory_health._remove_memory_entry` matches the needle only on the lead line of a
  parsed entry (then removes its span) or on a line outside every span; a match
  inside an entry's continuation or tail removes nothing.
- `locked_read_modify_write(..., create_section=True)` creates a missing section
  instead of writing under `## Distilled`; COE Registry uses it so the next event
  finds its record.
- `canonical_entry_from_text` reads a leading `**title** — body` before the generic
  first-dash split.

