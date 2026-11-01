/**
 * HiveGuide — what the Swarm Out panel shows when there is no running Hive to dispatch to.
 *
 * Swarm Out sends tasks to the person's own Hives, so with none running it can do
 * nothing; this card says so and walks through the setup in the Hive surface (left-nav
 * Hive card), naming its buttons as they are labelled there. Two cases:
 *  - no Hive at all: what a Hive is, then the three setup steps;
 *  - Hives, none running: which ones and their status, and to start one.
 * The "Open Hive" button is the person's own click, so it may open the Hive overlay
 * (the agent cannot: Hive is outside the ui_action allowlist).
 */
import { useTranslation } from 'react-i18next';
import type { SwarmOutHive } from '../../services/swarmOut';
import { hiveStatusLabel } from './live';

const card = 'rounded-xl border border-[var(--color-border)] bg-[var(--color-card)]';
const muted = 'text-[var(--color-text-muted)]';
const btn = 'btn-md btn-primary';

export function HiveGuide({ hives, onOpenHive }: { hives: SwarmOutHive[]; onOpenHive?: () => void }) {
  const { t } = useTranslation();
  const open = onOpenHive && (
    <button type="button" className={btn} onClick={onOpenHive} data-testid="swarm-out-open-hive">{t('swarmOut.guide.openHive')}</button>
  );
  if (hives.length > 0) {
    return (
      <section className={`${card} px-4 py-3 mb-4`} data-testid="swarm-out-hive-guide" data-case="none-running">
        <div className="font-medium">{t('swarmOut.guide.noneRunning')}</div>
        <div className={`mt-1 text-[12.5px] ${muted}`}>
          {t('swarmOut.guide.startOne', { list: hives.map((h) => t('swarmOut.guide.hiveWithStatus', { name: h.name, status: hiveStatusLabel(h.status) })).join(t('swarmOut.sep.list')) })}
        </div>
        {open && <div className="mt-3">{open}</div>}
      </section>
    );
  }
  return (
    <section className={`${card} px-4 py-3 mb-4`} data-testid="swarm-out-hive-guide" data-case="no-hive">
      <div className="font-medium">{t('swarmOut.guide.noHive')}</div>
      <div className={`mt-1 text-[12.5px] ${muted}`}>{t('swarmOut.guide.whatIsHive')}</div>
      <ol className="mt-2 mb-0 pl-5 text-[12.5px] leading-6">
        <li>{t('swarmOut.guide.step1')}</li>
        <li>{t('swarmOut.guide.step2')}</li>
        <li>{t('swarmOut.guide.step3')}</li>
      </ol>
      {open && <div className="mt-3">{open}</div>}
    </section>
  );
}
