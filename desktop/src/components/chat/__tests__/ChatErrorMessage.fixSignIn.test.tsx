/**
 * The chat's "Credentials Expired" card (design 2026-11-01-settings-account-merge §4):
 * does it carry a button that asks the sign-in line to fix it (the same action, one place),
 * and do other errors not?
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { ChatErrorMessage } from '../ChatErrorMessage';
import { FIX_SIGN_IN_EVENT } from '../../common/settingsEvents';

vi.mock('../../../contexts/HealthContext', () => ({ useHealth: () => ({ triggerHealthCheck: vi.fn() }) }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

describe('ChatErrorMessage — fix the sign-in', () => {
  it('CREDENTIALS_EXPIRED has the button, and it asks the sign-in line', () => {
    const seen = vi.fn();
    window.addEventListener(FIX_SIGN_IN_EVENT, seen);
    render(<ChatErrorMessage error={{ code: 'CREDENTIALS_EXPIRED', message: 'Your Midway sign-in has expired.' }} />);
    fireEvent.click(screen.getByTestId('chat-error-fix-sign-in'));
    expect(seen).toHaveBeenCalledTimes(1);
    window.removeEventListener(FIX_SIGN_IN_EVENT, seen);
  });

  it('other errors have no such button', () => {
    render(<ChatErrorMessage error={{ code: 'RATE_LIMIT_EXCEEDED', message: 'slow down' }} />);
    expect(screen.queryByTestId('chat-error-fix-sign-in')).toBeNull();
  });
});

describe('ChatErrorMessage — nothing connected yet', () => {
  it('says so in its own words and opens the account page', async () => {
    const { OPEN_SETTINGS_EVENT } = await import('../../common/settingsEvents');
    const seen = vi.fn();
    window.addEventListener(OPEN_SETTINGS_EVENT, seen);
    render(<ChatErrorMessage error={{ code: 'ACCOUNT_NOT_CONNECTED', message: 'No AWS account is connected yet.', suggestedAction: 'Connect one…' }} />);
    expect(screen.getByText('credentialBanner.chat.notConnected')).toBeInTheDocument();
    expect(screen.getByText('credentialBanner.remedy.not_connected.message')).toBeInTheDocument();
    expect(screen.queryByText(/Credentials Expired/)).toBeNull();
    expect(screen.queryByTestId('chat-error-fix-sign-in')).toBeNull();
    fireEvent.click(screen.getByTestId('chat-error-connect'));
    expect((seen.mock.calls[0][0] as CustomEvent).detail).toEqual({ tab: 'aws-account' });
    window.removeEventListener(OPEN_SETTINGS_EVENT, seen);
  });
});
