/**
 * The dashboard shows a run's release verdict (backend core/eval_release) where it
 * used to show a percentage, says "legacy" for a run recorded before the release
 * decision existed, and no dashboard source reads the removed weighted score.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ReleaseBadge, EvalLines, type EvalLinesData } from '../EvalDashboard';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ i18n: { language: 'en' } }) }));

describe('ReleaseBadge', () => {
  it.each([
    ['release', 'Release'],
    ['blocked', 'Blocked'],
    ['incomplete', 'Incomplete'],
  ] as const)('shows %s as a verdict, never a percentage', (verdict, label) => {
    render(<ReleaseBadge verdict={verdict} reason="why" />);
    const badge = screen.getByTestId('release-badge');
    expect(badge.textContent).toBe(label);
    expect(badge.textContent).not.toMatch(/%/);
    expect(badge.getAttribute('title')).toBe('why');
  });

  it('reads an unknown verdict as legacy instead of throwing', () => {
    render(<ReleaseBadge verdict={'weird' as never} />);
    expect(screen.getByTestId('release-badge').textContent).toBe('Legacy');
  });

  it.each(['toString', 'constructor', '__proto__'])('reads the object property name %s as legacy', (v) => {
    render(<ReleaseBadge verdict={v as never} />);
    expect(screen.getByTestId('release-badge').textContent).toBe('Legacy');
  });

  it('shows a run without a release decision as legacy', () => {
    render(<ReleaseBadge verdict={null} />);
    expect(screen.getByTestId('release-badge').textContent).toBe('Legacy');
  });
});

describe('EvalLines full run', () => {
  const lines: EvalLinesData = {
    quick: { last_run: null, passed: null, total: null },
    full: { last_run: '2026-10-26T11:04:58+00:00', release: 'blocked', next_run: null },
    online: { last_run: null, sessions_checked: null, next_run: null },
    golden_set: { product: 46, private: 0, total: 46, product_cap: 50, total_cap: 80 },
  };
  it('shows an unknown verdict of the last full run as a dash', () => {
    render(<EvalLines lines={{ ...lines, full: { ...lines.full, release: 'weird' as never } }} />);
    expect(screen.getByTestId('eval-line-full').textContent).toContain('—');
  });

  it('shows the release verdict of the last full run', () => {
    render(<EvalLines lines={lines} />);
    const full = screen.getByTestId('eval-line-full');
    expect(full.textContent).toContain('Blocked');
    expect(full.textContent).not.toMatch(/\d+%/);
  });
});

describe('no dashboard source reads the removed score', () => {
  const sources = import.meta.glob('/src/**/*.{ts,tsx}', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;
  it('has no overall_score or per-dimension percentage reader outside tests', () => {
    const offenders = Object.entries(sources)
      .filter(([path]) => !/\.test\.tsx?$|__tests__/.test(path))
      .filter(([, text]) => /overall_score|\.dimensions\?\.\[|r\.dimensions\b/.test(text))
      .map(([path]) => path);
    expect(Object.keys(sources).length).toBeGreaterThan(50);
    expect(offenders).toEqual([]);
  });
});
