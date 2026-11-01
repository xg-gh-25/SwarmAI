/**
 * The sign-in line speaks the person's language: the backend entry's id picks the
 * translation (credentialBanner.remedy.<id>), the backend English is only the fallback.
 */
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import CredentialBanner from './CredentialBanner';

vi.mock('../../contexts/HealthContext', () => ({
  useHealth: () => ({ health: { status: 'connected', auth: 'expired', lastCheckedAt: 0, consecutiveFailures: 0 }, triggerHealthCheck: vi.fn() }),
}));
vi.mock('../../services/awsAccount', () => ({
  awsAccountService: {
    get: () => Promise.resolve({ remediation: { id: 'not_connected', message: 'No AWS account is connected yet.',
      fix_text: 'Connect one in Settings.', settings_tab: 'aws-account', action: { kind: 'settings', tab: 'aws-account' } } }),
    verify: vi.fn(),
  },
}));
vi.mock('../../services/tauri', () => ({ isDesktop: () => true }));
const ZH: Record<string, string> = {
  'credentialBanner.remedy.not_connected.message': '还没连接 AWS 账号。',
  'credentialBanner.remedy.not_connected.fix': '到设置里连接一个。',
};
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => ZH[k] ?? k }) }));

describe('CredentialBanner words', () => {
  it('shows the translation for the entry, not the backend English', async () => {
    render(<CredentialBanner />);
    const line = await screen.findByTestId('credential-banner');
    await screen.findByText('还没连接 AWS 账号。');
    expect(line).toHaveTextContent('到设置里连接一个。');
    expect(line).not.toHaveTextContent('No AWS account');
  });
});
