/**
 * Settings → LLM Models: where the model is called from (design
 * docs/design/2026-11-01-settings-account-merge.md §3, §6).
 *
 * Two choices (XG, 2026-11-01): Bedrock through the Swarm AWS account (the default), or a
 * Bedrock API key. AWS sign-in itself lives on the Swarm AWS Account page; this only says
 * which way chat reaches the model, read from the one reader (`chat_via`, backend
 * aws_account.get_chat_via). Anthropic keys are not supported (an old setup is moved onto
 * Bedrock at startup, backend aws_account.retire_anthropic_direct).
 *   - account: `awsAccountService.useForChat` (use_bedrock + the record's sign-in method).
 *     Without an account record chat on Bedrock still works, on this computer's own AWS
 *     credentials: the row says so as it is, and picking it opens the account page to
 *     connect one (design 2026-11-01 §3).
 *   - Bedrock API key: the wizard's form (verify first, store only after it worked).
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { awsAccountService, type AwsAccountView } from '../../services/awsAccount';
import { BedrockKeyForm } from '../awsAccount/ConnectAccount';
import { openSettings } from '../common/settingsEvents';

type Via = AwsAccountView['chat_via'];

export default function ModelProvider() {
  const { t } = useTranslation();
  const [view, setView] = useState<AwsAccountView | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [keyOpen, setKeyOpen] = useState(false);
  const load = useCallback(async () => {
    try { setView(await awsAccountService.get()); setFailed(false); } catch { setFailed(true); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  if (!view) {
    return <p className="text-sm text-[var(--color-text-muted)]">{failed ? t('settings.awsAccount.loadFailed') : t('settings.awsAccount.loading')}</p>;
  }
  const acct = view.account;
  const via: Via = view.chat_via;
  const useAccount = async () => {
    setKeyOpen(false);
    if (!acct) { openSettings('aws-account'); return; }
    if (via === 'aws') return;
    setBusy(true);
    try { setView(await awsAccountService.useForChat(acct)); setKeyOpen(false); setFailed(false); }
    catch { setFailed(true); }
    finally { setBusy(false); }
  };
  const Radio = ({ on }: { on: boolean }) => (
    <span className={`mt-1 w-4 h-4 rounded-full shrink-0 border ${on ? 'border-[5px] border-[var(--color-primary)]' : 'border-[var(--color-text-muted)]'}`} />
  );
  const row = 'w-full text-left flex gap-3 py-3 border-b border-[var(--color-border)] disabled:opacity-60';

  return (
    <div data-testid="model-provider">
      <h3 className="text-sm font-medium text-[var(--color-text)] mb-2">{t('settings.modelProvider.title')}</h3>
      <div className="border-t border-[var(--color-border)]">
        <button type="button" className={row} disabled={busy} data-testid="model-provider-aws"
          aria-pressed={via === 'aws'} onClick={() => void useAccount()}>
          <Radio on={via === 'aws'} />
          <span>
            <span className="block text-sm text-[var(--color-text)]">
              {acct ? t('settings.modelProvider.aws') : t('settings.modelProvider.awsLocal')}
            </span>
            <span className="block text-xs text-[var(--color-text-muted)]">
              {acct ? `${acct.account_id ?? '—'} · ${view.region ?? ''}` : (
                <>
                  {t('settings.modelProvider.awsLocalLead')}{' '}
                  <span className="underline" data-testid="model-provider-connect">{t('settings.modelProvider.connect')}</span>
                </>
              )}
            </span>
          </span>
        </button>
        <button type="button" className={row} disabled={busy} data-testid="model-provider-key"
          aria-pressed={via === 'bedrock_key'} onClick={() => setKeyOpen(true)}>
          <Radio on={via === 'bedrock_key' || keyOpen} />
          <span>
            <span className="block text-sm text-[var(--color-text)]">{t('settings.modelProvider.key')}</span>
            <span className="block text-xs text-[var(--color-text-muted)]">{t('settings.modelProvider.keyLead')}</span>
          </span>
        </button>
      </div>
      {keyOpen && (
        <div className="mt-4">
          <BedrockKeyForm compact region={view.region ?? 'us-east-1'}
            context={acct?.context === 'internal' ? 'internal' : 'external'}
            onDone={() => { setKeyOpen(false); void load(); }} />
          <button type="button" className="mt-2 text-sm underline text-[var(--color-text-secondary)]"
            data-testid="model-provider-key-cancel" onClick={() => setKeyOpen(false)}>
            {t('settings.modelProvider.cancel')}
          </button>
        </div>
      )}
      <p className="mt-3 text-xs text-[var(--color-text-muted)]">
        {(() => {
          const [before, after = ''] = t('settings.modelProvider.signInIsThere', { page: '\u0000' }).split('\u0000');
          return (
            <>
              {before}
              <button type="button" className="underline" onClick={() => openSettings('aws-account')}>
                {t('settings.awsAccount.title')}
              </button>
              {after}
            </>
          );
        })()}
      </p>
      {failed && <p className="mt-2 text-sm text-[var(--color-error)]">{t('settings.awsAccount.actionFailed')}</p>}
    </div>
  );
}
