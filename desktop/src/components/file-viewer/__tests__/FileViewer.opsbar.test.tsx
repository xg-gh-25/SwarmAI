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
  default: () => <div data-testid="file-editor-core-stub" />,
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
  it('html file: status bar shows copy-path + attach; attach dispatches swarm:attach-file', async () => {
    const events: CustomEvent[] = [];
    const listener = (e: Event) => events.push(e as CustomEvent);
    window.addEventListener('swarm:attach-file', listener);
    try {
      render(<FileViewer {...base('deck.html')} />);
      await waitFor(() => expect(screen.getByTestId('statusbar-copy-path')).toBeTruthy());
      const attach = screen.getByTestId('statusbar-attach');
      fireEvent.click(attach);
      expect(events).toHaveLength(1);
      expect(events[0].detail).toMatchObject({ path: '/ws/out/deck.html', name: 'deck.html', type: 'file' });
    } finally {
      window.removeEventListener('swarm:attach-file', listener);
    }
  });

  it('html file (panel): status bar shows a Close button; clicking it closes the Canvas (onClose)', async () => {
    // Bug 1: non-text files were unclosable in panel variant (no tab bar, header × only
    // collapses). The status-bar close → FileViewer.handleCloseActive → last tab → onClose.
    const onClose = vi.fn();
    render(<FileViewer {...base('deck.html')} onClose={onClose} />);
    await waitFor(() => expect(screen.getByTestId('statusbar-close')).toBeTruthy());
    fireEvent.click(screen.getByTestId('statusbar-close'));
    expect(onClose).toHaveBeenCalledTimes(1);
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
});
