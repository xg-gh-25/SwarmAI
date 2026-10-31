/**
 * Settings → SwarmAI Account (read-only, design docs/design/2026-10-31-swarmai-aws-account.md
 * §6.5, step 1). The page answers three decisions: does the account need attention
 * (one status, the only thing that stands out), what is missing to use a capability
 * (only the gaps are listed; all ready = one line), and what identity is in use
 * (behind Details). Re-verify and the capability check are the only actions; changing
 * the account arrives with the setup step (design §9 step 4).
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { awsAccountService, type AwsAccountView, type Capability } from '../../services/awsAccount';

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
  const [busy, setBusy] = useState<'verify' | 'check' | null>(null);

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
      </div>
    );
  }

  const acct = view.account;
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
        <div className="mt-4 flex gap-3">
          <button type="button" disabled={busy !== null || !acct} onClick={() => run('verify')}
            className="px-3 py-1.5 text-sm rounded-md border border-[var(--color-border)] text-[var(--color-text)] hover:bg-[var(--color-card)] disabled:opacity-50">
            {busy === 'verify' ? '…' : t('settings.awsAccount.verify')}
          </button>
          <button type="button" disabled={busy !== null || !acct} onClick={() => run('check')}
            className="px-3 py-1.5 text-sm rounded-md border border-[var(--color-border)] text-[var(--color-text)] hover:bg-[var(--color-card)] disabled:opacity-50">
            {busy === 'check' ? '…' : t('settings.awsAccount.check')}
          </button>
        </div>
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
          {caps && CAPS.some((c) => caps[c]?.basis) && (
            <p className="mt-2 text-xs text-[var(--color-text-muted)]">{t('settings.awsAccount.readOnlyBasis')}</p>
          )}
          {caps?.checked_at && (
            <p className="mt-2 text-xs text-[var(--color-text-muted)]">
              {t('settings.awsAccount.checkedAt', { time: new Date(caps.checked_at).toLocaleString() })}
            </p>
          )}
        </section>
      )}

      <details className="text-sm">
        <summary className="cursor-pointer text-[var(--color-text-secondary)]">{t('settings.awsAccount.details')}</summary>
        <dl className="mt-3 grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 text-[var(--color-text-secondary)]">
          {acct?.caller_arn && (<><dt>{t('settings.awsAccount.callerArn')}</dt><dd className="font-mono break-all">{acct.caller_arn}</dd></>)}
          {acct?.verified_at && (<><dt>{t('settings.awsAccount.verifiedAt')}</dt><dd>{new Date(acct.verified_at).toLocaleString()}</dd></>)}
          <dt>{t('settings.awsAccount.chatVia')}</dt><dd>{t(`settings.awsAccount.chatViaValue.${view.chat_via}`)}</dd>
          <dt>{t('settings.awsAccount.profileFile')}</dt><dd className="font-mono break-all">{view.profile_file}</dd>
        </dl>
        {acct?.derived && <p className="mt-3 text-[var(--color-text-muted)]">{t('settings.awsAccount.derived')}</p>}
        <p className="mt-3 text-[var(--color-text-muted)]">{t('settings.awsAccount.changeLater')}</p>
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
