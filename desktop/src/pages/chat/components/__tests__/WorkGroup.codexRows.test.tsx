/**
 * WorkGroup — Codex-style tool rows (chat display v8, XG 2026-10-19).
 *
 * What is tested (WorkGroupView from the real projection: deriveTurnView → groupWork):
 * - a row says verb + real object (Read a.ts, Search "q", Ran git status) — not the
 *   tool name and the raw command
 * - the full command shows under a RUNNING row, stays FOLD_DELAY_MS after the call
 *   ends, then shows only once the row is opened; never when the row already says it
 * - settled adjacent read / search / list rows merge into ONE "Explored" row that
 *   lists them (deduplicated) and opens to one row per call; a row that may write
 *   never joins it (negative)
 * - a settled thought between two calls is not a line of its own: between two
 *   exploration rows it is inside the Explored row; between two other calls it merges
 *   into the call after it (shown when that row is opened, its time the row's
 *   duration); a settled thought elsewhere is one "Thought" line with no text
 * - a run row says the program and its subcommand; the whole command is its second
 *   line (spec: "Ran <program subcommand>"); find is a Search
 * - the WORKED line says how long the block was live, from 5s
 * - a live thought is shown in full (no clamp)
 * - durations under 5s are not shown
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, within, act } from '@testing-library/react';
import React, { useState } from 'react';
import { WorkGroupView } from '../WorkGroup';
import { FOLD_DELAY_MS } from '../../../../hooks/useLinger';
import { deriveTurnView, groupWork } from '../../turnView';
import type { WorkGroup } from '../../turnView';
import type { ContentBlock } from '../../../../types';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

afterEach(() => { vi.useRealTimers(); });

const sh = (id: string, cmd: string): ContentBlock => ({ type: 'tool_use', id, name: 'Bash', summary: `Running: ${cmd}`, category: 'bash' }) as ContentBlock;
const rd = (id: string, path: string): ContentBlock => ({ type: 'tool_use', id, name: 'Read', summary: `Reading ${path}`, category: 'read' }) as ContentBlock;
const ok = (id: string) => ({ type: 'tool_result', toolUseId: id, content: 'x', isError: false, truncated: false, outcome: 'ok' }) as ContentBlock;
const th = (t: string) => ({ type: 'thinking', thinking: t }) as ContentBlock;
const tx = (t: string) => ({ type: 'text', text: t }) as ContentBlock;

const groupOf = (content: ContentBlock[], streaming: boolean): WorkGroup =>
  groupWork(deriveTurnView(content, streaming).units, streaming).find((x): x is WorkGroup => x.kind === 'group')!;
/** The thought open/closed choice is lifted (AssistantMessageView owns it): hold it here. */
function Harness({ g, expanded }: { g: WorkGroup; expanded?: boolean }) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  return (
    <WorkGroupView group={g} expanded={expanded} onToggle={() => {}} thoughtOpen={(k) => open[k]}
      onThoughtToggle={(k, o) => setOpen((m) => ({ ...m, [k]: o }))} renderNote={(text, key) => <p key={key}>{text}</p>} />
  );
}
const view = (g: WorkGroup, expanded?: boolean) => <Harness g={g} expanded={expanded} />;

describe('Codex-style rows', () => {
  it('a row says verb + program and subcommand; the whole command is one click away', () => {
    render(view(groupOf([sh('a', 'cd /r && git log -6 --format=%h --no-merges'), ok('a'), tx('done')], false), true));
    const row = screen.getByTestId('step-row');
    expect(within(row).getByTestId('strip-tool').textContent).toBe('Ran');
    expect(within(row).getByTestId('strip-target').textContent).toBe('git log -6');
    expect(row.textContent).not.toContain('cd /r');
    fireEvent.click(within(row).getByTestId('row-toggle'));
    expect(screen.getByTestId('step-command').textContent).toBe('git log -6 --format=%h --no-merges');
  });

  it('find is a Search for its pattern, and joins an Explored row', () => {
    render(view(groupOf([sh('a', 'find src -name "*.test.ts"'), ok('a'), sh('b', 'cat src/a.ts'), ok('b'), tx('done')], false), true));
    const ex = screen.getByTestId('step-explored');
    expect(ex.textContent).toContain('Search');
    expect(ex.textContent).toContain('"*.test.ts"');
  });

  it('a running row shows the full command under it; it stays FOLD_DELAY_MS after the call ends; then only once opened', () => {
    vi.useFakeTimers();
    const cmd = 'python3 backend/scripts/artifact_cli.py run-update --project P --status completed';
    const { rerender } = render(view(groupOf([sh('a', `cd /r && ${cmd}`)], true)));
    expect(within(screen.getByTestId('step-row')).getByTestId('strip-target').textContent).toBe('artifact_cli.py run-update');
    expect(screen.getByTestId('step-command').textContent).toBe(cmd);
    rerender(view(groupOf([sh('a', `cd /r && ${cmd}`), ok('a'), tx('done')], false), true));
    expect(screen.getByTestId('step-command').textContent).toBe(cmd); // not on the spot
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    expect(screen.queryByTestId('step-command')).toBeNull();
    fireEvent.click(screen.getByTestId('step-row').querySelector('[data-testid="row-toggle"]')!);
    expect(screen.getByTestId('step-command').textContent).toBe(cmd);
  });

  it('a row that already says the whole command has no second line repeating it', () => {
    render(view(groupOf([sh('a', 'cd /r && git status')], true)));
    expect(within(screen.getByTestId('step-row')).getByTestId('strip-target').textContent).toBe('git status');
    expect(screen.queryByTestId('step-command')).toBeNull();
    expect(screen.queryByTestId('row-toggle')).toBeNull();
  });

  it('a settled thought between two other calls merges into the call after it', () => {
    render(view(groupOf([sh('a', 'make'), ok('a'), th('the build is green, now the tests'), sh('b', 'make test'), ok('b'), tx('done')], false), true));
    expect(screen.queryAllByTestId('thought')).toHaveLength(0);
    const rows = screen.getAllByTestId('step-row');
    expect(rows).toHaveLength(2);
    fireEvent.click(within(rows[1]).getByTestId('row-toggle'));
    expect(within(rows[1]).getByTestId('step-thought')).toBeTruthy();
    expect(within(rows[1]).getByTestId('thought-toggle').textContent).toBe('Thought');
  });

  it('a thought the user opened as a line is never merged away; opened inside a row it stays there', () => {
    vi.useFakeTimers();
    const head = [sh('a', 'make'), ok('a'), th('opened by the user'), sh('b', 'make test')];
    const { rerender } = render(view(groupOf(head, true)));
    fireEvent.click(screen.getByTestId('thought-toggle'));
    rerender(view(groupOf([...head, ok('b'), sh('c', 'make lint')], true)));
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    expect(screen.getAllByTestId('thought')).toHaveLength(1);
    expect(screen.getByTestId('thought-text').textContent).toBe('opened by the user');
  });

  it('a thought before the first call, or before a running one, stays its own line (negative)', () => {
    render(view(groupOf([th('first'), sh('a', 'make'), ok('a'), th('second'), sh('b', 'make test')], true)));
    expect(screen.getAllByTestId('thought')).toHaveLength(2);
  });

  it('the WORKED line says how long the block was live, from 5s; never from history', () => {
    vi.useFakeTimers();
    const live = [sh('a', 'make'), ok('a'), sh('b', 'make test')];
    const { rerender, unmount } = render(view(groupOf(live, true)));
    act(() => { vi.advanceTimersByTime(8000); });
    rerender(view(groupOf([...live, ok('b'), tx('done')], false)));
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    expect(screen.getByTestId('group-counts').textContent).toContain('8s');
    unmount();
    render(view(groupOf([...live, ok('b'), tx('done')], false)));
    expect(screen.getByTestId('group-counts').textContent).not.toMatch(/\d+s\b/);
  });

  it('settled exploration rows merge into ONE Explored row; opening it lists each call', () => {
    render(view(groupOf([
      sh('a', 'sed -n 1,80p src/a.ts'), ok('a'), rd('b', '/r/src/b.ts'), ok('b'),
      sh('c', 'grep -rn "heartbeat" src'), ok('c'), sh('d', 'sed -n 90,120p src/a.ts'), ok('d'), tx('done'),
    ], false), true));
    const ex = screen.getByTestId('step-explored');
    expect(ex.textContent).toContain('Explored');
    expect(ex.textContent).toContain('Read');
    expect(ex.textContent).toContain('a.ts, b.ts'); // a.ts once
    expect(ex.textContent).toContain('Search');
    expect(ex.textContent).toContain('"heartbeat"');
    expect(screen.queryAllByTestId('step-row')).toHaveLength(0);
    fireEvent.click(within(ex).getAllByRole('button')[0]);
    expect(within(ex).getAllByTestId('step-row')).toHaveLength(4);
  });

  it('a row that may write never joins an Explored row (negative)', () => {
    render(view(groupOf([
      sh('a', 'sed -n 1,80p src/a.ts'), ok('a'), sh('b', 'grep -l foo src | xargs rm'), ok('b'),
      sh('c', 'cat src/c.ts'), ok('c'), tx('done'),
    ], false), true));
    expect(screen.queryAllByTestId('step-explored')).toHaveLength(0);
    const rows = screen.getAllByTestId('step-row');
    expect(rows.map((r) => within(r).getByTestId('strip-tool').textContent)).toEqual(['Read', 'Ran', 'Read']);
  });

  it('a settled thought between exploration rows is inside the Explored row, not a line of its own', () => {
    render(view(groupOf([
      sh('a', 'sed -n 1,80p src/a.ts'), ok('a'), th('so the store is the writer'), sh('b', 'cat src/b.ts'), ok('b'), tx('done'),
    ], false), true));
    expect(screen.getAllByTestId('step-explored')).toHaveLength(1);
    expect(screen.queryAllByTestId('thought')).toHaveLength(0);
    fireEvent.click(within(screen.getByTestId('step-explored')).getAllByRole('button')[0]);
    expect(screen.getAllByTestId('thought')).toHaveLength(1);
  });

  it('a settled thought elsewhere is one "Thought" line with no text; the text is one click away', () => {
    render(view(groupOf([th('a reasoning paragraph that should not be on the folded line'), sh('a', 'make test'), ok('a'), tx('done')], false), true));
    const thought = screen.getByTestId('thought');
    expect(thought.textContent).toContain('Thought');
    expect(thought.textContent).not.toContain('reasoning paragraph');
    fireEvent.click(within(thought).getByTestId('thought-toggle'));
    expect(screen.getByTestId('thought').textContent).toContain('reasoning paragraph');
  });

  it('a live thought is shown in full: no line clamp', () => {
    const long = Array.from({ length: 12 }, (_, i) => `line ${i} of a long live thought`).join('\n');
    render(view(groupOf([sh('a', 'ls'), ok('a'), th(long)], true)));
    const text = screen.getByTestId('thought-text');
    expect(text.textContent).toBe(long);
    expect(text.style.webkitLineClamp).toBe('');
  });

  it('a running call shows its elapsed only from 5s', () => {
    vi.useFakeTimers();
    render(view(groupOf([sh('a', 'make test')], true)));
    act(() => { vi.advanceTimersByTime(3000); });
    expect(screen.queryByTestId('live-elapsed')).toBeNull();
    act(() => { vi.advanceTimersByTime(3000); });
    expect(screen.getByTestId('live-elapsed').textContent).toMatch(/^[56]s$/);
  });

  it('exploration rows merge only after they settle (FOLD_DELAY_MS), never the moment they finish', () => {
    vi.useFakeTimers();
    // Mounted while the first call runs; a mount with calls already finished (a tab
    // switch back) starts with them folded — nothing to watch settle there.
    const { rerender } = render(view(groupOf([sh('a', 'sed -n 1,80p src/a.ts')], true)));
    const live = [sh('a', 'sed -n 1,80p src/a.ts'), ok('a'), sh('b', 'cat src/b.ts'), ok('b'), sh('c', 'make')];
    rerender(view(groupOf(live, true)));
    expect(screen.queryAllByTestId('step-explored')).toHaveLength(0);
    expect(screen.getAllByTestId('step-row')).toHaveLength(3);
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    expect(screen.getAllByTestId('step-explored')).toHaveLength(1);
  });
});
