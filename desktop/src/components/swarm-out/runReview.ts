/**
 * runReview — what the Swarm Out run list and the run's big card show, as pure
 * functions over /api/swarm-out/runs (design docs/design/2026-10-31-swarm-out-run-review.md).
 *
 * The backend decides each run's stage and the commands it offers (core/swarm_out_settle
 * derive: ``stage``, ``actions``); nothing here decides what a person may do. This file
 * only arranges what came back:
 *  - ``sections``: Waiting for you (needs your answer > awaiting acceptance > stopped,
 *    the longest waiting first), Running, Recently done (settled in the last 24 h);
 *  - ``brainChips``: the Brains the rows went under, with counts, never a fixed list;
 *  - ``autonomyWord``: the one word a row and the card's header carry (Judge verdict for
 *    a pipeline run, else how often a person stepped in);
 *  - ``COMMANDS``: each command's name, consequence and shortcut keys (i18n).
 *
 * A dispatch from before Project Agents has no stage: it is shown from its Hive state
 * and offers only "open session".
 */
import i18n from '../../i18n';
import type { SwarmOutAction, SwarmOutAsk, SwarmOutCommand, SwarmOutResult, SwarmOutRun, SwarmOutStage } from '../../services/swarmOut';
import { judgeState, type JudgeState } from './judge';
import { brainLabel, checkStatus, runJudge } from './live';

const DAY_MS = 24 * 3600 * 1000;

const ms = (iso: string | null | undefined): number => {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isNaN(t) ? NaN : t;
};

/** The run's stage: the backend's, else (an older dispatch) from its Hive state. */
export function stageOf(r: SwarmOutRun): SwarmOutStage {
  if (r.stage) return r.stage;
  const waiting = r.state ? r.state === 'waiting' : r.waiting;
  if (waiting) return 'needs_answer';
  if (r.state === 'running' || (r.state === undefined && r.running === true)) return 'running';
  return 'stopped';
}

/** The run's commands in their order: the backend's; an older dispatch only opens. */
export function actionsOf(r: SwarmOutRun): SwarmOutAction[] {
  if (r.actions) return r.actions;
  return r.session_id && !r.session_unknown ? [{ key: 'open_session', primary: false }] : [];
}

export const actionOf = (r: SwarmOutRun, key: SwarmOutCommand): SwarmOutAction | undefined =>
  actionsOf(r).find((a) => a.key === key);

/** The settled stages: nothing is asked of anyone any more. */
export const DONE_STAGES: readonly SwarmOutStage[] = ['accepted', 'discarded', 'expired'];
const YOU_ORDER: readonly SwarmOutStage[] = ['needs_answer', 'awaiting', 'stopped'];

/** The open questions of the run's session (the Hive wrote them; the card answers them). */
export function asksOf(r: SwarmOutRun, asks: SwarmOutAsk[]): SwarmOutAsk[] {
  if (!r.session_id) return [];
  return asks.filter((a) => a.hive_id === r.hive_id && a.session_id === r.session_id && !a.refused);
}

/** Every wait of the run its big card shows (design 2026-10-31-swarm-out-single-loop-
 *  fixes §2.2): the ones waiting first, then the refused ones, then the ones answered
 *  from here (sent, taken, gone: one line each). */
export function waitsOf(r: SwarmOutRun, asks: SwarmOutAsk[], answered: SwarmOutAsk[] = []): SwarmOutAsk[] {
  if (!r.session_id) return [];
  const own = (a: SwarmOutAsk) => a.hive_id === r.hive_id && a.session_id === r.session_id;
  return [...asks.filter((a) => own(a) && !a.refused), ...asks.filter((a) => own(a) && a.refused),
    ...answered.filter(own)];
}

/** When the run started waiting in its stage (the list's "waited" column): the first
 *  question asked, the result's end, the settle; a running run counts from when it was
 *  sent. ISO, or null. */
export function waitingSince(r: SwarmOutRun, asks: SwarmOutAsk[] = []): string | null {
  const stage = stageOf(r);
  if (stage === 'needs_answer') {
    const first = asksOf(r, asks).map((a) => a.asked_at).sort()[0];
    if (first) return first;
  }
  if (DONE_STAGES.includes(stage)) return r.settle?.settled?.at ?? r.result?.ended_at ?? r.sent_at;
  if (stage === 'running') return r.sent_at;
  return r.result?.ended_at ?? r.sent_at;
}

/** "4m", "2h", "1d": how long ago, at most one unit. */
export function ago(iso: string | null | undefined, now: number): string {
  const t = ms(iso);
  if (Number.isNaN(t)) return '';
  const s = Math.max(0, (now - t) / 1000);
  if (s < 60) return i18n.t('swarmOut.review.ago.now');
  if (s < 3600) return i18n.t('swarmOut.review.ago.m', { n: Math.floor(s / 60) });
  if (s < 86400) return i18n.t('swarmOut.review.ago.h', { n: Math.floor(s / 3600) });
  return i18n.t('swarmOut.review.ago.d', { n: Math.floor(s / 86400) });
}

export interface RunSections {
  /** what waits on the person, most urgent first */
  you: SwarmOutRun[];
  running: SwarmOutRun[];
  /** settled in the last 24 h (older ones are in the history view) */
  done: SwarmOutRun[];
}

/** The list's three sections. Waiting for you: needs your answer, then awaiting
 *  acceptance, then stopped; within a stage the longest waiting first. A stopped run
 *  that offers nothing but "open session" (an older dispatch) is not waiting on anyone:
 *  it shows under done while it is recent (one whose session was never named, always). */
export function sections(runs: SwarmOutRun[], asks: SwarmOutAsk[], now: number): RunSections {
  const you: SwarmOutRun[] = [];
  const running: SwarmOutRun[] = [];
  const done: SwarmOutRun[] = [];
  for (const r of runs) {
    const stage = stageOf(r);
    const asksSomething = actionsOf(r).some((a) => a.key !== 'open_session');
    if (stage === 'running') running.push(r);
    // a run whose records go within days waits for you too, whatever its stage
    else if ((YOU_ORDER.includes(stage) && (asksSomething || stage === 'needs_answer'))
      || (r.cleanup_soon && !r.settle?.settled)) you.push(r);
    // an older dispatch whose session was never named may still run: kept in view
    else if (now - ms(waitingSince(r, asks)) < DAY_MS || (!r.stage && r.session_unknown)) done.push(r);
  }
  const since = (r: SwarmOutRun) => ms(waitingSince(r, asks));
  const rank = (r: SwarmOutRun) => { const i = YOU_ORDER.indexOf(stageOf(r)); return i < 0 ? YOU_ORDER.length : i; };
  you.sort((a, b) => (rank(a) - rank(b)) || (since(a) - since(b)));
  running.sort((a, b) => since(b) - since(a));
  done.sort((a, b) => since(b) - since(a));
  return { you, running, done };
}

/** The Brain filter chips: every Brain the rows went under, with its count, the
 *  biggest first. Nothing is fixed: a Brain added or deleted shows up or goes. */
export function brainChips(runs: SwarmOutRun[]): { brain: string; count: number }[] {
  const by = new Map<string, number>();
  for (const r of runs) by.set(brainLabel(r), (by.get(brainLabel(r)) ?? 0) + 1);
  return [...by.entries()].map(([brain, count]) => ({ brain, count }))
    .sort((a, b) => (b.count - a.count) || a.brain.localeCompare(b.brain));
}

/** How often a person stepped into the run: approvals, denials, answered questions and
 *  everything they said in the session after the task. */
export function interventions(res: SwarmOutResult): number {
  const h = res.work.human;
  return h.approved + h.denied + h.answered_questions + (h.said ?? 0);
}

export interface AutonomyWord {
  kind: 'judge' | 'full' | 'interventions';
  text: string;
  /** a pipeline run's Judge state */
  state?: JudgeState;
}

/** The one word on a row and in the card's header: a pipeline run's Autonomy Judge
 *  state, else "Fully autonomous" (nobody stepped in) or "Stepped in N×". None
 *  before a result is read. */
export function autonomyWord(res: SwarmOutResult | null | undefined): AutonomyWord | null {
  if (!res) return null;
  const judge = runJudge(res);
  if (judge) {
    const { state } = judgeState(judge.verdicts);
    return { kind: 'judge', state, text: i18n.t('swarmOut.review.autonomy.judge', { state }) };
  }
  const n = interventions(res);
  return n === 0 ? { kind: 'full', text: i18n.t('swarmOut.review.autonomy.full') }
    : { kind: 'interventions', text: i18n.t('swarmOut.review.autonomy.steppedIn', { count: n }) };
}

/** What the run asks of the person, as the row's one tag: answer the question, accept
 *  (what came back), or that it stopped. Null while running or done. */
export function taskTag(r: SwarmOutRun): { key: 'answer' | 'awaiting' | 'stopped'; text: string } | null {
  const stage = stageOf(r);
  if (stage === 'needs_answer') return { key: 'answer', text: i18n.t('swarmOut.review.tag.answer') };
  if (stage === 'stopped') return { key: 'stopped', text: i18n.t('swarmOut.review.stage.stopped') };
  if (stage !== 'awaiting') return null;
  return { key: 'awaiting', text: i18n.t('swarmOut.review.tag.awaiting', { what: deliveredWhat(r) }) };
}

/** What came back, in a word: the commits (with their count), a document, an answer. */
export function deliveredWhat(r: SwarmOutRun): string {
  const commits = (r.result?.work.repos ?? []).reduce((n, x) => n + (x.commits_total || 0), 0);
  const returns = r.project_agent?.returns;
  if (returns === 'commits' || (!returns && commits > 0)) return i18n.t('swarmOut.review.what.commits', { count: commits });
  if (returns === 'document' || (r.result?.work.files ?? []).length > 0) return i18n.t('swarmOut.review.what.document');
  return i18n.t('swarmOut.review.what.answer');
}

export interface CommandMeta {
  /** i18n key of the name, and of the one-line consequence */
  name: string;
  does: string;
  /** shortcut: a key name (KeyboardEvent.key, lower case), with shift */
  key: string | null;
  shift?: boolean;
  /** the hint shown next to the name */
  hint: string | null;
}

/** Every command's name, consequence and shortcut. An accept variant has its own name
 *  and consequence (landing commits / landed by hand / passing an answer). */
export function commandMeta(a: SwarmOutAction): CommandMeta {
  const base = 'swarmOut.review.cmd';
  switch (a.key) {
    case 'accept': {
      const v = a.variant ?? 'verdict';
      return { name: `${base}.accept.${v}.name`, does: `${base}.accept.${v}.does`, key: 'a', hint: 'A' };
    }
    case 'rerun': return { name: `${base}.rerun.name`, does: `${base}.rerun.does`, key: 'r', hint: 'R' };
    case 'stop': return { name: `${base}.stop.name`, does: `${base}.stop.does`, key: 's', hint: 'S' };
    case 'open_session': return a.variant === 'read_only'
      ? { name: `${base}.openReadOnly.name`, does: `${base}.openReadOnly.does`, key: 'o', hint: 'O' }
      : { name: `${base}.open.name`, does: `${base}.open.does`, key: 'o', hint: 'O' };
    case 'discard': return { name: `${base}.discard.name`, does: `${base}.discard.does`, key: 'd', shift: true, hint: '⇧D' };
    // not a button: its key takes you to the wait card (or, with none here, to where to
    // answer). Not A: accept's key, a stage away (pressing it as the run moves on must
    // never accept)
    case 'answer': return { name: `${base}.answer.name`, does: `${base}.answer.does`, key: 'w', hint: 'W' };
    default: return { name: `${base}.open.name`, does: `${base}.open.does`, key: null, hint: null };
  }
}

/** The command a key press gives on this run, or undefined (never while typing). */
export function commandForKey(actions: SwarmOutAction[], e: { key: string; shiftKey: boolean; metaKey?: boolean; ctrlKey?: boolean; altKey?: boolean }): SwarmOutAction | undefined {
  if (e.metaKey || e.ctrlKey || e.altKey) return undefined;
  const k = e.key.toLowerCase();
  return actions.find((a) => {
    const m = commandMeta(a);
    return m.key === k && !!m.shift === e.shiftKey;
  });
}

/** True when a key event comes from a field the person types in (no single-key
 *  shortcut fires there). */
export function typingIn(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el || typeof el.tagName !== 'string') return false;
  const tag = el.tagName.toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || el.isContentEditable === true;
}

/** The machine's checks on what came back, for the card's amber strip: each one ✓ or
 *  △, never a gate (no command waits on them). *full*: the run's whole record; only it
 *  says which files are here (/runs counts files, without paths), so the files check
 *  waits for it. */
export function machineChecks(r: SwarmOutRun, full?: SwarmOutResult | null): { key: string; ok: boolean; text: string }[] {
  const res = r.result;
  if (!res) return [];
  const out: { key: string; ok: boolean; text: string }[] = [];
  const t = (k: string, o?: Record<string, unknown>) => i18n.t(`swarmOut.review.check.${k}`, o);
  out.push({ key: 'ended', ok: res.ended === 'completed', text: res.ended === 'completed' ? t('ended') : t('notEnded', { how: res.ended }) });
  const checks = res.checks ?? [];
  if (checks.length) {
    // by what the Host can say of each: not seen, errored, ran with no result
    const of = (st: string) => checks.filter((c) => checkStatus(c.status) === st).map((c) => c.id).join(', ');
    const missed = (['not_seen', 'errored', 'unknown'] as const)
      .map((st) => [st, of(st)] as const).filter(([, list]) => list)
      .map(([st, list]) => t(`checks_${st}`, { list }));
    out.push({ key: 'checks', ok: missed.length === 0, text: missed.length === 0 ? t('checksRan') : missed.join(' · ') });
  }
  // a file "came back" only when it is here or was sent back (local / not_placed); the
  // rest were written on the Hive and stayed there (an answer that also wrote files)
  const files = full?.work.files ?? [];
  const back = files.filter((f) => f.local || f.not_placed).length;
  if (back) out.push({ key: 'files', ok: !files.some((f) => f.not_placed), text: t('files', { count: back }) });
  else if (files.length) out.push({ key: 'files', ok: true, text: t('filesOnHive', { count: files.length }) });
  const repos = res.work.repos ?? [];
  if (repos.some((x) => x.commits_total > 0)) {
    const sealed = repos.filter((x) => x.commits_total > 0).every((x) => x.bundle?.available !== false && !x.bundle?.error);
    out.push({ key: 'bundle', ok: sealed, text: sealed ? t('bundleSealed') : t('bundleUnsealed') });
  }
  if (res.execution_check) out.push({ key: 'execution', ok: res.execution_check.state === 'matches', text: res.execution_check.state === 'matches' ? t('executionMatches') : t('executionDiffers') });
  out.push({ key: 'unanswered', ok: res.work.human.unanswered === 0, text: res.work.human.unanswered === 0 ? t('noneUnanswered') : t('unanswered', { count: res.work.human.unanswered }) });
  return out;
}
