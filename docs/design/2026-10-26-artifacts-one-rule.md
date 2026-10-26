---
title: Artifacts, one rule (Canvas and Left Nav)
date: 2026-10-26
created: 2026-10-26
updated: 2026-10-26
status: accepted (Gawan 2026-10-26 "三条都做"); implemented
---

# Artifacts, one rule

## 0. What each surface answers

| Surface | Question | Filter |
|---|---|---|
| Turn product rows, DELIVERABLES | What did this turn write? | none: the emit gate's kinds, as they happened |
| Canvas Artifacts | Of what this session wrote, what should the user look at? | the product rule |
| Left Nav Artifacts | Of everything in SwarmAI, what should the user look at? | the product rule |

The product rule is `ProductRegistry._classify` (role not `Other`, no garbage or
secret-looking names, dot folders and `Attachments/` excluded, a gitignored file only
under a product dir), applied only to files **inside SwarmWS**. One function, both
surfaces.

## 1. What was wrong

1. Canvas Artifacts listed every `session_outputs` row. `canvas_session` already asked
   `ProductRegistry.lookup` and got `product`, but only for display. A session's repo
   code (session worktrees, external repos) and `.context/.sweep-backups/...` showed.
2. `ProductRegistry._resolve` used `_owning_tree`, which also returns bound-repo and run
   worktrees, so a repo file named like a product (`MEMORY.md`, a `Designs/` dir) could
   be judged a product. Left Nav's store holds only SwarmWS rows (watcher + backfill
   cover SwarmWS only), so the rule and the data disagreed.
3. A gitignored product a session wrote (`Projects/X/assets/deck.html`) never left the
   emit gate: `needs_human_review` says `process` (ignored), `is_canvas_surfaceable`
   declines inside-tree paths. The turn did not show it, Canvas did not, Left Nav did.

## 2. Changes

1. `canvas_session._artifacts` keeps rows with `product` true. Recorded rows are not
   rewritten; old sessions read clean at once. `artifact_rows` still joins every row
   (its callers and the landed-path mapping are unchanged). Rows past MAX_FILES are left
   off before the filter, as before. No workspace known: no row is a product.
2. `ProductRegistry._resolve` returns None unless the owning tree is SwarmWS
   (`repo is None`). Register, lookup and backfill all go through it.
3. New `ProductRegistry.is_ignored_product(abs_path)`: the rule with `ignored=True`
   (pure: no git call; only a product-dir file passes). The write and delete gates in
   `streaming_orchestrator` ask it when `needs_human_review` declines a SwarmWS path; a
   yes emits `kind: content`, so the turn rows, DELIVERABLES and `session_outputs` see
   it, and a delete marks its row.

## 3. Cost

Change 3 runs only for paths `needs_human_review` declined, on the `io` pool like the
verdict it follows: one `products.json`-free classification (path rules only). Nothing
on send or per token.
