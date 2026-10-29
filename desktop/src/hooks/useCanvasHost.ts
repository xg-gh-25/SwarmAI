/**
 * useCanvasHost — per-tab Canvas state + host wiring for the ChatPage-owned
 * Canvas (FileViewerPanel).
 *
 * WHY THIS EXISTS (bugs 2 + 3): Canvas used to live in ThreeColumnLayout as a
 * single global useState set, RESET (not restored) on tab switch, and mounted as
 * a full-height sibling of the chat column — so it sat beside the tab bar and
 * lost its state every switch. This hook moves Canvas ownership to ChatPage (the
 * owner of tabs), keyed PER TAB, so:
 *   - each tab remembers its own open file + pin/manuallyOpen (mirrors the
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
 *   - `swarm:ui-command-result`  → listened on **window** (uiCommands reports an
 *                                   agent Canvas command; this hook is the ONLY
 *                                   writer of a tab's `commands`).
 *   Auto-surface reads the slice directly (pinned, canvasTab, file.auto) — no event.
 * Fullscreen (FileEditorModal) stays in ThreeColumnLayout; when the user asks to
 * fullscreen a Canvas file we re-dispatch `swarm:open-file-fullscreen` (window)
 * which ThreeColumnLayout owns — nothing about the modal moves here.
 *
 * @exports useCanvasHost
 */
import { useState, useRef, useCallback, useEffect, useLayoutEffect, useMemo, startTransition } from 'react';
import api from '../services/api';
import { useCanvasAutoSurface, type CanvasSuppression } from './useCanvasAutoSurface';
import { useReferencedFiles, outputRows, outputKey, storedOutputRows, type GroupedReferencedFiles, type ReferencedFile } from './useReferencedFiles';
import { refreshCanvasArtifacts, refreshCanvasSession, setActiveCanvasSession, setCanvasStreaming } from '../services/canvasSession';
import type { GitStatus } from '../types';
import { MAX_SNAPSHOT_COMMANDS, MAX_SNAPSHOT_OUTPUTS, type CanvasFileViewReport, type CanvasSnapshot, type UiCommandResult } from '../utils/uiContext';
import { UI_COMMAND_RESULT_EVENT, type UiCommandResultDetail } from '../utils/uiCommands';
import { isHiveTab } from '../pages/chat/swarmOut';
import { isRemote } from '../services/remoteSession';
import i18n from '../i18n';

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
  /** Owning session id that surfaced this file (run_c014a4f3). Threaded to the render
   *  fetch so an EXTERNAL surfaced file renders read-only. Absent for internal files. */
  sessionId?: string;
  /** Canvas opened this file by itself (auto-surface, or the panel's default
   *  selection), not the user. Auto-surface may replace an `auto` file; a user's
   *  file it never replaces. Set by the swarm:open-file handler from detail.auto. */
  auto?: boolean;
}

interface CanvasTabState {
  file: CanvasFile | null;
  pinned: boolean;
  manuallyOpen: boolean;
  /** The output rows (by outputKey) listed the last time the user had this tab's
   *  Canvas OPEN (run_9dd59523; a set since canvas-proprioception §3.1a). A row is
   *  NEW when its key is not in here — exact under deletes, merges and same-stamp
   *  batches, which a count was not. The ChatHeader pill shows while any row is new.
   *  Per-tab, in-memory (a reload re-surfaces "here's what this tab produced"). */
  seen: ReadonlySet<string>;
  /** The shown file's view mode, reported by FileEditorCore through the panel.
   *  QUIET: written to mapRef only (nothing renders it — the snapshot reads it). */
  fileView: CanvasFileViewReport | null;
  /** The agent's Canvas commands since this tab's last send (§5), newest last.
   *  QUIET: mapRef only; this hook is the one writer; takeCanvasSnapshotFor clears it. */
  commands: (UiCommandResult & { id: string })[];
  /** An agent layout change waiting for the active tab's answer to finish (§4).
   *  QUIET; reported as a `pending` command. A user layout change drops it. */
  deferred: DeferredAgentChange | null;
  /** Whether the whole Canvas is collapsed to the thin vertical rail (Bug 2). Per-tab:
   *  the panel does NOT remount on tab switch (tabScopeKey, not React key), so this used
   *  to be panel-local useState + a GLOBAL localStorage key → collapsing tab A's Canvas
   *  left tab B collapsed too. In the slice it restores/clears per tab like file/pinned.
   *  In-memory (NOT persisted): a transient view pref — matches the sanctioned pattern
   *  (run_6fb6708c: move the drifting field INTO the container, don't reset()). */
  railed: boolean;
  /** Whether ONLY the output LIST is folded (panel keeps full width). Per-tab, same
   *  bleed + fix as railed (Bug 2). In-memory. */
  outputsCollapsed: boolean;
  /** The Canvas tab shown (Artifacts | Changes). Per tab for the same reason as
   *  railed: the panel never remounts on a tab switch, so panel-local state carried
   *  tab A's Changes view (and auto-surface's yield) into tab B. Every file open
   *  brings Artifacts forward (the open handler writes it with the file). */
  canvasTab: CanvasTabId;
  /** The shown file has unsaved edits (FileViewer reports it through the panel).
   *  While true, a user open of another file waits in `pendingOpen`, and an auto
   *  open (auto-surface, default selection) is dropped (super-canvas-fixing.md C1). */
  fileDirty: boolean;
  /** A user open held back by unsaved edits; the panel asks "discard and open it /
   *  keep editing" (resolvePending). */
  pendingOpen: CanvasFile | null;
}

/** The two Canvas tabs. */
export type CanvasTabId = 'artifacts' | 'changes';

/** An agent Canvas change, computed from the tab's state at the moment it applies. */
type AgentCompute = (cur: CanvasTabState) => {
  next: Partial<CanvasTabState> | null;
  result: 'applied' | 'ignored';
  reason?: string;
};
interface DeferredAgentChange { cmd: string; cmdId: string; compute: AgentCompute }

/** What the user sees: no Canvas, the folded side rail, or the open Canvas. */
const visibleOf = (st: CanvasTabState): 'hidden' | 'rail' | 'open' =>
  !(st.file || st.manuallyOpen) ? 'hidden' : st.railed ? 'rail' : 'open';

const EMPTY: CanvasTabState = {
  file: null,
  pinned: false,
  manuallyOpen: false,
  seen: new Set<string>(),
  fileView: null,
  commands: [],
  deferred: null,
  railed: false,
  outputsCollapsed: false,
  canvasTab: 'artifacts',
  fileDirty: false,
  pendingOpen: null,
};

/** The per-tab collapse view-state the panel reads (Bug 2). `expanded`/`width` are
 *  deliberately NOT here — they are a coupled panel-GLOBAL sizing preference (width is
 *  a high-frequency rAF drag target; expanded snaps width to max + restores via a
 *  preExpand ref). Lifting expanded per-tab while width stays global corrupts the
 *  restore (Gate-1 finding), so only the two true collapse flags are per-tab. */
export interface CanvasCollapse {
  railed: boolean;
  outputsCollapsed: boolean;
}

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
  /** outputCount minus the rows the user has not seen yet (derived from `seen`; the
   *  ChatHeader pill shows while outputCount > this). */
  lastSeenOutputCount: number;
  /** The active tab's collapse view-state (railed / outputsCollapsed) — Bug 2. Read
   *  by FileViewerPanel; stable across a keystroke render (mirrors the slice). */
  collapse: CanvasCollapse;
  /** Patch the active tab's collapse state (Bug 2). useCallback-stable — FileViewerPanel
   *  is memo'd on referentially-stable props, so this MUST NOT be a fresh fn per render. */
  setCollapse: (p: Partial<CanvasCollapse>) => void;
  /** The active tab's Canvas tab (per tab, in the slice). */
  canvasTab: CanvasTabId;
  /** Switch the active tab's Canvas tab. useCallback-stable (memo'd panel prop). */
  setCanvasTab: (t: CanvasTabId) => void;
  setFile: (f: CanvasFile | null) => void;
  setPinned: (v: boolean) => void;
  togglePin: () => void;
  /** Close Canvas on the active tab (clear file + manuallyOpen). */
  close: () => void;
  /** Reveal (manually open) Canvas on the active tab — fired by clicking the
   *  resident empty-state rail (FileViewerPanel, isOpen=false branch). Sets
   *  manuallyOpen:true via the SAME per-tab patch() chokepoint as swarm:open-canvas,
   *  so it lands on the active tab's slice and never bleeds cross-tab. Does NOT
   *  change isOpen's semantics (still file||manuallyOpen) — it just flips the flag
   *  the resident rail exists to flip. useCallback-stable (memo'd panel prop). */
  reveal: () => void;
  /** The active tab's held-back open (unsaved edits on the shown file), or null. */
  pendingOpen: CanvasFile | null;
  /** The shown file has unsaved edits (the panel's folded-header dot reads it). */
  fileDirty: boolean;
  /** The panel reports the shown file's unsaved state. Editing an auto-opened file
   *  makes it the user's (clears `auto`). Stable identity (memo'd panel prop). */
  setFileDirty: (dirty: boolean) => void;
  /** Answer the held-back open: discard the edits and open it, or keep editing.
   *  Stable identity (memo'd panel prop). */
  resolvePending: (discard: boolean) => void;
  /** Live, synchronous snapshot of Canvas state for the send-time SENSE read
   *  (beats the async canvas-state emit race). STABLE identity: a copy captured
   *  by a memoized send callback still reads the current state. Always the
   *  ACTIVE tab; the send path uses getCanvasSnapshotFor. */
  getCanvasSnapshot: () => CanvasSnapshot | null;
  /** The same snapshot for an explicit tab (the tab a message is sent for): that
   *  tab's slice + its stored rail count, active or not. null / '' (unless there
   *  is no active tab either) / a tab with nothing to report → null — never a
   *  fall back to the active tab. Stable identity. */
  getCanvasSnapshotFor: (tabId: string | null) => CanvasSnapshot | null;
  /** The SEND read: the same snapshot, and it hands the tab's `commands` over (clears
   *  them, mapRef only). Call it exactly once per request sent. Stable identity. */
  takeCanvasSnapshotFor: (tabId: string | null) => CanvasSnapshot | null;
  /** The panel reports how the shown file is displayed (§3.2). QUIET: no render.
   *  Stable identity (memo'd panel prop). */
  setFileView: (v: CanvasFileViewReport | null) => void;
}

const keyFor = (id: string | null | undefined) => id ?? '__no_tab__';

export function useCanvasHost({ activeTabId, sessionId, isStreaming }: UseCanvasHostArgs): CanvasHostApi {
  // Per-tab state map — the source of truth. A plain ref Map (like
  // inputValueMapRef): mutated directly, NEVER enters MessageStore/reconcile.
  const mapRef = useRef<Map<string, CanvasTabState>>(new Map());
  // The active tab's slice, mirrored into React state so the panel re-renders.
  // outputCount now lives INSIDE the slice (per-tab) — no separate state/ref, so
  // it restores on tab-switch, clears on close, and getCanvasSnapshot reads it
  // straight from mapRef (the synchronous source of truth) with no stale-closure
  // mirror to keep in sync.
  // Stored WITH the tab it belongs to. When the active tab changes, the new tab's
  // slice is taken from mapRef during this same render (React's "adjust state
  // while rendering"), so the switch commit already shows the new tab's Canvas.
  // A post-paint effect painted one frame with the OLD tab's Canvas, then moved
  // the chat column's width a frame later: the tab-switch jitter.
  const [shown, setShown] = useState<{ k: string; s: CanvasTabState }>(() => ({
    k: keyFor(activeTabId),
    s: mapRef.current.get(keyFor(activeTabId)) ?? EMPTY,
  }));
  const activeKey = keyFor(activeTabId);
  let slice = shown.s;
  if (shown.k !== activeKey) {
    slice = mapRef.current.get(activeKey) ?? EMPTY;
    setShown({ k: activeKey, s: slice });
  }
  const setSlice = useCallback((k: string, s: CanvasTabState) => setShown({ k, s }), []);

  // ── RESIDENT output-rail store (run_9e42c066) ───────────────────────────────
  // useCanvasHost is mounted UNCONDITIONALLY in ChatPage, so calling
  // useReferencedFiles HERE (keyed on the active tab) makes it the SOLE
  // swarm:file-changed rail listener — always alive, even when Canvas is closed.
  // This is the fix for "pipeline 跑完看不到输出": a source-final finish batch that
  // arrives with the panel unmounted is now captured + persisted here, and the
  // panel's CanvasOutputRail receives these rows via a prop instead of running a
  // second (panel-gated) listener. outputCount is derived via the shared SSOT
  // predicate so the ChatHeader pill and the rail rows can never drift apart.
  // The rail = the active session's rows in the backend's record
  // (services/canvasSession → core/session_outputs), the one source for live,
  // reloaded and restarted sessions. The store refreshes on swarm:file-changed
  // (artifacts only, debounced); a full refresh runs when the session changes; when a
  // turn ends, Artifacts are refreshed here and Changes by setCanvasStreaming (which
  // holds every Changes request while the turn streams, then sends exactly one). The
  // tab → session report is what lets a background tab's event refresh ITS session.
  useEffect(() => {
    setActiveCanvasSession(sessionId ?? null, activeTabId ?? null);
    // before the refresh: a tab that opens mid-turn sends no Changes request
    if (isStreaming) void setCanvasStreaming(sessionId ?? null, true);
    void refreshCanvasSession(sessionId ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- isStreaming is read once per session switch
  }, [sessionId, activeTabId]);
  const wasStreamingRef = useRef(isStreaming);
  useEffect(() => {
    void setCanvasStreaming(sessionId ?? null, isStreaming);
    if (wasStreamingRef.current && !isStreaming) void refreshCanvasArtifacts(sessionId ?? null);
    wasStreamingRef.current = isStreaming;
  }, [isStreaming, sessionId]);
  // artifacts only: ChatPage re-renders when the list changes, not on fetch flags
  const { files: referencedFiles } = useReferencedFiles(sessionId ?? null);
  // The output rows (pill / snapshot / count share one filter: outputRows).
  const rows = useMemo(() => outputRows(referencedFiles), [referencedFiles]);
  const outputCount = rows.length;


  // QUIET write (canvas-proprioception §3.2 / §5): state only the agent's snapshot
  // reads goes to mapRef and NEVER through setSlice, so it costs ChatPage no render.
  const writeQuiet = useCallback((k: string, p: Partial<CanvasTabState>) => {
    mapRef.current.set(k, { ...(mapRef.current.get(k) ?? EMPTY), ...p });
  }, []);
  // The ONE writer of a tab's `commands`: a report with a known id replaces that
  // entry (pending → applied/ignored); otherwise it is appended; the last few kept.
  const recordCommand = useCallback((k: string, r: UiCommandResult & { id: string }) => {
    const list = (mapRef.current.get(k) ?? EMPTY).commands;
    const i = list.findIndex((c) => c.id === r.id);
    const next = i >= 0 ? list.map((c, j) => (j === i ? r : c)) : [...list, r];
    writeQuiet(k, { commands: next.slice(-MAX_SNAPSHOT_COMMANDS) });
  }, [writeQuiet]);
  // Write a patch to the active tab's slice: update the map AND the React mirror.
  // A USER layout change (file / open / fold) wins over an agent change still
  // waiting for the answer to finish (§4): that one is dropped, with the reason.
  const patch = useCallback((p: Partial<CanvasTabState>) => {
    const k = keyFor(activeTabId);
    const cur = mapRef.current.get(k) ?? EMPTY;
    let next = { ...cur, ...p };
    const layoutChanged = ('file' in p && p.file?.filePath !== cur.file?.filePath)
      || ('manuallyOpen' in p && p.manuallyOpen !== cur.manuallyOpen)
      || ('railed' in p && p.railed !== cur.railed);
    if (cur.deferred && layoutChanged) {
      recordCommand(k, { id: cur.deferred.cmdId, cmd: cur.deferred.cmd, result: 'ignored', reason: 'the user changed the Canvas first' });
      next = { ...(mapRef.current.get(k) ?? EMPTY), ...p, deferred: null };
    }
    mapRef.current.set(k, next);
    setSlice(k, next);
  }, [activeTabId, recordCommand]);

  // ── Agent Canvas changes (canvas-proprioception §4) ──────────────────────────
  // One path for every agent command: compute the change from the tab's CURRENT
  // state, record the outcome (the one writer of `commands`), and
  //  · while the ACTIVE tab streams, a change that moves the chat column's width
  //    (closed ↔ open ↔ rail) waits in `deferred` until the answer finishes — the
  //    message list reflow is never paid mid-stream (run 0: WebKit 37 ms / 300 msgs);
  //  · otherwise it applies now, its render in a transition so streamed tokens
  //    paint first. A background tab's change applies to its slice (no layout).
  const isStreamingRef = useRef(isStreaming);
  isStreamingRef.current = isStreaming;
  const applyAgent = useCallback((landingTab: string | null | undefined, cmd: string, cmdId: string | undefined, compute: AgentCompute) => {
    const k = keyFor(landingTab);
    const cur = mapRef.current.get(k) ?? EMPTY;
    const r = compute(cur);
    const rec = (result: UiCommandResult['result'], reason?: string) => {
      if (cmdId) recordCommand(k, { id: cmdId, cmd, result, ...(reason ? { reason } : {}) });
    };
    if (!r.next) { rec(r.result, r.reason); return; }
    const next = { ...cur, ...r.next };
    const active = keyFor(activeTabIdRef.current) === k;
    if (active && isStreamingRef.current && visibleOf(next) !== visibleOf(cur)) {
      if (cur.deferred) {
        recordCommand(k, { id: cur.deferred.cmdId, cmd: cur.deferred.cmd, result: 'ignored', reason: 'replaced by a newer Canvas command' });
      }
      writeQuiet(k, { deferred: cmdId ? { cmd, cmdId, compute } : null });
      rec('pending', 'applies when your answer finishes');
      return;
    }
    const applied = { ...(mapRef.current.get(k) ?? EMPTY), ...r.next };
    mapRef.current.set(k, applied);
    if (active) startTransition(() => setSlice(k, applied));
    rec(r.result, r.reason);
  }, [recordCommand, writeQuiet]);
  // The answer finished (or the user came back to the tab): apply what waited.
  useEffect(() => {
    if (isStreaming) return;
    const k = keyFor(activeTabId);
    const d = (mapRef.current.get(k) ?? EMPTY).deferred;
    if (!d) return;
    writeQuiet(k, { deferred: null });
    applyAgent(activeTabId, d.cmd, d.cmdId, d.compute);
  }, [isStreaming, activeTabId, applyAgent, writeQuiet]);

  const setCollapse = useCallback((p: Partial<CanvasCollapse>) => patch(p), [patch]);
  const setCanvasTab = useCallback((t: CanvasTabId) => patch({ canvasTab: t }), [patch]);
  // setFile also un-rails on a NEW non-null file (Bug 2 root fix — no bypass): the
  // swarm:open-file handler is the primary write chokepoint, but setFile is a public
  // CanvasHostApi method, so it carries the SAME un-rail invariant to close the bypass
  // a future direct caller could open. Only un-rail on a real path change to a file
  // (null/same-path never fights a deliberate manual re-rail). Meta-review closed this.
  const setFile = useCallback((f: CanvasFile | null) => {
    const cur = mapRef.current.get(keyFor(activeTabId)) ?? EMPTY;
    const unrail = f && cur.file?.filePath !== f.filePath ? { railed: false } : {};
    // Same invariant as the open handler: a file shown brings Artifacts forward.
    patch({ file: f, ...unrail, ...(f ? { canvasTab: 'artifacts' as const } : {}) });
  }, [patch, activeTabId]);
  const setPinned = useCallback((v: boolean) => patch({ pinned: v }), [patch]);
  const togglePin = useCallback(() => patch({ pinned: !(mapRef.current.get(keyFor(activeTabId))?.pinned) }), [patch, activeTabId]);
  // close clears the tab's Canvas (file + manuallyOpen). outputCount is NOT reset
  // here — it now derives from the RESIDENT store (useReferencedFiles), which is
  // intentionally per-tab-persistent: closing the panel must not discard the
  // knowledge that N outputs were produced (the ChatHeader pill still shows them).
  const close = useCallback(() => patch({ file: null, manuallyOpen: false, fileDirty: false, pendingOpen: null }), [patch]);
  // reveal — manually open Canvas on the active tab (resident empty-state rail click).
  // Reuses patch() (the per-tab write chokepoint keyed on activeTabId) so it lands on
  // the correct tab's slice, mirroring the swarm:open-canvas handler's manuallyOpen
  // write. useCallback dep-[patch] so FileViewerPanel's load-bearing memo stays intact.
  const reveal = useCallback(() => patch({ manuallyOpen: true }), [patch]);
  const setFileDirty = useCallback((dirty: boolean) => {
    const cur = mapRef.current.get(keyFor(activeTabId)) ?? EMPTY;
    if (cur.fileDirty === dirty && !(dirty && cur.file?.auto)) return;
    // Editing a file Canvas opened by itself makes it the user's: auto-surface must
    // not replace it, with or without the dirty guard.
    const file = dirty && cur.file?.auto ? { ...cur.file, auto: undefined } : cur.file;
    patch({ fileDirty: dirty, file });
  }, [patch, activeTabId]);
  const setFileView = useCallback((v: CanvasFileViewReport | null) => {
    const k = keyFor(activeTabIdRef.current);
    const cur = (mapRef.current.get(k) ?? EMPTY).fileView;
    if (cur === v || (cur && v && cur.path === v.path && cur.view === v.view && cur.review === v.review)) return;
    writeQuiet(k, { fileView: v });
  }, [writeQuiet]);
  const resolvePending = useCallback((discard: boolean) => {
    const cur = mapRef.current.get(keyFor(activeTabId)) ?? EMPTY;
    if (!cur.pendingOpen) return;
    if (!discard) { patch({ pendingOpen: null }); return; }
    patch({ file: cur.pendingOpen, pendingOpen: null, fileDirty: false, canvasTab: 'artifacts', railed: false });
  }, [patch, activeTabId]);

  const isOpen = !!(slice.file || slice.manuallyOpen);

  // Stable collapse view-object for the memo'd panel: a fresh {railed, outputsCollapsed}
  // literal every render would defeat FileViewerPanel's load-bearing memo (keystroke
  // re-render → input lag). Memoize on the two primitive fields so identity only
  // changes when a value actually changes.
  const collapse = useMemo<CanvasCollapse>(
    () => ({ railed: slice.railed, outputsCollapsed: slice.outputsCollapsed }),
    [slice.railed, slice.outputsCollapsed],
  );

  // ── Mark outputs "seen" while Canvas is open (run_9dd59523; a set since §3.1a) ──
  // While OPEN, every listed row is seen (tracking every change, not only the
  // open transition, so no stale pill flashes when the user closes Canvas). A row
  // is new while its key is not in `seen` — so a delete, a merge or rows sharing one
  // backend stamp cannot mark a real new row as seen or an old one as new (the
  // count's shrink clamp is no longer needed). Writes only when a row is unseen.
  const unseenCount = useMemo(
    () => rows.reduce((n, r) => n + (slice.seen.has(outputKey(r)) ? 0 : 1), 0),
    [rows, slice.seen],
  );
  useEffect(() => {
    if (isOpen && unseenCount > 0) patch({ seen: new Set(rows.map(outputKey)) });
  }, [isOpen, unseenCount, rows, patch]);

  // ── Synchronous, send-time proprioception read (race fix, run_e45a04d3) ──────
  // The agent's SENSE snapshot reads Canvas state at chat-SEND time. The async
  // swarm:canvas-state emit (effect below) could lag a fast send, so the send
  // path reads this function instead: mapRef (the synchronous source of truth,
  // written in patch() BEFORE React commits) for open/pinned, plus the
  // output count. Mirrors the CanvasSnapshot shape exactly (uiContext.ts);
  // collapsed stays false (the dock that could set it true is gone).
  //
  // STABLE IDENTITY. ChatPage's send callbacks are memoized and do not list this
  // function in their deps, so they call whatever copy they captured. A copy that
  // closed over activeTabId/outputCount reported ANOTHER tab's Canvas (a tab whose
  // rail held 23 rows was reported as "2 outputs"). So this function has no
  // changing deps: it reads the active tab id from liveRef, synced after every
  // commit (layout effect: before any post-commit send or passive effect), the
  // slice from mapRef and the count from the tab's stored rail.
  //
  // getCanvasSnapshotFor(tabId) snapshots THAT tab: its slice in mapRef and its
  // stored rail count (sessionStorage is written in the same update that changes the
  // rail, and is the source of truth for a tab with no live hook instance). null / '' return
  // null — never a silent fall back to the active tab (a queued message drained for
  // a background tab must not carry the foreground tab's Canvas). Returns null when
  // there is nothing reportable.
  const liveRef = useRef<{ activeTabId: string | null | undefined; rows: ReferencedFile[] }>({ activeTabId, rows });
  useLayoutEffect(() => {
    liveRef.current = { activeTabId, rows };
  });
  const getCanvasSnapshotFor = useCallback((tabId: string | null): CanvasSnapshot | null => {
    const live = liveRef.current;
    const forActive = tabId === (live.activeTabId ?? null); // no tab: undefined ≡ null
    if (!forActive && !tabId) return null;
    const cur = mapRef.current.get(keyFor(tabId)) ?? EMPTY;
    // Rows from the tab's stored rail whenever there is a tab id — the active tab
    // included. useReferencedFiles writes storage in the same update that changes
    // its rows, so storage is never behind the rendered rows, and right after a
    // tab switch (before the hook reloads the new tab's rows in a passive effect)
    // the rendered rows still belong to the PREVIOUS tab while storage does not.
    // Only "no tab" has no storage key and uses the live rows. ONE grouping pass:
    // the count and the listed rows come from the same array.
    const tabRows = tabId ? storedOutputRows(tabId) : live.rows;
    const openNow = !!(cur.file || cur.manuallyOpen);
    // Report a snapshot when the panel is open, OR there are outputs while CLOSED
    // (the ChatHeader pill shows them, run_9e42c066), OR a command result is due.
    // Only truly-empty-and-closed reports null.
    if (!openNow && tabRows.length === 0 && cur.commands.length === 0) return null;
    const f = cur.file;
    const view = f && cur.fileView?.path === f.filePath ? cur.fileView : null;
    return {
      open: openNow,
      visible: !openNow ? 'hidden' : cur.railed ? 'rail' : 'open',
      tab: cur.canvasTab,
      listFolded: cur.outputsCollapsed,
      pinned: cur.pinned,
      file: f ? {
        path: f.filePath,
        name: f.fileName,
        openedBy: f.auto ? 'canvas' : 'user',
        dirty: cur.fileDirty,
        ...(view ? { view: view.view, review: view.review } : {}),
      } : null,
      pendingOpen: cur.pendingOpen?.filePath ?? null,
      outputCount: tabRows.length,
      outputs: tabRows.slice(0, MAX_SNAPSHOT_OUTPUTS).map((r) => ({
        path: r.path,
        ...(r.kind ? { kind: r.kind } : {}),
        isNew: !cur.seen.has(outputKey(r)),
      })),
      commands: cur.commands.map(({ cmd, result, reason }) => ({ cmd, result, ...(reason ? { reason } : {}) })),
    };
  }, []);
  const takeCanvasSnapshotFor = useCallback((tabId: string | null): CanvasSnapshot | null => {
    const snap = getCanvasSnapshotFor(tabId);
    const k = keyFor(tabId ?? liveRef.current.activeTabId);
    if ((mapRef.current.get(k) ?? EMPTY).commands.length) writeQuiet(k, { commands: [] });
    return snap;
  }, [getCanvasSnapshotFor, writeQuiet]);
  const getCanvasSnapshot = useCallback(
    () => getCanvasSnapshotFor(liveRef.current.activeTabId ?? null),
    [getCanvasSnapshotFor],
  );

  // ── swarm:open-canvas (window) — manual open on the ORIGIN tab ──────────────
  // Two origin cases, mirroring handleOpenFile (run_48a29fc2):
  //  · USER CLICK (ChatPage): bare event, NO detail.tabId → fires synchronously with
  //    the click, so activeTabIdRef.current IS the origin tab (fallback is correct).
  //  · AGENT ui_command (run_10c51cac): the swarm:open-canvas fires MID-STREAM from
  //    the ORIGINATING tab's (possibly background) stream handler; by then the user
  //    may have switched, so activeTabIdRef.current is the WRONG tab. The producer
  //    stamps detail.tabId = the stream's captured origin tab (same value the
  //    file_changed/open-file siblings carry), which we MUST prefer — else the manual
  //    open lands on whatever tab is active at fire time (the same cross-tab bleed).
  // Uses activeTabIdRef (not a patch() closure) so the deps-[] listener reads the
  // LIVE active tab at fire time. Lands manuallyOpen on the origin tab's slice and
  // only mirrors into React state when that origin tab is still active.
  useEffect(() => {
    const onOpenCanvas = (e: Event) => {
      const { tabId: stampedTab, cmdId, by } = (e as CustomEvent<{ tabId?: string; cmdId?: string; by?: string }>).detail ?? {};
      const landingTab = stampedTab ?? activeTabIdRef.current;
      if (by === 'agent') {
        // The agent "open Canvas" = what the user means by it: shown and unfolded.
        applyAgent(landingTab, 'open-canvas', cmdId, (cur) => ({
          next: { manuallyOpen: true, railed: false },
          result: 'applied',
          ...(visibleOf(cur) === 'open' ? { reason: 'already open' } : {}),
        }));
        return;
      }
      const k = keyFor(landingTab);
      const cur = mapRef.current.get(k) ?? EMPTY;
      const next = { ...cur, manuallyOpen: true };
      mapRef.current.set(k, next);
      if (activeTabIdRef.current === landingTab) setSlice(k, next);
    };
    window.addEventListener('swarm:open-canvas', onOpenCanvas);
    return () => window.removeEventListener('swarm:open-canvas', onOpenCanvas);
  }, [applyAgent]);

  // ── Agent-only Canvas layout commands (§4) — no user sender ─────────────────
  useEffect(() => {
    const detailOf = (e: Event) => (e as CustomEvent<{ tabId?: string; cmdId?: string }>).detail ?? {};
    const onFold = (e: Event) => {
      const { tabId, cmdId } = detailOf(e);
      applyAgent(tabId ?? activeTabIdRef.current, 'fold-canvas', cmdId, (cur) => {
        const v = visibleOf(cur);
        if (v === 'hidden') return { next: null, result: 'ignored', reason: 'Canvas is closed' };
        if (v === 'rail') return { next: null, result: 'applied', reason: 'already folded' };
        return { next: { railed: true }, result: 'applied' };
      });
    };
    const onTab = (tab: CanvasTabId, cmd: string) => (e: Event) => {
      const { tabId, cmdId } = detailOf(e);
      applyAgent(tabId ?? activeTabIdRef.current, cmd, cmdId, (cur) => {
        const v = visibleOf(cur);
        return {
          next: { canvasTab: tab },
          result: 'applied',
          ...(v === 'hidden' ? { reason: 'Canvas is closed; the tab shows when it opens' }
            : v === 'rail' ? { reason: 'Canvas is folded; the tab shows when it unfolds' } : {}),
        };
      });
    };
    const onArtifacts = onTab('artifacts', 'canvas-tab-artifacts');
    const onChanges = onTab('changes', 'canvas-tab-changes');
    window.addEventListener('swarm:canvas-fold', onFold);
    window.addEventListener('swarm:canvas-tab-artifacts', onArtifacts);
    window.addEventListener('swarm:canvas-tab-changes', onChanges);
    return () => {
      window.removeEventListener('swarm:canvas-fold', onFold);
      window.removeEventListener('swarm:canvas-tab-artifacts', onArtifacts);
      window.removeEventListener('swarm:canvas-tab-changes', onChanges);
    };
  }, [applyAgent]);

  // ── swarm:ui-command-result (window) — uiCommands reports an agent command ──
  useEffect(() => {
    const onResult = (e: Event) => {
      const d = (e as CustomEvent<UiCommandResultDetail>).detail;
      if (!d?.id || !d.cmd) return;
      recordCommand(keyFor(d.tabId ?? activeTabIdRef.current), { id: d.id, cmd: d.cmd, result: d.result, ...(d.reason ? { reason: d.reason } : {}) });
    };
    window.addEventListener(UI_COMMAND_RESULT_EVENT, onResult);
    return () => window.removeEventListener(UI_COMMAND_RESULT_EVENT, onResult);
  }, [recordCommand]);

  // ── swarm:open-file (DOCUMENT — Gate-1 Risk 4) — resolve + set active file ──
  // Preserves the resolve-then-open path that lived in ThreeColumnLayout: chat
  // paths may be source-repo-relative, so resolve to a workspace path first.
  const activeTabIdRef = useRef(activeTabId);
  activeTabIdRef.current = activeTabId;
  // The active tab's session: a click resolves against that session's own products
  // first (design 2026-10-26-session-products.md §3.4).
  const sessionIdRef = useRef(sessionId);
  sessionIdRef.current = sessionId;
  // Per-tab open generation (run_a9806ea0 adversarial B): each open-file bumps its
  // tab's counter; a resolve that completes AFTER a newer open on the SAME tab is
  // stale and discards itself, so out-of-order /resolve completions can't clobber
  // the most-recently-clicked file with an older one.
  const openGenRef = useRef<Map<string, number>>(new Map());
  useEffect(() => {
    let mounted = true;
    const handleOpenFile = async (e: Event) => {
      const { path: filePath, autoDiff, gitStatus, workspaceId, baseRef, sessionId: openSessionId, tabId: stampedTab, auto, cmdId, by } = (e as CustomEvent<{ path: string; autoDiff?: boolean; gitStatus?: GitStatus; workspaceId?: string; baseRef?: string; sessionId?: string; tabId?: string; auto?: boolean; cmdId?: string; by?: string }>).detail ?? {};
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
      // A Hive tab's paths name files on the Hive, not here: nothing on this machine is
      // opened for it, whatever path the content carries (design
      // docs/design/2026-10-29-swarm-out-hive-session-tab.md §7).
      if (isHiveTab(landingTab) || isRemote(openSessionId ?? (landingTab === activeTabIdRef.current ? sessionIdRef.current : null))) {
        if (cmdId) recordCommand(keyFor(landingTab), { id: cmdId, cmd: 'open-canvas-file', result: 'ignored', reason: 'remote session' });
        document.dispatchEvent(new CustomEvent('swarm:toast', { detail: { message: i18n.t('swarmOut.hiveTab.noLocalOpen') } }));
        return;
      }
      const k = keyFor(landingTab);
      // An agent open-canvas-file (cmdId set by uiCommands) settles its pending entry.
      const settle = (result: 'applied' | 'ignored', reason?: string) => {
        if (cmdId) recordCommand(k, { id: cmdId, cmd: 'open-canvas-file', result, ...(reason ? { reason } : {}) });
      };
      const gen = (openGenRef.current.get(k) ?? 0) + 1;
      openGenRef.current.set(k, gen);
      let resolvedPath = filePath;
      // The session whose products are matched first: the active tab's, for an open
      // that carries no session of its own (a click on a reference in the chat). An
      // open that names its session (a Canvas rail row) already points at the file
      // and resolves as before. Captured before the await.
      const resolveSid = !openSessionId && landingTab === activeTabIdRef.current ? sessionIdRef.current ?? undefined : undefined;
      let productSid: string | undefined;
      try {
        const resp = await api.get<{ resolved_path: string; session_product?: boolean }>('/workspace/file/resolve', {
          params: { path: filePath, ...(resolveSid ? { session_id: resolveSid } : {}) },
        });
        if (!mounted) return;
        resolvedPath = resp.data.resolved_path;
        // A file the session wrote keeps its session on the opened file, so its reads
        // pass that session's render gate (an outside-home product).
        if (resp.data.session_product && resolveSid) productSid = resolveSid;
      } catch (err: unknown) {
        if (!mounted) return;
        const status = (err as { response?: { status?: number } })?.response?.status;
        if (status === 400) { settle('ignored', 'invalid path'); return; } // path traversal / invalid — drop
        // 409 = several files this session wrote match the name: nothing is picked;
        // the toast names them so the user opens the one they mean from the rail.
        if (status === 409) {
          const cands = (err as { response?: { data?: { detail?: { candidates?: unknown } } } })?.response?.data?.detail?.candidates;
          const list = Array.isArray(cands) ? cands.filter((c): c is string => typeof c === 'string') : [];
          document.dispatchEvent(new CustomEvent('swarm:toast', {
            detail: { message: `Several files match ${filePath}: ${list.join(', ')}`, severity: 'warning', id: 'canvas-file-ambiguous' },
          }));
          settle('ignored', 'several files match');
          return;
        }
        // 404 = the exhaustive resolve cascade found the file NOWHERE → it is
        // genuinely nonexistent. DROP it (run_f49d3ff3 R1): the old fall-through to
        // the raw path opened Canvas onto a file /workspace/file also can't read →
        // an empty render (the reported bug). Surface a lightweight notice so the
        // user sees WHY nothing opened. NOTE (Gate-1 amendment 4): ONLY 404 drops —
        // a 500/network/transient error is NOT "file invalid", so it keeps the
        // existing fall-through (open on the raw path; the fetch layer retries/errors).
        if (status === 404) {
          // Stable id → rapid repeated bad-path opens collapse into ONE refreshed toast
          // instead of stacking (meta-review LOW; ToastContext dedups by id).
          document.dispatchEvent(new CustomEvent('swarm:toast', {
            detail: { message: `File not found: ${filePath}`, severity: 'warning', id: 'canvas-file-not-found' },
          }));
          settle('ignored', 'file not found');
          return;
        }
        // other errors (500/network) → fall through to raw path (unchanged).
      }
      // Stale-resolve guard (B): a newer open on the SAME origin tab superseded us.
      if (openGenRef.current.get(k) !== gen) { settle('ignored', 'replaced by a newer open'); return; }
      const fileName = resolvedPath.split('/').pop() || resolvedPath;
      // Land the file on its ORIGIN tab's slice (captured pre-await), and only push
      // it into the live React mirror if that ORIGIN tab is STILL active — a switch
      // during the resolve routes the file to A's map (restored when A is revisited)
      // and never disturbs B. `cur` is re-read HERE (post-await) so any patch() that
      // ran during the resolve (pin/mute toggle) is preserved, not clobbered (C).
      let cur = mapRef.current.get(k) ?? EMPTY;
      // UN-RAIL AT THE WRITE (Bug 2 root fix, run_5f5e7675): a new file arriving IS the
      // "I want to see this" intent, so reveal a railed Canvas. This lives HERE — the
      // single per-tab chokepoint where a file is SET — NOT in a panel render effect.
      // The panel-effect approach was render-timing-fragile: railTabId (=activeTabId,
      // immediate) and the file/collapse (=slice, restored one commit later) diverge on
      // a tab switch, so a switch-back to a railed tab false-fired the un-rail and popped
      // it open (adversarial HIGH). Keying the invariant to the actual file-set on the
      // owning tab's slice is timing-independent: a tab SWITCH never runs this handler,
      // so a restored railed tab is never un-railed. Only un-rail when the file actually
      // CHANGES (a re-open of the same path must not fight a deliberate manual re-rail).
      const fileChanged = cur.file?.filePath !== resolvedPath;
      const opened: CanvasFile = { filePath: resolvedPath, fileName, autoDiff: autoDiff || undefined, gitStatus, workspaceId, baseRef, sessionId: openSessionId ?? productSid, auto: auto || undefined };
      if (by === 'agent') {
        // §4: shown and unfolded like the user's own open, but never over unsaved
        // edits and without the "discard?" prompt (the agent must not interrupt).
        // Computed when it applies — a newer open or an edit since then wins.
        applyAgent(landingTab, 'open-canvas-file', cmdId, (c) => {
          if (openGenRef.current.get(k) !== gen) return { next: null, result: 'ignored', reason: 'replaced by a newer open' };
          const changed = c.file?.filePath !== resolvedPath;
          if (c.fileDirty && changed) return { next: null, result: 'ignored', reason: 'the shown file has unsaved edits; it was not replaced' };
          return {
            next: { file: opened, pendingOpen: null, canvasTab: 'artifacts', railed: false },
            result: 'applied',
            ...(changed ? {} : { reason: 'already shown' }),
          };
        });
        return;
      }
      // A user open wins over an agent change still waiting for the answer to end.
      if (cur.deferred) {
        recordCommand(k, { id: cur.deferred.cmdId, cmd: cur.deferred.cmd, result: 'ignored', reason: 'the user changed the Canvas first' });
        writeQuiet(k, { deferred: null });
        cur = mapRef.current.get(k) ?? EMPTY;
      }
      // Unsaved edits on the shown file (C1): Canvas never drops them by itself — an
      // auto open is ignored; a user open waits for "discard and open / keep editing".
      if (cur.fileDirty && fileChanged) {
        if (auto) return;
        const held = { ...cur, pendingOpen: opened };
        mapRef.current.set(k, held);
        if (activeTabIdRef.current === landingTab) setSlice(k, held);
        return;
      }
      const next = {
        ...cur,
        file: opened,
        pendingOpen: null,
        // An opened file shows in the document card: bring Artifacts forward (one
        // writer, with the file — the panel used to do this in a render effect).
        canvasTab: 'artifacts' as const,
        ...(fileChanged ? { railed: false } : {}),
      };
      mapRef.current.set(k, next);
      if (activeTabIdRef.current === landingTab) setSlice(k, next);
    };
    document.addEventListener('swarm:open-file', handleOpenFile);
    return () => {
      mounted = false;
      document.removeEventListener('swarm:open-file', handleOpenFile);
    };
  }, [recordCommand, applyAgent, writeQuiet]);

  // ── Auto-surface (streaming-gated, fail-closed) ─────────────────────────────
  // activeTabId = the tab-scope key (run_26aa6caa) — the same stable key the rest
  // of this hook uses. activeSessionId stays wired ONLY for the fire-time restart
  // fail-closed (a separate concern from tab-scoping).
  // Suppression is judged for the tab the open LANDS on (stateFor reads mapRef, the
  // per-tab source of truth; stable identity so the hook's listener never resubscribes).
  const stateFor = useCallback((tabId: string): CanvasSuppression => {
    const s = mapRef.current.get(keyFor(tabId)) ?? EMPTY;
    return { pinned: s.pinned, canvasTab: s.canvasTab, viewingUserChoice: !!s.file && !s.file.auto };
  }, []);
  useCanvasAutoSurface({
    stateFor,
    activeTabId: activeTabId ?? undefined,
    activeSessionId: sessionId ?? undefined,
    isStreaming,
  });

  return {
    file: slice.file,
    pinned: slice.pinned,
    isOpen,
    referencedFiles,
    outputCount,
    lastSeenOutputCount: outputCount - unseenCount,
    collapse,
    setCollapse,
    canvasTab: slice.canvasTab,
    setCanvasTab,
    setFile,
    setPinned,
    togglePin,
    close,
    reveal,
    pendingOpen: slice.pendingOpen,
    fileDirty: slice.fileDirty,
    setFileDirty,
    resolvePending,
    getCanvasSnapshot,
    getCanvasSnapshotFor,
    takeCanvasSnapshotFor,
    setFileView,
  };
}
