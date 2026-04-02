/**
 * First-run onboarding page.
 *
 * 4-step flow: System Check -> LLM Auth (blocking) -> Channels (optional) -> Ready.
 * Shown when onboardingComplete is false in system status.
 */
import { useState, useEffect, useCallback } from 'react';
import { systemService } from '../services/system';
import { channelsService } from '../services/channels';
import type { Channel } from '../types';
import AuthConfigPanel from '../components/settings/AuthConfigPanel';
import ChannelConfigForm from '../components/settings/ChannelConfigForm';

interface OnboardingPageProps {
  onComplete: () => void;
}

export default function OnboardingPage({ onComplete }: OnboardingPageProps) {
  const [step, setStep] = useState(1);
  const [systemOk, setSystemOk] = useState(false);
  const [authVerified, setAuthVerified] = useState(false);

  // Step 1: Auto-check system
  useEffect(() => {
    const check = async () => {
      try {
        const status = await systemService.getStatus();
        if (status.database.healthy && status.swarmWorkspace.ready) {
          setSystemOk(true);
          setStep(2); // Auto-advance
        }
      } catch {
        // Backend not ready yet
      }
    };
    check();
  }, []);

  // Step 4: Complete
  const handleComplete = useCallback(async () => {
    try {
      await systemService.setOnboardingComplete();
      onComplete();
    } catch (e) {
      console.error('Failed to set onboarding complete:', e);
    }
  }, [onComplete]);

  const steps = [
    { num: 1, title: 'System Check', done: systemOk },
    { num: 2, title: 'Authentication', done: authVerified },
    { num: 3, title: 'Channels', done: step > 3 },
    { num: 4, title: 'Ready', done: false },
  ];

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
            <div key={s.num} className="flex items-center gap-3">
              <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium ${
                s.done ? 'bg-green-500 text-white' :
                step === s.num ? 'bg-[var(--color-primary)] text-white' :
                'bg-[var(--color-card)] text-[var(--color-text-muted)] border border-[var(--color-border)]'
              }`}>
                {s.done ? (
                  <span className="material-symbols-outlined text-sm">check</span>
                ) : s.num}
              </div>
              <span className={`text-sm ${
                step === s.num ? 'text-[var(--color-text)] font-medium' : 'text-[var(--color-text-muted)]'
              }`}>{s.title}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 p-12 max-w-2xl">
        {step === 1 && <Step1SystemCheck ok={systemOk} />}
        {step === 2 && (
          <Step2Auth
            onVerified={() => { setAuthVerified(true); setStep(3); }}
          />
        )}
        {step === 3 && (
          <Step3Channels
            onContinue={() => setStep(4)}
            onSkip={() => setStep(4)}
          />
        )}
        {step === 4 && <Step4Ready onStart={handleComplete} />}
      </div>
    </div>
  );
}

// ── Step 1: System Check ──

function Step1SystemCheck({ ok }: { ok: boolean }) {
  return (
    <div>
      <h2 className="text-2xl font-bold text-[var(--color-text)] mb-2">System Check</h2>
      <p className="text-[var(--color-text-muted)] mb-6">Verifying your environment...</p>
      {ok ? (
        <div className="p-4 bg-green-500/10 border border-green-500/20 rounded-lg flex items-center gap-3">
          <span className="material-symbols-outlined text-green-400">check_circle</span>
          <span className="text-green-400">Backend, Database, and Workspace are ready.</span>
        </div>
      ) : (
        <div className="p-4 bg-[var(--color-card)] rounded-lg flex items-center gap-3">
          <span className="material-symbols-outlined animate-spin text-[var(--color-text-muted)]">progress_activity</span>
          <span className="text-[var(--color-text-muted)]">Checking system components...</span>
        </div>
      )}
    </div>
  );
}

// ── Step 2: LLM Authentication ──

function Step2Auth({ onVerified }: { onVerified: () => void }) {
  return (
    <div>
      <h2 className="text-2xl font-bold text-[var(--color-text)] mb-2">LLM Authentication</h2>
      <p className="text-[var(--color-text-muted)] mb-6">
        Connect to Claude so Swarm can help you. This is the only required step.
      </p>
      <AuthConfigPanel mode="onboarding" onVerifySuccess={onVerified} />
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
                className="px-3 py-1 text-sm bg-[var(--color-bg)] text-[var(--color-text-muted)] rounded hover:text-[var(--color-text)] transition-colors"
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
            className="px-6 py-2 text-sm bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80"
          >
            Continue
          </button>
        )}
      </div>
    </div>
  );
}

// ── Step 4: Ready ──

function Step4Ready({ onStart }: { onStart: () => void }) {
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
            <p className="text-[var(--color-text)] font-mono">claude-opus-4-6</p>
          </div>
          <div>
            <span className="text-[var(--color-text-muted)]">Region</span>
            <p className="text-[var(--color-text)] font-mono">us-east-1</p>
          </div>
          <div>
            <span className="text-[var(--color-text-muted)]">Theme</span>
            <p className="text-[var(--color-text)]">System</p>
          </div>
          <div>
            <span className="text-[var(--color-text-muted)]">Language</span>
            <p className="text-[var(--color-text)]">English</p>
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
        onClick={onStart}
        className="w-full px-6 py-3 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 font-medium text-lg"
      >
        Start Using SwarmAI
      </button>
    </div>
  );
}
