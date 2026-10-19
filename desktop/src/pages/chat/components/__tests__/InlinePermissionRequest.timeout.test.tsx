/**
 * InlinePermissionRequest — the display countdown never decides for the user.
 *
 * What is tested (fake timers, the real component):
 * - the countdown mirrors the backend's 4 h PERMISSION_ANSWER_TIMEOUT_SECONDS
 * - running out sends NO decision (the backend answers with its own "审批超时"
 *   reason; a frontend 'deny' used to be logged as "User denied") and shows a muted
 *   "Timed out" shell without Approve/Deny buttons
 * - before it runs out, Approve / Deny still send exactly one decision
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import React from 'react';
import { InlinePermissionRequest, TIMEOUT_MS } from '../InlinePermissionRequest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, fallback: unknown) => fallback }),
}));

const props = {
  requestId: 'perm_1',
  toolName: 'Bash',
  toolInput: { command: 'git commit --amend -m x' },
  reason: 'Matches dangerous command pattern',
  isPending: true,
};

describe('InlinePermissionRequest timeout', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('mirrors the backend 4 h timeout', () => {
    expect(TIMEOUT_MS).toBe(4 * 60 * 60 * 1000);
  });

  it('running out marks the card timed out and sends no decision', () => {
    const onDecision = vi.fn();
    render(<InlinePermissionRequest {...props} onDecision={onDecision} />);
    act(() => { vi.advanceTimersByTime(TIMEOUT_MS + 2000); });
    expect(onDecision).not.toHaveBeenCalled();
    expect(screen.getByText('Timed out')).toBeTruthy();
    expect(screen.queryByText('Deny')).toBeNull();
    expect(screen.queryByText('Approve')).toBeNull();
  });

  it('Deny before the timeout sends one deny', () => {
    const onDecision = vi.fn();
    render(<InlinePermissionRequest {...props} onDecision={onDecision} />);
    act(() => { vi.advanceTimersByTime(60_000); });
    fireEvent.click(screen.getByText('Deny'));
    act(() => { vi.advanceTimersByTime(TIMEOUT_MS); });
    expect(onDecision).toHaveBeenCalledTimes(1);
    expect(onDecision).toHaveBeenCalledWith('perm_1', 'deny');
  });

  it('Approve sends one approve', () => {
    const onDecision = vi.fn();
    render(<InlinePermissionRequest {...props} onDecision={onDecision} />);
    fireEvent.click(screen.getByText('Approve'));
    expect(onDecision).toHaveBeenCalledWith('perm_1', 'approve');
  });
});
