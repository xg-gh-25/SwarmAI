/**
 * Products in the turn (design docs/design/2026-10-26-session-products.md §3.1-§3.2).
 *
 * Tested through the real AssistantMessageView:
 * - a `turn_product` block of a top-level call is a first-level products zone after
 *   the work that made it, and it ends that work: calls after it open a new WORKING;
 * - nothing moves: the earlier WORKING folds later by the existing 2 s rule, while
 *   the product row's DOM node stays the same; the running call is untouched;
 * - a sub-agent's product is not drawn in the turn; it is on the card;
 * - the ending card lists the files once (no "shown above" marker) and lists remote
 *   products; a turn whose only product is remote still has a card;
 * - a turn with no product blocks is drawn exactly as before (no products zone).
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { AssistantMessageView } from '../AssistantMessageView';
import { ToastProvider } from '../../../../contexts/ToastContext';
import { FOLD_DELAY_MS } from '../../../../hooks/useLinger';
import type { Message, ContentBlock } from '../../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, fallback: unknown) => fallback }),
}));
vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({ health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 }, triggerHealthCheck: vi.fn() }),
}));
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

const tx = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;
const write = (id: string, path: string): ContentBlock =>
  ({ type: 'tool_use', id, name: 'Write', summary: `Writing ${path}`, category: 'edit', display: { tool: 'Write', object: path, key: `w-${path}` } }) as ContentBlock;
const bash = (id: string, cmd: string): ContentBlock =>
  ({ type: 'tool_use', id, name: 'Bash', summary: `Running: ${cmd}`, category: 'bash' }) as ContentBlock;
const tr = (id: string): ContentBlock => ({ type: 'tool_result', toolUseId: id, content: 'ok', isError: false, truncated: false }) as ContentBlock;
const fileP = (id: string, abs: string, parent?: string): ContentBlock =>
  ({ type: 'turn_product', toolUseId: id, product: 'file', path: abs.split('/').pop(), absolutePath: abs, fileKind: 'content',
    ...(parent ? { parentToolUseId: parent } : {}) }) as ContentBlock;
const remoteP = (id: string): ContentBlock =>
  ({ type: 'turn_product', toolUseId: id, product: 'remote', tool: 'mcp__slack-mcp__post_message', action: 'Posted to Slack', target: '#launch' }) as ContentBlock;
const deliv = (...abs: string[]): ContentBlock =>
  ({ type: 'turn_deliverables', files: abs.map((a) => ({ path: a.split('/').pop(), absolutePath: a, kind: 'content', operation: 'written' })) }) as ContentBlock;

function view(content: ContentBlock[], isStreaming = false) {
  const message: Message = { id: 'm1', role: 'assistant', content, timestamp: '2026-10-26T10:00:00+08:00', isError: false };
  return <ToastProvider><AssistantMessageView message={message} isStreaming={isStreaming} isLastAssistant sessionId="s1" /></ToastProvider>;
}
const zones = (root: HTMLElement) => [...root.querySelectorAll('[data-zone]')].map((e) => e.getAttribute('data-zone'));

describe('a product ends the work that made it', () => {
  it('the in-turn row is the same tile as the ending card (one look)', () => {
    const blocks = [write('w1', '/u/Desktop/q3.md'), tr('w1'), fileP('w1', '/u/Desktop/q3.md'), tx('Done.'), deliv('/u/Desktop/q3.md')];
    render(view(blocks, false));
    const inTurn = screen.getByTestId('zone-products').querySelector('[data-testid="deliverable"]')!;
    const onCard = screen.getByTestId('output-card').querySelector('[data-testid="deliverable"]')!;
    expect(inTurn.className).toBe(onCard.className);
    expect(inTurn.querySelector('[data-testid="deliverable-name"]')!.textContent).toBe('q3.md');
  });

  it('streaming: products zone after its WORKING; the next call opens a new WORKING', () => {
    const blocks = [write('w1', '/u/Desktop/q3.md'), tr('w1'), fileP('w1', '/u/Desktop/q3.md'), bash('b1', 'ls')];
    const { container } = render(view(blocks, true));
    expect(zones(container)).toEqual(['phase', 'products', 'phase']);
    const row = screen.getByTestId('zone-products');
    expect(row.textContent).toContain('q3.md');
    expect(row.closest('[data-zone]')!.getAttribute('data-attn')).toBe('know');
  });

  it('the row node never changes while the work above it folds 2 s later', () => {
    vi.useFakeTimers();
    const a = [write('w1', '/u/Desktop/q3.md'), tr('w1'), fileP('w1', '/u/Desktop/q3.md')];
    const r = render(view(a, true));
    const node = screen.getByTestId('zone-products');
    const b = [...a, bash('b1', 'ls')];
    r.rerender(view(b, true));
    expect(screen.getByTestId('zone-products')).toBe(node);
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    r.rerender(view(b, true));
    expect(screen.getByTestId('zone-products')).toBe(node);
    const phases = r.container.querySelectorAll('[data-testid="zone-phase"]');
    expect(phases.length).toBe(2);
    expect(phases[1].getAttribute('data-live')).toBe('true');
  });

  it('a sub-agent product is not a zone in the turn', () => {
    const blocks = [bash('a1', 'x'), tr('a1'), fileP('s1', '/u/Desktop/sub.md', 'ag1'), tx('Done.')];
    const { container } = render(view(blocks, false));
    expect(zones(container)).not.toContain('products');
  });

  it('a turn without product blocks has no products zone', () => {
    const blocks = [write('w1', '/u/Desktop/q3.md'), tr('w1'), tx('Done.')];
    const { container } = render(view(blocks, false));
    expect(zones(container)).not.toContain('products');
  });

  it('a file written again later in the turn keeps its one row; the later write adds none', () => {
    // Gawan 2026-10-26: the same file appeared after two WORKED groups
    const blocks = [write('w1', '/u/a.py'), tr('w1'), fileP('w1', '/u/a.py'),
      bash('b1', 'x'), tr('b1'),
      write('w2', '/u/a.py'), tr('w2'), fileP('w2', '/u/a.py'),
      write('w3', '/u/b.py'), tr('w3'), fileP('w3', '/u/b.py'), tx('Done.')];
    const { container } = render(view(blocks, false));
    const rows = [...container.querySelectorAll('[data-testid="zone-products"] [data-testid="deliverable"]')];
    expect(rows.map((r) => r.getAttribute('data-path'))).toEqual(['/u/a.py', '/u/b.py']);
    // the second write of a.py does not end the work: no extra zone for it
    expect(zones(container).filter((z) => z === 'products')).toHaveLength(2);
  });

  it('a repeat write alone does not split the work it is part of', () => {
    const blocks = [write('w1', '/u/a.py'), tr('w1'), fileP('w1', '/u/a.py'),
      bash('b1', 'x'), tr('b1'), write('w2', '/u/a.py'), tr('w2'), fileP('w2', '/u/a.py'),
      bash('b2', 'y'), tr('b2'), tx('Done.')];
    const { container } = render(view(blocks, false));
    expect(zones(container).filter((z) => z === 'phase')).toHaveLength(2);
  });
});

describe('the ending card', () => {
  it('lists each file once with no marker, and lists remote products as cards', () => {
    const blocks = [write('w1', '/u/Desktop/q3.md'), tr('w1'), fileP('w1', '/u/Desktop/q3.md'),
      bash('m1', 'post'), tr('m1'), remoteP('m1'), tx('Done.'), deliv('/u/Desktop/q3.md', '/u/Desktop/other.md')];
    render(view(blocks, false));
    const card = screen.getByTestId('output-card');
    const tiles = card.querySelectorAll('[data-testid="deliverable"]');
    expect([...tiles].map((t) => t.getAttribute('data-path'))).toEqual(['/u/Desktop/q3.md', '/u/Desktop/other.md']);
    expect(card.querySelector('[data-testid="shown-above"]')).toBeNull();
    expect(card.querySelector('[data-testid="remote-product"]')!.textContent).toContain('Posted to Slack');
    expect(card.textContent).toContain('3 deliverables');
  });

  it('a turn whose only product is remote has a card', () => {
    render(view([bash('m1', 'post'), tr('m1'), remoteP('m1'), tx('Done.')], false));
    expect(screen.getByTestId('output-card').textContent).toContain('Posted to Slack');
  });
});
