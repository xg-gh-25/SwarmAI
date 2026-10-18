/**
 * AssistantMessageView × TurnCards — the backend cards' fixed places (chat v7.1).
 *
 * What is tested (real AssistantMessageView + turnView, only leaf contexts mocked):
 * - pipeline_run: rendered once at the top as the alignment (requirement as plain
 *   text, run id, profile, stage count); the model's own opening text then loses
 *   the "Understanding" label and no "no restatement" note shows
 * - pipeline_attention: right after the Result divider and before the answer once
 *   the turn is over; with the DELIVER row while it streams; one item per kind;
 *   a blocking verdict is red
 * - turn_deliverables: after the answer, one tile per file (deduped across rows),
 *   middle-ellipsis names keep the extension, Open dispatches OPEN_FILE_EVENT
 *   with the absolute path, Copy copies it; a product is not repeated in
 *   "This turn changed"
 * - none of the three splits a process segment or hides an unrecovered failure
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { AssistantMessageView } from '../AssistantMessageView';
import { middleEllipsis } from '../TurnCards';
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
const copySpy = vi.fn(async () => true);
vi.mock('../../../../utils/clipboard', () => ({ copyToClipboard: (s: string) => copySpy(s) }));

const tu = (id: string, name: string, input: Record<string, unknown> = {}): ContentBlock =>
  ({ type: 'tool_use', id, name, input, summary: `${name}: x`, category: 'bash' }) as ContentBlock;
const tr = (id: string, isError = false): ContentBlock =>
  ({ type: 'tool_result', toolUseId: id, content: 'ok', isError, truncated: false }) as ContentBlock;
const tx = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;
const wr = (id: string, p: string): ContentBlock =>
  ({ type: 'tool_use', id, name: 'Edit', summary: `Writing to ${p}`, category: 'write' }) as ContentBlock;
const RUN = { type: 'pipeline_run', runId: 'run_a1', profile: 'full', stages: ['evaluate', 'build', 'deliver'],
  requirement: 'Add opus-5-5 to MODEL_REGISTRY', at: 'T' } as ContentBlock;
const stage = (s: string, position: number): ContentBlock => ({
  type: 'pipeline_stage', runId: 'run_a1', stage: s, status: 'completed', ordinal: position, position, total: 3,
  profile: 'full', next: s === 'deliver' ? null : 'deliver', nextOrdinal: 3, gate: null, summary: 'done', at: 'T',
}) as ContentBlock;
const ATTN = { type: 'pipeline_attention', runId: 'run_a1', more: 0, at: 'T', items: [
  { kind: 'self_answered', id: 'Q1', question: 'Where?', answer: 'prompt_builder', reason: 'covers stored configs' },
  { kind: 'assumption', id: 'FR-3', statement: 'eval later' },
  { kind: 'gate', name: 'GATE 1', verdict: 'BLOCK', detail: null },
  { kind: 'not_push_ready', reasons: ['tests: 1 failed'] },
] } as ContentBlock;
const ATTN_GATE = { kind: 'gate', name: 'GATE 0', verdict: 'WRONG-FRAME', detail: null };
const deliv = (...paths: string[]): ContentBlock => ({ type: 'turn_deliverables', files: paths.map((p) => (
  { path: p, absolutePath: `/ws/${p}`, kind: 'content', operation: 'written' })) }) as ContentBlock;

function renderView(content: ContentBlock[], isStreaming = false) {
  const message: Message = { id: 'm1', role: 'assistant', content, timestamp: new Date().toISOString(), isError: false };
  return render(<ToastProvider><AssistantMessageView message={message} isStreaming={isStreaming} /></ToastProvider>);
}
const order = (container: HTMLElement, ...ids: string[]) => {
  const all = Array.from(container.querySelectorAll('[data-testid]')).map((e) => e.getAttribute('data-testid'));
  return ids.map((id) => all.indexOf(id));
};

const PIPELINE: ContentBlock[] = [
  tx('I will run the pipeline.'), tu('a', 'Bash'), tr('a'), RUN, stage('evaluate', 1),
  tu('b', 'Bash'), tr('b'), stage('build', 2), tu('c', 'Bash'), tr('c'), stage('deliver', 3), ATTN,
  tx('Shipped: opus-5-5 is in the registry.'), deliv('docs/report.md', 'out/deck.pptx'),
];

describe('pipeline_run — the alignment', () => {
  it('renders once at the top; the opening text is not labelled again', () => {
    const { container } = renderView(PIPELINE);
    const run = screen.getByTestId('turn-run');
    expect(run.textContent).toContain('Add opus-5-5 to MODEL_REGISTRY');
    expect(run.textContent).toContain('run_a1');
    expect(run.textContent).toContain('full · 3 stages');
    expect(screen.getAllByText('Understanding')).toHaveLength(1);
    expect(screen.queryByTestId('turn-alignment')).toBeNull();
    expect(container.querySelector('[data-testid]')?.getAttribute('data-testid')).toBe('turn-run');
  });

  it('the running stage names the next planned stages from the run', () => {
    const run = { ...RUN, stages: ['evaluate', 'think', 'plan', 'build', 'deliver'] } as ContentBlock;
    const ev = { ...stage('evaluate', 1), next: 'think', nextOrdinal: 2, total: 5 } as ContentBlock;
    renderView([tx('Go.'), tu('a', 'Bash'), tr('a'), run, ev, tu('b', 'Bash')], true);
    const live = screen.getByTestId('pipeline-stage-live');
    expect(within(live).getByTestId('stage-upcoming').textContent).toBe(' · next PLAN → BUILD');
    // without the run's plan there is nothing to say
    renderView([tx('Go.'), tu('a', 'Bash'), tr('a'), ev, tu('b', 'Bash')], true);
    expect(screen.getAllByTestId('pipeline-stage-live')[1].querySelector('[data-testid="stage-upcoming"]')).toBeNull();
  });

  it('the last planned stage has no "next"', () => {
    const run = { ...RUN, stages: ['evaluate', 'think'] } as ContentBlock;
    const ev = { ...stage('evaluate', 1), next: 'think', nextOrdinal: 2, total: 2 } as ContentBlock;
    renderView([tu('a', 'Bash'), tr('a'), run, ev, tu('b', 'Bash')], true);
    expect(screen.queryByTestId('stage-upcoming')).toBeNull();
  });

  it('a run block suppresses the no-restatement note', () => {
    renderView([tu('a', 'Bash'), tr('a'), RUN, tu('b', 'Bash'), tr('b'), tu('c', 'Bash'), tr('c'), tx('ok')]);
    expect(screen.getByTestId('turn-run')).toBeTruthy();
    expect(screen.queryByTestId('turn-no-alignment')).toBeNull();
  });
});

describe('pipeline_attention — what needs the user', () => {
  it('leads the result: after the divider, before the answer', () => {
    const { container } = renderView(PIPELINE);
    const result = screen.getByTestId('turn-result');
    const card = within(result).getByTestId('turn-attention');
    expect(result.textContent!.indexOf('Result')).toBeLessThan(result.textContent!.indexOf('For you to check'));
    expect(result.textContent!.indexOf('For you to check')).toBeLessThan(result.textContent!.indexOf('Shipped'));
    expect(card.textContent).toContain('For you to check · 4');
    expect(within(card).getAllByTestId('attention-item').map((e) => e.getAttribute('data-kind')))
      .toEqual(['self_answered', 'assumption', 'gate', 'not_push_ready']);
    expect(card.textContent).toContain('→ prompt_builder');
    expect(within(card).getByText('BLOCK').className).toContain('text-red-500');
    // What Swarm settled without asking is ONE group under ONE name line.
    const decided = within(card).getByTestId('attention-decided');
    expect(within(decided).getAllByTestId('attention-item').map((e) => e.getAttribute('data-kind')))
      .toEqual(['self_answered', 'assumption']);
    expect(decided.textContent).toContain('Swarm decided for you · 2');
    expect(decided.textContent).toContain('Why: covers stored configs');
    expect(decided.textContent).toContain('Assumed FR-3');
    expect(card.textContent!.match(/Swarm/g)).toHaveLength(1);
    expect(screen.getAllByTestId('turn-attention')).toHaveLength(1);
    expect(order(container, 'turn-attention', 'turn-deliverables')[0]).toBeLessThan(
      order(container, 'turn-attention', 'turn-deliverables')[1]);
  });

  it('while streaming it stays with the DELIVER row', () => {
    const live = [...PIPELINE.slice(0, 12), tu('d', 'Bash')];
    renderView(live, true);
    const card = screen.getByTestId('turn-attention');
    expect(screen.queryByTestId('turn-result')).toBeNull();
    // right after the deliver section, before the work that follows it
    const rows = screen.getAllByTestId('work-group');
    expect(card.compareDocumentPosition(rows[rows.length - 1]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('no name line when Swarm settled nothing', () => {
    renderView([tu('a', 'Bash'), tr('a'), { ...ATTN, items: [ATTN_GATE] } as ContentBlock, tx('done')]);
    expect(screen.getByTestId('turn-attention').textContent).not.toContain('Swarm');
    expect(screen.queryByTestId('attention-decided')).toBeNull();
  });

  it('an empty item list renders nothing', () => {
    renderView([tu('a', 'Bash'), tr('a'), { ...ATTN, items: [] } as ContentBlock, tx('done')]);
    expect(screen.queryByTestId('turn-attention')).toBeNull();
  });
});

describe('turn_deliverables — what the turn produced', () => {
  it('one tile per file, after the answer, deduped across rows', () => {
    renderView([...PIPELINE, deliv('docs/report.md')]);
    const card = screen.getByTestId('turn-deliverables');
    expect(card.textContent).toContain('Produced this turn · 2');
    expect(within(card).getAllByTestId('deliverable').map((e) => e.getAttribute('data-path')))
      .toEqual(['/ws/docs/report.md', '/ws/out/deck.pptx']);
    const result = screen.getByTestId('turn-result');
    expect(result.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('Open dispatches OPEN_FILE_EVENT with the absolute path; Copy copies it', async () => {
    const seen: unknown[] = [];
    const on = (e: Event) => seen.push((e as CustomEvent).detail);
    document.addEventListener(OPEN_FILE_EVENT, on);
    renderView([tu('a', 'Bash'), tr('a'), tx('done'), deliv('out/deck.pptx')]);
    fireEvent.click(screen.getByText('Open in Canvas'));
    document.removeEventListener(OPEN_FILE_EVENT, on);
    expect(seen).toEqual([{ path: '/ws/out/deck.pptx' }]);
    fireEvent.click(screen.getByText('Copy path'));
    expect(copySpy).toHaveBeenCalledWith('/ws/out/deck.pptx');
    expect(await screen.findByText('Copied')).toBeTruthy();
  });

  it('an image gets a thumbnail; other files a type icon', () => {
    const { container } = renderView([tu('a', 'Bash'), tr('a'), tx('done'), deliv('out/chart.png', 'out/a.md')]);
    const [img, md] = screen.getAllByTestId('deliverable');
    expect(img.querySelector('img')?.getAttribute('src')).toContain(encodeURIComponent('/ws/out/chart.png'));
    expect(md.querySelector('img')).toBeNull();
    expect(md.querySelector('.material-symbols-outlined')).toBeTruthy();
    expect(container).toBeTruthy();
  });

  it('a product written by a step is not repeated in "This turn changed"', () => {
    renderView([
      wr('w', '/ws/docs/report.md'), tr('w'), wr('e', '/ws/backend/x.py'), tr('e'),
      tx('done'), deliv('docs/report.md'),
    ]);
    const changed = screen.getByTestId('turn-files');
    expect(within(changed).getAllByTestId('file-chip').map((e) => e.getAttribute('data-path'))).toEqual(['/ws/backend/x.py']);
  });

  it('read-only preview (History): products and file chips show but do not act', () => {
    const seen: unknown[] = [];
    const on = (e: Event) => seen.push((e as CustomEvent).detail);
    document.addEventListener(OPEN_FILE_EVENT, on);
    const message: Message = { id: 'm1', role: 'assistant', timestamp: new Date().toISOString(), isError: false,
      content: [wr('w', '/ws/backend/x.py'), tr('w'), tx('done'), deliv('out/deck.pptx')] };
    render(<ToastProvider><AssistantMessageView message={message} readOnly /></ToastProvider>);
    const tile = screen.getByTestId('deliverable');
    expect(tile.querySelector('button')).toBeNull();
    expect(screen.queryByText('Open in Canvas')).toBeNull();
    expect(screen.queryByText('Copy path')).toBeNull();
    fireEvent.click(tile.firstElementChild!);
    const chips = screen.getAllByTestId('file-chip');
    expect(chips.length).toBeGreaterThan(0);
    chips.forEach((c) => { expect(c.tagName).toBe('SPAN'); fireEvent.click(c); });
    document.removeEventListener(OPEN_FILE_EVENT, on);
    expect(seen).toEqual([]);
    expect(chips[0].getAttribute('title')).toBe('/ws/backend/x.py');
  });

  it('keeps the extension when the name is shortened', () => {
    expect(middleEllipsis('short.md')).toBe('short.md');
    const s = middleEllipsis('2026-10-18-quarterly-business-review-final-v3.pptx');
    expect(s.endsWith('.pptx')).toBe(true);
    expect(s.length).toBe(28);
    expect(s).toContain('…');
    expect(middleEllipsis('2026-10-18-review-v3.pptx', 10)).toBe('202…3.pptx');
  });
});

describe('display blocks never shape the flow', () => {
  it('a run block mid-work does not split the segment', () => {
    const plain = deriveTurnView([tu('a', 'Bash'), tr('a'), tu('b', 'Bash'), tr('b'), tx('ok')], false);
    const withRun = deriveTurnView([tu('a', 'Bash'), tr('a'), RUN, tu('b', 'Bash'), tr('b'), tx('ok')], false);
    expect(withRun.units.map((u) => u.kind)).toEqual(plain.units.map((u) => u.kind));
    expect(withRun.run?.runId).toBe('run_a1');
  });

  it('a trailing deliverables block does not hide an unrecovered failure', () => {
    const v = deriveTurnView([tu('a', 'Bash'), tr('a', true), deliv('out/a.md')], false);
    const proc = v.units.find((u) => u.kind === 'process');
    expect(proc && proc.kind === 'process' && proc.unrecoveredFailure?.block.id).toBe('a');
    expect(v.deliverables).toHaveLength(1);
  });
});
