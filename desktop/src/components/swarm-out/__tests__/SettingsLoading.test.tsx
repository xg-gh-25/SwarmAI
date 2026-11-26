/**
 * Swarm Out › Settings shows what it has at once (SwarmOutSettings: each request fills its own
 * section; the tab's last data is kept for when it opens again).
 *
 * Questions: (1) while the Elastic Workers status has not answered, do the account card and the
 * EC2 list show their data, and does the Elastic card say Loading (never Off); (2) while the
 * account view has not answered, is the button that adds an EC2 Hive disabled, and once a
 * request failed, does its section say it could not be read (never Loading for good) without
 * locking that button; (3) does a reopened tab show the data read last time at once, before
 * any request answers again, including a default the person just changed; (4) is the tab as wide
 * as the panel like the other views, and on a narrow panel does each row put its label above its
 * value instead of keeping a fixed label column?
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within, fireEvent } from '@testing-library/react';
import i18n from '../../../i18n';

const mocks = { get: vi.fn(), elastic: vi.fn(), instances: vi.fn(), accounts: vi.fn(), cfg: vi.fn() };
vi.mock('../../../services/awsAccount', () => ({
  awsAccountService: {
    get: (...a: unknown[]) => mocks.get(...a), elasticStatus: (...a: unknown[]) => mocks.elastic(...a),
    setElasticStandby: vi.fn(), setFleet: vi.fn(), createDefaultVpc: vi.fn(), permissionTemplate: vi.fn(), check: vi.fn(),
  },
  problemOf: (e: unknown) => ({ kind: 'error', message: String(e) }),
}));
vi.mock('../../../services/hive', async (orig) => ({
  ...(await orig<typeof import('../../../services/hive')>()),
  hiveService: {
    listInstances: (...a: unknown[]) => mocks.instances(...a), listAccounts: (...a: unknown[]) => mocks.accounts(...a),
    listSsh: vi.fn(() => new Promise(() => {})), setIdleStop: vi.fn(),
    deleteInstance: vi.fn(), retryInstance: vi.fn(), stopInstance: vi.fn(), startInstance: vi.fn(), listAccess: vi.fn(async () => []), addAccess: vi.fn(), revokeAccess: vi.fn(), rotateHostKey: vi.fn(),
    brainStatus: vi.fn(() => new Promise(() => {})),
  },
}));
vi.mock('../../../services/settings', () => ({
  settingsService: { getAPIConfiguration: (...a: unknown[]) => mocks.cfg(...a), updateAPIConfiguration: vi.fn(async () => undefined) },
}));
vi.mock('../../../services/tauri', () => ({ isDesktop: () => false }));
vi.mock('../../awsAccount/ConnectAccount', () => ({ default: () => null }));

import { SwarmOutSettings, forgetSettingsForTests } from '../SwarmOutSettings';

const never = () => new Promise(() => {});
const VIEW = { state: 'connected', account: { account_id: '533267412361', region: 'us-east-1' },
  hive: { state: 'ready', desired: 1, counted: 1, instance_type: 'm7g.xlarge', region: 'us-east-1', prices: {} } };
const HIVE = {
  id: 'remote-demo', name: 'remote-demo', ownerName: null, hiveType: 'my', accountRef: 'swarmai', region: 'us-east-1',
  instanceType: 'm7g.xlarge', ec2InstanceId: 'i-1', ec2PublicIp: null, elasticIpAllocId: null, securityGroupId: null,
  iamRoleName: null, cloudfrontDistId: null, cloudfrontDomain: 'd.cloudfront.net', s3Bucket: null, authUser: 'admin',
  status: 'stopped', version: '2.5.5', errorMessage: null, createdAt: 't', updatedAt: 't',
  idleStopMinutes: 0, idleStopEffective: 0, idleStopSettable: true,
};
const ELASTIC = { host_version: '2.5.5', can_enable: true, reason: null, standby: true, standby_last: null, size: null,
  hives: [{ id: 'e1', name: 'elastic', account_ref: 'swarmai', region: 'us-east-1', status: 'ready', error_message: null,
    runtime_status: 'READY', image_version: '2.5.5', up_to_date: true, open_runs: 0, working: false }] };

beforeEach(async () => {
  forgetSettingsForTests();
  for (const m of Object.values(mocks)) m.mockReset();
  mocks.get.mockResolvedValue(VIEW);
  mocks.elastic.mockResolvedValue(ELASTIC);
  mocks.instances.mockResolvedValue([HIVE]);
  mocks.accounts.mockResolvedValue([]);
  mocks.cfg.mockResolvedValue({ hiveIdleStopMinutes: 120 });
  await i18n.changeLanguage('en');
});

describe('the Settings tab shows what it has at once', () => {
  it('sections render on their own', async () => {
    mocks.elastic.mockImplementation(never);                    // AWS does not answer
    render(<SwarmOutSettings />);
    expect(await screen.findByTestId('so-account-id-value')).toHaveTextContent('5332');
    expect(await screen.findAllByTestId('so-hive-row')).toHaveLength(1);
    expect(screen.getByTestId('so-elastic-state')).toHaveTextContent('Loading');
    expect(screen.getByTestId('so-elastic-state')).not.toHaveTextContent('Off');
    expect(screen.getByTestId('so-cap-elastic')).toHaveTextContent('Loading');
    expect((screen.getByTestId('so-elastic-switch') as HTMLButtonElement).disabled).toBe(true);
  });

  it('the add button waits for the account view', async () => {
    mocks.get.mockImplementation(never);
    render(<SwarmOutSettings />);
    await screen.findAllByTestId('so-hive-row');
    expect((screen.getByTestId('so-hive-add') as HTMLButtonElement).disabled).toBe(true);
  });

  it('a status that could not be read says so, and an unread account does not lock the add button', async () => {
    mocks.elastic.mockRejectedValue(new Error('500'));
    mocks.get.mockRejectedValue(new Error('500'));
    render(<SwarmOutSettings />);
    await waitFor(() => expect(screen.getByTestId('so-elastic-state')).toHaveTextContent('Could not read its status'));
    expect(screen.getByTestId('so-elastic-state')).not.toHaveTextContent('Loading');
    await screen.findAllByTestId('so-hive-row');
    await waitFor(() => expect((screen.getByTestId('so-hive-add') as HTMLButtonElement).disabled).toBe(false));
  });

  it('with the account read, a failed status says so on the account card too', async () => {
    mocks.elastic.mockRejectedValue(new Error('500'));
    render(<SwarmOutSettings />);
    await waitFor(() => expect(screen.getByTestId('so-cap-elastic')).toHaveTextContent('Could not read its status'));
    expect(screen.getByTestId('so-cap-elastic')).not.toHaveTextContent('Loading');
  });

  it('a reopened tab shows its last data at once', async () => {
    const first = render(<SwarmOutSettings />);
    await waitFor(() => expect(screen.getByTestId('so-elastic-state')).toHaveTextContent('on standby'));
    await screen.findAllByTestId('so-hive-row');
    first.unmount();
    for (const m of [mocks.get, mocks.elastic, mocks.instances, mocks.accounts, mocks.cfg]) m.mockImplementation(never);
    render(<SwarmOutSettings />);
    // no wait: the last data is there before anything answers again
    expect(screen.getAllByTestId('so-hive-row')).toHaveLength(1);
    expect(within(screen.getByTestId('so-set-account')).getByTestId('so-account-id-value')).toHaveTextContent('5332');
    expect(screen.getByTestId('so-elastic-state')).toHaveTextContent('on standby');
    expect(mocks.elastic).toHaveBeenCalledTimes(2);             // and it is read again underneath
  });

  it('a default changed on the tab is what a reopened tab shows', async () => {
    mocks.cfg.mockResolvedValue({ hiveIdleStopMinutes: 120 });
    const first = render(<SwarmOutSettings />);
    const sel = await screen.findByTestId('so-idle') as HTMLSelectElement;
    await waitFor(() => expect(sel.value).toBe('120'));
    for (const m of [mocks.get, mocks.elastic, mocks.instances, mocks.accounts, mocks.cfg]) m.mockImplementation(never);
    fireEvent.change(sel, { target: { value: '30' } });
    await waitFor(() => expect(sel.value).toBe('30'));
    first.unmount();
    render(<SwarmOutSettings />);
    expect((screen.getByTestId('so-idle') as HTMLSelectElement).value).toBe('30');
  });

  it('fills the panel, and on a narrow panel each row stacks its label above its value', async () => {
    const { unmount } = render(<SwarmOutSettings />);
    const wide = await screen.findByTestId('so-account-id');
    expect(screen.getByTestId('swarm-out-settings').className).not.toMatch(/max-w-/);
    expect(wide.dataset.compact).toBeUndefined();
    expect(wide.firstElementChild!.className).toMatch(/w-\[150px\]/);
    unmount();
    render(<SwarmOutSettings compact />);
    const narrow = await screen.findByTestId('so-account-id');
    expect(narrow.dataset.compact).toBe('true');
    expect(narrow.className).toMatch(/flex-col/);
    expect(narrow.firstElementChild!.className).not.toMatch(/w-\[150px\]/);
  });
});
