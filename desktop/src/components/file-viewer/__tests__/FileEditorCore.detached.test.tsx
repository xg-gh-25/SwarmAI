/**
 * FileEditorCore detached (a versions document): the real FileViewer + editor never read
 * the document's path from disk, on a file change, on focus or from a Reload button, and
 * say what the read-only text is.
 */
import { describe, it, expect, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import FileViewer from '../FileViewer';
import api from '../../../services/api';

vi.mock('../renderers/CsvRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/ImageRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/PdfRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/HtmlRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/VideoRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/AudioRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/UnsupportedRenderer', () => ({ default: () => <div /> }));
vi.mock('../../../services/api', () => ({
  default: {
    get: vi.fn(async () => ({ data: { content: 'FROM DISK', encoding: 'utf-8', size: 9, name: 'a.py', path: 'p' } })),
    put: vi.fn(async () => ({ data: {} })),
  },
}));

describe('a detached editor', () => {
  it('never reads the versions document from disk', async () => {
    const path = 'hive:so_x@bbbbbbbbbbbb/src/a.py';
    render(<FileViewer variant="panel" onClose={() => {}}
      initialFile={{ filePath: path, fileName: 'a.py', versions: { base: 'a = 1\n', tip: 'a = 2\n' } }} />);
    expect((await screen.findByTestId('readonly-banner')).textContent).toMatch(/Hive's change, read only/);
    await act(async () => {
      window.dispatchEvent(new CustomEvent('swarm:file-changed', { detail: { path: `/ws/${path}`, operation: 'written' } }));
      window.dispatchEvent(new Event('focus'));
      await new Promise((r) => setTimeout(r, 600));
    });
    const reads = vi.mocked(api.get).mock.calls.filter(([url]) => String(url).startsWith('/workspace/file'));
    expect(reads).toEqual([]);
    expect(screen.queryByTestId('file-editor-reload')).toBeNull();
    expect(vi.mocked(api.put)).not.toHaveBeenCalled();
  });
});
