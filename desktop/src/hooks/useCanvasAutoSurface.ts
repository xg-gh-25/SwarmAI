/**
 * useCanvasAutoSurface — GENTLE, flow-aware auto-surfacing of agent outputs.
 *
 * The Canvas interaction model: you talk to the session, the products fly out
 * to you — but NEVER at the cost of stealing your attention. This hook watches
 * for files the agent WRITES and opens the newest one in Canvas ONLY when you
 * are not already looking at something.
 *
 * Trigger: `swarm:file-referenced` with operation==='written'. There is NO turn
 * / message id on that event (verified: MergedToolBlock dispatches {path,
 * operation} only), so "first write of a turn" is unbuildable — instead we
 * DEBOUNCE: a burst of writes coalesces to a single surface of the LAST written
 * file after the stream goes quiet for `debounceMs`.
 *
 * Suppression (gentle — user intent always wins):
 *   - pinned      → user pinned the panel; never auto-replace
 *   - muted       → user muted auto-surface for this session
 *   - editorOpen  → user is actively viewing/editing a file (swarm:editor-panel-state);
 *                   don't steal the view — the output still lands in the rail,
 *                   just no auto-open.
 * Bookkeeping paths (.artifacts/, dotfiles, temp) never surface.
 *
 * On fire: dispatch `swarm:open-file` (the EXISTING open path — handled in
 * ThreeColumnLayout, drives content-adaptive view) rather than reaching into
 * the viewer's private setFileViewerFile state.
 *
 * @exports useCanvasAutoSurface
 */
import { useEffect, useRef } from 'react';
import { OPEN_FILE_EVENT } from '../components/common/MarkdownRenderer';
import { isBookkeepingPath } from '../components/file-viewer/CanvasOutputRail';

export interface CanvasAutoSurfaceOptions {
  /** User pinned the panel — never auto-replace what they're looking at. */
  pinned: boolean;
  /** User muted auto-surface for this session. */
  muted: boolean;
  /** The ACTIVE tab's session id. A file-referenced event stamped with a
   *  DIFFERENT session (a background keep-mounted tab) is ignored, so its
   *  writes don't surface in the tab you're looking at. Absent stamp → fail
   *  open (surface anyway; no regression for un-updated dispatchers). */
  activeSessionId?: string;
  /** Debounce window to coalesce a write-burst (default 600ms). */
  debounceMs?: number;
}

export function useCanvasAutoSurface({ pinned, muted, activeSessionId, debounceMs = 600 }: CanvasAutoSurfaceOptions): void {
  // Keep suppression flags in a ref so the long-lived listener reads live
  // values without re-subscribing on every flag change.
  const pinnedRef = useRef(pinned);
  pinnedRef.current = pinned;
  const mutedRef = useRef(muted);
  mutedRef.current = muted;
  const activeSessionIdRef = useRef(activeSessionId);
  activeSessionIdRef.current = activeSessionId;

  // "User is actively viewing THEIR OWN file" — the suppression signal. The
  // subtlety (Gate-2 HIGH, 2026-08-02): a panel that AUTO-SURFACE opened must
  // NOT count as "user is viewing", or the feature fires once then suppresses
  // itself forever. So we track two things and compare:
  //   - panelOpenRef      : is a file panel open at all (swarm:editor-panel-state)
  //   - currentFileRef    : which file is showing (swarm:editor-file-changed)
  //   - lastAutoOpenedRef : the file WE last auto-surfaced
  // "User is viewing their own choice" ⇔ panel open AND current file is NOT the
  // one we auto-opened. In that case we yield. If the panel shows what we put
  // there (or is closed), the next output is free to replace it.
  const panelOpenRef = useRef(false);
  const currentFileRef = useRef<string | null>(null);
  const lastAutoOpenedRef = useRef<string | null>(null);
  useEffect(() => {
    const onPanel = (e: Event) => {
      const { open } = (e as CustomEvent<{ open: boolean }>).detail ?? {};
      panelOpenRef.current = !!open;
      if (!open) {
        currentFileRef.current = null;
        lastAutoOpenedRef.current = null;
      }
    };
    const onFileChanged = (e: Event) => {
      const detail = (e as CustomEvent<{ filePath: string } | null>).detail;
      currentFileRef.current = detail?.filePath ?? null;
    };
    window.addEventListener('swarm:editor-panel-state', onPanel);
    window.addEventListener('swarm:editor-file-changed', onFileChanged);
    return () => {
      window.removeEventListener('swarm:editor-panel-state', onPanel);
      window.removeEventListener('swarm:editor-file-changed', onFileChanged);
    };
  }, []);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    let pendingPath: string | null = null;

    const onWritten = (e: Event) => {
      const { path, operation, sessionId: evtSessionId } = (e as CustomEvent<{ path: string; operation: string; sessionId?: string }>).detail ?? {};
      if (operation !== 'written' || !path) return;
      // Tab-scope: ignore a background (keep-mounted) tab's write. Fail open
      // when the event is unstamped (evtSessionId absent) or we have no active
      // id yet — surface anyway rather than regress.
      const activeId = activeSessionIdRef.current;
      if (evtSessionId && activeId && evtSessionId !== activeId) return;
      if (isBookkeepingPath(path)) return;
      // Coalesce a burst → last written path wins.
      pendingPath = path;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        timer = null;
        const target = pendingPath;
        pendingPath = null;
        if (!target) return;
        // Gentle: user pin / session mute always win.
        if (pinnedRef.current || mutedRef.current) return;
        // Yield only if the user is viewing a file THEY opened — i.e. the panel
        // is open showing something other than what we last auto-surfaced.
        // (basename compare: the open handler resolves the path, so the echoed
        //  current file may differ from the raw dispatched path by prefix.)
        if (panelOpenRef.current) {
          const cur = currentFileRef.current;
          const mine = lastAutoOpenedRef.current;
          const curBase = cur ? cur.split('/').pop() : null;
          const mineBase = mine ? mine.split('/').pop() : null;
          const viewingUserChoice = !!cur && curBase !== mineBase;
          if (viewingUserChoice) return;
        }
        lastAutoOpenedRef.current = target;
        document.dispatchEvent(new CustomEvent(OPEN_FILE_EVENT, { detail: { path: target } }));
      }, debounceMs);
    };

    document.addEventListener('swarm:file-referenced', onWritten);
    return () => {
      document.removeEventListener('swarm:file-referenced', onWritten);
      if (timer) clearTimeout(timer);
    };
  }, [debounceMs]);
}
