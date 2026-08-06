/**
 * useCanvasHost — swarm:open-file → tab-switch bleed repro (run_a9806ea0).
 *
 * The existing useCanvasHost.test.ts drives state via the returned setters
 * (setFile/patch) and passes. This repro drives the REAL entry point a manual
 * file-click uses — the `swarm:open-file` DOCUMENT event + the async /resolve —
 * then switches the active tab, exercising the interaction between the once-
 * registered listener effect (deps []) and the restore effect (deps [activeTabId]).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useCanvasHost } from '../useCanvasHost';

vi.mock('../../services/api', () => ({
  default: { get: vi.fn(async (_url: string, opts?: { params?: { path?: string } }) => ({ data: { resolved_path: opts?.params?.path ?? '' } })) },
}));

function openFile(path: string, tabId?: string) {
  document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: tabId ? { path, tabId } : { path } }));
}

beforeEach(() => { sessionStorage.clear(); });
afterEach(() => { vi.restoreAllMocks(); });

describe('useCanvasHost — open-file then switch tab (real event path)', () => {
  it('a file opened on tab A does NOT appear on tab B after switch', async () => {
    const { result, rerender } = renderHook(
      ({ t }) => useCanvasHost({ activeTabId: t, sessionId: 's-' + t, isStreaming: false }),
      { initialProps: { t: 'tab-A' } },
    );

    // open a file on tab A via the real swarm:open-file event (async resolve)
    await act(async () => { openFile('a/alpha.md'); });
    await waitFor(() => expect(result.current.file?.fileName).toBe('alpha.md'));
    expect(result.current.isOpen).toBe(true);

    // switch to tab B → B has its own (empty) Canvas; A's file must NOT bleed
    act(() => { rerender({ t: 'tab-B' }); });
    expect(result.current.file).toBeNull();
    expect(result.current.isOpen).toBe(false);

    // switch back to A → A's file restored
    act(() => { rerender({ t: 'tab-A' }); });
    expect(result.current.file?.fileName).toBe('alpha.md');
  });

  it('switching tab DURING the async /resolve lands the file on the ORIGIN tab, not the destination (the real bleed)', async () => {
    // Make /resolve controllable so we can switch tabs WHILE it is in flight.
    let releaseResolve: (v: unknown) => void = () => {};
    const pending = new Promise((res) => { releaseResolve = res; });
    const api = (await import('../../services/api')).default;
    (api.get as ReturnType<typeof vi.fn>).mockImplementationOnce(async (_url: string, opts?: { params?: { path?: string } }) => {
      await pending;
      return { data: { resolved_path: opts?.params?.path ?? '' } };
    });

    const { result, rerender } = renderHook(
      ({ t }) => useCanvasHost({ activeTabId: t, sessionId: 's-' + t, isStreaming: false }),
      { initialProps: { t: 'tab-A' } },
    );

    // user clicks the file on tab A (resolve is now IN FLIGHT, not yet resolved)
    act(() => { openFile('a/alpha.md'); });
    // user switches to tab B BEFORE the resolve returns
    act(() => { rerender({ t: 'tab-B' }); });
    // now the resolve completes
    await act(async () => { releaseResolve(null); await pending; });

    // The file was opened on A → it must NOT bleed onto B (the destination tab).
    expect(result.current.file).toBeNull();
    // and switching back to A shows it (it landed on its ORIGIN tab)
    act(() => { rerender({ t: 'tab-A' }); });
    expect(result.current.file?.fileName).toBe('alpha.md');
  });

  it('two rapid opens on the SAME tab: an out-of-order (later-resolving) OLDER open does NOT clobber the newer file', async () => {
    // file1 resolve is held; file2 resolves immediately. file1 (older) releases LAST.
    let release1: (v: unknown) => void = () => {};
    const p1 = new Promise((res) => { release1 = res; });
    const api = (await import('../../services/api')).default;
    (api.get as ReturnType<typeof vi.fn>)
      .mockImplementationOnce(async () => { await p1; return { data: { resolved_path: 'a/file1.md' } }; })  // file1: slow
      .mockImplementationOnce(async () => ({ data: { resolved_path: 'a/file2.md' } }));                      // file2: fast

    const { result } = renderHook(() => useCanvasHost({ activeTabId: 'tab-A', sessionId: 's', isStreaming: false }));

    act(() => { openFile('a/file1.md'); });   // gen 1 (slow)
    act(() => { openFile('a/file2.md'); });   // gen 2 (fast) — supersedes
    await waitFor(() => expect(result.current.file?.fileName).toBe('file2.md'));  // file2 shows
    // now the STALE file1 resolve finally completes — it must NOT overwrite file2
    await act(async () => { release1(null); await p1; });
    expect(result.current.file?.fileName).toBe('file2.md');
  });

  it('an agent-stamped open-file (detail.tabId=A) lands on tab A even when tab B is active at fire time', async () => {
    // run_48a29fc2: the agent (ui_command→swarm:open-file) fires the event MID-STREAM,
    // seconds after send. If the user switched to B during the turn, activeTabIdRef is
    // now B — but the file's ORIGIN is A. The event carries detail.tabId=A (the stream's
    // captured origin tab, mirroring file_changed's _stampTab), which handleOpenFile must
    // prefer over the live active tab. Without the fix, landingTab=activeTabIdRef=B → the
    // file lands on B (the observed cross-tab bleed).
    const { result, rerender } = renderHook(
      ({ t }) => useCanvasHost({ activeTabId: t, sessionId: 's-' + t, isStreaming: false }),
      { initialProps: { t: 'tab-B' } },  // user is on B when the agent's event fires
    );
    // agent's event carries its origin tab A (not the active B)
    await act(async () => { openFile('a/alpha.md', 'tab-A'); });
    // B is active but the file was stamped for A → B must NOT show it
    expect(result.current.file).toBeNull();
    // switch to A → the file is there (landed on its stamped origin tab)
    act(() => { rerender({ t: 'tab-A' }); });
    await waitFor(() => expect(result.current.file?.fileName).toBe('alpha.md'));
  });

  it('open-file while activeTabId is momentarily null lands correctly once a real tab is active', async () => {
    // Simulates the manual-open race: the click fires before/around a tab resolve.
    const { result, rerender } = renderHook(
      ({ t }) => useCanvasHost({ activeTabId: t, sessionId: null, isStreaming: false }),
      { initialProps: { t: null as string | null } },
    );
    await act(async () => { openFile('a/alpha.md'); });
    // lands under the '__no_tab__' key; now a real tab becomes active
    act(() => { rerender({ t: 'tab-A' }); });
    // tab A is a DISTINCT tab — the file opened under no-tab must NOT show on A
    expect(result.current.file).toBeNull();
  });
});
