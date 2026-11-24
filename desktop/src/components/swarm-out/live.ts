/**
 * The Swarm Out panel's live data (design docs/design/2026-10-28-swarm-out-live-dashboard.md).
 *
 * Two answers while the panel is shown: /api/swarm-out/runs (dispatched runs with their
 * state from the Hive, and the questions waiting) and /api/swarm-out/fleet (every
 * Hive with the load a running one reports). Both come from the one shared poll every
 * reader of the runs uses (./runsPoll): opening the panel adds no request of its own.
 * Each keeps its last answer on a failed poll (an unreachable daemon is not "nothing"),
 * and says whether the last poll failed.
 *
 * The labels and facts below are pure functions of those answers; the history's
 * numbers are ./historyStats.
 *
 * Every label here comes from the locale files (swarmOut.* in i18n/locales), read at
 * call time through the app's i18n instance, so a panel rendered after a language
 * switch speaks the new language. Text a Hive wrote is data and is never translated.
 */
import i18n from '../../i18n';
import { type SwarmOutCheckStatus, type SwarmOutEnded, type SwarmOutFleet, type SwarmOutRepoFact, type SwarmOutResult, type SwarmOutRun, type SwarmOutRuns } from '../../services/swarmOut';
import { DIMENSIONS, type RunJudge, type Verdict } from './shapes';
import { endedTone, type Tone } from './tone';
import { POLL_MS, reloadFleet, reloadRuns, useSharedFleet, useSharedRuns } from './runsPoll';

export const LIVE_POLL_MS = POLL_MS;

interface Polled<T> {
  data: T | null;
  failed: boolean;
  reload: () => void;
}

/** The shared runs answer; ``hidden``: read it without keeping the poll going. */
export function useRuns(hidden: boolean): Polled<SwarmOutRuns> {
  const s = useSharedRuns(!hidden);
  return { data: s.data, failed: s.failed, reload: reloadRuns };
}

/** The shared fleet answer; ``hidden`` as for useRuns. */
export function useFleet(hidden: boolean): Polled<SwarmOutFleet> {
  const s = useSharedFleet(!hidden);
  return { data: s.data, failed: s.failed, reload: reloadFleet };
}

/** The Brain a run went under, as the cards name it. */
export function brainLabel(r: SwarmOutRun): string {
  const b = r.project_agent?.brain;
  if (!b) return i18n.t('swarmOut.brain.none');
  return b === 'General' ? i18n.t('swarmOut.brain.general') : b;
}

const CHECK_KEYS: Record<SwarmOutCheckStatus, string> = { ran: 'ran', errored: 'errored', unknown: 'unknown', not_seen: 'notSeen' };

/** A check status as it is shown: a value from before `not_seen` (`not_run`) or any value
 *  outside the set reads as not seen. Every reader of a check's status goes through it. */
export function checkStatus(s: string | null | undefined): SwarmOutCheckStatus {
  return s === 'ran' || s === 'errored' || s === 'unknown' || s === 'not_seen' ? s : 'not_seen';
}
const checkWord = (s: string): string => i18n.t(`swarmOut.check.${CHECK_KEYS[checkStatus(s)]}`);

/** True when a run named checks and one of them did not run cleanly (the amber line). */
export const checksIncomplete = (res: SwarmOutResult): boolean => (res.checks ?? []).some((c) => checkStatus(c.status) !== 'ran');

/** "校验：tests 跑了 · lint 没看到" for a run whose Project Agent named checks, else null. */
export function checksLine(res: SwarmOutResult): string | null {
  const c = res.checks;
  if (!c || c.length === 0) return null;
  return i18n.t(res.checks_partial ? 'swarmOut.check.linePartial' : 'swarmOut.check.line',
    { list: c.map((x) => `${x.id} ${checkWord(x.status)}`).join(' · ') });
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
 *  that does not count them, says so; a count a turn lost when its Hive stopped mid-turn
 *  (gate_refusals_partial) says so too; null otherwise. */
export function gateLine(res: SwarmOutResult): string | null {
  if (res.project_agent && res.gate_refusals === null) return i18n.t('swarmOut.gate.notCounted');
  const g = Object.entries(res.gate_refusals ?? {}).filter(([, n]) => n > 0);
  const lost = res.project_agent && res.gate_refusals_partial === true;
  if (g.length === 0) return lost ? i18n.t('swarmOut.gate.lost') : null;
  const total = g.reduce((a, [, n]) => a + n, 0);
  return i18n.t('swarmOut.gate.refused', { count: total, list: g.map(([k, n]) => `${gateName(k)} ${n}`).join(i18n.t('swarmOut.sep.list')) })
    + (lost ? i18n.t('swarmOut.gate.partial') : '');
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
  switch (r.state) {
    case 'waiting': return i18n.t('swarmOut.state.waiting');
    case 'running': return r.state_reason === 'background'
      ? i18n.t('swarmOut.review.stateReason.background')
      : r.state_reason && r.state_reason !== 'streaming'
        ? i18n.t('swarmOut.state.runningWhy', { reason: r.state_reason }) : i18n.t('swarmOut.state.running');
    case 'stopped': return r.state_reason === 'hive_stopped' ? i18n.t('swarmOut.state.stoppedHiveOff')
      : r.state_reason === 'hive_gone' ? i18n.t('swarmOut.state.stoppedHiveGone') : i18n.t('swarmOut.state.stopped');
    // an updating Hive is not a silent one: the person waits, the run continues after it
    default: return r.session_unknown ? i18n.t('swarmOut.state.sessionUnknown')
      : r.state_reason === 'hive_updating' ? i18n.t('swarmOut.state.unknownUpdating') : i18n.t('swarmOut.state.unknown');
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
  'stopped', 'restarted', 'superseded', 'gave_up', 'no_record', 'daemon_stopped'];

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

/** How often a person stepped into the run: the questions they answered and what they
 *  said in the session after the task. Approving or refusing a command is oversight the
 *  product keeps for the person, and a question nobody answered is the run waiting, so
 *  neither counts (design 2026-11-01-brain-eval-and-runs §3; the local twin is
 *  backend core/run_interventions). */
export function interventionsOf(res: SwarmOutResult): number | null {
  const h = res.work.human;
  // a record kept before what the person said was counted: not known, never a smaller number
  return h.said == null ? null : h.answered_questions + h.said;
}

/** The card's facts: how long it ran (and when it ended), how often a person stepped in
 *  (interventionsOf), the total cost (the CLI's own, per-turn increases) and, when
 *  any failed, the failed steps. The tokens are a row of their own (tokenStats). A value
 *  not known is "—" with the reason on hover, never a zero. */
export function runStats(res: SwarmOutResult): RunStat[] {
  const h = res.work.human;
  const loops = interventionsOf(res);
  const dash = '—';
  const ended = when(res.ended_at);
  const out: RunStat[] = [
    { key: 'time', label: i18n.t('swarmOut.stats.time'), value: res.active_s == null ? dash : minutes(res.active_s),
      sub: ended ? i18n.t('swarmOut.stats.endedAt', { when: ended }) : undefined },
    { key: 'human', label: i18n.t('swarmOut.stats.human'), value: loops == null ? dash : String(loops),
      sub: h.unanswered ? i18n.t('swarmOut.stats.unanswered', { count: h.unanswered }) : undefined,
      title: i18n.t('swarmOut.stats.humanTitle', { approved: h.approved, denied: h.denied, answered: h.answered_questions, said: h.said ?? 0 }),
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

/** The runs sent in the last `days` local calendar days (today included, the same days
 *  as the history's bars), or every run when `days` is null. */
export function inWindow(runs: SwarmOutRun[], now: number, days: number | null): SwarmOutRun[] {
  if (days == null) return runs;
  const first = new Date(now);
  first.setHours(0, 0, 0, 0);
  first.setDate(first.getDate() - (days - 1));
  return runs.filter((r) => { const t = Date.parse(r.sent_at); return !Number.isNaN(t) && t >= first.getTime() && t <= now; });
}
