/**
 * CommunityOverlay — SwarmAI's two-way membrane with the outside world.
 *
 * Design: Knowledge/Designs/2026-08-08-community-overlay-mockup.html (approved),
 * built by run_5165013e. Three tabs = the two hands of the flywheel + reports:
 *   📥 Feed              — inbound: recent signal digests + reports (click → Canvas)
 *   🔗 Sources           — inbound: configured feeds (read-only in Phase-1)
 *   📤 Engagement        — outbound: GitHub community metrics (data-backed only)
 *
 * Phase-1 is READ-ONLY (all three GET endpoints). Phase-2 (a future run) makes
 * Sources editable (add/edit/delete + managed_by:user, coexisting with self_tune).
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
import { useCallback, useEffect, useState } from 'react';
import {
  communityService,
  type CommunityFeedItem,
  type CommunitySource,
  type CommunityEngagement,
} from '../../services/community';

interface CommunityContentProps {
  /** Close the overlay (host-owned) — called before opening a file in Canvas. */
  close: () => void;
}

type TabId = 'feed' | 'sources' | 'engagement';

const TABS: Array<{ id: TabId; label: string }> = [
  { id: 'feed', label: '📥 Feed' },
  { id: 'sources', label: '🔗 Sources' },
  { id: 'engagement', label: '📤 Engagement & Reports' },
];

export function CommunityContent({ close }: CommunityContentProps) {
  const [tab, setTab] = useState<TabId>('feed');

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
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto px-5 py-4">
        {tab === 'feed' && <FeedTab close={close} />}
        {tab === 'sources' && <SourcesTab />}
        {tab === 'engagement' && <EngagementTab />}
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
    // .catch → null is the ONLY error signal. An empty array [] / all-zero object
    // is a SUCCESS (renders the empty/zero state), NOT an error — so we branch on
    // `=== null`, never on truthiness (a falsy-looking [] must not read as error).
    const res = await fetcher().catch(() => null);
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

// ── 📥 Feed tab — recent signal digests + reports (files → Canvas) ──────────

function FeedTab({ close }: { close: () => void }) {
  const { data, loading, error } = useFetch<CommunityFeedItem[]>(communityService.fetchFeed);
  const items = data ?? [];

  const openFile = useCallback(
    (path: string) => {
      close(); // close overlay before Canvas renders (BrainHub precedent)
      document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path } }));
    },
    [close],
  );

  const banner = <StateBanner loading={loading} error={error} empty={items.length === 0}
    emptyMsg="No recent signals or reports." />;
  if (loading || error || items.length === 0) return banner;

  return (
    <div className="flex flex-col gap-1 max-w-[860px]">
      {items.map((it) => (
        <button
          key={it.path}
          type="button"
          onClick={() => openFile(it.path)}
          data-testid="community-feed-item"
          className="group w-full text-left rounded-lg px-3 py-2.5 border border-transparent hover:border-[var(--color-border)] hover:bg-[var(--color-hover)] transition-colors flex items-center gap-3"
        >
          <span className="text-[9.5px] font-mono uppercase text-[var(--color-text-muted)] bg-[var(--color-hover)] rounded px-1.5 py-0.5">
            {it.category}
          </span>
          <span className="flex-1 text-[13px] text-[var(--color-text)] truncate">{it.name}</span>
          <span className="material-symbols-outlined text-[16px] text-[var(--color-text-muted)] group-hover:text-[var(--color-text)]">
            open_in_new
          </span>
        </button>
      ))}
    </div>
  );
}

// ── 🔗 Sources tab — configured feeds (read-only Phase-1) ────────────────────

function SourcesTab() {
  const { data, loading, error, reload } = useFetch<CommunitySource[]>(communityService.fetchSources);
  const sources = data ?? [];
  const [busy, setBusy] = useState<string | null>(null); // id being mutated
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null); // 2-step delete
  const [actionErr, setActionErr] = useState<string | null>(null);

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
          <div
            key={s.id}
            data-testid="community-source-row"
            className="rounded-lg px-3 py-2 hover:bg-[var(--color-hover)] transition-colors grid grid-cols-[1fr_auto_auto_auto_auto] items-center gap-3"
          >
            <div className="min-w-0">
              <div className="text-[13px] text-[var(--color-text)] truncate">{s.name}</div>
              <div className="text-[11px] text-[var(--color-text-dim)] truncate">
                {s.type}
                {s.sourceCount > 0 && ` · ${s.sourceCount} sources`}
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
                className="text-[10.5px] px-2 py-1 rounded bg-red-500/15 text-red-400 border border-red-500/40"
              >
                confirm?
              </button>
            ) : (
              <button
                type="button"
                data-testid="source-delete"
                onClick={() => setConfirmDelete(s.id)}
                className="material-symbols-outlined text-[15px] text-[var(--color-text-faint)] hover:text-red-400"
              >
                delete
              </button>
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
          {['rss', 'web-search', 'github-releases', 'hacker-news', 'trending', 'github-trending', 'github-community', 'weibo-trending', 'eastmoney-market'].map((t) => (
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

// ── 📤 Engagement tab — data-backed metrics only ─────────────────────────────

function EngagementTab() {
  const { data, loading, error } = useFetch<CommunityEngagement>(communityService.fetchEngagement);

  const banner = <StateBanner loading={loading} error={error} empty={false} emptyMsg="" />;
  if (loading || error) return banner;
  const e = data!;

  const kpis: Array<{ label: string; value: string }> = [
    { label: 'comments posted', value: String(e.commentsPosted) },
    { label: 'replies received', value: String(e.repliesReceived) },
    { label: 'maintainer replies', value: String(e.maintainerReplies) },
  ];
  if (e.stars !== null) kpis.push({ label: 'repo stars', value: String(e.stars) });

  return (
    <div className="max-w-[860px]">
      <div className="flex gap-8">
        {kpis.map((k) => (
          <div key={k.label} data-testid="community-kpi">
            <div className="text-[24px] font-semibold font-mono leading-none text-[var(--color-text)]">
              {k.value}
            </div>
            <div className="mt-1.5 text-[11.5px] text-[var(--color-text-faint)]">{k.label}</div>
          </div>
        ))}
      </div>
      <p className="mt-4 text-[11px] text-[var(--color-text-faint)] italic leading-relaxed max-w-[600px]">
        Metrics reflect our outbound GitHub community engagement. Only data-backed
        numbers are shown.
      </p>
    </div>
  );
}
