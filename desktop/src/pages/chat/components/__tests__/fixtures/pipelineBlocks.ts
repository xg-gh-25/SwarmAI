/**
 * Content-block builders for pipeline-turn tests (PipelineBlock, no-Intent, replay).
 * Placeholder text only: no real session text, paths or user content (the repo is
 * public).
 */
import type { ContentBlock, PipelineRunContent, PipelineStageContent } from '../../../../../types';

export const RUN = 'run_fixture';
export const FULL = ['evaluate', 'gate0', 'think', 'plan', 'gate1', 'build', 'review', 'test', 'gate2', 'deliver', 'reflect'];
const isGate = (s: string) => /^gate\d$/.test(s);

export const checklistOf = (names: string[]) => names.map((s) => ({ stage: s, gate: isGate(s) }));

export const runBlock = (over: Partial<PipelineRunContent> = {}): ContentBlock => ({
  type: 'pipeline_run', runId: RUN, profile: 'full', stages: FULL.filter((s) => !isGate(s)),
  checklist: checklistOf(FULL), requirement: 'REQUIREMENT TEXT', current: null,
  at: '2026-10-19T10:00:00+08:00', ...over,
}) as ContentBlock;

export const row = (stage: string, status: 'in_progress' | 'completed' | 'skipped', at = '2026-10-19T10:00:00+08:00',
  over: Partial<PipelineStageContent> = {}): ContentBlock => ({
  type: 'pipeline_stage', runId: RUN, stage, status, ordinal: null, position: null, total: 8,
  profile: 'full', next: null, nextOrdinal: null,
  gate: isGate(stage) ? { name: stage.replace('gate', 'GATE '), verdict: stage === 'gate2' ? '3/3 fixed' : 'PASS' } : null,
  summary: null, at, ...over,
}) as ContentBlock;

export const tu = (id: string, cmd = 'ls -la src'): ContentBlock =>
  ({ type: 'tool_use', id, name: 'Bash', summary: `Running: ${cmd}`, category: 'bash' }) as ContentBlock;
export const tr = (toolUseId: string): ContentBlock =>
  ({ type: 'tool_result', toolUseId, content: 'ok', isError: false, truncated: false }) as ContentBlock;
export const tx = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;
export const th = (thinking: string): ContentBlock => ({ type: 'thinking', thinking }) as ContentBlock;

/** One stage: its start row, one finished call, its end row. */
export const stageRun = (stage: string, n: number): ContentBlock[] =>
  [row(stage, 'in_progress'), tu(`${stage}-${n}`), tr(`${stage}-${n}`), row(stage, 'completed')];

/** A whole pipeline turn, block by block, for the replay test (X7). Every piece of text
 *  that should be readable carries a `tok…` marker, so a frame's visible set is the
 *  markers in its text. Synthetic: a restatement, a Gate 1 BLOCK that sends PLAN back
 *  (round 2), exploration calls, identical calls, a named script call, notes, thoughts
 *  between calls, and a final answer. */
export function replayTurn(): ContentBlock[] {
  const call = (id: string, cmd: string): ContentBlock[] => [tu(id, cmd), tr(id)];
  return [
    th('tokT0 weighing what the ask needs'),
    tx('I read it as: tokRESTATE.'),
    runBlock(),
    row('evaluate', 'in_progress'),
    th('tokT1 the scope comes first'),
    ...call('e1', 'cat src/tokR1.ts'), ...call('e2', 'grep -rn "tokS1" src'), ...call('e3', 'sed -n 1,40p src/tokR2.ts'),
    row('evaluate', 'completed'), row('gate0', 'completed'),
    row('think', 'in_progress'), th('tokT2 two ways to do it'), ...call('t1', 'cat docs/tokR3.md'), row('think', 'completed'),
    row('plan', 'in_progress'), ...call('p1', 'cat docs/tokR4.md'), row('plan', 'completed'),
    row('gate1', 'completed', undefined, { gate: { name: 'GATE 1', verdict: 'BLOCK' } }),
    row('plan', 'in_progress'), th('tokT3 the plan missed a case'), ...call('p2', 'cat docs/tokR5.md'), row('plan', 'completed'),
    row('gate1', 'completed'),
    row('build', 'in_progress'),
    ...call('b1', 'make tokM1'), ...call('b2', 'make tokM1'), tx('tokN1 the build is green.'),
    ...call('b3', 'python3 backend/scripts/artifact_cli.py run-update --project P --status tokX'),
    row('build', 'completed'),
    ...stageRun('review', 1), ...stageRun('test', 1), row('gate2', 'completed'),
    ...stageRun('deliver', 1), ...stageRun('reflect', 1),
    tx('tokANS the answer.'),
  ];
}
