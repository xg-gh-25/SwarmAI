/**
 * Swarm Out as the only place for Hives (design 2026-11-20-swarm-out-multi-carrier §6.8;
 * mock Knowledge/Mockups/2026-11-23-swarm-out-single-entry.html v3).
 *
 * Questions answered:
 * 1. the header names every place a task can run (one pill per Hive and Elastic Workers,
 *    each EC2 or AgentCore), and stopped Hives never replace the run view.
 * 2. without an AWS account the panel shows one card that connects the account right
 *    there: nothing opens the App's Settings.
 * 3. the settings view: the account with its Account ID and Region, the Elastic Workers
 *    switch (and the account choice with several accounts: saved at once on standby, shown
 *    again, fixed once it runs), each Hive with login info and delete (a broken one open by
 *    itself, with its reason and retry), "add one" naming the cost before anything is
 *    created, what blocks building one.
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
const desktop = { on: false };
vi.mock('../../../services/tauri', () => ({ isDesktop: () => desktop.on }));
vi.mock('../../awsAccount/ConnectAccount', () => ({
  default: ({ onConnected }: { onConnected: (v: unknown) => void }) => (
    <button type="button" data-testid="connect-account-flow" onClick={() => onConnected({ state: 'connected', account: { account_id: '111122223333' } })} />),
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
  desktop.on = false;
});

describe('Swarm Out as the only place for Hives', () => {
  it('the header names every place a task can run', async () => {
    render(<SwarmOutDashboard />);
    const pills = await screen.findAllByTestId('so-fleet-pill');
    expect(pills.map((p) => p.getAttribute('data-carrier'))).toEqual(['ec2', 'agentcore']);
    expect(pills[1]).toHaveTextContent('Elastic Workers');
    // every Hive stopped: the run view stays, and nothing asks the person to start one
    expect(screen.getByTestId('so-run-board')).toBeInTheDocument();
    expect(screen.queryByTestId('swarm-out-hive-guide')).toBeNull();
    expect(pills[0].getAttribute('title')).toContain('swarmOut.fleet.startsWhenNeeded');
  });

  it('no account: one card that connects it right there, never the App\'s Settings', async () => {
    mocks.get.mockResolvedValue(ACCOUNT({ state: 'unconnected', account: null, hive: null }));
    const seen: unknown[] = [];
    const on = (e: Event) => seen.push((e as CustomEvent).detail);
    window.addEventListener('swarm:open-settings', on);
    render(<SwarmOutDashboard />);
    fireEvent.click(await screen.findByTestId('swarm-out-connect-account'));
    window.removeEventListener('swarm:open-settings', on);
    expect(seen).toEqual([]);
    expect(screen.getByTestId('connect-account-flow')).toBeInTheDocument();
    expect(screen.queryByTestId('so-run-board')).toBeNull();
  });

  it('the settings view', async () => {
    const seen: unknown[] = [];
    const on = (e: Event) => seen.push((e as CustomEvent).detail);
    window.addEventListener('swarm:open-settings', on);
    render(<SwarmOutSettings />);
    expect(await screen.findByTestId('so-account-id-value')).toHaveTextContent('5332 6741 2361');
    expect(screen.getByTestId('so-account-region')).toHaveTextContent('us-east-1');
    expect(screen.getByTestId('so-set-elastic')).toHaveTextContent('swarmOut.board.elastic');
    expect(screen.getByTestId('so-set-hives')).toHaveTextContent('swarmOut.board.ec2');
    // changing the account opens the sign-in flow here, not the App's Settings
    fireEvent.click(screen.getByTestId('so-account-change'));
    expect(screen.getByTestId('connect-account-flow')).toBeInTheDocument();
    window.removeEventListener('swarm:open-settings', on);
    expect(seen).toEqual([]);
    // the Elastic Workers switch is the person's route
    const sw = await screen.findByTestId('so-elastic-switch');
    await waitFor(() => expect(sw).toBeChecked());
    fireEvent.click(sw);
    await waitFor(() => expect(mocks.standby).toHaveBeenCalledWith(false, undefined));
    // each Hive: login info and delete (the delete asks first)
    const row = await screen.findByTestId('so-hive-row');
    expect(within(row).queryByTestId('so-hive-login')).toBeNull();     // closed until opened
    fireEvent.click(within(row).getByTestId('so-hive-toggle'));
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
    // open by itself: why it is broken is the first thing to read
    expect(within(row).getByTestId('so-hive-error')).toHaveTextContent('Failed to become healthy after start');
    fireEvent.click(within(row).getByTestId('so-hive-retry'));
    expect(mocks.retry).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByTestId('so-hive-retry-go'));
    await waitFor(() => expect(mocks.retry).toHaveBeenCalledWith('h1', true));
  });

  it('the settings view: Hives short of what was asked: build them or later; "add one" never asks for fewer', async () => {
    mocks.get.mockResolvedValue(ACCOUNT({ hive: { state: 'short', desired: 3, counted: 1, short: 2, instance_type: 'm7g.xlarge',
      region: 'us-east-1', prices: { 'm7g.xlarge': 119 }, dismissed: false } }));
    render(<SwarmOutSettings />);
    const line = await screen.findByTestId('so-hive-short');
    expect(screen.queryByTestId('so-hive-add')).toBeNull();          // the shortage is the one way to add here
    fireEvent.click(within(line).getByTestId('so-hive-short-build'));
    await waitFor(() => expect(mocks.setFleet).toHaveBeenCalledWith({ desired_count: 3 }));
    fireEvent.click(await screen.findByTestId('so-hive-short-later'));
    await waitFor(() => expect(mocks.setFleet).toHaveBeenCalledWith({ dismissed: true }));
  });

  it('the settings view: "add one" asks for one more than both what is there and what was asked', async () => {
    // three asked for, one built (this account could not build the rest): one more is four, never two
    mocks.get.mockResolvedValue(ACCOUNT({ hive: { state: 'cannot_build', desired: 3, counted: 1, short: 2, instance_type: 'm7g.xlarge',
      region: 'us-east-1', prices: { 'm7g.xlarge': 119 }, capability: { status: 'missing', detail: 'x' } } }));
    render(<SwarmOutSettings />);
    fireEvent.click(await screen.findByTestId('so-hive-add'));
    fireEvent.click(screen.getByTestId('so-hive-add-go'));
    await waitFor(() => expect(mocks.setFleet).toHaveBeenCalledWith({ desired_count: 4 }));
  });

  it('the settings view: no "none" before the Hives are read, and an unreadable list says so', async () => {
    let fail: (e: Error) => void = () => {};
    mocks.instances.mockReturnValue(new Promise((_, rej) => { fail = rej; }));
    render(<SwarmOutSettings />);
    expect((await screen.findByTestId('so-hives-none')).textContent).toBe('swarmOut.loading');
    fail(new Error('down'));
    await waitFor(() => expect(screen.getByTestId('so-hives-none').textContent).toBe('swarmOut.setup.hivesUnreadable'));
  });

  it('the settings view: an account connected here reaches the panel', async () => {
    mocks.get.mockResolvedValue(ACCOUNT({ state: 'unconnected', account: null, hive: null }));
    const onConnected = vi.fn();
    render(<SwarmOutSettings onConnected={onConnected} />);
    fireEvent.click(await screen.findByTestId('so-account-change'));
    fireEvent.click(screen.getByTestId('connect-account-flow'));
    expect(onConnected).toHaveBeenCalledWith(expect.objectContaining({ state: 'connected' }));
  });

  it('the settings view: what blocks building a Hive is shown', async () => {
    mocks.get.mockResolvedValue(ACCOUNT({ hive: { state: 'cannot_build', desired: 1, counted: 0, short: 1, prices: { 'm7g.xlarge': 119 },
      capability: { status: 'missing', no_default_vpc: true, region: 'eu-west-1', detail: 'no default VPC in eu-west-1' } } }));
    render(<SwarmOutSettings />);
    expect(await screen.findByTestId('so-hive-cannot')).toBeInTheDocument();
  });

  it('Hives in other accounts are here (moved from the App\'s account page): only accounts that still hold Hives', async () => {
    desktop.on = true;
    const acc = (id: string, role: string, hiveCount: number) => ({ id, accountId: id, label: `label-${id}`, authMethod: 'access_keys',
      defaultRegion: 'us-east-1', createdAt: 't', verifiedAt: 't', role, hiveCount });
    mocks.accounts.mockResolvedValue([acc('swarmai', 'swarmai', 2), acc('old', 'other', 1), acc('empty', 'other', 0)]);
    render(<SwarmOutSettings />);
    const box = await screen.findByTestId('so-set-other-accounts');
    expect(box).toHaveTextContent('label-old');
    expect(box).not.toHaveTextContent('label-swarmai');
    expect(box).not.toHaveTextContent('label-empty');
    expect(box).toHaveTextContent('settings.awsAccount.otherHives');
    expect(within(box).queryByText('settings.awsAccount.addHiveAccount')).toBeNull();
  });

  it('without a SwarmAI account, the legacy Hive accounts are here too, with Add', async () => {
    desktop.on = true;
    mocks.get.mockResolvedValue(ACCOUNT({ state: 'unconnected', account: null, hive: null }));
    mocks.accounts.mockResolvedValue([{ id: 'mine', accountId: 'mine', label: 'label-mine', authMethod: 'access_keys',
      defaultRegion: 'us-east-1', createdAt: 't', verifiedAt: 't', role: 'own', hiveCount: 0 }]);
    render(<SwarmOutSettings />);
    const box = await screen.findByTestId('so-set-other-accounts');
    expect(box).toHaveTextContent('label-mine');
    expect(box).toHaveTextContent('settings.awsAccount.hiveAccounts');
    fireEvent.click(within(box).getByText('settings.awsAccount.addHiveAccount'));
    expect(screen.getByRole('dialog')).toHaveTextContent('ui.hiveComponents.addAccountTitle');
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
    await screen.findAllByTestId('so-fleet-pill');
    expect(screen.queryByTestId('swarm-out-single-entry-note')).toBeNull();
  });

  it('the upgrade note is not shown to a new user with nothing that moved', async () => {
    window.localStorage.removeItem(NOTE_SEEN_KEY);
    mocks.fleet.mockResolvedValue({ running: 0, hives: [] });
    render(<SwarmOutDashboard />);
    await screen.findByTestId('so-run-board');
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
