/**
 * AccountConnectedNotice — the one-time "SwarmAI account connected" line for an upgrader
 * (design 2026-10-31-swarmai-aws-account §6.4).
 * Questions: shown once for a connected account set up from the old settings; never for
 * one the wizard connected or one not verified; not again for the same account.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import AccountConnectedNotice, { SEEN_KEY } from './AccountConnectedNotice';

const mockGet = vi.fn();
const addToast = vi.fn();
vi.mock('../../services/awsAccount', () => ({ awsAccountService: { get: (...a: unknown[]) => mockGet(...a) } }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => ({ addToast }) }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string, o?: Record<string, string>) => (o ? `${k}|${o.account}` : k) }) }));

const view = (derived: boolean, state = 'connected') => ({ state, account: { account_id: '533267412361', derived } });

beforeEach(() => { mockGet.mockReset(); addToast.mockReset(); localStorage.clear(); });

describe('AccountConnectedNotice', () => {
  it('tells an upgrader once', async () => {
    mockGet.mockResolvedValue(view(true));
    const { unmount } = render(<AccountConnectedNotice />);
    await waitFor(() => expect(addToast).toHaveBeenCalledTimes(1));
    expect(addToast.mock.calls[0][0].message).toBe('awsConnect.connectedNotice|533267412361');
    expect(localStorage.getItem(SEEN_KEY)).toBe('533267412361');
    unmount();
    render(<AccountConnectedNotice />);
    await new Promise((r) => setTimeout(r, 20));
    expect(addToast).toHaveBeenCalledTimes(1);
  });

  it('waits for the startup check of an upgrader\'s record', async () => {
    vi.useFakeTimers();
    try {
      mockGet.mockResolvedValueOnce(view(true, 'unverified')).mockResolvedValue(view(true));
      render(<AccountConnectedNotice />);
      await vi.advanceTimersByTimeAsync(0);
      expect(addToast).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(20_000);
      expect(addToast).toHaveBeenCalledTimes(1);
    } finally { vi.useRealTimers(); }
  });

  it.each([[false, 'connected'], [true, 'needs_login']])(
    'says nothing for derived=%s state=%s', async (derived, state) => {
      mockGet.mockResolvedValue(view(derived, state));
      render(<AccountConnectedNotice />);
      await waitFor(() => expect(mockGet).toHaveBeenCalled());
      await new Promise((r) => setTimeout(r, 20));
      expect(addToast).not.toHaveBeenCalled();
    });
});
