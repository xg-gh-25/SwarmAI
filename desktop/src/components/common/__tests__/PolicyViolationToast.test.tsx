/**
 * Integration tests for PolicyViolationToast (Task 26.6)
 *
 * Tests the wiring of the 409 policy violation UI:
 * - Toast renders with violation message and details
 * - "Resolve in Settings" button triggers onResolve callback
 * - Dismiss button triggers onDismiss callback
 *
 * Requirements: 34.4, 34.5
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { PolicyViolationToast } from '../PolicyViolationToast';
import type { PolicyViolationDetail } from '../../../types';

const mockViolations: PolicyViolationDetail[] = [
  {
    entityType: 'skill',
    entityId: 'skill-123',
    message: "Blocked: requires [CodeReview] which is disabled in this workspace",
    suggestedAction: "Enable skill 'CodeReview' in workspace settings",
  },
  {
    entityType: 'mcp',
    entityId: 'mcp-456',
    message: "Blocked: requires [GitHubMCP] which is disabled in this workspace",
    suggestedAction: "Enable mcp 'GitHubMCP' in workspace settings",
  },
];

describe('PolicyViolationToast', () => {
  it('renders the violation message', () => {
    render(
      <PolicyViolationToast
        message="Execution blocked: requires [CodeReview] which is disabled"
        violations={mockViolations}
        onResolve={vi.fn()}
        onDismiss={vi.fn()}
      />
    );

    expect(
      screen.getByText("Execution blocked: requires [CodeReview] which is disabled")
    ).toBeInTheDocument();
  });

  it('renders individual violation details', () => {
    render(
      <PolicyViolationToast
        message="Policy violation"
        violations={mockViolations}
        onResolve={vi.fn()}
        onDismiss={vi.fn()}
      />
    );

    expect(
      screen.getByText(/requires \[CodeReview\] which is disabled/)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/requires \[GitHubMCP\] which is disabled/)
    ).toBeInTheDocument();
  });

  it('has role="alert" for accessibility', () => {
    render(
      <PolicyViolationToast
        message="Policy violation"
        violations={mockViolations}
        onResolve={vi.fn()}
        onDismiss={vi.fn()}
      />
    );

    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('calls onResolve when "Resolve in Settings" is clicked', () => {
    const onResolve = vi.fn();
    render(
      <PolicyViolationToast
        message="Policy violation"
        violations={mockViolations}
        onResolve={onResolve}
        onDismiss={vi.fn()}
      />
    );

    fireEvent.click(screen.getByText('Resolve in Settings'));
    expect(onResolve).toHaveBeenCalledOnce();
  });

  it('calls onDismiss when "Dismiss" button is clicked', async () => {
    const onDismiss = vi.fn();
    render(
      <PolicyViolationToast
        message="Policy violation"
        violations={mockViolations}
        onResolve={vi.fn()}
        onDismiss={onDismiss}
      />
    );

    fireEvent.click(screen.getByText('Dismiss'));

    // onDismiss is called after a 200ms setTimeout
    await waitFor(() => {
      expect(onDismiss).toHaveBeenCalledOnce();
    }, { timeout: 500 });
  });

  it('calls onDismiss when close icon button is clicked', async () => {
    const onDismiss = vi.fn();
    render(
      <PolicyViolationToast
        message="Policy violation"
        violations={mockViolations}
        onResolve={vi.fn()}
        onDismiss={onDismiss}
      />
    );

    fireEvent.click(screen.getByLabelText('Dismiss notification'));

    await waitFor(() => {
      expect(onDismiss).toHaveBeenCalledOnce();
    }, { timeout: 500 });
  });

  it('renders with empty violations array', () => {
    render(
      <PolicyViolationToast
        message="Something went wrong"
        violations={[]}
        onResolve={vi.fn()}
        onDismiss={vi.fn()}
      />
    );

    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
    // No list items rendered
    expect(screen.queryByRole('listitem')).not.toBeInTheDocument();
  });
});
