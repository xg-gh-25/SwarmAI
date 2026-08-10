/**
 * CommunityOverlay — SwarmAI's two-way membrane with the outside world.
 *
 * Design: Knowledge/Designs/2026-08-08-community-overlay-mockup.html (approved),
 * built by run_5165013e. Three tabs = the two hands of the flywheel + reports:
 *   📥 Feed              — inbound: recent signal digests + community reports (click → Canvas)
 *   🔗 Sources           — inbound: configured feeds — add/toggle/tier/delete a feed AND
 *                          add/delete a feed's internal members (urls/keywords/queries)
 *   📤 Engagement        — outbound: GitHub community metrics (data-backed only)
 *
 * The Feed's Reports section is community-scoped: internal governance reports
 * (ddd-weekly/pipeline-weekly/swarmai-monthly/validator-audit) are excluded by the
 * backend classifier (community_data._is_community_report). All Sources writes —
 * feed-level AND member-level — go through the shared config lock (managed_by:user,
 * coexisting with self_tune) so a UI edit and a scheduled tune never clobber.
 *
 * Honesty rules enforced here (Gate-1, run_5165013e):
 *   - No fabricated data. Feed shows real files; Sources shows real config.yaml
 *     feeds; Engagement shows only metrics with backing data (no invented quality
 *     score — there is none on disk).
 *   - Every tab has loading / error / empty branches (the 5-overlay fetch pattern,
 *     TECH.md § recurring-overlay-bug) so a failed OR empty fetch never renders a
 *     permanent spinner or a false-zero.
 *
 * Clone lineage: NeedYouOverlay (fetch+list shape) + BrainHub.openFile (close-then
 * -dispatch swarm:open-file). Chrome/geometry owned by OverlayHost.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  communityService,
  type CommunityFeed,
  type CommunitySource,
  type CommunityEngagement,
  type CommunityEngagementItem,
  type CommunityHotTopics,
} from '../../services/community';

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

// ── 📥 Inbound tab — Hot Topics + weekly-report card + Signals-only daily flow ──
// IA split (run_edcd9672): Reports and Signals are DIFFERENT artifacts (weekly
// synthesis vs daily raw log) — no longer interleaved in one mtime list. The latest
// Report is a hero card; Signals are the daily stream below. Reports are separated
// by `category === 'Reports'`, so a future report type surfaces automatically.

function InboundTab({ close }: { close: () => void }) {
  const { data, loading, error } = useFetch<CommunityFeed>(communityService.fetchFeed);

  const openFile = useCallback(
    (path: string) => {
      close(); // close overlay before Canvas renders (BrainHub precedent)
      document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path } }));
    },
    [close],
  );

  // Split by category: Reports → card(s), everything else (Signals) → daily list.
  // Backend already returns newest-first, so [0] is the latest of each kind.
  const { latestReport, pastReports, signals } = useMemo(() => {
    const items = data?.items ?? [];
    const reports = items.filter((it) => it.category === 'Reports');
    const sigs = items.filter((it) => it.category !== 'Reports');
    return { latestReport: reports[0] ?? null, pastReports: reports.slice(1), signals: sigs };
  }, [data]);

  // Honest cap disclosure: the backend caps the WHOLE feed (Signals + Reports) at
  // feed_cap and flags `truncated`. Surface it instead of dropping it (was silent).
  // NOTE: the cap is on the whole feed, so the disclosed number is `data.count` (the
  // total capped items fetched) — NOT signals.length, which is only the post-split
  // signal subset and would misstate the cap (adversarial-review finding).
  const truncated = data?.truncated === true;
  const fetchedCount = data?.count ?? 0;

  // Hot Topics renders ABOVE as an independent SIBLING — never gated by the feed's
  // own loading/empty state (an empty file feed must not hide hot topics).
  return (
    <div className="flex flex-col gap-4 max-w-[860px]">
      <HotTopicsSection />

      {/* Weekly report — the latest synthesis, as a hero card (not buried in the flow) */}
      {latestReport && (
        <div>
          <div className="text-[10.5px] uppercase tracking-wide text-[var(--color-text-muted)] font-semibold mb-1.5">
            Latest report
          </div>
          <button
            type="button"
            onClick={() => openFile(latestReport.path)}
            data-testid="community-report-card"
            className="group w-full text-left rounded-lg border border-[var(--color-border)] bg-[var(--color-hover)]/40 hover:border-[var(--color-border-strong)] transition-colors px-4 py-3 flex items-center gap-3"
          >
            <span className="text-[18px]">📊</span>
            <span className="flex-1 min-w-0">
              <span className="block text-[13px] font-medium text-[var(--color-text)] truncate">{latestReport.name}</span>
              <span className="block text-[11px] text-[var(--color-text-dim)]">Weekly community report</span>
            </span>
            {pastReports.length > 0 && (
              <span className="text-[10.5px] text-[var(--color-text-faint)] shrink-0">
                +{pastReports.length} past
              </span>
            )}
            <span className="text-[12px] text-[var(--panel-accent,var(--color-primary))] shrink-0">open ↗</span>
          </button>
        </div>
      )}

      {/* Daily signals — Signals only, newest-first, click → Canvas */}
      <div>
        <div className="text-[10.5px] uppercase tracking-wide text-[var(--color-text-muted)] font-semibold mb-1.5">
          Daily signals
        </div>
        {loading || error || signals.length === 0 ? (
          <StateBanner loading={loading} error={error} empty={signals.length === 0}
            emptyMsg="No recent signals." />
        ) : (
          <div className="flex flex-col gap-1">
            {signals.map((it) => (
              <button
                key={it.path}
                type="button"
                onClick={() => openFile(it.path)}
                data-testid="community-feed-item"
                className="group w-full text-left rounded-lg px-3 py-2 border border-transparent hover:border-[var(--color-border)] hover:bg-[var(--color-hover)] transition-colors flex items-center gap-3"
              >
                <span className="flex-1 text-[13px] text-[var(--color-text)] truncate">{it.name}</span>
                <span className="material-symbols-outlined text-[15px] text-[var(--color-text-muted)] group-hover:text-[var(--color-text)]">
                  open_in_new
                </span>
              </button>
            ))}
            {truncated && (
              <div
                data-testid="community-feed-truncated"
                className="px-3 py-1.5 text-[11px] text-[var(--color-text-faint)]"
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

// ── Hot Topics section (gap2) — community DEMAND from TECH.md, read-only ──────
// Freshness-honest: the source is a MANUAL scan snapshot (not live). The `updated`
// date is surfaced prominently; if it's stale (>21d) the label warns, so a 2-month-old
// snapshot never reads as current demand (Gate-1 finding #5).
const _STALE_DAYS = 21;

function _freshnessLabel(updated: string | null): { text: string; stale: boolean } {
  if (!updated) return { text: 'snapshot date unknown', stale: true };
  const then = new Date(updated + 'T00:00:00Z').getTime();
  if (Number.isNaN(then)) return { text: `last scan ${updated}`, stale: true };
  const days = Math.floor((Date.now() - then) / 86_400_000);
  if (days > _STALE_DAYS) return { text: `⚠ stale — last scanned ${updated} (${days}d ago)`, stale: true };
  return { text: `last scanned ${updated}`, stale: false };
}

function HotTopicsSection() {
  const { data, loading, error } = useFetch<CommunityHotTopics>(communityService.fetchHotTopics);
  // Fail-quiet: hot topics is a secondary panel — on load-fail or empty, render NOTHING
  // (don't push a red error banner above the primary feed). It's additive context.
  if (loading || error) return null;
  const topics = data?.topics ?? [];
  if (topics.length === 0) return null;
  const fresh = _freshnessLabel(data?.updated ?? null);

  return (
    <div data-testid="community-hot-topics" className="rounded-lg border border-[var(--color-border)] px-3 py-2.5">
      <div className="flex items-baseline justify-between mb-1.5">
        <span className="text-[12px] font-semibold text-[var(--color-text)]">🔥 Hot Topics · community demand</span>
        <span
          data-testid="hot-topics-freshness"
          className={`text-[10px] ${fresh.stale ? 'text-amber-500' : 'text-[var(--color-text-faint)]'}`}
        >
          {fresh.text}
        </span>
      </div>
      <ol className="flex flex-col gap-0.5">
        {topics.map((t) => (
          <li key={t.rank} data-testid="hot-topic-row" className="flex items-baseline gap-2 text-[12px] py-0.5" title={t.evidence}>
            <span className="font-mono text-[10.5px] text-[var(--color-text-faint)] w-4 shrink-0">{t.rank}</span>
            <span className="flex-1 text-[var(--color-text)] truncate">{t.topic}</span>
            <span className="text-[11px] text-[var(--color-text-muted)] shrink-0">{t.trend}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

// ── 🔗 Sources tab — configured feeds (add/toggle/tier/delete + member editing) ──

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
      } catch {
        setActionErr(`Couldn't update "${id}". Try again.`);
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
    <div className="max-w-[860px]">
      {actionErr && (
        <div className="mb-2 text-[11.5px] text-red-400">{actionErr}</div>
      )}
      <div className="flex flex-col gap-1">
        {sources.map((s) => (
          <div key={s.id} data-testid="community-source-row">
          <div
            className="rounded-lg px-3 py-2 hover:bg-[var(--color-hover)] transition-colors grid grid-cols-[auto_1fr_auto_auto_auto_auto] items-center gap-3"
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

// ── 📤 Outbound tab — demoted KPI strip + the actionable engagement LIST ─────
// The KPI strip is DEMOTED (small, muted) — it's context, not the answer. The
// answer is the list: what we posted, what came back, what needs a reply. Rows the
// backend marks needs_followup (a reply / maintainer reply) sort first; row click
// opens the comment on GitHub; replies expand inline. (run_edcd9672)

function OutboundTab() {
  const { data, loading, error } = useFetch<CommunityEngagement>(communityService.fetchEngagement);

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
  // capped, so a user counting rows must not think the number lies. Show it only when
  // the list is actually shorter than the posted-comments total.
  const capped = e.kpis.commentsPosted > items.length;

  return (
    <div className="max-w-[860px] flex flex-col gap-4">
      {/* Demoted KPI strip — one muted line, not a wall of big numbers */}
      <div data-testid="community-kpi-strip" className="text-[11.5px] text-[var(--color-text-muted)]">
        {kpiParts.join('  ·  ')}
        <span className="text-[var(--color-text-faint)]"> — outbound GitHub engagement (data-backed only)</span>
      </div>

      {items.length === 0 ? (
        <StateBanner loading={false} error={false} empty emptyMsg="No engagements yet." />
      ) : (
        <>
          {followups.length > 0 && (
            <EngagementGroup
              hint={`⬤ Needs follow-up — someone else replied last, awaiting your response (${followups.length})`}
              items={followups}
            />
          )}
          {posted.length > 0 && (
            <EngagementGroup hint="⬤ Posted / handled" items={posted} muted />
          )}
          {capped && (
            <div data-testid="community-list-cap" className="text-[10.5px] text-[var(--color-text-faint)] italic">
              Showing the {items.length} most recent of {e.kpis.commentsPosted} posted comments.
            </div>
          )}
        </>
      )}
    </div>
  );
}

function EngagementGroup({ hint, items, muted }: { hint: string; items: CommunityEngagementItem[]; muted?: boolean }) {
  return (
    <div>
      <div className={`text-[11px] mb-1.5 ${muted ? 'text-[var(--color-text-faint)]' : 'text-[var(--color-text-muted)]'}`}>
        {hint}
      </div>
      <div className="flex flex-col gap-1.5">
        {items.map((it) => (
          <EngagementRow key={`${it.repo}#${it.issueNumber}-${it.commentUrl}`} item={it} />
        ))}
      </div>
    </div>
  );
}

function EngagementRow({ item }: { item: CommunityEngagementItem }) {
  const [open, setOpen] = useState(false);
  const dotClass = item.hasMaintainerReply
    ? 'bg-[var(--panel-accent,var(--color-primary))]'
    : item.needsFollowup
      ? 'bg-emerald-500'
      : 'bg-[var(--color-text-faint)]';

  const hasUrl = !!item.commentUrl.trim();
  const openComment = useCallback(() => {
    // Guard the empty-URL case: window.open('') opens a blank about:blank tab.
    if (hasUrl) window.open(item.commentUrl, '_blank', 'noopener,noreferrer');
  }, [item.commentUrl, hasUrl]);

  return (
    <div data-testid="community-engagement-row" className="rounded-lg border border-[var(--color-border)] overflow-hidden">
      <div className="flex items-center gap-3 px-3 py-2.5">
        <span className={`w-2 h-2 rounded-full shrink-0 ${dotClass}`} aria-hidden />
        <button
          type="button"
          onClick={openComment}
          disabled={!hasUrl}
          data-testid="engagement-open-github"
          className="group flex-1 min-w-0 text-left disabled:cursor-default"
          title={item.commentUrl || 'no comment URL'}
        >
          <span className="block text-[13px] text-[var(--color-text)] truncate group-hover:text-[var(--panel-accent,var(--color-primary))]">
            {item.repo}{item.issueNumber != null ? ` #${item.issueNumber}` : ''}
          </span>
          <span className="block text-[11px] text-[var(--color-text-dim)] truncate">
            {item.topic && <span className="font-mono">{item.topic}</span>}
            {item.postedAt && <span> · {item.postedAt.slice(0, 10)}</span>}
            {item.confidence != null && <span> · conf {item.confidence}</span>}
          </span>
        </button>
        {item.replyCount > 0 && (
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            data-testid="engagement-toggle-replies"
            className={`text-[11px] px-2 py-0.5 rounded shrink-0 ${item.hasMaintainerReply ? 'text-[var(--panel-accent,var(--color-primary))]' : 'text-emerald-500'}`}
            aria-expanded={open}
          >
            {item.hasMaintainerReply ? '● maintainer ' : '● '}{item.replyCount} {open ? '▾' : '▸'}
          </button>
        )}
        <span className="material-symbols-outlined text-[15px] text-[var(--color-text-muted)] shrink-0">open_in_new</span>
      </div>
      {open && item.replies.length > 0 && (
        <div className="border-t border-[var(--color-border)] bg-[var(--color-hover)]/40 px-3 py-2 flex flex-col gap-2">
          {item.replies.map((r, i) => (
            <div key={`${r.author}-${r.createdAt}-${i}`} className="text-[12px]">
              <span className="text-[var(--color-text)] font-medium">{r.author}</span>
              {r.isMaintainer && (
                <span className="ml-1.5 text-[9px] uppercase text-[var(--panel-accent,var(--color-primary))] border border-[var(--panel-accent,var(--color-primary))] rounded px-1 py-0.5">
                  maintainer
                </span>
              )}
              {r.createdAt && <span className="ml-1.5 text-[10.5px] text-[var(--color-text-faint)]">{r.createdAt.slice(0, 10)}</span>}
              <p className="mt-0.5 text-[var(--color-text-dim)] leading-snug">{r.body}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
