/**
 * PipelineRunCard — the Feature checks block (design 2026-11-18-feature-map §8): each
 * reached walk with its result and its screenshots as file rows (they open in the
 * Canvas), the features not replayed on the final tree, the expectations the run
 * changed, and the note when blocking is off.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup, within } from '@testing-library/react';
import { FeatureChecks } from '../PipelineRunCard';
import type { FeatureCheckRepo } from '../../../services/pipelines';

afterEach(cleanup);

const fc: FeatureCheckRepo = {
  repo: 'app',
  blocking: true,
  rows: [
    { feature: 'title-a1', walk: 'title', project: 'regular', result: 'pass', preExisting: false,
      files: ['/runs/run_x/artifacts/feature-checks/app/0/0-test-finished-1.png'] },
    { feature: 'pop-b2', walk: 'popover in window', project: 'compact', result: 'fail', preExisting: true, files: [] },
  ],
  missing: ['state-d4'],
  changedExpectations: [{ file: '.swarmai/features/specs/title.spec.ts', change: '+', line: "toHaveText('Hi')" }],
  error: '',
};

describe('FeatureChecks', () => {
  it('renders feature checks with screenshots', () => {
    render(<FeatureChecks fc={fc} />);
    const box = screen.getByTestId('pipeline-card-feature-checks');
    const rows = within(box).getAllByTestId('pipeline-card-feature-row');
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain('title-a1 · title');
    expect(rows[0].textContent).toContain('✓');
    // the screenshot is a file row (opens in the Canvas), named by its file
    expect(within(rows[0]).getAllByTestId('pipeline-card-feature-shot')).toHaveLength(1);
    expect(rows[0].textContent).toContain('0-test-finished-1.png');
    expect(rows[1].textContent).toContain('✗');
    expect(rows[1].textContent).toMatch(/base/);           // red at the run's base too
    expect(within(box).getByTestId('pipeline-card-feature-missing').textContent).toContain('state-d4');
    expect(within(box).getByTestId('pipeline-card-feature-expectations').textContent).toContain("toHaveText('Hi')");
    expect(box.textContent).not.toMatch(/listed only/i);
  });

  it('says when blocking is off', () => {
    render(<FeatureChecks fc={{ ...fc, blocking: false }} />);
    expect(screen.getByTestId('pipeline-card-feature-checks').textContent).toMatch(/listed only/i);
  });
});
