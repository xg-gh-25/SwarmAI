---
title: Super Canvas — one surface, list over a raised content card
date: 2026-10-23
created: 2026-10-23
updated: 2026-10-23
project: SwarmAI
status: implemented 2026-10-23 (3 commits, agreed with XG from chat mockups)
---

# Super Canvas

Canvas becomes one surface instead of stacked parts: a recessed band (tabs + a
list of at most ~5 rows that scrolls) over a raised content card. Agreed with XG
2026-10-23 from chat mockups. Frontend only.

## Commit 1 — logic

| # | Change | Where the fact lives (one source) |
|---|---|---|
| L1 | **Pin and mute merge into one pin.** Today both do exactly one thing, `if (pinned \|\| muted) return` in `useCanvasAutoSurface`, and both are per-tab. `muted` is removed from the slice, `CanvasHostApi`, `FileViewerPanel` props, `useCanvasAutoSurface` and `uiContext` (the wire payload stops sending it; backend `CanvasState.muted` defaults to `False`, so it stays compatible and is left as is). Pin means: Canvas does not change by itself. | `CanvasTabState.pinned` |
| L2 | **Auto-surface yields while the Changes tab is shown.** Today an auto-opened file runs `setCanvasTab('artifacts')` and pulls the user off the diff they are reading. `canvasTab` moves from panel-local state into the per-tab `CanvasTabState` (the panel never remounts on a chat-tab switch, so panel state carried tab A's Changes into tab B). The hook reads it from the slice and returns early on `changes`. Every file open writes `canvasTab: 'artifacts'` together with the file, in the `swarm:open-file` handler. | `CanvasTabState.canvasTab` |
| L3 | **Default selection.** When the Artifacts view becomes visible on the same chat tab (first mount included) with no open file, a non-empty list and no pin, the panel opens the list's first row with the click's detail plus `auto: true` and the tab id. The open handler stores `auto` on the slice's file; the hook's "user is viewing their own file" is `slice.file && !slice.file.auto`, so a file Canvas opened by itself (auto-surface or default selection) may be replaced by the next output, and a user's file never is. This replaces the hook's old window-event mirror of the panel and its pre-resolution path comparison (which missed a resolved alias). | `CanvasFile.auto`; order from `railOrder()` (`hooks/railSsot.ts`) |
| L4 | **List order = newest first by `firstSeen`.** The A-first grouping goes; A/M stays as a badge. `railOrder()` is the one ordering used by the rail and by L3. | `railOrder()` |

Known limit (L3/L4): the data has only `firstSeen`, not the last write. A file the
agent rewrites later keeps its old place, so after a rewrite the first row and the
file auto-surface opened (the last written) can differ. Not filled by a guess.

Review round 2 fixes (in commit 1, missing from its message): the ~600ms
auto-surface delay now judges by the chat tab the file will land in, not the tab
active when the timer fires; a direct `setFile` also writes `canvasTab:
'artifacts'`; the tab control is controlled only when both value and setter are
passed.

## Commit 2 — list and card (visual)

As built:

- Header: no accent tint and no bottom hairline, so header + list are one recessed
  `--color-bg` surface; caret is an `icon-btn`; every idle icon (caret, pin, Changes
  refresh, expand, collapse) is `--color-text-muted`; the active tab chip is
  `--color-card` with a `--color-border` ring.
- `.canvas-content-card` (index.css): 0 8px 8px inset, 1px border, 10px radius,
  light shadow; the old 2px primary top border is gone (the selected row carries the
  accent now).
- `.canvas-row-sel` (index.css) is the ONE selected-row style for both lists:
  card-coloured background, border ring, 3px primary bar. Other rows are muted text
  with a hover shade; the selected name is bold. Supersedes run_02c5e32a's
  accent-free list.
- Artifacts row: type icon, name, A/M, folder (dim, truncated), copy (hover),
  `firstSeen` time (`clockOf`: HH:MM today, M/D before). The selected row scrolls
  itself into view. The rail's `<ul>` is focusable; ↑/↓ opens the previous / next
  openable row through that row's own click (`onOutputListKey`), deleted rows
  skipped, no wrap.
- Changes row: A/M/D, type icon, name, folder, +/−; commit rows unchanged in
  content. The per-row `uncommitted` chip is gone. Run titles are sticky on the
  list's solid surface. The detail card's three-line header became one line:
  icon + name (or sha + subject) · state · +/−; the run › repo › path crumb moved
  to its tooltip.
- Not done: ↑/↓ in the Changes list (not in the agreed scope).

## Commit 3 — file chrome and icons

As built (panel variant only; `FileEditorModal` and the modal FileViewer unchanged):

- `FileViewer`'s file header row is gone for every type; so is `crumbOf`.
- `FileEditorCore` (text / md / svg) has no header and no footer in the panel.
  `panelBar` FLOATS over the bottom of the content (absolutely positioned,
  `pointer-events: none` except its buttons): a raised group
  (`.editor-float-group`) with ONE Preview / Source / Changes control (radiogroup;
  Preview only for md / svg; Changes disabled with nothing to compare), Review,
  Open externally (html / svg / pdf / xml only), Attach, and inline notes ("Large
  file"; "Click a line to comment" in Changes, which replaces the modal's hint row);
  Save on the right, rendered only while there are unsaved edits (disabled in
  Changes). Its root drops its own border / radius / shadow, the Canvas card is the
  frame.
  - Every content scroll layer gets `PANEL_FLOAT_PAD` (56px) bottom padding: the
    textarea, the highlight and search overlays (so they keep the textarea's scroll
    height), the markdown / svg previews and their large-file fallbacks, and the
    diff view. The gutter positions from scrollTop and needs none. The last lines
    scroll clear of the bar; mid-scroll, content passes under it.
  - (First landed as a 40px strip below the content in e6802e60e; changed to float
    at XG's request the same day.)
  - Reload: dropped for editor types (they reload on an agent write via
    swarm:file-changed and on window focus). Non-editor renderers (html, image,
    pdf, csv, media) reload on an agent write but not on an outside edit, so their
    status bar gains Refresh (`onRefresh`).
  - Copy path: dropped in the panel (editor bar and status bar, `showCopyPath`);
    each list row has it on hover.
  - Close: no button. The Browsing row's × (`onStopBrowsing`) opens the first
    output, the same open a row click sends; with no output it bumps FileViewer's
    `closeRequest`, which runs the existing guarded close (FileEditorCore's
    unsaved-changes dialog) and ends in `canvas.close`.
  - Error texts no longer say "Close this tab".
- List folded on Artifacts: the header shows the file's badge, name, and a dot
  while it has unsaved edits (FileViewer reports `onDirtyChange`).
- `fileIcon` / `fileIconColor` are deleted. `fileTypeBadge(name)` (utils/fileUtils)
  gives the label + colour and `<FileTypeIcon>` (components/common) draws it, used
  by the file tree (including its drag ghost), Working files, chat file cards and
  change rows, inline file references, the modal editor header and both Canvas
  lists. Labels: known types (MD, TS, JS, PY, {}, YML, PDF, HTM, IMG, SH, …);
  unknown extension → first 3 letters; extensionless → first 3 letters, with
  Dockerfile → DK and Makefile → MK. Colours are existing theme tokens; the label
  mixes in --color-text so a light type colour stays readable.

## Hot-path cost

None on send or stream. Default selection is one effect in the panel keyed on
(tab, Artifacts-visible); it dispatches at most once per transition. Auto-surface
lost two window listeners.
