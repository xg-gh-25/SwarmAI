/**
 * TrendLane — 趋势 (design docs/design/2026-11-20-inbox.md §7, left rail §11.5).
 *
 * The left rail (`CategoryRail`): 今日小结 (the default when there is one) · 全部条目, then
 * 信息流 with one entry per feed the items come from (Hacker News, RSS, …) in name order,
 * each with its count. The list is the newest `LIST_CAP` items the server returns, so a
 * count at the cap reads "200+". A chosen feed that runs out falls back to 全部条目.
 * - 今日小结: today's summary as numbered points, each with one line on why it matters to
 *   the person; read-original and learn-into-Brain on hover.
 * - 全部条目 / a feed: the items newest first, one line each (feed · title · when),
 *   read-original on hover.
 * Below 720 px the rail is a row of chips on top.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { overlayQuery } from '../layout/overlayQueries';
import { OverlayEmpty, OverlayError, OverlayLoading } from '../layout/OverlayState';
import type { ItemOut, TrendPoint } from '../../services/inbox';
import { openExternal } from '../../utils/openExternal';
import { CategoryRail, clockTime, feedLabel, relTime, webUrl, type RailItem } from './parts';

/** The most items one list answer holds (`db.inbox_list_items`' limit). */
const LIST_CAP = 200;

const iconBtn = 'icon-btn text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]';

export function TrendLane({ items, onLearn, narrow = false }: {
  items: ItemOut[]; onLearn: (p: TrendPoint) => void; narrow?: boolean;
}) {
  const { t } = useTranslation();
  const q = useQuery(overlayQuery.inboxTrends());
  const points = q.data?.points ?? [];
  /** The rail's choice: the summary, every item, or `feed:<name>`. */
  const [chosen, setPart] = useState<string | null>(null);
  const feedOf = (it: ItemOut) => feedLabel(it.source_kind, it.channel_label);
  const feeds: RailItem<string>[] = [];
  for (const it of items) {
    const name = feedOf(it);
    const f = feeds.find((x) => x.key === `feed:${name}`);
    if (f) f.count = Number(f.count ?? 0) + 1;
    else feeds.push({ key: `feed:${name}`, label: name, count: 1 });
  }
  feeds.sort((a, b) => a.label.localeCompare(b.label));
  const rail = [
    { items: [
      { key: 'summary', label: t('ui.InboxOverlay.trend.railSummary') },
      { key: 'all', label: t('ui.InboxOverlay.trend.railAll'), count: items.length >= LIST_CAP ? `${LIST_CAP}+` : items.length },
    ] },
    { title: t('ui.InboxOverlay.channels.feedsTitle'), items: feeds },
  ];
  // no choice yet: the summary when there is one, else every item; a feed that ran out: every item
  const part = chosen == null ? (points.length ? 'summary' : 'all')
    : chosen.startsWith('feed:') && !feeds.some((f) => f.key === chosen) ? 'all' : chosen;
  const listed = part === 'all' ? items : items.filter((it) => `feed:${feedOf(it)}` === part);
  const partLabel = rail.flatMap((g) => g.items).find((x) => x.key === part)?.label ?? '';
  const ready = !q.isPending && !q.isError && (points.length > 0 || items.length > 0);

  return (
    <div className={`flex min-h-0 flex-1 ${narrow ? 'flex-col' : ''}`}>
      {ready && (
        <CategoryRail groups={rail} value={part} onChange={setPart} narrow={narrow}
          label={t('ui.InboxOverlay.railLabel')} testid="inbox-trend-rail" />
      )}
      <div className="min-h-0 flex-1 overflow-y-auto" data-testid="inbox-trend">
        <div className={`mx-auto max-w-[760px] pb-14 pt-7 ${narrow ? 'px-6' : 'px-10'}`}>
          {q.isPending ? <OverlayLoading /> : q.isError ? (
            <OverlayError testid="inbox-load-error" retryTestid="inbox-load-retry" message={t('ui.InboxOverlay.loadError')} onRetry={() => void q.refetch()} />
          ) : !ready ? (
            <OverlayEmpty icon="trending_up">{t('ui.InboxOverlay.trend.empty')}</OverlayEmpty>
          ) : part === 'summary' ? (
            points.length === 0 ? <OverlayEmpty icon="trending_up">{t('ui.InboxOverlay.trend.noSummary')}</OverlayEmpty> : (
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
              </>
            )
          ) : listed.length === 0 ? (
            <OverlayEmpty icon="trending_up">{t('ui.InboxOverlay.trend.emptyPart')}</OverlayEmpty>
          ) : (
            <>
              <p className="text-display font-semibold text-[var(--color-text)]" data-testid="inbox-trend-lead">{partLabel}</p>
              <ul className="mt-5 flex flex-col gap-0.5" data-testid="inbox-trend-items">
                {listed.map((it) => (
                  <li key={it.id} data-testid="inbox-trend-item" className="group flex items-center gap-3.5 rounded-xl px-3 py-2.5 hover:bg-[var(--color-hover)]">
                    <span className="min-w-0 flex-1">
                      <span className="block text-body text-[var(--color-text)]">{it.ask || it.excerpt}</span>
                      <span className="mt-0.5 block text-small text-[var(--color-text-dim)]">
                        {[part === 'all' ? feedOf(it) : '', relTime(it.received_at, t)].filter(Boolean).join(' · ')}
                      </span>
                    </span>
                    {webUrl(it.url) && (
                      <button type="button" className={`${iconBtn} opacity-0 group-hover:opacity-100 focus-visible:opacity-100`}
                        onClick={() => void openExternal(webUrl(it.url)!)} title={t('ui.InboxOverlay.trend.open')}
                        aria-label={t('ui.InboxOverlay.trend.open')} data-testid="inbox-trend-open">
                        <span className="material-symbols-outlined icon-sm">open_in_new</span>
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
