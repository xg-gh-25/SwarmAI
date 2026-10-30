/**
 * FileViewer — a reload from disk is not an unsaved edit.
 *
 * Bug: when the agent rewrites a markdown file the user has open (not edited),
 * FileEditorCore reloads the fresh disk copy and reported it through
 * onContentChange. FileViewer compared that content against its own cached
 * copy from when the file was opened, found them different, and marked the tab
 * dirty — so the Canvas told the agent "UNSAVED EDITS — do not replace this
 * file" although the user had typed nothing.
 *
 * Fix: the editor is the single authority on dirtiness (content vs. its own
 * saved baseline, which a disk reload moves) and reports it via onDirtyChange.
 *
 * Uses the REAL FileEditorCore so the reload path runs end to end.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, act, fireEvent } from '@testing-library/react';
import FileViewer from '../FileViewer';

vi.mock('../renderers/CsvRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/ImageRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/PdfRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/HtmlRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/VideoRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/AudioRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/UnsupportedRenderer', () => ({ default: () => <div /> }));

let disk = 'first version';
vi.mock('../../../services/api', () => ({
  default: {
    get: vi.fn(async (url: string) => {
      if (url === '/workspace/file') return { data: { content: disk, encoding: 'utf-8', size: disk.length, name: 'notes.txt', path: 'p', readonly: false } };
      if (url === '/workspace/file/committed') return { data: { content: '' } };
      return { data: {} };
    }),
    put: vi.fn(async () => ({ data: {} })),
  },
}));

beforeEach(() => { disk = 'first version'; vi.clearAllMocks(); });

function fireFileChanged(path: string) {
  act(() => {
    window.dispatchEvent(new CustomEvent('swarm:file-changed', { detail: { path, operation: 'written' } }));
  });
}

describe('FileViewer — disk reload does not mark the tab dirty', () => {
  it('an agent rewrite of an unedited open file leaves it clean', async () => {
    const onDirtyChange = vi.fn();
    render(
      <FileViewer
        initialFile={{ filePath: '/ws/notes.txt', fileName: 'notes.txt' }}
        onClose={vi.fn()}
        variant="panel"
        onDirtyChange={onDirtyChange}
      />,
    );
    await waitFor(() => expect(screen.getByDisplayValue('first version')).toBeTruthy());

    disk = 'second version';
    fireFileChanged('/ws/notes.txt');
    await waitFor(() => expect(screen.getByDisplayValue('second version')).toBeTruthy());
    await new Promise((r) => setTimeout(r, 30));

    expect(onDirtyChange).not.toHaveBeenLastCalledWith(true);
    expect(onDirtyChange.mock.calls.some(([d]) => d === true)).toBe(false);
  });

  it('a real user edit still marks the tab dirty', async () => {
    const onDirtyChange = vi.fn();
    render(
      <FileViewer
        initialFile={{ filePath: '/ws/notes.txt', fileName: 'notes.txt' }}
        onClose={vi.fn()}
        variant="panel"
        onDirtyChange={onDirtyChange}
      />,
    );
    const box = await waitFor(() => screen.getByDisplayValue('first version'));
    fireEvent.change(box, { target: { value: 'first version, edited' } });
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(true));
  });
});
