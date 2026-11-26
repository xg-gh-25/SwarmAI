/**
 * Who can sign in to a Hive (HiveAccess in hiveComponents): each person has their own
 * browser key, its password shown once; the Host's own key is never shown.
 *
 * Questions: (1) does a running Hive show no password and no Show button (the old shared
 * password is gone from the screen); (2) does the list show each access, the old shared
 * password under its own name; (3) does Add access show the new password once, Copy all
 * copy it, and Done discard it; (4) does Revoke ask first and then call revokeAccess?
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import i18n from '../../../i18n';
import type { HiveInstance } from '../../../services/hive';

const mocks = { list: vi.fn(), add: vi.fn(), revoke: vi.fn(), rotate: vi.fn(), copy: vi.fn() };
vi.mock('../../../services/hive', async (orig) => ({
  ...(await orig<typeof import('../../../services/hive')>()),
  hiveService: {
    listAccess: (...a: unknown[]) => mocks.list(...a), addAccess: (...a: unknown[]) => mocks.add(...a),
    revokeAccess: (...a: unknown[]) => mocks.revoke(...a), rotateHostKey: (...a: unknown[]) => mocks.rotate(...a),
    latestVersion: vi.fn(async () => null), brainStatus: vi.fn(() => new Promise(() => {})),
  },
}));
vi.mock('../../../utils/clipboard', () => ({ copyToClipboard: (...a: unknown[]) => mocks.copy(...a) }));
vi.mock('../HiveBrains', () => ({ HiveBrains: () => null }));   // its own test; needs a QueryClient

import { HiveAccess, InstanceCard } from '../hiveComponents';

const SECRET = 'tok-9f8e7d6c5b4a';
const HIVE: HiveInstance = {
  id: 'h1', name: 'demo', ownerName: null, hiveType: 'my', accountRef: 'swarmai', region: 'us-east-1',
  instanceType: 'm7g.xlarge', ec2InstanceId: 'i-1', ec2PublicIp: null, elasticIpAllocId: null, securityGroupId: null,
  iamRoleName: null, cloudfrontDistId: null, cloudfrontDomain: 'd.cloudfront.net', s3Bucket: null, authUser: 'admin',
  status: 'running', version: '2.5.5', errorMessage: null, createdAt: 't', updatedAt: 't',
};
const LIST = [
  { user: 'admin', label: 'admin', createdAt: null, legacy: true },
  { user: 'bob', label: 'bob', createdAt: '2026-11-20T10:00:00Z', legacy: false },
];

beforeEach(async () => {
  for (const m of Object.values(mocks)) m.mockReset();
  mocks.list.mockResolvedValue(LIST);
  mocks.add.mockResolvedValue({ url: 'https://d.cloudfront.net', user: 'alice', password: SECRET });
  mocks.revoke.mockResolvedValue({ ok: true });
  mocks.copy.mockResolvedValue(true);
  await i18n.changeLanguage('en');
});

describe('HiveAccess', () => {
  it('Hive access panel shows no password and manages accesses', async () => {
    // (1) a running Hive's card: no password, no Show, no Share
    render(<InstanceCard instance={HIVE} onAction={vi.fn()} />);
    const panel = await screen.findByTestId('hive-access');
    await within(panel).findByTestId('hive-access-list');
    expect(within(panel).queryByText(/password:/i)).toBeNull();
    expect(screen.queryByRole('button', { name: /^show$/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /share/i })).toBeNull();
    expect(screen.queryByTestId('hive-access-password')).toBeNull();
    expect(panel.textContent).not.toContain('••••');
    expect(mocks.list).toHaveBeenCalledWith('h1');

    // (2) the list: the old shared password under its own name, with the hint
    expect(within(panel).getByTestId('hive-access-row-admin')).toHaveTextContent('Old shared password');
    expect(within(panel).getByTestId('hive-access-row-admin')).toHaveTextContent('Revoking it is recommended');
    expect(within(panel).getByTestId('hive-access-row-bob')).toHaveTextContent('bob');

    // (3) add access: the password shows once, Copy all copies it, Done discards it
    fireEvent.click(within(panel).getByTestId('hive-access-add'));
    fireEvent.change(within(panel).getByTestId('hive-access-name'), { target: { value: 'Alice' } });
    fireEvent.click(within(panel).getByTestId('hive-access-create'));
    await waitFor(() => expect(mocks.add).toHaveBeenCalledWith('h1', 'alice'));
    const grant = await within(panel).findByTestId('hive-access-grant');
    expect(within(grant).getByTestId('hive-access-password')).toHaveTextContent(SECRET);
    expect(grant).toHaveTextContent('will not be shown again');
    fireEvent.click(within(grant).getByTestId('hive-access-copy-all'));
    await waitFor(() => expect(mocks.copy).toHaveBeenCalledTimes(1));
    const copied = String(mocks.copy.mock.calls[0][0]);
    expect(copied).toContain('https://d.cloudfront.net');
    expect(copied).toContain('alice');
    expect(copied).toContain(SECRET);
    fireEvent.click(within(grant).getByTestId('hive-access-done'));
    expect(within(panel).queryByTestId('hive-access-grant')).toBeNull();
    expect(panel.textContent).not.toContain(SECRET);

    // (4) revoke asks first, then calls revokeAccess
    fireEvent.click(within(panel).getByTestId('hive-access-revoke-bob'));
    expect(mocks.revoke).not.toHaveBeenCalled();
    fireEvent.click(within(panel).getByTestId('hive-access-revoke-go'));
    await waitFor(() => expect(mocks.revoke).toHaveBeenCalledWith('h1', 'bob'));
  });

  it('a backend refusal is shown inline, and a stopped Hive lists nothing', async () => {
    mocks.add.mockRejectedValue(Object.assign(new Error('x'), { detail: 'Update this Hive first' }));
    const { unmount } = render(<HiveAccess instance={HIVE} />);
    fireEvent.click(await screen.findByTestId('hive-access-add'));
    fireEvent.change(screen.getByTestId('hive-access-name'), { target: { value: 'carol' } });
    fireEvent.click(screen.getByTestId('hive-access-create'));
    expect(await screen.findByTestId('hive-access-error')).toBeInTheDocument();
    expect(screen.queryByTestId('hive-access-grant')).toBeNull();
    unmount();

    mocks.list.mockClear();
    render(<HiveAccess instance={{ ...HIVE, status: 'stopped' }} />);
    expect(screen.getByTestId('hive-access')).toHaveTextContent('Start the Hive');
    expect(mocks.list).not.toHaveBeenCalled();
  });
});
