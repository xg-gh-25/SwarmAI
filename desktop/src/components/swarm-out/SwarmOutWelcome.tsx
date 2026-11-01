/**
 * SwarmOutWelcome — the Swarm Out tab's own empty state (the Swarm Out welcome /
 * new-session design, ① and ②).
 *
 * It answers one question: what can I do here. The fleet, what waits on the person
 * and the runs are on the dashboard to the left; this side repeats none of them.
 *
 * - Three starters. A click puts the sentence into the input, after anything already
 *   typed, and never sends it: the person finishes it and sends.
 * - With no running Hive the dispatch starter becomes the one notice: why nothing can
 *   be dispatched, and a button that opens the Hive card. Asking about results and
 *   breaking an idea into tasks need no Hive, so those two stay.
 * - The four steps a dispatch goes through, in one line.
 *
 * The fleet is read from /api/swarm-out/fleet every 30 s, only while shown (this module
 * loads no locale module itself: TabView imports it).
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { dispatchInjectChatInput } from '../../pages/chat/injectChatInput';
import { swarmOutService, type SwarmOutFleet } from '../../services/swarmOut';

const FLEET_POLL_MS = 30_000;

/** The fleet while shown; the last answer is kept when a read fails. */
function useFleetWhileShown(hidden: boolean): SwarmOutFleet | null {
  const [fleet, setFleet] = useState<SwarmOutFleet | null>(null);
  useEffect(() => {
    if (hidden) return undefined;
    let alive = true;
    const load = () => { swarmOutService.fleet().then((f) => { if (alive) setFleet(f); }).catch(() => {}); };
    load();
    const id = window.setInterval(load, FLEET_POLL_MS);
    return () => { alive = false; window.clearInterval(id); };
  }, [hidden]);
  return fleet;
}

/** Whether a task can go out now: some Hive runs and answers. Null until known. */
export function canDispatch(fleet: SwarmOutFleet | null): boolean | null {
  if (!fleet) return null;
  return fleet.hives.some((h) => h.status === 'running' && h.reachable !== false);
}

function Starter({ icon, text, hint, strong, testId }: {
  icon: string; text: string; hint?: string; strong?: boolean; testId: string;
}) {
  return (
    <button type="button" data-testid={testId}
      className="w-full flex items-center gap-2.5 px-3.5 py-[11px] rounded-[10px] border bg-[var(--color-card)] text-left text-[13.5px] text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
      style={{ borderColor: strong ? 'color-mix(in srgb, var(--color-brand) 55%, var(--color-border))' : 'var(--color-border)' }}
      // after what the person already typed, never over it
      onClick={() => dispatchInjectChatInput({ text, focus: true, append: true })}>
      <span className="material-symbols-outlined text-[16px] text-[var(--color-brand)] w-[18px] text-center self-start mt-[1px]" aria-hidden>{icon}</span>
      {/* the hint on its own line: the column can be 360px wide */}
      <span className="flex-1 min-w-0 flex flex-col">
        <span>{text}</span>
        {hint && <span className="text-[11.5px] text-[var(--color-text-dim)]">{hint}</span>}
      </span>
    </button>
  );
}

export function SwarmOutWelcome({ hidden = false, onOpenHive }: { hidden?: boolean; onOpenHive?: () => void }) {
  const { t } = useTranslation();
  const fleet = useFleetWhileShown(hidden);
  const can = canDispatch(fleet);
  const stopped = (fleet?.hives ?? []).filter((h) => h.status === 'stopped').length;
  const steps = [t('swarmOut.welcome.step1'), t('swarmOut.welcome.step2'), t('swarmOut.welcome.step3'), t('swarmOut.welcome.step4')];
  return (
    <div className="flex flex-col h-full chat-scroll-gutter" data-testid="swarm-out-welcome">
      <div className="chat-column flex flex-col pt-[34px] pb-4">
        <div className="w-10 h-10 rounded-[11px] flex items-center justify-center mb-3 text-[var(--color-brand)]"
          style={{ background: 'color-mix(in srgb, var(--color-brand) 16%, transparent)' }} aria-hidden>
          <span className="material-symbols-outlined text-[22px]">hive</span>
        </div>
        <h2 className="m-0 text-[22px] font-semibold tracking-[-0.01em] text-[var(--color-text)]">{t('needYou.source.swarm_out')}</h2>
        <p className="mt-1 mb-[22px] text-[14px] text-[var(--color-text-secondary)]">{t('swarmOut.welcome.why')}</p>
        <div className="grid gap-2">
          {can === false ? (
            <div className="flex items-center gap-3 px-3.5 py-3 rounded-[10px] border" data-testid="swarm-out-welcome-nohive"
              style={{ borderColor: 'color-mix(in srgb, var(--color-status-warning) 45%, var(--color-border))',
                background: 'color-mix(in srgb, var(--color-status-warning) 8%, transparent)' }}>
              <div className="flex-1 min-w-0">
                <div className="text-[13.5px] font-medium text-[var(--color-text)]">{t('swarmOut.welcome.noHive')}</div>
                <div className="text-[12px] text-[var(--color-text-muted)]">
                  {stopped > 0 ? t('swarmOut.welcome.noHiveStopped', { count: stopped }) : t('swarmOut.welcome.noHiveNone')}
                </div>
              </div>
              {onOpenHive && (
                <button type="button" onClick={onOpenHive} data-testid="swarm-out-welcome-open-hive"
                  className="px-3 py-[5px] rounded-[7px] bg-[var(--color-text)] text-[var(--color-bg)] text-[12px] font-semibold whitespace-nowrap">
                  {t('swarmOut.welcome.openHive')}
                </button>
              )}
            </div>
          ) : (
            <Starter icon="north_east" text={t('swarmOut.welcome.dispatch')} hint={t('swarmOut.welcome.dispatchHint')}
              strong testId="swarm-out-start-dispatch" />
          )}
          <Starter icon="schedule" text={t('swarmOut.welcome.results')} testId="swarm-out-start-results" />
          <Starter icon="call_split" text={t('swarmOut.welcome.breakDown')} testId="swarm-out-start-break" />
        </div>
        <div className="mt-[22px] flex flex-wrap items-center gap-y-1 text-[12px] text-[var(--color-text-muted)]" data-testid="swarm-out-welcome-steps">
          {steps.map((s, i) => (
            <span key={s} className="whitespace-nowrap">
              {i > 0 && <span className="mx-2 text-[var(--color-text-dim)]" aria-hidden>→</span>}{s}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
