/**
 * Regression: an editor-type tab in the contentError state must be CLOSABLE, and a
 * 404 must show a friendly notice — not the raw "Resource not found" (run_7f6539b5).
 *
 * THE BUG (code-trace): FileViewer routes the panel header-X by STATIC isEditorType
 * (viewType text/md/svg → bump closeSignal, expecting FileEditorCore to consume it).
 * But when the fetch errors, renderActiveContent() returns an error placeholder and
 * FileEditorCore is NOT mounted → the sole closeSignal consumer is absent → the X is
 * a no-op → the dead tab hangs forever. Fix: handleUnifiedClose closes DIRECTLY
 * (handleCloseActive) when contentError is set (no live editor to guard).
 *
 * Also: a 404 arrives via the fetch catch (api.ts throws an ApiError with statusCode
 * 404 + message "Resource not found"); the friendly deleted-notice only fires on a
 * swarm:file-changed event, so a CLI/external delete showed the raw string. Fix: the
 * catch detects 404 (ApiError.statusCode/code) and sets a friendly local notice,
 * WITHOUT touching the shared api.ts interceptor.
 *
 * Uses the REAL FileViewer + a mocked api boundary that 404s, so the error path is
 * driven end-to-end (jsdom cannot reflow, but the close-routing + error text are
 * structural and fully assertable). Reverting either fix turns a case RED.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import FileViewer from '../FileViewer';
import { ApiError } from '../../../services/api';

vi.mock('../renderers/CsvRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/ImageRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/PdfRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/HtmlRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/VideoRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/AudioRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/UnsupportedRenderer', () => ({ default: () => <div /> }));

// api boundary that 404s the file fetch exactly as the real interceptor does:
// a rejected ApiError (statusCode 404, code NOT_FOUND, message "Resource not found").
vi.mock('../../../services/api', async () => {
  const actual = await vi.importActual<typeof import('../../../services/api')>('../../../services/api');
  return {
    ...actual,
    default: {
      get: vi.fn(async (url: string) => {
        if (url === '/workspace/file') {
          throw new actual.ApiError(
            { code: 'NOT_FOUND', message: 'Resource not found', suggestedAction: '' },
            404,
          );
        }
        if (url === '/workspace/file/committed') return { data: { content: '' } };
        return { data: {} };
      }),
      put: vi.fn(async () => ({ data: {} })),
    },
  };
});

const base = (fileName: string) => ({
  initialFile: { filePath: `/ws/${fileName}`, fileName },
  onClose: vi.fn(),
  variant: 'panel' as const,
});

beforeEach(() => vi.clearAllMocks());

describe('FileViewer — error-state tab is closable + friendly 404 (run_7f6539b5)', () => {
  it('AC3: a 404 on an editor file shows a friendly "no longer available" notice, NOT raw "Resource not found"', async () => {
    render(<FileViewer {...base('gone.md')} />);
    // The error placeholder must render friendly text, not the raw interceptor string.
    await waitFor(() => {
      expect(screen.getByText(/no longer available/i)).toBeTruthy();
    });
    expect(screen.queryByText(/Resource not found/i)).toBeNull();
    // And the editor is NOT mounted in the error state (the exact reason the old X no-op'd).
    expect(screen.queryByTestId('file-editor-textarea')).toBeNull();
  });

  it('AC1: the header X CLOSES a 404 editor tab (contentError-aware close, not a dead closeSignal)', async () => {
    const onClose = vi.fn();
    render(<FileViewer {...base('gone.md')} onClose={onClose} />);
    // Wait for the error state (editor unmounted).
    await waitFor(() => expect(screen.getByText(/no longer available/i)).toBeTruthy());
    // Click the unified header close — must actually close (single tab → onClose fires).
    await act(async () => { fireEvent.click(screen.getByTestId('file-chrome-close')); });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

// A separate suite whose api mock returns a 413 with a backend `detail`, to prove the
// Gate-2 MED fix: the detail is now read via ApiError.detail (was a dead .response.data.detail).
describe('FileViewer — 413 surfaces the backend detail (Gate-2 MED fix)', () => {
  it('AC4: a 413 with a specific backend detail shows THAT message, not the generic fallback', async () => {
    const { ApiError } = await import('../../../services/api');
    const api = (await import('../../../services/api')).default as { get: ReturnType<typeof vi.fn> };
    api.get.mockImplementation(async (url: string) => {
      if (url === '/workspace/file') {
        throw new ApiError(
          { code: 'PAYLOAD_TOO_LARGE', message: 'too large', detail: 'File too large to preview (72 MB). Maximum is 50 MB.', suggestedAction: '' },
          413,
        );
      }
      if (url === '/workspace/file/committed') return { data: { content: '' } };
      return { data: {} };
    });
    render(<FileViewer {...base('huge.md')} />);
    // The specific backend detail (with the MB numbers) must render — proving .detail is read.
    await waitFor(() => expect(screen.getByText(/72 MB.*Maximum is 50 MB/i)).toBeTruthy());
  });
});
