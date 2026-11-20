/**
 * TrendLane — 趋势: today's summary as numbered points, each with one line on why it
 * matters to the person; read-original and learn-into-Brain on hover. The trend items
 * no point refers to fold under "其余 N 条" (design docs/design/2026-11-20-inbox.md §7).
 */
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { overlayQuery } from '../layout/overlayQueries';
import { OverlayEmpty, OverlayError, OverlayLoading } from '../layout/OverlayState';
import type { ItemOut, TrendPoint } from '../../services/inbox';
import { openExternal } from '../../utils/openExternal';
import { clockTime, webUrl } from './parts';

const iconBtn = 'icon-btn text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]';

export function TrendLane({ items, onLearn }: { items: ItemOut[]; onLearn: (p: TrendPoint) => void }) {
  const { t } = useTranslation();
  const q = useQuery(overlayQuery.inboxTrends());
  const points = q.data?.points ?? [];
  const used = new Set(points.map((p) => p.item_id).filter(Boolean));
  const rest = items.filter((it) => !used.has(it.id));

  return (
    <div className="min-h-0 flex-1 overflow-y-auto" data-testid="inbox-trend">
      <div className="mx-auto max-w-[680px] px-8 pb-14 pt-7">
        {q.isPending ? <OverlayLoading /> : q.isError ? (
          <OverlayError testid="inbox-load-error" retryTestid="inbox-load-retry" message={t('ui.InboxOverlay.loadError')} onRetry={() => void q.refetch()} />
        ) : points.length === 0 && rest.length === 0 ? (
          <OverlayEmpty icon="trending_up">{t('ui.InboxOverlay.trend.empty')}</OverlayEmpty>
        ) : (
          <>
            <p className="text-display font-semibold text-[var(--color-text)]">{t('ui.InboxOverlay.trend.lead', { count: points.length })}</p>
            <p className="mt-1 text-small text-[var(--color-text-dim)]">
              {t('ui.InboxOverlay.trend.sub', { time: clockTime(q.data?.generated_at), feeds: q.data?.feeds ?? 0, repos: q.data?.watched_repos ?? 0 })}
            </p>
            <ol className="mt-6 flex flex-col gap-1.5">
              {points.map((p, i) => (
                <li key={`${i}-${p.title}`} data-testid="inbox-trend-point"
                  className="group grid grid-cols-[32px_1fr_auto] items-baseline gap-x-3 gap-y-1 rounded-xl px-3 py-3.5 hover:bg-[var(--color-hover)]">
                  <span className="text-right text-display font-semibold"
                    style={{ color: 'color-mix(in srgb, var(--color-primary) 55%, var(--color-text-dim))' }}>{i + 1}</span>
                  <span className="text-heading font-semibold text-[var(--color-text)]">
                    {p.title}
                    {p.source_label && (
                      <span className="ml-1.5 inline-flex h-[18px] items-center rounded-[5px] px-1.5 align-[1px] text-meta font-normal text-[var(--color-text-muted)]"
                        style={{ background: 'color-mix(in srgb, var(--color-text) 10%, transparent)' }}>{p.source_label}</span>
                    )}
                  </span>
                  <span className="col-start-3 row-span-2 row-start-1 flex gap-0.5 self-center opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                    {webUrl(p.url) && (
                      <button type="button" className={iconBtn} onClick={() => void openExternal(webUrl(p.url)!)} data-testid="inbox-trend-open"
                        title={t('ui.InboxOverlay.trend.open')} aria-label={t('ui.InboxOverlay.trend.open')}>
                        <span className="material-symbols-outlined icon-sm">open_in_new</span>
                      </button>
                    )}
                    <button type="button" className={iconBtn} onClick={() => onLearn(p)} data-testid="inbox-trend-learn"
                      title={t('ui.InboxOverlay.trend.learn')} aria-label={t('ui.InboxOverlay.trend.learn')}>
                      <span className="material-symbols-outlined icon-sm">menu_book</span>
                    </button>
                  </span>
                  <span className="col-start-2 text-small text-[var(--color-text-muted)]">{p.why}</span>
                </li>
              ))}
            </ol>
            {rest.length > 0 && (
              <details className="mt-7" data-testid="inbox-trend-rest">
                <summary className="cursor-pointer rounded-lg px-3 py-2 text-small text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]">
                  {t('ui.InboxOverlay.trend.rest', { count: rest.length })}
                </summary>
                <ul className="mt-1 flex flex-col gap-1">
                  {rest.map((it) => (
                    <li key={it.id} className="group flex items-center gap-3.5 rounded-xl px-3 py-2 hover:bg-[var(--color-hover)]">
                      <span className="min-w-0 flex-1 text-body text-[var(--color-text-secondary)]">
                        {[it.channel_label, it.ask || it.excerpt].filter(Boolean).join(' · ')}
                      </span>
                      {webUrl(it.url) && (
                        <button type="button" className={`${iconBtn} opacity-0 group-hover:opacity-100 focus-visible:opacity-100`}
                          onClick={() => void openExternal(webUrl(it.url)!)} aria-label={t('ui.InboxOverlay.trend.open')} data-testid="inbox-trend-open">
                          <span className="material-symbols-outlined icon-sm">open_in_new</span>
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </>
        )}
      </div>
    </div>
  );
}
