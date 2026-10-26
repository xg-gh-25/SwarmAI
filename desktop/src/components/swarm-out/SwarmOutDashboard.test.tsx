/**
 * Swarm Out dashboard + Autonomy Judge tag.
 *
 * Questions: does the tag show the worst verdict (any BLOCK → BLOCK, else the WARN
 * count, else ✓; N/A and PENDING not counted) rather than an average; does a click
 * open the five-dimension matrix and Esc close it; does the dashboard say its data is
 * a sample and switch between its two views.
 */
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { JudgeTag } from './JudgeTag';
import { judgeState, judgeSummary } from './judge';
import { SwarmOutDashboard } from './SwarmOutDashboard';
import type { RunJudge } from './mockData';

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
