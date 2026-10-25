---
title: What a session produced reaches the user (any task, any location, any kind)
date: 2026-10-26
created: 2026-10-26
updated: 2026-10-26
status: v3 draft (v1 worktree-only, v2 files-only; v3 adds remote products and the mid-turn row), not reviewed; no code yet
---

# What a session produced reaches the user

## 0. Principle

SwarmAI runs every kind of task, most of them not coding. Of everything the agent does,
the user's attention belongs on its **products**: what it made or changed that outlives
the turn. Everything else (reading, searching, scratch commands, thinking) is process.
Coding and pipeline runs are reference cases, not the frame.

Two kinds of product, one treatment:

| Kind | Examples | Fact it rests on |
|---|---|---|
| **file** | report, deck, Knowledge note, Desktop doc, design doc in a repo or a session worktree | the emit gate's `kind` for a written path (`needs_human_review` / `canvas_surface` / `canvas_noise`) |
| **remote** | email sent or drafted, Slack post, ticket / task / CRM record created or updated, PR opened, branch pushed | the tool's class in the one risk taxonomy (`core/tool_risk`) plus its result: the call succeeded |

Each product must reach the user in all four places, and all four read the same record:

1. **at the moment it happens**, as a row in the turn (know tier, not folded);
2. **on the turn's output card**, listed once;
3. **in the Canvas rail** (files);
4. **when the answer names it**, the name opens it.

## 1. Today, per kind (checked against code 2026-10-26)

| | in the turn | output card | Canvas rail | name opens it |
|---|---|---|---|---|
| file in SwarmWS / Desktop / external repo | only a tool strip, folds away | yes (`turn_deliverables`) | yes | only if under SwarmWS / `Projects/*` / a bound checkout |
| file in a session worktree | strip only | **no** | **no** | **no** |
| remote product | strip only | **no** | n/a | n/a |

Root causes:
- `canvas_noise` treats `~/.swarm-ai` as app plumbing except a list (`SwarmWS`, skill
  dirs); session worktrees (added 2026-10-24) were never added. Checked: `is_noise_path`
  is True for the observed doc, and that session's Canvas record holds only two run docs.
- Nothing records a remote product. `tool_risk.classify` decides the class but only the
  approval gate reads it.
- `/workspace/file/resolve` searches fixed folders and never asks what the session wrote.
- Nothing puts a product into the turn where it happened; the card is the only place.

## 2. The record: one `turn_product` block per product

Same pattern as `pipeline_stage` (a persisted block the backend inserts after the call
that caused it, streamed and stored identically, so live and reload show the same):

```
{"type": "turn_product", "toolUseId": "<the call>", "product": "file" | "remote",
 // file
 "path", "absolutePath", "fileKind": "<emit-gate kind>", "operation": "written" | "deleted",
 // remote
 "tool": "<tool name>", "action": "<verb token>", "target": "<strip object>", "url"?: "<http(s) URL from the result>"}
```

Who writes it (`streaming_orchestrator`, at the call's successful `tool_result`, where
`_pending_file_changes` is already keyed by `tool_use_id`):

- **file**: each `file_changed` it emits of a product kind (the existing
  `_DELIVERABLE_KINDS`) also yields a `turn_product` for the same call. One fact, two
  views: `file_changed` keeps feeding Canvas and `turn_deliverables`.
- **remote**: an MCP tool (`mcp__*`) that `tool_risk.classify` puts in `EXTERNAL` or
  `WRITE_LOCAL` (an MCP "local" write such as `email_draft` or `create_task` still writes
  to the remote service; the class only says it needs no approval), with a non-error
  result. Built-in tools are never remote products (Write/Edit are files, Agent/Skill/
  Todo are in-process).
- **remote, Bash**: a new pure function in `tool_risk`, `remote_action(command)`, using
  the same `shlex` tokenizing as the C041 check: `git push`, `gh pr create|merge|close`,
  `gh issue create|close`, `gh release create`, with a zero exit. Any other Bash is not a
  product (a `curl -X POST` is not recognised; §4).
- `target` reuses `tool_summarizer`'s object for that call (the strip's object column),
  not a new parser. `url`: the first `https://` URL in the persisted outcome, if any; it
  is text in the record, so nothing is inferred. None: the row shows no link.

A failed call yields no product (the strip already records it).

## 3. The four places

### 3.1 In the turn (`turnZones.ts` + a `ProductRow`)

A `turn_product` block becomes a `product` item at its position. When the segment or
phase that holds it folds, product items are lifted out and drawn after the fold's header,
in order, so they stay visible: the folded group says how it worked, the rows say what it
made. A file row is the existing `DocCard` look (badge, name, path, Open); a remote row
is `tool · action · target` with the URL as a link when present. Tier `know`, tone
neutral (§3 of `2026-10-25-turn-attention-tiers.md`; no success colour, a product is not
proof of success).

Invariants kept: rows arrive in stream order after their call, so the turn only grows
downward; running calls and thoughts are untouched; the 2 s fold delay and the freeze
while reading still decide folds; phase and segment levels are unchanged (a product row
is a sibling of the fold header, not a new level).

### 3.2 The output card

Lists the turn's products once: files from `turn_deliverables` (unchanged), remote
products from the turn's `turn_product` blocks. A product already shown in the turn is
listed by name with "shown above" and no excerpt, keyed by `absolutePath` (files) or
`toolUseId` (remote). The card no longer disappears when a turn's products are only
remote.

### 3.3 Canvas rail

Fixed by the classification change: add `.swarm-ai/session-worktrees` to
`canvas_noise._SWARM_AUTHOR_FACING`. Its files then pass `canvas_surface` like any repo
the app does not bind (`external-diff` with a diff base; gitignored → declined). That
same kind feeds `turn_deliverables`, `session_outputs` and the new `turn_product`.
`needs_human_review` / `auto_commit` are not touched.

### 3.4 The name opens it (`/workspace/file/resolve` + `MarkdownRenderer`)

- Resolve takes an optional `session_id`. A new first stage matches the reference against
  that session's products (`session_outputs.read`, the rail's record, on its
  `canvas_session` pool): absolute path equal to a row's `absolutePath`, or a relative
  path that is a whole-segment tail of one. One match opens it; none falls through to
  today's cascade unchanged; several open the newest `lastAt` (review point, §6).
  `useCanvasHost` sends the tab's session id. The streaming hot-path caller passes none.
- The inline file chip uses the markdown link look (`text-primary`, underline), keeps the
  type badge, title `Open in Canvas`.
- A remote product is opened by its own `url` link in the row and on the card.

## 4. Known limits (decided, not hidden)

- **Unrecognised off-machine Bash** (`curl -X POST`, a vendor CLI) is not a product: the
  command would have to be guessed at. The agent names such results in its answer.
- **MCP tools with no verb** in the taxonomy fall to `READ` (tool_risk's fail-open rule)
  and are not products. Checked 2026-10-26: `mcp__builder-mcp__TaskeiCreateTask` and
  `mcp__builder-mcp__TicketingWriteActions` both classify `READ`, because `_mcp_tokens`
  splits only on `_` (not CamelCase) and an action-dispatch tool carries its verb in the
  input's `action`. So creating a Taskei task or a SIM ticket would be missed today, and
  is also not approval-gated. Fix in the one taxonomy, as its own step (§8 step 3a):
  split CamelCase tokens, and when the call's `tool_input` has a string `action`, add its
  tokens (`create-ticket` → `create`, `ticket`). That changes approval prompts too, so it
  gets its own tests and is listed for review (§6).
- **The landed commit row** stays as designed in `2026-10-25-turn-attention-tiers.md`
  step 3 (`turn_changes.landed` + `tool=` in the stamp). It could become a `turn_product`
  later; not merged here so that design is not reopened.
- **History**: old turns have no `turn_product` blocks and are not recomputed; their card
  and rail are as before.

## 5. Cost

- File products: one extra dict per product `file_changed`, already on the orchestrator's
  path after the result. Remote: one `classify()` (pure, name tokens) per finished non-Bash
  call, one `remote_action()` (shlex) per finished Bash call. No I/O, no pool, nothing per
  token.
- Resolve: one small JSON read (≤300 rows) per click, on the `canvas_session` pool.
- Frontend: product items are a filter over blocks the model already walks.

## 6. Open points for review

- Several products matching one relative name: newest `lastAt`, or fall through?
- Lifting product rows out of a folded group: does any existing zone key or the
  stream-order / liveReload property depend on the fold body holding every block?
- MCP `WRITE_LOCAL` counted as remote: any MCP tool in use whose local-write verb really
  is local-only (and would then show a misleading row)?
- Step 3a widens which calls ask for approval (a `create-ticket` action would then be
  `WRITE_LOCAL`, a `send` action `EXTERNAL`). Is that wanted, or should products read the
  new tokens while approval keeps the old ones?

## 7. Acceptance tests (written first, red on today's code)

Backend:
1. `is_noise_path` False under `~/.swarm-ai/session-worktrees/`, still True for
   `~/.swarm-ai/logs`, `data.db`.
2. A Write in a session worktree yields `file_changed(kind=external-diff)`, a
   `turn_deliverables` row, a `session_outputs` row and a `turn_product(file)` with the
   call's `toolUseId`; gitignored → none.
3. A successful `mcp__slack-mcp__post_message` / `mcp__aws-outlook-mcp__email_draft`
   yields `turn_product(remote)` with target and URL when the outcome has one; an error
   result, a `get_*`, `Read`, `Agent` yield none.
4. `remote_action`: `git push origin feat`, `gh pr create ...` yes; `git status`,
   `echo "git push"`, `gh pr view` no; a failed exit no.
5. The block is persisted on the row and identical on reload.
6. Resolve with `session_id`: a relative tail of one product opens it even when SwarmWS
   or main has the same relative path; a Desktop product opens; no match falls through;
   without `session_id` exactly today's.

Frontend:
7. A product item appears after its call; when its group folds it stays visible after the
   header, in order; running rows and thoughts unchanged; stream-order and liveReload
   properties green.
8. The card lists each product once, "shown above" for in-turn ones; a turn whose only
   product is remote still shows the card.
9. Inline file chip: link classes, `title="Open in Canvas"`, sends the tab's session id.

Each fix gets a mutation check (revert it, its test goes red).

## 8. Steps (each one commit, ff-merged to local main, not pushed)

1. Classification (`canvas_noise`) + tests 1-2 (rail and card fixed for worktree files).
2. Resolve + link look + tests 6, 9.
3. `turn_product` block (file + remote, `remote_action`) + tests 2-5.
3a. `tool_risk` tokens: CamelCase + `tool_input.action` + tests (product and approval).
4. In-turn rows and card listing + tests 7-8; stop for Gawan's light/dark check.
5. Update spec v6 (§2.3 product rows, §8 card) and the attention-tiers doc.
