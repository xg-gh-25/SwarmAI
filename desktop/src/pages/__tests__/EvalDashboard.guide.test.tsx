/**
 * EvalDashboard GuideTab fact-freshness contract (bugfix run_26cc4bd4).
 *
 * The Guide tab had hardcoded facts that drifted from the live eval system:
 *   - "115 behavioral cases / 84 LLM-judged"  → dropped (volatile absolutes removed)
 *   - "~185 cases as of 2026-06"              → dropped (live count is on Overview/Golden Set)
 *   - "Seven Eval Dimensions" / "Dimension (7)" → 6 canonical dims (source: eval_runner.DIMENSIONS
 *       + golden_set.yaml `dimensions:`). A PRIOR fix (run_26cc4bd4) miscounted live as 7 and
 *       set "Five"→"Seven". run_8c44b7bf corrected it: folded the duplicate `utility`→`context_utility`,
 *       and kept `recovery` as a first-class (test_ac6-protected) dimension → 6 total. (run_8c44b7bf)
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

  it('shows the correct canonical dimension count (6, not the stale 7)', () => {
    const { container } = renderGuide();
    const text = container.textContent || '';
    // Source of truth = eval_runner.DIMENSIONS + golden_set.yaml (6: canonical 5 + recovery).
    // A prior fix wrongly set "Seven"; the duplicate "utility" folded into context_utility.
    expect(text).not.toContain('Seven Eval Dimensions');
    expect(text).not.toContain('七个评估维度');
    expect(text).not.toContain('Dimension (7)');
    expect(text).not.toContain('Dimension（7');
    expect(text).toContain('Six Eval Dimensions');
    // exactly 6 dimension cards render (stale "utility" removed, "recovery" kept)
    expect(container.querySelectorAll('[data-dim-key]').length).toBe(6);
    // the folded-away duplicate must be gone, the kept dimension present
    const keys = Array.from(container.querySelectorAll('[data-dim-key]')).map(e => e.getAttribute('data-dim-key'));
    expect(keys).toContain('recovery');
    expect(keys).not.toContain('utility');
  });

  it('reflects the CORRECT scheduled cadence (Monday-only, cron 30 4 * * 1) and runtime_health evaluator', () => {
    const { container } = renderGuide();
    const text = container.textContent || '';
    // The real job is `30 4 * * 1` = MONDAY only. A prior version said "Weekdays 12:30"
    // — that drift is fixed. Assert Monday is present AND the stale "weekday/工作日" claim is gone.
    expect(text).toMatch(/Monday|周一/);
    expect(text).not.toContain('Weekdays 12:30');
    expect(text).not.toContain('工作日 12:30');
    // the runtime_health evaluator stays documented
    expect(text).toContain('runtime_health');
  });

  it('embeds BOTH the overall architecture and the single-run sequence diagrams', () => {
    renderGuide();
    const arch = screen.getByAltText(/eval.*architecture/i);
    expect(arch.getAttribute('src')).toBe('/eval-architecture.svg');
    const seq = screen.getByAltText(/one run end to end|sequence/i);
    expect(seq.getAttribute('src')).toBe('/eval-sequence.svg');
  });
});
