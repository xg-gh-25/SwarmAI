/**
 * useCanvasHost — per-tab Canvas state + host wiring for the ChatPage-owned
 * Canvas (FileViewerPanel).
 *
 * WHY THIS EXISTS (bugs 2 + 3): Canvas used to live in ThreeColumnLayout as a
 * single global useState set, RESET (not restored) on tab switch, and mounted as
 * a full-height sibling of the chat column — so it sat beside the tab bar and
 * lost its state every switch. This hook moves Canvas ownership to ChatPage (the
 * owner of tabs), keyed PER TAB, so:
 *   - each tab remembers its own open file + pin/mute/manuallyOpen (mirrors the
 *     existing per-tab `inputValueMapRef` pattern — a plain useRef Map that NEVER
 *     flows into MessageStore/reconcile, so zero OT01 risk). (Expanded is a
 *     transient panel-local UI state, deliberately NOT per-tab.)
 *   - ChatPage renders <FileViewerPanel> BELOW ChatHeader, so Canvas visibly
 *     belongs to the current tab.
 *
 * EVENT-TARGET CONTRACT (Gate-1 Risk 4 — do NOT change targets):
 *   - `swarm:open-file`          → listened on **document** (all dispatchers use
 *                                   document: MarkdownRenderer, Library/CMBrain
 *                                   overlays, OpenFileButton, briefing, auto-surface).
 *   - `swarm:open-canvas`        → listened on **window**.
 *   - `swarm:editor-file-changed`/`swarm:canvas-state`/`swarm:editor-panel-state`
 *                                → emitted on **window** (ChatPage + auto-surface listen there).
 * Fullscreen (FileEditorModal) stays in ThreeColumnLayout; when the user asks to
 * fullscreen a Canvas file we re-dispatch `swarm:open-file-fullscreen` (window)
 * which ThreeColumnLayout owns — nothing about the modal moves here.
 *
 * @exports useCanvasHost
 */
import { useState, useRef, useCallback, useEffect } from 'react';
import api from '../services/api';
import { useCanvasAutoSurface } from './useCanvasAutoSurface';
import { useReferencedFiles, countOutputs, type GroupedReferencedFiles } from './useReferencedFiles';
import type { GitStatus } from '../types';
import type { CanvasSnapshot } from '../utils/uiContext';

export interface CanvasFile {
  filePath: string;
  fileName: string;
  gitStatus?: GitStatus;
  workspaceId?: string;
  autoDiff?: boolean;
  /** The git ref to diff the file AGAINST instead of the default HEAD (run_b8ea6d5c
   *  reserved it; run_030dc98e WIRED it). A source-final row carries `<sha>^` (the
   *  pre-run parent of the commit that introduced this run's change), so a
   *  just-committed file's diff baseline is its pre-run state, not HEAD (== working
   *  tree → empty). FileViewer passes it to /workspace/file/committed?ref=. Absent for
   *  content/knowledge rows → HEAD (correct, still uncommitted). */
  baseRef?: string;
}

interface CanvasTabState {
  file: CanvasFile | null;
  pinned: boolean;
  muted: boolean;
  manuallyOpen: boolean;
  /** The outputCount value at the last time the user had this tab's Canvas OPEN
   *  (run_9dd59523). The ChatHeader pill shows only when outputCount > this — so once
   *  the user opens Canvas and reviews, the pill hides and does NOT re-nag for the
   *  SAME outputs; it reappears only when a NEW output pushes outputCount higher.
   *  Per-tab (in the slice) so opening tab A's Canvas never marks tab B's outputs seen.
   *  In-memory per session (NOT persisted) — outputCount itself is session-scoped, and a
   *  reload legitimately re-surfaces "here's what this tab produced" (lastSeen resets to 0). */
  lastSeenOutputCount: number;
}

const EMPTY: CanvasTabState = {
  file: null,
  pinned: false,
  muted: false,
  manuallyOpen: false,
  lastSeenOutputCount: 0,
};

export interface UseCanvasHostArgs {
  /** Active tab id — the per-tab Map key. Stable per tab (exists before the
   *  session resolves), so it is the correct "which tab" signal (NOT sessionId,
   *  which flips undefined→resolved on first message — IMPROVEMENT.md tell#4). */
  activeTabId: string | null | undefined;
  /** Active tab's backend session id (for auto-surface tab-scoping + fail-closed).
   *  Accepts null (ChatPage's sessionId is string|null before a session exists). */
  sessionId: string | null | undefined;
  /** Active tab currently streaming (auto-surface streaming gate — bug1). */
  isStreaming: boolean;
}

export interface CanvasHostApi {
  /** The active tab's open file (null = none). */
  file: CanvasFile | null;
  pinned: boolean;
  muted: boolean;
  /** Canvas is visible when a file is open OR it was manually opened. */
  isOpen: boolean;
  /** The active tab's referenced-files (the output rail's rows), captured by the
   *  RESIDENT listener here so a write lands even when Canvas is closed. Passed
   *  down to CanvasOutputRail as a prop — the rail no longer runs its own listener. */
  referencedFiles: GroupedReferencedFiles;
  /** Count of OUTPUT rows (content/knowledge/source-final; process/source excluded)
   *  for the active tab — the single source of truth for the ChatHeader pill.
   *  Live even when Canvas is closed (that is the whole point). */
  outputCount: number;
  /** outputCount at the last time this tab's Canvas was open (run_9dd59523). The pill
   *  shows only when outputCount > this, so it stops nagging once outputs are reviewed. */
  lastSeenOutputCount: number;
  setFile: (f: CanvasFile | null) => void;
  setPinned: (v: boolean) => void;
  setMuted: (v: boolean) => void;
  togglePin: () => void;
  toggleMute: () => void;
  /** Close Canvas on the active tab (clear file + manuallyOpen). */
  close: () => void;
  /** Live, synchronous snapshot of Canvas state for the send-time SENSE read
   *  (beats the async canvas-state emit race). Null when nothing to report. */
  getCanvasSnapshot: () => CanvasSnapshot | null;
}

export function useCanvasHost({ activeTabId, sessionId, isStreaming }: UseCanvasHostArgs): CanvasHostApi {
  // Per-tab state map — the source of truth. A plain ref Map (like
  // inputValueMapRef): mutated directly, NEVER enters MessageStore/reconcile.
  const mapRef = useRef<Map<string, CanvasTabState>>(new Map());
  // The active tab's slice, mirrored into React state so the panel re-renders.
  // outputCount now lives INSIDE the slice (per-tab) — no separate state/ref, so
  // it restores on tab-switch, clears on close, and getCanvasSnapshot reads it
  // straight from mapRef (the synchronous source of truth) with no stale-closure
  // mirror to keep in sync.
  const [slice, setSlice] = useState<CanvasTabState>(EMPTY);

  // ── RESIDENT output-rail store (run_9e42c066) ───────────────────────────────
  // useCanvasHost is mounted UNCONDITIONALLY in ChatPage, so calling
  // useReferencedFiles HERE (keyed on the active tab) makes it the SOLE
  // swarm:file-changed rail listener — always alive, even when Canvas is closed.
  // This is the fix for "pipeline 跑完看不到输出": a source-final finish batch that
  // arrives with the panel unmounted is now captured + persisted here, and the
  // panel's CanvasOutputRail receives these rows via a prop instead of running a
  // second (panel-gated) listener. outputCount is derived via the shared SSOT
  // predicate so the ChatHeader pill and the rail rows can never drift apart.
  const { files: referencedFiles } = useReferencedFiles(activeTabId ?? undefined);
  const outputCount = countOutputs(referencedFiles);

  const keyFor = (id: string | null | undefined) => id ?? '__no_tab__';

  // Restore the active tab's slice whenever the active tab changes.
  useEffect(() => {
    const k = keyFor(activeTabId);
    setSlice(mapRef.current.get(k) ?? EMPTY);
  }, [activeTabId]);

  // Write a patch to the active tab's slice: update the map AND the React mirror.
  const patch = useCallback((p: Partial<CanvasTabState>) => {
    const k = keyFor(activeTabId);
    const cur = mapRef.current.get(k) ?? EMPTY;
    const next = { ...cur, ...p };
    mapRef.current.set(k, next);
    setSlice(next);
  }, [activeTabId]);

  const setFile = useCallback((f: CanvasFile | null) => patch({ file: f }), [patch]);
  const setPinned = useCallback((v: boolean) => patch({ pinned: v }), [patch]);
  const setMuted = useCallback((v: boolean) => patch({ muted: v }), [patch]);
  const togglePin = useCallback(() => patch({ pinned: !(mapRef.current.get(keyFor(activeTabId))?.pinned) }), [patch, activeTabId]);
  const toggleMute = useCallback(() => patch({ muted: !(mapRef.current.get(keyFor(activeTabId))?.muted) }), [patch, activeTabId]);
  // close clears the tab's Canvas (file + manuallyOpen). outputCount is NOT reset
  // here — it now derives from the RESIDENT store (useReferencedFiles), which is
  // intentionally per-tab-persistent: closing the panel must not discard the
  // knowledge that N outputs were produced (the ChatHeader pill still shows them).
  const close = useCallback(() => patch({ file: null, manuallyOpen: false }), [patch]);

  const isOpen = !!(slice.file || slice.manuallyOpen);

  // ── Mark outputs "seen" while Canvas is open (run_9dd59523, Gate-1 #3) ────────
  // The ChatHeader pill shows only when outputCount > lastSeenOutputCount. Two rules:
  //  (1) While OPEN: mark lastSeen = outputCount on every [isOpen, outputCount] (NOT
  //      the false→true transition only) — the pill is already hidden by its own
  //      `!canvasOpen` guard while open, and tracking the latest count here is what
  //      prevents a STALE pill from flashing the moment the user closes Canvas.
  //  (2) SHRINK clamp (open OR closed): if outputCount drops BELOW lastSeen (cap
  //      eviction at MAX_FILES, or clear() on a new session), pull lastSeen down to
  //      outputCount. Without this, a shrink-while-closed leaves lastSeen stuck HIGH,
  //      and a later genuinely-new output at/below that stale mark would never re-show
  //      the pill (REVIEW MED, run_9dd59523). The clamp can only LOWER lastSeen, so it
  //      never marks a real new output as already-seen.
  // Only writes when the value actually changes (patch would otherwise re-fire).
  useEffect(() => {
    const shouldMarkSeen = isOpen && slice.lastSeenOutputCount !== outputCount;
    const shouldClampDown = slice.lastSeenOutputCount > outputCount;
    if (shouldMarkSeen || shouldClampDown) {
      patch({ lastSeenOutputCount: outputCount });
    }
  }, [isOpen, outputCount, slice.lastSeenOutputCount, patch]);

  // ── Synchronous, send-time proprioception read (race fix, run_e45a04d3) ──────
  // The agent's SENSE snapshot reads canvas.open at chat-SEND time. The async
  // swarm:canvas-state emit (effect below) could lag a fast send, leaving the
  // snapshot stale (the observed race). getCanvasSnapshot() reads the LIVE
  // source instead — mapRef (the synchronous source of truth, written in patch()
  // BEFORE React commits) for open/pinned/muted, plus the resident `outputCount`
  // (from useReferencedFiles, run_9e42c066) — so a send after swarm:open-canvas
  // reports the true state. Mirrors the CanvasSnapshot shape exactly
  // (uiContext.ts); collapsed stays false (the dock that could set it true is gone).
  // Returns null when there is nothing reportable (parity with the effect's null).
  const getCanvasSnapshot = useCallback((): CanvasSnapshot | null => {
    const cur = mapRef.current.get(keyFor(activeTabId)) ?? EMPTY;
    const openNow = !!(cur.file || cur.manuallyOpen);
    // Report a snapshot when the panel is open OR there are pending outputs while
    // CLOSED (run_9e42c066 meta-review): the ChatHeader pill shows the user "N
    // outputs" on a closed Canvas, so the agent must be able to SENSE the same —
    // prompt_builder already renders "Canvas: closed, N outputs listed" from
    // output_count alone. Returning null here (the old open-only contract) hid the
    // pending outputs from the agent's SENSE payload even as the pill advertised
    // them. Only truly-empty-and-closed reports null.
    if (!openNow && outputCount === 0) return null;
    return {
      open: openNow,
      // outputCount comes from the resident store (SSOT), NOT a per-slice mirror.
      outputCount,
      pinned: cur.pinned,
      muted: cur.muted,
      collapsed: false,
    };
  }, [activeTabId, outputCount]);

  // ── swarm:open-canvas (window) — manual open on the ACTIVE tab ──────────────
  useEffect(() => {
    const onOpenCanvas = () => patch({ manuallyOpen: true });
    window.addEventListener('swarm:open-canvas', onOpenCanvas);
    return () => window.removeEventListener('swarm:open-canvas', onOpenCanvas);
  }, [patch]);

  // ── swarm:open-file (DOCUMENT — Gate-1 Risk 4) — resolve + set active file ──
  // Preserves the resolve-then-open path that lived in ThreeColumnLayout: chat
  // paths may be source-repo-relative, so resolve to a workspace path first.
  const activeTabIdRef = useRef(activeTabId);
  activeTabIdRef.current = activeTabId;
  // Per-tab open generation (run_a9806ea0 adversarial B): each open-file bumps its
  // tab's counter; a resolve that completes AFTER a newer open on the SAME tab is
  // stale and discards itself, so out-of-order /resolve completions can't clobber
  // the most-recently-clicked file with an older one.
  const openGenRef = useRef<Map<string, number>>(new Map());
  useEffect(() => {
    let mounted = true;
    const handleOpenFile = async (e: Event) => {
      const { path: filePath, autoDiff, gitStatus, workspaceId, baseRef, tabId: stampedTab } = (e as CustomEvent<{ path: string; autoDiff?: boolean; gitStatus?: GitStatus; workspaceId?: string; baseRef?: string; tabId?: string }>).detail ?? {};
      if (!filePath) return;
      // Landing tab = the event's STAMPED origin tab if present, else the live active
      // tab. Two distinct origin cases:
      //  · USER CLICK (run_a9806ea0): dispatches {path} with NO tabId → fire is
      //    synchronous with the click, so activeTabIdRef.current IS the origin tab.
      //    The capture-before-await below then pins it against a switch-during-/resolve.
      //  · AGENT ui_command (run_48a29fc2): the swarm:open-file fires MID-STREAM,
      //    seconds after send, from the ORIGINATING tab's (possibly background) stream
      //    handler. By then the user may have switched tabs, so activeTabIdRef.current
      //    is the WRONG tab. The producer stamps detail.tabId = the stream's captured
      //    origin tab (mirroring the file_changed sibling), which we MUST prefer — else
      //    the file lands on whatever tab is active at fire time (the observed bleed).
      const landingTab = stampedTab ?? activeTabIdRef.current;
      const k = keyFor(landingTab);
      const gen = (openGenRef.current.get(k) ?? 0) + 1;
      openGenRef.current.set(k, gen);
      let resolvedPath = filePath;
      try {
        const resp = await api.get<{ resolved_path: string }>('/workspace/file/resolve', { params: { path: filePath } });
        if (!mounted) return;
        resolvedPath = resp.data.resolved_path;
      } catch (err: unknown) {
        if (!mounted) return;
        const status = (err as { response?: { status?: number } })?.response?.status;
        if (status === 400) return; // path traversal / invalid — drop
        // 404 → fall through to raw path; other errors just use the raw path.
      }
      // Stale-resolve guard (B): a newer open on the SAME origin tab superseded us.
      if (openGenRef.current.get(k) !== gen) return;
      const fileName = resolvedPath.split('/').pop() || resolvedPath;
      // Land the file on its ORIGIN tab's slice (captured pre-await), and only push
      // it into the live React mirror if that ORIGIN tab is STILL active — a switch
      // during the resolve routes the file to A's map (restored when A is revisited)
      // and never disturbs B. `cur` is re-read HERE (post-await) so any patch() that
      // ran during the resolve (pin/mute toggle) is preserved, not clobbered (C).
      const cur = mapRef.current.get(k) ?? EMPTY;
      const next = { ...cur, file: { filePath: resolvedPath, fileName, autoDiff: autoDiff || undefined, gitStatus, workspaceId, baseRef } };
      mapRef.current.set(k, next);
      if (activeTabIdRef.current === landingTab) setSlice(next);
    };
    document.addEventListener('swarm:open-file', handleOpenFile);
    return () => {
      mounted = false;
      document.removeEventListener('swarm:open-file', handleOpenFile);
    };
  }, []);

  // ── Auto-surface (streaming-gated, fail-closed) ─────────────────────────────
  // activeTabId = the tab-scope key (run_26aa6caa) — the same stable key the rest
  // of this hook uses. activeSessionId stays wired ONLY for the fire-time restart
  // fail-closed (a separate concern from tab-scoping).
  useCanvasAutoSurface({ pinned: slice.pinned, muted: slice.muted, activeTabId: activeTabId ?? undefined, activeSessionId: sessionId ?? undefined, isStreaming });

  // ── Proprioception emits (WINDOW) — mirror the file + canvas snapshot ───────
  // editor-file-changed: the open file (or null). canvas-state: the full snapshot.
  // Both value-guarded to avoid redundant dispatches.
  const lastFileEmit = useRef<string>('');
  useEffect(() => {
    const detail = slice.file ? { filePath: slice.file.filePath, fileName: slice.file.fileName } : null;
    const sig = JSON.stringify(detail);
    if (sig === lastFileEmit.current) return;
    lastFileEmit.current = sig;
    window.dispatchEvent(new CustomEvent('swarm:editor-file-changed', { detail }));
    // editor-panel-state drives auto-surface's "user is viewing" suppression.
    window.dispatchEvent(new CustomEvent('swarm:editor-panel-state', { detail: { open: !!slice.file } }));
  }, [slice.file]);

  const lastCanvasEmit = useRef<string>('');
  useEffect(() => {
    // Emit when open OR there are pending outputs while closed (parity with
    // getCanvasSnapshot, run_9e42c066): a closed Canvas with N outputs must still
    // reach the agent's SENSE payload, matching the ChatHeader pill the user sees.
    const detail = (isOpen || outputCount > 0)
      ? { open: isOpen, outputCount, pinned: slice.pinned, muted: slice.muted, collapsed: false }
      : null;
    const sig = JSON.stringify(detail);
    if (sig === lastCanvasEmit.current) return;
    lastCanvasEmit.current = sig;
    window.dispatchEvent(new CustomEvent('swarm:canvas-state', { detail }));
  }, [isOpen, outputCount, slice.pinned, slice.muted]);

  return {
    file: slice.file,
    pinned: slice.pinned,
    muted: slice.muted,
    isOpen,
    referencedFiles,
    outputCount,
    lastSeenOutputCount: slice.lastSeenOutputCount,
    setFile,
    setPinned,
    setMuted,
    togglePin,
    toggleMute,
    close,
    getCanvasSnapshot,
  };
}
