/**
 * Turn attention tiers, step 1 (docs/design/2026-10-25-turn-attention-tiers.md §3, §5, §6).
 *
 * Renders real turns through AssistantMessageView and checks the DOM:
 * - every zone host carries `data-attn`, with the tier §3 gives its kind and state;
 * - the agent's process prose (restatement, notes between groups, notes inside a phase or
 *   a stage) carries `data-prose="process"`, the hook of the one CSS rule that dims it;
 *   the answer does not; the rule itself is in index.css;
 * - a thought being written inside a phase shows its first line, not all of it;
 * - a strip with detail shows a chevron, one without keeps an empty slot (alignment);
 * - guard: inside a process host no element carries a palette colour other than the
 *   v6 textures (amber running, purple agent) and primary focus rings.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { AssistantMessageView } from '../AssistantMessageView';
import { ToastProvider } from '../../../../contexts/ToastContext';
import type { Message, ContentBlock } from '../../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, fallback: unknown) => fallback }),
}));
vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({ health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 }, triggerHealthCheck: vi.fn() }),
}));
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

const th = (thinking: string): ContentBlock => ({ type: 'thinking', thinking }) as ContentBlock;
const tx = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;
const bash = (id: string, cmd: string): ContentBlock =>
  ({ type: 'tool_use', id, name: 'Bash', summary: `Running: ${cmd}`, category: 'bash' }) as ContentBlock;
const tr = (id: string, outcome?: string): ContentBlock =>
  ({ type: 'tool_result', toolUseId: id, content: 'ok', isError: false, truncated: false, ...(outcome ? { outcome } : {}) }) as ContentBlock;
const ask = (answered: boolean): ContentBlock => ({
  type: 'ask_user_question', toolUseId: 'q1',
  questions: [{ question: 'Q-TOKEN?', header: 'Q', options: [{ label: 'A', description: 'a' }, { label: 'B', description: 'b' }], multiSelect: false }],
  ...(answered ? { answers: { 'Q-TOKEN?': 'A' } } : {}),
}) as unknown as ContentBlock;
const RUN: ContentBlock = { type: 'pipeline_run', runId: 'run_z', profile: 'full', stages: ['evaluate', 'build'],
  requirement: 'REQ', current: null, at: '2026-10-19T10:00:00+08:00' } as ContentBlock;
const row = (stage: string, status: 'in_progress' | 'completed'): ContentBlock => ({
  type: 'pipeline_stage', runId: 'run_z', stage, status, ordinal: null, position: null, total: 2, profile: 'full',
  next: null, nextOrdinal: null, gate: null, summary: null, at: '2026-10-19T10:00:00+08:00' }) as ContentBlock;

function view(content: ContentBlock[], isStreaming = false) {
  const message: Message = { id: 'm1', role: 'assistant', content, timestamp: '2026-10-19T10:00:00+08:00', isError: false };
  return <ToastProvider><AssistantMessageView message={message} isStreaming={isStreaming} isLastAssistant sessionId="s1" /></ToastProvider>;
}
const host = (testid: string) => screen.getByTestId(testid).closest('[data-zone]') as HTMLElement;

describe('every zone carries its tier', () => {
  const turn = [th('hmm'), tx('I read it as X.'), bash('a', 'ls'), tr('a'), tx('Now the build:'), bash('b', 'make'), tr('b'), tx('Done: X works.')];

  it('the answer is know; restatement and process are process; every host has data-attn', () => {
    const { container } = render(view(turn, false));
    const hosts = [...container.querySelectorAll('[data-zone]')];
    expect(hosts.length).toBeGreaterThan(0);
    for (const h of hosts) expect(h.getAttribute('data-attn')).toMatch(/^(act|know|process)$/);
    expect(host('turn-result').getAttribute('data-attn')).toBe('know');
    expect(host('turn-restatement').getAttribute('data-attn')).toBe('process');
    expect(host('zone-phase').getAttribute('data-attn')).toBe('process');
    expect(host('zone-thinking').getAttribute('data-attn')).toBe('process');
  });

  it('process prose carries data-prose="process"; the answer does not', () => {
    const { container } = render(view(turn, false));
    expect(host('turn-restatement').getAttribute('data-prose')).toBe('process');
    expect(host('turn-result').getAttribute('data-prose')).toBeNull();
    fireEvent.click(within(screen.getByTestId('zone-phase')).getByTestId('group-toggle'));
    expect(screen.getByTestId('phase-text').getAttribute('data-prose')).toBe('process');
    expect(container.querySelector('[data-testid="turn-result"] [data-prose]')).toBeNull();
  });

  it('a question is act while unanswered, process once answered', () => {
    const { unmount } = render(view([tx('go'), bash('a', 'ls'), tr('a'), ask(false)], true));
    expect(screen.getAllByText(/Q-TOKEN/)[0].closest('[data-zone]')!.getAttribute('data-attn')).toBe('act');
    unmount();
    render(view([tx('go'), bash('a', 'ls'), tr('a'), ask(true), tx('Done.')], false));
    expect(screen.getAllByText(/Q-TOKEN/)[0].closest('[data-zone]')!.getAttribute('data-attn')).toBe('process');
  });

  it('a note inside a stage is process prose', () => {
    render(view([tx('go'), RUN, row('evaluate', 'in_progress'), bash('e', 'ls'), tr('e'), tx('STAGE-NOTE here'), bash('f', 'ls -l')], true));
    expect(screen.getByText(/STAGE-NOTE/).closest('[data-prose]')!.getAttribute('data-prose')).toBe('process');
  });

  it('index.css holds the one rule that dims process prose', () => {
    const css = readFileSync(resolve(__dirname, '../../../../index.css'), 'utf8');
    expect(css).toMatch(/\[data-prose="process"\]\s*\{\s*--color-text:\s*var\(--color-text-secondary\);?\s*\}/);
  });
});

describe('process-tier pilot', () => {
  it('a thought being written inside a phase shows its first line; open it for all', () => {
    render(view([tx('go'), bash('a', 'cat a.ts'), tr('a'), th('FIRST-LINE\nSECOND-LINE')], true));
    const thought = screen.getByTestId('thought');
    expect(thought.getAttribute('data-folded')).toBe('true');
    expect(thought.textContent).toContain('FIRST-LINE');
    expect(thought.textContent).not.toContain('SECOND-LINE');
    fireEvent.click(within(thought).getByTestId('thought-toggle'));
    expect(screen.getByTestId('thought-text').textContent).toContain('SECOND-LINE');
  });

  it('two thoughts in a row while written: the row shows the newest one', () => {
    render(view([tx('go'), bash('a', 'cat a.ts'), tr('a'), th('OLD-THOUGHT\nmore'), th('NEW-THOUGHT\nmore')], true));
    const thought = screen.getByTestId('thought');
    expect(thought.textContent).toContain('NEW-THOUGHT');
    expect(thought.textContent).not.toContain('OLD-THOUGHT');
  });

  it('the first THINKING passage stays in full while written', () => {
    render(view([th('ONLY-LINE\nMORE-LINE')], true));
    expect(screen.getByTestId('zone-thinking').textContent).toContain('MORE-LINE');
  });

  it('a strip with detail shows a chevron; one without keeps an empty slot', () => {
    render(view([tx('go'), bash('a', 'make'), tr('a', 'Tests 41 passed'), bash('b', 'ls'), tr('b')], true));
    const [withDetail, without] = screen.getAllByTestId('tool-strip');
    expect(within(withDetail).getByTestId('strip-chevron').textContent).toContain('chevron_right');
    expect(within(without).getByTestId('strip-chevron').textContent).toBe('');
  });
});

describe('guard: no palette colour inside a process host', () => {
  const PALETTE = /\b(?:text|bg|border|ring)-(?:red|orange|amber|yellow|green|emerald|blue|sky|purple|violet|pink|primary)(?:-\d{2,3})?(?:\/[\w.[\]]+)?\b/g;
  const ALLOWED = /^(?:(?:bg|text|border)-amber-\d{3}(?:\/.*)?|(?:bg|text|border)-purple-\d{3}(?:\/.*)?|ring-primary(?:\/.*)?)$/;

  it('only the running / agent textures and focus rings', () => {
    vi.useFakeTimers();
    const { container } = render(view([th('t'), tx('go'), bash('a', 'make'), tr('a', 'out'), tx('next:'), bash('b', 'make check'), th('mid')], true));
    const bad: string[] = [];
    for (const h of container.querySelectorAll('[data-attn="process"]')) {
      for (const el of [h, ...h.querySelectorAll('*')]) {
        for (const m of (el.getAttribute('class') ?? '').match(PALETTE) ?? []) {
          const cls = m.replace(/^(?:hover|focus-visible|dark):/, '');
          if (!ALLOWED.test(cls)) bad.push(cls);
        }
      }
    }
    expect(bad).toEqual([]);
  });
});
