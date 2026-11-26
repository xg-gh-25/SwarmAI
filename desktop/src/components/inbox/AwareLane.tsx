/**
 * AwareLane — 需要知道: what the person should know, no reply expected (design
 * docs/design/2026-11-20-inbox.md §7). One row per thread (the server's newest-first
 * order; a muted "+N" when N more of that thread are open); the list spans days.
 *
 * Lead "N 件需要知道的事" (threads), then:
 * 1. 要你动手 (only when an item has `needs_action`): something to do without replying
 *    (approve, renew, fill in). Sorted by `due` (soonest first, no date last), then
 *    newest. Each row shows its due chip: "已过期 · 11/20" in red when past, "今天到期"
 *    and "12/24 前" in the warning colour up to 3 days ahead, muted later; and two
 *    always-visible buttons: 打开原文 (an http(s) link only) and 处理好了.
 * 2. The rest, grouped by the local day it came in (今天 / 昨天 / "11月21日"). A day's
 *    header has 这一天的都知道了: the first click turns it into "再点一次，标完这一天的
 *    N 条" (back after 4 s or when the pointer leaves), the second sends one `doneMany`
 *    for that day's rows, which are then hidden here; the toast counts what the server
 *    marked. Rows show 交给 Swarm and 知道了 on hover / focus.
 * After a row is handled, focus goes to the next row shown (else the previous, else the
 * lane), never to <body>.
 *
 * The left rail (`CategoryRail`) narrows what is shown: 全部 · 要你动手; 时间: 今天 · 昨天 ·
 * 更早; 渠道: one entry per channel the items come from. Each entry carries its count; an
 * entry with nothing in it is not listed (全部 always is, without a count: the tab has it).
 * Channels keep a fixed order (mail, Slack, GitHub, other). When the chosen part runs out
 * (its last item handled), the rail goes back to 全部. The content shows the chosen part
 * with the same two sections. Below 720 px the rail is a row of chips on top.
 * Rows in a group sit closer together than the groups do; whitespace only, no boxes.
 */
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useState, type ReactNode } from 'react';
import { invalidateOverlay } from '../layout/overlayQueries';
import { OverlayEmpty, OverlayError, OverlayLoading } from '../layout/OverlayState';
import { channelKey, inboxService, type ItemOut } from '../../services/inbox';
import { openExternal } from '../../utils/openExternal';
import { Avatar, CategoryRail, ChannelPill, ThreadMore, dayKey, dayLabel, daysUntil, relTime, webUrl, type RailItem } from './parts';

/** A due date this many days away (or less) is shown in the warning colour; past, in red. */
const SOON_DAYS = 3;
/** How long 这一天的都知道了 waits for its confirming click. */
const CONFIRM_MS = 4000;

const stamp = (iso: string) => Date.parse(iso) || 0;

/** 要你动手 order: due soonest first, no due date last; then newest first. */
function byDue(a: ItemOut, b: ItemOut): number {
  if (a.due !== b.due) {
    if (!a.due) return 1;
    if (!b.due) return -1;
    return a.due < b.due ? -1 : 1;
  }
  return stamp(b.received_at) - stamp(a.received_at);
}

type When = 'today' | 'yesterday' | 'earlier';

/** Which day an item came in, as the rail counts it (local days). */
function whenOf(iso: string, now = new Date()): When {
  const k = dayKey(iso);
  if (k === dayKey(now.toISOString())) return 'today';
  if (k === dayKey(new Date(now.getTime() - 86_400_000).toISOString())) return 'yesterday';
  return 'earlier';
}

export function AwareLane({ items: all, loading, error, onRetry, onHandled, onToChat, notify, narrow = false }: {
  items: ItemOut[]; loading: boolean; error: boolean; onRetry: () => void; narrow?: boolean;
  /** Hide a handled row; `focusId` is the row to focus next (null: the lane itself). */
  onHandled: (id: string, focusId?: string | null) => void;
  onToChat: (item: ItemOut) => void; notify: (msg: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  /** The rail's choice: all, act, a time (today / yesterday / earlier) or `ch:<channel>`. */
  const [chosen, setPart] = useState<string>('all');
  const match = (x: ItemOut, k: string) =>
    k === 'all' ? true : k === 'act' ? x.needs_action
      : k.startsWith('ch:') ? channelKey(x.source_kind) === k.slice(3) : whenOf(x.received_at) === k;
  // a part with nothing left (its last item handled) falls back to 全部, and stays there
  const part = chosen !== 'all' && !all.some((x) => match(x, chosen)) ? 'all' : chosen;
  useEffect(() => { if (part !== chosen) setPart(part); }, [part, chosen]);
  const items = all.filter((x) => match(x, part));
  const railItem = (key: string, label: string): RailItem<string> | null => {
    const count = all.filter((x) => match(x, key)).length;
    return key === 'all' ? { key, label } : count ? { key, label, count } : null;
  };
  const channels: RailItem<string>[] = [];
  for (const x of all) {
    const k = `ch:${channelKey(x.source_kind)}`;
    if (!channels.some((c) => c.key === k)) channels.push({ key: k, label: x.channel_label, count: 0 });
  }
  channels.forEach((c) => { c.count = all.filter((x) => match(x, c.key)).length; });
  const ORDER = ['ch:mail', 'ch:slack', 'ch:github'];
  const rank = (k: string) => (ORDER.indexOf(k) + 1 || ORDER.length + 1);
  channels.sort((a, b) => rank(a.key) - rank(b.key) || a.label.localeCompare(b.label));
  const rail = [
    { items: [railItem('all', t('ui.InboxOverlay.aware.railAll')), railItem('act', t('ui.InboxOverlay.aware.actTitle'))] },
    { title: t('ui.InboxOverlay.aware.railWhen'), items: [
      railItem('today', t('ui.InboxOverlay.time.today')), railItem('yesterday', t('ui.InboxOverlay.time.yesterday')),
      railItem('earlier', t('ui.InboxOverlay.aware.railEarlier'))] },
    { title: t('ui.InboxOverlay.aware.railChannel'), items: channels },
  ].map((g) => ({ ...g, items: g.items.filter((x): x is RailItem<string> => x != null) }));
  const partLabel = rail.flatMap((g) => g.items).find((x) => x.key === part)?.label ?? '';
  /** The row id, or `day:<key>`, being written. */
  const [busy, setBusy] = useState<string | null>(null);
  /** The day whose 这一天的都知道了 was clicked once and waits for the confirming click. */
  const [confirmDay, setConfirmDay] = useState<string | null>(null);
  useEffect(() => {
    if (!confirmDay) return;
    const id = setTimeout(() => setConfirmDay(null), CONFIRM_MS);
    return () => clearTimeout(id);
  }, [confirmDay]);

  const toAct = items.filter((x) => x.needs_action).sort(byDue);
  const rest = items.filter((x) => !x.needs_action);
  const days: string[] = [];
  for (const x of rest) {
    const k = dayKey(x.received_at);
    if (!days.includes(k)) days.push(k);
  }
  const groupOf = (d: string) => rest.filter((x) => dayKey(x.received_at) === d);
  /** The rows in the order shown: 要你动手, then each day. */
  const shown = [...toAct, ...days.flatMap(groupOf)].map((x) => x.id);

  /** The row that takes focus once `hidden` are gone: the next one shown, else the
   *  previous, else none (the lane). */
  const nextAfter = (hidden: string[]): string | null => {
    const gone = new Set(hidden);
    const idx = shown.map((id, i) => (gone.has(id) ? i : -1)).filter((i) => i >= 0);
    if (!idx.length) return null;
    const after = shown.slice(idx[idx.length - 1] + 1).find((id) => !gone.has(id));
    return after ?? shown.slice(0, idx[0]).reverse().find((id) => !gone.has(id)) ?? null;
  };

  const run = async <R,>(key: string, fn: () => Promise<R>, after: (r: R) => void) => {
    if (busy) return;
    setBusy(key);
    try {
      after(await fn());
    } catch {
      notify(t('ui.InboxOverlay.actionError'));
    } finally {
      setBusy(null);
      void invalidateOverlay(qc, 'inbox');
    }
  };

  const done = (item: ItemOut, msgKey: string) =>
    run(item.id, () => inboxService.action(item.id, 'done'), () => {
      onHandled(item.id, nextAfter([item.id]));
      notify(t(msgKey));
    });

  const doneDay = (key: string, group: ItemOut[]) => {
    const ids = group.map((x) => x.id);
    const focusId = nextAfter(ids);
    return run(`day:${key}`, () => inboxService.doneMany(ids), (handled) => {
      ids.forEach((id, i) => onHandled(id, i === ids.length - 1 ? focusId : undefined));
      notify(t('ui.InboxOverlay.aware.dayMarked', { count: handled }));
    });
  };

  // 这一天的都知道了 marks many rows at once: the first click asks, the second does it
  const onDayDone = (d: string, group: ItemOut[]) => {
    if (confirmDay !== d) { setConfirmDay(d); return; }
    setConfirmDay(null);
    void doneDay(d, group);
  };

  const showRail = !loading && !error && all.length > 0;
  return (
    <div className={`flex min-h-0 flex-1 ${narrow ? 'flex-col' : ''}`}>
      {showRail && (
        <CategoryRail groups={rail} value={part} onChange={setPart} narrow={narrow}
          label={t('ui.InboxOverlay.railLabel')} testid="inbox-aware-rail" />
      )}
    <div className="min-h-0 flex-1 overflow-y-auto" data-testid="inbox-aware">
      <div className={`mx-auto max-w-[760px] pb-14 pt-7 ${narrow ? 'px-6' : 'px-10'}`}>
        {loading ? <OverlayLoading /> : error ? (
          <OverlayError testid="inbox-load-error" retryTestid="inbox-load-retry" message={t('ui.InboxOverlay.loadError')} onRetry={onRetry} />
        ) : all.length === 0 ? (
          <OverlayEmpty icon="check_circle">{t('ui.InboxOverlay.aware.empty')}</OverlayEmpty>
        ) : items.length === 0 ? (
          <OverlayEmpty icon="check_circle">{t('ui.InboxOverlay.aware.emptyPart')}</OverlayEmpty>
        ) : (
          <>
            <p className="text-display font-semibold text-[var(--color-text)]" data-testid="inbox-aware-lead">
              {part === 'all' ? t('ui.InboxOverlay.aware.lead', { count: items.length }) : `${partLabel} · ${items.length}`}
            </p>
            <p className="mt-1 text-small text-[var(--color-text-dim)]">{t('ui.InboxOverlay.aware.sub')}</p>

            {toAct.length > 0 && (
              <section className="mt-7" data-testid="inbox-aware-act">
                <h2 className="mb-1 px-3 text-meta font-semibold text-[var(--color-text-muted)]">{t('ui.InboxOverlay.aware.actTitle')}</h2>
                <ul className="flex flex-col gap-0.5">
                  {toAct.map((a) => (
                    <ActRow key={a.id} item={a} busy={busy === a.id} onDone={() => void done(a, 'ui.InboxOverlay.aware.handledToast')} />
                  ))}
                </ul>
              </section>
            )}

            {days.map((d) => {
              const group = groupOf(d);
              const confirming = confirmDay === d;
              return (
                <section key={d} className="mt-7" data-testid="inbox-aware-day" data-day={d}>
                  <div className="mb-1 flex items-center px-3">
                    <h2 className="text-meta font-semibold text-[var(--color-text-muted)]">{dayLabel(d, t, i18n.language)}</h2>
                    <button type="button" disabled={busy != null} data-confirm={confirming || undefined}
                      className={`ml-auto text-meta disabled:opacity-50 ${confirming ? 'font-semibold text-[var(--color-primary)]' : 'text-[var(--color-text-dim)] hover:text-[var(--color-text)]'}`}
                      onClick={() => onDayDone(d, group)} onMouseLeave={() => { if (confirming) setConfirmDay(null); }}
                      data-testid="inbox-aware-day-done">
                      {confirming ? t('ui.InboxOverlay.aware.dayConfirm', { count: group.length }) : t('ui.InboxOverlay.aware.dayDone')}
                    </button>
                  </div>
                  <ul className="flex flex-col gap-0.5">
                    {group.map((a) => (
                      <li key={a.id} data-testid="inbox-aware-row" data-item-id={a.id} tabIndex={-1}
                        className="group flex outline-none focus-visible:bg-[var(--color-hover)] items-center gap-3.5 rounded-xl px-3 py-2.5 hover:bg-[var(--color-hover)]">
                        <Avatar name={a.author} size="sm" />
                        <span className="min-w-0 flex-1">
                          <span className="text-body font-semibold text-[var(--color-text)]">{a.ask || a.excerpt}</span>
                          <Meta item={a} />
                        </span>
                        <span className="flex gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                          <button type="button" className="icon-btn text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]"
                            onClick={() => onToChat(a)} title={t('ui.InboxOverlay.toChatHint')} aria-label={t('ui.InboxOverlay.toChat')} data-testid="inbox-aware-chat">
                            <span className="material-symbols-outlined icon-sm">chat</span>
                          </button>
                          <button type="button" className="icon-btn text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]"
                            onClick={() => void done(a, 'ui.InboxOverlay.aware.marked')} disabled={busy === a.id}
                            title={t('ui.InboxOverlay.aware.done')} aria-label={t('ui.InboxOverlay.aware.done')} data-testid="inbox-aware-done">
                            <span className="material-symbols-outlined icon-sm">done</span>
                          </button>
                        </span>
                      </li>
                    ))}
                  </ul>
                </section>
              );
            })}
          </>
        )}
      </div>
    </div>
    </div>
  );
}

/** The muted line under a row's sentence: channel · who · where · when, "+N". */
function Meta({ item, due }: { item: ItemOut; due?: ReactNode }) {
  const { t } = useTranslation();
  return (
    <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-small text-[var(--color-text-dim)]">
      {due}
      <ChannelPill kind={item.source_kind} label={item.channel_label} />
      <span>{[item.author, item.where, relTime(item.received_at, t)].filter(Boolean).join(' · ')}</span>
      <ThreadMore count={item.thread_count} />
    </span>
  );
}

function ActRow({ item, busy, onDone }: { item: ItemOut; busy: boolean; onDone: () => void }) {
  const { t } = useTranslation();
  const url = webUrl(item.url);
  const due = daysUntil(item.due);
  // past: error; today and the next SOON_DAYS days: warning; later: muted
  const tone = due == null ? null : due.days < 0 ? 'overdue' : due.days === 0 ? 'today' : due.days <= SOON_DAYS ? 'soon' : 'later';
  const v = tone === 'overdue' ? '--color-status-error' : tone === 'later' ? '--color-text-muted' : '--color-status-warning';
  const dueText = due == null ? '' : tone === 'overdue' ? t('ui.InboxOverlay.aware.dueOverdue', { date: due.short })
    : tone === 'today' ? t('ui.InboxOverlay.aware.dueToday') : t('ui.InboxOverlay.aware.due', { date: due.short });
  return (
    <li data-testid="inbox-aware-act-row" data-item-id={item.id} tabIndex={-1} className="flex outline-none items-center gap-3.5 rounded-xl px-3 py-2.5">
      <Avatar name={item.author} size="sm" />
      <span className="min-w-0 flex-1">
        <span className="text-body font-semibold text-[var(--color-text)]">{item.ask || item.excerpt}</span>
        <Meta item={item} due={due && (
          <span data-testid="inbox-aware-due" data-tone={tone ?? undefined}
            className={`inline-flex h-5 items-center rounded-md px-1.5 text-meta ${tone === 'later' ? '' : 'font-semibold'}`}
            style={{ color: `var(${v})`, background: `color-mix(in srgb, var(${v}) 12%, transparent)` }}>
            {dueText}
          </span>
        )} />
      </span>
      <span className="flex shrink-0 items-center gap-1">
        {url && (
          <button type="button" className="btn-sm btn-ghost" onClick={() => { void openExternal(url).catch(() => undefined); }}
            data-testid="inbox-aware-open">
            {t('ui.InboxOverlay.reply.openOriginal')}
          </button>
        )}
        <button type="button" className="btn-sm btn-secondary" disabled={busy} onClick={onDone} data-testid="inbox-aware-handled">
          {t('ui.InboxOverlay.aware.handled')}
        </button>
      </span>
    </li>
  );
}
