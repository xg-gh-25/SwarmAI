/**
 * OverlayContext — the single `activeOverlay` state authority for the OverlayHost
 * subsystem (run_fdeaead8, design: Knowledge/Designs/2026-08-04-overlayhost-subsystem-design.md).
 *
 * This is the ONE state machine that will (across the strangler-fig migration M1–M5)
 * replace the legacy trio — the `useExclusiveOverlay` window-event bus, the
 * `activeOverlayEvent` module singleton, and `useLayout().activeModal` — for every
 * fullscreen surface. In M1 it holds ZERO registered surfaces: it introduces the
 * state + the hybrid bridge with no behavior change (the old paths still drive
 * every existing overlay).
 *
 * ── The show-event bridge (afferent OPEN) + back-to-chat (close) ─────────────────
 * The legacy `swarm:show-<id>` window events are NOT just a migration artifact — they
 * are the agent's ACT vocabulary: `ui_action` (backend UI_COMMAND_ALLOWLIST, derived
 * from ALL_SHOW_EVENTS) dispatches `swarm:show-<id>` to open a surface (SELF.md
 * proprioception contract). Now that EVERY ALL_SHOW_EVENTS surface is registered in
 * the OverlayHost, this context is the sole opener: a `swarm:show-<id>` event maps to
 * `openOverlay(<id>)` (strip the `swarm:show-` prefix → registry id). This keeps the
 * agent ACT contract intact through M5 (when the legacy `useExclusiveOverlay` HOOK +
 * module singleton are deleted, the event NAMES survive as the command vocabulary and
 * this bridge remains their only consumer).
 *   • agent/card dispatch `swarm:show-<id>` → openOverlay(<id>)  [afferent OPEN]
 *   • openOverlay itself dispatches BACK_TO_CHAT_EVENT           [efferent close-legacy]
 *   • BACK_TO_CHAT_EVENT (not our own) → null activeOverlay      [close]
 * Net: exactly one fullscreen surface open at any time; the agent can open every
 * non-Library surface by its show-event (Library is deliberately absent from
 * ALL_SHOW_EVENTS — nav-card-only, banned from the agent allowlist).
 */
import { createContext, useContext, useState, useCallback, useMemo, useEffect, useRef, ReactNode } from 'react';
import { BACK_TO_CHAT_EVENT, ALL_SHOW_EVENTS } from '../components/layout/useExclusiveOverlay';
import type { OverlayId } from '../components/layout/overlayIds';

/** `swarm:show-brain-hub` → `brain-hub`. The registry id is the event suffix; the
 *  events⊆ids invariant is test-enforced (overlayIds.test.ts), so every
 *  ALL_SHOW_EVENTS suffix is a real registered OverlayId — the sliced id below needs
 *  no runtime guard (its listeners bind only to ALL_SHOW_EVENTS; a guard would be
 *  dead code for an impossible state, PIT77). */
const SHOW_EVENT_PREFIX = 'swarm:show-';

/** Re-exported from the overlayIds SSOT so existing `import { OverlayId } from
 *  '../contexts/OverlayContext'` sites keep compiling. The union (not `string`) means
 *  a typo'd openOverlay(<id>) is now a COMPILE ERROR, not a silent null-render. */
export type { OverlayId };

interface OverlayContextValue {
  /** The single currently-open surface, or null. At most one — the whole point. */
  activeOverlay: OverlayId | null;
  /** Open a surface. Closes any legacy overlay first (hybrid bridge). Idempotent. */
  openOverlay: (id: OverlayId) => void;
  /** Close the active surface (if any). */
  closeOverlay: () => void;
  /** The current agent scope (ChatPage.selectedAgentId), published REACTIVELY here so
   *  an OPEN overlay (History's ['chatSessions', agentId] query) re-renders when the
   *  agent switches while it's open. This is the ONE reactive field that could NOT ride
   *  the non-reactive module `_bridge` (overlayRegistry): the bridge is read at host
   *  render time and the host only re-renders on activeOverlay change, so a bridged
   *  agentId went stale when the delete-agent-fallback effect (ChatPage) fired with
   *  History open (Gate-2 MED, run_06c49540). ChatPage writes it via setAgentId; the
   *  ref bridge still carries the ref-backed dispatch handlers (never stale). */
  agentId: string | null;
  /** ChatPage publishes its live selectedAgentId here (reactive). */
  setAgentId: (id: string | null) => void;
}

const OverlayContext = createContext<OverlayContextValue | undefined>(undefined);

interface OverlayProviderProps {
  children: ReactNode;
}

export function OverlayProvider({ children }: OverlayProviderProps) {
  const [activeOverlay, setActiveOverlay] = useState<OverlayId | null>(null);
  const [agentId, setAgentId] = useState<string | null>(null);

  // A guard so the BACK_TO_CHAT_EVENT we dispatch when opening a new-host overlay
  // does not immediately null the very overlay we are opening (our own broadcast
  // must close LEGACY overlays, not this context). Ref, not state — read/written
  // synchronously within one open() call, never triggers a render.
  const openingSelf = useRef(false);

  const openOverlay = useCallback((id: OverlayId) => {
    // Close any still-mounted legacy overlay (SwarmWSOverlay/BrainHubDemoOverlay etc.,
    // pending M5 deletion) before we open. Our OWN show-event/back-to-chat listeners
    // below would otherwise re-enter — guard them for this synchronous tick.
    // No try/finally needed: per the DOM spec, dispatchEvent does NOT rethrow a
    // listener's exception to the caller (it reports to window.onerror and continues),
    // so `= false` always runs. Verified by mutation test (run_fdeaead8): behavior is
    // byte-identical with/without a guard, and a throwing legacy listener does not
    // wedge the flag — so a guard would be defensive code for an impossible state.
    openingSelf.current = true;
    window.dispatchEvent(new CustomEvent(BACK_TO_CHAT_EVENT));
    openingSelf.current = false;
    setActiveOverlay(id);
  }, []);

  const closeOverlay = useCallback(() => {
    setActiveOverlay(null);
  }, []);

  // Show-event bridge: a `swarm:show-<id>` window event (from a nav card OR the agent's
  // ui_action) OPENS the mapped surface — this context is the sole opener now that every
  // ALL_SHOW_EVENTS surface is registered. BACK_TO_CHAT closes. `openingSelf` filters
  // the BACK_TO_CHAT that openOverlay itself fires (targets legacy overlays only).
  useEffect(() => {
    const openFromShow = (e: Event) => {
      // 'swarm:show-todo' → 'todo'. The cast is sound WITHOUT a runtime guard: these
      // listeners bind ONLY to ALL_SHOW_EVENTS (below), and overlayIds.test.ts
      // asserts every ALL_SHOW_EVENTS suffix ∈ OVERLAY_IDS — so this suffix is always
      // a registered OverlayId. A runtime isOverlayId() here would be dead code for an
      // impossible state (PIT77); the test is the guard.
      const id = e.type.slice(SHOW_EVENT_PREFIX.length) as OverlayId;
      // Reuse openOverlay so the efferent close-legacy broadcast + guard run uniformly.
      openOverlay(id);
    };
    const closeFromBackToChat = () => {
      // Ignore the broadcast our OWN openOverlay just fired (it targets legacy only).
      if (openingSelf.current) return;
      setActiveOverlay(null);
    };
    ALL_SHOW_EVENTS.forEach((ev) => window.addEventListener(ev, openFromShow));
    window.addEventListener(BACK_TO_CHAT_EVENT, closeFromBackToChat);
    return () => {
      ALL_SHOW_EVENTS.forEach((ev) => window.removeEventListener(ev, openFromShow));
      window.removeEventListener(BACK_TO_CHAT_EVENT, closeFromBackToChat);
    };
  }, [openOverlay]);

  const value = useMemo<OverlayContextValue>(
    () => ({ activeOverlay, openOverlay, closeOverlay, agentId, setAgentId }),
    [activeOverlay, openOverlay, closeOverlay, agentId],
  );

  return <OverlayContext.Provider value={value}>{children}</OverlayContext.Provider>;
}

/** Access the single-overlay state authority. Throws if used outside the provider. */
export function useOverlay(): OverlayContextValue {
  const ctx = useContext(OverlayContext);
  if (ctx === undefined) {
    throw new Error('useOverlay must be used within an OverlayProvider');
  }
  return ctx;
}
