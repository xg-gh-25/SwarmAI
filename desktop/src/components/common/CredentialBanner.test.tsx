/**
 * Tests for CredentialBanner — renders ONLY on auth==='expired'.
 *
 * Verifies the fail-open contract: valid / unknown / undefined → no banner;
 * only a definitive 'expired' shows the mwinit -f instruction.
 */
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import CredentialBanner from './CredentialBanner';
import type { HealthState, AuthStatus } from '../../types';

vi.mock('../../contexts/HealthContext', () => ({
  useHealth: vi.fn(),
}));
import { useHealth } from '../../contexts/HealthContext';

function setAuth(auth?: AuthStatus) {
  const health: HealthState = {
    status: 'connected',
    auth,
    lastCheckedAt: Date.now(),
    consecutiveFailures: 0,
  };
  (useHealth as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    health,
    triggerHealthCheck: vi.fn(),
  });
}

describe('CredentialBanner', () => {
  it('renders the mwinit instruction when auth is expired', () => {
    setAuth('expired');
    render(<CredentialBanner />);
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText(/mwinit -f/)).toBeInTheDocument();
    expect(screen.getByText(/credentials expired/i)).toBeInTheDocument();
  });

  it('renders nothing when auth is valid', () => {
    setAuth('valid');
    const { container } = render(<CredentialBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when auth is unknown (fail-open)', () => {
    setAuth('unknown');
    const { container } = render(<CredentialBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when auth is undefined (pre-first-poll)', () => {
    setAuth(undefined);
    const { container } = render(<CredentialBanner />);
    expect(container).toBeEmptyDOMElement();
  });
});
