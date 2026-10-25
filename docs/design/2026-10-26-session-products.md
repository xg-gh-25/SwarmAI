---
title: What a session produced reaches the user (any task, any location)
date: 2026-10-26
created: 2026-10-26
updated: 2026-10-26
status: v2 draft (replaces v1 "files written in a session worktree"), not reviewed; no code yet
---

# What a session produced reaches the user

## 0. Principle

SwarmAI runs every kind of task, most of them not coding: a report, a deck, a note in
Knowledge, a doc on the Desktop, a design doc in a repo. The question this design answers
is the same for all of them: **of what the agent produced, what must reach the user's
attention, and does every product reach it the same way?** Coding and pipeline runs are
reference cases, not the frame.

A product reaches the user through three surfaces, and all three must agree:

| Surface | Today's source |
|---|---|
| the Canvas Artifacts rail | `session_outputs` (each `file_changed` of a rail kind) |
| the turn's output card | `turn_deliverables` (same `file_changed` kinds) + `turn_changes` (code trees) |
| a file named in the answer, clicked | `/workspace/file/resolve` (a fixed path cascade) |

The first two already share one decision: the `kind` the emit gate gives each written
file (`needs_human_review`, then `canvas_surface` for files outside known trees, with
`canvas_noise` dropping machine noise). Content, knowledge, external repo files and plain
files on the Desktop all pass it, so a non-coding product already reaches the rail and
the card. The click is the odd one out: it never asks what the session produced.

## 1. What went wrong (2026-10-25, session `77c3d2c0`)

The session wrote `docs/design/2026-10-25-pipeline-handout-by-file.md` in its own session
worktree (`~/.swarm-ai/session-worktrees/20261025-234616-de3c/swarmai/`).

- **Rail and card both missed it.** `canvas_noise` treats everything under `~/.swarm-ai`
  as app plumbing except an author-facing list (`SwarmWS`, the skill dirs). Session
  worktrees (added 2026-10-24) were never added, so the doc was classified as noise: no
  `file_changed` of a product kind, no rail row, no card row. Checked: `is_noise_path`
  returns True for it, and the session's Canvas record holds only two run documents.
- **The click failed.** The answer named the doc by a relative path. Resolve looked in
  SwarmWS, `Projects/*/` and the bound checkouts, found nothing, and showed "File not
  found". Same failure for any product outside those: a session that writes
  `~/Desktop/Q3/summary.md` and says `` `Q3/summary.md` `` cannot open it either.
- **The reference does not look clickable.** The chip is a button styled as code.

v1 of this design fixed only the worktree case with a worktree-specific owner lookup and a
second code-tree source. That is a patch: it would not help the Desktop report.

## 2. Changes

### 2.1 Classification: a session worktree is author-facing (`core/canvas_noise.py`)

Add `.swarm-ai/session-worktrees` to `_SWARM_AUTHOR_FACING`. Its files then go through
`canvas_surface` like any file in a repo the app does not bind: not gitignored →
`external-diff` with a diff base, gitignored → declined (the secret rule is unchanged).
That one kind feeds both the rail and the card's deliverables; nothing else changes.
`needs_human_review` and `auto_commit` are not touched (a session worktree must not
become commit material for the workspace).

### 2.2 Opening a reference: the session's own products first (`/workspace/file/resolve`)

The endpoint takes an optional `session_id`. A new first stage reads that session's
products (`session_outputs.read`, the record the rail already uses, on its
`canvas_session` pool) and matches the reference against them:

- an absolute path: equal to a row's `absolutePath`;
- a relative path: a row whose `absolutePath` ends with `/<path>` (whole segments).

Exactly one match: open it. No match: today's cascade, unchanged. More than one match
(two `summary.md` in different folders): open the one with the newest `lastAt`, since an
answer names what was just written. This is the one place a choice is made between
records; review it (§6).

Generic: it covers a Desktop report, a Knowledge note, a deck, and a worktree doc alike,
and a reference in an old message resolves by the same rule when clicked. When a session
worktree is removed after `land`, its row no longer resolves to a file and the cascade
finds main's copy. `useCanvasHost` sends the tab's session id with the call. The hot-path
caller in `streaming_orchestrator` passes no session id and is unchanged.

### 2.3 A reference reads as a link (`MarkdownRenderer.InlineCode`)

The file chip uses the existing markdown link look (`text-primary`, underline with
`decoration-primary/50`, as the `a` renderer), keeps the type badge and monospace, title
`Open in Canvas`. Applies to every inline file reference. The attention tiers override
only `--color-text`, so links keep their colour in process text too.

## 3. Cost

- 2.1: one more prefix in a pure path check, on the emit gate that already runs per
  written file. Nothing new per token or per send.
- 2.2: one read of a small JSON file (at most 300 rows) per click, in the existing
  `canvas_session` pool. Nothing on send or the stream.
- 2.3: CSS classes only.

## 4. Not in scope (named, so they are not mistaken for done)

- **Products that are not files**: an email sent, a ticket filed, a Slack post, a PR
  opened. They are products the user must know about too, and today they appear only as
  a tool strip. `external_approval_gate` already recognises these off-machine actions;
  surfacing them as know-tier rows is its own design.
- **The mid-turn document card** (mockup 2026-10-25). It reads the same `file_changed`
  products as the card, so after 2.1 it has its data for every task type; it is the next
  design, not this one.
- **Code changes in a session worktree as `N files changed`** with per-file +/- from
  `turn_changes`. After 2.1 the file is on the card as a deliverable with a diff; making
  session worktrees code trees for `turn_changes` is not needed for either bug.
- **History**: no recompute, no migration.

## 5. Acceptance tests (written first, red on today's code)

1. `is_noise_path` is False for a file under `~/.swarm-ai/session-worktrees/...` and still
   True for `~/.swarm-ai/logs/x`, `~/.swarm-ai/data.db`, `~/.swarm-ai/worktrees/...` is
   unaffected (owned by `needs_human_review`).
2. Emit gate: a file written in a session worktree (temp git repo under a temp
   `.swarm-ai`) gets `external-diff`; a gitignored one is declined.
3. That file becomes a `turn_deliverables` row and a `session_outputs` row.
4. Resolve with `session_id`: a relative reference matching one product opens it, even
   when a SwarmWS or main-checkout file has the same relative path; a Desktop product
   (`external-nodiff`) opens by its relative tail; two matches open the newest `lastAt`;
   no match falls through; without `session_id` the result is exactly today's; `..` and
   null bytes are rejected as today.
5. A click on an inline file chip sends the tab's session id.
6. The chip has the link classes and `title="Open in Canvas"`; a non-path code span not.

Each fix gets a mutation check (revert it, its test goes red).

## 6. Open points for review

- The newest-`lastAt` rule for several matches (§2.2): is there a case where the user
  meant the older one, and should two matches instead fall through to the cascade?
- Whether any existing caller relies on session-worktree files being noise.

## 7. Regression guard and steps

Suites that must stay green: `test_canvas_noise*`, `test_canvas_surface*`, the emit-gate
and `session_outputs` tests, the resolve tests, `MarkdownRenderer` tests, `src/pages/chat`.
Any test whose rule changes is listed in its commit message.

Steps, each one commit ff-merged to local main, not pushed: (1) 2.1 + tests 1-3;
(2) 2.2 + tests 4-5; (3) 2.3 + test 6.
