/**
 * Swarm Out › Settings layout (SwarmOutSettings; mockup swarm-out-settings-v1.html): the page
 * answers "can work run now, and on what" before anything else, Elastic Workers sits on its own
 * above the machines, and EC2 Hives and SSH machines are one list.
 *
 * Questions: (1) does the sentence on top count what can take work (Elastic Workers ready, the
 * machines ready, off, and the ones needing the person), and say so when nothing can; (2) does
 * the Elastic Workers block come before the machine list, outside it; (3) are EC2 and SSH rows in
 * the one list, each closed to its name, kind and state; (4) does "+ Add a machine" ask which
 * kind and open the SSH form inside the list; (5) are the rare and dangerous actions of an
 * opened Hive (address, Host key, delete) under More, not beside the three things it changes?
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within, fireEvent } from '@testing-library/react';
import i18n from '../../../i18n';

const mocks = { get: vi.fn(), elastic: vi.fn(), instances: vi.fn(), ssh: vi.fn() };
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
    listInstances: (...a: unknown[]) => mocks.instances(...a), listAccounts: vi.fn(async () => []),
    listSsh: (...a: unknown[]) => mocks.ssh(...a), setIdleStop: vi.fn(), addSsh: vi.fn(),
    deleteInstance: vi.fn(), retryInstance: vi.fn(), stopInstance: vi.fn(), startInstance: vi.fn(),
    listAccess: vi.fn(async () => []), addAccess: vi.fn(), revokeAccess: vi.fn(), rotateHostKey: vi.fn(),
  },
}));
vi.mock('../../../services/settings', () => ({
  settingsService: { getAPIConfiguration: vi.fn(async () => ({ hiveIdleStopMinutes: 120 })), updateAPIConfiguration: vi.fn() },
}));
vi.mock('../../../services/tauri', () => ({ isDesktop: () => false }));
vi.mock('../../awsAccount/ConnectAccount', () => ({ default: () => null }));
vi.mock('../../settings/HiveBrains', () => ({ HiveBrains: () => null }));

import { SwarmOutSettings, forgetSettingsForTests } from '../SwarmOutSettings';

const VIEW = { state: 'connected', account: { account_id: '533267412361', region: 'us-east-1' },
  hive: { state: 'ready', desired: 2, counted: 2, instance_type: 'm7g.xlarge', region: 'us-east-1', prices: { 'm7g.xlarge': 119 } } };
const hive = (id: string, status: string, extra: Record<string, unknown> = {}) => ({
  id, name: id, ownerName: null, hiveType: 'my', accountRef: 'swarmai', region: 'us-east-1',
  instanceType: 'm7g.xlarge', ec2InstanceId: 'i-1', ec2PublicIp: null, elasticIpAllocId: null, securityGroupId: null,
  iamRoleName: null, cloudfrontDistId: null, cloudfrontDomain: `${id}.cloudfront.net`, s3Bucket: null, authUser: 'host',
  status, version: '2.6.0', errorMessage: null, createdAt: 't', updatedAt: 't',
  idleStopMinutes: null, idleStopEffective: 120, idleStopSettable: true, ...extra,
});
const ELASTIC = (status: string | null) => ({ host_version: '2.6.0', can_enable: true, reason: null, standby: status !== null,
  standby_last: null, size: null,
  hives: status ? [{ id: 'e1', name: 'elastic', account_ref: 'swarmai', region: 'us-east-1', status, error_message: null,
    runtime_status: 'READY', image_version: '2.6.0', up_to_date: true, open_runs: 0, working: false }] : [] });
const now = Date.now() / 1000;
const SSH = { id: 'm1', name: 'dev-box', status: 'running', reachable: true, cert_valid_until: now + 3600, version: '2.6.0',
  instance_type: 'ssh', error_message: null, tunnel_error: '',
  config: { ssh_host: 'dev.example.com', reaches_private: true, general_tasks: true, mounts: [], mounts_auto: true,
    cert_renew_hint: '', identity: 'rolesanywhere', not_after: now + 86400, renew_error: null } };

beforeEach(async () => {
  forgetSettingsForTests();
  for (const m of Object.values(mocks)) m.mockReset();
  mocks.get.mockResolvedValue(VIEW);
  mocks.elastic.mockResolvedValue(ELASTIC('ready'));
  mocks.instances.mockResolvedValue([hive('demo', 'running'), hive('explorer', 'stopped')]);
  mocks.ssh.mockResolvedValue([SSH]);
  await i18n.changeLanguage('en');
});

const verdict = async () => {
  await waitFor(() => expect(screen.getByTestId('so-verdict').dataset.state).not.toBe('checking'));
  await waitFor(() => expect(screen.getAllByTestId('so-ssh-row').length).toBeGreaterThan(0));
  return screen.getByTestId('so-verdict');
};

describe('the Settings tab answers first, then lists what runs work', () => {
  it('the sentence on top counts what can take work', async () => {
    render(<SwarmOutSettings />);
    // a running EC2 Hive and a reachable SSH machine are ready; a stopped Hive is off
    expect(await verdict()).toHaveTextContent('Work can run · Elastic Workers and 2 machines are ready · 1 machine is off');
  });

  it('a broken machine is counted as needing the person; nothing at all says what to do', async () => {
    mocks.instances.mockResolvedValue([hive('demo', 'error', { errorMessage: 'boom' })]);
    mocks.ssh.mockResolvedValue([]);
    mocks.elastic.mockResolvedValue(ELASTIC(null));
    render(<SwarmOutSettings />);
    await waitFor(() => expect(screen.getByTestId('so-verdict').dataset.state).toBe('none'));
    expect(screen.getByTestId('so-verdict')).toHaveTextContent('Nothing can take work now · 1 machine needs you');
  });

  it('never "nothing can run" on a guess: an unread SSH list is checking, a failed read says so', async () => {
    mocks.instances.mockResolvedValue([]);
    mocks.elastic.mockResolvedValue(ELASTIC(null));
    let answer: (v: unknown) => void = () => {};
    mocks.ssh.mockImplementation(() => new Promise((r) => { answer = r; }));
    const first = render(<SwarmOutSettings />);
    await waitFor(() => expect(mocks.instances).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByTestId('so-verdict').dataset.state).toBe('checking');
    answer([SSH]);                                              // its only machine is an SSH one
    await waitFor(() => expect(screen.getByTestId('so-verdict')).toHaveTextContent('1 machine is ready'));
    first.unmount();
    forgetSettingsForTests();
    mocks.ssh.mockRejectedValue(new Error('500'));
    render(<SwarmOutSettings />);
    await waitFor(() => expect(screen.getByTestId('so-verdict').dataset.state).toBe('unreadable'));
  });

  it('an opened row stays open when a poll changes its state; Restart asks first', async () => {
    const { rerender } = render(<SwarmOutSettings />);
    await verdict();
    const row = () => screen.getAllByTestId('so-hive-row')[0];
    fireEvent.click(within(row()).getByTestId('so-hive-toggle'));
    expect(within(row()).getByTestId('so-hive-login')).toBeInTheDocument();
    // the fleet poll now finds it unreachable (its tone turns to error): still open
    rerender(<SwarmOutSettings fleet={{ hives: [{ name: 'demo', reachable: false }] } as never} />);
    expect(within(row()).getByTestId('so-hive-login')).toBeInTheDocument();
    fireEvent.click(within(row()).getByTestId('so-hive-restart'));
    expect(within(row()).getByTestId('so-hive-restart-confirm')).toBeInTheDocument();
  });

  it('Elastic Workers is its own block above the machine list; EC2 and SSH share the list', async () => {
    render(<SwarmOutSettings />);
    await verdict();
    const elastic = screen.getByTestId('so-set-elastic');
    const machines = screen.getByTestId('so-set-hives');
    // the Elastic block comes first in the page and is not inside the machine list
    expect(elastic.compareDocumentPosition(machines) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(machines.contains(elastic)).toBe(false);
    expect(within(elastic).getByTestId('so-elastic-switch')).toBeInTheDocument();
    const list = screen.getByTestId('so-hive-list');
    expect(within(list).getAllByTestId('so-hive-row')).toHaveLength(2);
    expect(within(list).getAllByTestId('so-ssh-row')).toHaveLength(1);
    // a closed row: name, kind, state; no specs, no address
    const row = within(list).getAllByTestId('so-hive-row')[0];
    expect(row).toHaveTextContent('demo');
    expect(row).toHaveTextContent('EC2');
    expect(row).not.toHaveTextContent('m7g.xlarge');
    expect(row).not.toHaveTextContent('cloudfront');
  });

  it('"+ Add a machine" asks which kind, and the SSH form opens inside the list', async () => {
    render(<SwarmOutSettings />);
    await verdict();
    expect(screen.queryByTestId('so-ssh-form')).toBeNull();
    fireEvent.click(screen.getByTestId('so-machine-add'));
    expect(screen.getByTestId('so-hive-add')).toHaveTextContent('119');       // the EC2 choice names its cost
    fireEvent.click(screen.getByTestId('so-ssh-add'));
    expect(within(screen.getByTestId('so-hive-list')).getByTestId('so-ssh-form')).toBeInTheDocument();
    expect(screen.queryByTestId('so-machine-add')).toBeNull();
  });

  it('an opened Hive shows what it changes; address, Host key and delete are under More', async () => {
    render(<SwarmOutSettings />);
    await verdict();
    const row = screen.getAllByTestId('so-hive-row')[0];
    fireEvent.click(within(row).getByTestId('so-hive-toggle'));
    const detail = within(row).getByTestId('so-hive-detail');
    expect(within(detail).getByTestId('so-hive-idle-row')).toBeInTheDocument();
    expect(within(detail).getByTestId('so-hive-login')).toBeInTheDocument();
    const more = within(detail).getByTestId('so-hive-more') as HTMLDetailsElement;
    expect(more.open).toBe(false);
    for (const id of ['so-hive-url', 'hive-access-rotate', 'so-hive-delete']) {
      expect(more.contains(within(detail).getByTestId(id))).toBe(true);
    }
    expect(within(detail).getByTestId('so-hive-url')).toHaveTextContent('https://demo.cloudfront.net');
  });
});
