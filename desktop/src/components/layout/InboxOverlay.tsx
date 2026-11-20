/**
 * InboxOverlay — the person's messages from every channel in one place, triaged into
 * lanes, with a draft to send (design docs/design/2026-11-20-inbox.md §7-§8; approved
 * mockup docs/design/2026-11-20-inbox-mockup.html).
 *
 * One lane at a time, through the shared Tabs: 等你回复 (N) · 需要知道 (N) · 趋势 · 已发
 * (an alert count when a send needs the person). The toolbar holds the last update
 * time, refresh and the channel settings (gear); the host header holds the title and
 * close. The lanes live in components/inbox/.
 *
 * State owned here (fresh per open — OverlayHost mounts the surface on each open):
 * - the view (a tab, or the channel settings page) and the selected item;
 * - the ids handled in this sitting, hidden until the refetch drops them;
 * - the draft edits per item, so moving between items keeps what was typed;
 * - `narrow`: the content is measured; below 720 px the reply lane shows one pane at
 *   a time (list, or detail with a back button).
 *
 * Every write refreshes all Inbox queries (invalidateOverlay 'inbox'), the nav badge
 * included, which reads the same counts query. Agents do not open this surface: it is
 * not in ALL_SHOW_EVENTS.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Tabs, type TabItem } from '../overlay';
import { overlayQuery, invalidateOverlay } from './overlayQueries';
import type { DispatchPrompt } from './overlayRegistry';
import { inboxService, type ItemOut, type TrendPoint } from '../../services/inbox';
import { ReplyLane, type DraftEdit } from '../inbox/ReplyLane';
import { AwareLane } from '../inbox/AwareLane';
import { TrendLane } from '../inbox/TrendLane';
import { SentLane } from '../inbox/SentLane';
import { ChannelSettings } from '../inbox/ChannelSettings';
import { StatusLine, clockTime } from '../inbox/parts';
import { inboxChatPrompt, inboxLearnPrompt } from '../inbox/chatPrompt';

type TabKey = 'reply' | 'aware' | 'trend' | 'sent';
type View = TabKey | 'channels';

/** Below this content width the reply lane shows one pane at a time. */
export const INBOX_NARROW_PX = 720;

interface InboxContentProps {
  close: () => void;
  onDispatch: DispatchPrompt;
}

/** The content width, measured; 0 (not laid out yet, or jsdom) counts as wide. */
function useNarrow(ref: RefObject<HTMLElement | null>): boolean {
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? 0;
      setNarrow(w > 0 && w < INBOX_NARROW_PX);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return narrow;
}

export function InboxContent({ close, onDispatch }: InboxContentProps) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const narrow = useNarrow(rootRef);

  const [view, setView] = useState<View>('reply');
  const [lastTab, setLastTab] = useState<TabKey>('reply');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showDetail, setShowDetail] = useState(false);
  const [gone, setGone] = useState<ReadonlySet<string>>(new Set());
  const [edits, setEdits] = useState<Record<string, DraftEdit>>({});
  const [status, setStatus] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  /** An item 有回复 asked for, while the lists refetch to find it. */
  const [gotoId, setGotoId] = useState<string | null>(null);

  const counts = useQuery(overlayQuery.inboxCounts());
  const replyQ = useQuery(overlayQuery.inboxItems('reply'));
  const proposedQ = useQuery(overlayQuery.inboxItems('proposed'));
  const awareQ = useQuery({ ...overlayQuery.inboxItems('aware'), enabled: view === 'aware' });
  const trendQ = useQuery({ ...overlayQuery.inboxItems('trend'), enabled: view === 'trend' });
  const unhealthyIds = counts.data?.unhealthy_sources ?? [];
  const sourcesQ = useQuery({ ...overlayQuery.inboxSources(), enabled: unhealthyIds.length > 0 });

  const queue = useMemo<ItemOut[]>(
    () => [...(replyQ.data ?? []), ...(proposedQ.data ?? [])].filter((x) => !gone.has(x.id)),
    [replyQ.data, proposedQ.data, gone],
  );
  const awareItems = useMemo(() => (awareQ.data ?? []).filter((x) => !gone.has(x.id)), [awareQ.data, gone]);

  // the selection follows the list: a missing or handled id falls to the first item
  const effectiveSelected = queue.some((x) => x.id === selectedId) ? selectedId
    : gotoId != null ? null : (queue[0]?.id ?? null);

  const notify = useCallback((msg: string) => setStatus(msg), []);
  const clearStatus = useCallback(() => setStatus(null), []);

  /** Hide a handled item and select the next one in the shown order (else the previous). */
  const onHandled = useCallback((id: string) => {
    const order = queue.map((x) => x.id);
    const i = order.indexOf(id);
    const rest = order.filter((x) => x !== id);
    setGone((g) => new Set(g).add(id));
    if (i >= 0) setSelectedId(rest[i] ?? rest[i - 1] ?? null);
    setShowDetail(false);
  }, [queue]);

  const onEdit = useCallback((id: string, e: DraftEdit | null) => {
    setEdits((all) => {
      const next = { ...all };
      if (e) next[id] = e; else delete next[id];
      return next;
    });
  }, []);

  const goTab = (k: TabKey) => { setView(k); setLastTab(k); setShowDetail(false); };
  // 有回复: open that item in 等你回复. The reply may be newer than the cached list, so
  // the lists refetch first; if it is still not waiting on the person afterwards (it
  // went to 需要知道, or it is handled), say so instead of opening another item.
  const gotoItem = (id: string) => {
    setGone((g) => { const n = new Set(g); n.delete(id); return n; });
    goTab('reply');
    setSelectedId(id);
    setGotoId(id);
    void Promise.all([replyQ.refetch(), proposedQ.refetch()]).then(([r, p]) => {
      const found = [...(r.data ?? []), ...(p.data ?? [])].some((x) => x.id === id);
      setGotoId(null);
      if (found) setShowDetail(true);
      else notify(t('ui.InboxOverlay.reply.gotoMissing'));
    });
  };

  const toChat = (item: ItemOut) => { if (onDispatch(inboxChatPrompt(item), { autoSend: false })) close(); };
  const learn = (p: TrendPoint) => { if (onDispatch(inboxLearnPrompt(p), { autoSend: false })) close(); };

  const refresh = async () => {
    if (refreshing) return;
    setRefreshing(true);
    try {
      await inboxService.refresh();
    } catch {
      notify(t('ui.InboxOverlay.refreshError'));
    } finally {
      setRefreshing(false);
      void invalidateOverlay(qc, 'inbox');
    }
  };

  const c = counts.data;
  const tabs: TabItem<TabKey>[] = [
    { key: 'reply', label: t('ui.InboxOverlay.tabs.reply'), count: c?.reply, testid: 'inbox-tab-reply' },
    { key: 'aware', label: t('ui.InboxOverlay.tabs.aware'), count: c?.aware, testid: 'inbox-tab-aware' },
    { key: 'trend', label: t('ui.InboxOverlay.tabs.trend'), testid: 'inbox-tab-trend' },
    {
      key: 'sent', label: t('ui.InboxOverlay.tabs.sent'), testid: 'inbox-tab-sent',
      // a send that needs the person: the shared Tabs' alert count (the mockup's red dot)
      ...(c && c.sends_need_you > 0 ? { count: c.sends_need_you, tone: 'alert' as const } : {}),
    },
  ];

  const unhealthyLabels = unhealthyIds.map((id) => sourcesQ.data?.find((s) => s.id === id)?.channel_label ?? id);
  const replyLoading = replyQ.isPending || proposedQ.isPending;
  const replyError = (replyQ.isError && replyQ.data === undefined) || (proposedQ.isError && proposedQ.data === undefined);
  const updated = refreshing
    ? t('ui.InboxOverlay.refreshing')
    : c?.updated_at ? t('ui.InboxOverlay.updatedAt', { time: clockTime(c.updated_at) }) : '';

  return (
    <div ref={rootRef} className="relative flex min-h-0 flex-1 flex-col overflow-hidden" data-testid="inbox-overlay"
      data-narrow={narrow || undefined}>
      <div className="flex shrink-0 items-center gap-3 border-b border-[var(--color-border)] px-5 py-2.5">
        <Tabs label={t('ui.InboxOverlay.title')} value={view === 'channels' ? lastTab : view} onChange={goTab} items={tabs} testid="inbox-tabs" />
        <div className="ml-auto flex shrink-0 items-center gap-0.5">
          {!narrow && updated && <span className="mr-1.5 text-small text-[var(--color-text-dim)]" data-testid="inbox-updated">{updated}</span>}
          <button type="button" className="icon-btn text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] disabled:opacity-40"
            onClick={() => void refresh()} disabled={refreshing}
            title={t('ui.InboxOverlay.refresh')} aria-label={t('ui.InboxOverlay.refresh')} data-testid="inbox-refresh">
            <span className="material-symbols-outlined icon-sm">refresh</span>
          </button>
          <button type="button" aria-pressed={view === 'channels'}
            className={`icon-btn hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] ${view === 'channels' ? 'text-[var(--color-primary)]' : 'text-[var(--color-text-muted)]'}`}
            onClick={() => setView(view === 'channels' ? lastTab : 'channels')}
            title={t('ui.InboxOverlay.channelSettings')} aria-label={t('ui.InboxOverlay.channelSettings')} data-testid="inbox-settings">
            <span className="material-symbols-outlined icon-sm">tune</span>
          </button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        {view === 'reply' && (
          <ReplyLane items={queue} loading={replyLoading || gotoId != null} error={replyError}
            onRetry={() => { void replyQ.refetch(); void proposedQ.refetch(); }}
            selectedId={effectiveSelected} onSelect={(id) => { setSelectedId(id); setShowDetail(true); }}
            narrow={narrow} showDetail={showDetail} onBack={() => setShowDetail(false)}
            onHandled={onHandled} edits={edits} onEdit={onEdit}
            unhealthy={unhealthyLabels} onFixChannels={() => setView('channels')}
            awareCount={c?.aware ?? awareItems.length} onGoAware={() => goTab('aware')}
            onToChat={toChat} notify={notify} rootRef={rootRef} />
        )}
        {view === 'aware' && (
          <AwareLane items={awareItems} loading={awareQ.isPending} error={awareQ.isError && awareQ.data === undefined}
            onRetry={() => void awareQ.refetch()} onHandled={(id) => setGone((g) => new Set(g).add(id))}
            onToChat={toChat} notify={notify} />
        )}
        {view === 'trend' && <TrendLane items={(trendQ.data ?? []).filter((x) => !gone.has(x.id))} onLearn={learn} />}
        {view === 'sent' && <SentLane onGotoItem={gotoItem} onFixChannels={() => setView('channels')} notify={notify} />}
        {view === 'channels' && <ChannelSettings onBack={() => setView(lastTab)} notify={notify} />}
      </div>

      <StatusLine message={status} onDone={clearStatus} />
    </div>
  );
}
