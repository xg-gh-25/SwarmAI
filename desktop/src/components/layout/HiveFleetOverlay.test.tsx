import type React from 'react';
/**
 * Tests for HiveFleetOverlay — the Hive Fleet workbench (run_b450108e).
 *
 * Covers: fetch on mount; Fleet overview counts; My/Shared sections render instances;
 * Fleet⇄Accounts toggle; Deploy button gated on account presence; Accounts view lists
 * accounts. Mocks hiveService at the boundary (no real API).
 */
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';

const listInstances = vi.fn();
const listAccounts = vi.fn();
const stopInstance = vi.fn();
const updateInstance = vi.fn();
const latestVersion = vi.fn();
const retryInstance = vi.fn();
const startInstance = vi.fn();
const deleteInstance = vi.fn();
vi.mock('../../services/hive', async () => {
  const actual = await vi.importActual<typeof import('../../services/hive')>('../../services/hive');
  return {
    ...actual,
    hiveService: {
      listInstances: () => listInstances(),
      listAccounts: () => listAccounts(),
      stopInstance: (id: string) => stopInstance(id),
      updateInstance: (id: string, v: string) => updateInstance(id, v),
      latestVersion: () => latestVersion(),
      retryInstance: (id: string, c?: boolean) => retryInstance(id, c),
      startInstance: (id: string) => startInstance(id),
      deleteInstance: (id: string) => deleteInstance(id),
      // the Brains line of a running card (HiveBrains): covered by its own tests
      brainStatus: () => Promise.resolve({ hive: 'x', supported: true, brains: [] }),
      syncBrain: () => Promise.resolve(),
      syncDefaultBrains: () => Promise.resolve(),
    },
  };
});

import { HiveFleetContent } from './HiveFleetOverlay';
import { resetLatestHiveVersion } from '../settings/hiveComponents';
import { isNewerVersion } from '../../services/hive';
import type { HiveInstance, HiveAccount } from '../../services/hive';
import { QueryClient as __QC, QueryClientProvider as __QCP } from '@tanstack/react-query';
import { render as __render } from '@testing-library/react';
// The overlay reads its data through TanStack Query (overlayQueries): a fresh client
// per render, so no cache carries over between tests.
const renderQ = (ui: React.ReactElement) =>
  __render(<__QCP client={new __QC({ defaultOptions: { queries: { retry: false } } })}>{ui}</__QCP>);

const mkInst = (o: Partial<HiveInstance>): HiveInstance => ({
  id: 'i1', name: 'xg-hive', ownerName: null, hiveType: 'my', accountRef: 'a1',
  region: 'us-east-1', instanceType: 'm7g.xlarge', ec2InstanceId: null, ec2PublicIp: null,
  elasticIpAllocId: null, securityGroupId: null, iamRoleName: null, cloudfrontDistId: null,
  cloudfrontDomain: null, s3Bucket: null, authUser: null, authPassword: null,
  status: 'running', version: null, errorMessage: null, createdAt: '2026-08-01T00:00:00Z',
  updatedAt: '2026-08-01T00:00:00Z', ...o,
});

const ACCOUNT: HiveAccount = {
  id: 'a1', accountId: '123456789012', label: 'personal', authMethod: 'access_keys',
  defaultRegion: 'us-east-1', createdAt: '2026-07-01T00:00:00Z', verifiedAt: '2026-07-01T00:00:00Z',
};

afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => {
  listAccounts.mockResolvedValue([ACCOUNT]);
  listInstances.mockResolvedValue([
    mkInst({ id: 'i1', name: 'xg-hive', status: 'running', hiveType: 'my' }),
    mkInst({ id: 'i2', name: 'teammate-hive', status: 'stopped', hiveType: 'shared', ownerName: 'Teammate' }),
  ]);
  stopInstance.mockResolvedValue(undefined);
  updateInstance.mockResolvedValue(undefined);
  latestVersion.mockResolvedValue('2.3.0');
  resetLatestHiveVersion();
});

describe('HiveFleetOverlay', () => {
  it('a failed instances load is an error with Retry, not an empty fleet (OverlayState)', async () => {
    listInstances.mockReset().mockRejectedValueOnce(new Error('down')).mockResolvedValue([mkInst({ id: 'i1', name: 'xg-hive' })]);
    renderQ(<HiveFleetContent close={() => {}} />);
    fireEvent.click(await screen.findByTestId('hive-load-retry'));
    expect(await screen.findByText('xg-hive')).toBeInTheDocument();
    expect(listInstances).toHaveBeenCalledTimes(2);
  });

  it('a failed accounts load shows the error in the Accounts view, not "No AWS accounts yet"', async () => {
    listAccounts.mockReset().mockRejectedValue(new Error('down'));
    renderQ(<HiveFleetContent close={() => {}} />);
    fireEvent.click(await screen.findByRole('tab', { name: /Accounts/i }));
    expect(await screen.findByTestId('hive-load-error')).toBeInTheDocument();
    expect(screen.queryByTestId('hive-accounts-empty')).toBeNull();
  });

  it('fetches on mount and renders the fleet overview + instances', async () => {
    renderQ(<HiveFleetContent close={() => {}} />);
    await waitFor(() => expect(screen.getByTestId('hive-overview')).toBeInTheDocument());
    expect(listInstances).toHaveBeenCalled();
    expect(listAccounts).toHaveBeenCalled();
    // both instances render (My + Shared sections). They come from their own fetch,
    // which can land after the overview: wait for them, do not read them at once.
    expect(await screen.findByText('xg-hive')).toBeInTheDocument();
    expect(await screen.findByText('teammate-hive')).toBeInTheDocument();
  });

  it('overview reflects real counts (1 running)', async () => {
    renderQ(<HiveFleetContent close={() => {}} />);
    await waitFor(() => expect(screen.getByTestId('hive-overview')).toBeInTheDocument());
    const txt = screen.getByTestId('hive-overview').textContent || '';
    expect(txt).toContain('running');
  });

  it('toggles Fleet ⇄ Accounts and lists AWS accounts', async () => {
    renderQ(<HiveFleetContent close={() => {}} />);
    await waitFor(() => expect(screen.getByTestId('hive-view-accounts')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('hive-view-accounts'));
    await waitFor(() => expect(screen.getByTestId('hive-accounts-view')).toBeInTheDocument());
    expect(screen.getByText('123456789012')).toBeInTheDocument();
  });

  it('Deploy button is enabled when an account exists', async () => {
    renderQ(<HiveFleetContent close={() => {}} />);
    // enabled once the accounts arrive
    await waitFor(() => expect(screen.getByTestId('hive-deploy-btn')).not.toBeDisabled());
  });

  it('Deploy button is disabled when there are no accounts', async () => {
    listAccounts.mockResolvedValue([]);
    renderQ(<HiveFleetContent close={() => {}} />);
    await waitFor(() => expect(screen.getByTestId('hive-deploy-btn')).toBeInTheDocument());
    expect(screen.getByTestId('hive-deploy-btn')).toBeDisabled();
    // disabled state is discoverable (UX1): a title explains WHY
    expect(screen.getByTestId('hive-deploy-btn').getAttribute('title')).toContain('AWS account');
  });

  // ── failure/empty paths (REVIEW UX/Test gaps) ──
  it('renders the empty state when there are 0 instances', async () => {
    listInstances.mockResolvedValue([]);
    listAccounts.mockResolvedValue([ACCOUNT]);
    renderQ(<HiveFleetContent close={() => {}} />);
    await waitFor(() => expect(screen.getByText(/No Hives deployed yet/)).toBeInTheDocument());
    expect(screen.getByTestId('hive-overview').textContent).toContain('0');
  });

  it('degrades gracefully when listInstances rejects (accounts still render — allSettled independence)', async () => {
    listInstances.mockRejectedValueOnce(new Error('Network error'));
    listAccounts.mockResolvedValue([ACCOUNT]);
    renderQ(<HiveFleetContent close={() => {}} />);
    // does not crash: overview renders (empty), and the accounts leg still fulfilled
    await waitFor(() => expect(screen.getByTestId('hive-overview')).toBeInTheDocument());
    expect(screen.queryByText('xg-hive')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('hive-view-accounts'));
    await waitFor(() => expect(screen.getByText('123456789012')).toBeInTheDocument());
  });

  it('stops a running instance via the real InstanceCard action → calls hiveService.stopInstance', async () => {
    stopInstance.mockResolvedValue(undefined);
    renderQ(<HiveFleetContent close={() => {}} />);
    await waitFor(() => expect(screen.getByText('xg-hive')).toBeInTheDocument());
    // xg-hive is the running 'my' instance → its card offers Stop
    fireEvent.click(screen.getAllByRole('button', { name: /^Stop$/ })[0]);
    await waitFor(() => expect(stopInstance).toHaveBeenCalledWith('i1'));
  });

  it('view toggle carries aria-selected (accessible tabs)', async () => {
    renderQ(<HiveFleetContent close={() => {}} />);
    await waitFor(() => expect(screen.getByTestId('hive-view-fleet')).toBeInTheDocument());
    expect(screen.getByTestId('hive-view-fleet').getAttribute('aria-selected')).toBe('true');
    fireEvent.click(screen.getByTestId('hive-view-accounts'));
    await waitFor(() => expect(screen.getByTestId('hive-view-accounts').getAttribute('aria-selected')).toBe('true'));
    expect(screen.getByTestId('hive-view-fleet').getAttribute('aria-selected')).toBe('false');
  });
});

describe('Hive update to the latest release', () => {
  it('isNewerVersion compares major.minor.patch and ignores a local suffix', () => {
    expect(isNewerVersion('2.4.0', '2.3.0-hive1')).toBe(true);
    expect(isNewerVersion('2.3.0', '2.3.0-hive1')).toBe(false);
    expect(isNewerVersion('2.10.0', '2.9.9')).toBe(true);
    expect(isNewerVersion('2.3.0', '2.4.0')).toBe(false);
    expect(isNewerVersion(null, '2.3.0')).toBe(false);
    expect(isNewerVersion('2.4.0', null)).toBe(false);
  });

  it('a running Hive behind the latest release offers Update, which asks first', async () => {
    latestVersion.mockResolvedValue('2.4.0');
    listInstances.mockResolvedValue([mkInst({ id: 'i1', name: 'xg-hive', status: 'running', version: '2.3.0-hive1' })]);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
    renderQ(<HiveFleetContent close={() => {}} />);
    const btn = await screen.findByTestId('hive-update');
    expect(btn.textContent).toBe('Update to v2.4.0');
    fireEvent.click(btn);
    expect(updateInstance).not.toHaveBeenCalled();
    fireEvent.click(btn);
    await waitFor(() => expect(updateInstance).toHaveBeenCalledWith('i1', '2.4.0'));
    expect(confirm.mock.calls[0][0]).toContain('restarts');
    // accepted: the button goes away until the list shows the update (no second request)
    await waitFor(() => expect(screen.queryByTestId('hive-update')).toBeNull());
    confirm.mockRestore();
  });

  it('no Update when the Hive is current, stopped, or the release is unknown', async () => {
    latestVersion.mockResolvedValue('2.4.0');
    listInstances.mockResolvedValue([
      mkInst({ id: 'i1', name: 'current', status: 'running', version: '2.4.0' }),
      mkInst({ id: 'i2', name: 'stopped', status: 'stopped', version: '2.3.0' }),
    ]);
    renderQ(<HiveFleetContent close={() => {}} />);
    await waitFor(() => expect(screen.getByText('current')).toBeInTheDocument());
    await waitFor(() => expect(latestVersion).toHaveBeenCalled());
    expect(screen.queryByTestId('hive-update')).toBeNull();
  });

  it('a failed release lookup is asked again on the next mount', async () => {
    latestVersion.mockRejectedValueOnce(new Error('offline')).mockResolvedValue('2.4.0');
    listInstances.mockResolvedValue([mkInst({ id: 'i1', name: 'xg-hive', status: 'running', version: '2.3.0' })]);
    const first = renderQ(<HiveFleetContent close={() => {}} />);
    await waitFor(() => expect(latestVersion).toHaveBeenCalledTimes(1));
    first.unmount();
    renderQ(<HiveFleetContent close={() => {}} />);
    expect(await screen.findByTestId('hive-update')).toBeInTheDocument();
  });

  it('an updating Hive shows its own line, not the deploy steps', async () => {
    listInstances.mockResolvedValue([mkInst({ id: 'i1', name: 'xg-hive', status: 'updating', version: '2.3.0' })]);
    renderQ(<HiveFleetContent close={() => {}} />);
    expect(await screen.findByTestId('hive-updating')).toBeInTheDocument();
    expect(screen.queryByText('EC2 launched')).toBeNull();
    // transitional: no Delete (or Stop) while the update runs, and the list keeps polling
    expect(screen.queryByRole('button', { name: /^Delete$/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Stop$/ })).toBeNull();
  });
});

describe('Retry and Delete protect a Hive that holds data', () => {
  it('an errored Hive whose instance exists offers Start, and Retry warns before deleting', async () => {
    listInstances.mockResolvedValue([mkInst({ id: 'i1', name: 'xg-hive', status: 'error', ec2InstanceId: 'i-0abc' })]);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
    renderQ(<HiveFleetContent close={() => {}} />);
    await waitFor(() => expect(screen.getByText('xg-hive')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /^Start$/ })).toBeInTheDocument();
    const retry = screen.getByRole('button', { name: /^Retry$/ });
    fireEvent.click(retry);
    expect(retryInstance).not.toHaveBeenCalled();
    expect(confirm.mock.calls[0][0]).toContain('snapshots');
    expect(confirm.mock.calls[0][0]).toContain('use Start instead');
    fireEvent.click(retry);
    await waitFor(() => expect(retryInstance).toHaveBeenCalledWith('i1', true));
    confirm.mockRestore();
  });

  it('an errored Hive with no instance left offers only Retry, without the data warning', async () => {
    listInstances.mockResolvedValue([mkInst({ id: 'i1', name: 'xg-hive', status: 'error', ec2InstanceId: null })]);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderQ(<HiveFleetContent close={() => {}} />);
    await waitFor(() => expect(screen.getByText('xg-hive')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: /^Start$/ })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /^Retry$/ }));
    await waitFor(() => expect(retryInstance).toHaveBeenCalledWith('i1', false));
    expect(confirm.mock.calls[0][0]).not.toContain('snapshots');
    confirm.mockRestore();
  });

  it('Delete says the data goes with it', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderQ(<HiveFleetContent close={() => {}} />);
    await waitFor(() => expect(screen.getByText('xg-hive')).toBeInTheDocument());
    fireEvent.click(screen.getAllByRole('button', { name: /^Delete$/ })[0]);
    expect(confirm.mock.calls[0][0]).toContain('cannot be undone');
    expect(deleteInstance).not.toHaveBeenCalled();
    confirm.mockRestore();
  });
});
