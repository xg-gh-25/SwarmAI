/** Tests Pipeline API snake_case to camelCase mapping. */
import { describe, expect, it, vi } from 'vitest';

const { apiPatch } = vi.hoisted(() => ({ apiPatch: vi.fn() }));
vi.mock('./api', () => ({ default: { patch: apiPatch } }));

import { pipelineToCamelCase, pipelinesService } from './pipelines';

describe('pipelineToCamelCase', () => {
  it('maps the renderer-owned audit card', () => {
    const run = pipelineToCamelCase({
      id: 'run_x', project: 'P', requirement: 'x', status: 'completed',
      progress: '6/6', updated_at: '2026-10-22T00:00:00Z',
      audit_card: 'Run Audit · run_x',
    });
    expect(run.auditCard).toBe('Run Audit · run_x');
  });
});


describe('pipelinesService.resumeRun', () => {
  it('calls the protocol-aware resume endpoint', async () => {
    apiPatch.mockResolvedValueOnce({});
    await pipelinesService.resumeRun('run_v1');
    expect(apiPatch).toHaveBeenCalledWith('/pipelines/run_v1/resume');
  });
});
