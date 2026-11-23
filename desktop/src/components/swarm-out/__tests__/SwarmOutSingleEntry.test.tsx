/**
 * Swarm Out as the only place for Hives (design 2026-11-20-swarm-out-multi-carrier §6.8;
 * mock Knowledge/Mockups/2026-11-23-swarm-out-single-entry.html v3).
 *
 * Questions answered:
 * 1. the run view only reads: one row per Hive and Elastic Workers, each naming its
 *    execution surface, no button on any row, and stopped Hives never replace the view.
 * 2. without an AWS account the panel shows one card whose button opens Settings.
 * 3. the settings view: the account line, the Elastic Workers switch (and the account
 *    choice with several accounts: saved at once on standby, shown again, fixed once it runs), each Hive with login info and delete
 *    (a broken one with its reason and retry), "add one" naming
 *    the cost before anything is created, what blocks building one.
 * 4. no Hive nav card and no hive overlay remain.
 * 5. an upgrading user sees the note once.
 * 6. no locale says Elastic Hive or points to a Hive page.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import en from '../../../i18n/locales/en.json';
import zh from '../../../i18n/locales/zh.json';

const mocks = {
  runs: vi.fn(), fleet: vi.fn(), get: vi.fn(), elastic: vi.fn(), standby: vi.fn(), setFleet: vi.fn(),
  instances: vi.fn(), accounts: vi.fn(), del: vi.fn(), cfg: vi.fn(), updateCfg: vi.fn(), retry: vi.fn(),
};
vi.mock('../../../services/swarmOut', () => ({
  swarmOutService: { runs: (...a: unknown[]) => mocks.runs(...a), fleet: (...a: unknown[]) => mocks.fleet(...a), answer: vi.fn(), hives: vi.fn() },
}));
vi.mock('../../../services/awsAccount', () => ({
  awsAccountService: {
    get: (...a: unknown[]) => mocks.get(...a), elasticStatus: (...a: unknown[]) => mocks.elastic(...a),
    setElasticStandby: (...a: unknown[]) => mocks.standby(...a), setFleet: (...a: unknown[]) => mocks.setFleet(...a),
    createDefaultVpc: vi.fn(), permissionTemplate: vi.fn(), check: vi.fn(),
  },
  problemOf: (e: unknown) => ({ kind: 'error', message: String(e) }),
}));
vi.mock('../../../services/hive', async (orig) => ({
  ...(await orig<typeof import('../../../services/hive')>()),
  hiveService: {
    listInstances: (...a: unknown[]) => mocks.instances(...a), listAccounts: (...a: unknown[]) => mocks.accounts(...a),
    deleteInstance: (...a: unknown[]) => mocks.del(...a), retryInstance: (...a: unknown[]) => mocks.retry(...a), stopInstance: vi.fn(), startInstance: vi.fn(), getCredentials: vi.fn(),
  },
}));
vi.mock('../../../services/settings', () => ({
  settingsService: { getAPIConfiguration: (...a: unknown[]) => mocks.cfg(...a), updateAPIConfiguration: (...a: unknown[]) => mocks.updateCfg(...a) },
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: Record<string, unknown>) => (o ? `${k}|${Object.values(o).join(',')}` : k) }),
}));

import { SwarmOutDashboard, NOTE_SEEN_KEY } from '../SwarmOutDashboard';
import { SwarmOutSettings } from '../SwarmOutSettings';
import { OVERLAY_IDS } from '../../layout/overlayIds';
import { NAV } from '../../layout/navTable';

const fleetRow = (over: Record<string, unknown>) => ({
  name: 'demo', status: 'stopped', region: 'us-east-1', version: '2.5.5', instance_type: 'm7g.xlarge',
  reachable: null, streaming: null, saturated: null, memory_percent: null, carrier: 'ec2', ...over,
});
const FLEET = { running: 0, hives: [fleetRow({}), fleetRow({ name: 'elastic', status: 'ready', carrier: 'agentcore', instance_type: 'agentcore' })] };
const ACCOUNT = (over: Record<string, unknown> = {}) => ({
  state: 'connected', account: { account_id: '533267412361', region: 'us-east-1' },
  hive: { state: 'ready', desired: 1, counted: 1, instance_type: 'm7g.xlarge', region: 'us-east-1', prices: { 'm7g.xlarge': 119 } }, ...over,
});
const ELASTIC = (over: Record<string, unknown> = {}) => ({
  host_version: '2.5.5', can_enable: true, reason: null, standby: true, standby_last: 'ready',
  size: { vcpu_base: 2, memory_gib_base: 8, vcpu_peak: 8, memory_gib_peak: 32, disk_gib: 32 },
  hives: [{ id: 'e1', name: 'elastic', account_ref: 'swarmai', region: 'us-east-1', status: 'ready', error_message: null,
            runtime_status: 'READY', image_version: '2.5.5', up_to_date: true, open_runs: 0, working: false }], ...over,
});
const INSTANCE = { id: 'h1', name: 'demo', ownerName: null, hiveType: 'my', accountRef: 'swarmai', region: 'us-east-1',
  instanceType: 'm7g.xlarge', ec2InstanceId: 'i-1', ec2PublicIp: null, elasticIpAllocId: null, securityGroupId: null,
  iamRoleName: null, cloudfrontDistId: null, cloudfrontDomain: 'd.cloudfront.net', s3Bucket: null, authUser: 'admin',
  authPassword: null, status: 'stopped', version: '2.5.5', errorMessage: null, createdAt: 't', updatedAt: 't' };

beforeEach(() => {
  for (const m of Object.values(mocks)) m.mockReset();
  mocks.runs.mockResolvedValue({ runs: [], asks: [], errors: [] });
  mocks.fleet.mockResolvedValue(FLEET);
  mocks.get.mockResolvedValue(ACCOUNT());
  mocks.elastic.mockResolvedValue(ELASTIC());
  mocks.instances.mockResolvedValue([INSTANCE]);
  mocks.accounts.mockResolvedValue([{ id: 'swarmai', accountId: '533267412361', label: 'SwarmAI account', authMethod: 'swarmai',
    defaultRegion: 'us-east-1', createdAt: 't', verifiedAt: null, role: 'swarmai', hiveCount: 1 }]);
  mocks.cfg.mockResolvedValue({ hiveIdleStopMinutes: 120 });
  mocks.standby.mockResolvedValue({ standby: false });
  mocks.setFleet.mockResolvedValue(ACCOUNT());
  window.localStorage.setItem(NOTE_SEEN_KEY, '1');
});

describe('Swarm Out as the only place for Hives', () => {
  it('the run view only reads', async () => {
    render(<SwarmOutDashboard />);
    const rows = await screen.findAllByTestId('swarm-out-fleet-hive');
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByTestId('swarm-out-fleet-kind')).toHaveTextContent('Hive · EC2');
    expect(within(rows[1]).getByTestId('swarm-out-fleet-kind')).toHaveTextContent('AgentCore Runtime');
    expect(rows[1]).toHaveTextContent('Elastic Workers');
    // no button on any row (a row is a "contents" wrapper: its cells hold no control)
    for (const r of rows) expect(within(r).queryAllByRole('button')).toHaveLength(0);
    // every Hive stopped: the run view stays, and nothing asks the person to start one
    expect(screen.getByTestId('swarm-out-live')).toBeInTheDocument();
    expect(screen.queryByTestId('swarm-out-hive-guide')).toBeNull();
    expect(rows[0]).toHaveTextContent('swarmOut.fleet.startsWhenNeeded');
  });

  it('no account: one card to Settings', async () => {
    mocks.get.mockResolvedValue(ACCOUNT({ state: 'unconnected', account: null, hive: null }));
    const seen: unknown[] = [];
    const on = (e: Event) => seen.push((e as CustomEvent).detail);
    window.addEventListener('swarm:open-settings', on);
    render(<SwarmOutDashboard />);
    fireEvent.click(await screen.findByTestId('swarm-out-connect-account'));
    window.removeEventListener('swarm:open-settings', on);
    expect(seen).toEqual([{ tab: 'aws-account' }]);
    expect(screen.queryByTestId('swarm-out-live')).toBeNull();
  });

  it('the settings view', async () => {
    render(<SwarmOutSettings />);
    expect(await screen.findByTestId('so-set-account')).toHaveTextContent('533267412361');
    expect(screen.getByTestId('so-set-elastic-kind')).toHaveTextContent('AgentCore Runtime');
    expect(screen.getByTestId('so-set-hives-kind')).toHaveTextContent('EC2');
    // the Elastic Workers switch is the person's route
    const sw = await screen.findByTestId('so-elastic-switch');
    await waitFor(() => expect(sw).toBeChecked());
    fireEvent.click(sw);
    await waitFor(() => expect(mocks.standby).toHaveBeenCalledWith(false, undefined));
    // each Hive: login info and delete (the delete asks first)
    const row = await screen.findByTestId('so-hive-row');
    expect(within(row).getByTestId('so-hive-login')).toBeInTheDocument();
    fireEvent.click(within(row).getByTestId('so-hive-delete'));
    mocks.del.mockResolvedValue(undefined);
    fireEvent.click(await screen.findByTestId('so-hive-delete-go'));
    await waitFor(() => expect(mocks.del).toHaveBeenCalledWith('h1'));
    // add one: the cost is named, nothing is created before the confirm
    fireEvent.click(screen.getByTestId('so-hive-add'));
    expect(mocks.setFleet).not.toHaveBeenCalled();
    expect(screen.getByTestId('so-hive-add-go')).toHaveTextContent('119');
    fireEvent.click(screen.getByTestId('so-hive-add-go'));
    await waitFor(() => expect(mocks.setFleet).toHaveBeenCalledWith({ desired_count: 2 }));
    // the idle stop time is the person's setting
    fireEvent.change(screen.getByTestId('so-idle'), { target: { value: '60' } });
    await waitFor(() => expect(mocks.updateCfg).toHaveBeenCalledWith({ hiveIdleStopMinutes: 60 }));
  });

  it('the settings view: several accounts, the person chooses the one for Elastic Workers', async () => {
    mocks.get.mockResolvedValue(ACCOUNT({ hive: { state: 'unavailable', desired: null, counted: 0 } }));
    mocks.elastic.mockResolvedValue(ELASTIC({ standby: false, hives: [] }));
    const own = (id: string) => ({ id, accountId: id, label: id, authMethod: 'access_keys', defaultRegion: 'us-east-1',
      createdAt: 't', verifiedAt: 't', role: 'own', hiveCount: 0 });
    mocks.accounts.mockResolvedValue([own('a1'), own('a2')]);
    mocks.standby.mockResolvedValue({ standby: true });
    render(<SwarmOutSettings />);
    const sw = await screen.findByTestId('so-elastic-switch');
    await waitFor(() => expect(sw).toBeDisabled());        // nothing chosen yet
    fireEvent.change(screen.getByTestId('so-elastic-account'), { target: { value: 'a2' } });
    fireEvent.click(sw);
    await waitFor(() => expect(mocks.standby).toHaveBeenCalledWith(true, 'a2'));
  });

  it('the settings view: several accounts on standby, the choice is saved at once and shown again', async () => {
    mocks.get.mockResolvedValue(ACCOUNT({ hive: { state: 'unavailable', desired: null, counted: 0 } }));
    mocks.elastic.mockResolvedValue(ELASTIC({ standby: true, hives: [], chosen_account: 'a1' }));
    const own = (id: string) => ({ id, accountId: id, label: id, authMethod: 'access_keys', defaultRegion: 'us-east-1',
      createdAt: 't', verifiedAt: 't', role: 'own', hiveCount: 0 });
    mocks.accounts.mockResolvedValue([own('a1'), own('a2')]);
    mocks.standby.mockResolvedValue({ standby: true });
    render(<SwarmOutSettings />);
    const select = await screen.findByTestId('so-elastic-account');
    await waitFor(() => expect(select).toHaveValue('a1'));
    fireEvent.change(select, { target: { value: 'a2' } });
    await waitFor(() => expect(mocks.standby).toHaveBeenCalledWith(true, 'a2'));
  });

  it('the settings view: once Elastic Workers runs, its account is shown, not chosen', async () => {
    mocks.get.mockResolvedValue(ACCOUNT({ hive: { state: 'unavailable', desired: null, counted: 0 } }));
    mocks.elastic.mockResolvedValue(ELASTIC({ hives: [{ ...ELASTIC().hives[0], account_ref: 'a2' }] }));
    const own = (id: string) => ({ id, accountId: id, label: `label-${id}`, authMethod: 'access_keys', defaultRegion: 'us-east-1',
      createdAt: 't', verifiedAt: 't', role: 'own', hiveCount: 0 });
    mocks.accounts.mockResolvedValue([own('a1'), own('a2')]);
    render(<SwarmOutSettings />);
    expect(await screen.findByTestId('so-elastic-account-fixed')).toHaveTextContent('label-a2');
    expect(screen.queryByTestId('so-elastic-account')).toBeNull();
  });

  it('the settings view: a broken Hive says why and can be retried after a confirm', async () => {
    mocks.instances.mockResolvedValue([{ ...INSTANCE, status: 'error', errorMessage: 'Failed to become healthy after start' }]);
    mocks.retry.mockResolvedValue(undefined);
    render(<SwarmOutSettings />);
    const row = await screen.findByTestId('so-hive-row');
    expect(within(row).getByTestId('so-hive-error')).toHaveTextContent('Failed to become healthy after start');
    fireEvent.click(within(row).getByTestId('so-hive-retry'));
    expect(mocks.retry).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByTestId('so-hive-retry-go'));
    await waitFor(() => expect(mocks.retry).toHaveBeenCalledWith('h1', true));
  });

  it('the settings view: what blocks building a Hive is shown', async () => {
    mocks.get.mockResolvedValue(ACCOUNT({ hive: { state: 'cannot_build', desired: 1, counted: 0, short: 1, prices: { 'm7g.xlarge': 119 },
      capability: { status: 'missing', no_default_vpc: true, region: 'eu-west-1', detail: 'no default VPC in eu-west-1' } } }));
    render(<SwarmOutSettings />);
    expect(await screen.findByTestId('so-hive-cannot')).toBeInTheDocument();
  });

  it('no hive nav card or overlay', () => {
    expect((OVERLAY_IDS as readonly string[]).includes('hive')).toBe(false);
    expect(Object.keys(NAV)).not.toContain('hive');
  });

  it('the upgrade note shows once', async () => {
    window.localStorage.removeItem(NOTE_SEEN_KEY);
    const first = render(<SwarmOutDashboard />);
    fireEvent.click(await screen.findByTestId('swarm-out-single-entry-note-ok'));
    expect(window.localStorage.getItem(NOTE_SEEN_KEY)).toBe('1');
    expect(screen.queryByTestId('swarm-out-single-entry-note')).toBeNull();
    first.unmount();
    render(<SwarmOutDashboard />);
    await screen.findAllByTestId('swarm-out-fleet-hive');
    expect(screen.queryByTestId('swarm-out-single-entry-note')).toBeNull();
  });

  it('the upgrade note is not shown to a new user with nothing that moved', async () => {
    window.localStorage.removeItem(NOTE_SEEN_KEY);
    mocks.fleet.mockResolvedValue({ running: 0, hives: [] });
    render(<SwarmOutDashboard />);
    await screen.findByTestId('swarm-out-live');
    expect(screen.queryByTestId('swarm-out-single-entry-note')).toBeNull();
  });

  it('no locale says elastic hive', () => {
    const old = /elastic hive|弹性 ?hive|hive page|hive 页/i;
    const found: string[] = [];
    const walk = (o: unknown, path: string) => {
      if (typeof o === 'string') { if (old.test(o)) found.push(`${path}: ${o}`); return; }
      if (o && typeof o === 'object') for (const [k, v] of Object.entries(o)) walk(v, `${path}.${k}`);
    };
    walk(en, 'en'); walk(zh, 'zh');
    expect(found).toEqual([]);
  });
});
