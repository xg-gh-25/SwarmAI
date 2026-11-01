/**
 * CredentialBanner — the sign-in problem line (design 2026-11-01-settings-account-merge §4).
 * Questions: does it show only on a definitive expiry, in flow (no fixed overlay), and
 * not while Settings is open; does its one button do what the backend's action says
 * (type the command into the terminal without Enter, sign in again, open a tab); does it
 * wait for the sign-in one request at a time and say when it worked; does × hold until
 * the credentials work again?
 */
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import CredentialBanner from './CredentialBanner';
import type { HealthState, AuthStatus } from '../../types';

vi.mock('../../contexts/HealthContext', () => ({ useHealth: vi.fn() }));
import { useHealth } from '../../contexts/HealthContext';

const mockGet = vi.fn();
const mockVerify = vi.fn();
vi.mock('../../services/awsAccount', () => ({
  awsAccountService: { get: (...a: unknown[]) => mockGet(...a), verify: (...a: unknown[]) => mockVerify(...a) },
}));
const desktop = { on: true };
vi.mock('../../services/tauri', () => ({ isDesktop: () => desktop.on }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: Record<string, string>) => (o ? `${k}|${Object.values(o).join('|')}` : k) }),
}));

function setAuth(auth?: AuthStatus) {
  const health: HealthState = { status: 'connected', auth, lastCheckedAt: Date.now(), consecutiveFailures: 0 };
  (useHealth as unknown as ReturnType<typeof vi.fn>).mockReturnValue({ health, triggerHealthCheck: vi.fn() });
}

const remedy = (action: unknown) => ({
  remediation: { message: 'Your Midway sign-in has expired.', fix_text: 'Run `mwinit -f` …', settings_tab: 'aws-account', action },
});
const terminal = { kind: 'terminal', command: 'mwinit -f', title: 'Midway sign-in' };

function listen(name: string) {
  const seen: unknown[] = [];
  const on = (e: Event) => seen.push((e as CustomEvent).detail);
  window.addEventListener(name, on);
  return { seen, stop: () => window.removeEventListener(name, on) };
}

beforeEach(() => {
  vi.clearAllMocks();
  desktop.on = true;
  mockGet.mockResolvedValue(remedy(terminal));
});
afterEach(() => { vi.useRealTimers(); });

describe('CredentialBanner', () => {
  it.each([['valid'], ['unknown'], [undefined]])('says nothing when auth is %s', (auth) => {
    setAuth(auth as AuthStatus | undefined);
    const { container } = render(<CredentialBanner />);
    expect(container.firstChild).toBeNull();
  });

  it('is one in-flow line with the backend\'s message, never a fixed box', async () => {
    setAuth('expired');
    render(<CredentialBanner />);
    const line = await screen.findByTestId('credential-banner');
    await screen.findByText('Your Midway sign-in has expired.');
    expect(line.className).not.toMatch(/\bfixed\b|absolute/);
    expect(screen.getByTestId('credential-banner-action')).toHaveTextContent('credentialBanner.inTerminal');
  });

  it('is hidden while Settings is open', () => {
    setAuth('expired');
    const { container } = render(<CredentialBanner hidden />);
    expect(container.firstChild).toBeNull();
  });

  it('types the command into a terminal (the event carries it; no Enter is added here)', async () => {
    setAuth('expired');
    render(<CredentialBanner />);
    await screen.findByText('Your Midway sign-in has expired.');
    const ev = listen('swarm:terminal-run');
    fireEvent.click(screen.getByTestId('credential-banner-action'));
    ev.stop();
    expect(ev.seen).toEqual([{ command: 'mwinit -f', title: 'Midway sign-in' }]);
    expect(screen.getByTestId('credential-banner')).toHaveTextContent('credentialBanner.waiting|mwinit -f');
  });

  it('waits one verify at a time, 5 s after each answer, and says when it worked', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    setAuth('expired');
    let release: (v: unknown) => void = () => {};
    mockVerify.mockImplementationOnce(() => new Promise((r) => { release = r; }))
      .mockResolvedValue({ state: 'connected', account: { account_id: '533267412361' } });
    render(<CredentialBanner />);
    await screen.findByText('Your Midway sign-in has expired.');
    fireEvent.click(screen.getByTestId('credential-banner-action'));
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(mockVerify).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect(mockVerify).toHaveBeenCalledTimes(1);           // still in flight: no second request
    await act(async () => { release({ state: 'needs_login', account: null }); await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(mockVerify).toHaveBeenCalledTimes(2);
    expect(await screen.findByTestId('credential-banner-done')).toHaveTextContent('533267412361');
    await act(async () => { await vi.advanceTimersByTimeAsync(2_500); });
    // health may still say "expired" until its next sample: the line stays away
    expect(screen.queryByTestId('credential-banner')).toBeNull();
  });

  it('stops checking after 5 minutes and offers "check again"', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    setAuth('expired');
    mockVerify.mockResolvedValue({ state: 'needs_login', account: null });
    render(<CredentialBanner />);
    await screen.findByText('Your Midway sign-in has expired.');
    fireEvent.click(screen.getByTestId('credential-banner-action'));
    await act(async () => { await vi.advanceTimersByTimeAsync(5 * 60_000 + 6_000); });
    const n = mockVerify.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(mockVerify.mock.calls.length).toBe(n);
    expect(screen.getByTestId('credential-banner-action')).toHaveTextContent('credentialBanner.checkAgain');
  });

  it.each([
    [{ kind: 'sign_in' }, 'aws-account', 'credentialBanner.signInAgain'],
    [{ kind: 'settings', tab: 'ai-models' }, 'ai-models', 'credentialBanner.openSettings'],
  ])('action %o opens settings', async (action, tab, label) => {
    mockGet.mockResolvedValue(remedy(action));
    setAuth('expired');
    render(<CredentialBanner />);
    await screen.findByText('Your Midway sign-in has expired.');
    const btn = screen.getByTestId('credential-banner-action');
    expect(btn).toHaveTextContent(label);
    const ev = listen('swarm:open-settings');
    fireEvent.click(btn);
    ev.stop();
    expect(ev.seen).toEqual([{ tab }]);
  });

  it('outside the desktop app a terminal action copies the command instead', async () => {
    desktop.on = false;
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    setAuth('expired');
    render(<CredentialBanner />);
    await screen.findByText('Your Midway sign-in has expired.');
    fireEvent.click(screen.getByTestId('credential-banner-action'));
    // copyToClipboard (utils/clipboard) tries the Tauri command first, then the
    // Clipboard API: the write lands after a microtask, not in the click
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('mwinit -f'));
  });

  it('× hides it until the credentials work again', async () => {
    setAuth('expired');
    const { rerender } = render(<CredentialBanner />);
    await screen.findByText('Your Midway sign-in has expired.');
    fireEvent.click(screen.getByTestId('credential-banner-dismiss'));
    expect(screen.queryByTestId('credential-banner')).toBeNull();
    setAuth('valid');
    rerender(<CredentialBanner />);
    setAuth('expired');
    rerender(<CredentialBanner />);
    expect(await screen.findByTestId('credential-banner')).toBeInTheDocument();
  });

  it('with no answer from the account endpoint it still shows a line that opens the account page', async () => {
    mockGet.mockRejectedValue(new Error('500'));
    setAuth('expired');
    render(<CredentialBanner />);
    expect(await screen.findByText('credentialBanner.generic')).toBeInTheDocument();
    const ev = listen('swarm:open-settings');
    fireEvent.click(screen.getByTestId('credential-banner-action'));
    ev.stop();
    expect(ev.seen).toEqual([{ tab: 'aws-account' }]);
  });
});

describe('CredentialBanner — after the review', () => {
  it('Settings opened while waiting: shown again, it checks at once and keeps going', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    setAuth('expired');
    mockVerify.mockResolvedValue({ state: 'needs_login', account: null });
    const { rerender } = render(<CredentialBanner />);
    await screen.findByText('Your Midway sign-in has expired.');
    fireEvent.click(screen.getByTestId('credential-banner-action'));
    rerender(<CredentialBanner hidden />);
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(mockVerify).not.toHaveBeenCalled();             // no asking while hidden
    rerender(<CredentialBanner />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(mockVerify).toHaveBeenCalledTimes(1);           // at once when shown again
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(mockVerify).toHaveBeenCalledTimes(2);
  });

  it('a later expiry starts fresh, not in the old wait', async () => {
    setAuth('expired');
    const { rerender } = render(<CredentialBanner />);
    await screen.findByText('Your Midway sign-in has expired.');
    fireEvent.click(screen.getByTestId('credential-banner-action'));
    expect(screen.getByTestId('credential-banner')).toHaveTextContent('credentialBanner.waiting');
    setAuth('valid'); rerender(<CredentialBanner />);
    setAuth('expired'); rerender(<CredentialBanner />);
    expect(await screen.findByTestId('credential-banner-action')).toHaveTextContent('credentialBanner.inTerminal');
  });

  it('opening Settings right after success does not leave the success line forever', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    setAuth('expired');
    mockVerify.mockResolvedValue({ state: 'connected', account: { account_id: '1' } });
    const { rerender } = render(<CredentialBanner />);
    await screen.findByText('Your Midway sign-in has expired.');
    fireEvent.click(screen.getByTestId('credential-banner-action'));
    fireEvent.click(await screen.findByTestId('credential-banner-check'));
    await screen.findByTestId('credential-banner-done');
    rerender(<CredentialBanner hidden />);
    await act(async () => { await vi.advanceTimersByTimeAsync(2_500); });
    rerender(<CredentialBanner />);
    expect(screen.queryByTestId('credential-banner-done')).toBeNull();
  });

  it('"check now" during a running check sends no second request', async () => {
    setAuth('expired');
    mockVerify.mockImplementation(() => new Promise(() => {}));
    render(<CredentialBanner />);
    await screen.findByText('Your Midway sign-in has expired.');
    fireEvent.click(screen.getByTestId('credential-banner-action'));
    fireEvent.click(screen.getByTestId('credential-banner-check'));
    fireEvent.click(screen.getByTestId('credential-banner-check'));
    expect(mockVerify).toHaveBeenCalledTimes(1);
  });

  it('a sign_in action asks the account page to go straight into the sign-in', async () => {
    mockGet.mockResolvedValue(remedy({ kind: 'sign_in' }));
    setAuth('expired');
    render(<CredentialBanner />);
    await screen.findByText('Your Midway sign-in has expired.');
    expect(screen.queryByTestId('credential-banner-settings')).toBeNull();   // no second same button
    fireEvent.click(screen.getByTestId('credential-banner-action'));
    const { takeSignInRequest } = await import('./settingsEvents');
    expect(takeSignInRequest()).toBe(true);
    expect(takeSignInRequest()).toBe(false);
  });
});

describe('CredentialBanner — asked by a chat error card (FIX_SIGN_IN_EVENT)', () => {
  it('before health has noticed: verifies, shows itself and types the command at once', async () => {
    setAuth('valid');                                  // health's next sample is not in yet
    mockVerify.mockResolvedValueOnce({ state: 'needs_login', account: null, ...remedy(terminal) });
    const { TERMINAL_RUN_EVENT, fixSignIn } = await import('./settingsEvents');
    const runs = listen(TERMINAL_RUN_EVENT);
    render(<CredentialBanner />);
    await act(async () => { fixSignIn(); });
    await waitFor(() => expect(runs.seen).toEqual([{ command: 'mwinit -f', title: 'Midway sign-in' }]));
    expect(await screen.findByTestId('credential-banner')).toHaveTextContent('credentialBanner.waiting');
    runs.stop();
  });

  it('already signed in again: says so and runs nothing', async () => {
    setAuth('valid');
    mockVerify.mockResolvedValueOnce({ state: 'connected', account: { account_id: '533267412361' }, remediation: null });
    const { TERMINAL_RUN_EVENT, fixSignIn } = await import('./settingsEvents');
    const runs = listen(TERMINAL_RUN_EVENT);
    render(<CredentialBanner />);
    await act(async () => { fixSignIn(); });
    expect(await screen.findByTestId('credential-banner-done')).toHaveTextContent('533267412361');
    expect(runs.seen).toEqual([]);
    runs.stop();
  });
});

describe('CredentialBanner — asked, after the review', () => {
  it('a settings action opens the page and leaves no line behind', async () => {
    setAuth('valid');
    mockVerify.mockResolvedValueOnce({ state: 'needs_login', account: null, ...remedy({ kind: 'settings', tab: 'ai-models' }) });
    const { OPEN_SETTINGS_EVENT, fixSignIn } = await import('./settingsEvents');
    const opened = listen(OPEN_SETTINGS_EVENT);
    const { container } = render(<CredentialBanner />);
    await act(async () => { fixSignIn(); });
    await waitFor(() => expect(opened.seen).toEqual([{ tab: 'ai-models' }]));
    expect(container.querySelector('[data-testid="credential-banner"]')).toBeNull();
    opened.stop();
  });

  it('already waiting on the terminal: a second click checks, it does not type again', async () => {
    setAuth('valid');
    mockVerify.mockResolvedValue({ state: 'needs_login', account: null, ...remedy(terminal) });
    const { TERMINAL_RUN_EVENT, fixSignIn } = await import('./settingsEvents');
    const runs = listen(TERMINAL_RUN_EVENT);
    render(<CredentialBanner />);
    await act(async () => { fixSignIn(); });
    await waitFor(() => expect(runs.seen.length).toBe(1));
    await screen.findByText(/credentialBanner\.waiting\|/);
    await act(async () => { fixSignIn(); });
    await waitFor(() => expect(mockVerify).toHaveBeenCalledTimes(2));
    expect(runs.seen.length).toBe(1);
    expect(mockGet).toHaveBeenCalledTimes(1);   // the click's own read; the line does not read again over it
    runs.stop();
  });
});

describe('CredentialBanner — words and the Bedrock-key case', () => {
  it('an entry with no translation shows the backend text', async () => {
    setAuth('expired');
    mockGet.mockResolvedValue({ remediation: { id: 'not_connected', message: 'No AWS account is connected yet.',
      fix_text: 'Connect one…', settings_tab: 'aws-account', action: { kind: 'settings', tab: 'aws-account' } } });
    render(<CredentialBanner />);
    // the mock t returns the key unchanged = no translation
    expect(await screen.findByTestId('credential-banner')).toHaveTextContent('Connect one…');
  });

  it('chat on a Bedrock API key: the card button opens LLM Models, nothing is verified', async () => {
    setAuth('valid');
    mockGet.mockResolvedValue({ chat_via: 'bedrock_key', remediation: null });
    const { OPEN_SETTINGS_EVENT, fixSignIn } = await import('./settingsEvents');
    const opened = listen(OPEN_SETTINGS_EVENT);
    render(<CredentialBanner />);
    await act(async () => { fixSignIn(); });
    await waitFor(() => expect(opened.seen).toEqual([{ tab: 'ai-models' }]));
    expect(mockVerify).not.toHaveBeenCalled();
    opened.stop();
  });
});
