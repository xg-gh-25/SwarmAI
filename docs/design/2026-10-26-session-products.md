---
title: What a session produced reaches the user (any task, any location, any kind)
date: 2026-10-26
created: 2026-10-26
updated: 2026-10-26
status: v4 draft (rebuilt after the v3 skeptic review, verdict needs redesign); no code yet
---

# What a session produced reaches the user

## 0. Principle

SwarmAI runs every kind of task, most of them not coding. The user's attention belongs on
the agent's **products**: what it made or did that outlives the turn. Reading, searching,
scratch commands and thinking are process. Coding and pipeline runs are reference cases.

A product reaches the user in four places: **at the moment it happens** in the turn, on
the **output card** at the end, in the **Canvas rail** (files), and **by name** in the
answer (a click opens it). All four read **one record**.

## 1. What v3 got wrong (review 2026-10-26, each finding checked against code)

| v3 | Why it fails | v4 |
|---|---|---|
| A product row sits in its group, then is lifted out when the group folds | The phase body mounts only while open (`TurnZones.tsx:868`, `open && <PhaseBody>`): moving a row out of it remounts it, and drawing it outside from the start lets later calls grow above it | A product is a **first-level element that ends the current group** (§3.1), the rule v6 §2.3 already applies to questions, permission prompts and the pipeline divider. Nothing moves |
| "One record" but four sources (`turn_product`, `turn_deliverables`, `session_outputs`, resolve's cascade) | Three persisted copies with their own dedupe; "shown above" still shows a product twice | `turn_product` is the turn's record; the card is derived from it; `session_outputs` stays the Canvas rail's per-session view, written from the **same event** |
| Session worktree added to `_SWARM_AUTHOR_FACING` | Bypasses `needs_human_review`'s tree-relative dot-segment rule (`needs_human_review.py:392`): a tracked `.env` there would surface | A Canvas-only classifier for session worktrees that applies the same tree-relative dot-segment rule and `git check-ignore` (§3.3) |
| Remote product = `tool_risk` class `EXTERNAL` or `WRITE_LOCAL` | Approval risk is a different question; `WRITE_LOCAL` verbs (`draft/create/add/set/save`) do not prove a remote object was made; the orchestrator does not keep tool input to result time; approval prompts only on `EXTERNAL` (`security_hooks.py:1738`), so v3's "widens approval" was wrong too | An explicit **product capability table**, separate from approval; unknown tools and actions are left off (§2.2). Approval is not touched |
| URL = first URL in the persisted outcome | The outcome is one first/last line cut to 120 chars (`tool_summarizer.py:575-588`); the URL is usually gone, and a first arbitrary URL may be presigned | URL only from a named field of a registered tool's full result, host checked; else no link |
| Bash parser "like C041" | A second parser drifts from C041's chain splitting, wrappers and git global options | Extract C041's segment parser into one pure function both use |
| Several products match a name → open the newest | A guess | Several matches → no pick: a toast lists them; the user opens one from the rail |
| — | Pipeline run docs would show twice (StageResult already shows them) | StageResult is the in-turn row for run documents; they get no `turn_product` |

## 2. The record

### 2.1 `turn_product` block

One persisted block per product, inserted by the backend right after the successful
result of the call that made it (the `pipeline_stage` pattern: same bytes streamed and
stored, so live and reload agree).

```
{"type": "turn_product", "toolUseId", "parentToolUseId"?,
 "product": "file", "path", "absolutePath", "fileKind", "operation": "written" | "deleted"}
{"type": "turn_product", "toolUseId", "parentToolUseId"?,
 "product": "remote", "tool", "action", "target", "url"?}
```

Identity: `toolUseId + absolutePath + operation` (file), `toolUseId` (remote). One call
writing three files yields three blocks.

### 2.2 What is a product

- **File**: a `file_changed` the orchestrator already emits, with a product kind
  (`content`, `knowledge`, `external-diff`, `external-nodiff`; the router's existing
  `_DELIVERABLE_KINDS`). The same event feeds `session_outputs` (rail), so rail and turn
  cannot disagree. Code files in bound repos and run worktrees stay `source` and keep
  their existing home, the `turn_changes` card section and the landing row
  (`2026-10-25-turn-attention-tiers.md` step 3). Pipeline run documents: §1 last row.
- **Remote**: a call listed in a new table `core/turn_products.py :: REMOTE_PRODUCTS`,
  keyed by exact tool name, and for action-dispatch tools by exact `tool_input.action`
  value. Each entry names: the action label, which input field is the target, and which
  result field (if any) holds the URL plus its allowed hosts. Examples: Slack
  `post_message` (target `channel`), Outlook `email_send` / `email_draft` (target
  `to` / `subject`), Taskei `TaskeiCreateTask` (target `name`), SIM-T
  `TicketingWriteActions` with `action=create-ticket` (target `title`). At `tool_use` the
  orchestrator keeps only the redacted label and target for a listed call (not the raw
  input); on a non-error result it emits the block. A tool not in the table is not a
  product, and adding one is one table row with a test.
- **Remote via Bash**: `git push`, `gh pr create|merge`, `gh issue create`,
  `gh release create`, found by the parser shared with C041, with a non-error result.

### 2.3 Lifecycle

- Failed call: no product. Stop mid-call: no result, no product.
- Parallel calls: blocks follow the order their results are persisted, each after its own
  result row; a row never moves later.
- Sub-agent calls: the block carries `parentToolUseId` and is drawn in the parent's
  Agents panel position (§3.1). A background agent finishing in a later turn: its
  products belong to that later turn.
- A file written several times in a turn: one block per write call; the card lists it
  once.
- Steer-continued turns: the card is drawn only on the terminal segment (existing rule).
- History: old turns have no blocks; their card keeps reading `turn_deliverables`.

## 3. The four places

### 3.1 In the turn: a product ends the group

`turnZones.ts` turns consecutive `turn_product` blocks into one `products` zone at the
first level, exactly like v6 §2.3 treats a question or the pipeline divider: the current
WORKING group ends there, and calls after it start a new group below. Consequences:

- Nothing moves. The row is appended where the stream is, and the group above it folds by
  the existing rules (2 s, freeze while reading, the user's own choice).
- Running calls and thoughts are untouched; they are in a group, the row is after it.
- Phase and segment levels are unchanged; this adds one zone kind, not a level.
- A products zone shows one row per product: a file as the existing `DocCard` look (type
  badge, name, path, Open in Canvas); a remote product as `action · target`, the URL as a
  link when present. Several files from one call: one row `N files · a.md, b.md`,
  expanding to one row each (v6 bans `… N more`, not a counted merge).
- Attention tier `know`, tone neutral (no success colour).
- A sub-agent's product appears inside the Agents panel, under that agent's line, not as a
  first-level row (the panel is where the sub-agent's work is).

Spec v6 §2.3 gains "a product" in its list of first-level elements that end a group.

### 3.2 The output card

For a turn with `turn_product` blocks, the card's product list is derived from them: each
file once (by `absolutePath`), each remote product once. A product shown in the turn is
on the card by name only (no excerpt), so the card stays the one complete list for the
turn and the in-turn row is where it happened. `turn_changes` (code files) and the landing
section are unchanged. A turn whose only products are remote now has a card.
`turn_deliverables` keeps being written for old readers and is read only for turns
without `turn_product` blocks.

### 3.3 Canvas rail: session worktrees

New `canvas_surface` branch for paths under `<app data>/session-worktrees/<name>/<repo>/`:
take the tree-relative path, reject any dot segment (the `needs_human_review` rule, same
function), reject `git check-ignore` hits, otherwise `external-diff` with the existing
diff base. `canvas_noise` stays as it is except that this root is handed to that branch
instead of being dropped. `_owning_tree` / `auto_commit` are not widened.

### 3.4 By name

- `/workspace/file/resolve` takes an optional `session_id`. First stage: match the
  reference against that session's products (`session_outputs.read`, skipping deleted
  rows): an absolute path equal to a row's `absolutePath`, or a relative path that is a
  whole-segment tail of exactly one row. One match opens it. Several: `409` with the
  candidate paths; the frontend shows a toast naming them. None: today's cascade.
- `useCanvasHost` sends the tab's session id and stores it on the opened `CanvasFile`, so
  the following `GET /workspace/file` / preview reads carry it and the existing
  per-session allowlist applies. `_resolve_file_path` and every PUT path are unchanged.
- The inline file chip uses the markdown link look, title `Open in Canvas`.

## 4. Registration checklist for the new block

`context_injector._TOOL_ONLY_TYPES` (resume exclusion); orchestrator carry of listed
remote candidates; `types/index.ts` interface + `ContentBlock` union; the services
snake→camel mapper (`tool_use_id`, `parent_tool_use_id`, `absolute_path`); `turnView`
scan; stream `blockKey`; transcript-feed identity; live/reload signatures and keyed-block
preservation; `message_search_text` (products indexed by path and target, so recall finds
"the report I wrote"); fix `TurnDeliverable.kind`, which omits the external kinds.

## 5. Cost

All new work runs where the orchestrator already handles a finished call: one dict per
product, one table lookup per call at `tool_use`, the shared Bash parser per Bash call. No
I/O, nothing per token, nothing on send. Resolve: one small JSON read per click on the
`canvas_session` pool.

## 6. Acceptance tests (written first, red on today's code)

1. Session-worktree file → `external-diff`; a tracked `.env`, a `.git/` path and a
   gitignored file → declined.
2. Write of a Desktop `.md`, a Knowledge note and a session-worktree doc → one
   `turn_product` each, after its result; failed write → none; run docs → none.
3. Listed remote call with a non-error result → block with target and URL when the
   registered field holds one; error result, unlisted tool, `get_*` → none.
4. Shared Bash parser: same verdicts as C041's existing tests, plus `git push origin x`
   and `gh pr create` as products, `echo "git push"` and `gh pr view` not.
5. Persisted and identical on reload; stream-order and liveReload properties green.
6. Zones: a product ends the group; the group above folds after 2 s and the product row's
   DOM node is the same before and after; running rows unchanged; freeze while reading.
7. Card: each product once; a remote-only turn has a card; a turn without blocks reads
   `turn_deliverables` as today.
8. Resolve: one match opens it over a same-named SwarmWS or main file; several → 409; none
   → cascade; no `session_id` → today's result; an outside-home product readable only
   with its session id.
9. Chip: link classes, title, sends the tab's session id.

Each fix gets a mutation check.

## 7. Steps (each one commit, ff-merged to local main, not pushed)

1. Canvas classifier for session worktrees (tests 1).
2. Resolve + chip (tests 8-9).
3. `turn_product` record: files, `REMOTE_PRODUCTS`, shared Bash parser, registration
   (tests 2-5).
4. Products zone + derived card (tests 6-7); stop for Gawan's light/dark check.
5. Spec v6 §2.3 / §8 and the attention-tiers doc.

## 8. Decisions for Gawan

- Code files in repos stay on the card and the landing row only, not as in-turn product
  rows, so a coding turn is not a list of every edited file. Documents in a repo
  (`docs/design/*.md`) are `source` today and also get no in-turn row; only the session
  worktree case, as `external-diff`, does. Should repo documents get rows too? That needs
  a rule for which repo files are documents (by extension: `.md .docx .pptx .pdf .html`).
- `REMOTE_PRODUCTS` starts with the tools in daily use (Slack, Outlook, Taskei, SIM-T,
  GitHub via `gh`); anything else is left off until added.
