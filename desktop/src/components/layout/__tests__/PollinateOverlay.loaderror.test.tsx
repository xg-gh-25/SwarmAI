/**
 * PollinateOverlay load-error classification (de-mask sweep, run_d6fd2c13).
 * Error-render path had ZERO coverage before. Verifies 4xx vs outage classification
 * replaced the always-"backend may be unavailable" string (WARN-2 from Gate-1).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PollinateOverlay } from '../PollinateOverlay';
import { pollinateService } from '../../../services/pollinate';
import { ApiError } from '../../../services/api';

vi.mock('../../../services/pollinate', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/pollinate')>();
  // Faithful mock: mirror the REAL pollinateService surface (fetchAssets +
  // fetchAssetBody + fetchTopicDetail) so it can't drift out of sync with the
  // service. These tests only trigger fetchAssets (the drawer never opens), but
  // an incomplete mock is a latent AttributeError for any future drawer test.
  return { ...actual, pollinateService: { fetchAssets: vi.fn(), fetchAssetBody: vi.fn(), fetchTopicDetail: vi.fn() } };
});

function openOverlay() {
  window.dispatchEvent(new CustomEvent('swarm:show-pollinate'));
}

describe('PollinateOverlay load-error classification', () => {
  beforeEach(() => vi.clearAllMocks());

  it('4xx → client-error message, NOT backend-unavailable', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    (pollinateService.fetchAssets as ReturnType<typeof vi.fn>).mockRejectedValue(
      new ApiError({ code: 'VALIDATION_FAILED', message: 'bad' }, 422),
    );
    render(<PollinateOverlay onDispatch={() => true} />);
    openOverlay();
    const err = await screen.findByTestId('pollinate-load-error');
    expect(err.textContent).toContain('HTTP 422');
    expect(err.textContent).toContain('client error');
    expect(err.textContent).not.toContain('backend may be unavailable');
  });

  it('5xx/outage → keeps backend-unavailable message', async () => {
    (pollinateService.fetchAssets as ReturnType<typeof vi.fn>).mockRejectedValue(
      new ApiError({ code: 'SERVICE_UNAVAILABLE', message: 'down' }, 500),
    );
    render(<PollinateOverlay onDispatch={() => true} />);
    openOverlay();
    const err = await screen.findByTestId('pollinate-load-error');
    expect(err.textContent).toContain('backend may be unavailable');
    expect(err.textContent).not.toContain('client error');
  });
});
