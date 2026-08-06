/**
 * FileViewer unified-close dirty-guard (run_f49d3ff3 R2, AC4).
 *
 * The unified file-chrome header (panel) owns close for ALL types. For editor types
 * (text/md/svg → FileEditorCore) the close MUST reuse FileEditorCore's existing
 * unsaved-changes guard rather than silently no-op a dirty tab (useFileViewerTabs:107).
 * FileViewer routes editor-type close through a closeSignal counter → FileEditorCore's
 * effect runs its guarded close.
 *
 * This test uses the REAL FileEditorCore (NOT stubbed) so the delegation + guard fire
 * end-to-end: edit a text file → dirty → click the unified header close → the
 * unsaved-changes dialog appears (NOT an immediate close, NOT a silent no-op).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import FileViewer from '../FileViewer';

// Real FileEditorCore. Stub only the lazy renderers + api boundary.
vi.mock('../renderers/CsvRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/ImageRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/PdfRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/HtmlRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/VideoRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/AudioRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/UnsupportedRenderer', () => ({ default: () => <div /> }));

vi.mock('../../../services/api', () => ({
  default: {
    get: vi.fn(async (url: string) => {
      if (url === '/workspace/file') return { data: { content: 'hello', encoding: 'utf-8', size: 5, name: 'app.ts', path: 'p', readonly: false } };
      if (url === '/workspace/file/committed') return { data: { content: 'hello' } };
      return { data: {} };
    }),
    put: vi.fn(async () => ({ data: {} })),
  },
}));

const base = (fileName: string) => ({
  initialFile: { filePath: `/ws/${fileName}`, fileName },
  onClose: vi.fn(),
  variant: 'panel' as const,
});

beforeEach(() => vi.clearAllMocks());

describe('FileViewer — unified close dirty-guard (R2 AC4)', () => {
  it('closing a CLEAN text file via the unified header closes immediately (onClose fires)', async () => {
    const onClose = vi.fn();
    render(<FileViewer {...base('app.ts')} onClose={onClose} />);
    // Wait for the real editor to mount + content to load.
    await waitFor(() => expect(screen.getByTestId('file-editor-textarea')).toBeTruthy());
    // Not dirty → unified close → editor's guarded close → onClose (no dialog).
    await act(async () => { fireEvent.click(screen.getByTestId('file-chrome-close')); });
    expect(screen.queryByTestId('unsaved-warning-discard')).toBeNull();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closing a DIRTY text file via the unified header shows the unsaved-changes dialog (guard preserved, NOT a silent no-op)', async () => {
    const onClose = vi.fn();
    render(<FileViewer {...base('app.ts')} onClose={onClose} />);
    const ta = await screen.findByTestId('file-editor-textarea');
    // Make it dirty.
    await act(async () => { fireEvent.change(ta, { target: { value: 'hello EDITED' } }); });
    // Unified header close → delegates to FileEditorCore's guard → dialog, NOT close.
    await act(async () => { fireEvent.click(screen.getByTestId('file-chrome-close')); });
    await waitFor(() => expect(screen.getByTestId('unsaved-warning-discard')).toBeTruthy());
    expect(onClose).not.toHaveBeenCalled();
    // Confirming discard then closes.
    await act(async () => { fireEvent.click(screen.getByTestId('unsaved-warning-discard')); });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('FileEditorCore does NOT render its OWN filename/close in panel variant (no double header)', async () => {
    render(<FileViewer {...base('app.ts')} />);
    await waitFor(() => expect(screen.getByTestId('file-editor-textarea')).toBeTruthy());
    // The unified header exists exactly once...
    expect(screen.getAllByTestId('file-chrome-close')).toHaveLength(1);
    // ...and FileEditorCore's own header Close (aria-label="Close") is suppressed in panel.
    expect(screen.queryByLabelText('Close')).toBeNull();
  });
});
