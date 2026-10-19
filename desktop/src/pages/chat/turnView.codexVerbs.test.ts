/**
 * toolRow — what a tool row says (Codex-style verbs, XG 2026-10-19).
 *
 * What is tested: `toolRow(block)` turns a tool_use into the row's verb + real
 * object (Read <file>, Search "<q>", List <dir>, Edited / Wrote <file>, Ran
 * <command>) and whether the row may join an "Explored" line. Built on the one
 * classifier (`stepLabel` / labelBash); the object is always a value from the call.
 *
 * Negative cases: a command that may write is never an exploration row, and an
 * unrecognised one shows its own first segment (Ran <command>), never an invented
 * file name.
 */
import { describe, it, expect } from 'vitest';
import type { ContentBlock, ToolUseContent } from '../../types';
import { toolRow, stepLabel } from './turnView';

const tu = (summary: string, name = 'Bash', category = 'bash'): ToolUseContent =>
  ({ type: 'tool_use', id: `x-${summary}`, name, summary, category }) as ContentBlock as ToolUseContent;
const bash = (cmd: string) => toolRow(tu(`Running: ${cmd}`));

describe('toolRow — file tools', () => {
  it('Read names the file', () => {
    expect(toolRow(tu('Reading /r/src/a.ts', 'Read', 'read'))).toMatchObject({ verb: 'Read', object: 'a.ts', path: '/r/src/a.ts', explore: true });
  });
  it('Write is Wrote, Edit and MultiEdit are Edited', () => {
    expect(toolRow(tu('Writing to /r/x.md', 'Write', 'write'))).toMatchObject({ verb: 'Wrote', object: 'x.md', explore: false });
    expect(toolRow(tu('Writing to /r/y.ts', 'Edit', 'write'))).toMatchObject({ verb: 'Edited', object: 'y.ts', explore: false });
    expect(toolRow(tu('Writing to /r/z.ts', 'MultiEdit', 'write'))).toMatchObject({ verb: 'Edited', object: 'z.ts' });
  });
  it('Grep and Glob are exploration', () => {
    expect(toolRow(tu('Searching for heartbeat', 'Grep', 'search'))).toMatchObject({ verb: 'Search', explore: true });
    expect(toolRow(tu('Listing /r/docs', 'Glob', 'search'))).toMatchObject({ verb: 'List', object: 'docs', explore: true });
  });
});

describe('toolRow — shell commands', () => {
  it.each([
    ['sed -n 1,80p pages/chat/turnView.ts', 'Read', 'turnView.ts', true],
    ['cd /r && cat a.ts 2>/dev/null', 'Read', 'a.ts', true],
    ['grep -rn "heartbeat" backend/routers', 'Search', '"heartbeat"', true],
    ['ls -la i18n/locales', 'List', 'locales', true],
    ['cat > /tmp/r/cleanup.py <<\'EOF\'', 'Wrote', 'cleanup.py', false],
    ['echo hi >> notes.md', 'Wrote', 'notes.md', false],
    ["sed -i '' 's/a/b/' src/f.ts", 'Edited', 'f.ts', false],
    ["perl -pi -e 's/a/b/' src/g.ts", 'Edited', 'g.ts', false],
    ['git status --short', 'Ran', 'git status --short', false],
    ['npm run build:all', 'Ran', 'npm run build:all', false],
    ['python backend/scripts/artifact_cli.py run-update --project P', 'Ran', 'artifact_cli.py run-update', false],
    ["perl -e 'alarm 110; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py --mode tdd", 'Ran', 'pipeline_test.py', false],
  ])('%s → %s %s', (cmd, verb, object, explore) => {
    expect(bash(cmd)).toMatchObject({ verb, object, explore });
  });

  it.each([
    ['find src -name "*.test.ts"', 'Search', '"*.test.ts"'],
    ['find -type f', 'Search', '.'],
    ['grep ">" src/a.ts', 'Search', '">"'],
    ['cat src/a.ts 2>/dev/null', 'Read', 'a.ts'],
    ['cat src/a.ts 2>&1', 'Read', 'a.ts'],
  ])('a read stays a read: %s → %s %s', (cmd, verb, object) => {
    expect(bash(cmd)).toMatchObject({ verb, object, explore: true });
  });

  it('an in-place edit names its ONE file — never the script, never one of several', () => {
    expect(bash("sed -i '/foo/d' Makefile")).toMatchObject({ verb: 'Edited', object: 'Makefile' });
    expect(bash("sed -i 'y/abc/xyz/' Makefile")).toMatchObject({ verb: 'Edited', object: 'Makefile' });
    expect(bash("sed -i -e 's/a/b/' src/f.ts")).toMatchObject({ verb: 'Edited', object: 'f.ts' });
    expect(bash("sed -i 's/a/b/' src/a.ts src/b.ts").verb).toBe('Ran');
    // -M / -I are module and include switches, not -i / -p: printing to stdout
    expect(bash("perl -Mstrict -pe 's/a/b/' src/f.txt").verb).not.toBe('Edited');
    expect(bash("perl -mList::Util -pe 's/a/b/' src/f.txt").verb).not.toBe('Edited');
  });

  it('a command that may write is never an exploration row (fail closed)', () => {
    for (const cmd of ['grep -l foo src | xargs rm', 'find . -name "*.tmp" -delete', 'cat $(ls -t logs | head -1)', 'sort -o out.txt in.txt',
      // background job, process substitution, an attached redirection, write / execute options
      'cat a.txt & rm -rf build/', 'cat <(rm -rf out.txt)', 'grep foo src/a.ts>out.txt', 'cat a.ts &>log.txt',
      'tree -o out.txt', "sed -n 'w out.txt' a.ts", "sed 's/a/b/w out.txt' a.ts", 'rg --pre ./x.sh foo',
      "sed -n 's/a/b/gw out.txt' a.ts", "sed 's/x/y/ge' a.ts"]) {
      const r = bash(cmd);
      expect(r.verb).toBe('Ran');
      expect(r.explore).toBe(false);
    }
  });

  it('an unrecognised command shows its own command, never an invented file', () => {
    const r = bash('grep -l foo src | xargs rm');
    expect(r.object).toBe('grep -l foo src | xargs rm');
    expect(r.path).toBeUndefined();
  });

  it('the full command stays available for the open / running row', () => {
    const r = bash('cd /r && sed -n 1,80p a.ts');
    expect(r.command).toBe('sed -n 1,80p a.ts');
  });

  it('an in-place sed counts as a changed file (stepLabel edit with a path)', () => {
    const l = stepLabel(tu("Running: cd /r && sed -i '' 's/a/b/' src/f.ts"));
    expect(l).toMatchObject({ verb: 'edit', object: 'f.ts', path: '/r/src/f.ts' });
  });
});
