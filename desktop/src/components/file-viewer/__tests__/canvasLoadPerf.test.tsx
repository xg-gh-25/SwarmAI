/**
 * Canvas loading performance (super-canvas-fixing.md C5).
 *
 * 7. FileViewer renders a text file as soon as the file arrives; the committed
 *    baseline (git show, ~120 ms on the daemon) loads in parallel and reaches the
 *    editor when it lands. A file opened ON its diff (autoDiff) still waits for it.
 * 8. FileEditorCore coalesces swarm:file-changed for its file: a burst inside the
 *    window is one read; while a read is in flight a newer event schedules exactly
 *    one more, and only the newest read is applied.
 * 9. The changed-lines overlay renders one node per changed line plus one per run
 *    of unchanged lines before them, not one per line of the file.
 *
 * api.get is the boundary (held on deferreds where order matters); timers faked
 * for the coalescing window.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act, waitFor } from '@testing-library/react';
import { FILE_CHANGE_COALESCE_MS, changedLineSegments } from '../../common/FileEditorCore';

type Resolve = (v: unknown) => void;
const held: Record<string, Resolve[]> = {};
let mode: 'hold-committed' | 'hold-file' = 'hold-committed';
const get = vi.fn(async (url: string) => {
  if (url === '/workspace/file/committed' && mode === 'hold-committed') {
    return new Promise((r) => { (held[url] ??= []).push(r); });
  }
  if (url === '/workspace/file' && mode === 'hold-file') {
    return new Promise((r) => { (held[url] ??= []).push(r); });
  }
  if (url === '/workspace/file') return { data: { content: 'one\ntwo\n', encoding: 'utf-8', size: 8, name: 'a.ts', path: 'p' } };
  return { data: { content: 'base\n' } };
});
vi.mock('../../../services/api', () => ({ default: { get: (u: string) => get(u), put: vi.fn() } }));
vi.mock('../renderers/CsvRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/ImageRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/PdfRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/HtmlRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/VideoRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/AudioRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/UnsupportedRenderer', () => ({ default: () => <div /> }));
import FileViewer from '../FileViewer';
import FileEditorCore from '../../common/FileEditorCore';

beforeEach(() => { get.mockClear(); for (const k of Object.keys(held)) delete held[k]; mode = 'hold-committed'; });

describe('7 — first paint does not wait for the committed baseline', () => {
  it('renders the content while /committed is still pending, then enables Changes when it lands', async () => {
    render(<FileViewer variant="panel" onClose={vi.fn()} initialFile={{ filePath: '/ws/a.ts', fileName: 'a.ts' }} />);
    const ta = await screen.findByTestId('file-editor-textarea') as HTMLTextAreaElement;
    expect(ta.value).toBe('one\ntwo\n');
    expect(held['/workspace/file/committed']).toHaveLength(1); // still pending
    expect((screen.getByTestId('editor-view-changes') as HTMLButtonElement).disabled).toBe(true);
    await act(async () => { held['/workspace/file/committed'][0]({ data: { content: 'base\n' } }); });
    await waitFor(() => expect((screen.getByTestId('editor-view-changes') as HTMLButtonElement).disabled).toBe(false));
    expect(ta.value).toBe('one\ntwo\n'); // the baseline never resets the text
  });

  it('a file opened on its diff waits for the baseline (it has nothing to show without it)', async () => {
    render(<FileViewer variant="panel" onClose={vi.fn()} initialFile={{ filePath: '/ws/a.ts', fileName: 'a.ts', autoDiff: true }} />);
    await waitFor(() => expect(held['/workspace/file/committed']).toHaveLength(1));
    expect(screen.queryByTestId('file-editor-core')).toBeNull();
    await act(async () => { held['/workspace/file/committed'][0]({ data: { content: 'base\n' } }); });
    expect(await screen.findByTestId('diff-view')).toBeTruthy();
  });
});

describe('8 — swarm:file-changed is coalesced', () => {
  afterEach(() => vi.useRealTimers());
  const changed = () => window.dispatchEvent(new CustomEvent('swarm:file-changed', { detail: { path: '/ws/src/x.ts' } }));
  const reads = () => get.mock.calls.filter((c) => c[0] === '/workspace/file').length;

  it('a burst is one read; an event during the read schedules one more; the newest wins', async () => {
    vi.useFakeTimers();
    mode = 'hold-file';
    render(<FileEditorCore variant="panel" filePath="src/x.ts" fileName="x.ts" workspaceId="" initialContent="v0\n" onSave={vi.fn()} onClose={vi.fn()} />);
    await act(async () => { for (let i = 0; i < 5; i++) changed(); });
    expect(reads()).toBe(0);
    await act(async () => { vi.advanceTimersByTime(FILE_CHANGE_COALESCE_MS); });
    expect(reads()).toBe(1);
    // more writes while the read is in flight
    await act(async () => { changed(); changed(); vi.advanceTimersByTime(FILE_CHANGE_COALESCE_MS); });
    expect(reads()).toBe(1); // still one in flight
    await act(async () => { held['/workspace/file'][0]({ data: { content: 'v1\n' } }); });
    await act(async () => { vi.advanceTimersByTime(FILE_CHANGE_COALESCE_MS); });
    expect(reads()).toBe(2); // exactly one follow-up
    await act(async () => { held['/workspace/file'][1]({ data: { content: 'v2\n' } }); });
    expect((screen.getByTestId('file-editor-textarea') as HTMLTextAreaElement).value).toBe('v2\n');
    // the overlay marks the one changed line with one node (not one per line)
    expect(screen.getAllByTestId('changed-line')).toHaveLength(1);
  });
});

describe('9 — the changed-lines overlay grows with the changes, not the file', () => {
  it('segments: changed lines alone, unchanged runs joined, nothing after the last change', () => {
    const content = Array.from({ length: 2000 }, (_, i) => `line ${i + 1}`).join('\n');
    const segs = changedLineSegments(content, new Set([10, 11, 500]));
    expect(segs.map((s) => s.changed)).toEqual([false, true, true, false, true]);
    expect(segs[0].text.split('\n')).toHaveLength(9);
    expect(segs[1].text).toBe('line 10');
    expect(segs[3].text.split('\n')).toHaveLength(488);
    expect(segs[4].text).toBe('line 500');
  });

  it('a change on line 1 has no leading run; no change, no segments', () => {
    expect(changedLineSegments('a\nb', new Set([1])).map((s) => s.changed)).toEqual([true]);
    expect(changedLineSegments('a\nb', new Set())).toEqual([]);
  });
});
