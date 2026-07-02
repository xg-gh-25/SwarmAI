/**
 * Tests for AttentionSection (🔔 Needs You).
 *
 * Focus: the click-dispatch-by-kind contract (AC4) and empty-hide (AC3).
 *   - paused / job → onItemClick (inject to input)
 *   - waiting      → onSelectTab (switch tab), NEVER onItemClick
 *   - empty        → renders null
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { AttentionSection } from '../AttentionSection';
import type { AttentionItem } from '../types';

describe('AttentionSection', () => {
  it('AC3: empty items → renders null (section disappears)', () => {
    const { container } = render(<AttentionSection items={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('AC4: paused item click → onItemClick with resume message + reason context, NOT onSelectTab', () => {
    const onItemClick = vi.fn();
    const onSelectTab = vi.fn();
    const items: AttentionItem[] = [
      { kind: 'paused', id: 'run_x', title: 'do a thing', project: 'SwarmAI', stage: 'build', reason: 'Gate-1 BLOCK: decide X?' },
    ];
    render(<AttentionSection items={items} onItemClick={onItemClick} onSelectTab={onSelectTab} />);
    fireEvent.click(screen.getByText('do a thing'));
    expect(onItemClick).toHaveBeenCalledTimes(1);
    const [msg, ctx] = onItemClick.mock.calls[0];
    expect(msg).toContain('run_x');
    expect(ctx).toContain('Gate-1 BLOCK: decide X?');
    expect(onSelectTab).not.toHaveBeenCalled();
  });

  it('AC4: waiting item click → onSelectTab(tabId), NEVER onItemClick', () => {
    const onItemClick = vi.fn();
    const onSelectTab = vi.fn();
    const items: AttentionItem[] = [
      { kind: 'waiting', id: 'tab-42', title: 'Tab · abc12345', question: 'Pick A/B/C' },
    ];
    render(<AttentionSection items={items} onItemClick={onItemClick} onSelectTab={onSelectTab} />);
    fireEvent.click(screen.getByText(/is waiting for you/));
    expect(onSelectTab).toHaveBeenCalledWith('tab-42');
    expect(onItemClick).not.toHaveBeenCalled();
  });

  it('AC4: job item click → onItemClick triage message', () => {
    const onItemClick = vi.fn();
    const items: AttentionItem[] = [
      { kind: 'job', id: 'morning-inbox', title: 'Morning Inbox', failures: 2 },
    ];
    render(<AttentionSection items={items} onItemClick={onItemClick} />);
    fireEvent.click(screen.getByText(/failed 2x/));
    expect(onItemClick).toHaveBeenCalledTimes(1);
    expect(onItemClick.mock.calls[0][0]).toContain('Morning Inbox');
  });
});
