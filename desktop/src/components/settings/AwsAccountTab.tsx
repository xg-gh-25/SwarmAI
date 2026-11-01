/**
 * Settings → Swarm AWS Account (design docs/design/2026-11-01-settings-account-merge.md §5,
 * docs/design/2026-10-31-swarmai-aws-account.md §6.5): everything AWS lives here.
 * Zones, in the order of the decisions made on this page:
 *   ① the account and whether it needs the person: one line with a state dot; when it
 *     does, ONE primary action, the same one the chat's sign-in line offers (backend
 *     `remediation.action`: type the sign-in command into the SwarmAI terminal, run the
 *     in-app sign-in, or change the account);
 *   ② the main region (Bedrock, cloud storage, voice, new Hives; writes `aws_region`);
 *   ③ what can be used (only the gaps, with the permission template);
 *   ④ how many Hives (HiveFleetSection: − / +, the cost named first, one row per Hive);
 *   ⑤ Hive accounts (desktop only): with a SwarmAI account, the Hives left in other
 *     accounts; without one, the legacy Hive accounts exactly as the old Hive Accounts
 *     tab showed them (AccountCard verify / delete, Add account);
 *   ⑥ Details: identity, why the state is what it is, verify, check, template.
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { awsAccountService, type AwsAccountView, type Capability } from '../../services/awsAccount';
import { settingsService } from '../../services/settings';
import { hiveService, hiveAccountView, type HiveAccount } from '../../services/hive';
import { isDesktop } from '../../services/tauri';
import { copyToClipboard } from '../../utils/clipboard';
import ConnectAccount, { type ConnectStart } from '../awsAccount/ConnectAccount';
import { CopyTemplate, HiveFleetSection } from '../awsAccount/HiveFleet';
import { regionOptions } from '../awsAccount/regions';
import { Dropdown } from '../common';
import { markTerminalRun, runInTerminal, takeSignInRequest, terminalRunRecently } from '../common/settingsEvents';
import { AccountCard, AddAccountDialog } from './hiveComponents';

const CAPS = ['bedrock', 'hive', 'storage', 'voice'] as const;

const STATE_DOT: Record<AwsAccountView['state'], string> = {
  connected: 'bg-green-500',
  needs_login: 'bg-[var(--color-warning)]',
  unverified: 'bg-[var(--color-warning)]',
  account_mismatch: 'bg-[var(--color-error)]',
  unconnected: 'bg-[var(--color-text-muted)]',
};

export default function AwsAccountTab() {
  const { t } = useTranslation();
  const [view, setView] = useState<AwsAccountView | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState<'verify' | 'check' | 'chat' | 'region' | null>(null);
  const [connecting, setConnecting] = useState<ConnectStart | null>(null);
  // the sign-in command was typed into the terminal: "Verify again" becomes the next step
  // (also when the chat's sign-in line did it a moment ago)
  const [launched, setLaunched] = useState(terminalRunRecently);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    try {
      setView(await awsAccountService.get());
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, []);
  useEffect(() => { load(); }, [load]);
  // the chat's sign-in line asked for the in-app sign-in: go straight into it
  useEffect(() => {
    if (!view || view.state !== 'needs_login' || !takeSignInRequest()) return;
    const src = view.account?.source;
    if (src === 'sso' || src === 'login') setConnecting(src);
  }, [view]);

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
  // the sign-in this page can run again itself (App SSO, AWS login)
  const signInAgain: ConnectStart | null = view.state === 'needs_login'
    ? acct?.source === 'sso' ? 'sso' : acct?.source === 'login' ? 'login' : null : null;
  const action = view.remediation?.action;
  const terminal = view.state === 'needs_login' && action?.kind === 'terminal' ? action : null;
  // exactly one emphasized action: what this state needs
  const primary: 'signIn' | 'terminal' | 'verify' | 'change' | null = signInAgain ? 'signIn' : terminal ? 'terminal'
    : acct && view.state === 'unverified' ? 'verify'
    : !acct || view.state === 'needs_login' || view.state === 'account_mismatch' ? 'change' : null;
  const chatOnAccount = async () => {
    if (!acct) return;
    setBusy('chat');
    try {
      setView(await awsAccountService.useForChat(acct));
      setFailed(false);
    } catch {
      setFailed(true);
    } finally {
      setBusy(null);
    }
  };
  const setRegion = async (region: string) => {
    setBusy('region');
    try {
      await settingsService.updateAPIConfiguration({ aws_region: region });
      // the capabilities are checked in the main region: check them there
      setView(acct ? await awsAccountService.check() : await awsAccountService.get());
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
  // what is wrong, said once: with a terminal fix the steps are the button and its hint
  const stateLine = view.state === 'connected' ? t('settings.awsAccount.state.connected')
    : terminal ? (acct?.source === 'ada' ? t('settings.awsAccount.remedy.expired_ada') : t('settings.awsAccount.remedy.expired'))
      : view.state === 'needs_login' && acct?.source === 'ada' ? t('settings.awsAccount.remedy.needs_login_ada')
        : t(`settings.awsAccount.remedy.${view.state}`);
  const caps = view.capabilities;
  const gaps = caps
    ? CAPS.filter((c) => caps[c] && caps[c].status !== 'ok' && caps[c].status !== 'not_applicable')
    : [];
  const smallBtn = 'btn-md btn-secondary disabled:opacity-50';

  return (
    <div className="space-y-8" data-testid="aws-account-tab">
      <section>
        <h2 className="text-lg font-semibold text-[var(--color-text)]">{t('settings.awsAccount.title')}</h2>
        <p className="text-sm text-[var(--color-text-muted)] mt-1">{t('settings.awsAccount.lead')}</p>
        {acct && (
          <p className="mt-4 text-sm text-[var(--color-text)]">
            <span className="font-mono">{acct.account_id ?? '—'}</span>
            {sourceLabel && <span className="text-[var(--color-text-secondary)]"> · {sourceLabel}</span>}
          </p>
        )}
        <p data-testid="aws-account-state" className="mt-2 flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
          <span className={`w-2 h-2 rounded-full shrink-0 ${STATE_DOT[view.state]}`} />
          <span data-testid="aws-account-remedy">{stateLine}</span>
          {primary === null && (
            <button type="button" disabled={busy !== null} onClick={() => setConnecting('pick')}
              data-testid="aws-account-change" className="ml-2 underline disabled:opacity-50">
              {t('settings.awsAccount.change')}
            </button>
          )}
        </p>
        {terminal && launched && (
          <p data-testid="aws-account-terminal-hint" className="mt-2 text-sm text-[var(--color-text-secondary)]">
            {t('settings.awsAccount.terminalHint', { command: terminal.command })}
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
        {primary !== null && (
          <div className="mt-4 flex flex-wrap items-center gap-3">
            {primary === 'signIn' && signInAgain && (
              <button type="button" disabled={busy !== null} onClick={() => setConnecting(signInAgain)}
                data-testid="aws-account-sign-in" className="btn-md btn-primary disabled:opacity-50">
                {t('settings.awsAccount.signInAgain')}
              </button>
            )}
            {primary === 'verify' && (
              <button type="button" disabled={busy !== null} onClick={() => run('verify')}
                data-testid="aws-account-verify-now" className="btn-md btn-primary disabled:opacity-50">
                {busy === 'verify' ? '…' : t('settings.awsAccount.verify')}
              </button>
            )}
            {primary === 'terminal' && terminal && (
              <>
                <button type="button" disabled={busy !== null} data-testid="aws-account-terminal"
                  onClick={() => {
                    if (!isDesktop()) {
                      void copyToClipboard(terminal.command).then((ok) => { if (ok) setCopied(true); });
                      return;
                    }
                    runInTerminal(terminal.command, terminal.title);
                    markTerminalRun();
                    setLaunched(true);
                  }}
                  className={launched ? smallBtn : 'btn-md btn-primary disabled:opacity-50'}>
                  {isDesktop() ? t('credentialBanner.inTerminal') : copied ? t('credentialBanner.copied') : t('credentialBanner.copy')}
                </button>
                <button type="button" disabled={busy !== null || !acct} onClick={() => run('verify')}
                  data-testid="aws-account-verify-now"
                  className={launched ? 'btn-md btn-primary disabled:opacity-50' : smallBtn}>
                  {busy === 'verify' ? '…' : t('settings.awsAccount.verify')}
                </button>
              </>
            )}
            <button type="button" disabled={busy !== null}
              onClick={() => setConnecting('pick')} data-testid="aws-account-change"
              className={primary === 'change' ? 'btn-md btn-primary disabled:opacity-50' : 'underline text-sm disabled:opacity-50'}>
              {acct ? t('settings.awsAccount.change') : t('settings.awsAccount.connect')}
            </button>
          </div>
        )}
      </section>

      {isDesktop() || acct ? (
        <section data-testid="aws-account-region">
          <Dropdown label={t('settings.awsAccount.mainRegion')} options={regionOptions(view.region)}
            selectedId={view.region ?? 'us-east-1'}
            onChange={(id) => { if (id !== view.region) void setRegion(id); }} />
          <p className="mt-1 text-xs text-[var(--color-text-muted)]">
            {busy === 'region' ? t('settings.awsAccount.checking') : t('settings.awsAccount.mainRegionLead')}
          </p>
        </section>
      ) : null}

      {acct && view.chat_via !== 'aws' && view.capabilities?.bedrock?.status === 'ok' && (
        <p className="text-sm text-[var(--color-text-secondary)]" data-testid="aws-account-chat-switch">
          {t('settings.awsAccount.chatNotOnAccount', { via: t(`settings.awsAccount.chatViaValue.${view.chat_via}`) })}{' '}
          <button type="button" className="underline" disabled={busy !== null} onClick={() => void chatOnAccount()}>
            {t('settings.awsAccount.chatUseAccount')}
          </button>
        </p>
      )}

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

      {isDesktop() && <HiveAccountsSection />}

      <details className="text-sm">
        <summary className="cursor-pointer text-[var(--color-text-secondary)]">{t('settings.awsAccount.details')}</summary>
        <dl className="mt-3 grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 text-[var(--color-text-secondary)]">
          {acct?.caller_arn && (<><dt>{t('settings.awsAccount.callerArn')}</dt><dd className="font-mono break-all">{acct.caller_arn}</dd></>)}
          {view.state_detail && (<><dt>{t('settings.awsAccount.stateDetail')}</dt><dd className="break-words">{view.state_detail}</dd></>)}
          {acct?.verified_at && (<><dt>{t('settings.awsAccount.verifiedAt')}</dt><dd>{new Date(acct.verified_at).toLocaleString()}</dd></>)}
          <dt>{t('settings.awsAccount.chatVia')}</dt><dd>{t(`settings.awsAccount.chatViaValue.${view.chat_via}`)}</dd>
          <dt>{t('settings.awsAccount.profileFile')}</dt><dd className="font-mono break-all">{view.profile_file}</dd>
        </dl>
        {acct?.derived && <p className="mt-3 text-[var(--color-text-muted)]">{t('settings.awsAccount.derived')}</p>}
        {acct && (
          <div className="mt-3 flex flex-wrap gap-3">
            <button type="button" disabled={busy !== null} onClick={() => run('verify')} className={smallBtn}
              data-testid="aws-account-verify">
              {busy === 'verify' ? '…' : t('settings.awsAccount.verify')}
            </button>
            <button type="button" disabled={busy !== null} onClick={() => run('check')} className={smallBtn}>
              {busy === 'check' ? '…' : t('settings.awsAccount.check')}
            </button>
            {!gaps.some((c) => c === 'hive' || c === 'storage') && (
              <span className="self-center text-xs text-[var(--color-text-secondary)]">{t('settings.awsAccount.giveTemplate')} <CopyTemplate /></span>
            )}
          </div>
        )}
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

/** ⑤ The Hive accounts (the former Hive Accounts tab, same building blocks as the Hive
 *  nav card): with a SwarmAI account only the other accounts that still hold Hives;
 *  without one, the legacy accounts Hives deploy into, and Add account. */
function HiveAccountsSection() {
  const { t } = useTranslation();
  const [accounts, setAccounts] = useState<HiveAccount[] | null>(null);
  const [adding, setAdding] = useState(false);
  const refresh = useCallback(async () => {
    try { setAccounts(await hiveService.listAccounts()); } catch { setAccounts([]); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  if (!accounts) return null;
  const v = hiveAccountView(accounts);
  const others = v.swarmai ? v.shown.filter((a) => a.role !== 'swarmai') : v.shown;
  if (others.length === 0 && !v.canAdd) return null;
  return (
    <section data-testid="aws-account-hive-accounts">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-[var(--color-text)]">
          {v.swarmai ? t('settings.awsAccount.otherHives') : t('settings.awsAccount.hiveAccounts')}
        </h3>
        {v.canAdd && (
          <button type="button" onClick={() => setAdding(true)} className="btn-md btn-secondary">
            {t('settings.awsAccount.addHiveAccount')}
          </button>
        )}
      </div>
      {others.length === 0 ? (
        <p className="mt-2 text-sm text-[var(--color-text-muted)]">{t('settings.awsAccount.noHiveAccounts')}</p>
      ) : (
        <div className="mt-2 space-y-2">
          {others.map((a) => <AccountCard key={a.id} account={a} onDelete={() => void refresh()} />)}
        </div>
      )}
      {adding && (
        <AddAccountDialog onClose={() => setAdding(false)}
          onSaved={() => { setAdding(false); void refresh(); }} />
      )}
    </section>
  );
}
