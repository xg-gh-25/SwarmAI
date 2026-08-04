/**
 * FileViewer cache-invalidation on swarm:file-changed (run_a400d951, bug #1).
 *
 * Bug: when the agent RE-WRITES a file already open in Canvas, FileViewer's
 * per-filePath contentCache early-returns on a hit and the fetch effect (deps
 * [filePath, viewType]) never re-runs → the viewer shows STALE content. This is
 * specific to FileViewer's OWN contentCache renderers (image/pdf/video/audio/
 * csv/html-preview/unsupported); text/md/svg delegate to FileEditorCore which
 * already self-refreshes via its own swarm:file-changed listener (:680) — so
 * FileViewer must NOT double-handle those.
 *
 * Fix: FileViewer listens for swarm:file-changed; on a path match to a cached
 * entry (and NOT a text/md/svg type), it invalidates the cache and bumps a
 * refetch nonce (deleting a useRef entry alone is invisible to React deps, so
 * the nonce is what actually forces the fetch effect to re-run).
 *
 * These tests lock the LOGIC: a matching file-changed event re-fetches a csv
 * (contentCache renderer); it does NOT re-fetch for a text file (FileEditorCore
 * owns that); a non-matching path is ignored.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, waitFor, act } from '@testing-library/react';
import FileViewer from '../FileViewer';

vi.mock('../../common/FileEditorCore', () => ({
  default: () => <div data-testid="file-editor-core-stub" />,
}));
vi.mock('../FileViewerTabBar', () => ({ default: () => <div /> }));
vi.mock('../FileViewerStatusBar', () => ({ default: () => <div /> }));
vi.mock('../renderers/CsvRenderer', () => ({ default: () => <div data-testid="csv-renderer-stub" /> }));
vi.mock('../renderers/ImageRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/PdfRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/HtmlRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/VideoRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/AudioRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/UnsupportedRenderer', () => ({ default: () => <div /> }));

const getMock = vi.fn(async (url: string) => {
  if (url === '/workspace/file') return { data: { content: 'x', encoding: 'utf-8', size: 1, name: 'f', path: 'p' } };
  if (url === '/workspace/file/committed') return { data: { content: '' } };
  if (url === '/workspace/file/meta') return { data: { size: 1, mime_type: 'text/csv' } };
  return { data: {} };
});
vi.mock('../../../services/api', () => ({
  default: { get: (...args: unknown[]) => getMock(...(args as [string])) },
}));

const contentFetchCount = (path: string) =>
  getMock.mock.calls.filter((c) => c[0] === '/workspace/file').length;

function fireFileChanged(path: string) {
  act(() => {
    window.dispatchEvent(new CustomEvent('swarm:file-changed', { detail: { path, operation: 'written' } }));
  });
}

beforeEach(() => { getMock.mockClear(); });

describe('FileViewer — swarm:file-changed cache invalidation', () => {
  it('re-fetches a csv (contentCache renderer) when its file is rewritten', async () => {
    const { container } = render(
      <FileViewer initialFile={{ filePath: '/ws/data.csv', fileName: 'data.csv' }} onClose={vi.fn()} variant="panel" />,
    );
    await waitFor(() => expect(container.querySelector('[data-testid="csv-renderer-stub"]')).toBeTruthy());
    const before = contentFetchCount('/ws/data.csv');
    fireFileChanged('/ws/data.csv');
    await waitFor(() => expect(contentFetchCount('/ws/data.csv')).toBeGreaterThan(before));
  });

  it('does NOT re-fetch a text file on file-changed (FileEditorCore owns that refresh)', async () => {
    const { container } = render(
      <FileViewer initialFile={{ filePath: '/ws/notes.txt', fileName: 'notes.txt' }} onClose={vi.fn()} variant="panel" />,
    );
    await waitFor(() => expect(container.querySelector('[data-testid="file-editor-core-stub"]')).toBeTruthy());
    const before = contentFetchCount('/ws/notes.txt');
    fireFileChanged('/ws/notes.txt');
    // Give any (wrong) refetch a chance to fire, then assert it did NOT.
    await new Promise((r) => setTimeout(r, 30));
    expect(contentFetchCount('/ws/notes.txt')).toBe(before);
  });

  it('ignores a file-changed for a DIFFERENT path', async () => {
    const { container } = render(
      <FileViewer initialFile={{ filePath: '/ws/data.csv', fileName: 'data.csv' }} onClose={vi.fn()} variant="panel" />,
    );
    await waitFor(() => expect(container.querySelector('[data-testid="csv-renderer-stub"]')).toBeTruthy());
    const before = contentFetchCount('/ws/data.csv');
    fireFileChanged('/ws/other.csv');
    await new Promise((r) => setTimeout(r, 30));
    expect(contentFetchCount('/ws/data.csv')).toBe(before);
  });

  it('does NOT false-match a shorter changed path against a longer cached path (Gate-2 HIGH)', async () => {
    // Active file '/ws/foo.csv'. A file-changed for the bare 'foo.csv' must NOT be
    // treated as a match (asymmetric rule: only the event path ending with the
    // cached path counts, not the reverse) — else an unrelated file rewrite would
    // wrongly refetch this one.
    const { container } = render(
      <FileViewer initialFile={{ filePath: '/ws/deep/foo.csv', fileName: 'foo.csv' }} onClose={vi.fn()} variant="panel" />,
    );
    await waitFor(() => expect(container.querySelector('[data-testid="csv-renderer-stub"]')).toBeTruthy());
    const before = contentFetchCount('x');
    fireFileChanged('other/foo.csv'); // ends with 'foo.csv' but NOT '/ws/deep/foo.csv'
    await new Promise((r) => setTimeout(r, 30));
    expect(contentFetchCount('x')).toBe(before);
  });

  it('shows a helpful "too large" message on a 413 instead of a raw error (#4)', async () => {
    getMock.mockImplementationOnce(async () => {
      throw { response: { status: 413, data: { detail: 'File too large to preview (72 MB). Maximum is 50 MB.' } } };
    });
    const { findByText } = render(
      <FileViewer initialFile={{ filePath: '/ws/huge.pdf', fileName: 'huge.pdf' }} onClose={vi.fn()} variant="panel" />,
    );
    expect(await findByText(/too large/i)).toBeTruthy();
  });
});
