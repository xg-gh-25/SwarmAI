/**
 * useCanvasHost × the Runs tab (design "the runs this session dispatched, visible in
 * the dispatching tab" r2 §3, invariant 5 and §6).
 *
 * Questions: does a tab's runs mark (swarm:open-canvas-runs with its tab id) open that
 * tab's Canvas on Runs, also while another tab is active; does a tab that changes
 * session show Artifacts instead of the old session's Runs; does a tab switch leave the
 * other tab's Canvas tab as it was; does an auto-surface on a tab showing Runs open
 * nothing; does a file open bring Artifacts forward.
 *
 * Methodology: the real host; services/api `get` mocked (the rail's refresh).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

const get = vi.fn();
vi.mock('../../services/api', () => ({ default: { get: (...a: unknown[]) => get(...a), post: vi.fn() } }));

import { useCanvasHost } from '../useCanvasHost';
import { resetCanvasSession } from '../../services/canvasSession';
import { openCanvasRuns } from '../../utils/canvasRuns';
import { OPEN_FILE_EVENT } from '../../components/common/MarkdownRenderer';

type P = { activeTabId: string; sessionId: string | null; isStreaming: boolean };
const host = (initial: P) => renderHook((p: P) => useCanvasHost(p), { initialProps: initial });

beforeEach(() => {
  resetCanvasSession();
  get.mockReset();
  // the rail's refresh; an open's resolve answers the path it was asked for
  get.mockImplementation(async (url: string, cfg?: { params?: { path?: string } }) => (String(url).includes('resolve')
    ? { data: { resolved_path: cfg?.params?.path ?? 'x' } }
    : { data: { artifacts: [], changes: { turns: [], commits: [] } } }));
});

describe('the Canvas Runs tab', () => {
  it('the active tab\'s mark opens its Canvas on Runs', () => {
    const { result } = host({ activeTabId: 'A', sessionId: 'S1', isStreaming: false });
    expect(result.current.isOpen).toBe(false);
    act(() => openCanvasRuns({ tabId: 'A', sessionId: 'S1' }));
    expect(result.current.isOpen).toBe(true);
    expect(result.current.canvasTab).toBe('runs');
  });

  it('a background tab\'s mark opens that tab on Runs, and leaves the active one alone', () => {
    const { result, rerender } = host({ activeTabId: 'A', sessionId: 'S1', isStreaming: false });
    act(() => result.current.setCanvasTab('changes'));
    act(() => openCanvasRuns({ tabId: 'B', sessionId: 'S2' }));
    expect(result.current.canvasTab).toBe('changes');          // A unchanged
    rerender({ activeTabId: 'B', sessionId: 'S2', isStreaming: false });
    expect(result.current.isOpen).toBe(true);
    expect(result.current.canvasTab).toBe('runs');
    rerender({ activeTabId: 'A', sessionId: 'S1', isStreaming: false });
    expect(result.current.canvasTab).toBe('changes');          // no bleed back
  });

  it('a tab that changes session shows Artifacts, not the old session\'s Runs', () => {
    const { result, rerender } = host({ activeTabId: 'A', sessionId: 'S1', isStreaming: false });
    act(() => result.current.setCanvasTab('runs'));
    expect(result.current.canvasTab).toBe('runs');
    rerender({ activeTabId: 'A', sessionId: 'S9', isStreaming: false });   // reset / resume
    expect(result.current.canvasTab).toBe('artifacts');
    expect(result.current.getCanvasSnapshot()?.tab ?? 'artifacts').toBe('artifacts');
  });

  it('a file open brings Artifacts forward', async () => {
    const { result } = host({ activeTabId: 'A', sessionId: 'S1', isStreaming: false });
    act(() => openCanvasRuns());
    expect(result.current.canvasTab).toBe('runs');
    act(() => result.current.setFile({ filePath: 'Knowledge/x.md', fileName: 'x.md' }));
    expect(result.current.canvasTab).toBe('artifacts');
  });

  it('an auto-surface on the tab showing Runs opens nothing', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    try {
      const opened: string[] = [];
      const on = (e: Event) => opened.push((e as CustomEvent<{ path: string }>).detail.path);
      document.addEventListener(OPEN_FILE_EVENT, on);
      const write = (path: string) => act(() => {
        window.dispatchEvent(new CustomEvent('swarm:file-changed', { detail: { path, operation: 'written', kind: 'content', tabId: 'A' } }));
      });
      const { result } = host({ activeTabId: 'A', sessionId: 'S1', isStreaming: true });
      write('Knowledge/first.md');                 // on Artifacts: it surfaces (the control)
      await act(async () => {
        vi.advanceTimersByTime(1000);
        for (let i = 0; i < 20; i++) await Promise.resolve();   // the open's resolve settles
      });
      expect(opened).toEqual(['Knowledge/first.md']);
      act(() => openCanvasRuns());
      write('Knowledge/out.md');
      act(() => { vi.advanceTimersByTime(1000); });
      document.removeEventListener(OPEN_FILE_EVENT, on);
      expect(opened).toEqual(['Knowledge/first.md']);
      expect(result.current.canvasTab).toBe('runs');
    } finally {
      vi.useRealTimers();
    }
  });
});
