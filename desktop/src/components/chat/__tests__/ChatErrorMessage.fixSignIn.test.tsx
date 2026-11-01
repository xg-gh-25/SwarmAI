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
