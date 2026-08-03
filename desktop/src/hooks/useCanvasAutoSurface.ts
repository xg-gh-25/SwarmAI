/**
 * useCanvasAutoSurface — GENTLE, flow-aware auto-surfacing of agent outputs.
 *
 * The Canvas interaction model: you talk to the session, the products fly out
 * to you — but NEVER at the cost of stealing your attention. This hook watches
 * for files the agent WRITES and opens the newest one in Canvas ONLY when you
 * are not already looking at something.
 *
 * Trigger: the unified `swarm:file-changed` (run_e626e121) with operation==='written'
 * AND relevance==='deliverable'. There is NO turn
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
  /** Whether the active tab is CURRENTLY streaming a response. This is the
   *  discriminator between a LIVE agent write (surface it) and a HISTORICAL
   *  MergedToolBlock re-dispatch on restart/remount (must NOT surface — bug1).
   *  The gate activates ONLY when this is explicitly provided (a boolean):
   *   - `isStreaming === false` → the write arrived while idle → SUPPRESS.
   *   - `isStreaming === true`  → live output → surface (subject to other guards).
   *   - `isStreaming === undefined` → gate OFF (legacy behavior preserved).
   *  When the gate is active, an ABSENT `activeSessionId` fails CLOSED (a tab
   *  whose session hasn't resolved yet — e.g. on restart — has no baseline, so
   *  we do not surface). Deliberate inverse of the legacy unstamped fail-OPEN,
   *  which only applies when the gate is off. */
  isStreaming?: boolean;
  /** Debounce window to coalesce a write-burst (default 600ms). */
  debounceMs?: number;
}

export function useCanvasAutoSurface({ pinned, muted, activeSessionId, isStreaming, debounceMs = 600 }: CanvasAutoSurfaceOptions): void {
  // Keep suppression flags in a ref so the long-lived listener reads live
  // values without re-subscribing on every flag change.
  const pinnedRef = useRef(pinned);
  pinnedRef.current = pinned;
  const mutedRef = useRef(muted);
  mutedRef.current = muted;
  const activeSessionIdRef = useRef(activeSessionId);
  activeSessionIdRef.current = activeSessionId;
  const isStreamingRef = useRef(isStreaming);
  isStreamingRef.current = isStreaming;

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
      const { path, operation, relevance, sessionId: evtSessionId } = (e as CustomEvent<{ path: string; operation: string; relevance?: string; sessionId?: string }>).detail ?? {};
      if (operation !== 'written' || !path) return;
      // WHITELIST gate (run_e626e121): only a backend-classified `deliverable`
      // auto-surfaces. `incidental` (read/grep/list) lists in the rail but never
      // pops. bookkeeping is already dropped server-side. This replaces the old
      // isBookkeepingPath() blacklist call below. Fail OPEN for an older backend
      // that doesn't send `relevance` (undefined → treat a write as deliverable,
      // preserving legacy behavior; no regression).
      if (relevance !== undefined && relevance !== 'deliverable') return;
      // Streaming gate (bug1) — checked at WRITE-ARRIVAL, not debounce-fire,
      // because a historical MergedToolBlock re-dispatch arrives while the tab
      // is idle. Active ONLY when isStreaming was explicitly provided:
      //   - not streaming → the write is not live output → SUPPRESS.
      //   - gate active + no resolved activeSessionId → fail CLOSED (restart:
      //     the tab has no session baseline yet, so don't surface history).
      // When isStreaming is undefined the gate is off (legacy behavior).
      const streamingGate = isStreamingRef.current;
      if (streamingGate !== undefined) {
        if (streamingGate === false) return;  // not live output → historical re-dispatch
        if (!activeSessionIdRef.current) return;  // gated but no session baseline → fail closed
      }
      // Tab-scope: ignore a background (keep-mounted) tab's write. Fail open
      // when the event is unstamped (evtSessionId absent) or we have no active
      // id yet — surface anyway rather than regress (legacy path, gate off).
      const activeId = activeSessionIdRef.current;
      if (evtSessionId && activeId && evtSessionId !== activeId) return;  // background tab's write
      // Local bookkeeping fallback (kept as defense-in-depth for an older backend
      // that doesn't classify relevance; the primary filter is the server-side
      // `relevance` gate above + server-side drop of bookkeeping paths).
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

    window.addEventListener('swarm:file-changed', onWritten);
    return () => {
      window.removeEventListener('swarm:file-changed', onWritten);
      if (timer) clearTimeout(timer);
    };
  }, [debounceMs]);
}
