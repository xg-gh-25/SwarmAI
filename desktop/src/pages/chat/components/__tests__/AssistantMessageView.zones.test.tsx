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
 *   commands, never files; a turn with no calls has no RESULT label
 * - AC4 one light strip per call: the tool's own name (MCP prefix dropped), the
 *   backend display object as the hover; a failed call looks like the others (no red)
 * - AC5 sub-agents that ran at the same time are ONE Agents panel even with a call
 *   between their spawns; a running agent's ✕ stops only it (chatService.stopTask)
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
import { ReadingContext } from '../../readingContext';
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

  it('after the turn: THOUGHTS · N stays first, folded FOLD_DELAY_MS later', () => {
    vi.useFakeTimers();
    const blocks = [th('first'), th('second'), tx('I read it as X.'), bash('a', 'ls'), tr('a'), tx('Done.')];
    const r = render(view(blocks, true));
    r.rerender(view(blocks, false));
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    const z = screen.getByTestId('zone-thinking');
    expect(within(z).getByTestId('thinking-toggle').textContent).toMatch(/Thoughts.*2/);
    expect(within(z).queryByText('second')).toBeNull();
    expect(firstLevel(r.container)[0]).toBe('zone-thinking');
    fireEvent.click(within(z).getByTestId('thinking-toggle'));
    expect(within(screen.getByTestId('zone-thinking')).getByText('second')).toBeTruthy();
  });
});

describe('AC2 — notes stay in their group', () => {
  const turn = [th('look'), tx('I read it as X.'), bash('a', 'cat a.ts'), tr('a'), th('so the store writes'),
    bash('b', 'grep -rn foo src'), tr('b'), tx('Build is green, now the review.'), bash('c', 'make test'), tr('c'), tx('All done.')];

  it('THOUGHTS → restatement → WORKED → RESULT; the note is inside WORKED', () => {
    const { container } = render(view(turn, false));
    expect(firstLevel(container)).toEqual(['zone-thinking', 'turn-restatement', 'zone-phase', 'turn-result']);
    expect(screen.getByTestId('turn-result').textContent).toContain('Result');
    const phase = screen.getByTestId('zone-phase');
    fireEvent.click(within(phase).getByTestId('group-toggle'));
    const note = within(screen.getByTestId('zone-phase')).getByTestId('phase-text');
    expect(note.textContent).toContain('Build is green, now the review.');
  });

  it('a thought between calls stays inside its group', () => {
    render(view(turn, false));
    const first = screen.getAllByTestId('zone-phase')[0];
    fireEvent.click(within(first).getByTestId('group-toggle'));
    expect(within(screen.getAllByTestId('zone-phase')[0]).getByText(/so the store writes/)).toBeTruthy();
  });

  it('a thought is one ✻ Thought row in the strips’ column, also while it streams; it opens to all of it', () => {
    const blocks = [tx('go'), bash('a', 'cat a.ts'), tr('a'), th('first line\nsecond line'), bash('b', 'make'), tr('b')];
    render(view(blocks, true));
    const thought = screen.getByTestId('thought');
    expect(thought.textContent).toContain('✻ Thought');
    expect(thought.textContent).toContain('first line');
    expect(thought.textContent).not.toContain('second line');
    // the same column as the strips around it: no gap breaks the strips apart
    const column = thought.parentElement!;
    expect(within(column).getAllByTestId('tool-strip')).toHaveLength(2);
    fireEvent.click(within(thought).getByTestId('thought-toggle'));
    expect(screen.getByTestId('thought-text').textContent).toContain('second line');
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
    render(view([tx('I read it as X.'), mcp, tr('m'), bash('e', 'make broken'), tr('e', true), disp, tr('d')], true));
    const strips = screen.getAllByTestId('tool-strip');
    expect(strips.map((s) => s.getAttribute('data-tool'))).toEqual(['email_search', 'Bash', 'Bash']);
    expect(within(strips[2]).getByRole('button').getAttribute('title')).toBe('python3 x.py --run-id run_d91fc178 --mode verify');
    expect(strips[1].getAttribute('data-status')).toBe('error');
    expect(strips[1].innerHTML).not.toMatch(/red/);
    expect(strips[1].textContent).not.toContain('✕');
  });

  it('identical commands merge as ×N; different commands do not', () => {
    render(view([tx('go'), bash('a', 'make'), tr('a'), bash('b', 'make'), tr('b'), bash('c', 'make lint'), tr('c'), tx('ok')], false));
    fireEvent.click(screen.getByTestId('group-toggle'));
    const tools = screen.getAllByTestId('strip-tool').map((s) => s.textContent);
    expect(tools).toEqual(['Bash ×2', 'Bash']);
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
    render(view([tx('go'), bash('a', 'ls'), tr('a'), perm], true));
    const card = screen.getByText(/REASON-TOKEN/);
    expect(card.closest('[data-testid="zone-phase"]')).toBeNull();
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

  it('a stop the backend explained says why (core/turn_notice)', () => {
    const n = (reason: string, cause?: string) => ({ type: 'turn_notice', reason, ...(cause ? { cause } : {}) }) as ContentBlock;
    const { unmount } = render(view([tx('go'), bash('a', 'make'), tr('a'), bash('b', 'make'), n('restarted', 'stalled')], false));
    expect(screen.getByTestId('turn-stopped').textContent)
      .toBe('Stopped · the AI process was restarted because it stopped responding (for example while the computer slept); the call it was running was lost');
    unmount();
    render(view([tx('go'), bash('a', 'make'), tr('a'), bash('b', 'make'), n('superseded')], false));
    expect(screen.getByTestId('turn-stopped').textContent).toBe('Stopped · your newer message continued the conversation');
  });

  it('auto-continue (historical rows): a resumed turn is not stopped; one that gave up says so', () => {
    const n = (reason: string) => ({ type: 'turn_notice', reason, cause: 'disconnected' }) as ContentBlock;
    // the dropped call, the restarted notice, then the continuation's output
    const { unmount } = render(view([tx('go'), bash('a', 'make'), n('restarted'), bash('b', 'make'), tr('b'), tx('done')], false));
    expect(screen.queryByTestId('turn-stopped')).toBeNull();
    unmount();
    render(view([tx('go'), bash('a', 'make'), n('gave_up')], false));
    expect(screen.getByTestId('turn-stopped').textContent)
      .toBe('Stopped · the connection dropped 3 times and auto-continue gave up; send a message to continue');
  });
});
