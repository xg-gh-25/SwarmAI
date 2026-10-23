/**
 * Canvas proprioception run 1 (canvas-proprioception.md §3, §5, §7):
 *
 * - T8  "new" is exact: a delete or a replaced row never marks a seen row new nor
 *       a new row seen; the pill count follows (lastSeenOutputCount = count − unseen).
 * - T9  agent Canvas commands: ONE writer (this hook) settles each report —
 *       ignored on the spot, ignored after resolve (404 / unsaved edits), applied,
 *       pending while resolve is in flight; the send read hands them over once.
 * - T12 quiet state (view report, command results, the send read) renders nothing.
 * - T1  the send read is synchronous: with 500 rows the first read after a change
 *       groups them once (< 5 ms under load), later reads hit the memo (< 1 ms);
 *       the payload is ≤ 2 KB.
 * - the snapshot reports visible (hidden / rail / open), tab, folded list, the file
 *   with who opened it and the reported view (only for the same path).
 *
 * Drives the real hook with the fake Canvas backend (test-utils/fakeCanvasBackend).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { Mock } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useCanvasHost } from '../useCanvasHost';
import api from '../../services/api';
import { resetCanvasSession } from '../../services/canvasSession';
import { installFakeCanvasBackend, settleCanvas } from '../../test-utils/fakeCanvasBackend';
import { dispatchUiCommand } from '../../utils/uiCommands';
import { toEditorContextPayload } from '../../utils/uiContext';

vi.mock('../../services/api', () => ({
  default: { get: vi.fn().mockResolvedValue({ data: { resolved_path: 'resolved.md' } }) },
}));

const write = (tabId: string, path: string, operation: 'written' | 'deleted' = 'written') =>
  window.dispatchEvent(new CustomEvent('swarm:file-changed', {
    detail: { path, tabId, operation, relevance: 'deliverable', kind: 'content' },
  }));

let fake: ReturnType<typeof installFakeCanvasBackend>;
beforeEach(() => { vi.clearAllMocks(); resetCanvasSession(); fake = installFakeCanvasBackend(api.get as Mock); });
afterEach(() => fake.uninstall());

function mount(tab = 'A') {
  let renders = 0;
  const hook = renderHook(({ t }) => { renders += 1; return useCanvasHost({ activeTabId: t, sessionId: 's-' + t, isStreaming: false }); },
    { initialProps: { t: tab } });
  return { ...hook, renders: () => renders };
}

describe('T8 — "new" is exact', () => {
  it('a delete plus a new row: only the new row is new, the pill counts one', async () => {
    const { result } = mount();
    act(() => { write('A', 'a.md'); write('A', 'b.md'); });
    await settleCanvas();
    act(() => { window.dispatchEvent(new CustomEvent('swarm:open-canvas')); });
    act(() => result.current.close());
    act(() => { write('A', 'a.md', 'deleted'); write('A', 'c.md'); });
    await settleCanvas();
    const snap = result.current.getCanvasSnapshot()!;
    expect(snap.outputs.map((o) => [o.path, o.isNew])).toEqual([['c.md', true], ['b.md', false]]);
    expect(result.current.outputCount).toBe(2);
    expect(result.current.lastSeenOutputCount).toBe(1); // 2 listed, 1 unseen → pill shows
  });
});

describe('snapshot fields', () => {
  it('reports visible, tab, folded list, and the file with its reported view', async () => {
    const { result } = mount();
    expect(result.current.getCanvasSnapshot()).toBeNull(); // pristine
    act(() => result.current.setFile({ filePath: 'd.md', fileName: 'd.md', auto: true }));
    act(() => result.current.setFileView({ path: 'd.md', view: 'preview', review: true }));
    act(() => result.current.setCollapse({ outputsCollapsed: true }));
    act(() => result.current.setCanvasTab('changes'));
    expect(result.current.getCanvasSnapshot()).toMatchObject({
      visible: 'open', tab: 'changes', listFolded: true,
      file: { path: 'd.md', openedBy: 'canvas', dirty: false, view: 'preview', review: true },
    });
    act(() => result.current.setCollapse({ railed: true }));
    expect(result.current.getCanvasSnapshot()!.visible).toBe('rail');
    // a view reported for ANOTHER path is never attached to this file
    act(() => result.current.setFileView({ path: 'other.md', view: 'changes', review: false }));
    expect(result.current.getCanvasSnapshot()!.file!.view).toBeUndefined();
  });
});

describe('T9 — command results, one writer', () => {
  it('open-canvas-file: pending while resolving, then applied; the send read hands it over once', async () => {
    let release!: (v: unknown) => void;
    const get = api.get as Mock;
    const prev = get.getMockImplementation();
    get.mockImplementation((url: string, cfg: unknown) => url === '/workspace/file/resolve'
      ? new Promise((r) => { release = r; }) : prev!(url, cfg));
    const { result } = mount();
    act(() => { dispatchUiCommand('open-canvas-file', 'notes/x.md', 'A'); });
    expect(result.current.getCanvasSnapshot()!.commands).toEqual([{ cmd: 'open-canvas-file', result: 'pending' }]);
    await act(async () => { release({ data: { resolved_path: 'notes/x.md' } }); await Promise.resolve(); });
    const taken = result.current.takeCanvasSnapshotFor('A')!;
    expect(taken.commands).toEqual([{ cmd: 'open-canvas-file', result: 'applied' }]);
    expect(result.current.getCanvasSnapshot()!.commands).toEqual([]); // handed over
  });

  it('ignored: on the spot, file not found, and unsaved edits — each with its reason', async () => {
    const get = api.get as Mock;
    const prev = get.getMockImplementation();
    get.mockImplementation((url: string, cfg: { params?: { path?: string } }) => {
      if (url !== '/workspace/file/resolve') return prev!(url, cfg);
      if (cfg?.params?.path === 'gone.md') return Promise.reject({ response: { status: 404 } });
      return Promise.resolve({ data: { resolved_path: cfg?.params?.path } });
    });
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    const { result } = mount();
    act(() => result.current.setFile({ filePath: 'mine.md', fileName: 'mine.md' }));
    act(() => result.current.setFileDirty(true));
    await act(async () => {
      dispatchUiCommand('open-canvas-file', '../escape.md', 'A');
      dispatchUiCommand('open-canvas-file', 'gone.md', 'A');
      dispatchUiCommand('open-canvas-file', 'other.md', 'A');
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(result.current.getCanvasSnapshot()!.commands).toEqual([
      { cmd: 'open-canvas-file', result: 'ignored', reason: 'path must be workspace-relative' },
      { cmd: 'open-canvas-file', result: 'ignored', reason: 'file not found' },
      { cmd: 'open-canvas-file', result: 'ignored', reason: expect.stringMatching(/unsaved edits/) },
    ]);
  });

  it('open-canvas on a folded Canvas is applied and says it stays folded', () => {
    const { result } = mount();
    act(() => result.current.setFile({ filePath: 'a.md', fileName: 'a.md' }));
    act(() => result.current.setCollapse({ railed: true }));
    act(() => { dispatchUiCommand('open-canvas', undefined, 'A'); });
    expect(result.current.getCanvasSnapshot()!.commands).toEqual([
      { cmd: 'open-canvas', result: 'applied', reason: 'Canvas stays folded to the side rail' },
    ]);
  });

  it('a background tab command lands on that tab only', () => {
    const { result } = mount('A');
    act(() => { dispatchUiCommand('open-canvas', undefined, 'B'); });
    expect(result.current.getCanvasSnapshot()).toBeNull(); // A untouched
    expect(result.current.getCanvasSnapshotFor('B')!.commands[0]).toMatchObject({ cmd: 'open-canvas', result: 'applied' });
  });
});

describe('T12 — quiet state renders nothing', () => {
  it('view reports, command results and the send read cause zero renders', () => {
    const { result, renders } = mount();
    act(() => result.current.setFile({ filePath: 'a.md', fileName: 'a.md' }));
    const before = renders();
    act(() => {
      result.current.setFileView({ path: 'a.md', view: 'edit', review: false });
      result.current.setFileView({ path: 'a.md', view: 'changes', review: true });
      window.dispatchEvent(new CustomEvent('swarm:ui-command-result', { detail: { id: 'x1', tabId: 'A', cmd: 'open-canvas-file', result: 'ignored', reason: 'r' } }));
      result.current.takeCanvasSnapshotFor('A');
    });
    expect(renders()).toBe(before);
  });
});

describe('T1 — the send read is cheap', () => {
  it('500 rows: < 1 ms per read, payload ≤ 2 KB', async () => {
    const { result } = mount();
    act(() => { for (let i = 0; i < 500; i++) write('A', `deliverables/report-${i}.md`); });
    await settleCanvas();
    act(() => result.current.setFile({ filePath: 'deliverables/report-1.md', fileName: 'report-1.md' }));
    act(() => { dispatchUiCommand('open-canvas', undefined, 'A'); });
    // cold: the first read after the rows changed groups them once
    const c0 = performance.now();
    const snap = result.current.getCanvasSnapshotFor('A')!;
    const cold = performance.now() - c0;
    expect(snap.outputCount).toBe(500);
    // warm: every later send reads the memoized rows
    const N = 200;
    const t0 = performance.now();
    for (let i = 0; i < N; i++) result.current.getCanvasSnapshotFor('A');
    const per = (performance.now() - t0) / N;
    console.log(`T1 send read, 500 rows: cold ${cold.toFixed(2)}ms, warm ${(per * 1000).toFixed(0)}µs`);
    expect(cold).toBeLessThan(5);
    expect(per).toBeLessThan(1);
    const bytes = JSON.stringify(toEditorContextPayload({ filePath: snap.file!.path, fileName: snap.file!.name, canvas: snap })).length;
    expect(bytes).toBeLessThanOrEqual(2048);
  });
});
