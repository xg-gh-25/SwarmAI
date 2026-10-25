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
  if (r.status === 'completed') return r.verified === false ? 'failed' : 'landed';
  if (r.status === 'failed' || r.status === 'abandoned') return 'failed';
  return 'stopped';
}

export interface NeedItem {
  kind: 'run' | 'proposal';
  id: string;
  title: string;
  detail: string;
}

/** Everything in this brain that waits for a person, in one list. */
export function needsYouItems(runs: PipelineRunSummary[], review: ReviewData | null): NeedItem[] {
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
  return items;
}

export interface BrainEval {
  runs: { total: number; landed: number; completionRate: number | null; avgCycleMin: number | null; tokens: number | null; needsYou: number };
  brain: { entries: number; sinking: number | null; reclaimable: number | null; recentActivity: number | null; autoApplied: number; pending: number };
}

export function brainEval(
  runs: PipelineRunSummary[], detail: BrainDetail | null, review: ReviewData | null,
): BrainEval {
  const landed = runs.filter((r) => r.status === 'completed' && r.verified !== false);
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
  /** items waiting on a person: Runs that need you + staged rule proposals +
   *  Project Agents to confirm or fix */
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
  kind: 'run' | 'proposals' | 'agent';
  id: string;
  text: string;
  /** ISO time it started waiting (Runs only) */
  since: string | null;
}

/** A Project Agent waits on the person while it is a draft (confirm) or has problems (fix). */
const agentWaits = (b: BrainSummary) => b.projectAgentStatus === 'draft' || b.projectAgentStatus === 'invalid';

/** Every item across all brains that waits for a person: oldest Run first, then
 *  proposals, then Project Agents to confirm or fix. */
export function hubNeeds(brains: BrainSummary[], a: PipelineAnalytics | null | undefined): HubNeed[] {
  const known = new Set(brains.map((b) => b.name));
  const runs: HubNeed[] = [];
  for (const g of a?.byProject ?? []) {
    if (!known.has(g.project)) continue;
    for (const r of g.runs) {
      if (!r.needsYou) continue;
      const why = r.pauseKind === 'decision' ? 'question' : r.pauseKind === 'stopped' ? 'stopped' : 'waiting';
      runs.push({ brain: g.project, kind: 'run', id: r.id, text: `Run ${why}: ${r.requirement || r.id}`, since: r.updatedAt || null });
    }
  }
  runs.sort((x, y) => (x.since || '').localeCompare(y.since || ''));
  const props: HubNeed[] = brains.filter((b) => (b.health.pending ?? 0) > 0).map((b) => ({
    brain: b.name, kind: 'proposals', id: `${b.name}-proposals`,
    text: `${b.health.pending} rule proposal${b.health.pending === 1 ? '' : 's'} to review`, since: null,
  }));
  const agents: HubNeed[] = brains.filter(agentWaits).map((b) => ({
    brain: b.name, kind: 'agent', id: `${b.name}-agent`, since: null,
    text: b.projectAgentStatus === 'draft' ? 'Project Agent draft to confirm' : 'Project Agent file has problems to fix',
  }));
  return [...runs, ...props, ...agents];
}

export function hubSummary(brains: BrainSummary[], a?: PipelineAnalytics | null): HubSummary {
  const known = new Set(brains.map((b) => b.name));
  const runs = (a?.byProject ?? []).filter((g) => known.has(g.project)).flatMap((g) => g.runs);
  const reported = brains.filter((b) => typeof b.recentActivity === 'number');
  return {
    brains: brains.length,
    needsYou: runs.filter((r) => r.needsYou).length + brains.reduce((s, b) => s + (b.health.pending ?? 0), 0)
      + brains.filter(agentWaits).length,
    running: brains.filter((b) => b.health.liveRun).length,
    born: brains.filter((b) => b.setupState === 'setting-up').length,
    learned: reported.length ? reported.reduce((s, b) => s + (b.recentActivity ?? 0), 0) : null,
    learnedBrains: reported.filter((b) => (b.recentActivity ?? 0) > 0).length,
    sinking: brains.reduce((s, b) => s + (b.health.sinking ?? 0), 0),
    landed: runs.filter((r) => r.status === 'completed' && r.verified !== false).length,
    finished: runs.filter((r) => r.status !== 'running').length,
    liveRuns: runs.filter((r) => r.status === 'running').length,
  };
}

export type BrainSignalTone = 'error' | 'primary' | 'warning' | 'muted';
export interface BrainSignal { tone: BrainSignalTone; text: string; rank: number }

/** The ONE most urgent thing to say about a brain on its Hub card, and its sort rank. */
export function brainSignal(b: BrainSummary, a?: PipelineAnalytics | null): BrainSignal {
  const runs = a?.byProject.find((g) => g.project === b.name)?.runs ?? [];
  const runNeeds = runs.filter((r) => r.needsYou).length;
  const pending = b.health.pending ?? 0;
  if (runNeeds) return { tone: 'error', rank: 0, text: runNeeds === 1 ? 'A Run needs you' : `${runNeeds} Runs need you` };
  if (pending) return { tone: 'error', rank: 0, text: `${pending} proposal${pending === 1 ? '' : 's'} to review` };
  if (b.projectAgentStatus === 'draft') return { tone: 'error', rank: 0, text: 'Project Agent to confirm' };
  if (b.projectAgentStatus === 'invalid') return { tone: 'error', rank: 0, text: 'Project Agent has problems' };
  if (b.health.liveRun) return { tone: 'primary', rank: 1, text: 'Run in progress' };
  if (b.setupState === 'setting-up') {
    const p = b.setupProgress;
    return { tone: 'warning', rank: 2, text: p ? `Being born · ${p.done} of ${p.total} checks` : 'Being born' };
  }
  const last = b.health.lastChangeRelative;
  return { tone: 'muted', rank: 3, text: last && last !== '--' ? `Last landed ${last}` : 'No landed change in 30 days' };
}
