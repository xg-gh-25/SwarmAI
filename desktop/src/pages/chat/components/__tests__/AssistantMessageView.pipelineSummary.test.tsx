/**
 * AssistantMessageView × a pipeline run's ending (design
 * docs/design/2026-10-26-turn-cards-unified.md §5, §5.1; acceptance tests 3, 3a).
 *
 * What is tested (real AssistantMessageView + turnView, only leaf contexts mocked):
 * - `pipeline_summary` is lifted out of the turn's flow (turnView.summary, never a unit)
 * - no run-result card and no attention card ("not ready to push" / "needs your
 *   judgement"), with or without a `pipeline_attention` block: one grey run line
 *   `<run id> · <status> · landed <sha> | not landed · N documents`
 * - its details are expanded by default for a landed, a not-landed and an abandoned
 *   run: Asked (intent), Not landed (only when not landed), the five audit rows
 *   (verdict coloured only for WARN / BLOCK), missing REQUIRED documents, and a link
 *   to REPORT.md; never solved, never REVIEW stats; an older block without audit rows
 *   shows the backend's audit card text; clicking the line folds them
 * - the run's present documents are rows in the one DELIVERABLES box (the shared
 *   ItemRow), opening the Canvas; a skipped one is not listed; a document is listed
 *   once there (not again as a deliverable tile)
 * - a completed stage's document is the same row under the stage line: no excerpt,
 *   no Show full text, the row opens the Canvas
 * - nothing while the turn streams
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
vi.mock('../../../../services/api', () => ({ default: { get: vi.fn() } }));
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
const abs = (n: string) => `/ws/Projects/P/.artifacts/runs/run_s/${n}`;
const DOC = { name: 'REQUIREMENTS.md', path: 'Projects/P/.artifacts/runs/run_s/REQUIREMENTS.md',
  absolutePath: abs('REQUIREMENTS.md') };
const stage = (s: string, status: 'completed' | 'in_progress', extra: Record<string, unknown> = {}): ContentBlock => ({
  type: 'pipeline_stage', runId: 'run_s', stage: s, status, ordinal: 1, position: 1, total: 2, profile: 'full',
  next: 'deliver', nextOrdinal: 9, gate: null, summary: null, at: 'T', ...extra,
}) as ContentBlock;
const AUDIT = [
  { dim: 'Run Isolation', verdict: 'PASS', detail: '1 of 1 repo isolated' },
  { dim: 'Speed', verdict: 'PASS', detail: '42m wall' },
  { dim: 'Quality', verdict: 'BLOCK', detail: 'X1 exit criterion unmet' },
  { dim: 'Autonomy', verdict: 'PASS', detail: '0 decisions' },
  { dim: 'Token Cost', verdict: 'WARN', detail: 'over the reference p95 by 18%' },
];
const SUMMARY = (landing: string | null, extra: Record<string, unknown> = {}) => ({
  type: 'pipeline_summary', runId: 'run_s', profile: 'full', status: 'completed', landing,
  notLandedReason: landing === 'not_landed' ? 'live test not run' : null,
  landedSha: landing === 'landed' ? '5d941e20aa11' : null,
  intent: 'fix the 3 red tests on main', solved: { before: 'red', now: 'green' },
  stagesDone: 8, at: 'T', audit: AUDIT, auditCard: 'Run Audit · run_s', docs: [
    { name: 'REQUIREMENTS.md', purpose: null, path: 'p', absolutePath: abs('REQUIREMENTS.md'), required: true, reason: null },
    { name: 'DESIGN_SPEC.md', purpose: null, path: null, absolutePath: null, required: false,
      reason: 'not written by this run' },
    { name: 'REVIEW.md', purpose: null, path: 'p', absolutePath: abs('REVIEW.md'), required: true, reason: null,
      stat: { experts: 5, findings: 26, settled: 24 } },
    { name: 'REPORT.md', purpose: null, path: 'p', absolutePath: abs('REPORT.md'), required: true, reason: null },
  ], ...extra }) as unknown as ContentBlock;
const DELIV = { type: 'turn_deliverables', files: [
  { path: 'REPORT.md', absolutePath: abs('REPORT.md'), kind: 'knowledge', operation: 'written' },
  { path: 'deck.pptx', absolutePath: '/ws/out/deck.pptx', kind: 'content', operation: 'written' },
] } as ContentBlock;
const ATTN = { type: 'pipeline_attention', runId: 'run_s', at: 'T', more: 0, items: [
  { kind: 'not_landed', reasons: ['live test not run'] },
  { kind: 'gate', name: 'X1', verdict: 'BLOCK', detail: 'unmet' }] } as ContentBlock;

function renderView(content: ContentBlock[], isStreaming = false) {
  const message: Message = { id: 'm1', role: 'assistant', content, timestamp: new Date().toISOString(), isError: false };
  return render(<ToastProvider><AssistantMessageView message={message} isStreaming={isStreaming} /></ToastProvider>);
}
const TURN = (summary: ContentBlock): ContentBlock[] => [
  tx('Running the pipeline.'), tu('a'), tr('a'), RUN, stage('evaluate', 'completed', { doc: DOC, docs: [DOC] }),
  tu('b'), tr('b'), stage('deliver', 'completed'), summary, tx('Shipped.'), DELIV,
];
const opened = (act: () => void) => {
  const seen: string[] = [];
  const on = (e: Event) => seen.push((e as CustomEvent).detail.path);
  document.addEventListener(OPEN_FILE_EVENT, on);
  act();
  document.removeEventListener(OPEN_FILE_EVENT, on);
  return seen;
};

describe('a pipeline run ends in one grey line (test 3)', () => {
  it('is lifted into turnView.summary, never a unit', () => {
    const view = deriveTurnView(TURN(SUMMARY('landed')), false);
    expect(view.summary?.runId).toBe('run_s');
    expect(view.units.some((u) => (u as { block?: { type?: string } }).block?.type === 'pipeline_summary')).toBe(false);
  });

  it('no run-result and no attention card, even with an attention block', () => {
    renderView([...TURN(SUMMARY('not_landed')), ATTN]);
    expect(screen.queryByTestId('run-result')).toBeNull();
    expect(screen.queryByTestId('turn-attention')).toBeNull();
    expect(screen.queryByTestId('summary-verdict')).toBeNull();
    expect(document.body.textContent).not.toMatch(/ready to push|For you to check|Needs your judgement/i);
  });

  it('the line: run id, status, where it landed, how many documents; grey, no frame', () => {
    renderView(TURN(SUMMARY('landed')));
    const line = screen.getByTestId('run-line');
    expect(line.textContent).toContain('run_s · completed · landed 5d941e20 · 3 documents');
    expect(line.className).toContain('text-[var(--color-text-dim)]');
    expect(line.className).not.toMatch(/\bborder\b|rounded-|amber|bg-/);
    renderView(TURN(SUMMARY('not_landed')));
    expect(screen.getAllByTestId('run-line')[1].textContent).toContain('run_s · completed · not landed · 3 documents');
  });

  it('present documents are rows in the one DELIVERABLES box, listed once; a skipped one is not', () => {
    renderView(TURN(SUMMARY('landed')));
    const box = within(screen.getByTestId('output-deliverables')).getByTestId('product-box');
    const paths = within(box).getAllByTestId('deliverable').map((d) => d.getAttribute('data-path'));
    expect(paths).toEqual([abs('REQUIREMENTS.md'), abs('REVIEW.md'), abs('REPORT.md'), '/ws/out/deck.pptx']);
    for (const row of within(box).getAllByTestId('deliverable')) expect(row.hasAttribute('data-item-row')).toBe(true);
    expect(box.textContent).not.toContain('DESIGN_SPEC.md');
    expect(screen.queryByTestId('summary-review-stat')).toBeNull();
    expect(screen.queryByTestId('summary-doc')).toBeNull();
    expect(box.textContent).toContain('4 deliverables');
    const row = within(box).getAllByTestId('deliverable')[1];
    expect(opened(() => fireEvent.click(row))).toEqual([abs('REVIEW.md')]);
  });

  it('nothing while the turn streams', () => {
    renderView(TURN(SUMMARY('landed')), true);
    expect(screen.queryByTestId('run-line')).toBeNull();
  });
});

describe('the run line\'s details, expanded by default (test 3a)', () => {
  it.each([
    ['landed', SUMMARY('landed')],
    ['not landed', SUMMARY('not_landed')],
    ['abandoned', SUMMARY('not_landed', { status: 'abandoned' })],
  ])('%s: open on first render', (_name, sum) => {
    renderView(TURN(sum));
    const line = screen.getByTestId('run-line');
    expect(line.getAttribute('aria-expanded')).toBe('true');
    const details = screen.getByTestId('run-details');
    expect(within(details).getByTestId('run-intent').textContent).toContain('fix the 3 red tests on main');
    expect(within(details).getAllByTestId('run-audit-row')).toHaveLength(5);
  });

  it('the not-landed reason only when it did not land', () => {
    renderView(TURN(SUMMARY('not_landed')));
    expect(screen.getByTestId('run-not-landed').textContent).toContain('live test not run');
    renderView(TURN(SUMMARY('landed', { notLandedReason: 'stale reason' })));
    expect(screen.getAllByTestId('run-details')[1].querySelector('[data-testid="run-not-landed"]')).toBeNull();
  });

  it('audit rows: name, verdict, detail; only WARN and BLOCK are coloured', () => {
    renderView(TURN(SUMMARY('landed')));
    const rows = screen.getAllByTestId('run-audit-row');
    expect(rows.map((r) => r.getAttribute('data-dim'))).toEqual(AUDIT.map((a) => a.dim));
    const verdict = (i: number) => rows[i].querySelector('[data-testid="run-audit-verdict"]')!;
    expect(verdict(2).textContent).toBe('BLOCK');
    expect(verdict(2).className).toContain('--color-status-error');
    expect(verdict(4).className).toContain('--color-status-warning');
    expect(verdict(0).className).toContain('--color-text-dim');
    expect(verdict(0).className).not.toMatch(/status-/);
    expect(rows[4].textContent).toContain('over the reference p95 by 18%');
    expect(screen.queryByTestId('run-audit-card')).toBeNull();
  });

  it('an older block without audit rows shows the backend audit card text', () => {
    renderView(TURN(SUMMARY('landed', { audit: undefined, auditCard: 'Run Audit · run_s\nSpeed PASS' })));
    expect(screen.getByTestId('run-audit-card').textContent).toBe('Run Audit · run_s\nSpeed PASS');
  });

  it('missing REQUIRED documents are named; an optional skipped one is not', () => {
    const docs = (SUMMARY('landed') as unknown as { docs: Record<string, unknown>[] }).docs
      .map((d) => (d.name === 'REVIEW.md' ? { ...d, absolutePath: null, path: null, reason: 'not written by this run' } : d));
    renderView(TURN(SUMMARY('landed', { docs })));
    const missing = screen.getByTestId('run-missing');
    expect(missing.textContent).toContain('REVIEW.md');
    expect(missing.textContent).toContain('not written by this run');
    expect(missing.textContent).not.toContain('DESIGN_SPEC.md');
    renderView(TURN(SUMMARY('landed')));
    expect(screen.getAllByTestId('run-details')[1].querySelector('[data-testid="run-missing"]')).toBeNull();
  });

  it('a REPORT.md link opens it; no solved and no review stats', () => {
    renderView(TURN(SUMMARY('landed')));
    expect(opened(() => fireEvent.click(screen.getByTestId('run-report-link')))).toEqual([abs('REPORT.md')]);
    const details = screen.getByTestId('run-details');
    expect(details.textContent).not.toMatch(/Before|Now|green|experts/);
  });

  it('clicking the line folds the details', () => {
    renderView(TURN(SUMMARY('landed')));
    fireEvent.click(screen.getByTestId('run-line'));
    expect(screen.getByTestId('run-line').getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByTestId('run-details')).toBeNull();
  });
});

describe('a completed stage\'s document is the same row', () => {
  it('under the stage line: the shared row, no excerpt, no Show full text; it opens the Canvas', () => {
    const turn = [tx('Running.'), tu('a'), tr('a'), RUN,
      stage('evaluate', 'completed', { summary: 'GO', docs: [{ ...DOC, excerpt: '## R\nShip the cards.' }] }),
      tu('b'), tr('b'), stage('deliver', 'in_progress')];
    renderView(turn);
    const row = screen.getByTestId('stage-doc');
    expect(row.getAttribute('data-name')).toBe('REQUIREMENTS.md');
    expect(row.hasAttribute('data-item-row')).toBe(true);
    expect(row.closest('[data-item-box]')).toBeTruthy();
    expect(screen.getByTestId('stage-summary').textContent).toBe('GO');
    expect(document.body.textContent).not.toContain('Ship the cards.');
    expect(screen.queryByTestId('stage-doc-toggle')).toBeNull();
    expect(screen.queryByText('Show full text')).toBeNull();
    expect(opened(() => fireEvent.click(row))).toEqual([DOC.absolutePath]);
  });

  it('a document on its stage is not repeated as a deliverable tile', () => {
    const tiles = { type: 'turn_deliverables', files: [
      { path: 'REQUIREMENTS.md', absolutePath: DOC.absolutePath, kind: 'knowledge', operation: 'written' },
      { path: 'deck.pptx', absolutePath: '/ws/out/deck.pptx', kind: 'content', operation: 'written' },
    ] } as ContentBlock;
    renderView([tx('x'), tu('a'), tr('a'), RUN, stage('evaluate', 'completed', { docs: [DOC] }),
      tu('b'), tr('b'), stage('deliver', 'completed'), tiles]);
    expect(screen.getAllByTestId('deliverable').map((d) => d.getAttribute('data-path'))).toEqual(['/ws/out/deck.pptx']);
  });
});
