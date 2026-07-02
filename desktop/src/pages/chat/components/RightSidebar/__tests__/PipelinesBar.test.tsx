/**
 * Tests for PipelinesBar — Run 1 redesign.
 * Focus (AC5): empty → null; running items are DISPLAY-ONLY (no button/click target).
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PipelinesBar } from '../PipelinesBar';
import type { RunningPipeline } from '../types';

describe('PipelinesBar', () => {
  it('AC5: empty running list → renders null', () => {
    const { container } = render(<PipelinesBar running={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('AC5: running pipelines render but are NOT clickable (no button element)', () => {
    const running: RunningPipeline[] = [
      { id: 'run_a', title: 'Todo merge', project: 'SwarmAI', stage: 'test' },
      { id: 'run_b', title: '报表重构', project: 'Rocky_ISV', stage: 'build' },
    ];
    const { container } = render(<PipelinesBar running={running} />);
    // shows both + the count
    expect(screen.getByText('2 running')).toBeInTheDocument();
    expect(screen.getByText('Todo merge')).toBeInTheDocument();
    // cross-project label shown, same-project (SwarmAI) suppressed
    expect(screen.getByText('Rocky_ISV')).toBeInTheDocument();
    // NOT clickable: zero <button> elements in the bar
    expect(container.querySelectorAll('button')).toHaveLength(0);
  });
});
