/**
 * BrainMapView + BrainHubMap tests — the Map view of one Brain and the Hub map.
 * Services are mocked at the boundary (ddd hooks, pipeline analytics). Checks: the
 * map shows real counts and this brain's Runs only; clicking map parts opens the
 * matching panel or the Browse view; Needs you lists Runs + proposals; the Project
 * Agent submits to chat through onDispatch; unbuilt parts are labelled mock; Eval
 * says UNKNOWN for what nobody measures; the Hub map opens a brain.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const mockAnalytics = vi.fn();
vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({ data: mockAnalytics() }),
}));
const DETAIL = {
  name: 'P', kind: 'knowledge',
  sections: [
    { key: 'knowledge', members: [], entries: [{}, {}], completeNotBroken: false },
    { key: 'delivery', members: [{ path: 'bindings.yaml', gitStatus: 'clean' }], entries: [], completeNotBroken: false },
  ],
};
const REVIEW = {
  last_reviewed_sha: 'a', head_sha: 'abcdef1234', diff_incomplete: false,
  hunks: [{ file: 'Projects/P/K.md', signature: 's1', tag: 'cultivation·auto-applied', diff_text: '' }],
  proposals: [{ id: 'p1', target_doc: 'TECH.md', target_section: 'Gates', content: 'Add a concurrency reviewer', confidence: 0.8, source_run_id: 'run_x' }],
};
vi.mock('../../services/ddd', () => ({
  useBrainDetail: () => ({ data: DETAIL }),
  useReview: () => ({ data: REVIEW }),
}));
vi.mock('../../services/pipelines', () => ({ pipelinesService: { fetchAnalytics: vi.fn() } }));

import { BrainMapView } from './BrainMapView';
import { BrainHubMap } from './BrainHubMap';

const run = (id: string, o: Record<string, unknown> = {}) => ({
  id, requirement: `Req ${id}`, status: 'completed', profile: 'bugfix', progress: '8/8', cycleTimeMin: 10,
  tokensActual: null, createdAt: '', updatedAt: '2026-10-20', pauseKind: null, needsYou: false,
  checkpointReason: null, ownerSessionId: null, verified: true, reportPath: null, auditCard: null, ...o,
});

beforeEach(() => {
  mockAnalytics.mockReturnValue({
    window: '30d', overall: {}, trend: [],
    byProject: [
      { project: 'P', runs: [run('run_1', { needsYou: true, pauseKind: 'decision', status: 'running' }), run('run_2')] },
      { project: 'Other', runs: [run('run_9')] },
    ],
  });
});

describe('BrainMapView', () => {
  it('draws the map with real counts and only this brain\'s Runs', () => {
    render(<BrainMapView name="P" onGoTo={vi.fn()} />);
    expect(screen.getByTestId('brain-map')).toBeTruthy();
    expect(screen.getByTestId('bm-sec-knowledge').textContent).toContain('2 entries');
    expect(screen.getByTestId('bm-brain').textContent).toContain('abcdef1');
    expect(screen.getByTestId('bm-run-run_1')).toBeTruthy();
    expect(screen.queryByTestId('bm-run-run_9')).toBeNull();
    expect(screen.getByTestId('bm-you').textContent).toContain('2');   // 1 Run + 1 proposal
  });

  it('Needs you lists the waiting Run and the proposal; Review goes to the Review view', () => {
    const onGoTo = vi.fn();
    render(<BrainMapView name="P" onGoTo={onGoTo} />);
    expect(screen.getByTestId('bm-need-run_1')).toBeTruthy();
    fireEvent.click(screen.getByTestId('bm-need-p1').querySelector('button')!);
    expect(onGoTo).toHaveBeenCalledWith('review');
  });

  it('clicking map parts opens the matching panel; a section opens Browse', () => {
    const onGoTo = vi.fn();
    render(<BrainMapView name="P" onGoTo={onGoTo} />);
    fireEvent.click(screen.getByTestId('bm-conductor'));
    expect(screen.getByTestId('bm-panel-runs')).toBeTruthy();
    expect(screen.getByTestId('bm-runrow-run_2')).toBeTruthy();
    fireEvent.click(screen.getByTestId('bm-reflect'));
    expect(screen.getByTestId('bm-panel-reflect').textContent).toContain('Add a concurrency reviewer');
    fireEvent.click(screen.getByTestId('bm-eval'));
    expect(screen.getByTestId('bm-panel-eval').textContent).toContain('UNKNOWN');
    fireEvent.click(screen.getByTestId('bm-sec-gates'));
    expect(onGoTo).toHaveBeenCalledWith('browse');
  });

  it('the Project Agent submits a request to chat and marks unbuilt places as mock', async () => {
    const onDispatch = vi.fn(() => true);
    render(<BrainMapView name="P" onGoTo={vi.fn()} onDispatch={onDispatch} />);
    fireEvent.click(screen.getByTestId('bm-agent'));
    expect(screen.getAllByTestId('bm-mock').length).toBeGreaterThan(0);
    const submit = screen.getByTestId('bm-agent-submit') as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    fireEvent.change(screen.getByTestId('bm-agent-req'), { target: { value: 'Add jittered backoff to retries' } });
    fireEvent.click(submit);
    expect(onDispatch).toHaveBeenCalledTimes(1);
    const calls = onDispatch.mock.calls as unknown as string[][];
    expect(calls[0][0]).toContain('Use the pipeline on P');
    await waitFor(() => expect(screen.getByTestId('bm-agent-sent').textContent).toContain('Sent to chat'));
  });
});

describe('BrainMapView · Project Agent definition', () => {
  const AGENT = {
    path: 'project-agent.yaml', status: 'draft', summary: 'Small fixes behind the service layer.',
    defaultProfile: 'bugfix', profileRules: [{ when: 'config-only change', profile: 'direct' }],
    reviewFocus: [{ area: 'concurrency', why: 'refunds settle asynchronously', source: 'TECH.md' }],
    validations: [{ id: 'unit', command: 'pytest -q', source: 'validations.json' }],
    placements: ['chat-tab'], landing: { approval: 'human', via: 'github-pr' }, sources: ['TECH.md'], problems: [],
  };
  const openAgent = (onDispatch = vi.fn(() => true)) => {
    render(<BrainMapView name="P" onGoTo={vi.fn()} onDispatch={onDispatch} />);
    fireEvent.click(screen.getByTestId('bm-agent'));
    return onDispatch;
  };

  it('shows the generated definition with its sources, and confirm goes to chat', () => {
    (DETAIL as Record<string, unknown>).projectAgent = AGENT;
    const onDispatch = openAgent();
    const def = screen.getByTestId('bm-agent-def');
    for (const s of ['bugfix', 'direct', 'concurrency', 'TECH.md', 'pytest -q', 'you approve every landing']) {
      expect(def.textContent).toContain(s);
    }
    expect(screen.getByTestId('bm-agent-status').textContent).toContain('draft');
    fireEvent.click(screen.getByTestId('bm-agent-confirm'));
    const calls = onDispatch.mock.calls as unknown as string[][];
    expect(calls[0][0]).toContain('status: reviewed');
  });

  it('a revise note goes to chat; a reviewed definition has no confirm button', () => {
    (DETAIL as Record<string, unknown>).projectAgent = { ...AGENT, status: 'reviewed' };
    const onDispatch = openAgent();
    expect(screen.queryByTestId('bm-agent-confirm')).toBeNull();
    fireEvent.change(screen.getByTestId('bm-agent-note'), { target: { value: 'use full for API changes' } });
    fireEvent.click(screen.getByTestId('bm-agent-revise'));
    const calls = onDispatch.mock.calls as unknown as string[][];
    expect(calls[0][0]).toContain('use full for API changes');
  });

  it('no definition yet offers to generate it from the DDD', () => {
    (DETAIL as Record<string, unknown>).projectAgent = null;
    const onDispatch = openAgent();
    fireEvent.click(screen.getByTestId('bm-agent-generate'));
    const calls = onDispatch.mock.calls as unknown as string[][];
    expect(calls[0][0]).toContain('P3b');
    delete (DETAIL as Record<string, unknown>).projectAgent;
  });
});

describe('BrainHubMap', () => {
  it('draws every brain and opens one on click', () => {
    const onOpen = vi.fn();
    const b = (name: string, pending = 0) => ({
      name, kind: 'knowledge', lifecycleStage: 'GROW',
      sectionsPresent: { identity: true, knowledge: true, gates: false, capabilities: true, delivery: true, refresher: false },
      health: { sinking: 0, pending, liveRun: false, lastChangeRelative: '--' },
    });
    render(<BrainHubMap brains={[b('A', 2), b('B')] as never} onOpen={onOpen} />);
    expect(screen.getByTestId('brainhub-map-conductor')).toBeTruthy();
    fireEvent.click(screen.getByTestId('brainhub-map-B'));
    expect(onOpen).toHaveBeenCalledWith('B');
    expect(screen.getByTestId('brainhub-map-A').textContent).toContain('2');
    expect(screen.queryByTestId('brainhub-map-born-A')).toBeNull();
  });

  it('only a Brain whose setup is recorded as setting-up is marked as being born', () => {
    const mk = (name: string, setupState: unknown, lifecycleStage = 'GROW') => ({ name, kind: 'knowledge', lifecycleStage, setupState,
      sectionsPresent: { identity: true, knowledge: true, gates: false, capabilities: true, delivery: false, refresher: false },
      health: { sinking: 0, pending: 0, liveRun: false, lastChangeRelative: '--' } });
    // GROW but still setting up (knowledge written, P6 not passed) -> born;
    // ready -> not; an old Brain with no record, even at CREATE -> not.
    render(<BrainHubMap brains={[mk('New', 'setting-up'), mk('Done', 'ready'), mk('Old', null, 'CREATE')] as never} onOpen={vi.fn()} />);
    expect(screen.getByTestId('brainhub-map-born-New')).toBeTruthy();
    expect(screen.queryByTestId('brainhub-map-born-Done')).toBeNull();
    expect(screen.queryByTestId('brainhub-map-born-Old')).toBeNull();
  });
});
