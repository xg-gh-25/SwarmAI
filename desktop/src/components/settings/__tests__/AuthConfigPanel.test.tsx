/**
 * AuthConfigPanel tests (AC4 + AC5).
 *
 * AC4: SSO must NOT show a dead editable Account ID input (boto3/SSO reads the
 *      account from the profile; an editable value was silently discarded).
 *      ADA keeps the editable field (it builds the ada-credentials-update cmd).
 * AC5: verify happens with the attempted override body; persist only on success.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import AuthConfigPanel from '../AuthConfigPanel';

const mockVerifyAuth = vi.fn();
const mockGetAuthHint = vi.fn();
const mockGetAPIConfiguration = vi.fn();
const mockUpdateAPIConfiguration = vi.fn();

vi.mock('../../../services/system', () => ({
  systemService: {
    verifyAuth: (...a: unknown[]) => mockVerifyAuth(...a),
    getAuthHint: (...a: unknown[]) => mockGetAuthHint(...a),
  },
}));
vi.mock('../../../services/settings', () => ({
  settingsService: {
    getAPIConfiguration: (...a: unknown[]) => mockGetAPIConfiguration(...a),
    updateAPIConfiguration: (...a: unknown[]) => mockUpdateAPIConfiguration(...a),
  },
}));
vi.mock('../../common', () => ({
  Dropdown: ({ label }: { label: string }) => <div data-testid="dropdown">{label}</div>,
}));

beforeEach(() => {
  vi.clearAllMocks();
  mockGetAPIConfiguration.mockResolvedValue({ awsRegion: 'us-east-1' });
  mockUpdateAPIConfiguration.mockResolvedValue({});
  mockVerifyAuth.mockResolvedValue({ success: true, model: 'claude-opus-4-8', latencyMs: 100 });
});

describe('AuthConfigPanel — AC4 SSO account input', () => {
  it('SSO with a probed account shows a READ-ONLY account (no editable textbox)', async () => {
    mockGetAuthHint.mockResolvedValue({
      suggestedMethod: 'sso', hasAdaDir: false, runMode: 'desktop',
      iamDetails: { accountId: '123456789012', region: 'us-east-1' },
    });
    render(<AuthConfigPanel mode="onboarding" />);
    await waitFor(() => screen.getByText('Verify Connection'));
    // The probed account is shown...
    expect(screen.getByText('123456789012')).toBeInTheDocument();
    // ...but NOT as an editable input with the old placeholder.
    expect(screen.queryByPlaceholderText(/12-digit AWS account ID/i)).not.toBeInTheDocument();
  });

  it('SSO with NO probed account hides the field entirely (no empty read-only box)', async () => {
    mockGetAuthHint.mockResolvedValue({
      suggestedMethod: 'sso', hasAdaDir: false, runMode: 'desktop',
      // no iamDetails, no adaDetails → accountId stays ''
    });
    render(<AuthConfigPanel mode="onboarding" />);
    await waitFor(() => screen.getByText('Verify Connection'));
    // No editable input AND no read-only "AWS Account ID" label at all.
    expect(screen.queryByPlaceholderText(/12-digit AWS account ID/i)).not.toBeInTheDocument();
    expect(screen.queryByText('AWS Account ID')).not.toBeInTheDocument();
  });

  it('ADA keeps an EDITABLE account input', async () => {
    mockGetAuthHint.mockResolvedValue({
      suggestedMethod: 'ada', hasAdaDir: true, runMode: 'desktop',
      adaDetails: { accountId: '', roleName: '' },
    });
    render(<AuthConfigPanel mode="onboarding" />);
    await waitFor(() => screen.getByText('Verify Connection'));
    // Select ADA method (card button)
    await act(async () => { fireEvent.click(screen.getByText('Ada').closest('button')!); });
    expect(screen.getByPlaceholderText(/12-digit AWS account ID/i)).toBeInTheDocument();
  });
});

describe('AuthConfigPanel — AC5 verify then persist', () => {
  it('failed verify does NOT persist config', async () => {
    mockGetAuthHint.mockResolvedValue({ suggestedMethod: 'sso', hasAdaDir: false, runMode: 'desktop' });
    mockVerifyAuth.mockResolvedValue({ success: false, errorType: 'access_denied', fixHint: 'x' });
    render(<AuthConfigPanel mode="settings" />);
    await waitFor(() => screen.getByText('Verify Connection'));
    await act(async () => { fireEvent.click(screen.getByText('Verify Connection').closest('button')!); });
    await waitFor(() => expect(mockVerifyAuth).toHaveBeenCalled());
    expect(mockUpdateAPIConfiguration).not.toHaveBeenCalled();
  });

  it('successful verify passes the attempted override to verifyAuth, then persists', async () => {
    mockGetAuthHint.mockResolvedValue({ suggestedMethod: 'sso', hasAdaDir: false, runMode: 'desktop' });
    render(<AuthConfigPanel mode="settings" />);
    await waitFor(() => screen.getByText('Verify Connection'));
    await act(async () => { fireEvent.click(screen.getByText('Verify Connection').closest('button')!); });
    await waitFor(() => expect(mockUpdateAPIConfiguration).toHaveBeenCalled());
    expect(mockVerifyAuth).toHaveBeenCalledWith(expect.objectContaining({ use_bedrock: true }));
  });
});
