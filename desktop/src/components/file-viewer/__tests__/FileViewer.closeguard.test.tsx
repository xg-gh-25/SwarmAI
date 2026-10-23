/**
 * FileViewer close-request dirty-guard (run_f49d3ff3 R2 AC4; Super Canvas: the
 * request now comes from the panel's closeRequest prop, not a header button).
 *
 * For editor types (text/md/svg → FileEditorCore) a close MUST reuse FileEditorCore's
 * unsaved-changes guard rather than silently no-op a dirty tab (useFileViewerTabs:107).
 * FileViewer routes it through a closeSignal counter → FileEditorCore's guarded close.
 *
 * Uses the REAL FileEditorCore so the guard fires end-to-end: edit → dirty → close
 * request → the unsaved-changes dialog (not an immediate close, not a silent no-op).
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

// The panel has no close button (Super Canvas): a close is requested by bumping the
// closeRequest prop (FileViewerPanel does it for the Browsing row's × when no output
// is left). It must still run FileEditorCore's unsaved-changes guard.
function mount(onClose: () => void, onDirtyChange?: (d: boolean) => void) {
  const props = { ...base('app.ts'), onClose, onDirtyChange };
  const r = render(<FileViewer {...props} closeRequest={0} />);
  let n = 0;
  const requestClose = async () => {
    n += 1;
    await act(async () => { r.rerender(<FileViewer {...props} closeRequest={n} />); });
  };
  return requestClose;
}

describe('FileViewer — close request keeps the dirty guard', () => {
  it('a CLEAN text file closes immediately (onClose fires)', async () => {
    const onClose = vi.fn();
    const requestClose = mount(onClose);
    await waitFor(() => expect(screen.getByTestId('file-editor-textarea')).toBeTruthy());
    await requestClose();
    expect(screen.queryByTestId('unsaved-warning-discard')).toBeNull();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('a DIRTY text file shows the unsaved-changes dialog; discarding then closes and clears dirty', async () => {
    const onClose = vi.fn();
    const onDirty = vi.fn();
    const requestClose = mount(onClose, onDirty);
    const ta = await screen.findByTestId('file-editor-textarea');
    await act(async () => { fireEvent.change(ta, { target: { value: 'hello EDITED' } }); });
    // The dirty state reached the panel (it shows a dot by the folded file name).
    await waitFor(() => expect(onDirty).toHaveBeenLastCalledWith(true));
    await requestClose();
    await waitFor(() => expect(screen.getByTestId('unsaved-warning-discard')).toBeTruthy());
    expect(onClose).not.toHaveBeenCalled();
    await act(async () => { fireEvent.click(screen.getByTestId('unsaved-warning-discard')); });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onDirty).toHaveBeenLastCalledWith(false);
  });

  it('editor type in panel has NO close affordance of its own', async () => {
    render(<FileViewer {...base('app.ts')} />);
    await waitFor(() => expect(screen.getByTestId('file-editor-textarea')).toBeTruthy());
    expect(screen.queryByLabelText('Close')).toBeNull();
    expect(screen.queryByLabelText('Close file')).toBeNull();
    expect(screen.queryByTestId('file-editor-cancel')).toBeNull();
  });
});
