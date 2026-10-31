/**
 * Settings → SwarmAI Account (design docs/design/2026-10-31-swarmai-aws-account.md §6.5).
 * The page answers the decisions the person comes here for: does the account need them
 * (one status, the only thing that stands out; "Sign in again" when it expired), what
 * is missing to use a capability (only the gaps, with the permission template to hand
 * to the administrator), and how many Hives (− / +, the cost named before anything is
 * created). "Change account" runs the wizard's steps ②–④ in place (ConnectAccount);
 * the identity in use stays behind Details.
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { awsAccountService, type AwsAccountView, type Capability } from '../../services/awsAccount';
import { settingsService } from '../../services/settings';
import { systemService } from '../../services/system';
import ConnectAccount, { type ConnectStart } from '../awsAccount/ConnectAccount';
import { CopyTemplate, HiveFleetSection } from '../awsAccount/HiveFleet';

const CAPS = ['bedrock', 'hive', 'storage', 'voice'] as const;

const STATE_TONE: Record<AwsAccountView['state'], string> = {
  connected: 'text-green-500 border-green-500/40',
  needs_login: 'text-[var(--color-warning)] border-[var(--color-warning)]/40',
  unverified: 'text-[var(--color-warning)] border-[var(--color-warning)]/40',
  account_mismatch: 'text-[var(--color-error)] border-[var(--color-error)]/40',
  unconnected: 'text-[var(--color-text-muted)] border-[var(--color-border)]',
};

export default function AwsAccountTab() {
  const { t } = useTranslation();
  const [view, setView] = useState<AwsAccountView | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState<'verify' | 'check' | 'chat' | null>(null);
  const [connecting, setConnecting] = useState<ConnectStart | null>(null);

  const load = useCallback(async () => {
    try {
      setView(await awsAccountService.get());
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const run = async (kind: 'verify' | 'check') => {
    setBusy(kind);
    try {
      setView(kind === 'verify' ? await awsAccountService.verify() : await awsAccountService.check());
      setFailed(false);
    } catch {
      setFailed(true);
    } finally {
      setBusy(null);
    }
  };

  if (!view) {
    return (
      <div className="text-sm text-[var(--color-text-muted)]">
        {failed ? t('settings.awsAccount.loadFailed') : t('settings.awsAccount.loading')}
        {failed && (
          <button type="button" className="ml-3 underline" data-testid="aws-account-reload" onClick={() => void load()}>
            {t('settings.awsAccount.retry')}
          </button>
        )}
      </div>
    );
  }

  if (connecting) {
    const a = view.account;
    return (
      <div data-testid="aws-account-connect">
        <ConnectAccount mode="settings" start={connecting}
          prefill={{ accountId: a?.account_id, region: a?.region, ada: a?.ada, sso: a?.sso }}
          onConnected={(v) => { setView(v); setConnecting(null); }}
          onCancel={() => { setConnecting(null); void load(); }} />
      </div>
    );
  }

  const acct = view.account;
  // the sign-in this page can run again itself (ADA: mwinit in a terminal, then verify)
  const signInAgain: ConnectStart | null = view.state === 'needs_login'
    ? acct?.source === 'sso' ? 'sso' : acct?.source === 'login' ? 'login' : null : null;
  // exactly one emphasized action: what this state needs
  const primary: 'signIn' | 'verify' | 'change' | null = signInAgain ? 'signIn'
    : view.state === 'needs_login' && acct?.source === 'ada' ? 'verify'
    : !acct || view.state === 'needs_login' || view.state === 'account_mismatch' ? 'change' : null;
  const btn = (kind: 'verify' | 'change') => primary === kind ? 'btn-md btn-primary disabled:opacity-50'
    : 'px-3 py-1.5 text-sm rounded-md border border-[var(--color-border)] text-[var(--color-text)] hover:bg-[var(--color-card)] disabled:opacity-50';
  const chatOnAccount = async () => {
    setBusy('chat');
    try {
      const method = acct?.source === 'ada' ? 'ada' : 'sso';
      await settingsService.updateAPIConfiguration({ use_bedrock: true, auth_method: method });
      await systemService.setAuthMethod(method, acct?.context ?? undefined);
      setView(await awsAccountService.get());
      setFailed(false);
    } catch {
      setFailed(true);
    } finally {
      setBusy(null);
    }
  };
  const sourceLabel = acct?.source
    ? t(`settings.awsAccount.source.${acct.source}`, { name: acct.profile ?? '' })
      + (acct.ada?.role ? ` (${acct.ada.role})` : '')
    : null;
  const remedy = view.state === 'connected' ? null
    : view.state === 'needs_login' && acct?.source === 'ada'
      ? t('settings.awsAccount.remedy.needs_login_ada')
      : t(`settings.awsAccount.remedy.${view.state}`);
  const caps = view.capabilities;
  const gaps = caps
    ? CAPS.filter((c) => caps[c] && caps[c].status !== 'ok' && caps[c].status !== 'not_applicable')
    : [];

  return (
    <div className="space-y-8" data-testid="aws-account-tab">
      <section>
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-[var(--color-text)]">{t('settings.awsAccount.title')}</h2>
            <p className="text-sm text-[var(--color-text-muted)] mt-1">{t('settings.awsAccount.lead')}</p>
          </div>
          <span data-testid="aws-account-state"
            className={`shrink-0 text-xs font-medium px-2.5 py-1 rounded-full border ${STATE_TONE[view.state]}`}>
            {t(`settings.awsAccount.state.${view.state}`)}
          </span>
        </div>
        {acct && (
          <p className="mt-4 text-sm text-[var(--color-text)]">
            <span className="font-mono">{acct.account_id ?? '—'}</span>
            {sourceLabel && <span className="text-[var(--color-text-secondary)]"> · {sourceLabel}</span>}
            {acct.region && <span className="text-[var(--color-text-secondary)]"> · {acct.region}</span>}
          </p>
        )}
        {remedy && (
          <p data-testid="aws-account-remedy" className="mt-2 text-sm text-[var(--color-text-secondary)]">
            {remedy}{view.state_detail ? <span className="text-[var(--color-text-muted)]"> ({view.state_detail})</span> : null}
          </p>
        )}
        {!view.supported && (
          <p className="mt-2 text-sm text-[var(--color-text-muted)]">{t('settings.awsAccount.unsupported')}</p>
        )}
        {failed && (
          <p data-testid="aws-account-action-failed" className="mt-2 text-sm text-[var(--color-error)]">
            {t('settings.awsAccount.actionFailed')}
          </p>
        )}
        <div className="mt-4 flex flex-wrap gap-3">
          {signInAgain && (
            <button type="button" disabled={busy !== null} onClick={() => setConnecting(signInAgain)}
              data-testid="aws-account-sign-in" className="btn-md btn-primary disabled:opacity-50">
              {t('settings.awsAccount.signInAgain')}
            </button>
          )}
          <button type="button" disabled={busy !== null || !acct} onClick={() => run('verify')}
            className={btn('verify')}>
            {busy === 'verify' ? '…' : t('settings.awsAccount.verify')}
          </button>
          <button type="button" disabled={busy !== null || !acct} onClick={() => run('check')}
            className="px-3 py-1.5 text-sm rounded-md border border-[var(--color-border)] text-[var(--color-text)] hover:bg-[var(--color-card)] disabled:opacity-50">
            {busy === 'check' ? '…' : t('settings.awsAccount.check')}
          </button>
          <button type="button" disabled={busy !== null}
            onClick={() => setConnecting('pick')} data-testid="aws-account-change"
            className={btn('change')}>
            {acct ? t('settings.awsAccount.change') : t('settings.awsAccount.connect')}
          </button>
        </div>
        {acct && view.chat_via !== 'aws' && view.capabilities?.bedrock?.status === 'ok' && (
          <p className="mt-3 text-sm text-[var(--color-text-secondary)]" data-testid="aws-account-chat-switch">
            {t('settings.awsAccount.chatNotOnAccount', { via: t(`settings.awsAccount.chatViaValue.${view.chat_via}`) })}{' '}
            <button type="button" className="underline" disabled={busy !== null} onClick={() => void chatOnAccount()}>
              {t('settings.awsAccount.chatUseAccount')}
            </button>
          </p>
        )}
      </section>

      {acct && (
        <section>
          <h3 className="text-sm font-medium text-[var(--color-text)]">{t('settings.awsAccount.capabilities')}</h3>
          {!caps ? (
            <p className="mt-2 text-sm text-[var(--color-text-muted)]">{t('settings.awsAccount.notChecked')}</p>
          ) : gaps.length === 0 ? (
            <p data-testid="aws-account-all-ready" className="mt-2 text-sm text-[var(--color-text-secondary)]">
              {t('settings.awsAccount.allReady')}
            </p>
          ) : (
            <ul className="mt-2 space-y-2" data-testid="aws-account-gaps">
              {gaps.map((c) => <GapRow key={c} name={t(`settings.awsAccount.cap.${c}`)} cap={caps[c]} />)}
            </ul>
          )}
          {gaps.some((c) => (c === 'hive' || c === 'storage') && caps?.[c]?.status === 'missing') && (
            <p className="mt-2 text-xs text-[var(--color-text-secondary)]">
              {t('settings.awsAccount.giveTemplate')} <CopyTemplate />
            </p>
          )}
          {caps?.checked_at && (
            <p className="mt-2 text-xs text-[var(--color-text-muted)]">
              {t('settings.awsAccount.checkedAt', { time: new Date(caps.checked_at).toLocaleString() })}
            </p>
          )}
        </section>
      )}

      {acct && <HiveFleetSection view={view} onView={setView} />}

      <details className="text-sm">
        <summary className="cursor-pointer text-[var(--color-text-secondary)]">{t('settings.awsAccount.details')}</summary>
        <dl className="mt-3 grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 text-[var(--color-text-secondary)]">
          {acct?.caller_arn && (<><dt>{t('settings.awsAccount.callerArn')}</dt><dd className="font-mono break-all">{acct.caller_arn}</dd></>)}
          {acct?.verified_at && (<><dt>{t('settings.awsAccount.verifiedAt')}</dt><dd>{new Date(acct.verified_at).toLocaleString()}</dd></>)}
          <dt>{t('settings.awsAccount.chatVia')}</dt><dd>{t(`settings.awsAccount.chatViaValue.${view.chat_via}`)}</dd>
          <dt>{t('settings.awsAccount.profileFile')}</dt><dd className="font-mono break-all">{view.profile_file}</dd>
        </dl>
        {acct?.derived && <p className="mt-3 text-[var(--color-text-muted)]">{t('settings.awsAccount.derived')}</p>}
      </details>
    </div>
  );
}

function GapRow({ name, cap }: { name: string; cap: Capability }) {
  const { t } = useTranslation();
  return (
    <li className="text-sm">
      <span className="text-[var(--color-text)]">{name}</span>
      <span className="text-[var(--color-warning)]"> · {t(`settings.awsAccount.capStatus.${cap.status}`)}</span>
      {cap.detail && <span className="block text-[var(--color-text-muted)] break-words">{cap.detail}</span>}
    </li>
  );
}
