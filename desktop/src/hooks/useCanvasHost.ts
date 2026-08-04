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
  /** Panel-internal output count, PER TAB (like the rest of the slice) — so it
   *  restores on tab-switch and clears on close, never bleeding across tabs. */
  outputCount: number;
}

const EMPTY: CanvasTabState = {
  file: null,
  pinned: false,
  muted: false,
  manuallyOpen: false,
  outputCount: 0,
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
  setFile: (f: CanvasFile | null) => void;
  setPinned: (v: boolean) => void;
  setMuted: (v: boolean) => void;
  togglePin: () => void;
  toggleMute: () => void;
  /** Close Canvas on the active tab (clear file + manuallyOpen). */
  close: () => void;
  /** Report panel-internal meta (outputCount) up for proprioception. */
  onCanvasMeta: (meta: { collapsed: boolean; outputCount: number }) => void;
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
  // close clears the tab's Canvas INCLUDING its output count (no stale count on
  // reopen — the count is now per-tab slice state).
  const close = useCallback(() => patch({ file: null, manuallyOpen: false, outputCount: 0 }), [patch]);
  const onCanvasMeta = useCallback((meta: { collapsed: boolean; outputCount: number }) => {
    // Write the count into the per-tab slice via patch → mapRef (synchronous
    // source of truth) + setSlice (re-fires the canvas-state emit effect below).
    // No separate state/ref: getCanvasSnapshot reads cur.outputCount from mapRef.
    patch({ outputCount: meta.outputCount });
  }, [patch]);

  const isOpen = !!(slice.file || slice.manuallyOpen);

  // ── Synchronous, send-time proprioception read (race fix, run_e45a04d3) ──────
  // The agent's SENSE snapshot reads canvas.open at chat-SEND time. The async
  // swarm:canvas-state emit (effect below) could lag a fast send, leaving the
  // snapshot stale (the observed race). getCanvasSnapshot() reads the LIVE
  // source instead — mapRef (the synchronous source of truth, written in patch()
  // BEFORE React commits; outputCount now lives in the slice too) — so a send after
  // swarm:open-canvas reports the true state. Mirrors the CanvasSnapshot shape
  // exactly (uiContext.ts); collapsed stays false to match the emit effect
  // — onCanvasMeta's real collapsed is not persisted (pre-existing, unchanged).
  // Returns null when there is nothing reportable (parity with the effect's null).
  const getCanvasSnapshot = useCallback((): CanvasSnapshot | null => {
    const cur = mapRef.current.get(keyFor(activeTabId)) ?? EMPTY;
    const openNow = !!(cur.file || cur.manuallyOpen);
    if (!openNow) return null;
    return {
      open: true,
      outputCount: cur.outputCount,
      pinned: cur.pinned,
      muted: cur.muted,
      collapsed: false,
    };
  }, [activeTabId]);

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
  useEffect(() => {
    let mounted = true;
    const handleOpenFile = async (e: Event) => {
      const { path: filePath, autoDiff, gitStatus, workspaceId, baseRef } = (e as CustomEvent<{ path: string; autoDiff?: boolean; gitStatus?: GitStatus; workspaceId?: string; baseRef?: string }>).detail ?? {};
      if (!filePath) return;
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
      const fileName = resolvedPath.split('/').pop() || resolvedPath;
      // Route into the ACTIVE tab's slice (read the ref, not the closure — the
      // async resolve may land after a tab switch; patch() reads activeTabId too,
      // but we guard here so a late resolve never lands in the wrong tab).
      const landingTab = activeTabIdRef.current;
      const k = keyFor(landingTab);
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
  useCanvasAutoSurface({ pinned: slice.pinned, muted: slice.muted, activeSessionId: sessionId ?? undefined, isStreaming });

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
    const detail = isOpen
      ? { open: true, outputCount: slice.outputCount, pinned: slice.pinned, muted: slice.muted, collapsed: false }
      : null;
    const sig = JSON.stringify(detail);
    if (sig === lastCanvasEmit.current) return;
    lastCanvasEmit.current = sig;
    window.dispatchEvent(new CustomEvent('swarm:canvas-state', { detail }));
  }, [isOpen, slice.outputCount, slice.pinned, slice.muted]);

  return {
    file: slice.file,
    pinned: slice.pinned,
    muted: slice.muted,
    isOpen,
    setFile,
    setPinned,
    setMuted,
    togglePin,
    toggleMute,
    close,
    onCanvasMeta,
    getCanvasSnapshot,
  };
}
