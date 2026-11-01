/**
 * ConnectAccount — wizard steps ②–④ (design 2026-10-31-swarmai-aws-account §6.1, §6.3,
 * §6.3b, §7.5).
 *
 * Questions answered:
 * 1. the detected accounts show at once; only the rows that cannot prompt are verified
 *    unasked (a "check" row runs only on the person's click); the default is ADA's own
 *    profile, and "use" connects with that row's account, role and provider.
 * 2. a typed account ID is the anchor: other accounts' rows cannot be chosen.
 * 3. Midway expired → the wait screen retries every 5 s and goes on once it works.
 * 4. a failure keeps the person on the step with both accounts named; the step never
 *    reports a connection the backend refused.
 * 5. Hives in the current account: keep (stop / delete only) asks before changing; a
 *    sign-in that cannot keep them offers no keep button.
 * 6. AWS SSO in the app: the code is shown, the AWS link opened, the sign-in polled; the
 *    account typed in filters what it can see; the candidate carries account and role.
 * 7. AWS login without a new enough CLI says so and cannot be started (not a dead end).
 * 8. chat only: a key that fails verification stores nothing; a working key is stored
 *    through its secret route and never sent with the settings.
 * 9. access keys are stored first, then connected; nothing found → the two sign-in
 *    cards for the context; Bedrock missing after connecting → recheck / continue anyway.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import ConnectAccount from '../ConnectAccount';
import type { AwsAccountView, DetectResult, DetectRow } from '../../../services/awsAccount';

const svc = {
  detect: vi.fn(), verifyRow: vi.fn(), checkRow: vi.fn(), connect: vi.fn(), check: vi.fn(),
  ssoStart: vi.fn(), ssoPoll: vi.fn(), loginStart: vi.fn(), loginStatus: vi.fn(), storeKeys: vi.fn(),
};
vi.mock('../../../services/awsAccount', () => ({
  awsAccountService: new Proxy({}, { get: (_t, k: string) => (...a: unknown[]) => (svc as Record<string, (...x: unknown[]) => unknown>)[k](...a) }),
  problemOf: (e: unknown) => {
    const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    return d && typeof d === 'object' ? d : { kind: 'error', message: String(e) };
  },
}));
const sys = { verifyAuth: vi.fn(), persistBearerToken: vi.fn(), setAuthMethod: vi.fn() };
vi.mock('../../../services/system', () => ({
  systemService: new Proxy({}, { get: (_t, k: string) => (...a: unknown[]) => (sys as Record<string, (...x: unknown[]) => unknown>)[k](...a) }),
}));
const mockUpdateConfig = vi.fn();
vi.mock('../../../services/settings', () => ({
  settingsService: { updateAPIConfiguration: (...a: unknown[]) => mockUpdateConfig(...a) },
}));
const mockOpen = vi.fn();
vi.mock('../../../utils/openExternal', () => ({ openExternal: (...a: unknown[]) => mockOpen(...a) }));
const mockDesktop = vi.fn(() => true);
vi.mock('../../../services/tauri', () => ({ isDesktop: () => mockDesktop() }));
const mockRun = vi.fn();
vi.mock('../../common/settingsEvents', () => ({
  runInTerminal: (...a: unknown[]) => mockRun(...a), markTerminalRun: () => {},
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, o?: Record<string, unknown>) => {
      if (!o) return k;
      const vals = Object.entries(o).filter(([n]) => n !== 'defaultValue').map(([, v]) => String(v));
      return vals.length ? `${k}|${vals.join(',')}` : k;
    },
  }),
}));

const A = '533267412361';
const B = '850141023576';

function row(over: Partial<DetectRow>): DetectRow {
  return { profile: 'p', kind: 'keys', verify: 'auto', account_id: null, role: null, detail: null,
           sso: null, ada: null, ...over };
}
const ADA_OWN = row({ profile: '', kind: 'ada_default', account_id: A, role: 'Admin',
                      ada: { role: 'Admin', provider: 'isengard' } });
const ADA_B = row({ profile: 'ivt-beta', kind: 'ada', account_id: B, role: 'Admin',
                    ada: { role: 'Admin', provider: 'isengard' } });
const PROC = row({ profile: 'codex', kind: 'process', verify: 'check', detail: 'runs another program' });

function detected(rows: DetectRow[], over: Partial<DetectResult> = {}): DetectResult {
  return { rows, context: 'internal', login: { available: true, reason: null, version: '2.33.0' }, ...over };
}

function view(over: Partial<AwsAccountView> = {}): AwsAccountView {
  const ok = { status: 'ok' as const };
  return {
    state: 'connected', state_detail: null,
    account: { source: 'ada', account_id: A, region: 'us-east-1', context: 'internal', profile: null,
               caller_arn: `arn:aws:sts::${A}:assumed-role/Admin/me`, verified_at: 'x', derived: false,
               ada: { role: 'Admin', provider: 'isengard' } },
    chat_via: 'aws', capabilities: { bedrock: ok, hive: ok, storage: ok, voice: ok },
    profile_file: '/x', supported: true, ...over,
  };
}
const problem = (detail: unknown) => ({ response: { data: { detail } } });

beforeEach(() => {
  for (const m of [...Object.values(svc), ...Object.values(sys), mockUpdateConfig, mockOpen, mockRun]) m.mockReset();
  mockDesktop.mockReturnValue(true);
  svc.verifyRow.mockResolvedValue({ status: 'verified', account_id: A });
  mockOpen.mockResolvedValue(undefined);
});
afterEach(() => { vi.useRealTimers(); });

function renderIt(props: Partial<React.ComponentProps<typeof ConnectAccount>> = {}) {
  const onConnected = vi.fn();
  const onChatOnly = vi.fn();
  const onLater = vi.fn();
  render(<ConnectAccount mode="onboarding" onConnected={onConnected} onChatOnly={onChatOnly} onLater={onLater} {...props} />);
  return { onConnected, onChatOnly, onLater };
}

describe('ConnectAccount ② detection', () => {
  it('verifies only the rows that cannot prompt, defaults to ADA, connects with the row', async () => {
    svc.detect.mockResolvedValue(detected([ADA_OWN, ADA_B, PROC]));
    svc.verifyRow.mockImplementation(async (p: string) => ({ status: 'verified', account_id: p === '' ? A : B }));
    svc.connect.mockResolvedValue(view());
    const { onConnected } = renderIt();
    expect(await screen.findAllByTestId('connect-row')).toHaveLength(3);
    await waitFor(() => expect(svc.verifyRow).toHaveBeenCalledTimes(2));
    expect(svc.verifyRow).not.toHaveBeenCalledWith('codex');           // may prompt: not unasked
    expect(svc.checkRow).not.toHaveBeenCalled();
    const use = await screen.findByTestId('connect-use');
    expect(use).toHaveTextContent(A);
    fireEvent.click(use);
    await waitFor(() => expect(svc.connect).toHaveBeenCalledWith(
      expect.objectContaining({ source: 'ada', account_id: A, region: 'us-east-1',
                                ada: { role: 'Admin', provider: 'isengard' } }), undefined));
    fireEvent.click(await screen.findByTestId('connect-continue'));
    expect(onConnected).toHaveBeenCalled();
  });

  it('a "check" row runs its program only on the click', async () => {
    svc.detect.mockResolvedValue(detected([PROC]));
    svc.checkRow.mockResolvedValue({ status: 'verified', account_id: B });
    renderIt();
    fireEvent.click(await screen.findByText('awsConnect.check'));
    await waitFor(() => expect(svc.checkRow).toHaveBeenCalledWith('codex'));
    expect(await screen.findByTestId('connect-use')).toHaveTextContent(B);
  });

  it('the account ID is the anchor: other accounts cannot be chosen', async () => {
    svc.detect.mockResolvedValue(detected([ADA_OWN, ADA_B]));
    svc.verifyRow.mockImplementation(async (p: string) => ({ status: 'verified', account_id: p === '' ? A : B }));
    renderIt();
    await screen.findByTestId('connect-use');
    fireEvent.change(screen.getByTestId('connect-anchor'), { target: { value: B } });
    expect(screen.getByTestId('connect-use')).toHaveTextContent(B);
    const rowA = screen.getAllByTestId('connect-row').find((r) => r.getAttribute('data-account') === A)!;
    expect(rowA).toHaveTextContent('awsConnect.notAnchor');
    fireEvent.click(rowA);
    expect(screen.getByTestId('connect-use')).toHaveTextContent(B);    // still B
  });

  it('nothing found: the two sign-in cards for the context', async () => {
    svc.detect.mockResolvedValue(detected([], { context: 'external' }));
    renderIt();
    expect(await screen.findByTestId('connect-method-login')).toBeInTheDocument();
    expect(screen.getByTestId('connect-method-sso')).toBeInTheDocument();
    expect(screen.queryByTestId('connect-method-ada')).toBeNull();
    fireEvent.click(screen.getByTestId('connect-ctx-switch'));
    expect(screen.getByTestId('connect-method-ada')).toBeInTheDocument();
    expect(screen.getByTestId('connect-later')).toBeInTheDocument();
    expect(screen.getByTestId('connect-chat-only')).toBeInTheDocument();
  });
});

describe('ConnectAccount ③ ④ signing in and verifying', () => {
  it('Midway expired: waits, retries every 5 s, goes on once it works', async () => {
    svc.detect.mockResolvedValue(detected([], { context: 'internal' }));
    svc.connect
      .mockRejectedValueOnce(problem({ kind: 'needs_login', message: 'Midway has expired' }))
      .mockRejectedValueOnce(problem({ kind: 'needs_login', message: 'Midway has expired' }))
      .mockResolvedValue(view());
    renderIt();
    fireEvent.click(await screen.findByTestId('connect-method-ada'));
    fireEvent.change(screen.getByTestId('connect-ada-account'), { target: { value: A } });
    vi.useFakeTimers();
    await act(async () => { fireEvent.click(screen.getByTestId('connect-ada-go')); });
    expect(screen.getByTestId('connect-ada-wait')).toHaveTextContent('mwinit -f');
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(svc.connect).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId('connect-ada-wait')).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(svc.connect).toHaveBeenCalledTimes(3);
    expect(screen.getByTestId('connect-verified')).toBeInTheDocument();
  });

  it('Midway expired: in Settings one click types mwinit -f into the SwarmAI terminal; the wizard only says to run it', async () => {
    const reach = async (mode: 'onboarding' | 'settings') => {
      svc.detect.mockResolvedValue(detected([], { context: 'internal' }));
      svc.connect.mockRejectedValue(problem({ kind: 'needs_login', message: 'Midway has expired' }));
      const r = render(<ConnectAccount mode={mode} onConnected={vi.fn()} onLater={vi.fn()} onCancel={vi.fn()} />);
      fireEvent.click(await screen.findByTestId('connect-method-ada'));
      fireEvent.change(screen.getByTestId('connect-ada-account'), { target: { value: A } });
      await act(async () => { fireEvent.click(screen.getByTestId('connect-ada-go')); });
      expect(await screen.findByTestId('connect-ada-wait')).toHaveTextContent('mwinit -f');
      return r;
    };
    const wizard = await reach('onboarding');   // before the app's terminal exists: any terminal
    expect(screen.queryByTestId('connect-ada-terminal')).toBeNull();
    expect(screen.getByTestId('connect-ada-wait')).toHaveTextContent('awsConnect.ada.midwayRun');
    wizard.unmount();
    await reach('settings');
    fireEvent.click(screen.getByTestId('connect-ada-terminal'));
    expect(mockRun).toHaveBeenCalledWith('mwinit -f', expect.any(String));
  });

  it('a mismatch names both accounts and connects nothing', async () => {
    svc.detect.mockResolvedValue(detected([ADA_OWN]));
    svc.connect.mockRejectedValue(problem({ kind: 'account_mismatch', message: 'x', expected: A, actual: B }));
    const { onConnected } = renderIt();
    fireEvent.click(await screen.findByTestId('connect-use'));
    const err = await screen.findByTestId('connect-error');
    expect(err).toHaveTextContent(A);
    expect(err).toHaveTextContent(B);
    expect(screen.queryByTestId('connect-verified')).toBeNull();
    expect(onConnected).not.toHaveBeenCalled();
  });

  it('Hives in the current account: keep asks first, then changes', async () => {
    svc.detect.mockResolvedValue(detected([ADA_OWN]));
    svc.connect
      .mockRejectedValueOnce(problem({ kind: 'old_hives', message: 'x', account_id: B, can_keep: true,
                                       hives: [{ id: 'h1', name: 'hive-a', status: 'running' }] }))
      .mockResolvedValue(view());
    renderIt();
    fireEvent.click(await screen.findByTestId('connect-use'));
    expect(await screen.findByTestId('connect-old-hives')).toHaveTextContent('hive-a');
    fireEvent.click(screen.getByTestId('connect-keep-hives'));
    await waitFor(() => expect(svc.connect).toHaveBeenLastCalledWith(expect.objectContaining({ source: 'ada' }), 'keep'));
    expect(await screen.findByTestId('connect-verified')).toBeInTheDocument();
  });

  it('a sign-in that cannot keep the old Hives offers no keep', async () => {
    svc.detect.mockResolvedValue(detected([ADA_OWN]));
    svc.connect.mockRejectedValue(problem({ kind: 'old_hives', message: 'x', account_id: B, can_keep: false, hives: [] }));
    renderIt();
    fireEvent.click(await screen.findByTestId('connect-use'));
    expect(await screen.findByTestId('connect-old-hives')).toHaveTextContent('awsConnect.oldHives.cannotKeep');
    expect(screen.queryByTestId('connect-keep-hives')).toBeNull();
  });

  it('AWS SSO: code shown, link opened, polled, the anchor filters, the candidate has account and role', async () => {
    svc.detect.mockResolvedValue(detected([], { context: 'external' }));
    svc.ssoStart.mockResolvedValue({ flow_id: 'f1', user_code: 'WDJB-MJHT', interval: 1, expires_in: 600,
                                     verification_url: 'https://device.sso.us-east-1.amazonaws.com/?user_code=WDJB-MJHT' });
    svc.ssoPoll.mockResolvedValueOnce({ status: 'pending' }).mockResolvedValue({ status: 'done', accounts: [
      { account_id: A, name: 'prod', roles: ['ReadOnly', 'AdministratorAccess'] },
      { account_id: B, name: 'dev', roles: ['Dev'] }] });
    svc.connect.mockResolvedValue(view());
    renderIt();
    await screen.findByTestId('connect-method-sso');
    fireEvent.change(screen.getByTestId('connect-anchor'), { target: { value: A } });
    fireEvent.click(screen.getByTestId('connect-method-sso'));
    fireEvent.change(screen.getByTestId('connect-sso-url'), { target: { value: 'https://acme.awsapps.com/start' } });
    fireEvent.click(screen.getByTestId('connect-sso-go'));
    expect(await screen.findByTestId('connect-sso-code')).toHaveTextContent('WDJB-MJHT');
    expect(mockOpen).toHaveBeenCalledWith(expect.stringContaining('device.sso.us-east-1.amazonaws.com'));
    expect(await screen.findByTestId('connect-sso-pick', {}, { timeout: 4000 })).not.toHaveTextContent(B);
    fireEvent.click(screen.getByTestId('connect-sso-use'));
    await waitFor(() => expect(svc.connect).toHaveBeenCalledWith(expect.objectContaining({
      source: 'sso', account_id: A,
      sso: { start_url: 'https://acme.awsapps.com/start', sso_region: 'us-east-1', role: 'AdministratorAccess' } }), undefined));
  });

  it('AWS login without a new enough CLI says so and cannot start', async () => {
    svc.detect.mockResolvedValue(detected([], { context: 'external',
      login: { available: false, reason: 'old_cli', version: '2.24.10' } }));
    renderIt();
    fireEvent.click(await screen.findByTestId('connect-method-login'));
    expect(screen.getByTestId('connect-login-cli')).toHaveTextContent('2.24.10');
    expect(screen.getByTestId('connect-login-go')).toBeDisabled();
    expect(screen.getByTestId('connect-exits')).toHaveTextContent('awsConnect.another');
  });

  it('access keys are stored, then connected; the secret does not stay in the field', async () => {
    svc.detect.mockResolvedValue(detected([]));
    svc.storeKeys.mockResolvedValue({ stored: true, keys_id: 'AKIAEXAMPLE' });
    svc.connect.mockResolvedValue(view({ account: { ...view().account!, source: 'keys' } }));
    renderIt();
    fireEvent.click(await screen.findByTestId('connect-more'));
    fireEvent.click(screen.getByTestId('connect-way-keys'));
    fireEvent.change(screen.getByTestId('connect-key-id'), { target: { value: 'AKIAEXAMPLE' } });
    fireEvent.change(screen.getByTestId('connect-key-secret'), { target: { value: 'sekret' } });
    fireEvent.click(screen.getByTestId('connect-more-go'));
    await waitFor(() => expect(svc.connect).toHaveBeenCalledWith(
      expect.objectContaining({ source: 'keys', keys_id: 'AKIAEXAMPLE' }), undefined));
    expect(svc.storeKeys).toHaveBeenCalledWith('AKIAEXAMPLE', 'sekret');
    expect(svc.storeKeys.mock.invocationCallOrder[0]).toBeLessThan(svc.connect.mock.invocationCallOrder[0]);
  });

  it('Bedrock missing after connecting: recheck, or continue anyway', async () => {
    svc.detect.mockResolvedValue(detected([ADA_OWN]));
    svc.connect.mockResolvedValue(view({ capabilities: { bedrock: { status: 'missing', detail: 'model access' },
      hive: { status: 'ok' }, storage: { status: 'ok' }, voice: { status: 'ok' } } }));
    svc.check.mockResolvedValue(view());
    renderIt();
    fireEvent.click(await screen.findByTestId('connect-use'));
    expect(await screen.findByTestId('connect-bedrock-gap')).toBeInTheDocument();
    expect(screen.getByTestId('connect-continue')).toHaveTextContent('continueAnyway');
    fireEvent.click(screen.getByText('awsConnect.verified.recheck'));
    await waitFor(() => expect(screen.queryByTestId('connect-bedrock-gap')).toBeNull());
  });
});

describe('ConnectAccount: the review fixes', () => {
  it('a failed AWS login connect goes back to the login step with the error (no endless wait)', async () => {
    svc.detect.mockResolvedValue(detected([], { context: 'external' }));
    svc.loginStart.mockResolvedValue({ status: 'running', profile: 'swarmai-login-0000000a' });
    svc.loginStatus.mockResolvedValue({ status: 'done', profile: 'swarmai-login-0000000a' });
    svc.connect.mockRejectedValue(problem({ kind: 'account_mismatch', message: 'x', expected: A, actual: B }));
    renderIt();
    fireEvent.click(await screen.findByTestId('connect-method-login'));
    fireEvent.click(screen.getByTestId('connect-login-go'));
    await waitFor(() => expect(svc.connect).toHaveBeenCalledWith(
      expect.objectContaining({ source: 'login', login: { profile: 'swarmai-login-0000000a' } }), undefined),
      { timeout: 6000 });
    expect(await screen.findByTestId('connect-error')).toHaveTextContent(B);
    expect(screen.getByTestId('connect-login-go')).toBeInTheDocument();      // not the waiting screen
  });

  it('detection that fails says so, can be retried, and still offers the sign-in methods', async () => {
    svc.detect.mockRejectedValueOnce(new Error('500')).mockResolvedValue(detected([ADA_OWN]));
    renderIt();
    expect(await screen.findByTestId('connect-detect-failed')).toBeInTheDocument();
    expect(screen.getByTestId('connect-method-sso')).toBeInTheDocument();
    fireEvent.click(screen.getByText('awsConnect.retry'));
    expect(await screen.findByTestId('connect-use')).toHaveTextContent(A);
  });

  it('an ADA failure on a detected row opens the ADA form filled in', async () => {
    svc.detect.mockResolvedValue(detected([ADA_OWN]));
    svc.connect.mockRejectedValue(problem({ kind: 'ada_failed', message: 'no such role' }));
    renderIt();
    fireEvent.click(await screen.findByTestId('connect-use'));
    expect(await screen.findByTestId('connect-ada')).toBeInTheDocument();
    expect(screen.getByTestId('connect-ada-account')).toHaveValue(A);
    expect(screen.getByTestId('connect-error')).toHaveTextContent('no such role');
  });

  it('rows are radios a keyboard can choose; a failed check can be run again', async () => {
    svc.detect.mockResolvedValue(detected([ADA_OWN, ADA_B]));
    let betaCalls = 0;
    svc.verifyRow.mockImplementation(async (p: string) => (p === '' ? { status: 'verified', account_id: A }
      : ++betaCalls === 1 ? { status: 'timeout' } : { status: 'verified', account_id: B }));
    renderIt();
    await screen.findByTestId('connect-use');
    const rows = screen.getAllByTestId('connect-row');
    expect(rows[0]).toHaveAttribute('role', 'radio');
    expect(rows[0]).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(await screen.findByText('awsConnect.checkAgain'));
    await waitFor(() => expect(svc.verifyRow).toHaveBeenCalledTimes(3));     // an auto row: verified again
    await waitFor(() => expect(screen.getAllByTestId('connect-row')[1]).toHaveAttribute('aria-disabled', 'false'));
    fireEvent.keyDown(screen.getAllByTestId('connect-row')[1], { key: ' ' });
    expect(screen.getByTestId('connect-use')).toHaveTextContent(B);
  });

  it('a wrong region says why the button is grey', async () => {
    svc.detect.mockResolvedValue(detected([ADA_OWN]));
    renderIt();
    await screen.findByTestId('connect-use');
    fireEvent.change(screen.getByTestId('connect-region'), { target: { value: 'useast1' } });
    expect(screen.getByTestId('connect-region-hint')).toBeInTheDocument();
    expect(screen.getByTestId('connect-use')).toBeDisabled();
  });
});

describe('ConnectAccount ④ when chat uses a key', () => {
  it("Bedrock not used (chat on a key) is not a gap to fix", async () => {
    svc.detect.mockResolvedValue(detected([ADA_OWN]));
    svc.connect.mockResolvedValue(view({ chat_via: 'bedrock_key', capabilities: {
      bedrock: { status: 'not_applicable' }, hive: { status: 'ok' }, storage: { status: 'ok' }, voice: { status: 'ok' } } }));
    renderIt({ mode: 'settings', onCancel: vi.fn() });
    fireEvent.click(await screen.findByTestId('connect-use'));
    expect(await screen.findByTestId('connect-verified')).toBeInTheDocument();
    expect(screen.queryByTestId('connect-bedrock-gap')).toBeNull();
  });
});

describe('ConnectAccount: chat only', () => {
  async function toChat() {
    svc.detect.mockResolvedValue(detected([]));
    const cbs = renderIt();
    fireEvent.click(await screen.findByTestId('connect-chat-only'));
    fireEvent.change(screen.getByTestId('connect-chat-key'), { target: { value: ' br-key ' } });
    return cbs;
  }

  it('a key that fails verification stores nothing', async () => {
    sys.verifyAuth.mockResolvedValue({ success: false, fixHint: 'no model access' });
    const { onChatOnly } = await toChat();
    fireEvent.click(screen.getByTestId('connect-chat-go'));
    expect(await screen.findByTestId('connect-chat-failed')).toHaveTextContent('no model access');
    expect(sys.persistBearerToken).not.toHaveBeenCalled();
    expect(mockUpdateConfig).not.toHaveBeenCalled();
    expect(onChatOnly).not.toHaveBeenCalled();
  });

  it('a working key goes to its secret route, never with the settings', async () => {
    sys.verifyAuth.mockResolvedValue({ success: true });
    for (const m of [sys.persistBearerToken, sys.setAuthMethod, mockUpdateConfig]) m.mockResolvedValue(undefined);
    const { onChatOnly } = await toChat();
    fireEvent.click(screen.getByTestId('connect-chat-go'));
    await waitFor(() => expect(onChatOnly).toHaveBeenCalled());
    expect(sys.verifyAuth).toHaveBeenCalledWith(expect.objectContaining({ aws_bearer_token_bedrock: 'br-key', auth_method: 'bedrock_api_key' }));
    expect(sys.persistBearerToken).toHaveBeenCalledWith('br-key');
    expect(JSON.stringify(mockUpdateConfig.mock.calls)).not.toContain('br-key');
    expect(sys.setAuthMethod).toHaveBeenCalledWith('bedrock_api_key', 'internal');
  });
});
