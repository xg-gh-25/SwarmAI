/**
 * AssistantMessageView × turnZones — the rendered first level of a chat turn (chat
 * turn spec v6, Knowledge/Designs/2026-10-19-chat-turn-display-spec.md).
 *
 * What is tested (real AssistantMessageView + turnView + turnZones; only leaf
 * contexts and the chat service mocked):
 * - AC1 the first thoughts are the THINKING receipt: full text while streaming, kept
 *   first after the turn as `THOUGHTS · N`, folded FOLD_DELAY_MS later
 * - AC2 THOUGHTS → restatement → WORKED → RESULT: a note between calls stays inside
 *   its group where it was written (it never splits one); a thought between calls is
 *   one `✻ Thought` row in the strip column
 * - AC2b each note carries the work after it under a ▸: while streaming the latest is
 *   open and an earlier one folds FOLD_DELAY_MS after the next note (never on the spot,
 *   never while the user reads); a finished group opened later shows every note folded
 * - AC3 every group but RESULT toggles from its header; headers count steps and
 *   commands, never files; a turn with no calls has no RESULT region; the RESULT
 *   region carries no visible heading (only its aria-label)
 * - AC4 one light strip per call: the tool's own name (MCP prefix dropped), the
 *   backend display object as the hover; a failed call looks like the others (no red)
 * - AC5 sub-agents that ran at the same time are ONE Agents panel even with a call
 *   between their spawns; a running agent's ✕ stops only it (chatService.stopTask);
 *   a group holding a background agent the daemon still runs never folds it away
 * - AC6 a question / permission is a first-level card, outside every group
 * - AC7 a pipeline turn: the pre-run work above `Pipeline started · <profile>`, only
 *   started stages, a re-run stage is one group with `round 2`, a gate is a verdict
 *   row with its earlier rounds, a run that stopped mid-way says `Stopped at X`; a
 *   direct run shows none of it
 * - AC9 no spinner while streaming; a turn stopped by the user says so
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, within, act } from '@testing-library/react';
import { AssistantMessageView } from '../AssistantMessageView';
import { ToastProvider } from '../../../../contexts/ToastContext';
import { FOLD_DELAY_MS } from '../../../../hooks/useLinger';
import { backgroundAgentsStore } from '../../../../stores/backgroundAgentsStore';
import { ReadingContext } from '../../readingContext';
import { backgroundAgentsStore } from '../../../../stores/backgroundAgentsStore';
import { chatService } from '../../../../services/chat';
import type { Message, ContentBlock, SubAgentProgress } from '../../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, fallback: unknown) => fallback }),
}));
vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({ health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 }, triggerHealthCheck: vi.fn() }),
}));
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

const th = (thinking: string): ContentBlock => ({ type: 'thinking', thinking }) as ContentBlock;
const tx = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;
const edit = (id: string, path: string): ContentBlock =>
  ({ type: 'tool_use', id, name: 'Edit', summary: `Editing ${path}`, category: 'edit', display: { tool: 'Edit', object: path, key: `e-${path}` } }) as ContentBlock;
const bash = (id: string, cmd: string): ContentBlock =>
  ({ type: 'tool_use', id, name: 'Bash', summary: `Running: ${cmd}`, category: 'bash' }) as ContentBlock;
const tr = (id: string, isError = false): ContentBlock =>
  ({ type: 'tool_result', toolUseId: id, content: isError ? 'boom' : 'ok', isError, truncated: false }) as ContentBlock;
const agent = (id: string, desc: string): ContentBlock =>
  ({ type: 'tool_use', id, name: 'Agent', summary: `Agent: ${desc}`, category: 'agent' }) as ContentBlock;
const RUN = (profile = 'full'): ContentBlock => ({
  type: 'pipeline_run', runId: 'run_z', profile, stages: ['evaluate', 'plan', 'build', 'deliver'],
  requirement: 'REQ', current: null, at: '2026-10-19T10:00:00+08:00',
}) as ContentBlock;
const row = (stage: string, status: 'in_progress' | 'completed', over: Record<string, unknown> = {}): ContentBlock => ({
  type: 'pipeline_stage', runId: 'run_z', stage, status, ordinal: null, position: null, total: 4, profile: 'full',
  next: null, nextOrdinal: null, gate: /^gate\d$/.test(stage) ? { name: stage, verdict: 'PASS' } : null,
  summary: null, at: '2026-10-19T10:00:00+08:00', ...over,
}) as ContentBlock;

function view(content: ContentBlock[], isStreaming = false, extra: Partial<Message> = {}, props: Record<string, unknown> = {}) {
  const message: Message = { id: 'm1', role: 'assistant', content, timestamp: '2026-10-19T10:00:00+08:00', isError: false, ...extra };
  return <ToastProvider><AssistantMessageView message={message} isStreaming={isStreaming} isLastAssistant sessionId="s1" {...props} /></ToastProvider>;
}
const FIRST = ['zone-thinking', 'turn-restatement', 'zone-phase', 'turn-note', 'pipeline-start', 'zone-stage', 'zone-gate',
  'turn-result', 'turn-answer', 'turn-stopped'].map((id) => `[data-testid="${id}"]`).join(',');
const firstLevel = (root: HTMLElement) => [...root.querySelectorAll(FIRST)]
  .filter((el) => !el.parentElement?.closest('[data-testid="zone-stage"],[data-testid="zone-phase"]'))
  .map((el) => el.getAttribute('data-testid'));

describe('AC1 — the first thoughts are the receipt', () => {
  it('streaming: THINKING with the full text, nothing else yet', () => {
    const { container } = render(view([th('reading the ask\nsecond line')], true));
    const z = screen.getByTestId('zone-thinking');
    expect(z.textContent).toContain('Thinking');
    expect(z.textContent).toContain('second line');
    expect(firstLevel(container)).toEqual(['zone-thinking']);
    expect(screen.queryByTestId('zone-phase')).toBeNull();
  });

  it('after the turn: THOUGHTS · N stays first, folded FOLD_DELAY_MS later; adjacent thinking blocks are one thought', () => {
    vi.useFakeTimers();
    const blocks = [th('first'), th('second'), tx('I read it as X.'), bash('a', 'ls'), tr('a'), tx('Done.')];
    const r = render(view(blocks, true));
    r.rerender(view(blocks, false));
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    const z = screen.getByTestId('zone-thinking');
    expect(within(z).getByTestId('thinking-toggle').textContent).toMatch(/Thoughts.*1/);
    expect(within(z).queryByText(/second/)).toBeNull();
    expect(firstLevel(r.container)[0]).toBe('zone-thinking');
    fireEvent.click(within(z).getByTestId('thinking-toggle'));
    const [only] = within(screen.getByTestId('zone-thinking')).getAllByTestId('thought');
    expect(only.textContent).toBe('first\n\nsecond');
  });
});

describe('AC2 — notes stay in their group', () => {
  const turn = [th('look'), tx('I read it as X.'), bash('a', 'cat a.ts'), tr('a'), th('so the store writes'),
    bash('b', 'grep -rn foo src'), tr('b'), tx('Build is green, now the review.'), bash('c', 'make test'), tr('c'), tx('All done.')];

  it('THOUGHTS → restatement → WORKED → RESULT; the note is inside WORKED', () => {
    const { container } = render(view(turn, false));
    expect(firstLevel(container)).toEqual(['zone-thinking', 'turn-restatement', 'zone-phase', 'turn-result']);
    // the answer carries no visible "Result" heading: the fold above and the gap set it
    // apart, and a heading would claim "final" while background agents still run
    expect(screen.getByTestId('turn-result').textContent).not.toMatch(/Result/);
    expect(screen.getByTestId('turn-result').getAttribute('aria-label')).toBe('Result');
    const phase = screen.getByTestId('zone-phase');
    fireEvent.click(within(phase).getByTestId('group-toggle'));
    const note = within(screen.getByTestId('zone-phase')).getByTestId('phase-text');
    expect(note.textContent).toContain('Build is green, now the review.');
  });

  it('a thought between calls stays inside its group (carried into the merged strip, spec §3.1 2026-10-25)', () => {
    render(view(turn, false));
    const first = screen.getAllByTestId('zone-phase')[0];
    fireEvent.click(within(first).getByTestId('group-toggle'));
    const phase = screen.getAllByTestId('zone-phase')[0];
    fireEvent.click(within(within(phase).getByTestId('tool-strip')).getAllByRole('button')[0]);
    expect(within(phase).getByText(/so the store writes/)).toBeTruthy();
  });

  it('a thought is one ✻ Thought row in the strips’ column; the one being written shows its first line, not all of it', () => {
    vi.useFakeTimers();
    const head = [tx('go'), bash('a', 'cat a.ts'), tr('a'), th('first line\nsecond line')];
    const r = render(view(head, true));
    // being written: process prose, one line (attention tiers §6; was: open in full)
    expect(screen.getByTestId('thought').getAttribute('data-folded')).toBe('true');
    expect(screen.getByTestId('thought').textContent).not.toContain('second line');
    // the next call starts, then settles: the row does not change
    const blocks = [...head, bash('b', 'make'), tr('b')];
    r.rerender(view(blocks, true));
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    r.rerender(view(blocks, true));
    const thought = screen.getByTestId('thought');
    expect(thought.textContent).toContain('✻ Thought');
    expect(thought.textContent).toContain('first line');
    expect(thought.textContent).not.toContain('second line');
    // the same column as the strips around it: no gap breaks the strips apart
    expect(within(thought.parentElement!).getAllByTestId('tool-strip')).toHaveLength(2);
    fireEvent.click(within(thought).getByTestId('thought-toggle'));
    expect(screen.getByTestId('thought-text').textContent).toContain('second line');
    // open, the first line is shown once (in the text), not again in the row
    expect(screen.getByTestId('thought').textContent!.split('first line')).toHaveLength(2);
  });
});

describe('AC2b — a note carries the work after it; earlier ones fold', () => {
  const blocks = [tx('go'), bash('a', 'cat lead.ts'), tr('a'), tx('Now the runner:'), bash('b', 'edit runner.py'), tr('b'),
    tx('Now the landing:'), bash('c', 'edit land.py'), tr('c')];
  const strips = () => screen.queryAllByTestId('tool-strip').map((s) => s.textContent ?? '');
  const isShown = (cmd: string) => strips().some((t) => t.includes(cmd));

  it('while streaming: the latest note is open, an earlier one folds FOLD_DELAY_MS after the next (not on the spot)', () => {
    vi.useFakeTimers();
    const r = render(view(blocks.slice(0, 6), true));
    expect(isShown('runner.py')).toBe(true);
    r.rerender(view(blocks, true));
    expect(isShown('runner.py')).toBe(true); // never on the spot
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    r.rerender(view(blocks, true));
    expect(isShown('runner.py')).toBe(false);
    expect(isShown('land.py')).toBe(true); // the latest stays open
    expect(isShown('lead.ts')).toBe(true); // the work before the first note has no note to hang under
    const notes = screen.getAllByTestId('phase-text').map((n) => n.textContent ?? '');
    expect(notes).toHaveLength(2);
    expect(notes[0]).toContain('Now the runner:');
    expect(notes[1]).toContain('Now the landing:');
    // ▸ opens it again, under the note
    fireEvent.click(screen.getAllByTestId('segment-toggle')[0]);
    expect(isShown('runner.py')).toBe(true);
  });

  it('a finished group opened later shows every note folded', () => {
    vi.useFakeTimers();
    const done = [...blocks, tx('All done.')];
    const r = render(view(done, true));
    r.rerender(view(done, false));
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    fireEvent.click(within(screen.getByTestId('zone-phase')).getByTestId('group-toggle'));
    expect(isShown('lead.ts')).toBe(true);
    expect(isShown('runner.py')).toBe(false);
    expect(isShown('land.py')).toBe(false);
    expect(screen.getAllByTestId('segment-toggle')).toHaveLength(2);
  });

  it('a later note with no work yet does not fold the work before it', () => {
    vi.useFakeTimers();
    const live = [...blocks, tx('Now checking the result:')];
    const r = render(view(live, true));
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    r.rerender(view(live, true));
    expect(isShown('land.py')).toBe(true); // the latest work stays open
    expect(isShown('runner.py')).toBe(false);
    // the newest note starts work: the one before folds FOLD_DELAY_MS later
    const working = [...live, bash('d', 'make check'), tr('d')];
    r.rerender(view(working, true));
    expect(isShown('land.py')).toBe(true);
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    r.rerender(view(working, true));
    expect(isShown('land.py')).toBe(false);
    expect(isShown('make check')).toBe(true);
  });

  it('a segment with a background agent still running does not fold, until it ends', () => {
    vi.useFakeTimers();
    act(() => {
      backgroundAgentsStore.apply('s1', [
        { toolUseId: 'g1', description: 'blind review A', status: 'running' },
        { toolUseId: 'g2', description: 'blind review B', status: 'running' },
      ]);
    });
    // the launch acks return at once: in the stream the calls look finished
    const live = [tx('go'), bash('a', 'cat lead.ts'), tr('a'), tx('Two blind reviews:'),
      agent('g1', 'blind review A'), agent('g2', 'blind review B'), tr('g1'), tr('g2'),
      tx('Meanwhile, the next part:'), bash('n', 'edit next.py'), tr('n')];
    const panels = () => screen.queryAllByText(/blind review A/).length;
    const r = render(view(live, true));
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    r.rerender(view(live, true));
    expect(panels()).toBeGreaterThan(0); // still running: the AGENTS panel stays where it started
    act(() => {
      backgroundAgentsStore.apply('s1', [
        { toolUseId: 'g1', description: 'blind review A', status: 'completed' },
        { toolUseId: 'g2', description: 'blind review B', status: 'completed' },
      ]);
    });
    r.rerender(view(live, true));
    expect(panels()).toBeGreaterThan(0); // never on the spot
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    r.rerender(view(live, true));
    expect(panels()).toBe(0);
    act(() => { backgroundAgentsStore.apply('s1', []); });
  });

  it('the turn ended with a background agent still running: WORKED stays open and shows it, it folds after the agent ends', () => {
    vi.useFakeTimers();
    act(() => {
      backgroundAgentsStore.apply('s1', [{ toolUseId: 'g1', description: 'blind review A', status: 'running' }]);
    });
    const launched = { type: 'tool_result', toolUseId: 'g1', content: 'Async agent launched', isError: false, truncated: false,
      subagent: { status: 'background' } } as ContentBlock;
    const turn = [tx('go'), bash('a', 'cat lead.ts'), tr('a'), agent('g1', 'blind review A'), launched,
      tx('The review runs; I fix what it finds when it is back.')];
    const shown = () => screen.queryAllByText(/blind review A/).length > 0;
    const r = render(view(turn, true));
    r.rerender(view(turn, false));
    act(() => { vi.advanceTimersByTime(5 * FOLD_DELAY_MS); });
    r.rerender(view(turn, false));
    expect(shown()).toBe(true); // the turn's fold did not hide the running agent
    act(() => { backgroundAgentsStore.apply('s1', [{ toolUseId: 'g1', description: 'blind review A', status: 'completed' }]); });
    r.rerender(view(turn, false));
    expect(shown()).toBe(true); // never on the spot
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    r.rerender(view(turn, false));
    expect(shown()).toBe(false);
    act(() => { backgroundAgentsStore.apply('s1', []); });
  });

  it('while the user reads (scrolled up), nothing new folds', () => {
    vi.useFakeTimers();
    const reading = (n: number) => <ReadingContext.Provider value>{view(blocks.slice(0, n), true)}</ReadingContext.Provider>;
    const r = render(reading(6));
    r.rerender(reading(blocks.length));
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    r.rerender(reading(blocks.length));
    expect(isShown('runner.py')).toBe(true);
    // back to following: it folds FOLD_DELAY_MS later
    r.rerender(view(blocks, true));
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    r.rerender(view(blocks, true));
    expect(isShown('runner.py')).toBe(false);
  });

  it('scrolling up keeps what already folded folded (reading freezes, it does not unfold)', () => {
    vi.useFakeTimers();
    // TabView always provides ReadingContext; toggling only its value never remounts
    const at = (b: ContentBlock[], away: boolean) => <ReadingContext.Provider value={away}>{view(b, true)}</ReadingContext.Provider>;
    const reading = (b: ContentBlock[]) => at(b, true);
    const following = (b: ContentBlock[]) => at(b, false);
    const r = render(following(blocks.slice(0, 6)));
    r.rerender(following(blocks));
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    r.rerender(following(blocks));
    expect(isShown('runner.py')).toBe(false); // folded while following
    r.rerender(reading(blocks));
    expect(isShown('runner.py')).toBe(false); // the user scrolls up: it stays folded
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    r.rerender(reading(blocks));
    expect(isShown('runner.py')).toBe(false);
    r.rerender(following(blocks));
    expect(isShown('runner.py')).toBe(false); // and after coming back
  });

  it('a step that finished while the user read folds FOLD_DELAY_MS after they return, not on the spot', () => {
    vi.useFakeTimers();
    // TabView always provides ReadingContext; toggling only its value never remounts
    const at = (b: ContentBlock[], away: boolean) => <ReadingContext.Provider value={away}>{view(b, true)}</ReadingContext.Provider>;
    const reading = (b: ContentBlock[]) => at(b, true);
    const following = (b: ContentBlock[]) => at(b, false);
    const r = render(reading(blocks.slice(0, 6)));
    r.rerender(reading(blocks));
    act(() => { vi.advanceTimersByTime(3 * FOLD_DELAY_MS); });
    r.rerender(reading(blocks));
    expect(isShown('runner.py')).toBe(true);
    r.rerender(following(blocks));
    expect(isShown('runner.py')).toBe(true); // back at the bottom: not on the spot
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    r.rerender(following(blocks));
    expect(isShown('runner.py')).toBe(false);
  });
});

describe('AC3 — groups toggle; headers count steps and commands only', () => {
  it('a finished group folds; its header opens and closes it; no file count', () => {
    vi.useFakeTimers();
    const blocks = [tx('I read it as X.'), bash('a', 'ls'), tr('a'), bash('b', 'make'), tr('b'), tx('Done.')];
    const r = render(view(blocks, true));
    r.rerender(view(blocks, false));
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    const phase = screen.getByTestId('zone-phase');
    const header = within(phase).getByTestId('group-toggle');
    expect(header.textContent).toMatch(/Worked.*2 steps/);
    // every step is a Bash command: the same number is not said twice
    expect(header.textContent).not.toMatch(/command/);
    expect(header.textContent).not.toMatch(/file/);
    expect(within(phase).queryByTestId('tool-strip')).toBeNull();
    fireEvent.click(header);
    expect(within(screen.getByTestId('zone-phase')).getAllByTestId('tool-strip')).toHaveLength(2);
    fireEvent.click(within(screen.getByTestId('zone-phase')).getByTestId('group-toggle'));
    expect(within(screen.getByTestId('zone-phase')).queryByTestId('tool-strip')).toBeNull();
  });

  it('steps and commands both show when not every step is a command', () => {
    vi.useFakeTimers();
    const blocks = [tx('I read it as X.'), bash('a', 'ls'), tr('a'), agent('g', 'look around'), tr('g'), tx('Done.')];
    const r = render(view(blocks, true));
    r.rerender(view(blocks, false));
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    expect(within(screen.getByTestId('zone-phase')).getByTestId('group-toggle').textContent).toMatch(/2 steps.*1 command/);
  });

  it('a turn with no calls has no RESULT label', () => {
    const { container } = render(view([th('hm'), tx('The answer is 4.')], false));
    expect(screen.queryByTestId('turn-result')).toBeNull();
    expect(container.textContent).not.toMatch(/\bResult\b/);
  });
});

describe('AC4 — one light strip per call', () => {
  it('a finished call shows the time this view saw it run (>= 5s), a short one none', () => {
    vi.useFakeTimers();
    const start = [tx('go'), bash('slow', 'make'), bash('quick', 'ls')];
    const r = render(view([tx('go'), bash('slow', 'make')], true));
    act(() => { vi.advanceTimersByTime(7000); });
    r.rerender(view([...start.slice(0, 2), tr('slow'), bash('quick', 'ls')], true));
    act(() => { vi.advanceTimersByTime(1000); });
    r.rerender(view([...start.slice(0, 2), tr('slow'), bash('quick', 'ls'), tr('quick')], true));
    const times = screen.getAllByTestId('strip-time').map((e) => e.textContent);
    expect(times).toEqual(['7s', '']);
  });

  it('a call from history (never seen running) shows no time', () => {
    render(view([tx('go'), bash('old', 'make'), tr('old'), tx('done')], false));
    fireEvent.click(screen.getByTestId('group-toggle'));
    expect(screen.getByTestId('strip-time').textContent).toBe('');
  });
  it('the tool’s own name, the display object as the hover, no red for a failed call', () => {
    const mcp = { type: 'tool_use', id: 'm', name: 'mcp__srv__email_search', summary: 'MCP: email_search — q',
      category: 'mcp', display: { tool: 'email_search', object: 'q=launch', key: 'k1' } } as ContentBlock;
    const disp = { type: 'tool_use', id: 'd', name: 'Bash', summary: 'Running: python3 x.py --run-id ru',
      category: 'bash', display: { tool: 'Bash', object: 'python3 x.py --run-id run_d91fc178 --mode verify', key: 'k2' } } as ContentBlock;
    // a note between the two commands keeps them apart (commands of one kind merge, §3.1)
    render(view([tx('I read it as X.'), mcp, tr('m'), bash('e', 'make broken'), tr('e', true), tx('Retrying.'), disp, tr('d')], true));
    const strips = screen.getAllByTestId('tool-strip');
    expect(strips.map((s) => s.getAttribute('data-tool'))).toEqual(['email_search', 'Bash', 'Bash']);
    expect(within(strips[2]).getByRole('button').getAttribute('title')).toBe('python3 x.py --run-id run_d91fc178 --mode verify');
    expect(strips[1].getAttribute('data-status')).toBe('error');
    expect(strips[1].innerHTML).not.toMatch(/red/);
    expect(strips[1].textContent).not.toContain('✕');
  });

  it('identical commands merge as ×N; commands of one kind merge as Ran; an Edit stays its own row', () => {
    render(view([tx('go'), bash('a', 'make'), tr('a'), bash('b', 'make'), tr('b'), edit('e', '/p/a.ts'), tr('e'),
      bash('c', 'make lint'), tr('c'), bash('d', 'npm test'), tr('d'), tx('ok')], false));
    fireEvent.click(screen.getByTestId('group-toggle'));
    const tools = screen.getAllByTestId('strip-tool').map((s) => s.textContent);
    expect(tools).toEqual(['Bash ×2', 'Edit', 'Ran']);
  });

  it('Result is a structured reading region without a private width or font class', () => {
    render(view([
      tx('go'), bash('a', 'ls'), tr('a'),
      tx('结论先说。\n\n## 改了什么\n\n- **执行方式**：独立 worktree\n  - 细节退到下一层\n\n## 验证\n\n- 55 tests passed'),
    ], false));
    const result = screen.getByTestId('turn-result');
    expect(result.classList.contains('turn-result')).toBe(true);
    expect(result.getAttribute('role')).toBe('region');
    expect(result.getAttribute('aria-label')).toBe('Result');
    expect(result.className).not.toMatch(/max-w|font-/);
    expect(result.closest('.assistant-message')).not.toBeNull();
    const markdown = within(result).getByText('结论先说。').closest('.markdown-content')!;
    expect(markdown.querySelectorAll(':scope > h2')).toHaveLength(2);
    expect(markdown.querySelector(':scope > ul > li > ul')).not.toBeNull();
  });
});

describe('AC5 — the Agents panel', () => {
  const progress = (id: string, taskId: string, status = 'running'): SubAgentProgress =>
    ({ parentToolUseId: id, taskId, status, lastToolName: 'Read', toolUses: 3 });

  it('agents that ran at the same time are one panel, even with a call between their spawns', () => {
    render(view([tx('Spawning two reviewers.'), agent('g1', 'security review'), bash('x', 'ls'), tr('x'), agent('g2', 'correctness review')],
      true, { subAgentProgress: { g1: progress('g1', 't1'), g2: progress('g2', 't2') } }));
    const panels = screen.getAllByTestId('agents-panel');
    expect(panels).toHaveLength(1);
    expect(within(panels[0]).getAllByTestId('agent-row')).toHaveLength(2);
    expect(within(panels[0]).getAllByTestId('agent-now').map((e) => e.textContent)).toEqual(['→ Read', '→ Read']);
  });

  it('a running agent’s ✕ stops only that agent', () => {
    const stopTask = vi.spyOn(chatService, 'stopTask').mockResolvedValue({ status: 'stopped' });
    render(view([tx('go'), agent('g1', 'security review'), agent('g2', 'correctness review')],
      true, { subAgentProgress: { g1: progress('g1', 't1'), g2: progress('g2', 't2') } }));
    fireEvent.click(screen.getAllByTestId('agent-stop')[1]);
    expect(stopTask).toHaveBeenCalledTimes(1);
    expect(stopTask).toHaveBeenCalledWith('s1', 't2');
  });

  it('an agent started in the background says in background, not done', () => {
    backgroundAgentsStore.reset();
    const launched = { type: 'tool_result', toolUseId: 'g1', content: 'Async agent launched', isError: false, truncated: false,
      subagent: { status: 'background' } } as ContentBlock;
    render(view([tx('go'), agent('g1', 'measure reflect'), launched, bash('x', 'ls'), tr('x'), tx('Waiting for it.')], false));
    fireEvent.click(screen.getByTestId('group-toggle'));
    const row = screen.getByTestId('agent-row');
    expect(row.textContent).toContain('↗');
    expect(within(row).getByTestId('agent-background').textContent).toBe('in background');
    const panel = screen.getByTestId('agents-panel').textContent ?? '';
    expect(panel).toContain('1 in background');
    expect(panel).not.toContain('done');
  });

  describe('a background agent follows the daemon’s status in place', () => {
    const launched = { type: 'tool_result', toolUseId: 'g1', content: 'Async agent launched', isError: false, truncated: false,
      subagent: { status: 'background' } } as ContentBlock;
    const turn = [tx('go'), agent('g1', 'measure reflect'), launched, tx('Waiting for it.')];
    const rowOf = () => {
      // a running background agent keeps its group open; anything else is opened here
      if (!screen.queryByTestId('agent-row')) fireEvent.click(screen.getByTestId('group-toggle'));
      return screen.getByTestId('agent-row');
    };
    afterEach(() => { backgroundAgentsStore.reset(); });

    it('running while the daemon says so', () => {
      backgroundAgentsStore.applySnapshot({ s1: [{ toolUseId: 'g1', description: 'measure reflect', status: 'running' }] });
      render(view(turn, false));
      expect(within(rowOf()).getByTestId('agent-background').textContent).toBe('in background');
    });

    it('while it runs, its progress moves the row: elapsed, tools, the tool now (D1)', () => {
      vi.useFakeTimers();
      const t0 = Date.now() - 125_000;
      backgroundAgentsStore.applySnapshot({ s1: [{ toolUseId: 'g1', status: 'running', startedAt: t0, toolUses: 43, lastTool: 'Bash' }] });
      render(view(turn, false));
      const row = rowOf();
      expect(row.textContent).toContain('2m 05s');
      expect(row.textContent).toContain('43 tools');
      expect(within(row).getByTestId('agent-now').textContent).toBe('→ Bash');
      act(() => { backgroundAgentsStore.apply('s1', [{ toolUseId: 'g1', status: 'running', startedAt: t0, toolUses: 44, lastTool: 'Read' }]); });
      act(() => { vi.advanceTimersByTime(3000); });
      expect(row.textContent).toContain('44 tools');
      expect(row.textContent).toContain('2m 08s');
      expect(within(row).getByTestId('agent-now').textContent).toBe('→ Read');
      vi.useRealTimers();
    });

    it('done in background, with its time, once the daemon saw it end — no reload', () => {
      backgroundAgentsStore.applySnapshot({ s1: [{ toolUseId: 'g1', status: 'running' }] });
      render(view(turn, false));
      act(() => { backgroundAgentsStore.apply('s1', [{ toolUseId: 'g1', status: 'completed', durationMs: 252000 }]); });
      const row = rowOf();
      expect(row.textContent).toContain('✓');
      expect(within(row).getByTestId('agent-background').textContent).toBe('done in background');
      expect(row.textContent).toContain('4m 12s');
      expect(screen.getByTestId('agents-panel').textContent).toContain('1 done');
    });

    it('lost when the daemon does not know it (it restarted, or the CLI went away)', () => {
      backgroundAgentsStore.applySnapshot({ s1: [] });
      render(view(turn, false));
      const row = rowOf();
      expect(row.textContent).toContain('■');
      expect(within(row).getByTestId('agent-background').textContent).toContain('lost');
    });

    it('after a reload the stored end is shown as is', () => {
      const ended = { ...(launched as object), subagent: { status: 'completed', durationMs: 9000, background: true } } as ContentBlock;
      backgroundAgentsStore.applySnapshot({ s1: [] });
      render(view([tx('go'), agent('g1', 'measure reflect'), ended, tx('Waiting for it.')], false));
      expect(within(rowOf()).getByTestId('agent-background').textContent).toBe('done in background');
    });
  });

  it('a turn the CLI started for finished background agents begins with a stable Agent card and folded result', () => {
    const fin = { type: 'background_finished', agents: [{
      toolUseId: 'g1', description: 'Measure REFLECT', status: 'completed', durationMs: 252000,
      toolUses: 3, summary: 'The stored state matches the code.',
    }] } as ContentBlock;
    const { container } = render(view([fin, tx('Here is what it found.')], false));
    const card = screen.getByTestId('background-finished-card');
    expect(card.className).toContain('border-purple-400/20');
    expect(card.textContent).toContain('Agent');
    expect(card.textContent).toContain('Measure REFLECT');
    expect(card.textContent).toContain('finished · 4m 12s');
    expect(screen.queryByTestId('background-finished-result')).toBeNull();
    fireEvent.click(screen.getByTestId('background-finished-result-toggle'));
    expect(screen.getByTestId('background-finished-result').textContent).toBe('The stored state matches the code.');
    // it comes before the turn's text
    expect(container.textContent!.indexOf('Measure REFLECT')).toBeLessThan(container.textContent!.indexOf('Here is what it found.'));
  });

  it('the completion card opens every folded ancestor, opens the original Agent row, then scrolls once', () => {
    const scrollIntoView = vi.fn();
    (Element.prototype as unknown as { scrollIntoView: typeof scrollIntoView }).scrollIntoView = scrollIntoView;
    const source: Message = {
      id: 'source', role: 'assistant', timestamp: '2026-10-19T10:00:00+08:00',
      content: [tx('I will inspect it.'), bash('a', 'cat state.py'), tr('a'), tx('Delegating the check.'), agent('g1', 'Measure REFLECT'), tr('g1'), tx('Waiting for it.')],
    };
    const finished: Message = {
      id: 'finished', role: 'assistant', timestamp: '2026-10-19T10:01:00+08:00',
      content: [{ type: 'background_finished', agents: [{ toolUseId: 'g1', description: 'Measure REFLECT', status: 'completed', summary: 'Done.' }] } as ContentBlock, tx('Here is what it found.')],
    };
    render(<ToastProvider>
      <AssistantMessageView message={source} viewKey="source" sessionId="s1" />
      <AssistantMessageView message={finished} viewKey="finished" sessionId="s1" isLastAssistant />
    </ToastProvider>);
    const sourcePhase = screen.getByTestId('zone-phase');
    const phaseToggle = within(sourcePhase).getByTestId('group-toggle');
    if (phaseToggle.getAttribute('aria-expanded') !== 'true') fireEvent.click(phaseToggle);
    const segmentToggle = screen.getByTestId('segment-toggle');
    if (segmentToggle.getAttribute('aria-expanded') !== 'true') fireEvent.click(segmentToggle);
    fireEvent.click(screen.getByTestId('agents-toggle'));
    expect(screen.queryByTestId('agent-row')).toBeNull();
    fireEvent.click(screen.getByTestId('background-finished-origin'));
    expect(screen.getByTestId('agent-row').dataset.agentId).toBe('g1');
    expect(screen.getByTestId('agent-detail')).toBeTruthy();
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    expect(scrollIntoView).toHaveBeenCalledWith({ block: 'center', behavior: 'smooth' });
  });

  it('calls of an agent started in an earlier turn get a stand-in Agent step, not hidden', () => {
    const sub = (id: string, cmd: string) =>
      ({ type: 'tool_use', id, name: 'Bash', summary: `Running: ${cmd}`, category: 'bash', parentToolUseId: 'earlier' }) as ContentBlock;
    render(view([sub('s1', 'grep -rn reflect'), sub('s2', 'wc -l runs')], true));
    const panel = screen.getByTestId('agents-panel');
    const row = within(panel).getByTestId('agent-row');
    expect(row.textContent).toContain('background agent from an earlier turn');
    expect(row.textContent).toContain('⟳'); // running while the turn streams
    fireEvent.click(within(row).getByTestId('agent-toggle'));
    expect(within(row).getAllByTestId('tool-strip').map((e) => e.textContent).join(' ')).toContain('grep -rn reflect');
  });

  it('an agent the turn stopped under says stopped, not done', () => {
    render(view([tx('go'), agent('g1', 'security review'), tx('---\n*Stopped*')], false));
    fireEvent.click(screen.getByTestId('group-toggle'));
    const row = screen.getByTestId('agent-row');
    expect(row.textContent).toContain('■');
    expect(row.textContent).toContain('stopped');
    expect(screen.getByTestId('agents-panel').textContent).toContain('1 stopped');
    expect(screen.getByTestId('agents-panel').textContent).not.toContain('done');
  });

  it('read-only (History): no stop buttons', () => {
    render(view([tx('go'), agent('g1', 'security review')], true, { subAgentProgress: { g1: progress('g1', 't1') } }, { readOnly: true }));
    expect(screen.queryByTestId('agent-stop')).toBeNull();
    expect(screen.queryByTestId('agents-stop-all')).toBeNull();
  });
});

describe('AC6 — what needs the user is a first-level card', () => {
  it('a permission request sits between groups, never inside one', () => {
    const perm = { type: 'cmd_permission_request', requestId: 'p1', toolName: 'Bash', toolInput: { command: 'rm -rf build' },
      reason: 'REASON-TOKEN', options: ['approve', 'deny'] } as ContentBlock;
    // the live card: this tab offers that request
    render(view([tx('go'), bash('a', 'ls'), tr('a'), perm], true, {}, { pendingPermissionRequestId: 'p1' }));
    const card = screen.getByText(/REASON-TOKEN/);
    expect(card.closest('[data-testid="zone-phase"]')).toBeNull();
    expect(screen.getByText('Approve')).toBeTruthy();
  });

  it('a stored prompt this tab does not offer has no live buttons; an expired one says so', () => {
    const perm = (over: Record<string, unknown> = {}) => ({ type: 'cmd_permission_request', requestId: 'p1', toolName: 'Bash',
      toolInput: { command: 'make clean' }, reason: 'R', options: ['approve', 'deny'], ...over }) as ContentBlock;
    const r = render(view([tx('go'), bash('a', 'ls'), tr('a'), perm()], false));
    expect(screen.queryByText('Approve')).toBeNull();
    expect(screen.getByText('Awaiting decision')).toBeTruthy();
    r.unmount();
    render(view([tx('go'), bash('a', 'ls'), tr('a'), perm({ decision: 'expired' })], false, {}, { pendingPermissionRequestId: 'p1' }));
    expect(screen.queryByText('Approve')).toBeNull();
    expect(screen.getByText('No longer pending')).toBeTruthy();
  });
});

describe('AC7 — the pipeline inside the turn', () => {
  it('pre-run work above the divider, only started stages, a re-run is one group, a gate keeps its rounds', () => {
    const blocks = [
      th('reading'), tx('I read it as X.'), bash('s', 'cat skill'), tr('s'), RUN(),
      row('evaluate', 'in_progress'), bash('e', 'grep a'), tr('e'), row('evaluate', 'completed'),
      row('plan', 'in_progress'), bash('p', 'cat b'), tr('p'), row('plan', 'completed'),
      row('gate1', 'completed', { gate: { name: 'GATE 1', verdict: 'BLOCK' } }),
      row('plan', 'in_progress'), bash('p2', 'cat c'), tr('p2'), row('plan', 'completed'),
      row('gate1', 'completed'),
      row('build', 'in_progress'), bash('b', 'make'),
    ];
    const { container } = render(view(blocks, true));
    expect(firstLevel(container)).toEqual(['zone-thinking', 'turn-restatement', 'zone-phase', 'pipeline-start',
      'zone-stage', 'zone-stage', 'zone-gate', 'zone-stage']);
    expect(screen.getByTestId('pipeline-start').textContent).toContain('Pipeline started · full');
    expect(screen.getAllByTestId('zone-stage').map((s) => s.getAttribute('data-stage'))).toEqual(['evaluate', 'plan', 'build']);
    const plan = screen.getAllByTestId('zone-stage')[1];
    expect(within(plan).getByTestId('stage-toggle').textContent).toContain('round 2');
    const gate = screen.getByTestId('zone-gate');
    expect(gate.textContent).toMatch(/GATE 1.*PASS.*round 1: BLOCK/);
    const build = screen.getAllByTestId('zone-stage')[2];
    expect(build.getAttribute('data-status')).toBe('running');
    expect(within(build).getByTestId('stage-toggle').textContent).toContain('next: DELIVER');
    expect(container.textContent).not.toContain('REQ');
  });

  it('a run that stopped mid-way says Stopped at X, not 0/N', () => {
    render(view([tx('go'), RUN(), row('evaluate', 'completed'), row('build', 'in_progress'), bash('b', 'make'), tr('b'), tx('Paused here.')], false));
    const build = screen.getAllByTestId('zone-stage').find((s) => s.getAttribute('data-stage') === 'build')!;
    expect(within(build).getByTestId('stage-toggle').textContent).toContain('Stopped at BUILD');
    expect(document.body.textContent).not.toMatch(/\d+\/\d+/);
  });

  it('a direct run is not a pipeline', () => {
    const d = (b: ContentBlock) => ({ ...b, profile: 'direct' }) as ContentBlock;
    const { container } = render(view([tx('I read it as X.'), d(RUN('direct')), d(row('plan', 'completed')), bash('a', 'ls'), tr('a'), tx('Done.')], false));
    expect(screen.queryByTestId('pipeline-start')).toBeNull();
    expect(screen.queryByTestId('zone-stage')).toBeNull();
    expect(firstLevel(container)).toEqual(['turn-restatement', 'zone-phase', 'turn-result']);
  });

  it('an unanswered question inside a finished stage keeps the stage open', () => {
    const ask = { type: 'ask_user_question', toolUseId: 'q1', questions: [{ question: 'QUESTION-TOKEN?', header: 'Q',
      options: [{ label: 'A', description: 'a' }, { label: 'B', description: 'b' }], multiSelect: false }] } as unknown as ContentBlock;
    render(view([tx('go'), RUN(), row('evaluate', 'in_progress'), bash('e', 'ls'), tr('e'), ask, row('evaluate', 'completed')], false));
    expect(within(screen.getAllByTestId('zone-stage')[0]).getAllByText(/QUESTION-TOKEN/).length).toBeGreaterThan(0);
  });
});

describe('AC9 — one signal; a stopped turn says so', () => {
  it('a streaming turn has no spinner element', () => {
    const { container } = render(view([tx('go'), bash('a', 'make')], true));
    expect(container.querySelector('.animate-spin')).toBeNull();
    expect(screen.getByTestId('zone-phase').getAttribute('data-live')).toBe('true');
  });

  it('stopped with no call cut: just Stopped by you, no RESULT', () => {
    render(view([tx('go'), bash('a', 'make'), tr('a'), tx('partial'), tx('---\n*Stopped*')], false));
    expect(screen.getByTestId('turn-stopped').textContent).toBe('Stopped by you');
    expect(screen.queryByTestId('turn-result')).toBeNull();
  });

  it('a turn the user stopped ends with Stopped by you, and its unfinished call says stopped', () => {
    render(view([tx('go'), bash('a', 'make'), tx('---\n*Stopped*')], false));
    expect(screen.getByTestId('turn-stopped').textContent).toContain('Stopped by you');
    fireEvent.click(screen.getByTestId('group-toggle'));
    expect(screen.getByTestId('strip-time').textContent).toBe('stopped');
    expect(screen.queryByTestId('turn-result')).toBeNull();
  });

  // core/turn_stop: the CLI answers the interrupted call with an error written for the
  // model; that is a stop, never "NEEDS YOU · This call failed".
  const receipt = (calls: string[], agents: Array<{ toolUseId: string; description: string; status: string }> = []) =>
    ({ type: 'turn_stop', at: '2026-10-22T09:00:00Z', calls: calls.map((id) => ({ toolUseId: id, name: 'Bash', elapsedMs: 252000 })),
      agents: agents.map((a) => ({ ...a, background: true })) }) as ContentBlock;

  it('stop receipt: the interrupted call is stopped in place, not a failure; one summary line says it', () => {
    const { container } = render(view([tx('go'), bash('a', 'make'), tr('a', true), receipt(['a'],
      [{ toolUseId: 'g1', description: 'phase3 experiment', status: 'stopped' }])], false));
    expect(container.querySelector('[data-zone="failure"]')).toBeNull();
    const stopped = screen.getByTestId('turn-stopped');
    expect(stopped.hasAttribute('data-receipt')).toBe(true);
    expect(within(stopped).getByTestId('stop-text').textContent)
      .toBe('Stopped by you · Bash stopped at 4m 12s · background agent phase3 experiment stopped');
    expect(within(stopped).queryByTestId('tool-strip')).toBeNull(); // the call is not listed twice
    expect(within(stopped).getByTestId('stop-continue-hint')).toBeTruthy();
    expect(stopped.textContent).not.toContain('boom'); // the CLI's note to the model stays out
    fireEvent.click(screen.getByTestId('group-toggle'));
    expect(screen.getByTestId('strip-time').textContent).toBe('stopped');
  });

  it('the summary opens what each stopped item left: written files, an agent transcript, else output not kept', () => {
    const r = { type: 'turn_stop', calls: [
      { toolUseId: 'a', name: 'Bash', elapsedMs: 252000, writes: ['/w/out.log'] },
      { toolUseId: 'b', name: 'Bash', elapsedMs: 1000 }],
      agents: [{ toolUseId: 'g1', description: 'phase3', status: 'stopped', background: true,
        transcript: { path: '/t/agent-g1.jsonl', toolCalls: 55 } }] } as ContentBlock;
    render(view([tx('go'), bash('a', 'make'), bash('b', 'ls'), r], false));
    expect(screen.queryByTestId('stop-detail')).toBeNull();
    fireEvent.click(screen.getByTestId('stop-summary'));
    const rows = screen.getAllByTestId('stop-detail-row').map((e) => e.textContent);
    expect(rows).toEqual([
      'Bash · stopped at 4m 12s · output not kept',
      'Bash · stopped · output not kept',
      'Agent · phase3 · background · stopped · 55 calls kept, no conclusion',
    ]);
    expect(screen.getAllByTestId('stop-detail-path').map((e) => e.textContent))
      .toEqual(['may have written: /w/out.log', '/t/agent-g1.jsonl']);
  });

  it('a call still waiting for approval at the Stop is "not run", never "stopped"', () => {
    const r = { type: 'turn_stop', calls: [{ toolUseId: 'a', name: 'Bash', ran: false }], agents: [] } as ContentBlock;
    render(view([tx('go'), bash('a', 'rm -rf build'), r], false));
    expect(screen.getByTestId('stop-text').textContent).toBe('Stopped by you · Bash not run');
  });

  it('background agents stopped after the turn ended: said at its end, its RESULT kept', () => {
    const r = { type: 'turn_stop', turn: false, calls: [],
      agents: [{ toolUseId: 'g1', description: 'phase3', status: 'stopped', background: true }] } as ContentBlock;
    render(view([tx('go'), bash('a', 'make'), tr('a'), tx('done'), r], false));
    expect(screen.getByTestId('turn-result')).toBeTruthy();
    expect(screen.getByTestId('stop-text').textContent).toBe('Stopped by you · background agent phase3 stopped');
  });

  it('live, before the receipt: the marker alone makes the last call stopped, not failed', () => {
    const { container } = render(view([tx('go'), bash('a', 'make'), tr('a', true), tx('---\n*Stopped*')], false));
    expect(container.querySelector('[data-zone="failure"]')).toBeNull();
    expect(screen.getByTestId('turn-stopped').textContent).toContain('Stopped by you');
  });

  it('a call that failed before the Stop is still a failure (the receipt does not list it)', () => {
    const { container } = render(view([tx('go'), bash('a', 'make'), tr('a', true), receipt([])], false));
    expect(container.querySelector('[data-zone="failure"]')).not.toBeNull();
  });

  it('a receipt with nothing running is just Stopped by you', () => {
    render(view([tx('go'), bash('a', 'make'), tr('a'), tx('partial'), receipt([])], false));
    expect(screen.getByTestId('turn-stopped').textContent).toBe('Stopped by you');
  });

  it('a stop the backend explained says why (core/turn_notice)', () => {
    const n = (reason: string, cause?: string) => ({ type: 'turn_notice', reason, ...(cause ? { cause } : {}) }) as ContentBlock;
    const { unmount } = render(view([tx('go'), bash('a', 'make'), tr('a'), bash('b', 'make'), n('restarted', 'stalled')], false));
    expect(screen.getByTestId('turn-stopped').textContent)
      .toBe('Stopped · the Swarm process was restarted because it stopped responding (for example while the computer slept); the call it was running was lost');
    unmount();
    render(view([tx('go'), bash('a', 'make'), tr('a'), bash('b', 'make'), n('superseded')], false));
    expect(screen.getByTestId('turn-stopped').textContent).toBe('Stopped · your newer message continued the conversation');
  });

  it('a restarted turn that went on is not stopped; one that gave up says so', () => {
    const n = (reason: string) => ({ type: 'turn_notice', reason, cause: 'disconnected' }) as ContentBlock;
    // the dropped call, the restarted notice, then the continuation's output
    const { unmount } = render(view([tx('go'), bash('a', 'make'), n('restarted'), bash('b', 'make'), tr('b'), tx('done')], false));
    expect(screen.queryByTestId('turn-stopped')).toBeNull();
    unmount();
    const r1 = render(view([tx('go'), bash('a', 'make'), n('gave_up')], false));
    expect(screen.getByTestId('turn-stopped').textContent)
      .toBe('Stopped · the Swarm process could not be restarted (the chat lost its connection to it); send a message to continue');
    r1.unmount();
    const g = (cause: string) => ({ type: 'turn_notice', reason: 'gave_up', cause }) as ContentBlock;
    const r2 = render(view([tx('go'), bash('a', 'make'), g('spawn_failed')], false));
    expect(screen.getByTestId('turn-stopped').textContent)
      .toBe('Stopped · the Swarm process could not be restarted (it could not be started); send a message to continue');
    r2.unmount();
    render(view([tx('go'), bash('a', 'make'), g('unknown')], false));
    expect(screen.getByTestId('turn-stopped').textContent)
      .toBe('Stopped · the Swarm process could not be restarted; send a message to continue');
  });
});

describe('the ending lands in one frame (no flash)', () => {
  const WORK = [bash('b1', 'ls'), tr('b1'), tx('The answer.')];

  it('keeps the streamed layout through the settle window, then folds, labels RESULT and shows the ending together', () => {
    vi.useFakeTimers();
    const { container, rerender } = render(view(WORK, true));
    const answerWhileStreaming = within(screen.getByTestId('zone-phase')).getByText('The answer.');
    rerender(view(WORK, false));
    // Ended, but nothing moved: the answer is the same node, still in the open phase.
    expect(screen.queryByTestId('turn-result')).toBeNull();
    expect(within(screen.getByTestId('zone-phase')).getByText('The answer.')).toBe(answerWhileStreaming);
    expect(screen.getByTestId('zone-phase').textContent).toContain('Worked');
    expect(container.querySelector('[title="Copy message"]')).toBeNull();
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS); });
    // One commit: the phase folded, the answer is the RESULT, the actions appeared.
    expect(within(screen.getByTestId('zone-phase')).queryByText('The answer.')).toBeNull();
    expect(screen.getByTestId('turn-result').textContent).toContain('The answer.');
    expect(container.querySelector('[title="Copy message"]')).not.toBeNull();
  });

  it('a history turn (never streamed here) renders its ending at once', () => {
    render(view(WORK, false));
    expect(screen.getByTestId('turn-result').textContent).toContain('The answer.');
  });

  it('a background-finished card arriving later does not remount the turn', () => {
    const first = render(view([tx('Hi.')], false));
    const answer = screen.getByText('Hi.');
    const bg = { type: 'background_finished', agents: [{ id: 'a1', description: 'scan', status: 'completed' }] } as unknown as ContentBlock;
    first.rerender(view([bg, tx('Hi.')], false));
    expect(screen.getByText('Hi.')).toBe(answer);
  });
});
