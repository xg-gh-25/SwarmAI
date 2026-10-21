/**
 * turnZones / stripView / mergeStrips — the first level of a chat turn and its tool
 * strips (chat turn spec v6, Knowledge/Designs/2026-10-19-chat-turn-display-spec.md).
 *
 * What is tested (pure projection over deriveTurnView):
 * - the lead thoughts are their own THINKING zone; the text before the first call is
 *   the restatement; every note between calls closes the phase and stands between two
 *   phases; a thought between calls stays inside its phase (never starts one)
 * - questions / permissions and an unrecovered final failure are first-level needs zones
 * - a Q&A turn (no calls) has no RESULT; a turn with calls marks its final text RESULT
 *   once it has ended
 * - a pipeline turn: the work before the run stays above the `pipelineStart` divider
 *   (a call straddling the run block is split there); only started stages appear; a
 *   stage run twice is one zone with rounds = 2; a gate keeps its earlier verdicts; the
 *   running stage names the next one; a turn that ended mid-stage stops there; a direct
 *   run has no divider and no stage
 * - a turn that ended with an unfinished call ends with a `stopped` zone; history with
 *   no results at all is not judged stopped; a stopped turn has no RESULT, and only the
 *   first text after the last call is RESULT (not when a question still waits after it)
 * - between two stages and after the last finished one, the stage that took the latest
 *   work is live (open, `next:`) while streaming, its mark still done
 * - work before the first stage row never moves: below the divider for a fresh run, in
 *   the stage a resumed run announced from the first frame (ids checked with their parent)
 * - zone ids only grow while streaming: every id in frame k is in frame k+1, in order
 * - stripView: the tool's own name, MCP prefix dropped; the display object when the
 *   block has one, else the summary; setup and wrappers removed, heredoc cut at its
 *   marker, UUIDs / long hashes shortened, paths split into dir (~) + file
 * - mergeStrips: identical input (display key) or adjacent Read / Grep / Glob merge;
 *   an error, a running call or an Agent never merges; an unsettled call stays alone
 */
import { describe, it, expect } from 'vitest';
import { deriveTurnView } from './turnView';
import type { ToolStep } from './turnView';
import { mergeStrips, stripView, turnZones } from './turnZones';
import type { Zone } from './turnZones';
import type { ContentBlock, PipelineStageContent, ToolUseContent } from '../../types';

const tu = (id: string, cmd = `ls ${id}`, name = 'Bash', display?: ToolUseContent['display']): ContentBlock =>
  ({ type: 'tool_use', id, name, summary: name === 'Bash' ? `Running: ${cmd}` : `Reading ${cmd}`, category: 'bash', ...(display ? { display } : {}) }) as ContentBlock;
const rd = (id: string, path: string): ContentBlock =>
  ({ type: 'tool_use', id, name: 'Read', summary: `Reading ${path}`, category: 'read', display: { tool: 'Read', object: path, key: `k-${path}` } }) as ContentBlock;
const agent = (id: string, desc: string): ContentBlock =>
  ({ type: 'tool_use', id, name: 'Agent', summary: `Agent: ${desc}`, category: 'agent' }) as ContentBlock;
const tr = (id: string, isError = false): ContentBlock => ({ type: 'tool_result', toolUseId: id, isError, outcome: 'ok' }) as ContentBlock;
const tx = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;
const th = (thinking: string): ContentBlock => ({ type: 'thinking', thinking }) as ContentBlock;
const RUN = 'run_z';
const runBlock = (profile: string | null = 'full'): ContentBlock => ({
  type: 'pipeline_run', runId: RUN, profile, stages: ['evaluate', 'think', 'plan', 'build', 'deliver'],
  checklist: [], requirement: 'R', current: null, at: '2026-10-19T10:00:00Z',
}) as ContentBlock;
const row = (stage: string, status: PipelineStageContent['status'], over: Partial<PipelineStageContent> = {}): ContentBlock => ({
  type: 'pipeline_stage', runId: RUN, stage, status, ordinal: null, position: null, total: 5, profile: 'full',
  next: null, nextOrdinal: null, gate: /^gate/.test(stage) ? { name: stage, verdict: 'PASS' } : null,
  summary: null, at: '2026-10-19T10:00:00Z', ...over,
}) as ContentBlock;

const zones = (content: ContentBlock[], streaming = false): Zone[] => turnZones(deriveTurnView(content, streaming), streaming);
const kinds = (zs: Zone[]) => zs.map((z) => z.kind);

describe('turnZones — a chat turn', () => {
  it('THOUGHTS → restatement → WORKED → note → WORKED → RESULT', () => {
    const zs = zones([th('look'), tx('I read it as X.'), tu('a'), tr('a'), th('mid'), tu('b'), tr('b'),
      tx('Step one done.'), tu('c'), tr('c'), tx('The answer.')]);
    expect(kinds(zs)).toEqual(['thinking', 'text', 'phase', 'text', 'phase', 'text']);
    const [, restate, p1, note, , answer] = zs;
    expect(restate.kind === 'text' && restate.role).toBe('restatement');
    expect(note.kind === 'text' && note.role).toBe('note');
    expect(answer.kind === 'text' && answer.result).toBe(true);
    // the thought between two calls stays in the phase
    expect(p1.kind === 'phase' && p1.members.map((m) => m.kind)).toEqual(['process', 'thought', 'process']);
  });

  it('a thought right after a note opens the next phase with it (thoughts never stand alone between)', () => {
    const zs = zones([tx('go'), tu('a'), tr('a'), tx('next.'), th('hmm'), tu('b'), tr('b'), tx('done')]);
    expect(kinds(zs)).toEqual(['text', 'phase', 'text', 'phase', 'text']);
    expect(zs[3].kind === 'phase' && zs[3].members.map((m) => m.kind)).toEqual(['thought', 'process']);
  });

  it('a Q&A turn: THOUGHTS then the answer, no RESULT', () => {
    const zs = zones([th('think'), tx('Just the answer.')]);
    expect(kinds(zs)).toEqual(['thinking', 'text']);
    expect(zs[1].kind === 'text' && zs[1].result).toBe(false);
  });

  it('while streaming the final text is not yet RESULT, and the last phase is live', () => {
    const zs = zones([tx('go'), tu('a'), tr('a'), tu('b')], true);
    expect(kinds(zs)).toEqual(['text', 'phase']);
    expect(zs[1].kind === 'phase' && zs[1].live).toBe(true);
    const z2 = zones([tx('go'), tu('a'), tr('a'), tx('answer so')], true);
    expect(z2[2].kind === 'text' && z2[2].result).toBe(false);
  });

  it('no empty WORKING: a note just written while streaming is the last zone', () => {
    const zs = zones([tx('go'), tu('a'), tr('a'), tx('next.')], true);
    expect(kinds(zs)).toEqual(['text', 'phase', 'text']);
  });

  it('an interrupt and an unrecovered final failure are needs zones', () => {
    const ask: ContentBlock = { type: 'ask_user_question', toolUseId: 'q', questions: [] } as unknown as ContentBlock;
    const zs = zones([tx('go'), tu('a'), tr('a'), ask, tu('b'), tr('b', true)]);
    expect(kinds(zs)).toEqual(['text', 'phase', 'needs', 'phase', 'failure']);
  });

  it('a turn that ended with an unfinished call ends with stopped', () => {
    const zs = zones([tx('go'), tu('a'), tr('a'), tu('b')]);
    expect(kinds(zs).at(-1)).toBe('stopped');
    expect(kinds(zones([tx('go'), tu('a'), tr('a'), tx('done')]))).not.toContain('stopped');
  });
});

describe('turnZones — RESULT and stopped', () => {
  it('only the first text after the last call is RESULT', () => {
    const zs = zones([tx('go'), tu('a'), tr('a'), tx('part one'), th('more'), tx('part two')]);
    expect(zs.filter((z) => z.kind === 'text' && z.result).map((z) => z.kind === 'text' && z.text)).toEqual(['part one']);
  });

  it('no RESULT while a question still waits after the text', () => {
    const ask: ContentBlock = { type: 'ask_user_question', toolUseId: 'q', questions: [] } as unknown as ContentBlock;
    const zs = zones([tx('go'), tu('a'), tr('a'), tx('Before I go on:'), ask]);
    expect(zs.some((z) => z.kind === 'text' && z.result)).toBe(false);
    expect(kinds(zs).at(-1)).toBe('needs');
  });

  it('a turn the user stopped has no RESULT; the stop line says whether a call was cut', () => {
    const cut = zones([tx('go'), tu('a'), tr('a'), tu('b'), tx('---\n*Stopped*')]);
    expect(cut.some((z) => z.kind === 'text' && z.result)).toBe(false);
    expect(cut.at(-1)).toMatchObject({ kind: 'stopped', byUser: true, unfinished: true });
    const clean = zones([tx('go'), tu('a'), tr('a'), tx('partial'), tx('---\n*Stopped*')]);
    expect(clean.some((z) => z.kind === 'text' && z.result)).toBe(false);
    expect(clean.at(-1)).toMatchObject({ kind: 'stopped', byUser: true, unfinished: false });
  });

  it('history that kept no results is not judged stopped', () => {
    expect(kinds(zones([tx('go'), tu('a'), tu('b')]))).not.toContain('stopped');
  });

  // the backend says why a call did not finish (core/turn_notice, 2026-10-20)
  const notice = (reason: string, cause?: string): ContentBlock =>
    ({ type: 'turn_notice', reason, ...(cause ? { cause } : {}) }) as ContentBlock;

  it('a call lost in a restart the turn recovered from does not stop the turn', () => {
    const zs = zones([tx('go'), tu('a'), tr('a'), tu('b'), notice('restarted', 'stalled'), tu('c'), tr('c'), tx('done')]);
    expect(kinds(zs)).not.toContain('stopped');
  });

  it('a restart with nothing after it stops the turn with its reason', () => {
    const zs = zones([tx('go'), tu('a'), tr('a'), tu('b'), notice('restarted', 'stalled')]);
    expect(zs.at(-1)).toMatchObject({ kind: 'stopped', byUser: false, notice: { reason: 'restarted', cause: 'stalled' } });
  });

  it('a hand-over to a newer message stops the turn with that reason', () => {
    const zs = zones([tx('go'), tu('a'), tr('a'), tu('b'), notice('superseded')]);
    expect(zs.at(-1)).toMatchObject({ kind: 'stopped', notice: { reason: 'superseded' } });
  });

  it('a call cut after the notice keeps the unexplained stop line', () => {
    const zs = zones([tx('go'), tu('a'), tr('a'), notice('restarted', 'oom'), tu('b'), tr('b'), tu('c')]);
    const last = zs.at(-1) as Extract<Zone, { kind: 'stopped' }>;
    expect(last.kind).toBe('stopped');
    expect(last.notice).toBeUndefined();
  });
});

describe('turnZones — a pipeline turn', () => {
  const turn = (): ContentBlock[] => [
    th('t0'), tx('I read it as X.'), tu('s1', 'cat SKILL.md'), tr('s1'), tu('c1', 'artifact_cli.py run-create'), runBlock(), tr('c1'),
    row('evaluate', 'in_progress'), tu('e1'), tr('e1'), row('evaluate', 'completed'), row('gate0', 'completed'),
    row('think', 'in_progress'), tu('t1'), tr('t1'), row('think', 'completed'),
    row('plan', 'in_progress'), tu('p1'), tr('p1'), row('plan', 'completed'),
    row('gate1', 'completed', { gate: { name: 'GATE 1', verdict: 'BLOCK' } }),
    row('plan', 'in_progress'), tu('p2'), tr('p2'), row('plan', 'completed'),
    row('gate1', 'completed'),
    row('build', 'in_progress'), tu('b1'), tr('b1'),
  ];

  it('pre-run work above the divider, then only the started stages, rounds merged', () => {
    const zs = zones(turn(), true);
    expect(kinds(zs)).toEqual(['thinking', 'text', 'phase', 'pipelineStart', 'stage', 'gate', 'stage', 'stage', 'gate', 'stage']);
    const pre = zs[2];
    // the run-create call (the run block arrives during it) is still pre-run work
    expect(pre.kind === 'phase' && pre.members.flatMap((m) => (m.kind === 'process' ? m.steps.map((s) => s.block.id) : []))).toEqual(['s1', 'c1']);
    expect(zs[3].kind === 'pipelineStart' && zs[3].profile).toBe('full');
    const stages = zs.filter((z): z is Extract<Zone, { kind: 'stage' }> => z.kind === 'stage');
    expect(stages.map((s) => [s.stage, s.rounds, s.status])).toEqual([
      ['evaluate', 1, 'done'], ['think', 1, 'done'], ['plan', 2, 'done'], ['build', 1, 'running']]);
    expect(stages[3].live).toBe(true);
    expect(stages[3].next).toBe('deliver');
    const gate1 = zs.filter((z): z is Extract<Zone, { kind: 'gate' }> => z.kind === 'gate')[1];
    expect([gate1.verdict, gate1.earlier]).toEqual(['PASS', ['BLOCK']]);
  });

  it('a turn that ended mid-stage stops there', () => {
    const zs = zones(turn(), false);
    const last = zs.filter((z) => z.kind === 'stage').at(-1);
    expect(last?.kind === 'stage' && last.stoppedHere).toBe(true);
  });

  it('work after a finished stage keeps that stage live and open while streaming', () => {
    const zs = zones([runBlock(), row('evaluate', 'in_progress'), tu('e1'), tr('e1'),
      row('evaluate', 'completed', { next: 'plan' }), tu('p1')], true);
    const ev = zs.find((z): z is Extract<Zone, { kind: 'stage' }> => z.kind === 'stage');
    expect(ev).toMatchObject({ stage: 'evaluate', status: 'done', live: true, next: 'plan' });
    expect(ev?.body.at(-1)).toMatchObject({ kind: 'phase', live: true });
  });

  it('the answer after DELIVER streams in the open stage; settled it is the RESULT', () => {
    // the plan goes on to REFLECT, so after DELIVER the plan is not through yet
    const withReflect = { ...(runBlock() as object), stages: ['build', 'deliver', 'reflect'] } as ContentBlock;
    const blocks = [withReflect, row('deliver', 'in_progress'), tu('d1'), tr('d1'),
      row('deliver', 'completed', { next: 'reflect' }), tx('Delivered.')];
    const live = zones(blocks, true);
    const dv = live.find((z): z is Extract<Zone, { kind: 'stage' }> => z.kind === 'stage');
    expect(dv?.live).toBe(true);
    expect(dv?.body.some((z) => z.kind === 'text')).toBe(true);
    const done = zones(blocks, false);
    expect(done.at(-1)).toMatchObject({ kind: 'text', result: true });
  });

  it('a resumed run: the work before the first row is the announced stage’s from the first frame', () => {
    const resumed = { ...(runBlock() as object), current: 'build' } as ContentBlock;
    const blocks = [tu('r', 'artifact_cli.py run-get'), resumed, tr('r'), tu('b1'), tr('b1'), row('build', 'completed'),
      row('deliver', 'in_progress'), tu('d1')];
    for (let n = 2; n <= blocks.length; n += 1) {
      const zs = zones(blocks.slice(0, n), true);
      expect(kinds(zs).slice(0, 3), `frame ${n}`).toEqual(['phase', 'pipelineStart', 'stage']);
    }
  });

  it('a direct run is a chat turn: no divider, no stage', () => {
    const direct = [tx('go'), tu('a'), runBlock('direct'), tr('a'), row('build', 'in_progress', { profile: 'direct' }), tu('b'), tr('b'), tx('done')];
    const zs = zones(direct.map((b) => (b.type === 'pipeline_stage' ? { ...b, profile: 'direct' } : b)) as ContentBlock[]);
    expect(kinds(zs)).toEqual(['text', 'phase', 'text']);
  });
});

describe('turnZones — streaming only grows', () => {
  it('every zone id of frame k is still there, in order, in frame k+1', () => {
    const plain: ContentBlock[] = [th('a'), tx('restate'), tu('x'), tr('x'), th('b'), tu('y'), tr('y'), tx('note'), tu('z'), tr('z'), tx('answer')];
    const pipe: ContentBlock[] = [th('t0'), tx('restate'), tu('s1'), runBlock(), tr('s1'), row('evaluate', 'in_progress'), tu('e1'), tr('e1'),
      row('evaluate', 'completed'), row('gate0', 'completed'), row('think', 'in_progress'), tx('mid note'), tu('t1'), tr('t1'), row('think', 'completed'), tx('answer')];
    for (const blocks of [plain, pipe]) {
      let prev: string[] = [];
      for (let n = 1; n <= blocks.length; n += 1) {
        const ids = zones(blocks.slice(0, n), true).map((z) => z.id);
        const kept = ids.filter((id) => prev.includes(id));
        expect(kept, `frame ${n}`).toEqual(prev);
        prev = ids;
      }
    }
  });

  it('nothing moves to another parent: work before the first row of a run created without a profile', () => {
    const bare: ContentBlock = { ...(runBlock(null) as object), stages: [] } as ContentBlock;
    const blocks: ContentBlock[] = [tx('restate'), tu('rc', 'artifact_cli.py run-create'), bare, tr('rc'), tu('e1'), tr('e1'), th('x'),
      tu('e2'), tr('e2'), runBlock(), row('evaluate', 'in_progress'), tu('e3'), tr('e3'), row('evaluate', 'completed', { next: 'plan' }),
      tu('p0'), tr('p0'), row('plan', 'in_progress'), tu('p1')];
    const paths = (zs: Zone[], parent = ''): string[] => zs.flatMap((z) => [`${parent}${z.id}`,
      ...(z.kind === 'stage' ? paths(z.body, `${z.id}/`) : [])]);
    let prev: string[] = [];
    for (let n = 1; n <= blocks.length; n += 1) {
      const ids = paths(zones(blocks.slice(0, n), true));
      expect(ids.filter((id) => prev.includes(id)), `frame ${n}`).toEqual(prev);
      prev = ids;
    }
  });

  it('text zones keep their id when an answer-so-far turns into a note', () => {
    const a = zones([tx('go'), tu('a'), tr('a'), tx('so far')], true).map((z) => z.id);
    const b = zones([tx('go'), tu('a'), tr('a'), tx('so far'), tu('b')], true).map((z) => z.id);
    expect(b.slice(0, a.length)).toEqual(a);
  });
});

const step = (block: ContentBlock, status: ToolStep['status'] = 'done', index = 0): ToolStep =>
  ({ kind: 'tool', block: block as ToolUseContent, index, status });

describe('stripView', () => {
  it('Bash: setup and wrappers removed, heredoc cut, uuid shortened', () => {
    expect(stripView(tu('a', 'cd /x/y && perl -e \'alarm 90; exec @ARGV\' make test').valueOf() as ToolUseContent).object).toBe('make test');
    expect(stripView(tu('a', "cat > a.py <<'EOF'\nprint(1)\nEOF") as ToolUseContent).object).toBe("cat > a.py <<'EOF' …");
    expect(stripView(tu('a', 'curl /s/3f2a9c1e-1b2c-4d5e-8f90-a1b2c3d4e5f6/x') as ToolUseContent).object).toBe('curl /s/3f2a9c1e/x');
    expect(stripView(tu('a', 'git show 8fc6b1b0f38067ccdf7c15e5ce1afc784681a239') as ToolUseContent).object).toBe('git show 8fc6b1b0');
    expect(stripView(tu('a', 'sed -n 1,9p f.ts') as ToolUseContent).tool).toBe('Bash');
  });

  it('Bash: env prefix, absolute interpreter paths and stdin programs are tidied', () => {
    const o = (cmd: string) => stripView(tu('a', cmd) as ToolUseContent).object;
    const venv = '/Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/.venv/bin';
    expect(o(`env PATH=${venv}:/usr/bin:/bin python -m pytest tests/test_x.py -q`)).toBe('python -m pytest tests/test_x.py -q');
    expect(o(`env PATH=${venv}:/usr/bin:/bin perl -e 'alarm 90; exec @ARGV' ${venv}/python -m pytest t.py`)).toBe('python -m pytest t.py');
    expect(o('FOO=1 BAR="a b" make test')).toBe('make test');
    expect(o("python3 -I - <<'EOF'\nimport json, re\nfrom x import y\n\nd = json.load(open('run.json'))\nEOF")).toBe("python3 -I - <<EOF · d = json.load(open('run.json'))");
    expect(o('cat /Users/gawan/.swarm-ai/x.json')).toBe('cat ~/.swarm-ai/x.json');
    // code in front stays: `f=$(…)` is not a plain assignment
    expect(o('f=$(ls -t | head -1); echo "$f"')).toBe('f=$(ls -t | head -1); echo "$f"');
    // a file write keeps its target
    expect(o("cat > /tmp/fix1234/e9.py <<'EOF'\nprint(1)\nEOF")).toBe("cat > /tmp/fix1234/e9.py <<'EOF' …");
  });

  it('Bash: leading assignments inlined, later wrappers removed, python -c by its first statement', () => {
    const o = (cmd: string) => stripView(tu('a', cmd) as ToolUseContent).object;
    // the commands from the 2026-10-21 screenshot
    expect(o('B=~/Desktop/SwarmAI-Workspace/swarmai/backend; $B/.venv/bin/python $B/scripts/artifact_cli.py stage-start --project SwarmAI'))
      .toBe('python ~/Desktop/SwarmAI-Workspace/swarmai/backend/scripts/artifact_cli.py stage-start --project SwarmAI');
    expect(o("git add backend/core/r.py; perl -e 'alarm 110; exec @ARGV' python ~/x/pipeline_test.py --mode tdd"))
      .toBe('git add backend/core/r.py; python ~/x/pipeline_test.py --mode tdd');
    expect(o(`python3 -I -c " import json;d=json.load(open('/tmp/run_333ffbf8/build-start2.json')) print(sorted(d.keys()))"`))
      .toBe("python3 -I -c · d=json.load(open('/tmp/run_333ffbf8/build-start2.json')) print(sorted(d.keys()))");
    // cut by the 200-char summary: no closing quote
    expect(o(`R=~/runs/run_1; python3 -I -c " import json;d=json.load(open('$R/run.j`))
      .toBe("python3 -I -c · d=json.load(open('~/runs/run_1/run.j");
    // an assignment with code in it is part of what ran
    expect(o('f=$(ls -t | head -1); echo "$f"')).toBe('f=$(ls -t | head -1); echo "$f"');
    // an assignment with nothing after it stays
    expect(o('X=1;')).toBe('X=1;');
  });

  it('prefers the display object (full text on hover) over the 200-char summary', () => {
    const long = `echo ${'x'.repeat(400)}`;
    const v = stripView(tu('a', long.slice(0, 190), 'Bash', { tool: 'Bash', object: long, key: 'k' }) as ToolUseContent);
    expect(v.full).toBe(long);
    expect(v.mergeKey).toBe('k');
  });

  it('paths: home as ~, dir + file', () => {
    const v = stripView(rd('r', '/Users/me/proj/src/a/TabView.tsx') as ToolUseContent);
    expect([v.tool, v.dir, v.file]).toEqual(['Read', '~/proj/src/a/', 'TabView.tsx']);
  });

  it('MCP prefix dropped', () => {
    const b = { type: 'tool_use', id: 'm', name: 'mcp__swarm_ui__ui_action', summary: 'MCP: ui_action — open', category: 'x' } as ToolUseContent;
    expect(stripView(b).tool).toBe('ui_action');
  });
});

describe('mergeStrips', () => {
  const settled = () => true;
  it('identical input merges; different input does not', () => {
    const same = { tool: 'Bash', object: 'make test', key: 'k1' };
    const items = mergeStrips([step(tu('a', 'make test', 'Bash', same)), step(tu('b', 'make test', 'Bash', same)), step(tu('c', 'make lint'))], settled);
    expect(items.map((i) => i.steps.length)).toEqual([2, 1]);
  });

  it('adjacent reads merge with unique objects', () => {
    const items = mergeStrips([step(rd('a', '/p/a.ts')), step(rd('b', '/p/b.ts')), step(rd('c', '/p/a.ts'))], settled);
    expect(items).toHaveLength(1);
    expect(items[0].objects).toEqual(['a.ts', 'b.ts']);
  });

  it('errors, running calls and agents never merge; an unsettled call stays alone', () => {
    expect(mergeStrips([step(rd('a', '/p/a.ts'), 'error'), step(rd('b', '/p/b.ts'))], settled)).toHaveLength(2);
    expect(mergeStrips([step(rd('a', '/p/a.ts')), step(rd('b', '/p/b.ts'), 'pending')], settled)).toHaveLength(2);
    expect(mergeStrips([step(agent('a', 'x')), step(agent('b', 'x'))], settled)).toHaveLength(2);
    expect(mergeStrips([step(rd('a', '/p/a.ts')), step(rd('b', '/p/b.ts'))], (id) => id !== 'b')).toHaveLength(2);
  });
});
