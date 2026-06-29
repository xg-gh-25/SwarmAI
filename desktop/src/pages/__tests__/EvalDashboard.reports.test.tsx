/**
 * EvalDashboard ReportsTab iframe-render contract.
 *
 * Invariant under test (bugfix run_ba089062):
 *  - When a report is opened, the viewer iframe must use sandbox="allow-same-origin"
 *    (NOT "allow-scripts") and carry an opaque dark backdrop (bg-[var(--color-bg)],
 *    matching the report's own dark theme — not white, which would flash pre-paint).
 *    Same sandbox mechanism as HtmlRenderer.tsx / FilePreviewModal.tsx.
 *  - Root cause of the blank/black render: sandbox="allow-scripts" without
 *    allow-same-origin gives the srcDoc document a null/opaque origin, which fails
 *    to paint inline-styled HTML in the Tauri WebKit webview.
 *
 * NOTE (honest scope — GUI/jsdom limitation): jsdom is NOT WebKit, so this test
 * can only assert the iframe ATTRIBUTES are set correctly. The actual render
 * correctness is verified in the deployed Tauri app (REPRO gate), not here.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ReportsTab } from '../EvalDashboard';

const mockGet = vi.fn();
vi.mock('../../services/api', () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn().mockResolvedValue({ data: {} }),
    put: vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  },
}));

const REPORTS = [
  { filename: '2026-06-29_biweekly.html', sizeBytes: 47665, modified: 1751200000 },
];

// A report body the way the backend actually returns it: pure inline <style>, zero <script>.
const REPORT_HTML =
  '<!DOCTYPE html><html><head><style>:root{--bg:#0f172a}body{background:var(--bg)}</style></head>' +
  '<body><h1>OS Eval Report</h1></body></html>';

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ReportsTab />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGet.mockImplementation((url: string) => {
    if (url === '/eval/reports') return Promise.resolve({ data: REPORTS });
    if (url.startsWith('/eval/reports/')) return Promise.resolve({ data: REPORT_HTML });
    return Promise.resolve({ data: {} });
  });
});

describe('ReportsTab iframe render contract', () => {
  it('opens a report into an iframe with allow-same-origin + opaque bg (not allow-scripts)', async () => {
    const { container } = renderTab();

    // click the report row to enter the viewer (label = filename, .html stripped, _→space)
    const label = await screen.findByText('2026-06-29 biweekly');
    const row = label.closest('tr')!;
    fireEvent.click(row);

    // the iframe renders once the HTML is loaded
    const iframe = await screen.findByTitle('2026-06-29_biweekly.html');
    expect(iframe.tagName).toBe('IFRAME');

    // ROOT-CAUSE assertions — these FAIL on the old allow-scripts + no-bg iframe:
    expect(iframe.getAttribute('sandbox')).toBe('allow-same-origin');
    expect(iframe.getAttribute('sandbox')).not.toContain('allow-scripts');
    // opaque backdrop (dark, matching the report's own theme) — guards against
    // a transparent iframe showing the black void behind it. Not specifically
    // white: eval reports are dark-themed, so the backdrop is bg-[var(--color-bg)].
    expect(iframe.getAttribute('class') || '').toMatch(/\bbg-/);

    // the report content is actually wired into srcDoc (not empty)
    expect(iframe.getAttribute('srcdoc') || '').toContain('OS Eval Report');

    // silence unused-var lint on container
    expect(container).toBeTruthy();
  });
});
