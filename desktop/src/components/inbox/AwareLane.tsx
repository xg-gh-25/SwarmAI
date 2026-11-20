/**
 * AwareLane — 需要知道: one row per thing worth knowing, no answer expected (design
 * docs/design/2026-11-20-inbox.md §7). Avatar, the one sentence in bold, a muted line
 * (channel pill · who · where · when). Send-to-chat and 知道了 show on hover / focus.
 */
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { invalidateOverlay } from '../layout/overlayQueries';
import { OverlayEmpty, OverlayError, OverlayLoading } from '../layout/OverlayState';
import { inboxService, type ItemOut } from '../../services/inbox';
import { Avatar, ChannelPill, relTime } from './parts';

export function AwareLane({ items, loading, error, onRetry, onHandled, onToChat, notify }: {
  items: ItemOut[]; loading: boolean; error: boolean; onRetry: () => void;
  onHandled: (id: string) => void; onToChat: (item: ItemOut) => void; notify: (msg: string) => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);

  const done = async (item: ItemOut) => {
    if (busy) return;
    setBusy(item.id);
    try {
      await inboxService.action(item.id, 'done');
      onHandled(item.id);
      notify(t('ui.InboxOverlay.aware.marked'));
    } catch {
      notify(t('ui.InboxOverlay.actionError'));
    } finally {
      setBusy(null);
      void invalidateOverlay(qc, 'inbox');
    }
  };

  return (
    <div className="min-h-0 flex-1 overflow-y-auto" data-testid="inbox-aware">
      <div className="mx-auto max-w-[680px] px-8 pb-14 pt-7">
        {loading ? <OverlayLoading /> : error ? (
          <OverlayError testid="inbox-load-error" retryTestid="inbox-load-retry" message={t('ui.InboxOverlay.loadError')} onRetry={onRetry} />
        ) : items.length === 0 ? (
          <OverlayEmpty icon="check_circle">{t('ui.InboxOverlay.aware.empty')}</OverlayEmpty>
        ) : (
          <>
            <p className="text-display font-semibold text-[var(--color-text)]">{t('ui.InboxOverlay.aware.lead', { count: items.length })}</p>
            <p className="mt-1 text-small text-[var(--color-text-dim)]">{t('ui.InboxOverlay.aware.sub')}</p>
            <ul className="mt-6 flex flex-col gap-1">
              {items.map((a) => (
                <li key={a.id} data-testid="inbox-aware-row" className="group flex items-center gap-3.5 rounded-xl p-3 hover:bg-[var(--color-hover)]">
                  <Avatar name={a.author} size="sm" />
                  <span className="min-w-0 flex-1">
                    <span className="text-body font-semibold text-[var(--color-text)]">{a.ask || a.excerpt}</span>
                    <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-small text-[var(--color-text-dim)]">
                      <ChannelPill kind={a.source_kind} label={a.channel_label} />
                      <span>{[a.author, a.where, relTime(a.received_at, t)].filter(Boolean).join(' · ')}</span>
                    </span>
                  </span>
                  <span className="flex gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                    <button type="button" className="icon-btn text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]"
                      onClick={() => onToChat(a)} title={t('ui.InboxOverlay.toChat')} aria-label={t('ui.InboxOverlay.toChat')} data-testid="inbox-aware-chat">
                      <span className="material-symbols-outlined icon-sm">chat</span>
                    </button>
                    <button type="button" className="icon-btn text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]"
                      onClick={() => void done(a)} disabled={busy === a.id}
                      title={t('ui.InboxOverlay.aware.done')} aria-label={t('ui.InboxOverlay.aware.done')} data-testid="inbox-aware-done">
                      <span className="material-symbols-outlined icon-sm">done</span>
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}
