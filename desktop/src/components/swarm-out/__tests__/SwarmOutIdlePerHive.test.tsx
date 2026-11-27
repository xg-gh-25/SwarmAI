/**
 * Each Resident Hive's own idle stop on the Swarm Out settings view (SwarmOutSettings,
 * services/hive setIdleStop, ./idlePolicy).
 *
 * Questions answered:
 * 1. does a collapsed row say its policy in a few words next to its status (Always on,
 *    Stops after 2 h idle, Stops after 30 min idle), in English and in Chinese?
 * 2. does an opened row whose machine the Host stops when idle offer "Stop when idle for"
 *    with Always on first, then the default selector's minute choices, then
 *    "Default (N minutes)", and does each choice write 0, the minutes or null for that Hive?
 * 3. does a row the Host never stops (idleStopSettable false) offer no selector?
 * 4. does the default selector say it is the default for new Hives and Hives without their
 *    own setting, and still write hiveIdleStopMinutes?
 */
import { describe, it, expect, vi, beforeEach, afterAll } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import i18n from '../../../i18n';
import { idlePolicyLabel } from '../idlePolicy';

const mocks = {
  get: vi.fn(), elastic: vi.fn(), instances: vi.fn(), accounts: vi.fn(), cfg: vi.fn(), updateCfg: vi.fn(), setIdle: vi.fn(),
};
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
    setIdleStop: (...a: unknown[]) => mocks.setIdle(...a),
    deleteInstance: vi.fn(), retryInstance: vi.fn(), stopInstance: vi.fn(), startInstance: vi.fn(), listAccess: vi.fn(async () => []), addAccess: vi.fn(), revokeAccess: vi.fn(), rotateHostKey: vi.fn(),
    brainStatus: vi.fn(() => new Promise(() => {})),
  },
}));
vi.mock('../../../services/settings', () => ({
  settingsService: { getAPIConfiguration: (...a: unknown[]) => mocks.cfg(...a), updateAPIConfiguration: (...a: unknown[]) => mocks.updateCfg(...a) },
}));
vi.mock('../../../services/tauri', () => ({ isDesktop: () => false }));
vi.mock('../../awsAccount/ConnectAccount', () => ({ default: () => null }));

import { SwarmOutSettings, forgetSettingsForTests } from '../SwarmOutSettings';

const hive = (id: string, over: Record<string, unknown>) => ({
  id, name: id, ownerName: null, hiveType: 'my', accountRef: 'swarmai', region: 'us-east-1', instanceType: 'm7g.xlarge',
  ec2InstanceId: 'i-1', ec2PublicIp: null, elasticIpAllocId: null, securityGroupId: null, iamRoleName: null, cloudfrontDistId: null,
  cloudfrontDomain: 'd.cloudfront.net', s3Bucket: null, authUser: 'admin', status: 'stopped', version: '2.5.5',
  errorMessage: null, createdAt: 't', updatedAt: 't', idleStopMinutes: null, idleStopEffective: 120, idleStopSettable: true, ...over,
});
const HIVES = [
  hive('remote-demo', { idleStopMinutes: 0, idleStopEffective: 0 }),           // set always on
  hive('remote-explorer', {}),                                                 // follows the default
  hive('quick', { idleStopMinutes: 30, idleStopEffective: 30 }),               // its own 30 min
  hive('desk', { idleStopSettable: false, idleStopEffective: 0 }),             // the Host never stops it
];

const rowOf = async (name: string) => {
  const rows = await screen.findAllByTestId('so-hive-row');
  const row = rows.find((r) => within(r).queryByText(name));
  if (!row) throw new Error(`no row ${name}`);
  return row;
};

beforeEach(async () => {
  forgetSettingsForTests();                 // each case starts from an empty tab
  for (const m of Object.values(mocks)) m.mockReset();
  mocks.get.mockResolvedValue({ state: 'connected', account: { account_id: '533267412361', region: 'us-east-1' },
    hive: { state: 'ready', desired: 4, counted: 4, instance_type: 'm7g.xlarge', region: 'us-east-1', prices: {} } });
  mocks.elastic.mockResolvedValue({ host_version: '2.5.5', can_enable: true, reason: null, standby: false, standby_last: null, size: null, hives: [] });
  mocks.instances.mockResolvedValue(HIVES);
  mocks.accounts.mockResolvedValue([]);
  mocks.cfg.mockResolvedValue({ hiveIdleStopMinutes: 120 });
  mocks.setIdle.mockResolvedValue(undefined);
  mocks.updateCfg.mockResolvedValue(undefined);
  await i18n.changeLanguage('en');
});
afterAll(async () => { await i18n.changeLanguage('en'); });

describe('each Resident Hive has its own idle stop', () => {
  it('a collapsed row says its policy next to its status', async () => {
    render(<SwarmOutSettings />);
    const policy = async (name: string) => within(await rowOf(name)).getByTestId('so-hive-idle-policy').textContent;
    expect(await policy('remote-demo')).toBe('· Always on');
    expect(await policy('remote-explorer')).toBe('· Stops after 2 h idle');
    expect(await policy('quick')).toBe('· Stops after 30 min idle');
    expect(await policy('desk')).toBe('· Always on');
  });

  it('the selector offers always on, the minutes and the default, and writes the Hive\'s own value', async () => {
    render(<SwarmOutSettings />);
    const row = await rowOf('remote-explorer');
    expect(within(row).queryByTestId('so-hive-idle')).toBeNull();              // closed until opened
    fireEvent.click(within(row).getByTestId('so-hive-toggle'));
    const sel = within(row).getByTestId('so-hive-idle') as HTMLSelectElement;
    // "Keep running", its choices in the words the closed rows use (never "stop when idle for: always on")
    expect(within(row).getByTestId('so-hive-idle-row')).toHaveTextContent('Keep running');
    expect(Array.from(sel.options).map((o) => [o.value, o.textContent])).toEqual([
      ['0', 'Always on'], ['30', 'Stops after 30 min idle'], ['60', 'Stops after 1 h idle'], ['120', 'Stops after 2 h idle'],
      ['240', 'Stops after 4 h idle'], ['480', 'Stops after 8 h idle'], ['default', 'Default (Stops after 2 h idle)']]);
    expect(sel.value).toBe('default');
    fireEvent.change(sel, { target: { value: '0' } });
    await waitFor(() => expect(mocks.setIdle).toHaveBeenCalledWith('remote-explorer', 0));
    fireEvent.change(sel, { target: { value: '240' } });
    await waitFor(() => expect(mocks.setIdle).toHaveBeenCalledWith('remote-explorer', 240));
    fireEvent.change(sel, { target: { value: 'default' } });
    await waitFor(() => expect(mocks.setIdle).toHaveBeenLastCalledWith('remote-explorer', null));
    await waitFor(() => expect(mocks.instances.mock.calls.length).toBeGreaterThanOrEqual(4));  // reloaded after each
  });

  it('a Hive set always on shows it chosen; an own value outside the choices stays shown', async () => {
    mocks.instances.mockResolvedValue([hive('remote-demo', { idleStopMinutes: 0, idleStopEffective: 0 }),
      hive('odd', { idleStopMinutes: 45, idleStopEffective: 45 })]);
    render(<SwarmOutSettings />);
    const demo = await rowOf('remote-demo');
    fireEvent.click(within(demo).getByTestId('so-hive-toggle'));
    expect((within(demo).getByTestId('so-hive-idle') as HTMLSelectElement).value).toBe('0');
    const odd = await rowOf('odd');
    expect(within(odd).getByTestId('so-hive-idle-policy')).toHaveTextContent('Stops after 45 min idle');
    fireEvent.click(within(odd).getByTestId('so-hive-toggle'));
    expect((within(odd).getByTestId('so-hive-idle') as HTMLSelectElement).value).toBe('45');
  });

  it('a machine the Host never stops offers no selector', async () => {
    render(<SwarmOutSettings />);
    const row = await rowOf('desk');
    fireEvent.click(within(row).getByTestId('so-hive-toggle'));
    expect(within(row).getByTestId('so-hive-detail')).toBeInTheDocument();
    expect(within(row).queryByTestId('so-hive-idle')).toBeNull();
  });

  it('the default selector says it is the default and still writes the app setting', async () => {
    render(<SwarmOutSettings />);
    const rules = await screen.findByTestId('so-set-rules');
    fireEvent.click(within(rules).getByTestId('so-defaults-edit'));
    expect(rules).toHaveTextContent('Default: stop when idle for');
    expect(rules).toHaveTextContent('for new Hives and Hives without their own setting');
    const sel = within(rules).getByTestId('so-idle');
    await waitFor(() => expect(sel).not.toBeDisabled());
    fireEvent.change(sel, { target: { value: '60' } });
    await waitFor(() => expect(mocks.updateCfg).toHaveBeenCalledWith({ hiveIdleStopMinutes: 60 }));
    expect(mocks.setIdle).not.toHaveBeenCalled();
  });

  it('in Chinese', async () => {
    await i18n.changeLanguage('zh');
    render(<SwarmOutSettings />);
    expect(within(await rowOf('remote-demo')).getByTestId('so-hive-idle-policy')).toHaveTextContent('常开');
    expect(within(await rowOf('remote-explorer')).getByTestId('so-hive-idle-policy')).toHaveTextContent('闲置 2 小时后停');
    expect(within(await rowOf('quick')).getByTestId('so-hive-idle-policy')).toHaveTextContent('闲置 30 分钟后停');
    const row = await rowOf('remote-explorer');
    fireEvent.click(within(row).getByTestId('so-hive-toggle'));
    const sel = within(row).getByTestId('so-hive-idle') as HTMLSelectElement;
    expect(sel.options[0].textContent).toBe('常开');
    expect(sel.options[sel.options.length - 1].textContent).toBe('默认（闲置 2 小时后停）');
    fireEvent.click(within(await screen.findByTestId('so-set-rules')).getByTestId('so-defaults-edit'));
    expect(screen.getByTestId('so-set-rules')).toHaveTextContent('默认：闲置多久自动停');
  });

  it('the policy words for every kind of value', () => {
    const t = i18n.getFixedT('en');
    expect(idlePolicyLabel(t, 0)).toBe('Always on');
    expect(idlePolicyLabel(t, 480)).toBe('Stops after 8 h idle');
    expect(idlePolicyLabel(t, 90)).toBe('Stops after 90 min idle');
    expect(idlePolicyLabel(t, 2.5)).toBe('Stops after 3 min idle');
    expect(idlePolicyLabel(t, undefined)).toBeNull();
    expect(idlePolicyLabel(t, null)).toBeNull();
  });
});
