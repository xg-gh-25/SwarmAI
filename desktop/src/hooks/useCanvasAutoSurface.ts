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

export interface CanvasAutoSurfaceOptions {
  /** User pinned the panel — never auto-replace what they're looking at. */
  pinned: boolean;
  /** User muted auto-surface for this session. */
  muted: boolean;
  /** The ACTIVE tab id — the tab-scope key (run_26aa6caa). A file-changed event
   *  stamped with a DIFFERENT tabId (a background keep-mounted tab) is ignored, so
   *  its writes don't auto-pop in the tab you're looking at. tabId is stable and
   *  always present, so the old "unstamped → fail open → cross-tab bleed" window is
   *  gone. Absent stamp → fail open (older-backend migration only). */
  activeTabId?: string;
  /** The ACTIVE tab's session id — kept ONLY for the fire-time restart fail-closed
   *  (a streaming-gated write whose session never resolves is a restart-history
   *  replay; suppress it). This is a SEPARATE concern from tab-scoping (now on
   *  activeTabId) — do NOT fold the two; the session-resolved signal is the restart
   *  discriminator, unchanged from run_5a7be540. */
  activeSessionId?: string;
  /** Whether the active tab is CURRENTLY streaming a response.
   *  CONTRACT (Gate-2, run_5a7be540): this MUST be kept in sync with the real SDK
   *  streaming state by the caller (ChatPage wires it from useUnifiedTabState's
   *  setIsStreaming, driven by the SSE start/stop events). The bug1 historical-replay
   *  suppression depends on it: a stale MergedToolBlock re-dispatch arrives with
   *  isStreaming===false and is dropped AT ARRIVAL (below). If a caller passed a
   *  desynced/stale `true` during a replay, that suppression would weaken — so the
   *  boolean's freshness is load-bearing, not advisory. This is the
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

export function useCanvasAutoSurface({ pinned, muted, activeTabId, activeSessionId, isStreaming, debounceMs = 600 }: CanvasAutoSurfaceOptions): void {
  // Keep suppression flags in a ref so the long-lived listener reads live
  // values without re-subscribing on every flag change.
  const pinnedRef = useRef(pinned);
  pinnedRef.current = pinned;
  const mutedRef = useRef(muted);
  mutedRef.current = muted;
  const activeTabIdRef = useRef(activeTabId);
  activeTabIdRef.current = activeTabId;
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
    // The origin tab of the pending write (the event's own stamp). Captured at
    // ARRIVAL and stamped onto the dispatched open-file so useCanvasHost lands the
    // file on the tab that PRODUCED it — immune to a tab switch during the debounce
    // window (the run_48a29fc2 origin-tab class). Null when the event is unstamped
    // (older backend) → the dispatch omits tabId → useCanvasHost falls back to active.
    let pendingTabId: string | undefined;

    const onWritten = (e: Event) => {
      const { path, operation, relevance, kind, tabId: evtTabId } = (e as CustomEvent<{ path: string; operation: string; relevance?: string; kind?: string; tabId?: string }>).detail ?? {};
      if (operation !== 'written' || !path) return;
      // Unified-verdict gate (PRIMARY): content|knowledge|source-final auto-pop.
      // `source-final` (run_d3cc1f2c — Option A, reversing run_b8ea6d5c): the
      // pipeline-finish coding batch NOW auto-pops (Canvas opens on the last changed
      // file), subject to the SAME gentle suppression as content/knowledge below
      // (pin/mute/user-viewing) — NOT a bypass. XG: N files = list + 1 render, no
      // hijack/perf concern justified compromising the auto-pop behavior.
      // `source` = a MID-run coding edit — still never pops (noise while working).
      // `process` is machine noise. Undefined kind (older backend) → fall through to
      // the legacy `relevance` gate below (no regression).
      if (kind !== undefined && kind !== 'content' && kind !== 'knowledge' && kind !== 'source-final') return;
      // WHITELIST gate (run_e626e121, legacy/migration): only a backend-classified
      // `deliverable` auto-surfaces. Fail OPEN for an older backend that doesn't send
      // `relevance` (undefined → treat a write as deliverable, no regression).
      if (relevance !== undefined && relevance !== 'deliverable') return;
      // Streaming gate (bug1) — the isStreaming half is checked at WRITE-ARRIVAL,
      // because a historical MergedToolBlock re-dispatch arrives while the tab is
      // idle (isStreaming===false) and must be suppressed at the source. Active
      // ONLY when isStreaming was explicitly provided:
      //   - not streaming → the write is not live output → SUPPRESS (at arrival).
      // When isStreaming is undefined the gate is off (legacy behavior).
      // NOTE: the `!activeSessionId` fail-closed check moved to DEBOUNCE-FIRE (G3,
      // run_5a7be540) — a LIVE write during the brief session-resolving startup
      // window used to be dropped at arrival; now the debounce HOLDS it and the
      // session-baseline check runs at fire time, so a session that resolves within
      // the window surfaces the write. A session that NEVER resolves still fails
      // closed (checked below), preserving the restart-history suppression.
      const streamingGate = isStreamingRef.current;
      if (streamingGate === false) return;  // not live output → historical re-dispatch
      // Tab-scope (run_26aa6caa): ignore a background (keep-mounted) tab's write.
      // Keyed on tabId now (was sessionId) — stable + always-stamped, so a live
      // write is never mis-scoped. Fail open only when truly unstamped (evtTabId
      // absent — older backend) or no active tab id yet.
      const activeTab = activeTabIdRef.current;
      if (evtTabId && activeTab && evtTabId !== activeTab) return;  // background tab's write
      // run_4de279ca: the frontend isBookkeepingPath fallback is REMOVED. The
      // backend git verdict (`kind`, gated above at the PRIMARY check) is the sole
      // authority — process/source are never emitted for live surfacing, so a
      // second frontend denylist is redundant (and drifted from the real boundary).
      // Coalesce a burst → last written path wins. Its origin tab travels with it.
      pendingPath = path;
      pendingTabId = evtTabId;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        timer = null;
        const target = pendingPath;
        const targetTabId = pendingTabId;
        pendingPath = null;
        pendingTabId = undefined;
        if (!target) return;
        // Streaming-gate fail-closed, re-checked HERE at fire time (G3): if the gate
        // is active (isStreaming provided) but the tab STILL has no resolved session,
        // suppress — a genuine restart-history replay never gets a baseline, so it
        // stays closed. A live write whose session resolved during the debounce window
        // now passes (the window's transient undefined is gone by fire time).
        if (isStreamingRef.current !== undefined && !activeSessionIdRef.current) return;
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
        // Stamp the write's ORIGIN tab (run_d3cc1f2c edit3) so useCanvasHost lands the
        // file on the producing tab — closes the debounce-window tab-switch gap
        // (run_48a29fc2 class). Omit when unstamped → useCanvasHost falls back to active.
        document.dispatchEvent(new CustomEvent(OPEN_FILE_EVENT, {
          detail: targetTabId ? { path: target, tabId: targetTabId } : { path: target },
        }));
      }, debounceMs);
    };

    window.addEventListener('swarm:file-changed', onWritten);
    return () => {
      window.removeEventListener('swarm:file-changed', onWritten);
      if (timer) clearTimeout(timer);
    };
  }, [debounceMs]);
}
