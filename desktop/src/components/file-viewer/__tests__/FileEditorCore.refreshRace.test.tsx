/**
 * FileEditorCore — a disk refresh never overwrites what the user typed meanwhile.
 *
 * The file-changed refresh checks "unsaved edits?" before it reads the file; the
 * user can type while that read is in flight. Invariant: if they did, the disk copy
 * is NOT applied (their edit stays); with no edit, the disk copy is applied.
 *
 * Methodology: the real FileViewer + FileEditorCore; api.get mocked, the refresh read
 * held on a deferred so the edit lands inside the window.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';
import FileViewer from '../FileViewer';

vi.mock('../renderers/CsvRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/ImageRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/PdfRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/HtmlRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/VideoRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/AudioRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/UnsupportedRenderer', () => ({ default: () => <div /> }));

let reads = 0;
let release: (content: string) => void = () => {};
vi.mock('../../../services/api', () => ({
  default: {
    get: vi.fn(async (url: string) => {
      if (url === '/workspace/file') {
        reads += 1;
        if (reads === 1) return { data: { content: 'hello', encoding: 'utf-8', size: 5, name: 'app.ts', path: 'p', readonly: false } };
        return new Promise((r) => { release = (content) => r({ data: { content } }); });
      }
      if (url === '/workspace/file/committed') return { data: { content: 'hello' } };
      return { data: {} };
    }),
    put: vi.fn(async () => ({ data: {} })),
  },
}));

const fileChanged = () => window.dispatchEvent(new CustomEvent('swarm:file-changed', {
  detail: { path: '/ws/app.ts', operation: 'written', kind: 'content' } }));

beforeEach(() => { reads = 0; });

describe('FileEditorCore disk refresh vs typing', () => {
  it('an edit typed while the refresh read is in flight is kept', async () => {
    render(<FileViewer initialFile={{ filePath: '/ws/app.ts', fileName: 'app.ts' }} onClose={() => {}} variant="panel" />);
    const ta = await screen.findByTestId('file-editor-textarea') as HTMLTextAreaElement;
    await act(async () => { fileChanged(); });
    await waitFor(() => expect(reads).toBe(2));               // the read is in flight
    await act(async () => { fireEvent.change(ta, { target: { value: 'hello EDITED' } }); });
    await act(async () => { release('changed on disk'); });
    expect((screen.getByTestId('file-editor-textarea') as HTMLTextAreaElement).value).toBe('hello EDITED');
  });

  it('with no edit, the disk copy is applied', async () => {
    render(<FileViewer initialFile={{ filePath: '/ws/app.ts', fileName: 'app.ts' }} onClose={() => {}} variant="panel" />);
    await screen.findByTestId('file-editor-textarea');
    await act(async () => { fileChanged(); });
    await waitFor(() => expect(reads).toBe(2));
    await act(async () => { release('changed on disk'); });
    await waitFor(() => expect((screen.getByTestId('file-editor-textarea') as HTMLTextAreaElement).value).toBe('changed on disk'));
  });
});
