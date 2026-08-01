import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { AlertsPill } from './AlertsPill';
import type { AttentionItem } from './RightSidebar/types';

/**
 * Tests for AlertsPill (🔔 需要你) — run_843962a5.
 * Verifies the calm/alert states, the popover open + rich content, and that
 * item clicks route to onItemClick / onSelectTab (reusing AttentionList).
 */
describe('AlertsPill', () => {
  const paused: AttentionItem = {
    kind: 'paused', id: 'run_x', title: 'do a thing', project: 'SwarmAI',
    stage: 'build', reason: 'Gate-1 BLOCK: decide X?',
  };
  const waiting: AttentionItem = {
    kind: 'waiting', id: 'tab-42', title: 'Tab · abc12345', question: 'Pick A/B/C',
  };

  it('CALM: 0 items → pill shows no count badge', () => {
    render(<AlertsPill items={[]} />);
    // The label is present…
    expect(screen.getByText('需要你')).toBeTruthy();
    // …but there is no numeric badge (calm state).
    expect(screen.queryByText(/^\d+$/)).toBeNull();
  });

  it('ALERT: N items → pill shows the count badge', () => {
    render(<AlertsPill items={[paused, waiting]} />);
    expect(screen.getByText('2')).toBeTruthy();
  });

  it('does not render the popover until the pill is clicked', () => {
    render(<AlertsPill items={[paused]} />);
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Alerts/i }));
    expect(screen.getByRole('dialog')).toBeTruthy();
  });

  it('open popover reuses AttentionList — paused click → onItemClick, closes popover', () => {
    const onItemClick = vi.fn();
    const onSelectTab = vi.fn();
    render(<AlertsPill items={[paused]} onItemClick={onItemClick} onSelectTab={onSelectTab} />);
    fireEvent.click(screen.getByRole('button', { name: /Alerts/i }));
    fireEvent.click(screen.getByText('do a thing'));
    expect(onItemClick).toHaveBeenCalledTimes(1);
    const [msg, ctx] = onItemClick.mock.calls[0];
    expect(msg).toContain('run_x');
    expect(ctx).toContain('Gate-1 BLOCK: decide X?');
    expect(onSelectTab).not.toHaveBeenCalled();
    // clicking an item closes the popover
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('waiting click → onSelectTab(tabId), never onItemClick', () => {
    const onItemClick = vi.fn();
    const onSelectTab = vi.fn();
    render(<AlertsPill items={[waiting]} onItemClick={onItemClick} onSelectTab={onSelectTab} />);
    fireEvent.click(screen.getByRole('button', { name: /Alerts/i }));
    fireEvent.click(screen.getByText(/is waiting for you/));
    expect(onSelectTab).toHaveBeenCalledWith('tab-42');
    expect(onItemClick).not.toHaveBeenCalled();
  });

  it('CALM popover shows the "nothing needs you" empty state', () => {
    render(<AlertsPill items={[]} />);
    fireEvent.click(screen.getByRole('button', { name: /Alerts/i }));
    expect(screen.getByText(/没有需要你处理的事/)).toBeTruthy();
  });
});
