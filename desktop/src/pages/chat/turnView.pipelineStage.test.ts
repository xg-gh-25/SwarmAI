/**
 * turnView — backend-observed pipeline stage rows + model-written landmarks.
 *
 * What is tested:
 * - a `pipeline_stage` content block (written by the backend from run.json,
 *   core/pipeline_stage_events.py) becomes a `stage` unit: never folded, in content
 *   order, splitting the surrounding process into two segments;
 * - while the turn streams, the live process row after a stage row carries the
 *   NEXT stage (`live.stage`), so a long BUILD reads as BUILD;
 * - `isLandmarkText` keeps model-written gate verdicts and circled-number progress
 *   lines visible, without catching mid-line mentions or ordinary numbered lists.
 */
import { describe, it, expect } from 'vitest';
import type { ContentBlock, PipelineStageContent } from '../../types';
import { deriveTurnView, isLandmarkText } from './turnView';

const tu = (id: string, summary = 'Running: ls'): ContentBlock =>
  ({ type: 'tool_use', id, name: 'Bash', summary, category: 'bash' }) as ContentBlock;
const tx = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;
const stage = (over: Partial<PipelineStageContent> = {}): PipelineStageContent => ({
  type: 'pipeline_stage', runId: 'run_t1', stage: 'build', status: 'completed', ordinal: 5,
  position: 4, total: 8, profile: 'full', next: 'review', nextOrdinal: 6,
  gate: { name: 'GATE 1', verdict: 'WARN' }, summary: null, at: '2026-10-17T22:00:00+08:00',
  ...over,
});

describe('deriveTurnView — pipeline_stage blocks', () => {
  it('a stage block is its own unit between two process segments', () => {
    const view = deriveTurnView([tu('a'), stage(), tu('b'), tx('done')], false);
    expect(view.units.map((u) => u.kind)).toEqual(['process', 'stage', 'process', 'outcome']);
    const s = view.units[1];
    expect(s.kind === 'stage' && s.block.stage).toBe('build');
    // not counted as a step of either segment
    const steps = view.units.filter((u) => u.kind === 'process')
      .flatMap((u) => (u.kind === 'process' ? u.steps : []));
    expect(steps.every((st) => st.kind === 'tool')).toBe(true);
  });

  it('a stage block after the last tool call is still a stage, not the answer', () => {
    const view = deriveTurnView([tu('a'), stage({ stage: 'reflect', ordinal: 10, next: null, nextOrdinal: null })], false);
    expect(view.units.map((u) => u.kind)).toEqual(['process', 'stage']);
    expect(view.copyText).toBe('');
  });

  it('several stage rows keep content order', () => {
    const view = deriveTurnView([
      stage({ stage: 'think', ordinal: 2, next: 'plan', nextOrdinal: 3 }),
      tu('a'),
      stage({ stage: 'plan', ordinal: 3, next: 'build', nextOrdinal: 5 }),
    ], false);
    const stages = view.units.flatMap((u) => (u.kind === 'stage' ? [u.block.stage] : []));
    expect(stages).toEqual(['think', 'plan']);
  });

  it('streaming: the live row after a stage row names the next stage', () => {
    const view = deriveTurnView([tu('a'), stage(), tu('b')], true);
    const last = view.units[view.units.length - 1];
    expect(last.kind).toBe('process');
    expect(last.kind === 'process' && last.live?.stage).toEqual({ stage: 'review', ordinal: 6 });
  });

  it('streaming with nothing after the stage row yet: a live row still names it', () => {
    const view = deriveTurnView([tu('a'), stage()], true);
    const last = view.units[view.units.length - 1];
    expect(last.kind === 'process' && last.isLive).toBe(true);
    expect(last.kind === 'process' && last.live?.stage).toEqual({ stage: 'review', ordinal: 6 });
  });

  it('the latest stage row wins, and a final stage (no next) gives no live stage', () => {
    const v1 = deriveTurnView([
      stage({ stage: 'think', ordinal: 2, next: 'plan', nextOrdinal: 3 }), tu('a'),
      stage({ stage: 'plan', ordinal: 3, next: 'build', nextOrdinal: 5 }), tu('b'),
    ], true);
    const l1 = v1.units[v1.units.length - 1];
    expect(l1.kind === 'process' && l1.live?.stage).toEqual({ stage: 'build', ordinal: 5 });
    const v2 = deriveTurnView([tu('a'), stage({ stage: 'reflect', ordinal: 10, next: null, nextOrdinal: null }), tu('b')], true);
    const l2 = v2.units[v2.units.length - 1];
    expect(l2.kind === 'process' && l2.live?.stage).toBeUndefined();
  });

  it('a landmark after the stage row (e.g. a Gate BLOCK) drops the stale live stage', () => {
    const view = deriveTurnView([tu('a'), stage(), tu('b'), tx('Gate 1: **BLOCK** — back to plan'), tu('c')], true);
    const last = view.units[view.units.length - 1];
    expect(last.kind === 'process' && last.live?.stage).toBeUndefined();
  });

  it('no stage row → no live stage (ordinary turns are unchanged)', () => {
    const view = deriveTurnView([tu('a'), tu('b')], true);
    const last = view.units[view.units.length - 1];
    expect(last.kind === 'process' && last.live?.stage).toBeUndefined();
  });

  it('a finished turn carries no live stage', () => {
    const view = deriveTurnView([tu('a'), stage(), tu('b')], false);
    expect(view.units.some((u) => u.kind === 'process' && u.live?.stage)).toBe(false);
  });
});

describe('isLandmarkText — model-written verdicts and progress lines', () => {
  it.each([
    'Gate 1: **PASS** — 全 5 check 过,独立核实 API 全存在',
    '**Gate 2** 结果: 3 findings',
    '★ Gate 0: PASS',
    'Gate 2 结果:\n- correctness: NO FINDINGS',
    '③ ④ ⑤ ⑥ 都已完成，进 ⑦ TEST。',
    '⑤ BUILD → TDD 6R→GREEN',
    '## ✦ ⑥ REVIEW\n→ spec PASS',
  ])('%s → landmark', (t) => expect(isLandmarkText(t)).toBe(true));

  it.each([
    'aggregate 1',
    'delegate 2',
    'the Gate 1 skeptic found nothing',
    '① 第一点:先读源码\n② 第二点:再跑测试',
    '① 先看 TECH.md',
    'gateway 1 is down',
  ])('%s → prose', (t) => expect(isLandmarkText(t)).toBe(false));
});
