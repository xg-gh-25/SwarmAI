/**
 * Strip merging by kind (chat turn spec v6 §3.1, revised 2026-10-25).
 *
 * Pure tests of turnZones: `isReadOnlyCommand` (a Bash counts as exploring only when
 * every segment runs a known read-only program and nothing writes), `stripKind`
 * (Explored / Edited / Ran / none) and `mergeColumn` (adjacent settled calls of one kind
 * merge, a failed call joins, a finished thought between two such calls is carried into
 * the merged row in order; a running call, an unsettled call, an Agent, a thought with no
 * settled call after it and an Edit next to a read never merge).
 */
import { describe, it, expect } from 'vitest';
import type { ThoughtUnit, ToolStep } from '../turnView';
import { isReadOnlyCommand, mergeColumn, mergeStrips, stripKind, stripLabel } from '../turnZones';
import type { StripItem } from '../turnZones';
import type { ToolUseContent } from '../../../types';

let n = 0;
const call = (name: string, object: string, status: ToolStep['status'] = 'done'): ToolStep => {
  n += 1;
  const block = { type: 'tool_use', id: `c${n}`, name, summary: name === 'Bash' ? `Running: ${object}` : object, category: 'x',
    display: { tool: name, object, key: `${name}:${object}` } } as ToolUseContent;
  return { kind: 'tool', block, index: n, status };
};
const bash = (cmd: string, status?: ToolStep['status']) => call('Bash', cmd, status);
const read = (p: string) => call('Read', p);
const edit = (p: string) => call('Edit', p);
const thought = (text: string, live = false): ThoughtUnit => ({ kind: 'thought', text, index: (n += 1), ordinal: n, live });
const all = () => true;
const strips = (out: ReturnType<typeof mergeColumn>) => out.filter((x): x is StripItem => 'steps' in x);

describe('isReadOnlyCommand', () => {
  it.each([
    'sed -n 1,80p hooks/a.ts',
    'grep -rn "x" src | head -5',
    'cd src && wc -l a.ts b.ts; cat c.ts',
    'git status --short && git log --oneline -3 && git diff main...HEAD --stat',
    'ls -la src 2>/dev/null',
    'rg foo backend/core | tail -20',
    'find . -name "*.ts" -maxdepth 2',
  ])('read-only: %s', (cmd) => expect(isReadOnlyCommand(cmd)).toBe(true));
  it.each([
    'sed -i s/a/b/ x.ts',
    'cat > a.py <<EOF',
    'grep x a > out.txt',
    'python3 - <<\'EOF\'',
    'npx vitest run src/a.test.ts',
    'git commit -m x -- a.ts',
    'git branch -D old',
    'find . -name x -delete',
    'cat $(ls)',
    'make test',
    'ls; rm -rf build',
    '',
  ])('not read-only: %s', (cmd) => expect(isReadOnlyCommand(cmd)).toBe(false));
});

describe('stripKind', () => {
  it('reads, edits, commands, agents', () => {
    expect(stripKind(read('/p/a.ts'))).toBe('explore');
    expect(stripKind(call('Grep', 'x'))).toBe('explore');
    expect(stripKind(bash('sed -n 1,2p a'))).toBe('explore');
    expect(stripKind(edit('/p/a.ts'))).toBe('edit');
    expect(stripKind(call('Write', '/p/b.ts'))).toBe('edit');
    expect(stripKind(bash('npm test'))).toBe('run');
    expect(stripKind(call('Agent', 'x'))).toBeNull();
    expect(stripKind(call('mcp__x__y', 'q'))).toBeNull();
  });
});

describe('mergeColumn', () => {
  it('adjacent reads of different tools merge as Explored, with a thought between carried in order', () => {
    const a = read('/p/a.ts'); const t = thought('look at b'); const b = bash('grep -n x b.ts'); const c = call('Glob', '*.ts');
    const out = mergeColumn([a, t, b, c], all);
    expect(out).toHaveLength(1);
    const it0 = out[0] as StripItem;
    expect(it0.steps.map((s) => s.block.id)).toEqual([a.block.id, b.block.id, c.block.id]);
    expect(it0.seq).toEqual([a, t, b, c]);
    expect(it0.key).toBe(`tu-${a.block.id}`);
    expect(stripLabel(it0).verb).toBe('Explored');
  });

  it('commands merge as Ran, a failed one joins; Edit stays out of reads and commands', () => {
    const out = mergeColumn([bash('npm test'), bash('make lint', 'error'), edit('/p/a.ts'), edit('/p/b.ts'), read('/p/c.ts')], all);
    expect(strips(out).map((s) => [stripLabel(s).verb, s.steps.length])).toEqual([['Ran', 2], ['Edited', 2], [undefined, 1]]);
  });

  it('a running, waiting, unsettled call or an Agent never merges; a thought before it stays its own row', () => {
    const a = read('/p/a.ts'); const t = thought('next'); const b = read('/p/b.ts', );
    expect(mergeColumn([a, t, { ...b, status: 'pending' }], all)).toHaveLength(3);
    expect(mergeColumn([a, t, b], (id) => id !== b.block.id)).toHaveLength(3);
    expect(mergeColumn([call('Agent', 'x'), call('Agent', 'x')], all)).toHaveLength(2);
  });

  it('a thought with no settled call after it is never absorbed (the one being written included)', () => {
    const out = mergeColumn([read('/p/a.ts'), read('/p/b.ts'), thought('writing…', true)], all);
    expect(out.map((x) => ('steps' in x ? 'strip' : 'thought'))).toEqual(['strip', 'thought']);
  });

  it('a thought between two calls that do not merge stays where it was', () => {
    const out = mergeColumn([read('/p/a.ts'), thought('now edit'), edit('/p/a.ts')], all);
    expect(out.map((x) => ('steps' in x ? 'strip' : 'thought'))).toEqual(['strip', 'thought', 'strip']);
  });

  it('a single-tool merge with no thought keeps the old label (Read ×N); objects are distinct', () => {
    const it0 = strips(mergeColumn([read('/p/a.ts'), read('/p/b.ts'), read('/p/a.ts')], all))[0];
    expect(stripLabel(it0).verb).toBeUndefined();
    expect(it0.objects).toEqual(['a.ts', 'b.ts']);
  });

  it('mergeStrips is mergeColumn over calls only', () => {
    const s = [bash('npm test'), bash('make lint')];
    expect(mergeStrips(s, all).map((x) => x.steps.length)).toEqual([2]);
  });
});
