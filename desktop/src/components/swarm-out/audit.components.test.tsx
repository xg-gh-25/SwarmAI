/**
 * The run audit's components (Autonomy Judge tag, trace tags), kept for when a run's
 * result and audit come back from its Hive; the live panel does not render them yet.
 *
 * Questions: does the tag show the worst verdict (any BLOCK → BLOCK, else the WARN
 * count, else ✓; N/A and PENDING not counted) rather than an average; does a click
 * open the five-dimension matrix and Esc close it; does a trace part that is missing
 * say so (无 Brain, Report 生成中) instead of vanishing.
 */
import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest';
import type { ReactNode } from 'react';
import { useEscape } from '../../hooks/useLayer';
import { fireEvent, render, screen } from '@testing-library/react';
import { JudgeTag } from './JudgeTag';
import { judgeState, judgeSummary } from './judge';
import type { RunJudge } from './shapes';
import i18n from '../../i18n';

// These cases check the Chinese copy (zh.json); swarmOutI18n.test.tsx checks English.
beforeAll(async () => { await i18n.changeLanguage('zh'); });
afterAll(async () => { await i18n.changeLanguage('en'); });

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
  it('Escape closes only the popover (not the layer it sits on) and returns focus to the tag', () => {
    const below = vi.fn();
    function Below({ children }: { children: ReactNode }) {
      useEscape(true, below);   // the overlay or the chat command list under the dashboard
      return <>{children}</>;
    }
    render(<Below><JudgeTag judge={judge(['PASS', 'PASS', 'PASS', 'PASS', 'PASS'])} title="Run Y" /></Below>);
    fireEvent.click(screen.getByTestId('judge-tag'));
    const pop = screen.getByTestId('judge-popover');
    expect(document.activeElement).toBe(pop);
    fireEvent.keyDown(pop, { key: 'Escape' });
    expect(screen.queryByTestId('judge-popover')).toBeNull();
    expect(below).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(screen.getByTestId('judge-tag'));
  });

  it('scrolling closes the popover', () => {
    render(<JudgeTag judge={judge(['PASS', 'PASS', 'PASS', 'PASS', 'PASS'])} title="Run Z" />);
    fireEvent.click(screen.getByTestId('judge-tag'));
    fireEvent.scroll(window);
    expect(screen.queryByTestId('judge-popover')).toBeNull();
  });
});


