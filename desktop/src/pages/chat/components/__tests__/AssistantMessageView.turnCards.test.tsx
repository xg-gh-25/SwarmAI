/**
 * AssistantMessageView × TurnCards — the backend cards' fixed places (chat v7.1).
 *
 * What is tested (real AssistantMessageView + turnView, only leaf contexts mocked):
 * - pipeline_run: never rendered as text (no requirement card, no "Intent" label)
 * - pipeline_attention: immediate action before Output; self-answered decisions and
 *   assumptions in a folded disclosure after Output, beside Next steps
 * - turn_deliverables: the output section after the answer, one compact row per
 *   file (deduped across rows), middle-ellipsis names keep the extension, Open dispatches OPEN_FILE_EVENT with the absolute path, Copy copies it; a product
 *   is not listed again as a changed file (turn_changes)
 * - none of the three splits a process segment or hides an unrecovered failure
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { AssistantMessageView } from '../AssistantMessageView';
import { middleEllipsis, shortFolder } from '../TurnCards';
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
  return render(<ToastProvider><AssistantMessageView message={message} isStreaming={isStreaming} isLastAssistant /></ToastProvider>);
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

describe('pipeline_run — never rendered as text (chat display v8)', () => {
  it('no requirement card and no Intent label; the plan only drives the pipeline block', () => {
    renderView(PIPELINE);
    expect(screen.queryByText(/Add opus-5-5 to MODEL_REGISTRY/)).toBeNull();
    expect(screen.queryAllByText('Intent')).toHaveLength(0);
  });

  it('a run block suppresses the no-restatement note', () => {
    renderView([tu('a', 'Bash'), tr('a'), RUN, tu('b', 'Bash'), tr('b'), tu('c', 'Bash'), tr('c'), tx('ok')]);
    expect(screen.queryByTestId('turn-no-alignment')).toBeNull();
  });
});

describe('pipeline_attention — what needs the user', () => {
  it('puts action before Output and folded assumptions immediately before proposed next steps', () => {
    const withNext = PIPELINE.map((block) => block.type === 'text' && block.text.startsWith('Shipped:')
      ? tx(`${block.text}\n\n\`\`\`next-steps\nVerify the assumption\n\`\`\``)
      : block);
    const { container } = renderView(withNext);
    const result = screen.getByTestId('turn-result');
    const action = screen.getByTestId('turn-attention');
    const assumptions = screen.getByTestId('turn-assumptions');
    expect(within(result).queryByTestId('turn-attention')).toBeNull();
    expect(within(action).getAllByTestId('attention-item').map((e) => e.getAttribute('data-kind')))
      .toEqual(['gate', 'not_push_ready']);
    expect(action.textContent).toContain('For you to check');
    expect(action.textContent).not.toContain('For you to check ·');
    expect(within(action).getByText('BLOCK').className).toContain('text-red-500');
    expect(assumptions.getAttribute('open')).toBeNull();
    expect(within(assumptions).getAllByTestId('attention-item').map((e) => e.getAttribute('data-kind')))
      .toEqual(['self_answered', 'assumption']);
    expect(assumptions.textContent).toContain('Assumptions to verify');
    expect(assumptions.textContent).toContain('→ prompt_builder');
    expect(assumptions.textContent).not.toContain('Q1');
    expect(assumptions.textContent).not.toContain('FR-3');
    expect(order(container, 'turn-attention', 'output-card', 'turn-assumptions', 'next-steps'))
      .toEqual([expect.any(Number), expect.any(Number), expect.any(Number), expect.any(Number)]);
    const positions = order(container, 'turn-attention', 'output-card', 'turn-assumptions', 'next-steps');
    expect(positions[0]).toBeLessThan(positions[1]);
    expect(positions[1]).toBeLessThan(positions[2]);
    expect(positions[2]).toBeLessThan(positions[3]);
  });

  it('while streaming it shows action after the work so far, and defers assumptions to their final slot', () => {
    const live = [...PIPELINE.slice(0, 12), tu('d', 'Bash')];
    renderView(live, true);
    const card = screen.getByTestId('turn-attention');
    expect(screen.queryByTestId('turn-assumptions')).toBeNull();
    expect(screen.queryByTestId('turn-result')).toBeNull();
    const stages = screen.getAllByTestId('zone-stage');
    expect(stages[stages.length - 1].compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('verification-only attention creates no action card', () => {
    const verificationOnly = { ...ATTN, items: [
      { kind: 'self_answered', id: 'Q1', question: 'Where?', answer: 'prompt_builder', reason: 'covers stored configs' },
      { kind: 'assumption', id: 'FR-3', statement: 'eval later' },
    ] } as ContentBlock;
    renderView([tu('a', 'Bash'), tr('a'), verificationOnly, tx('done')]);
    expect(screen.queryByTestId('turn-attention')).toBeNull();
    expect(screen.getByTestId('turn-assumptions')).toBeTruthy();
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

  it('an older not_push_ready item shows its reasons with no push verdict', () => {
    const item = { kind: 'not_push_ready', reasons: ['Tests failed'] };
    renderView([tu('a', 'Bash'), tr('a'), { ...ATTN, items: [item] } as ContentBlock, tx('done')]);
    const card = screen.getByTestId('turn-attention');
    expect(card.textContent).toContain('Tests failed');
    expect(card.textContent).not.toMatch(/push/i);
  });

  it('not landed is amber, one reason per line, with where the details are', () => {
    const item = { kind: 'not_landed', reasons: ['No smoke test ran the changed code', 'Tests failed'], detail: 'Details: REPORT §10' };
    renderView([tu('a', 'Bash'), tr('a'), { ...ATTN, items: [item] } as ContentBlock, tx('done')]);
    const card = screen.getByTestId('turn-attention');
    const label = within(card).getByText('Not landed');
    expect(label.className).toContain('text-amber');
    expect(label.className).not.toContain('text-red');
    expect(within(within(card).getByTestId('not-ready-reasons')).getAllByRole('listitem').map((e) => e.textContent))
      .toEqual(['No smoke test ran the changed code', 'Tests failed']);
    expect(card.textContent).toContain('Details: REPORT §10');
  });
});

describe('turn_deliverables — what the turn produced', () => {
  // 2026-10-26 (Gawan): the deliverables are the turn's main result: one card per file,
  // the name a link, the folder under it, the actions right next to it (was: one
  // compact borderless row with the actions at the far edge).
  it('one card per file, after the answer, deduped across rows', () => {
    renderView([...PIPELINE, deliv('docs/report.md')]);
    const output = screen.getByTestId('output-card');
    const card = screen.getByTestId('output-deliverables');
    expect(output.className).not.toMatch(/border|rounded|overflow-hidden/);
    expect(card.textContent).toContain('2 deliverables');
    const rows = within(card).getAllByTestId('deliverable');
    expect(rows.map((e) => e.getAttribute('data-path')))
      .toEqual(['/ws/docs/report.md', '/ws/out/deck.pptx']);
    // v3 (2026-10-26, Gawan): ONE bordered box holds every file, its title inside it;
    // a row is a line in that box, not a card of its own
    const box = within(card).getByTestId('product-box');
    expect(box.className).toMatch(/\bborder\b/);
    expect(box.className).toMatch(/rounded/);
    expect(box.className).toContain('w-fit');
    expect(box.textContent).toContain('2 deliverables');
    expect(rows.every((row) => box.contains(row) && !/\bborder\b|rounded/.test(row.className))).toBe(true);
    // one file per line: a column, never a grid
    expect(rows[0].parentElement!.className).toContain('flex-col');
    expect(rows[0].parentElement!.className).not.toContain('grid');
    const name = within(rows[1]).getByTestId('deliverable-name');
    expect(name.textContent).toBe('deck.pptx');
    expect(name.className).toContain('text-primary');
    expect(name.className).not.toMatch(/font-semibold|font-bold/);
    const dir = within(rows[1]).getByTestId('deliverable-dir');
    expect(dir.textContent).toBe('/ws/out');
    expect(dir.getAttribute('title')).toBe('/ws/out/deck.pptx');
    expect(name.nextElementSibling).toBe(dir);              // same line, right after the name
    // the only action is Copy, right after the folder (not pushed to the far edge)
    const copyBtn = within(rows[1]).getByLabelText('Copy path');
    expect(copyBtn.previousElementSibling!.contains(dir)).toBe(true);
    expect(copyBtn.previousElementSibling!.className).not.toContain('flex-1');
    expect(within(card).queryByText('Open in Canvas')).toBeNull();
    const result = screen.getByTestId('turn-result');
    expect(result.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('the name, or anywhere on the row, opens the file once; Copy copies and does not open', async () => {
    const seen: unknown[] = [];
    const on = (e: Event) => seen.push((e as CustomEvent).detail);
    document.addEventListener(OPEN_FILE_EVENT, on);
    renderView([tu('a', 'Bash'), tr('a'), tx('done'), deliv('out/deck.pptx')]);
    fireEvent.click(screen.getByTestId('deliverable-name'));
    expect(seen).toEqual([{ path: '/ws/out/deck.pptx' }]);
    fireEvent.click(screen.getByTestId('deliverable'));
    expect(seen).toHaveLength(2);
    fireEvent.click(screen.getByLabelText('Copy path'));
    document.removeEventListener(OPEN_FILE_EVENT, on);
    expect(seen).toHaveLength(2);
    expect(copySpy).toHaveBeenCalledWith('/ws/out/deck.pptx');
    expect(await screen.findByLabelText('Copied')).toBeTruthy();
  });

  it('every file, image included, gets the one type badge', () => {
    renderView([tu('a', 'Bash'), tr('a'), tx('done'), deliv('out/chart.png', 'out/a.md')]);
    for (const row of screen.getAllByTestId('deliverable')) {
      expect(row.querySelector('img')).toBeNull();
      expect(row.querySelector('.file-badge')).toBeTruthy();
    }
  });

  it('a long folder is cut in the middle, keeping where it ends', () => {
    expect(shortFolder('/ws/out')).toBe('/ws/out');
    expect(shortFolder('/Users/gawan/.swarm-ai/SwarmWS/Projects/SwarmAI/2-understanding')).toBe('~/.swarm-ai/…/2-understanding');
    expect(shortFolder('/Users/gawan/Desktop/Q3')).toBe('~/Desktop/Q3');
    renderView([tu('a', 'Bash'), tr('a'), tx('done'), deliv('a/b/c/d/e/TECH.md')]);
    const dir = screen.getByTestId('deliverable-dir');
    expect(dir.textContent).toBe('/ws/…/e');
    expect(dir.getAttribute('title')).toBe('/ws/a/b/c/d/e/TECH.md');
  });

  it('a product is not listed again as a changed file; no "This turn changed" chips', () => {
    const row = (path: string) => ({ path, absolutePath: `/ws/${path}`, repo: 'r', status: 'modified', added: 2, deleted: 1,
      binary: false, diffable: true, gitRoot: '/ws', start: 'a', end: 'b' });
    const changes = { type: 'turn_changes', files: [row('docs/report.md'), row('backend/x.py')] } as ContentBlock;
    renderView([wr('w', '/ws/docs/report.md'), tr('w'), wr('e', '/ws/backend/x.py'), tr('e'), tx('done'), deliv('docs/report.md'), changes]);
    expect(screen.getAllByTestId('change-row').map((e) => e.getAttribute('data-path'))).toEqual(['backend/x.py']);
    expect(screen.queryByTestId('turn-files')).toBeNull();
    expect(screen.queryAllByTestId('output-card')).toHaveLength(1);
  });

  it('read-only preview (History): products show but do not act', () => {
    const seen: unknown[] = [];
    const on = (e: Event) => seen.push((e as CustomEvent).detail);
    document.addEventListener(OPEN_FILE_EVENT, on);
    const message: Message = { id: 'm1', role: 'assistant', timestamp: new Date().toISOString(), isError: false,
      content: [wr('w', '/ws/backend/x.py'), tr('w'), tx('done'), deliv('out/deck.pptx')] };
    render(<ToastProvider><AssistantMessageView message={message} readOnly /></ToastProvider>);
    const tile = screen.getByTestId('deliverable');
    expect(tile.querySelector('button')).toBeNull();
    expect(screen.queryByText('Open in Canvas')).toBeNull();
    expect(screen.queryByLabelText('Copy path')).toBeNull();
    fireEvent.click(tile.firstElementChild!);
    document.removeEventListener(OPEN_FILE_EVENT, on);
    expect(seen).toEqual([]);
  });

  it('a file name is never shortened on the card', () => {
    const long = '2026-10-18-quarterly-business-review-final-v3-with-appendix.pptx';
    renderView([tu('a', 'Bash'), tr('a'), tx('done'), deliv(`out/${long}`)]);
    const name = screen.getByTestId('deliverable-name');
    expect(name.textContent).toBe(long);
    expect(name.className).not.toMatch(/truncate|ellipsis/);
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


describe('terminal errors — preserve the completed work above the interruption', () => {
  it('keeps prior Result and renders one local coded alert with retry', () => {
    const retry = vi.fn();
    const message: Message = {
      id: 'm-error',
      role: 'assistant',
      timestamp: new Date().toISOString(),
      isError: true,
      errorCode: 'QUEUE_TIMEOUT',
      content: [tu('a', 'Bash'), tr('a'), tx('Partial result survived.'), tx('Slots stayed busy.')],
    };
    render(
      <ToastProvider>
        <AssistantMessageView message={message} isLastAssistant onRetryError={retry} />
      </ToastProvider>,
    );
    const result = screen.getByTestId('turn-result');
    const alert = screen.getByRole('alert');
    expect(result.textContent).toContain('Partial result survived.');
    expect(alert.textContent).toContain('Slots stayed busy.');
    expect(alert.contains(result)).toBe(false);
    expect(screen.getAllByText('Slots Busy')).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: /Retry when slot opens/ }));
    expect(retry).toHaveBeenCalledOnce();
  });
});
