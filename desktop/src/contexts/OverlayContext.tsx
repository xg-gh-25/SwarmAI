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
 * ── The two-way hybrid bridge (Gate-1 WARN5, run_fdeaead8) ───────────────────────
 * During M2–M4 the app runs a HYBRID: some surfaces already migrated to this context,
 * the rest still on the legacy `useExclusiveOverlay` bus. Without a bridge BOTH could
 * be open at once (new `activeOverlay` non-null AND a legacy overlay open) → double
 * backdrop / double-Esc / the single-overlay invariant broken mid-migration. The
 * bridge enforces mutual exclusion ACROSS the two systems:
 *   • opening a NEW-host overlay → dispatch BACK_TO_CHAT_EVENT (closes ALL legacy
 *     overlays; every legacy overlay already listens for it — useExclusiveOverlay.ts).
 *   • any LEGACY show-event (ALL_SHOW_EVENTS) OR a BACK_TO_CHAT broadcast → null this
 *     context (closes the new-host overlay).
 * Net: at most one fullscreen surface — legacy OR new — is ever open, at every step
 * of the migration. When M5 deletes the legacy trio, the bridge listeners become
 * inert no-ops (no ALL_SHOW_EVENTS ever fire) and are removed with the same commit.
 */
import { createContext, useContext, useState, useCallback, useMemo, useEffect, useRef, ReactNode } from 'react';
import { BACK_TO_CHAT_EVENT, ALL_SHOW_EVENTS } from '../components/layout/useExclusiveOverlay';

/**
 * The id of a registered fullscreen surface. Open-ended `string` in M1 (the registry
 * that constrains it lands in M2); a nullable slot = "which single surface is open".
 */
export type OverlayId = string;

interface OverlayContextValue {
  /** The single currently-open surface, or null. At most one — the whole point. */
  activeOverlay: OverlayId | null;
  /** Open a surface. Closes any legacy overlay first (hybrid bridge). Idempotent. */
  openOverlay: (id: OverlayId) => void;
  /** Close the active surface (if any). */
  closeOverlay: () => void;
}

const OverlayContext = createContext<OverlayContextValue | undefined>(undefined);

interface OverlayProviderProps {
  children: ReactNode;
}

export function OverlayProvider({ children }: OverlayProviderProps) {
  const [activeOverlay, setActiveOverlay] = useState<OverlayId | null>(null);

  // A guard so the BACK_TO_CHAT_EVENT we dispatch when opening a new-host overlay
  // does not immediately null the very overlay we are opening (our own broadcast
  // must close LEGACY overlays, not this context). Ref, not state — read/written
  // synchronously within one open() call, never triggers a render.
  const openingSelf = useRef(false);

  const openOverlay = useCallback((id: OverlayId) => {
    // Hybrid bridge (efferent): close every legacy overlay before we open. Our own
    // BACK_TO_CHAT listener below would otherwise null us — guard it for this tick.
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

  // Hybrid bridge (afferent): a legacy overlay opening (any ALL_SHOW_EVENTS) OR a
  // back-to-chat broadcast NOT originating from our own open() must close us.
  useEffect(() => {
    const closeFromLegacy = () => setActiveOverlay(null);
    const closeFromBackToChat = () => {
      // Ignore the broadcast our OWN openOverlay just fired (it targets legacy only).
      if (openingSelf.current) return;
      setActiveOverlay(null);
    };
    ALL_SHOW_EVENTS.forEach((e) => window.addEventListener(e, closeFromLegacy));
    window.addEventListener(BACK_TO_CHAT_EVENT, closeFromBackToChat);
    return () => {
      ALL_SHOW_EVENTS.forEach((e) => window.removeEventListener(e, closeFromLegacy));
      window.removeEventListener(BACK_TO_CHAT_EVENT, closeFromBackToChat);
    };
  }, []);

  const value = useMemo<OverlayContextValue>(
    () => ({ activeOverlay, openOverlay, closeOverlay }),
    [activeOverlay, openOverlay, closeOverlay],
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
