/**
 * WorkGroup density (XG 2026-10-19, items 3 / 4 / 5 / 7).
 *
 * What is tested (WorkGroupView rendered from the real projection —
 * deriveTurnView → groupWork):
 * - item 3: a tool row never cuts a file name; an Agent row shows its whole task
 *   description (it wraps) and no verdict / result text
 * - item 4: tool rows are a step quieter than thoughts (tool name muted, target dim,
 *   12px); in a LIVE block nothing disappears when the next item arrives: 2s later an
 *   older thought is one line and finished calls fold (useSettled)
 * - item 5: two or more consecutive finished tool rows fold into one "Bash ×4 · Read ×2"
 *   row that opens to one row per call; a running, failed or Agent row never folds,
 *   and one finished row alone is not folded
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
      onThoughtToggle={() => {}} renderNote={(text, key) => <p key={key}>{text}</p>} compact />,
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
      const live = (c: ContentBlock[]) => groupsOf(c, true)[0];
      const r = render(
        <WorkGroupView group={live(head)} onToggle={() => {}} thoughtOpen={() => undefined}
          onThoughtToggle={() => {}} renderNote={(text, key) => <p key={key}>{text}</p>} />,
      );
      const next = [...head, tu('a', 'Running: ls'), res('a'), tu('b', 'Running: pwd'), res('b'), th('newest idea')];
      r.rerender(
        <WorkGroupView group={live(next)} onToggle={() => {}} thoughtOpen={() => undefined}
          onThoughtToggle={() => {}} renderNote={(text, key) => <p key={key}>{text}</p>} />,
      );
      // the same render the new items arrived in: every thought and call still shown as before
      expect(screen.getAllByTestId('thought')).toHaveLength(2);
      expect(screen.getAllByTestId('thought-text')[0].style.webkitLineClamp).toBe('3');
      expect(screen.queryByTestId('step-fold')).toBeNull();
      act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 5); });
      expect(screen.getAllByTestId('thought')).toHaveLength(2); // collapsed, never hidden
      expect(screen.getAllByTestId('thought-text')[0].style.webkitLineClamp).toBe('1');
      expect(screen.getByTestId('step-fold').textContent).toContain('Bash ×2');
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('item 5 — adjacent finished calls fold', () => {
  const six = [
    tu('a', 'Running: ls'), res('a'), tu('b', 'Running: pwd'), res('b'),
    tu('c', 'Reading /r/one.ts', 'Read'), res('c'), tu('d', 'Running: git status'), res('d'),
    tu('e', 'Reading /r/two.ts', 'Read'), res('e'), tu('f', 'Running: make'), res('f'),
  ];

  it('six finished calls are one "Bash ×4 · Read ×2" row that opens to six rows', () => {
    const [g] = groupsOf(six);
    show(g);
    const fold = screen.getByTestId('step-fold');
    expect(fold.textContent).toContain('Bash ×4 · Read ×2');
    expect(screen.queryAllByTestId('step-row')).toHaveLength(0);
    fireEvent.click(within(fold).getByRole('button'));
    expect(screen.getAllByTestId('step-row')).toHaveLength(6);
  });

  it('a running call stays its own row after the fold; one finished call alone is not folded', () => {
    const [live] = groupsOf([...six, tu('g', 'Running: npm test')], true);
    show(live);
    expect(screen.getByTestId('step-fold').textContent).toContain('Bash ×4 · Read ×2');
    const rows = screen.getAllByTestId('step-row');
    expect(rows.map((r) => r.dataset.status)).toEqual(['pending']);
    const [one] = groupsOf([tu('a', 'Running: ls'), res('a')]);
    const r2 = show(one);
    expect(within(r2.container).queryByTestId('step-fold')).toBeNull();
  });

  it('a failed call and an Agent call break the fold and stay rows', () => {
    const [g] = groupsOf([
      tu('a', 'Running: ls'), res('a'), tu('b', 'Running: pwd'), res('b'),
      tu('x', 'Running: false'), res('x', true),
      tu('ag', `Agent: ${DESC}`, 'Agent'), res('ag', false, { subagent: { status: 'completed', toolUses: 2, durationMs: 4000, summary: 'ok' } }),
      tu('c', 'Running: ls'), res('c'),
    ]);
    show(g);
    expect(screen.getAllByTestId('step-fold')).toHaveLength(1);
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
      tu('ag', `Agent: ${DESC}`, 'Agent'), sub('s1', 'ls'), subRes('s1'), sub('s2', 'pwd'), subRes('s2'), sub('s3', 'id'), subRes('s3'),
      res('ag', false, { subagent: { status: 'completed', toolUses: 3, durationMs: 9000, summary: 'ok' } }),
    ]);
    show(g);
    fireEvent.click(screen.getByTestId('agent-toggle'));
    const detail = screen.getByTestId('agent-detail');
    expect(within(detail).getByTestId('step-fold').textContent).toContain('Bash ×3');
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
  it('parallel calls: A finishes after B started; A stays a row for 2s, then joins the fold', () => {
    vi.useFakeTimers();
    try {
      const view = (c: ContentBlock[]) => (
        <WorkGroupView group={groupsOf(c, true)[0]} onToggle={() => {}} thoughtOpen={() => undefined}
          onThoughtToggle={() => {}} renderNote={(text, key) => <p key={key}>{text}</p>} />
      );
      const start = [tu('x', 'Running: ls'), res('x'), tu('a', 'Running: sleep 30'), tu('b', 'Running: make')];
      const r = render(view(start));
      act(() => { vi.advanceTimersByTime(30_000); }); // A has long been followed by B
      r.rerender(view([...start, res('a')])); // A finishes now
      const rowsNow = screen.getAllByTestId('step-row').map((n) => n.dataset.status);
      expect(rowsNow).toContain('done'); // A is still its own row
      expect(screen.queryByTestId('step-fold')).toBeNull();
      act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 5); });
      expect(screen.getByTestId('step-fold').textContent).toContain('Bash ×2');
    } finally {
      vi.useRealTimers();
    }
  });
});
