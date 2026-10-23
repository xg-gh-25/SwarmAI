/**
 * Tests for OverlayState — the shared first-load states of an overlay
 * (docs/design/left-nav-overlay-finish.md §1.2).
 *
 * Covers: OverlayLoading shows its label (default "Loading…"); OverlayError shows
 * the message and a Retry that calls onRetry, with the caller's testids passed
 * through (existing tests lock those ids); OverlayEmpty shows its children and an
 * optional icon.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { OverlayLoading, OverlayError, OverlayEmpty } from './OverlayState';

describe('OverlayLoading', () => {
  it('defaults to "Loading…" and takes a label + testid', () => {
    const { rerender } = render(<OverlayLoading />);
    expect(screen.getByText('Loading…')).toBeInTheDocument();
    rerender(<OverlayLoading label="Loading brains…" testid="x-loading" />);
    expect(screen.getByTestId('x-loading')).toHaveTextContent('Loading brains…');
  });
});

describe('OverlayError', () => {
  it('shows the message and a Retry that calls onRetry; testids pass through', () => {
    const onRetry = vi.fn();
    render(<OverlayError message="Could not load jobs." onRetry={onRetry} testid="jobs-load-error" retryTestid="jobs-load-retry" />);
    expect(screen.getByTestId('jobs-load-error')).toHaveTextContent('Could not load jobs.');
    fireEvent.click(screen.getByTestId('jobs-load-retry'));
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('jobs-load-retry')).toHaveTextContent('Retry');
  });
});

describe('OverlayEmpty', () => {
  it('shows its children, an optional icon, and the testid', () => {
    render(<OverlayEmpty testid="x-empty" icon="check_circle">Nothing here</OverlayEmpty>);
    const el = screen.getByTestId('x-empty');
    expect(el).toHaveTextContent('Nothing here');
    expect(el.querySelector('.material-symbols-outlined')).toHaveTextContent('check_circle');
  });
});
