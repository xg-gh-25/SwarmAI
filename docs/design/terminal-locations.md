---
title: Terminal locations — SwarmWS, the chat's working repo, any folder
date: 2026-10-23
created: 2026-10-23
updated: 2026-10-25
project: SwarmAI
status: implemented (frontend + one read endpoint)
---

# Terminal locations

Agreed with XG in chat on 2026-10-23. Owner files:
`desktop/src/stores/terminalLocation.ts` (where a shell opens),
`desktop/src/stores/TerminalStore.ts` (the shells),
`desktop/src/components/terminal/TerminalPanel.tsx` (the switcher),
`desktop/src/components/terminal/TerminalTab.tsx` (xterm: theme, keys, selection menu),
`desktop/src/utils/terminalAttachment.ts` (terminal text as a chat attachment),
`desktop/src-tauri/src/pty_pump.rs` (PTY output batching),
`backend/core/session_changes.py::working_repo` + `GET /api/canvas/session/{id}/working-repo`.

## Problem

Every shell opened in `~/.swarm-ai/SwarmWS`; most terminal work happens in the repo the
chat is coding in, so the user typed a `cd` every time. The panel was a fixed dark slab
(not themed), its buttons were unicode glyphs outside the icon/button scale, "attach" sent
the whole screen, and an exited shell was just a grey dot.

## Model

A **location** is where a shell opens: `swarmws` (the Rust default, no cwd passed),
`repo` (this chat's working repo) or `custom` (a folder the user picked, or the explorer's
"Open terminal here"). Every shell carries the key of the location it was opened at
(`swarmws` or its absolute path); shells are grouped by that key.

**Working repo** — one fact, one source: the session's own transcript, the same
record Canvas Changes reads (core/session_changes, design canvas-changes-from-turns.md).
Newest "N files changed" card first, its landings then its files in order (a card that
only landed names its landing's repo, design 2026-10-25-landings-consumers.md D2): the
first `gitRoot` that is a bound repo (repo_changes.authorized_root, Projects/*/bindings.yaml) and a main checkout
(`.git` a directory). A card's gitRoot is already its object store's main checkout, so
a change made in a worktree names its main. A root outside the bindings (SwarmWS, a
scratch clone) is skipped; nothing found is none — no guess. No run.json is read
(changed 2026-10-24: code changes are native by default, so a session usually owns no
pipeline run and the run-based rule left the repo segment empty). It runs on the
`canvas_changes` pool from the same cached, rev-incremental window as Changes, never
on the chat path; git only through the cached repo identities. Label = the directory's basename.

**Selection** is per chat tab (`swarmws` | `repo` | `{custom, path}`), localStorage
`terminalLocationByChatTab` (newest 100 tabs). Without a selection: repo when the chat
has one, else SwarmWS. `repo` selected while the chat has none reads as SwarmWS. Recent
custom folders are global, `terminalRecentDirs`, newest 6.

When the working repo is read: chat tab switch, end of a turn (isStreaming true→false),
panel reveal. A read asked for while one is in flight for the same session runs once
more after it. Panel reveal always waits for a fresh read before it auto-opens, and
skips the auto-open if the chat in front changed meanwhile. Nothing is added to send
or the stream.

## UI

- Layout (XG 2026-10-23, "too cramped, can't tell it from chat and Canvas"): the panel
  is two layers, like Canvas — a recessed `--color-bg-chrome` gutter (10px around;
  its top 10px is the resize handle, a grip on hover) holding a raised
  `.terminal-card` (`--color-card`, 1px border, radius 10, soft shadow). The xterm
  surface uses the card colour, 12px text, line height 1.2, 14/8px inner padding.
  Output text is --color-text eased 80% toward the card (88% in light), bold is 500,
  and the dark ANSI set is GitHub's dark-dimmed palette (XG 2026-10-23: full-contrast
  text glared).
  Header 40px; segments and shell tabs 26px; the BottomBar is 30px with 24px-tall
  click targets.
- Header: a segmented switcher — `SwarmWS`, the repo (when
  there is one), the selected custom folder, and any other location that still has live
  shells (so no running shell is ever hidden) — each segment with a shell-count badge
  when it is not the selected one; then the folder button, which opens the native
  folder picker in ONE click, and — only when there are recent folders that are not
  already a segment — a chevron listing them.
- Clicking a segment selects it for this chat tab and shows that location's shells; if
  it has none, one is opened there. Switching chat tabs only shows: it never spawns.
- The tab strip lists only the selected location's shells, numbered within it.
- Icon buttons use Material Symbols + `icon-btn` / `icon-sm`: new shell, add to chat,
  hide panel.
- xterm theme follows the app theme (light / dark palettes); WebGL renderer
  (`@xterm/addon-webgl`, lazy chunk) with the DOM renderer as the fallback when WebGL
  is missing or its context is lost.
- BottomBar shows the selected location; clicking it reveals the panel at it.
- An exited shell shows a bar: `Process exited (code N) · ↵ Restart · Close`. An
  unmodified ↵ typed into the shell restarts it; ⌘↵ and ↵ on the bar's buttons do not.
- Keys: ⌘\` toggle panel (existing), ⌘⇧\` SwarmWS ↔ repo; in the terminal ⌘C copy
  (app clipboard command — navigator.clipboard is denied in the webview), ⌘A select
  all, ⌘K clear, ⌘↵ add the selection to chat. Ctrl-C / Ctrl-K reach the shell.

## Selecting text

Drag, double-click (word), triple-click (line) are xterm's. A program that turns on
mouse reporting (vim, less, htop, a TUI) takes every drag; ⌥-drag still selects
(`macOptionClickForcesSelection`). Right-click opens a menu: Copy, Add to chat (the
selection, else the last 200 lines), Select all, Clear.

## Add to chat

Terminal text becomes ONE terminal attachment chip in the chat input of the chat tab
in front (`utils/terminalAttachment`, stored by `useUnifiedAttachments` with the tab's
other attachments in tabMapRef). The chip shows the folder and line count, opens the
full text, and is removable; the chat input takes focus. The chip keeps its pieces,
each with its shell's cwd; a piece from a different folder than the one before it is
headed `# cwd: …`, and the chip's cwd is its first piece's. Its text is held to
16,000 characters — the backend's `buffer_tail` max_length (a longer value is a 422
that fails the whole message; a test checks the two agree): whole oldest pieces are
dropped first, then the oldest lines of the one left, and the chip says it was cut.
Appending to a queued message folds the terminal chips into one, and the terminal
chip always survives the 10-attachment limit (a file past it is dropped instead).
⌘↵ never reaches the shell: it adds the selection, or the last 200 lines.

At send the chip is not a content block: every send site computes
`terminalContextOf(<that message's attachments>)` into the request's
`terminalContext`, which the backend fences as UNTRUSTED data on the user turn. So
the terminal output belongs to its message: a queued message drains with its own, a
reconnect resend carries the same value, another tab never gets it. This replaced a
page-level one-shot ref that leaked a selection into another tab's send or a
background drain, was cleared before the resend read it, was invisible, and sent
over-long text that failed the message.

When the BACKEND parks a send as pending (all chat slots busy → QUEUE_TIMEOUT, or
SESSION_BUSY), `mark_pending_by_id` keeps its terminal_context in the row's metadata
while it is pending (no schema change; `mark_sent_batch` removes it once delivered).
The IDLE-edge drain passes `combine_terminal(claimed)` to the turn, the cold-spawn
coalesce prefixes the claimed rows' block (pending output first, the arriving turn's
after), and the persist-failed `retryPayload` carries `terminalContext` — rebuilt as
the re-queued message's chip, or sent again by the QUEUE_TIMEOUT Retry.

## PTY output

A macOS PTY read returns at most 1024 bytes, usually a few (measured, `seq 1 300000`:
125,056 reads of 2.3 MB, median 8 bytes; a 64 KB buffer instead of 4 KB changed
nothing). Each read used to be one `pty_read` IPC call + `spawn_blocking` +
`xterm.write`. Now a per-session pump thread (`src-tauri/src/pty_pump.rs`) reads
continuously into a buffer; `pty_read` waits for output, lingers ≤ 4 ms for more and
returns up to 256 KB. Same command: 56 calls (probe of the same algorithm on a real
PTY). The pump stops reading at 1 MB buffered until the frontend drains it, so a fast
producer is still back-pressured. A keystroke echo waits at most the 4 ms linger.

The parent drops its child-side (pts) handle once the child holds it, and macOS reports
the closed child side as EIO, which the pump maps to EOF (a real-PTY Rust test checks a
child's exit arrives as EOF after its output). `pty_kill` removes the session from
the registry and reaps the child off the async runtime, giving up after 3 s; before,
a closed terminal's session stayed in the registry for the app's lifetime (the
frontend never asks a killed PTY for its exit status).

## Deviations from the chat mock

- Tab labels are `zsh`, `zsh 2` within a location, not the running command: the shell
  title (OSC 0/2) is theme-dependent (`user@host: ~/path` when idle), and telling a
  prompt title from a command title would be a heuristic.
- The panel body keeps its own surface colour per theme rather than a raised card: it
  already sits between chat and the bottom bar with a full-width border.
- Revealing the panel waits for one working-repo read (a local HTTP call) before the
  first shell appears, instead of opening from the cached answer: the cache can name a
  repo a newer run has moved away from.

## Not done

Shell integration (OSC 133 per-command exit codes), mirroring agent Bash into the
terminal, split panes.
