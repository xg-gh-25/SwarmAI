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
] as const;

export function useExclusiveOverlay(myEvent: string): {
  open: boolean;
  close: () => void;
} {
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    const show = () => setOpen(true);
    const closeSelf = () => setOpen(false);

    window.addEventListener(myEvent, show);
    window.addEventListener(BACK_TO_CHAT_EVENT, closeSelf);
    // Any OTHER domain-overlay show-event closes this one (single-overlay invariant).
    const others = ALL_SHOW_EVENTS.filter((e) => e !== myEvent);
    others.forEach((e) => window.addEventListener(e, closeSelf));

    return () => {
      window.removeEventListener(myEvent, show);
      window.removeEventListener(BACK_TO_CHAT_EVENT, closeSelf);
      others.forEach((e) => window.removeEventListener(e, closeSelf));
    };
  }, [myEvent]);

  return { open, close };
}
