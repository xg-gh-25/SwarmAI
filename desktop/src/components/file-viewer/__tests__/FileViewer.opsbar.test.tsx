/**
 * FileViewer — non-text renderers get the status-bar file-op cluster (run_5b330415).
 *
 * Locks the WIRING: a non-text file (html) renders FileViewerStatusBar WITH
 * filePath + an onAttach that dispatches the decoupled `swarm:attach-file` window
 * event (the same channel the Explorer uses → ChatPage → addWorkspaceFiles), and a
 * text file (FileEditorCore) does NOT render the status bar (no double footer).
 *
 * FileViewerStatusBar is NOT stubbed here (unlike chrome.test) so we assert the
 * real buttons; the lazy renderers + editor + api are stubbed.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import FileViewer from '../FileViewer';

vi.mock('../../common/FileEditorCore', () => ({
  default: ({ onAttachToChat }: { onAttachToChat?: (i: { path: string; name: string }) => void }) => (
    <button data-testid="file-editor-core-stub" data-attach={onAttachToChat ? 'yes' : 'no'}
      onClick={() => onAttachToChat?.({ path: '/ws/out/app.ts', name: 'app.ts' })} />
  ),
}));
vi.mock('../renderers/HtmlRenderer', () => ({ default: () => <div data-testid="html-stub" /> }));
vi.mock('../renderers/CsvRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/ImageRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/PdfRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/VideoRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/AudioRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/UnsupportedRenderer', () => ({ default: () => <div /> }));
vi.mock('../../../utils/clipboard', () => ({ copyToClipboard: vi.fn(async () => true) }));

vi.mock('../../../services/api', () => ({
  default: {
    get: vi.fn(async (url: string) => {
      if (url === '/workspace/file') return { data: { content: 'x', encoding: 'utf-8', size: 42, name: 'f', path: 'p' } };
      if (url === '/workspace/file/committed') return { data: { content: '' } };
      if (url === '/workspace/file/meta') return { data: { size: 42, mime_type: 'text/html' } };
      return { data: {} };
    }),
  },
}));

const base = (fileName: string) => ({
  initialFile: { filePath: `/ws/out/${fileName}`, fileName },
  onClose: vi.fn(),
  variant: 'panel' as const,
});

beforeEach(() => vi.clearAllMocks());

describe('FileViewer — non-text file-op cluster', () => {
  it('html file (panel): status bar shows attach + refresh + copy-path; attach dispatches swarm:attach-file', async () => {
    const events: CustomEvent[] = [];
    const listener = (e: Event) => events.push(e as CustomEvent);
    window.addEventListener('swarm:attach-file', listener);
    try {
      render(<FileViewer {...base('deck.html')} />);
      await waitFor(() => expect(screen.getByTestId('statusbar-refresh')).toBeTruthy());
      // Copy path is a frequent action: shown in the panel too (§2b).
      expect(screen.getByTestId('statusbar-copy-path')).toBeTruthy();
      const attach = screen.getByTestId('statusbar-attach');
      fireEvent.click(attach);
      expect(events).toHaveLength(1);
      expect(events[0].detail).toMatchObject({ path: '/ws/out/deck.html', name: 'deck.html', type: 'file' });
    } finally {
      window.removeEventListener('swarm:attach-file', listener);
    }
  });

  it('html file (panel): no close anywhere (no file header, no status-bar close)', async () => {
    render(<FileViewer {...base('deck.html')} />);
    await waitFor(() => expect(screen.getByTestId('statusbar-attach')).toBeTruthy());
    expect(screen.queryByTestId('statusbar-close')).toBeNull();
    expect(screen.queryByLabelText('Close file')).toBeNull();
  });

  it('html file (modal): status bar keeps copy-path and has no refresh', async () => {
    render(<FileViewer {...base('deck.html')} variant="modal" />);
    await waitFor(() => expect(screen.getByTestId('statusbar-copy-path')).toBeTruthy());
    expect(screen.queryByTestId('statusbar-refresh')).toBeNull();
  });

  it('text file: NO status bar (FileEditorCore owns its footer — no double footer)', async () => {
    render(<FileViewer {...base('app.ts')} />);
    await waitFor(() => expect(screen.getByTestId('file-editor-core-stub')).toBeTruthy());
    expect(screen.queryByTestId('statusbar-copy-path')).toBeNull();
  });

  it('when onAttachToChat prop IS provided, attach calls it instead of dispatching the event', async () => {
    const onAttachToChat = vi.fn();
    const events: CustomEvent[] = [];
    const listener = (e: Event) => events.push(e as CustomEvent);
    window.addEventListener('swarm:attach-file', listener);
    try {
      render(<FileViewer {...base('deck.html')} onAttachToChat={onAttachToChat} />);
      await waitFor(() => expect(screen.getByTestId('statusbar-attach')).toBeTruthy());
      fireEvent.click(screen.getByTestId('statusbar-attach'));
      expect(onAttachToChat).toHaveBeenCalledTimes(1);
      expect(events).toHaveLength(0); // prop wins, no event fallback
    } finally {
      window.removeEventListener('swarm:attach-file', listener);
    }
  });

  it('text file (panel, no onAttachToChat): the editor still gets attach, which dispatches swarm:attach-file (C3)', async () => {
    const events: CustomEvent[] = [];
    const listener = (e: Event) => events.push(e as CustomEvent);
    window.addEventListener('swarm:attach-file', listener);
    try {
      render(<FileViewer {...base('app.ts')} />);
      const ed = await screen.findByTestId('file-editor-core-stub');
      expect(ed.getAttribute('data-attach')).toBe('yes');
      fireEvent.click(ed);
      expect(events).toHaveLength(1);
      expect(events[0].detail).toMatchObject({ path: '/ws/out/app.ts', name: 'app.ts', type: 'file' });
    } finally {
      window.removeEventListener('swarm:attach-file', listener);
    }
  });
});
