/**
 * Grouping of the run history by brain (DDD) or by Project Agent.
 *
 * Each group is one brain or one agent with its runs, newest first. Runs with no brain
 * (or no agent) form one group of their own, always placed last whatever its size, so
 * the brains that drove the work come first and the unassigned runs are never mixed
 * into them. The other groups are ordered by how many runs they drove.
 */
import { BY_TYPE, type HistoryRun } from './mockData';

export type HistoryAxis = 'brain' | 'agent';

export const NO_BRAIN = '没有大脑';
export const NO_AGENT = '没有 Project Agent';

export interface HistoryGroup {
  /** The brain / agent name, or '' for the group of runs that have none. */
  key: string;
  name: string;
  unassigned: boolean;
  runs: HistoryRun[];
}

export function groupHistory(runs: HistoryRun[], axis: HistoryAxis): HistoryGroup[] {
  const by = new Map<string, HistoryRun[]>();
  for (const r of runs) {
    const k = (axis === 'brain' ? r.brain : r.agent) ?? '';
    const g = by.get(k);
    if (g) g.push(r);
    else by.set(k, [r]);
  }
  return [...by]
    .map(([key, rs]) => ({ key, name: key || (axis === 'brain' ? NO_BRAIN : NO_AGENT), unassigned: !key, runs: rs }))
    .sort((a, b) => Number(a.unassigned) - Number(b.unassigned) || b.runs.length - a.runs.length || a.name.localeCompare(b.name));
}

export const isCodeRun = (r: HistoryRun): boolean => r.type === 'bugfix' || r.type === 'trivial';

/** What a group's runs were, e.g. "bugfix 6 · trivial 2 · 调研 2" (in 按类型's order). */
export function typeMix(runs: HistoryRun[]): string {
  return BY_TYPE
    .map(({ key }) => [key, runs.filter((r) => r.type === key).length] as const)
    .filter(([, n]) => n > 0)
    .map(([k, n]) => `${k} ${n}`)
    .join(' · ');
}
