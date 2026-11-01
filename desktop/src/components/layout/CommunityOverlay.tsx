/**
 * CommunityOverlay — SwarmAI's two-way membrane with the outside world.
 *
 * Design: Knowledge/Designs/2026-08-12-community-overlay-redesign-mockup.html (approved),
 * rebuilt by run_ced271e8. Two tabs:
 *   Inbound   — recent signal digests + reports (local files → Canvas)
 *   Watching  — configured feeds — add/toggle/tier/delete a feed AND members
 * (Hot Topics and the Outbound GitHub-engagement tab read one user's GitHub_Community
 *  DDD artifacts and were removed: usable-by-anyone P7.)
 *
 * UI craft (KNOWLEDGE.md 5-check + s_frontend-design/data/design-judgment.md):
 *   - NO per-row border box-wall — whitespace + hover-highlight groups (Tufte data-ink,
 *     Refactoring UI "fewer borders").
 *   - One focus per tab (Von Restorff): Inbound = the latest-report hero.
 *
 * Link routing: LOCAL synthesis files (our Signals/Reports .md) → Canvas via swarm:open-file.
 *
 * Honesty rules (Gate-1, run_5165013e): no fabricated data; every tab has loading/error/
 * empty branches (the 5-overlay fetch pattern). Clone lineage: NeedYouOverlay + BrainHub.
 * Chrome/geometry owned by OverlayHost.
 */
import { Tabs } from '../overlay';
import { useCallback, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { overlayQuery, type CommunityPart } from './overlayQueries';
import {
  communityService,
  type CommunityFeed,
  type CommunitySource,
} from '../../services/community';
import { OverlayLoading, OverlayError, OverlayEmpty } from './OverlayState';

interface CommunityContentProps {
  /** Close the overlay (host-owned) — called before opening a file in Canvas. */
  close: () => void;
}

type TabId = 'inbound' | 'watching';

// Inbound = what we read from the world, Watching = what we watch.
const TAB_IDS: TabId[] = ['inbound', 'watching'];

export function CommunityContent({ close }: CommunityContentProps) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<TabId>('inbound');
  const TABS: Array<{ id: TabId; label: string; sub: string }> = TAB_IDS.map((id) =>
    id === 'inbound'
      ? { id, label: t('ui.CommunityOverlay.tabInbound'), sub: t('ui.CommunityOverlay.tabInboundSub') }
      : { id, label: t('ui.CommunityOverlay.tabWatching'), sub: t('ui.CommunityOverlay.tabWatchingSub') },
  );

  return (
    <div className="flex-1 flex flex-col overflow-hidden" data-testid="community-overlay">
      <div className="px-5 pt-4 pb-0">
        <h2 className="text-heading font-semibold text-[var(--color-text)]">{t('ui.CommunityOverlay.title')}</h2>
        <p className="mt-0.5 text-meta text-[var(--color-text-dim)]">
          {t('ui.CommunityOverlay.subtitle')}
        </p>
      </div>

      {/* Tabs */}
      <div className="px-5 py-2.5 border-b border-[var(--color-border)]">
        <Tabs label={t('ui.CommunityOverlay.title')} value={tab} onChange={setTab}
          items={TABS.map((tb) => ({ key: tb.id, label: tb.label, hint: tb.sub, testid: `community-tab-${tb.id}` }))} />
      </div>

      <div className="flex-1 overflow-y-auto px-5 py-4">
        {tab === 'inbound' && <InboundTab close={close} />}
        {tab === 'watching' && <SourcesTab />}
      </div>
    </div>
  );
}

// ── shared fetch-state hook (loading / error / empty) ──
// Through the shared overlay query of `part` (overlayQueries): a reopen inside the
// freshness window or after a nav hover shows the cached tab at once. null is the
// only error signal and any failure mode (rejection, sync throw, a missing method)
// lands in the visible error branch — overlayQuery.community makes them all a
// rejected query (run_a1f4c2d8).
function useFetch<T>(part: CommunityPart): {
  data: T | null;
  loading: boolean;
  error: boolean;
  reload: () => void;
} {
  const qc = useQueryClient();
  const q = useQuery(overlayQuery.community(part));
  const reload = useCallback(() => { void qc.invalidateQueries({ queryKey: overlayQuery.community(part).queryKey }); }, [qc, part]);
  return {
    data: (q.data as T | undefined) ?? null,
    loading: q.isPending,
    error: q.isError && q.data === undefined,
    reload,
  };
}

// First-load states through the shared OverlayState (left-nav-overlay-finish.md §1.2).
function StateBanner({ loading, error, empty, emptyMsg, onRetry }: {
  loading: boolean; error: boolean; empty: boolean; emptyMsg: string; onRetry?: () => void;
}) {
  const { t } = useTranslation();
  if (loading) return <OverlayLoading />;
  if (error) {
    return (
      <OverlayError
        testid="community-load-error"
        retryTestid="community-load-retry"
        message={t('ui.CommunityOverlay.loadError')}
        onRetry={onRetry ?? (() => {})}
      />
    );
  }
  if (empty) return <OverlayEmpty>{emptyMsg}</OverlayEmpty>;
  return null;
}

// A small section label — uppercase, muted (mockup .lbl). ONE representation; used to
// chunk a tab into whitespace-separated groups instead of boxing every row.
function SectionLabel({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`text-meta uppercase tracking-wide text-[var(--color-text-faint)] font-semibold ${className}`}>
      {children}
    </div>
  );
}

// ── Inbound tab — latest-report hero + daily signals ──
// IA (run_edcd9672 + redesign run_ced271e8): the latest weekly Report is the ONE hero;
// Hot Topics is the live demand ranking (heat bar); daily Signals are a plain list below.
// De-boxed: no per-row borders — whitespace + hover-highlight (Tufte / Refactoring UI).

function InboundTab({ close }: { close: () => void }) {
  const { t } = useTranslation();
  const { data, loading, error, reload } = useFetch<CommunityFeed>('feed');

  const openFile = useCallback(
    (path: string) => {
      close(); // close overlay before Canvas renders (BrainHub precedent)
      document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path } }));
    },
    [close],
  );

  // Split by category: Reports → hero card, everything else (Signals) → daily list.
  // Backend already returns newest-first, so [0] is the latest of each kind.
  const { latestReport, pastReports, signals } = useMemo(() => {
    const items = data?.items ?? [];
    const reports = items.filter((it) => it.category === 'Reports');
    const sigs = items.filter((it) => it.category !== 'Reports');
    return { latestReport: reports[0] ?? null, pastReports: reports.slice(1), signals: sigs };
  }, [data]);

  // Honest cap disclosure: the backend caps the WHOLE feed (Signals + Reports) at
  // feed_cap and flags `truncated`. Surface it instead of dropping it. The disclosed
  // number is `data.count` (the total capped items fetched) — NOT signals.length.
  const truncated = data?.truncated === true;
  const fetchedCount = data?.count ?? 0;

  // Hot Topics renders ABOVE as an independent SIBLING — never gated by the feed's
  // own loading/empty state (an empty file feed must not hide hot topics).
  return (
    <div className="flex flex-col gap-6 max-w-[760px]">
      {/* Latest weekly report — the ONE hero (Von Restorff): accent bar, no tile-wall */}
      {latestReport && (
        <div>
          <SectionLabel className="mb-2">{t('ui.CommunityOverlay.latestLabel')}</SectionLabel>
          <button
            type="button"
            onClick={() => openFile(latestReport.path)}
            data-testid="community-report-card"
            className="group relative w-full text-left rounded-[10px] bg-[var(--color-hover)]/50 hover:bg-[var(--color-hover)] transition-colors pl-[18px] pr-4 py-3.5 flex items-center gap-3
 before:content-[''] before:absolute before:left-0 before:top-2.5 before:bottom-2.5 before:w-[3px] before:rounded before:bg-[var(--panel-accent,var(--color-primary))]"
          >
            <span className="material-symbols-outlined icon-lg text-[var(--color-text-muted)]">bar_chart</span>
            <span className="flex-1 min-w-0">
              <span className="block text-heading font-semibold text-[var(--color-text)] truncate">{latestReport.name}</span>
              <span className="block text-meta text-[var(--color-text-dim)]">{t('ui.CommunityOverlay.weeklyReport')}</span>
            </span>
            {pastReports.length > 0 && (
              <span className="text-meta text-[var(--color-text-faint)] shrink-0">{t('ui.CommunityOverlay.pastReports', { count: pastReports.length })}</span>
            )}
            <span className="text-small text-[var(--panel-accent,var(--color-primary))] shrink-0">{t('ui.CommunityOverlay.open')}</span>
          </button>
        </div>
      )}

      {/* Daily signals — Signals only, newest-first, click → Canvas (LOCAL files) */}
      <div>
        <SectionLabel className="mb-2">{t('ui.CommunityOverlay.todaysSignals')}</SectionLabel>
        {loading || error || signals.length === 0 ? (
          <StateBanner loading={loading} error={error} onRetry={reload} empty={signals.length === 0}
            emptyMsg={t('ui.CommunityOverlay.noSignals')} />
        ) : (
          <div className="flex flex-col gap-0.5">
            {signals.map((it) => (
              <button
                key={it.path}
                type="button"
                onClick={() => openFile(it.path)}
                data-testid="community-feed-item"
                className="group w-full text-left rounded-lg px-2 py-2 hover:bg-[var(--color-hover)] transition-colors flex items-center gap-3"
              >
                <span className="flex-1 text-body text-[var(--color-text)] truncate">{it.name}</span>
                <span className="material-symbols-outlined icon-sm text-[var(--color-text-faint)] opacity-0 group-hover:opacity-100">
                  open_in_new
                </span>
              </button>
            ))}
            {truncated && (
              <div
                data-testid="community-feed-truncated"
                className="px-2 py-1.5 text-meta text-[var(--color-text-faint)]"
              >
                {t('ui.CommunityOverlay.feedTruncated', { count: fetchedCount })}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Watching tab — configured feeds (add/toggle/tier/delete + member editing) ──
// De-boxed (run_ced271e8): rows separated by hover-highlight, not per-row borders. It's
// a config surface, so it keeps its inline row editor (tier select / toggle / delete).

function SourcesTab() {
  const { t } = useTranslation();
  const { data, loading, error, reload } = useFetch<CommunitySource[]>('sources');
  const sources = data ?? [];
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
        // generic "Try again" — a validation failure (e.g. "must be an https:// URL",
        // "already exists") is ACTIONABLE, and swallowing it makes the write look flaky.
        // Axios puts the body on error.response.data.detail; fall back only if absent.
        const detail = (e as { response?: { data?: { detail?: unknown } } })
          ?.response?.data?.detail;
        const reason = typeof detail === 'string' && detail.trim() ? detail : null;
        setActionErr(reason ?? t('ui.CommunityOverlay.updateError', { id }));
        // leave confirmDelete as-is on failure — the row keeps its state, user can retry
      } finally {
        setBusy(null);
      }
    },
    [reload, t],
  );

  const banner = <StateBanner loading={loading} error={error} onRetry={reload} empty={sources.length === 0}
    emptyMsg={t('ui.CommunityOverlay.noSources')} />;
  // Show the banner for loading/error; but on empty still render the add-form (below),
  // so a fresh user can add their first source.
  if (loading || error) return banner;

  return (
    <div className="max-w-[760px]">
      <SectionLabel className="mb-2">{t('ui.CommunityOverlay.sourcesLabel')}</SectionLabel>
      {actionErr && (
        <div className="mb-2 text-meta text-[var(--color-status-error)]">{actionErr}</div>
      )}
      <div className="flex flex-col gap-0.5">
        {sources.map((s) => (
          <div key={s.id} data-testid="community-source-row">
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
                  ? t('ui.CommunityOverlay.hideMembersFor', { name: s.name })
                  : t('ui.CommunityOverlay.showMembersFor', { name: s.name })}
                title={expanded === s.id ? t('ui.CommunityOverlay.hideMembers') : t('ui.CommunityOverlay.showMembers')}
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
              onChange={(e) => mutate(s.id, () => communityService.updateSource(s.id, { tier: e.target.value }))}
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
              onClick={() => mutate(s.id, () => communityService.updateSource(s.id, { enabled: !s.enabled }))}
              className={[
                'text-meta px-2 py-1 rounded border transition-colors',
                s.enabled
                  ? 'text-[var(--color-text)] border-[var(--color-border-strong)]'
                  : 'text-[var(--color-text-faint)] border-[var(--color-border)]',
              ].join(' ')}
            >
              {s.enabled ? t('ui.CommunityOverlay.enabledOn') : t('ui.CommunityOverlay.enabledOff')}
            </button>
            {/* delete — 2-step confirm */}
            {confirmDelete === s.id ? (
              <button
                type="button"
                data-testid="source-delete-confirm"
                disabled={busy === s.id}
                onClick={() => mutate(s.id, () => communityService.deleteSource(s.id))}
                aria-label={t('ui.CommunityOverlay.confirmDeleteSource', { name: s.name })}
                className="btn-sm btn-danger"
              >
                {t('ui.CommunityOverlay.confirm')}
              </button>
            ) : (
              <button
                type="button"
                data-testid="source-delete"
                onClick={() => setConfirmDelete(s.id)}
                aria-label={t('ui.CommunityOverlay.deleteSource', { name: s.name })}
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
        {t('ui.CommunityOverlay.editsNoteBefore')}<span className="font-mono">user</span>{t('ui.CommunityOverlay.editsNoteAfter')}
      </p>
    </div>
  );
}

// Member editor — the individual urls/keywords/queries inside one feed. Reuses the
// same busy/confirm/error discipline as the feed rows. Writes go through the shared
// config lock (managed_by:user); refetch after each so the UI reflects persisted truth.
function MemberEditor({ source, onChanged }: { source: CommunitySource; onChanged: () => void }) {
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
        setErr(t('ui.CommunityOverlay.memberSaveError'));
      } finally {
        setBusy(false);
      }
    },
    [onChanged, t],
  );

  const submitAdd = () => {
    const v = adding.trim();
    if (!v) return;
    run(async () => { await communityService.addMember(source.id, v); setAdding(''); }, false);
  };

  return (
    <div data-testid="member-editor" className="ml-7 mb-1 pl-3 border-l border-[var(--color-border)] flex flex-col gap-0.5 max-w-[720px]">
      {err && <div className="text-meta text-[var(--color-status-error)] py-1">{err}</div>}
      {source.members.length === 0 && (
        <div className="text-meta text-[var(--color-text-faint)] py-1">{t('ui.CommunityOverlay.noMembers')}</div>
      )}
      {source.members.map((m) => (
        <div key={m} data-testid="member-row" className="flex items-center gap-2 py-0.5 group">
          <span className="flex-1 text-meta font-mono text-[var(--color-text-muted)] truncate" title={m}>{m}</span>
          {confirm === m ? (
            <button
              type="button"
              data-testid="member-delete-confirm"
              disabled={busy}
              onClick={() => run(() => communityService.deleteMember(source.id, m), true)}
              aria-label={t('ui.CommunityOverlay.confirmRemoveMember', { member: m })}
              className="btn-sm btn-danger"
            >
              {t('ui.CommunityOverlay.confirm')}
            </button>
          ) : (
            <button
              type="button"
              data-testid="member-delete"
              onClick={() => setConfirm(m)}
              aria-label={t('ui.CommunityOverlay.removeMember', { member: m })}
              className="material-symbols-outlined icon-xs text-[var(--color-text-faint)] hover:text-[var(--color-status-error)] opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
            >
              close
            </button>
          )}
        </div>
      ))}
      {source.membersTruncated && (
        <div className="text-meta text-[var(--color-text-faint)] py-0.5">
          {t('ui.CommunityOverlay.membersTruncated', { shown: source.members.length, total: source.memberCount })}
        </div>
      )}
      <div className="flex items-center gap-2 pt-1">
        <input
          data-testid="member-add-input"
          value={adding}
          disabled={busy}
          onChange={(e) => setAdding(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') submitAdd(); }}
          placeholder={t('ui.CommunityOverlay.addMemberPlaceholder', {
            kind: source.memberKind?.replace(/s$/, '') ?? t('ui.CommunityOverlay.memberFallback'),
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
          {t('ui.CommunityOverlay.add')}
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
    if (!id.trim() || !name.trim()) { setErr(t('ui.CommunityOverlay.idNameRequired')); return; }
    setSaving(true);
    try {
      await communityService.addSource({ id: id.trim(), name: name.trim(), type, tier });
      setId(''); setName(''); setOpen(false);
      onAdded();
    } catch {
      setErr(t('ui.CommunityOverlay.addSourceError'));
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
        {t('ui.CommunityOverlay.addSource')}
      </button>
    );
  }

  return (
    <div className="mt-3 rounded-lg border border-[var(--color-border)] p-3 flex flex-col gap-2 max-w-[560px]" data-testid="source-add-form">
      {err && <div className="text-meta text-[var(--color-status-error)]">{err}</div>}
      <div className="flex gap-2">
        <input data-testid="add-id" value={id} onChange={(e) => setId(e.target.value)} placeholder={t('ui.CommunityOverlay.idPlaceholder')}
          className="flex-1 text-small bg-transparent border border-[var(--color-border)] rounded px-2 py-1" />
        <input data-testid="add-name" value={name} onChange={(e) => setName(e.target.value)} placeholder={t('ui.CommunityOverlay.namePlaceholder')}
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
        <button type="button" onClick={() => setOpen(false)} className="btn-sm btn-ghost">{t('ui.CommunityOverlay.cancel')}</button>
        <button type="button" data-testid="add-submit" disabled={saving || busy} onClick={submit}
          className="btn-sm btn-secondary disabled:opacity-50">
          {saving ? t('ui.CommunityOverlay.adding') : t('ui.CommunityOverlay.add')}
        </button>
      </div>
    </div>
  );
}
