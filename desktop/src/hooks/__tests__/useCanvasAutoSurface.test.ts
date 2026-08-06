/**
 * Tests for useCanvasAutoSurface — the gentle, flow-aware auto-surface.
 *
 * The suppression matrix is the load-bearing behavior: auto-open ONLY when the
 * user isn't already viewing (editor-panel-state), and hasn't pinned/muted.
 * Mutation-provable: remove any suppression clause in the hook → the matching
 * test goes RED.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useCanvasAutoSurface } from '../useCanvasAutoSurface';
import { OPEN_FILE_EVENT } from '../../components/common/MarkdownRenderer';

const DEBOUNCE = 20;

function writeFile(path: string, operation = 'written', tabId?: string, relevance = 'deliverable', kind?: string) {
  // Unified backend event (run_e626e121): swarm:file-changed on window, carries
  // the whitelist `relevance` (only 'deliverable' auto-surfaces) and (run_26aa6caa)
  // the owning `tabId` stamp used for tab-scope isolation. `kind` (run_4de279ca) is
  // the git-verdict authority — process/source never auto-pop.
  window.dispatchEvent(new CustomEvent('swarm:file-changed', { detail: { path, operation, relevance, tabId, kind } }));
}
function setPanelOpen(open: boolean) {
  window.dispatchEvent(new CustomEvent('swarm:editor-panel-state', { detail: { open } }));
}
function setCurrentFile(filePath: string | null) {
  window.dispatchEvent(
    new CustomEvent('swarm:editor-file-changed', { detail: filePath ? { filePath } : null }),
  );
}

describe('useCanvasAutoSurface', () => {
  let opened: string[];
  let openedDetails: Array<{ path: string; tabId?: string }>;
  let onOpen: (e: Event) => void;

  beforeEach(() => {
    vi.useFakeTimers();
    opened = [];
    openedDetails = [];
    onOpen = (e: Event) => {
      const d = (e as CustomEvent<{ path: string; tabId?: string }>).detail;
      opened.push(d.path);
      openedDetails.push({ path: d.path, tabId: d.tabId });
    };
    document.addEventListener(OPEN_FILE_EVENT, onOpen);
  });
  afterEach(() => {
    document.removeEventListener(OPEN_FILE_EVENT, onOpen);
    vi.useRealTimers();
  });

  it('auto-opens the written file when idle (not viewing, not pinned/muted)', () => {
    renderHook(() => useCanvasAutoSurface({ pinned: false, muted: false, debounceMs: DEBOUNCE }));
    writeFile('Knowledge/out.md');
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(opened).toEqual(['Knowledge/out.md']);
  });

  it('does NOT surface an INCIDENTAL file (whitelist gate — read/grep goes to rail only)', () => {
    renderHook(() => useCanvasAutoSurface({ pinned: false, muted: false, debounceMs: DEBOUNCE }));
    // A written event but classified incidental → must NOT pop. Mutation-provable:
    // remove the `relevance !== 'deliverable'` gate → this goes RED.
    writeFile('Knowledge/out.md', 'written', undefined, 'incidental');
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(opened).toEqual([]);
  });

  it('coalesces a write-burst to the LAST file', () => {
    renderHook(() => useCanvasAutoSurface({ pinned: false, muted: false, debounceMs: DEBOUNCE }));
    writeFile('a.md');
    writeFile('b.md');
    writeFile('c.md');
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(opened).toEqual(['c.md']);
  });

  it('SUPPRESSES when the user is viewing a file THEY opened (Gate-2 HIGH)', () => {
    renderHook(() => useCanvasAutoSurface({ pinned: false, muted: false, debounceMs: DEBOUNCE }));
    // User opened their own file
    setPanelOpen(true);
    setCurrentFile('src/user-picked.ts');
    writeFile('Knowledge/out.md');
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(opened).toEqual([]); // don't steal the user's chosen view
  });

  it('does NOT self-suppress after auto-opening (fires again on the next output)', () => {
    // The Gate-2 HIGH: auto-open A → panel shows A → next write B must still fire,
    // because the panel is showing what WE surfaced, not a user choice.
    renderHook(() => useCanvasAutoSurface({ pinned: false, muted: false, debounceMs: DEBOUNCE }));
    writeFile('Knowledge/a.md');
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(opened).toEqual(['Knowledge/a.md']);
    // Simulate the panel opening + echoing the file WE opened (resolved path)
    setPanelOpen(true);
    setCurrentFile('/resolved/Knowledge/a.md');
    // Next output must NOT be suppressed
    writeFile('Knowledge/b.md');
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(opened).toEqual(['Knowledge/a.md', 'Knowledge/b.md']);
  });

  it('SUPPRESSES when pinned', () => {
    renderHook(() => useCanvasAutoSurface({ pinned: true, muted: false, debounceMs: DEBOUNCE }));
    writeFile('Knowledge/out.md');
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(opened).toEqual([]);
  });

  it('SUPPRESSES when muted', () => {
    renderHook(() => useCanvasAutoSurface({ pinned: false, muted: true, debounceMs: DEBOUNCE }));
    writeFile('Knowledge/out.md');
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(opened).toEqual([]);
  });

  it('ignores read/searched operations', () => {
    renderHook(() => useCanvasAutoSurface({ pinned: false, muted: false, debounceMs: DEBOUNCE }));
    writeFile('a.md', 'read');
    writeFile('b.md', 'searched');
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(opened).toEqual([]);
  });

  it('tab-scope: IGNORES a write stamped with a foreign tab (background tab)', () => {
    renderHook(() => useCanvasAutoSurface({ pinned: false, muted: false, activeTabId: 'tab-A', debounceMs: DEBOUNCE }));
    writeFile('Knowledge/from-B.md', 'written', 'tab-B'); // a background tab's write
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(opened).toEqual([]); // must not leak into the active tab
  });

  it('tab-scope: FIRES for a write stamped with the active tab', () => {
    renderHook(() => useCanvasAutoSurface({ pinned: false, muted: false, activeTabId: 'tab-A', debounceMs: DEBOUNCE }));
    writeFile('Knowledge/from-A.md', 'written', 'tab-A');
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(opened).toEqual(['Knowledge/from-A.md']);
  });

  it('tab-scope: FAILS OPEN when the event is unstamped (no tabId)', () => {
    renderHook(() => useCanvasAutoSurface({ pinned: false, muted: false, activeTabId: 'tab-A', debounceMs: DEBOUNCE }));
    writeFile('Knowledge/legacy.md'); // no tabId stamp
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(opened).toEqual(['Knowledge/legacy.md']); // no regression for un-updated dispatchers
  });

  it('source-final AUTO-POPS at pipeline finish (run_d3cc1f2c — Option A)', () => {
    // The pipeline-finish batch (surface_run_outputs → kind=source-final) must now
    // auto-open Canvas, subject to the SAME gentle suppression as content/knowledge
    // (NOT a bypass). Mutation-provable: remove source-final from the kind-gate → RED.
    renderHook(() => useCanvasAutoSurface({ pinned: false, muted: false, debounceMs: DEBOUNCE }));
    writeFile('backend/core/x.py', 'written', undefined, 'deliverable', 'source-final');
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(opened).toEqual(['backend/core/x.py']);
  });

  it('source-final coalesces to the LAST changed file (renders the last, per Option A)', () => {
    renderHook(() => useCanvasAutoSurface({ pinned: false, muted: false, debounceMs: DEBOUNCE }));
    writeFile('backend/a.py', 'written', undefined, 'deliverable', 'source-final');
    writeFile('backend/b.py', 'written', undefined, 'deliverable', 'source-final');
    writeFile('backend/c.py', 'written', undefined, 'deliverable', 'source-final');
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(opened).toEqual(['backend/c.py']); // the last changed file
  });

  it('source-final still RESPECTS pin/mute/user-viewing (gentle-suppression parity, NOT bypass)', () => {
    // XG: user intent always wins. A finish batch must NOT steal a file the user is
    // actively viewing — it goes to the rail + pill instead. This is the discriminator
    // from the skeptic's rejected "bypass suppression" suggestion.
    renderHook(() => useCanvasAutoSurface({ pinned: false, muted: false, debounceMs: DEBOUNCE }));
    setPanelOpen(true);
    setCurrentFile('src/user-picked.ts');
    writeFile('backend/core/x.py', 'written', undefined, 'deliverable', 'source-final');
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(opened).toEqual([]); // suppressed while viewing user's choice — pill will show
  });

  it('still DROPS mid-run source (only source-FINAL pops, not source)', () => {
    renderHook(() => useCanvasAutoSurface({ pinned: false, muted: false, debounceMs: DEBOUNCE }));
    writeFile('backend/mid.py', 'written', undefined, 'deliverable', 'source');
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(opened).toEqual([]); // mid-run coding edits never auto-pop
  });

  it('stamps the origin tabId on the dispatched open-file (run_d3cc1f2c edit3)', () => {
    // The dispatched OPEN_FILE_EVENT must carry the WRITE's origin tab so useCanvasHost
    // lands it on that tab even if the active tab changed during the debounce window
    // (the run_48a29fc2 class). Captured from the event's own tabId, not activeTabIdRef.
    renderHook(() => useCanvasAutoSurface({ pinned: false, muted: false, activeTabId: 'tab-A', debounceMs: DEBOUNCE }));
    writeFile('Knowledge/out.md', 'written', 'tab-A');
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(openedDetails).toEqual([{ path: 'Knowledge/out.md', tabId: 'tab-A' }]);
  });

  it('source-final ALSO stamps the origin tab (Gate-2: backend omits tabId but the frontend re-stamps every file_changed with capturedTabId before dispatch)', () => {
    // Gate-2 adversarial doubted EDIT 3 helps source-final because build_surface_events
    // (backend) emits no tabId. But useChatStreamingLifecycle re-stamps EVERY file_changed
    // SSE event (incl. source-final) with capturedTabId (the stream's origin tab) before
    // dispatching swarm:file-changed — so evtTabId IS populated here. This pins that: a
    // source-final write stamped tab-A lands on tab-A, not the active tab.
    renderHook(() => useCanvasAutoSurface({ pinned: false, muted: false, activeTabId: 'tab-A', debounceMs: DEBOUNCE }));
    writeFile('backend/core/x.py', 'written', 'tab-A', 'deliverable', 'source-final');
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(openedDetails).toEqual([{ path: 'backend/core/x.py', tabId: 'tab-A' }]);
  });

  it('skips bookkeeping writes (kind=process — git-verdict authority)', () => {
    // run_4de279ca: the frontend .artifacts/ path denylist was REMOVED; the backend
    // git verdict `kind` is now the sole authority. A bookkeeping write arrives
    // stamped kind='process' → suppressed by the PRIMARY kind gate (never auto-pops).
    renderHook(() => useCanvasAutoSurface({ pinned: false, muted: false, debounceMs: DEBOUNCE }));
    writeFile('Projects/P/.artifacts/runs/x/run.json', 'written', undefined, 'deliverable', 'process');
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(opened).toEqual([]);
  });

  // ── Streaming gate + fail-closed (bug1: restart must not auto-open history) ──
  // The gate activates ONLY when `isStreaming` is explicitly provided (production
  // always passes it). When absent, legacy behavior is preserved (the 11 tests above).
  it('streaming-gate: SUPPRESSES a write that arrives while NOT streaming (historical remount)', () => {
    // On restart, keep-mounted MergedToolBlocks re-dispatch swarm:file-referenced
    // with the tab NOT streaming — this must NOT auto-open a past-session file.
    renderHook(() => useCanvasAutoSurface({ pinned: false, muted: false, activeSessionId: 'tab-A', isStreaming: false, debounceMs: DEBOUNCE }));
    writeFile('Projects/SwarmAI/2-understanding/TECH.md', 'written', 'tab-A');
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(opened).toEqual([]); // no historical auto-open on restart
  });

  it('streaming-gate: FIRES a write that arrives while streaming (live output)', () => {
    renderHook(() => useCanvasAutoSurface({ pinned: false, muted: false, activeSessionId: 'tab-A', isStreaming: true, debounceMs: DEBOUNCE }));
    writeFile('Knowledge/live.md', 'written', 'tab-A');
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(opened).toEqual(['Knowledge/live.md']);
  });

  it('fail-closed: SUPPRESSES when gated but activeSessionId is absent (tab unresolved on restart)', () => {
    // isStreaming provided (gate active) but the active tab has no resolved session
    // yet → fail CLOSED (opposite of the legacy unstamped-event fail-open).
    renderHook(() => useCanvasAutoSurface({ pinned: false, muted: false, activeSessionId: undefined, isStreaming: true, debounceMs: DEBOUNCE }));
    writeFile('Knowledge/x.md', 'written', 'tab-A');
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(opened).toEqual([]); // no session baseline → don't surface
  });

  it('G3: a live write during the session-resolving window FIRES once the session resolves (not dropped)', () => {
    // Startup window: isStreaming=true but activeSessionId not yet resolved. The
    // write must be HELD by the debounce and fire once the session resolves within
    // the window — NOT permanently dropped at arrival.
    const { rerender } = renderHook(
      ({ sid }) => useCanvasAutoSurface({ pinned: false, muted: false, activeSessionId: sid, isStreaming: true, debounceMs: DEBOUNCE }),
      { initialProps: { sid: undefined as string | undefined } },
    );
    writeFile('Knowledge/live.md', 'written'); // unstamped (session not resolved yet)
    rerender({ sid: 'tab-A' });                // session resolves during the window
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(opened).toEqual(['Knowledge/live.md']);
  });

  it('re-fires after the user closes their file (suppression is live, not latched)', () => {
    renderHook(() => useCanvasAutoSurface({ pinned: false, muted: false, debounceMs: DEBOUNCE }));
    setPanelOpen(true);
    setCurrentFile('src/user-picked.ts'); // user viewing their own file
    writeFile('a.md');
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(opened).toEqual([]); // suppressed while viewing user's choice
    setPanelOpen(false); // user closed it → clears current + last-auto
    writeFile('b.md');
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(opened).toEqual(['b.md']); // fires once view is released
  });
});
