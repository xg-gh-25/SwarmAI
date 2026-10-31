/**
 * First-run onboarding page (design docs/design/2026-10-31-swarmai-aws-account.md §6.1).
 *
 * Desktop: System Check -> [Restore (if backup)] -> SwarmAI AWS account (②–④,
 *   ConnectAccount) -> Hive (⑤, HiveSetupStep) -> Channels (optional) -> Ready.
 *   Restore comes before the account: a backup carries the Hive tables and settings,
 *   restored after the account it would overwrite what was just set up. "Chat only"
 *   skips the Hive step (Hive needs the AWS account).
 * Hive (cloud instance): System Check -> LLM Auth -> Channels -> Ready (the account is
 *   the instance's own role; there are no Hives to create from a Hive).
 * Every step can be left: "Configure later" completes onboarding, CredentialBanner and
 * the account page say what is still missing. Shown when onboardingComplete is false.
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import { systemService, RestoreEvent } from '../services/system';
import { settingsService } from '../services/settings';
import { channelsService } from '../services/channels';
import type { Channel } from '../types';
import AuthConfigPanel from '../components/settings/AuthConfigPanel';
import ChannelConfigForm from '../components/settings/ChannelConfigForm';
import ConnectAccount from '../components/awsAccount/ConnectAccount';
import { HiveSetupStep } from '../components/awsAccount/HiveFleet';
import { useTheme } from '../contexts/ThemeContext';

/**
 * Pre-resolve fallback for the model name shown during onboarding.
 *
 * ⚠️ MUST equal `model_registry.FLAGSHIP_MODEL` in the backend. The frontend
 * cannot import Python, so this is the one place a BINDING TEST is the right
 * tool instead of derivation — `backend/tests/test_model_registry.py`
 * (TestFrontendFallbackBinding) reads this literal and fails if it drifts from
 * the backend flagship. It is exported so that test has a stable anchor.
 *
 * Overwritten by the real value from `getAPIConfiguration()` on mount; it only
 * governs Step 4's first paint and the case where that fetch fails or returns
 * no default model — where showing a model the user is not running is the
 * actual bug. ("Configure later" skips Step 4 entirely, so it never shows this.)
 */
export const FALLBACK_FLAGSHIP_MODEL = 'claude-opus-5-5';

interface OnboardingPageProps {
  onComplete: () => void;
}

// After this many consecutive failed/not-ready polls (~60s at 3s each), Step1
// stops pretending to spin forever and surfaces a failure card + an escape.
const SYSTEM_CHECK_FAILURE_THRESHOLD = 20;

type StepId = 'system' | 'restore' | 'auth' | 'account' | 'hive' | 'channels' | 'ready';

const STEP_TITLE: Record<StepId, string> = {
  system: 'System Check', restore: 'Restore', auth: 'Authentication', account: 'AWS Account',
  hive: 'Hive', channels: 'Channels', ready: 'Ready',
};

export default function OnboardingPage({ onComplete }: OnboardingPageProps) {
  const [step, setStep] = useState<StepId>('system');
  const [systemOk, setSystemOk] = useState(false);
  const [systemCheckFailed, setSystemCheckFailed] = useState(false);
  const [hasBackup, setHasBackup] = useState(false);
  const [isHive, setIsHive] = useState(false);
  const [chatOnly, setChatOnly] = useState(false);
  const [done, setDone] = useState<Set<StepId>>(new Set());
  const skippingRef = useRef(false);

  // Step 1: Auto-check system with retry every 3s until backend is ready.
  // If it never becomes ready, surface a failure state after a bounded number
  // of attempts instead of spinning forever (a partial-init backend must not
  // trap the user on an infinite spinner — the gate now routes them here).
  useEffect(() => {
    let cancelled = false;
    let done = false;
    let attempts = 0;
    let interval: ReturnType<typeof setInterval> | null = null;
    const stop = () => { if (interval) { clearInterval(interval); interval = null; } };
    const check = async () => {
      if (done) return; // Already succeeded — no-op for late interval fires
      try {
        const status = await systemService.getStatus();
        if (cancelled) return;
        if (status.database.healthy && status.swarmWorkspace.ready) {
          done = true;
          setSystemOk(true);
          setSystemCheckFailed(false);
          // Check if backup exists (fresh install might have a backup to restore)
          // Skip on Hive — cloud instances don't restore from user backups
          let hive = false;
          let backup = false;
          try {
            const hint = await systemService.getAuthHint();
            hive = hint.runMode === 'hive';
            if (!hive) {
              const b = await systemService.getBackupStatus();
              backup = !!(b.repoUrl && b.lastBackup);
            }
          } catch { /* no backup configured — skip */ }
          if (cancelled) return;
          setIsHive(hive);
          setHasBackup(backup);
          setStep(backup ? 'restore' : hive ? 'auth' : 'account'); // auto-advance
          stop(); // Stop polling — system is confirmed ready, step 1 done
          return;
        }
        // Reachable-but-not-ready counts as a failed attempt too.
        attempts += 1;
      } catch {
        // Backend not reachable yet — count the attempt.
        attempts += 1;
      }
      if (!cancelled && !done && attempts >= SYSTEM_CHECK_FAILURE_THRESHOLD) {
        setSystemCheckFailed(true); // surface the failure card + escape (does not stop polling — may still recover)
      }
    };
    check();
    interval = setInterval(check, 3000);
    return () => { cancelled = true; stop(); };
  }, []);


  // Step 4: Complete — always proceed even if backend flag fails to persist
  const handleComplete = useCallback(async () => {
    try {
      await systemService.setOnboardingComplete();
    } catch (e) {
      console.error('Failed to set onboarding complete:', e);
      // Still proceed — user shouldn't be blocked by a flag persistence failure.
      // Worst case: they see onboarding again next launch (not a dead-end).
    }
    onComplete();
  }, [onComplete]);

  // Escape hatch shared by Step1 failure card and Step2 "Configure later":
  // mark onboarding complete and enter the app. CredentialBanner surfaces any
  // unverified/expired auth state afterward, so this is never a dead-end.
  const handleSkipSetup = useCallback(() => {
    if (skippingRef.current) return; // guard against double-click → duplicate onboarding-complete PUT
    skippingRef.current = true;
    void handleComplete();
  }, [handleComplete]);

  const order: StepId[] = [
    'system',
    ...(hasBackup ? ['restore' as const] : []),
    ...(isHive ? ['auth' as const] : ['account' as const, ...(chatOnly ? [] : ['hive' as const])]),
    'channels', 'ready',
  ];
  const advance = (from: StepId) => {
    setDone((d) => new Set(d).add(from));
    const i = order.indexOf(from);
    setStep(order[Math.min(order.length - 1, i + 1)]);
  };
  const steps = order.map((id, i) => ({ id, num: i + 1, title: STEP_TITLE[id],
    done: id === 'system' ? systemOk : done.has(id) }));

  return (
    <div className="min-h-screen bg-[var(--color-bg)] flex">
      {/* Left rail: step indicator */}
      <div className="w-64 border-r border-[var(--color-border)] p-8 flex flex-col">
        <div className="mb-8">
          <h1 className="text-xl font-bold text-[var(--color-text)]">SwarmAI</h1>
          <p className="text-sm text-[var(--color-text-muted)] mt-1">Setup Wizard</p>
        </div>
        <div className="space-y-4">
          {steps.map((s) => (
            <div key={s.id} className="flex items-center gap-3">
              <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium ${
                s.done ? 'bg-green-500 text-white' :
                step === s.id ? 'bg-[var(--color-primary)] text-white' :
                'bg-[var(--color-card)] text-[var(--color-text-muted)] border border-[var(--color-border)]'
              }`}>
                {s.done ? (
                  <span className="material-symbols-outlined icon-xs">check</span>
                ) : s.num}
              </div>
              <span className={`text-sm ${
                step === s.id ? 'text-[var(--color-text)] font-medium' : 'text-[var(--color-text-muted)]'
              }`}>{s.title}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 p-12 max-w-2xl">
        {step === 'system' && <Step1SystemCheck ok={systemOk} failed={systemCheckFailed} onSkip={handleSkipSetup} />}
        {step === 'restore' && (
          <StepRestore
            onRestored={() => advance('restore')}
            onSkip={() => advance('restore')}
          />
        )}
        {step === 'auth' && (
          <Step2Auth
            onVerified={() => advance('auth')}
            onSkip={handleSkipSetup}
          />
        )}
        {step === 'account' && (
          <ConnectAccount
            mode="onboarding"
            onConnected={() => advance('account')}
            onChatOnly={() => {
              // chat works with a key; Hive needs the AWS account, so its step is
              // dropped (the account page offers it once an account is connected)
              setChatOnly(true);
              setDone((d) => new Set(d).add('account'));
              setStep('channels');
            }}
            onLater={handleSkipSetup}
          />
        )}
        {step === 'hive' && <HiveSetupStep onDone={() => advance('hive')} onBack={() => setStep('account')} />}
        {step === 'channels' && (
          <Step3Channels
            onContinue={() => advance('channels')}
            onSkip={() => advance('channels')}
          />
        )}
        {step === 'ready' && <Step4Ready onStart={handleComplete} />}
      </div>
    </div>
  );
}

// ── Step 1: System Check ──

function Step1SystemCheck({ ok, failed, onSkip }: { ok: boolean; failed: boolean; onSkip: () => void }) {
  return (
    <div>
      <h2 className="text-2xl font-bold text-[var(--color-text)] mb-2">System Check</h2>
      <p className="text-[var(--color-text-muted)] mb-6">Verifying your environment...</p>
      {ok ? (
        <div className="p-4 bg-green-500/10 border border-green-500/20 rounded-lg flex items-center gap-3">
          <span className="material-symbols-outlined icon-md text-green-400">check_circle</span>
          <span className="text-green-400">Backend, Database, and Workspace are ready.</span>
        </div>
      ) : failed ? (
        <div className="space-y-4">
          <div className="p-4 bg-red-500/10 border border-red-500/20 rounded-lg">
            <div className="flex items-center gap-3 mb-1">
              <span className="material-symbols-outlined icon-md text-red-400">error</span>
              <span className="text-red-400 font-medium">System check is taking longer than expected</span>
            </div>
            <p className="text-xs text-[var(--color-text-muted)]">
              The backend hasn't reported ready. It may still be starting up — this will keep retrying.
              You can wait, restart the app, or proceed now and finish setup later.
            </p>
          </div>
          <button
            onClick={onSkip}
            className="btn-lg btn-primary"
          >
            Continue anyway
          </button>
        </div>
      ) : (
        <div className="p-4 bg-[var(--color-card)] rounded-lg flex items-center gap-3">
          <span className="material-symbols-outlined icon-sm animate-spin text-[var(--color-text-muted)]">progress_activity</span>
          <span className="text-[var(--color-text-muted)]">Checking system components...</span>
        </div>
      )}
    </div>
  );
}

// ── Step 2: LLM Authentication ──

function Step2Auth({ onVerified, onSkip }: { onVerified: () => void; onSkip: () => void }) {
  const [failCount, setFailCount] = useState(0);
  const [isHive, setIsHive] = useState(false);

  // Detect Hive mode for escape-hatch wording
  useEffect(() => {
    systemService.getAuthHint()
      .then((hint) => { if (hint.runMode === 'hive') setIsHive(true); })
      .catch(() => {});
  }, []);

  return (
    <div>
      <h2 className="text-2xl font-bold text-[var(--color-text)] mb-2">LLM Authentication</h2>
      <p className="text-[var(--color-text-muted)] mb-6">
        Connect to Claude so Swarm can help you. This is the only required step.
      </p>
      <AuthConfigPanel
        mode="onboarding"
        onVerifySuccess={onVerified}
        onVerifyFail={() => setFailCount(c => c + 1)}
      />
      {/* Escape hatch — ALWAYS available (desktop + Hive): a user who can't reach
          AWS right now must not be trapped in the wizard. "Configure later"
          completes onboarding; CredentialBanner surfaces the unverified state. */}
      <div className="mt-4">
        <button
          onClick={onSkip}
          className="px-4 py-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
        >
          {isHive && failCount >= 2 ? 'Skip — fix IAM permissions later' : 'Configure later'}
        </button>
      </div>
    </div>
  );
}

// ── Step 3: Channels ──

function Step3Channels({ onContinue, onSkip }: { onContinue: () => void; onSkip: () => void }) {
  const [showSlack, setShowSlack] = useState(false);
  const [slackDone, setSlackDone] = useState(false);
  const [existingSlack, setExistingSlack] = useState<Channel | null>(null);

  // Load existing channel configs so tokens are pre-filled
  useEffect(() => {
    channelsService.list()
      .then((channels) => {
        for (const ch of channels) {
          if (ch.channelType === 'slack') {
            setExistingSlack(ch);
            setSlackDone(true);
          }
        }
      })
      .catch(() => {});
  }, []);

  return (
    <div>
      <h2 className="text-2xl font-bold text-[var(--color-text)] mb-2">Connect Channels</h2>
      <p className="text-[var(--color-text-muted)] mb-6">
        Talk to Swarm from Slack — not just the desktop app. This is optional.
      </p>
      <div className="space-y-4 mb-6">
        {/* Slack */}
        <div className="bg-[var(--color-card)] rounded-lg p-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <span className="text-xl">💬</span>
              <div>
                <span className="text-[var(--color-text)] font-medium">Slack</span>
                {slackDone && <span className="ml-2 text-green-400 text-xs">Connected</span>}
              </div>
            </div>
            {!slackDone && (
              <button
                onClick={() => setShowSlack(!showSlack)}
                className="btn-md btn-secondary transition-colors"
              >
                {showSlack ? 'Cancel' : 'Set Up'}
              </button>
            )}
          </div>
          {showSlack && !slackDone && (
            <div className="mt-4 pt-4 border-t border-[var(--color-border)]">
              <ChannelConfigForm
                channelType="slack"
                existingConfig={existingSlack}
                compact
                onSave={() => { setSlackDone(true); setShowSlack(false); }}
                onCancel={() => setShowSlack(false)}
              />
            </div>
          )}
        </div>

      </div>

      <div className="flex gap-3">
        <button
          onClick={onSkip}
          className="px-6 py-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
        >
          Skip for now
        </button>
        {slackDone && (
          <button
            onClick={onContinue}
            className="btn-lg btn-primary"
          >
            Continue
          </button>
        )}
      </div>
    </div>
  );
}

// ── Step: Restore from Backup ──

function StepRestore({ onRestored, onSkip }: { onRestored: () => void; onSkip: () => void }) {
  const [repoUrl, setRepoUrl] = useState('');
  const [token, setToken] = useState('');
  const [restoring, setRestoring] = useState(false);
  const [events, setEvents] = useState<RestoreEvent[]>([]);
  const [done, setDone] = useState(false);
  // AbortController for the in-flight restore. On unmount (e.g. the user hits
  // "Skip — start fresh", which unmounts this component) we abort it — that
  // errors the fetch inside restoreBackup, so the reader.read() the generator
  // is parked on rejects and the generator exits (its finally releases the
  // stream). A fire-and-forget for-await loop is NOT closed by React unmount,
  // and .return() on a generator parked at `await` cannot abort it — only an
  // external signal can. Without this, the fetch lingers to the 90s stall-guard.
  const abortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);

  // Pre-fill from backup status
  useEffect(() => {
    systemService.getBackupStatus().then(s => {
      if (s.repoUrl) setRepoUrl(s.repoUrl);
    }).catch(() => {});
  }, []);

  // On unmount: abort any in-flight restore so the fetch/stream is released.
  useEffect(() => {
    return () => {
      mountedRef.current = false;
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, []);

  const handleRestore = async () => {
    if (!repoUrl) return;
    setRestoring(true);
    setEvents([]);
    const ac = new AbortController();
    abortRef.current = ac;
    try {
      for await (const event of systemService.restoreBackup(repoUrl, token || undefined, ac.signal)) {
        if (!mountedRef.current) break; // component gone — stop consuming
        setEvents(prev => [...prev, event]);
        if (event.error) break;
        if (event.progress === 100) setDone(true);
      }
    } catch { /* handled via events */ }
    finally {
      abortRef.current = null;
      if (mountedRef.current) setRestoring(false);
    }
  };

  if (done) {
    const last = events[events.length - 1];
    return (
      <div>
        <h2 className="text-2xl font-bold text-[var(--color-text)] mb-2">Restore Complete</h2>
        <div className="p-4 bg-green-500/10 border border-green-500/20 rounded-lg mb-6">
          <span className="material-symbols-outlined icon-md text-green-400 align-middle mr-2">check_circle</span>
          <span className="text-green-400">
            {last?.messagesCount ?? 0} conversations, {last?.sessionsCount ?? 0} sessions,
            {last?.todosCount ?? 0} todos restored.
          </span>
        </div>
        <button onClick={onRestored}
          className="btn-lg btn-primary hover:opacity-90">
          Continue
        </button>
      </div>
    );
  }

  return (
    <div>
      <h2 className="text-2xl font-bold text-[var(--color-text)] mb-2">Restore from Backup</h2>
      <p className="text-[var(--color-text-muted)] mb-6">
        A backup was detected. Restore your memory, knowledge, and conversation history from your private GitHub repository.
      </p>

      {!restoring && events.length === 0 && (
        <div className="space-y-4 mb-6">
          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">Repository URL</label>
            <input type="text" value={repoUrl} onChange={e => setRepoUrl(e.target.value)}
              placeholder="https://github.com/user/swarm-brain.git"
              className="w-full px-3 py-2 text-sm rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text)]" />
          </div>
          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">GitHub Token (optional if gh auth configured)</label>
            <input type="password" value={token} onChange={e => setToken(e.target.value)}
              placeholder="ghp_..."
              className="w-full px-3 py-2 text-sm rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text)]" />
          </div>
          <div className="flex gap-3">
            <button onClick={handleRestore} disabled={!repoUrl}
              className="btn-lg btn-primary hover:opacity-90 disabled:opacity-50">
              Restore
            </button>
            <button onClick={onSkip}
              className="px-6 py-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
              Skip — start fresh
            </button>
          </div>
        </div>
      )}

      {(restoring || events.length > 0) && (
        <div className="space-y-3">
          {events.map((e, i) => (
            <div key={i} className="flex items-center gap-2 text-sm">
              <span className={`w-2 h-2 rounded-full ${e.error ? 'bg-red-500' : e.progress === 100 ? 'bg-green-500' : 'bg-blue-500 animate-pulse'}`} />
              <span className="text-[var(--color-text-muted)]">{e.stage}</span>
              <span className="text-[var(--color-text)]">{e.detail || e.error || ''}</span>
            </div>
          ))}
          {restoring && (
            <>
              <div className="w-full bg-[var(--color-bg-secondary)] rounded-full h-2">
                <div className="bg-[var(--color-primary)] h-2 rounded-full transition-all duration-300"
                  style={{ width: `${events[events.length - 1]?.progress ?? 0}%` }} />
              </div>
              {/* Escape hatch DURING restore — never trap the user behind a
                  progress bar if the stream is slow or silently stalled. The
                  stall-guard (system.ts) bounds a true hang at 90s, but the
                  user can bail immediately. onSkip unmounts StepRestore, and
                  the unmount effect .return()s the active generator → its
                  finally aborts the fetch (no zombie stream). */}
              <div className="pt-1">
                <button onClick={onSkip}
                  className="px-4 py-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
                  Skip — start fresh
                </button>
              </div>
            </>
          )}
          {/* Show escape hatch when restore failed (not restoring, has error) */}
          {!restoring && events.some(e => e.error) && (
            <div className="flex gap-3 pt-2">
              <button onClick={() => { setEvents([]); }}
                className="btn-lg btn-primary hover:opacity-90">
                Try Again
              </button>
              <button onClick={onSkip}
                className="px-4 py-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
                Skip — start fresh
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Step 4: Ready ──

function Step4Ready({ onStart }: { onStart: () => void }) {
  const [starting, setStarting] = useState(false);
  const { theme } = useTheme();  // real theme, not a hardcoded "System" string
  // Show the ACTUAL configured model + region (persisted by Step2 on verify),
  // not hardcoded strings that lie when the user picked a different region.
  // The fallback is only the pre-resolve paint (overwritten by the API call
  // below) and the fetch-failed case; it must still match the backend flagship
  // or the user sees a model they are not running.
  const [model, setModel] = useState<string>(FALLBACK_FLAGSHIP_MODEL);
  const [region, setRegion] = useState<string>('us-east-1');

  useEffect(() => {
    settingsService.getAPIConfiguration()
      .then((config) => {
        if (config.defaultModel) setModel(config.defaultModel);
        if (config.awsRegion) setRegion(config.awsRegion);
      })
      .catch(() => { /* keep sensible defaults if config fetch fails */ });
  }, []);

  return (
    <div>
      <h2 className="text-2xl font-bold text-[var(--color-text)] mb-2">You're All Set!</h2>
      <p className="text-[var(--color-text-muted)] mb-6">
        SwarmAI is ready. Here are your defaults — change anything anytime.
      </p>

      <div className="bg-[var(--color-card)] rounded-lg p-6 mb-6">
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <span className="text-[var(--color-text-muted)]">Model</span>
            <p className="text-[var(--color-text)] font-mono">{model}</p>
          </div>
          <div>
            <span className="text-[var(--color-text-muted)]">Region</span>
            <p className="text-[var(--color-text)] font-mono">{region}</p>
          </div>
          <div>
            <span className="text-[var(--color-text-muted)]">Theme</span>
            <p className="text-[var(--color-text)] capitalize">{theme}</p>
          </div>
        </div>
      </div>

      <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg p-4 mb-8">
        <p className="text-sm text-[var(--color-text)]">
          <strong>Tip:</strong> After setup, just tell Swarm what you need. All settings can be changed through natural conversation.
        </p>
        <p className="text-xs text-[var(--color-text-muted)] mt-2">
          "Change model to sonnet" &middot; "Enable Playwright MCP" &middot; "Set timezone to UTC+8"
        </p>
      </div>

      <button
        onClick={() => { if (!starting) { setStarting(true); onStart(); } }}
        disabled={starting}
        className="btn-lg btn-primary w-full disabled:opacity-50"
      >
        {starting ? 'Starting...' : 'Start Using SwarmAI'}
      </button>
    </div>
  );
}
