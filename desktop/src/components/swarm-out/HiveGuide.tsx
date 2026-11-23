/**
 * HiveGuide — what the Swarm Out panel shows when there is nowhere to send a task yet:
 * the AWS account is not connected. Swarm Out sends tasks to the person's own AWS (Hives
 * on EC2, Elastic Workers on AgentCore Runtime); once the account is connected SwarmAI
 * prepares Elastic Workers by itself, so this is the only setup step the panel asks for.
 * The account is connected right here, with the same sign-in flow as the App's Settings
 * (ConnectAccount): the person is never sent away from Swarm Out to do it.
 * A stopped Hive is never a reason to show it: a dispatch starts one.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { AwsAccountView } from '../../services/awsAccount';
import ConnectAccount from '../awsAccount/ConnectAccount';

const muted = 'text-[var(--color-text-muted)]';

export function HiveGuide({ onConnected }: { onConnected?: (view: AwsAccountView) => void }) {
  const { t } = useTranslation();
  const [connecting, setConnecting] = useState(false);
  return (
    <section className="so-card rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] px-5 py-4 mb-4 max-w-[640px]"
      data-testid="swarm-out-hive-guide" data-case="no-account">
      <div className="text-title font-semibold">{t('swarmOut.guide.title')}</div>
      <div className="mt-1 text-body text-[var(--color-text-secondary)]">{t('swarmOut.guide.why')}</div>
      {!connecting ? (
        <>
          <button type="button" className="mt-3 btn-md btn-primary" data-testid="swarm-out-connect-account"
            onClick={() => setConnecting(true)}>
            {t('swarmOut.guide.connect')}
          </button>
          <div className={`mt-2 text-small ${muted}`}>{t('swarmOut.guide.once')}</div>
        </>
      ) : (
        <div className="mt-4 pt-4 border-t border-[var(--color-border)]" data-testid="swarm-out-connect-flow">
          <ConnectAccount mode="settings" start="pick"
            onConnected={(v) => { setConnecting(false); onConnected?.(v); }} onCancel={() => setConnecting(false)} />
        </div>
      )}
    </section>
  );
}
