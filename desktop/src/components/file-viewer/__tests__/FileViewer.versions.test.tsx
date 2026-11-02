/**
 * FileViewer — a read-only versions document (utils/fileVersions): a Hive's changed
 * file at its base and tip, carried with the open. Nothing is read from or written to
 * its path, whatever its name's type it is the editor on its diff, read-only, detached
 * from the disk, and its save always fails.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import FileViewer from '../FileViewer';
import api from '../../../services/api';

const seen: Record<string, unknown>[] = [];
vi.mock('../../common/FileEditorCore', () => ({
  default: (p: Record<string, unknown>) => { seen.push(p); return <div data-testid="file-editor-core-stub" />; },
}));
vi.mock('../FileViewerTabBar', () => ({ default: () => <div /> }));
vi.mock('../FileViewerStatusBar', () => ({ default: () => <div data-testid="statusbar" /> }));
vi.mock('../renderers/HtmlRenderer', () => ({ default: () => <div data-testid="html" /> }));
vi.mock('../renderers/CsvRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/ImageRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/PdfRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/VideoRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/AudioRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/UnsupportedRenderer', () => ({ default: () => <div /> }));
vi.mock('../../../services/api', () => ({
  default: { get: vi.fn(async () => ({ data: { content: 'FROM DISK', encoding: 'utf-8', size: 9, name: 'f', path: 'p' } })), put: vi.fn() },
}));

beforeEach(() => { vi.clearAllMocks(); seen.length = 0; });

describe('a versions document', () => {
  it('opens on its diff, read-only and detached, reads nothing and never saves', async () => {
    const file = { filePath: 'hive:so_x@bbbbbbbbbbbb/web/index.html', fileName: 'index.html',
      versions: { base: '<p>old</p>\n', tip: '<p>new</p>\n' } };
    render(<FileViewer variant="panel" onClose={vi.fn()} initialFile={file} />);
    await waitFor(() => expect(screen.getByTestId('file-editor-core-stub')).toBeTruthy());
    expect(screen.queryByTestId('html')).toBeNull();          // the text, never rendered as a page
    const p = seen[seen.length - 1];
    expect(p).toMatchObject({ initialContent: '<p>new</p>\n', committedContent: '<p>old</p>\n',
      readonly: true, detached: true, initialShowDiff: true });
    expect(p.onAttachToChat).toBeUndefined();
    expect(p.onToggleMode).toBeUndefined();
    await expect((p.onSave as (c: string) => Promise<void>)('edited')).rejects.toThrow(/read-only/);
    expect(vi.mocked(api.get)).not.toHaveBeenCalled();
    expect(vi.mocked(api.put)).not.toHaveBeenCalled();
  });

  it('a deleted file shows its base against nothing; an added one nothing against its tip', async () => {
    const { rerender } = render(<FileViewer variant="panel" onClose={vi.fn()}
      initialFile={{ filePath: 'hive:so_x@b/docs/old.md', fileName: 'old.md', versions: { base: 'old\n', tip: null } }} />);
    await waitFor(() => expect(seen.length).toBeGreaterThan(0));
    expect(seen[seen.length - 1]).toMatchObject({ initialContent: '', committedContent: 'old\n' });
    rerender(<FileViewer variant="panel" onClose={vi.fn()}
      initialFile={{ filePath: 'hive:so_x@b/src/new.py', fileName: 'new.py', versions: { base: null, tip: 'new\n' } }} />);
    await waitFor(() => expect(seen[seen.length - 1]).toMatchObject({ initialContent: 'new\n', committedContent: '' }));
    expect(vi.mocked(api.get)).not.toHaveBeenCalled();
  });
});
