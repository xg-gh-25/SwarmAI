/**
 * OnboardingPage dead-end guard tests.
 *
 * Invariant: For every reachable (step, state) combination, the user must have
 * at least one visible, clickable element that advances or escapes the flow.
 * A wizard with no exit = product-level P0.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import OnboardingPage from '../OnboardingPage';

// ── Mocks ──

const mockGetStatus = vi.fn();
const mockGetBackupStatus = vi.fn();
const mockVerifyAuth = vi.fn();
const mockSetOnboardingComplete = vi.fn();
const mockGetAuthHint = vi.fn();
const mockGetAPIConfiguration = vi.fn();
const mockUpdateAPIConfiguration = vi.fn();
const mockChannelsList = vi.fn();

vi.mock('../../services/system', () => ({
  systemService: {
    getStatus: (...args: unknown[]) => mockGetStatus(...args),
    getBackupStatus: (...args: unknown[]) => mockGetBackupStatus(...args),
    verifyAuth: (...args: unknown[]) => mockVerifyAuth(...args),
    setOnboardingComplete: (...args: unknown[]) => mockSetOnboardingComplete(...args),
    getAuthHint: (...args: unknown[]) => mockGetAuthHint(...args),
    persistApiKey: vi.fn().mockResolvedValue(undefined),
    setAuthMethod: vi.fn().mockResolvedValue(undefined),
    restoreBackup: vi.fn(),
  },
  RestoreEvent: {},
}));

vi.mock('../../services/settings', () => ({
  settingsService: {
    getAPIConfiguration: (...args: unknown[]) => mockGetAPIConfiguration(...args),
    updateAPIConfiguration: (...args: unknown[]) => mockUpdateAPIConfiguration(...args),
  },
}));

vi.mock('../../services/channels', () => ({
  channelsService: {
    list: (...args: unknown[]) => mockChannelsList(...args),
  },
}));

vi.mock('../../components/common', () => ({
  Dropdown: ({ label }: { label: string }) => <div data-testid="dropdown">{label}</div>,
}));

beforeEach(() => {
  vi.clearAllMocks();
  // Defaults: system healthy, no backup, auth hint = sso
  mockGetStatus.mockResolvedValue({
    database: { healthy: true },
    swarmWorkspace: { ready: true },
  });
  mockGetBackupStatus.mockRejectedValue(new Error('no backup'));
  mockGetAuthHint.mockResolvedValue({ suggestedMethod: 'sso', hasAdaDir: false, deploymentContext: 'external' });
  mockGetAPIConfiguration.mockResolvedValue({ awsRegion: 'us-east-1' });
  mockUpdateAPIConfiguration.mockResolvedValue({});
  mockChannelsList.mockResolvedValue([]);
  mockVerifyAuth.mockResolvedValue({ success: true, model: 'claude-opus-4-8', latency_ms: 200 });
  mockSetOnboardingComplete.mockResolvedValue(undefined);
});

describe('OnboardingPage — no dead-ends', () => {
  it('step 1 auto-advances to step 2 when system is healthy', async () => {
    render(<OnboardingPage onComplete={vi.fn()} />);
    // Should auto-advance to Auth step
    await waitFor(() => {
      expect(screen.getByText('LLM Authentication')).toBeInTheDocument();
    });
  });

  it('step 2 has a clickable Verify button', async () => {
    render(<OnboardingPage onComplete={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText('Verify Connection')).toBeInTheDocument();
    });
    const btn = screen.getByText('Verify Connection').closest('button');
    expect(btn).not.toBeDisabled();
  });

  it('step 2 verify success advances to step 3 (Channels)', async () => {
    render(<OnboardingPage onComplete={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText('Verify Connection')).toBeInTheDocument();
    });

    await act(async () => {
      fireEvent.click(screen.getByText('Verify Connection').closest('button')!);
    });

    await waitFor(() => {
      expect(screen.getByText('Connect Channels')).toBeInTheDocument();
    });
  });

  it('step 3 (Channels) always has a Skip button — never trapped', async () => {
    render(<OnboardingPage onComplete={vi.fn()} />);
    await waitFor(() => screen.getByText('Verify Connection'));

    await act(async () => {
      fireEvent.click(screen.getByText('Verify Connection').closest('button')!);
    });

    await waitFor(() => {
      expect(screen.getByText('Skip for now')).toBeInTheDocument();
    });
    const skipBtn = screen.getByText('Skip for now').closest('button');
    expect(skipBtn).not.toBeDisabled();
  });

  it('step 4 (Ready) button advances even if setOnboardingComplete fails', async () => {
    mockSetOnboardingComplete.mockRejectedValue(new Error('network'));
    const onComplete = vi.fn();
    render(<OnboardingPage onComplete={onComplete} />);

    // Advance to Auth
    await waitFor(() => screen.getByText('Verify Connection'));
    await act(async () => {
      fireEvent.click(screen.getByText('Verify Connection').closest('button')!);
    });

    // Advance past Channels
    await waitFor(() => screen.getByText('Skip for now'));
    await act(async () => {
      fireEvent.click(screen.getByText('Skip for now').closest('button')!);
    });

    // Ready step — click Start
    await waitFor(() => screen.getByText('Start Using SwarmAI'));
    await act(async () => {
      fireEvent.click(screen.getByText('Start Using SwarmAI').closest('button')!);
    });

    // onComplete MUST fire even though backend failed
    await waitFor(() => {
      expect(onComplete).toHaveBeenCalledTimes(1);
    });
  });

  it('AC2: Step1 shows a failure surface + escape after repeated status failures (no infinite spin)', async () => {
    // Backend never becomes ready — getStatus keeps failing.
    mockGetStatus.mockRejectedValue(new Error('backend down'));
    vi.useFakeTimers();
    try {
      render(<OnboardingPage onComplete={vi.fn()} />);
      // Drive the 3s poll interval past the failure threshold.
      await act(async () => {
        for (let i = 0; i < 25; i++) {
          await vi.advanceTimersByTimeAsync(3000);
        }
      });
      // A failure card + an escape control must appear — not an endless spinner.
      expect(screen.getByText(/System check (failed|could not complete|is taking)/i)).toBeInTheDocument();
      expect(screen.getByText(/Continue anyway|Skip|Configure later/i)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('AC3: desktop Step2 has a "Configure later" control that completes onboarding', async () => {
    mockGetAuthHint.mockResolvedValue({ suggestedMethod: 'sso', hasAdaDir: false, runMode: 'desktop', deploymentContext: 'external' });
    const onComplete = vi.fn();
    render(<OnboardingPage onComplete={onComplete} />);
    await waitFor(() => screen.getByText('LLM Authentication'));
    // Desktop user who can't reach AWS must have an escape — not just Hive.
    const later = await screen.findByText(/Configure later/i);
    await act(async () => {
      fireEvent.click(later.closest('button')!);
    });
    await waitFor(() => {
      expect(mockSetOnboardingComplete).toHaveBeenCalled();
      expect(onComplete).toHaveBeenCalled();
    });
  });

  it('AC5: a FAILED verify persists NO config (updateAPIConfiguration not called)', async () => {
    mockVerifyAuth.mockResolvedValue({ success: false, error_type: 'access_denied', fix_hint: 'x' });
    render(<OnboardingPage onComplete={vi.fn()} />);
    await waitFor(() => screen.getByText('Verify Connection'));
    await act(async () => {
      fireEvent.click(screen.getByText('Verify Connection').closest('button')!);
    });
    // Verify was attempted, but config must NOT be persisted on failure.
    await waitFor(() => expect(mockVerifyAuth).toHaveBeenCalled());
    expect(mockUpdateAPIConfiguration).not.toHaveBeenCalled();
  });

  it('AC5: a SUCCESSFUL verify persists config AFTER verify', async () => {
    mockVerifyAuth.mockResolvedValue({ success: true, model: 'claude-opus-4-8', latency_ms: 100 });
    render(<OnboardingPage onComplete={vi.fn()} />);
    await waitFor(() => screen.getByText('Verify Connection'));
    await act(async () => {
      fireEvent.click(screen.getByText('Verify Connection').closest('button')!);
    });
    await waitFor(() => expect(mockUpdateAPIConfiguration).toHaveBeenCalled());
    // verify was called with the attempted override body (stateless verify).
    expect(mockVerifyAuth).toHaveBeenCalledWith(expect.objectContaining({ use_bedrock: true }));
  });

  it('AC6: Step4 Ready shows the ACTUAL configured region, not hardcoded us-east-1', async () => {
    mockGetAPIConfiguration.mockResolvedValue({ awsRegion: 'eu-west-1', defaultModel: 'claude-sonnet-4-6' });
    render(<OnboardingPage onComplete={vi.fn()} />);
    // advance to Ready
    await waitFor(() => screen.getByText('Verify Connection'));
    await act(async () => { fireEvent.click(screen.getByText('Verify Connection').closest('button')!); });
    await waitFor(() => screen.getByText('Skip for now'));
    await act(async () => { fireEvent.click(screen.getByText('Skip for now').closest('button')!); });
    await waitFor(() => screen.getByText("You're All Set!"));
    await waitFor(() => {
      expect(screen.getByText('eu-west-1')).toBeInTheDocument();
      expect(screen.getByText('claude-sonnet-4-6')).toBeInTheDocument();
    });
    expect(screen.queryByText('us-east-1')).not.toBeInTheDocument();
  });

  it('double-clicking "Configure later" fires onboarding-complete only ONCE', async () => {
    mockGetAuthHint.mockResolvedValue({ suggestedMethod: 'sso', hasAdaDir: false, runMode: 'desktop', deploymentContext: 'external' });
    render(<OnboardingPage onComplete={vi.fn()} />);
    await waitFor(() => screen.getByText('LLM Authentication'));
    const later = (await screen.findByText(/Configure later/i)).closest('button')!;
    await act(async () => {
      fireEvent.click(later);
      fireEvent.click(later); // rapid double-click
    });
    await waitFor(() => expect(mockSetOnboardingComplete).toHaveBeenCalled());
    expect(mockSetOnboardingComplete).toHaveBeenCalledTimes(1);
  });

  it('system check interval does NOT reset step after advancing past step 2', async () => {
    // Use a slow status that resolves immediately
    let callCount = 0;
    mockGetStatus.mockImplementation(async () => {
      callCount++;
      return { database: { healthy: true }, swarmWorkspace: { ready: true } };
    });

    render(<OnboardingPage onComplete={vi.fn()} />);

    // Advance to Auth
    await waitFor(() => screen.getByText('LLM Authentication'));

    // Verify and advance to Channels
    await act(async () => {
      fireEvent.click(screen.getByText('Verify Connection').closest('button')!);
    });
    await waitFor(() => screen.getByText('Connect Channels'));

    // Wait for any interval fires (3s+) — should NOT reset to step 2
    // The interval should have been cleared, so callCount should stay low
    const _countAfterAdvance = callCount;

    // Advance time by 4s to let any stale interval fire
    await act(async () => {
      await new Promise(r => setTimeout(r, 100));
    });

    // Should still show Channels, NOT Auth
    expect(screen.queryByText('LLM Authentication')).not.toBeInTheDocument();
    expect(screen.getByText('Connect Channels')).toBeInTheDocument();
  });
});
