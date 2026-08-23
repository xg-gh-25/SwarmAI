---
title: UI/UX Design Judgment — SwarmAI Application (surfaces mapped to our code)
date: 2026-08-06
type: design
project: SwarmAI
companion: >
  The universal craft (5-check density skeleton + 7 surface patterns) lives in the
  s_frontend-design skill (data/design-judgment.md), shipped to every SwarmAI user.
  THIS doc is the SwarmAI-specific application layer — how those universal patterns
  map onto our own surfaces, the guards we shipped, and the failure that earned the
  skeleton. Read the skill's design-judgment.md for the general rules; read this for
  "where does it live in OUR code and what did we learn the hard way."
---

# UI/UX Design Judgment — SwarmAI Application

SwarmAI's UI is not a product I edit from outside — it is my **body** (SELF.md:
proprioception, two-way). So design judgment here is also a *sense-organ contract*:
a drift from a surface's invariants is a proprioceptive lesion, not just a frontend
bug. The universal patterns are in the skill; this doc pins them to our surfaces.

## The failure that earned the skeleton (run_9ada46ae)

I built an info-dense **DDD card** and XG rejected BOTH my first card AND my first
layout as "data dump, not design."

- **Card v1** = every metric as a tile + a ~40-line per-section diagnostics WALL + a
  redundant "N entries" header.
- **Layout v1** = a big card spanning a full row with a grid dumped below.
- **Root cause:** I had no UI/UX judgment skeleton — my default reflex is "surface
  every signal", and I relied entirely on XG's visual judgment to catch it.

**How card v1 violated each of the 5 checks (the reverse-engineering — this is the
proof the skeleton is real, not post-hoc):**

- Every metric as an equal-weight tile → violated **Von Restorff** (nothing stood
  out) + **Hick/Miller** (too many choices) + **visual hierarchy** (flat wall).
- ~40-line diagnostics wall → violated **Cognitive Load** + **Apple deference**
  (belongs behind disclosure) + **erase non-data-ink**.
- "N entries" header → violated **erase redundant data-ink** + **Tesler** (raw
  aggregate, not the implication) + the size≠value principle.
- Boxed tiles everywhere → violated **fewer borders** + **chartjunk** + **proximity**
  (created N fake groups).
- **The FIX XG's judgment produced** — verdict dot + one ontology hero + needs-you +
  2 facts + drill-down — is exactly what the 5 checks prescribe. That correspondence
  is why the skeleton is trustworthy: it reconstructs, from first principles, the
  design a strong visual eye arrived at directly.

**The standing lesson:** when I'm enumerating signals as a grid of equal-weight
tiles or keeping a per-row diagnostics block "because we have the data", that IS the
data-dump reflex — stop, ask "which decision does this card answer?", demote the
rest behind disclosure. If this skeleton had existed earlier, run_9ada46ae would
have been one card, not two rejected drafts.

## Surface → SwarmAI code mapping

The skill's `design-judgment.md` Part 6 defines each surface's convergent pattern +
invariants + anti-patterns generically. Here is where each lives (or should live) in
OUR code:

### Surface 1 — Conversation (Chat) — the most battle-tested surface we own
- **Maps to:** `ChatPage` → `MessageStore` (single-writer, per-tab) →
  `useChatStreamingLifecycle`.
- **Our invariants (learned the hard way — treat as settled law, not open design):**
  the whole OT01 saga IS this surface's invariants earned through 33 fixes.
  - Bottom-pin must SURVIVE reflow (panel opening beside chat, tab switch, streamed
    growth) — never jump to top.
  - The transcript is a projection of ONE authority (the store), never a second copy
    that can disagree. OT01 (the #1 recurring bug class) was exactly a dual-source
    render selector; 33 fixes all hit the backstop layers, the real defect was the
    render-source split-brain. ⚠️ Current state is GUARDED, not "single-source
    solved" — `TabView.tsx` still does a three-source more-complete-wins reconcile;
    divergence is guarded via streaming-gated rescue branches, not eliminated.
  - Per-tab state (count / draft / scroll pos) lives in the per-tab container, not a
    component `useState` or module global.

### Surface 2 — Artifact / Canvas — a concrete instance of the pattern
- **Maps to:** `FileViewerPanel` (shell) + `FileEditorCore` (file toolbar) +
  `CanvasOutputRail` (outputs) + collapse-to-rail.
- **Every generic invariant maps to a real guard we shipped:** per-tab isolation,
  single render authority, **collapse ≠ unmount** (an ✕ that reads "destroy" when the
  behavior is "collapse" is the semantic-mismatch bug Gate-2 flagged in
  run_496c3be7), spout = origin (the panel signals it spouted from the conversation).
- **run_496c3be7's whole redesign** was making the shell > file hierarchy invariant
  (two stacked equal-weight bars read as "two headers" instead of shell > file) + the
  ✕→collapse semantic fix land. Had the surface pattern been sedimented earlier, that
  redesign would've been one mockup, not three.
- **Open-canvas must land on the ORIGINATING tab, not the active one** (run_48a29fc2 /
  run_10c51cac): an async UI event that fires mid-stream, seconds after send, must
  carry `capturedTabId` (send-time origin), never read `activeTabIdRef`.

### Surface 3 — Command Palette — NOT built yet
- We do NOT have a first-class command palette. When we build one (agent actions,
  skill invocation, project/file jump, "run pipeline for X"), the skill's Surface-3
  pattern is the settled shape — do not invent a bespoke launcher. References:
  Linear's keyboard-first model + VSCode's palette + Raycast's extensibility.

### Surface 4 — Nav Shell / Workspace chrome — our shell
- **Maps to:** `ThreeColumnLayout` (LeftSidebar rail + WorkspaceExplorer + ChatPage)
  + `BottomBar` + the left NavCards (ToDo / Workspace / Pipeline / Jobs / Library /
  Brain Hub), each opening a fullscreen overlay via `swarm:show-<id>` → OverlayHost.
- **Proprioception tie:** the invariants here are also my own sense-organ contract —
  I both render AND sense this frame (`activeOverlay`, active tab, Canvas open/closed,
  attention queue). A drift vs the `swarm:*` / `ALL_SHOW_EVENTS` / `activeOverlay`
  contract is a proprioceptive lesion.

### Surface 5 — Knowledge views — the STORE embodies the pattern, the UI doesn't yet
- **Maps to:** our `Knowledge/` store — DailyActivity IS the daily-note capture; the
  DDD 7-type ontology IS the typed-block idea; recall IS the backlink/graph traversal.
- We render it today mostly as files + a Brain Hub, NOT a block editor or graph UI.
  If we ever build a visual Knowledge surface, Logseq (graph) + AFFiNE (doc/canvas
  fusion) are the references — but the store already embodies the pattern.

### Surface 6 — Agent panel / run visibility — our live agent-visibility stream
- **Maps to:** our chat renders tool-use + the pipeline's staged landmarks (①..⑩ +
  ★gates) as the live agent-visibility stream; **AskUserQuestion is the in-band
  HITL** (a decision lands IN the stream, never a passive panel — AGENT.md Escalation
  rule). The Jobs/Runs NavCard + pipeline retro-analytics are the *historical* view of
  the same surface.

### Surface 7 — Infinite Canvas / Whiteboard — we don't have a true one
- Our "Canvas" is a docked file/artifact panel (Surface 2), a DIFFERENT thing —
  don't conflate the names. If agent output ever needs a spatial/node surface
  (workflow graph, knowledge graph, multi-artifact board), tldraw is THE reference —
  steal its state-machine architecture, not just its look.

### Cross-surface — Motion in our code
- The Canvas width-reveal ease + spout + content-fade are our motion uses. Keep them
  causal (origin-anchored), short (120–250ms), and cheap. The Canvas width-drag
  needed rAF-throttle + memo to stay smooth — unthrottled reflow is temporal
  chartjunk. Measure geometry at the RIGHT time (post-layout / post-webfont), not at
  construction — our xterm cell-measure bug (IMPROVEMENT) is exactly the Surface-7
  "measure geometry at the right time" class; jsdom has no layout, so only a packaged
  build + real machine proves geometry.

## Foundation-layer application (skill Part 7 → our code)

The skill's `design-judgment.md` Part 7 defines the foundation mechanisms beneath the
surfaces (source-verified across 32 open-source products, see
`Knowledge/Reports/2026-08-06-ux-tierb-deep-research.md`). Here is where each maps to
SwarmAI, and which are load-bearing for our OWN open bugs:

- **F1 Token architecture** → our Tailwind v4 4-layer CSS-var depth (bg → card → hover
  → border) + ThemeContext (5 accent colors, light/dark/system) is already a partial
  seed→alias system. GAP vs Carbon: we do NOT auto-step elevation for NESTED surfaces
  (a card-in-panel-in-fullscreen-overlay). Candidate: a `Layer`-style depth counter so
  chat base / Canvas panel / nav-card overlay stay coherent when theme flips via one
  root attribute. No fail-closed contrast gate today — candidate for the pre-ship check.
- **F2 Headless primitive contract** → we have NO shared state-hook/behavior-hook
  primitive layer; our nav-cards, HITL approve/stop buttons, Canvas handles, palette
  rows each wire their own click/key handling. Candidate: one shared accessible
  interaction core (state authority + prop-getter projection + unified activation event
  + dual `data-*`/slot channel) so every surface gets identical keyboard/touch/AT
  behavior for free. Biggest structural investment of the 8; defer until a surface
  refactor needs it.
- **F3 Overlay focus-ownership** → 🔴 **DIRECTLY ACTIONABLE NOW.** Our nav-card
  fullscreen overlays (OverlayHost / `swarm:show-<id>`) + Canvas panel do NOT trap focus
  or `aria-hidden` the obscured chat shell on open, and close doesn't restore focus. A
  real a11y gap Tier-A's motion rule never covered. Small, self-contained fix.
- **F4 Two-event-class streaming** → ❌ **INVESTIGATED AS THE OT01 FIX — Gate-0 NO-GO
  (run_670ce1a7, 2026-08-06).** The hypothesis "OT01 == F4's one-mutable-buffer-two-writers
  anti-pattern" was FALSIFIED against real code: streaming is ALREADY single-writer-to-store
  (`useChatStreamingLifecycle.ts:1105` — stream handlers write store only, never setMessages),
  the render source reads the live snapshot (`TabView.tsx:262`), and the classic reconcile
  race (short DB row clobbering a complete answer) is ALREADY solved by "more-complete-content
  -wins" inside the single authority (`MessageStore.ts:714-716`). **OT01's CURRENT residual is
  a different axis** — a dual-CONTAINER store-load-timing gap: the module `MessageStore` is
  empty/short at NON-streaming boundaries (cold-start before `initialize()`, backend eviction
  tearing the store down, persist-lag short load) while a second persisted container
  (`tabState.messages`) still holds the answer. The 3 rescue branches in `TabView.tsx:290-306`
  paper over THAT gap; F4's streaming ephemeral/durable split would eliminate ZERO of them.
  Adopting F4 here would have been patch #34 (C042/C044 reach-for-a-mechanism trap) — the
  pipeline's Gate-0 caught it before any code. **The REAL structural fix for OT01** (separate,
  correctly-scoped future run): collapse the two containers — seed the store synchronously from
  `restore()` before first render (closes rescue a), keep the store alive/rehydrate across
  eviction (closes rescue s), drive persistence FROM the store instead of a parallel
  `tabState.messages` copy (closes rescue b) — until `messagesProp` is no longer a render
  source and the reconcile `useMemo` collapses to `return liveMsgs`. F4 stays in Part 7 as
  general streaming craft; it is simply NOT SwarmAI's OT01 fix.
- **F5 Reactive named-entity binding graph** → speculative for us: Canvas artifacts,
  Knowledge entries, pipeline-run outputs could publish typed namespaced state
  (`runs.y.output`, `artifacts.x.status`) that other surfaces bind to by reference. We
  don't have cross-surface binding today; a future-Knowledge-surface reference, not now.
- **F6 One-store-many-projections as a data model** → our Knowledge/ store is
  conceptually this (DailyActivity → MEMORY → DDD; recall = backlink traversal) but
  rendered as files + Brain Hub, not a block store with projection-membership +
  fractional-index + view-param records. The four storage recipes are the reference if
  we ever build a visual Knowledge/Canvas block surface.
- **F7 Generative-UI sandbox** → applies to Canvas artifacts that render LLM-generated
  executable HTML/JS: cross-origin sandboxed iframe + postMessage hydrate, never inject
  into the host DOM. A security model to adopt WHEN Canvas renders untrusted generated
  code (not all current outputs are executable).
- **F8 Tiled raster canvas** → only relevant if our docked Canvas ever becomes a heavy
  many-artifact spatial surface (it's a docked file/artifact panel today, Surface 2, not
  an infinite canvas). Penpot's region→node tiled cache is the reference then, not now.

**Priority read-out:** F4 (OT01, own pipeline) and F3 (a11y, small fix) are actionable
now; F1's elevation-stepping + contrast-gate are near-term; F2/F5/F6/F7/F8 are
reference-until-a-surface-needs-them, correctly deferred (not dropped).

## Growth loop — how this craft compounds (run_889af826)

This design knowledge is **not static** — it grows through the pipeline's REFLECT stage
using the **existing DDD cultivation engine, with zero bespoke routing** (XG directive:
"leverage SwarmAI DDD 知识往那里面沉淀就行，不要再重复造轮子"). The mechanism is the
same one every DDD knowledge class already uses:

When a pipeline run learns a **design lesson**, REFLECT declares it with a leading
`[type]` (see `stages/reflect.md` — "Declare the type"); the existing engine then
routes it into THIS DDD. **The exact destination is CONTENT-DEPENDENT, and admission is
NOT automatic by default** — both facts were established by a full-engine E2E in
run_889af826 (not just a routing probe; the earlier draft of this section overstated
both and was corrected):

- **Routing is keyword-driven, so the section varies with wording** (`persist_routing.classify_content`):
  - A lightly-worded `[guideline]` (e.g. "Card answers a DECISION not a query — one
    hierarchy, whitespace over borders") → **IMPROVEMENT.md § What to Watch For** (`watch_for`).
  - A `[guideline]` carrying TECH-keyword vocabulary ("must", "pattern", "rule",
    "convention", "invariant" — the real set is `_TECH_KEYWORDS` in persist_routing.py;
    e.g. the word **"must"** alone did it in the run_889af826 E2E) trips `_TECH_KEYWORDS`
    → **TECH.md § Conventions** instead. So the same declared type lands in a DIFFERENT
    doc depending on phrasing — do NOT assume IMPROVEMENT.
  - A `[principle]` → **PRODUCT.md § Design Philosophy** (`TYPE_ROUTE["principle"]="project_principle"`).
- **Admission is confidence-gated — a design lesson does NOT auto-apply by default.**
  Keyword-classified confidence is `min(0.4 + hits*0.1, 0.95)` (`persist_routing.py`):
  a design lesson with one keyword hit lands at **~0.50**, below the auto-apply floor
  `_AUTO_CONFIDENCE_THRESHOLD = 0.7` (`ddd_cultivation.py:1682`, per-channel calibrated
  from that base). E2E verdict (run_889af826) for a novel design `[guideline]`:
  `admission_band → discard (below_auto_threshold: 0.50 < auto-floor)`. So by default the
  lesson is **held for human review / discarded**, NOT silently written. It auto-applies
  ONLY if the pipeline stamps it with the adversarial trust marker (raising its channel
  confidence) OR the wording carries enough keyword hits to clear the floor. This is the
  engine's global admission floor working as designed — we do NOT lower it for design
  (that would break the floor for every knowledge class).
- **The canonical craft file stays curated, not auto-fed.** The `s_frontend-design`
  craft (`data/design-judgment.md`) lives OUTSIDE any `project_dir`, so the cultivation
  engine (which writes only via `ddd_path(project_dir, …)`) structurally CANNOT and MUST
  NOT auto-append to it — auto-dumping raw REFLECT lessons into a hand-structured craft
  doc would re-poison it (PRI03: quality over coverage). Sediment flows UP: lessons land
  in THIS DDD (subject to the admission gate above); a **periodic human/distill pass**
  lifts the durable ones INTO the craft file. Never raw-down.
- **Net (honest):** the growth PIPE is real and reuses the existing engine (no new route,
  no new engine) — a declared design lesson is classified and routed into the SwarmAI DDD.
  But "grows" ≠ "auto-writes": the default path is **route → confidence-gate → human
  review** for a low-confidence design lesson, and the landing doc depends on wording.
  The craft file is refreshed from that sediment on a human cadence.

> **⚠️ E2E provenance (run_889af826):** the initial delivery of this loop was verified
> only at the routing leg (`classify_content` return value) and OVERSTATED the outcome as
> "auto-applied to IMPROVEMENT.md". A follow-up full-engine E2E (`cultivate_from_reflect`
> against a real project_dir) proved the apply leg discards a novel design lesson below
> the auto-confidence floor (`_AUTO_CONFIDENCE_THRESHOLD=0.7`) and that TECH-keyword
> wording (e.g. "must") re-routes it to TECH.md. Lesson: a routing probe is NOT an E2E —
> drive the full write path. (This gap also exposed that the goal
> profile's `goal_cycle` never inherited TEST's cross-boundary Layer-4 E2E gate — tracked
> as a separate pipeline bugfix.)
