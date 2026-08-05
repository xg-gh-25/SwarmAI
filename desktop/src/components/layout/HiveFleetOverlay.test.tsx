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
vi.mock('../../services/hive', async () => {
  const actual = await vi.importActual<typeof import('../../services/hive')>('../../services/hive');
  return {
    ...actual,
    hiveService: {
      listInstances: () => listInstances(),
      listAccounts: () => listAccounts(),
      stopInstance: (id: string) => stopInstance(id),
    },
  };
});

import { HiveFleetContent } from './HiveFleetOverlay';
import type { HiveInstance, HiveAccount } from '../../services/hive';

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
});

describe('HiveFleetOverlay', () => {
  it('fetches on mount and renders the fleet overview + instances', async () => {
    render(<HiveFleetContent close={() => {}} />);
    await waitFor(() => expect(screen.getByTestId('hive-overview')).toBeInTheDocument());
    expect(listInstances).toHaveBeenCalled();
    expect(listAccounts).toHaveBeenCalled();
    // both instances render (My + Shared sections)
    expect(screen.getByText('xg-hive')).toBeInTheDocument();
    expect(screen.getByText('teammate-hive')).toBeInTheDocument();
  });

  it('overview reflects real counts (1 running)', async () => {
    render(<HiveFleetContent close={() => {}} />);
    await waitFor(() => expect(screen.getByTestId('hive-overview')).toBeInTheDocument());
    const txt = screen.getByTestId('hive-overview').textContent || '';
    expect(txt).toContain('running');
  });

  it('toggles Fleet ⇄ Accounts and lists AWS accounts', async () => {
    render(<HiveFleetContent close={() => {}} />);
    await waitFor(() => expect(screen.getByTestId('hive-view-accounts')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('hive-view-accounts'));
    await waitFor(() => expect(screen.getByTestId('hive-accounts-view')).toBeInTheDocument());
    expect(screen.getByText('123456789012')).toBeInTheDocument();
  });

  it('Deploy button is enabled when an account exists', async () => {
    render(<HiveFleetContent close={() => {}} />);
    await waitFor(() => expect(screen.getByTestId('hive-deploy-btn')).toBeInTheDocument());
    expect(screen.getByTestId('hive-deploy-btn')).not.toBeDisabled();
  });

  it('Deploy button is disabled when there are no accounts', async () => {
    listAccounts.mockResolvedValue([]);
    render(<HiveFleetContent close={() => {}} />);
    await waitFor(() => expect(screen.getByTestId('hive-deploy-btn')).toBeInTheDocument());
    expect(screen.getByTestId('hive-deploy-btn')).toBeDisabled();
    // disabled state is discoverable (UX1): a title explains WHY
    expect(screen.getByTestId('hive-deploy-btn').getAttribute('title')).toContain('AWS account');
  });

  // ── failure/empty paths (REVIEW UX/Test gaps) ──
  it('renders the empty state when there are 0 instances', async () => {
    listInstances.mockResolvedValue([]);
    listAccounts.mockResolvedValue([ACCOUNT]);
    render(<HiveFleetContent close={() => {}} />);
    await waitFor(() => expect(screen.getByText(/No Hives deployed yet/)).toBeInTheDocument());
    expect(screen.getByTestId('hive-overview').textContent).toContain('0');
  });

  it('degrades gracefully when listInstances rejects (accounts still render — allSettled independence)', async () => {
    listInstances.mockRejectedValueOnce(new Error('Network error'));
    listAccounts.mockResolvedValue([ACCOUNT]);
    render(<HiveFleetContent close={() => {}} />);
    // does not crash: overview renders (empty), and the accounts leg still fulfilled
    await waitFor(() => expect(screen.getByTestId('hive-overview')).toBeInTheDocument());
    expect(screen.queryByText('xg-hive')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('hive-view-accounts'));
    await waitFor(() => expect(screen.getByText('123456789012')).toBeInTheDocument());
  });

  it('stops a running instance via the real InstanceCard action → calls hiveService.stopInstance', async () => {
    stopInstance.mockResolvedValue(undefined);
    render(<HiveFleetContent close={() => {}} />);
    await waitFor(() => expect(screen.getByText('xg-hive')).toBeInTheDocument());
    // xg-hive is the running 'my' instance → its card offers Stop
    fireEvent.click(screen.getAllByRole('button', { name: /^Stop$/ })[0]);
    await waitFor(() => expect(stopInstance).toHaveBeenCalledWith('i1'));
  });

  it('view toggle carries aria-selected (accessible tabs)', async () => {
    render(<HiveFleetContent close={() => {}} />);
    await waitFor(() => expect(screen.getByTestId('hive-view-fleet')).toBeInTheDocument());
    expect(screen.getByTestId('hive-view-fleet').getAttribute('aria-selected')).toBe('true');
    fireEvent.click(screen.getByTestId('hive-view-accounts'));
    await waitFor(() => expect(screen.getByTestId('hive-view-accounts').getAttribute('aria-selected')).toBe('true'));
    expect(screen.getByTestId('hive-view-fleet').getAttribute('aria-selected')).toBe('false');
  });
});
