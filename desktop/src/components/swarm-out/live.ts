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
import { swarmOutService, type SwarmOutAsk, type SwarmOutCheckStatus, type SwarmOutEnded, type SwarmOutFleet, type SwarmOutRepoFact, type SwarmOutResult, type SwarmOutRun, type SwarmOutRuns } from '../../services/swarmOut';
import { DIMENSIONS, type RunJudge, type Verdict } from './shapes';
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

/** The Brain a run went under, as the cards name it. */
export function brainLabel(r: SwarmOutRun): string {
  const b = r.project_agent?.brain;
  if (!b) return '没走 Project Agent';
  return b === 'General' ? '通用（Runner Agent）' : b;
}

const CHECK: Record<SwarmOutCheckStatus, string> = { ran: '跑了', errored: '报错了', unknown: '跑了但没有结果', not_run: '没跑' };

/** "校验：tests 跑了 · lint 没跑" for a run whose Project Agent named checks, else null.
 *  When older turns were not read, "没跑" is only "没看到". */
export function checksLine(res: SwarmOutResult): string | null {
  const c = res.checks;
  if (!c || c.length === 0) return null;
  const word = (s: SwarmOutCheckStatus) => (s === 'not_run' && res.checks_partial ? '没看到' : CHECK[s] ?? '没跑');
  return `校验：${c.map((x) => `${x.id} ${word(x.status)}`).join(' · ')}${res.checks_partial ? '（只读了最近的轮次）' : ''}`;
}

const gateName = (k: string): string =>
  k === 'integrity' ? '完整性' : k.startsWith('error:') ? `${k.slice(6)} 出错` : k;

/** "Brain gate 拦下 3 次（no_git_push 2、完整性 1）"; for a Project Agent run on a Hive
 *  that does not count them, says so; null otherwise. */
export function gateLine(res: SwarmOutResult): string | null {
  if (res.project_agent && res.gate_refusals === null) return 'Brain gate：这台 Hive 不记录拦截次数';
  const g = Object.entries(res.gate_refusals ?? {}).filter(([, n]) => n > 0);
  if (g.length === 0) return null;
  const total = g.reduce((a, [, n]) => a + n, 0);
  return `Brain gate 拦下 ${total} 次（${g.map(([k, n]) => `${gateName(k)} ${n}`).join('、')}）`;
}

/** "教训：交回 3 条，本机判官写进 Brain 1 条、没通过 1 条、待判 1 条" for a Project Agent
 *  run (design §13); null when the run has no lessons part, or nothing came back and
 *  nothing went wrong. */
export function lessonsLine(res: SwarmOutResult): string | null {
  const l = res.lessons;
  if (!l) return null;
  if (l.error === 'not_supported') return '教训：这台 Hive 还不能交回教训（需要更新 Hive）';
  if (l.sent === 0 && l.returned === 0) return l.error ? '教训：这次没取回来，下次检查再试' : null;
  const parts = [`本机判官写进 Brain ${l.written} 条`];
  if (l.rejected) parts.push(`没通过 ${l.rejected} 条`);
  if (l.pending) parts.push(`待判 ${l.pending} 条`);
  const extra = [l.skipped ? `${l.skipped} 条不是这个 Brain 的，没收` : '',
    l.refused ? `${l.refused} 条没过本机的检查` : ''].filter(Boolean);
  return `教训：交回 ${l.sent} 条，${parts.join('、')}${extra.length ? `（${extra.join('，')}）` : ''}`;
}

export interface BrainRow { brain: string; label: string; runs: number; completed: number; failed: number; checksNotRun: number }

/** The last `days` days' runs by the Brain they went under, Brains by run count;
 *  runs without a Project Agent last. */
export function runsByBrain(runs: SwarmOutRun[], now: number, days = 7): BrainRow[] {
  const by = new Map<string, BrainRow>();
  for (const r of runs) {
    if (!(now - sentAt(r) < days * DAY_MS)) continue;
    const key = r.project_agent?.brain ?? '';
    const row = by.get(key) ?? { brain: key, label: brainLabel(r), runs: 0, completed: 0, failed: 0, checksNotRun: 0 };
    row.runs += 1;
    const bucket = outcomeBucket(r);
    if (bucket === 'completed') row.completed += 1;
    else if (bucket === 'error') row.failed += 1;
    // a run still going has not reached its checks yet
    if (r.result && !r.result.provisional && r.state !== 'running') {
      row.checksNotRun += (r.result.checks ?? []).filter((c) => c.status !== 'ran').length;
    }
    by.set(key, row);
  }
  return [...by.values()].sort((a, b) => (a.brain ? 0 : 1) - (b.brain ? 0 : 1) || b.runs - a.runs
    || a.label.localeCompare(b.label));
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

// ── a run's result (design docs/design/2026-10-28-swarm-out-run-results.md §4.6) ──

/** How a run ended, in the card's words, and whether it is the one to look at (an
 *  error). Facts only: a provisional ending says so, an unrecorded one says why. */
export function endedLabel(res: SwarmOutResult): { text: string; tone: 'error' | 'normal' | 'muted' } {
  const base: Record<SwarmOutEnded, string> = {
    completed: '做完', turn_limit: '到了轮数上限', error: '出错', crashed: '出错（崩溃）',
    paused: '停在问题或审批上', no_terminal: '没有正常结束（没收到结束事件）', stopped: '被停', restarted: '进程重启后没接上',
    superseded: '被新消息接替', gave_up: '重试用尽', no_record: '没有结束记录',
  };
  let text = base[res.ended] ?? res.ended;
  if ((res.ended === 'error' || res.ended === 'crashed') && res.error?.code && res.error.code !== 'other') text += ` · ${res.error.code}`;
  if (res.ended === 'no_record' && !res.outcome_recorded) {
    text = res.hive_records_outcomes ? '没有结束记录：这个 turn 没走到结束（进程中途退出）'
      : '结束方式不知道：这台 Hive 的版本不记录，或 turn 没走到结束';
  }
  if (res.provisional && res.ended !== 'no_record') text += ' · 暂定';
  const tone = res.ended === 'error' || res.ended === 'crashed' || res.ended === 'gave_up' ? 'error'
    : res.provisional || res.ended === 'no_record' || res.ended === 'no_terminal' ? 'muted' : 'normal';
  return { text, tone };
}

const repoName = (dir: string | null, url: string | null): string => {
  // an scp-style URL (git@host:owner/name) names its path after the colon
  const parts = (dir || (url ?? '').replace(/^[^/:]+@[^/:]+:/, '') || '').replace(/\.git$/, '').split('/').filter(Boolean);
  return parts.slice(-2).join('/') || '仓库';
};

const PUSHED: Record<SwarmOutRepoFact['pushed'], string> = {
  yes: '已推送', no: '没推送', nothing_to_push: '没有新 commit', unknown: '推送情况不明',
};

/** The errors core/task_repos writes (the only ones /runs passes through). */
const REPO_ERROR: Record<string, string> = {
  'the directory leads outside Hive/repos': '目录指到了 Hive/repos 外面，没读',
  'the repository has no commit checked out': '没有检出任何 commit',
  "the declared ref is not among the repository's remote refs": '远端没有任务给的分支或 tag',
  'HEAD and the declared ref share no history': '当前代码和任务给的分支没有共同历史',
  'git diff failed': 'git diff 失败',
  'reading the repository timed out': '读仓库超时',
  'could not read the repository': '读不了这个仓库',
};

/** One line per thing the run did (code per repository, files by kind, a pipeline,
 *  or only an answer), in the order the kinds come. Empty when nothing is known. */
export function workLines(res: SwarmOutResult): string[] {
  const out: string[] = [];
  const p = res.pipeline;
  if (p) {
    const land = p.landing === 'landed' ? `已落地${p.landed_sha ? ` ${p.landed_sha}` : ''}`
      : p.landing === 'not_landed' ? '没有落地' : p.landing === 'no_code' ? '没有代码改动' : (p.status ?? '');
    out.push(['pipeline', p.profile, land].filter(Boolean).join(' · '));
  }
  for (const r of res.work.repos ?? []) {
    const name = repoName(r.dir, r.url);
    if (r.present === false) out.push(`${name}：还没拉下来`);
    else if (r.origin_matches === false) out.push(`${name}：origin 不是任务给的地址，没计入`);
    else if (r.error) out.push(`${name}：${REPO_ERROR[r.error] ?? '读不了这个仓库'}`);
    else {
      // commits only: the Hive never reads the working tree (core/task_repos)
      out.push(`${name}：${r.commits_total} 个 commit，改了 ${r.files_total} 个文件 +${r.added} −${r.deleted}，${PUSHED[r.pushed]}`
        + (r.untracked ? `，另有 ${r.untracked} 个未跟踪文件` : ''));
    }
  }
  const declared = new Set((res.work.repos ?? []).map((r) => repoName(r.dir, r.url).split('/').pop()));
  for (const c of res.work.changes) {
    if (c.repo && declared.has(c.repo)) continue;   // the declared repository's facts are the whole change
    out.push(`${c.repo ?? '一个仓库'}：${c.files} 个文件 +${c.added} −${c.deleted}${c.landed.length ? `，已提交 ${c.landed[c.landed.length - 1]}` : ''}`);
  }
  const by = { docs: 0, knowledge: 0, outside: 0 };
  for (const f of res.work.files) {
    if (f.kind === 'knowledge') by.knowledge += 1;
    else if (f.kind === 'external-diff' || f.kind === 'external-nodiff') by.outside += 1;
    else by.docs += 1;
  }
  const files = [by.docs && `${by.docs} 份文档`, by.knowledge && `${by.knowledge} 个知识文件`,
    by.outside && `${by.outside} 个仓库外的文件`].filter(Boolean);
  if (files.length) out.push(`写了 ${files.join('、')}${res.work.files_omitted ? `（另有 ${res.work.files_omitted} 个没列出）` : ''}`);
  if (!out.length && res.kinds.includes('answer')) out.push('只回答，没有产出文件');
  return out;
}

const minutes = (s: number): string => (s < 60 ? `${s} 秒` : `${Math.round(s / 60)} 分钟`);

/** The card's numbers: when it ended, time, cost, people asked, failed steps. A value
 *  not known is "—" with the reason, never a zero. */
export function numberParts(res: SwarmOutResult): string[] {
  const h = res.work.human;
  const asked = h.approved + h.denied + h.unanswered + h.answered_questions;
  const t = res.ended_at ? Date.parse(res.ended_at) : NaN;
  return [
    Number.isNaN(t) ? '' : `结束于 ${new Date(t).toLocaleString(undefined, { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}`,
    res.active_s == null ? '用时 —' : `用时 ${minutes(res.active_s)}`,
    res.cost_usd == null ? '花费 —（没有记录）' : `花费 ${res.cost_partial ? '至少 ' : ''}$${res.cost_usd.toFixed(2)}`,
    `问人 ${asked} 次${h.unanswered ? `（${h.unanswered} 个没回答）` : ''}`,
    `失败 ${res.work.steps.failed} 步`,
  ].filter(Boolean);
}

/** A pipeline run's five audit rows as the JudgeTag's shape (in its dimension order;
 *  a dimension the run does not carry is N/A), or null for any other run. */
export function runJudge(res: SwarmOutResult): RunJudge | null {
  const audit = res.pipeline?.audit ?? [];
  if (!res.pipeline || audit.length === 0) return null;
  const norm = (v: string | null): Verdict => (v === 'N/A' ? 'NA' : (['PASS', 'WARN', 'BLOCK', 'NA', 'PENDING'].includes(v ?? '') ? v as Verdict : 'NA'));
  const row = (d: string) => audit.find((a) => a.dim === d);
  const p = res.pipeline;
  return {
    outcome: [p.status, p.landing, p.landed_sha].filter(Boolean).join(' · ').toUpperCase(),
    verdicts: DIMENSIONS.map((d) => norm(row(d)?.verdict ?? null)) as RunJudge['verdicts'],
    numbers: DIMENSIONS.map((d) => row(d)?.detail ?? '') as RunJudge['numbers'],
  };
}

/** Why a run's result could not be read, in the panel's words. */
export function resultErrorLabel(e: string | null | undefined): string {
  if (!e) return '';
  return ({ session_gone: 'Hive 上已经没有这个会话', hive_silent: 'Hive 没回答，稍后再试',
    hive_not_running: 'Hive 不在运行', hive_unusable: '这台 Hive 的记录不完整', unexpected_body: 'Hive 返回的内容不对',
    'no session': '没有会话 id' } as Record<string, string>)[e] ?? (e.startsWith('http_') ? `Hive 返回 ${e.slice(5)}` : e);
}

export type OutcomeBucket = 'completed' | 'error' | 'stopped' | 'other';

/** The history bucket of a run: done, failed, stopped, or not known (no result yet,
 *  provisional, no record). */
export function outcomeBucket(r: SwarmOutRun): OutcomeBucket {
  const res = r.result;
  if (!res) return 'other';   // paused / no_record (the provisional endings) fall to 'other' below
  if (res.ended === 'completed' || res.ended === 'turn_limit') return 'completed';
  if (res.ended === 'error' || res.ended === 'crashed' || res.ended === 'gave_up') return 'error';
  if (res.ended === 'stopped' || res.ended === 'superseded' || res.ended === 'restarted') return 'stopped';
  return 'other';
}

/** Runs sent per local calendar day over the last `days` days, oldest first, each
 *  day split by outcome (the same days as dailySent). */
export function dailyByOutcome(runs: SwarmOutRun[], now: number, days = 14):
{ label: string; count: number; by: Record<OutcomeBucket, number> }[] {
  const days_ = dailySent([], now, days).map((d) => ({ ...d, by: { completed: 0, error: 0, stopped: 0, other: 0 } }));
  const dayOf = (t: number) => { const d = new Date(t); return `${d.getMonth() + 1}/${d.getDate()}`; };
  const first = new Date(now);
  first.setHours(0, 0, 0, 0);
  first.setDate(first.getDate() - (days - 1));
  const byLabel = new Map(days_.map((d) => [d.label, d]));
  for (const r of runs) {
    const t = Date.parse(r.sent_at);
    if (Number.isNaN(t) || t < first.getTime() || t > now) continue;
    const d = byLabel.get(dayOf(t));
    if (!d) continue;
    d.count += 1;
    d.by[outcomeBucket(r)] += 1;
  }
  return days_;
}
