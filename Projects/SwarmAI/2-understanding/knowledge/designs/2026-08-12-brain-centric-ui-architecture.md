# Brain-Centric UI Architecture — Canvas · LeftNav/Overlay · TSCC · Brain-Hub

> **Type:** `[model]` + `[decision]` (architecture + design philosophy)
> **Verified:** 2026-08-12 — written from a source-code read (not doc inference), repo
> `/Users/gawan/Desktop/SwarmAI-Workspace/swarmai`. Numbers deliberately omitted per
> AGENT R30#4 (LOC/counts drift; measure live). File paths + invariants are the stable part.
> **Why this doc exists:** the agent (me) repeatedly failed to describe its OWN recent UI
> changes because it read stale README/CHANGELOG instead of source. This is the
> authoritative self-understanding artifact for the brain-centric UI turn.

---

## 0. The one-line frame

The LeftNav was re-architected from a **file-explorer + tool-box** (SwarmWS tree +
SwarmRadar sidebar, always-visible) into a **brain-centric organ panel**: every DDD
project is a *brain* (BrainHub), agent outputs surface in a *Canvas*, and each thread's
real injected cognition is inspectable via *TSCC*. This is the UI expression of the
product paradigm **"DDD = a universal brain + 0..N governed assets"** — the nav is no
longer "where are my files", it is "here is the mind and its organs".

---

## 1. LeftNav + Overlay system

**SSOT chain (do not hand-sync a 4th list):**
`overlayIds.ts` (`OVERLAY_IDS` tuple → `OverlayId` closed union) →
`overlaySurfaces.tsx` (one `registerOverlay({id,title,mode,width,sourceCardTestId,render})`
per surface) → `overlayRegistry.tsx` (Map) → `OverlayContext.tsx` (single `activeOverlay`
state machine, ≤1 open at a time) → `OverlayHost.tsx` (scrim/panel/spout chrome).

**Three nav regions (order is the message — BRAIN first):**
- **BRAIN** (认知区, mint-green `#5fc99a` — the sole visual differentiator, "this is YOUR
  mind"): `brain-hub` · `swarmws` · `context` · `library` · `new-brain`
- **WORK** (青蓝 `#4a8fb0`): `history` · `todo` · `jobs` · `pipeline` · `pollinate` ·
  `capabilities` · `community` · `needs-you`
- **SYSTEM** (slate `#7c8194`, dimmed labels): `hive` · `settings` · `eval`

**Agent-openable vs nav-only (a security boundary, not cosmetic):** the agent's ACT
vocabulary is `ALL_SHOW_EVENTS` (`useExclusiveOverlay.ts`) — a STRICT SUBSET of
`OVERLAY_IDS`. Deliberately absent (nav-card-only, user-intent only): **`hive`** (controls
AWS credentials + live cloud infra), **`settings`** (can change security posture),
**`library`** + **`eval`** (read-only stores). An agent turn structurally cannot open these.

**Proprioception contract (agent ACT on its own surfaces), and how drift is killed:**
`ui_action` tool → backend `UI_COMMAND_ALLOWLIST` (Python, `ui_actions.py`) validates →
emits `ui_command` SSE → frontend `dispatchUiCommand` re-derives event/target from ITS OWN
`UI_COMMAND_TABLE` (never trusts the wire) → `window.dispatchEvent('swarm:show-<id>')` →
`OverlayContext` opens. The 3 lists that must agree live in 2 languages, so:
(1) frontend `UI_COMMAND_TABLE` is **derived** from `ALL_SHOW_EVENTS`
(`Object.fromEntries(ALL_SHOW_EVENTS.map(...))`) → FE drift structurally impossible;
(2) Python can't import TS, so `UI_COMMAND_ALLOWLIST` stays a literal BUT a test
(`test_ui_actions.py`) parses the TS source and asserts equality → BE drift fails the
build. Adding an overlay = 3 coordinated edits (OVERLAY_IDS + registerOverlay +
optionally ALL_SHOW_EVENTS); the tests enforce the seam.

---

## 2. Canvas (output panel) — replaced SwarmRadar

**What it is:** the single output surface where agent-produced files (deliverables,
knowledge, reports, a finish-time code PR) auto-surface, open, and render (HTML inline,
diff via per-file toggle). **Per-tab**, keyed by **stable `tabId`** (never the volatile
`sessionId` — that key changes on first message → cross-tab bleed).

**Frontend:** `useCanvasHost.ts` (per-tab state map + event handlers + resident rail
listener) · `useReferencedFiles.ts` (rail row store, `swarm:file-changed` listener,
sessionStorage per tab) · `useCanvasAutoSurface.ts` (gentle auto-open gate) ·
`CanvasOutputRail.tsx` (row list + git badges + bee empty-state) · `FileViewerPanel.tsx`.

**Backend authority — `needs_human_review.py` (git-based verdict, the SSOT):** every
changed path is CLASSIFIED by KIND — `content` (SwarmWS deliverable) / `knowledge` (DDD,
MEMORY, KNOWLEDGE, design docs) → **pop to Canvas**; `source` (code inside a bound repo)
→ **NOT popped per-file mid-run**, aggregated into ONE `LOCAL_PR.md` at finish
(`source-final`, `baseRef=<sha>^`); `process` (`.artifacts`, `.context/*.json`,
dot-segments, gitignored) → **dropped**. Two channels: per-tool emit at ToolResult
(`_build_file_write_events`) + pipeline-finish batch (`build_surface_events` via the
`surface_run_outputs` tool).

**Design philosophy — "default surface, subtract known noise" (blacklist, not whitelist):**
old SwarmRadar had 3 independently-drifting sections each with its own data source and a
hand-maintained `BOOKKEEPING_DIRS` list duplicated in the frontend. New model: backend git
verdict is the sole authority, frontend is purely presentational; over-surface is the SAFE
direction (recoverable next sweep), under-surface is not. Code攒成一次性 PR、其它走正常路
(XG, 2026-08-04). SwarmRadar's 3 sections re-homed: Changes → Canvas · Attention →
`needs-you` overlay · Jobs&Runs → `jobs` overlay.

**Load-bearing invariants (traps):**
- Canvas state per-tab on **stable tabId** — a global var / global storage key bleeds instantly.
- **Resident rail listener** mounted once unconditionally in `useCanvasHost` (NOT in
  FileViewerPanel) — else source-final finish batches arriving while the panel is closed
  are lost. A second listener = data-loss.
- **Dot-segment check runs on TREE-RELATIVE path, never absolute** — the whole workspace
  is under `~/.swarm-ai/`, so an absolute-path scan drops EVERY deliverable (reverted
  2026-08-02, nearly re-landed twice).
- **Porcelain paths absolutized against their OWN repo root** before classify — a
  source-repo-relative path joined to SwarmWS root misclassifies as content (live bug 2026-08-04).
- **Git-error → fail-CLOSED** (kind=process, not surfaced) — an unknowable path might be a
  gitignored secret; loud-log, never silent.
- **Streaming gate**: `isStreaming===false` drops historical MergedToolBlock re-dispatches
  on restart (fail-closed).

---

## 3. TSCC — Thread-Scoped Cognitive Context

**What it is:** a per-thread transparency panel showing what was ACTUALLY injected into
THIS thread's system prompt — honest telemetry, a lower-bound, never a fabrication. Five
tabs: **Flow** (the assembly pipeline diagram) · **Files** (per-file token bars +
ownership color: sys/user/agent/gen) · **Recall** (hits with source + BM25 score + domain;
distinguishes *ran-but-missed* from *never-ran*) · **Security** (grade A–C, masked
credential/PII findings; lazy — scans only when the tab opens) · **Prompt** (real per-model
token budget + View-Full-Prompt).

**Frontend:** `TSCCPanel.tsx` / `TSCCModules.tsx` / `TSCCPopoverButton.tsx` /
`useTSCCState.ts` / `services/tscc.ts`. **Backend:** `routers/tscc.py` (4 read-only
endpoints) → `session_registry.py` module dicts (`system_prompt_metadata`,
`recall_snapshot`) populated at **delivery time** by `SessionUnit._spawn`, cleaned by
`LifecycleManager`.

**Design philosophy:** transparency into the agent's OWN cognition, decoupled from the
chat hot path (recall reads a stashed snapshot — never re-runs; security scans on demand).
It exists because after the vector-leg removal, a *systematic keyword recall MISS* is a
load-bearing failure mode that must be VISIBLE, not silently collapsed into "no recall".

**Load-bearing invariants (traps):**
- **Never show a never-sent prompt** — metadata is published at DELIVERY time
  (`_spawn`, the only consumer of `options.system_prompt`), not build time; router uses
  `setdefault` (write-if-absent). A warm-subprocess reuse discards rebuilt `options`, so
  build-time publish would show turn-2+ a prompt that was never sent (fixed run_abab234c).
- **`SELF.md` is classified `sys`, not `agent`** — runtime-owned (human/distill only,
  auto-cultivation code-blocked); the OWNER map must match the backend.
- **`ran=True`+empty-hits ≠ `ran=False`** — recall miss is counted + visible, never
  conflated with never-ran.
- **`degraded` field explicit in schema** — else Pydantic `extra=ignore` silently drops
  the fail-loud signal.

---

## 4. Brain-Hub — the DDD-as-brain lens

**What it is:** the overlay that presents each DDD project as a *brain* with the SAME
six-section cognitive structure (① Identity ② Knowledge ③ Gates ④ Capabilities ⑤ Delivery
⑥ Refresher). Gallery of brains (verdict-first: Needs-You amber zone vs Calm muted) →
Brain Detail with TWO FIXED sub-tabs (**Overview** | **Browse**, never dynamic).

**Backend — ONE builder, TWO projections** (`routers/ddd_brain.py`):
`build_brain_state(with_noise=False)` feeds the cheap gallery (`list_brains` — batches ONE
attention `collect`, ONE git-status, ONE git-log walk across all projects);
`with_noise=True` feeds the rich detail (`get_brain` — parses ② docs once). **Zero stored
metrics** — every count (sinking, typeCounts, trust distribution, escalationPending) is
computed fresh per request (R30#4); the ONE persisted artifact is a `.last-reviewed-sha`
watermark for the Review tab.

**Design philosophy — brain-first:** the BRAIN region is first + visually distinct because
the reasoning core is SwarmAI's core differentiation, and these surfaces are the
least-frequent / highest-leverage. Maps directly to the paradigm: a project is a cognitive
entity, not a folder.

**Load-bearing invariants (traps):**
- **Ontology (§①) is a FIXED slot that NEVER vanishes** — even a degenerate/0-asset brain
  renders the grid (all zeros). Gating its render on `health.noise` would drop it for a
  degenerate brain → §② becomes first → per-brain structural drift (Gate-2 HIGH).
- **A 0-asset / 0-entry brain is COMPLETE, not broken** (R31) — nascent, not a failure.
- **Trust is a DISTRIBUTION, never a project-level rollup** (Gate-1 refused the rollup).
- **Hunk→doc match by BASENAME, excluding `/knowledge/` corpus** — a corpus PRODUCT.md is
  not the canonical PRODUCT.md.
- **mtime is filesystem, not git** for gitignored DDDs (git time would be stale).
- **Section members resolved via `ddd_paths` SSOT**, never hardcoded — a misplaced doc
  fails containment silently.

---

## 5. Fate of the OLD nav (the specific changes XG named)

| Old surface | What happened | Commit(s) |
|---|---|---|
| **SwarmRadar** (3-section right sidebar) | **REMOVED**; 3 sections re-homed (Changes→Canvas, Attention→`needs-you`, Jobs&Runs→`jobs`) | `9a9fe9cc` remove · `f8f9297c` prune · `4ff70f28` retire "Radar" naming |
| **Canvas** | **ADDED** (雏形→v2 tab-scoped auto-surface→v3 HTML inline/dock/bee) as the output panel | `a18706ca` → `fba6f363` → `68a66fff` → `d9dcfb3e` tab-scope |
| **SwarmWS** (workspace tree) | **NOT deleted** — migrated from always-visible side column to an ON-DEMAND overlay; nav label de-jargoned to "Workspace"; still agent-openable (`swarm:show-swarmws` ∈ ALL_SHOW_EVENTS) | `e4bfaaf6` migrate · `25970ded` "Workspace" label |
| **TSCC** | **STRENGTHENED** — real recall hits+sources+scores, real token budget, stop-showing-never-sent-prompt, SELF.md owner fix | `e9ab51a1` · `5f1a884c` · `3d29171f` · `5540489c` · `88dba2f8` |
| **LeftNav** | **RE-ARCHITECTED** into BRAIN/WORK/SYSTEM regions with brain-hub as the primary BRAIN surface | `0bcd2684` Brain Home → `38818dc0` split → `317e077e` nav moves |

> ⚠️ Precision note: "去掉 SwarmWS 显示" = removed from the always-visible default column /
> folded under the brain-centric nav — the `swarmws` overlay itself is STILL registered and
> openable. Do NOT write "SwarmWS was deleted" in any external copy.

---

## 6. Why this matters for repositioning

These four changes ARE the brain-centric turn made concrete. Any external repositioning
("not a chatbot, a brain") is now backed by shipped UI evidence, not just a thesis:
- BRAIN-first nav + brain-hub = "each project is a mind with six cognitive sections"
- Canvas = "the mind's outputs surface here, classified by what they are"
- TSCC = "you can see exactly what this mind is thinking with, this thread"
- The paradigm anchor: `docs`/SWARMAI.md § "SwarmAI & DDD" + PRODUCT.md brain positioning.
