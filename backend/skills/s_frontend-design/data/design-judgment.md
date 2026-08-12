# UI/UX Design Judgment — Industry-Validated Skeleton

> Read this BEFORE laying out ANY info-dense UI — card / dashboard / detail panel /
> gallery / report / settings page. It is the judgment skeleton that stops the
> single most common failure mode of a capable builder: **"surface every signal" →
> a data-dump that reads as broken even when every number is correct.**
>
> Sources professional designers actually use: Laws of UX (Jon Yablonski,
> https://lawsofux.com) · Refactoring UI (Wathan & Schoger, https://refactoringui.com)
> · Apple Human Interface Guidelines
> (https://developer.apple.com/design/human-interface-guidelines) · Edward Tufte
> (data-ink ratio / chartjunk / small multiples) · Web Design in 4 Minutes (jgthms,
> http://jgthms.com/web-design-in-4-minutes/).
>
> **SwarmAI counterpart:** this is the universal craft (canonical home). For how it maps
> onto SwarmAI's own surfaces + the run_9ada46ae failure that earned this skeleton, read
> `Projects/SwarmAI/2-understanding/knowledge/designs/2026-08-06-ui-ux-design-judgment-swarmai.md`.
> The WHEN-reflex ("read this before producing info-dense UI") is governed by AGENT.md R15.

## The one-line thesis (memorize)

> A card answers a DECISION, not a query. Show the few things that change the
> decision, give them whitespace + one clear hierarchy, and erase every pixel —
> border, badge, redundant count, decorative tile — that carries neither data nor a
> comparison. Everything secondary goes behind disclosure.

## The pre-ship checklist (5 checks — burn these in)

For ANY info-dense card/dashboard, before shipping:

1. **Does every element earn its place?** Delete any tile/metric/badge that doesn't
   change a user decision. Start from "the 2-4 decisions the user makes here", NOT
   from "the fields the data has." (Apple deference + Occam + Pareto + Hick/Miller)
2. **Erase REDUNDANT data-ink.** One representation each — no count-header over a
   list that already shows N, no legend repeating on-datum labels, no count + bar +
   percent for one fact. (Tufte)
3. **Erase NON-data-ink / no chartjunk.** Kill borders, background fills, gradients,
   drop-shadows, decorative per-tile icons. A boxed-shadowed-tile-WALL is
   layout-level chartjunk. Separate with whitespace, not boxes. (Tufte + Refactoring
   UI "fewer borders")
4. **Exactly ONE thing stands out — the verdict/answer — everything else demoted.**
   If every tile is bold+boxed, NOTHING stands out. Build hierarchy with weight +
   color, not just size; de-emphasize the surroundings to emphasize the primary.
   (Von Restorff + visual hierarchy)
5. **"Compared to what?" + hierarchy-first + fix crowding with SPACING.** A number
   with no reference (vs target / last period / peer) is detail-on-demand, not a
   headline. Establish hierarchy before polish; add whitespace before adding
   structure ("when a page looks broken, it's usually spacing"). (Tufte + jgthms)

**The tell that you're about to ship a data-dump:** if you're enumerating signals
as a grid of equal-weight tiles, or keeping a per-row diagnostics block "because we
have the data" — that IS the data-dump reflex. Stop, ask "which decision?", demote
the rest behind disclosure.

---

## Part 1 — Laws that govern info density (Laws of UX)

| Law | Rule | The decision it forces |
|-----|------|------------------------|
| **Hick's Law** | Decision time grows with number + complexity of choices | Every tile you add SLOWS the user. Cut to the 3 that drive action. |
| **Miller's Law** | ~7±2 items in working memory | Never > ~5-7 top-level items; chunk the rest under headers. |
| **Chunking** | Group into meaningful blocks, not a flat list | 15 loose fields → 3 groups (Status/Cost/Owner). Scan 3, not 15. |
| **Cognitive Load** | Every element spends a finite mental budget | A diagnostics wall is pure extraneous load — remove it from default view. |
| **Von Restorff (Isolation)** | The thing that stands out is what's noticed — so ONLY ONE should | If every tile is bold+boxed, NOTHING stands out. Make the verdict dominant, mute the rest. |
| **Pareto** | ~80% of value from ~20% of content | Find the 20% users decide on; that IS the page. Demote the 80%. |
| **Occam's Razor** | Among designs that work, fewest elements wins | 4 metrics work? Don't ship 12 "because we have the data." |
| **Tesler's Law** | Irreducible complexity must be absorbed by the SYSTEM, not the user | Don't dump raw fields; compute a verdict, hide the machinery. |
| **Prägnanz** | People read the simplest possible form | A sentence verdict beats a 6-tile grid. |
| **Proximity + Common Region** | Nearness / shared boundary = "these are related" | Spacing IS grouping. One boundary per decision-unit — don't box every field. |
| **Serial Position** | First + last remembered best | Most important metric FIRST, primary action LAST, low-value in the middle. |
| **Jakob's Law** | Users expect your UI to work like others they know | Use the summary-card + drill-down convention; don't invent a tile-wall. |
| **Aesthetic-Usability** | Clean designs are PERCEIVED as more usable | A cluttered data dump reads as "broken" even when the data is correct. |

## Part 2 — Tactics (Refactoring UI)

1. **Use a visual hierarchy** — rank elements deliberately; a flat design "feels
   noisy, one big wall where it's not clear what matters." Pick ONE primary; make it
   big+bold; supporting stats small+grey.
2. **Size isn't everything** — build hierarchy with *weight + color/contrast*, not
   just font-size. Verdict = normal size, bold, dark; metadata = same size,
   light-grey.
3. **De-emphasize to emphasize** — make the important thing pop by TONING DOWN
   everything around it, not shouting louder.
4. **Labels are a last resort** — drop labels when the value's format is
   self-evident ("$99.00" needs no "Price:"); fold label into value; de-emphasize
   any that remain. *(Exception: dense spec pages where users scan FOR the label
   word.)*
5. **Use fewer borders** — borders make it busy; separate with shadow / background /
   spacing instead. Replace a grid of bordered boxes with whitespace-separated
   groups.
6. **Start with too much whitespace, remove until happy** — breathing room ≈ clean.
7. **Don't fill the whole screen** — full-width content is harder to interpret;
   constrain to a readable column.
8. **Systematize everything** — pick from a fixed scale (4/8/12/16/24px spacing,
   sm/md/lg shadow) — never hand-tune from a limitless pool.
9. **Avoid ambiguous spacing** — space WITHIN a group must be tighter than space
   BETWEEN groups, or grouping reads wrong.
10. **Beware empty states** — hide tabs/filters that do nothing yet.

## Part 3 — Tufte (the direct antidote to data dumps)

1. **Above all else, show the data** — data gets the ink budget; chrome is overhead.
2. **Maximize data-ink ratio** — pixels showing VALUES ÷ total pixels → toward 1.0.
   If half the card is border/background/badge, you failed the ratio.
3. **Erase non-data-ink** — delete borders, background fills, heavy dividers,
   shading.
4. **Erase REDUNDANT data-ink** — kill the "total count" header when the list
   already shows N; don't show count + bar + percent when one answers the decision.
5. **No chartjunk** — no 3-D, gradients, drop-shadows-for-depth, decorative per-tile
   icons. The boxed-shadowed-tile-wall is chartjunk at the LAYOUT level.
6. **Small multiples** — to compare N partitions, use a grid of small charts on a
   SHARED scale, not N differently-scaled tiles.
7. **"Compared to what?"** — a number with no reference point (vs target / last
   period / peer) is noise. No reference → it's not a headline, it's
   detail-on-demand. *(Caveat: minimalism can be over-applied — a removed reference
   line or a meaningful icon can aid readability/memory. Erase decoration; keep ink
   that carries a comparison.)*

## Part 4 — Apple HIG (Clarity · Deference · Depth)

_[Canonical, well-established themes — Apple's HIG is a JS SPA, not fetched
verbatim.]_

1. **Deference — content over chrome.** UI must never compete with content;
   minimize toolbars, borders, decoration. If a container out-shouts its contents,
   strip the container.
2. **Clarity — legible + highlight with negative space.** The primary datum is the
   most legible element; don't make a label or a "TOTAL:" header bigger than the
   number the user reads.
3. **Depth — layers convey hierarchy.** Primary metric foreground; context one step
   back (muted, smaller); raw diagnostics a layer deeper (collapsed).
4. **The deference test: "does every element earn its place?"** Before adding a
   tile, ask "which user decision does this change?" None → it's not a tile, at most
   detail-on-demand.
5. **Fill with content, HINT at the rest** — show the top 3-5; disclosure hints at
   more rather than dumping a wall.

## Part 5 — Web Design in 4 Minutes (do these first, in order)

Content first → constrain line length (~50em) → typography before decoration →
**spacing is the #1 fix** ("when a page looks broken, it's usually spacing") →
soften contrast deliberately (body #555, headings #333 — color AS hierarchy) → ONE
accent color → hierarchy via size+weight+color together → identity/imagery LAST.

---

## Part 6 — Surface patterns (what shape each surface converges to)

The checklist above governs *how to lay out a dense card* (density). This part
governs *what shape a specific SURFACE should take* (pattern). World-class products
independently converge on the same shape for the same surface — that convergence
point IS the pattern, and copying it is Jakob's Law, not unoriginality. Deviate only
where there's a REAL structural difference, and name it.

Each pattern: **convergence** (who landed on it) · **why** (the human need) ·
**invariants** (what must hold) · **anti-patterns**.

### Surface 1 — Conversation (Chat)
- **Convergence:** ChatGPT · Claude · Open WebUI · LibreChat — a bottom-anchored
  streaming transcript, message-level affordances on hover (copy/edit/regenerate/
  branch), a model/agent selector that doesn't crowd the composer, a composer that
  grows with content but never eats the transcript.
- **Why:** conversation is ephemeral + linear; attention lives at the newest token
  (bottom). Affordances must be reachable but invisible until wanted (Tufte
  deference).
- **Invariants:** (1) bottom-pinned by default, and the pin SURVIVES reflow (a panel
  opening beside chat, a tab switch, streamed growth) — never jump to top. (2) The
  transcript is a projection of ONE authority (a store / state machine), never a
  second copy that can disagree. (3) Per-tab state (count / draft / scroll pos) lives
  in the per-tab container, not a component `useState` or module global. (4) Message
  affordances appear on hover, one row, no reflow. (5) Streaming has a visible,
  honest state (thinking / streaming / done) driven by the real state machine, not a
  frontend guess.
- **Anti-patterns:** a spinner driven by a frontend boolean that drifts from backend
  truth; affordances in an always-visible button bar (chrome noise); a fat-header
  model selector; losing scroll position on any layout change.

### Surface 2 — Artifact / Canvas (durable output beside the chat)
- **Convergence:** ChatGPT Canvas · Claude Artifacts · Bolt.new preview · Cursor
  composer — all MOVE the durable produced thing OUT of the chat stream into a
  persistent side surface. Chat = conversation; side panel = the artifact you
  review, edit, iterate.
- **Why:** separate the ephemeral (dialog) from the durable (deliverable). A produced
  artifact needs full-width review and must not be washed away by streaming; but chat
  stays the primary column.
- **Invariants:** (1) chat stays primary; the artifact panel is secondary +
  clampable (never starves chat below a healthy min width). (2) The panel is a
  projection of a persistent store, not a copy (single authority). (3) Open/close
  NEVER destroys the artifact — collapse ≠ delete; a dismissed artifact is one click
  from return, content + outputs intact. (4) Exactly ONE "current" artifact; the rest
  addressable / re-openable (an outputs list). (5) The panel signals its ORIGIN
  ("this spouted out of the conversation") so the spatial relationship reads.
- **Anti-patterns:** a modal that blocks chat; a tab switch that loses artifact
  content; two authorities (panel keeps its own drifting copy); an ✕ that reads
  "destroy" when the behavior is "collapse" (semantic mismatch); two stacked bars at
  equal weight reading as "two headers" instead of shell > file.

### Surface 3 — Command Palette (keyboard-first action surface)
- **Convergence:** VSCode (⌘⇧P) · Raycast · Linear · Zed · Superhuman — one
  fuzzy-searchable modal, the SINGLE entry to every action, recent/contextual on
  top, keyboard-navigable, showing each action's shortcut (so it TEACHES its
  accelerators).
- **Why:** power users hate hunting menus; a palette collapses an unbounded action
  space into one search box (Hick's Law) and progressively teaches shortcuts.
- **Invariants:** (1) ONE consistent invocation (a global chord), same everywhere.
  (2) Fuzzy match on action NAME, ranked by recency + context. (3) Each row shows its
  shortcut. (4) Fully keyboard-drivable (never forces the mouse). (5) Dismiss is cheap
  + non-destructive (Esc / click-out).
- **Anti-patterns:** a palette only some views expose; mouse-only; no shortcut hints
  (misses the teaching value); slow/janky filtering (must feel instant or users
  revert to menus).

### Surface 4 — Nav Shell / Workspace chrome
- **Convergence:** VSCode (activity bar + collapsible side panel + editor + bottom
  bar) · Linear · Grafana · Slack — a thin persistent nav rail, a collapsible
  contextual sidebar, a dominant content area, a slim status/utility bar. Density is
  HIGH but hierarchy is strict.
- **Why:** a workspace is open all day; the frame must be dense yet calm. Rail =
  muscle memory (fixed positions), sidebar = context, content = where work happens,
  status bar = ambient (never demands attention).
- **Invariants:** (1) nav rail = stable positions; icons + tooltip, not a fat labeled
  menu. (2) Sidebars collapsible; content area is the protagonist (Apple deference).
  (3) High density OK IF hierarchy is strict — ONE thing dominant per zone (Von
  Restorff), the rest ambient (Grafana is the master of dense-but-calm). (4)
  Status/ambient info in a slim bar, never a toast-storm or modal. (5) State (which
  panel open, widths) persists per user.
- **Anti-patterns:** everything equal-weight (dense AND flat = "broken"); modal
  interruptions for ambient info; a nav that reflows/reorders (destroys muscle
  memory); sidebars that can't get out of the way.

### Surface 5 — Knowledge views (block editor · graph · backlinks)
- **Convergence:** block editor (Notion · AFFiNE · AppFlowy — everything is a typed,
  draggable, slash-created block); graph/backlink (Logseq · Obsidian — bidirectional
  links + graph view + daily-note capture); docs+canvas fusion (AFFiNE — one store,
  two projections).
- **Why:** knowledge work is non-linear — you link, transclude, re-view the same node
  many ways. A block model makes structure manipulable; backlinks make the graph
  navigable without manual filing.
- **Invariants:** (1) typed blocks with a slash-command creation menu; drag to
  reorder. (2) Bidirectional links — creating A→B surfaces B's backlink to A
  automatically. (3) ONE content store, MULTIPLE projections (doc / graph / canvas) —
  never divergent copies. (4) Low-friction capture (daily note / quick-add) separate
  from organization.
- **Anti-patterns:** a monolithic text blob (no manipulable structure); manual-only
  filing with no backlink/graph; divergent copies per view.

### Surface 6 — Agent panel / tool-call & run visibility (AI-native)
- **Convergence:** Claude/ChatGPT tool-call cards · Cursor/Windsurf agent loop ·
  OpenHands · Continue.dev — the agent's actions (tool calls, file edits, terminal
  runs, sub-agent spawns) render as a legible, collapsible STREAM of typed cards,
  each showing intent + status + result, expandable for detail.
- **Why:** an autonomous agent is opaque by default; trust requires legibility. A
  typed action-card stream turns an opaque loop into an auditable one.
- **Invariants:** (1) each agent action = a typed card (tool name + args summary +
  status + result), collapsible; detail on demand. (2) Live status per action
  (running / done / failed) driven by real events. (3) Human-in-the-loop affordances
  land IN the stream, in-band (approve / stop / answer) — never a passive panel the
  user must dig out. (4) Long/nested work (sub-agents) is summarized, expandable, not
  dumped.
- **Anti-patterns:** raw log dump as the primary view; a decision the agent needs
  routed to an async panel instead of the current channel; no stop/intervene; status
  that lies (frontend guess vs real state).

### Surface 7 — Infinite Canvas / Whiteboard (spatial surface)
- **Convergence:** tldraw · Excalidraw · Figma · Penpot — an infinite pannable/
  zoomable surface; shapes as first-class objects; a selection model with handles; a
  state machine driving tool modes; plugin/shape extensibility.
- **Why:** spatial thinking (diagrams, boards, node graphs) needs a canvas, not a
  document. tldraw's core lesson: the whole thing is a STATE MACHINE (idle → pointing
  → dragging → resizing …) — get that right and interactions compose; get it wrong
  and you have edge-case hell.
- **Invariants:** (1) infinite pan/zoom with a stable coordinate model; viewport ≠
  document space. (2) shapes are typed, addressable objects; selection has handles.
  (3) tool modes are an explicit STATE MACHINE, not a pile of boolean flags. (4)
  extensible shape/tool registry. (5) measure geometry at the RIGHT time (post-layout
  / post-webfont), not at construction.
- **Anti-patterns:** boolean-flag soup instead of a state machine; measuring
  `getBoundingClientRect`/cell size before layout/fonts settle; verifying geometry on
  jsdom (it has no layout — only a packaged build + real machine proves it).

### Cross-surface — Interaction & Motion (applies to all)
- **Convergence:** Framer Motion · Linear's micro-interactions · Apple HIG motion —
  motion communicates CAUSALITY and continuity (where did this come from, where did
  it go), it is not decoration.
- **Invariants:** animate to show relationship (a panel eases open FROM its origin);
  respect `prefers-reduced-motion`; keep durations short (120–250ms for UI, not
  showy); never animate the thing the user is trying to read WHILE they read it.
- **Anti-pattern:** motion as flourish (temporal chartjunk); janky transitions from
  unthrottled reflow (throttle with rAF + memoize).

### The frontier — Generative UI (watch, don't adopt yet)
OpenUI · Hue · Open Design · thesys — the bet that agents will GENERATE UI directly
(a spec/intent → a rendered interface) rather than a human hand-coding each screen.
The pattern to watch is "design-system-as-constraint for generated UI" — the
generator is only as good as the token/component system it's constrained by. Early
and unproven; named so a future strategy call has the reference, not adopted now.

---

## Part 7 — Foundation layer (the mechanisms BENEATH the surfaces)

Part 6 governs what SHAPE each surface takes. This part governs the reusable
foundation every surface is built ON — token architecture, the interactive-primitive
contract, declarative a11y, cross-surface state binding, block storage, sandboxed
generative UI, and heavy-canvas rendering. Source-verified across 32 world-class
open-source products (IBM Carbon · Ant/TDesign · React Aria · Zag/Ark UI · Base UI ·
Headless UI · USWDS · OpenHands · Continue.dev · Appsmith/ToolJet/Budibase · AFFiNE ·
Logseq · Focalboard · OpenUI · Penpot …). Where Part 6 says WHAT, Part 7 says HOW the
substrate is structured.

### F1 — Token architecture: derive, don't hard-code
Color/elevation/theme is a DERIVED, contextual token system, not per-component hex.
Three convergent mechanisms: **(1) Seed→Map→Alias** — a tiny seed set (accent, radius,
base spacing/font) is expanded by algorithm functions into map + alias tokens, so
dark / compact / high-contrast are *algorithms over one seed*, not parallel
hand-authored stylesheets (Ant Design v5). **(2) Two-layer indirection** — authoring
alias → runtime CSS custom property (`var(--x)`) with semantic background LAYERS
(page / container / component) + per-state hover/active variants, so theme is a single
`:root[theme-mode]` attribute swap with zero recompile and zero component branching
(TDesign). **(3) Contextual elevation stepping** — a `Layer` wrapper auto-steps an
elevation counter on nesting (level 0→1→2 via a `useLayer()`-style hook) so a nested
surface picks the correct stepped background for its depth WITHOUT knowing its own
context; paint is decoupled from depth-tracking via an opt-in `withBackground` prop
(IBM Carbon). **(4) Fail-closed contrast gate** — a validator resolves token refs and
computes REAL WCAG contrast on resolved text/bg pairs, blocking ship on <4.5:1 —
correctness is a program-gate, not a prose checklist (Hue). *Anti-pattern:* per-component
hard-coded color; parallel hand-authored dark/light stylesheets; contrast "checked by eye."

### F2 — Headless primitive contract: state-hook / behavior-hook / prop-getter
Push "projection of ONE authority" DOWN to the component-primitive level. Every
interactive primitive splits into a platform-agnostic **STATE authority** (a statechart
/ state-hook that owns truth with no DOM/ARIA) and a thin **BEHAVIOR layer** that only
PROJECTS that state into spreadable DOM prop-getters whose handlers call back into
state actions (React Aria `useX`; Zag `connect(service, normalizeProps) → api.getRootProps()`).
ALL aria/focus/keyboard live in the machine and cannot drift per-render. Two sharpenings:
**(a) one unified activation event** carrying a `pointerType` discriminator
(mouse|touch|keyboard|virtual) absorbs device quirks (touch-before-emulated-mouse dedupe;
drag-off clears pressed unlike CSS `:active`; virtual/screen-reader clicks via
`detail===0`) so `onClick`/`onKeyDown`/`onTouch` are never wired separately (React Aria).
**(b) dual state channel** — every state exposed BOTH as a CSS-targetable `data-*`
attribute (`data-open`, `data-selected`, `data-enter/leave`) AND a JS render-prop/slot
value, plus one polymorphic `render`/`asChild` escape hatch — so styling never
re-derives state and consumers restyle inner parts without forking (Base UI · Headless
UI · Ark UI). *Anti-pattern:* aria/keyboard logic scattered in render; separate
click/key/touch handlers; CSS re-deriving open/selected from props.

### F3 — Overlay focus-ownership contract + exit-animation lifecycle
An overlay/panel owns an explicit ACCESSIBILITY focus contract distinct from visual
layering (Part 6's motion rule covers neither). On open: **trap focus** (skipping to
the panel), and mark the obscured shell `aria-hidden`/`inert` so keyboard + assistive
tech cannot wander into content the user can't see. On close: **restore focus** to the
invoking element, and run the exit animation as an explicit lifecycle state
(`data-closed`/`data-leave` + a "still mounted while animating out" phase) so the node
isn't yanked mid-transition (USWDS modal · Base UI · Headless UI transitions).
*Anti-pattern:* an overlay that leaves focus in the hidden shell; unmounting on close
before the exit animation finishes; visual dimming without AT-level hiding.

### F4 — Streaming as a SEPARATE ephemeral event class, reconciled into the durable authority
Split live streaming into TWO event classes with explicit reconciliation instead of
mutating one buffer: an **ephemeral** stream delta (may be reasoning-only, may be
revised) and the **durable** authoritative event that SUPERSEDES it via ordered
text-segment matching. The UI renders the ephemeral for liveness, then swaps to the
durable without a from-scratch re-render or a double-render (OpenHands
action/observation stream · Continue.dev). Two recipes that make "never diverge" hold:
**(a) sender-scoped deltas** — each delta is tagged with its producer so a sub-agent's
live tokens are never concatenated onto the main agent's final event (Continue.dev).
**(b) tense-template status** — each tool/action declares its state as `wouldLikeTo` /
`isCurrently` / `hasAlready` so the card's live label derives from an explicit lifecycle,
not a guessed boolean (Continue.dev). *Anti-pattern:* one mutable buffer that both
the live stream and the final authority write to (they drift; the classic reconcile
race); a frontend boolean guessing "done" instead of a durable event marking it.

### F5 — Cross-surface state as a reactive named-entity binding graph
Model cross-surface state as ONE global named-entity DataTree where any node publishes
a typed namespaced state (`entityA.field`) and any other surface references it by
`{{...}}` dot-path. Evaluation is **topologically sorted** with **first-class cycle
diagnostics** (a reference cycle is a named, surfaced error, not a stack overflow)
(Appsmith · ToolJet · Budibase binding models). *Anti-pattern:* ad-hoc prop-drilling
between surfaces; a cycle that manifests as an infinite render instead of a diagnostic.

### F6 — "One store, many projections" as a DATA MODEL, not an aspiration
Four convergent storage recipes make the Part-6 knowledge-view law concrete:
**(1) projection membership + geometry ON the node** — each block/node stores which
views it belongs to + optional `xywh`, so doc / graph / canvas are render-time FILTERS
(switching view ≠ migrating data). **(2) fractional-index ordering** — order is a
between-keys fractional index so reordering is a single-node write, not a re-index of
siblings. **(3) param-only view records** — a "view" is just a stored set of filter/
sort/group params over the shared store, not a copy. **(4) type→builder registry** —
a node's `type` maps to a renderer via a registry, so new block/view types are
additive (AFFiNE/BlockSuite · Logseq · Focalboard · Vikunja). *Anti-pattern:* a
separate table per view; integer position columns re-written on every reorder;
divergent copies per projection.

### F7 — Generative UI: sandbox + hydrate, never inject
LLM-generated (untrusted) UI code renders as a durable artifact inside a **cross-origin
sandboxed iframe**, hydrated via `postMessage`, NEVER injected into the host DOM. All
inbound events cross the message boundary; the host stays isolated from generated code.
Pair with a **select-then-refine editing loop** (select a rendered element → refine
just that region) rather than regenerating the whole surface (OpenUI). *Anti-pattern:*
`dangerouslySetInnerHTML` of model output into the app; regenerating the entire artifact
for a local edit.

### F8 — Heavy canvas: tiled invalidated raster path decoupled from the node model
When a canvas/output surface gets heavy (many artifacts/shapes), the render path is a
projection of the addressable node tree onto a **TILED, cached RASTER surface** rendered
by a dedicated engine, with a `region→node` index so only invalidated tiles re-paint.
The addressable model (selection, hit-testing, edits) stays separate from the raster
cache (Penpot). *Anti-pattern:* re-painting the whole canvas on any change; coupling
hit-testing to the raster layer.
