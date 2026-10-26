/**
 * Swarm Out dashboard + Autonomy Judge tag.
 *
 * Questions: does the tag show the worst verdict (any BLOCK → BLOCK, else the WARN
 * count, else ✓; N/A and PENDING not counted) rather than an average; does a click
 * open the five-dimension matrix and Esc close it; does the dashboard say its data is
 * a sample and switch between its two views; does every prepared run carry its trace
 * (Brain → Agent → Context → Hive → Report + time and tokens), with a missing part
 * saying so (无 Brain, Report 生成中) instead of vanishing.
 */
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { JudgeTag } from './JudgeTag';
import { judgeState, judgeSummary } from './judge';
import { SwarmOutDashboard } from './SwarmOutDashboard';
import { TraceTags } from './TraceTags';
import type { RunJudge, RunTrace } from './mockData';

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

  it('wide: the table layout, four KPI tiles in a row', () => {
    withPanelWidth(1100, () => {
      render(<SwarmOutDashboard />);
      expect(screen.getByTestId('swarm-out-dashboard').dataset.compact).toBeUndefined();
      expect(screen.getByTestId('kpi-grid').style.gridTemplateColumns).toBe('repeat(4, minmax(0, 1fr))');
      expect(screen.getAllByTestId('run-row')[0].tagName).toBe('TR');
    });
  });

  it('narrow: stacked run rows with the same runs and traces, KPI tiles 2×2', () => {
    withPanelWidth(500, () => {
      render(<SwarmOutDashboard />);
      expect(screen.getByTestId('swarm-out-dashboard').dataset.compact).toBe('true');
      expect(screen.getByTestId('kpi-grid').style.gridTemplateColumns).toBe('repeat(2, minmax(0, 1fr))');
      const rows = screen.getAllByTestId('run-row');
      expect(rows[0].tagName).toBe('DIV');
      expect(rows).toHaveLength(10);
      expect(screen.getAllByTestId('run-trace-row')).toHaveLength(8);
      // the Autonomy Judge tag is still on every judged run
      expect(screen.getAllByTestId('judge-tag').length).toBeGreaterThan(0);
    });
  });
});
