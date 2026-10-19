/**
 * WorkGroup / ToolStrip — unit contract (chat display v7.12).
 *
 * What is tested (WorkGroupView rendered from the real projection —
 * deriveTurnView → groupWork):
 * - a finished block folds to ONE line of counts (steps · files changed · commands):
 *   no file names, no title borrowed from a thought; the toggle is a button
 * - opened, it lists thoughts, notes and tool rows in stream order
 * - the live block is an open WORKING card without a toggle; `flat` (inside a stage)
 *   has no header at all
 * - ToolStrip: the real tool name and the real command; a merged read shows N× and
 *   its ranges; no last-output-line result; an error one line below
 * - an unrecovered failure stays visible under the folded line
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import React from 'react';
import { WorkGroupView, workCounts, pathTail } from '../WorkGroup';
import { deriveTurnView, groupWork } from '../../turnView';
import type { WorkGroup } from '../../turnView';
import type { ContentBlock } from '../../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, fallback: unknown) => fallback }),
}));

const tu = (id: string, summary: string, name = 'Bash'): ContentBlock =>
  ({ type: 'tool_use', id, name, summary, category: 'bash' }) as ContentBlock;
const th = (t: string) => ({ type: 'thinking', thinking: t }) as ContentBlock;
const tx = (t: string) => ({ type: 'text', text: t }) as ContentBlock;
const res = (id: string, outcome: string, isError = false) =>
  ({ type: 'tool_result', toolUseId: id, content: 'x', isError, truncated: false, outcome }) as ContentBlock;

function groupsOf(content: ContentBlock[], streaming = false): WorkGroup[] {
  return groupWork(deriveTurnView(content, streaming).units, streaming).filter((x): x is WorkGroup => x.kind === 'group');
}
function show(g: WorkGroup, opts: { expanded?: boolean; flat?: boolean; tail?: WorkGroup['members'][number]; defaultOpen?: boolean } = {}) {
  const onToggle = vi.fn();
  const r = render(
    <WorkGroupView group={g} expanded={opts.expanded} flat={opts.flat} tail={opts.tail} defaultOpen={opts.defaultOpen} onToggle={onToggle} thoughtOpen={() => undefined}
      onThoughtToggle={() => {}} renderNote={(text, key) => <p key={key}>{text}</p>} compact />,
  );
  return { onToggle, ...r };
}

describe('WorkGroupView', () => {
  it('finished: one WORKED line of counts, no file names', () => {
    const [g] = groupsOf([
      tu('a', 'Reading /r/a.ts', 'Read'), res('a', 'lines 1–40'),
      tu('b', 'Writing to /r/b.ts', 'Edit'), tu('c', 'Writing to /r/c.ts', 'Edit'), tu('d', 'Running: make'),
    ]);
    expect(workCounts(g)).toEqual({ steps: 4, files: 2, commands: 1, thoughts: 0 });
    const { onToggle } = show(g);
    const row = screen.getByTestId('group-row');
    expect(row.textContent).toContain('Worked');
    expect(screen.getByTestId('group-counts').textContent).toBe('· 4 steps · 2 files changed · 1 command');
    expect(row.querySelector('[data-testid="file-chip"]')).toBeNull();
    const toggle = screen.getByTestId('group-toggle');
    expect(toggle.tagName).toBe('BUTTON');
    fireEvent.click(toggle);
    expect(onToggle).toHaveBeenCalledWith(true);
  });

  it('a block with only thinking says how many thoughts, never "0 steps"', () => {
    const g: WorkGroup = { kind: 'group', id: 'grp-th-1', live: false, hasTools: false, members: [
      { kind: 'thought', text: 'one', index: 0, ordinal: 1, live: false, intent: false },
      { kind: 'thought', text: 'two', index: 1, ordinal: 2, live: false, intent: false },
    ] };
    show(g);
    expect(screen.getByTestId('group-counts').textContent).toBe('· 2 thoughts');
  });

  it('a block of notes only has no "0 thoughts" count', () => {
    const g: WorkGroup = { kind: 'group', id: 'grp-note-3', live: false, hasTools: false, members: [
      { kind: 'note', text: 'Got it, going with A.', index: 3, textOrdinal: 0 },
    ] };
    show(g);
    expect(screen.queryByTestId('group-counts')).toBeNull();
    expect(screen.getByTestId('group-row').textContent).not.toContain('0');
  });

  it('flat (inside a stage): live shows every item; once done, the first 12 and a show-all', () => {
    const blocks: ContentBlock[] = [];
    // failed calls never fold (WorkGroup.density.test.tsx), so each stays its own row
    for (let i = 0; i < 30; i += 1) blocks.push(tu(`s${i}`, `Running: echo ${i}`), res(`s${i}`, 'no', true));
    const [live] = groupsOf(blocks, true);
    const r = show(live, { flat: true });
    expect(screen.getAllByTestId('step-row')).toHaveLength(30);
    expect(screen.queryByTestId('show-all-steps')).toBeNull();
    r.unmount();
    const [done] = groupsOf(blocks, false);
    show(done, { flat: true });
    const rows = screen.getAllByTestId('step-row');
    expect(rows).toHaveLength(12);
    expect(within(rows[0]).getByTestId('strip-target').textContent).toBe('echo 0');
    fireEvent.click(screen.getByTestId('show-all-steps'));
    expect(screen.getAllByTestId('step-row')).toHaveLength(30);
  });

  it('a long tool name truncates and keeps its name as the tooltip', () => {
    const name = 'mcp__plugin_AmazonBuilderCoreAIAgents-pipeline-assistant_builder-mcp__GetPipelineHealth';
    const [g] = groupsOf([tu('a', 'MCP: GetPipelineHealth — p', name), tx('x')]);
    show(g, { expanded: true });
    const tool = screen.getByTestId('strip-tool');
    expect(tool.className).toContain('truncate');
    expect(tool.getAttribute('title')).toBe(name);
    expect(screen.getByTestId('strip-target').textContent).toBe('p');
  });

  it('opened: thoughts, notes and tool rows in stream order', () => {
    const [g] = groupsOf([tu('a', 'Running: ls'), tx('a note'), th('a thought'), tu('b', 'Running: npm test'), tx('end')]);
    const { container } = show(g, { expanded: true });
    const body = screen.getByTestId('group-body');
    const order = [...body.children].map((c) => c.getAttribute('data-testid'));
    expect(order).toEqual(['step-row', 'group-note', 'thought', 'step-row']);
    expect(screen.getAllByTestId('strip-tool').map((e) => e.textContent)).toEqual(['Bash', 'Bash']);
    expect(screen.getAllByTestId('strip-target').map((e) => e.textContent)).toEqual(['ls', 'npm test']);
    expect(container.textContent).toContain('a note');
  });

  it('a tool row shows the real command, setup and wrapper stripped', () => {
    const [g] = groupsOf([tu('a', "Running: cd /Users/x/repo\nperl -e 'alarm 60; exec @ARGV' git log -6 --format=%h"), tu('b', 'Running: ls')]);
    show(g, { expanded: true });
    expect(screen.getAllByTestId('strip-target')[0].textContent).toBe('git log -6 --format=%h');
  });

  it('no last-output-line on a row; an error shows one line below', () => {
    const [g] = groupsOf([tu('a', 'Running: git log'), res('a', '19:01:37'), tu('b', 'Running: make'), res('b', 'make: failed', true), tx('x')]);
    const { container } = show(g, { expanded: true });
    expect(container.textContent).not.toContain('19:01:37');
    expect(screen.getByTestId('step-error').textContent).toBe('make: failed');
  });

  it('adjacent reads of one file are one row with N× and the ranges read', () => {
    const rd = (id: string) => tu(id, 'Reading /r/src/a.ts', 'Read');
    // the failed call keeps the merged read from folding with it
    const [g] = groupsOf([rd('a'), res('a', 'lines 1–200'), rd('b'), res('b', 'lines 200–400'), tu('c', 'Running: ls'), res('c', 'no', true), tx('x')]);
    show(g, { expanded: true });
    expect(screen.getAllByTestId('step-row')).toHaveLength(2);
    expect(screen.getByTestId('step-repeat').textContent).toBe('2×');
    expect(screen.getByTestId('step-ranges').textContent).toBe('lines 1–200, lines 200–400');
  });

  it('live: an open WORKING card without a toggle; the running row spins', () => {
    const [g] = groupsOf([tu('a', 'Running: ls'), res('a', 'ok'), tu('b', 'Running: make')], true);
    expect(g.live).toBe(true);
    const { container } = show(g);
    expect(screen.getByTestId('group-row').textContent).toContain('Working');
    expect(screen.queryByTestId('group-toggle')).toBeNull();
    expect(container.querySelectorAll('.animate-spin')).toHaveLength(1);
    expect(screen.getAllByTestId('step-row')[1].querySelector('.animate-spin')).not.toBeNull();
  });

  it('flat (inside a stage): the items with no WORKING / WORKED header', () => {
    const [g] = groupsOf([tu('a', 'Running: ls'), th('hm'), tu('b', 'Running: pwd'), tx('x')]);
    show(g, { flat: true });
    expect(screen.queryByTestId('group-toggle')).toBeNull();
    expect(screen.queryByTestId('group-row')).toBeNull();
    expect(screen.getAllByTestId('step-row')).toHaveLength(2);
  });

  it('a long path keeps its file name visible; the full path stays on the chip', () => {
    expect(pathTail('/Users/gawan/Desktop/repo/desktop/src/pages/chat/turnView.ts')).toBe('…/pages/chat/turnView.ts');
    expect(pathTail('/r/a.ts')).toBe('/r/a.ts');
    const [g] = groupsOf([tu('a', 'Reading /Users/gawan/repo/desktop/src/x/a.ts', 'Read'), res('a', 'lines 1–9'), tx('x')]);
    show(g, { expanded: true });
    const chip = screen.getByTestId('file-chip');
    expect(chip.textContent).toContain('…/src/x/a.ts');
    expect(chip.getAttribute('data-path')).toBe('/Users/gawan/repo/desktop/src/x/a.ts');
  });

  it('an Edit row is a diff chip (the verb, not a tool-name list)', () => {
    const [g] = groupsOf([tu('a', 'Writing to /r/b.ts', 'MultiEdit'), tx('x')]);
    show(g, { expanded: true });
    expect(screen.getByTestId('strip-tool').textContent).toBe('MultiEdit');
    expect(screen.getByTestId('file-chip')).toBeTruthy();
  });

  it('`tail`: folded, the note shows under the line; opened, it is in its place once', () => {
    const [g] = groupsOf([tu('a', 'Running: ls'), res('a', 'ok'), tx('last words'), tu('b', 'Running: pwd'), res('b', 'ok')]);
    const note = g.members.find((m) => m.kind === 'note')!;
    const { rerender } = show(g, { tail: note });
    expect(screen.getByTestId('turn-tail').textContent).toBe('last words');
    expect(screen.getByTestId('group-counts').textContent).toBe('· 2 steps · 2 commands');
    rerender(<WorkGroupView group={g} expanded tail={note} onToggle={() => {}} thoughtOpen={() => undefined}
      onThoughtToggle={() => {}} renderNote={(text, key) => <p key={key}>{text}</p>} compact />);
    expect(screen.queryByTestId('turn-tail')).toBeNull();
    expect(screen.getAllByText('last words')).toHaveLength(1);
  });

  it('`defaultOpen`: a turn with nothing to read starts with its last block open, newest items', () => {
    const blocks: ContentBlock[] = [];
    for (let i = 0; i < 20; i += 1) blocks.push(tu(`s${i}`, `Running: echo ${i}`), res(`s${i}`, 'no', true));
    const [g] = groupsOf(blocks);
    show(g, { defaultOpen: true });
    const rows = screen.getAllByTestId('step-row');
    expect(rows).toHaveLength(12);
    expect(within(rows[11]).getByTestId('strip-target').textContent).toBe('echo 19');
  });

  it('an unrecovered failure stays visible under the folded line', () => {
    const [g] = groupsOf([tu('a', 'Running: ls'), res('a', 'ok'), tu('b', 'Running: make'), res('b', 'make: failed', true)]);
    show(g);
    expect(screen.queryByTestId('group-body')).toBeNull();
    expect(within(document.body).getByTestId('step-error').textContent).toBe('make: failed');
  });
});
