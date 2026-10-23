---
title: Terminal locations — SwarmWS, the chat's working repo, any folder
date: 2026-10-23
created: 2026-10-23
updated: 2026-10-23
project: SwarmAI
status: implemented (frontend + one read endpoint)
---

# Terminal locations

Agreed with XG in chat on 2026-10-23. Owner files:
`desktop/src/stores/terminalLocation.ts` (where a shell opens),
`desktop/src/stores/TerminalStore.ts` (the shells),
`desktop/src/components/terminal/TerminalPanel.tsx` (the switcher),
`backend/core/canvas_session.py::working_repo` + `GET /api/canvas/session/{id}/working-repo`.

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

**Working repo** — one fact, one source: the newest pipeline run this session owns
(`canvas_session.session_runs`, run.json `session_id` / `session_history`, the same
source Canvas Changes reads) that names a repo; exactly its first named repo (first
`worktrees[].main`, else first commit's repo). Accepted only when that path is a real
directory whose `.git` is a directory — a main checkout, never a run worktree (a linked
worktree's `.git` is a file). Otherwise none: no later repo, no older run, no guess.
Cost (measured on the live workspace, 753 run.json): 329 ms on the first read after a
daemon start, 4 ms after (the run index's mtime cache); no git. It runs on the
`canvas_changes` pool, never on the chat path. Label = the directory's basename.

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

- Header (32px, `--color-bg-chrome`): a segmented switcher — `SwarmWS`, the repo (when
  there is one), the selected custom folder, and any other location that still has live
  shells (so no running shell is ever hidden) — each segment with a shell-count badge
  when it is not the selected one; `⋯` = recent folders + Browse… (directory dialog).
- Clicking a segment selects it for this chat tab and shows that location's shells; if
  it has none, one is opened there. Switching chat tabs only shows: it never spawns.
- The tab strip lists only the selected location's shells, numbered within it.
- Icon buttons use Material Symbols + `icon-btn` / `icon-sm`: new shell, send to chat,
  hide panel.
- xterm theme follows the app theme (light / dark palettes).
- Selecting text shows a "Send to chat ⌘↵" pill; the header send button sends the
  selection when there is one, else the last 200 lines. The cwd rides along.
- An exited shell shows a bar: `Process exited (code N) · ↵ Restart · Close`. An
  unmodified ↵ typed into the shell restarts it; ⌘↵ and ↵ on the bar's buttons do not.
- BottomBar shows the selected location; clicking it reveals the panel at it.
- Keys: ⌘\` toggle panel (existing), ⌘⇧\` SwarmWS ↔ repo, ⌘K clears the terminal.

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
