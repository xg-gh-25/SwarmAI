/**
 * EvalDashboard ReportsTab — open-in-system-browser contract (run_7bfdabac).
 *
 * Decision (XG): the in-app srcDoc iframe report viewer was replaced with opening
 * the report in the system browser. Rationale: srcDoc rendering in Tauri WebKit is
 * unreliable (2 failed iframe fixes) and an in-app arbitrary-HTML render surface is
 * a needless security/complexity cost. The list table stays; clicking a row calls
 * openExternal(<api base>/api/eval/reports/<encoded filename>).
 *
 * Invariants under test:
 *  - clicking a report row calls openExternal exactly once
 *  - the URL uses the dynamic api base (NOT a hardcoded host/port) + the /api/eval
 *    path, and the filename is encodeURIComponent'd (filenames contain spaces)
 *  - no iframe is rendered (the viewer is gone)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ReportsTab } from '../EvalDashboard';

const mockGet = vi.fn();
vi.mock('../../services/api', () => ({
  default: { get: (...a: unknown[]) => mockGet(...a), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const mockOpenExternal = vi.fn();
vi.mock('../../utils/openExternal', () => ({
  openExternal: (...a: unknown[]) => mockOpenExternal(...a),
}));

// getApiBaseUrl is the dynamic base — mock it to a known value so we can assert the URL.
vi.mock('../../services/tauri', () => ({
  getApiBaseUrl: () => 'http://localhost:18321',
}));

const REPORTS = [
  { filename: '2026-06-27_run C3 bvt-stamp.html', sizeBytes: 46856, modified: 1782496945 },
  { filename: '2026-06-29_biweekly.html', sizeBytes: 47665, modified: 1782711762 },
];

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
    return Promise.resolve({ data: {} });
  });
});

describe('ReportsTab opens reports in the system browser', () => {
  it('clicking a row opens the report URL via openExternal (dynamic base + encoded filename)', async () => {
    renderTab();
    // a filename with spaces — label strips .html and turns _ into space
    const label = await screen.findByText('2026-06-27_run C3 bvt-stamp'.replace(/_/g, ' '));
    fireEvent.click(label.closest('tr')!);

    expect(mockOpenExternal).toHaveBeenCalledTimes(1);
    const calledUrl = mockOpenExternal.mock.calls[0][0] as string;
    // dynamic base, not hardcoded 127.0.0.1
    expect(calledUrl.startsWith('http://localhost:18321/api/eval/reports/')).toBe(true);
    // filename spaces are percent-encoded
    expect(calledUrl).toContain(encodeURIComponent('2026-06-27_run C3 bvt-stamp.html'));
    expect(calledUrl).not.toContain(' ');
  });

  it('renders no iframe (the in-app viewer was removed)', async () => {
    const { container } = renderTab();
    await screen.findByText('2026-06-29 biweekly');
    expect(container.querySelector('iframe')).toBeNull();
  });
});
