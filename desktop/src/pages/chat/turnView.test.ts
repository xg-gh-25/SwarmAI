/**
 * turnView — the single display projection of an assistant turn.
 *
 * What is tested: `deriveTurnView` (units + done rule), `stepLabel` (summary
 * string → human verb/object), `extractNextSteps` (trailing ```next-steps block),
 * `isLandmarkText` (pipeline landmark detection).
 *
 * Fixtures mirror the REAL persisted shape: the backend never persists
 * tool_result blocks, so a reloaded turn is tool_use + text only (verified
 * 2026-10-17: 0 tool_result rows in data.db). A test that seeds tool_results
 * for the history case would re-create the Run-1 false-green.
 */
import { describe, it, expect } from 'vitest';
import type { ContentBlock } from '../../types';
import {
  deriveTurnView, stepLabel, extractNextSteps, isLandmarkText, isGateBlock, groupSteps,
  changedFiles, sectionUnits, toolCall,
} from './turnView';
import type { ToolUseContent } from '../../types';

const tu = (id: string, summary: string, name = 'Bash', category = 'bash'): ContentBlock =>
  ({ type: 'tool_use', id, name, summary, category }) as ContentBlock;
const tr = (toolUseId: string, isError = false): ContentBlock =>
  ({ type: 'tool_result', toolUseId, content: isError ? 'boom' : 'ok', isError, truncated: false }) as ContentBlock;
const tx = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;

function bashRun(n: number): ContentBlock[] {
  return Array.from({ length: n }, (_, i) =>
    tu(`t${i}`, `Running: cd /Users/x/repo; grep -n "needle${i}" src/file${i}.ts`));
}

describe('deriveTurnView — history (no tool_result persisted)', () => {
  it('folds 38 tool calls into ONE process unit, every step done', () => {
    const view = deriveTurnView(bashRun(38), false);
    const process = view.units.filter((u) => u.kind === 'process');
    expect(process).toHaveLength(1);
    expect(view.units).toHaveLength(1);
    const steps = process[0].kind === 'process' ? process[0].steps : [];
    const tools = steps.filter((s) => s.kind === 'tool');
    expect(tools).toHaveLength(38);
    // A turn that ENDS inside a tool call (no answer after it): the last call's
    // outcome is unknowable from history; every earlier call returned.
    expect(tools.slice(0, 37).every((s) => s.kind === 'tool' && s.status === 'done')).toBe(true);
    expect(tools[37].kind === 'tool' && tools[37].status).toBe('unknown');
  });
});

describe('deriveTurnView — streaming / outcome / interim notes', () => {
  it('while streaming only the last tool is pending and it is the live step', () => {
    // Real live shape: each finished tool already has its tool_result in memory.
    const live = bashRun(38).flatMap((b, i) => (i < 37 ? [b, tr(`t${i}`)] : [b]));
    const view = deriveTurnView(live, true);
    expect(view.units).toHaveLength(1);
    const p = view.units[0];
    if (p.kind !== 'process') throw new Error('expected process');
    const tools = p.steps.filter((s) => s.kind === 'tool');
    expect(tools.filter((s) => s.kind === 'tool' && s.status === 'pending')).toHaveLength(1);
    expect(tools[37].kind === 'tool' && tools[37].status).toBe('pending');
    expect(p.isLive).toBe(true);
    expect(p.live?.step?.block.id).toBe('t37');
  });

  it('text after the last tool is the OUTCOME, rendered outside the process', () => {
    const view = deriveTurnView([...bashRun(3), tx('The answer is 42.')], false);
    expect(view.units.map((u) => u.kind)).toEqual(['process', 'outcome']);
    const o = view.units[1];
    expect(o.kind === 'outcome' && o.text).toBe('The answer is 42.');
  });

  it('text BETWEEN tools is a note unit in the stream, in time order (never a step)', () => {
    const view = deriveTurnView(
      [tx('looking at X'), tu('a', 'Reading /r/a.ts', 'Read', 'read'), tx('found Y'), tu('b', 'Reading /r/b.ts', 'Read', 'read'), tx('final')],
      false,
    );
    expect(view.units.map((u) => u.kind)).toEqual(['note', 'process', 'note', 'process', 'outcome']);
    expect(view.units.map((u) => ('text' in u ? u.text : null))).toEqual(['looking at X', null, 'found Y', null, 'final']);
    for (const u of view.units) {
      if (u.kind === 'process') expect(u.steps.map((s) => s.kind)).toEqual(['tool']);
    }
    expect(view.units.filter((u) => u.kind === 'process').map((u) => (u as { id: string }).id)).toEqual(['seg-0', 'seg-1']);
    expect(view.copyText).toBe('final');
  });

  it('a pure-text message is all outcome (unchanged from today)', () => {
    const view = deriveTurnView([tx('hello'), tx('world')], false);
    expect(view.units.map((u) => u.kind)).toEqual(['outcome', 'outcome']);
  });
});

describe('deriveTurnView — landmarks, interrupts, live states, failures', () => {
  it('a pipeline landmark stays in the main stream and splits the process (AC8)', () => {
    const view = deriveTurnView(
      [tu('a', 'Reading /r/a.ts', 'Read', 'read'), tx('## ★ GATE 1: SKEPTIC + SSA\n→ PASS'), tu('b', 'Running: ls'), tu('c', 'Running: ls -la'), tx('done')],
      false,
    );
    expect(view.units.map((u) => u.kind)).toEqual(['process', 'landmark', 'process', 'outcome']);
    expect(view.units[2].kind === 'process' && view.units[2].id).toBe('seg-1');
  });

  it('interrupts are never folded and split the process (AC4)', () => {
    const ask = { type: 'ask_user_question', toolUseId: 'q1', questions: [{ question: 'Q?', header: 'h', options: [], multiSelect: false }] } as ContentBlock;
    const perm = { type: 'cmd_permission_request', requestId: 'p1', toolName: 'Bash', toolInput: {}, reason: 'r' } as ContentBlock;
    const view = deriveTurnView([tu('a', 'Running: ls'), ask, tu('b', 'Running: pwd'), perm], false);
    expect(view.units.map((u) => u.kind)).toEqual(['process', 'interrupt', 'process', 'interrupt']);
  });

  it('empty content while streaming yields no unit: the streaming bee is the pre-content indicator', () => {
    expect(deriveTurnView([], true).units).toHaveLength(0);
    expect(deriveTurnView([], false).units).toHaveLength(0);
  });

  it('thinking-only content while streaming is a live thought (AC10, XG 2026-10-18)', () => {
    const th = { type: 'thinking', thinking: 'pondering the problem' } as ContentBlock;
    const view = deriveTurnView([th], true);
    expect(view.units.map((u) => u.kind)).toEqual(['thought']);
    const t = view.units[0];
    expect(t.kind === 'thought' && [t.live, t.ordinal]).toEqual([true, 0]);
  });

  it('a thought is narration: it closes the tool segment and keeps its place', () => {
    const th = { type: 'thinking', thinking: 'First I read the config.\n\nThen compare the two thresholds.\n' } as ContentBlock;
    const v = deriveTurnView([tu('a', 'Running: ls'), tr('a'), th, tu('b', 'Running: pwd')], true);
    expect(v.units.map((u) => u.kind)).toEqual(['process', 'thought', 'process']);
    const t = v.units[1];
    expect(t.kind === 'thought' && [t.text, t.live]).toEqual([
      'First I read the config.\n\nThen compare the two thresholds.\n', false]);
    const live = v.units[2];
    expect(live.kind === 'process' && live.isLive && live.live?.step?.block.id).toBe('b');
  });

  it('only the NEWEST thought is live; earlier thoughts and notes stay in the stream', () => {
    const th = (t: string) => ({ type: 'thinking', thinking: t }) as ContentBlock;
    const v = deriveTurnView([th('old thought'), tx('checking logs'), tu('a', 'Running: ls'), tr('a'), th('new thought')], true);
    expect(v.units.map((u) => u.kind)).toEqual(['thought', 'note', 'process', 'thought']);
    const [first, , work, last] = v.units;
    expect(first.kind === 'thought' && first.live).toBe(false);
    expect(work.kind === 'process' && work.isLive).toBe(false);
    expect(last.kind === 'thought' && [last.live, last.ordinal]).toEqual([true, 1]);
  });

  it('live row keeps the newest FINISHED step next to the current one', () => {
    const p = deriveTurnView([tu('a', 'Running: ls'), tr('a'), tu('b', 'Running: pwd'), tr('b'), tu('c', 'Running: make')], true).units[0];
    expect(p.kind === 'process' && p.live?.step?.block.id).toBe('c');
    expect(p.kind === 'process' && p.live?.recent?.block.id).toBe('b');
    const between = deriveTurnView([tu('a', 'Running: ls'), tr('a')], true).units[0];
    expect(between.kind === 'process' && between.live?.step).toBeUndefined();
    expect(between.kind === 'process' && between.live?.recent?.block.id).toBe('a');
  });

  it('a finished turn has no live thought', () => {
    const th = { type: 'thinking', thinking: 'plan' } as ContentBlock;
    const v = deriveTurnView([th, tu('a', 'Running: ls'), tr('a'), tx('done')], false);
    expect(v.units.map((u) => u.kind)).toEqual(['thought', 'process', 'outcome']);
    expect(v.units.some((u) => u.kind === 'thought' && u.live)).toBe(false);
  });

  it('a note written between tools sits between the segments; the NEXT segment is live', () => {
    const view = deriveTurnView([tu('a', 'Running: ls'), tx('checking the config next'), tu('b', 'Reading /r/c.json', 'Read', 'read')], true);
    expect(view.units.map((u) => u.kind)).toEqual(['process', 'note', 'process']);
    const [first, note, live] = view.units;
    expect(first.kind === 'process' && first.isLive).toBe(false);
    expect(note.kind === 'note' && note.text).toBe('checking the config next');
    if (live.kind !== 'process') throw new Error('expected process');
    expect(live.isLive).toBe(true);
    expect(live.live?.step?.block.id).toBe('b');
  });

  it('an errored result is status=error; a final unrecovered failure is flagged (AC4)', () => {
    const view = deriveTurnView([tu('a', 'Running: ls'), tr('a', true), tu('b', 'Running: make'), tr('b', true)], false);
    const p = view.units[0];
    if (p.kind !== 'process') throw new Error('expected process');
    expect(p.steps.filter((s) => s.kind === 'tool' && s.status === 'error')).toHaveLength(2);
    expect(p.unrecoveredFailure?.block.id).toBe('b');
  });

  it('a recovered failure (later steps follow) is NOT flagged as unrecovered', () => {
    const view = deriveTurnView([tu('a', 'Running: ls'), tr('a', true), tu('b', 'Running: ls -a'), tr('b'), tx('ok')], false);
    const p = view.units[0];
    expect(p.kind === 'process' && p.unrecoveredFailure).toBeUndefined();
  });

  it('history (no results persisted) reports zero failures instead of guessing', () => {
    const view = deriveTurnView([tu('a', 'Running: ls'), tu('b', 'Running: make')], false);
    const p = view.units[0];
    if (p.kind !== 'process') throw new Error('expected process');
    expect(p.steps.some((s) => s.kind === 'tool' && s.status === 'error')).toBe(false);
    expect(p.unrecoveredFailure).toBeUndefined();
  });
});

describe('stepLabel — backend summary string → verb + object (AC5)', () => {
  const lab = (summary: string, name = 'Bash', category = 'bash') =>
    stepLabel({ type: 'tool_use', id: 'x', name, summary, category } as never);

  it.each([
    ['Running: cd /Users/x/repo && grep -n "foo" a.ts', 'search', '"foo"'],
    ['Running: cd /Users/x/repo; grep -rn toolContext --include=*.ts .', 'search', '"toolContext"'],
    ['Running: cd /a/b; sed -n 1,200p backend/core/tool_summarizer.py', 'read', 'tool_summarizer.py'],
    ['Running: cat components/ContentBlockRenderer.tsx | sed -n 1,306p', 'read', 'ContentBlockRenderer.tsx'],
    ['Running: ls -la desktop/src/pages/chat/components', 'list', 'components'],
    ['Running: cd /a/b && git log --oneline -5 -- desktop/', 'run', 'git log --oneline'],
    ["Running: perl -e 'alarm 90; exec @ARGV' python3 -I /tmp/x/turns.py", 'run', 'turns.py'],
    ['Running: export FOO=1; npx vitest run src/a.test.ts', 'run', 'npx vitest run'],
  ])('bash %s → %s %s', (summary, verb, object) => {
    const l = lab(summary);
    expect(l.verb).toBe(verb);
    expect(l.object).toBe(object);
    expect(l.object).not.toContain('/Users/x');
  });

  it('Read keeps the full path for click-to-open, shows the basename', () => {
    const l = lab('Reading /a/b/c.tsx', 'Read', 'read');
    expect(l).toMatchObject({ verb: 'read', object: 'c.tsx', path: '/a/b/c.tsx' });
  });

  it.each([
    ['Writing to /a/b/d.ts', 'Edit', 'write', 'edit', 'd.ts'],
    ['Searching for useState', 'Grep', 'search', 'search', 'useState'],
    ['Fetching https://example.com/docs/page', 'WebFetch', 'web_fetch', 'fetch', 'example.com/docs/page'],
    ['Searching web for claude opus', 'WebSearch', 'web_search', 'websearch', 'claude opus'],
    ['Loading tools: Bash, Read', 'ToolSearch', 'tool_search', 'loadTools', 'Bash, Read'],
    ['Using skill: s_pdf', 'Skill', 'skill', 'skill', 's_pdf'],
    ['Listing /a/b', 'LS', 'list_dir', 'list', 'b'],
    ['Writing 7 todos → tasks.md', 'TodoWrite', 'todowrite', 'todo', '7 todos'],
    ['Agent: inventory streaming surfaces', 'Agent', 'agent', 'agent', 'inventory streaming surfaces'],
    ['MCP: search_coe — lambda timeout', 'mcp__coe__search_coe', 'fallback', 'mcp', 'search_coe — lambda timeout'],
  ])('%s → %s', (summary, name, category, verb, object) => {
    const l = lab(summary, name, category);
    expect(l.verb).toBe(verb);
    expect(l.object).toBe(object);
  });

  it('an unrecognised summary falls back to the raw text (never loses information)', () => {
    const l = lab('SomeTool: weird context', 'SomeTool', 'fallback');
    expect(l.verb).toBe('tool');
    expect(l.object).toBe('SomeTool: weird context');
  });

  it('raw is always the original summary (third disclosure layer)', () => {
    const s = 'Running: cd /Users/x/repo; grep -n "a" b.ts';
    expect(lab(s).raw).toBe(s);
  });
});

describe('extractNextSteps — trailing ```next-steps block (AC6)', () => {
  it('strips a trailing block and returns its action lines', () => {
    const r = extractNextSteps('Done.\n\n```next-steps\n起 pipeline：过程折叠\n先只改 label 文案\n```\n', false);
    expect(r.text).toBe('Done.');
    expect(r.steps).toEqual(['起 pipeline：过程折叠', '先只改 label 文案']);
  });

  it('caps at 3 steps and strips list bullets', () => {
    const r = extractNextSteps('x\n```next-steps\n- a\n2. b\n* c\nd\n```', false);
    expect(r.steps).toEqual(['a', 'b', 'c']);
  });

  it('no block → text unchanged, no steps', () => {
    const r = extractNextSteps('plain answer with ```code``` inline', false);
    expect(r).toEqual({ text: 'plain answer with ```code``` inline', steps: [] });
  });

  it('a block that is NOT at the end is left alone', () => {
    const t = 'a\n```next-steps\nx\n```\nmore text after';
    expect(extractNextSteps(t, false)).toEqual({ text: t, steps: [] });
  });

  it('an unterminated block at the end of a finished reply is still taken', () => {
    const r = extractNextSteps('a\n```next-steps\nfirst action', false);
    expect(r).toEqual({ text: 'a', steps: ['first action'] });
  });

  it('while streaming, a half-arrived fence never shows as raw text', () => {
    expect(extractNextSteps('answer\n```next-st', true).text).toBe('answer');
    expect(extractNextSteps('answer\n```', true).text).toBe('answer');
    expect(extractNextSteps('answer\n```next-steps\nfirst ac', true)).toEqual({ text: 'answer', steps: [] });
  });

  it('a normal code fence while streaming is not hidden once it diverges', () => {
    expect(extractNextSteps('see\n```python', true).text).toBe('see\n```python');
  });
});

describe('isLandmarkText', () => {
  it.each([
    ['## ✦ ① EVALUATE\n→ GO', true],
    ['## ★ GATE 2: ADVERSARIAL', true],
    ['---\n**PHASE B: EXECUTION**\n---', true],
    ['checking the config next', false],
  ])('%s → %s', (t, v) => expect(isLandmarkText(t)).toBe(v));
});

describe('deriveTurnView — next steps + copy text', () => {
  it('lifts the trailing next-steps block off the final outcome', () => {
    const view = deriveTurnView([tu('a', 'Running: ls'), tx('All good.\n\n```next-steps\nship it\n```')], false);
    const o = view.units[view.units.length - 1];
    expect(o.kind === 'outcome' && o.text).toBe('All good.');
    expect(view.nextSteps).toEqual(['ship it']);
    expect(view.copyText).toBe('All good.');
  });

  it('copy text is the outcome only — process notes are not copied', () => {
    const view = deriveTurnView([tx('working note'), tu('a', 'Running: ls'), tx('Answer A'), tx('Answer B')], false);
    expect(view.copyText).toBe('Answer A\n\nAnswer B');
    expect(view.nextSteps).toEqual([]);
  });

  it('a next-steps block in an EARLIER outcome block is not lifted', () => {
    const view = deriveTurnView([tx('a\n```next-steps\nx\n```'), tx('final')], false);
    expect(view.nextSteps).toEqual([]);
  });
});

describe('isLandmarkText — no false landmarks from ordinary words (REVIEW fix)', () => {
  it.each(['aggregate 1 more result', 'delegate 2 tasks', 'investigate 0 errors', 'the gate 1 check passed'])(
    '%s is NOT a landmark', (t) => expect(isLandmarkText(t)).toBe(false));
  it('a plain-text gate verdict line still is', () => expect(isLandmarkText('★ GATE 2: ADVERSARIAL → PASS')).toBe(true));
});

describe('REVIEW round 2 — id stability + next-step hygiene', () => {
  it('a segment keeps its id as tools arrive after a thought (expansion survives)', () => {
    const th = { type: 'thinking', thinking: 'planning' } as ContentBlock;
    const live = deriveTurnView([th, tu('a', 'Running: ls')], true).units;
    const done = deriveTurnView([th, tu('a', 'Running: ls'), tu('b', 'Running: pwd')], false).units;
    expect(live[1].kind === 'process' && live[1].id).toBe('seg-0');
    expect(done[1].kind === 'process' && done[1].id).toBe('seg-0');
  });

  it('next-step lines are capped in length and stripped of invisible/bidi characters', () => {
    const long = 'x'.repeat(300);
    const r = extractNextSteps(`a\n\`\`\`next-steps\nrun\u202Etxt.exe\u200B ok\n${long}\n\`\`\``, false);
    expect(r.steps[0]).toBe('runtxt.exe ok');
    expect(r.steps[1].length).toBeLessThanOrEqual(120);
  });
});

describe('stepLabel — real-world setup prefixes (Layer-4 replay found these)', () => {
  const lab = (summary: string) => stepLabel({ type: 'tool_use', id: 'x', name: 'Bash', summary, category: 'bash' } as never);
  it.each([
    ['Running: cd /a/repo 2>/dev/null && source backend/.venv/bin/activate 2>/dev/null; python backend/scripts/x.py --run', 'run', 'x.py'],
    ['Running: cd ~/.app/logs 2>/dev/null || cd ~/.app\nLOG=$(ls -t ~/.app/logs/b.log | head -1)', 'list', 'b.log'],
    ['Running: cd /a/b 2>/dev/null\nls -la 2>/dev/null', 'list', '.'],
    ['Running: cd /a/logs 2>/dev/null || cd $HOME/x/logs; pwd; ls -la', 'list', '.'],
    ['Running: cd /a/p 2>/dev/null && git log --oneline -5 2>/dev/null', 'run', 'git log --oneline'],
    ['Running: cd /a/repo || exit\ngrep -n "needs_review" core/x.py', 'search', '"needs_review"'],
    ['Running: cd ~/.c/projects 2>/dev/null || { echo "none"; exit 0; }\nls -d *ws*', 'list', '*ws*'],
    ['Running: export PATH="/a/node/bin:$PATH"\nsource /a/repo/.venv/bin/activate\nnpx vitest run x', 'run', 'npx vitest run'],
    ['Running: cd /Users/someone/Deskt', 'run', '…'],
  ])('%s', (summary, verb, object) => {
    const l = lab(summary);
    expect(l.object).not.toMatch(/^cd\s/);
    expect(l.verb).toBe(verb);
    expect(l.object).toBe(object);
  });
});

describe('Gate-2 fixes', () => {
  it('segment ids survive the stream-end reconcile that drops tool_result + thinking blocks', () => {
    const th = { type: 'thinking', thinking: 'plan' } as ContentBlock;
    const live = [th, tu('a', 'Running: ls'), tr('a'), tu('b', 'Running: pwd'), tr('b'), tx('## ★ GATE 1: PASS'), tu('c', 'Running: make'), tr('c'), tx('done')];
    const db = [tu('a', 'Running: ls'), tu('b', 'Running: pwd'), tx('## ★ GATE 1: PASS'), tu('c', 'Running: make'), tx('done')];
    const ids = (c: ContentBlock[], s: boolean) => deriveTurnView(c, s).units.filter((u) => u.kind === 'process').map((u) => (u as { id: string }).id);
    expect(ids(live, true)).toEqual(ids(db, false));
  });

  it('parallel tool calls without results stay pending while streaming', () => {
    const view = deriveTurnView([tu('a', 'Running: sleep 5'), tu('b', 'Running: sleep 6')], true);
    const p = view.units[0];
    if (p.kind !== 'process') throw new Error('expected process');
    expect(p.steps.map((s) => s.kind === 'tool' && s.status)).toEqual(['pending', 'pending']);
  });

  it('a sequential tool with its result is done while the next one runs', () => {
    const view = deriveTurnView([tu('a', 'Running: ls'), tr('a'), tu('b', 'Running: pwd')], true);
    const p = view.units[0];
    if (p.kind !== 'process') throw new Error('expected process');
    expect(p.steps.map((s) => s.kind === 'tool' && s.status)).toEqual(['done', 'pending']);
  });

  it('streaming after a trailing landmark adds a live row (work is still going on)', () => {
    const view = deriveTurnView([tu('a', 'Running: ls'), tr('a'), tx('## ✦ ② THINK\n→ 3 alternatives')], true);
    expect(view.units.map((u) => u.kind)).toEqual(['process', 'outcome']);
    const view2 = deriveTurnView([tu('a', 'Running: ls'), tr('a'), tx('## ✦ ② THINK'), tu('b', 'Running: pwd'), tr('b'), tx('## ✦ ③ PLAN')], true);
    expect(view2.units.map((u) => u.kind)).toEqual(['process', 'landmark', 'process', 'outcome']);
  });

  it('a summary truncated by the backend (200 chars) never yields a clickable path', () => {
    const long = `Reading /a/${'d'.repeat(200)}/file.ts`.slice(0, 200);
    const l = stepLabel({ type: 'tool_use', id: 'x', name: 'Read', summary: long, category: 'read' } as never);
    expect(l.path).toBeUndefined();
    expect(l.verb).toBe('read');
  });

  it('quoted separators do not cut the command head', () => {
    const l = stepLabel({ type: 'tool_use', id: 'x', name: 'Bash', summary: 'Running: grep -n "a && b" src/x.ts', category: 'bash' } as never);
    expect(l).toMatchObject({ verb: 'search', object: '"a && b"' });
  });

  it('Unicode tag characters, variation selectors and C1 controls are stripped from next steps', () => {
    const tag = String.fromCodePoint(0xE0063, 0xE0075, 0xE0072, 0xE006C); // invisible "curl"
    const r = extractNextSteps(`x\n\`\`\`next-steps\nRun the tests${tag}\uFE0E\u0085\n\`\`\``, false);
    expect(r.steps).toEqual(['Run the tests']);
  });
});

describe('Red-team fixes', () => {
  it('a tool gated by an unanswered permission request is WAITING, not done (live or not)', () => {
    const perm = { type: 'cmd_permission_request', requestId: 'p1', toolName: 'Bash', toolInput: {}, reason: 'r' } as ContentBlock;
    for (const streaming of [true, false]) {
      const p = deriveTurnView([tu('a', 'Running: rm -rf build'), perm], streaming).units[0];
      if (p.kind !== 'process') throw new Error('expected process');
      expect(p.steps[0].kind === 'tool' && p.steps[0].status).toBe('waiting');
    }
    const decided = { ...perm, decision: 'approve' } as ContentBlock;
    // Once answered, the call ran — the text the model wrote after it proves it.
    const p2 = deriveTurnView([tu('a', 'Running: rm -rf build'), decided, tx('removed it')], false).units[0];
    expect(p2.kind === 'process' && p2.steps[0].kind === 'tool' && p2.steps[0].status).toBe('done');
  });

  it('thinking before a landmark stays BEFORE it (stream order); reconcile cannot shift ordinals', () => {
    // Non-empty thinking is persisted with the turn; the reload reconcile only drops
    // live tool_result blocks — so the live and stored views give the same ids.
    const th = { type: 'thinking', thinking: 'plan' } as ContentBlock;
    const live = [th, tx('## ✦ ① EVALUATE'), tu('a', 'Running: ls'), tr('a'), tx('## ✦ ② THINK'), tu('b', 'Running: pwd'), tr('b'), tx('done')];
    const db = [th, tx('## ✦ ① EVALUATE'), tu('a', 'Running: ls'), tx('## ✦ ② THINK'), tu('b', 'Running: pwd'), tx('done')];
    const ids = (c: ContentBlock[], s: boolean) => deriveTurnView(c, s).units.filter((u) => u.kind === 'process').map((u) => (u as { id: string }).id);
    expect(ids(live, false)).toEqual(ids(db, false));
    expect(ids(live, true)).toEqual(['seg-0', 'seg-1']);
    expect(deriveTurnView(live, false).units.map((u) => u.kind)).toEqual(['thought', 'landmark', 'process', 'landmark', 'process', 'outcome']);
  });

  it('a thought at the tail while streaming is the live item', () => {
    const th = { type: 'thinking', thinking: 'plan' } as ContentBlock;
    const v = deriveTurnView([tx('## ✦ ① EVALUATE'), tu('a', 'Running: ls'), tr('a'), tx('## ✦ ② THINK'), th], true);
    const last = v.units[v.units.length - 1];
    expect(last.kind === 'thought' && last.live).toBe(true);
  });

  it('emoji joiners and VS16 survive the invisible-character strip; bidi/tags do not', () => {
    const family = '\u{1F468}‍\u{1F469}‍\u{1F467}';
    const r = extractNextSteps(`x\n\`\`\`next-steps\nShip it ${family} ❤️‮\u{E0041}\n\`\`\``, false);
    expect(r.steps).toEqual([`Ship it ${family} ❤️`]);
  });
});

describe('turn that ended inside a tool call', () => {
  it('the last result-less tool of a finished turn with no answer is UNKNOWN, not done', () => {
    const p = deriveTurnView([tu('a', 'Running: ls'), tu('b', 'Running: make')], false).units[0];
    if (p.kind !== 'process') throw new Error('expected process');
    expect(p.steps.map((s) => s.kind === 'tool' && s.status)).toEqual(['done', 'unknown']);
  });
  it('a normal turn that ends with an answer keeps every tool done', () => {
    const p = deriveTurnView([tu('a', 'Running: ls'), tu('b', 'Running: make'), tx('done')], false).units[0];
    expect(p.kind === 'process' && p.steps.every((s) => s.kind === 'tool' && s.status === 'done')).toBe(true);
  });
});

describe('stepLabel — a Bash step is relabelled only when ALL of it only reads', () => {
  const label = (cmd: string) => stepLabel(tu('x', `Running: ${cmd}`) as ToolUseContent);
  it.each([
    ['grep -n foo src/a.ts | head -5', 'search', '"foo"'],
    ['cd /r && cat a.ts 2>/dev/null', 'read', 'a.ts'],
    ['sed -n 1,40p src/b.py', 'read', 'b.py'],
    ['ls -la /r/docs; wc -l /r/docs/x.md', 'list', 'docs'],
  ])('%s → %s %s', (cmd, verb, object) => {
    expect(label(cmd)).toMatchObject({ verb, object });
  });
  it.each([
    'grep -l foo src | xargs rm',
    'find . -name "*.tmp" -delete',
    'sort -o out.txt in.txt',
    'cat $(ls -t logs | head -1)',
  ])('%s → Run with the command itself', (cmd) => {
    const l = label(cmd);
    expect(l.verb).toBe('run');
    expect(l.object).toBe(cmd.length > 60 ? `${cmd.slice(0, 57)}…` : cmd);
  });
  it.each([
    ['cat a.ts > b.ts', 'b.ts'],
    ['echo hi >> notes.md', 'notes.md'],
  ])('%s writes a file: an edit of %s (counted as changed)', (cmd, object) => {
    expect(label(cmd)).toMatchObject({ verb: 'edit', object });
  });
  it('an in-place sed of one file is an edit of that file (turnView.codexVerbs)', () => {
    expect(label('sed -i s/a/b/ f.ts')).toMatchObject({ verb: 'edit', object: 'f.ts' });
  });
  it('a script behind a writing pipeline keeps its short name', () => {
    expect(label('python3 scripts/x.py --run > out.log')).toMatchObject({ verb: 'run', object: 'x.py' });
  });
});

describe('groupSteps — adjacent reads of one file are one row', () => {
  const rd = (id: string, path = '/r/a.ts') => tu(id, `Reading ${path}`, 'Read', 'read');
  const stepsOf = (c: ContentBlock[]) => {
    const p = deriveTurnView(c, true).units[0];
    return p.kind === 'process' ? p.steps : [];
  };
  it('merges a run of finished reads of the same file, keeps order', () => {
    const rows = groupSteps(stepsOf([rd('a'), tr('a'), rd('b'), tr('b'), rd('c', '/r/b.ts'), tr('c'), rd('d'), tr('d')]));
    expect(rows.map((r) => (r.kind === 'reads' ? `reads×${r.steps.length}` : 'step'))).toEqual(['reads×2', 'step', 'step']);
  });
  it('a failure or a pending read breaks the run', () => {
    expect(groupSteps(stepsOf([rd('a'), tr('a', true), rd('b'), tr('b')]))).toHaveLength(2);
    expect(groupSteps(stepsOf([rd('a'), tr('a'), rd('b')]))).toHaveLength(2);
  });
  it('a thought splits the reads into two segments (it is not a step)', () => {
    const th = { type: 'thinking', thinking: 'hm' } as ContentBlock;
    const v = deriveTurnView([rd('a'), tr('a'), th, rd('b'), tr('b'), tx('x')], false);
    expect(v.units.map((u) => u.kind)).toEqual(['process', 'thought', 'process', 'outcome']);
    const p = v.units[0];
    expect(p.kind === 'process' && p.steps.filter((x) => x.kind === 'tool').length).toBe(1);
  });
});

describe('deriveTurnView — sub-agent steps nest under their Agent step', () => {
  const agent = (id = 'ag') => tu(id, 'Agent: Review spec', 'Agent', 'agent');
  const sub = (id: string, parent = 'ag') =>
    ({ ...(tu(id, `Reading /r/${id}.ts`, 'Read', 'read') as object), parentToolUseId: parent }) as ContentBlock;
  const subRes = (id: string, parent = 'ag', isError = false) =>
    ({ type: 'tool_result', toolUseId: id, isError, outcome: isError ? 'boom' : 'lines 1–9', parentToolUseId: parent }) as ContentBlock;

  it('a sub-agent step is a child of the Agent step, never a main step or the turn boundary', () => {
    const view = deriveTurnView([agent(), sub('s1'), subRes('s1'), sub('s2'), tr('ag'), tx('All good.')], false);
    expect(view.units.map((u) => u.kind)).toEqual(['process', 'outcome']);
    const p = view.units[0];
    if (p.kind !== 'process') throw new Error('expected process');
    expect(p.steps).toHaveLength(1);
    const a = p.steps[0];
    if (a.kind !== 'tool') throw new Error('expected tool');
    expect(a.children?.map((c) => [c.block.id, c.status, c.result?.outcome])).toEqual([
      ['s1', 'done', 'lines 1–9'], ['s2', 'done', undefined]]);
    expect(p.steps.filter((x) => x.kind === 'tool').length).toBe(1);
  });

  it('children share a running or cut-off Agent fate unless their own result came back', () => {
    const live = deriveTurnView([agent(), sub('s1'), subRes('s1', 'ag', true), sub('s2')], true).units[0];
    const a = live.kind === 'process' ? live.steps[0] : undefined;
    expect(a?.kind === 'tool' && a.status).toBe('pending');
    expect(a?.kind === 'tool' && a.children?.map((c) => c.status)).toEqual(['error', 'pending']);
    expect(live.kind === 'process' && live.live?.step?.block.id).toBe('ag');
    const cut = deriveTurnView([agent(), sub('s1')], false).units[0];
    const b = cut.kind === 'process' ? cut.steps[0] : undefined;
    expect(b?.kind === 'tool' && [b.status, b.children?.[0].status]).toEqual(['unknown', 'unknown']);
  });

  it('live sub-agent progress attaches to its Agent step only while streaming', () => {
    const progress = { ag: { parentToolUseId: 'ag', status: 'running', toolUses: 4, durationMs: 9000 } };
    const live = deriveTurnView([agent()], true, progress).units[0];
    expect(live.kind === 'process' && live.steps[0].kind === 'tool' && live.steps[0].progress?.toolUses).toBe(4);
    const done = deriveTurnView([agent(), tx('x')], false, progress).units[0];
    expect(done.kind === 'process' && done.steps[0].kind === 'tool' && done.steps[0].progress).toBeUndefined();
  });

  it('a persisted compact result (no content) gives a reloaded step its failure and outcome', () => {
    const compact = { type: 'tool_result', toolUseId: 'a', isError: true, outcome: 'make: *** no rule' } as ContentBlock;
    const p = deriveTurnView([tu('a', 'Running: make'), compact], false).units[0];
    if (p.kind !== 'process' || p.steps[0].kind !== 'tool') throw new Error('expected tool step');
    expect([p.steps[0].status, p.steps[0].result?.outcome]).toEqual(['error', 'make: *** no rule']);
    expect(p.unrecoveredFailure?.block.id).toBe('a');
  });
});

describe('v7.1 — what changed, stage sections, clickable Bash paths', () => {
  const wr = (id: string, p: string) => tu(id, `Writing to ${p}`, 'Edit', 'write');
  const st = (stage: string, over: Record<string, unknown> = {}) => ({
    type: 'pipeline_stage', runId: 'r', stage, status: 'completed', ordinal: 1, position: 1, total: 8,
    profile: 'full', next: 'think', nextOrdinal: 2, gate: null, summary: null, at: '2026-10-18T00:00:00Z', ...over,
  }) as ContentBlock;

  it('changedFiles: written paths in first-touch order, failed writes excluded', () => {
    const p = deriveTurnView([wr('a', '/r/x.ts'), tr('a'), wr('b', '/r/y.py'), tr('b', true), wr('c', '/r/x.ts'), tr('c'), tx('ok')], false)
      .units[0];
    expect(p.kind === 'process' && changedFiles(p.steps)).toEqual(['/r/x.ts']);
  });

  it('sectionUnits: no stage rows → the units unchanged', () => {
    const v = deriveTurnView([tu('a', 'Running: ls'), tx('done')], false);
    expect(sectionUnits(v.units, false).map((s) => s.kind)).toEqual(['unit', 'unit']);
  });

  it('sectionUnits: alignment stays out; work before a stage row belongs to it; the rest is the running stage', () => {
    const v = deriveTurnView([tx('I read it as X.'), tu('a', 'Running: ls'), tr('a'), st('evaluate'),
      tu('b', 'Running: pwd'), st('think', { ordinal: 2, position: 2, next: 'plan', nextOrdinal: 3 }), tu('c', 'Running: make')], true);
    const secs = sectionUnits(v.units, true, v.liveStage);
    expect(secs.map((s) => s.kind)).toEqual(['unit', 'stage', 'stage', 'liveStage']);
    expect(secs[1].kind === 'stage' && secs[1].children.map((u) => u.kind)).toEqual(['process']);
    expect(secs[2].kind === 'stage' && secs[2].prevAt).toBe('2026-10-18T00:00:00Z');
    const live = secs[3];
    expect(live.kind === 'liveStage' && [live.stage.stage, live.position]).toEqual(['plan', { n: 3, total: 8 }]);
  });

  it('sectionUnits: once the turn ends, what follows the last stage row is flat (the result)', () => {
    const v = deriveTurnView([tu('a', 'Running: ls'), st('deliver', { next: null, nextOrdinal: null }), tx('All done.')], false);
    expect(sectionUnits(v.units, false).map((s) => s.kind)).toEqual(['stage', 'unit']);
  });


  it.each([
    ['cd /a/repo && cat src/x.ts', '/a/repo/src/x.ts'],
    ['sed -n 1,40p /abs/b.py', '/abs/b.py'],
    ['cat ./src/c.md', undefined],             // relative with no known cd: no link
    ['cd /a && cat ../up.ts', undefined],      // escaping the cd: no guess
    ['cd /a && cat ~/x.md', undefined],
    ['cd $HOME && cat x.md', undefined],
  ])('Bash read %s → path %s', (cmd, path) => {
    expect(stepLabel(tu('x', `Running: ${cmd}`) as ToolUseContent).path).toBe(path);
  });

  it('a summary cut at the cap gives no Bash path (it may be cut through the path)', () => {
    const long = `cd /a/repo && cat ${'d/'.repeat(100)}x.ts`;
    const label = stepLabel(tu('x', `Running: ${long}`.slice(0, 200)) as ToolUseContent);
    expect(label.path).toBeUndefined();
  });
});

describe('toolCall — the real tool and the real command (chat display v7.12)', () => {
  const call = (summary: string, name = 'Bash') => toolCall({ type: 'tool_use', id: `x-${summary}`, name, summary } as ToolUseContent);

  it.each([
    ['Running: cd /Users/a/repo\ngit log -6 --format=%h', 'git log -6 --format=%h'],
    ["Running: cd /r && perl -e 'alarm 120; exec @ARGV' backend/.venv/bin/python backend/scripts/x.py --mode verify", 'backend/.venv/bin/python backend/scripts/x.py --mode verify'],
    ['Running: export FOO=1; npx vitest run src/a.test.ts', 'npx vitest run src/a.test.ts'],
    ['Running: timeout 30 curl -s localhost:18321/health', 'curl -s localhost:18321/health'],
    ['Running: R=run_x; python3 -c "import json"', 'R=run_x; python3 -c "import json"'],
    ["Running: cat > /tmp/x/c1.py <<'PYEOF'", "cat > /tmp/x/c1.py <<'PYEOF'"],
  ])('Bash %s → the command itself, setup and wrapper stripped', (summary, text) => {
    const c = call(summary);
    expect(c.tool).toBe('Bash');
    expect(c.text).toBe(text);
  });

  it('never a translated object: no "inline script", no bare program name', () => {
    expect(call('Running: python3 -c "print(1)"').text).toBe('python3 -c "print(1)"');
    expect(call('Running: rsync -av src/ dst/').text).toBe('rsync -av src/ dst/');
  });

  it('a summary cut inside its setup shows an ellipsis, not the setup', () => {
    expect(call('Running: cd /Users/someone/Deskt').text).toBe('…');
  });

  it('file tools: the real tool name and the path (home shown as ~), linked', () => {
    const c = call('Reading /Users/gawan/r/src/a.ts', 'Read');
    expect(c).toEqual({ tool: 'Read', text: '~/r/src/a.ts', path: '/Users/gawan/r/src/a.ts' });
    expect(call('Writing to /abs/b.md', 'Write').tool).toBe('Write');
  });

  it('other tools keep their own name and argument text', () => {
    expect(call('Agent: Gate-2 correctness', 'Agent')).toEqual({ tool: 'Agent', text: 'Gate-2 correctness' });
    expect(call('Agent (Explore): find the store', 'Agent').text).toBe('find the store');
    expect(call('MCP: builder search', 'mcp__builder__search')).toEqual({ tool: 'mcp__builder__search', text: 'builder search' });
  });

  it('every tool_summarizer form loses only the summarizer\'s own words', () => {
    expect(call('Searching web for vite 7 release', 'WebSearch').text).toBe('vite 7 release');
    expect(call('Searching for "needs_review"', 'Grep').text).toBe('"needs_review"');
    expect(call('Fetching https://a.dev/x', 'WebFetch').text).toBe('https://a.dev/x');
    expect(call('Loading tools: a, b', 'ToolSearch').text).toBe('a, b');
    expect(call('Using skill: s_x', 'Skill').text).toBe('s_x');
    expect(call('Writing 3 todos', 'TodoWrite').text).toBe('3 todos');
    expect(call('NotebookEdit: cell 3', 'NotebookEdit').text).toBe('cell 3');
    // `Using <name>` names nothing but the tool: the row is the name alone.
    expect(call('Using ExitPlanMode', 'ExitPlanMode')).toEqual({ tool: 'ExitPlanMode', text: 'ExitPlanMode' });
    // the summarizer's words are case-sensitive: an argument's word is never eaten
    expect(call('reading list', 'Custom').text).toBe('reading list');
  });

  it('an empty or bare Bash summary shows the tool name alone, never an empty command', () => {
    expect(call('', 'Bash')).toEqual({ tool: 'Bash', text: 'Bash' });
    expect(call('Running:', 'Bash')).toEqual({ tool: 'Bash', text: 'Bash' });
  });

  it('a setup segment that runs code stays on the row (it is part of what ran)', () => {
    expect(call('Running: source <(curl -s https://x.example/a) && ls').text).toBe('source <(curl -s https://x.example/a) && ls');
    expect(call('Running: export X=$(cat ~/.aws/credentials) && npm test').text).toBe('export X=$(cat ~/.aws/credentials) && npm test');
    expect(call('Running: cd /r || { curl -o /tmp/x https://x.example; } && git status').text)
      .toBe('cd /r || { curl -o /tmp/x https://x.example; } && git status');
    // a plain setup still goes
    expect(call('Running: cd /r && source .venv/bin/activate && pytest -q').text).toBe('pytest -q');
  });

  it('an MCP row does not repeat the tool name', () => {
    expect(call('MCP: email_search — q=standup', 'mcp__srv__email_search')).toEqual({ tool: 'mcp__srv__email_search', text: 'q=standup' });
    expect(call('MCP: email_search', 'mcp__srv__email_search').text).toBe('mcp__srv__email_search');
  });

  it('display keeps an assignment (the real command); only setup and wrappers go', () => {
    expect(call('Running: cd /r\nLOG=$(ls -t b.log | head -1)').text).toBe('LOG=$(ls -t b.log | head -1)');
  });
});

describe('isGateBlock — only a gate verdict of BLOCK', () => {
  it.each([
    ['★ GATE 1: BLOCK — back to PLAN', true],
    ['**Gate 2:** BLOCK', true],
    ['## ★ GATE 1 · Skeptic → BLOCK', true],
    ['Gate 1 BLOCK', true],
    ['gate 0: block', true],
    ['Gate 1：BLOCK', true],
    ['GATE 1 判定：BLOCK', true],
    ['Gate 1 · BLOCK', true],
    ['Gate 1 ⛔ BLOCK', true],
    ['Gate 1 → PASS; Skeptic: BLOCK resolved', false],
    ['Gate 1 → 0 BLOCK', false],
    ['Gate 1 non-blocking note', false],
    ['★ GATE 0 PASS · 0 BLOCK', false],
    ['Gate 2 → PASS (2 BLOCK findings fixed)', false],
    ['## ✦ ⑦ TEST\n→ BLOCK list unchanged', false],
  ])('%s → %s', (text, blocks) => {
    expect(isGateBlock(text)).toBe(blocks);
  });

  it('the module has no regex lookbehind (the macOS 10.15 webview cannot parse one)', async () => {
    const { readFileSync } = await import('fs');
    const { join, dirname } = await import('path');
    const { fileURLToPath } = await import('url');
    const src = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'turnView.ts'), 'utf-8');
    expect(src).not.toMatch(/\(\?<[!=]/);
  });

  it('a PASS gate with a zero BLOCK count keeps the running stage', () => {
    const st = { type: 'pipeline_stage', runId: 'r', stage: 'plan', status: 'completed', ordinal: 3, position: 3, total: 8,
      profile: 'full', next: 'build', nextOrdinal: 4, gate: null, summary: 'ok', at: 'T' } as ContentBlock;
    const v = deriveTurnView([tu('a', 'Running: ls'), tr('a'), st, tx('★ GATE 1 PASS · 0 BLOCK'), tu('b', 'Running: make')], true);
    expect(v.liveStage?.stage).toBe('build');
  });
});

describe('midWork — a compacted / resumed tail', () => {
  it('is on the view', () => {
    const tail = deriveTurnView([tr('a'), { type: 'thinking', thinking: 'continuing the fix' } as ContentBlock, tu('b', 'Running: ls'), tx('done')], false);
    expect(tail.midWork).toBe(true);
    expect(deriveTurnView([tu('b', 'Running: ls'), tx('done')], false).midWork).toBe(false);
  });
});

describe('the running stage row survives the pipeline\'s own stage landmark', () => {
  const st = (stage: string, next: string | null): ContentBlock => ({
    type: 'pipeline_stage', runId: 'r', stage, status: 'completed', ordinal: 7, position: 6, total: 8, profile: 'full',
    next, nextOrdinal: 9, gate: null, summary: 'ok', at: '2026-10-18T00:00:00Z',
  }) as ContentBlock;

  it('"✦ ⑦ TEST" after the TEST row keeps DELIVER as the running stage', () => {
    const v = deriveTurnView([tu('a', 'Running: ls'), tr('a'), st('test', 'deliver'), tx('## ✦ ⑦ TEST\n→ 137 passed'), tu('b', 'Running: make')], true);
    expect(v.liveStage?.stage).toBe('deliver');
  });

  it('a Gate BLOCK after the row still hides it (the run goes back a stage)', () => {
    const v = deriveTurnView([tu('a', 'Running: ls'), tr('a'), st('plan', 'build'), tx('★ GATE 1: BLOCK — back to PLAN'), tu('b', 'Running: make')], true);
    expect(v.liveStage).toBeUndefined();
  });
});

describe('a Bash write-only command counts as a changed file', () => {
  it('cat > file <<EOF and tee file are edits of that file', () => {
    const w = (summary: string) => stepLabel({ type: 'tool_use', id: `w-${summary}`, name: 'Bash', summary } as ToolUseContent);
    expect(w("Running: cat > /tmp/x/c1.py <<'PYEOF'")).toMatchObject({ verb: 'edit', object: 'c1.py', path: '/tmp/x/c1.py' });
    expect(w('Running: tee /tmp/x/a.md').verb).toBe('edit');
    // an assignment in front does not hide the write
    expect(w('Running: R=run_x; cat > /tmp/x/c2.py <<EOF')).toMatchObject({ verb: 'edit', object: 'c2.py' });
  });
});
