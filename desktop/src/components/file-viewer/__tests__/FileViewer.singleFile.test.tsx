/**
 * FileViewer — the panel holds exactly one file (super-canvas-fixing.md C1, F4).
 *
 * The Canvas panel shows the slice's one file, so FileViewer in the panel keeps one:
 * after any number of opens a close request ends in onClose (canvas.close), never in
 * a switch to a hidden earlier file. The modal keeps its tab list (its tab bar gets
 * every opened file).
 *
 * Renderers, the editor and the tab bar are stubbed; the tab bar stub reports the
 * tab count it was given.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import FileViewer from '../FileViewer';

vi.mock('../../common/FileEditorCore', () => ({ default: () => <div data-testid="file-editor-core-stub" /> }));
vi.mock('../FileViewerTabBar', () => ({
  default: ({ tabs }: { tabs: unknown[] }) => <div data-testid="tabbar" data-count={tabs.length} />,
}));
vi.mock('../FileViewerStatusBar', () => ({ default: () => <div data-testid="statusbar" /> }));
vi.mock('../renderers/HtmlRenderer', () => ({ default: () => <div data-testid="html" /> }));
vi.mock('../renderers/CsvRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/ImageRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/PdfRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/VideoRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/AudioRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/UnsupportedRenderer', () => ({ default: () => <div /> }));
vi.mock('../../../services/api', () => ({
  default: { get: vi.fn(async () => ({ data: { content: '<p/>', encoding: 'utf-8', size: 4, name: 'f', path: 'p' } })) },
}));

const f = (n: string) => ({ filePath: `/ws/${n}`, fileName: n });

beforeEach(() => vi.clearAllMocks());

describe('panel FileViewer is single-file', () => {
  it('after three opens a close request closes the Canvas, not a hidden earlier file', async () => {
    const onClose = vi.fn();
    const { rerender } = render(<FileViewer variant="panel" onClose={onClose} initialFile={f('a.html')} closeRequest={0} />);
    await waitFor(() => expect(screen.getByTestId('html')).toBeTruthy());
    rerender(<FileViewer variant="panel" onClose={onClose} initialFile={f('b.html')} closeRequest={0} />);
    rerender(<FileViewer variant="panel" onClose={onClose} initialFile={f('c.html')} closeRequest={0} />);
    await waitFor(() => expect(screen.getByTestId('html')).toBeTruthy());
    await act(async () => {
      rerender(<FileViewer variant="panel" onClose={onClose} initialFile={f('c.html')} closeRequest={1} />);
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('the modal keeps every opened file as a tab', async () => {
    const { rerender } = render(<FileViewer variant="modal" onClose={vi.fn()} initialFile={f('a.html')} />);
    rerender(<FileViewer variant="modal" onClose={vi.fn()} initialFile={f('b.html')} />);
    await waitFor(() => expect(screen.getByTestId('tabbar').getAttribute('data-count')).toBe('2'));
  });
});
