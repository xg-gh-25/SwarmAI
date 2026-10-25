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
 *   hubSummary           Hub map totals across all brains
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

export interface HubSummary { brains: number; needsYou: number; live: number }

export function hubSummary(brains: BrainSummary[]): HubSummary {
  return {
    brains: brains.length,
    needsYou: brains.reduce((s, b) => s + (b.health.pending ?? 0), 0),
    live: brains.filter((b) => b.health.liveRun).length,
  };
}
