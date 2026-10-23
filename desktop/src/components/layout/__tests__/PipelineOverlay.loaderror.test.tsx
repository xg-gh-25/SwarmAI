import type React from 'react';
/**
 * PipelineOverlay load-error classification (de-mask sweep, run_d6fd2c13).
 * The error-render path had ZERO coverage before this. Verifies the overlay now
 * distinguishes a 4xx contract error from a true outage instead of always saying
 * "backend may be unavailable" (WARN-2 from Gate-1).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PipelineContent } from '../PipelineOverlay';
import { pipelinesService } from '../../../services/pipelines';
import { ApiError } from '../../../services/api';
import { QueryClient as __QC, QueryClientProvider as __QCP } from '@tanstack/react-query';
import { render as __render } from '@testing-library/react';
// The overlay reads its data through TanStack Query (overlayQueries): a fresh client
// per render, so no cache carries over between tests.
const renderQ = (ui: React.ReactElement) =>
  __render(<__QCP client={new __QC({ defaultOptions: { queries: { retry: false } } })}>{ui}</__QCP>);

vi.mock('../../../services/pipelines', () => ({
  pipelinesService: { fetchAnalytics: vi.fn(), fetchRunDetail: vi.fn(), fetchActivePipelines: vi.fn().mockResolvedValue([]) },
}));

function openOverlay() {
  /* no-op: host-owned open */
}

describe('PipelineOverlay load-error classification', () => {
  beforeEach(() => vi.clearAllMocks());

  it('4xx → client-error message, NOT backend-unavailable', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    (pipelinesService.fetchAnalytics as ReturnType<typeof vi.fn>).mockRejectedValue(
      new ApiError({ code: 'VALIDATION_FAILED', message: 'bad window' }, 400),
    );
    renderQ(<PipelineContent onDispatch={() => true} close={() => {}} />);
    openOverlay();
    const err = await screen.findByTestId('pipeline-load-error');
    expect(err.textContent).toContain('HTTP 400');
    expect(err.textContent).toContain('client error');
    expect(err.textContent).not.toContain('backend may be unavailable');
  });

  it('5xx/outage → keeps backend-unavailable message', async () => {
    (pipelinesService.fetchAnalytics as ReturnType<typeof vi.fn>).mockRejectedValue(
      new ApiError({ code: 'SERVICE_UNAVAILABLE', message: 'down' }, 503),
    );
    renderQ(<PipelineContent onDispatch={() => true} close={() => {}} />);
    openOverlay();
    const err = await screen.findByTestId('pipeline-load-error');
    expect(err.textContent).toContain('backend may be unavailable');
    expect(err.textContent).not.toContain('client error');
  });
});
