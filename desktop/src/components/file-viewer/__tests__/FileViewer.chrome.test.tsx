/**
 * FileViewer chrome de-duplication (v6 Canvas redesign, run_09431085).
 *
 * Business rules under test:
 *  - PANEL variant does NOT render the horizontal FileViewerTabBar (the OUTPUTS
 *    list is the file selector); MODAL variant still renders it.
 *  - The FileViewerStatusBar (the bottom "type · size" bar) is SUPPRESSED for
 *    view types that delegate to FileEditorCore (text/markdown/svg) — those own
 *    their footer, so a status bar would be a SECOND footer (double-footer bug).
 *    It is KEPT for renderers with no own footer (csv → CsvRenderer, image, etc).
 *
 * The Gate-1 CRITICAL catch: the gate must key on the FileEditorCore set
 * {text,markdown,svg}, NOT isEditableType() (which also includes 'csv').
 *
 * FileEditorCore + lazy renderers are leaf-stubbed; api.get is mocked so the
 * content-load effect resolves synchronously enough for the status-bar branch.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import FileViewer from '../FileViewer';

// Stub the heavy editor + renderers to leaf nodes.
vi.mock('../../common/FileEditorCore', () => ({
  default: () => <div data-testid="file-editor-core-stub" />,
}));
vi.mock('../FileViewerTabBar', () => ({
  default: () => <div data-testid="file-viewer-tabbar-stub" />,
}));
vi.mock('../FileViewerStatusBar', () => ({
  default: () => <div data-testid="file-viewer-statusbar-stub" data-role="statusbar" />,
}));
// Lazy renderers — stub the CSV one (the Gate-1 edge case).
vi.mock('../renderers/CsvRenderer', () => ({ default: () => <div data-testid="csv-renderer-stub" /> }));
vi.mock('../renderers/ImageRenderer', () => ({ default: () => <div data-testid="image-renderer-stub" /> }));
vi.mock('../renderers/PdfRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/HtmlRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/VideoRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/AudioRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/UnsupportedRenderer', () => ({ default: () => <div /> }));

// api.get: return content for /workspace/file, empty committed for the diff fetch.
vi.mock('../../../services/api', () => ({
  default: {
    get: vi.fn(async (url: string) => {
      if (url === '/workspace/file') return { data: { content: 'x', encoding: 'utf-8', size: 1, name: 'f', path: 'p' } };
      if (url === '/workspace/file/committed') return { data: { content: '' } };
      if (url === '/workspace/file/meta') return { data: { size: 1, mime_type: 'text/csv' } };
      return { data: {} };
    }),
  },
}));

const base = (fileName: string) => ({
  initialFile: { filePath: `/ws/${fileName}`, fileName },
  onClose: vi.fn(),
  variant: 'panel' as const,
});

beforeEach(() => vi.clearAllMocks());

describe('FileViewer — chrome dedup (v6)', () => {
  it('panel variant does NOT render the horizontal tab bar', () => {
    render(<FileViewer {...base('notes.md')} />);
    expect(screen.queryByTestId('file-viewer-tabbar-stub')).toBeNull();
  });

  it('modal variant DOES render the tab bar', () => {
    render(<FileViewer {...base('notes.md')} variant="modal" />);
    expect(screen.getByTestId('file-viewer-tabbar-stub')).toBeTruthy();
  });

  it('suppresses the status bar for markdown (FileEditorCore owns its footer → no double footer)', async () => {
    render(<FileViewer {...base('notes.md')} />);
    await waitFor(() => expect(screen.getByTestId('file-editor-core-stub')).toBeTruthy());
    expect(screen.queryByTestId('file-viewer-statusbar-stub')).toBeNull();
  });

  it('suppresses the status bar for a .ts (text) file', async () => {
    render(<FileViewer {...base('app.ts')} />);
    await waitFor(() => expect(screen.getByTestId('file-editor-core-stub')).toBeTruthy());
    expect(screen.queryByTestId('file-viewer-statusbar-stub')).toBeNull();
  });

  it('KEEPS the status bar for CSV (CsvRenderer has no own footer — Gate-1 edge)', async () => {
    render(<FileViewer {...base('data.csv')} />);
    await waitFor(() => expect(screen.getByTestId('file-viewer-statusbar-stub')).toBeTruthy());
    // and it did NOT route to FileEditorCore
    expect(screen.queryByTestId('file-editor-core-stub')).toBeNull();
  });

  it('KEEPS the status bar for an image (binary renderer, no footer)', async () => {
    render(<FileViewer {...base('pic.png')} />);
    await waitFor(() => expect(screen.getByTestId('file-viewer-statusbar-stub')).toBeTruthy());
  });

  // ── R2 (run_f49d3ff3): unified type-agnostic file-chrome close header ──
  // Every viewType shows ONE close in the panel's file-chrome header, regardless of
  // whether it routes to FileEditorCore (text) or a lazy renderer (html/img/pdf/csv).
  it.each([
    ['app.ts', 'text→FileEditorCore'],
    ['notes.md', 'markdown→FileEditorCore'],
    ['icon.svg', 'svg→FileEditorCore'],
    ['deck.html', 'html→HtmlRenderer'],
    ['pic.png', 'image→ImageRenderer'],
    ['doc.pdf', 'pdf→PdfRenderer'],
    ['data.csv', 'csv→CsvRenderer'],
    ['blob.bin', 'unsupported→UnsupportedRenderer'],
  ])('shows the unified file-chrome close for %s (%s) in panel variant', async (fileName) => {
    render(<FileViewer {...base(fileName)} />);
    await waitFor(() => expect(screen.getByTestId('file-chrome-header')).toBeTruthy());
    expect(screen.getByTestId('file-chrome-close')).toBeTruthy();
  });

  it('the unified close is PANEL-only — modal variant does NOT render the file-chrome header (amendment 3)', async () => {
    render(<FileViewer {...base('deck.html')} variant="modal" />);
    await waitFor(() => expect(screen.getByTestId('file-viewer-tabbar-stub')).toBeTruthy());
    expect(screen.queryByTestId('file-chrome-header')).toBeNull();
  });

  it('non-editor type: clicking the unified close fires onClose directly (canvas.close)', async () => {
    const onClose = vi.fn();
    render(<FileViewer {...base('deck.html')} onClose={onClose} />);
    await waitFor(() => expect(screen.getByTestId('file-chrome-close')).toBeTruthy());
    screen.getByTestId('file-chrome-close').click();
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
