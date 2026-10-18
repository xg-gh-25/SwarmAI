/**
 * Layer-4 contract: the stage-row projection vs the REAL backend block shape.
 *
 * `__fixtures__/pipeline-stage-blocks.json` is generated FROM
 * `backend/core/pipeline_stage_events.new_stage_blocks` and kept in lockstep by
 * `backend/tests/test_pipeline_stage_events.py::test_frontend_fixture_matches_real_block_output`
 * (red there when the backend shape changes). Here every real block must become a
 * `stage` unit carrying the fields the row renders — so a backend rename goes red on
 * one side or the other, never silently renders an empty row.
 */
import { describe, it, expect } from 'vitest';
import fixture from './__fixtures__/pipeline-stage-blocks.json';
import type { ContentBlock, PipelineStageContent } from '../../types';
import { deriveTurnView } from './turnView';

const blocks = fixture as PipelineStageContent[];

describe('deriveTurnView × real backend pipeline_stage blocks', () => {
  it('the fixture is non-empty and every block is a pipeline_stage', () => {
    expect(blocks.length).toBeGreaterThanOrEqual(4);
    expect(blocks.every((b) => b.type === 'pipeline_stage')).toBe(true);
  });

  it.each(blocks.map((b) => [b.runId, b.stage, b] as const))('%s %s → a stage unit', (_r, _s, b) => {
    const tool = { type: 'tool_use', id: 't', name: 'Bash', summary: 'Running: ls', category: 'bash' } as ContentBlock;
    const view = deriveTurnView([tool, b as ContentBlock, tool], false);
    const unit = view.units.find((u) => u.kind === 'stage');
    expect(unit && unit.kind === 'stage' && unit.block).toBe(b);
    // the fields the row and the live label read must be present with the promised types
    expect(typeof b.stage).toBe('string');
    expect(['completed', 'skipped']).toContain(b.status);
    expect(b.ordinal === null || typeof b.ordinal === 'number').toBe(true);
    expect(b.position === null || typeof b.position === 'number').toBe(true);
    expect(typeof b.total).toBe('number');
    expect(b.next === null || typeof b.next === 'string').toBe(true);
    expect(b.gate === null || (typeof b.gate.name === 'string' && typeof b.gate.verdict === 'string')).toBe(true);
  });

  it('a streaming turn after a real block names its next stage', () => {
    const withNext = blocks.find((b) => b.next);
    expect(withNext).toBeTruthy();
    const view = deriveTurnView([withNext as ContentBlock], true);
    expect(view.liveStage?.stage).toBe(withNext!.next);
  });
});
