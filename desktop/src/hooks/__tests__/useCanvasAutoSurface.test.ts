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

function writeFile(path: string, operation = 'written') {
  document.dispatchEvent(new CustomEvent('swarm:file-referenced', { detail: { path, operation } }));
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

  it('skips bookkeeping paths (.artifacts/)', () => {
    renderHook(() => useCanvasAutoSurface({ pinned: false, muted: false, debounceMs: DEBOUNCE }));
    writeFile('Projects/P/.artifacts/runs/x/run.json');
    vi.advanceTimersByTime(DEBOUNCE + 5);
    expect(opened).toEqual([]);
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
