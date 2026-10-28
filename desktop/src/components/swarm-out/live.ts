/**
 * The Swarm Out panel's live data (design docs/design/2026-10-28-swarm-out-live-dashboard.md).
 *
 * Two polls while the panel is shown: /api/swarm-out/runs (dispatched runs with their
 * state from the Hive, and the questions waiting) and /api/swarm-out/fleet (every
 * Hive with the load a running one reports). Each keeps its last answer on a failed
 * poll (an unreachable daemon is not "nothing"), and says whether the last poll failed.
 *
 * The numbers are pure functions of those answers, and the counts of waiting and
 * running use the tab badge's own rule (activityOf): the two can differ only for the
 * moment between their polls, never by rule.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { swarmOutService, type SwarmOutAsk, type SwarmOutFleet, type SwarmOutRun, type SwarmOutRuns } from '../../services/swarmOut';
import { activityOf, waitingUnasked } from './useSwarmOutActivity';

export const LIVE_POLL_MS = 30_000;
const DAY_MS = 24 * 3600 * 1000;

interface Polled<T> {
  data: T | null;
  failed: boolean;
  reload: () => void;
}

function usePoll<T>(fetch: () => Promise<T>, hidden: boolean): Polled<T> {
  const [data, setData] = useState<T | null>(null);
  const [failed, setFailed] = useState(false);
  const seq = useRef(0);
  const load = useCallback(async () => {
    const mine = ++seq.current;   // an older answer arriving late never overwrites a newer one
    try {
      const d = await fetch();
      if (mine === seq.current) { setData(d); setFailed(false); }
    } catch {
      if (mine === seq.current) setFailed(true);   // keep the last answer
    }
  }, [fetch]);
  useEffect(() => {
    if (hidden) return undefined;
    void load();
    const id = window.setInterval(() => { void load(); }, LIVE_POLL_MS);
    return () => window.clearInterval(id);
  }, [hidden, load]);
  return { data, failed, reload: () => { void load(); } };
}

const fetchRuns = () => swarmOutService.runs();
const fetchFleet = () => swarmOutService.fleet();

export const useRuns = (hidden: boolean): Polled<SwarmOutRuns> => usePoll(fetchRuns, hidden);
export const useFleet = (hidden: boolean): Polled<SwarmOutFleet> => usePoll(fetchFleet, hidden);

/** Free slots and capacity over the running Hives that answered: a full (saturated)
 *  Hive has none, and more streams than its cap never makes a negative. null when no
 *  running Hive answered (unknown, not zero). */
export function capacity(fleet: SwarmOutFleet | null): { free: number; total: number } | null {
  const up = (fleet?.hives ?? []).filter((h) => h.status === 'running' && h.reachable && h.max_concurrent != null);
  if (up.length === 0) return null;
  let free = 0;
  let total = 0;
  for (const h of up) {
    const cap = h.max_concurrent ?? 0;
    total += cap;
    free += h.saturated ? 0 : Math.max(0, cap - (h.streaming ?? 0));
  }
  return { free, total };
}

const sentAt = (r: SwarmOutRun): number => Date.parse(r.sent_at);
const recent = (r: SwarmOutRun, now: number): boolean => now - sentAt(r) < DAY_MS;

/** A run's state, from `state` or, from an older daemon without it, the booleans. */
const stateOf = (r: SwarmOutRun): NonNullable<SwarmOutRun['state']> =>
  r.state ?? (r.waiting ? 'waiting' : r.running === true ? 'running' : r.running === false ? 'stopped' : 'unknown');

/** The run view's groups. Stopped runs show only when sent in the last 24 h (older
 *  ones are in the history view); unknown ones too, except a dispatch whose session
 *  was never named (it may still be running). A waiting run shows its question in the
 *  等你 section; one whose question has not reached this Host at all is listed there
 *  as a run (waitingUnasked), so it never vanishes. */
export function runGroups(runs: SwarmOutRun[], now: number, asks: SwarmOutAsk[] = []) {
  return {
    waitingUnasked: waitingUnasked({ runs, asks, errors: [] }),
    running: runs.filter((r) => stateOf(r) === 'running'),
    stopped: runs.filter((r) => stateOf(r) === 'stopped' && recent(r, now)),
    unknown: runs.filter((r) => stateOf(r) === 'unknown' && (r.session_unknown || recent(r, now))),
  };
}

/** The numbers at the top of the run view: what waits on the person (and, apart, what
 *  must be settled on the Hive's own page), the free slots, what is running. */
export function panelNumbers(data: SwarmOutRuns | null, fleet: SwarmOutFleet | null) {
  const act = data ? activityOf(data) : null;
  return {
    waiting: act?.waiting ?? null,
    onHivePage: data ? data.asks.filter((a) => a.refused).length : null,
    capacity: capacity(fleet),
    running: act?.running ?? null,
  };
}

/** Runs sent per local calendar day over the last `days` days, oldest first. */
export function dailySent(runs: SwarmOutRun[], now: number, days = 14): { label: string; count: number }[] {
  const dayStart = (t: number) => { const d = new Date(t); d.setHours(0, 0, 0, 0); return d.getTime(); };
  const buckets: { label: string; start: number; count: number }[] = [];
  const today = new Date(dayStart(now));
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(today.getDate() - i);
    buckets.push({ label: `${d.getMonth() + 1}/${d.getDate()}`, start: d.getTime(), count: 0 });
  }
  const byStart = new Map(buckets.map((b) => [b.start, b]));
  for (const r of runs) {
    const t = sentAt(r);
    if (!Number.isNaN(t)) {
      const b = byStart.get(dayStart(t));
      if (b) b.count += 1;
    }
  }
  return buckets.map(({ label, count }) => ({ label, count }));
}

/** The last `days` days' runs by Hive, newest first in each, Hives by run count. */
export function runsByHive(runs: SwarmOutRun[], now: number, days = 7): { hive: string; runs: SwarmOutRun[] }[] {
  const by = new Map<string, SwarmOutRun[]>();
  for (const r of runs) {
    if (!(now - sentAt(r) < days * DAY_MS)) continue;
    const g = by.get(r.hive);
    if (g) g.push(r);
    else by.set(r.hive, [r]);
  }
  return [...by]
    .map(([hive, rs]) => ({ hive, runs: [...rs].sort((a, b) => sentAt(b) - sentAt(a)) }))
    .sort((a, b) => b.runs.length - a.runs.length || a.hive.localeCompare(b.hive));
}

/** What a run's state means to the person, in the words the cards use. */
export function stateLabel(r: SwarmOutRun): string {
  switch (stateOf(r)) {
    case 'waiting': return '在等你';
    case 'running': return r.state_reason && r.state_reason !== 'streaming' ? `在跑 · ${r.state_reason}` : '在跑';
    case 'stopped': return r.state_reason === 'hive_stopped' ? '不在跑了 · Hive 已关机'
      : r.state_reason === 'hive_gone' ? '不在跑了 · Hive 已删除' : '不在跑了';
    default: return r.session_unknown ? '会话未确认，可能在跑' : '状态未知 · Hive 没回答';
  }
}

/** A Hive's status in the panel's words. */
export function hiveStatusLabel(status: string): string {
  return ({ running: '运行中', stopped: '已关机', starting: '启动中', stopping: '关机中', updating: '更新中',
    deploying: '部署中', provisioning: '部署中', error: '出错', failed: '出错' } as Record<string, string>)[status] ?? status;
}

/** The daemon's English messages about what it could not read, in the panel's words. */
export function errorLabel(e: string): string {
  const q = /^could not read the questions of Hive (.+)$/.exec(e);
  if (q) return `读不到 Hive ${q[1]} 的问题：「等你」可能不全`;
  if (e === 'could not list the running Hives') return '读不到 Hive 列表：只显示派发记录里的 Hive';
  if (e === 'could not read a Hive record') return '有一台 Hive 的记录读不出来';
  return e;
}
