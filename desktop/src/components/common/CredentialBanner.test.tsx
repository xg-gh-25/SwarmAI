/**
 * Tests for CredentialBanner — renders ONLY on auth==='expired', with
 * METHOD-AWARE remediation (never hardcodes mwinit for non-ADA) + an in-app
 * "Open Settings" deep-link button.
 */
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import CredentialBanner from './CredentialBanner';
import type { HealthState, AuthStatus } from '../../types';

vi.mock('../../contexts/HealthContext', () => ({ useHealth: vi.fn() }));
import { useHealth } from '../../contexts/HealthContext';

const mockGetAuthHint = vi.fn();
vi.mock('../../services/system', () => ({
  systemService: { getAuthHint: (...a: unknown[]) => mockGetAuthHint(...a) },
}));

// NOTE: CredentialBanner deliberately does NOT import LayoutContext — it is
// mounted at the app root (outside LayoutProvider); calling useLayout() there
// crashed the app at boot. The "Open Settings" deep-link is a window event
// (see the deep-link test below), so there is no useLayout mock here.

function setAuth(auth?: AuthStatus) {
  const health: HealthState = {
    status: 'connected', auth, lastCheckedAt: Date.now(), consecutiveFailures: 0,
  };
  (useHealth as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    health, triggerHealthCheck: vi.fn(),
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGetAuthHint.mockResolvedValue({ deploymentContext: 'internal', suggestedMethod: 'ada' });
});

describe('CredentialBanner', () => {
  it('shows a banner when auth is expired', async () => {
    setAuth('expired');
    render(<CredentialBanner />);
    expect(await screen.findByRole('alert')).toBeInTheDocument();
  });

  it('ADA method: shows ada/mwinit remediation', async () => {
    // A CONFIGURED ada user whose creds expired (hasAdaDir true → configured).
    mockGetAuthHint.mockResolvedValue({ deploymentContext: 'internal', suggestedMethod: 'ada', hasAdaDir: true });
    setAuth('expired');
    render(<CredentialBanner />);
    await screen.findByRole('alert');
    const txt = screen.getByRole('alert').textContent!.toLowerCase();
    expect(txt.includes('ada') || txt.includes('mwinit')).toBe(true);
  });

  it('SSO method: says aws sso login, NOT mwinit', async () => {
    // A CONFIGURED sso user whose session expired (hasSsoCache true → configured).
    mockGetAuthHint.mockResolvedValue({ deploymentContext: 'external', suggestedMethod: 'sso', hasSsoCache: true });
    setAuth('expired');
    render(<CredentialBanner />);
    await screen.findByRole('alert');
    const txt = screen.getByRole('alert').textContent!.toLowerCase();
    expect(txt).toContain('aws sso login');
    expect(txt).not.toContain('mwinit');
  });

  it('never-configured (no creds detected): says CONFIGURE, not expired/refresh', async () => {
    // A first-time user who skipped setup has NoCredentialsError → auth=expired,
    // but no ada/sso/apikey signal. They must be told to CONFIGURE, not that a
    // session they never had "expired" or needs "refresh" (F2).
    mockGetAuthHint.mockResolvedValue({
      deploymentContext: 'external', suggestedMethod: 'sso',
      hasAdaDir: false, hasSsoCache: false, hasApiKey: false,
    });
    setAuth('expired');
    render(<CredentialBanner />);
    await screen.findByRole('alert');
    const txt = screen.getByRole('alert').textContent!.toLowerCase();
    expect(txt).toMatch(/configure|set up|set-up|get started|no credentials/);
    expect(txt).not.toContain('expired');
    expect(txt).not.toContain('refresh');
    expect(txt).not.toContain('aws sso login');
  });

  it('Hive iam_role: never shows "configure" (instance role IS configured)', async () => {
    // On Hive, has_ada_dir/has_sso_cache are forced false and has_api_key is
    // false, but the IAM instance role IS a valid credential. A false-driven
    // "No credentials configured / set up" banner would be a dead-end (there's
    // no in-app setup for an instance role). Must show the IAM-policy fix instead.
    mockGetAuthHint.mockResolvedValue({
      deploymentContext: 'external', suggestedMethod: 'iam_role', runMode: 'hive',
      hasAdaDir: false, hasSsoCache: false, hasApiKey: false,
    });
    setAuth('expired');
    render(<CredentialBanner />);
    await screen.findByRole('alert');
    const txt = screen.getByRole('alert').textContent!.toLowerCase();
    expect(txt).not.toMatch(/no credentials configured|set up authentication/);
    expect(txt).toContain('bedrock:invokemodel');
  });

  it('creds present but rejected (hasSsoCache): keeps the refresh wording', async () => {
    // Regression guard: a real expired SSO session (cache exists) still says refresh.
    mockGetAuthHint.mockResolvedValue({
      deploymentContext: 'external', suggestedMethod: 'sso',
      hasAdaDir: false, hasSsoCache: true, hasApiKey: false,
    });
    setAuth('expired');
    render(<CredentialBanner />);
    await screen.findByRole('alert');
    const txt = screen.getByRole('alert').textContent!.toLowerCase();
    expect(txt).toContain('aws sso login');
  });

  it('Open Settings button dispatches the swarm:open-settings deep-link event (no useLayout)', async () => {
    // Regression: the banner is mounted OUTSIDE LayoutProvider (app root), so it
    // must NOT call setSettingsTab via useLayout (that threw at boot). It fires a
    // window event the app shell handles instead. This test also guards the wire
    // contract: event name + { tab: 'ai-models' } detail.
    mockGetAuthHint.mockResolvedValue({ deploymentContext: 'external', suggestedMethod: 'sso' });
    setAuth('expired');
    const onOpen = vi.fn();
    window.addEventListener('swarm:open-settings', onOpen);
    try {
      render(<CredentialBanner />);
      const btn = await screen.findByRole('button', { name: /settings/i });
      await act(async () => { fireEvent.click(btn); });
      expect(onOpen).toHaveBeenCalledTimes(1);
      const evt = onOpen.mock.calls[0][0] as CustomEvent<{ tab?: string }>;
      expect(evt.detail?.tab).toBe('ai-models');
    } finally {
      window.removeEventListener('swarm:open-settings', onOpen);
    }
  });

  it('renders nothing when auth is valid', () => {
    setAuth('valid');
    const { container } = render(<CredentialBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when auth is unknown (fail-open)', () => {
    setAuth('unknown');
    const { container } = render(<CredentialBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when auth is undefined (pre-first-poll)', () => {
    setAuth(undefined);
    const { container } = render(<CredentialBanner />);
    expect(container).toBeEmptyDOMElement();
  });
});
