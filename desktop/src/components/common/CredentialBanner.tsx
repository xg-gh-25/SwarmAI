/**
 * The sign-in problem line at the top of the chat (design
 * docs/design/2026-11-01-settings-account-merge.md §4).
 *
 * Shows ONLY on health's definitive `auth === 'expired'` (valid / unknown → nothing). It is
 * one in-flow line under the chat tab bar, never a floating box over the tabs or over
 * Settings: ChatPage hides it while Settings is open, because the account page shows the
 * same state there.
 *
 * What it says and its one button come from the backend (`GET /api/aws-account`
 * `remediation`, core/auth_remediation + core/aws_account): for the account in use and,
 * without one, for the legacy sign-in method, so there is one wording for everyone.
 *   - action `terminal`: "Sign in in terminal" opens a new SwarmAI terminal with the
 *     command typed in, no Enter (the person presses it, then the PIN / security key).
 *     The line then waits: it asks the backend to verify again, one request at a time,
 *     5 s after each answer, for up to 5 minutes, and says when it worked. While
 *     Settings is open it stops asking; shown again, it asks at once and goes on.
 *   - action `sign_in`: opens the account page straight into the in-app sign-in.
 *   - action `settings`: opens that settings tab.
 * Outside the desktop app (no terminal) a terminal action becomes "Copy command".
 * × hides it until the credentials work again; then every state resets for the next expiry.
 *
 * Context-free on purpose (no layout / overlay hooks): ChatPage passes `hidden`.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useHealth } from '../../contexts/HealthContext';
import { awsAccountService, type AwsAccountView, type RemediationAction } from '../../services/awsAccount';
import { isDesktop } from '../../services/tauri';
import { markTerminalRun, openSettings, openSignIn, runInTerminal } from './settingsEvents';

type Remedy = NonNullable<AwsAccountView['remediation']>;

const POLL_MS = 5_000;
const WAIT_MAX_MS = 5 * 60_000;

const keyOf = (r: Remedy | null) => (r ? `${r.message}|${JSON.stringify(r.action ?? null)}` : '');

export default function CredentialBanner({ hidden = false }: { hidden?: boolean }) {
  const { t } = useTranslation();
  const { health } = useHealth();
  const expired = health.auth === 'expired';
  const [remedy, setRemedy] = useState<Remedy | null>(null);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const [phase, setPhase] = useState<'idle' | 'waiting' | 'stopped' | 'done'>('idle');
  const [doneAccount, setDoneAccount] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const waitStarted = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);       // the next verify
  const doneTimer = useRef<ReturnType<typeof setTimeout> | null>(null);   // the success line
  const inFlight = useRef(false);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;   // StrictMode mounts twice: set again on every mount
    return () => {
      alive.current = false;
      if (timer.current) clearTimeout(timer.current);
      if (doneTimer.current) clearTimeout(doneTimer.current);
    };
  }, []);

  // What to say: read once each time the credentials turn expired. When they work
  // again, everything resets (a dismissal, a wait, a stop), so the next expiry starts fresh.
  useEffect(() => {
    if (!expired) {
      setDismissed(null);
      setPhase((p) => (p === 'done' ? p : 'idle'));
      return;
    }
    let cancelled = false;
    awsAccountService.get()
      .then((v) => { if (!cancelled) setRemedy(v.remediation ?? null); })
      .catch(() => { /* nothing to say beyond the generic line */ });
    return () => { cancelled = true; };
  }, [expired]);

  const stopTimer = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  }, []);

  // one verify at a time; the next one 5 s after the answer, for up to 5 minutes
  const check = useCallback(async () => {
    if (inFlight.current) return;
    stopTimer();
    inFlight.current = true;
    try {
      const v = await awsAccountService.verify();
      if (!alive.current) return;
      if (v.state === 'connected') {
        setDoneAccount(v.account?.account_id ?? null);
        setDismissed(keyOf(remedy));   // health may say "expired" until its next sample
        setPhase('done');
        if (doneTimer.current) clearTimeout(doneTimer.current);
        doneTimer.current = setTimeout(() => { if (alive.current) setPhase('idle'); }, 2_000);
        return;
      }
    } catch { /* not yet */ } finally {
      inFlight.current = false;
    }
    if (!alive.current) return;
    if (Date.now() - waitStarted.current >= WAIT_MAX_MS) { setPhase('stopped'); return; }
    timer.current = setTimeout(() => void check(), POLL_MS);
  }, [stopTimer, remedy]);

  const startWaiting = () => {
    waitStarted.current = Date.now();
    setPhase('waiting');
    stopTimer();
    timer.current = setTimeout(() => void check(), POLL_MS);
  };

  // Hidden (Settings open) or valid again: stop asking. Shown again while still
  // waiting: ask again right away, then keep going.
  useEffect(() => {
    if (hidden || !expired) { stopTimer(); return; }
    if (phase === 'waiting' && !timer.current && !inFlight.current) void check();
  }, [hidden, expired, phase, stopTimer, check]);

  const act = (a: RemediationAction | undefined) => {
    if (!a || a.kind === 'settings') { openSettings(a?.tab ?? remedy?.settings_tab ?? 'aws-account'); return; }
    if (a.kind === 'sign_in') { openSignIn(); return; }
    if (!isDesktop()) {
      void navigator.clipboard?.writeText(a.command).then(() => setCopied(true)).catch(() => {});
      return;
    }
    runInTerminal(a.command, a.title);
    markTerminalRun();
    startWaiting();
  };

  if (hidden) return null;
  if (phase === 'done') {
    return (
      <div role="status" data-testid="credential-banner-done"
        className="shrink-0 flex items-center gap-3 px-4 py-2 text-sm border-b border-green-500/30 bg-green-500/10">
        <span className="w-2 h-2 rounded-full bg-green-500 shrink-0" />
        <span className="text-[var(--color-text)]">
          {doneAccount ? t('credentialBanner.done', { account: doneAccount }) : t('credentialBanner.doneNoAccount')}
        </span>
        <span className="text-[var(--color-text-secondary)]">{t('credentialBanner.resend')}</span>
      </div>
    );
  }
  if (!expired) return null;
  if (dismissed !== null && dismissed === keyOf(remedy)) return null;

  const action = remedy?.action;
  const terminal = action?.kind === 'terminal' ? action : null;
  const waiting = phase === 'waiting' && terminal;
  const label = !action || action.kind === 'settings' ? t('credentialBanner.openSettings')
    : action.kind === 'sign_in' ? t('credentialBanner.signInAgain')
    : isDesktop() ? t('credentialBanner.inTerminal') : copied ? t('credentialBanner.copied') : t('credentialBanner.copy');

  return (
    <div role="alert" aria-live="assertive" data-testid="credential-banner"
      className={`shrink-0 flex items-center gap-3 px-4 py-2 text-sm border-b ${waiting
        ? 'border-blue-500/30 bg-blue-500/10' : 'border-[var(--color-warning)]/30 bg-[var(--color-warning)]/10'}`}>
      <span className={`w-2 h-2 rounded-full shrink-0 ${waiting ? 'bg-blue-400' : 'bg-[var(--color-warning)]'}`} />
      <span className="flex-1 min-w-0 truncate">
        {waiting ? (
          <>
            <span className="text-[var(--color-text)]">{t('credentialBanner.waiting', { command: terminal.command })}</span>
            <span className="ml-2 text-[var(--color-text-secondary)]">{t('credentialBanner.waitingSub')}</span>
          </>
        ) : (
          <>
            <span className="text-[var(--color-text)]">{remedy?.message ?? t('credentialBanner.generic')}</span>
            <span className="ml-2 text-[var(--color-text-secondary)]" title={remedy?.fix_text}>
              {terminal ? t('credentialBanner.blocked') : remedy?.fix_text}
            </span>
          </>
        )}
      </span>
      {waiting ? (
        <button type="button" className="btn-sm btn-secondary shrink-0" data-testid="credential-banner-check"
          onClick={() => void check()}>{t('credentialBanner.checkNow')}</button>
      ) : (
        <>
          <button type="button" className="btn-sm btn-primary shrink-0" data-testid="credential-banner-action"
            onClick={() => (phase === 'stopped' && terminal ? startWaiting() : act(action))}>
            {phase === 'stopped' && terminal ? t('credentialBanner.checkAgain') : label}
          </button>
          {action?.kind === 'terminal' && (
            <button type="button" className="shrink-0 underline text-[var(--color-text-secondary)]"
              data-testid="credential-banner-settings" onClick={() => openSettings('aws-account')}>
              {t('credentialBanner.accountSettings')}
            </button>
          )}
        </>
      )}
      <button type="button" aria-label={t('credentialBanner.dismiss')} data-testid="credential-banner-dismiss"
        className="shrink-0 px-1 text-[var(--color-text-muted)]"
        onClick={() => { stopTimer(); setPhase('idle'); setDismissed(keyOf(remedy)); }}>×</button>
    </div>
  );
}
