---
created: 2026-10-30
updated: 2026-10-30
title: "The files a Hive run writes come back into the run's Brain"
status: implemented 2026-10-30 (native)
---

# The files a Hive run writes come back into the run's Brain

Owner decision (2026-10-30): a file a dispatched run writes (a report, a page, a note)
comes back to this machine into the directory of the Brain the run went under; a run
under no specific Brain is under the General Brain.

## 1. What was wrong

A Hive run record (core/hive_records) carried `bundle`, `turn`, `lessons`, `closed`. The
turn's `turn_deliverables` block listed the files the turn wrote, by path only, so a
"write a report" task left its report on the Hive: the run card said "wrote 1 document"
and nothing here could open it.

## 2. The change

1. **Hive: a `file` entry per deliverable.** When a turn's record is sealed
   (`seal_turn`), each file its `turn_deliverables` block lists is read and sealed as a
   `file` entry whose manifest line carries `path`, the file's path relative to the
   workspace. Read through directory descriptors opened without following a link, as a
   regular file only. Not sealed (and logged): a path outside the workspace, under
   `Hive/` (repositories come back as bundles), a secret path (`is_secret_path`), a file
   over `MAX_BYTES["file"]` (20 MB; never cut). The block's own cap (12 files a turn,
   `omitted` counts the rest) is the cap here too.
2. **Host: kept, checked, placed.** `pull` takes a `file` entry like any other (next seq,
   size cap, sha256), and additionally its `path` must be a plain relative path (no
   `..`, no absolute, no empty or dot segment, not secret). It is then written into
   `<Brain dir>/deliverables/hive/<YYYY-MM-DD>-<dispatch id>/<path>`: the Brain is the
   dispatch row's `project_agent.brain`, General (`Knowledge/Library`) when there is
   none; written through no-follow directory descriptors to a fresh name and renamed in
   (core/brain_sync), then the record's own copy is deleted (the Brain's copy is the one
   kept). A Brain no longer here is never made (every directory under `Projects/` reads
   as a project): the file goes to General. A later turn that rewrites the same file
   replaces it. `placed.json` (`{seq: {path, listed, local, error}}`, written under the
   record's lock) says where each one went or why not; a failed placing is tried again at
   every pull. A file over the run's kept budget is listed as `skipped` without its bytes
   and never stops the pull, so the turns, lessons and close after it still come back.
3. **Returned files are deliverables, not knowledge.** The Library index
   (`knowledge_store.sync_knowledge_index`) and CDF's corpus (`card3.corpus_paths`) skip
   `deliverables/hive/` (`hive_files.is_returned`): what a Hive wrote, possibly after
   reading an injected page, never reaches recall as trusted Library text; it reaches
   cognition only through CDF (lessons). DDD recall reads fixed trees and never reads a
   Brain's `deliverables/`.
4. **General Brain sync skips `deliverables/`.** The General Brain is the whole
   `Knowledge/Library`, every `.md` of which goes to each Hive; returned files are not
   pushed back out. A project Brain's top-level `deliverables/` was never synced.
5. **Run details.** The result's `files` carry `local` (the workspace path here) for each
   one that came back, matched by identity (the exact path the turn's block listed, kept
   on the entry as `listed`), or `not_placed`; the run's details list them, each opening in the Canvas
   (`swarm:open-file`); a file that did not come back keeps showing its Hive path. No
   "N did not come back" count: a Hive older than this change never sends files, and
   the count would read as a failure for every one of its runs.

## 3. Not changed

- Code in repositories still comes back as commits (`hive_bundle` → `hive_return`).
- Lessons still go through CDF and the person's acceptance; a returned file is a
  deliverable on disk, the same as a report written on this machine.

- **Known issue:** a host older than this change stops its pull at a `file` entry
  (`unknown_kind`), so a Hive newer than its host would cut that host's view of the run
  after the first file. A Hive's release is built from its host's own tree, so in the
  normal update flow a Hive is never newer than its host; a host rolled back after its
  Hives were updated would hit it.

## 4. Verification

- Hive: a turn listing a report seals one `file` entry with its path and bytes; a
  symlinked file, a file under `Hive/`, a secret path, and a file over the cap seal none.
- Host: a pulled `file` entry lands under the run's Brain (General when the row names
  none) at `deliverables/hive/<date>-<dispatch>/<path>`; a path with `..` or absolute is
  refused at validation; a symlinked destination directory is refused, nothing written.
- General Brain sync does not include `deliverables/`; the Library index and CDF's corpus
  do not read a returned file.
- A failed placing is said and placed at the next pull; the record's copy is gone once
  placed; a gone Brain is not made; a file over the kept budget never stops the pull; a
  FIFO is never read.
- The run's details open a returned file's copy here; a file not returned shows its Hive path.
- Each guard reverted turns its test red (`scripts.mutation_check`).
