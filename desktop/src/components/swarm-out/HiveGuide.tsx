/**
 * HiveGuide — what the Swarm Out panel shows when there is nowhere to send a task yet:
 * the AWS account is not connected. Swarm Out sends tasks to the person's own AWS (Hives
 * on EC2, Elastic Workers on AgentCore Runtime); once the account is connected SwarmAI
 * prepares Elastic Workers by itself, so this is the only setup step the panel asks for.
 * A stopped Hive is never a reason to show it: a dispatch starts one.
 */
import { useTranslation } from 'react-i18next';
import { OPEN_SETTINGS_EVENT } from '../common/settingsEvents';

const card = 'rounded-xl border border-[var(--color-border)] bg-[var(--color-card)]';
const muted = 'text-[var(--color-text-muted)]';

export function HiveGuide() {
  const { t } = useTranslation();
  return (
    <section className={`${card} px-5 py-4 mb-4 max-w-[620px]`} data-testid="swarm-out-hive-guide" data-case="no-account">
      <div className="font-semibold text-[15px]">{t('swarmOut.guide.title')}</div>
      <div className="mt-1 text-[13px] text-[var(--color-text-secondary)]">{t('swarmOut.guide.why')}</div>
      <button type="button" className="mt-3 btn-md btn-primary" data-testid="swarm-out-connect-account"
        onClick={() => window.dispatchEvent(new CustomEvent(OPEN_SETTINGS_EVENT, { detail: { tab: 'aws-account' } }))}>
        {t('swarmOut.guide.connect')}
      </button>
      <div className={`mt-2 text-[12px] ${muted}`}>{t('swarmOut.guide.once')}</div>
    </section>
  );
}
