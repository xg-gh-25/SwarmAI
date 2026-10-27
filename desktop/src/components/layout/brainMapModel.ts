/**
 * brainMapModel — pure builders for the Brain map (Brain Hub detail "Map" view and
 * the Hub map above the card wall). No React, no fetch: every number comes from a
 * record the backend already produced (GET /ddd/brains[/{name}], the brain's review
 * queue, GET /pipelines/analytics). What does not exist yet is marked `mock`, and a
 * value nobody measures is `null` (shown as UNKNOWN), never a guess.
 *
 * Exports:
 *   SECTION_ORDER        the six DDD sections, map order
 *   sectionCounts        per-section count line from a BrainDetail
 *   projectRuns          this brain's Runs from the 30-day analytics payload
 *   runTone              Run status -> map dot tone
 *   needsYouItems        one queue: Runs waiting on you + proposals waiting on you
 *   brainEval            the Eval numbers for one brain (null = not measured)
 *   hubSummary           Brain Hub home totals across all brains
 *   hubNeeds             Brain Hub home: everything waiting on you, all brains
 *   pendingSplit         health.pending split by source (Runs / proposals / escalations / jobs)
 *   brainSignal          the one signal a Hub card shows, and its sort rank
 */
import type { BrainDetail, BrainSummary, ReviewData, SectionKey } from '../../services/ddd';
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
  kind: 'run' | 'proposal' | 'agent' | 'other';
  id: string;
  title: string;
  detail: string;
}

/** Everything in this brain that waits for a person, in one list: Runs, rule
 *  proposals, and a Project Agent draft to confirm (or a file with problems). */
export function needsYouItems(
  runs: PipelineRunSummary[], review: ReviewData | null,
  agent?: { status?: string; problems?: string[] } | null,
  bySource?: Record<string, number>,
): NeedItem[] {
  const items: NeedItem[] = [];
  for (const r of runs) {
    if (!r.needsYou) continue;
    const why = r.pauseKind === 'decision' ? 'a question is waiting'
      : r.pauseKind === 'stopped' ? 'stopped, needs a resume'
      : r.checkpointReason || 'waiting on you';
    items.push({ kind: 'run', id: r.id, title: r.requirement || r.id, detail: `${r.id} · ${why}` });
  }
  for (const p of review?.proposals ?? []) {
    items.push({
      kind: 'proposal', id: p.id, title: p.content,
      detail: `rule proposal · ${p.target_doc}${p.target_section ? ' / ' + p.target_section : ''}${p.source_run_id ? ' · from ' + p.source_run_id : ''}`,
    });
  }
  // what the attention channel holds that neither list above carries
  const esc = bySource?.escalation ?? 0, jobs = bySource?.job ?? 0;
  if (esc) items.push({ kind: 'other', id: 'escalations', title: `${esc} escalation${esc === 1 ? '' : 's'} to decide`, detail: 'open Need You to answer' });
  if (jobs) items.push({ kind: 'other', id: 'jobs', title: `${jobs} failing job${jobs === 1 ? '' : 's'}`, detail: 'open Need You to triage' });
  if (agent && agent.problems && agent.problems.length) {
    items.push({ kind: 'agent', id: 'project-agent', title: 'The Project Agent file has problems to fix', detail: agent.problems.join(' · ') });
  } else if (agent && agent.status !== 'reviewed') {
    items.push({ kind: 'agent', id: 'project-agent', title: 'Confirm the Project Agent draft', detail: 'generated from this Brain; review it, then confirm or revise' });
  }
  return items;
}

export interface BrainEval {
  runs: { total: number; landed: number; completionRate: number | null; avgCycleMin: number | null; tokens: number | null; needsYou: number };
  brain: { entries: number; sinking: number | null; reclaimable: number | null; recentActivity: number | null; autoApplied: number; pending: number };
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
      sinking: detail?.health?.sinking ?? null,
      reclaimable: detail?.health?.noise.reclaimable ?? null,
      recentActivity: detail?.health?.recentActivity ?? null,
      autoApplied: (review?.hunks ?? []).filter((h) => h.tag === 'cultivation·auto-applied').length,
      pending: review?.proposals.length ?? 0,
    },
  };
}

export interface HubSummary {
  brains: number;
  /** items waiting on a person: the attention channel's count (Runs, rule
   *  proposals, escalations, failing jobs) + Project Agents to confirm or fix */
  needsYou: number;
  /** brains with a Run in progress */
  running: number;
  /** brains whose setup is recorded as setting-up */
  born: number;
  /** ddd-changelog entries stamped in the last 30 days, summed; null = no brain reported it */
  learned: number | null;
  learnedBrains: number;
  /** dormant or archived knowledge entries, summed */
  sinking: number;
  /** last 30 days: Runs that landed, and Runs that finished (landed or not) */
  landed: number;
  finished: number;
  /** Runs running now (all lanes are the chat tab today) */
  liveRuns: number;
}

export interface HubNeed {
  brain: string;
  /** run: one Run (from the analytics); runs: Runs waiting that the analytics did
   *  not list; proposals / escalations / jobs: counted per brain; agent: a draft */
  kind: 'run' | 'runs' | 'proposals' | 'escalations' | 'jobs' | 'agent';
  id: string;
  text: string;
  /** ISO time it started waiting (Runs only) */
  since: string | null;
}

/** A Project Agent waits on the person while it is a draft (confirm) or has problems (fix). */
const agentWaits = (b: BrainSummary) => b.projectAgentStatus === 'draft' || b.projectAgentStatus === 'invalid';

/** health.pending split by source (the attention channel's own split). An old daemon
 *  without the split counts everything as one unnamed bucket. */
export function pendingSplit(b: Pick<BrainSummary, 'health' | 'pendingBySource'>) {
  const by = b.pendingBySource;
  if (!by) return { runs: 0, proposals: 0, escalations: 0, jobs: 0, other: b.health?.pending ?? 0 };
  const runs = by.paused_run ?? 0, proposals = by.cultivation ?? 0, escalations = by.escalation ?? 0, jobs = by.job ?? 0;
  const total = Object.values(by).reduce((x, y) => x + y, 0);
  return { runs, proposals, escalations, jobs, other: total - runs - proposals - escalations - jobs };
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** Every item across all brains that waits for a person: Runs oldest first, then
 *  rule proposals, escalations, failing jobs and Project Agents to confirm or fix.
 *  Each item is counted once: the attention channel's count per source, with the
 *  Runs named from the analytics where it lists them. */
export function hubNeeds(brains: BrainSummary[], a: PipelineAnalytics | null | undefined): HubNeed[] {
  const out: HubNeed[] = [];
  const runs: HubNeed[] = [];
  for (const b of brains) {
    const split = pendingSplit(b);
    const listed = (a?.byProject.find((g) => g.project === b.name)?.runs ?? []).filter((r) => r.needsYou);
    for (const r of listed) {
      const why = r.pauseKind === 'decision' ? 'question' : r.pauseKind === 'stopped' ? 'stopped' : 'waiting';
      runs.push({ brain: b.name, kind: 'run', id: r.id, text: `Run ${why}: ${r.requirement || r.id}`, since: r.updatedAt || null });
    }
    const unlisted = split.runs - listed.length;
    if (unlisted > 0) out.push({ brain: b.name, kind: 'runs', id: `${b.name}-runs`, text: `${plural(unlisted, 'Run')} waiting on you`, since: null });
    if (split.proposals) out.push({ brain: b.name, kind: 'proposals', id: `${b.name}-proposals`, text: `${plural(split.proposals, 'rule proposal')} to review`, since: null });
    if (split.escalations) out.push({ brain: b.name, kind: 'escalations', id: `${b.name}-escalations`, text: `${plural(split.escalations, 'escalation')} to decide`, since: null });
    if (split.jobs + split.other) out.push({ brain: b.name, kind: 'jobs', id: `${b.name}-jobs`, text: `${plural(split.jobs + split.other, 'other item')} in Need You`, since: null });
  }
  runs.sort((x, y) => (x.since || '').localeCompare(y.since || ''));
  const agents: HubNeed[] = brains.filter(agentWaits).map((b) => ({
    brain: b.name, kind: 'agent', id: `${b.name}-agent`, since: null,
    text: b.projectAgentStatus === 'draft' ? 'Project Agent draft to confirm' : 'Project Agent file has problems to fix',
  }));
  const order: HubNeed['kind'][] = ['runs', 'proposals', 'escalations', 'jobs'];
  out.sort((x, y) => order.indexOf(x.kind) - order.indexOf(y.kind));
  return [...runs, ...out, ...agents];
}

export function hubSummary(brains: BrainSummary[], a?: PipelineAnalytics | null): HubSummary {
  const known = new Set(brains.map((b) => b.name));
  const runs = (a?.byProject ?? []).filter((g) => known.has(g.project)).flatMap((g) => g.runs);
  const reported = brains.filter((b) => typeof b.recentActivity === 'number');
  return {
    brains: brains.length,
    // the attention channel already counts waiting Runs (paused_run): never add them twice
    needsYou: brains.reduce((s, b) => s + (b.health.pending ?? 0), 0) + brains.filter(agentWaits).length,
    running: brains.filter((b) => b.health.liveRun).length,
    born: brains.filter((b) => b.setupState === 'setting-up').length,
    learned: reported.length ? reported.reduce((s, b) => s + (b.recentActivity ?? 0), 0) : null,
    learnedBrains: reported.filter((b) => (b.recentActivity ?? 0) > 0).length,
    sinking: brains.reduce((s, b) => s + (b.health.sinking ?? 0), 0),
    landed: runs.filter((r) => r.status === 'completed' && !r.notLanded).length,
    finished: runs.filter((r) => r.status !== 'running').length,
    liveRuns: runs.filter((r) => r.status === 'running').length,
  };
}

export type BrainSignalTone = 'error' | 'primary' | 'warning' | 'muted';
export interface BrainSignal { tone: BrainSignalTone; text: string; rank: number }

/** The ONE most urgent thing to say about a brain on its Hub card, and its sort rank. */
export function brainSignal(b: BrainSummary, a?: PipelineAnalytics | null): BrainSignal {
  const runs = a?.byProject.find((g) => g.project === b.name)?.runs ?? [];
  const split = pendingSplit(b);
  const runNeeds = Math.max(runs.filter((r) => r.needsYou).length, split.runs);
  if (runNeeds) return { tone: 'error', rank: 0, text: runNeeds === 1 ? 'A Run needs you' : `${runNeeds} Runs need you` };
  if (split.proposals) return { tone: 'error', rank: 0, text: `${plural(split.proposals, 'proposal')} to review` };
  if (split.escalations) return { tone: 'error', rank: 0, text: `${plural(split.escalations, 'escalation')} to decide` };
  if (split.jobs + split.other) return { tone: 'error', rank: 0, text: `${plural(split.jobs + split.other, 'item')} in Need You` };
  if (b.projectAgentStatus === 'draft') return { tone: 'error', rank: 0, text: 'Project Agent to confirm' };
  if (b.projectAgentStatus === 'invalid') return { tone: 'error', rank: 0, text: 'Project Agent has problems' };
  if (b.health.liveRun) return { tone: 'primary', rank: 1, text: 'Run in progress' };
  if (b.setupState === 'setting-up') {
    return { tone: 'warning', rank: 2, text: bornText(b.setupProgress) };
  }
  const last = b.health.lastChangeRelative;
  return { tone: 'muted', rank: 3, text: last && last !== '--' ? `Last landed ${last}` : 'No landed change in 30 days' };
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
