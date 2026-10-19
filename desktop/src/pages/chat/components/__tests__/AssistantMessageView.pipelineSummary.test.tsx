/**
 * AssistantMessageView × pipeline run documents (backend core/run_docs.py).
 *
 * What is tested (real AssistantMessageView + turnView, only leaf contexts mocked):
 * - `pipeline_summary` is lifted out of the turn's flow (turnView.summary, never a unit)
 * - the run's documents are the output card's deliverables section (chat turn spec
 *   v6 §8), after the outcome: the run's verdict (ready / not ready + why) on the
 *   section line, then one row per document; a present row opens the Canvas
 *   (OPEN_FILE_EVENT with the absolute path), a skipped one says why; REVIEW.md
 *   carries its counts
 * - a run document is listed once: not again as a deliverable tile, never on a stage
 * - not shown while the turn streams; a direct run (no pipeline part) shows none
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { AssistantMessageView } from '../AssistantMessageView';
import { deriveTurnView } from '../../turnView';
import { ToastProvider } from '../../../../contexts/ToastContext';
import { OPEN_FILE_EVENT } from '../../../../components/common/MarkdownRenderer';
import type { Message, ContentBlock } from '../../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, fallback: unknown) => fallback }),
}));
vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({
    health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 },
    triggerHealthCheck: vi.fn(),
  }),
}));

const tu = (id: string): ContentBlock =>
  ({ type: 'tool_use', id, name: 'Bash', input: {}, summary: 'Bash: x', category: 'bash' }) as ContentBlock;
const tr = (id: string): ContentBlock =>
  ({ type: 'tool_result', toolUseId: id, content: 'ok', isError: false, truncated: false }) as ContentBlock;
const tx = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;
const RUN = { type: 'pipeline_run', runId: 'run_s', profile: 'full', stages: ['evaluate', 'deliver'],
  requirement: 'r', at: 'T' } as ContentBlock;
const DOC = { name: 'REQUIREMENTS.md', path: 'Projects/P/.artifacts/runs/run_s/REQUIREMENTS.md',
  absolutePath: '/ws/Projects/P/.artifacts/runs/run_s/REQUIREMENTS.md' };
const stage = (s: string, status: 'completed' | 'in_progress', doc?: typeof DOC): ContentBlock => ({
  type: 'pipeline_stage', runId: 'run_s', stage: s, status, ordinal: 1, position: 1, total: 2, profile: 'full',
  next: 'deliver', nextOrdinal: 9, gate: null, summary: null, at: 'T', ...(doc ? { doc } : {}),
}) as ContentBlock;
const abs = (n: string) => `/ws/Projects/P/.artifacts/runs/run_s/${n}`;
const SUMMARY = (pushReady: boolean | null) => ({
  type: 'pipeline_summary', runId: 'run_s', profile: 'full', pushReady, pushReason: pushReady === false ? 'X1 unmet' : null,
  stagesDone: 8, at: 'T', docs: [
    { name: 'REQUIREMENTS.md', purpose: null, path: 'p', absolutePath: abs('REQUIREMENTS.md'), required: true, reason: null },
    { name: 'DESIGN_SPEC.md', purpose: null, path: null, absolutePath: null, required: false,
      reason: "skipped: profile 'trivial' has no plan stage" },
    { name: 'REVIEW.md', purpose: null, path: 'p', absolutePath: abs('REVIEW.md'), required: true, reason: null,
      stat: { experts: 5, findings: 26, settled: 24 } },
    { name: 'REPORT.md', purpose: null, path: 'p', absolutePath: abs('REPORT.md'), required: true, reason: null },
  ] }) as ContentBlock;
const DELIV = { type: 'turn_deliverables', files: [
  { path: 'REPORT.md', absolutePath: abs('REPORT.md'), kind: 'knowledge', operation: 'written' },
  { path: 'deck.pptx', absolutePath: '/ws/out/deck.pptx', kind: 'content', operation: 'written' },
] } as ContentBlock;

function renderView(content: ContentBlock[], isStreaming = false) {
  const message: Message = { id: 'm1', role: 'assistant', content, timestamp: new Date().toISOString(), isError: false };
  return render(<ToastProvider><AssistantMessageView message={message} isStreaming={isStreaming} /></ToastProvider>);
}
const TURN = (summary: ContentBlock): ContentBlock[] => [
  tx('Running the pipeline.'), tu('a'), tr('a'), RUN, stage('evaluate', 'completed', DOC),
  tu('b'), tr('b'), stage('deliver', 'completed'), summary, tx('Shipped.'), DELIV,
];

describe('pipeline_summary', () => {
  it('is lifted into turnView.summary, never a unit', () => {
    const view = deriveTurnView(TURN(SUMMARY(true)), false);
    expect(view.summary?.runId).toBe('run_s');
    expect(view.units.some((u) => (u as { block?: { type?: string } }).block?.type === 'pipeline_summary')).toBe(false);
  });

  it('renders the verdict and one row per document; a row opens the Canvas', () => {
    renderView(TURN(SUMMARY(true)));
    const card = screen.getByTestId('output-deliverables');
    expect(within(card).getByTestId('summary-verdict').textContent).toBe('· Done · ready to push');
    const rows = within(card).getAllByTestId('summary-doc');
    expect(rows.map((r) => r.getAttribute('data-name'))).toEqual(['REQUIREMENTS.md', 'DESIGN_SPEC.md', 'REVIEW.md', 'REPORT.md']);
    expect(rows[1].getAttribute('data-present')).toBe('false');
    expect(rows[1].textContent).toContain("skipped: profile 'trivial' has no plan stage");
    expect(within(rows[2]).getByTestId('summary-review-stat').textContent).toBe('5 experts · 26 findings · 24 settled');
    const seen: string[] = [];
    const on = (e: Event) => seen.push((e as CustomEvent).detail.path);
    document.addEventListener(OPEN_FILE_EVENT, on);
    fireEvent.click(within(rows[2]).getByRole('button'));
    document.removeEventListener(OPEN_FILE_EVENT, on);
    expect(seen).toEqual([abs('REVIEW.md')]);
    expect(within(rows[1]).queryByRole('button')).toBeNull();
  });

  it('a run not ready to push says so, with why', () => {
    renderView(TURN(SUMMARY(false)));
    const v = screen.getByTestId('summary-verdict');
    expect(v.textContent).toContain('Done · not ready to push');
    expect(v.textContent).toContain('X1 unmet');
  });

  it('a document in the summary is not repeated as a deliverable tile', () => {
    renderView(TURN(SUMMARY(true)));
    const produced = screen.getByTestId('output-deliverables');
    const paths = within(produced).getAllByTestId('deliverable').map((d) => d.getAttribute('data-path'));
    expect(paths).toEqual(['/ws/out/deck.pptx']);
  });

  it('is not shown while the turn streams', () => {
    renderView(TURN(SUMMARY(true)), true);
    expect(screen.queryByTestId('summary-doc')).toBeNull();
  });

  it('a direct run has no run documents in the turn', () => {
    const direct = TURN({ ...(SUMMARY(true) as object), profile: 'direct' } as ContentBlock)
      .map((b) => (b.type === 'pipeline_run' || b.type === 'pipeline_stage' ? { ...b, profile: 'direct' } as ContentBlock : b));
    renderView(direct);
    expect(screen.queryByTestId('summary-doc')).toBeNull();
    expect(screen.queryByTestId('pipeline-start')).toBeNull();
  });
});

describe('stage rows carry no document link', () => {
  it('the documents are only in the output card', () => {
    renderView(TURN(SUMMARY(true)));
    expect(screen.queryByTestId('stage-doc')).toBeNull();
    expect(screen.getAllByTestId('summary-doc').map((r) => r.getAttribute('data-name'))).toContain('REQUIREMENTS.md');
  });
});
