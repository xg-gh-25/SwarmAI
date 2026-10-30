/**
 * brainMapModel — pure builders for the Brain map (Brain Hub detail "Map" view and
 * the Hub map above the card wall). No React, no fetch: every number comes from a
 * record the backend already produced (GET /ddd/brains[/{name}], the brain's review
 * queue, GET /pipelines/analytics, GET /swarm-out/runs). A
 * value nobody measures is `null` (shown as UNKNOWN), never a guess.
 *
 * Exports:
 *   SECTION_ORDER        the six DDD sections, map order
 *   sectionCounts        per-section count line from a BrainDetail
 *   projectRuns          this brain's Runs from the 30-day analytics payload
 *   runTone              Run status -> map dot tone
 *   needsYouItems        one queue: everything waiting on you in one Brain (needsCount: its total)
 *   brainHiveRuns        the Hive runs of one brain (30 days); hiveLive: one that is going
 *   humanPerRun          human decisions per Run, local and Hive
 *   brainEval            the Eval numbers for one brain (null = not measured)
 *   hubSummary           Brain Hub home totals across all brains
 *   hubNeeds             Brain Hub home: everything waiting on you, all brains
 *   pendingSplit         health.pending split by source (Runs / escalations / jobs)
 *   brainSignal          the one signal a Hub card shows, and its sort rank
 */
import type { BrainDetail, BrainSummary, GeneralBrain, ReviewData, SectionKey } from '../../services/ddd';
import type { SwarmOutRun } from '../../services/swarmOut';
import type { PipelineAnalytics, PipelineRunSummary } from '../../services/pipelines';

export const SECTION_ORDER: { key: SectionKey; label: string }[] = [
  { key: 'identity', label: 'Identity' },
  { key: 'knowledge', label: 'Knowledge' },
  { key: 'gates', label: 'Gates' },
  { key: 'capabilities', label: 'Capabilities' },
  { key: 'refresher', label: 'Refresher' },
  { key: 'delivery', label: 'Delivery' },
];

/** The short count line under each hex. */
export function sectionCounts(detail: BrainDetail | null): Record<SectionKey, string> {
  const out = {} as Record<SectionKey, string>;
  for (const { key } of SECTION_ORDER) {
    const s = detail?.sections.find((x) => x.key === key);
    if (!s) { out[key] = '--'; continue; }
    if (key === 'knowledge') out[key] = `${s.entries.length} entries`;
    else if (s.members.length === 0) out[key] = s.completeNotBroken ? 'none (ok)' : 'empty';
    else out[key] = `${s.members.length} file${s.members.length === 1 ? '' : 's'}`;
  }
  return out;
}

/** This brain's Runs in the last 30 days, newest first. */
export function projectRuns(a: PipelineAnalytics | null | undefined, name: string): PipelineRunSummary[] {
  const g = a?.byProject.find((p) => p.project === name);
  return (g?.runs ?? []).slice().sort((x, y) => (y.updatedAt || '').localeCompare(x.updatedAt || ''));
}

export type RunTone = 'running' | 'needs' | 'landed' | 'failed' | 'stopped';

export function runTone(r: PipelineRunSummary): RunTone {
  if (r.needsYou) return 'needs';
  if (r.status === 'running') return 'running';
  if (r.status === 'completed') return r.notLanded ? 'failed' : 'landed';
  if (r.status === 'failed' || r.status === 'abandoned') return 'failed';
  return 'stopped';
}

export interface NeedItem {
  kind: 'run' | 'agent' | 'other';
  id: string;
  title: string;
  detail: string;
  /** how many of the Brain's waiting items this row stands for (an escalations row: N) */
  count: number;
}

/** Everything in this brain that waits for a person, in one list: Runs, and a Project Agent draft to confirm (or a file with problems), then one
 *  row per kind the attention channel holds that neither list names (escalations,
 *  failing jobs, newly read repositories, Runs not listed, anything else).
 *  The rows' counts add up to the Brain header's count (the attention channel's
 *  pending + a Project Agent waiting), so the header, the map's You and this list agree. */
export function needsYouItems(
  runs: PipelineRunSummary[],
  agent?: { status?: string; problems?: string[] } | null,
  bySource?: Record<string, number>,
): NeedItem[] {
  const items: NeedItem[] = [];
  for (const r of runs) {
    if (!r.needsYou) continue;
    const why = r.pauseKind === 'decision' ? 'a question is waiting'
      : r.pauseKind === 'stopped' ? 'stopped, needs a resume'
      : r.checkpointReason || 'waiting on you';
    items.push({ kind: 'run', id: r.id, title: r.requirement || r.id, detail: `${r.id} · ${why}`, count: 1 });
  }
  // what the attention channel holds that the lists above do not name one by one
  if (bySource) {
    const split = pendingSplit({ health: { pending: 0 } as BrainSummary['health'], pendingBySource: bySource });
    const listedRuns = items.filter((i) => i.kind === 'run').length;
    const rest: [string, number, string, string][] = [
      ['runs-unlisted', split.runs - listedRuns, plural(split.runs - listedRuns, 'more Run waiting', 'more Runs waiting'), 'open Need You to answer'],
      ['escalations', split.escalations, `${plural(split.escalations, 'escalation')} to decide`, 'open Need You to answer'],
      ['jobs', split.jobs, plural(split.jobs, 'failing job'), 'open Need You to triage'],
      ['repos', split.repos, `${plural(split.repos, 'repository', 'repositories')} newly read`, 'the Brain started reading it; open Need You to see it'],
      ['other', split.other, plural(split.other, 'other item'), 'open Need You'],
    ];
    for (const [id, n, title, detail] of rest) if (n > 0) items.push({ kind: 'other', id, title, detail, count: n });
  }
  if (agent && agent.problems && agent.problems.length) {
    items.push({ kind: 'agent', id: 'project-agent', title: 'The Project Agent file has problems to fix', detail: agent.problems.join(' · '), count: 1 });
  } else if (agent && agent.status !== 'reviewed') {
    items.push({ kind: 'agent', id: 'project-agent', title: 'Confirm the Project Agent draft', detail: 'generated from this Brain; review it, then confirm or revise', count: 1 });
  }
  return items;
}

/** How many waiting items a needs list stands for. */
export const needsCount = (items: NeedItem[]): number => items.reduce((s, i) => s + i.count, 0);

/** The Hive runs (Swarm Out) whose Project Agent is this Brain, sent in the last 30 days,
 *  newest first. The Brain is the dispatch record's, else the run's result's. */
export function brainHiveRuns(runs: SwarmOutRun[] | null | undefined, name: string, now = Date.now()): SwarmOutRun[] {
  const since = now - 30 * 24 * 3600 * 1000;
  return (runs ?? [])
    .filter((r) => (r.project_agent?.brain ?? r.result?.project_agent?.brain) === name)
    .filter((r) => { const t = Date.parse(r.sent_at); return !Number.isNaN(t) && t >= since; })
    .sort((x, y) => Date.parse(y.sent_at) - Date.parse(x.sent_at));
}

/** A Hive run that is going: running, or waiting on the person. */
export const hiveLive = (r: SwarmOutRun): boolean => r.state === 'running' || r.state === 'waiting';

/** Human decisions per Run, over every Run that recorded them: a local Run's
 *  human_decisions, a Hive run's approvals + denials + answered questions. */
export function humanPerRun(local: PipelineRunSummary[], hive: SwarmOutRun[]): { avg: number | null; runs: number } {
  const n: number[] = [];
  for (const r of local) if (r.humanDecisions != null) n.push(r.humanDecisions);
  for (const r of hive) {
    const h = r.result?.work?.human;
    if (h) n.push(h.approved + h.denied + h.answered_questions);
  }
  return { avg: n.length ? n.reduce((a, b) => a + b, 0) / n.length : null, runs: n.length };
}

export interface BrainEval {
  runs: { total: number; landed: number; completionRate: number | null; avgCycleMin: number | null; tokens: number | null; needsYou: number };
  brain: { entries: number; recentActivity: number | null; autoApplied: number };
}

export function brainEval(
  runs: PipelineRunSummary[], detail: BrainDetail | null, review: ReviewData | null,
): BrainEval {
  const landed = runs.filter((r) => r.status === 'completed' && !r.notLanded);
  const done = runs.filter((r) => r.status !== 'running');
  const cyc = runs.map((r) => r.cycleTimeMin).filter((x): x is number => typeof x === 'number');
  const tok = runs.map((r) => r.tokensActual);
  const knowledge = detail?.sections.find((s) => s.key === 'knowledge');
  return {
    runs: {
      total: runs.length,
      landed: landed.length,
      completionRate: done.length ? landed.length / done.length : null,
      avgCycleMin: cyc.length ? Math.round(cyc.reduce((a, b) => a + b, 0) / cyc.length) : null,
      // a total is only honest when every Run was measured
      tokens: tok.length && tok.every((t) => typeof t === 'number') ? (tok as number[]).reduce((a, b) => a + b, 0) : null,
      needsYou: runs.filter((r) => r.needsYou).length,
    },
    brain: {
      entries: knowledge?.entries.length ?? 0,
      recentActivity: detail?.health?.recentActivity ?? null,
      autoApplied: (review?.hunks ?? []).filter((h) => h.tag === 'cultivation·auto-applied').length,
    },
  };
}

export interface HubSummary {
  brains: number;
  /** items waiting on a person: the attention channel's count (Runs, escalations,
   *  failing jobs) + Project Agents to confirm or fix */
  needsYou: number;
  /** brains with a Run in progress */
  running: number;
  /** brains whose setup is recorded as setting-up */
  born: number;
  /** ddd-changelog entries stamped in the last 30 days, summed; null = no brain reported it */
  learned: number | null;
  learnedBrains: number;
  /** last 30 days: Runs that landed, and Runs that finished (landed or not) */
  landed: number;
  finished: number;
  /** Runs running now (all lanes are the chat tab today) */
  liveRuns: number;
}

/** How much an open item asks of the person, most first: a Run or an escalation is
 *  blocked on them; a failing job waits for a review; a Project Agent
 *  draft or a Brain being born is setup; a repository a Brain started reading is a
 *  notice. Only urgent and review carry a warm color on the Hub. */
export type Severity = 'urgent' | 'review' | 'setup' | 'notice';
export const SEVERITY_ORDER: Severity[] = ['urgent', 'review', 'setup', 'notice'];

export interface HubNeed {
  brain: string;
  /** run: one Run (from the analytics); runs: Runs waiting that the analytics did
   *  not list; escalations / jobs / repos: counted per brain; agent: a draft */
  kind: 'run' | 'runs' | 'escalations' | 'jobs' | 'agent' | 'repos';
  id: string;
  text: string;
  /** how many items this row stands for (a per-brain count, or 1) */
  count: number;
  severity: Severity;
  /** ISO time it started waiting (Runs only) */
  since: string | null;
  /** the built-in General Brain (opened by its own view, never by the name) */
  builtin?: boolean;
}

/** The General Brain's Runner Agent waits on the person (draft or problems). */
const generalWaits = (g?: GeneralBrain | null) =>
  !!g && (g.projectAgentStatus === 'draft' || g.projectAgentStatus === 'changed' || g.projectAgentStatus === 'invalid');

/** The Need You row of the built-in General Brain's Runner Agent, or none. Its id never
 *  collides with a Projects/ Brain that carries the same name (`<name>-agent`). */
export function generalNeed(g?: GeneralBrain | null): HubNeed | null {
  if (!g || !generalWaits(g)) return null;
  const s = g.projectAgentStatus;
  return { brain: g.name, kind: 'agent', id: 'builtin-general-agent', since: null, builtin: true, count: 1,
    severity: s === 'invalid' ? 'review' : 'setup',
    text: s === 'draft' ? 'Runner Agent draft to confirm' : s === 'changed' ? CHANGED_TEXT('Runner Agent')
      : 'Runner Agent file has problems to fix' };
}

/** A Project Agent waits on the person while it is a draft or changed since it was
 *  confirmed (confirm), or has problems (fix). */
const agentWaits = (b: BrainSummary) =>
  b.projectAgentStatus === 'draft' || b.projectAgentStatus === 'changed' || b.projectAgentStatus === 'invalid';

/** Confirmed, then changed (in chat, by an agent or by hand): dispatch refuses it until
 *  the person confirms it again (core/project_agent.confirmation). */
const CHANGED_TEXT = (who: string) => `${who} changed since you confirmed it: confirm again`;

/** health.pending split by source (the attention channel's own split). An old daemon
 *  without the split counts everything as one unnamed bucket. */
export function pendingSplit(b: Pick<BrainSummary, 'health' | 'pendingBySource'>) {
  const by = b.pendingBySource;
  if (!by) return { runs: 0, escalations: 0, jobs: 0, repos: 0, other: b.health?.pending ?? 0 };
  const runs = by.paused_run ?? 0, escalations = by.escalation ?? 0, jobs = by.job ?? 0;
  const repos = by.local_root ?? 0;   // a repository it started reading: a notice, not a task
  const total = Object.values(by).reduce((x, y) => x + y, 0);
  return { runs, escalations, jobs, repos, other: total - runs - escalations - jobs - repos };
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** Every item across all brains that waits for a person: Runs oldest first, then
 *  escalations, failing jobs and Project Agents to confirm or fix.
 *  Each item is counted once: the attention channel's count per source, with the
 *  Runs named from the analytics where it lists them. */
export function hubNeeds(brains: BrainSummary[], a: PipelineAnalytics | null | undefined,
  general?: GeneralBrain | null): HubNeed[] {
  const out: HubNeed[] = [];
  const runs: HubNeed[] = [];
  for (const b of brains) {
    const split = pendingSplit(b);
    const listed = (a?.byProject.find((g) => g.project === b.name)?.runs ?? []).filter((r) => r.needsYou);
    for (const r of listed) {
      const why = r.pauseKind === 'decision' ? 'question' : r.pauseKind === 'stopped' ? 'stopped' : 'waiting';
      runs.push({ brain: b.name, kind: 'run', id: r.id, text: `Run ${why}: ${r.requirement || r.id}`,
        since: r.updatedAt || null, count: 1, severity: 'urgent' });
    }
    const unlisted = split.runs - listed.length;
    const row = (kind: HubNeed['kind'], count: number, severity: Severity, text: string) =>
      out.push({ brain: b.name, kind, id: `${b.name}-${kind}`, text, since: null, count, severity });
    if (unlisted > 0) row('runs', unlisted, 'urgent', `${plural(unlisted, 'Run')} waiting on you`);
    if (split.escalations) row('escalations', split.escalations, 'urgent', `${plural(split.escalations, 'escalation')} to decide`);
    if (split.jobs + split.other) row('jobs', split.jobs + split.other, 'review', `${plural(split.jobs + split.other, 'other item')} in Need You`);
    if (split.repos) row('repos', split.repos, 'notice', `now reads ${plural(split.repos, 'new repository', 'new repositories')}`);
  }
  runs.sort((x, y) => (x.since || '').localeCompare(y.since || ''));
  const agents: HubNeed[] = brains.filter(agentWaits).map((b) => ({
    brain: b.name, kind: 'agent', id: `${b.name}-agent`, since: null, count: 1,
    severity: b.projectAgentStatus === 'invalid' ? 'review' : 'setup',
    text: b.projectAgentStatus === 'draft' ? 'Project Agent draft to confirm'
      : b.projectAgentStatus === 'changed' ? CHANGED_TEXT('Project Agent') : 'Project Agent file has problems to fix',
  }));
  const order: HubNeed['kind'][] = ['runs', 'escalations', 'jobs', 'repos'];
  out.sort((x, y) => order.indexOf(x.kind) - order.indexOf(y.kind));
  const g = generalNeed(general);
  return [...runs, ...out, ...agents, ...(g ? [g] : [])];
}

export function hubSummary(brains: BrainSummary[], a?: PipelineAnalytics | null,
  general?: GeneralBrain | null): HubSummary {
  const known = new Set(brains.map((b) => b.name));
  const runs = (a?.byProject ?? []).filter((g) => known.has(g.project)).flatMap((g) => g.runs);
  const reported = brains.filter((b) => typeof b.recentActivity === 'number');
  return {
    brains: brains.length,
    // the attention channel already counts waiting Runs (paused_run): never add them twice
    needsYou: brains.reduce((s, b) => s + (b.health.pending ?? 0), 0) + brains.filter(agentWaits).length
      + (generalWaits(general) ? 1 : 0),
    running: brains.filter((b) => b.health.liveRun).length,
    born: brains.filter((b) => b.setupState === 'setting-up').length,
    learned: reported.length ? reported.reduce((s, b) => s + (b.recentActivity ?? 0), 0) : null,
    learnedBrains: reported.filter((b) => (b.recentActivity ?? 0) > 0).length,
    landed: runs.filter((r) => r.status === 'completed' && !r.notLanded).length,
    finished: runs.filter((r) => r.status !== 'running').length,
    liveRuns: runs.filter((r) => r.status === 'running').length,
  };
}

/** A card's status: an open item's severity, `live` for a Run in progress, `calm`
 *  when nothing is going on (the card then shows no status at all). */
export type BrainSignalTone = Severity | 'live' | 'calm';
export interface BrainSignal { tone: BrainSignalTone; text: string; rank: number }

/** The ONE most pressing thing to say about a brain on its Hub card: what blocks on the
 *  person, then what waits for a review, a Run in progress, setup, a notice. */
export function brainSignal(b: BrainSummary, a?: PipelineAnalytics | null): BrainSignal {
  const runs = a?.byProject.find((g) => g.project === b.name)?.runs ?? [];
  const split = pendingSplit(b);
  const runNeeds = Math.max(runs.filter((r) => r.needsYou).length, split.runs);
  if (runNeeds) return { tone: 'urgent', rank: 0, text: runNeeds === 1 ? 'A Run needs you' : `${runNeeds} Runs need you` };
  if (split.escalations) return { tone: 'urgent', rank: 0, text: `${plural(split.escalations, 'escalation')} to decide` };
  if (split.jobs + split.other) return { tone: 'review', rank: 1, text: `${plural(split.jobs + split.other, 'item')} in Need You` };
  if (b.projectAgentStatus === 'invalid') return { tone: 'review', rank: 1, text: 'Project Agent has problems' };
  if (b.health.liveRun) return { tone: 'live', rank: 2, text: 'Run in progress' };
  if (b.setupState === 'setting-up') return { tone: 'setup', rank: 3, text: bornText(b.setupProgress) };
  if (b.projectAgentStatus === 'draft') return { tone: 'setup', rank: 3, text: 'Project Agent to confirm' };
  if (b.projectAgentStatus === 'changed') return { tone: 'setup', rank: 3, text: 'Project Agent to confirm again' };
  if (split.repos) return { tone: 'notice', rank: 4, text: `Reads ${plural(split.repos, 'new repository', 'new repositories')}` };
  return { tone: 'calm', rank: 5, text: '' };
}

/** What each P6 check asks for, as the next thing to do (verify_ddd_complete.py names).
 *  A check not listed falls back to its own name without the number. */
const NEXT_STEP: Record<string, string> = {
  'Identity/Manifest': 'repair the manifest',
  'Knowledge': 'create the four docs',
  'Knowledge/structure': 'add the missing sections',
  'Knowledge/content': 'write the first knowledge',
  'Gates': 'add the gates folder',
  'Capabilities': 'add the skills',
  'Delivery Contract': 'bind the repo',
  'Project Agent': 'confirm the Project Agent',
  'Refresher/code-intel': 'build the code graph',
};

/** "Being born · next: write the first knowledge (4/9)" — the next step first, so a
 *  truncated card still shows it; the count is the P6 checks done of all. */
export function bornText(p: { done: number; total: number; next?: string | null } | null | undefined): string {
  if (!p) return 'Being born';
  const key = p.next ? p.next.replace(/^[^A-Za-z]+/, '').trim() : '';
  const step = key ? (NEXT_STEP[key] ?? key.replace(/\//g, ' ')) : '';
  return step ? `Being born · next: ${step} (${p.done}/${p.total})` : `Being born · ${p.done} of ${p.total} checks`;
}
