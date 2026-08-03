/**
 * HtmlRenderer — inline render via src=<raw endpoint URL> (run_344d1fd6).
 *
 * Bug + fix history: the in-app HTML preview used `<iframe srcDoc={content}>`.
 * In the packaged Tauri WKWebView, srcDoc (a JS→DOM string injection) renders a
 * BLANK frame. Two earlier srcDoc fixes (sandbox, height) were both falsified
 * (commit 496bbd7c). The reliable path in WKWebView is REAL NAVIGATION: point the
 * iframe `src` at the backend raw endpoint (which serves Content-Type: text/html,
 * no Content-Disposition:attachment — verified live), so the WebView loads it as a
 * normal document instead of a srcDoc string. The user wants the report rendered
 * INLINE in Canvas (not a browser jump), so this keeps the iframe — but src=, not
 * srcDoc.
 *
 * Isolation is UNCHANGED: sandbox="allow-scripts" WITHOUT allow-same-origin forces
 * an OPAQUE origin even for a same-origin src URL (MDN-verified) — the frame still
 * cannot reach the parent DOM/cookies/storage. Same risk profile as the old
 * srcDoc+allow-scripts, but it actually renders.
 *
 * Invariants under test:
 *  - default (Rendered) mode renders an iframe whose `src` is the dynamic api base
 *    (NOT hardcoded) + /api/workspace/file/raw + encoded path, NOT srcDoc
 *  - the iframe sandbox has `allow-scripts` but NOT `allow-same-origin` (opaque origin)
 *  - "Open in browser" fallback still calls openExternal with the same URL
 *  - the Source toggle shows the raw HTML markup in-app (no iframe)
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

const EXPECTED_RAW_URL = `http://localhost:18321/api/workspace/file/raw?path=${encodeURIComponent(PROPS.filePath)}`;

describe('HtmlRenderer renders HTML inline via src=<raw endpoint URL>', () => {
  it('default mode renders an iframe with src=<raw URL> (NOT srcDoc — the WKWebView blank-frame fix)', () => {
    const { container } = render(<HtmlRenderer {...PROPS} />);
    const iframe = container.querySelector('iframe');
    expect(iframe).not.toBeNull();
    // Real navigation: src points at the raw endpoint (dynamic base + encoded path)…
    expect(iframe!.getAttribute('src')).toBe(EXPECTED_RAW_URL);
    // …and it is NOT the old srcDoc string-injection path (the blank-frame trigger).
    expect(iframe!.hasAttribute('srcdoc')).toBe(false);
    // encodeURIComponent escaped the space in the path.
    expect(iframe!.getAttribute('src')).toContain('my%20report.html');
    expect(iframe!.getAttribute('src')).not.toContain(' ');
  });

  it('iframe sandbox keeps allow-scripts but NOT allow-same-origin (opaque-origin isolation)', () => {
    const { container } = render(<HtmlRenderer {...PROPS} />);
    const sandbox = container.querySelector('iframe')!.getAttribute('sandbox') ?? '';
    expect(sandbox).toContain('allow-scripts');
    expect(sandbox).not.toContain('allow-same-origin');
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
