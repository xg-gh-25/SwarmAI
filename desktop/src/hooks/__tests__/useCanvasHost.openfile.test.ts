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

describe('useCanvasHost — invalid file does NOT open Canvas (run_f49d3ff3 R1)', () => {
  it('a path that resolves 404 does NOT open Canvas (no empty render) and emits a toast', async () => {
    const api = (await import('../../services/api')).default;
    (api.get as ReturnType<typeof vi.fn>).mockImplementationOnce(async () => {
      const err = new Error('not found') as Error & { response?: { status?: number } };
      err.response = { status: 404 };
      throw err;
    });
    const toasts: string[] = [];
    const onToast = (e: Event) => toasts.push((e as CustomEvent).detail?.message);
    document.addEventListener('swarm:toast', onToast);
    try {
      const { result } = renderHook(() =>
        useCanvasHost({ activeTabId: 'tab-A', sessionId: 's-A', isStreaming: false }),
      );
      await act(async () => { openFile('nope/ghost.json'); await Promise.resolve(); await Promise.resolve(); });
      // Invalid file → Canvas stays closed, no empty render.
      expect(result.current.file).toBeNull();
      expect(result.current.isOpen).toBe(false);
      // User sees why.
      expect(toasts.some((m) => m && m.includes('ghost.json'))).toBe(true);
    } finally {
      document.removeEventListener('swarm:toast', onToast);
    }
  });

  it('a 500/network error still falls through to the raw path (NOT treated as invalid — amendment 4)', async () => {
    const api = (await import('../../services/api')).default;
    (api.get as ReturnType<typeof vi.fn>).mockImplementationOnce(async () => {
      const err = new Error('server error') as Error & { response?: { status?: number } };
      err.response = { status: 500 };
      throw err;
    });
    const { result } = renderHook(() =>
      useCanvasHost({ activeTabId: 'tab-A', sessionId: 's-A', isStreaming: false }),
    );
    await act(async () => { openFile('real/exists.md'); await Promise.resolve(); await Promise.resolve(); });
    // 500 is transient/unknown, NOT "file invalid" → keep the existing fall-through (opens on raw path).
    await waitFor(() => expect(result.current.file?.fileName).toBe('exists.md'));
  });
});

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

  // ── Un-rail AT THE WRITE (Bug 2 root fix, run_5f5e7675) ──────────────────────
  // A new file landing on a tab un-rails THAT tab's slice in the same write. This
  // replaces the render-timing-fragile panel effect. These tests drive the REAL
  // swarm:open-file chokepoint where the invariant now lives.
  it('opening a NEW file un-rails the owning tab (railed → reveal)', async () => {
    const { result } = renderHook(
      ({ t }) => useCanvasHost({ activeTabId: t, sessionId: 's-' + t, isStreaming: false }),
      { initialProps: { t: 'tab-A' } },
    );
    // Rail tab A, then open a file → un-rails (the "I want to see this" intent).
    act(() => result.current.setCollapse({ railed: true }));
    expect(result.current.collapse.railed).toBe(true);
    await act(async () => { openFile('a/alpha.md'); });
    await waitFor(() => expect(result.current.file?.fileName).toBe('alpha.md'));
    expect(result.current.collapse.railed).toBe(false);
  });

  it('re-opening the SAME file does NOT un-rail (a deliberate manual re-rail is honored)', async () => {
    const { result } = renderHook(
      ({ t }) => useCanvasHost({ activeTabId: t, sessionId: 's-' + t, isStreaming: false }),
      { initialProps: { t: 'tab-A' } },
    );
    await act(async () => { openFile('a/alpha.md'); });
    await waitFor(() => expect(result.current.file?.fileName).toBe('alpha.md'));
    // User rails while viewing alpha.md, then the SAME path is re-dispatched (e.g. a
    // re-render/re-emit) → must NOT pop open (path unchanged = not a new-file intent).
    act(() => result.current.setCollapse({ railed: true }));
    await act(async () => { openFile('a/alpha.md'); });
    await waitFor(() => expect(result.current.file?.fileName).toBe('alpha.md'));
    expect(result.current.collapse.railed).toBe(true);
  });

  it('REGRESSION (adversarial HIGH): switching back to a railed tab does NOT un-rail it', async () => {
    // The two-commit race the old panel effect had: railTabId (immediate) vs slice
    // (restored one commit later) diverged on switch, false-firing un-rail. With the
    // invariant at the write chokepoint, a tab SWITCH never runs open-file → a
    // restored railed tab stays railed. This is the real production scenario.
    const { result, rerender } = renderHook(
      ({ t }) => useCanvasHost({ activeTabId: t, sessionId: 's-' + t, isStreaming: false }),
      { initialProps: { t: 'tab-A' } },
    );
    // Tab A: open a file, then rail it.
    await act(async () => { openFile('a/alpha.md'); });
    await waitFor(() => expect(result.current.file?.fileName).toBe('alpha.md'));
    act(() => result.current.setCollapse({ railed: true }));
    // Tab B: open its own file (B is not railed).
    act(() => { rerender({ t: 'tab-B' }); });
    await act(async () => { openFile('b/beta.md', 'tab-B'); });
    await waitFor(() => expect(result.current.file?.fileName).toBe('beta.md'));
    // Switch BACK to A → A's railed state must be preserved (NOT popped open).
    act(() => { rerender({ t: 'tab-A' }); });
    expect(result.current.collapse.railed).toBe(true);
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

  it('an agent-stamped open-canvas (detail.tabId=A) opens A even when tab B is active (sibling bleed, run_10c51cac)', () => {
    // The bare manual-open command has the SAME cross-tab bleed as open-file
    // (run_48a29fc2): the agent's ui_command → swarm:open-canvas fires MID-STREAM
    // from the ORIGINATING tab's stream, seconds after send. If the user switched
    // to B, onOpenCanvas would patch B (the live active tab) instead of A (the
    // origin). The producer now stamps detail.tabId; onOpenCanvas must prefer it.
    const { result, rerender } = renderHook(
      ({ t }) => useCanvasHost({ activeTabId: t, sessionId: 's-' + t, isStreaming: false }),
      { initialProps: { t: 'tab-B' } }, // user is on B when the agent's event fires
    );
    // agent's open-canvas carries its origin tab A (not the active B)
    act(() => { window.dispatchEvent(new CustomEvent('swarm:open-canvas', { detail: { tabId: 'tab-A' } })); });
    // B is active but the open was stamped for A → B must NOT open
    expect(result.current.isOpen).toBe(false);
    // switch to A → Canvas is open there (manuallyOpen landed on its origin tab)
    act(() => { rerender({ t: 'tab-A' }); });
    expect(result.current.isOpen).toBe(true);
  });

  it('a bare open-canvas (no detail.tabId) still opens the ACTIVE tab (user-click path unchanged)', () => {
    // ChatPage's onOpenCanvas dispatches bare swarm:open-canvas synchronously with
    // the click → no tabId → must fall back to the active tab (regression guard).
    const { result, rerender } = renderHook(
      ({ t }) => useCanvasHost({ activeTabId: t, sessionId: 's-' + t, isStreaming: false }),
      { initialProps: { t: 'A' } },
    );
    act(() => { window.dispatchEvent(new CustomEvent('swarm:open-canvas')); });
    expect(result.current.isOpen).toBe(true);
    rerender({ t: 'B' });
    expect(result.current.isOpen).toBe(false);
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
