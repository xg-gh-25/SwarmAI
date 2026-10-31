/**
 * HiveSetupStep — wizard step ⑤ (design 2026-10-31-swarmai-aws-account §6.1 ⑤, §7.3).
 *
 * Questions answered:
 * 1. the default is 1 Hive; the button names the count, the monthly cost and the
 *    account; clicking it sends the count, size and region and goes on without waiting.
 * 2. the "can it create" check runs for the region chosen here; when it fails the
 *    button only moves on (nothing is created) and the template is offered.
 * 3. Hives already in the account: "already N", nothing more is created by default.
 * 4. no account (chat only, or Hive unavailable): the step says so and moves on.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { HiveSetupStep } from '../HiveFleet';
import type { AwsAccountView, FleetStatus } from '../../../services/awsAccount';

const mockGet = vi.fn();
const mockCheck = vi.fn();
const mockSetFleet = vi.fn();
vi.mock('../../../services/awsAccount', () => ({
  awsAccountService: {
    get: (...a: unknown[]) => mockGet(...a),
    check: (...a: unknown[]) => mockCheck(...a),
    setFleet: (...a: unknown[]) => mockSetFleet(...a),
    permissionTemplate: vi.fn(),
  },
  problemOf: (e: unknown) => ({ kind: 'error', message: String(e) }),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, o?: Record<string, unknown>) => (o ? `${k}|${Object.values(o).join(',')}` : k),
  }),
}));

const A = '533267412361';
function fleet(over: Partial<FleetStatus> = {}): FleetStatus {
  return { state: 'short', desired: 1, counted: 0, counted_all: 0, max: 8, instance_type: 'm7g.xlarge',
           region: 'us-east-1', monthly_usd: 0, dismissed: false, short: 1,
           prices: { 'm7g.xlarge': 119, 'm7g.large': 60 }, ...over };
}
function view(hive: FleetStatus | null, hiveCap: Record<string, unknown> = { status: 'ok', region: 'us-east-1' }): AwsAccountView {
  return {
    state: 'connected', state_detail: null,
    account: { source: 'ada', account_id: A, region: 'us-east-1', context: 'internal', profile: null,
               caller_arn: 'x', verified_at: 'x', derived: false, ada: null },
    chat_via: 'aws',
    capabilities: { bedrock: { status: 'ok' }, hive: hiveCap as never, storage: { status: 'ok' }, voice: { status: 'ok' } },
    profile_file: '/x', supported: true, hive,
  };
}

beforeEach(() => { for (const m of [mockGet, mockCheck, mockSetFleet]) m.mockReset(); });

describe('HiveSetupStep', () => {
  it('defaults to 1 and names what the button does', async () => {
    mockGet.mockResolvedValue(view(fleet()));
    mockSetFleet.mockResolvedValue(view(fleet({ state: 'ready', counted: 1 })));
    const onDone = vi.fn();
    render(<HiveSetupStep onDone={onDone} />);
    const go = await screen.findByTestId('hive-go');
    expect(go).toHaveTextContent(`hiveFleet.create|1,119,${A}`);
    fireEvent.click(screen.getByTestId('hive-plus'));
    expect(go).toHaveTextContent(`hiveFleet.create|2,238,${A}`);
    fireEvent.click(go);
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(mockSetFleet).toHaveBeenCalledWith({ desired_count: 2, instance_type: 'm7g.xlarge', region: 'us-east-1' });
    expect(mockCheck).not.toHaveBeenCalled();                     // already checked for this region
  });

  it('checks the chosen region; when it cannot create, the button only moves on', async () => {
    mockGet.mockResolvedValue(view(fleet()));
    mockCheck.mockResolvedValue(view(fleet(), { status: 'missing', region: 'eu-west-1', missing: ['ec2:RunInstances'] }));
    const onDone = vi.fn();
    render(<HiveSetupStep onDone={onDone} />);
    fireEvent.change(await screen.findByTestId('hive-region'), { target: { value: 'eu-west-1' } });
    await waitFor(() => expect(mockCheck).toHaveBeenCalledWith('eu-west-1'));
    expect(await screen.findByTestId('copy-permission-template')).toBeInTheDocument();
    expect(screen.getByTestId('hive-cap')).toHaveTextContent('ec2:RunInstances');
    const go = screen.getByTestId('hive-go');
    expect(go).toHaveTextContent('hiveFleet.createLater');
    fireEvent.click(go);
    expect(onDone).toHaveBeenCalled();
    expect(mockSetFleet).not.toHaveBeenCalled();
    expect(mockCheck).toHaveBeenCalledTimes(1);                   // once per region, not a loop
  });

  it('a check that comes back without its region is not asked again (no loop)', async () => {
    mockGet.mockResolvedValue(view(fleet()));
    mockCheck.mockResolvedValue(view(fleet(), { status: 'unknown', detail: 'offline' }));
    render(<HiveSetupStep onDone={vi.fn()} />);
    fireEvent.change(await screen.findByTestId('hive-region'), { target: { value: 'eu-west-1' } });
    await waitFor(() => expect(mockCheck).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 1500));
    expect(mockCheck).toHaveBeenCalledTimes(1);
  });

  it('Hives already in the account: nothing more is created by default', async () => {
    mockGet.mockResolvedValue(view(fleet({ state: 'ready', desired: 2, counted: 2, counted_all: 2, short: 0 })));
    mockSetFleet.mockResolvedValue(view(fleet({ state: 'ready', desired: 2, counted: 2 })));
    render(<HiveSetupStep onDone={vi.fn()} />);
    expect(await screen.findByTestId('hive-existing')).toHaveTextContent('2');
    expect(screen.getByTestId('hive-go')).toHaveTextContent('hiveFleet.continue');
    expect(screen.getByTestId('hive-count')).toHaveTextContent('2');
  });

  it('no Hive available: says so and moves on', async () => {
    mockGet.mockResolvedValue(view(null));
    const onDone = vi.fn();
    render(<HiveSetupStep onDone={onDone} />);
    fireEvent.click(await screen.findByText('hiveFleet.continue'));
    expect(screen.getByText('hiveFleet.connectFirst')).toBeInTheDocument();
    expect(onDone).toHaveBeenCalled();
  });

  it('an unknown price is said, never "$0"', async () => {
    mockGet.mockResolvedValue(view(fleet({ prices: {} })));
    render(<HiveSetupStep onDone={vi.fn()} />);
    expect(await screen.findByTestId('hive-go')).toHaveTextContent(`hiveFleet.createNoPrice|1,${A}`);
  });

  it('when the 8 are already used across accounts, nothing can be created and it says why', async () => {
    mockGet.mockResolvedValue(view(fleet({ counted: 0, counted_all: 8 })));
    const onDone = vi.fn();
    render(<HiveSetupStep onDone={onDone} />);
    expect(await screen.findByTestId('hive-no-room')).toBeInTheDocument();
    expect(screen.getByTestId('hive-limit')).toHaveTextContent('hiveFleet.maxElsewhere');
    fireEvent.click(screen.getByTestId('hive-go'));
    expect(onDone).toHaveBeenCalled();
    expect(mockSetFleet).not.toHaveBeenCalled();
  });

  it('going back to a region whose check is running shows its answer (no stuck "checking")', async () => {
    mockGet.mockResolvedValue(view(fleet(), { status: 'ok', region: 'us-east-1' }));
    let answer: (v: AwsAccountView) => void = () => {};
    mockCheck.mockImplementationOnce(() => new Promise((r) => { answer = r; }));
    render(<HiveSetupStep onDone={vi.fn()} />);
    const input = await screen.findByTestId('hive-region');
    fireEvent.change(input, { target: { value: 'eu-west-1' } });
    await waitFor(() => expect(mockCheck).toHaveBeenCalledWith('eu-west-1'));
    expect(screen.getByTestId('hive-cap')).toHaveTextContent('hiveFleet.checking');
    fireEvent.change(input, { target: { value: 'eu-west-2' } });
    fireEvent.change(input, { target: { value: 'eu-west-1' } });                // back within 600 ms
    answer(view(fleet(), { status: 'ok', region: 'eu-west-1' }));
    await waitFor(() => expect(screen.getByTestId('hive-cap')).toHaveTextContent('hiveFleet.canCreate'));
  });

  it('has a way back to the account step', async () => {
    mockGet.mockResolvedValue(view(fleet()));
    const onBack = vi.fn();
    render(<HiveSetupStep onDone={vi.fn()} onBack={onBack} />);
    fireEvent.click(await screen.findByTestId('hive-back'));
    expect(onBack).toHaveBeenCalled();
  });

  it('leaving a region before its answer comes back asks again on return', async () => {
    mockGet.mockResolvedValue(view(fleet(), { status: 'ok', region: 'us-east-1' }));
    let first: (v: AwsAccountView) => void = () => {};
    mockCheck.mockImplementationOnce(() => new Promise((r) => { first = r; }))
      .mockResolvedValue(view(fleet(), { status: 'ok', region: 'eu-west-1' }));
    render(<HiveSetupStep onDone={vi.fn()} />);
    const input = await screen.findByTestId('hive-region');
    fireEvent.change(input, { target: { value: 'eu-west-1' } });
    await waitFor(() => expect(mockCheck).toHaveBeenCalledWith('eu-west-1'));
    fireEvent.change(input, { target: { value: 'us-east-1' } });                // left before the answer
    first(view(fleet(), { status: 'ok', region: 'eu-west-1' }));
    await new Promise((r) => setTimeout(r, 50));
    fireEvent.change(input, { target: { value: 'eu-west-1' } });                // back: asked again
    await waitFor(() => expect(mockCheck).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByTestId('hive-cap')).toHaveTextContent('hiveFleet.canCreate'));
  });
});

