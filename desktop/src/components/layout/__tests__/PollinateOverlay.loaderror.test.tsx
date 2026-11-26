import type React from 'react';
/**
 * PollinateOverlay load-error classification: a works load that fails shows the shared
 * load error, a 4xx as a client error and an outage as "backend may be unavailable".
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { PollinateContent } from '../PollinateOverlay';
import { pollinateService } from '../../../services/pollinate';
import { ApiError } from '../../../services/api';
import { QueryClient as __QC, QueryClientProvider as __QCP } from '@tanstack/react-query';
import { render as __render } from '@testing-library/react';

const renderQ = (ui: React.ReactElement) =>
  __render(<__QCP client={new __QC({ defaultOptions: { queries: { retry: false } } })}>{ui}</__QCP>);

vi.mock('../../../services/pollinate', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/pollinate')>();
  // the REAL service surface, every call mocked
  return { ...actual, pollinateService: { fetchWorks: vi.fn(), fetchWorkView: vi.fn(), fetchEval: vi.fn() } };
});

describe('PollinateOverlay load-error classification', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('4xx → client-error message, NOT backend-unavailable', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    (pollinateService.fetchWorks as ReturnType<typeof vi.fn>).mockRejectedValue(
      new ApiError({ code: 'VALIDATION_FAILED', message: 'bad' }, 422),
    );
    renderQ(<PollinateContent onDispatch={() => true} close={() => {}} />);
    const err = await screen.findByTestId('pollinate-load-error');
    expect(err.textContent).toContain('HTTP 422');
    expect(err.textContent).toContain('client error');
    expect(err.textContent).not.toContain('backend may be unavailable');
  });

  it('5xx/outage → keeps backend-unavailable message', async () => {
    (pollinateService.fetchWorks as ReturnType<typeof vi.fn>).mockRejectedValue(
      new ApiError({ code: 'SERVICE_UNAVAILABLE', message: 'down' }, 500),
    );
    renderQ(<PollinateContent onDispatch={() => true} close={() => {}} />);
    const err = await screen.findByTestId('pollinate-load-error');
    expect(err.textContent).toContain('backend may be unavailable');
    expect(err.textContent).not.toContain('client error');
  });
});
