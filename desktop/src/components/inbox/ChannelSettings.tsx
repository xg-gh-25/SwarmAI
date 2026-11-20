/**
 * ChannelSettings — the gear page of the Inbox: one row per source (icon, backend,
 * identity, pull interval, health; a source that is not ok shows its fix and 重试),
 * then the feeds the Inbox watches (FeedEditor) (design docs/design/2026-11-20-inbox.md §7).
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { overlayQuery, invalidateOverlay } from '../layout/overlayQueries';
import { OverlayEmpty, OverlayError, OverlayLoading } from '../layout/OverlayState';
import { inboxService, type SourceOut } from '../../services/inbox';
import { Avatar, channelIcon, channelVar, intervalLabel } from './parts';
import { FeedEditor } from './FeedEditor';

const HEALTH_VAR: Record<SourceOut['health'], string> = {
  ok: '--color-status-success',
  auth_failed: '--color-status-error',
  error: '--color-status-error',
  read_only: '--color-status-warning',
};

export function ChannelSettings({ onBack, notify }: { onBack: () => void; notify: (msg: string) => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const q = useQuery(overlayQuery.inboxSources());
  const [busy, setBusy] = useState<string | null>(null);

  const retry = async (s: SourceOut) => {
    if (busy) return;
    setBusy(s.id);
    try {
      await inboxService.retrySource(s.id);
      notify(t('ui.InboxOverlay.channels.retried', { name: s.channel_label }));
    } catch {
      notify(t('ui.InboxOverlay.actionError'));
    } finally {
      setBusy(null);
      void invalidateOverlay(qc, 'inbox');
    }
  };

  const healthText = (s: SourceOut) =>
    s.health === 'ok' ? t('ui.InboxOverlay.channels.ok')
      : s.health === 'read_only' ? t('ui.InboxOverlay.channels.readOnly')
        : s.health === 'auth_failed'
          ? (s.fix ? t('ui.InboxOverlay.channels.authFailedFix', { fix: s.fix }) : t('ui.InboxOverlay.channels.authFailed'))
          : (s.last_error || s.fix || t('ui.InboxOverlay.channels.error'));

  return (
    <div className="min-h-0 flex-1 overflow-y-auto" data-testid="inbox-channels">
      <div className="mx-auto max-w-[680px] px-8 pb-14 pt-7">
        <p className="text-display font-semibold text-[var(--color-text)]">{t('ui.InboxOverlay.channels.title')}</p>
        <p className="mt-1 text-small text-[var(--color-text-dim)]">
          <button type="button" className="text-small text-[var(--color-primary)] hover:underline" onClick={onBack} data-testid="inbox-channels-back">
            {t('ui.InboxOverlay.channels.back')}
          </button>
          {' · '}{t('ui.InboxOverlay.channels.sub')}
        </p>
        {q.isPending ? <OverlayLoading /> : q.isError ? (
          <OverlayError testid="inbox-load-error" retryTestid="inbox-load-retry" message={t('ui.InboxOverlay.loadError')} onRetry={() => void q.refetch()} />
        ) : q.data.length === 0 ? (
          <OverlayEmpty icon="hub">{t('ui.InboxOverlay.channels.empty')}</OverlayEmpty>
        ) : (
          <ul className="mt-6 flex flex-col gap-1">
            {q.data.map((s) => (
              <li key={s.id} data-testid="inbox-source-row" data-health={s.health} className="flex items-center gap-3.5 rounded-xl p-3 hover:bg-[var(--color-hover)]">
                <Avatar name={s.channel_label} size="sm" icon={channelIcon(s.kind)} colorVar={channelVar(s.kind)} />
                <span className="min-w-0 flex-1">
                  <span className="block text-body font-semibold text-[var(--color-text)]">{s.channel_label}</span>
                  <span className="mt-0.5 block truncate text-small text-[var(--color-text-dim)]">
                    {[s.backend, s.identity, s.detail, intervalLabel(s.interval_s, t)].filter(Boolean).join(' · ')}
                  </span>
                </span>
                <span className="whitespace-nowrap text-small" style={{ color: `var(${HEALTH_VAR[s.health] ?? '--color-text-muted'})` }}>{healthText(s)}</span>
                {s.health !== 'ok' && s.health !== 'read_only' && (
                  <button type="button" className="btn-md btn-primary" disabled={busy === s.id}
                    onClick={() => void retry(s)} data-testid="inbox-source-retry">{t('ui.InboxOverlay.channels.retry')}</button>
                )}
              </li>
            ))}
          </ul>
        )}
        <div className="mt-9">
          <h2 className="mb-3 text-heading font-semibold text-[var(--color-text)]">{t('ui.InboxOverlay.channels.feedsTitle')}</h2>
          <FeedEditor />
        </div>
      </div>
    </div>
  );
}
