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

  return { data, loading, error };
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
  const { data, loading, error } = useFetch<CommunitySource[]>(communityService.fetchSources);
  const sources = data ?? [];

  const banner = <StateBanner loading={loading} error={error} empty={sources.length === 0}
    emptyMsg="No subscribed sources." />;
  if (loading || error || sources.length === 0) return banner;

  return (
    <div className="max-w-[860px]">
      <div className="flex flex-col gap-1">
        {sources.map((s) => (
          <div
            key={s.id}
            data-testid="community-source-row"
            className="rounded-lg px-3 py-2.5 hover:bg-[var(--color-hover)] transition-colors grid grid-cols-[1fr_auto_auto_auto] items-center gap-3"
          >
            <div className="min-w-0">
              <div className="text-[13px] text-[var(--color-text)] truncate">{s.name}</div>
              <div className="text-[11px] text-[var(--color-text-dim)] truncate">
                {s.type}
                {s.sourceCount > 0 && ` · ${s.sourceCount} sources`}
              </div>
            </div>
            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded border border-[var(--color-border-strong)] text-[var(--color-text-muted)]">
              {s.tier}
            </span>
            <span className="text-[10px] font-mono text-[var(--color-text-faint)]">{s.managedBy}</span>
            <span
              className={[
                'text-[10.5px]',
                s.enabled ? 'text-[var(--color-text-muted)]' : 'text-[var(--color-text-faint)]',
              ].join(' ')}
            >
              {s.enabled ? '● on' : '○ off'}
            </span>
          </div>
        ))}
      </div>
      <p className="mt-3 text-[11px] text-[var(--color-text-faint)] leading-relaxed max-w-[640px]">
        Read-only for now. Editing subscriptions (add / remove / toggle) is coming next —
        your manual changes will be preserved against auto-tuning.
      </p>
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
