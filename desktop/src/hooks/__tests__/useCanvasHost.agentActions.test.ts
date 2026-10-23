/**
 * Canvas proprioception run 2 — the agent's Canvas commands (canvas-proprioception.md §4, §7).
 *
 * - T4  a command applies synchronously in the handler (no await on the stream path);
 * - open-canvas unfolds; fold-canvas folds (ignored when closed); canvas-tab-* picks
 *   the tab with an honest reason when the Canvas is closed / folded;
 * - T7  an agent open never replaces unsaved edits and never raises the "discard?"
 *       prompt; a background tab's command lands on that tab only;
 * - T10 a USER open (no `by`) over unsaved edits still raises the prompt;
 * - T11 while the active tab streams, a width-changing command (open / fold / an
 *       open-file on a folded Canvas) waits — reported `pending` — and applies when
 *       the answer finishes; a non-width change (tab) applies at once; a user layout
 *       change drops the waiting one; a newer one replaces it; the deferred open
 *       re-checks unsaved edits when it applies; focus never moves.
 *
 * Drives the real hook + the real dispatcher (utils/uiCommands).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { Mock } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useCanvasHost } from '../useCanvasHost';
import api from '../../services/api';
import { resetCanvasSession } from '../../services/canvasSession';
import { installFakeCanvasBackend } from '../../test-utils/fakeCanvasBackend';
import { dispatchUiCommand } from '../../utils/uiCommands';

vi.mock('../../services/api', () => ({
  default: { get: vi.fn().mockResolvedValue({ data: { resolved_path: 'resolved.md' } }) },
}));

let fake: ReturnType<typeof installFakeCanvasBackend>;
beforeEach(() => {
  vi.clearAllMocks(); resetCanvasSession(); fake = installFakeCanvasBackend(api.get as Mock);
  const get = api.get as Mock;
  const prev = get.getMockImplementation()!;
  get.mockImplementation((url: string, cfg: { params?: { path?: string } }) => url === '/workspace/file/resolve'
    ? Promise.resolve({ data: { resolved_path: cfg?.params?.path } }) : prev(url, cfg));
});
afterEach(() => fake.uninstall());

function mount(streaming = false) {
  return renderHook(({ s }) => useCanvasHost({ activeTabId: 'A', sessionId: 's-A', isStreaming: s }), { initialProps: { s: streaming } });
}
const snap = (r: { current: ReturnType<typeof useCanvasHost> }) => r.current.getCanvasSnapshot()!;
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

describe('T4 + command semantics', () => {
  it('open-canvas applies in the handler: the state is open right after dispatch', () => {
    const { result } = mount();
    act(() => result.current.setFile({ filePath: 'a.md', fileName: 'a.md' }));
    act(() => result.current.setCollapse({ railed: true }));
    act(() => {
      dispatchUiCommand('open-canvas', undefined, 'A');
      expect(result.current.getCanvasSnapshot()!.visible).toBe('open'); // same tick
    });
    expect(result.current.collapse.railed).toBe(false);
  });

  it('fold-canvas folds; ignored when closed; already folded is applied with a note', () => {
    const { result } = mount();
    act(() => { dispatchUiCommand('fold-canvas', undefined, 'A'); });
    expect(result.current.getCanvasSnapshotFor('A')!.commands).toEqual([{ cmd: 'fold-canvas', result: 'ignored', reason: 'Canvas is closed' }]);
    act(() => result.current.setFile({ filePath: 'a.md', fileName: 'a.md' }));
    act(() => { result.current.takeCanvasSnapshotFor('A'); dispatchUiCommand('fold-canvas', undefined, 'A'); });
    expect(snap(result).visible).toBe('rail');
    act(() => { dispatchUiCommand('fold-canvas', undefined, 'A'); });
    expect(snap(result).commands).toEqual([
      { cmd: 'fold-canvas', result: 'applied' },
      { cmd: 'fold-canvas', result: 'applied', reason: 'already folded' },
    ]);
  });

  it('canvas-tab-* picks the tab and says when it is not visible yet', () => {
    const { result } = mount();
    act(() => result.current.setFile({ filePath: 'a.md', fileName: 'a.md' }));
    act(() => { dispatchUiCommand('canvas-tab-changes', undefined, 'A'); });
    expect(result.current.canvasTab).toBe('changes');
    act(() => result.current.setCollapse({ railed: true }));
    act(() => { dispatchUiCommand('canvas-tab-artifacts', undefined, 'A'); });
    expect(snap(result).commands).toEqual([
      { cmd: 'canvas-tab-changes', result: 'applied' },
      { cmd: 'canvas-tab-artifacts', result: 'applied', reason: 'Canvas is folded; the tab shows when it unfolds' },
    ]);
  });
});

describe('T7 / T10 — unsaved edits', () => {
  it('an agent open over unsaved edits is ignored with no prompt; a user open still asks', async () => {
    const { result } = mount();
    act(() => result.current.setFile({ filePath: 'mine.md', fileName: 'mine.md' }));
    act(() => result.current.setFileDirty(true));
    act(() => { dispatchUiCommand('open-canvas-file', 'other.md', 'A'); });
    await flush();
    expect(result.current.pendingOpen).toBeNull();
    expect(result.current.file!.filePath).toBe('mine.md');
    expect(snap(result).commands[0]).toMatchObject({ result: 'ignored', reason: expect.stringMatching(/unsaved edits/) });
    // the user's own open (any other swarm:open-file sender) keeps the prompt
    act(() => { document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path: 'other.md' } })); });
    await flush();
    expect(result.current.pendingOpen?.filePath).toBe('other.md');
  });

  it('a background tab command lands on that tab only', () => {
    const { result } = mount();
    act(() => result.current.setFile({ filePath: 'a.md', fileName: 'a.md' }));
    act(() => { dispatchUiCommand('fold-canvas', undefined, 'B'); });
    expect(snap(result).visible).toBe('open');
    expect(result.current.getCanvasSnapshotFor('B')!.commands[0]).toMatchObject({ cmd: 'fold-canvas', result: 'ignored' });
  });
});

describe('T11 — no chat-width change while the answer streams', () => {
  it('open waits (pending) and applies when the answer finishes; a tab switch applies at once', () => {
    const { result, rerender } = mount(true);
    act(() => result.current.setFile({ filePath: 'a.md', fileName: 'a.md' }));
    act(() => result.current.setCollapse({ railed: true }));
    act(() => { dispatchUiCommand('open-canvas', undefined, 'A'); dispatchUiCommand('canvas-tab-changes', undefined, 'A'); });
    expect(snap(result).visible).toBe('rail');
    expect(result.current.canvasTab).toBe('changes');
    expect(snap(result).commands).toEqual([
      { cmd: 'open-canvas', result: 'pending', reason: 'applies when your answer finishes' },
      { cmd: 'canvas-tab-changes', result: 'applied', reason: 'Canvas is folded; the tab shows when it unfolds' },
    ]);
    rerender({ s: false });
    expect(snap(result).visible).toBe('open');
    expect(snap(result).commands[0]).toEqual({ cmd: 'open-canvas', result: 'applied' }); // same entry, settled
  });

  it('fold waits too; a user layout change drops the waiting command', () => {
    const { result, rerender } = mount(true);
    act(() => result.current.setFile({ filePath: 'a.md', fileName: 'a.md' }));
    act(() => { dispatchUiCommand('fold-canvas', undefined, 'A'); });
    expect(snap(result).visible).toBe('open');
    act(() => result.current.setCollapse({ outputsCollapsed: true })); // not a width change: keeps waiting
    expect(snap(result).commands[0].result).toBe('pending');
    act(() => result.current.close()); // the user closes the Canvas
    rerender({ s: false });
    expect(snap(result).visible).toBe('hidden');
    expect(snap(result).commands).toEqual([{ cmd: 'fold-canvas', result: 'ignored', reason: 'the user changed the Canvas first' }]);
  });

  it('a newer waiting command replaces the older one', () => {
    const { result, rerender } = mount(true);
    act(() => result.current.setFile({ filePath: 'a.md', fileName: 'a.md' }));
    act(() => { dispatchUiCommand('fold-canvas', undefined, 'A'); });
    act(() => { result.current.setCollapse({ railed: true }); }); // user folds (drops it)
    act(() => { dispatchUiCommand('open-canvas', undefined, 'A'); dispatchUiCommand('open-canvas', undefined, 'A'); });
    expect(snap(result).commands.map((c) => c.result)).toEqual(['ignored', 'ignored', 'pending']);
    rerender({ s: false });
    expect(snap(result).visible).toBe('open');
  });

  it('an open-file on a folded Canvas waits, then re-checks unsaved edits when it applies', async () => {
    const { result, rerender } = mount(true);
    act(() => result.current.setFile({ filePath: 'mine.md', fileName: 'mine.md' }));
    act(() => result.current.setCollapse({ railed: true }));
    act(() => { dispatchUiCommand('open-canvas-file', 'other.md', 'A'); });
    await flush();
    expect(snap(result).commands[0]).toMatchObject({ result: 'pending' });
    act(() => result.current.setFileDirty(true)); // the user starts editing meanwhile
    rerender({ s: false });
    expect(result.current.file!.filePath).toBe('mine.md');
    expect(snap(result).commands[0]).toMatchObject({ result: 'ignored', reason: expect.stringMatching(/unsaved edits/) });
  });

  it('commands never move focus', () => {
    const input = document.createElement('textarea');
    document.body.appendChild(input);
    input.focus();
    const { result } = mount();
    act(() => result.current.setFile({ filePath: 'a.md', fileName: 'a.md' }));
    act(() => {
      dispatchUiCommand('fold-canvas', undefined, 'A');
      dispatchUiCommand('open-canvas', undefined, 'A');
      dispatchUiCommand('canvas-tab-changes', undefined, 'A');
    });
    expect(document.activeElement).toBe(input);
    input.remove();
  });
});
