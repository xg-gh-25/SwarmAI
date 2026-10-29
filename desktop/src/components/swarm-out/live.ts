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
 *
 * Every label here comes from the locale files (swarmOut.* in i18n/locales), read at
 * call time through the app's i18n instance, so a panel rendered after a language
 * switch speaks the new language. Text a Hive wrote is data and is never translated.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import i18n from '../../i18n';
import { swarmOutService, type SwarmOutAsk, type SwarmOutCheckStatus, type SwarmOutEnded, type SwarmOutFleet, type SwarmOutRepoFact, type SwarmOutResult, type SwarmOutRun, type SwarmOutRuns } from '../../services/swarmOut';
import { DIMENSIONS, type RunJudge, type Verdict } from './shapes';
import { activityOf, waitingUnasked } from './useSwarmOutActivity';
import { endedTone, type Tone } from './tone';

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
  if (!b) return i18n.t('swarmOut.brain.none');
  return b === 'General' ? i18n.t('swarmOut.brain.general') : b;
}

const CHECK_KEYS: Record<SwarmOutCheckStatus, string> = { ran: 'ran', errored: 'errored', unknown: 'unknown', not_run: 'notRun' };
const checkWord = (s: SwarmOutCheckStatus): string => i18n.t(`swarmOut.check.${CHECK_KEYS[s] ?? 'notRun'}`);

/** True when a run named checks and one of them did not run cleanly (the amber line). */
export const checksIncomplete = (res: SwarmOutResult): boolean => (res.checks ?? []).some((c) => c.status !== 'ran');

/** "校验：tests 跑了 · lint 没跑" for a run whose Project Agent named checks, else null.
 *  When older turns were not read, "没跑" is only "没看到". */
export function checksLine(res: SwarmOutResult): string | null {
  const c = res.checks;
  if (!c || c.length === 0) return null;
  const word = (s: SwarmOutCheckStatus) => (s === 'not_run' && res.checks_partial ? i18n.t('swarmOut.check.notSeen') : checkWord(s));
  return i18n.t(res.checks_partial ? 'swarmOut.check.linePartial' : 'swarmOut.check.line',
    { list: c.map((x) => `${x.id} ${word(x.status)}`).join(' · ') });
}

const gateName = (k: string): string =>
  k === 'integrity' ? i18n.t('swarmOut.gate.integrity') : k.startsWith('error:') ? i18n.t('swarmOut.gate.errored', { name: k.slice(6) }) : k;

/** "Brain gate 拦下 3 次（no_git_push 2、完整性 1）"; for a Project Agent run on a Hive
 *  that does not count them, says so; null otherwise. */
export function gateLine(res: SwarmOutResult): string | null {
  if (res.project_agent && res.gate_refusals === null) return i18n.t('swarmOut.gate.notCounted');
  const g = Object.entries(res.gate_refusals ?? {}).filter(([, n]) => n > 0);
  if (g.length === 0) return null;
  const total = g.reduce((a, [, n]) => a + n, 0);
  return i18n.t('swarmOut.gate.refused', { count: total, list: g.map(([k, n]) => `${gateName(k)} ${n}`).join(i18n.t('swarmOut.sep.list')) });
}

/** "教训：交回 3 条，本机判官写进 Brain 1 条、没通过 1 条、待判 1 条" for a Project Agent
 *  run (design §13); null when the run has no lessons part, or nothing came back and
 *  nothing went wrong. */
export function lessonsLine(res: SwarmOutResult): string | null {
  const l = res.lessons;
  if (!l) return null;
  if (l.error === 'not_supported') return i18n.t('swarmOut.lessons.notSupported');
  if (l.sent === 0 && l.returned === 0) return l.error ? i18n.t('swarmOut.lessons.notFetched') : null;
  if (l.sent === 0) {
    return l.pending ? i18n.t('swarmOut.lessons.waiting', { count: l.pending })
      : l.skipped ? i18n.t('swarmOut.lessons.noneForBrain', { count: l.skipped }) : null;
  }
  const parts = [i18n.t('swarmOut.lessons.written', { count: l.written })];
  if (l.rejected) parts.push(i18n.t('swarmOut.lessons.rejected', { count: l.rejected }));
  if (l.judging) parts.push(i18n.t('swarmOut.lessons.judging', { count: l.judging }));
  if (l.pending) parts.push(i18n.t('swarmOut.lessons.morePending', { count: l.pending }));
  const extra = [l.skipped ? i18n.t('swarmOut.lessons.skipped', { count: l.skipped }) : '',
    l.refused ? i18n.t('swarmOut.lessons.refused', { count: l.refused }) : ''].filter(Boolean);
  const line = i18n.t('swarmOut.lessons.line', { count: l.sent, parts: parts.join(i18n.t('swarmOut.sep.list')) });
  return extra.length ? i18n.t('swarmOut.lessons.withExtra', { line, extra: extra.join(i18n.t('swarmOut.sep.clause')) }) : line;
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
    case 'waiting': return i18n.t('swarmOut.state.waiting');
    case 'running': return r.state_reason && r.state_reason !== 'streaming'
      ? i18n.t('swarmOut.state.runningWhy', { reason: r.state_reason }) : i18n.t('swarmOut.state.running');
    case 'stopped': return r.state_reason === 'hive_stopped' ? i18n.t('swarmOut.state.stoppedHiveOff')
      : r.state_reason === 'hive_gone' ? i18n.t('swarmOut.state.stoppedHiveGone') : i18n.t('swarmOut.state.stopped');
    default: return r.session_unknown ? i18n.t('swarmOut.state.sessionUnknown') : i18n.t('swarmOut.state.unknown');
  }
}

/** The Hive statuses the locale names (core/hive_manager's states); any other is shown as is. */
export const HIVE_STATUSES = ['running', 'stopped', 'starting', 'stopping', 'updating', 'deploying', 'provisioning', 'error', 'failed'];

/** A Hive's status in the panel's words. */
export function hiveStatusLabel(status: string): string {
  return HIVE_STATUSES.includes(status) ? i18n.t(`swarmOut.hiveStatus.${status}`) : status;
}

/** The daemon's English messages about what it could not read, in the panel's words. */
export function errorLabel(e: string): string {
  const q = /^could not read the questions of Hive (.+)$/.exec(e);
  if (q) return i18n.t('swarmOut.error.questions', { hive: q[1] });
  if (e === 'could not list the running Hives') return i18n.t('swarmOut.error.hiveList');
  if (e === 'could not read a Hive record') return i18n.t('swarmOut.error.hiveRecord');
  return e;
}

// ── a run's result (design docs/design/2026-10-28-swarm-out-run-results.md §4.6) ──

/** Every ending the Hive records (SwarmOutEnded), each with its locale label. */
export const ENDINGS: readonly SwarmOutEnded[] = ['completed', 'turn_limit', 'error', 'crashed', 'paused', 'no_terminal',
  'stopped', 'restarted', 'superseded', 'gave_up', 'no_record'];

/** How a run ended, in the card's words, and its tone (./tone). Facts only: a
 *  provisional ending says so, an unrecorded one says why. */
export function endedLabel(res: SwarmOutResult): { text: string; tone: Tone } {
  let text = (ENDINGS as readonly string[]).includes(res.ended) ? i18n.t(`swarmOut.ended.${res.ended}`) : res.ended;
  if ((res.ended === 'error' || res.ended === 'crashed') && res.error?.code && res.error.code !== 'other') text += ` · ${res.error.code}`;
  if (res.ended === 'no_record' && !res.outcome_recorded) {
    text = res.hive_records_outcomes ? i18n.t('swarmOut.ended.noRecordExited') : i18n.t('swarmOut.ended.noRecordUnknown');
  }
  if (res.provisional && res.ended !== 'no_record') text += i18n.t('swarmOut.ended.provisional');
  return { text, tone: endedTone(res) };
}

const repoName = (dir: string | null, url: string | null): string => {
  // an scp-style URL (git@host:owner/name) names its path after the colon
  const parts = (dir || (url ?? '').replace(/^[^/:]+@[^/:]+:/, '') || '').replace(/\.git$/, '').split('/').filter(Boolean);
  return parts.slice(-2).join('/') || i18n.t('swarmOut.work.repo');
};

const PUSHED: Record<SwarmOutRepoFact['pushed'], string> = {
  yes: 'yes', no: 'no', nothing_to_push: 'nothingToPush', unknown: 'unknown',
};

/** The errors core/task_repos writes (the only ones /runs passes through), by locale key. */
const REPO_ERROR: Record<string, string> = {
  'the directory leads outside Hive/repos': 'outside',
  'the repository has no commit checked out': 'noCommit',
  "the declared ref is not among the repository's remote refs": 'noRef',
  'HEAD and the declared ref share no history': 'noHistory',
  'git diff failed': 'diffFailed',
  'reading the repository timed out': 'timeout',
  'could not read the repository': 'unreadable',
};
const repoError = (e: string): string => i18n.t(`swarmOut.repoError.${REPO_ERROR[e] ?? 'unreadable'}`);

/** One line per thing the run did (code per repository, files by kind, a pipeline,
 *  or only an answer), in the order the kinds come. Empty when nothing is known. */
export function workLines(res: SwarmOutResult): string[] {
  const out: string[] = [];
  const p = res.pipeline;
  if (p) {
    const land = p.landing === 'landed' ? i18n.t('swarmOut.work.landed') + (p.landed_sha ? ` ${p.landed_sha}` : '')
      : p.landing === 'not_landed' ? i18n.t('swarmOut.work.notLanded') : p.landing === 'no_code' ? i18n.t('swarmOut.work.noCode') : (p.status ?? '');
    out.push(['pipeline', p.profile, land].filter(Boolean).join(' · '));
  }
  for (const r of res.work.repos ?? []) {
    const name = repoName(r.dir, r.url);
    const said = (what: string) => i18n.t('swarmOut.work.repoSays', { name, what });
    if (r.present === false) out.push(said(i18n.t('swarmOut.work.notPulled')));
    else if (r.origin_matches === false) out.push(said(i18n.t('swarmOut.work.otherOrigin')));
    else if (r.error) out.push(said(repoError(r.error)));
    else {
      // commits only: the Hive never reads the working tree (core/task_repos)
      out.push(i18n.t('swarmOut.work.repoChanged', {
        name, commits: i18n.t('swarmOut.work.commits', { count: r.commits_total }),
        files: i18n.t('swarmOut.work.files', { count: r.files_total }), added: r.added, deleted: r.deleted,
        pushed: i18n.t(`swarmOut.pushed.${PUSHED[r.pushed] ?? 'unknown'}`),
      }) + (r.untracked ? i18n.t('swarmOut.work.untracked', { count: r.untracked }) : ''));
    }
  }
  const declared = new Set((res.work.repos ?? []).map((r) => repoName(r.dir, r.url).split('/').pop()));
  for (const c of res.work.changes) {
    if (c.repo && declared.has(c.repo)) continue;   // the declared repository's facts are the whole change
    out.push(i18n.t('swarmOut.work.change', {
      name: c.repo ?? i18n.t('swarmOut.work.aRepo'), files: i18n.t('swarmOut.work.files', { count: c.files }), added: c.added, deleted: c.deleted,
    }) + (c.landed.length ? i18n.t('swarmOut.work.committed', { sha: c.landed[c.landed.length - 1] }) : ''));
  }
  const by = { docs: 0, knowledge: 0, outside: 0 };
  for (const f of res.work.files) {
    if (f.kind === 'knowledge') by.knowledge += 1;
    else if (f.kind === 'external-diff' || f.kind === 'external-nodiff') by.outside += 1;
    else by.docs += 1;
  }
  const files = [by.docs && i18n.t('swarmOut.work.docs', { count: by.docs }), by.knowledge && i18n.t('swarmOut.work.knowledge', { count: by.knowledge }),
    by.outside && i18n.t('swarmOut.work.outside', { count: by.outside })].filter(Boolean);
  if (files.length) {
    out.push(i18n.t('swarmOut.work.wrote', { list: files.join(i18n.t('swarmOut.sep.list')) })
      + (res.work.files_omitted ? i18n.t('swarmOut.work.omitted', { count: res.work.files_omitted }) : ''));
  }
  if (!out.length && res.kinds.includes('answer')) out.push(i18n.t('swarmOut.work.answerOnly'));
  return out;
}

const minutes = (s: number): string => (s < 60 ? i18n.t('swarmOut.time.seconds', { count: s }) : i18n.t('swarmOut.time.minutes', { count: Math.round(s / 60) }));

/** A timestamp as month/day hour:minute in the app's language (empty when unparseable). */
export function when(iso: string | null | undefined): string {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isNaN(t) ? '' : new Date(t).toLocaleString(i18n.language || undefined, { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

/** One of a card's facts, shown as a label over a value. */
export interface RunStat {
  key: 'time' | 'human' | 'spend';
  label: string;
  value: string;
  /** a second, smaller line under the value (e.g. how many questions went unanswered) */
  sub?: string;
  /** the breakdown or the reason, on hover */
  title?: string;
  tone?: Tone;
  /** takes two cells (the spend: tokens and cost read as one number) */
  wide?: boolean;
}

/** A token count as K / M / B in every language ("4.8M"), the unit people quote for
 *  model usage; under a thousand, the number itself. */
export function tokenCount(n: number): string {
  const unit = (v: number, u: string) => `${v >= 100 ? Math.round(v) : Number(v.toFixed(1))}${u}`;
  if (n >= 1e9) return unit(n / 1e9, 'B');
  if (n >= 1e6) return unit(n / 1e6, 'M');
  if (n >= 1e3) return unit(n / 1e3, 'K');
  return String(n);
}

/** All the tokens a run used, or null when not known (no record, or a record kept
 *  before the cache was counted: input + output alone would be a small fraction). */
export function tokenTotal(res: SwarmOutResult): number | null {
  const t = res.tokens;
  if (!t || t.cache_read == null || t.cache_write == null) return null;
  return t.input + t.output + t.cache_read + t.cache_write;
}

/** The card's facts, three cells: how long it ran (and when it ended), the human loops
 *  (approvals and answered questions), and what it used — tokens and cost as one
 *  number, "4.8M token · $3.70", the share read from the cache under it and the
 *  breakdown on hover. A value not known is "—" with the reason on hover, never a zero. */
export function runStats(res: SwarmOutResult): RunStat[] {
  const h = res.work.human;
  const loops = h.approved + h.denied + h.unanswered + h.answered_questions;
  const tok = res.tokens;
  const total = tokenTotal(res);
  const dash = '—';
  const cost = res.cost_usd == null ? dash : `${res.cost_partial ? '≥ ' : ''}$${res.cost_usd.toFixed(2)}`;
  const cacheShare = total && tok?.cache_read != null ? Math.round((tok.cache_read / total) * 100) : null;
  const spendSub = [cacheShare != null && i18n.t('swarmOut.stats.cacheShare', { pct: cacheShare }),
    res.work.steps.failed > 0 && i18n.t('swarmOut.stats.failedSteps', { count: res.work.steps.failed })].filter(Boolean).join(' · ');
  const spendTitle = [
    total != null ? i18n.t('swarmOut.stats.tokensTitle', { cacheRead: tokenCount(tok!.cache_read!), cacheWrite: tokenCount(tok!.cache_write!),
      output: tokenCount(tok!.output), input: tokenCount(tok!.input) })
      : tok ? i18n.t('swarmOut.stats.tokensLegacy') : i18n.t('swarmOut.stats.tokensNotRecorded'),
    res.cost_usd == null ? i18n.t('swarmOut.stats.costNotRecorded') : res.cost_partial ? i18n.t('swarmOut.stats.costPartial') : '',
  ].filter(Boolean).join('\n');
  const ended = when(res.ended_at);
  return [
    { key: 'time', label: i18n.t('swarmOut.stats.time'), value: res.active_s == null ? dash : minutes(res.active_s),
      sub: ended ? i18n.t('swarmOut.stats.endedAt', { when: ended }) : undefined },
    { key: 'human', label: i18n.t('swarmOut.stats.human'), value: String(loops),
      sub: h.unanswered ? i18n.t('swarmOut.stats.unanswered', { count: h.unanswered }) : undefined,
      title: i18n.t('swarmOut.stats.humanTitle', { approved: h.approved, denied: h.denied, answered: h.answered_questions, unanswered: h.unanswered }),
      tone: h.unanswered ? 'attention' : undefined },
    { key: 'spend', label: i18n.t('swarmOut.stats.spend'), wide: true,
      value: i18n.t('swarmOut.stats.spendValue', { tokens: total == null ? dash : tokenCount(total), cost }),
      sub: spendSub || undefined, title: spendTitle },
  ];
}

/** The Brain and the Project Agent a run went under, for the card's two labelled
 *  fields (the dispatch record's, else the result's). No Project Agent: both say so. */
export function agentFacts(r: SwarmOutRun): { brain: string; agent: string; title?: string; none: boolean } {
  const pa = r.project_agent;
  const brain = pa?.brain ?? r.result?.project_agent?.brain ?? null;
  if (!brain) return { brain: i18n.t('swarmOut.card.noBrain'), agent: i18n.t('swarmOut.card.noAgent'), none: true };
  if (brain === 'General') return { brain: i18n.t('swarmOut.card.generalBrain'), agent: i18n.t('swarmOut.card.runnerAgent'), none: false };
  const mode = pa?.mode ?? r.result?.project_agent?.mode ?? null;
  const profile = pa?.profile ?? r.result?.project_agent?.profile ?? null;
  const parts = [pa?.revision && i18n.t('swarmOut.card.revision', { rev: pa.revision }),
    pa?.delivery && i18n.t('swarmOut.card.delivery', { delivery: pa.delivery }),
    pa?.acceptance && i18n.t('swarmOut.card.acceptance', { text: pa.acceptance })].filter(Boolean);
  return { brain, agent: [mode, profile].filter(Boolean).join(' · ') || i18n.t('swarmOut.card.agentDefault'), title: parts.join('\n') || undefined, none: false };
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
  const key = ({ session_gone: 'sessionGone', hive_silent: 'hiveSilent', hive_not_running: 'hiveNotRunning',
    hive_unusable: 'hiveUnusable', unexpected_body: 'unexpectedBody', 'no session': 'noSession',
    record_check_failed: 'recordCheck' } as Record<string, string>)[e];
  if (key) return i18n.t(`swarmOut.resultError.${key}`);
  return e.startsWith('http_') ? i18n.t('swarmOut.resultError.http', { code: e.slice(5) }) : e;
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
