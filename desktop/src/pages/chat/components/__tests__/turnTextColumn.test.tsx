/**
 * One text column for the agent's prose (answer-column fix, 2026-10-24).
 *
 * What is tested (real AssistantMessageView + turnView + turnZones):
 * - C1 the text written after the last call sits at the SAME horizontal offset while
 *   the turn streams (inside the WORKING group) and after it ended (the RESULT on the
 *   first level). It used to move 44px left at the end, which read as a flash.
 * - C2 every piece of prose in a settled turn (restatement, a note with work under
 *   it, a note without, the RESULT) shares that column.
 *
 * Method: jsdom has no layout; x comes from the classes on the path to the turn root
 * (helpers/columnOffset, shared with turnGrid.test.tsx; it throws on an unknown
 * spacing class, so a new indent cannot slip past the test).
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, act } from '@testing-library/react';
import { AssistantMessageView } from '../AssistantMessageView';
import { ToastProvider } from '../../../../contexts/ToastContext';
import { FOLD_DELAY_MS } from '../../../../hooks/useLinger';
import type { Message, ContentBlock } from '../../../../types';
import { columnOffset } from './helpers/columnOffset';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, fallback: unknown) => fallback }),
}));
vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({ health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 }, triggerHealthCheck: vi.fn() }),
}));
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

const tx = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;
const bash = (id: string, cmd: string): ContentBlock =>
  ({ type: 'tool_use', id, name: 'Bash', summary: `Running: ${cmd}`, category: 'bash' }) as ContentBlock;
const RUN = (): ContentBlock => ({
  type: 'pipeline_run', runId: 'run_z', profile: 'full', stages: ['evaluate', 'build', 'deliver'],
  requirement: 'REQ', current: null, at: '2026-10-19T10:00:00+08:00',
}) as ContentBlock;
const row = (stage: string, status: 'in_progress' | 'completed'): ContentBlock => ({
  type: 'pipeline_stage', runId: 'run_z', stage, status, ordinal: null, position: null, total: 3, profile: 'full',
  next: null, nextOrdinal: null, gate: null, summary: null, at: '2026-10-19T10:00:00+08:00',
}) as ContentBlock;
const tr = (id: string): ContentBlock =>
  ({ type: 'tool_result', toolUseId: id, content: 'ok', isError: false, truncated: false }) as ContentBlock;

function view(content: ContentBlock[], isStreaming: boolean) {
  const message: Message = { id: 'm1', role: 'assistant', content, timestamp: '2026-10-19T10:00:00+08:00', isError: false };
  return <ToastProvider><AssistantMessageView message={message} isStreaming={isStreaming} isLastAssistant sessionId="s1" /></ToastProvider>;
}

/** The deepest element holding `text`, and the turn's content column. */
function prose(container: HTMLElement, text: string): { el: Element; root: Element } {
  const root = container.querySelector('.assistant-message > .flex-1');
  if (!root) throw new Error('no turn column');
  const hits = [...root.querySelectorAll('*')].filter((e) => e.textContent?.includes(text));
  const el = hits.find((e) => ![...e.children].some((c) => c.textContent?.includes(text)));
  if (!el) throw new Error(`text not found: ${text}`);
  return { el, root };
}
const at = (c: HTMLElement, text: string) => { const p = prose(c, text); return columnOffset(p.el, p.root); };

describe('one text column for the agent prose', () => {
  it('C1: the text after the last call does not move sideways when the turn ends', () => {
    vi.useFakeTimers();
    const blocks = [tx('I will check it.'), bash('a', 'ls'), tr('a'), tx('The answer is here.')];
    const r = render(view(blocks, true));
    const streaming = at(r.container, 'The answer is here.');
    r.rerender(view(blocks, false));
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    expect(r.container.querySelector('[data-testid="turn-result"]')?.textContent).toContain('The answer is here.');
    expect(at(r.container, 'The answer is here.')).toBe(streaming);
  });

  it('C2: every piece of prose in a settled turn shares one column', () => {
    vi.useFakeTimers();
    const blocks = [tx('I read it as X.'), bash('a', 'ls'), tr('a'), tx('Now the second step.'), bash('b', 'pwd'), tr('b'),
      tx('Nothing after this note yet.'), bash('c', 'id'), tr('c'), tx('Final answer.')];
    const r = render(view(blocks, true));
    const live = ['I read it as X.', 'Now the second step.', 'Nothing after this note yet.', 'Final answer.']
      .map((s) => at(r.container, s));
    expect(new Set(live).size).toBe(1);
    r.rerender(view(blocks, false));
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    expect(at(r.container, 'Final answer.')).toBe(live[0]);
    expect(at(r.container, 'I read it as X.')).toBe(live[0]);
  });

  it('C3: prose inside a pipeline stage (a note before a call, a closed note, a note with no work) is on the column', () => {
    vi.useFakeTimers();
    const blocks = [tx('Starting the run.'), RUN(),
      row('build', 'in_progress'), tx('Stage note before work.'), bash('b', 'make'), tr('b'),
      tx('Second stage note.'), bash('c', 'make test'), tr('c'), tx('Last stage note.')];
    const r = render(view(blocks, true));
    const top = at(r.container, 'Starting the run.');
    for (const s of ['Stage note before work.', 'Second stage note.', 'Last stage note.']) expect(at(r.container, s)).toBe(top);
    // the user closes the first note: its text stays on the column
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    const toggles = r.container.querySelectorAll('[data-testid="zone-stage"] [data-testid="segment-toggle"]');
    expect(toggles.length).toBeGreaterThan(0);
    act(() => { (toggles[0] as HTMLElement).click(); });
    expect(at(r.container, 'Stage note before work.')).toBe(top);
  });
});
