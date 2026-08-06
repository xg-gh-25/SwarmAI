/**
 * useOverlayDraft — an in-memory, per-overlay form-draft store.
 *
 * WHY: a fullscreen overlay surface UNMOUNTS on close (OverlayHost sets
 * `renderedId → null` after the exit transition), so any component-local `useState`
 * in the surface is destroyed — reopening starts blank. For a data-entry surface
 * (NewBrain: name / governs / starter items / draft) that "fresh birth" silently
 * discards work when the user closes by accident. This hook parks the form snapshot
 * in a MODULE-LEVEL Map keyed by overlayId, so a reopen restores exactly what the
 * user had. `clear()` is called ONLY when the work is dispatched (landed) — Esc,
 * backdrop click, and a failed dispatch all PRESERVE the draft.
 *
 * IN-MEMORY ONLY — deliberately NOT localStorage. NewBrain starter items are local
 * ABSOLUTE filesystem paths; persisting them to disk would leak them across app
 * restarts and onto the disk. A module Map lives only for the current app process
 * (cleared on restart), which is the right retention: "don't lose it if I reopen
 * this session," not "remember my paths forever."
 *
 * Same shape as useState (`[value, setValue]`) plus a third `clear` element.
 * Generic so Jobs / Pollinate creation forms can reuse it.
 *
 * @exports useOverlayDraft
 */
import { useCallback, useState } from 'react';

/** Module-level store — survives surface unmount, lives for the app process only. */
const _drafts = new Map<string, unknown>();

/** TEST-ONLY: wipe the module store so a test's parked draft can't leak into the
 *  next test (the store is intentionally process-lived, which crosses test cases).
 *  Not for production use — production clears via the hook's clear() on dispatch. */
export function __resetOverlayDraftsForTest(): void {
  _drafts.clear();
}

/**
 * @param overlayId stable key (the overlay's id, e.g. 'new-brain')
 * @param initial   the empty-form value used when no draft is parked
 * @returns [value, setValue, clear] — clear() wipes the parked draft so the next
 *          mount starts from `initial`. Call clear() ONLY on successful dispatch.
 */
export function useOverlayDraft<T>(
  overlayId: string,
  initial: T,
): [T, (next: T | ((prev: T) => T)) => void, () => void] {
  // Seed from the parked draft if present, else the initial value. Read lazily so
  // a remount picks up whatever the previous mount left in the Map.
  const [value, setValueState] = useState<T>(() =>
    _drafts.has(overlayId) ? (_drafts.get(overlayId) as T) : initial,
  );

  // Accepts a value OR a functional updater (like useState) so callers doing
  // several sequential updates in one tick (e.g. addItem per line of a paste)
  // don't lose all-but-last to a stale closure. The Map is written from inside
  // the state updater so it always sees the latest value.
  const setValue = useCallback(
    (next: T | ((prev: T) => T)) => {
      setValueState((prev) => {
        const resolved = typeof next === 'function' ? (next as (p: T) => T)(prev) : next;
        _drafts.set(overlayId, resolved);
        return resolved;
      });
    },
    [overlayId],
  );

  const clear = useCallback(() => {
    _drafts.delete(overlayId);
    setValueState(initial);
    // `initial` intentionally excluded from deps: a caller that passes a fresh
    // object literal each render would otherwise churn this callback identity.
    // The value used at clear-time is the mount's initial, which is what we want.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [overlayId]);

  return [value, setValue, clear];
}
