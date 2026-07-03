/**
 * EvalDashboard GuideTab fact-freshness contract (bugfix run_26cc4bd4).
 *
 * The Guide tab had hardcoded facts that drifted from the live eval system:
 *   - "115 behavioral cases / 84 LLM-judged"  → dropped (volatile absolutes removed)
 *   - "~185 cases as of 2026-06"              → dropped (live count is on Overview/Golden Set)
 *   - "Seven Eval Dimensions" / "Dimension (7)" → 5 canonical dims (source: eval_runner.DIMENSIONS
 *       + golden_set.yaml `dimensions:`). A PRIOR fix (run_26cc4bd4) miscounted live as 7 and
 *       set "Five"→"Seven"; the source truth is 5, so this run corrects it back. (run_8c44b7bf)
 *   - triggers "Weekly (Thu 04:00 UTC)"       → live lunchtime 12:30 ICT weekdays
 *   - 6 programmatic evaluators               → 7 (runtime_health added)
 *   - no architecture diagram                 → embed eval-architecture.svg
 *
 * These assertions FAIL on the stale content and pass only after the refresh.
 * Counts are checked for the absence of the OLD wrong values (drift guard),
 * not pinned to exact live numbers (which legitimately grow).
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { GuideTab } from '../EvalDashboard';

function renderGuide() {
  return render(<GuideTab />);
}

describe('GuideTab fact freshness', () => {
  it('does not show the stale case/judge counts (115 / 84 / 185) or Weekly-Thu cadence', () => {
    const { container } = renderGuide();
    const text = container.textContent || '';
    // stale absolutes that must be gone
    expect(text).not.toContain('115 behavioral cases');
    expect(text).not.toContain('115 个');
    expect(text).not.toContain('84 LLM-judged');
    expect(text).not.toContain('185 cases');
    expect(text).not.toContain('185 个');
    expect(text).not.toContain('Thu 04:00');
    expect(text).not.toContain('周四 04:00');
  });

  it('shows the correct canonical dimension count (5, not the stale 7)', () => {
    const { container } = renderGuide();
    const text = container.textContent || '';
    // Source of truth = eval_runner.DIMENSIONS (5). A prior fix wrongly set "Seven".
    expect(text).not.toContain('Seven Eval Dimensions');
    expect(text).not.toContain('七个评估维度');
    expect(text).not.toContain('Dimension (7)');
    expect(text).not.toContain('Dimension（7');
    expect(text).toContain('Five Eval Dimensions');
    // exactly 5 dimension cards render (utility + recovery removed)
    expect(container.querySelectorAll('[data-dim-key]').length).toBe(5);
  });

  it('reflects the refreshed cadence (lunchtime / weekday) and runtime_health evaluator', () => {
    const { container } = renderGuide();
    const text = container.textContent || '';
    // refreshed trigger cadence — 12:30 ICT weekday lunch window
    expect(text).toMatch(/12:30|lunch|weekday|工作日|午/);
    // the 7th programmatic evaluator now documented
    expect(text).toContain('runtime_health');
  });

  it('embeds the official eval-architecture diagram', () => {
    renderGuide();
    const img = screen.getByAltText(/eval.*architecture/i);
    expect(img.getAttribute('src')).toBe('/eval-architecture.svg');
  });
});
