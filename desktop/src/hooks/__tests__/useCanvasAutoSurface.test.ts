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

function writeFile(path: string, operation = 'written', sessionId?: string, relevance = 'deliverable') {
  // Unified backend event (run_e626e121): swarm:file-changed on window, carries
  // the whitelist `relevance` (only 'deliverable' auto-surfaces).
  window.dispatchEvent(new CustomEvent('swarm:file-changed', { detail: { path, operation, relevance, sessionId } }));
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
  let onOpen: (e: Event) => void;

  beforeEach(() => {
    vi.useFakeTimers();
    opened = [];
    onOpen = (e: Event) => opened.push((e as CustomEvent<{ path: string }>).detail.path);
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

  it('tab-scope: IGNORES a write stamped with a foreign session (background tab)', () => {
    renderHook(() => useCanvasAutoSurface({ pinned: false, muted: false, activeSessionId: 'tab-A', debounceMs: DEBOUNCE }));
    writeFile('Knowledge/from-B.md', 'written', 'tab-B'); // a background tab's write
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(opened).toEqual([]); // must not leak into the active tab
  });

  it('tab-scope: FIRES for a write stamped with the active session', () => {
    renderHook(() => useCanvasAutoSurface({ pinned: false, muted: false, activeSessionId: 'tab-A', debounceMs: DEBOUNCE }));
    writeFile('Knowledge/from-A.md', 'written', 'tab-A');
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(opened).toEqual(['Knowledge/from-A.md']);
  });

  it('tab-scope: FAILS OPEN when the event is unstamped (no sessionId)', () => {
    renderHook(() => useCanvasAutoSurface({ pinned: false, muted: false, activeSessionId: 'tab-A', debounceMs: DEBOUNCE }));
    writeFile('Knowledge/legacy.md'); // no sessionId stamp
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(opened).toEqual(['Knowledge/legacy.md']); // no regression for un-updated dispatchers
  });

  it('skips bookkeeping paths (.artifacts/)', () => {
    renderHook(() => useCanvasAutoSurface({ pinned: false, muted: false, debounceMs: DEBOUNCE }));
    writeFile('Projects/P/.artifacts/runs/x/run.json');
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
