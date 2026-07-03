/**
 * HtmlRenderer — open-in-system-browser contract (run_628c36d3).
 *
 * Bug: the in-app HTML preview used a sandboxed iframe with `srcDoc={content}`.
 * In the Tauri WKWebView production build, srcDoc renders a BLANK frame (works in
 * Chrome/dev, so invisible until packaged). Same conclusion the Eval report viewer
 * already reached (EvalDashboard ReportsTab). Fix: default "Rendered" mode is a card
 * with an "Open in browser" button (opens /workspace/file/raw in the system browser),
 * plus the existing in-app "Source" view (<pre>, unaffected by the iframe bug).
 *
 * Invariants under test:
 *  - the default view renders NO iframe (the blank-frame bug is gone, not relocated)
 *  - clicking "Open in browser" calls openExternal exactly once, with the dynamic
 *    api base (NOT hardcoded host/port) + /api/workspace/file/raw + encoded path
 *  - the Source toggle shows the raw HTML markup in-app
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import HtmlRenderer from '../HtmlRenderer';

const mockOpenExternal = vi.fn();
vi.mock('../../../../utils/openExternal', () => ({
  openExternal: (...a: unknown[]) => mockOpenExternal(...a),
}));

// Dynamic api base — mock to a known value so we can assert the exact URL.
vi.mock('../../../../services/tauri', () => ({
  getApiBaseUrl: () => 'http://localhost:18321',
}));

const PROPS = {
  filePath: 'Knowledge/Reports/my report.html', // space → must be encoded
  fileName: 'my report.html',
  content: '<!DOCTYPE html><html><body><h1>Hello</h1></body></html>',
  encoding: 'utf-8' as const,
  mimeType: 'text/html',
  fileSize: 1234,
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('HtmlRenderer opens HTML in the system browser', () => {
  it('renders NO iframe by default (blank-frame bug is eliminated)', () => {
    const { container } = render(<HtmlRenderer {...PROPS} />);
    expect(container.querySelector('iframe')).toBeNull();
  });

  it('clicking "Open in browser" calls openExternal once with dynamic base + /api/workspace/file/raw + encoded path', () => {
    render(<HtmlRenderer {...PROPS} />);
    fireEvent.click(screen.getByText('Open in browser'));
    expect(mockOpenExternal).toHaveBeenCalledTimes(1);
    expect(mockOpenExternal).toHaveBeenCalledWith(
      `http://localhost:18321/api/workspace/file/raw?path=${encodeURIComponent(PROPS.filePath)}`,
    );
    // encodeURIComponent must have escaped the space (no raw space in the URL)
    const calledUrl = mockOpenExternal.mock.calls[0][0] as string;
    expect(calledUrl).not.toContain(' ');
    expect(calledUrl).toContain('my%20report.html');
  });

  it('Source toggle shows the raw HTML markup in-app (no iframe path)', () => {
    const { container } = render(<HtmlRenderer {...PROPS} />);
    // toggle button carries a title attr; use it to disambiguate from any
    // "Source" text that may appear elsewhere after toggling
    fireEvent.click(screen.getByTitle('Show HTML source')); // rendered → source
    // markup is escaped into a <pre><code> block
    expect(container.querySelector('pre')).not.toBeNull();
    expect(container.textContent).toContain('DOCTYPE html');
    // still no iframe in source mode
    expect(container.querySelector('iframe')).toBeNull();
  });

  it('shows a graceful message when content is null', () => {
    render(<HtmlRenderer {...PROPS} content={null} />);
    expect(screen.getByText(/No HTML content available/i)).toBeInTheDocument();
  });
});
