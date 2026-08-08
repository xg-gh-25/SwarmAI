# Canvas Surfacing — the authoritative E2E flow (READ THIS BEFORE TOUCHING CANVAS)

> **Why this doc exists.** Canvas auto-surfacing is a recurring regression site — the
> OT01 reconcile-race family, the `relevance`-gate kill (run_1e39d21e), the dual
> workspace-resolver near-miss, the close-first-then-dispatch z-index trap. Every time,
> the cost was *re-discovering the whole chain* from scratch. This is the map: the
> legs, the contracts between them, and the traps that have actually bitten. Trace the
> chain here first; then `grep` the symbol to find the current line.
>
> **No line numbers by design (AGENT R30#4).** Line numbers drift and then mislead —
> the exact failure class this subsystem keeps hitting (a stale comment/gate outliving
> the code). This doc names **symbols + files + data flow**; you locate the line by
> grepping the symbol. If a symbol name here ever stops matching code, THAT is the bug
> to fix (update this doc in the same change — touch-it-fix-it).

---

## 0. The one-paragraph mental model

A file reaches Canvas through **one pipe with three entry points and a two-gate
admission**. The backend decides *whether a change is worth a human's eyes* (`kind`
via `needs_human_review`) and emits a `file_changed` SSE event. The frontend turns
that SSE event into a `swarm:file-changed` window event, which feeds **two independent
consumers**: the **rail** (always lists review-worthy files) and **auto-pop** (opens
the newest one in Canvas, gently). Opening = dispatch `swarm:open-file` →
`useCanvasHost` resolves the path against the workspace and sets the active file, which
auto-opens the panel. That's the whole thing. Every trap below is a contract mismatch
between two adjacent legs.

---

## 1. The pipe, end to end (the happy path)

```
 BACKEND (daemon, per streaming turn)
 ─────────────────────────────────────
 (a) agent runs a tool that writes a file
      Write/Edit/NotebookEdit → block.input.file_path
      Bash >/tee/cp/mv-DEST   → parse_bash_write_targets
   → streaming_orchestrator records it in _pending_file_changes[tool_use_id]
 (b) matching ToolResult(success) arrives
   → _build_file_write_events(paths):
       · resolve_path_to_physical(raw)         (off-loop, asyncio.to_thread)
       · verdict = needs_human_review(abs, "written")
           - not review_worthy → DROP (machine noise / gitignored)
           - else → kind = verdict.kind  (content|knowledge|source|source-final|process)
       · emit  {type:"file_changed", path, absolutePath, kind, operation:"written"}
         ⚠️  NOTE: write-emit sends `kind` + `operation`, **NO `relevance`**.
   → orchestrator `yield`s the event into the SSE stream
      (mid-stream, on the WRITING session's own stream → correct tab stamp)

 SSE  ── {type:"file_changed", ...} ──▶  FRONTEND

 FRONTEND
 ────────
 (c) useChatStreamingLifecycle: on event.type === "file_changed"
       re-dispatch a WINDOW event `swarm:file-changed` with detail:
         { path, absolutePath,
           relevance: e.relevance ?? "incidental",   ← FAIL-CLOSED default (see Trap 2)
           kind:      e.kind ?? undefined,
           baseRef, operation, tabId: capturedTabId }  ← origin-tab stamp
 (d) TWO independent consumers listen on `swarm:file-changed`:
       ┌─ useReferencedFiles  → the RAIL (list every review-worthy file)
       └─ useCanvasAutoSurface → AUTO-POP (open the newest, gently)
 (e) auto-pop (after gates + debounce) dispatches `swarm:open-file` (= OPEN_FILE_EVENT,
     'swarm:open-file', DOCUMENT target) with { path, tabId }
 (f) useCanvasHost.handleOpenFile (document listener):
       · GET /workspace/file/resolve?path=…  → resolved workspace path
       · set the active file on the origin tab's slice → panel auto-opens
         (isOpen = !!(slice.file || slice.manuallyOpen))
```

**The 3 surfacing entry points (all converge on `file_changed` → `swarm:file-changed`):**
1. **Mid-run write** — `_build_file_write_events` at each ToolResult(success). Live, per-file.
2. **Pipeline-finish batch** — the agent calls the `surface_run_outputs` SDK tool at
   COMPLETE; the orchestrator observes the tool_use BY NAME and emits N `file_changed`
   events via `build_surface_events` (one `source-final` row per committed file + the
   run's `REPORT.md` as `knowledge`). A CLI subprocess **cannot** emit — this rides the
   live turn. (See the sibling doc on the surface-vs-run-report ordering fix, run_f1fbf37d.)
3. **Manual / agent-directed open** — a chat file-chip click, or the agent's
   `open-canvas-file` `ui_action`, dispatches `swarm:open-file` directly (skips the
   gates — it's an explicit "open this" intent, not auto-surfacing).

---

## 2. The admission gates (this is where the bugs live)

Two consumers, each with its own gate logic. **They are NOT the same gate** — a bug in
one does not imply a bug in the other (verify each independently — Trap 4).

### `kind` — the SOLE authority (backend `needs_human_review._classify_kind`)
Precedence (first match wins):
1. any **dot-segment** in the tree-relative path (`.artifacts`, `.context`, `.git`, hidden) → `process`
2. **knowledge doc** — `MEMORY/EVOLUTION/KNOWLEDGE/PROJECTS.md` under `.context`, or a
   `REPORT.md` under `.artifacts/runs/`, or a `.md` in the SwarmWS knowledge store → `knowledge`
3. inside a **bound-repo worktree** → `source`
4. else → `content`  ← the "real session output" bucket (HTML, notes, reports)

`source-final` is minted ONLY by the finish-batch (`build_surface_events`), never by
`_classify_kind`. `review_worthy=False` (gitignored / machine / git-error fail-closed)
→ never emitted at all.

### Auto-pop gate (`useCanvasAutoSurface`, the `onWritten` handler) — ordered:
1. `operation !== 'written'` → ignore (reads/searches/deletes don't pop)
2. **kind gate (PRIMARY):** pop only `content | knowledge | source-final`. `source`
   (mid-run edit) + `process` (noise) never pop. `undefined` kind → fall through.
3. **relevance gate (LEGACY FALLBACK — `kind === undefined` ONLY):** an old backend
   that sent `relevance` but no `kind` still needs `relevance === 'deliverable'` to pop.
   **MUST be gated on `kind === undefined`** (run_1e39d21e — see Trap 1). When `kind` is
   present the kind gate is the sole authority; this clause must not run.
4. streaming gate — suppress a write that arrives while NOT streaming (restart-history replay)
5. tab-scope — ignore a write stamped with a foreign (background) tab
6. gentle suppression (at debounce fire) — pin / mute / user-is-viewing-their-own-file
7. debounce (coalesce a burst → last path wins) → dispatch `swarm:open-file`

### Rail gate (`useReferencedFiles`) — DIFFERENT, simpler:
- reject `relevance === 'bookkeeping'` (defensive; backend drops these server-side)
- reject `kind === 'process' || kind === 'source'`
- everything else (incl. `content|knowledge|source-final` AND `undefined` kind) → **lists in the rail**
- **The rail does NOT gate on `relevance !== 'deliverable'`** — which is why the
  run_1e39d21e regression broke auto-POP but NOT the rail (files still listed).

---

## 3. The traps that have actually bitten (each = a contract mismatch)

**Trap 1 — a new authoritative gate stacked ON TOP of a legacy one, unconditionally ANDed (run_1e39d21e, THE regression this doc was born from).**
`kind` became the authority, but the older `relevance !== 'deliverable'` gate was left
running unconditionally below it. Since write-emit sends no `relevance`, the SSE bridge
fail-closes it to `'incidental'`, so the legacy gate killed **every** kind-whitelisted
write → session HTML/DDD/Knowledge/context stopped auto-opening; only the finish-batch
(which hard-codes `relevance:'deliverable'`) survived. **Rule: when you add an
authoritative check above a legacy one, SCOPE the legacy one to its fallback condition
(`kind === undefined`) — never leave it unconditional.** git-blame localizes these:
the gates were added by different commits weeks apart.

**Trap 2 — a fail-closed default is locally correct but a silent killer downstream.**
The SSE bridge defaulting `relevance → 'incidental'` is *right* in isolation (don't
auto-pop an unclassified write). It became lethal only because a downstream gate treated
that default as a reject. **Rule: trace every default to its consumers — a
locally-reasonable default + a locally-reasonable gate can multiply into a bug neither
leg owns alone.**

**Trap 3 — write-emit and delete-emit are ASYMMETRIC on `relevance`.**
`_build_file_delete_events` emits `relevance:"incidental"` + `kind:"content"`; the
write path emits **`kind` only, no `relevance`**. This asymmetry is the seed of Trap 1.
If you ever "unify" them, do it toward `kind`-as-sole-authority (the deliberate
collapse — backend does NOT gate on relevance), NOT by resurrecting a relevance value
on the write path (that re-creates the two-authority system).

**Trap 4 — don't assume the two consumers break together.** During the run_1e39d21e
fix I *assumed* the rail was also broken; reading its actual gates proved only auto-pop
broke. **Rule: verify each claimed-affected surface against ITS OWN gate; symmetry is a
hypothesis, not a fact.**

**Trap 5 — close() BEFORE dispatch when opening Canvas from an overlay (z-index).**
An overlay that opens a file must call the host `close()` FIRST, THEN
`document.dispatchEvent('swarm:open-file', {path})` — else Canvas renders *under* the
overlay host. The `swarm:open-file` listener lives on `document` (outside the overlay's
React subtree), so the close-scheduled-unmount can't detach it. Precedent: the swarmws
overlay + the Pipeline overlay's `openReportInCanvas`. Do NOT copy the chat-inject
double-`requestAnimationFrame` here — that delay is only for waiting on a tab to
activate; open-file is a synchronous document event.

**Trap 6 — path form for the resolver.** `swarm:open-file` carries a `path` that
`/workspace/file/resolve` resolves. Use a **workspace-relative** path
(`Projects/<p>/.artifacts/runs/<id>/REPORT.md`) — it hits resolve Stage 1 directly.
The resolver has **no `.artifacts`/directory denylist** (only an `_is_path_under`
workspace-containment check), so `.artifacts`-nested files open fine. Never hand it an
absolute host path from the agent efferent channel (infoleak — that's why raw
`open-file` is dropped from the `ui_action` allowlist; only `open-canvas-file`, which
rides the workspace-scoped resolver, is allowed).

**Trap 7 — the render-source split-brain (OT01 family, still #1 recurring debt).**
Separate from surfacing: once a file is IN Canvas, the *message* render path
(TabView's three-source more-complete-wins reconcile) is a different, unresolved
regression zone. Don't conflate "why didn't Canvas open" (this doc) with "why did the
rendered content truncate/duplicate" (OT01, see MEMORY §Open Threads).

---

## 4. Symbol index (grep these; don't trust any line number)

| Leg | File | Symbol |
|-----|------|--------|
| write-emit | `backend/core/streaming_orchestrator.py` | `_build_file_write_events`, `_pending_file_changes` |
| delete-emit | `backend/core/streaming_orchestrator.py` | `_build_file_delete_events` |
| finish-batch emit | `backend/core/streaming_orchestrator.py` | `SURFACE_OUTPUTS_FULL_TOOL_NAME` branch |
| finish-batch build | `backend/core/ui_actions.py` | `build_surface_events`, `ensure_report_for_run` |
| kind verdict | `backend/core/needs_human_review.py` | `needs_human_review`, `_classify_kind`, `_is_surfaceable_knowledge` |
| bash parse | `backend/core/file_change_classifier.py` | `parse_bash_write_targets`, `parse_bash_delete_targets` |
| SSE→window bridge | `desktop/src/hooks/useChatStreamingLifecycle.ts` | `event.type === 'file_changed'` handler (dispatches `swarm:file-changed`) |
| rail consumer | `desktop/src/hooks/useReferencedFiles.ts` | the `swarm:file-changed` handler |
| auto-pop consumer | `desktop/src/hooks/useCanvasAutoSurface.ts` | `onWritten` (kind gate + legacy relevance fallback + gentle suppression) |
| open-file host | `desktop/src/hooks/useCanvasHost.ts` | `handleOpenFile`, `setFile`, `isOpen`, `close` |
| open-file event const | `desktop/src/components/common/MarkdownRenderer.tsx` | `OPEN_FILE_EVENT` (`'swarm:open-file'`) |
| agent ACT allowlist | `backend/core/ui_actions.py` | `UI_COMMAND_ALLOWLIST` (`open-canvas-file` → `swarm:open-file`, document target) |

---

## 5. The invariant to hold (one line)

**`kind` (backend `needs_human_review`) is the SOLE surfacing authority. `relevance` is
legacy-fallback-only, active exclusively when `kind === undefined`.** Every leg
downstream must treat a present `kind` as the whole truth; any gate that also consults
`relevance` when `kind` is present is a Trap-1 regression waiting to happen.
