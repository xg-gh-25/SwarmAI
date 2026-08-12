/**
 * CommunityOverlay — SwarmAI's two-way membrane with the outside world.
 *
 * Design: Knowledge/Designs/2026-08-12-community-overlay-redesign-mockup.html (approved),
 * rebuilt by run_ced271e8. Three tabs = the two hands of the flywheel + reports:
 *   📥 Inbound   — recent signal digests + community reports (local files → Canvas) + Hot Topics
 *   🔗 Watching  — configured feeds — add/toggle/tier/delete a feed AND members
 *   📤 Outbound  — GitHub community engagement (needs-followup hero + collapsed handled)
 *
 * UI craft (KNOWLEDGE.md 5-check + s_frontend-design/data/design-judgment.md):
 *   - NO per-row border box-wall — whitespace + hover-highlight groups (Tufte data-ink,
 *     Refactoring UI "fewer borders").
 *   - ONE focus per tab (Von Restorff): Inbound = latest-report hero + demand ranking;
 *     Outbound = needs-followup accent rows, "Posted/handled" DEMOTED to a collapsed count.
 *
 * Link routing (run_ced271e8):
 *   - LOCAL synthesis files (our Signals/Reports .md) → Canvas via swarm:open-file.
 *   - EXTERNAL github links (Hot Topics top thread, Outbound comment) → the SYSTEM
 *     browser via openExternal(). A raw window.open is SILENTLY IGNORED by the Tauri v2
 *     WKWebview (see openExternal.ts / ToDoOverlay.tsx) — never use it for external URLs.
 *
 * Honesty rules (Gate-1, run_5165013e): no fabricated data; every tab has loading/error/
 * empty branches (the 5-overlay fetch pattern). Clone lineage: NeedYouOverlay + BrainHub.
 * Chrome/geometry owned by OverlayHost.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  communityService,
  type CommunityFeed,
  type CommunitySource,
  type CommunityEngagement,
  type CommunityEngagementItem,
  type CommunityHotTopics,
  type CommunityHotTopic,
} from '../../services/community';
import { openExternal } from '../../utils/openExternal';

interface CommunityContentProps {
  /** Close the overlay (host-owned) — called before opening a file in Canvas. */
  close: () => void;
}

type TabId = 'inbound' | 'watching' | 'outbound';

// Two-hands-of-the-membrane IA (run_edcd9672): Inbound = what we read from the world,
// Watching = what we watch, Outbound = what we say back. Subtitles make each self-evident.
const TABS: Array<{ id: TabId; label: string; sub: string }> = [
  { id: 'inbound', label: '📥 Inbound', sub: 'read the world' },
  { id: 'watching', label: '🔗 Watching', sub: 'what we watch' },
  { id: 'outbound', label: '📤 Outbound', sub: 'speak back' },
];

export function CommunityContent({ close }: CommunityContentProps) {
  const [tab, setTab] = useState<TabId>('inbound');

  return (
    <div className="flex-1 flex flex-col overflow-hidden" data-testid="community-overlay">
      <div className="px-5 pt-4 pb-0">
        <h2 className="text-[15px] font-semibold text-[var(--color-text)]">Community</h2>
        <p className="mt-0.5 text-[11.5px] text-[var(--color-text-dim)]">
          SwarmAI's two-way membrane — what you learn from the world, what you give back.
        </p>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 px-5 pt-3 border-b border-[var(--color-border)]">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            data-testid={`community-tab-${t.id}`}
            className={[
              'text-[13px] px-3 py-2 border-b-2 transition-colors font-medium',
              tab === t.id
                ? 'text-[var(--color-text)] border-[var(--panel-accent,var(--color-primary))]'
                : 'text-[var(--color-text-muted)] border-transparent hover:text-[var(--color-text)] hover:bg-[var(--color-hover)]',
            ].join(' ')}
          >
            {t.label}
            <span className="ml-1.5 text-[10px] font-normal text-[var(--color-text-faint)]">{t.sub}</span>
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto px-5 py-4">
        {tab === 'inbound' && <InboundTab close={close} />}
        {tab === 'watching' && <SourcesTab />}
        {tab === 'outbound' && <OutboundTab />}
      </div>
    </div>
  );
}

// ── shared fetch-state hook (loading / error / empty — the 5-overlay pattern) ──

function useFetch<T>(fetcher: () => Promise<T>): {
  data: T | null;
  loading: boolean;
  error: boolean;
  reload: () => void;
} {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    // null is the ONLY error signal. An empty array [] / all-zero object is a SUCCESS
    // (renders the empty/zero state), NOT an error — so we branch on `=== null`, never
    // on truthiness (a falsy-looking [] must not read as error).
    //
    // try/catch, NOT `fetcher().catch(...)`: `.catch` only handles a REJECTED promise.
    // A SYNCHRONOUS throw from the call itself escapes it entirely — and because load()
    // is invoked as a floating promise in useEffect, that escape became an UNHANDLED
    // REJECTION instead of this component's error state. Real occurrence: a new service
    // method (fetchHotTopics) was added to this overlay but not to the test's
    // whole-service mock, so `fetcher` was `undefined` → `fetcher()` threw a TypeError
    // → 22 unhandled errors and a NON-ZERO vitest exit while every assertion still
    // "passed". try/catch makes ANY failure mode — rejection, sync throw, a
    // missing/renamed method — land in the visible error branch (run_a1f4c2d8).
    let res: T | null = null;
    try {
      res = await fetcher();
    } catch {
      res = null;
    }
    if (res === null) {
      setError(true);
    } else {
      setData(res);
      setError(false);
    }
    setLoading(false);
    // fetcher identity is stable (module singleton method) — safe dep.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return { data, loading, error, reload: load };
}

function StateBanner({ loading, error, empty, emptyMsg }: {
  loading: boolean; error: boolean; empty: boolean; emptyMsg: string;
}) {
  if (loading) {
    return <div className="py-8 text-center text-[12px] text-[var(--color-text-muted)]">Loading…</div>;
  }
  if (error) {
    return (
      <div className="py-8 text-center text-[12px] text-red-400">
        Couldn't load. Retry shortly.
      </div>
    );
  }
  if (empty) {
    return <div className="py-10 text-center text-[13px] text-[var(--color-text-muted)]">{emptyMsg}</div>;
  }
  return null;
}

// A small section label — uppercase, muted (mockup .lbl). ONE representation; used to
// chunk a tab into whitespace-separated groups instead of boxing every row.
function SectionLabel({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`text-[10px] uppercase tracking-wide text-[var(--color-text-faint)] font-semibold ${className}`}>
      {children}
    </div>
  );
}

// ── 📥 Inbound tab — latest-report hero + Hot Topics demand ranking + daily signals ──
// IA (run_edcd9672 + redesign run_ced271e8): the latest weekly Report is the ONE hero;
// Hot Topics is the live demand ranking (heat bar); daily Signals are a plain list below.
// De-boxed: no per-row borders — whitespace + hover-highlight (Tufte / Refactoring UI).

function InboundTab({ close }: { close: () => void }) {
  const { data, loading, error } = useFetch<CommunityFeed>(communityService.fetchFeed);

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
          <SectionLabel className="mb-2">Latest — read this first</SectionLabel>
          <button
            type="button"
            onClick={() => openFile(latestReport.path)}
            data-testid="community-report-card"
            className="group relative w-full text-left rounded-[10px] bg-[var(--color-hover)]/50 hover:bg-[var(--color-hover)] transition-colors pl-[18px] pr-4 py-3.5 flex items-center gap-3
                       before:content-[''] before:absolute before:left-0 before:top-2.5 before:bottom-2.5 before:w-[3px] before:rounded before:bg-[var(--panel-accent,var(--color-primary))]"
          >
            <span className="text-[18px]">📊</span>
            <span className="flex-1 min-w-0">
              <span className="block text-[14px] font-semibold text-[var(--color-text)] truncate">{latestReport.name}</span>
              <span className="block text-[11.5px] text-[var(--color-text-dim)]">Weekly community report</span>
            </span>
            {pastReports.length > 0 && (
              <span className="text-[10.5px] text-[var(--color-text-faint)] shrink-0">+{pastReports.length} past</span>
            )}
            <span className="text-[12px] text-[var(--panel-accent,var(--color-primary))] shrink-0">open ↗</span>
          </button>
        </div>
      )}

      {/* Hot Topics — live community demand ranking (heat bar + thread count) */}
      <HotTopicsSection />

      {/* Daily signals — Signals only, newest-first, click → Canvas (LOCAL files) */}
      <div>
        <SectionLabel className="mb-2">Today's signals</SectionLabel>
        {loading || error || signals.length === 0 ? (
          <StateBanner loading={loading} error={error} empty={signals.length === 0}
            emptyMsg="No recent signals." />
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
                <span className="flex-1 text-[13px] text-[var(--color-text)] truncate">{it.name}</span>
                <span className="material-symbols-outlined text-[15px] text-[var(--color-text-faint)] opacity-0 group-hover:opacity-100">
                  open_in_new
                </span>
              </button>
            ))}
            {truncated && (
              <div
                data-testid="community-feed-truncated"
                className="px-2 py-1.5 text-[11px] text-[var(--color-text-faint)]"
              >
                Showing the newest {fetchedCount} {fetchedCount === 1 ? 'item' : 'items'} — more on disk.
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Hot Topics section — live community DEMAND from signals.json, read-only ──────
// Freshness-honest: the label reflects the feed's real scan time (scanned_at). Because
// the feed auto-refreshes weekly, a >21d gap now signals the SCAN may be DOWN (not human
// neglect). Each row's external GitHub thread opens in the system browser (openExternal).
const _STALE_DAYS = 21;

/** Derive a freshness label from an ISO scan timestamp. Defensive parse (H3): the
 *  timestamp is a full ISO string with microseconds and no 'Z' (e.g.
 *  "2026-08-11T17:50:56.037632"). The backend writes it from Python's naive
 *  datetime.isoformat() (UTC, no offset) — and `new Date("...T...")` WITHOUT a
 *  timezone is parsed as LOCAL time by the JS engine, which would skew the age by
 *  the machine's UTC offset (e.g. read 8h newer in UTC-8, under-reporting
 *  staleness). So append 'Z' to force UTC (matching BottomBar.tsx's fix) unless an
 *  offset is already present. A NaN result still degrades to "unknown", never
 *  crashes. For an auto-refreshed feed, >21d stale means the weekly scan may be
 *  down (H4), not that a human forgot. */
function _freshnessLabel(scannedAt: string | null): { text: string; stale: boolean } {
  if (!scannedAt) return { text: 'scan time unknown', stale: true };
  const normalized = scannedAt.includes('Z') || scannedAt.includes('+') ? scannedAt : scannedAt + 'Z';
  const then = new Date(normalized).getTime();
  if (Number.isNaN(then)) return { text: 'scan time unknown', stale: true };
  const days = Math.floor((Date.now() - then) / 86_400_000);
  if (days > _STALE_DAYS) {
    return { text: `⚠ scan may be down — last ${days}d ago`, stale: true };
  }
  if (days <= 0) return { text: 'synced today · auto weekly', stale: false };
  return { text: `synced ${days}d ago · auto weekly`, stale: false };
}

function HotTopicsSection() {
  const { data, loading, error } = useFetch<CommunityHotTopics>(communityService.fetchHotTopics);
  // Fail-quiet: hot topics is secondary — on load-fail or empty, render NOTHING
  // (don't push a red error banner above the primary content). It's additive context.
  if (loading || error) return null;
  const topics = data?.topics ?? [];
  if (topics.length === 0) return null;
  const fresh = _freshnessLabel(data?.scannedAt ?? null);
  // Heat bar is scaled to the top topic's comment count (small-multiples on a shared
  // scale — Tufte). Guard div-by-zero when the leader has 0 comments.
  const maxComments = Math.max(...topics.map((t) => t.comments), 1);

  return (
    <div data-testid="community-hot-topics">
      <div className="flex items-baseline justify-between mb-2">
        <SectionLabel>This week the community is discussing</SectionLabel>
        <span
          data-testid="hot-topics-freshness"
          className={`text-[10.5px] flex items-center gap-1.5 ${fresh.stale ? 'text-amber-500' : 'text-emerald-500'}`}
        >
          <span className={`w-[5px] h-[5px] rounded-full ${fresh.stale ? 'bg-amber-500' : 'bg-emerald-500'}`} aria-hidden />
          {fresh.text}
        </span>
      </div>
      <div className="flex flex-col gap-0.5">
        {topics.map((t) => (
          <HotTopicRow key={t.id} topic={t} widthPct={Math.round((t.comments / maxComments) * 100)} />
        ))}
      </div>
    </div>
  );
}

function HotTopicRow({ topic, widthPct }: { topic: CommunityHotTopic; widthPct: number }) {
  const hasUrl = !!topic.url.trim();
  const open = useCallback(() => {
    // External GitHub discussion → SYSTEM browser (openExternal), NOT window.open
    // (dead in the Tauri v2 WKWebview). No-op when the URL couldn't be built.
    // openExternal already falls back internally + never throws meaningfully, but
    // attach a .catch so a rejected promise is logged, not an unhandled rejection.
    if (hasUrl) openExternal(topic.url).catch((e) => console.warn('openExternal failed:', topic.url, e));
  }, [topic.url, hasUrl]);

  return (
    <button
      type="button"
      onClick={open}
      disabled={!hasUrl}
      data-testid="hot-topic-row"
      title={topic.topTitle || topic.topic}
      className="group flex items-center gap-3 px-2 py-1.5 rounded-lg text-left hover:bg-[var(--color-hover)] transition-colors disabled:cursor-default disabled:opacity-40 disabled:hover:bg-transparent"
    >
      <span className="font-mono text-[11px] text-[var(--color-text-faint)] w-3.5 text-right shrink-0">{topic.rank}</span>
      <span className="flex-1 min-w-0 text-[13px] text-[var(--color-text)] truncate group-hover:text-[var(--panel-accent,var(--color-primary))]">
        {topic.topic}
        {topic.threads > 0 && (
          <span className="ml-2 text-[11px] text-[var(--color-text-faint)]">{topic.threads} threads</span>
        )}
      </span>
      <span className="w-16 h-1 rounded-full bg-[var(--color-border)] shrink-0 overflow-hidden" aria-hidden>
        <span className="block h-full rounded-full bg-[var(--panel-accent,var(--color-primary))]" style={{ width: `${widthPct}%` }} />
      </span>
      <span className="text-[10.5px] text-[var(--color-text-dim)] w-8 text-right shrink-0">{topic.comments}</span>
    </button>
  );
}

// ── 🔗 Watching tab — configured feeds (add/toggle/tier/delete + member editing) ──
// De-boxed (run_ced271e8): rows separated by hover-highlight, not per-row borders. It's
// a config surface, so it keeps its inline row editor (tier select / toggle / delete).

function SourcesTab() {
  const { data, loading, error, reload } = useFetch<CommunitySource[]>(communityService.fetchSources);
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
        setActionErr(reason ?? `Couldn't update "${id}". Try again.`);
        // leave confirmDelete as-is on failure — the row keeps its state, user can retry
      } finally {
        setBusy(null);
      }
    },
    [reload],
  );

  const banner = <StateBanner loading={loading} error={error} empty={sources.length === 0}
    emptyMsg="No subscribed sources yet — add one below." />;
  // Show the banner for loading/error; but on empty still render the add-form (below),
  // so a fresh user can add their first source.
  if (loading || error) return banner;

  return (
    <div className="max-w-[760px]">
      <SectionLabel className="mb-2">Subscribed sources · your edits are never auto-disabled</SectionLabel>
      {actionErr && (
        <div className="mb-2 text-[11.5px] text-red-400">{actionErr}</div>
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
                className="material-symbols-outlined text-[16px] text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                aria-expanded={expanded === s.id}
                aria-label={`${expanded === s.id ? 'Hide' : 'Show'} members for ${s.name}`}
                title={expanded === s.id ? 'Hide members' : 'Show members'}
              >
                {expanded === s.id ? 'expand_more' : 'chevron_right'}
              </button>
            ) : (
              <span className="w-4" />
            )}
            <div className="min-w-0">
              <div className="text-[13px] text-[var(--color-text)] truncate">{s.name}</div>
              <div className="text-[11px] text-[var(--color-text-dim)] truncate">
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
              className="text-[11px] bg-transparent border border-[var(--color-border)] rounded px-1.5 py-1 text-[var(--color-text-muted)]"
            >
              {['frontier', 'leaders', 'research', 'engineering', 'opinion', 'aggregate'].map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
            {/* enabled toggle */}
            <button
              type="button"
              data-testid="source-toggle"
              disabled={busy === s.id}
              onClick={() => mutate(s.id, () => communityService.updateSource(s.id, { enabled: !s.enabled }))}
              className={[
                'text-[10.5px] px-2 py-1 rounded border transition-colors',
                s.enabled
                  ? 'text-[var(--color-text)] border-[var(--color-border-strong)]'
                  : 'text-[var(--color-text-faint)] border-[var(--color-border)]',
              ].join(' ')}
            >
              {s.enabled ? '● on' : '○ off'}
            </button>
            {/* delete — 2-step confirm */}
            {confirmDelete === s.id ? (
              <button
                type="button"
                data-testid="source-delete-confirm"
                disabled={busy === s.id}
                onClick={() => mutate(s.id, () => communityService.deleteSource(s.id))}
                aria-label={`Confirm delete source ${s.name}`}
                className="text-[10.5px] px-2 py-1 rounded bg-red-500/15 text-red-400 border border-red-500/40"
              >
                confirm?
              </button>
            ) : (
              <button
                type="button"
                data-testid="source-delete"
                onClick={() => setConfirmDelete(s.id)}
                aria-label={`Delete source ${s.name}`}
                className="material-symbols-outlined text-[15px] text-[var(--color-text-faint)] hover:text-red-400 focus-visible:text-red-400"
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

      <p className="mt-3 text-[11px] text-[var(--color-text-faint)] leading-relaxed max-w-[640px]">
        Your edits are marked <span className="font-mono">user</span>-managed and are never
        auto-disabled by self-tuning. Changes write safely even while a background tune runs.
      </p>
    </div>
  );
}

// Member editor — the individual urls/keywords/queries inside one feed. Reuses the
// same busy/confirm/error discipline as the feed rows. Writes go through the shared
// config lock (managed_by:user); refetch after each so the UI reflects persisted truth.
function MemberEditor({ source, onChanged }: { source: CommunitySource; onChanged: () => void }) {
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
        setErr('Could not save — try again (duplicate or removed?).');
      } finally {
        setBusy(false);
      }
    },
    [onChanged],
  );

  const submitAdd = () => {
    const v = adding.trim();
    if (!v) return;
    run(async () => { await communityService.addMember(source.id, v); setAdding(''); }, false);
  };

  return (
    <div data-testid="member-editor" className="ml-7 mb-1 pl-3 border-l border-[var(--color-border)] flex flex-col gap-0.5 max-w-[720px]">
      {err && <div className="text-[11px] text-red-400 py-1">{err}</div>}
      {source.members.length === 0 && (
        <div className="text-[11px] text-[var(--color-text-faint)] py-1">No members yet — add one below.</div>
      )}
      {source.members.map((m) => (
        <div key={m} data-testid="member-row" className="flex items-center gap-2 py-0.5 group">
          <span className="flex-1 text-[11.5px] font-mono text-[var(--color-text-muted)] truncate" title={m}>{m}</span>
          {confirm === m ? (
            <button
              type="button"
              data-testid="member-delete-confirm"
              disabled={busy}
              onClick={() => run(() => communityService.deleteMember(source.id, m), true)}
              aria-label={`Confirm remove member ${m}`}
              className="text-[10px] px-1.5 py-0.5 rounded bg-red-500/15 text-red-400 border border-red-500/40"
            >
              confirm?
            </button>
          ) : (
            <button
              type="button"
              data-testid="member-delete"
              onClick={() => setConfirm(m)}
              aria-label={`Remove member ${m}`}
              className="material-symbols-outlined text-[13px] text-[var(--color-text-faint)] hover:text-red-400 opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
            >
              close
            </button>
          )}
        </div>
      ))}
      {source.membersTruncated && (
        <div className="text-[10.5px] text-[var(--color-text-faint)] py-0.5">
          Showing first {source.members.length} of {source.memberCount}.
        </div>
      )}
      <div className="flex items-center gap-2 pt-1">
        <input
          data-testid="member-add-input"
          value={adding}
          disabled={busy}
          onChange={(e) => setAdding(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') submitAdd(); }}
          placeholder={`add ${source.memberKind?.replace(/s$/, '') ?? 'member'}…`}
          className="flex-1 text-[11.5px] bg-transparent border border-[var(--color-border)] rounded px-2 py-1"
        />
        <button
          type="button"
          data-testid="member-add-submit"
          disabled={busy || !adding.trim()}
          onClick={submitAdd}
          className="text-[11px] px-2 py-1 rounded text-[var(--panel-accent,var(--color-primary))] hover:underline disabled:opacity-40"
        >
          add
        </button>
      </div>
    </div>
  );
}

// Add-source form (collapsed to a "+ add" affordance until opened).
function AddSourceForm({ onAdded, busy }: { onAdded: () => void; busy: boolean }) {
  const [open, setOpen] = useState(false);
  const [id, setId] = useState('');
  const [name, setName] = useState('');
  const [type, setType] = useState('rss');
  const [tier, setTier] = useState('engineering');
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const submit = useCallback(async () => {
    setErr(null);
    if (!id.trim() || !name.trim()) { setErr('id and name are required'); return; }
    setSaving(true);
    try {
      await communityService.addSource({ id: id.trim(), name: name.trim(), type, tier });
      setId(''); setName(''); setOpen(false);
      onAdded();
    } catch {
      setErr('Could not add (duplicate id or invalid type/tier?)');
    } finally {
      setSaving(false);
    }
  }, [id, name, type, tier, onAdded]);

  if (!open) {
    return (
      <button
        type="button"
        data-testid="source-add-open"
        onClick={() => setOpen(true)}
        className="mt-3 text-[12.5px] text-[var(--panel-accent,var(--color-primary))] hover:underline"
      >
        ＋ Add source
      </button>
    );
  }

  return (
    <div className="mt-3 rounded-lg border border-[var(--color-border)] p-3 flex flex-col gap-2 max-w-[560px]" data-testid="source-add-form">
      {err && <div className="text-[11.5px] text-red-400">{err}</div>}
      <div className="flex gap-2">
        <input data-testid="add-id" value={id} onChange={(e) => setId(e.target.value)} placeholder="id (unique)"
          className="flex-1 text-[12px] bg-transparent border border-[var(--color-border)] rounded px-2 py-1" />
        <input data-testid="add-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="name"
          className="flex-1 text-[12px] bg-transparent border border-[var(--color-border)] rounded px-2 py-1" />
      </div>
      <div className="flex gap-2 items-center">
        <select data-testid="add-type" value={type} onChange={(e) => setType(e.target.value)}
          className="text-[11px] bg-transparent border border-[var(--color-border)] rounded px-1.5 py-1">
          {/* Must stay in sync with backend FeedType (jobs/models.py). `github-people`
              was MISSING here — the backend accepts it and it has editable `logins`
              members (MEMBER_KEY), but users had no UI to create one (frontend
              zero-wiring). Added next to its github-community sibling. */}
          {['rss', 'web-search', 'github-releases', 'hacker-news', 'trending', 'github-trending', 'github-community', 'github-people', 'weibo-trending', 'eastmoney-market'].map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
        <select data-testid="add-tier" value={tier} onChange={(e) => setTier(e.target.value)}
          className="text-[11px] bg-transparent border border-[var(--color-border)] rounded px-1.5 py-1">
          {['frontier', 'leaders', 'research', 'engineering', 'opinion', 'aggregate'].map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
        <span className="flex-1" />
        <button type="button" onClick={() => setOpen(false)} className="text-[11.5px] text-[var(--color-text-muted)] px-2 py-1">cancel</button>
        <button type="button" data-testid="add-submit" disabled={saving || busy} onClick={submit}
          className="text-[11.5px] px-3 py-1 rounded bg-[var(--panel-accent,var(--color-primary))] text-white disabled:opacity-50">
          {saving ? 'adding…' : 'add'}
        </button>
      </div>
    </div>
  );
}

// ── 📤 Outbound tab — demoted KPI strip + needs-followup HERO + collapsed handled ──
// The answer is "who is waiting on me": needs-followup rows are the accent hero. The
// KPI strip is one muted line (context, not the answer). "Posted / handled" is DEMOTED
// to a collapsed expandable count — not a wall of stacked boxes (run_ced271e8 redesign).

function OutboundTab() {
  const { data, loading, error } = useFetch<CommunityEngagement>(communityService.fetchEngagement);
  const [showHandled, setShowHandled] = useState(false); // Posted/handled default COLLAPSED

  if (loading || error) return <StateBanner loading={loading} error={error} empty={false} emptyMsg="" />;
  const e = data!;
  const items = e.items ?? [];

  const kpiParts: string[] = [
    `${e.kpis.commentsPosted} posted`,
    `${e.kpis.repliesReceived} replies`,
    `${e.kpis.maintainerReplies} maintainer`,
  ];
  if (e.kpis.stars !== null) kpiParts.push(`${e.kpis.stars} stars`);

  const followups = items.filter((it) => it.needsFollowup);
  const posted = items.filter((it) => !it.needsFollowup);
  // Honest cap disclosure: the KPI count is the TRUE total (e.g. 216) but the list is
  // capped. Show it only when the list is actually shorter than the posted total.
  const capped = e.kpis.commentsPosted > items.length;

  return (
    <div className="max-w-[760px] flex flex-col gap-5">
      {/* Demoted KPI strip — one muted line, not a wall of big numbers */}
      <div data-testid="community-kpi-strip" className="text-[11.5px] text-[var(--color-text-muted)]">
        {kpiParts.join('  ·  ')}
        <span className="text-[var(--color-text-faint)]"> — outbound GitHub engagement (data-backed only)</span>
      </div>

      {items.length === 0 ? (
        <StateBanner loading={false} error={false} empty emptyMsg="No engagements yet." />
      ) : (
        <>
          {/* HERO: needs-your-reply — the one thing that stands out */}
          {followups.length > 0 && (
            <div>
              <SectionLabel className="mb-2 !text-amber-500/90">
                Needs your reply — someone answered last, waiting on you ({followups.length})
              </SectionLabel>
              <div className="flex flex-col gap-2">
                {followups.map((it) => (
                  <FollowupRow key={`${it.repo}#${it.issueNumber}-${it.commentUrl}`} item={it} />
                ))}
              </div>
            </div>
          )}

          {/* DEMOTED: posted / handled — collapsed to a count, expand on demand */}
          {posted.length > 0 && (
            <div>
              <button
                type="button"
                data-testid="handled-toggle"
                onClick={() => setShowHandled((v) => !v)}
                aria-expanded={showHandled}
                className="flex items-center gap-2 text-[12px] text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)] px-1 py-1.5 rounded"
              >
                <span className="w-[7px] h-[7px] rounded-full bg-[var(--color-text-faint)]" aria-hidden />
                Posted · handled ({posted.length}{capped ? ` shown of ${e.kpis.commentsPosted}` : ''})
                <span className="material-symbols-outlined text-[16px]">{showHandled ? 'expand_more' : 'chevron_right'}</span>
              </button>
              {showHandled && (
                <div data-testid="handled-list" className="mt-1 flex flex-col gap-0.5">
                  {posted.map((it) => (
                    <PostedRow key={`${it.repo}#${it.issueNumber}-${it.commentUrl}`} item={it} />
                  ))}
                  {capped && (
                    <div data-testid="community-list-cap" className="mt-1 px-2 text-[10.5px] text-[var(--color-text-faint)] italic">
                      Showing the {items.length} most recent of {e.kpis.commentsPosted} posted comments.
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

/** Open an engagement's GitHub comment in the SYSTEM browser (openExternal — a raw
 *  window.open is silently ignored by the Tauri v2 WKWebview). Shared by both row
 *  variants. Returns {hasUrl, open}. */
function useOpenComment(commentUrl: string) {
  const hasUrl = !!commentUrl.trim();
  const open = useCallback(() => {
    // .catch: openExternal falls back internally, but log a rejection rather than
    // leak an unhandled promise rejection.
    if (hasUrl) openExternal(commentUrl).catch((e) => console.warn('openExternal failed:', commentUrl, e));
  }, [commentUrl, hasUrl]);
  return { hasUrl, open };
}

// A needs-followup row — the accent hero: shows the latest reply inline so the user
// knows WHAT they're being asked, without expanding.
function FollowupRow({ item }: { item: CommunityEngagementItem }) {
  const { hasUrl, open } = useOpenComment(item.commentUrl);
  const latest = item.replies.length > 0 ? item.replies[item.replies.length - 1] : null;

  return (
    <button
      type="button"
      onClick={open}
      disabled={!hasUrl}
      data-testid="engagement-followup-row"
      title={item.commentUrl || 'no comment URL'}
      className="group relative w-full text-left rounded-[10px] bg-amber-500/[0.06] hover:bg-amber-500/[0.11] transition-colors pl-4 pr-3.5 py-3 disabled:cursor-default disabled:opacity-50
                 before:content-[''] before:absolute before:left-0 before:top-2.5 before:bottom-2.5 before:w-[3px] before:rounded before:bg-amber-500"
    >
      <div className="flex items-center gap-2.5">
        <span className="flex-1 min-w-0 text-[13px] font-semibold text-[var(--color-text)] truncate">
          {item.repo}{item.issueNumber != null ? ` #${item.issueNumber}` : ''}
        </span>
        {item.hasMaintainerReply && (
          <span className="text-[9px] uppercase tracking-wide text-amber-500 border border-amber-500/40 rounded px-1.5 py-0.5 shrink-0">
            maintainer
          </span>
        )}
        <span className="material-symbols-outlined text-[15px] text-[var(--color-text-faint)] shrink-0">open_in_new</span>
      </div>
      {latest && (
        <div className="mt-1.5 flex items-baseline gap-2 text-[12px]">
          <span className="text-[var(--color-text)] font-medium shrink-0">{latest.author}</span>
          <span className="text-[var(--color-text-dim)] truncate">{latest.body}</span>
        </div>
      )}
      <div className="mt-1 text-[10.5px] text-[var(--color-text-faint)]">
        {item.topic && <span className="font-mono">{item.topic}</span>}
        {item.postedAt && <span> · posted {item.postedAt.slice(0, 10)}</span>}
        {item.confidence != null && <span> · conf {item.confidence}</span>}
      </div>
    </button>
  );
}

// A posted/handled row — demoted, compact, one line. Reply count shown as a subtle badge.
function PostedRow({ item }: { item: CommunityEngagementItem }) {
  const { hasUrl, open } = useOpenComment(item.commentUrl);
  return (
    <button
      type="button"
      onClick={open}
      disabled={!hasUrl}
      data-testid="engagement-posted-row"
      title={item.commentUrl || 'no comment URL'}
      className="group flex items-center gap-3 px-2 py-1.5 rounded-lg text-left hover:bg-[var(--color-hover)] transition-colors disabled:cursor-default disabled:opacity-40 disabled:hover:bg-transparent"
    >
      <span className="w-[6px] h-[6px] rounded-full bg-[var(--color-text-faint)] shrink-0" aria-hidden />
      <span className="flex-1 min-w-0 text-[12.5px] text-[var(--color-text-dim)] truncate group-hover:text-[var(--color-text)]">
        {item.repo}{item.issueNumber != null ? ` #${item.issueNumber}` : ''}
      </span>
      {item.replyCount > 0 && (
        <span className={`text-[10.5px] shrink-0 ${item.hasMaintainerReply ? 'text-[var(--panel-accent,var(--color-primary))]' : 'text-emerald-500'}`}>
          ● {item.replyCount}
        </span>
      )}
      <span className="material-symbols-outlined text-[14px] text-[var(--color-text-faint)] opacity-0 group-hover:opacity-100 shrink-0">open_in_new</span>
    </button>
  );
}
