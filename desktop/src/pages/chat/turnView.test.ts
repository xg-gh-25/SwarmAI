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
import { deriveTurnView, stepLabel, extractNextSteps, isLandmarkText, summarizeProcess } from './turnView';

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

  it('text BETWEEN tools is a process note, interleaved in time order', () => {
    const view = deriveTurnView(
      [tx('looking at X'), tu('a', 'Reading /r/a.ts', 'Read', 'read'), tx('found Y'), tu('b', 'Reading /r/b.ts', 'Read', 'read'), tx('final')],
      false,
    );
    expect(view.units.map((u) => u.kind)).toEqual(['process', 'outcome']);
    const p = view.units[0];
    if (p.kind !== 'process') throw new Error('expected process');
    expect(p.steps.map((s) => s.kind)).toEqual(['note', 'tool', 'note', 'tool']);
    expect(p.isLive).toBe(false);
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

  it('empty content while streaming yields ONE live process row (pre-content indicator, AC10)', () => {
    const view = deriveTurnView([], true);
    expect(view.units).toHaveLength(1);
    const p = view.units[0];
    expect(p.kind === 'process' && p.isLive && p.steps.length).toBe(0);
    expect(deriveTurnView([], false).units).toHaveLength(0);
  });

  it('thinking-only content while streaming is a live process with a thinking step (AC10)', () => {
    const th = { type: 'thinking', thinking: 'pondering the problem' } as ContentBlock;
    const view = deriveTurnView([th], true);
    expect(view.units.map((u) => u.kind)).toEqual(['process']);
    const p = view.units[0];
    expect(p.kind === 'process' && p.isLive).toBe(true);
  });

  it('the live row carries the latest note written between tools', () => {
    const view = deriveTurnView([tu('a', 'Running: ls'), tx('checking the config next'), tu('b', 'Reading /r/c.json', 'Read', 'read')], true);
    const p = view.units[0];
    if (p.kind !== 'process') throw new Error('expected process');
    expect(p.live?.note).toBe('checking the config next');
    expect(p.live?.step?.block.id).toBe('b');
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

describe('summarizeProcess — the collapsed-row facts', () => {
  it('counts tool steps, failures and the top verbs (notes/thinking are not steps)', () => {
    const view = deriveTurnView([
      tu('a', 'Reading /r/a.ts', 'Read', 'read'), tr('a'),
      tx('note'),
      tu('b', 'Reading /r/b.ts', 'Read', 'read'), tr('b'),
      tu('c', 'Running: grep -n x y.ts'), tr('c', true),
      tu('d', 'Running: make test'), tr('d'),
      tx('answer'),
    ], false);
    const p = view.units[0];
    if (p.kind !== 'process') throw new Error('expected process');
    const s = summarizeProcess(p);
    expect(s.stepCount).toBe(4);
    expect(s.errorCount).toBe(1);
    expect(s.counts).toEqual([{ verb: 'read', count: 2 }, { verb: 'search', count: 1 }, { verb: 'run', count: 1 }]);
  });
});

describe('isLandmarkText — no false landmarks from ordinary words (REVIEW fix)', () => {
  it.each(['aggregate 1 more result', 'delegate 2 tasks', 'investigate 0 errors', 'the gate 1 check passed'])(
    '%s is NOT a landmark', (t) => expect(isLandmarkText(t)).toBe(false));
  it('a plain-text gate verdict line still is', () => expect(isLandmarkText('★ GATE 2: ADVERSARIAL → PASS')).toBe(true));
});

describe('REVIEW round 2 — id stability + next-step hygiene', () => {
  it('a segment keeps its id as tools arrive after thinking (expansion survives)', () => {
    const th = { type: 'thinking', thinking: 'planning' } as ContentBlock;
    const before = deriveTurnView([th], true).units[0];
    const after = deriveTurnView([th, tu('a', 'Running: ls'), tu('b', 'Running: pwd')], false).units[0];
    expect(before.kind === 'process' && before.id).toBe('seg-0');
    expect(after.kind === 'process' && after.id).toBe('seg-0');
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
    ['Running: cd ~/.app/logs 2>/dev/null || cd ~/.app\nLOG=$(ls -t ~/.app/logs/b.log | head -1)', 'run', 'LOG=$(ls -t ~/.app/logs/b.log'],
    ['Running: cd /a/b 2>/dev/null\nls -la 2>/dev/null', 'list', '.'],
    ['Running: cd /a/logs 2>/dev/null || cd $HOME/x/logs; pwd; ls -la', 'run', 'pwd'],
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

  it('thinking before a landmark joins the NEXT segment, so reconcile cannot shift ordinals', () => {
    const th = { type: 'thinking', thinking: 'plan' } as ContentBlock;
    const live = [th, tx('## ✦ ① EVALUATE'), tu('a', 'Running: ls'), tr('a'), tx('## ✦ ② THINK'), tu('b', 'Running: pwd'), tr('b'), tx('done')];
    const db = [tx('## ✦ ① EVALUATE'), tu('a', 'Running: ls'), tx('## ✦ ② THINK'), tu('b', 'Running: pwd'), tx('done')];
    const ids = (c: ContentBlock[], s: boolean) => deriveTurnView(c, s).units.filter((u) => u.kind === 'process').map((u) => (u as { id: string }).id);
    expect(ids(live, false)).toEqual(ids(db, false));
    expect(deriveTurnView(live, false).units.map((u) => u.kind)).toEqual(['landmark', 'process', 'landmark', 'process', 'outcome']);
  });

  it('a thinking-only tail while streaming is still a live segment', () => {
    const th = { type: 'thinking', thinking: 'plan' } as ContentBlock;
    const v = deriveTurnView([tx('## ✦ ① EVALUATE'), tu('a', 'Running: ls'), tr('a'), tx('## ✦ ② THINK'), th], true);
    const last = v.units[v.units.length - 1];
    expect(last.kind === 'process' && last.isLive).toBe(true);
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
