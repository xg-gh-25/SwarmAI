/**
 * AssistantMessageView × turnView — the two-level render contract (chat display v8).
 *
 * What is tested (real AssistantMessageView, only leaf contexts mocked):
 * - FIRST level only: the restatement (plain text, no label), WORKING / WORKED
 *   blocks, the pipeline block, interrupts, RESULT. Everything between two of them
 *   is ONE block (AC1, AC2)
 * - a folded WORKED line is counts only — no file names (AC2)
 * - opened: thoughts, notes and tool rows in stream order (AC3)
 * - a tool row is a Codex-style verb + the call's real object; settled exploration
 *   calls are one Explored row (AC4)
 * - thoughts: the streaming one in full; a settled one is one "Thought" line whose
 *   text is one click away; the lane holds the process, the restatement and the
 *   answer sit outside (AC7)
 * - fold timing: a finished block folds FOLD_DELAY_MS later, keeping its live
 *   layout until then; a user's open/closed choice wins
 * - a running sub-agent's progress and a finished one's verdict sit on its row
 * - a trailing ```next-steps block renders as chips, never as raw text
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act, within } from '@testing-library/react';
import React from 'react';
import { AssistantMessageView } from '../AssistantMessageView';
import { FOLD_DELAY_MS } from '../../../../hooks/useLinger';
import { resetViewMemory } from '../WorkGroup';
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
/** Open every folded run of finished calls (an Explored row, a "×N" row), so each call is a row. */
const expandFolds = (root: HTMLElement = document.body) => {
  for (const f of [...within(root).queryAllByTestId('step-fold'), ...within(root).queryAllByTestId('step-explored')]) {
    fireEvent.click(within(f).getAllByRole('button')[0]);
  }
};
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
    // the 37 finished searches are one Explored row; the running call is its own row, last
    const explored = screen.getByTestId('step-explored');
    expect(explored.textContent).toContain('Search');
    expect(explored.textContent).toContain('"needle0"');
    expect(explored.textContent).toContain('+33'); // 4 objects named, the rest counted
    const rows = screen.getAllByTestId('step-row');
    expect(rows).toHaveLength(1);
    expect(rows[rows.length - 1].textContent).toContain('needle37');
    expect(rows[rows.length - 1].dataset.status).toBe('pending');
    // The running strip holds the one spinner.
    expect(container.querySelectorAll('.animate-spin')).toHaveLength(1);
    expect(rows[rows.length - 1].querySelector('.animate-spin')).not.toBeNull();
    expect(container.textContent).not.toContain('/Users/x/repo');
  });

  it('history (no tool_result, no text) → one WORKED block, no spinner, open on its newest 12; show all → 38', () => {
    // A turn with no text at all has nothing else to read: its last block starts open.
    const { container } = renderView(bashRun(38), { isStreaming: false });
    expect(screen.getAllByTestId('group-row')).toHaveLength(1);
    expect(screen.getByTestId('group-row').textContent).toContain('Worked');
    expect(container.querySelector('.animate-spin')).toBeNull();
    // the last call never finished (the turn ended on it): it stays its own row
    expect(screen.getByTestId('step-explored')).toBeTruthy();
    expandFolds();
    expect(screen.getAllByTestId('step-row')).toHaveLength(38);
    fireEvent.click(screen.getByTestId('group-toggle'));
    expect(screen.queryAllByTestId('step-row')).toHaveLength(0);
  });

  it('the whole process of a turn is ONE WORKED line before RESULT, the opening thought included (AC1)', () => {
    renderView([th('look'), tu('a', 'Running: ls'), tx('found it'), th('now the store'), tu('b', 'Running: pwd'), tx('the answer')]);
    expect(screen.getAllByTestId('work-group')).toHaveLength(1);
    expect(screen.queryByTestId('thought')).toBeNull(); // no intent thought: it is inside the fold
    expect(screen.queryByText('found it')).toBeNull(); // a note is second level: inside the fold
    fireEvent.click(screen.getByTestId('group-toggle'));
    expect(screen.getByText('found it')).toBeTruthy();
  });

  it('the folded line is counts only: steps, files changed, commands — no file names (AC2)', () => {
    const { container } = renderView([tu('a', 'Running: ls'), wr('b', '/r/src/TabView.tsx'), wr('c', '/r/src/x.py'), tx('done')]);
    const row = screen.getByTestId('group-row');
    expect(row.textContent).toContain('Worked');
    expect(screen.getByTestId('group-counts').textContent).toBe('· 3 steps · 2 files changed · 1 command');
    expect(row.querySelector('[data-testid="file-chip"]')).toBeNull();
    expect(container.querySelectorAll('button button')).toHaveLength(0);
  });

  it('a folded line counts what it has', () => {
    renderView([tu('a', 'Writing 3 todos'), tu('b', 'Writing 4 todos'), tx('done')]);
    expect(screen.getByTestId('group-counts').textContent).toBe('· 2 steps · 2 commands');
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

describe('AssistantMessageView — the answer-so-far and a turn without a result (option B)', () => {
  it('streaming: the answer-so-far is first level and the block above stays open (no toggle, live)', () => {
    renderView([tu('a', 'Running: ls'), tr('a'), tx('Checking the store next.')], { isStreaming: true });
    expect(screen.getByText('Checking the store next.').closest('[data-testid="work-group"]')).toBeNull();
    expect(screen.getByTestId('work-group').dataset.live).toBe('true');
    expect(screen.queryByTestId('group-toggle')).toBeNull();
  });

  it('a tool call after it: the text joins the same, still-live block', () => {
    const c = [tu('a', 'Running: ls'), tr('a'), tx('Checking the store next.')];
    const { rerender } = render(viewOf(c, true));
    const block = screen.getByTestId('work-group');
    rerender(viewOf([...c, tu('b', 'Running: pwd')], true));
    expect(screen.getByTestId('work-group')).toBe(block);
    expect(block.dataset.live).toBe('true');
    expect(screen.getByText('Checking the store next.').closest('[data-testid="work-group"]')).toBe(block);
    // the accepted residual (option B): the note moves in once, never shown twice
    expect(screen.getAllByText('Checking the store next.')).toHaveLength(1);
  });

  it('when the turn ends, the final answer is the RESULT, first level, once (it never was in a block)', () => {
    const work = [tu('a', 'Running: ls'), tr('a')];
    const { rerender } = render(viewOf([...work, tx('Done.')], true));
    expect(screen.getByText('Done.').closest('[data-testid="work-group"]')).toBeNull();
    rerender(viewOf([...work, tx('Done.')], false));
    expect(screen.getByTestId('turn-result').textContent).toContain('Done.');
    expect(screen.getAllByText('Done.')).toHaveLength(1);
    expect(screen.getByText('Done.').closest('[data-testid="work-group"]')).toBeNull();
  });

  it('streaming with no tool call yet: the text stays first level', () => {
    renderView([tx('Here is the answer.')], { isStreaming: true });
    expect(screen.getByText('Here is the answer.').closest('[data-testid="work-group"]')).toBeNull();
  });

  it('a finished turn that ends on a tool call shows its last note under the folded line', () => {
    renderView([tu('a', 'Running: ls'), tr('a'), tx('Fixed the cache; the reader is still open.'), tu('b', 'Running: git status'), tr('b')]);
    expect(screen.queryByTestId('group-body')).toBeNull(); // folded
    expect(screen.getByTestId('turn-tail').textContent).toContain('Fixed the cache; the reader is still open.');
    expect(screen.getByTestId('group-counts').textContent).toBe('· 2 steps · 2 commands');
    fireEvent.click(screen.getByTestId('group-toggle'));
    expect(screen.getAllByText('Fixed the cache; the reader is still open.')).toHaveLength(1);
    expect(screen.queryByTestId('turn-tail')).toBeNull(); // in its place now
  });

  it('the kept note follows an unrecovered failure shown under the folded line', () => {
    renderView([tu('a', 'Running: ls'), tr('a'), tx('Trying the build.'), tu('b', 'Running: make'), res('b', 'make: failed', true)]);
    const failure = screen.getByTestId('step-error');
    const tail = screen.getByTestId('turn-tail');
    expect(failure.compareDocumentPosition(tail) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('a finished turn with no text at all opens its last block', () => {
    renderView([tu('a', 'Running: ls'), tr('a'), tu('b', 'Running: pwd'), tr('b')]);
    expect(screen.getByTestId('group-body')).toBeTruthy();
    expandFolds();
    expect(screen.getAllByTestId('step-row')).toHaveLength(2);
  });

  it('no tail when the only text is the intent restatement (the last block opens instead)', () => {
    renderView([tx('I read it as: fix the cache.'), tu('a', 'Running: ls'), tr('a'), tu('b', 'Running: pwd'), tr('b')]);
    expect(screen.queryByTestId('turn-tail')).toBeNull();
    expect(screen.getByTestId('turn-restatement').textContent).toContain('fix the cache');
    expect(screen.getByTestId('group-body')).toBeTruthy();
  });

  it('a pipeline turn stopped after a stage row: the note in the stage does not count, the last block opens', () => {
    const stage = { type: 'pipeline_stage', runId: 'r', stage: 'build', status: 'completed', ordinal: 5, position: 4, total: 8,
      profile: 'full', next: 'review', nextOrdinal: 6, gate: null, summary: 'ok', at: '2026-10-18T00:00:00Z' } as ContentBlock;
    renderView([tu('a', 'Running: ls'), tr('a'), tx('inside the stage'), tu('b', 'Running: pwd'), tr('b'), stage, tu('c', 'Running: make'), tr('c')]);
    expect(screen.queryByTestId('turn-tail')).toBeNull();
    // the work after the last row stays under it, and that stage starts open
    const body = screen.getByTestId('stage-body');
    expandFolds(body);
    expect(body.textContent).toContain('make');
    expect(screen.queryAllByTestId('group-body')).toHaveLength(0);
  });

  it('no top-level tail for a note inside a stage (the stage row is the fold)', () => {
    const stage = { type: 'pipeline_stage', runId: 'r', stage: 'build', status: 'completed', ordinal: 5, position: 4, total: 8,
      profile: 'full', next: 'review', nextOrdinal: 6, gate: null, summary: 'ok', at: '2026-10-18T00:00:00Z' } as ContentBlock;
    renderView([tu('a', 'Running: ls'), tr('a'), tx('inside the stage'), tu('b', 'Running: pwd'), tr('b'), stage]);
    expect(screen.queryByTestId('turn-tail')).toBeNull();
  });

  it('a turn with a RESULT keeps its notes inside the folded block', () => {
    renderView([tu('a', 'Running: ls'), tr('a'), tx('a note'), tu('b', 'Running: pwd'), tr('b'), tx('All done.')]);
    expect(screen.queryByText('a note')).toBeNull();
    expect(screen.queryByTestId('turn-tail')).toBeNull();
    expect(screen.getByTestId('turn-result').textContent).toContain('All done.');
  });
});

describe('AssistantMessageView — one strip per tool call (AC3)', () => {
  it('a row is the real tool and command; a failure one line below, in red (AC4)', () => {
    renderView([tu('a', 'Running: npm test'), res('a', '12 passed in 3.1s'),
      tu('b', 'Running: make'), res('b', 'make: *** no rule to make target build, stop.', true), tu('c', 'Running: ls')], { isStreaming: true });
    const rows = screen.getAllByTestId('step-row');
    expect(within(rows[0]).getByTestId('strip-tool').textContent).toBe('Ran');
    expect(within(rows[0]).getByTestId('strip-target').textContent).toBe('npm test');
    expect(rows[0].textContent).not.toContain('12 passed'); // a command's own output is not the row
    const err = within(rows[1]).getByTestId('step-error');
    expect(err.className).toMatch(/text-red-400/);
    expect(err.getAttribute('title')).toBe('make: *** no rule to make target build, stop.');
  });

  it('a long outcome is not repeated on the row', () => {
    const long = 'diff --git a/src/x.ts b/src/x.ts index 1234..5678 100644 --- a/src/x.ts';
    renderView([tu('a', 'Running: git diff'), res('a', long), tu('b', 'Running: ls')], { isStreaming: true });
    expect(screen.queryByText(long)).toBeNull();
  });

  it('a row names what ran; the whole command — only the leading cd and a timeout wrapper gone — is one click away (AC4)', () => {
    const cmd = 'Running: R=run_df73d970; python3 -c "import json; print(1)"';
    renderView([tu('a', cmd), tu('b', "Running: cd /a && perl -e 'alarm 60; exec @ARGV' node -e \"x()\""), tx('done')]);
    fireEvent.click(screen.getByTestId('group-toggle'));
    expandFolds();
    expect(screen.getAllByTestId('strip-target').map((e) => e.textContent).join(' | ')).toBe('python3 -c "import json; print(1)" | node -e "x()"');
    fireEvent.click(within(screen.getAllByTestId('step-row')[0]).getByTestId('row-toggle'));
    expect(screen.getByTestId('step-command').textContent).toBe('R=run_df73d970; python3 -c "import json; print(1)"');
    expect(document.body.textContent).not.toContain('inline script');
  });

  it('an unrecognised tool without a summary names itself once', () => {
    renderView([{ type: 'tool_use', id: 'z', name: 'Frobnicate', category: 'fallback' } as ContentBlock]);
    // no text in the turn: its block starts open
    expect(screen.getByTestId('step-row').textContent!.match(/Frobnicate/g)).toHaveLength(1);
  });

  it('an unrecognised tool shows its own name and summary', () => {
    renderView([{ type: 'tool_use', id: 'z', name: 'Weird', summary: 'Weird: some context', category: 'fallback' } as ContentBlock]);
    const row = screen.getByTestId('step-row');
    expect(within(row).getByTestId('strip-tool').textContent).toBe('Weird');
    // the generic `<name>: ctx` form: the name once, then its context
    expect(within(row).getByTestId('strip-target').textContent).toBe('some context');
    expect(row.textContent!.match(/Weird/g)).toHaveLength(1);
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

describe('AssistantMessageView — thoughts (AC4, chat display v8)', () => {
  const SIX = [tu('a', 'Running: ls'), tr('a'), tu('a1', 'Running: pwd'), tr('a1'), tu('a2', 'Running: id'), tr('a2')];

  it('a finished non-tool reply folds its thought into one "Thought" line, the text one click away', () => {
    renderView([th('deliberating'), tx('the answer')]);
    expect(screen.queryByTestId('thought-text')).toBeNull();
    fireEvent.click(screen.getByTestId('group-toggle'));
    expect(screen.queryByTestId('thought-text')).toBeNull();
    expect(screen.getByTestId('thought-toggle').textContent).toBe('Thought');
    fireEvent.click(screen.getByTestId('thought-toggle'));
    expect(screen.getByTestId('thought-text').textContent).toBe('deliberating');
  });

  it('no Show all / Show less anywhere; a thought shown is shown whole, never clamped', () => {
    const { container } = renderView([th(LONG), ...SIX, th(LONG), tu('b', 'Running: pwd'), tx('ok')], { isStreaming: true });
    expect(container.textContent).not.toMatch(/Show all|Show less|Show more/);
    for (const text of screen.queryAllByTestId('thought-text')) {
      expect(text.textContent).toBe(LONG);
      expect(text.style.webkitLineClamp).toBe('');
    }
  });

  it('a long thought, turn over: one line; the text opens and closes in place', () => {
    renderView([...SIX, th(LONG), tx('ok')]);
    fireEvent.click(screen.getByTestId('group-toggle'));
    const toggle = screen.getByTestId('thought-toggle');
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(toggle);
    expect(screen.getByTestId('thought-text').textContent).toBe(LONG);
    expect(screen.getByTestId('thought-toggle').getAttribute('aria-expanded')).toBe('true');
    fireEvent.click(screen.getByTestId('thought-toggle'));
    expect(screen.queryByTestId('thought-text')).toBeNull();
  });

  it('the text is not the control: clicking inside it (selecting) does not close it', () => {
    renderView([...SIX, th(LONG), tx('ok')]);
    fireEvent.click(screen.getByTestId('group-toggle'));
    fireEvent.click(screen.getByTestId('thought-toggle'));
    fireEvent.click(screen.getByTestId('thought-text'));
    expect(screen.getByTestId('thought-text').textContent).toBe(LONG);
  });

  it('while the turn streams a thought stays whole until 2s after the next item arrives, then is one line', () => {
    vi.useFakeTimers();
    try {
      const head = [...SIX, th(LONG)];
      const { rerender } = render(viewOf(head, true));
      rerender(viewOf([...head, tu('b', 'Running: pwd')], true));
      // the next item has arrived: nothing changed shape yet
      expect(screen.getByTestId('thought-text').textContent).toBe(LONG);
      act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 5); });
      expect(screen.queryByTestId('thought-text')).toBeNull();
      expect(screen.getByTestId('thought-toggle').textContent).toBe('Thought');
    } finally {
      vi.useRealTimers();
    }
  });

  it('a thought this view saw stream for 5s or more says how long — also after a remount (tab switch); one it never saw stream says nothing', () => {
    vi.useFakeTimers();
    try {
      const { rerender, unmount } = render(viewOf([...SIX, th('weighing it')], true));
      act(() => { vi.advanceTimersByTime(7000); });
      rerender(viewOf([...SIX, th('weighing it, still')], true)); // a delta at 7s
      rerender(viewOf([...SIX, th('weighing it, done'), tu('b', 'Running: make')], true));
      act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 5); });
      expect(screen.getByTestId('thought-seconds').textContent).toBe('· 7s');
      unmount();
      // a tab switch: the same message mounts again, the duration stays
      const back = render(viewOf([...SIX, th('weighing it, done'), tu('b', 'Running: make'), tr('b'), tx('ok')], false));
      fireEvent.click(screen.getByTestId('group-toggle'));
      fireEvent.click(within(screen.getAllByTestId('step-row')[3]).getByTestId('row-toggle'));
      expect(screen.getByTestId('thought-seconds').textContent).toBe('· 7s');
      back.unmount();
      // a turn this view never saw stream (history after a reload): no duration
      resetViewMemory();
      renderView([th('weighing it, done'), ...SIX, tx('ok')]);
      fireEvent.click(screen.getByTestId('group-toggle'));
      expect(screen.getByTestId('thought-toggle').textContent).toBe('Thought');
      expect(screen.queryByTestId('thought-seconds')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
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
    const { rerender } = render(viewOf([...SIX, th(LONG), tu('b', 'Running: pwd')], true));
    fireEvent.click(screen.getByTestId('thought-toggle'));
    rerender(viewOf([...SIX, th(LONG), tu('b', 'Running: pwd'), tr('b'), tx('done')], false));
    expect(screen.getByTestId('thought-text').textContent).toBe(LONG);
    expect(screen.getByTestId('thought-toggle').getAttribute('aria-expanded')).toBe('true');
  });
});

describe('AssistantMessageView — fold timing', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it('the opening thought folds with the work, FOLD_DELAY_MS after the turn ends (no intent exception)', () => {
    const { rerender } = render(viewOf([th('step one')], true));
    expect(screen.getByTestId('thought').dataset.live).toBe('true');
    // the first tool call arrives while streaming, then the turn ends
    rerender(viewOf([th('step one, then two'), tu('a', 'Running: ls')], true));
    rerender(viewOf([th('step one, then two'), tu('a', 'Running: ls'), tr('a'), tx('done')], false));
    expect(screen.getByTestId('thought-text').textContent).toBe('step one, then two'); // not at once
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS * 5); });
    expect(screen.queryByTestId('thought')).toBeNull();
    fireEvent.click(screen.getByTestId('group-toggle'));
    fireEvent.click(screen.getByTestId('thought-toggle'));
    expect(screen.getByTestId('thought-text').textContent).toBe('step one, then two');
  });

  it('in the 2s after it finishes, a group keeps its live layout (every strip), then folds', () => {
    // failed calls never fold, so each stays a row
    const many = Array.from({ length: 15 }, (_, i) => [tu(`s${i}`, `Running: cat f${i}.ts`), res(`s${i}`, 'no', true)]).flat();
    const { rerender } = render(viewOf([...many, tu('z', 'Running: date')], true));
    // live: nothing is cut off the top
    expect(screen.getAllByTestId('step-row')).toHaveLength(16);
    // The turn ends: the block stops being live (text after work while streaming is
    // the live block's own last item, so only the end of the turn ends it).
    rerender(viewOf([...many, tu('z', 'Running: date'), res('z', 'no', true), tx('done')], false));
    const body = screen.getByTestId('group-body');
    expect(body.dataset.liveLayout).toBe('true');
    expect(body.textContent).toContain('f14.ts');
    expect(body.textContent).toContain('f0.ts'); // the same rows as while live: nothing reshuffled
    expect(screen.getAllByTestId('step-row')).toHaveLength(16);
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS); });
    expect(screen.queryByTestId('group-body')).toBeNull(); // folded in one move
    fireEvent.click(screen.getByTestId('group-toggle'));
    expect(screen.getByTestId('group-body').dataset.liveLayout).toBe('false'); // opened by hand: first strips
    expect(screen.getByTestId('group-body').textContent).toContain('f0.ts');
  });

  it('a block the user opened stays open when the turn ends', () => {
    const ask = { type: 'ask_user_question', toolUseId: 'q1', questions: [] } as unknown as ContentBlock;
    const c = [tu('a', 'Running: ls'), tr('a'), tu('a2', 'Running: pwd'), tr('a2'), ask, tu('b', 'Running: make')];
    const { rerender } = render(viewOf(c, true));
    fireEvent.click(screen.getAllByTestId('group-toggle')[0]);
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
    expect(within(row).getByTestId('strip-tool').textContent).toBe('Agent');
    expect(row.textContent).toContain('Review spec');
    expect(within(row).getByTestId('agent-facts').textContent).toContain('8 steps');
    expect(within(row).getByTestId('agent-facts').textContent).toContain('now Read');
    expect(screen.getAllByTestId('step-row')).toHaveLength(1); // its own steps are not rows here
    expect(row.querySelector('.animate-spin')).not.toBeNull();
  });

  it('done, while the turn streams: its verdict is in its detail, not on the row', () => {
    withProgress([agent, ...subs(3), { type: 'tool_result', toolUseId: 'ag', isError: false } as ContentBlock, tx('Review passed.')],
      { ag: { parentToolUseId: 'ag', status: 'completed', toolUses: 3, summary: 'PASS — no findings\nmore detail' } });
    // still streaming: the block is the open WORKING card (its rows are shown)
    const row = screen.getAllByTestId('step-row').find((r) => r.dataset.tool === 'Agent')!;
    expect(row.textContent).not.toContain('PASS — no findings');
    fireEvent.click(within(row).getByTestId('agent-toggle'));
    expect(screen.getByTestId('agent-result').textContent).toBe('PASS — no findings\nmore detail');
  });

  it('from history: the persisted result is its verdict, whole, in its detail', () => {
    const verdict = 'BLOCK — two findings in the commit gate; see the report for both of them';
    renderView([agent, ...subs(3), res('ag', verdict), tx('Review done.')]);
    fireEvent.click(screen.getByTestId('group-toggle'));
    fireEvent.click(screen.getByTestId('agent-toggle'));
    expect(screen.getByTestId('agent-result').textContent).toBe(verdict);
  });
});

describe('AssistantMessageView — turn frame and lane (AC5, AC6)', () => {
  it('the restatement is plain text (no label) and the final answer gets a Result divider, both outside the lane', () => {
    renderView([tx('Three steps: export, sync, push.'), tu('a', 'Running: ls'), tx('checking'), tu('b', 'Running: pwd'),
      tx('All three done.')]);
    const intent = screen.getByTestId('turn-restatement');
    const result = screen.getByTestId('turn-result');
    expect(intent.textContent).not.toContain('Intent');
    expect(screen.queryByText('Understanding')).toBeNull();
    expect(result.textContent).toContain('All three done.');
    expect(intent.closest('[data-lane="true"]')).toBeNull();
    expect(result.closest('[data-lane="true"]')).toBeNull();
    const lane = screen.getByTestId('work-group').closest('[data-lane="true"]')!;
    expect(lane.className).toMatch(/border-l/);
  });

  it('no divider while streaming', () => {
    renderView([tx('a'), tu('a', 'Running: ls'), tx('partial')], { isStreaming: true });
    expect(screen.queryByTestId('turn-result')).toBeNull();
  });

  it('a pure text reply has neither label nor lane', () => {
    renderView([tx('just an answer')]);
    expect(screen.queryAllByTestId('turn-restatement')).toHaveLength(0);
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

  it('notes stay in stream order inside the block (OT03); none of them is a title (AC3)', () => {
    renderView([tx('plan'), tu('a', 'Running: ls'), tx('found it'), tu('b', 'Running: pwd'),
      tx('Now the two docs:'), wr('c', '/r/a.md'), wr('d', '/r/b.md'), tx('done')]);
    fireEvent.click(screen.getByTestId('group-toggle'));
    const body = screen.getByTestId('group-body');
    // two edits of different files are two rows: only identical calls fold
    expect([...body.children].map((c) => c.getAttribute('data-testid'))).toEqual(['step-row', 'group-note', 'step-row', 'group-note', 'step-row', 'step-row']);
    expect(screen.getByText('Now the two docs:')).toBeTruthy();
  });

  it('the answer-so-far moves into the block once a tool call follows it — still shown', () => {
    const c = [tx('Plan.'), tu('a', 'Running: ls'), tr('a'), tx('Found the store bug, now the fix')];
    const { rerender } = render(viewOf(c, true));
    expect(screen.getByText('Found the store bug, now the fix').closest('[data-testid="work-group"]')).toBeNull();
    rerender(viewOf([...c, tu('b', 'Running: make')], true));
    expect(screen.getByText('Found the store bug, now the fix').closest('[data-testid="work-group"]')).not.toBeNull();
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

  it('a turn that starts with a tool result (compacted tail) shows no INTENT (AC5)', () => {
    renderView([tr('z'), th(`I'll inline a fallback expression here. ${'x'.repeat(170)}`), tu('a', 'Running: ls'), tx('done')]);
    expect(screen.queryByTestId('turn-restatement')).toBeNull();
    expect(document.body.textContent).not.toContain('Intent');
  });

  it('with a restatement, the opening thought is inside the work; nothing is labelled Intent (AC5)', () => {
    renderView([th('plan it'), tx('I read it as: fix X.'), tu('a', 'Running: ls'), tx('done')]);
    expect(screen.queryAllByText('Intent')).toHaveLength(0);
    expect(screen.getByTestId('turn-restatement').textContent).toContain('I read it as');
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
