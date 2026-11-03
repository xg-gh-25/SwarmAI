/**
 * SwarmRunCard — a Swarm Run in its lead run's chat (design
 * docs/design/2026-10-31-swarm-run.md §5.9; mock Knowledge/Mockups/2026-10-31-swarm-run-card.html).
 *
 * It answers one question: is the run going, and is anything asked of me? So it is the
 * Swarm Out run list itself (RunList, the same rows, sections and big card a child has
 * in Swarm Out), filtered to this run's children, under one head line: the run's title,
 * "Brain · lead run <profile> · N children", what the children cost on Hives, the run's
 * state, and "Stop all". The packages not dispatched yet are a section of their own.
 * Taking or correcting a child is its lead run's, so a child row never offers accept or
 * rerun (the backend's actions); a child waiting for the lead run is not "waiting for
 * you". The block is the backend's (core/swarm_run_card); the rows come from the shared
 * runs poll, the head from /swarm-out/swarm-runs/<run>.
 *
 * @exports SwarmRunCard
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { SwarmRunContent } from '../../../types';
import { swarmOutService, type SwarmRunView } from '../../../services/swarmOut';
import { RunList } from '../../../components/swarm-out/RunList';
import { TONE_VAR } from '../../../components/swarm-out/tone';
import { DISPATCH_POLL_MS, reloadRunsPoll, useRunsPoll } from './DispatchCard';

const muted = 'text-[var(--color-text-muted)]';
const LIVE = new Set(['active', 'paused']);

function useView(runId: string): { view: SwarmRunView | null; failed: boolean; reload: () => void } {
  const [view, setView] = useState<SwarmRunView | null>(null);
  const [failed, setFailed] = useState(false);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let gone = false;
    const load = () => swarmOutService.swarmRun(runId)
      .then((v) => { if (!gone) { setView(v); setFailed(false); } })
      .catch(() => { if (!gone) setFailed(true); });
    load();
    const timer = window.setInterval(load, DISPATCH_POLL_MS);
    return () => { gone = true; window.clearInterval(timer); };
  }, [runId, tick]);
  return { view, failed, reload: () => setTick((n) => n + 1) };
}

export function SwarmRunCard({ block }: { block: SwarmRunContent }) {
  const { t } = useTranslation();
  const all = useRunsPoll();
  const { view, failed, reload } = useView(block.runId);
  const [stopping, setStopping] = useState(false);
  const [stopFailed, setStopFailed] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);
  const runs = (all?.runs ?? []).filter((r) => r.parent_run === block.runId);
  const sessions = new Set(runs.map((r) => r.session_id).filter(Boolean));
  const asks = (all?.asks ?? []).filter((a) => sessions.has(a.session_id));
  const answered = (all?.answered ?? []).filter((a) => sessions.has(a.session_id));
  const onReload = () => { reloadRunsPoll(); reload(); };
  const live = view ? LIVE.has(view.status) : true;
  const project = view?.project ?? block.project ?? '';
  const count = view?.children ?? runs.length;
  return (
    <div className="my-2 px-3 py-2.5 rounded-[var(--radius-md,8px)] border border-[var(--color-border)]" data-testid="swarm-run-card">
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className="text-[13.5px] font-semibold truncate">
            {t('swarmOut.review.swarmRun.title', { title: view?.title ?? block.runId })}
          </div>
          <div className={`text-[12px] ${muted}`} data-testid="swarm-run-meta">
            {t('swarmOut.review.swarmRun.meta', { project, profile: view?.profile ?? '—', count })}
            {view?.children_cost_usd != null && <> · {t('swarmOut.review.swarmRun.cost', { cost: view.children_cost_usd.toFixed(2) })}</>}
            {view && <> · {t(`swarmOut.review.swarmRun.status.${view.status}`, { defaultValue: view.status })}</>}
          </div>
          {failed && !view && <div className={`text-[12px] ${muted}`}>{t('swarmOut.review.swarmRun.unreadable')}</div>}
        </div>
        {live && runs.some((r) => r.state === 'running' || r.state === 'waiting') && (
          <button type="button" disabled={stopping} data-testid="swarm-run-stop-all"
            className="btn-sm border disabled:opacity-50 shrink-0"
            style={{ color: TONE_VAR.error, borderColor: `color-mix(in srgb, ${TONE_VAR.error} 45%, var(--color-border))` }}
            title={stopFailed ? t('swarmOut.review.swarmRun.stopAllFailed') : t('swarmOut.review.swarmRun.stopAllDoes')}
            onClick={() => {
              setStopping(true); setStopFailed(false);
              swarmOutService.stopSwarmRun(block.runId)
                .then((r) => { if (r.not_stopped.length) setStopFailed(true); onReload(); })
                .catch(() => setStopFailed(true)).finally(() => setStopping(false));
            }}>■ {t('swarmOut.review.swarmRun.stopAll')}</button>
        )}
      </div>
      <RunList runs={runs} asks={asks} answered={answered} now={now} onReload={onReload} compact
        pending={view?.not_dispatched ?? []} />
    </div>
  );
}
