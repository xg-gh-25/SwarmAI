/**
 * WorkGroup / ToolStrip — unit contract (chat display v7.11).
 *
 * What is tested (WorkGroupView rendered from the real projection —
 * deriveTurnView → groupWork):
 * - a finished group folds to one line: WORKED / THOUGHT tag + title + files; the
 *   toggle is a button and the FileChips sit beside it, never inside (AC1, AC7)
 * - a group of ONE item (a tool call, or a thought) is that item — no header (AC3)
 * - the live group is an open WORKING card without a toggle
 * - ToolStrip: kind word, target, short outcome, elapsed while running, status;
 *   strip-kind words follow STRIP_KIND_FALLBACK (AC3)
 * - an unrecovered failure stays visible under the folded line
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import React from 'react';
import { WorkGroupView } from '../WorkGroup';
import { deriveTurnView, groupWork } from '../../turnView';
import type { WorkGroup } from '../../turnView';
import type { ContentBlock } from '../../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, fallback: unknown) => fallback }),
}));

const tu = (id: string, summary: string, name = 'Bash'): ContentBlock =>
  ({ type: 'tool_use', id, name, summary, category: 'bash' }) as ContentBlock;
const th = (t: string) => ({ type: 'thinking', thinking: t }) as ContentBlock;
const res = (id: string, outcome: string, isError = false) =>
  ({ type: 'tool_result', toolUseId: id, content: 'x', isError, truncated: false, outcome }) as ContentBlock;

function groupsOf(content: ContentBlock[], streaming = false): WorkGroup[] {
  return groupWork(deriveTurnView(content, streaming).units, streaming).filter((x): x is WorkGroup => x.kind === 'group');
}
function show(g: WorkGroup, expanded?: boolean) {
  const onToggle = vi.fn();
  render(<WorkGroupView group={g} expanded={expanded} onToggle={onToggle} thoughtOpen={() => undefined} onThoughtToggle={() => {}} compact />);
  return onToggle;
}

describe('WorkGroupView', () => {
  it('finished: one WORKED line, toggle button, files beside it', () => {
    const [g] = groupsOf([
      tu('a', 'Reading /r/a.ts', 'Read'), res('a', 'lines 1–40'),
      tu('b', 'Writing to /r/b.ts', 'Edit'), tu('c', 'Writing to /r/c.ts', 'Edit'),
    ]);
    const onToggle = show(g);
    const row = screen.getByTestId('group-row');
    expect(row.textContent).toContain('Worked');
    const toggle = screen.getByTestId('group-toggle');
    expect(toggle.tagName).toBe('BUTTON');
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(toggle.querySelector('button')).toBeNull();
    const files = within(screen.getByTestId('group-files')).getAllByTestId('file-chip');
    expect(files.map((f) => f.getAttribute('data-path'))).toEqual(['/r/b.ts', '/r/c.ts']);
    fireEvent.click(toggle);
    expect(onToggle).toHaveBeenCalledWith(true);
  });

  it('opened: its strips in order', () => {
    const [g] = groupsOf([tu('a', 'Running: ls'), tu('b', 'Running: pwd'), tu('c', 'Running: npm test')]);
    show(g, true);
    expect(screen.getAllByTestId('strip-kind').map((e) => e.textContent)).toEqual(['Read', 'Run', 'Test']);
  });

  it('no tool call and several thoughts: a THOUGHT line', () => {
    // An intent-less run of thoughts after work: the reflection is grouped with the tool;
    // a group with no tool at all only arises when thoughts follow a boundary.
    const g: WorkGroup = {
      kind: 'group', id: 'grp-th-1', live: false, hasTools: false,
      members: [
        { kind: 'thought', text: 'First idea. More.', index: 0, ordinal: 1, live: false, intent: false },
        { kind: 'thought', text: 'Second idea.', index: 1, ordinal: 2, live: false, intent: false },
      ],
    };
    show(g);
    expect(screen.getByTestId('group-row').textContent).toContain('Thought');
    expect(screen.getByTestId('group-title').textContent).toBe('First idea.');
  });

  it('a group of one item is the item itself', () => {
    const [g] = groupsOf([tu('a', 'Running: ls')]);
    show(g);
    expect(screen.queryByTestId('group-row')).toBeNull();
    expect(screen.getByTestId('work-group').dataset.single).toBe('true');
    expect(screen.getAllByTestId('step-row')).toHaveLength(1);
  });

  it('a single item with a lead-in title keeps the title as its header', () => {
    const items = groupWork(deriveTurnView([tu('a', 'Running: ls'), { type: 'text', text: 'Now the two docs:' } as ContentBlock,
      tu('b', 'Writing to /r/a.md', 'Edit'), { type: 'text', text: 'done' } as ContentBlock], false).units, false);
    const g = items.filter((x): x is WorkGroup => x.kind === 'group')[1];
    show(g);
    expect(screen.getByTestId('group-title').textContent).toBe('Now the two docs');
    expect(screen.getByTestId('group-row').textContent).toContain('a.md');
  });

  it('live: an open WORKING card without a toggle; the running strip spins', () => {
    const [g] = groupsOf([tu('a', 'Running: ls'), res('a', 'ok'), tu('b', 'Running: make')], true);
    expect(g.live).toBe(true);
    const { container } = render(<WorkGroupView group={g} onToggle={() => {}} thoughtOpen={() => undefined} onThoughtToggle={() => {}} />);
    expect(screen.getByTestId('group-row').textContent).toContain('Working');
    expect(screen.queryByTestId('group-toggle')).toBeNull();
    expect(container.querySelectorAll('.animate-spin')).toHaveLength(1);
    const rows = screen.getAllByTestId('step-row');
    expect(rows[1].dataset.status).toBe('pending');
    expect(rows[1].querySelector('.animate-spin')).not.toBeNull();
  });

  it('an unrecovered failure stays visible under the folded line', () => {
    const [g] = groupsOf([tu('a', 'Running: ls'), res('a', 'ok'), tu('b', 'Running: make'), res('b', 'make: failed', true)]);
    show(g);
    expect(screen.queryByTestId('group-body')).toBeNull();
    expect(screen.getByTestId('step-error').textContent).toBe('make: failed');
  });
});
