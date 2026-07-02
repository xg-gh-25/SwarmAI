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

  const pausedWithReason: AttentionItem = {
    kind: 'paused', id: 'run_x', title: 'do a thing', project: 'SwarmAI', stage: 'build',
    reason: 'Gate-1 BLOCK: this is a very long decision reason that should be collapsed by default',
  };

  it('AC1: paused reason is COLLAPSED by default (title + action shown, reason hidden)', () => {
    render(<AttentionSection items={[pausedWithReason]} onItemClick={vi.fn()} />);
    expect(screen.getByText('do a thing')).toBeInTheDocument();
    expect(screen.getByText('→ Resume & answer')).toBeInTheDocument();
    // reason text is NOT rendered until expanded
    expect(screen.queryByText(/very long decision reason/)).not.toBeInTheDocument();
    // chevron toggle present
    expect(screen.getByLabelText('Expand decision detail')).toBeInTheDocument();
  });

  it('AC2: chevron click expands reason WITHOUT firing the card action; toggles back', () => {
    const onItemClick = vi.fn();
    render(<AttentionSection items={[pausedWithReason]} onItemClick={onItemClick} />);
    const chevron = screen.getByLabelText('Expand decision detail');
    fireEvent.click(chevron);
    // reason now visible, action NOT fired
    expect(screen.getByText(/very long decision reason/)).toBeInTheDocument();
    expect(onItemClick).not.toHaveBeenCalled();
    // collapse again
    fireEvent.click(screen.getByLabelText('Collapse decision detail'));
    expect(screen.queryByText(/very long decision reason/)).not.toBeInTheDocument();
  });

  it('AC3: paused card with NO reason shows no chevron', () => {
    const noReason: AttentionItem = { kind: 'paused', id: 'run_y', title: 'no reason', project: 'P', stage: 'test', reason: '' };
    render(<AttentionSection items={[noReason]} onItemClick={vi.fn()} />);
    expect(screen.queryByLabelText('Expand decision detail')).not.toBeInTheDocument();
    expect(screen.getByText('no reason')).toBeInTheDocument();
  });
});
