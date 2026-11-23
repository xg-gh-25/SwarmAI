/**
 * ChannelSettings — the gear page of the Inbox (design docs/design/2026-11-20-inbox.md §4,
 * §7; mockup docs/design/2026-11-20-inbox-mockup.html "渠道").
 *
 * The page answers "is each channel working, and what do I change?":
 *   1. the connected sources, one row each (icon, backend, identity, pull interval, health;
 *      a source that is not ok shows its fix and 重试, a read-only one why it cannot send).
 *      Its settings sit behind 设置 (disclosure): pull on/off, interval, Slack watched
 *      channels or mail extra addresses, a new token / password for the Web API and IMAP
 *      backends, and 移除 with a second confirm click (the backend then removes its
 *      items, drafts and send records too). The one feed row only has an interval: the
 *      feeds themselves are edited under 信息流;
 *   2. 加一个渠道 (AddChannel): one-click sources this machine can connect, and the
 *      manual Slack-token / IMAP forms;
 *   3. the feeds the Inbox watches (FeedEditor).
 * Every write refetches the Inbox queries, so the page shows what the backend stored.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { overlayQuery, invalidateOverlay } from '../layout/overlayQueries';
import { OverlayEmpty, OverlayError, OverlayLoading } from '../layout/OverlayState';
import { isApiError } from '../../services/api';
import { inboxService, type SourceOut, type SourcePatchRequest } from '../../services/inbox';
import { Avatar, channelIcon, channelVar, intervalLabel } from './parts';
import { FeedEditor } from './FeedEditor';
import { AddChannel } from './AddChannel';

const HEALTH_VAR: Record<SourceOut['health'], string> = {
  ok: '--color-status-success',
  auth_failed: '--color-status-error',
  error: '--color-status-error',
  read_only: '--color-status-warning',
};

// the backend accepts 300 s and up (schemas.inbox ge=300)
const INTERVALS = [300, 900, 1800, 3600, 3 * 3600, 6 * 3600, 24 * 3600];
const FEED_SOURCE_ID = 'feed';
/** The secret each manual backend reads (sources/{slack,mail}.py SECRET_NAMES). */
const SECRET_OF: Record<string, { name: string; labelKey: string }> = {
  'slack-web': { name: 'user_token', labelKey: 'ui.InboxOverlay.channels.secretToken' },
  imap: { name: 'password', labelKey: 'ui.InboxOverlay.channels.secretPassword' },
};
/** The option a person edits per kind (SourcePatchRequest). */
const LIST_OF: Record<string, { field: 'watched_channels' | 'extra_addresses'; labelKey: string; hintKey: string }> = {
  slack: { field: 'watched_channels', labelKey: 'ui.InboxOverlay.channels.watchedChannels', hintKey: 'ui.InboxOverlay.channels.watchedChannelsHint' },
  mail: { field: 'extra_addresses', labelKey: 'ui.InboxOverlay.channels.extraAddresses', hintKey: 'ui.InboxOverlay.channels.extraAddressesHint' },
};

const reasonOf = (e: unknown, fallback: string) => (isApiError(e) && e.statusCode < 500 && e.message.trim() ? e.message : fallback);
const listOf = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []);
/** A source is on unless the backend says it is off. */
const isOn = (s: SourceOut) => s.enabled !== false;

export function ChannelSettings({ onBack, notify }: { onBack: () => void; notify: (msg: string) => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const q = useQuery(overlayQuery.inboxSources());
  const [busy, setBusy] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

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
    !isOn(s) ? t('ui.InboxOverlay.channels.off')
      : s.health === 'ok' ? t('ui.InboxOverlay.channels.ok')
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
              <li key={s.id} data-testid="inbox-source-row" data-health={s.health} data-enabled={isOn(s) ? 'true' : 'false'}
                className="rounded-xl hover:bg-[var(--color-hover)]">
                <div className={`flex items-center gap-3.5 p-3 ${isOn(s) ? '' : 'opacity-60'}`}>
                  <Avatar name={s.channel_label} size="sm" icon={channelIcon(s.kind)} colorVar={channelVar(s.kind)} />
                  <span className="min-w-0 flex-1">
                    <span className="block text-body font-semibold text-[var(--color-text)]">{s.channel_label}</span>
                    <span className="mt-0.5 block truncate text-small text-[var(--color-text-dim)]">
                      {[s.backend, s.identity, s.detail, intervalLabel(s.interval_s, t)].filter(Boolean).join(' · ')}
                    </span>
                    {s.health === 'read_only' && s.status_detail && (
                      <span className="mt-0.5 block text-small text-[var(--color-text-muted)]" data-testid="inbox-source-status-detail">
                        {s.status_detail}
                      </span>
                    )}
                  </span>
                  <span className="whitespace-nowrap text-small"
                    style={{ color: `var(${isOn(s) ? (HEALTH_VAR[s.health] ?? '--color-text-muted') : '--color-text-muted'})` }}>
                    {healthText(s)}
                  </span>
                  {isOn(s) && s.health !== 'ok' && s.health !== 'read_only' && (
                    <button type="button" className="btn-md btn-primary" disabled={busy === s.id}
                      onClick={() => void retry(s)} data-testid="inbox-source-retry">{t('ui.InboxOverlay.channels.retry')}</button>
                  )}
                  <button type="button" className="btn-md btn-ghost" aria-expanded={open === s.id}
                    aria-label={t('ui.InboxOverlay.channels.settingsFor', { name: s.channel_label })}
                    onClick={() => setOpen((o) => (o === s.id ? null : s.id))} data-testid="inbox-source-settings">
                    {t('ui.InboxOverlay.channels.settings')}
                  </button>
                </div>
                {open === s.id && <SourceSettings source={s} notify={notify} onRemoved={() => setOpen(null)} />}
              </li>
            ))}
          </ul>
        )}
        <AddChannel notify={notify} />
        <div className="mt-9">
          <h2 className="mb-3 text-heading font-semibold text-[var(--color-text)]">{t('ui.InboxOverlay.channels.feedsTitle')}</h2>
          <FeedEditor />
        </div>
      </div>
    </div>
  );
}

function SourceSettings({ source: s, notify, onRemoved }: { source: SourceOut; notify: (msg: string) => void; onRemoved: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const list = LIST_OF[s.kind];
  const secret = SECRET_OF[s.backend];
  const isFeed = s.id === FEED_SOURCE_ID;
  const [values, setValues] = useState(() => (list ? listOf(s.options?.[list.field]).join('\n') : ''));
  const [secretValue, setSecretValue] = useState('');
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (fn: () => Promise<unknown>, done: string | null) => {
    if (busy) return false;
    setBusy(true);
    setError(null);
    try {
      await fn();
      if (done) notify(done);
      return true;
    } catch (e) {
      setError(reasonOf(e, t('ui.InboxOverlay.actionError')));
      return false;
    } finally {
      setBusy(false);
      void invalidateOverlay(qc, 'inbox');
    }
  };

  const patch = (p: SourcePatchRequest) =>
    run(() => inboxService.updateSource(s.id, p), t('ui.InboxOverlay.channels.saved', { name: s.channel_label }));

  const saveSecret = async () => {
    if (!secret || !secretValue) return;
    const value = secretValue;
    setSecretValue('');   // a secret never stays in the form
    await run(() => inboxService.setSourceSecret(s.id, secret.name, value), t('ui.InboxOverlay.channels.secretSaved'));
  };

  const intervals = INTERVALS.includes(s.interval_s) ? INTERVALS : [...INTERVALS, s.interval_s].sort((a, b) => a - b);

  return (
    <div className="flex flex-col gap-4 px-3 pb-4 pl-[54px]" data-testid="inbox-source-settings-panel">
      <div className="flex flex-wrap items-center gap-4">
        {!isFeed && (
          <label className="flex items-center gap-2 text-small text-[var(--color-text-muted)]">
            {t('ui.InboxOverlay.channels.enabled')}
            <button type="button" role="switch" aria-checked={isOn(s)} disabled={busy} data-testid="inbox-source-enabled"
              onClick={() => void patch({ enabled: !isOn(s) })}
              className={`btn-sm border ${isOn(s) ? 'border-[var(--color-border-strong)] text-[var(--color-text)]' : 'border-[var(--color-border)] text-[var(--color-text-faint)]'}`}>
              {isOn(s) ? t('ui.InboxOverlay.channels.enabledOn') : t('ui.InboxOverlay.channels.enabledOff')}
            </button>
          </label>
        )}
        <label className="flex items-center gap-2 text-small text-[var(--color-text-muted)]">
          {t('ui.InboxOverlay.channels.interval')}
          <select value={s.interval_s} disabled={busy} data-testid="inbox-source-interval"
            onChange={(e) => void patch({ interval_s: Number(e.target.value) })}
            className="rounded border border-[var(--color-border)] bg-transparent px-1.5 py-1 text-meta text-[var(--color-text-muted)]">
            {intervals.map((v) => (
              <option key={v} value={v}>
                {v % 3600 === 0 ? t('ui.InboxOverlay.channels.intervalHours', { count: v / 3600 }) : t('ui.InboxOverlay.channels.intervalMinutes', { count: Math.round(v / 60) })}
              </option>
            ))}
          </select>
        </label>
      </div>

      {list && (
        <label className="block text-small text-[var(--color-text-muted)]">
          {t(list.labelKey)}
          <span className="ml-2 text-meta text-[var(--color-text-faint)]">{t(list.hintKey)}</span>
          <textarea rows={3} value={values} disabled={busy} onChange={(e) => setValues(e.target.value)} spellCheck={false}
            data-testid="inbox-source-list-input"
            className="mt-1 block w-full max-w-[460px] rounded-md border border-[var(--color-border)] bg-transparent px-2 py-1.5 font-mono text-small text-[var(--color-text)]" />
          <button type="button" className="btn-sm btn-secondary mt-2" disabled={busy} data-testid="inbox-source-list-save"
            onClick={() => void patch({ [list.field]: values.split(/[\n,]/).map((v) => v.trim()).filter(Boolean) })}>
            {t('ui.InboxOverlay.channels.save')}
          </button>
        </label>
      )}

      {secret && (
        <label className="block text-small text-[var(--color-text-muted)]">
          {t(secret.labelKey)}
          <span className="mt-1 flex max-w-[460px] gap-2">
            <input type="password" autoComplete="off" spellCheck={false} value={secretValue} disabled={busy}
              onChange={(e) => setSecretValue(e.target.value)} data-testid="inbox-source-secret-input"
              className="min-w-0 flex-1 rounded-md border border-[var(--color-border)] bg-transparent px-2 py-1.5 font-mono text-small text-[var(--color-text)]" />
            <button type="button" className="btn-sm btn-secondary" disabled={busy || !secretValue} data-testid="inbox-source-secret-save"
              onClick={() => void saveSecret()}>{t('ui.InboxOverlay.channels.secretSave')}</button>
          </span>
        </label>
      )}

      {!isFeed && (
        <div className="flex items-center gap-3">
          {confirm ? (
            <>
              <button type="button" className="btn-sm btn-danger" disabled={busy} data-testid="inbox-source-remove-confirm"
                onClick={() => void run(() => inboxService.removeSource(s.id), t('ui.InboxOverlay.channels.removed', { name: s.channel_label }))
                  .then((ok) => { if (ok) onRemoved(); })}>
                {t('ui.InboxOverlay.channels.removeConfirm')}
              </button>
              <button type="button" className="btn-sm btn-ghost" disabled={busy} onClick={() => setConfirm(false)}>
                {t('ui.InboxOverlay.channels.cancel')}
              </button>
            </>
          ) : (
            <button type="button" className="text-small text-[var(--color-text-faint)] hover:text-[var(--color-status-error)]"
              onClick={() => setConfirm(true)} data-testid="inbox-source-remove">
              {t('ui.InboxOverlay.channels.remove')}
            </button>
          )}
        </div>
      )}

      {error && <p role="alert" className="text-small text-[var(--color-status-error)]">{error}</p>}
    </div>
  );
}
