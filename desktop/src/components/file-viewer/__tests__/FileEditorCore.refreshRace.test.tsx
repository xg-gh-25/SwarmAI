/**
 * FileEditorCore — a disk refresh never overwrites what the user typed meanwhile.
 *
 * The file-changed refresh checks "unsaved edits?" before it reads the file; the
 * user can type while that read is in flight. Invariant: if they did, the disk copy
 * is NOT applied (their edit stays); with no edit, the disk copy is applied.
 *
 * Methodology: the real FileViewer + FileEditorCore; api.get mocked, the refresh reads
 * held on deferreds so the edit lands inside the window. The event is re-sent until a
 * read starts: the editor subscribes in a mount effect, which can run after the
 * textarea is already in the DOM.
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
let pending: Array<(v: unknown) => void> = [];
const release = (content: string) => { pending.splice(0).forEach((r) => r({ data: { content } })); };
vi.mock('../../../services/api', () => ({
  default: {
    get: vi.fn(async (url: string) => {
      if (url === '/workspace/file') {
        reads += 1;
        if (reads === 1) return { data: { content: 'hello', encoding: 'utf-8', size: 5, name: 'app.ts', path: 'p', readonly: false } };
        return new Promise((r) => { pending.push(r); });
      }
      if (url === '/workspace/file/committed') return { data: { content: 'hello' } };
      return { data: {} };
    }),
    put: vi.fn(async () => ({ data: {} })),
  },
}));

const fileChanged = () => window.dispatchEvent(new CustomEvent('swarm:file-changed', {
  detail: { path: '/ws/app.ts', operation: 'written', kind: 'content' } }));

beforeEach(() => { reads = 0; pending = []; });

describe('FileEditorCore disk refresh vs typing', () => {
  it('an edit typed while the refresh read is in flight is kept', async () => {
    render(<FileViewer initialFile={{ filePath: '/ws/app.ts', fileName: 'app.ts' }} onClose={() => {}} variant="panel" />);
    const ta = await screen.findByTestId('file-editor-textarea') as HTMLTextAreaElement;
    // re-sent until the editor's subscription (a mount effect) has picked one up
    await waitFor(() => { fileChanged(); expect(reads).toBeGreaterThanOrEqual(2); });   // a read is in flight
    await act(async () => { fireEvent.change(ta, { target: { value: 'hello EDITED' } }); });
    await act(async () => { release('changed on disk'); });
    expect((screen.getByTestId('file-editor-textarea') as HTMLTextAreaElement).value).toBe('hello EDITED');
  });

  it('with no edit, the disk copy is applied', async () => {
    render(<FileViewer initialFile={{ filePath: '/ws/app.ts', fileName: 'app.ts' }} onClose={() => {}} variant="panel" />);
    await screen.findByTestId('file-editor-textarea');
    await waitFor(() => { fileChanged(); expect(reads).toBeGreaterThanOrEqual(2); });
    await act(async () => { release('changed on disk'); });
    await waitFor(() => expect((screen.getByTestId('file-editor-textarea') as HTMLTextAreaElement).value).toBe('changed on disk'));
  });
});
