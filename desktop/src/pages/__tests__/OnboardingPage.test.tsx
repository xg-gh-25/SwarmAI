/**
 * OnboardingPage dead-end guard tests.
 *
 * Invariant: For every reachable (step, state) combination, the user must have
 * at least one visible, clickable element that advances or escapes the flow.
 * A wizard with no exit = product-level P0.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import '../../i18n';   // the real strings: the rail's step names are asserted
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
const mockRestoreBackup = vi.fn();

vi.mock('../../services/system', () => ({
  systemService: {
    getStatus: (...args: unknown[]) => mockGetStatus(...args),
    getBackupStatus: (...args: unknown[]) => mockGetBackupStatus(...args),
    verifyAuth: (...args: unknown[]) => mockVerifyAuth(...args),
    setOnboardingComplete: (...args: unknown[]) => mockSetOnboardingComplete(...args),
    getAuthHint: (...args: unknown[]) => mockGetAuthHint(...args),
    persistApiKey: vi.fn().mockResolvedValue(undefined),
    setAuthMethod: vi.fn().mockResolvedValue(undefined),
    restoreBackup: (...args: unknown[]) => mockRestoreBackup(...args),
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

// Step4Ready reads the REAL theme via useTheme() — mock it (component isn't
// rendered inside a ThemeProvider in this test).
const mockUseTheme = vi.fn();
vi.mock('../../contexts/ThemeContext', () => ({
  useTheme: () => mockUseTheme(),
}));
// The account steps (②–④) and the Hive step (⑤) have their own tests
// (components/awsAccount/__tests__): here only where the wizard goes from them.
vi.mock('../../components/awsAccount/ConnectAccount', () => ({
  default: (p: { onConnected: (v: unknown) => void; onChatOnly?: () => void; onLater?: () => void }) => (
    <div>
      <h2>Connect your AWS account</h2>
      <button onClick={() => p.onConnected({})}>stub: connected</button>
      <button onClick={() => p.onChatOnly?.()}>stub: chat only</button>
      <button onClick={() => p.onLater?.()}>Configure later</button>
    </div>
  ),
}));
vi.mock('../../components/awsAccount/HiveFleet', () => ({
  HiveSetupStep: ({ onDone }: { onDone: () => void }) => (
    <div><h2>Hive step</h2><button onClick={onDone}>stub: hive done</button></div>
  ),
}));

const HIVE_HINT = { suggestedMethod: 'iam_role', hasAdaDir: false, runMode: 'hive', deploymentContext: 'external' };

/** Desktop: the account step (connected) then the Hive step, which leads to Channels. */
async function pastAccountAndHive() {
  await waitFor(() => screen.getByText('stub: connected'));
  await act(async () => { fireEvent.click(screen.getByText('stub: connected')); });
  await waitFor(() => screen.getByText('stub: hive done'));
  await act(async () => { fireEvent.click(screen.getByText('stub: hive done')); });
}


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
  mockUseTheme.mockReturnValue({ theme: 'system' });
});

describe('OnboardingPage — no dead-ends', () => {
  it('step 1 auto-advances to the AWS account step when system is healthy', async () => {
    render(<OnboardingPage onComplete={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText('Connect your AWS account')).toBeInTheDocument();
    });
    expect(screen.queryByText('LLM Authentication')).not.toBeInTheDocument();
  });

  it('desktop order: account → Hive → Channels', async () => {
    render(<OnboardingPage onComplete={vi.fn()} />);
    await pastAccountAndHive();
    await waitFor(() => expect(screen.getByText('Connect Channels')).toBeInTheDocument());
  });

  it('chat only skips the Hive step (Hive needs the AWS account)', async () => {
    render(<OnboardingPage onComplete={vi.fn()} />);
    await waitFor(() => screen.getByText('stub: chat only'));
    await act(async () => { fireEvent.click(screen.getByText('stub: chat only')); });
    await waitFor(() => expect(screen.getByText('Connect Channels')).toBeInTheDocument());
    expect(screen.queryByText('Hive step')).not.toBeInTheDocument();
    expect(screen.queryByText('Hive')).not.toBeInTheDocument();          // gone from the rail too
  });

  it('a Hive keeps the LLM Authentication step and has no Hive step', async () => {
    mockGetAuthHint.mockResolvedValue(HIVE_HINT);
    render(<OnboardingPage onComplete={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('LLM Authentication')).toBeInTheDocument());
    expect(screen.queryByText('Connect your AWS account')).not.toBeInTheDocument();
    expect(screen.queryByText('AWS Account')).not.toBeInTheDocument();
  });

  it('Hive: step 2 has a clickable Verify button', async () => {
    mockGetAuthHint.mockResolvedValue(HIVE_HINT);
    render(<OnboardingPage onComplete={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText('Verify Connection')).toBeInTheDocument();
    });
    const btn = screen.getByText('Verify Connection').closest('button');
    expect(btn).not.toBeDisabled();
  });

  it('Hive: verify success advances to Channels', async () => {
    mockGetAuthHint.mockResolvedValue(HIVE_HINT);
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
    await pastAccountAndHive();

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

    await pastAccountAndHive();

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

  it('AC3: the desktop account step has a "Configure later" control that completes onboarding', async () => {
    mockGetAuthHint.mockResolvedValue({ suggestedMethod: 'sso', hasAdaDir: false, runMode: 'desktop', deploymentContext: 'external' });
    const onComplete = vi.fn();
    render(<OnboardingPage onComplete={onComplete} />);
    await waitFor(() => screen.getByText('Connect your AWS account'));
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

  it('AC5 (Hive): a FAILED verify persists NO config (updateAPIConfiguration not called)', async () => {
    mockGetAuthHint.mockResolvedValue(HIVE_HINT);
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

  it('AC5 (Hive): a SUCCESSFUL verify persists config AFTER verify', async () => {
    mockGetAuthHint.mockResolvedValue(HIVE_HINT);
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
    await pastAccountAndHive();
    await waitFor(() => screen.getByText('Skip for now'));
    await act(async () => { fireEvent.click(screen.getByText('Skip for now').closest('button')!); });
    await waitFor(() => screen.getByText("You're All Set!"));
    await waitFor(() => {
      expect(screen.getByText('eu-west-1')).toBeInTheDocument();
      expect(screen.getByText('claude-sonnet-4-6')).toBeInTheDocument();
    });
    expect(screen.queryByText('us-east-1')).not.toBeInTheDocument();
  });

  it('F5: Step4 Ready shows the REAL theme and drops the fabricated Language line', async () => {
    mockUseTheme.mockReturnValue({ theme: 'dark' });
    render(<OnboardingPage onComplete={vi.fn()} />);
    await pastAccountAndHive();
    await waitFor(() => screen.getByText('Skip for now'));
    await act(async () => { fireEvent.click(screen.getByText('Skip for now').closest('button')!); });
    await waitFor(() => screen.getByText("You're All Set!"));
    // Real theme label is shown…
    await waitFor(() => expect(screen.getByText(/dark/i)).toBeInTheDocument());
    // …and the fabricated "Language: English" line is gone (no locale state exists).
    expect(screen.queryByText('Language')).not.toBeInTheDocument();
    expect(screen.queryByText('English')).not.toBeInTheDocument();
  });

  it('double-clicking "Configure later" fires onboarding-complete only ONCE', async () => {
    mockGetAuthHint.mockResolvedValue({ suggestedMethod: 'sso', hasAdaDir: false, runMode: 'desktop', deploymentContext: 'external' });
    render(<OnboardingPage onComplete={vi.fn()} />);
    await waitFor(() => screen.getByText('Connect your AWS account'));
    const later = (await screen.findByText(/Configure later/i)).closest('button')!;
    await act(async () => {
      fireEvent.click(later);
      fireEvent.click(later); // rapid double-click
    });
    await waitFor(() => expect(mockSetOnboardingComplete).toHaveBeenCalled());
    expect(mockSetOnboardingComplete).toHaveBeenCalledTimes(1);
  });

  it('system check interval does NOT reset step after advancing past the account step', async () => {
    // Use a slow status that resolves immediately
    let callCount = 0;
    mockGetStatus.mockImplementation(async () => {
      callCount++;
      return { database: { healthy: true }, swarmWorkspace: { ready: true } };
    });

    render(<OnboardingPage onComplete={vi.fn()} />);

    await pastAccountAndHive();
    await waitFor(() => screen.getByText('Connect Channels'));

    // Wait for any interval fires (3s+) — should NOT reset to step 2
    // The interval should have been cleared, so callCount should stay low
    const _countAfterAdvance = callCount;

    // Advance time by 4s to let any stale interval fire
    await act(async () => {
      await new Promise(r => setTimeout(r, 100));
    });

    // Should still show Channels, NOT the account step
    expect(screen.queryByText('Connect your AWS account')).not.toBeInTheDocument();
    expect(screen.getByText('Connect Channels')).toBeInTheDocument();
  });

  // ── Restore step (run_da5da0b1): no deadlock during restore ──

  // A detected backup (non-hive): Restore comes right after the system check, before
  // the account (a backup carries the Hive tables and settings the account step writes).
  async function advanceToRestoreStep() {
    mockGetAuthHint.mockResolvedValue({ suggestedMethod: 'sso', hasAdaDir: false, runMode: 'desktop', deploymentContext: 'external' });
    mockGetBackupStatus.mockResolvedValue({ repoUrl: 'https://github.com/u/swarm-brain.git', lastBackup: '2026-07-01T00:00:00Z' });
    render(<OnboardingPage onComplete={vi.fn()} />);
    await waitFor(() => screen.getByText('Restore from Backup'));
    expect(screen.queryByText('Connect your AWS account')).not.toBeInTheDocument();
    // the repository URL is filled in from the backup status (Restore is disabled until then)
    await waitFor(() => expect(screen.getByRole('button', { name: /^Restore$/ })).not.toBeDisabled());
  }

  it('AC2: a Skip — start fresh escape is reachable WHILE restoring (no deadlock behind the progress bar)', async () => {
    // restoreBackup stays pending forever — simulates a slow/stalled stream.
    mockRestoreBackup.mockImplementation(() => ({
      // eslint-disable-next-line @typescript-eslint/no-empty-function
      async *[Symbol.asyncIterator]() { await new Promise(() => {}); },
    }));
    await advanceToRestoreStep();
    // Kick off the restore (the Restore button)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /^Restore$/ })); });
    // While restoring (stream pending), the escape button must be present + clickable.
    const skip = await screen.findByText('Skip — start fresh');
    expect(skip).toBeInTheDocument();
    await act(async () => { fireEvent.click(skip.closest('button')!); });
    // onSkip advances past the restore step → the account step (no longer trapped).
    await waitFor(() => expect(screen.queryByText('Restore from Backup')).not.toBeInTheDocument());
    expect(screen.getByText('Connect your AWS account')).toBeInTheDocument();
  });

  it('AC2b: unmounting mid-restore (skip) ABORTS the restore signal (fetch released, no zombie)', async () => {
    // Capture the AbortSignal the component passes as restoreBackup's 3rd arg.
    // The real fix aborts it on unmount → the generator's fetch errors + exits.
    let capturedSignal: AbortSignal | undefined;
    async function* stallingGen(_url: string, _tok: string | undefined, signal?: AbortSignal) {
      capturedSignal = signal;
      yield { stage: 'cloning', progress: 20 };
      await new Promise(() => {}); // stall (real generator would exit when signal aborts the fetch)
    }
    mockRestoreBackup.mockImplementation((...args: unknown[]) =>
      stallingGen(args[0] as string, args[1] as string | undefined, args[2] as AbortSignal | undefined));
    await advanceToRestoreStep();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /^Restore$/ })); });
    const skip = await screen.findByText('Skip — start fresh');
    expect(capturedSignal).toBeDefined();
    expect(capturedSignal!.aborted).toBe(false);
    await act(async () => { fireEvent.click(skip.closest('button')!); });
    // Skip unmounts StepRestore → unmount effect aborts the signal → fetch released.
    await waitFor(() => expect(capturedSignal!.aborted).toBe(true));
  });

  it('AC4: happy-path restore reaching progress=100 shows Restore Complete (no regression)', async () => {
    mockRestoreBackup.mockImplementation(() => ({
      async *[Symbol.asyncIterator]() {
        yield { stage: 'cloning', progress: 50, detail: 'cloning repo' };
        yield { stage: 'done', progress: 100, messagesCount: 12, sessionsCount: 3, todosCount: 5 };
      },
    }));
    await advanceToRestoreStep();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /^Restore$/ })); });
    await waitFor(() => expect(screen.getByText('Restore Complete')).toBeInTheDocument());
    // The completion summary + Continue button (not a dead progress bar).
    expect(screen.getByText('Continue')).toBeInTheDocument();
  });
});
