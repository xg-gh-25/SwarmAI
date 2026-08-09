/**
 * Tests for RecallMetricsPanel — the per-context recall-latency read-out (Run 3).
 *
 * Load-bearing contract:
 *  - renders count + p50/p95 per (context, domain) row from GET /api/recall/metrics;
 *  - QUIET on no data (a faint "no recall samples yet" line, not an empty void);
 *  - hidden on error (a visibility widget must not shout when the read fails).
 *
 * api is mocked at the boundary; the component invents no numbers.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../services/api', () => ({
  default: { get: vi.fn() },
}));
import api from '../../services/api';
import { RecallMetricsPanel } from './RecallMetricsPanel';

function mockMetrics(body: unknown) {
  (api.get as ReturnType<typeof vi.fn>).mockResolvedValue({ data: body });
}

function renderPanel(context = 'library_overlay') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <RecallMetricsPanel context={context} />
    </QueryClientProvider>,
  );
}

beforeEach(() => { vi.clearAllMocks(); });
afterEach(() => { cleanup(); });

describe('RecallMetricsPanel', () => {
  it('renders count + p50/p95 for each row', async () => {
    mockMetrics({
      generated_at: '2026-08-09T10:00:00',
      contexts: [
        { context: 'library_overlay', domain: 'library', count: 5, p50_ms: 12, p95_ms: 40 },
        { context: 'library_overlay', domain: 'codeintel', count: 3, p50_ms: 8, p95_ms: 15 },
      ],
    });
    renderPanel();
    await waitFor(() => {
      expect(screen.getAllByTestId('recall-metrics-row')).toHaveLength(2);
    });
    // The p50/p95 numbers are rendered (not fabricated) — assert the exact read-out.
    expect(screen.getByText(/n=5 · p50 12ms · p95 40ms/)).toBeTruthy();
    expect(screen.getByText(/n=3 · p50 8ms · p95 15ms/)).toBeTruthy();
    expect(screen.getByText('library')).toBeTruthy();
    // it queried the endpoint filtered to its context
    expect(api.get).toHaveBeenCalledWith(expect.stringContaining('/recall/metrics?context=library_overlay'));
  });

  it('is quiet (empty line) when there are no samples yet', async () => {
    mockMetrics({ generated_at: '2026-08-09T10:00:00', contexts: [] });
    renderPanel();
    await waitFor(() => {
      expect(screen.getByTestId('recall-metrics-empty')).toBeTruthy();
    });
    expect(screen.queryByTestId('recall-metrics-panel')).toBeNull();
  });

  it('hides on error (does not shout)', async () => {
    (api.get as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('boom'));
    renderPanel();
    await waitFor(() => {
      expect(screen.getByTestId('recall-metrics-error')).toBeTruthy();
    });
    expect(screen.queryByTestId('recall-metrics-panel')).toBeNull();
  });
});
