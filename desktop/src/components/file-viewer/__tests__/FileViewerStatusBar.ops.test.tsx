/**
 * FileViewerStatusBar — optional file-operation cluster (run_5b330415).
 *
 * Non-text renderers (html/image/pdf/csv) route through FileViewer and get ONLY
 * this status bar — previously an info-only bar with ZERO file operations, while
 * text/md/svg (FileEditorCore) had copy-path + attach in their header. This adds a
 * copy-path + attach-to-chat cluster to the status bar, gated on optional props so
 * the default (no props) renders identically to before (pure info bar).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import FileViewerStatusBar from '../FileViewerStatusBar';

const clip = vi.fn(async () => true);
vi.mock('../../../utils/clipboard', () => ({ copyToClipboard: (p: string) => clip(p) }));

beforeEach(() => vi.clearAllMocks());

describe('FileViewerStatusBar — file-operation cluster', () => {
  it('renders NO operation buttons when filePath is absent (info-only, unchanged)', () => {
    render(<FileViewerStatusBar fileName="deck.html" fileSize={100} viewType="html-preview" />);
    expect(screen.queryByTestId('statusbar-copy-path')).toBeNull();
    expect(screen.queryByTestId('statusbar-attach')).toBeNull();
  });

  it('renders copy-path when filePath present; clicking copies the path', () => {
    render(
      <FileViewerStatusBar
        fileName="deck.html"
        fileSize={100}
        viewType="html-preview"
        filePath="/ws/out/deck.html"
      />,
    );
    const btn = screen.getByTestId('statusbar-copy-path');
    fireEvent.click(btn);
    expect(clip).toHaveBeenCalledWith('/ws/out/deck.html');
  });

  it('renders attach button only when onAttach is provided; clicking fires it', () => {
    const onAttach = vi.fn();
    render(
      <FileViewerStatusBar
        fileName="deck.html"
        fileSize={100}
        viewType="html-preview"
        filePath="/ws/out/deck.html"
        onAttach={onAttach}
      />,
    );
    const btn = screen.getByTestId('statusbar-attach');
    fireEvent.click(btn);
    expect(onAttach).toHaveBeenCalledTimes(1);
  });

  it('cancels the copied-reset timer on unmount (no setState-after-unmount leak)', async () => {
    const clearSpy = vi.spyOn(globalThis, 'clearTimeout');
    const { unmount } = render(
      <FileViewerStatusBar fileName="deck.html" fileSize={100} viewType="html-preview" filePath="/ws/out/deck.html" />,
    );
    // Click copy → sets copied=true + arms the 2s reset timer.
    fireEvent.click(screen.getByTestId('statusbar-copy-path'));
    // Let the async copyToClipboard().then resolve so the timer is actually armed.
    await Promise.resolve();
    await Promise.resolve();
    const before = clearSpy.mock.calls.length;
    unmount();
    // Cleanup effect must clear the armed timer (else setState-after-unmount leak).
    expect(clearSpy.mock.calls.length).toBeGreaterThan(before);
  });

  it('omits attach button when onAttach is not provided (copy-path still shown)', () => {
    render(
      <FileViewerStatusBar
        fileName="deck.html"
        fileSize={100}
        viewType="html-preview"
        filePath="/ws/out/deck.html"
      />,
    );
    expect(screen.getByTestId('statusbar-copy-path')).toBeTruthy();
    expect(screen.queryByTestId('statusbar-attach')).toBeNull();
  });
});
