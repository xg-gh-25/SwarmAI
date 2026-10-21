# Requirements

**Run** `run_95bd0b0c` · **profile** full · **project** SwarmAI

> 按推荐

_Generated from the run's stage artifacts by the pipeline (not hand-written)._

**Verdict** GO · **Scope** standard · **Scores** strategic 8, priority 9, feasibility 9

## Requirements

| ID | Requirement | Source | Status |
|---|---|---|---|
| FR-1 | Archiving a decayed DDD entry moves it: it leaves the live doc in the same pass it is written to the archive. | user-stated | confirmed |
| FR-2 | Appending to an archive is idempotent: an entry already in the archive is not appended again. | ddd-derived | confirmed |
| FR-3 | A test forces the repeated-run path (two passes on one fixture) and goes red on today's code. | user-stated | confirmed |

**FR-1 acceptance**
- Given a live IMPROVEMENT.md with a dormant entry idle past the archive threshold · when the orchestrator lifecycle pass runs · then the entry is in IMPROVEMENT-archive.md and absent from IMPROVEMENT.md

**FR-2 acceptance**
- Given an archive that already holds entry E · when archive_entries is called with E again · then the archive file content for E is unchanged and E appears once

**FR-3 acceptance**
- Given the current main code · when the two-pass test runs · then it fails (archive holds the entry twice, live doc still holds it)

### Acceptance criteria

- AC1: When the DDD entry lifecycle (ddd_orchestrator decay pass) transitions an entry to archived, that entry's bullet block is no longer present in the live doc after the pass, and it is present in the sibling archive file.
- AC2: Running the lifecycle pass twice on the same fixture leaves each archived entry exactly once in the archive file (second pass appends nothing for it).
- AC3: archive_entries skips an entry whose bullet text is already present in the target archive file, for every caller (decay pass, reclaim, retire).
- AC4: An entry in the live doc that is already marked decay:archived or decay:dormant past the archive threshold (today's real shape: still live after being archived many times) is archived at most once more and then stripped; no archive content is ever removed or rewritten.

## Understanding (Gate 0)

- **Present state:** In the current code, the orchestrator's DDD decay pass appends an archived entry to the archive file but leaves its bullet in the live doc with its old decay:dormant metadata, so every later pass re-archives the same entry.
- **Evidence:** Code-trace: core/ddd_orchestrator.py:915-941 calls assess_decay, sets t.entry.decay_state, calls archive_entries(project_dir, to_archive, source_path=imp_path), then writes inject_entry_metadata(content, active_entries) where active_entries excludes the archived ones. core/ddd_entry_lifecycle.py:1003-1058 archive_entries only appends (existing + new_content), no dedup. inject_entry_metadata (642-737) only rewrites metadata of entries passed in and never removes bullets, so the archived bullet keeps its old <!-- decay:dormant --> line. Observation: Projects/SwarmAI/2-understanding/IMPROVEMENT.…
- **Alternative framing considered:** Follow the sibling MEMORY/KNOWLEDGE/EVOLUTION paths in hooks/context_health_hook.py (~2015-2060): drop archive_entries from the decay pass, persist the archived state via inject, and let reclaim_noise_entries be the single archive+strip authority. Loses here because reclaim protects keep-class entries (is_keep_class: decision/model/principle/correction/COE, ref>=2), so a decision or model entry that decays to archived would stay live forever marked archived — the requirement is that an archived entry leaves the live doc. Reusing _archive_and_strip (already the shared archive+strip tail for re…
- **Gate-0 skeptic:** SUPPORTED

## Root cause

W1. Archives grow by the same entries every run: one entry appears 1,375 times.  
   _evidence:_ IMPROVEMENT-archive.md: 116,194 bullets / 269 distinct; Counter of titles
W2. The decay pass archives an entry but never strips it from the live doc, so the next pass sees it dormant again and re-archives it.  
   _evidence:_ ddd_orchestrator.py:926-941 archive_entries + inject_entry_metadata(active_entries); IMPROVEMENT.md:1143 decay:dormant
W3. archive_entries was designed as append-only with no idempotency, and the decay pass treats 'annotate' as if it were 'move'; the two archive paths (decay vs reclaim/retire) diverged: reclaim/retire go through _archive_and_strip, decay calls the raw appender.  
   _evidence:_ ddd_entry_lifecycle.py:1651-1693 _archive_and_strip (archive + _strip_entries); commit 8c3bb512 introduced archive_entries; commit 86a48c0d wired reclaim with strip but left the decay call as-is

- **Root layer:** module-design
- **Missed invariant:** An archived entry exists in exactly one place: archiving is a move (append once, strip from the source), never a copy.
- **Searched:** git log -S'archive_entries(project_dir, to_archive' --oneline; git log -S'inject_entry_metadata only annotates' --oneline; grep -rn 'assess_decay(' backend (non-test callers); grep -rn 'archive_entries(' backend (non-test callers)
- **Prior instances:** 2:
  - run_f71e5920: a 17MB orphan archive from the decay pass writing to the wrong directory (path split-brain on the same call site, fixed by source_path=imp_path)
  - hooks/context_health_hook.py MEMORY/KNOWLEDGE/EVOLUTION lifecycle comments already name this exact double-archive (archive_entries has no dedup, inject can't strip) and avoided it by not calling archive_entries
- **Siblings (same shape elsewhere):** 3:
  - C1 core/ddd_entry_lifecycle.py:1635 reclaim_duplicate_entries → archive_entries — strips after archiving (safe) but the append itself has no dedup
  - C2 core/ddd_entry_lifecycle.py:1684 _archive_and_strip → archive_entries — archive+strip (safe); append not idempotent if the same entry is ever re-selected
  - C3 hooks/context_health_hook.py:2020/2175/2290 MEMORY/KNOWLEDGE/EVOLUTION decay — deliberately does not archive in the decay pass; reclaim is the single authority — not affected

## Pre-mortem

- Stripping by (title, section) removes a different live entry that shares the archived entry's title in the same section (duplicate titles exist: audit found 15 near-duplicate pairs) — the strip must only remove the selected entries.
- The dedup check misses because the archive stores raw_text plus a rewritten metadata line, so comparing whole blocks never matches; compare on the bullet text, not the metadata.
- The first pass after deploy re-archives hundreds of already-archived dormant entries at once and strips them from live docs; correct by design but must not touch archive content or entries that are not archived-state.
