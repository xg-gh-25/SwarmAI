/**
 * AwsAccountTab — the SwarmAI Account page (design 2026-10-31-swarmai-aws-account §6.5).
 *
 * Questions: does the page show the one state and the account line; does it list only
 * the capabilities that are not ready (all ready = one line); does a needs-login ADA
 * account say to run mwinit; do the two actions call the backend and show its answer;
 * does an unconnected install offer no action?
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import AwsAccountTab from '../AwsAccountTab';
import type { AwsAccountView } from '../../../services/awsAccount';

const mockGet = vi.fn();
const mockVerify = vi.fn();
const mockCheck = vi.fn();
vi.mock('../../../services/awsAccount', () => ({
  awsAccountService: {
    get: (...a: unknown[]) => mockGet(...a),
    verify: (...a: unknown[]) => mockVerify(...a),
    check: (...a: unknown[]) => mockCheck(...a),
  },
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

beforeEach(() => { mockGet.mockReset(); mockVerify.mockReset(); mockCheck.mockReset(); });

describe('AwsAccountTab', () => {
  it('shows the state, the account line and one all-ready line', async () => {
    mockGet.mockResolvedValue(view());
    render(<AwsAccountTab />);
    expect(await screen.findByTestId('aws-account-state')).toHaveTextContent('settings.awsAccount.state.connected');
    expect(screen.getByText('533267412361')).toBeInTheDocument();
    expect(screen.getByTestId('aws-account-all-ready')).toBeInTheDocument();
    expect(screen.queryByTestId('aws-account-gaps')).toBeNull();
    expect(screen.queryByTestId('aws-account-remedy')).toBeNull();
    expect(screen.getByText('settings.awsAccount.readOnlyBasis')).toBeInTheDocument();
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
    fireEvent.click(await screen.findByText('settings.awsAccount.verify'));
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

  it('an unconnected install shows the remedy and offers no action', async () => {
    mockGet.mockResolvedValue(view({ state: 'unconnected', account: null, capabilities: null }));
    render(<AwsAccountTab />);
    expect(await screen.findByTestId('aws-account-remedy')).toHaveTextContent('remedy.unconnected');
    expect(screen.getByText('settings.awsAccount.verify').closest('button')).toBeDisabled();
    expect(screen.getByText('settings.awsAccount.check').closest('button')).toBeDisabled();
  });
});
