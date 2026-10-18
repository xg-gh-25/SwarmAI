/**
 * AssistantMessageView × turnView — the work-group render contract (chat display v7.11).
 *
 * What is tested (real AssistantMessageView, only leaf contexts mocked):
 * - a streaming turn's last group is the open WORKING card (no toggle), paged from
 *   the end, with the running strip last; from history a group is ONE folded
 *   WORKED line that opens to its strips (AC1)
 * - a group of one tool call is that strip itself — never a header opening to the
 *   same line again (AC3)
 * - every tool call is one strip: kind · target · short outcome · status, an error
 *   one line below, never the raw command (AC3)
 * - notes to the user stay in the stream, in order; a ":"-ended note titles the
 *   next group (AC2); interrupts render outside every group
 * - thoughts: no per-item label or Show all/less, the text is the control (AC4);
 *   the intent is "Approach", 6 lines; no "Started without restating" line (AC5)
 * - the process lane holds the work; the alignment and the answer sit outside (AC6);
 *   no button is nested in another (AC7)
 * - fold timing: a finished group folds FOLD_DELAY_MS later, keeping its live layout
 *   until then; a user's open/closed choice wins
 * - a running sub-agent's progress and a finished one's verdict sit on its strip
 * - a trailing ```next-steps block renders as chips, never as raw text (AC9)
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act, within } from '@testing-library/react';
import React from 'react';
import { AssistantMessageView } from '../AssistantMessageView';
import { FOLD_DELAY_MS } from '../../../../hooks/useLinger';
import { ToastProvider } from '../../../../contexts/ToastContext';
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
const injectSpy = vi.fn();
vi.mock('../../injectChatInput', () => ({
  dispatchInjectChatInput: (d: unknown) => injectSpy(d),
  INJECT_CHAT_INPUT: 'swarm:inject-chat-input',
}));

const tu = (id: string, summary: string): ContentBlock =>
  ({ type: 'tool_use', id, name: 'Bash', summary, category: 'bash' }) as ContentBlock;
const tx = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;
const th = (t: string) => ({ type: 'thinking', thinking: t }) as ContentBlock;
const tr = (toolUseId: string): ContentBlock =>
  ({ type: 'tool_result', toolUseId, content: 'ok', isError: false, truncated: false }) as ContentBlock;
const res = (id: string, outcome: string, isError = false) =>
  ({ type: 'tool_result', toolUseId: id, content: 'x', isError, truncated: false, outcome }) as ContentBlock;
const wr = (id: string, p: string): ContentBlock =>
  ({ type: 'tool_use', id, name: 'Edit', summary: `Writing to ${p}`, category: 'write' }) as ContentBlock;
const bashRun = (n: number) =>
  Array.from({ length: n }, (_, i) => tu(`t${i}`, `Running: cd /Users/x/repo; grep -n "needle${i}" f${i}.ts`));
const LONG = Array.from({ length: 12 }, (_, i) => `line ${i} of a long thought`).join('\n');

function msg(content: ContentBlock[]): Message {
  return { id: 'm1', role: 'assistant', content, timestamp: new Date().toISOString(), isError: false };
}
/** The same message re-rendered as it streams: `rerender(viewOf(next, s))`. */
const viewOf = (c: ContentBlock[], s: boolean) => (
  <ToastProvider><AssistantMessageView message={msg(c)} isStreaming={s} /></ToastProvider>
);
function renderView(content: ContentBlock[], opts: { isStreaming?: boolean; isLastAssistant?: boolean } = {}) {
  return render(
    <ToastProvider>
      <AssistantMessageView message={msg(content)} isStreaming={opts.isStreaming} isLastAssistant={opts.isLastAssistant} />
    </ToastProvider>,
  );
}

beforeEach(() => injectSpy.mockClear());

describe('AssistantMessageView — work groups (AC1)', () => {
  it('streaming 38 tool calls → one open WORKING card, paged from the end, running strip last', () => {
    const live = bashRun(38).flatMap((b, i) => (i < 37 ? [b, tr(`t${i}`)] : [b]));
    const { container } = renderView(live, { isStreaming: true });
    const card = screen.getByTestId('work-group');
    expect(card.dataset.live).toBe('true');
    expect(within(card).getByTestId('group-row').textContent).toContain('Working');
    // The live card is never a toggle: only finished work folds.
    expect(screen.queryByTestId('group-toggle')).toBeNull();
    const rows = screen.getAllByTestId('step-row');
    expect(rows).toHaveLength(12);
    expect(rows[rows.length - 1].textContent).toContain('needle37');
    expect(rows[rows.length - 1].dataset.status).toBe('pending');
    expect(screen.getByTestId('show-all-steps').textContent).toContain('26 more');
    // The running strip holds the one spinner.
    expect(container.querySelectorAll('.animate-spin')).toHaveLength(1);
    expect(rows[rows.length - 1].querySelector('.animate-spin')).not.toBeNull();
    expect(container.textContent).not.toContain('/Users/x/repo');
  });

  it('history (no tool_result) → one folded WORKED line, no spinner; opening shows all 38', () => {
    const { container } = renderView(bashRun(38), { isStreaming: false });
    expect(screen.getAllByTestId('group-row')).toHaveLength(1);
    expect(screen.getByTestId('group-row').textContent).toContain('Worked');
    expect(screen.queryAllByTestId('step-row')).toHaveLength(0);
    expect(container.querySelector('.animate-spin')).toBeNull();
    fireEvent.click(screen.getByTestId('group-toggle'));
    expect(screen.getAllByTestId('step-row')).toHaveLength(12);
    fireEvent.click(screen.getByTestId('show-all-steps'));
    expect(screen.getAllByTestId('step-row')).toHaveLength(38);
  });

  it('a group of one tool call is that strip itself, with no fold', () => {
    renderView([th('look'), tu('a', 'Running: ls'), tx('the answer')]);
    const g = screen.getByTestId('work-group');
    expect(g.dataset.single).toBe('true');
    expect(screen.queryByTestId('group-toggle')).toBeNull();
    expect(screen.getAllByTestId('step-row')).toHaveLength(1);
    expect(screen.getByTestId('strip-kind').textContent).toBe('Read');
  });

  it('finished multi-step work: WORKED, then the files it changed as chips BESIDE the toggle', () => {
    const seen: unknown[] = [];
    const onOpen = (e: Event) => seen.push((e as CustomEvent).detail);
    document.addEventListener('swarm:open-file', onOpen);
    try {
      const { container } = renderView([tu('a', 'Running: ls'), wr('b', '/r/src/TabView.tsx'), wr('c', '/r/src/x.py'), tx('done')]);
      const row = screen.getByTestId('group-row');
      expect(row.textContent).toContain('Worked');
      expect(row.textContent).toContain('changed');
      const chips = within(row).getAllByTestId('file-chip');
      expect(chips.map((c) => c.getAttribute('data-path'))).toEqual(['/r/src/TabView.tsx', '/r/src/x.py']);
      // AC7: no interactive element inside another.
      expect(screen.getByTestId('group-toggle').querySelector('[data-testid="file-chip"]')).toBeNull();
      expect(container.querySelectorAll('button button')).toHaveLength(0);
      fireEvent.click(chips[0]);
      expect(seen).toEqual([{ path: '/r/src/TabView.tsx', autoDiff: true }]);
      expect(screen.queryByTestId('group-body')).toBeNull(); // the chip opens the file, not the group
    } finally {
      document.removeEventListener('swarm:open-file', onOpen);
    }
  });

  it('a folded line with nothing to name falls back to its size', () => {
    renderView([tu('a', 'Writing 3 todos'), tu('b', 'Writing 4 todos'), tx('done')]);
    expect(screen.getByTestId('group-row').textContent).toContain('2 steps');
  });

  it('a group is titled by the first sentence of its first thought', () => {
    renderView([th('Intent: read the store and compare both paths before deciding anything here, at all, really, yes, of course.'),
      tu('a', 'Running: ls'), tr('a'), th('Now the store. Then the reader.'), tu('b', 'Running: pwd'), tu('c', 'Running: make'), tx('done')]);
    const titles = screen.getAllByTestId('group-title').map((e) => e.textContent);
    expect(titles).toContain('Now the store.');
  });

  it('an interrupt renders outside every group', () => {
    const ask = {
      type: 'ask_user_question', toolUseId: 'q1',
      questions: [{ question: 'INTERRUPT_Q which option?', header: 'h', options: [{ label: 'A', description: 'a' }], multiSelect: false }],
    } as ContentBlock;
    const { container } = renderView([tu('a', 'Running: ls'), ask]);
    const q = screen.getByText(/INTERRUPT_Q/);
    const groups = [...container.querySelectorAll('[data-testid="work-group"]')];
    expect(groups.length).toBeGreaterThan(0);
    expect(groups.some((g) => g.contains(q))).toBe(false);
    expect(q.closest('[data-lane="true"]')).toBeNull();
  });
});

describe('AssistantMessageView — one strip per tool call (AC3)', () => {
  it('kind · target · short outcome on the row; a failure one line below, in red', () => {
    renderView([tu('a', 'Running: npm test'), res('a', '12 passed in 3.1s'),
      tu('b', 'Running: make'), res('b', 'make: *** no rule to make target build, stop.', true), tu('c', 'Running: ls')], { isStreaming: true });
    const rows = screen.getAllByTestId('step-row');
    expect(within(rows[0]).getByTestId('strip-kind').textContent).toBe('Test');
    expect(within(rows[0]).getByTestId('step-outcome').textContent).toBe('12 passed in 3.1s');
    const err = within(rows[1]).getByTestId('step-error');
    expect(err.className).toMatch(/text-red-400/);
    expect(err.className).toMatch(/truncate/);
    expect(err.getAttribute('title')).toBe('make: *** no rule to make target build, stop.');
  });

  it('a long outcome is not repeated on the row', () => {
    const long = 'diff --git a/src/x.ts b/src/x.ts index 1234..5678 100644 --- a/src/x.ts';
    renderView([tu('a', 'Running: git diff'), res('a', long), tu('b', 'Running: ls')], { isStreaming: true });
    expect(screen.queryByText(long)).toBeNull();
  });

  it('never shows the raw command: not as text, title or tooltip', () => {
    const cmd = 'Running: R=run_df73d970; python3 -c "import json; print(json.load(open(\'x.json\')))"';
    const { container } = renderView([tu('a', cmd), tu('b', 'Running: cd /a && FOO=1 node -e "x()"'), tx('done')]);
    fireEvent.click(screen.getByTestId('group-toggle'));
    const html = container.innerHTML;
    expect(html).not.toContain('run_df73d970');
    expect(html).not.toContain('import json');
    expect(html).not.toContain('FOO=1');
    expect(screen.getAllByTestId('strip-target').map((e) => e.textContent)).toEqual(['inline script', 'inline script']);
  });

  it('an unrecognised tool without a summary names itself once', () => {
    renderView([{ type: 'tool_use', id: 'z', name: 'Frobnicate', category: 'fallback' } as ContentBlock]);
    expect(screen.getByTestId('step-row').textContent!.match(/Frobnicate/g)).toHaveLength(1);
  });

  it('an unrecognised tool shows its own name, no invented verb', () => {
    renderView([{ type: 'tool_use', id: 'z', name: 'Weird', summary: 'Weird: some context', category: 'fallback' } as ContentBlock]);
    const row = screen.getByTestId('step-row');
    expect(within(row).getByTestId('strip-kind').textContent).toBe('Weird');
    expect(row.textContent).toContain('Weird: some context');
    expect(row.textContent).not.toContain('Ran');
  });

  it('adjacent finished reads of one file are ONE strip with every range', () => {
    const rd = (id: string): ContentBlock =>
      ({ type: 'tool_use', id, name: 'Read', summary: 'Reading /r/src/a.ts', category: 'read' }) as ContentBlock;
    renderView([rd('a'), res('a', 'lines 1–200'), rd('b'), res('b', 'lines 200–400'), rd('c'), res('c', 'lines 400–480'),
      tu('d', 'Running: ls')], { isStreaming: true });
    const rows = screen.getAllByTestId('step-row');
    expect(rows).toHaveLength(2);
    expect(screen.getByTestId('step-repeat').textContent).toBe('3×');
    expect(rows[0].textContent).toContain('lines 1–200, lines 200–400, lines 400–480');
  });

  it('after a tool returns and before the next starts, the WORKING header spins', () => {
    const { container } = renderView([tu('a', 'Running: ls'), tr('a')], { isStreaming: true });
    const head = screen.getByTestId('group-row');
    expect(head.querySelector('.animate-spin')).not.toBeNull();
    expect(container.querySelectorAll('.animate-spin')).toHaveLength(1);
    expect(screen.getAllByTestId('step-row')[0].dataset.status).toBe('done');
    // Colour only for exceptions: a normal finish is a neutral mark.
    expect(container.querySelector('.text-status-online, .text-green-400, .text-green-500')).toBeNull();
  });

  it('before any content: one "Thinking" line with a spinner, nothing to open', () => {
    renderView([], { isStreaming: true });
    const row = screen.getByTestId('group-row');
    expect(row.tagName).toBe('DIV');
    expect(row.textContent).toContain('Thinking');
    expect(row.querySelector('.animate-spin')).not.toBeNull();
  });
});

describe('AssistantMessageView — thoughts carry no chrome (AC4)', () => {
  it('a finished non-tool reply keeps its thought readable as the Approach', () => {
    renderView([th('deliberating'), tx('the answer')]);
    expect(screen.getByTestId('thought-text').textContent).toBe('deliberating');
    expect(screen.queryByTestId('group-toggle')).toBeNull();
  });

  it('no Thought label and no Show all / Show less anywhere', () => {
    const { container } = renderView([th(LONG), tu('a', 'Running: ls'), tr('a'), tu('a1', 'Running: pwd'), tr('a1'), tu('a2', 'Running: id'), tr('a2'), th(LONG), tu('b', 'Running: pwd'), tx('ok')], { isStreaming: true });
    expect(container.textContent).not.toMatch(/Show all|Show less|Show more/);
    expect(screen.queryByTestId('thought-toggle')).toBeNull();
    const labels = [...container.querySelectorAll('[data-testid="thought"]')].map((e) => e.textContent ?? '');
    expect(labels.filter((l) => l.startsWith('Thought'))).toHaveLength(0);
  });

  it('a long thought: 1 line once the turn is over; the text opens and closes in place', () => {
    renderView([tu('a', 'Running: ls'), tr('a'), tu('a1', 'Running: pwd'), tr('a1'), tu('a2', 'Running: id'), tr('a2'), th(LONG), tx('ok')]);
    fireEvent.click(screen.getByTestId('group-toggle'));
    const text = screen.getByTestId('thought-text');
    expect(text.style.webkitLineClamp).toBe('1');
    expect(text.getAttribute('role')).toBe('button');
    fireEvent.click(text);
    expect(screen.getByTestId('thought-text').style.webkitLineClamp).toBe('');
    expect(screen.getByTestId('thought-text').getAttribute('aria-expanded')).toBe('true');
    fireEvent.keyDown(screen.getByTestId('thought-text'), { key: 'Enter' });
    expect(screen.getByTestId('thought-text').style.webkitLineClamp).toBe('1');
  });

  it('selecting text in a thought does not open or close it', () => {
    renderView([tu('a', 'Running: ls'), tr('a'), tu('a1', 'Running: pwd'), tr('a1'), tu('a2', 'Running: id'), tr('a2'), th(LONG), tx('ok')]);
    fireEvent.click(screen.getByTestId('group-toggle'));
    const sel = vi.spyOn(window, 'getSelection').mockReturnValue({ toString: () => 'line 3' } as unknown as Selection);
    try {
      fireEvent.click(screen.getByTestId('thought-text'));
      expect(screen.getByTestId('thought-text').getAttribute('aria-expanded')).toBe('false');
    } finally {
      sel.mockRestore();
    }
  });

  it('while the turn streams a finished thought keeps 3 lines', () => {
    render(viewOf([tu('a', 'Running: ls'), tr('a'), tu('a1', 'Running: pwd'), tr('a1'), tu('a2', 'Running: id'), tr('a2'), th(LONG), tu('b', 'Running: pwd')], true));
    expect(screen.getByTestId('thought-text').style.webkitLineClamp).toBe('3');
  });

  it('a one-line thought is not a control', () => {
    renderView([tu('a', 'Running: ls'), tr('a'), th('short'), tx('ok')]);
    fireEvent.click(screen.getByTestId('group-toggle'));
    expect(screen.getByTestId('thought-text').getAttribute('role')).toBeNull();
  });

  it('the intent thought gets 6 lines and the one Approach label', () => {
    renderView([th(LONG), tu('a', 'Running: ls'), tr('a'), tx('ok')]);
    const t = screen.getByTestId('thought');
    expect(t.dataset.intent).toBe('true');
    expect(t.textContent).toContain('Approach');
    expect(screen.getByTestId('thought-text').style.webkitLineClamp).toBe('6');
  });

  it('a streaming thought in the WORKING card wraps; the card holds the one spinner', () => {
    const { container } = renderView([tu('a', 'Running: ls'), tr('a'), th('Read the config first.\nThen compare both thresholds.')], { isStreaming: true });
    const thought = screen.getByTestId('thought');
    expect(thought.dataset.live).toBe('true');
    expect(thought.textContent).not.toContain('Thinking');
    const text = screen.getByTestId('thought-text');
    expect(text.textContent).toBe('Read the config first.\nThen compare both thresholds.');
    expect(text.className).toMatch(/whitespace-pre-wrap/);
    expect(container.querySelectorAll('.animate-spin')).toHaveLength(1);
  });

  it('the user\'s open thought survives the stream ending', () => {
    const { rerender } = render(viewOf([tu('a', 'Running: ls'), tr('a'), tu('a1', 'Running: pwd'), tr('a1'), tu('a2', 'Running: id'), tr('a2'), th(LONG), tu('b', 'Running: pwd')], true));
    fireEvent.click(screen.getByTestId('thought-text'));
    rerender(viewOf([tu('a', 'Running: ls'), tr('a'), tu('a1', 'Running: pwd'), tr('a1'), tu('a2', 'Running: id'), tr('a2'), th(LONG), tu('b', 'Running: pwd'), tr('b'), tx('done')], false));
    expect(screen.getByTestId('thought-text').getAttribute('aria-expanded')).toBe('true');
  });
});

describe('AssistantMessageView — fold timing', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it('a finished intent thought never folds away', () => {
    const { rerender } = render(viewOf([th('step one')], true));
    expect(screen.getByTestId('thought').dataset.live).toBe('true');
    rerender(viewOf([th('step one, then two'), tu('a', 'Running: ls'), tr('a'), tx('done')], false));
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS * 5); });
    expect(screen.getByTestId('thought').dataset.live).toBe('false');
    expect(screen.getByTestId('thought-text').textContent).toBe('step one, then two');
  });

  it('in the 2s after it finishes, a group keeps its live layout (newest strips), then folds', () => {
    const many = Array.from({ length: 15 }, (_, i) => [tu(`s${i}`, `Running: cat f${i}.ts`), tr(`s${i}`)]).flat();
    const { rerender } = render(viewOf([...many, tu('z', 'Running: date')], true));
    rerender(viewOf([...many, tu('z', 'Running: date'), tr('z'), tx('next note')], true));
    const body = screen.getByTestId('group-body');
    expect(body.dataset.liveLayout).toBe('true');
    expect(body.textContent).toContain('f14.ts'); // the newest strips, not reshuffled to the first
    expect(body.textContent).not.toContain('f0.ts');
    expect(screen.getAllByTestId('step-row')).toHaveLength(12);
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS); });
    expect(screen.queryByTestId('group-body')).toBeNull(); // folded in one move
    fireEvent.click(screen.getByTestId('group-toggle'));
    expect(screen.getByTestId('group-body').dataset.liveLayout).toBe('false'); // opened by hand: first strips
    expect(screen.getByTestId('group-body').textContent).toContain('f0.ts');
  });

  it('a group the user opened stays open when the turn ends', () => {
    const c = [th('intent first'), tu('a', 'Running: ls'), tr('a'), tu('a2', 'Running: pwd'), tr('a2'),
      th('Now the next part.'), tu('b', 'Running: make')];
    const { rerender } = render(viewOf(c, true));
    const toggle = screen.getAllByTestId('group-toggle')[0];
    fireEvent.click(toggle);
    rerender(viewOf([...c, tr('b'), tx('done')], false));
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS * 3); });
    expect(screen.getAllByTestId('group-toggle')[0].getAttribute('aria-expanded')).toBe('true');
  });

  it('a group the user folded while it lingered stays folded', () => {
    const c = [tu('a', 'Running: ls'), tr('a'), tu('b', 'Running: pwd')];
    const { rerender } = render(viewOf(c, true));
    rerender(viewOf([...c, tr('b'), tx('done')], false));
    fireEvent.click(screen.getByTestId('group-toggle')); // fold during the linger
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS * 3); });
    expect(screen.queryByTestId('group-body')).toBeNull();
  });
});

describe('AssistantMessageView — a sub-agent on its strip', () => {
  const agent = { type: 'tool_use', id: 'ag', name: 'Agent', summary: 'Agent: Review spec', category: 'agent' } as ContentBlock;
  const sub = (i: number) => ({
    type: 'tool_use', id: `s${i}`, name: 'Bash', summary: `Running: grep -n "x${i}" f.ts`, category: 'bash',
    parentToolUseId: 'ag',
  }) as ContentBlock;
  const subs = (n: number) => Array.from({ length: n }, (_, i) => sub(i));
  const withProgress = (content: ContentBlock[], progress: Message['subAgentProgress'], isStreaming = true) => render(
    <ToastProvider>
      <AssistantMessageView message={{ ...msg(content), subAgentProgress: progress }} isStreaming={isStreaming} />
    </ToastProvider>,
  );

  it('running: its step count and the tool it is on, on the Agent strip; no nested level', () => {
    withProgress([agent, ...subs(8)], { ag: { parentToolUseId: 'ag', status: 'running', toolUses: 8, lastToolName: 'Read' } });
    const row = screen.getByTestId('step-row');
    expect(within(row).getByTestId('strip-kind').textContent).toBe('Agent');
    expect(row.textContent).toContain('Review spec');
    expect(within(row).getByTestId('agent-facts').textContent).toContain('8 steps');
    expect(within(row).getByTestId('agent-facts').textContent).toContain('now Read');
    expect(screen.getAllByTestId('step-row')).toHaveLength(1); // its own steps are not rows here
    expect(row.querySelector('.animate-spin')).not.toBeNull();
  });

  it('done, while the turn streams: the live summary is its verdict', () => {
    withProgress([agent, ...subs(3), { type: 'tool_result', toolUseId: 'ag', isError: false } as ContentBlock, tx('Review passed.')],
      { ag: { parentToolUseId: 'ag', status: 'completed', toolUses: 3, summary: 'PASS — no findings\nmore detail' } });
    expect(screen.getByTestId('agent-verdict').textContent).toContain('PASS — no findings');
    expect(screen.getByTestId('agent-verdict').textContent).not.toContain('more detail');
  });

  it('from history: the persisted result is its verdict, however long', () => {
    const verdict = 'BLOCK — two findings in the commit gate; see the report for both of them';
    renderView([agent, ...subs(3), res('ag', verdict), tx('Review done.')]);
    expect(screen.getByTestId('agent-verdict').textContent).toBe(`· ${verdict}`);
    expect(screen.queryByTestId('step-outcome')).toBeNull(); // said once
  });
});

describe('AssistantMessageView — turn frame and lane (AC5, AC6)', () => {
  it('the restatement is the alignment and the final answer gets a Result divider, both outside the lane', () => {
    renderView([tx('Three steps: export, sync, push.'), tu('a', 'Running: ls'), tx('checking'), tu('b', 'Running: pwd'),
      tx('All three done.')]);
    const align = screen.getByTestId('turn-alignment');
    const result = screen.getByTestId('turn-result');
    expect(align.textContent).toContain('Understanding');
    expect(result.textContent).toContain('Result');
    expect(result.textContent).toContain('All three done.');
    expect(align.closest('[data-lane="true"]')).toBeNull();
    expect(result.closest('[data-lane="true"]')).toBeNull();
    const lane = screen.getByText('checking').closest('[data-lane="true"]')!;
    expect(lane).not.toBeNull();
    expect(lane.className).toMatch(/border-l/);
    expect(lane.className).toMatch(/text-\[12\.5px\]/);
  });

  it('no divider while streaming', () => {
    renderView([tx('a'), tu('a', 'Running: ls'), tx('partial')], { isStreaming: true });
    expect(screen.queryByTestId('turn-result')).toBeNull();
  });

  it('a pure text reply has neither label nor lane', () => {
    renderView([tx('just an answer')]);
    expect(screen.queryAllByTestId('turn-alignment')).toHaveLength(0);
    expect(document.querySelectorAll('[data-lane="true"]')).toHaveLength(0);
  });

  it('never says "Started without restating the ask"', () => {
    const { container } = renderView([tu('a', 'Running: ls'), tu('b', 'Running: pwd'), tu('c', 'Running: make'), tx('done')]);
    expect(container.textContent).not.toContain('Started without restating');
    expect(screen.queryByTestId('turn-no-alignment')).toBeNull();
  });

  it('a finished plain turn lists what it changed, each opening its diff', () => {
    renderView([tu('a', 'Running: ls'), wr('b', '/r/a.ts'), tx('note'), wr('c', '/r/b.md'), tx('Done.')]);
    const line = screen.getByTestId('turn-files');
    expect([...line.querySelectorAll('[data-testid="file-chip"]')].map((c) => c.getAttribute('data-path')))
      .toEqual(['/r/a.ts', '/r/b.md']);
  });

  it('notes stay in stream order (OT03); a ":"-ended note titles the next group instead', () => {
    const { container } = renderView([tx('plan'), tu('a', 'Running: ls'), tx('found it'), tu('b', 'Running: pwd'),
      tx('Now the two docs:'), wr('c', '/r/a.md'), wr('d', '/r/b.md'), tx('done')]);
    const note = screen.getByText('found it');
    const groups = [...container.querySelectorAll('[data-testid="work-group"]')];
    expect(note.compareDocumentPosition(groups[1]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(groups[2] as HTMLElement).getByTestId('group-title').textContent).toBe('Now the two docs');
    expect(screen.queryByText('Now the two docs:')).toBeNull();
  });

  it('the answer-so-far keeps its DOM node when the next tool call turns it into a note (no remount)', () => {
    const c = [tx('Plan.'), tu('a', 'Running: ls'), tr('a'), tx('Found the store bug, now the fix')];
    const { rerender } = render(viewOf(c, true));
    const node = screen.getByText('Found the store bug, now the fix').closest('[data-scroll-anchor]');
    rerender(viewOf([...c, tu('b', 'Running: make')], true));
    const after = screen.getByText('Found the store bug, now the fix').closest('[data-scroll-anchor]');
    expect(after).toBe(node);
    expect(after!.closest('[data-lane="true"]')).not.toBeNull(); // it moved into the lane by class
  });

  it('a streaming answer ending in ":" stays the answer — never a WORKING title', () => {
    renderView([tx('Here are three options:')], { isStreaming: true });
    expect(screen.queryByTestId('work-group')).toBeNull();
    expect(screen.getByText('Here are three options:')).toBeTruthy();
  });

  it('a question inside a pipeline stage is reset to the answer\'s size', () => {
    const ask = {
      type: 'ask_user_question', toolUseId: 'q1',
      questions: [{ question: 'STAGE_Q pick one?', header: 'h', options: [{ label: 'A', description: 'a' }], multiSelect: false }],
    } as ContentBlock;
    const RUN = { type: 'pipeline_run', runId: 'run_t', profile: 'full', stages: ['evaluate', 'think'], requirement: 'Do X', at: 'T' } as ContentBlock;
    renderView([tx('Go.'), tu('c', 'Running: ls'), tr('c'), RUN, tu('a', 'Running: pwd'), tr('a'), ask], { isStreaming: true });
    const q = screen.getByText(/STAGE_Q/);
    expect(q.closest('.text-\\[15px\\]')).not.toBeNull();
  });

  it('the alignment note is never swallowed as a title', () => {
    renderView([tx('Plan for this:'), tu('a', 'Running: ls'), tu('b', 'Running: pwd'), tx('done')]);
    expect(screen.getByTestId('turn-alignment').textContent).toContain('Plan for this:');
  });
});

describe('AssistantMessageView — next steps and actions (AC9)', () => {
  const withSteps = [tu('a', 'Running: ls'), tx('Done.\n\n```next-steps\nStart the pipeline\nOnly fix labels\n```')];

  it('renders chips, never the raw block', () => {
    const { container } = renderView(withSteps, { isLastAssistant: true });
    expect(screen.getAllByTestId('next-step-chip')).toHaveLength(2);
    expect(container.textContent).not.toContain('```');
    expect(container.textContent).not.toContain('next-steps');
  });

  it('clicking the text fills the input; the arrow sends', () => {
    renderView(withSteps, { isLastAssistant: true });
    fireEvent.click(screen.getAllByTestId('next-step-fill')[0]);
    expect(injectSpy).toHaveBeenLastCalledWith({ text: 'Start the pipeline', focus: true, autoSend: false });
    fireEvent.click(screen.getAllByTestId('next-step-send')[1]);
    expect(injectSpy).toHaveBeenLastCalledWith({ text: 'Only fix labels', focus: true, autoSend: true });
  });

  it('no chips while streaming, on a non-last message, or without a block', () => {
    renderView(withSteps, { isLastAssistant: true, isStreaming: true });
    expect(screen.queryAllByTestId('next-step-chip')).toHaveLength(0);
    renderView(withSteps, { isLastAssistant: false });
    expect(screen.queryAllByTestId('next-step-chip')).toHaveLength(0);
    renderView([tx('plain')], { isLastAssistant: true });
    expect(screen.queryAllByTestId('next-step-chip')).toHaveLength(0);
  });

  it('while streaming, a half-arrived next-steps fence never renders', () => {
    const { container } = renderView([tu('a', 'Running: ls'), tx('answer\n```next-st')], { isStreaming: true, isLastAssistant: true });
    expect(container.textContent).not.toContain('```');
  });

  it('a turn that ended mid-process (notes, no answer) still shows the action row', () => {
    renderView([tx('working note'), tu('a', 'Running: ls')], { isLastAssistant: true });
    expect(screen.getByTitle('Copy message')).toBeTruthy();
  });

  it('hotkeys do not fire from a hidden (background) tab', () => {
    const { container } = render(
      <ToastProvider>
        <div aria-hidden="true">
          <AssistantMessageView message={msg([tx('Done.\n```next-steps\nhidden tab action\n```')])} isLastAssistant />
        </div>
      </ToastProvider>,
    );
    expect(container.querySelectorAll('[data-testid="next-step-chip"]').length).toBe(1);
    fireEvent.keyDown(window, { key: '1' });
    expect(injectSpy).not.toHaveBeenCalled();
  });

  it('hotkey 1 fills the first chip from a visible tab', () => {
    renderView([tx('Done.\n```next-steps\nvisible action\n```')], { isLastAssistant: true });
    fireEvent.keyDown(window, { key: '1' });
    expect(injectSpy).toHaveBeenLastCalledWith({ text: 'visible action', focus: true, autoSend: false });
  });
});
