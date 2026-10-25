/**
 * One column grid for a chat turn (docs/design/2026-10-24-turn-column-grid.md).
 *
 * What is tested (real AssistantMessageView + turnZones + TurnZones): a row's x is set
 * by its kind, never by how deep it is nested. Over a live chat turn (the newest note's
 * segment open, an open thought, an Agents panel), the same turn settled and then opened
 * by the user, a live pipeline stage with notes, and a stopped turn:
 * - every mark (▸, stage ●/✓) is in the gutter (0);
 * - every text row (group labels, notes, restatement, RESULT, the stopped line) at 22;
 * - every work row (strip, thought row, Agents panel) has its box at 22;
 * - an opened thought's text sits at one fixed inset from its row, whatever the depth;
 * - a row's x while live equals its x once settled.
 * Method: x from the Tailwind classes on the element's path (helpers/columnOffset, which
 * throws on an unknown spacing class). Plus a source guard: the 22px offset is written
 * only in the grid constants.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, act } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { AssistantMessageView } from '../AssistantMessageView';
import { ToastProvider } from '../../../../contexts/ToastContext';
import { FOLD_DELAY_MS } from '../../../../hooks/useLinger';
import type { Message, ContentBlock } from '../../../../types';
import { columnOffset, contentOffset, turnRoot } from './helpers/columnOffset';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (_k: string, f: unknown) => f }) }));
vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({ health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 }, triggerHealthCheck: vi.fn() }),
}));
afterEach(() => { vi.useRealTimers(); });

const tx = (text: string) => ({ type: 'text', text }) as ContentBlock;
const th = (thinking: string) => ({ type: 'thinking', thinking }) as ContentBlock;
const bash = (id: string, cmd: string) => ({ type: 'tool_use', id, name: 'Bash', summary: `Running: ${cmd}`, category: 'bash' }) as ContentBlock;
const tr = (id: string) => ({ type: 'tool_result', toolUseId: id, content: 'ok', isError: false, truncated: false }) as ContentBlock;
const agent = (id: string) => ({ type: 'tool_use', id, name: 'Agent', summary: 'Agent: review', category: 'agent' }) as ContentBlock;
const RUN = { type: 'pipeline_run', runId: 'r', profile: 'full', stages: ['build', 'deliver'], requirement: 'R', current: null,
  at: '2026-10-19T10:00:00+08:00' } as ContentBlock;
const stage = (s: string, status: string) => ({ type: 'pipeline_stage', runId: 'r', stage: s, status, ordinal: null, position: null,
  total: 2, profile: 'full', next: null, nextOrdinal: null, gate: null, summary: null, at: '2026-10-19T10:00:00+08:00' }) as ContentBlock;
const stopped = { type: 'turn_stop', at: '2026-10-19T10:01:00+00:00', calls: [{ toolUseId: 'z', name: 'Bash', elapsedMs: 3000 }], agents: [] } as unknown as ContentBlock;

const view = (content: ContentBlock[], s: boolean) => {
  const m: Message = { id: 'm1', role: 'assistant', content, timestamp: '2026-10-19T10:00:00+08:00', isError: false };
  return <ToastProvider><AssistantMessageView message={m} isStreaming={s} isLastAssistant sessionId="s1" /></ToastProvider>;
};

const GUTTER = 0;
const TEXT = 22;
const WORK = 22;

/** Every row of every kind in the turn, with its x. */
function grid(c: HTMLElement) {
  const root = turnRoot(c);
  const all = (sel: string) => [...root.querySelectorAll(sel)];
  return {
    marks: [
      ...all('[data-testid="group-toggle"],[data-testid="stage-toggle"],[data-testid="thinking-toggle"]').map((h) => columnOffset(h.children[0], root)),
      ...all('[data-testid="phase-text"]').map((n) => columnOffset(n.children[0], root)),
      ...all('[data-testid="stop-summary"] [data-testid="stop-mark"]').map((m) => columnOffset(m, root)),
      ...all('[data-testid="zone-gate"]').map((g) => columnOffset(g.children[0], root)),
      ...all('[data-testid="zone-stage-result"]').map((r) => columnOffset(r.children[0].children[0], root)),
    ],
    text: [
      ...all('[data-testid="group-toggle"],[data-testid="stage-toggle"],[data-testid="thinking-toggle"]').map((h) => columnOffset(h.children[1], root)),
      ...all('[data-testid="phase-text"]').map((n) => columnOffset(n.children[1], root)),
      ...all('[data-testid="turn-result"],[data-testid="turn-answer"],[data-testid="turn-note"],[data-testid="turn-restatement"],[data-testid="zone-note"]').map((e) => contentOffset(e, root)),
      ...all('[data-testid="turn-stopped"]').map((e) => contentOffset(e, root)),
      ...all('[data-testid="stop-summary"] [data-testid="stop-text"]').map((e) => columnOffset(e, root)),
      ...all('[data-testid="zone-gate"]').map((g) => columnOffset(g.children[1], root)),
      ...all('[data-testid="zone-stage-result"]').map((r) => columnOffset(r.children[0].children[1], root)),
    ],
    work: [
      // a merged strip's opened rows (its calls and the thoughts carried in) are its detail
      ...all('[data-testid="tool-strip"] > button').filter((b) => !b.closest('[data-testid="strip-detail"]')).map((b) => columnOffset(b, root)),
      ...all('[data-testid="thought-toggle"]').filter((b) => !b.closest('[data-testid="strip-detail"]')).map((b) => columnOffset(b, root)),
      ...all('[data-testid="agents-panel"]').map((p) => columnOffset(p, root)),
    ],
    // a row's own opened body, whichever kind of row it belongs to
    detail: all('[data-testid="thought-text"],[data-testid="strip-detail"],[data-testid="stop-detail"]').map((d) => contentOffset(d, root)),
    // full-width blocks: first-level zones that are cards, a card inside a stage, the bee
    cards: [
      ...all('[data-zone="needs"],[data-zone="failure"],[data-zone="pipelineStart"]').map((z) => columnOffset(z, root)),
      ...all('[data-testid="card-slot"]').map((z) => columnOffset(z, root)),
      ...all('[data-testid="streaming-bee"]').map((b) => columnOffset(b, root)),
    ],
  };
}
const only = (xs: number[]) => [...new Set(xs)];
const DETAIL = 43; // the text / work column + DETAIL_INSET

function expectGrid(c: HTMLElement, label: string, needWork = true) {
  const g = grid(c);
  if (needWork) expect(g.work.length, `${label}: has work rows`).toBeGreaterThan(0);
  expect(only(g.marks), `${label}: marks`).toEqual([GUTTER]);
  expect(only(g.text), `${label}: text`).toEqual([TEXT]);
  if (g.work.length) expect(only(g.work), `${label}: work`).toEqual([WORK]);
  if (g.detail.length) expect(only(g.detail), `${label}: detail`).toEqual([DETAIL]);
  if (g.cards.length) expect(only(g.cards), `${label}: cards`).toEqual([0]);
  return g;
}

const CHAT = [th('first'), tx('I read it as X.'), bash('a', 'git status'), tr('a'), th('mid thought'), bash('b', 'sed -n'), tr('b'),
  tx('Done with part one; next part two.'), bash('c', 'perl -pi'), tr('c'), agent('g'), tr('g'), th('live thought now')];

describe('one column grid for a turn', () => {
  it('live chat turn: the open segment, its thought and its Agents panel are on the grid', () => {
    expectGrid(render(view(CHAT, true)).container, 'live');
  });

  it('settled and opened by the user: the same columns, and each row where it was live', () => {
    vi.useFakeTimers();
    const r = render(view(CHAT, true));
    // the live thought shows one line (attention tiers §6): open it, as the user would,
    // so its detail column is on the live grid to compare with
    for (const b of r.container.querySelectorAll('[data-testid="thought-toggle"][aria-expanded="false"]')) act(() => { (b as HTMLElement).click(); });
    const live = grid(r.container);
    r.rerender(view([...CHAT, tx('Final answer.')], false));
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    // open everything, level by level (opening a group reveals its notes and thoughts)
    for (let round = 0; round < 5; round++) {
      const closed = [...r.container.querySelectorAll('[data-testid="group-toggle"],[data-testid="segment-toggle"],[data-testid="thought-toggle"]')]
        .filter((b) => b.getAttribute('aria-expanded') !== 'true');
      if (!closed.length) break;
      for (const b of closed) act(() => { (b as HTMLElement).click(); });
    }
    const settled = expectGrid(r.container, 'settled+opened');
    expect(only(settled.detail)).toEqual(only(live.detail));
  });

  it('a live pipeline stage with notes is on the same grid', () => {
    const blocks = [tx('Start.'), RUN, stage('build', 'in_progress'), bash('a', 'make'), tr('a'), tx('stage note'),
      bash('b', 'make test'), tr('b'), th('stage thought')];
    expectGrid(render(view(blocks, true)).container, 'pipeline');
  });

  it('a stopped turn: its line and receipt are in the text column, the receipt mark in the gutter', () => {
    const blocks = [tx('Go.'), bash('a', 'ls'), tr('a'), bash('z', 'sleep 9'), stopped];
    const r = render(view(blocks, false));
    const g = expectGrid(r.container, 'stopped', false);
    expect(g.text.length, 'the stopped line is measured').toBeGreaterThan(0);
    act(() => { (r.container.querySelector('[data-testid="stop-summary"]') as HTMLElement).click(); });
    expect(expectGrid(r.container, 'stopped, receipt opened', false).detail.length).toBeGreaterThan(0);
  });

  it('pipeline: gate, divider, and a question card inside a stage sit where they sit at the first level', () => {
    const gate = { ...stage('gate1', 'completed'), gate: { name: 'GATE 1', verdict: 'PASS' } } as ContentBlock;
    const ask = { type: 'ask_user_question', toolUseId: 'q1', questions: [{ question: 'Which one?', header: 'Q',
      options: [{ label: 'A', description: 'a' }, { label: 'B', description: 'b' }], multiSelect: false }] } as unknown as ContentBlock;
    const ask0 = { ...ask, toolUseId: 'q0' } as ContentBlock;
    const blocks = [tx('Start.'), bash('p', 'ls'), tr('p'), ask0, RUN, stage('build', 'completed'), gate,
      stage('deliver', 'in_progress'), bash('a', 'make'), tr('a'), ask];
    const r = render(view(blocks, false));
    const g = expectGrid(r.container, 'pipeline cards', false);
    expect(g.cards.length, 'divider + first-level card + stage card').toBeGreaterThanOrEqual(3);
    expect(r.container.querySelectorAll('[data-testid="card-slot"]').length, 'a card inside a stage').toBe(1);
    expect(r.container.querySelectorAll('[data-testid="zone-gate"]').length).toBe(1);
  });

  it('a direct run: its stage result row has its mark in the gutter and its name in the text column', () => {
    const d = (b: ContentBlock) => ({ ...b, profile: 'direct' }) as ContentBlock;
    const done = { ...stage('build', 'completed'), summary: 'built it' } as ContentBlock;
    const r = render(view([tx('Go.'), d(RUN), d(done), bash('a', 'ls'), tr('a'), tx('Done.')], false));
    expect(r.container.querySelectorAll('[data-testid="zone-stage-result"]').length).toBe(1);
    expectGrid(r.container, 'direct run', false);
  });

  it('an opened strip shows its detail at the same inset as an opened thought', () => {
    const bad = { type: 'tool_result', toolUseId: 'b', content: 'boom', outcome: 'exit 1: boom', isError: true, truncated: false } as ContentBlock;
    // two identical adjacent calls merge into one strip, which opens to one row per call
    const r = render(view([tx('Go.'), bash('a', 'ls'), tr('a'), bash('b', 'ls'), bad, th('then'), bash('c', 'pwd')], true));
    for (const b of r.container.querySelectorAll('[data-testid="tool-strip"] > button[aria-expanded="false"]')) act(() => { (b as HTMLElement).click(); });
    const g = expectGrid(r.container, 'strip detail');
    expect(r.container.querySelectorAll('[data-testid="strip-detail"]').length).toBeGreaterThan(0);
    expect(g.detail.length).toBeGreaterThan(0);
  });

  it('the bee is a full-width block at the card column', () => {
    const r = render(view([tx('Go.'), bash('a', 'ls')], true));
    expect(r.container.querySelectorAll('[data-testid="streaming-bee"]').length).toBe(1);
    expectGrid(r.container, 'bee');
  });

  it('source guard: no other left offset is written in the turn components', () => {
    const src = readFileSync(resolve(__dirname, '../TurnZones.tsx'), 'utf8');
    // any arbitrary-px left offset, anywhere: only the three grid constants
    const px = [...src.matchAll(/-?(?:ml|pl|mx|px|left|inset-x)-\[\d+(?:\.\d+)?px\]/g)].map((m) => m[0]);
    expect(px.sort(), 'GROUP_BODY, TEXT_COL and HANG are the only px offsets').toEqual(['-ml-[22px]', 'ml-[22px]', 'pl-[22px]']);
    // no inline left offsets (columnOffset cannot see them)
    const styles = [...src.matchAll(/style=\{\{([^}]*)\}\}/g)].map((m) => m[1]).join(' ');
    expect(styles).not.toMatch(/\b(?:marginLeft|paddingLeft|left|marginInlineStart|paddingInlineStart)\s*:|translateX?\(/);
    // the row-internal insets that remain, listed (each one is inside a row, not per depth)
    const scale = [...src.matchAll(/(?:^|[\s`'"])(-?(?:ml|pl)-\d+(?:\.\d+)?)(?=[\s`'"])/g)].map((m) => m[1]);
    expect([...new Set(scale)].sort()).toEqual(['-ml-1', 'ml-2', 'ml-6', 'pl-2', 'pl-3', 'pl-4']);
  });
});
