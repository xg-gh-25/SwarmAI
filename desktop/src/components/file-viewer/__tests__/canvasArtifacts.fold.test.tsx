/**
 * Canvas Artifacts — Activity rows fold.
 *
 * Invariants: rows whose catalog role is Activity sit under one "Activity · N"
 * toggle, closed by default, after the other rows; the toggle opens and closes them;
 * a selected Activity row keeps the group open.
 *
 * Methodology: the real CanvasOutputRail with rows passed in.
 */
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import type { ReferencedFile } from '../../../hooks/useReferencedFiles';


import { CanvasOutputRail } from '../CanvasOutputRail';

const row = (path: string, role: string, firstSeen: number): ReferencedFile => ({
  path, absolutePath: `/ws/${path}`, fileName: path.split('/').pop()!, operation: 'written',
  firstSeen, count: 1, kind: 'knowledge', role,
});
const FILES = [
  row('Knowledge/DailyActivity/2026-10-22.md', 'Activity', 3),
  row('Projects/P/designs/plan.md', 'Knowledge', 2),
  row('Knowledge/Signals/s.md', 'Activity', 1),
];
const names = () => screen.queryAllByText(/\.md$/).map((e) => e.textContent);

describe('Artifacts rail — Activity folds', () => {
  it('Activity rows are folded under one toggle after the others, and open on click', () => {
    render(<CanvasOutputRail files={{ written: FILES }} />);
    const toggle = screen.getByTestId('canvas-activity-toggle');
    expect(toggle.textContent).toContain('Activity');
    expect(toggle.textContent).toContain('2');
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(names()).toEqual(['plan.md']);
    fireEvent.click(toggle);
    expect(names()).toEqual(['plan.md', '2026-10-22.md', 's.md']);
    fireEvent.click(toggle);
    expect(names()).toEqual(['plan.md']);
  });

  it('a selected Activity row keeps its group open', () => {
    render(<CanvasOutputRail files={{ written: FILES }} selectedPath="Knowledge/Signals/s.md" />);
    expect(screen.getByTestId('canvas-activity-toggle').getAttribute('aria-expanded')).toBe('true');
    expect(names()).toContain('s.md');
  });

  it('no Activity rows: no toggle', () => {
    render(<CanvasOutputRail files={{ written: [FILES[1]] }} />);
    expect(screen.queryByTestId('canvas-activity-toggle')).toBeNull();
  });
});
