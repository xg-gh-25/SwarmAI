---
title: Super Canvas — fixing spec (round 1)
date: 2026-10-23
created: 2026-10-23
updated: 2026-10-23
project: SwarmAI
status: draft for XG review
---

# Super Canvas — fixing spec (round 1)

Follow-up to `super-canvas.md` (commits c8f6eb87c, 1726b6a84, e6802e60e,
3b974b9d4). Lists every problem found by XG's review and the agent's self-review,
groups them by cause, and gives one fix per group. Frontend only, panel variant
only unless stated.

## 1. Problems

| # | Problem | Evidence |
|---|---|---|
| F1 | No refresh for text / md / svg files. Commit 3 removed it because the editor "reloads when the window comes back", but it only listens to `visibilitychange`, which never fires when you click from a side-by-side VS Code back into SwarmAI. An outside edit never shows. | `FileEditorCore.tsx` visibility effect: `document.addEventListener('visibilitychange', …)` only |
| F2 | Attach to chat is missing for text / md / svg in the panel. `attachBtn` renders only when `onAttachToChat` is passed; ChatPage never passes it to the Canvas, so it never rendered in the panel (the status bar of non-editor files falls back to the `swarm:attach-file` event; the editor has no fallback). | `grep onAttachToChat pages/ChatPage.tsx` → no hit |
| F3 | The floating group is nearly invisible: `--color-bg` fill on a `--color-card` card, 1px `--color-border`, muted icons. | `.editor-float-group` in `index.css` |
| F4 | "Stop browsing" with no output can close the wrong file. Panel FileViewer silently keeps a list of every file opened (up to 10); a close only ends in `canvas.close` when one is left, else FileViewer shows another hidden file while the Canvas still marks the closed one. | `FileViewer.handleCloseTab`: `if (tabs.length <= 1) onClose()` |
| F5 | That hidden list also loses edits: switching rows with unsaved edits drops them (FileEditorCore remounts per file) but leaves the hidden tab flagged dirty; 10 such flags block every new open ("all 10 tabs have unsaved changes"). Auto-surface can also replace an auto-opened file the user has started editing. | `useFileViewerTabs.openTab` eviction; `useCanvasAutoSurface` reads only `file.auto` |
| F6 | First load waits for two serial requests: the file, then its committed baseline (`git show`) for the diff. | measured on the live daemon (read-only HTTP): `/workspace/file` 8–17 ms, `/workspace/file/committed` 119–164 ms (AGENTS.md 36 KB, FileEditorCore.tsx 96 KB, security_hooks.py 402 KB) |
| F7 | A file the agent is changing re-does all work on every write: one full GET per `swarm:file-changed` (no coalescing, no ordering guard, so an older response can land after a newer one), a full markdown re-render, and a "changed lines" overlay that renders a `<div>` for EVERY line of the file. | jsdom bench: markdown render 127–183 ms (36 KB), 137–205 ms (45 KB); overlay 123 ms for a 2,105-line file |
| F8 | Large markdown under the 100 KB guard renders the whole document synchronously on open and on every rewrite (same numbers as F7), blocking the panel. | same bench |
| F9 | File tree misalignment: file badge 21 px wide, folder icon 16 px, so names at one depth do not line up. | `.file-badge-sm` vs `icon-sm` |
| F10 | ↑/↓ list has no visible focus (`outline-none`). | `CanvasOutputRail` `<ul>` |
| F11 | Changes list is 220 px (~8 rows); XG's rule is a 5-row list. | `FileViewerPanel` `max-h-[220px]` |
| F12 | Not yet verified on WebKit: textarea bottom padding counted in scroll height (the floating bar's end room). | jsdom does no layout |
| F13 | The selected list row barely stands out: `.canvas-row-sel` is the card colour (`--color-card` #1a1f2b dark / #fff light) on the list's `--color-bg` (#111318 / #f8fafc) — a 3–5% lightness step — plus a 3px bar. | XG 2026-10-23 |

## 2. Causes and fixes

### C1 — the panel keeps a hidden multi-file list it cannot show (F4, F5)

The panel shows exactly one file (the Canvas slice's `file`), so FileViewer in the
panel holds exactly one. One source: `CanvasTabState.file`.

- Panel FileViewer opens the new file by replacing the current one (no hidden
  tabs, no eviction). The modal keeps its tabs.
- The shown file's unsaved state lives in the slice: `CanvasTabState.fileDirty`,
  written from FileViewer's `onDirtyChange` (already exists).
- Switching away from a dirty file: useCanvasHost's `swarm:open-file` handler does
  not switch; it stores `pendingOpen` in the slice. The panel shows a small inline
  bar above the card: "Unsaved changes in X — Discard and open Y / Keep editing".
  Save stays where it is (⌘S / the floating Save), no third button.
- Auto opens (`auto: true`: auto-surface, default selection) never replace a dirty
  file and never create a pending prompt. Editing an auto-opened file clears its
  `auto` flag, so the hook treats it as the user's.
- A close request always ends in `canvas.close` after FileEditorCore's guard (one
  file, so "last tab" is always true).
- As built: `useFileViewerTabs.showOnly` / `clearAll` (single-file mode, used by
  the panel); the panel's cache keeps only the shown file's entry; the slice owns
  `fileDirty` + `pendingOpen`, the panel gets them as props (`canvas-pending-open`
  bar: "Keep editing" / "Discard and open <name>").
- Known limit, unchanged: switching CHAT tabs with unsaved edits drops them (the
  Canvas of another chat tab is a different slice; before, the edits sat in a
  hidden tab nobody could reach).

### C2 — reload claims were wider than the code (F1)

- Refresh comes back for every type in the panel: in the floating group for
  text / md / svg (the existing guarded `doReload`, unsaved edits prompt
  "Discard & Reload"), in the status bar for the rest (already there).
- The editor's existing visibility refetch also runs on `window` `focus` (same
  3 s throttle, same unsaved-edits skip). One handler, two events.
- As built: `reloadBtn` is one element rendered by both the modal header and the
  panel group.

### C3 — the panel's actions depend on props the Canvas never passes (F2)

- FileViewer gives FileEditorCore an attach handler in every case: the caller's
  `onAttachToChat` when given, else the same `swarm:attach-file` event the status
  bar already dispatches. One attach builder in FileViewer for all three (editor, status bar, unsupported renderer): `attachFile`.

### C4 — floating bar contrast and the selected row (F3, F13)

- `.editor-float-group`: `--color-card` fill mixed 6% toward `--color-text`, a 1px
  `--color-border-strong` (fallback `--color-border`) ring, shadow
  `0 4px 14px rgba(0,0,0,.28)`, `backdrop-filter: blur(6px)`; icons
  `--color-text-secondary`, active view chip `--color-primary` text on a 14% primary
  tint (matches the pin's active look). Mock first, then code (XG's rule for
  visual changes).
- F13: `.canvas-row-sel` (the one selected-row style, both lists) gets a primary
  tint fill — `color-mix(--color-primary 16%, --color-card)` — a 1px ring of
  `color-mix(--color-primary 40%, --color-border)`, the 3px primary bar, bold name.
  Hover stays the neutral `--color-hover`, so selected ≠ hovered. Chosen by
  XG from the mock: variant A (2026-10-23).

### C5 — rewrite and first-load cost (F6, F7, F8)

- First load (F6): render as soon as the file arrives. The committed baseline loads
  in parallel and is stored when it lands; until then Changes shows as loading
  (disabled). Target: content visible after the first request alone.
  As built: a file opened ON its diff (autoDiff) still waits, it has nothing to
  show without the baseline. FileEditorCore takes a late `committedContent` in its
  own effect (it only changes what Changes compares against; the reset effect no
  longer lists it, so a late baseline never resets the text, view or edits).
- Rewrites (F7): FileEditorCore coalesces `swarm:file-changed` for its file —
  throttled (the first event schedules a read 250 ms later, later events join it,
  so a file written continuously still refreshes every 250 ms), at most one GET in
  flight (an event during it schedules exactly one more), a sequence number so only
  the newest response is applied. The changed-lines overlay renders one node per
  changed line and one per run of unchanged lines before it (`changedLineSegments`),
  nothing after the last change. (Deviation: segments instead of positioned bars —
  a bar at (line − 1) × 24 px is wrong as soon as a line wraps; a joined text run
  wraps exactly like the textarea.) jsdom: 2,164-line file, 4 changed lines →
  104 ms / 2,164 nodes before, 1.2 ms / 7 nodes after.
- Markdown (F7, F8): the preview renders from `useDeferredValue(content)`, so a
  rewrite or a large document never blocks input, scrolling or the rest of the
  panel; it is not skipped or truncated.
- Targets (jsdom, asserted in tests): 5 file-changed events inside 250 ms → 1 GET;
  a stale response is never applied; overlay DOM nodes = changed lines, not total
  lines.

### C6 — small visual fixes (F9, F10, F11)

- F9: directory icons in the file tree and Working files sit in a 21 px slot.
- F10: the Artifacts `<ul>` shows a `focus-visible` ring (`--color-primary`, 1px).
- F11: Changes list `max-h` 140 px like Artifacts.

### F12

Checked by XG after rebuild. If WebKit drops textarea end padding, append the end
room as trailing empty lines in the highlight layer and use a spacer-based scroll
height instead of padding (no code now).

## 3. Acceptance tests (written first, each mutation-checked)

1. Panel FileViewer never holds more than one file across N opens (C1).
2. Open of another file while dirty → no switch, `pendingOpen` set, bar shown;
   Discard → switches; Keep editing → stays, edits intact (C1).
3. `auto` open while dirty → ignored; editing an auto file clears `auto` (C1).
4. Close request with several prior opens → `canvas.close` (C1, regression for F4).
5. Panel editor has Refresh and Attach without `onAttachToChat`; attach dispatches
   `swarm:attach-file` (C2, C3).
6. `window` focus triggers the guarded refetch; skipped while dirty (C2).
7. Content renders before the committed request resolves (C5).
8. 5 file-changed in 250 ms → 1 GET; out-of-order responses → newest wins (C5).
9. Overlay node count = changed-line count (C5).
10. Tree row: directory icon slot width = badge width (C6).

## 4. Order

1. C1 (+ tests 1–4)  2. C2 + C3 (5–6)  3. C5 (7–9)  4. C4 mock → XG → code
5. C6 (10). One commit each, own worktree, ff-merge to local main, no push.

## 5. Out of scope

Modal-variant behaviour; backend `/committed` speed (git show ~120 ms is only
taken off the first paint, not made faster); Changes tab diff loading.
