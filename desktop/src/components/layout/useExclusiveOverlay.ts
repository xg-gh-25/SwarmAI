/**
 * useExclusiveOverlay — shared open/close state for the A10 window-event overlays
 * (SwarmWS / Brain Hub / the domain stubs).
 *
 * Enforces the single-overlay invariant + the Chat-hero return path, fixing two
 * Gate-2 findings from run_1aab916c:
 *   A-1 (CRITICAL): the Chat hero dispatches `swarm:back-to-chat` — every overlay
 *        listens for it and closes, so the hero button actually returns to chat.
 *   F-1 (HIGH): opening one overlay must close any other. Each overlay closes when
 *        ANY `swarm:show-*` event other than its own fires — so two fullscreen
 *        overlays can never stack (double backdrop / Esc-closes-both).
 *
 * ALL_SHOW_EVENTS is the single source of truth for the domain-overlay events.
 * A new event-driven overlay must add its event here to participate in the
 * mutual-exclusion + back-to-chat contract.
 */
import { useEffect, useState, useCallback } from 'react';

export const BACK_TO_CHAT_EVENT = 'swarm:back-to-chat';

/** Every window event that opens a fullscreen domain overlay. */
export const ALL_SHOW_EVENTS = [
  'swarm:show-swarmws',
  'swarm:show-brain-hub',
  'swarm:show-context',
  'swarm:show-pipeline',
  'swarm:show-pollinate',
  'swarm:show-history',
  'swarm:show-todo',
  'swarm:show-jobs',
  'swarm:show-new-brain',
] as const;

/* ── Shared "which domain overlay is currently open" source ──────────────────
 * A SINGLE module-level value + subscriber set, so the nav cards can render an
 * active/selected highlight while their overlay is open. It is the ONE writer:
 * a show-event SETS it, and `close()` / back-to-chat / a non-window surface
 * CLEARS it — so it can never diverge from the per-overlay `open` state
 * (Gate-1 REVISE, run_ad7b32f6: close() is the reconciliation point). */
let activeOverlayEvent: string | null = null;
const activeSubscribers = new Set<(e: string | null) => void>();

function setActiveOverlayEvent(next: string | null): void {
  if (activeOverlayEvent === next) return;
  activeOverlayEvent = next;
  activeSubscribers.forEach((fn) => fn(next));
}

/** Clear the active-overlay highlight. Call when a NON-window surface takes over
 *  (Settings/Eval modal, Memory/Community file panel) so no window card stays lit. */
export function clearActiveOverlayEvent(): void {
  setActiveOverlayEvent(null);
}

/**
 * Close EVERY open window-event overlay + clear the active highlight — the single
 * cross-mechanism mutual-exclusion primitive (run_9f8b6c21).
 *
 * Why a broadcast (not a per-event `others` close): `swarm:show-library` is NOT in
 * ALL_SHOW_EVENTS (it's banned from the agent UI-action allowlist SSOT — see
 * uiCommands.ts), so a peer overlay's `others` set does NOT include Library and
 * cannot close it. But EVERY overlay (Library included) registers a `back-to-chat`
 * listener regardless of ALL_SHOW_EVENTS membership — so dispatching BACK_TO_CHAT_EVENT
 * closes ALL of them uniformly. Call this before opening a NON-overlay surface
 * (Settings/Eval modal, a file panel, the settings deep-link) OR at the overlay-open
 * chokepoint, so at most one fullscreen surface is ever open.
 *
 * dispatchEvent is synchronous: every overlay's closeSelf + clearIfMine run to
 * completion here before the caller's next line (e.g. the target overlay's own show,
 * or openModal). setActiveOverlayEvent(null) is belt-and-suspenders — each overlay's
 * clearIfMine already nulls it; the explicit clear covers the zero-overlay case.
 */
export function closeOpenOverlays(): void {
  window.dispatchEvent(new CustomEvent(BACK_TO_CHAT_EVENT));
  setActiveOverlayEvent(null);
}

/** Subscribe a component to "which window overlay is active" (or null). */
export function useActiveOverlayEvent(): string | null {
  // Seed from the current module value to avoid a first-render flicker.
  const [active, setActive] = useState<string | null>(activeOverlayEvent);
  useEffect(() => {
    activeSubscribers.add(setActive);
    setActive(activeOverlayEvent); // resync in case it changed before subscribe
    return () => {
      activeSubscribers.delete(setActive);
    };
  }, []);
  return active;
}

/** Test-only: reset module singletons (module state leaks across a file's tests). */
export function __resetActiveOverlayEvent(): void {
  activeOverlayEvent = null;
  activeSubscribers.clear();
}

export function useExclusiveOverlay(myEvent: string): {
  open: boolean;
  close: () => void;
} {
  const [open, setOpen] = useState(false);
  // close() is the reconciliation point: it clears BOTH the local open state and
  // the shared activeEvent, so Esc / backdrop / file-open (which fire no window
  // event) can never leave a stale card highlight (Gate-1 Finding 1/3).
  const close = useCallback(() => {
    setOpen(false);
    if (activeOverlayEvent === myEvent) setActiveOverlayEvent(null);
  }, [myEvent]);

  useEffect(() => {
    const show = () => {
      setOpen(true);
      setActiveOverlayEvent(myEvent);
    };
    const closeSelf = () => setOpen(false);
    const clearIfMine = () => {
      if (activeOverlayEvent === myEvent) setActiveOverlayEvent(null);
    };

    window.addEventListener(myEvent, show);
    window.addEventListener(BACK_TO_CHAT_EVENT, closeSelf);
    window.addEventListener(BACK_TO_CHAT_EVENT, clearIfMine);
    // Any OTHER domain-overlay show-event closes this one (single-overlay invariant).
    // The OTHER event's own `show` handler sets activeEvent to itself, so we only
    // clear local open here — activeEvent is already re-pointed by the opener.
    const others = ALL_SHOW_EVENTS.filter((e) => e !== myEvent);
    others.forEach((e) => window.addEventListener(e, closeSelf));

    return () => {
      window.removeEventListener(myEvent, show);
      window.removeEventListener(BACK_TO_CHAT_EVENT, closeSelf);
      window.removeEventListener(BACK_TO_CHAT_EVENT, clearIfMine);
      others.forEach((e) => window.removeEventListener(e, closeSelf));
    };
  }, [myEvent]);

  return { open, close };
}
