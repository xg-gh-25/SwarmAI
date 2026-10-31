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
import { swarmOutService, type SwarmOutCheckStatus, type SwarmOutEnded, type SwarmOutFleet, type SwarmOutRepoFact, type SwarmOutResult, type SwarmOutRun, type SwarmOutRuns } from '../../services/swarmOut';
import { DIMENSIONS, type RunJudge, type Verdict } from './shapes';
import { judgeState } from './judge';
import { activityOf } from './useSwarmOutActivity';
import { endedTone, type Tone } from './tone';

export const LIVE_POLL_MS = 30_000;

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

/** How many of the running Hives that answered would start a task now, of how many.
 *  A Hive has no fixed number of slots: it queues a new task only while its memory is
 *  under critical pressure (saturated). null when no running Hive answered (unknown,
 *  not zero). */
export function capacity(fleet: SwarmOutFleet | null): { free: number; total: number } | null {
  const up = (fleet?.hives ?? []).filter((h) => h.status === 'running' && h.reachable);
  if (up.length === 0) return null;
  return { free: up.filter((h) => !h.saturated).length, total: up.length };
}

const sentAt = (r: SwarmOutRun): number => Date.parse(r.sent_at);
/** A run's state, from `state` or, from an older daemon without it, the booleans. */
const stateOf = (r: SwarmOutRun): NonNullable<SwarmOutRun['state']> =>
  r.state ?? (r.waiting ? 'waiting' : r.running === true ? 'running' : r.running === false ? 'stopped' : 'unknown');

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

/** "执行方式和派发时定的不同：建了 pipeline run" when the run did not run the way the
 *  Host pinned it (backend execution_check), else null. */
export function executionLine(res: SwarmOutResult): string | null {
  const c = res.execution_check;
  if (!c || c.state !== 'differs' || !c.why) return null;
  return i18n.t('swarmOut.execution.differs', { why: i18n.t(`swarmOut.execution.${c.why}`) });
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
  const dq = /^could not read the decisions of Hive (.+)$/.exec(e);
  if (dq) return i18n.t('swarmOut.error.decisions', { hive: dq[1] });
  if (e === 'could not list the running Hives') return i18n.t('swarmOut.error.hiveList');
  if (e === 'could not read a Hive record') return i18n.t('swarmOut.error.hiveRecord');
  if (e === "could not read the runs' acceptance") return i18n.t('swarmOut.error.acceptance');
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
  "the person's commit is not in the repository": 'noPersonCommit',
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
  key: 'time' | 'human' | 'cost' | 'failed' | 'rework' | 'tokens' | 'input' | 'output' | 'cacheRead' | 'cacheWrite' | 'cacheHit';
  label: string;
  value: string;
  /** a second, smaller line under the value (e.g. how many questions went unanswered) */
  sub?: string;
  /** the breakdown or the reason, on hover */
  title?: string;
  tone?: Tone;
  /** the number the row is read by (the total cost, the billed tokens): a size up */
  strong?: boolean;
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

/** The share of the input-side tokens read from the prompt cache: cache reads over
 *  input + cache reads + cache writes (output is not input). null when not known. */
export function cacheHitRate(t: { input: number; cache_read: number | null; cache_write: number | null } | null): number | null {
  if (!t || t.cache_read == null || t.cache_write == null) return null;
  const side = t.input + t.cache_read + t.cache_write;
  return side ? t.cache_read / side : null;
}

/** A model as a person reads it: "claude-opus-5-5" → "Opus 5.5" (registry short names;
 *  anything else as it is). */
export function modelLabel(name: string): string {
  const m = /^claude-(opus|sonnet|haiku|fable)-(\d+)(?:-(\d+))?$/.exec(name);
  if (!m) return name === 'other' ? i18n.t('swarmOut.stats.modelOther') : name;
  return `${m[1][0].toUpperCase()}${m[1].slice(1)} ${m[2]}${m[3] ? `.${m[3]}` : ''}`;
}

/** The card's facts: how long it ran (and when it ended), the human loops (approvals
 *  and answered questions), the total cost (the CLI's own, per-turn increases) and, when
 *  any failed, the failed steps. The tokens are a row of their own (tokenStats). A value
 *  not known is "—" with the reason on hover, never a zero. */
export function runStats(res: SwarmOutResult): RunStat[] {
  const h = res.work.human;
  const loops = h.approved + h.denied + h.unanswered + h.answered_questions;
  const dash = '—';
  const ended = when(res.ended_at);
  const out: RunStat[] = [
    { key: 'time', label: i18n.t('swarmOut.stats.time'), value: res.active_s == null ? dash : minutes(res.active_s),
      sub: ended ? i18n.t('swarmOut.stats.endedAt', { when: ended }) : undefined },
    { key: 'human', label: i18n.t('swarmOut.stats.human'), value: String(loops),
      sub: h.unanswered ? i18n.t('swarmOut.stats.unanswered', { count: h.unanswered }) : undefined,
      title: i18n.t('swarmOut.stats.humanTitle', { approved: h.approved, denied: h.denied, answered: h.answered_questions, unanswered: h.unanswered }),
      tone: h.unanswered ? 'attention' : undefined },
    { key: 'cost', label: i18n.t('swarmOut.stats.cost'), strong: true,
      value: res.cost_usd == null ? dash : `${res.cost_partial ? '≥ ' : ''}$${res.cost_usd.toFixed(2)}`,
      title: res.cost_usd == null ? i18n.t('swarmOut.stats.costNotRecorded') : res.cost_partial ? i18n.t('swarmOut.stats.costPartial') : undefined,
      sub: res.models?.length ? res.models.map(modelLabel).join(' · ') : undefined },
  ];
  if (res.work.steps.failed > 0) out.push({ key: 'failed', label: i18n.t('swarmOut.stats.failed'), value: String(res.work.steps.failed) });
  return out;
}

/** The billed tokens of a run, each kind its own cell: the total, input (not read from
 *  the cache), output, cache reads, cache writes, and the cache hit rate. A record kept
 *  before the cache was counted, or one with no tokens, is one "—" cell saying why. */
export function tokenStats(res: SwarmOutResult): RunStat[] {
  const t = res.tokens;
  const total = tokenTotal(res);
  if (total == null || !t) {
    return [{ key: 'tokens', label: i18n.t('swarmOut.stats.tokensBilled'), value: '—',
      title: t ? i18n.t('swarmOut.stats.tokensLegacy') : i18n.t('swarmOut.stats.tokensNotRecorded') }];
  }
  const hit = cacheHitRate(t);
  const cell = (key: RunStat['key'], value: number, label: string): RunStat => ({ key, label, value: tokenCount(value), title: value.toLocaleString() });
  // a lower bound until the ledger's rows arrive: "≥", and the reason on hover
  const atLeast = (c: RunStat): RunStat => (res.tokens_partial
    ? { ...c, value: `≥ ${c.value}`, title: `${c.title} · ${i18n.t('swarmOut.stats.tokensPartial')}` } : c);
  return [
    atLeast({ ...cell('tokens', total, i18n.t('swarmOut.stats.tokensBilled')), strong: true }),
    atLeast(cell('input', t.input, i18n.t('swarmOut.stats.input'))),
    atLeast(cell('output', t.output, i18n.t('swarmOut.stats.output'))),
    atLeast(cell('cacheRead', t.cache_read!, i18n.t('swarmOut.stats.cacheRead'))),
    atLeast(cell('cacheWrite', t.cache_write!, i18n.t('swarmOut.stats.cacheWrite'))),
    { key: 'cacheHit', label: i18n.t('swarmOut.stats.cacheHit'), value: hit == null ? '—' : `${Math.round(hit * 100)}%`,
      title: i18n.t('swarmOut.stats.cacheHitTitle') },
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

// ── the history view (docs/design/2026-10-29-swarm-out-history.md) ─────────────

/** The runs sent in the last `days` local calendar days (today included, the same days
 *  as the daily bars), or every run when `days` is null. */
export function inWindow(runs: SwarmOutRun[], now: number, days: number | null): SwarmOutRun[] {
  if (days == null) return runs;
  const first = new Date(now);
  first.setHours(0, 0, 0, 0);
  first.setDate(first.getDate() - (days - 1));
  return runs.filter((r) => { const t = sentAt(r); return !Number.isNaN(t) && t >= first.getTime() && t <= now; });
}

/** How a run is counted in the history: running (not in any rate), or how it ended. */
export type HistoryOutcome = OutcomeBucket | 'running';
export const historyOutcome = (r: SwarmOutRun): HistoryOutcome => (stateOf(r) === 'running' ? 'running' : outcomeBucket(r));

/** The human loops of a run (approvals, denials and questions), as the card counts them. */
const loopsOf = (res: SwarmOutResult): number => {
  const h = res.work.human;
  return h.approved + h.denied + h.unanswered + h.answered_questions;
};

/** A run's one type, by precedence: a pipeline run (with its profile), then code, files,
 *  an answer only, nothing produced; a run with no result yet is not known. */
export function runType(r: SwarmOutRun): { key: string; label: string } {
  const res = r.result;
  if (!res) return { key: 'unknown', label: i18n.t('swarmOut.type.unknown') };
  if (res.pipeline || res.kinds.includes('pipeline')) {
    const profile = res.pipeline?.profile ?? null;
    return { key: `pipeline:${profile ?? ''}`, label: profile ? i18n.t('swarmOut.type.pipelineProfile', { profile }) : i18n.t('swarmOut.type.pipeline') };
  }
  for (const k of ['code', 'files', 'answer'] as const) if (res.kinds.includes(k)) return { key: k, label: i18n.t(`swarmOut.type.${k}`) };
  return { key: 'none', label: i18n.t('swarmOut.type.none') };
}

export interface RunTally {
  runs: number;
  by: Record<HistoryOutcome, number>;
  /** runs whose ending is known (done, failed, stopped): the denominator of every rate */
  known: number;
  /** done with no human loop at all */
  unattended: number;
  loops: number;
  unanswered: number;
  /** the median of the ended runs' active seconds, null when none is recorded */
  medianActive: number | null;
  cost: number;
  /** runs whose cost is not in `cost`, or only partly (then the sum is "at least") */
  costMissing: number;
  tokens: number;
  tokensMissing: number;
  /** runs whose tokens are in `tokens` only as a lower bound (`tokens_partial`) */
  tokensPartial: number;
  /** pipeline runs by their Autonomy Judge state */
  judge: { PASS: number; WARN: number; BLOCK: number };
  lessonsReturned: number;
  lessonsWritten: number;
  checksNotRun: number;
}

/** The numbers of a set of runs: what the history's summary line and each row of the
 *  breakdown table show. Nothing is guessed: a run without a result is not known, its
 *  cost and tokens are counted as missing, never as zero. */
export function tallyRuns(runs: SwarmOutRun[]): RunTally {
  const t: RunTally = { runs: runs.length, by: { completed: 0, error: 0, stopped: 0, other: 0, running: 0 }, known: 0,
    unattended: 0, loops: 0, unanswered: 0, medianActive: null, cost: 0, costMissing: 0, tokens: 0, tokensMissing: 0, tokensPartial: 0,
    judge: { PASS: 0, WARN: 0, BLOCK: 0 }, lessonsReturned: 0, lessonsWritten: 0, checksNotRun: 0 };
  const active: number[] = [];
  for (const r of runs) {
    const o = historyOutcome(r);
    t.by[o] += 1;
    if (o === 'completed' || o === 'error' || o === 'stopped') t.known += 1;
    const res = r.result;
    if (!res) { t.costMissing += 1; t.tokensMissing += 1; continue; }
    const loops = loopsOf(res);
    if (o === 'completed' && loops === 0) t.unattended += 1;
    t.loops += loops;
    t.unanswered += res.work.human.unanswered;
    if (o !== 'running' && res.active_s != null) active.push(res.active_s);
    if (res.cost_usd == null) t.costMissing += 1;
    else { t.cost += res.cost_usd; if (res.cost_partial) t.costMissing += 1; }
    const tok = tokenTotal(res);
    if (tok == null) t.tokensMissing += 1;
    else { t.tokens += tok; if (res.tokens_partial) t.tokensPartial += 1; }
    const j = runJudge(res);
    if (j) t.judge[judgeState(j.verdicts).state] += 1;
    t.lessonsReturned += res.lessons?.returned ?? 0;
    t.lessonsWritten += res.lessons?.written ?? 0;
    if (!res.provisional && o !== 'running') t.checksNotRun += (res.checks ?? []).filter((c) => c.status !== 'ran').length;
  }
  if (active.length) {
    active.sort((a, b) => a - b);
    const m = Math.floor(active.length / 2);
    t.medianActive = active.length % 2 ? active[m] : Math.round((active[m - 1] + active[m]) / 2);
  }
  return t;
}

export type BreakdownDim = 'brain' | 'type' | 'hive';

/** The key and label of a run's group along one dimension. A run with no Project Agent
 *  is its own Brain group, "no Project Agent". */
export function groupOf(r: SwarmOutRun, dim: BreakdownDim): { key: string; label: string } {
  if (dim === 'hive') return { key: r.hive, label: r.hive };
  if (dim === 'type') return runType(r);
  const f = agentFacts(r);
  const brain = r.project_agent?.brain ?? r.result?.project_agent?.brain ?? '';
  return brain ? { key: brain, label: f.brain } : { key: '', label: f.agent };
}

export interface BreakdownRow { key: string; label: string; agent?: string; tally: RunTally }

/** The runs split along one dimension, the biggest group first; the runs without a
 *  Project Agent (by Brain) and the ones not known yet (by type) last. */
export function breakdown(runs: SwarmOutRun[], dim: BreakdownDim): BreakdownRow[] {
  const by = new Map<string, { label: string; agent?: string; runs: SwarmOutRun[] }>();
  for (const r of runs) {
    const g = groupOf(r, dim);
    const cur = by.get(g.key) ?? { label: g.label, agent: dim === 'brain' && g.key ? agentFacts(r).agent : undefined, runs: [] };
    cur.runs.push(r);
    by.set(g.key, cur);
  }
  const last = (k: string) => (dim === 'brain' && k === '') || (dim === 'type' && k === 'unknown') ? 1 : 0;
  return [...by].map(([key, g]) => ({ key, label: g.label, agent: g.agent, tally: tallyRuns(g.runs) }))
    .sort((a, b) => last(a.key) - last(b.key) || b.tally.runs - a.tally.runs || a.label.localeCompare(b.label));
}

/** For each of the five audit dimensions, how many pipeline runs got each verdict. */
export function dimensionCounts(runs: SwarmOutRun[]): { dim: string; counts: Record<Verdict, number> }[] | null {
  const judges = runs.map((r) => (r.result ? runJudge(r.result) : null)).filter((j): j is RunJudge => !!j);
  if (judges.length === 0) return null;
  return DIMENSIONS.map((dim, i) => {
    const counts: Record<Verdict, number> = { PASS: 0, WARN: 0, BLOCK: 0, NA: 0, PENDING: 0 };
    for (const j of judges) counts[j.verdicts[i]] += 1;
    return { dim, counts };
  });
}

/** The history's records filter by how a run went: failed, stopped, needed a person,
 *  not known (running ones are under "not known" too: their ending is not known yet). */
export type RecordFilter = 'all' | 'error' | 'stopped' | 'human' | 'unknown';
export function matchesFilter(r: SwarmOutRun, f: RecordFilter): boolean {
  if (f === 'all') return true;
  const o = historyOutcome(r);
  if (f === 'error') return o === 'error';
  if (f === 'stopped') return o === 'stopped';
  if (f === 'unknown') return o === 'other' || o === 'running';
  return !!r.result && loopsOf(r.result) > 0;
}

/** A spend as the cards write it: "4.8M token · $3.70", "≥" on what is only partly
 *  known, "—" for what is not known at all. */
export function spendText(t: Pick<RunTally, 'runs' | 'cost' | 'costMissing' | 'tokens' | 'tokensMissing'> & { tokensPartial?: number }, short = false): string {
  const all = (missing: number) => missing >= t.runs;
  const tokens = all(t.tokensMissing) ? '—' : `${t.tokensMissing || t.tokensPartial ? '≥ ' : ''}${tokenCount(t.tokens)}`;
  const cost = all(t.costMissing) ? '—' : `${t.costMissing ? '≥ ' : ''}$${t.cost.toFixed(2)}`;
  return i18n.t(short ? 'swarmOut.stats.spendShort' : 'swarmOut.stats.spendValue', { tokens, cost });
}

/** One run's spend as a tally of one, for spendText. */
export function runSpend(res: SwarmOutResult | null | undefined): Pick<RunTally, 'runs' | 'cost' | 'costMissing' | 'tokens' | 'tokensMissing' | 'tokensPartial'> {
  const tok = res ? tokenTotal(res) : null;
  return { runs: 1, cost: res?.cost_usd ?? 0, costMissing: !res || res.cost_usd == null || res.cost_partial ? 1 : 0,
    tokens: tok ?? 0, tokensMissing: tok == null ? 1 : 0, tokensPartial: tok != null && res?.tokens_partial ? 1 : 0 };
}

/** A duration in the cards' words ("55 秒", "5 分钟"). */
export const durationText = (s: number | null): string => (s == null ? '—' : minutes(s));
