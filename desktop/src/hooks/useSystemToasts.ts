/**
 * System-level toasts raised from the chat page — the ONLY ones it may raise.
 *
 * The top-right toast stack is for events no single chat tab owns. On the chat
 * page that is exactly the case where opening or landing in a tab FAILED, so
 * there is no tab to show the message in yet. Everything that belongs to a tab
 * or session goes to that tab's notices (stores/tabNoticeStore) instead, and
 * `__tests__/noTabScopedToasts.test.ts` forbids `addToast(` in ChatPage.tsx and
 * pages/chat/** — a new system-level case is added here, by name.
 *
 * Key exports:
 * - `useSystemToasts` — returns stable named raisers:
 *   - `tabLimitReached()`      — new tab refused: tab cap under memory pressure
 *   - `allTabsBusy()`          — no tab free to open a session / dispatch in
 *   - `promptCannotLand(msg)`  — a prompt could not be placed in any tab (msg
 *                                explains which tab blocked it and what to do)
 */
import { useMemo } from 'react';
import { useToast } from '../contexts/ToastContext';

export interface SystemToasts {
  tabLimitReached: () => void;
  allTabsBusy: () => void;
  promptCannotLand: (message: string) => void;
}

export function useSystemToasts(): SystemToasts {
  const { addToast } = useToast();
  return useMemo(() => ({
    tabLimitReached: () => addToast({
      severity: 'info',
      message: 'Memory usage is high. Close an idle tab or quit other apps to free memory, then try again.',
      autoDismiss: true,
    }),
    allTabsBusy: () => addToast({
      severity: 'warning',
      message: 'Could not open a new tab — all tabs are busy.',
      autoDismiss: true,
    }),
    promptCannotLand: (message: string) => addToast({ severity: 'warning', message, autoDismiss: true }),
  }), [addToast]);
}
