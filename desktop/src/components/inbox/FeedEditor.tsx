/**
 * FeedEditor — the feeds the Inbox watches (信息流), on the channel settings page.
 *
 * The old Community page's Watching tab, moved here (design docs/design/2026-11-20-inbox.md
 * §7 and §10; the Community page is deleted), pointed at `/api/inbox/feeds`.
 *
 * Add / toggle / tier / delete a feed, and edit a feed's members. Every write refetches
 * so the page shows what was persisted; a backend refusal shows its reason.
 */
import { useCallback, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { overlayQuery } from '../layout/overlayQueries';
import { inboxService } from '../../services/inbox';
import { isApiError } from '../../services/api';
import type { FeedSource } from '../../services/inbox';
import { OverlayLoading, OverlayError, OverlayEmpty } from '../layout/OverlayState';

function StateBanner({ loading, error, empty, emptyMsg, onRetry }: {
  loading: boolean; error: boolean; empty: boolean; emptyMsg: string; onRetry?: () => void;
}) {
  const { t } = useTranslation();
  if (loading) return <OverlayLoading />;
  if (error) {
    return (
      <OverlayError testid="inbox-feeds-error" retryTestid="inbox-feeds-retry"
        message={t('ui.InboxOverlay.feeds.loadError')} onRetry={onRetry ?? (() => {})} />
    );
  }
  if (empty) return <OverlayEmpty>{emptyMsg}</OverlayEmpty>;
  return null;
}

function SectionLabel({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={`text-meta uppercase tracking-wide text-[var(--color-text-faint)] font-semibold ${className}`}>
      {children}
    </div>
  );
}

export function FeedEditor() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const q = useQuery(overlayQuery.inboxFeeds());
  const reload = useCallback(() => { void qc.invalidateQueries({ queryKey: overlayQuery.inboxFeeds().queryKey }); }, [qc]);
  const loading = q.isPending;
  const error = q.isError && q.data === undefined;
  const sources = q.data ?? [];
  const [busy, setBusy] = useState<string | null>(null); // id being mutated
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null); // 2-step delete
  const [actionErr, setActionErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null); // feed id whose members are shown

  // A mutation: run it, then refetch so the UI reflects the real persisted state
  // (no optimistic-only — the write goes through the shared config lock and we want
  // the truth, incl. managed_by:user flipping).
  const mutate = useCallback(
    async (id: string, fn: () => Promise<void>) => {
      setBusy(id);
      setActionErr(null);
      try {
        await fn();
        setConfirmDelete((c) => (c === id ? null : c)); // clear armed-delete only on SUCCESS
        reload();
      } catch (e) {
        // Surface the backend's real reason (FastAPI 422/409 `detail`) instead of a
        // generic "Try again": a validation failure ("must be an https:// URL",
        // "already exists") is actionable. The shared api client turns a failed
        // response into an ApiError whose message is that `detail` (services/api.ts
        // parseApiError), so read it there; a 5xx / outage keeps the generic text.
        const reason = isApiError(e) && e.statusCode < 500 && e.message.trim() ? e.message : null;
        setActionErr(reason ?? t('ui.InboxOverlay.feeds.updateError', { id }));
        // leave confirmDelete as-is on failure — the row keeps its state, user can retry
      } finally {
        setBusy(null);
      }
    },
    [reload, t],
  );

  const banner = <StateBanner loading={loading} error={error} onRetry={reload} empty={sources.length === 0}
    emptyMsg={t('ui.InboxOverlay.feeds.noSources')} />;
  // Show the banner for loading/error; but on empty still render the add-form (below),
  // so a fresh user can add their first source.
  if (loading || error) return banner;

  return (
    <div className="max-w-[760px]">
      <SectionLabel className="mb-2">{t('ui.InboxOverlay.feeds.sourcesLabel')}</SectionLabel>
      {actionErr && (
        <div className="mb-2 text-meta text-[var(--color-status-error)]">{actionErr}</div>
      )}
      <div className="flex flex-col gap-0.5">
        {sources.map((s) => (
          <div key={s.id} data-testid="inbox-feed-row">
          <div
            className="rounded-lg px-2 py-2 hover:bg-[var(--color-hover)] transition-colors grid grid-cols-[auto_1fr_auto_auto_auto_auto] items-center gap-3"
          >
            {/* expand toggle — only for feeds that HAVE editable members */}
            {s.memberKind !== null ? (
              <button
                type="button"
                data-testid="source-expand"
                onClick={() => setExpanded((e) => (e === s.id ? null : s.id))}
                className="material-symbols-outlined icon-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                aria-expanded={expanded === s.id}
                aria-label={expanded === s.id
                  ? t('ui.InboxOverlay.feeds.hideMembersFor', { name: s.name })
                  : t('ui.InboxOverlay.feeds.showMembersFor', { name: s.name })}
                title={expanded === s.id ? t('ui.InboxOverlay.feeds.hideMembers') : t('ui.InboxOverlay.feeds.showMembers')}
              >
                {expanded === s.id ? 'expand_more' : 'chevron_right'}
              </button>
            ) : (
              <span className="w-4" />
            )}
            <div className="min-w-0">
              <div className="text-body text-[var(--color-text)] truncate">{s.name}</div>
              <div className="text-meta text-[var(--color-text-dim)] truncate">
                {s.type}
                {s.memberKind !== null && ` · ${s.memberCount} ${s.memberKind}`}
                {` · ${s.managedBy}`}
              </div>
            </div>
            {/* tier editor */}
            <select
              data-testid="source-tier"
              value={s.tier}
              disabled={busy === s.id}
              onChange={(e) => mutate(s.id, () => inboxService.updateFeed(s.id, { tier: e.target.value }))}
              className="text-meta bg-transparent border border-[var(--color-border)] rounded px-1.5 py-1 text-[var(--color-text-muted)]"
            >
              {['frontier', 'leaders', 'research', 'engineering', 'opinion', 'aggregate'].map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
            {/* enabled toggle */}
            <button
              type="button"
              data-testid="source-toggle"
              role="switch"
              aria-checked={s.enabled}
              disabled={busy === s.id}
              onClick={() => mutate(s.id, () => inboxService.updateFeed(s.id, { enabled: !s.enabled }))}
              className={[
                'text-meta px-2 py-1 rounded border transition-colors',
                s.enabled
                  ? 'text-[var(--color-text)] border-[var(--color-border-strong)]'
                  : 'text-[var(--color-text-faint)] border-[var(--color-border)]',
              ].join(' ')}
            >
              {s.enabled ? t('ui.InboxOverlay.feeds.enabledOn') : t('ui.InboxOverlay.feeds.enabledOff')}
            </button>
            {/* delete — 2-step confirm */}
            {confirmDelete === s.id ? (
              <button
                type="button"
                data-testid="source-delete-confirm"
                disabled={busy === s.id}
                onClick={() => mutate(s.id, () => inboxService.deleteFeed(s.id))}
                aria-label={t('ui.InboxOverlay.feeds.confirmDeleteSource', { name: s.name })}
                className="btn-sm btn-danger"
              >
                {t('ui.InboxOverlay.feeds.confirm')}
              </button>
            ) : (
              <button
                type="button"
                data-testid="source-delete"
                onClick={() => setConfirmDelete(s.id)}
                aria-label={t('ui.InboxOverlay.feeds.deleteSource', { name: s.name })}
                className="material-symbols-outlined icon-sm text-[var(--color-text-faint)] hover:text-[var(--color-status-error)] focus-visible:text-[var(--color-status-error)]"
              >
                delete
              </button>
            )}
          </div>
          {expanded === s.id && s.memberKind !== null && (
            <MemberEditor source={s} onChanged={reload} />
          )}
          </div>
        ))}
      </div>

      <AddSourceForm onAdded={reload} busy={!!busy} />

      <p className="mt-3 text-meta text-[var(--color-text-faint)] leading-relaxed max-w-[640px]">
        {t('ui.InboxOverlay.feeds.editsNoteBefore')}<span className="font-mono">user</span>{t('ui.InboxOverlay.feeds.editsNoteAfter')}
      </p>
    </div>
  );
}

// Member editor — the individual urls/keywords/queries inside one feed. Reuses the
// same busy/confirm/error discipline as the feed rows. Writes go through the shared
// config lock (managed_by:user); refetch after each so the UI reflects persisted truth.
function MemberEditor({ source, onChanged }: { source: FeedSource; onChanged: () => void }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<string | null>(null); // member value armed for delete
  const [err, setErr] = useState<string | null>(null);
  const [adding, setAdding] = useState('');

  const run = useCallback(
    async (fn: () => Promise<void>, clearConfirm: boolean) => {
      setBusy(true);
      setErr(null);
      try {
        await fn();
        if (clearConfirm) setConfirm(null);
        onChanged();
      } catch {
        setErr(t('ui.InboxOverlay.feeds.memberSaveError'));
      } finally {
        setBusy(false);
      }
    },
    [onChanged, t],
  );

  const submitAdd = () => {
    const v = adding.trim();
    if (!v) return;
    run(async () => { await inboxService.addFeedMember(source.id, v); setAdding(''); }, false);
  };

  return (
    <div data-testid="member-editor" className="ml-7 mb-1 pl-3 border-l border-[var(--color-border)] flex flex-col gap-0.5 max-w-[720px]">
      {err && <div className="text-meta text-[var(--color-status-error)] py-1">{err}</div>}
      {source.members.length === 0 && (
        <div className="text-meta text-[var(--color-text-faint)] py-1">{t('ui.InboxOverlay.feeds.noMembers')}</div>
      )}
      {source.members.map((m) => (
        <div key={m} data-testid="member-row" className="flex items-center gap-2 py-0.5 group">
          <span className="flex-1 text-meta font-mono text-[var(--color-text-muted)] truncate" title={m}>{m}</span>
          {confirm === m ? (
            <button
              type="button"
              data-testid="member-delete-confirm"
              disabled={busy}
              onClick={() => run(() => inboxService.deleteFeedMember(source.id, m), true)}
              aria-label={t('ui.InboxOverlay.feeds.confirmRemoveMember', { member: m })}
              className="btn-sm btn-danger"
            >
              {t('ui.InboxOverlay.feeds.confirm')}
            </button>
          ) : (
            <button
              type="button"
              data-testid="member-delete"
              onClick={() => setConfirm(m)}
              aria-label={t('ui.InboxOverlay.feeds.removeMember', { member: m })}
              className="material-symbols-outlined icon-xs text-[var(--color-text-faint)] hover:text-[var(--color-status-error)] opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
            >
              close
            </button>
          )}
        </div>
      ))}
      {source.membersTruncated && (
        <div className="text-meta text-[var(--color-text-faint)] py-0.5">
          {t('ui.InboxOverlay.feeds.membersTruncated', { shown: source.members.length, total: source.memberCount })}
        </div>
      )}
      <div className="flex items-center gap-2 pt-1">
        <input
          data-testid="member-add-input"
          value={adding}
          disabled={busy}
          onChange={(e) => setAdding(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') submitAdd(); }}
          placeholder={t('ui.InboxOverlay.feeds.addMemberPlaceholder', {
            kind: source.memberKind?.replace(/s$/, '') ?? t('ui.InboxOverlay.feeds.memberFallback'),
          })}
          className="flex-1 text-meta bg-transparent border border-[var(--color-border)] rounded px-2 py-1"
        />
        <button
          type="button"
          data-testid="member-add-submit"
          disabled={busy || !adding.trim()}
          onClick={submitAdd}
          className="btn-sm btn-ghost text-[var(--panel-accent,var(--color-primary))] disabled:opacity-40"
        >
          {t('ui.InboxOverlay.feeds.add')}
        </button>
      </div>
    </div>
  );
}

// Add-source form (collapsed to a "+ add" affordance until opened).
function AddSourceForm({ onAdded, busy }: { onAdded: () => void; busy: boolean }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [id, setId] = useState('');
  const [name, setName] = useState('');
  const [type, setType] = useState('rss');
  const [tier, setTier] = useState('engineering');
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const submit = useCallback(async () => {
    setErr(null);
    if (!id.trim() || !name.trim()) { setErr(t('ui.InboxOverlay.feeds.idNameRequired')); return; }
    setSaving(true);
    try {
      await inboxService.addFeed({ id: id.trim(), name: name.trim(), type, tier });
      setId(''); setName(''); setOpen(false);
      onAdded();
    } catch {
      setErr(t('ui.InboxOverlay.feeds.addSourceError'));
    } finally {
      setSaving(false);
    }
  }, [id, name, type, tier, onAdded, t]);

  if (!open) {
    return (
      <button
        type="button"
        data-testid="source-add-open"
        onClick={() => setOpen(true)}
        className="mt-3 text-small text-[var(--panel-accent,var(--color-primary))] hover:underline"
      >
        {t('ui.InboxOverlay.feeds.addSource')}
      </button>
    );
  }

  return (
    <div className="mt-3 rounded-lg border border-[var(--color-border)] p-3 flex flex-col gap-2 max-w-[560px]" data-testid="source-add-form">
      {err && <div className="text-meta text-[var(--color-status-error)]">{err}</div>}
      <div className="flex gap-2">
        <input data-testid="add-id" value={id} onChange={(e) => setId(e.target.value)} placeholder={t('ui.InboxOverlay.feeds.idPlaceholder')}
          className="flex-1 text-small bg-transparent border border-[var(--color-border)] rounded px-2 py-1" />
        <input data-testid="add-name" value={name} onChange={(e) => setName(e.target.value)} placeholder={t('ui.InboxOverlay.feeds.namePlaceholder')}
          className="flex-1 text-small bg-transparent border border-[var(--color-border)] rounded px-2 py-1" />
      </div>
      <div className="flex gap-2 items-center">
        <select data-testid="add-type" value={type} onChange={(e) => setType(e.target.value)}
          className="text-meta bg-transparent border border-[var(--color-border)] rounded px-1.5 py-1">
          {/* Must stay in sync with backend FeedType (jobs/models.py). `github-people`
              was MISSING here — the backend accepts it and it has editable `logins`
              members (MEMBER_KEY), but users had no UI to create one (frontend
              zero-wiring). Added next to its github-community sibling. */}
          {['rss', 'web-search', 'github-releases', 'hacker-news', 'trending', 'github-trending', 'github-community', 'github-people', 'weibo-trending', 'eastmoney-market'].map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
        <select data-testid="add-tier" value={tier} onChange={(e) => setTier(e.target.value)}
          className="text-meta bg-transparent border border-[var(--color-border)] rounded px-1.5 py-1">
          {['frontier', 'leaders', 'research', 'engineering', 'opinion', 'aggregate'].map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
        <span className="flex-1" />
        <button type="button" onClick={() => setOpen(false)} className="btn-sm btn-ghost">{t('ui.InboxOverlay.feeds.cancel')}</button>
        <button type="button" data-testid="add-submit" disabled={saving || busy} onClick={submit}
          className="btn-sm btn-secondary disabled:opacity-50">
          {saving ? t('ui.InboxOverlay.feeds.adding') : t('ui.InboxOverlay.feeds.add')}
        </button>
      </div>
    </div>
  );
}
