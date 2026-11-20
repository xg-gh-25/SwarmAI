/**
 * SentLane — 已发: what went out in the person's name, and which send needs them
 * (design docs/design/2026-11-20-inbox.md §7). The data is `inbox_sends` as the API
 * returns it (`GET /api/inbox/sends`), nothing stored apart.
 *
 * 1. 要你处理 (only when there is one): a tinted card per send. `unknown` (warning):
 *    去看看 + 确认没发出去; `failed` (error): 再发一次, disabled with 修复渠道 beside it
 *    when the failure is the source's (`blocked_by_source`).
 * 2. 已发, grouped by day: who it went to, the channel, 自动 for an auto send, 有回复
 *    (jumps to that item in 等你回复). Filter 全部 / 我发的 / 自动.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Segmented } from '../overlay';
import { overlayQuery, invalidateOverlay } from '../layout/overlayQueries';
import { OverlayEmpty, OverlayError, OverlayLoading } from '../layout/OverlayState';
import { inboxService, type SendOut, type SendsFilter } from '../../services/inbox';
import { openExternal } from '../../utils/openExternal';
import { Avatar, ChannelPill, clockTime, dayKey, dayLabel, webUrl } from './parts';

export function SentLane({ onGotoItem, onFixChannels, notify }: {
  onGotoItem: (itemId: string) => void; onFixChannels: () => void; notify: (msg: string) => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [filter, setFilter] = useState<SendsFilter>('all');
  const q = useQuery(overlayQuery.inboxSends(filter));
  const [busy, setBusy] = useState<string | null>(null);

  const run = async (s: SendOut, fn: () => Promise<unknown>, okMsg: string) => {
    if (busy) return;
    setBusy(s.id);
    try {
      await fn();
      notify(okMsg);
    } catch {
      notify(t('ui.InboxOverlay.actionError'));
    } finally {
      setBusy(null);
      void invalidateOverlay(qc, 'inbox');
    }
  };

  if (q.isPending) return <div className="flex-1"><OverlayLoading /></div>;
  if (q.isError) {
    return (
      <div className="flex-1">
        <OverlayError testid="inbox-load-error" retryTestid="inbox-load-retry" message={t('ui.InboxOverlay.loadError')} onRetry={() => void q.refetch()} />
      </div>
    );
  }
  const { need_you: need, sent, total_7d, auto_7d } = q.data;
  const days: string[] = [];
  for (const s of sent) {
    const k = dayKey(s.sent_at ?? s.created_at);
    if (!days.includes(k)) days.push(k);
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto" data-testid="inbox-sent">
      <div className="mx-auto max-w-[680px] px-8 pb-14 pt-7">
        <p className="text-display font-semibold text-[var(--color-text)]">
          {need.length ? t('ui.InboxOverlay.sent.leadNeed', { count: need.length }) : t('ui.InboxOverlay.sent.leadAll')}
        </p>
        <p className="mt-1 text-small text-[var(--color-text-dim)]">{t('ui.InboxOverlay.sent.sub', { total: total_7d, auto: auto_7d })}</p>

        {need.length > 0 && (
          <div className="mt-5 flex flex-col gap-2.5">
            {need.map((s) => <NeedCard key={s.id} s={s} busy={busy === s.id} onFix={onFixChannels}
              onNotSent={() => void run(s, () => inboxService.notSent(s.id), t('ui.InboxOverlay.sent.markedNotSent'))}
              onResend={() => void run(s, () => inboxService.resend(s.id), t('ui.InboxOverlay.sent.resent'))} />)}
          </div>
        )}

        <div className="mt-9 flex items-center">
          <h2 className="text-heading font-semibold text-[var(--color-text)]">{t('ui.InboxOverlay.sent.title')}</h2>
          <span className="ml-auto">
            <Segmented value={filter} onChange={setFilter} options={[
              { key: 'all', label: t('ui.InboxOverlay.sent.filterAll'), testid: 'inbox-sent-filter-all' },
              { key: 'mine', label: t('ui.InboxOverlay.sent.filterMine'), testid: 'inbox-sent-filter-mine' },
              { key: 'auto', label: t('ui.InboxOverlay.sent.filterAuto'), testid: 'inbox-sent-filter-auto' },
            ]} />
          </span>
        </div>
        {sent.length === 0 && <OverlayEmpty>{t('ui.InboxOverlay.sent.empty')}</OverlayEmpty>}
        {days.map((d) => (
          <div key={d}>
            <div className="mx-3 mb-0.5 mt-[18px] text-meta text-[var(--color-text-dim)]">{dayLabel(d, t)}</div>
            <ul className="flex flex-col gap-1">
              {sent.filter((s) => dayKey(s.sent_at ?? s.created_at) === d).map((s) => (
                <li key={s.id} data-testid="inbox-sent-row" className="group flex items-center gap-3.5 rounded-xl p-3 hover:bg-[var(--color-hover)]">
                  <Avatar name={s.to_label} size="sm" />
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-1.5">
                      <span className="text-body font-semibold text-[var(--color-text)]">{s.to_label}</span>
                      <ChannelPill kind={s.source_kind} label={s.channel_label} />
                      {s.origin === 'auto' && (
                        <span className="inline-flex h-[18px] items-center rounded-[5px] px-1.5 text-meta text-[var(--color-zone-work)]"
                          style={{ background: 'color-mix(in srgb, var(--color-zone-work) 10%, transparent)' }}>{t('ui.InboxOverlay.sent.auto')}</span>
                      )}
                      {s.has_reply && s.reply_item_id && (
                        <button type="button" className="btn-sm btn-soft" data-testid="inbox-sent-has-reply"
                          onClick={() => onGotoItem(s.reply_item_id!)}>
                          {t('ui.InboxOverlay.sent.hasReply')}
                        </button>
                      )}
                    </span>
                    <span className="mt-0.5 block truncate text-small text-[var(--color-text-dim)]">
                      {clockTime(s.sent_at ?? s.created_at)} · {s.text.split('\n').find((l) => l.trim()) ?? ''}
                    </span>
                  </span>
                  {webUrl(s.url) && (
                    <button type="button" className="icon-btn text-[var(--color-text-muted)] opacity-0 hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] group-hover:opacity-100 focus-visible:opacity-100"
                      onClick={() => void openExternal(webUrl(s.url)!)} data-testid="inbox-sent-open" title={t('ui.InboxOverlay.sent.openAt')} aria-label={t('ui.InboxOverlay.sent.openAt')}>
                      <span className="material-symbols-outlined icon-sm">open_in_new</span>
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}

function NeedCard({ s, busy, onFix, onNotSent, onResend }: {
  s: SendOut; busy: boolean; onFix: () => void; onNotSent: () => void; onResend: () => void;
}) {
  const { t } = useTranslation();
  const unknown = s.status !== 'failed';
  const v = unknown ? '--color-status-warning' : '--color-status-error';
  const note = unknown
    ? t('ui.InboxOverlay.sent.unknownNote')
    : s.error || t(s.blocked_by_source ? 'ui.InboxOverlay.sent.blockedNote' : 'ui.InboxOverlay.sent.failedNote');
  return (
    <div data-testid="inbox-need-card" data-status={s.status}
      className="flex items-start gap-3 rounded-xl px-4 py-3.5" style={{ background: `color-mix(in srgb, var(${v}) 8%, transparent)` }}>
      <span className="material-symbols-outlined icon-sm mt-0.5" style={{ color: `var(${v})` }} aria-hidden>{unknown ? 'warning' : 'error'}</span>
      <span className="min-w-0 flex-1">
        <span className="block text-body font-semibold text-[var(--color-text)]">
          {unknown ? t('ui.InboxOverlay.sent.unknownTitle', { who: s.to_label }) : t('ui.InboxOverlay.sent.failedTitle', { who: s.to_label })}
        </span>
        <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-small text-[var(--color-text-muted)]">
          <ChannelPill kind={s.source_kind} label={s.channel_label} />
          <span>{clockTime(s.created_at)} · {note}</span>
        </span>
      </span>
      <span className="flex flex-wrap items-center justify-end gap-1.5">
        {unknown ? (
          <>
            <button type="button" className="btn-md btn-secondary" disabled={!webUrl(s.url)}
              onClick={() => { const u = webUrl(s.url); if (u) void openExternal(u); }} data-testid="inbox-need-look">{t('ui.InboxOverlay.sent.goLook')}</button>
            <button type="button" className="btn-md btn-secondary" disabled={busy}
              onClick={onNotSent} data-testid="inbox-need-not-sent">{t('ui.InboxOverlay.sent.notSent')}</button>
          </>
        ) : s.blocked_by_source ? (
          <>
            <button type="button" className="btn-md btn-secondary" onClick={onFix} data-testid="inbox-need-fix">{t('ui.InboxOverlay.sent.fixChannel')}</button>
            <button type="button" className="btn-md btn-secondary disabled:opacity-50" disabled data-testid="inbox-need-resend">{t('ui.InboxOverlay.sent.resend')}</button>
          </>
        ) : (
          <button type="button" className="btn-md btn-primary" disabled={busy}
            onClick={onResend} data-testid="inbox-need-resend">{t('ui.InboxOverlay.sent.resend')}</button>
        )}
      </span>
    </div>
  );
}
