/**
 * WorkGroup density (XG 2026-10-19, items 3 / 4 / 5 / 7).
 *
 * What is tested (WorkGroupView rendered from the real projection —
 * deriveTurnView → groupWork):
 * - item 3: a tool row never cuts a file name; an Agent row shows its whole task
 *   description (it wraps) and no verdict / result text
 * - item 4: tool rows are a step quieter than thoughts (verb muted, target dim,
 *   12px); in a LIVE block nothing disappears when the next item arrives: 2s later an
 *   older thought is one "Thought" line and finished exploration calls merge (useSettled)
 * - item 5 (chat display v8, Codex-style): adjacent finished read / search / list calls
 *   are one "Explored" row that opens to one row per call; identical consecutive other
 *   calls are one "×N" row; a running, failed or Agent row never folds, and one
 *   finished row alone is not folded
 * - item 7: an Agent row is a button opening its detail — task, result, steps — and
 *   says its duration once it finished (its elapsed while it runs)
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within, act } from '@testing-library/react';
import React from 'react';
import { WorkGroupView } from '../WorkGroup';
import { FOLD_DELAY_MS } from '../../../../hooks/useLinger';
import { deriveTurnView, groupWork } from '../../turnView';
import type { WorkGroup } from '../../turnView';
import type { ContentBlock } from '../../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, fallback: unknown) => fallback }),
}));

const tu = (id: string, summary: string, name = 'Bash'): ContentBlock =>
  ({ type: 'tool_use', id, name, summary, category: 'bash' }) as ContentBlock;
const th = (t: string) => ({ type: 'thinking', thinking: t }) as ContentBlock;
const res = (id: string, isError = false, extra: Record<string, unknown> = {}) =>
  ({ type: 'tool_result', toolUseId: id, content: 'x', isError, truncated: false, outcome: isError ? 'boom' : 'ok', ...extra }) as ContentBlock;

function groupsOf(content: ContentBlock[], streaming = false): WorkGroup[] {
  return groupWork(deriveTurnView(content, streaming).units, streaming).filter((x): x is WorkGroup => x.kind === 'group');
}
function show(g: WorkGroup, expanded = true) {
  return render(
    <WorkGroupView group={g} expanded={expanded} onToggle={() => {}} thoughtOpen={() => undefined}
      onThoughtToggle={() => {}} renderNote={(text, key) => <p key={key}>{text}</p>} />,
  );
}

const LONG = '/Users/x/swarmai/desktop/src/pages/chat/components/__tests__/a_really_long_test_file_name_for_the_density_check.test.tsx';
const DESC = 'Gate-2 correctness review of the whole streaming bee diff against the requirement and DoD';

describe('item 3 — names are never cut', () => {
  it('a file row keeps the whole file name (no truncation on the chip label)', () => {
    const [g] = groupsOf([tu('r', `Reading ${LONG}`, 'Read'), res('r')]);
    show(g);
    const chip = screen.getByTestId('file-chip');
    expect(chip.textContent).toContain('a_really_long_test_file_name_for_the_density_check.test.tsx');
    expect(chip.querySelector('.truncate')).toBeNull();
  });

  it('an Agent row shows its whole description and no verdict', () => {
    const [g] = groupsOf([
      tu('ag', `Agent: ${DESC}`, 'Agent'),
      res('ag', false, { subagent: { status: 'completed', toolUses: 7, durationMs: 65000, summary: 'NO FINDINGS (HIGH/MED)' } }),
    ]);
    show(g);
    const row = screen.getByTestId('step-row');
    expect(row.textContent).toContain(DESC);
    expect(within(row).getByTestId('strip-target').className).not.toMatch(/\btruncate\b/);
    expect(screen.queryByTestId('agent-verdict')).toBeNull();
    expect(row.textContent).not.toContain('NO FINDINGS');
  });
});

describe('item 4 — tool rows are quieter, live blocks show the newest thought', () => {
  it('tool name muted, target dim, 12px', () => {
    const [g] = groupsOf([tu('a', 'Running: make test'), res('a')]);
    show(g);
    expect(screen.getByTestId('strip-tool').className).toContain('text-[var(--color-text-muted)]');
    expect(screen.getByTestId('strip-target').className).toContain('text-[var(--color-text-dim)]');
    expect(screen.getByTestId('step-row').firstElementChild?.className).toContain('text-[12px]');
  });

  it('live: nothing disappears when the next item arrives; 2s later the older thought is one line and the calls fold', () => {
    vi.useFakeTimers();
    try {
      // mounted with the thought as the newest item (an item already followed at mount is settled)
      const head = [tu('z', 'Running: date'), res('z'), th('first idea\nsecond line\nthird\nfourth')];
      const FIRST = 'first idea\nsecond line\nthird\nfourth';
      const live = (c: ContentBlock[]) => groupsOf(c, true)[0];
      const r = render(
        <WorkGroupView group={live(head)} onToggle={() => {}} thoughtOpen={() => undefined}
          onThoughtToggle={() => {}} renderNote={(text, key) => <p key={key}>{text}</p>} />,
      );
      const next = [...head, tu('a', 'Running: sed -n 1,9p a.ts'), res('a'), tu('b', 'Running: cat b.ts'), res('b'), th('newest idea')];
      r.rerender(
        <WorkGroupView group={live(next)} onToggle={() => {}} thoughtOpen={() => undefined}
          onThoughtToggle={() => {}} renderNote={(text, key) => <p key={key}>{text}</p>} />,
      );
      // the same render the new items arrived in: every thought and call still shown as before
      expect(screen.getAllByTestId('thought')).toHaveLength(2);
      expect(screen.getAllByTestId('thought-text')[0].textContent).toBe(FIRST); // in full
      expect(screen.queryByTestId('step-explored')).toBeNull();
      act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 5); });
      // the older thought sat between two calls: it is inside the Explored row now
      // (folded with it, never dropped); the newest one is still whole
      expect(screen.getAllByTestId('thought-text').map((e) => e.textContent)).toEqual(['newest idea']);
      const explored = screen.getByTestId('step-explored');
      expect(explored.textContent).toContain('a.ts, b.ts');
      fireEvent.click(within(explored).getAllByRole('button')[0]);
      expect(within(explored).getAllByTestId('thought')).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('item 5 — adjacent finished calls fold (Codex-style)', () => {
  const explore = [
    tu('a', 'Running: ls src'), res('a'), tu('c', 'Reading /r/one.ts', 'Read'), res('c'),
    tu('d', 'Running: grep -rn foo src'), res('d'), tu('e', 'Reading /r/two.ts', 'Read'), res('e'),
  ];

  it('adjacent exploration calls are one Explored row that opens to one row per call', () => {
    const [g] = groupsOf(explore);
    show(g);
    const ex = screen.getByTestId('step-explored');
    expect(ex.textContent).toContain('Explored');
    expect(ex.textContent).toContain('one.ts, two.ts');
    expect(ex.textContent).toContain('"foo"');
    expect(screen.queryAllByTestId('step-row')).toHaveLength(0);
    fireEvent.click(within(ex).getByRole('button'));
    expect(screen.getAllByTestId('step-row')).toHaveLength(4);
  });

  it('identical consecutive other calls are one "×N" row; different ones stay rows', () => {
    const [g] = groupsOf([
      tu('a', 'Running: make test'), res('a'), tu('b', 'Running: make test'), res('b'), tu('c', 'Running: make test'), res('c'),
      tu('d', 'Running: make lint'), res('d'),
    ]);
    show(g);
    const fold = screen.getByTestId('step-fold');
    expect(fold.textContent).toContain('make test');
    expect(fold.textContent).toContain('×3');
    expect(screen.getAllByTestId('step-row')).toHaveLength(1); // make lint
  });

  it('a running call stays its own row after the fold; one finished call alone is not folded', () => {
    const [live] = groupsOf([...explore, tu('g', 'Running: npm test')], true);
    show(live);
    expect(screen.getByTestId('step-explored')).toBeTruthy();
    expect(screen.getAllByTestId('step-row').map((r) => r.dataset.status)).toEqual(['pending']);
    const [one] = groupsOf([tu('a', 'Running: cat a.ts'), res('a')]);
    const r2 = show(one);
    expect(within(r2.container).queryByTestId('step-explored')).toBeNull();
  });

  it('a failed call and an Agent call break the fold and stay rows', () => {
    const [g] = groupsOf([
      tu('a', 'Running: cat a.ts'), res('a'), tu('b', 'Running: cat b.ts'), res('b'),
      tu('x', 'Running: cat x.ts'), res('x', true),
      tu('ag', `Agent: ${DESC}`, 'Agent'), res('ag', false, { subagent: { status: 'completed', toolUses: 2, durationMs: 4000, summary: 'ok' } }),
      tu('c', 'Running: cat c.ts'), res('c'),
    ]);
    show(g);
    expect(screen.getAllByTestId('step-explored')).toHaveLength(1);
    expect(screen.getAllByTestId('step-row').map((r) => r.dataset.tool)).toEqual(['Bash', 'Agent', 'Bash']);
    expect(screen.getAllByTestId('step-row')[0].dataset.status).toBe('error');
  });
});

describe('item 7 — an Agent row opens to what it did, with its time', () => {
  it('finished: a button; its detail shows task, result and steps; the duration stays', () => {
    const [g] = groupsOf([
      tu('ag', `Agent: ${DESC}`, 'Agent'),
      res('ag', false, { subagent: { status: 'completed', toolUses: 7, durationMs: 65000, summary: 'NO FINDINGS (HIGH/MED)' } }),
    ]);
    show(g);
    const row = screen.getByTestId('step-row');
    expect(row.textContent).toContain('1m 05s');
    expect(screen.queryByTestId('agent-detail')).toBeNull();
    fireEvent.click(within(row).getByTestId('agent-toggle'));
    const detail = screen.getByTestId('agent-detail');
    expect(detail.textContent).toContain(DESC);
    expect(detail.textContent).toContain('NO FINDINGS (HIGH/MED)');
    expect(detail.textContent).toContain('7 steps');
  });

  it('running: its elapsed time, no duration yet', () => {
    const [g] = groupsOf([tu('ag', `Agent: ${DESC}`, 'Agent')], true);
    show(g);
    const row = screen.getByTestId('step-row');
    expect(row.dataset.status).toBe('pending');
    expect(within(row).queryByTestId('agent-duration')).toBeNull();
    expect(within(row).getByTestId('agent-toggle').tagName).toBe('BUTTON');
  });
});

describe('item 5 inside item 7 — a sub-agent\'s own calls fold the same way', () => {
  it('its finished calls fold in its detail', () => {
    const sub = (id: string, cmd: string) =>
      ({ type: 'tool_use', id, name: 'Bash', summary: `Running: ${cmd}`, category: 'bash', parentToolUseId: 'ag' }) as ContentBlock;
    const subRes = (id: string) =>
      ({ type: 'tool_result', toolUseId: id, content: 'x', isError: false, outcome: 'ok', parentToolUseId: 'ag' }) as ContentBlock;
    const [g] = groupsOf([
      tu('ag', `Agent: ${DESC}`, 'Agent'), sub('s1', 'cat a.ts'), subRes('s1'), sub('s2', 'cat b.ts'), subRes('s2'), sub('s3', 'grep -n x c.ts'), subRes('s3'),
      res('ag', false, { subagent: { status: 'completed', toolUses: 3, durationMs: 9000, summary: 'ok' } }),
    ]);
    show(g);
    fireEvent.click(screen.getByTestId('agent-toggle'));
    const detail = screen.getByTestId('agent-detail');
    expect(within(detail).getByTestId('step-explored').textContent).toContain('a.ts, b.ts');
  });
});

describe('finding 5 — a running sub-agent shows its newest calls', () => {
  it('running: the newest 12, a show-all for the rest; show all → every call', () => {
    const sub = (i: number) => [
      { type: 'tool_use', id: `s${i}`, name: 'Bash', summary: `Running: echo ${i}`, category: 'bash', parentToolUseId: 'ag' },
      { type: 'tool_result', toolUseId: `s${i}`, content: 'x', isError: true, outcome: 'no', parentToolUseId: 'ag' },
    ] as ContentBlock[];
    const content = [tu('ag', `Agent: ${DESC}`, 'Agent'), ...Array.from({ length: 15 }, (_, i) => sub(i)).flat()];
    const [g] = groupsOf(content, true);
    show(g);
    fireEvent.click(screen.getByTestId('agent-toggle'));
    const detail = screen.getByTestId('agent-detail');
    const rows = within(detail).getAllByTestId('step-row');
    expect(rows).toHaveLength(12);
    expect(rows[11].textContent).toContain('echo 14');
    expect(rows[0].textContent).toContain('echo 3');
    fireEvent.click(within(detail).getByTestId('agent-calls-all'));
    expect(within(detail).getAllByTestId('step-row')).toHaveLength(15);
  });
});

describe('round-3 N1 — a call that finishes late does not fold the moment it finishes', () => {
  it('parallel calls: A finishes after B started; A stays a row for 2s, then joins the Explored row', () => {
    vi.useFakeTimers();
    try {
      const view = (c: ContentBlock[]) => (
        <WorkGroupView group={groupsOf(c, true)[0]} onToggle={() => {}} thoughtOpen={() => undefined}
          onThoughtToggle={() => {}} renderNote={(text, key) => <p key={key}>{text}</p>} />
      );
      const start = [tu('x', 'Running: cat x.ts'), res('x'), tu('a', 'Running: cat a.ts'), tu('b', 'Running: make')];
      const r = render(view(start));
      act(() => { vi.advanceTimersByTime(30_000); }); // A has long been followed by B
      r.rerender(view([...start, res('a')])); // A finishes now
      const rowsNow = screen.getAllByTestId('step-row').map((n) => n.dataset.status);
      expect(rowsNow).toContain('done'); // A is still its own row
      expect(screen.queryByTestId('step-explored')).toBeNull();
      act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 5); });
      expect(screen.getByTestId('step-explored').textContent).toContain('x.ts, a.ts');
    } finally {
      vi.useRealTimers();
    }
  });
});
