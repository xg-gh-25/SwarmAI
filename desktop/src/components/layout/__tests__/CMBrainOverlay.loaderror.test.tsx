/**
 * CMBrainOverlay "Needs you" load-error classification (de-mask sweep, run_d6fd2c13).
 * The needs-error path had ZERO coverage. Verifies the queue-load failure now
 * classifies 4xx-vs-outage while PRESERVING the domain nuance ("This is NOT
 * 'nothing to do'.") on the outage branch (WARN-1 + WARN-2 from Gate-1).
 *
 * CMBrain uses TanStack Query (api.get under the hood) — mock api.get to reject,
 * wrap in a QueryClientProvider with retry disabled so isError fires immediately.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { CMBrainOverlay } from '../CMBrainOverlay';
import api, { ApiError } from '../../../services/api';

vi.mock('../../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api')>();
  return { ...actual, default: { get: vi.fn() } };
});

function renderOpen() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <CMBrainOverlay />
    </QueryClientProvider>,
  );
  window.dispatchEvent(new CustomEvent('swarm:show-context'));
}

describe('CMBrainOverlay needs-error classification', () => {
  beforeEach(() => vi.clearAllMocks());

  it('4xx → client-error message, NOT the outage nuance', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    (api.get as ReturnType<typeof vi.fn>).mockRejectedValue(
      new ApiError({ code: 'VALIDATION_FAILED', message: 'bad' }, 400),
    );
    renderOpen();
    const err = await screen.findByTestId('cm-needs-error');
    expect(err.textContent).toContain('HTTP 400');
    expect(err.textContent).toContain('client error');
    expect(err.textContent).not.toContain('nothing to do');
  });

  it('outage → preserves the "NOT nothing to do" domain nuance', async () => {
    (api.get as ReturnType<typeof vi.fn>).mockRejectedValue(
      new ApiError({ code: 'SERVICE_UNAVAILABLE', message: 'down' }, 503),
    );
    renderOpen();
    const err = await screen.findByTestId('cm-needs-error');
    expect(err.textContent).toContain('nothing to do'); // WARN-1: nuance preserved
    expect(err.textContent).toContain('backend may be unavailable');
    expect(err.textContent).not.toContain('client error');
  });
});
