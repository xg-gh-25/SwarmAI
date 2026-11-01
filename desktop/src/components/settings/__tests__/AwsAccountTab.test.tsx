/**
 * AwsAccountTab — the SwarmAI Account page (design 2026-10-31-swarmai-aws-account §6.5).
 *
 * Questions: does the page show the one state and the account line; does it list only
 * the capabilities that are not ready (all ready = one line); does a needs-login ADA
 * account say to run mwinit; do the two actions call the backend and show its answer;
 * does an unconnected install offer only "connect"; do "change account" and "sign in
 * again" open the connect steps in place; do the gaps offer the permission template;
 * does the Hive row name the cost before a raise creates anything, lower without a
 * question, and ask an upgrader with no Hive once (create / later)?
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import AwsAccountTab from '../AwsAccountTab';
import type { AwsAccountView } from '../../../services/awsAccount';

const mockGet = vi.fn();
const mockVerify = vi.fn();
const mockCheck = vi.fn();
const mockSetFleet = vi.fn();
const mockTemplate = vi.fn();
const mockCreateVpc = vi.fn();
const mockUseForChat = vi.fn();
vi.mock('../../../services/awsAccount', () => ({
  awsAccountService: {
    useForChat: (...a: unknown[]) => mockUseForChat(...a),
    get: (...a: unknown[]) => mockGet(...a),
    verify: (...a: unknown[]) => mockVerify(...a),
    check: (...a: unknown[]) => mockCheck(...a),
    setFleet: (...a: unknown[]) => mockSetFleet(...a),
    permissionTemplate: (...a: unknown[]) => mockTemplate(...a),
    createDefaultVpc: (...a: unknown[]) => mockCreateVpc(...a),
  },
  problemOf: (e: unknown) => ({ kind: 'error', message: String(e) }),
}));
const mockUpdateConfig = vi.fn();
const mockSetAuthMethod = vi.fn();
vi.mock('../../../services/settings', () => ({
  settingsService: { updateAPIConfiguration: (...a: unknown[]) => mockUpdateConfig(...a) },
}));
vi.mock('../../../services/system', () => ({
  systemService: { setAuthMethod: (...a: unknown[]) => mockSetAuthMethod(...a) },
}));
// the connect steps have their own tests: here only which start they get and what
// the page does with their answer
const connectProps: { current: Record<string, unknown> | null } = { current: null };
vi.mock('../../awsAccount/ConnectAccount', () => ({
  default: (p: Record<string, unknown>) => {
    connectProps.current = p;
    return <div data-testid="connect-stub" data-start={String(p.start)} />;
  },
}));
const desktop = { on: false };
vi.mock('../../../services/tauri', () => ({ isDesktop: () => desktop.on }));
const mockListAccounts = vi.fn();
vi.mock('../../../services/hive', async (orig) => ({
  ...(await orig<typeof import('../../../services/hive')>()),
  hiveService: { listAccounts: (...a: unknown[]) => mockListAccounts(...a) },
}));
vi.mock('../hiveComponents', () => ({
  AccountCard: ({ account }: { account: { id: string } }) => <div data-testid={`hive-account-${account.id}`} />,
  AddAccountDialog: () => <div data-testid="add-hive-account" />,
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: Record<string, string>) => (o?.name ? `${k}:${o.name}` : k) }),
}));

const ok = { status: 'ok' as const, detail: 'x' };

function view(over: Partial<AwsAccountView> = {}): AwsAccountView {
  return {
    state: 'connected', state_detail: null,
    account: {
      source: 'ada', account_id: '533267412361', region: 'us-east-1', context: 'internal',
      profile: null, caller_arn: 'arn:aws:sts::533267412361:assumed-role/Admin/me',
      verified_at: '2026-10-31T09:00:00+00:00', derived: true, ada: { role: 'Admin', provider: 'isengard' },
    },
    chat_via: 'aws',
    capabilities: { bedrock: ok, hive: { ...ok, basis: 'read-only' }, storage: ok, voice: ok,
                    checked_at: '2026-10-31T09:00:00+00:00' },
    profile_file: '/tmp/aws/config', supported: true,
    ...over,
  };
}

const fleet = (over = {}) => ({
  state: 'ready' as const, desired: 2, counted: 2, counted_all: 2, max: 8, instance_type: 'm7g.xlarge',
  region: 'us-east-1', monthly_usd: 238, dismissed: false, prices: { 'm7g.xlarge': 119 }, ...over,
});

beforeEach(() => {
  for (const m of [mockGet, mockVerify, mockCheck, mockSetFleet, mockTemplate, mockUpdateConfig, mockSetAuthMethod, mockCreateVpc, mockUseForChat, mockListAccounts]) m.mockReset();
  connectProps.current = null;
  desktop.on = false;
  mockListAccounts.mockResolvedValue([]);
});

describe('AwsAccountTab', () => {
  it('shows the state, the account line and one all-ready line', async () => {
    mockGet.mockResolvedValue(view());
    render(<AwsAccountTab />);
    expect(await screen.findByTestId('aws-account-state')).toHaveTextContent('settings.awsAccount.state.connected');
    expect(screen.getByText('533267412361')).toBeInTheDocument();
    expect(screen.getByTestId('aws-account-all-ready')).toBeInTheDocument();
    expect(screen.queryByTestId('aws-account-gaps')).toBeNull();
    expect(screen.queryByTestId('aws-account-sign-in')).toBeNull();
    // connected: no primary button, "change account" is a link on the state line
    expect(screen.getByTestId('aws-account-change')).toHaveTextContent('settings.awsAccount.change');
    expect(screen.queryByTestId('aws-account-terminal')).toBeNull();
  });

  it('lists only the capabilities that are not ready', async () => {
    mockGet.mockResolvedValue(view({
      capabilities: { bedrock: ok, hive: { status: 'missing', detail: 'ec2:DescribeInstances (AccessDenied)' },
                      storage: ok, voice: { status: 'not_applicable' } },
    }));
    render(<AwsAccountTab />);
    const gaps = await screen.findByTestId('aws-account-gaps');
    expect(gaps.querySelectorAll('li')).toHaveLength(1);
    expect(gaps).toHaveTextContent('settings.awsAccount.cap.hive');
    expect(gaps).toHaveTextContent('ec2:DescribeInstances');
  });

  it('tells an ADA user with an expired Midway to run mwinit', async () => {
    mockGet.mockResolvedValue(view({ state: 'needs_login', state_detail: 'Midway has expired' }));
    render(<AwsAccountTab />);
    expect(await screen.findByTestId('aws-account-remedy')).toHaveTextContent('settings.awsAccount.remedy.needs_login_ada');
  });

  it('verify and check call the backend and show its answer', async () => {
    mockGet.mockResolvedValue(view({ state: 'unverified' }));
    mockVerify.mockResolvedValue(view({ state: 'account_mismatch' }));
    mockCheck.mockResolvedValue(view({ state: 'connected' }));
    render(<AwsAccountTab />);
    fireEvent.click(await screen.findByTestId('aws-account-verify-now'));
    await waitFor(() => expect(screen.getByTestId('aws-account-state')).toHaveTextContent('account_mismatch'));
    fireEvent.click(screen.getByText('settings.awsAccount.check'));
    await waitFor(() => expect(screen.getByTestId('aws-account-state')).toHaveTextContent('connected'));
    expect(mockVerify).toHaveBeenCalledTimes(1);
    expect(mockCheck).toHaveBeenCalledTimes(1);
  });

  it('a failed action says so and keeps the last answer', async () => {
    mockGet.mockResolvedValue(view());
    mockVerify.mockRejectedValue(new Error('500'));
    render(<AwsAccountTab />);
    fireEvent.click(await screen.findByText('settings.awsAccount.verify'));
    expect(await screen.findByTestId('aws-account-action-failed')).toBeInTheDocument();
    expect(screen.getByTestId('aws-account-state')).toHaveTextContent('connected');
  });

  it('an unconnected install shows the remedy and offers only connecting', async () => {
    mockGet.mockResolvedValue(view({ state: 'unconnected', account: null, capabilities: null }));
    render(<AwsAccountTab />);
    expect(await screen.findByTestId('aws-account-remedy')).toHaveTextContent('remedy.unconnected');
    expect(screen.queryByText('settings.awsAccount.verify')).toBeNull();
    expect(screen.queryByText('settings.awsAccount.check')).toBeNull();
    expect(screen.queryByTestId('aws-account-region')).toBeNull();
    expect(screen.getByTestId('aws-account-change')).toHaveTextContent('settings.awsAccount.connect');
    expect(screen.queryByTestId('hive-fleet')).toBeNull();
  });

  it('change account opens the connect steps in place; their answer replaces the page', async () => {
    mockGet.mockResolvedValue(view());
    render(<AwsAccountTab />);
    fireEvent.click(await screen.findByTestId('aws-account-change'));
    expect(screen.getByTestId('connect-stub')).toHaveAttribute('data-start', 'pick');
    const p = connectProps.current!;
    expect(p.mode).toBe('settings');
    expect((p.prefill as { accountId: string }).accountId).toBe('533267412361');
    await act(async () => { (p.onConnected as (v: AwsAccountView) => void)(view({
      account: { ...view().account!, account_id: '111111111111' } })); });
    expect(screen.queryByTestId('connect-stub')).toBeNull();
    expect(screen.getByText('111111111111')).toBeInTheDocument();
  });

  it.each([['sso', 'sso'], ['login', 'login']])('an expired %s sign-in offers "sign in again" (%s)', async (source, start) => {
    mockGet.mockResolvedValue(view({ state: 'needs_login',
      account: { ...view().account!, source: source as 'sso', ada: null } }));
    render(<AwsAccountTab />);
    fireEvent.click(await screen.findByTestId('aws-account-sign-in'));
    expect(screen.getByTestId('connect-stub')).toHaveAttribute('data-start', start);
  });

  it('an expired ADA sign-in has no in-app sign-in: mwinit, then verify', async () => {
    mockGet.mockResolvedValue(view({ state: 'needs_login' }));
    render(<AwsAccountTab />);
    await screen.findByTestId('aws-account-remedy');
    expect(screen.queryByTestId('aws-account-sign-in')).toBeNull();
  });

  it('a missing Hive permission offers the template to copy', async () => {
    mockGet.mockResolvedValue(view({ capabilities: { bedrock: ok, storage: ok, voice: ok,
      hive: { status: 'missing', detail: 'missing ec2:RunInstances', missing: ['ec2:RunInstances'] } } }));
    mockTemplate.mockResolvedValue({ policy: {} });
    const write = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText: write } });
    render(<AwsAccountTab />);
    fireEvent.click(await screen.findByTestId('copy-permission-template'));
    await waitFor(() => expect(write).toHaveBeenCalledWith(JSON.stringify({ policy: {} }, null, 2)));
  });

  it('raising the Hive count names the cost first; only the confirm creates', async () => {
    mockGet.mockResolvedValue(view({ hive: fleet() }));
    mockSetFleet.mockResolvedValue(view({ hive: fleet({ desired: 3, counted: 3 }) }));
    render(<AwsAccountTab />);
    fireEvent.click(await screen.findByTestId('hive-plus'));
    expect(mockSetFleet).not.toHaveBeenCalled();
    expect(screen.getByTestId('hive-confirm')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('hive-confirm-go'));
    await waitFor(() => expect(mockSetFleet).toHaveBeenCalledWith({ desired_count: 3 }));
  });

  it('lowering the count creates nothing and asks nothing; it stops at 1', async () => {
    mockGet.mockResolvedValue(view({ hive: fleet() }));
    mockSetFleet.mockResolvedValue(view({ hive: fleet({ desired: 1, state: 'over', over: 1 }) }));
    render(<AwsAccountTab />);
    fireEvent.click(await screen.findByTestId('hive-minus'));
    await waitFor(() => expect(mockSetFleet).toHaveBeenCalledWith({ desired_count: 1 }));
    expect(await screen.findByTestId('hive-over')).toBeInTheDocument();
    expect(screen.getByTestId('hive-minus')).toBeDisabled();
  });

  it('an upgrader with no Hive is asked once: create 1, or later', async () => {
    mockGet.mockResolvedValue(view({ hive: fleet({ state: 'short', desired: 1, counted: 0, counted_all: 0, short: 1, monthly_usd: 0 }) }));
    mockSetFleet.mockResolvedValue(view({ hive: fleet({ state: 'short', desired: 1, counted: 0, short: 1, dismissed: true }) }));
    render(<AwsAccountTab />);
    expect(await screen.findByTestId('hive-upgrade-ask')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('hive-upgrade-later'));
    await waitFor(() => expect(mockSetFleet).toHaveBeenCalledWith({ dismissed: true }));
    expect(screen.queryByTestId('hive-upgrade-ask')).toBeNull();
    expect(screen.getByTestId('hive-short')).toBeInTheDocument();     // the page still shows it
  });

  it('chat on a key can be moved back to the account once Bedrock works', async () => {
    mockGet.mockResolvedValue(view({ chat_via: 'bedrock_key' }));
    mockUseForChat.mockResolvedValue(view());
    render(<AwsAccountTab />);
    fireEvent.click(await screen.findByText('settings.awsAccount.chatUseAccount'));
    await waitFor(() => expect(mockUseForChat).toHaveBeenCalledWith(view().account));
    await waitFor(() => expect(screen.queryByTestId('aws-account-chat-switch')).toBeNull());
  });

  it('a page that failed to load can be loaded again', async () => {
    mockGet.mockRejectedValueOnce(new Error('500')).mockResolvedValue(view());
    render(<AwsAccountTab />);
    fireEvent.click(await screen.findByTestId('aws-account-reload'));
    expect(await screen.findByTestId('aws-account-state')).toBeInTheDocument();
  });

  it('an account that cannot build Hives offers no + ; a raise shows the asked count while confirming', async () => {
    mockGet.mockResolvedValue(view({ hive: fleet({ state: 'cannot_build', desired: 2, counted: 1, short: 1,
      capability: { status: 'missing', missing: ['ec2:RunInstances'] } }) }));
    const { unmount } = render(<AwsAccountTab />);
    expect(await screen.findByTestId('hive-plus')).toBeDisabled();
    unmount();
    mockGet.mockResolvedValue(view({ hive: fleet() }));
    render(<AwsAccountTab />);
    fireEvent.click(await screen.findByTestId('hive-plus'));
    expect(screen.getByTestId('hive-count')).toHaveTextContent('3');
  });

  it('lists each Hive, and a missing default VPC can be created in its region', async () => {
    mockGet.mockResolvedValue(view({ hive: fleet({ state: 'cannot_build', desired: 2, counted: 1, short: 1,
      hives: [{ id: 'h1', name: 'hive-a', instance_type: 'm7g.xlarge', region: 'eu-west-1', status: 'running', created_at: 't' }],
      capability: { status: 'missing', no_default_vpc: true, region: 'eu-west-1', detail: 'no default VPC in eu-west-1' } }) }));
    mockCreateVpc.mockResolvedValue(view({ hive: fleet() }));
    render(<AwsAccountTab />);
    expect(await screen.findByTestId('hive-list')).toHaveTextContent('hive-a');
    fireEvent.click(screen.getByTestId('create-default-vpc'));
    await waitFor(() => expect(mockCreateVpc).toHaveBeenCalledWith('eu-west-1'));
    await waitFor(() => expect(screen.queryByTestId('hive-cannot')).toBeNull());
  });
});


describe('AwsAccountTab — the merged page (design 2026-11-01-settings-account-merge)', () => {
  const terminal = { kind: 'terminal' as const, command: 'mwinit -f', title: 'Midway sign-in' };

  it('an expired ADA sign-in types the backend\'s command into the terminal, then verify is next', async () => {
    desktop.on = true;
    mockGet.mockResolvedValue(view({ state: 'needs_login',
      remediation: { message: 'm', fix_text: 'f', settings_tab: 'aws-account', action: terminal } }));
    const seen: unknown[] = [];
    const on = (e: Event) => seen.push((e as CustomEvent).detail);
    window.addEventListener('swarm:terminal-run', on);
    render(<AwsAccountTab />);
    const btn = await screen.findByTestId('aws-account-terminal');
    expect(btn.className).toContain('btn-primary');
    fireEvent.click(btn);
    window.removeEventListener('swarm:terminal-run', on);
    expect(seen).toEqual([{ command: 'mwinit -f', title: 'Midway sign-in' }]);
    expect(screen.getByTestId('aws-account-terminal-hint')).toBeInTheDocument();
    expect(screen.getByTestId('aws-account-verify-now').className).toContain('btn-primary');
  });

  it('changing the main region writes aws_region and checks the account there', async () => {
    mockGet.mockResolvedValue(view({ region: 'us-east-1' }));
    mockUpdateConfig.mockResolvedValue({});
    mockCheck.mockResolvedValue(view({ region: 'eu-west-1' }));
    render(<AwsAccountTab />);
    await screen.findByTestId('aws-account-region');
    fireEvent.click(screen.getByText('US East (N. Virginia)'));
    fireEvent.click(await screen.findByText('EU (Ireland)'));
    await waitFor(() => expect(mockUpdateConfig).toHaveBeenCalledWith({ aws_region: 'eu-west-1' }));
    await waitFor(() => expect(mockCheck).toHaveBeenCalled());
  });

  const acc = (id: string, role: string, hiveCount: number) => ({ id, role, hiveCount }) as never;

  it('with a SwarmAI account, only other accounts that still hold Hives are listed, no add', async () => {
    desktop.on = true;
    mockGet.mockResolvedValue(view());
    mockListAccounts.mockResolvedValue([acc('swarmai', 'swarmai', 2), acc('old', 'other', 1), acc('empty', 'other', 0)]);
    render(<AwsAccountTab />);
    expect(await screen.findByTestId('hive-account-old')).toBeInTheDocument();
    expect(screen.queryByTestId('hive-account-swarmai')).toBeNull();
    expect(screen.queryByTestId('hive-account-empty')).toBeNull();
    expect(screen.getByTestId('aws-account-hive-accounts')).toHaveTextContent('settings.awsAccount.otherHives');
    expect(screen.queryByText('settings.awsAccount.addHiveAccount')).toBeNull();
  });

  it('without one, the legacy Hive accounts stay exactly as the old tab had them, with Add', async () => {
    desktop.on = true;
    mockGet.mockResolvedValue(view({ state: 'unconnected', account: null, capabilities: null }));
    mockListAccounts.mockResolvedValue([acc('mine', 'own', 0)]);
    render(<AwsAccountTab />);
    expect(await screen.findByTestId('hive-account-mine')).toBeInTheDocument();
    expect(screen.getByTestId('aws-account-hive-accounts')).toHaveTextContent('settings.awsAccount.hiveAccounts');
    fireEvent.click(screen.getByText('settings.awsAccount.addHiveAccount'));
    expect(screen.getByTestId('add-hive-account')).toBeInTheDocument();
  });
});

describe('AwsAccountTab — after the review', () => {
  const terminal = { kind: 'terminal' as const, command: 'mwinit -f', title: 'Midway sign-in' };
  const expired = () => view({ state: 'needs_login',
    remediation: { message: 'm', fix_text: 'f', settings_tab: 'aws-account', action: terminal } });

  it('says what is broken once: no hand instructions next to the button', async () => {
    desktop.on = true;
    mockGet.mockResolvedValue(expired());
    render(<AwsAccountTab />);
    expect(await screen.findByTestId('aws-account-remedy')).toHaveTextContent('settings.awsAccount.remedy.expired_ada');
    expect(screen.queryByText('settings.awsAccount.remedy.needs_login_ada')).toBeNull();
  });

  it('after the chat line typed the command, "verify again" is the next step here too', async () => {
    desktop.on = true;
    const ev = await import('../../common/settingsEvents');
    ev.markTerminalRun();
    mockGet.mockResolvedValue(expired());
    render(<AwsAccountTab />);
    expect((await screen.findByTestId('aws-account-verify-now')).className).toContain('btn-primary');
    expect(screen.getByTestId('aws-account-terminal').className).not.toContain('btn-primary');
  });

  it('an unverified account has "verify again" as its one primary action', async () => {
    mockGet.mockResolvedValue(view({ state: 'unverified' }));
    render(<AwsAccountTab />);
    expect((await screen.findByTestId('aws-account-verify-now')).className).toContain('btn-primary');
  });

  it('a sign-in request from the chat line goes straight into the in-app sign-in', async () => {
    const ev = await import('../../common/settingsEvents');
    ev.openSignIn();
    mockGet.mockResolvedValue(view({ state: 'needs_login', account: { ...view().account!, source: 'sso' } }));
    render(<AwsAccountTab />);
    expect(await screen.findByTestId('connect-stub')).toHaveAttribute('data-start', 'sso');
  });

  it('on the desktop the main region is offered even without an account', async () => {
    desktop.on = true;
    mockGet.mockResolvedValue(view({ state: 'unconnected', account: null, capabilities: null }));
    render(<AwsAccountTab />);
    expect(await screen.findByTestId('aws-account-region')).toBeInTheDocument();
  });
});
