/**
 * FileViewer — a file routed as text that the server finds binary is never put in
 * the editor (docs/design/canvas-file-types.md P0 B3).
 *
 * - The text route asks GET /workspace/file with text_only=1.
 * - On 415 the tab shows the info card ("Binary file"), not FileEditorCore: no
 *   textarea, no Save — the editor would show base64 and Save would overwrite it.
 * - Known data/model/video/iWork extensions route straight to the card.
 *
 * api.get is the boundary; the info card is the real UnsupportedRenderer.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { ApiError } from '../../../services/api';
import { classifyFileForViewer } from '../utils/fileViewTypes';

const get = vi.fn();
vi.mock('../../../services/api', async (orig) => {
  const real = await orig<typeof import('../../../services/api')>();
  return { ...real, default: { get: (...a: unknown[]) => get(...a), put: vi.fn() } };
});
import FileViewer from '../FileViewer';

beforeEach(() => {
  get.mockReset();
  get.mockImplementation(async (url: string, opts?: { params?: Record<string, unknown> }) => {
    if (url === '/workspace/file') {
      if (opts?.params?.text_only) throw new ApiError({ code: 'UNSUPPORTED', message: 'binary', detail: 'binary', suggestedAction: '' }, 415);
      return { data: { content: 'AAAA', encoding: 'base64', size: 3, name: 'x', path: 'p' } };
    }
    if (url === '/workspace/file/meta') return { data: { size: 2048, absolute_path: '/ws/blob.weird' } };
    return { data: {} };
  });
});

describe('binary behind a text route', () => {
  it('asks text_only and shows the info card on 415 — no editor', async () => {
    render(<FileViewer variant="panel" onClose={vi.fn()} initialFile={{ filePath: '/ws/blob.weird', fileName: 'blob.weird' }} />);
    expect(await screen.findByText('Binary file')).toBeTruthy();
    expect(get.mock.calls.find((c) => c[0] === '/workspace/file')?.[1]?.params?.text_only).toBe(1);
    expect(screen.queryByTestId('file-editor-textarea')).toBeNull();
    expect(screen.queryByTestId('file-editor-save')).toBeNull();
  });

  it('a text file still opens in the editor', async () => {
    get.mockImplementation(async (url: string) => (url === '/workspace/file'
      ? { data: { content: 'print(1)\n', encoding: 'utf-8', size: 9, name: 'a.py', path: 'p' } }
      : { data: { content: '' } }));
    render(<FileViewer variant="panel" onClose={vi.fn()} initialFile={{ filePath: '/ws/a.py', fileName: 'a.py' }} />);
    await waitFor(() => expect(screen.getByTestId('file-editor-textarea')).toBeTruthy());
  });
});

describe('known binaries route to the card', () => {
  it.each(['m.safetensors', 'd.parquet', 'a.npy', 'v.mkv', 'v.avi', 'r.pages', 'x.psd'])('%s', (n) => {
    expect(classifyFileForViewer(n)).toBe('unsupported');
  });
});
