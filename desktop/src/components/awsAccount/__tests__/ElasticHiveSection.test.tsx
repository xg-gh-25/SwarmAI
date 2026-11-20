/**
 * ElasticHiveSection — the Hive page's elastic Hive (design
 * 2026-11-20-swarm-out-multi-carrier §6.8).
 *
 * Questions answered:
 * 1. off and it can be turned on: one button, which asks the backend to turn it on.
 * 2. off and it cannot (no image configured): the button is disabled and the reason shows.
 * 3. on: its status and size (8 vCPU / 32 GB peak, 32 GB disk) and Turn off, which asks
 *    first (naming the runs that stop) and then turns it off.
 * 4. on with an image behind this SwarmAI: Update is offered and sent.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ElasticHiveSection } from '../HiveFleet';
import type { ElasticHive, ElasticStatus } from '../../../services/awsAccount';

const mockStatus = vi.fn();
const mockEnable = vi.fn();
const mockUpdate = vi.fn();
const mockDisable = vi.fn();
vi.mock('../../../services/awsAccount', () => ({
  awsAccountService: {
    elasticStatus: (...a: unknown[]) => mockStatus(...a),
    enableElastic: (...a: unknown[]) => mockEnable(...a),
    updateElastic: (...a: unknown[]) => mockUpdate(...a),
    disableElastic: (...a: unknown[]) => mockDisable(...a),
  },
  problemOf: (e: unknown) => ({ kind: 'error', message: String(e) }),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, o?: Record<string, unknown>) => (o ? `${k}|${Object.values(o).join(',')}` : k),
  }),
}));

const SIZE = { vcpu_base: 2, memory_gib_base: 8, vcpu_peak: 8, memory_gib_peak: 32, disk_gib: 32 };
function hive(over: Partial<ElasticHive> = {}): ElasticHive {
  return { id: 'e1', name: 'elastic', account_ref: 'swarmai', region: 'us-east-1', status: 'ready',
           error_message: null, runtime_status: 'READY', image_version: '2.5.5', up_to_date: true,
           open_runs: 0, working: false, ...over };
}
function status(over: Partial<ElasticStatus> = {}): ElasticStatus {
  return { host_version: '2.5.5', can_enable: true, reason: null, size: SIZE, hives: [], ...over };
}

beforeEach(() => { for (const m of [mockStatus, mockEnable, mockUpdate, mockDisable]) m.mockReset(); });

describe('ElasticHiveSection', () => {
  it('off: one button turns it on', async () => {
    mockStatus.mockResolvedValue(status());
    mockEnable.mockResolvedValue({ id: 'e1', status: 'enabling' });
    render(<ElasticHiveSection />);
    const go = await screen.findByTestId('elastic-enable');
    expect(go).not.toBeDisabled();
    expect(screen.queryByTestId('elastic-reason')).toBeNull();
    fireEvent.click(go);
    await waitFor(() => expect(mockEnable).toHaveBeenCalledTimes(1));
  });

  it('off and it cannot be turned on: disabled, with the reason', async () => {
    mockStatus.mockResolvedValue(status({ can_enable: false, reason: 'No elastic Hive image is configured.' }));
    render(<ElasticHiveSection />);
    expect(await screen.findByTestId('elastic-enable')).toBeDisabled();
    expect(screen.getByTestId('elastic-reason')).toHaveTextContent('No elastic Hive image is configured.');
  });

  it('on: status, size and Turn off, which asks first', async () => {
    mockStatus.mockResolvedValue(status({ hives: [hive({ open_runs: 2 })] }));
    mockDisable.mockResolvedValue({ id: 'e1', status: 'disabling' });
    render(<ElasticHiveSection />);
    expect(await screen.findByTestId('elastic-status')).toHaveTextContent('hiveFleet.elastic.status.ready');
    expect(screen.getByTestId('elastic-size')).toHaveTextContent('hiveFleet.elastic.size|8,32,32');
    expect(screen.queryByTestId('elastic-enable')).toBeNull();
    fireEvent.click(screen.getByTestId('elastic-disable'));
    expect(screen.getByTestId('elastic-confirm-off')).toHaveTextContent('hiveFleet.elastic.offRuns|2');
    expect(mockDisable).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('elastic-disable-go'));
    await waitFor(() => expect(mockDisable).toHaveBeenCalledWith('e1'));
  });

  it('on with an older image: Update is offered', async () => {
    mockStatus.mockResolvedValue(status({ host_version: '2.5.6', hives: [hive({ up_to_date: false })] }));
    mockUpdate.mockResolvedValue({ id: 'e1', status: 'upgrading', version: '2.5.6' });
    render(<ElasticHiveSection />);
    expect(await screen.findByTestId('elastic-outdated')).toHaveTextContent('2.5.5,2.5.6');
    fireEvent.click(screen.getByTestId('elastic-update'));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith('e1'));
  });

  it('a failed one shows why; one this SwarmAI is turning on has no Turn off, one a quit SwarmAI left has', async () => {
    mockStatus.mockResolvedValue(status({ hives: [
      hive({ status: 'failed', error_message: 'AgentCore says CREATE_FAILED' }),
      hive({ id: 'e2', status: 'enabling', working: true }),
      hive({ id: 'e3', status: 'enabling', working: false })] }));
    render(<ElasticHiveSection />);
    expect(await screen.findByTestId('elastic-error-message')).toHaveTextContent('CREATE_FAILED');
    expect(screen.getAllByTestId('elastic-disable')).toHaveLength(2);
  });

  it('after a failed update, Update is offered again', async () => {
    mockStatus.mockResolvedValue(status({ host_version: '2.5.6',
      hives: [hive({ status: 'failed', up_to_date: false, error_message: 'Update to 2.5.6 failed' })] }));
    render(<ElasticHiveSection />);
    expect(await screen.findByTestId('elastic-update')).toBeInTheDocument();
  });
});
