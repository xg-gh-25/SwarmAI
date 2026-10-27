/**
 * Swarm Out dashboard + Autonomy Judge tag.
 *
 * Questions: does the tag show the worst verdict (any BLOCK → BLOCK, else the WARN
 * count, else ✓; N/A and PENDING not counted) rather than an average; does a click
 * open the five-dimension matrix and Esc close it; does the dashboard say its data is
 * a sample and switch between its two views; does every prepared run carry its trace
 * (Brain → Agent → Context → Hive → Report + time and tokens), with a missing part
 * saying so (无 Brain, Report 生成中) instead of vanishing; with no running Hive,
 * does the run view show the setup guide in place of the live section, opening the Hive
 * surface from its button, and does the header count only real Hives.
 */
import { describe, it, expect, vi } from 'vitest';
import { swarmOutService } from '../../services/swarmOut';
import { fireEvent, render, screen } from '@testing-library/react';
import { JudgeTag } from './JudgeTag';
import { judgeState, judgeSummary } from './judge';
import { SwarmOutDashboard } from './SwarmOutDashboard';
import { TraceTags } from './TraceTags';
import { BY_TYPE, HISTORY_RUNS, type HistoryRun, type RunJudge, type RunTrace } from './mockData';
import { groupHistory, NO_AGENT, NO_BRAIN } from './history';

// The dashboard's live section polls /api/swarm-out/runs; these tests cover the sample
// parts, so the service answers with nothing (LiveDispatches.test.tsx covers the live part).
vi.mock('../../services/swarmOut', () => ({
  swarmOutService: {
    runs: vi.fn(async () => ({ runs: [], asks: [], errors: [] })),
    answer: vi.fn(),
    hives: vi.fn(async () => ({ hives: [{ name: 'w1', status: 'running', region: 'us-east-1' }], running: 1 })),
  },
}));

const judge = (verdicts: RunJudge['verdicts']): RunJudge => ({
  outcome: 'COMPLETED · NOT_LANDED · NOT PUSH READY',
  verdicts,
  numbers: ['iso', 'q', 'a', 's', 't'],
});

describe('judgeState — the worst verdict, never an average', () => {
  it('all PASS → PASS', () => {
    expect(judgeState(['PASS', 'PASS', 'PASS', 'PASS', 'PASS'])).toEqual({ state: 'PASS', warns: 0 });
  });
  it('N/A and PENDING do not count against the run', () => {
    expect(judgeState(['PASS', 'NA', 'PASS', 'PASS', 'PENDING'])).toEqual({ state: 'PASS', warns: 0 });
  });
  it('WARNs are counted', () => {
    expect(judgeState(['PASS', 'WARN', 'PASS', 'PASS', 'WARN'])).toEqual({ state: 'WARN', warns: 2 });
  });
  it('one BLOCK makes the run BLOCK, whatever else passes', () => {
    expect(judgeState(['PASS', 'BLOCK', 'PASS', 'PASS', 'PASS']).state).toBe('BLOCK');
  });
});

describe('judgeSummary', () => {
  it('names only the dimensions that did not pass, with their numbers', () => {
    const s = judgeSummary(judge(['PASS', 'WARN', 'PASS', 'PASS', 'PASS']));
    expect(s).toContain('Quality WARN：q');
    expect(s).not.toContain('Run Isolation');
  });

  it('never says Speed / Token are within their line when they are N/A', () => {
    const s = judgeSummary(judge(['PASS', 'PASS', 'PASS', 'NA', 'NA']));
    expect(s).not.toContain('警戒线内');
    expect(s).toContain('Speed、Token Cost 暂无参考线');
    const w = judgeSummary(judge(['PASS', 'WARN', 'PASS', 'NA', 'PASS']));
    expect(w).toContain('Speed 暂无参考线');
  });
});

describe('JudgeTag', () => {
  it('a click opens the five-dimension matrix; Esc closes it', () => {
    render(<JudgeTag judge={judge(['PASS', 'WARN', 'PASS', 'PASS', 'WARN'])} title="Run X" />);
    expect(screen.getByTestId('judge-tag').textContent).toContain('! 2 WARN');
    fireEvent.click(screen.getByTestId('judge-tag'));
    const pop = screen.getByTestId('judge-popover');
    for (const d of ['Run Isolation', 'Quality', 'Autonomy', 'Speed', 'Token Cost']) expect(pop.textContent).toContain(d);
    expect(pop.textContent).toContain('COMPLETED · NOT_LANDED · NOT PUSH READY');
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByTestId('judge-popover')).toBeNull();
  });
});

describe('JudgeTag keyboard', () => {
  it('Escape closes only the popover (does not reach a global handler) and returns focus to the tag', () => {
    const globalEsc = vi.fn();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') globalEsc(); };
    window.addEventListener('keydown', onKey);
    try {
      render(<JudgeTag judge={judge(['PASS', 'PASS', 'PASS', 'PASS', 'PASS'])} title="Run Y" />);
      fireEvent.click(screen.getByTestId('judge-tag'));
      const pop = screen.getByTestId('judge-popover');
      expect(document.activeElement).toBe(pop);
      fireEvent.keyDown(pop, { key: 'Escape' });
      expect(screen.queryByTestId('judge-popover')).toBeNull();
      expect(globalEsc).not.toHaveBeenCalled();
      expect(document.activeElement).toBe(screen.getByTestId('judge-tag'));
    } finally {
      window.removeEventListener('keydown', onKey);
    }
  });

  it('scrolling closes the popover', () => {
    render(<JudgeTag judge={judge(['PASS', 'PASS', 'PASS', 'PASS', 'PASS'])} title="Run Z" />);
    fireEvent.click(screen.getByTestId('judge-tag'));
    fireEvent.scroll(window);
    expect(screen.queryByTestId('judge-popover')).toBeNull();
  });
});

describe('SwarmOutDashboard', () => {
  it('says its data is a sample, shows the run view first, switches to history', () => {
    render(<SwarmOutDashboard />);
    expect(screen.getByTestId('swarm-out-dashboard').textContent).toContain('示例数据');
    expect(screen.getByTestId('swarm-out-overview')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '历史' }));
    expect(screen.getByTestId('swarm-out-history')).toBeTruthy();
    expect(screen.queryByTestId('swarm-out-overview')).toBeNull();
  });
});

describe('SwarmOutDashboard and the Hives it can dispatch to', () => {
  const hivesAre = (hives: { name: string; status: string; region: string }[]) => {
    vi.mocked(swarmOutService.hives).mockResolvedValue(
      { hives, running: hives.filter((h) => h.status === 'running').length });
  };

  it('with no Hive: the guide replaces the live section and its button opens Hive', async () => {
    hivesAre([]);
    const onOpenHive = vi.fn();
    render(<SwarmOutDashboard onOpenHive={onOpenHive} />);
    const guide = await screen.findByTestId('swarm-out-hive-guide');
    expect(guide.getAttribute('data-case')).toBe('no-hive');
    expect(guide.textContent).toContain('Add Account');
    expect(guide.textContent).toContain('Deploy Hive');
    expect(screen.queryByTestId('swarm-out-live')).toBeNull();
    expect(screen.getByTestId('swarm-out-hive-count').textContent).toBe('还没有 Hive');
    fireEvent.click(screen.getByTestId('swarm-out-open-hive'));
    expect(onOpenHive).toHaveBeenCalledTimes(1);
  });

  it('with Hives but none running: names them and says to start one', async () => {
    hivesAre([{ name: 'w1', status: 'stopped', region: 'us-east-1' }]);
    render(<SwarmOutDashboard />);
    const guide = await screen.findByTestId('swarm-out-hive-guide');
    expect(guide.getAttribute('data-case')).toBe('none-running');
    expect(guide.textContent).toContain('w1（stopped）');
    expect(screen.getByTestId('swarm-out-hive-count').textContent).toBe('1 台 Hive，都没在运行');
  });

  it('with a running Hive: the live section, no guide, the real count', async () => {
    hivesAre([{ name: 'w1', status: 'running', region: 'us-east-1' }, { name: 'w2', status: 'stopped', region: 'us-east-1' }]);
    render(<SwarmOutDashboard />);
    expect((await screen.findByTestId('swarm-out-hive-count')).textContent).toContain('1 台 Hive 在运行');
    expect(screen.getByTestId('swarm-out-live')).toBeTruthy();
    expect(screen.queryByTestId('swarm-out-hive-guide')).toBeNull();
  });

  it('before the Hives are known, shows no guide and no count (an unreachable daemon is not "no Hive")', () => {
    vi.mocked(swarmOutService.hives).mockReturnValue(new Promise(() => {}));
    render(<SwarmOutDashboard />);
    expect(screen.queryByTestId('swarm-out-hive-guide')).toBeNull();
    expect(screen.queryByTestId('swarm-out-hive-count')).toBeNull();
    expect(screen.getByTestId('swarm-out-live')).toBeTruthy();
  });

  it('a failed Hive lookup is not "no Hive": no guide', async () => {
    vi.mocked(swarmOutService.hives).mockRejectedValue(new Error('daemon down'));
    render(<SwarmOutDashboard />);
    await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByTestId('swarm-out-hive-guide')).toBeNull();
    expect(screen.getByTestId('swarm-out-live')).toBeTruthy();
  });

  it('marks the sample data as not launched yet, in both views', () => {
    render(<SwarmOutDashboard />);
    expect(screen.getByTestId('swarm-out-sample-notice').textContent).toContain('还没正式上线，敬请期待');
    fireEvent.click(screen.getByRole('button', { name: '历史' }));
    expect(screen.getByTestId('swarm-out-sample-notice').textContent).toContain('敬请期待');
  });
});

const trace = (over: Partial<RunTrace> = {}): RunTrace => ({
  brain: { name: 'SwarmAI', version: 'v3', docs: ['TECH.md'] },
  agent: { name: 'swarmai-agent', status: 'reviewed', profile: 'bugfix', rule: '有复现的缺陷', validations: ['pytest'], placement: 'hive', landing: '自动落到 main' },
  context: { goal: '修 X', acceptance: '测试绿', slices: [{ source: 'TECH.md § Recall', score: 0.82 }], memory: 2, tokens: '6.1k', packedAt: '22:19' },
  hive: { instance: 'explorer', slot: 1, session: 'ses_1', run: 'run_1' },
  startedAt: '22:20', endedAt: '22:58', duration: '38 分', tokens: '519k',
  report: { outcome: 'COMPLETED · LANDED', deliverables: ['commit abc'], turns: 14, flowedBack: ['IMPROVEMENT.md 1 条'], path: 'records/ses_1/report.md' },
  ...over,
});

describe('TraceTags', () => {
  it('each prepared part opens its detail', () => {
    render(<TraceTags trace={trace()} />);
    fireEvent.click(screen.getByTestId('trace-context'));
    const ctx = screen.getByTestId('trace-context-popover');
    expect(ctx.textContent).toContain('TECH.md § Recall');
    expect(ctx.textContent).toContain('0.82');
    fireEvent.click(screen.getByTestId('trace-agent'));
    expect(screen.getByTestId('trace-agent-popover').textContent).toContain('有复现的缺陷');
    fireEvent.click(screen.getByTestId('trace-report'));
    expect(screen.getByTestId('trace-report-popover').textContent).toContain('IMPROVEMENT.md 1 条');
  });

  it('a task with no brain says 无 Brain and has nothing to open', () => {
    render(<TraceTags trace={trace({ brain: undefined, agent: undefined })} />);
    const tag = screen.getByTestId('trace-brain');
    expect(tag.textContent).toContain('无 Brain');
    expect(tag.tagName).not.toBe('BUTTON');
    expect(screen.getByTestId('trace-agent').textContent).toContain('通用 Agent');
  });

  it('a running run shows Report 生成中, not an openable report', () => {
    render(<TraceTags trace={trace({ report: undefined, endedAt: undefined })} />);
    const tag = screen.getByTestId('trace-report');
    expect(tag.textContent).toContain('Report 生成中');
    expect(tag.tagName).not.toBe('BUTTON');
  });
});

describe('SwarmOutDashboard run rows', () => {
  it('every run that was sent out has a trace line; scheduled runs do not', () => {
    render(<SwarmOutDashboard />);
    const rows = screen.getAllByTestId('run-row');
    const traces = screen.getAllByTestId('run-trace-row');
    // 2 waiting + 2 running + 4 ended were prepared; 2 scheduled were not
    expect(rows).toHaveLength(10);
    expect(traces).toHaveLength(8);
    const metas = screen.getAllByTestId('trace-meta').map((m) => m.textContent ?? '');
    expect(metas).toContain('22:20 → 22:58 · 38 分 · 519k tokens');
    expect(metas).toContain('23:41 开始 · 已跑 12 分 · 还要 10–20 分 · 141k tokens');
  });
});

describe('SwarmOutDashboard on a narrow panel', () => {
  function withPanelWidth(width: number, fn: () => void) {
    const orig = globalThis.ResizeObserver;
    class FakeRO {
      constructor(private cb: ResizeObserverCallback) {}
      observe() { this.cb([{ contentRect: { width } } as ResizeObserverEntry], this as unknown as ResizeObserver); }
      unobserve() {}
      disconnect() {}
    }
    globalThis.ResizeObserver = FakeRO as unknown as typeof ResizeObserver;
    try { fn(); } finally { globalThis.ResizeObserver = orig; }
  }

  it('wide: run cards in an auto-fill grid, four KPI tiles in a row', () => {
    withPanelWidth(1100, () => {
      render(<SwarmOutDashboard />);
      expect(screen.getByTestId('swarm-out-dashboard').dataset.compact).toBeUndefined();
      expect(screen.getByTestId('kpi-grid').style.gridTemplateColumns).toBe('repeat(4, minmax(0, 1fr))');
      // runs are cards in a grid that fits as many columns as the width allows
      const grid = screen.getAllByTestId('run-grid')[0];
      // (jsdom drops min() from inline styles, so the card minimum is read from the grid's attribute)
      expect(grid.dataset.cardMin).toBe('300');
      expect(grid.querySelector('table')).toBeNull();
      expect(grid.querySelectorAll('[data-testid="run-row"]').length).toBeGreaterThan(0);
    });
  });

  it('narrow: the same run cards and traces, KPI tiles 2×2', () => {
    withPanelWidth(500, () => {
      render(<SwarmOutDashboard />);
      expect(screen.getByTestId('swarm-out-dashboard').dataset.compact).toBe('true');
      expect(screen.getByTestId('kpi-grid').style.gridTemplateColumns).toBe('repeat(2, minmax(0, 1fr))');
      const rows = screen.getAllByTestId('run-row');
      expect(rows).toHaveLength(10);
      expect(screen.getAllByTestId('run-trace-row')).toHaveLength(8);
      // the Autonomy Judge tag is still on every judged run
      expect(screen.getAllByTestId('judge-tag').length).toBeGreaterThan(0);
    });
  });
});

describe('Run history by brain / Project Agent', () => {
  // Questions: is every run in exactly one group, the brains that drove the most first and
  // the runs with no brain (no agent) in their own group, last; does the agent view name
  // each run's brain; does a group cap at 5 rows and open the rest; does the sample agree
  // with 按类型 (one sample, not two that drift apart).
  const groupsOf = () => screen.getAllByTestId('history-group');
  const openHistory = () => {
    render(<SwarmOutDashboard />);
    fireEvent.click(screen.getByRole('button', { name: '历史' }));
  };

  it('pure grouping: unassigned last whatever its size, then by run count', () => {
    const run = (id: string, brain?: string): HistoryRun => ({ id, when: '', title: id, type: '定时', brain, outcome: 'done', result: '', duration: '', tokensK: 1 });
    const g = groupHistory([run('a'), run('b'), run('c'), run('d', 'X'), run('e', 'Y'), run('f', 'Y')], 'brain');
    expect(g.map((x) => x.name)).toEqual(['Y', 'X', NO_BRAIN]);
    expect(g[2].unassigned).toBe(true);
    expect(groupHistory([run('a', 'X')], 'agent').map((x) => x.name)).toEqual([NO_AGENT]);
  });

  it('by brain: every run once, the no-brain group last', () => {
    openHistory();
    const groups = groupsOf();
    expect(groups.at(-1)?.dataset.group).toBe(NO_BRAIN);
    expect(groups.filter((x) => x.dataset.unassigned)).toHaveLength(1);
    const counts = groups.map((x) => Number(x.querySelector('[data-testid="history-group-count"]')?.textContent?.split(' ')[0]));
    expect(counts.reduce((a, b) => a + b, 0)).toBe(HISTORY_RUNS.length);
    expect(groups[0].dataset.group).toBe('SwarmAI');
  });

  it('by Project Agent: the no-agent group last; the brain named once per agent, per run without one', () => {
    openHistory();
    fireEvent.click(screen.getByRole('button', { name: '按 Project Agent' }));
    const groups = groupsOf();
    expect(groups.at(-1)?.dataset.group).toBe(NO_AGENT);
    // an agent's brain is named once in its heading, not on every run
    expect(groups[0].querySelector('h4')?.parentElement?.textContent).toContain('大脑 SwarmAI');
    expect(groups[0].querySelector('[data-testid="history-run"]')?.textContent).not.toContain('大脑');
    // runs with no agent come from different brains (or none): each run names its own
    expect(groups.at(-1)?.textContent).toContain('大脑 SwarmAI');
  });

  it('a group shows 5 runs and opens the rest', () => {
    openHistory();
    const big = groupsOf()[0];
    const total = HISTORY_RUNS.filter((r) => r.brain === 'SwarmAI').length;
    expect(total).toBeGreaterThan(5);
    expect(big.querySelectorAll('[data-testid="history-run"]')).toHaveLength(5);
    fireEvent.click(screen.getByRole('button', { name: `再显示 ${total - 5} 个` }));
    expect(groupsOf()[0].querySelectorAll('[data-testid="history-run"]')).toHaveLength(total);
  });

  it('the sample runs add up to 按类型', () => {
    for (const t of BY_TYPE) expect(HISTORY_RUNS.filter((r) => r.type === t.key)).toHaveLength(t.runs);
    for (const t of BY_TYPE.filter((x) => x.qualified !== 'N/A')) {
      const rs = HISTORY_RUNS.filter((r) => r.type === t.key);
      expect(`${rs.filter((r) => r.outcome === 'landed').length} / ${rs.length}`).toBe(t.qualified);
    }
  });
});
