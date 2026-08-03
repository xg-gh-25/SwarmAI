/**
 * FilePreviewModal — HTML preview renders via a data: URL (run_7886ca1c).
 *
 * Bug: the html-preview iframe used `srcDoc={content}`, which renders a BLANK
 * frame in the packaged Tauri WKWebView (same root cause as HtmlRenderer,
 * run_344d1fd6). Fix: a `data:text/html` URL built from the ALREADY-FETCHED
 * content. Chosen over src=<raw endpoint URL> because the raw endpoint resolves
 * only the singleton workspace — it loses the basePath/agentId context this modal
 * reads with, so agent-workdir files would 404 (Gate-1 CRITICAL, run_7886ca1c).
 * A data: URL uses content already in memory, is natively opaque-origin, and
 * renders in WKWebView. Kept script-inert (no allow-scripts).
 *
 * Invariants under test:
 *  - html-preview iframe src is a data:text/html;charset=utf-8 URL encoding the content
 *  - it is NOT srcDoc (the blank-frame trigger)
 *  - sandbox has NEITHER allow-same-origin NOR allow-scripts (opaque + script-inert)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { FilePreviewModal } from '../FilePreviewModal';

// Kept in sync with the mock's returned content below. (Cannot reference a
// top-level const inside vi.mock — it is hoisted above the const init.)
const HTML = '<!DOCTYPE html><html><body><h1>Hi &amp; bye</h1></body></html>';

// Mock the file-read boundary → return html-preview content. The literal is
// inlined here (hoist-safe); the assertions use the HTML const, which equals it.
vi.mock('../../../services/workspace', () => ({
  workspaceService: {
    readFile: vi.fn().mockResolvedValue({
      content: '<!DOCTYPE html><html><body><h1>Hi &amp; bye</h1></body></html>',
      encoding: 'utf-8',
      mimeType: 'text/html',
      size: 60,
    }),
  },
}));

function renderModal() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <FilePreviewModal
        isOpen
        onClose={() => {}}
        agentId="a1"
        file={{ path: 'Knowledge/Reports/x.html', name: 'x.html' }}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.clearAllMocks());

describe('FilePreviewModal HTML preview — data: URL (WKWebView blank-frame fix)', () => {
  it('renders the html-preview iframe with a data:text/html src encoding the content, NOT srcDoc', async () => {
    const { container } = renderModal();
    const iframe = await waitFor(() => {
      const el = container.querySelector('iframe');
      expect(el).not.toBeNull();
      return el!;
    });
    const src = iframe.getAttribute('src') ?? '';
    expect(src.startsWith('data:text/html;charset=utf-8,')).toBe(true);
    expect(src).toContain(encodeURIComponent(HTML));
    // NOT the old srcDoc string-injection (the blank-frame trigger).
    expect(iframe.hasAttribute('srcdoc')).toBe(false);
  });

  it('iframe sandbox has NEITHER allow-same-origin NOR allow-scripts (opaque + script-inert)', async () => {
    const { container } = renderModal();
    const iframe = await waitFor(() => {
      const el = container.querySelector('iframe');
      expect(el).not.toBeNull();
      return el!;
    });
    const sandbox = iframe.getAttribute('sandbox') ?? '';
    expect(sandbox).not.toContain('allow-same-origin');
    expect(sandbox).not.toContain('allow-scripts');
  });
});
