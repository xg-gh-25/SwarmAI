/**
 * overlayRegistry — the single declarative source of truth for every fullscreen
 * surface (OverlayHost subsystem, design 2026-08-04). Each surface is a DATA entry,
 * not a bespoke mounted component: id + chrome params + a `render(ctx)` that returns
 * the CONTENT only. The OverlayHost provides the shell (scrim, panel, spout, header,
 * Esc, scroll-lock, geometry); the registry provides "what's inside + where it spouts
 * from".
 *
 * This kills D1 (one host, not 11 split mounts) + D2 (one `activeOverlay` enum, not a
 * window-event bus + singletons + activeModal) + D4 (spout source is `sourceCardId`
 * DATA, re-derived at open time — no mutable navSource singleton to remember to clear).
 *
 * All surfaces are registered (M5 complete). `OverlayId` is the closed union defined
 * in the `overlayIds` SSOT — `registerOverlay`'s `spec.id: OverlayId` type-binds every
 * registration to that tuple (an unlisted id is a compile error), and
 * overlayIds.test.ts asserts registeredOverlayIds() === OVERLAY_IDS both directions.
 */
import type { ReactNode } from 'react';
import type { OverlayId } from './overlayIds';

/** Handlers the host injects into a surface's `render`. `close` is host-owned and
 *  always present. `dispatch*` are ChatPage-owned tab operations (dispatch is a
 *  ChatPage responsibility — it owns the tab store; the host reaches them via a
 *  controlled bridge, run_fdeaead8 decision B). They are `undefined` until ChatPage
 *  mounts the bridge, so a render fn must null-guard them (a workbench surface only
 *  ever opens while ChatPage is mounted, so in practice they are present at open). */
export interface OverlayRenderCtx {
  /** Close the active overlay (the host's closeOverlay). */
  close: () => void;
  /** Land a prompt in a chat tab (ChatPage.handleDispatchJobPrompt). Returns true if
   *  it landed, false if all tabs busy / draft blocks it. undefined pre-bridge. */
  dispatchPrompt?: (prompt: string) => boolean;
  /** Land a ToDo work-packet in a chat tab (ChatPage.handleDispatchTodo). */
  dispatchTodo?: (todo: unknown) => boolean;
  /** Open a workspace file in the Canvas (ChatPage useCanvasHost / swarm:open-file). */
  openFile?: (file: unknown, autoDiff?: boolean) => void;
  /** Resume a session in a chat tab (ChatPage.handleResumeSession). Returns true if
   *  it landed. Session-list DATA is NOT bridged — History self-fetches it from the
   *  shared TanStack Query cache so it stays reactive (a ref-bridge would go stale). */
  resumeSession?: (session: unknown) => boolean;
  /** Delete a session (ChatPage's delete-confirm flow). */
  deleteSession?: (session: unknown) => void;
  /** Current agent scope (ChatPage.selectedAgentId) — History's session query key.
   *  Sourced by the host from OverlayContext (REACTIVE), NOT the module bridge: an open
   *  History overlay re-renders + re-queries when the agent switches while open
   *  (run_06c49540). ChatPage publishes it via OverlayContext.setAgentId. */
  agentId?: string | null;
}

/**
 * The bridge that carries ChatPage-owned handlers to host-rendered content
 * (decision B, RefreshTreeBridge precedent). ChatPage writes the live closures into
 * this module-level ref on every render; OverlayHost reads them when building the ctx.
 * A ref (not context) so ChatPage's high-frequency re-renders never re-render the host.
 */
export interface OverlayCtxBridge {
  dispatchPrompt?: (prompt: string) => boolean;
  dispatchTodo?: (todo: unknown) => boolean;
  openFile?: (file: unknown, autoDiff?: boolean) => void;
  resumeSession?: (session: unknown) => boolean;
  deleteSession?: (session: unknown) => void;
}
const _bridge: OverlayCtxBridge = {};

/** ChatPage calls this to publish its live tab-dispatch closures. These are all
 *  ref-backed useCallbacks (stable identity, never stale), so a non-reactive module
 *  ref is the correct channel — the host reads them at render time and they hold live
 *  values. The one REACTIVE field (agentId) does NOT ride this bridge: it lives in
 *  OverlayContext so an open overlay re-renders on an agent switch (run_06c49540). */
export function setOverlayCtxBridge(b: OverlayCtxBridge): void {
  _bridge.dispatchPrompt = b.dispatchPrompt;
  _bridge.dispatchTodo = b.dispatchTodo;
  _bridge.openFile = b.openFile;
  _bridge.resumeSession = b.resumeSession;
  _bridge.deleteSession = b.deleteSession;
}

/** OverlayHost reads the current bridge handlers when assembling a render ctx. */
export function getOverlayCtxBridge(): OverlayCtxBridge {
  return _bridge;
}

export interface OverlaySpec {
  /** Stable id — the value stored in `activeOverlay` + the key nav cards open. */
  id: OverlayId;
  /** Header title (fullscreen chrome). */
  title: string;
  /** Mono mode badge in the header (e.g. "BRAIN", "EVAL"). */
  mode?: string;
  /** Content-adaptive width tier — same contract as the old Modal.fullscreenWidth. */
  width?: 's' | 'm' | 'l' | 'xl';
  /** Height mode: false (default) = fill the chat-area height (definite height for
   *  full-height flex children); true = shrink-to-content, clamped. */
  autoHeight?: boolean;
  /** `data-testid` on the LeftNav card this surface spouts from — the host re-derives
   *  the card's live rect at open time (replaces the navSource singleton push). When
   *  absent, the panel opens with no spout (e.g. deep-link / non-card open). */
  sourceCardTestId?: string;
  /** Region tint (hex) for the panel accent (border/ring/spout/header underline).
   *  Falls back to --color-primary when omitted. */
  tint?: string;
  /** The CONTENT only — the host wraps it in scrim + panel + header chrome. */
  render: (ctx: OverlayRenderCtx) => ReactNode;
}

const _registry = new Map<OverlayId, OverlaySpec>();

/** Register a surface. Idempotent per id (last registration wins — hot-reload safe). */
export function registerOverlay(spec: OverlaySpec): void {
  _registry.set(spec.id, spec);
}

/** Look up a registered surface (undefined if not registered — host renders nothing). */
export function getOverlaySpec(id: OverlayId | null): OverlaySpec | undefined {
  return id == null ? undefined : _registry.get(id);
}

/** All registered ids (for tests / debugging). */
export function registeredOverlayIds(): OverlayId[] {
  return [..._registry.keys()];
}
